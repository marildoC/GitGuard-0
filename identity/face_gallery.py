# identity/face_gallery.py
# Encrypted face gallery with FAISS/NumPy search for identity matching.

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np

from face.config import FaceGalleryConfig, default_face_config
from identity.crypto import CryptoConfig, encrypt_json, decrypt_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional FAISS backend
# ---------------------------------------------------------------------------

try:
    import faiss  # type: ignore[import]
except Exception as exc:  # pragma: no cover
    faiss = None  # type: ignore[assignment]
    _faiss_error = exc
else:
    _faiss_error = None

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

Category = Literal["resident", "visitor", "watchlist", "unknown"]


@dataclass
class FaceTemplate:
    """
    One face template (single embedding) for a person.
    """

    embedding: np.ndarray
    condition: str = "neutral"  # e.g. neutral / glasses / cap / etc.
    created_at: float = field(default_factory=lambda: time.time())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PersonEntry:
    """
    Full person record stored in the gallery.
    """

    person_id: str
    category: Category = "resident"
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    templates: List[FaceTemplate] = field(default_factory=list)


@dataclass
class PersonSummary:
    """
    Lightweight person view for CLI / UI listing.

    We do NOT expose embeddings here, only high-level info.
    """

    person_id: str
    category: Category
    name: Optional[str]
    num_templates: int


@dataclass
class SearchResult:
    """
    Result of a gallery search for a single query embedding.
    """

    person_id: str
    distance: float
    score: float
    condition: str
    category: Category
    name: Optional[str]


# ---------------------------------------------------------------------------
# FaceGallery
# ---------------------------------------------------------------------------


class FaceGallery:
    """
    Encrypted on-disk + in-memory face gallery with FAISS/NumPy search.

    Responsibilities:
      - Hold PersonEntry objects with one or more FaceTemplate embeddings.
      - Save/load an encrypted JSON representation to disk.
      - Build a FAISS (or NumPy) index for fast nearest-neighbour search.
    """

    def __init__(
        self,
        cfg: Optional[FaceGalleryConfig] = None,
        crypto_cfg: Optional[CryptoConfig] = None,
    ) -> None:
        # Configuration (dim, metric, path, env var)
        if cfg is None:
            cfg = default_face_config().gallery
        self.cfg = cfg
        self.crypto_cfg = crypto_cfg or CryptoConfig()

        # In-memory state
        self._persons: Dict[str, PersonEntry] = {}
        self._next_person_index: int = 1

        # Search index state
        self._embeddings: Optional[np.ndarray] = None
        self._index_meta: List[Tuple[str, int]] = []  # (person_id, template_idx)
        self._faiss_index: Any = None
        self._index_dirty: bool = True

        # Auto-load existing gallery if present
        if self.cfg.gallery_path:
            self.load_if_exists()

    # ------------------------------------------------------------------ #
    # Enrollment / mutation                                              #
    # ------------------------------------------------------------------ #

    def enroll_person(
        self,
        templates: Optional[Sequence[np.ndarray]] = None,
        *,
        conditions: Optional[Sequence[str]] = None,
        category: Category = "resident",
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        # ---- backward-compat keywords used by enrollment_cli ----
        embeddings: Optional[np.ndarray] = None,
        condition: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        """
        Enroll a new person with one or more face template embeddings.

        Preferred usage (new):
            enroll_person(
                templates=[emb1, emb2, ...],
                conditions=["neutral", "glasses", ...],
                category="resident",
                name="Alice",
                metadata={...},
            )

        Backward-compatible usage (old, from enrollment_cli):
            enroll_person(
                embeddings=emb_array,         # shape (N, dim)
                category="resident",
                name="Alice",
                condition="neutral",
                notes="TESTING",
            )
        """
        # ---- Backward-compatibility shim ----
        # If caller used `embeddings=` instead of `templates=`
        if templates is None and embeddings is not None:
            arr = np.asarray(embeddings, dtype=np.float32)
            if arr.ndim == 1:
                # single embedding (dim,) -> (1, dim)
                arr = arr.reshape(1, -1)
            if arr.ndim != 2:
                raise ValueError(
                    f"`embeddings` must be 1D or 2D array, got shape {arr.shape}"
                )
            # Convert to a list of 1D vectors
            templates = [row.copy() for row in arr]

            # If no explicit conditions list is provided, but a single
            # `condition` string is, replicate it for all templates
            if conditions is None and condition is not None:
                conditions = [condition] * len(templates)

            # Attach notes into metadata if provided
            base_meta = dict(metadata or {})
            if notes:
                base_meta.setdefault("notes", notes)
            metadata = base_meta

        # ---- Normal argument validation from here on ----
        if not templates:
            raise ValueError("enroll_person requires at least one template embedding.")

        person_id = self._allocate_person_id()
        entry = PersonEntry(
            person_id=person_id,
            category=category,
            name=name,
            metadata=metadata or {},
        )
        # Register the person before adding templates so we do not lose them.
        self._persons[person_id] = entry

        # If still no conditions list, fall back to neutral for everyone
        if conditions is None:
            conditions = ["neutral"] * len(templates)
        if len(conditions) != len(templates):
            raise ValueError("conditions length must match templates length.")

        for emb, cond in zip(templates, conditions):
            emb_valid = self._validate_embedding(emb)
            tmpl = FaceTemplate(embedding=emb_valid, condition=cond, metadata={})
            entry.templates.append(tmpl)

        self._index_dirty = True
        logger.info(
            "Enrolled new person %s (category=%s, templates=%d).",
            person_id,
            category,
            len(templates),
        )
        return person_id


    def add_template(
        self,
        person_id: str,
        embedding: np.ndarray,
        *,
        condition: str = "neutral",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Add a new template to an existing (or new) person.
        """
        if person_id not in self._persons:
            self._persons[person_id] = PersonEntry(person_id=person_id)

        emb = self._validate_embedding(embedding)
        tmpl = FaceTemplate(embedding=emb, condition=condition, metadata=metadata or {})
        self._persons[person_id].templates.append(tmpl)
        self._index_dirty = True

    def delete_person(self, person_id: str) -> bool:
        """
        Delete a person from the gallery.

        Returns
        -------
        bool
            True if the person existed and was deleted, False otherwise.
        """
        if person_id in self._persons:
            del self._persons[person_id]
            self._index_dirty = True
            return True
        return False

    def update_metadata(
        self,
        person_id: str,
        *,
        category: Optional[Category] = None,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update high-level metadata for a person.
        """
        p = self._persons.get(person_id)
        if p is None:
            raise KeyError(f"Person '{person_id}' not found.")
        if category is not None:
            p.category = category
        if name is not None:
            p.name = name
        if metadata is not None:
            p.metadata.update(metadata)

    # ------------------------------------------------------------------ #
    # Search                                                             #
    # ------------------------------------------------------------------ #

    def search_best(
        self,
        embedding: np.ndarray,
        k: int = 5,
    ) -> Optional[SearchResult]:
        """
        Search the gallery for the closest match to the given embedding.

        Returns the best SearchResult or None if gallery is empty.
        """
        emb = self._validate_embedding(embedding, normalize=True)
        self._ensure_index()

        if self._embeddings is None or self._embeddings.shape[0] == 0:
            return None

        k = max(1, min(k, self._embeddings.shape[0]))

        if self._faiss_index is not None:
            q = emb.reshape(1, -1)
            if self.cfg.metric == "cosine":
                sims, idxs = self._faiss_index.search(q, k)
                sims = sims[0]
                idxs = idxs[0]
                best_idx = int(idxs[0])
                sim = float(sims[0])
                distance = 1.0 - sim
            else:  # l2
                dists, idxs = self._faiss_index.search(q, k)
                dists = dists[0]
                idxs = idxs[0]
                best_idx = int(idxs[0])
                distance = float(dists[0])
        else:
            # NumPy fallback
            E = self._embeddings  # type: ignore[assignment]
            if self.cfg.metric == "cosine":
                sims = (E @ emb.reshape(-1, 1)).reshape(-1)
                best_idx = int(np.argmax(sims))
                sim = float(sims[best_idx])
                distance = 1.0 - sim
            else:
                diffs = E - emb.reshape(1, -1)
                dists = np.sum(diffs * diffs, axis=1)
                best_idx = int(np.argmin(dists))
                distance = float(dists[best_idx])

        person_id, tmpl_idx = self._index_meta[best_idx]
        person = self._persons[person_id]
        tmpl = person.templates[tmpl_idx]

        if self.cfg.metric == "cosine":
            score = max(0.0, 1.0 - distance)
        else:
            score = 1.0 / (1.0 + distance)

        return SearchResult(
            person_id=person_id,
            distance=distance,
            score=score,
            condition=tmpl.condition,
            category=person.category,
            name=person.name,
        )

    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #

    def save(self, path: Optional[Path] = None) -> None:
        """
        Encrypt and save the gallery to disk.
        """
        if path is None:
            path = self.cfg.gallery_path
        path = Path(path)

        data = self._to_serializable()
        blob = encrypt_json(
            data,
            env_var=self.crypto_cfg.env_var,
        )

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("wb") as f:
            f.write(blob)
        os.replace(tmp_path, path)
        logger.info("Face gallery saved to %s (persons=%d).", path, len(self._persons))

    def load(self, path: Optional[Path] = None) -> None:
        """
        Load and decrypt the gallery from disk.
        """
        if path is None:
            path = self.cfg.gallery_path
        path = Path(path)

        with path.open("rb") as f:
            blob = f.read()

        data = decrypt_json(
            blob,
            env_var=self.crypto_cfg.env_var,
        )
        self._from_serializable(data)
        self._index_dirty = True
        logger.info("Face gallery loaded from %s (persons=%d).", path, len(self._persons))

    def load_if_exists(self, path: Optional[Path] = None) -> None:
        """
        Load the gallery if the file exists, else do nothing.
        """
        if path is None:
            path = self.cfg.gallery_path
        path = Path(path)
        if path.exists():
            try:
                self.load(path)
            except Exception as exc:
                logger.error("Failed to load face gallery from %s: %s", path, exc)

    # ------------------------------------------------------------------ #
    # Introspection / helpers                                            #
    # ------------------------------------------------------------------ #

    @property
    def persons(self) -> Dict[str, PersonEntry]:
        """
        Direct access to full person entries (for advanced use).
        """
        return self._persons

    def list_persons(self) -> List[PersonSummary]:
        """
        Return a list of PersonSummary for CLI / UI listing.

        This is what identity.enrollment_cli expects.
        """
        summaries: List[PersonSummary] = []
        for person_id, entry in self._persons.items():
            summaries.append(
                PersonSummary(
                    person_id=person_id,
                    category=entry.category,
                    name=entry.name,
                    num_templates=len(entry.templates),
                )
            )
        return summaries

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _allocate_person_id(self) -> str:
        """
        Allocate a new unique person_id of the form 'p_0001'.
        """
        while True:
            pid = f"p_{self._next_person_index:04d}"
            self._next_person_index += 1
            if pid not in self._persons:
                return pid

    def _validate_embedding(
        self,
        embedding: np.ndarray,
        *,
        normalize: bool = False,
    ) -> np.ndarray:
        """
        Ensure the embedding is a float32 1-D vector of the right dim,
        optionally L2-normalised.
        """
        emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if emb.size != self.cfg.dim:
            raise ValueError(
                f"Embedding dim mismatch: expected {self.cfg.dim}, got {emb.size}."
            )
        if normalize:
            norm = float(np.linalg.norm(emb))
            if norm > 1e-6:
                emb /= norm
            else:
                emb[:] = 0.0
        return emb

    # ------------------------------------------------------------------ #
    # Index building                                                     #
    # ------------------------------------------------------------------ #

    def _ensure_index(self) -> None:
        if not self._index_dirty:
            return
        self._rebuild_index()
        self._index_dirty = False

    def _rebuild_index(self) -> None:
        """
        Build or rebuild the FAISS/NumPy index from all person templates.
        """
        all_embs: List[np.ndarray] = []
        meta: List[Tuple[str, int]] = []
        for person_id, person in self._persons.items():
            for idx, tmpl in enumerate(person.templates):
                all_embs.append(self._validate_embedding(tmpl.embedding, normalize=True))
                meta.append((person_id, idx))

        if not all_embs:
            self._embeddings = None
            self._index_meta = []
            self._faiss_index = None
            return

        self._embeddings = np.stack(all_embs, axis=0)
        self._index_meta = meta

        if faiss is None:
            if _faiss_error is not None:
                logger.warning(
                    "FAISS not available (%s). Using NumPy search for face gallery.",
                    _faiss_error,
                )
            self._faiss_index = None
            return

        dim = self.cfg.dim
        if self.cfg.metric == "cosine":
            index = faiss.IndexFlatIP(dim)
        else:
            index = faiss.IndexFlatL2(dim)

        index.add(self._embeddings.astype(np.float32))
        self._faiss_index = index
        logger.info("Face gallery FAISS index rebuilt (templates=%d).", self._embeddings.shape[0])

    # ------------------------------------------------------------------ #
    # Serialization helpers                                              #
    # ------------------------------------------------------------------ #

    def _to_serializable(self) -> Dict[str, Any]:
        """
        Convert in-memory gallery state to a plain-JSON-serialisable dict.
        """
        persons_data: List[Dict[str, Any]] = []
        max_pid = 0

        for p in self._persons.values():
            # Track the largest numeric suffix for next_person_index
            try:
                n = int(p.person_id.split("_")[-1])
                max_pid = max(max_pid, n)
            except Exception:
                pass

            t_list: List[Dict[str, Any]] = []
            for t in p.templates:
                t_list.append(
                    {
                        "condition": t.condition,
                        "created_at": t.created_at,
                        "metadata": t.metadata,
                        "embedding": t.embedding.astype(float).tolist(),
                    }
                )

            persons_data.append(
                {
                    "person_id": p.person_id,
                    "category": p.category,
                    "name": p.name,
                    "metadata": p.metadata,
                    "templates": t_list,
                }
            )

        # Ensure next_person_index is ahead of the largest existing ID
        self._next_person_index = max(max_pid + 1, self._next_person_index)

        return {
            "version": 1,
            "dim": self.cfg.dim,
            "metric": self.cfg.metric,
            "persons": persons_data,
        }

    def _from_serializable(self, data: Dict[str, Any]) -> None:
        """
        Restore gallery state from a JSON-like dict.
        """
        if data.get("dim") != self.cfg.dim:
            logger.warning(
                "Loaded gallery dim=%s differs from config dim=%s.",
                data.get("dim"),
                self.cfg.dim,
            )

        self._persons.clear()
        persons_data = data.get("persons", []) or []

        max_pid = 0

        for p_data in persons_data:
            pid = str(p_data["person_id"])
            entry = PersonEntry(
                person_id=pid,
                category=p_data.get("category", "resident"),
                name=p_data.get("name"),
                metadata=p_data.get("metadata", {}) or {},
            )

            t_list = p_data.get("templates", []) or []
            for t_data in t_list:
                emb = np.asarray(t_data["embedding"], dtype=np.float32).reshape(-1)
                tmpl = FaceTemplate(
                    embedding=emb,
                    condition=t_data.get("condition", "neutral"),
                    created_at=float(t_data.get("created_at", time.time())),
                    metadata=t_data.get("metadata", {}) or {},
                )
                entry.templates.append(tmpl)

            self._persons[pid] = entry

            # Track numeric suffix
            try:
                n = int(pid.split("_")[-1])
                max_pid = max(max_pid, n)
            except Exception:
                pass

        # Initialise next_person_index after load
        self._next_person_index = max(max_pid + 1, 1)
        self._index_dirty = True
