"""
gait/gait_gallery.py - FAISS GALLERY & MATCHING LOGIC
"""
import numpy as np
from typing import Dict, Optional, Tuple, List
import logging
import pickle
from dataclasses import dataclass
from gait.config import GaitConfig
import faiss

logger = logging.getLogger(__name__)

@dataclass
class GaitIdentityData:
    """
    Data structure representing a unique identity in the gallery.
    
    Attributes:
        identity_id: The unique string ID (e.g., "Francesco").
        faiss_id: The integer ID used internally by FAISS.
        ema_embedding: The representative embedding vector (averaged over time).
        raw_embeddings: List of recent raw embeddings (optional history).
        num_updates: How many times this identity has been updated/seen.
        category: User category (e.g., "resident", "guest").
    """
    identity_id: str
    faiss_id: int
    ema_embedding: np.ndarray       
    raw_embeddings: List[np.ndarray] 
    num_updates: int = 0
    category: str = "resident"

@dataclass
class PersonSummary:
    """DTO for listing persons via CLI."""
    person_id: str
    name: str
    category: str
    num_templates: int

class FaissIndexWrapper:
    """
    Wrapper around the FAISS library to handle vector indexing and searching.
    
    Technical Note:
    We use 'IndexFlatIP' (Inner Product). 
    Since the embeddings are L2-normalized (length = 1), the Inner Product 
    is mathematically equivalent to Cosine Similarity.
    
    Distance = 1.0 - CosineSimilarity.
    """
    def __init__(self, dim: int):
        self.dim = dim
        self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))
        self.current_faiss_id_counter = 0

    def add(self, emb, fid):
        """Adds a normalized vector to the FAISS index with a specific ID."""
        if emb.ndim == 1:
            emb = emb[np.newaxis, :]
        
        # Copy to avoid modifying the original array reference
        emb_copy = emb.astype('float32').copy()
        
        # Ensure L2 Normalization (Critical for Cosine Similarity via IP)
        faiss.normalize_L2(emb_copy)
        self.index.add_with_ids(emb_copy, np.array([fid], dtype=np.int64))
        
    def search(self, query, k=1):
        """Searches for the k-nearest neighbors."""
        if query.ndim == 1:
            query = query[np.newaxis, :]
            
        query_copy = query.astype('float32').copy()
        faiss.normalize_L2(query_copy)
        
        # Returns (similarities, indices)
        return self.index.search(query_copy, k)

class GaitGallery:
    """
    Manages the database of known gait identities.
    Handles loading/saving, updating embeddings via EMA (Exponential Moving Average),
    and searching for matches with strict thresholding.
    """
    def __init__(self, config: GaitConfig):
        self.config = config
        self._identities: Dict[str, GaitIdentityData] = {}
        self.faiss_index = FaissIndexWrapper(config.gallery.dim)
        
        if config.gallery.gallery_path.exists():
            self.load_gallery()

    def save_gallery(self):
        """Persists the gallery state to disk using Pickle."""
        try:
            state = {
                "identities": self._identities, 
                "cnt": self.faiss_index.current_faiss_id_counter
            }
            with open(self.config.gallery.gallery_path, "wb") as f:
                pickle.dump(state, f)
            logger.debug("Gallery saved successfully.")
        except Exception as e:
            logger.error(f"Error saving gallery: {e}")

    def load_gallery(self):
        """Loads the gallery state and reconstructs the FAISS index."""
        try:
            with open(self.config.gallery.gallery_path, "rb") as f:
                state = pickle.load(f)
                self._identities = state["identities"]
                self.faiss_index.current_faiss_id_counter = state["cnt"]
            
            # Rebuild FAISS index from stored embeddings
            self.faiss_index.index.reset()
            for d in self._identities.values():
                self.faiss_index.add(d.ema_embedding, d.faiss_id)
            logger.info(f"Gallery loaded: {len(self._identities)} identities found.")
        except Exception:
            logger.warning("No existing gallery found or file corrupted. Starting fresh.")

    def _normalize_numpy(self, x):
        """Helper to normalize a numpy array to unit length (L2 norm)."""
        norm = np.linalg.norm(x)
        if norm > 1e-6:
            return x / norm
        return x

    def add_gait_embedding(self, identity_id, new_embedding, category="resident", confirmed=True):
        """
        Adds or Updates an identity.
        
        If the identity exists and 'confirmed' is True, it updates the stored embedding
        using Exponential Moving Average (EMA). This allows the system to adapt
        to slight changes in a person's gait over time.
        """
        if identity_id not in self._identities:
            # New Identity
            fid = self.faiss_index.current_faiss_id_counter
            self.faiss_index.current_faiss_id_counter += 1
            
            # Normalize before storing
            new_embedding = self._normalize_numpy(new_embedding)
            
            self._identities[identity_id] = GaitIdentityData(
                identity_id=identity_id, 
                faiss_id=fid, 
                ema_embedding=new_embedding, 
                raw_embeddings=[new_embedding], 
                num_updates=1, 
                category=category
            )
            self.faiss_index.add(new_embedding, fid)
        else:
            # Update Existing Identity
            d = self._identities[identity_id]
            d.category = category
            if confirmed:
                alpha = self.config.gallery.ema_alpha
                
                # EMA Update: (1 - alpha) * Old + alpha * New
                updated = (1 - alpha) * d.ema_embedding + alpha * new_embedding
                d.ema_embedding = self._normalize_numpy(updated)
                
                d.num_updates += 1
                
                # Update FAISS: Remove old vector, add new vector
                self.faiss_index.index.remove_ids(np.array([d.faiss_id], dtype=np.int64))
                self.faiss_index.add(d.ema_embedding, d.faiss_id)
        
        self.save_gallery()

    def search(self, query_embedding: np.ndarray) -> Tuple[Optional[str], Optional[float]]:
        """
        Searches the gallery for a match.
        
        STRICT RULES:
        - Returns (name, confidence) ONLY if similarity >= 0.80 (Distance <= 0.20).
        - Otherwise, it explicitly returns (None, 0.0).
        """
        total_people = self.faiss_index.index.ntotal
        if total_people == 0:
            return None, 0.0

        # Search for Top 3 candidates
        k_search = min(3, total_people)
        sims, fids = self.faiss_index.search(query_embedding, k=k_search)
        
        sims_row = sims[0]
        fids_row = fids[0]

        if fids_row[0] == -1:
            return None, 0.0

        # --- CANDIDATE RETRIEVAL ---
        best_pid = "Unknown"
        best_dist = 1.0
        best_sim = 0.0
        second_dist = 1.0
        
        print("\n🔍 --- LIVE SEARCH RANKING ---")
        for i in range(len(fids_row)):
            fid = fids_row[i]
            similarity = sims_row[i]
            distance = 1.0 - similarity
            
            # Retrieve String ID
            pid = next((k for k, v in self._identities.items() if v.faiss_id == fid), "Unknown")
            
            print(f"   #{i+1}: {pid:<15} | Sim: {similarity:.4f} | Dist: {distance:.4f}")
            
            if i == 0:
                best_pid = pid
                best_dist = distance
                best_sim = similarity
            
            if i == 1:
                second_dist = distance

        # --- DECISION LOGIC (STRICT FILTER) ---
        
        # 1. Threshold Check
        # Example: if max_match_distance is 0.20, then Similarity must be > 0.80
        limit = self.config.thresholds.max_match_distance
        
        if best_dist > limit:
            # Explicit Rejection Logic
            required_sim = 1.0 - limit
            print(f"❌ REJECTED: {best_pid} (Sim {best_sim:.4f} is too low. Required: > {required_sim:.2f})")
            return None, 0.0  # <--- Forces UI to show "Unknown"

        # 2. Margin Check (Optional)
        # Prevents ambiguity if the top 2 candidates are too close
        margin = 1.0
        if k_search > 1:
            margin = second_dist - best_dist
            
        if margin < self.config.thresholds.min_match_margin:
            print(f"⚠️ AMBIGUOUS: Margin too low ({margin:.3f}) between {best_pid} and 2nd place.")
            return None, 0.0

        # If we reach here, the match is valid
        print(f"✅ CONFIRMED: {best_pid} (Sim: {best_sim:.4f})")
        return best_pid, best_sim

    def list_persons(self) -> List[PersonSummary]:
        return [PersonSummary(k, k, v.category, v.num_updates) for k, v in self._identities.items()]

    def delete_person(self, pid: str) -> bool:
        if pid in self._identities:
            self.faiss_index.index.remove_ids(np.array([self._identities[pid].faiss_id], dtype=np.int64))
            del self._identities[pid]
            self.save_gallery()
            return True
        return False

    def get_category(self, pid: str) -> str:
        """Returns the category (e.g., 'resident', 'visitor') of an enrolled identity."""
        if pid in self._identities:
            return self._identities[pid].category
        return "unknown"