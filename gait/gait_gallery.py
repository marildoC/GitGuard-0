import numpy as np
import logging
import pickle
import faiss
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass
from gait.config import GaitConfig

logger = logging.getLogger(__name__)

@dataclass
class GaitIdentityData:
    """Storage structure for an enrolled individual's gait data."""
    identity_id: str
    faiss_id: int
    ema_embedding: np.ndarray       # Moving average template
    raw_embeddings: List[np.ndarray] # Historical signatures
    num_updates: int = 0
    category: str = "resident"

@dataclass
class PersonSummary:
    """DTO for identity management via CLI."""
    person_id: str
    name: str
    category: str
    num_templates: int

class FaissIndexWrapper:
    """Utility wrapper for FAISS vector operations (L2 or Cosine)."""
    def __init__(self, dim: int, metric: str = "cosine"):
        self.dim = dim
        self.metric = metric
        if metric == "cosine":
            # Inner Product su vettori normalizzati = Cosine Similarity
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))
            self.normalize = True
        else:
            self.index = faiss.IndexIDMap(faiss.IndexFlatL2(dim))
            self.normalize = False
        self.current_faiss_id_counter = 0

    def _get_next_faiss_id(self) -> int:
        new_id = self.current_faiss_id_counter
        self.current_faiss_id_counter += 1
        return new_id
    
    def add(self, embeddings: np.ndarray, faiss_id: int):
        """Inserts a vector into the index; normalizes for cosine similarity if required."""
        if embeddings.ndim == 1: embeddings = embeddings[np.newaxis, :]
        
        # Copia e converti in float32 per FAISS
        emb_copy = embeddings.astype('float32').copy()
        
        if self.normalize: 
            faiss.normalize_L2(emb_copy)
            
        ids = np.array([faiss_id] * emb_copy.shape[0], dtype=np.int64)
        self.index.add_with_ids(emb_copy, ids)
        
    def remove(self, faiss_ids: List[int]):
        """Removes specific IDs from the index."""
        if faiss_ids: self.index.remove_ids(np.array(faiss_ids, dtype=np.int64))
        
    def search(self, query: np.ndarray, k: int = 1) -> Tuple[np.ndarray, np.ndarray]:
        """Performs a vector search and returns distances and FAISS IDs."""
        if query.ndim == 1: query = query[np.newaxis, :]
        
        query_copy = query.astype('float32').copy()
        
        if self.normalize: 
            faiss.normalize_L2(query_copy)
            
        D, I = self.index.search(query_copy, k)
        return D[0], I[0]

class GaitGallery:
    """High-level manager for identity enrollment and recognition."""
    def __init__(self, config: GaitConfig):
        self.config = config
        self._identities: Dict[str, GaitIdentityData] = {}
        self.faiss_index = FaissIndexWrapper(dim=config.gallery.dim, metric=config.gallery.metric)
        self.load_gallery()

    def save_gallery(self):
        """Serializes the identity database to disk."""
        try:
            self.config.gallery.gallery_path.parent.mkdir(parents=True, exist_ok=True)
            state = {"identities": self._identities, "faiss_id_counter": self.faiss_index.current_faiss_id_counter}
            with open(self.config.gallery.gallery_path, "wb") as f:
                pickle.dump(state, f)
        except Exception as e:
            logger.error(f"Failed to save gallery: {e}")

    def load_gallery(self):
        """Loads identity data and rebuilds the FAISS index."""
        if not self.config.gallery.gallery_path.exists(): return
        try:
            with open(self.config.gallery.gallery_path, "rb") as f:
                state = pickle.load(f)
                self._identities = state["identities"]
                self.faiss_index.current_faiss_id_counter = state["faiss_id_counter"]
            
            # Ricostruisce l'indice FAISS
            self.faiss_index.index.reset()
            for data in self._identities.values():
                self.faiss_index.add(data.ema_embedding, data.faiss_id)
            logger.info(f"Gallery loaded: {len(self._identities)} identities found.")
        except Exception as e:
            logger.error(f"Failed to load gallery: {e}")

    def _update_ema_embedding(self, current: np.ndarray, new_emb: np.ndarray) -> np.ndarray:
        """Applies Exponential Moving Average to update the person's signature template."""
        alpha = self.config.gallery.ema_alpha
        updated = (1 - alpha) * current + alpha * new_emb
        norm = np.linalg.norm(updated)
        return updated / norm if norm > 0 else np.zeros_like(updated)

    def add_gait_embedding(self, identity_id: str, embedding: np.ndarray, category: str = "resident", confirmed: bool = True):
        """Enrolls a new person or updates an existing identity template."""
        if identity_id not in self._identities:
            fid = self.faiss_index._get_next_faiss_id()
            data = GaitIdentityData(identity_id, fid, embedding, [embedding], 1, category)
            self._identities[identity_id] = data
            self.faiss_index.add(embedding, fid)
        elif confirmed:
            data = self._identities[identity_id]
            # Rimuovi vecchio vettore
            self.faiss_index.remove([data.faiss_id])
            
            # Aggiorna EMA
            data.ema_embedding = self._update_ema_embedding(data.ema_embedding, embedding)
            data.raw_embeddings.append(embedding)
            data.num_updates += 1
            
            # Aggiungi nuovo vettore aggiornato
            self.faiss_index.add(data.ema_embedding, data.faiss_id)
        
        self.save_gallery()

    def search(self, query_embedding: np.ndarray) -> Tuple[Optional[str], Optional[float]]:
        """Identifies the closest match in the database and prints a live ranking."""
        if self.faiss_index.index.ntotal == 0: return None, 0.0

        k_search = min(3, self.faiss_index.index.ntotal)
        sims, fids = self.faiss_index.search(query_embedding, k=k_search)

        print(f"\n🔍 LIVE RANKING:")
        match_id, match_conf = None, 0.0

        for rank, (sim, fid) in enumerate(zip(sims, fids)):
            if fid == -1: continue
            
            # Map FAISS ID back to identity string
            id_str = next((k for k, v in self._identities.items() if v.faiss_id == fid), None)
            if not id_str: continue

            distance = 1.0 - sim if self.faiss_index.normalize else sim
            
            # Visualizzazione stato
            status = "✅ MATCH" if distance <= self.config.thresholds.max_match_distance else "❌ NO   "
            print(f"   #{rank+1}: {id_str:<15} | Dist: {distance:.4f} | {status}")

            # Logica di selezione del migliore
            if rank == 0:
                if distance <= self.config.thresholds.max_match_distance:
                    match_id = id_str
                    match_conf = 1.0 - distance
                else:
                    # Se il primo non passa la soglia, è sconosciuto
                    pass

        return match_id, match_conf

    def list_persons(self) -> List[PersonSummary]:
        return [PersonSummary(pid, pid, d.category, d.num_updates) for pid, d in self._identities.items()]

    def delete_person(self, identity_id: str) -> bool:
        if identity_id in self._identities:
            self.faiss_index.remove([self._identities[identity_id].faiss_id])
            del self._identities[identity_id]
            self.save_gallery()
            return True
        return False

    # === METODO CHE MANCAVA ===
    def get_category(self, identity_id: str) -> str:
        """Returns the category (e.g., 'resident', 'visitor') of an enrolled identity."""
        if identity_id in self._identities:
            return self._identities[identity_id].category
        return "unknown"