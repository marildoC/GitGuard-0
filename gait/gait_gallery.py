"""
GaitGallery class to load/save the gallery
and to search for gait embeddings using FAISS.
"""
import numpy as np
from typing import Dict, Optional, Tuple,List
import logging
import pickle #To save and load the gallery
import hashlib
import os
from scipy.spatial.distance import cosine
from gait.config import GaitConfig
from dataclasses import dataclass

#Import FAISS
try:
    import faiss
except ImportError:
    raise ImportError("FAISS is not installed. Please install with 'pip install faiss-cpu'")

logger=logging.getLogger(__name__)

# --- New gallery entry structure to support EMA ---
@dataclass
class GaitIdentityData:
    """
    Stores all relevant data for a single gait identity within the gallery.

    How it works:
    This dataclass acts as a comprehensive record for each known person's gait.
    It holds both the current Exponential Moving Average (EMA) of their gait
    embeddings (used for fast search in FAISS) and a history of all raw embeddings
    that contributed to that EMA. This history allows for potential re-calculation,
    analysis, or auditing of the identity's gait profile. The `num_updates`
    field keeps track of how many times the EMA has been refined.

    Attributes:
    - identity_id (str): A unique string identifier for the person (e.g., "John Doe", "person_XYZ").
    - faiss_id (int): A unique numerical ID assigned internally for FAISS indexing.
    - ema_embedding (np.ndarray): The current Exponential Moving Average of gait embeddings
                                   for this identity. This is the vector used for searching.
    - raw_embeddings (List[np.ndarray]): A list containing all individual gait embeddings
                                         that have been added for this identity.
    - num_updates (int): A counter for how many times the `ema_embedding` has been updated.
    """
    identity_id: str
    faiss_id: int
    ema_embedding: np.ndarray        # The current Exponential Moving Average embedding
    raw_embeddings: List[np.ndarray] # History of raw embeddings added for this identity
    num_updates: int = 0             # Number of times EMA has been updated

class FaissIndexWrapper:
    """
    A wrapper around FAISS IndexIDMap for adding, searching, and removing embeddings by ID.
    It specifically handles L2 normalization for cosine similarity when configured.
    """
    def __init__(self,dim:int,metric:str="cosine"):
        """
        Initializes the FAISS index wrapper.

        How it works:
        Upon initialization, it sets up a FAISS `IndexIDMap`. If `metric` is "cosine",
        it uses `IndexFlatIP` (Inner Product) which is equivalent to cosine similarity
        when embeddings are L2-normalized. It also sets a `normalize` flag to ensure
        embeddings are normalized before being added or searched. If any other metric
        is specified, it defaults to `IndexFlatL2` (Euclidean distance) and warns
        that direct metric support is limited. A counter (`current_faiss_id_counter`)
        is initialized to assign unique numerical IDs to new entries for FAISS.

        Args:
            dim (int): The dimensionality of the gait embeddings.
            metric (str, optional): The similarity metric to use ("cosine" or others).
                                    Defaults to "cosine".
        """
        self.dim=dim
        self.metric=metric
        
        if metric == "cosine":
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))
            self.normalize = True # L2 normalize embeddings for cosine similarity with IP index
            
        else:
            self.index = faiss.IndexIDMap(faiss.IndexFlatL2(dim))
            self.normalize = False 
        self.current_faiss_id_counter = 0

    def _get_next_faiss_id(self) -> int:
        """
        Generates and returns a new unique numerical ID for FAISS.

        How it works:
        This helper method simply returns the current value of `self.current_faiss_id_counter`
        and then increments it, ensuring that each new entry added to FAISS gets a unique,
        sequential integer ID. This is necessary because FAISS requires integer IDs for `IndexIDMap`.
        """
        new_id = self.current_faiss_id_counter
        self.current_faiss_id_counter += 1
        return new_id
    
    def add(self, embeddings:np.ndarray, faiss_id:str)->None:
        """
        Adds one or more embeddings to the FAISS index with a specific FAISS ID.

        How it works:
        If a single 1D embedding is provided, it's reshaped to 2D for FAISS.
        If `self.normalize` is True (e.g., for cosine similarity), the embeddings
        are L2-normalized before being added. The `faiss_id` is then replicated
        for all embeddings being added (useful if adding multiple embeddings for one ID),
        and `self.index.add_with_ids` is called to insert them into the FAISS index.

        Args:
            embeddings (np.ndarray): A single 1D embedding or a 2D array of embeddings
                                     to be added.
            faiss_id (int): The numerical FAISS ID to associate with these embeddings.
        """
        if embeddings.ndim==1: #If is a single embedding, we transform it in 2D for FAISS
            embeddings = embeddings[np.newaxis,:]
        if self.normalize:
            faiss.normalize_L2(embeddings)

        # FAISS add_with_ids takes a 1D array of numerical IDs
        faiss_ids_array = np.array([faiss_id] * embeddings.shape[0], dtype=np.int64)
        self.index.add_with_ids(embeddings, faiss_ids_array)
        
    def remove(self, faiss_ids: List[int]) -> None:
        """
        Removes embeddings from the FAISS index by their FAISS IDs.

        How it works:
        Given a list of numerical `faiss_ids`, it converts this list into
        a NumPy array of `int64` and calls `self.index.remove_ids`. This
        efficiently deletes all embeddings associated with the specified IDs
        from the FAISS index.

        Args:
            faiss_ids (List[int]): A list of numerical FAISS IDs to remove.
        """
        if not faiss_ids:
            return
        # FAISS remove_ids takes a 1D array of numerical IDs
        ids_to_remove_array = np.array(faiss_ids, dtype=np.int64)
        self.index.remove_ids(ids_to_remove_array)
        
    def search(self,query_embedding:np.ndarray, k:int=1)->Tuple[np.ndarray, np.ndarray]:
        """
        Searches the FAISS index for the k nearest neighbors to a query embedding.

        How it works:
        The `query_embedding` is reshaped to 2D if it's 1D. If `self.normalize` is True,
        the query embedding is L2-normalized to ensure consistency with the index.
        Then, `self.index.search` is called to find the `k` closest embeddings.
        This method returns two NumPy arrays: `D` (distances/similarities) and
        `I` (the numerical FAISS IDs of the nearest neighbors). The results
        are specific to the query's first entry (index `[0]`) for simplicity.

        Args:
            query_embedding (np.ndarray): The 1D or 2D embedding to search for.
            k (int, optional): The number of nearest neighbors to retrieve. Defaults to 1.

        Returns:
            Tuple[np.ndarray, np.ndarray]:
                - D (np.ndarray): Distances (or similarities for IP index) to the k nearest neighbors.
                - I (np.ndarray): The FAISS IDs of the k nearest neighbors.
        """
        if query_embedding.ndim == 1:
            query_embedding = query_embedding[np.newaxis, :]
        
        if self.normalize:
            faiss.normalize_L2(query_embedding)
        # D: distances (for IP, these are similarities), I: indices (FAISS IDs)
        D, I = self.index.search(query_embedding, k)
        return D[0], I[0] # Returns only the first result of the query if K is 1 (or the first query if multiple)
    

class GaitGallery:
    """
    Manages the collection of known gait embeddings for identity recognition.
    It uses FAISS for efficient similarity search and supports Exponential
    Moving Average (EMA) updates for registered identities, with persistence
    through pickle serialization.
    """

    def __init__(self,config: GaitConfig):
        """Initializes the GaitGallery.

        How it works:
        It stores the `GaitConfig` and initializes an empty dictionary `_identities`
        to hold `GaitIdentityData` objects, mapping string `identity_id` to their data.
        A `FaissIndexWrapper` is instantiated to handle the underlying FAISS operations.
        If a gallery file specified by `config.gallery.gallery_path` exists, it attempts
        to load the gallery from disk; otherwise, it starts with an empty gallery.

        Args:
            config (GaitConfig): The configuration object for gait parameters."""
        self.config = config
        # _identities: Map identity_id to GaitIdentityData (contains EMA and raw embeddings)
        self._identities: Dict[str, GaitIdentityData] = {}
        
        # Wrapper FAISS for fast research
        self.faiss_index = FaissIndexWrapper(dim=config.gallery.dim, metric=config.gallery.metric)

        
        if self.config.gallery.gallery_path.exists():
            self.load_gallery()
        else:
            logger.info("Gait gallery file not found, starting with empty gallery.")

        logger.info(f"GaitGallery initialized with {len(self._identities)} unique identities.")
   
    #Persistence of the gallery with pickle
    def save_gallery(self)->None:
        """Saves the current state of the gait gallery to disk using pickle.

        How it works:
        It creates the necessary parent directories for the `gallery_path` if they don't exist.
        The gallery's state, comprising the `_identities` dictionary (which contains EMA and
        raw embeddings) and the `faiss_id_counter`, is serialized using `pickle.dump`
        into the file specified by `self.config.gallery.gallery_path`. Note that the FAISS
        index itself is NOT saved directly; it is designed to be rebuilt from the EMA embeddings
        stored in `_identities` upon loading.

        Args:
            None
        """
        try:
            self.config.gallery.gallery_path.parent.mkdir(parents=True, exist_ok=True)

            #We save only the idendity, index FAISS will be reconstruct at the load
            state = {
                "identities": self._identities,
                "faiss_id_counter": self.faiss_index.current_faiss_id_counter
            }
            with open(self.config.gallery.gallery_path, "wb") as f:
                pickle.dump(state, f)
            logger.debug(f"Gait identity data saved to {self.config.gallery.gallery_path}")
        except Exception as e:
            logger.error(f"Error saving gait gallery: {e}")

    
    def load_gallery(self) ->None:
        """Loads the gallery state from disk and rebuilds the FAISS index.

        How it works:
        It attempts to load the serialized gallery state from `self.config.gallery.gallery_path`
        using `pickle.load`. The loaded `_identities` dictionary and `faiss_id_counter` are
        then restored. After loading the identity data, it iterates through all `identity_data`
        objects and adds their `ema_embedding` to the `faiss_index` to reconstruct the search index.
        Robust error handling is included for `FileNotFoundError` and other exceptions,
        resetting the gallery to an empty state if loading fails.

        Args:
            None"""
        try:
            with open(self.config.gallery.gallery_path, "rb") as f:
                state = pickle.load(f)
                self._identities = state["identities"]
                self.faiss_index.current_faiss_id_counter = state["faiss_id_counter"]
            logger.debug(f"Gait identity data loaded from {self.config.gallery.gallery_path}")

            # Rebuild the FAISS INDEX from the loaded EMA embeddings
            if self._identities:
                for identity_data in self._identities.values():
                    # add EMA embedding to the FAISS index
                    self.faiss_index.add(identity_data.ema_embedding, identity_data.faiss_id)
                logger.info(f"FAISS index rebuilt with {self.faiss_index.index.ntotal} embeddings.")

        except FileNotFoundError:
            logger.info(f"Gait gallery file not found at {self.config.gallery.gallery_path}.")
            self._identities = {}
            self.faiss_index.current_faiss_id_counter = 0 # Reset counter
        except Exception as e:
            logger.error(f"Error loading gait gallery: {e}")
            self._identities = {}
            self.faiss_index.current_faiss_id_counter = 0 # Reset counter

    def _update_ema_embedding(self, current_ema: np.ndarray, new_embedding: np.ndarray) -> np.ndarray:
        """
        Updates an Exponential Moving Average (EMA) embedding with a new embedding.

        How it works:
        It calculates the new EMA using the formula `(1 - alpha) * current_ema + alpha * new_embedding`,
        where `alpha` is the smoothing factor from `self.config.gallery.ema_alpha`.
        The `updated_ema` is then L2-normalized to ensure compatibility with cosine
        distance calculations. If the updated EMA is a zero vector, it remains zero
        to prevent division by zero during normalization.

        Args:
            current_ema (np.ndarray): The current EMA embedding.
            new_embedding (np.ndarray): The latest raw embedding to incorporate into the EMA.

        Returns:
            np.ndarray: The newly calculated and normalized EMA embedding.
        """
        alpha = self.config.gallery.ema_alpha
        updated_ema = (1 - alpha) * current_ema + alpha * new_embedding
        #Normalize the updated EMA to keep compatibility with cosine distance
        norm = np.linalg.norm(updated_ema)
        return updated_ema / norm if norm > 0 else np.zeros_like(updated_ema)
    
    def add_gait_embedding(self, identity_id: str, new_embedding: np.ndarray, confirmed: bool = False) -> None:
        """
        Adds a gait embedding for a specific identity to the gallery or updates its EMA.
        This method is gated by the 'confirmed' flag for EMA updates.

        How it works:
        If `identity_id` is new, a `GaitIdentityData` object is created with a fresh FAISS ID,
        the `new_embedding` is set as the initial EMA, and it's added to `_identities`
        and the `faiss_index`.
        If the `identity_id` already exists and `confirmed` is True:
        1.  The old EMA embedding is first removed from the `faiss_index`.
        2.  `_update_ema_embedding` is called to calculate the new EMA.
        3.  The `GaitIdentityData` object is updated with the new EMA and `new_embedding`
            is added to `raw_embeddings`.
        4.  The new EMA is then added back to the `faiss_index` with the same `faiss_id`.
        If the identity exists but `confirmed` is False, no update occurs.
        Finally, the gallery state is saved after any modification.

        Args:
            identity_id (str): The unique string identifier for the person.
            new_embedding (np.ndarray): The gait embedding to add or use for updating the EMA.
            confirmed (bool, optional): If True, the new embedding updates the EMA of an
                                        existing identity. If False, it's ignored for existing
                                        identities. Defaults to False.
        """
        if identity_id not in self._identities:
            faiss_id = self.faiss_index._get_next_faiss_id() # Assign a new numerical ID for FAISS
            identity_data = GaitIdentityData(
                identity_id=identity_id,
                faiss_id=faiss_id,
                ema_embedding=new_embedding,
                raw_embeddings=[new_embedding],
                num_updates=1
            )
            self._identities[identity_id] = identity_data
            
            self.faiss_index.add(new_embedding, faiss_id) # Add the initial EMA embedding to the FAISS 
            logger.info(f"New identity {identity_id} (FAISS ID: {faiss_id}) added to gallery with initial EMA. Total unique identities: {len(self._identities)}")
        else:
            # Existing identity: Updated only if confirmed
            if confirmed:
                identity_data = self._identities[identity_id]
                # Remove the old EMA embedding from the FAISS index
                self.faiss_index.remove([identity_data.faiss_id])

                # Update the EMA embedding
                updated_ema = self._update_ema_embedding(identity_data.ema_embedding, new_embedding)
                identity_data.ema_embedding = updated_ema
                identity_data.raw_embeddings.append(new_embedding)
                identity_data.num_updates += 1
                
                self.faiss_index.add(updated_ema, identity_data.faiss_id)

                logger.debug(f"EMA for {identity_id} (FAISS ID: {identity_data.faiss_id}) updated (num_updates={identity_data.num_updates}).")
            else:
                logger.debug(f"New embedding for {identity_id} received but 'confirmed' is False. EMA not updated.")
        self.save_gallery()

    def search(self, query_embedding:np.ndarray)->Tuple[Optional[str], Optional[float]]:
        """
        Searches the gallery for the closest matching gait embedding using FAISS.

        How it works:
        It first checks if the FAISS index is empty; if so, it returns no match.
        Otherwise, it performs a search using `self.faiss_index.search` for the
        `query_embedding`. The returned FAISS distance/similarity and ID are used
        to find the corresponding string `identity_id` from `_identities`.
        The FAISS result (inner product for cosine, L2 distance for Euclidean) is
        then converted into a consistent `min_distance` metric (cosine distance, i.e., 1 - similarity).
        This `min_distance` is compared against `max_match_distance` and `max_weak_match_distance`
        from the configuration. If a match is found within these thresholds, the `identity_id`
        and a calculated `confidence` (1 - `min_distance`) are returned; otherwise, `None, None`
        is returned.

        Args:
            query_embedding (np.ndarray): The gait embedding of an unknown person to search for.

        Returns:
            Tuple[Optional[str], Optional[float]]:
                - identity_id (Optional[str]): The string ID of the matched person, or None if no match.
                - confidence (Optional[float]): The confidence score (0-1) of the match, or None.
        """
        if self.faiss_index.index.ntotal == 0:
            logger.debug("FAISS index is empty, no search performed.")
            return None, None

        distances_faiss, faiss_indices = self.faiss_index.search(query_embedding, k=1)
        
        best_dist_or_sim = distances_faiss[0]
        best_faiss_id = faiss_indices[0]

        if best_faiss_id == -1: # No result found by FAISS (e.g., empty index, or query too far)
            return None, None

       # Find the original identity_id from the FAISS ID
        best_match_id: Optional[str] = None
        for id_str, identity_data in self._identities.items():
            if identity_data.faiss_id == best_faiss_id:
                best_match_id = id_str
                break
        
        if best_match_id is None:
            logger.error(f"FAISS ID {best_faiss_id} found but no corresponding identity_id in _identities. Data inconsistency.")
            return None, None

        # Convert FAISS distance/similarity to our cosine distance for threshold comparison
        if self.config.gallery.metric == "cosine" and self.faiss_index.normalize:
            # For FAISS IndexFlatIP with normalized vectors, the result is cosine similarity.
            # Convert to cosine distance (1 - similarity) for consistency with thresholds.
            min_distance = 1.0 - best_dist_or_sim
        else:
            min_distance = best_dist_or_sim # Otherwise, assume it's already a distance

        # Apply distance thresholds.
        if min_distance <= self.config.thresholds.max_match_distance:
            confidence = 1.0 - min_distance
            logger.info(f"Found strong gait match for {best_match_id} (FAISS ID {best_faiss_id}) with distance {min_distance:.2f} (confidence {confidence:.2f}).")
            return best_match_id, confidence
        
        elif min_distance <= self.config.thresholds.max_weak_match_distance:
            confidence = 1.0 - min_distance
            logger.info(f"Found weak gait match for {best_match_id} (FAISS ID {best_faiss_id}) with distance {min_distance:.2f} (confidence {confidence:.2f}).")
            return best_match_id, confidence
        else:
            logger.info(f"No gait match found. Best distance was {min_distance:.2f}, above weak threshold {self.config.thresholds.max_weak_match_distance}.")
            return None, None