# face/embedder.py
# Lightweight compatibility wrapper for face embeddings.
#
# In the original design, this module loaded a separate ArcFace model via
# insightface.model_zoo.get_model and produced embeddings from aligned
# 112x112 face crops.
#
# After migrating to InsightFace's "buffalo_l" pack, detection and
# embedding are both provided by FaceAnalysis inside face/detector_align.py.
#
# This module is now a small helper that:
#   - keeps the FaceEmbedder API available so old imports don't break,
#   - normalises and validates 1-D embedding vectors,
#   - explicitly refuses to embed raw images (to avoid silent misuse).
#
# New code should obtain embeddings from FaceDetectorAligner (buffalo_l)
# and NOT rely on this class for heavy model inference.

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

import numpy as np

from .config import FaceConfig, default_face_config

logger = logging.getLogger(__name__)


class FaceEmbedder:
    """
    Compatibility / utility wrapper for face embeddings.

    Responsibilities now:

    - Provide a stable .dim property (usually 512).
    - Provide embed(...) / embed_many(...) that accept *embedding-like*
      inputs (1-D arrays) and:
        * cast to float32,
        * flatten to 1-D,
        * L2-normalise,
        * adjust dim if necessary (with a warning).

    It NO LONGER performs model inference on raw images. If you pass in
    an HxWx3 array, it will raise a RuntimeError and remind you that
    embeddings must come from FaceDetectorAligner (buffalo_l).
    """

    def __init__(self, cfg: Optional[FaceConfig] = None) -> None:
        self.cfg = cfg or default_face_config()
        self._dim = int(self.cfg.gallery.dim)

        logger.info(
            "FaceEmbedder initialised in passthrough mode "
            "(expects precomputed embeddings, dim=%d).",
            self._dim,
        )

    # ------------------------------------------------------------------ #
    # Properties                                                         #
    # ------------------------------------------------------------------ #

    @property
    def dim(self) -> int:
        """
        Current expected embedding dimensionality.
        """
        return self._dim

    # ------------------------------------------------------------------ #
    # Core helpers                                                       #
    # ------------------------------------------------------------------ #

    def warmup(self) -> None:
        """
        Kept for API compatibility. Does nothing in passthrough mode.
        """
        return

    def _ensure_embedding_1d(self, vector: np.ndarray) -> np.ndarray:
        """
        Ensure vector is a 1-D float32 embedding, L2-normalised.

        If the size doesn't match cfg.gallery.dim, we log a warning and
        update self._dim to the new observed size, then continue.
        """
        emb = np.asarray(vector, dtype=np.float32).reshape(-1)

        if emb.size != self._dim:
            logger.warning(
                "FaceEmbedder received embedding with dim=%d, expected %d. "
                "Updating internal dim to %d.",
                emb.size,
                self._dim,
                emb.size,
            )
            self._dim = emb.size

        norm = float(np.linalg.norm(emb))
        if norm > 1e-6:
            emb /= norm
        else:
            emb[:] = 0.0

        return emb

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def embed(self, vector_or_image: np.ndarray) -> np.ndarray:
        """
        Normalise a precomputed embedding.

        Parameters
        ----------
        vector_or_image : np.ndarray
            Expected to be a 1-D or (N, 1)/(1, N) array containing a
            face embedding. If an HxWx3 image is passed, this method
            will raise an error because model inference is no longer
            supported here.

        Returns
        -------
        np.ndarray
            1-D float32, L2-normalised embedding.
        """
        arr = np.asarray(vector_or_image)

        # If it's clearly an image (HxWx3), refuse and explain:
        if arr.ndim == 3 and arr.shape[2] == 3:
            raise RuntimeError(
                "FaceEmbedder.embed no longer supports raw image inputs. "
                "Embeddings must be produced by FaceDetectorAligner "
                "(buffalo_l) and then passed here only if you need "
                "normalisation."
            )

        # Treat anything 1-D or 'vector-shaped' as an embedding.
        return self._ensure_embedding_1d(arr)

    def embed_many(self, vectors: Iterable[np.ndarray]) -> np.ndarray:
        """
        Normalise multiple precomputed embeddings.

        Parameters
        ----------
        vectors : Iterable[np.ndarray]
            Iterable of embedding-like arrays (1-D).

        Returns
        -------
        np.ndarray
            Array of shape (num_vectors, dim), each row L2-normalised.
            If no vectors are provided, returns an empty array with
            shape (0, dim).
        """
        embs: List[np.ndarray] = []

        for v in vectors:
            try:
                embs.append(self.embed(v))
            except Exception as exc:
                logger.warning(
                    "Failed to normalise one embedding in embed_many: %s", exc
                )
                # Fill with zeros as a safe fallback for that position.
                embs.append(np.zeros(self._dim, dtype=np.float32))

        if not embs:
            return np.zeros((0, self._dim), dtype=np.float32)

        return np.stack(embs, axis=0)
