# gait/gallery.py

import numpy as np
from typing import Dict, Optional, Tuple, List
import logging
import pickle
import os
from dataclasses import dataclass
from gait.config import GaitConfig

try:
    import faiss
except ImportError:
    raise ImportError("FAISS is not installed. Please install with 'pip install faiss-cpu'")

logger = logging.getLogger(__name__)

@dataclass
class GaitIdentityData:
    """
    Data structure holding all information about a single enrolled identity.
    """
    identity_id: str
    faiss_id: int
    ema_embedding: np.ndarray       # Exponential Moving Average of the embedding
    raw_embeddings: List[np.ndarray] # History of raw embeddings
    num_updates: int = 0
    category: str = "resident"      # Added for CLI compatibility

@dataclass
class PersonSummary:
    """
    Lightweight structure for listing enrolled persons.
    """
    person_id: str
    name: str
    category: str
    num_templates: int

class FaissIndexWrapper:
    """
    Wrapper around the FAISS library to handle vector search operations.
    Supports both Cosine Similarity (via Inner Product + Normalization) and L2 distance.
    """
    def __init__(self, dim: int, metric: str = "cosine"):
        self.dim = dim
        self.metric = metric
        if metric == "cosine":
            # Inner Product (IP) is equivalent to Cosine Similarity if vectors are normalized
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))
            self.normalize = True
        else:
            self.index = faiss.IndexIDMap(faiss.IndexFlatL2(dim))
            self.normalize = False
        self.current_faiss_id_counter = 0

    def _get_next_faiss_id(self) -> int:
        """Generates a unique incremental integer ID for FAISS."""
        new_id = self.current_faiss_id_counter
        self.current_faiss_id_counter += 1
        return new_id
    
    def add(self, embeddings: np.ndarray, faiss_id: int) -> None:
        """Adds a vector to the FAISS index associated with a specific ID."""
        if embeddings.ndim == 1:
            embeddings = embeddings[np.newaxis, :]
        if self.normalize:
            faiss.normalize_L2(embeddings)
        faiss_ids_array = np.array([faiss_id] * embeddings.shape[0], dtype=np.int64)
        self.index.add_with_ids(embeddings, faiss_ids_array)
        
    def remove(self, faiss_ids: List[int]) -> None:
        """Removes vectors from the index by their FAISS IDs."""
        if not faiss_ids: return
        ids_to_remove_array = np.array(faiss_ids, dtype=np.int64)
        self.index.remove_ids(ids_to_remove_array)
        
    def search(self, query_embedding: np.ndarray, k: int = 1) -> Tuple[np.ndarray, np.ndarray]:
        """
        Searches for the k-nearest neighbors.
        Returns:
            D: Distances (or similarities)
            I: Indices (FAISS IDs)
        """
        if query_embedding.ndim == 1:
            query_embedding = query_embedding[np.newaxis, :]
        if self.normalize:
            faiss.normalize_L2(query_embedding)
        D, I = self.index.search(query_embedding, k)
        return D[0], I[0]

class GaitGallery:
    """
    High-level manager for the Gait Database.
    Handles loading/saving to disk, updating embeddings with EMA,
    and searching for identities.
    """
    def __init__(self, config: GaitConfig):
        self.config = config
        self._identities: Dict[str, GaitIdentityData] = {}
        self.faiss_index = FaissIndexWrapper(dim=config.gallery.dim, metric=config.gallery.metric)
        
        if self.config.gallery.gallery_path.exists():
            self.load_gallery()
        else:
            logger.info("Gait gallery file not found, starting with empty gallery.")

    def save_gallery(self) -> None:
        """Persists the identity dictionary and FAISS counter to a pickle file."""
        try:
            self.config.gallery.gallery_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "identities": self._identities,
                "faiss_id_counter": self.faiss_index.current_faiss_id_counter
            }
            with open(self.config.gallery.gallery_path, "wb") as f:
                pickle.dump(state, f)
            logger.debug(f"Gait gallery saved to {self.config.gallery.gallery_path}")
        except Exception as e:
            logger.error(f"Error saving gait gallery: {e}")

    def load_gallery(self) -> None:
        """Loads the gallery from disk and rebuilds the FAISS index."""
        try:
            with open(self.config.gallery.gallery_path, "rb") as f:
                state = pickle.load(f)
                self._identities = state["identities"]
                self.faiss_index.current_faiss_id_counter = state["faiss_id_counter"]
            
            if self._identities:
                self.faiss_index.index.reset() # Reset index before rebuilding
                for identity_data in self._identities.values():
                    self.faiss_index.add(identity_data.ema_embedding, identity_data.faiss_id)
                logger.info(f"FAISS index rebuilt with {self.faiss_index.index.ntotal} embeddings.")
        except Exception as e:
            logger.error(f"Error loading gait gallery: {e}")
            self._identities = {}
            self.faiss_index.current_faiss_id_counter = 0

    def _update_ema_embedding(self, current_ema: np.ndarray, new_embedding: np.ndarray) -> np.ndarray:
        """
        Updates the stored embedding using Exponential Moving Average.
        This allows the model to adapt to slight changes in gait over time.
        """
        alpha = self.config.gallery.ema_alpha
        updated_ema = (1 - alpha) * current_ema + alpha * new_embedding
        norm = np.linalg.norm(updated_ema)
        return updated_ema / norm if norm > 0 else np.zeros_like(updated_ema)

    def add_gait_embedding(self, identity_id: str, new_embedding: np.ndarray, category: str = "resident", confirmed: bool = True) -> None:
        """
        Adds a new identity or updates an existing one.
        
        Args:
            identity_id: The unique name/ID of the person.
            new_embedding: The gait vector extracted from the video.
            category: 'resident', 'visitor', etc.
            confirmed: If True, updates the EMA model.
        """
        if identity_id not in self._identities:
            # NEW USER
            faiss_id = self.faiss_index._get_next_faiss_id()
            identity_data = GaitIdentityData(
                identity_id=identity_id,
                faiss_id=faiss_id,
                ema_embedding=new_embedding,
                raw_embeddings=[new_embedding],
                num_updates=1,
                category=category
            )
            self._identities[identity_id] = identity_data
            self.faiss_index.add(new_embedding, faiss_id)
            logger.info(f"Added new identity {identity_id} (ID: {faiss_id}).")
        else:
            # EXISTING USER
            identity_data = self._identities[identity_id]
            # Update category just in case
            identity_data.category = category 
            
            if confirmed:
                self.faiss_index.remove([identity_data.faiss_id])
                updated_ema = self._update_ema_embedding(identity_data.ema_embedding, new_embedding)
                identity_data.ema_embedding = updated_ema
                identity_data.raw_embeddings.append(new_embedding)
                identity_data.num_updates += 1
                self.faiss_index.add(updated_ema, identity_data.faiss_id)
                logger.info(f"Updated identity {identity_id} (Updates: {identity_data.num_updates}).")
        
        self.save_gallery()

    def search(self, query_embedding: np.ndarray) -> Tuple[Optional[str], Optional[float]]:
        """
        Searches for the closest identity in the gallery.
        Logs a ranking of the top-k matches for debugging purposes.
        
        Returns:
            (identity_id, confidence) if a valid match is found within thresholds.
            (None, None) otherwise.
        """
        if self.faiss_index.index.ntotal == 0:
            return None, None

        # Search up to 5 results
        k_search = min(5, self.faiss_index.index.ntotal)
        
        # NOTE: Wrapper returns 1D arrays (shape: (k,))
        sims, fids = self.faiss_index.search(query_embedding, k=k_search)

        logger.info(f"\n--- 📊 GAIT RANKING (Top {k_search}) ---")
        
        best_match_id = None
        best_confidence = 0.0
        match_found = False

        # Iterate through the results
        for rank, (sim, fid) in enumerate(zip(sims, fids)):
            if fid == -1: continue

            # 1. Find Identity ID from FAISS numeric ID
            match_id_str = None
            for id_str, identity_data in self._identities.items():
                if identity_data.faiss_id == fid:
                    match_id_str = id_str
                    break
            
            if match_id_str is None: continue

            # 2. Calculate Distance and Confidence
            if self.config.gallery.metric == "cosine" and self.faiss_index.normalize:
                min_distance = 1.0 - sim
            else:
                min_distance = sim

            confidence = 1.0 - min_distance
            
            # 3. Check Thresholds for logging
            status_icon = "❌"
            status_msg = "REJECTED"
            
            if min_distance <= self.config.thresholds.max_match_distance:
                status_icon = "✅"
                status_msg = "STRONG MATCH"
            elif min_distance <= self.config.thresholds.max_weak_match_distance:
                status_icon = "⚠️"
                status_msg = "WEAK MATCH"

            # 4. Print ranking log
            logger.info(f"#{rank+1}: {match_id_str:<20} | Conf: {confidence:.4f} (Dist: {min_distance:.4f}) | {status_icon} {status_msg}")

            # 5. Return Logic (Only rank 1 counts for decision)
            if rank == 0:
                if min_distance <= self.config.thresholds.max_weak_match_distance:
                    best_match_id = match_id_str
                    best_confidence = confidence
                    match_found = True

        logger.info("------------------------------------------\n")

        if match_found:
            return best_match_id, best_confidence
        
        return None, None

    # --- METHODS ADDED FOR CLI ---
    def list_persons(self) -> List[PersonSummary]:
        """Returns a list of all enrolled persons."""
        summary_list = []
        for pid, data in self._identities.items():
            summary_list.append(PersonSummary(
                person_id=pid,
                name=pid,
                category=data.category,
                num_templates=data.num_updates
            ))
        return summary_list

    def delete_person(self, identity_id: str) -> bool:
        """Deletes a person from the gallery by ID."""
        if identity_id in self._identities:
            data = self._identities[identity_id]
            self.faiss_index.remove([data.faiss_id])
            del self._identities[identity_id]
            self.save_gallery()
            return True
        return False
        
    def get_category(self, identity_id: str) -> str:
        """Retrieves the category (e.g., resident, visitor) of an identity."""
        if identity_id in self._identities:
            return self._identities[identity_id].category
        return "unknown"