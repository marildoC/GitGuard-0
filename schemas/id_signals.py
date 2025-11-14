from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class IdSignals:
    """
    All identity-related features for a given track.

    Embeddings are typically 1D NumPy arrays (e.g. shape (512,)).

    - track_id            : link to Tracklet.track_id
    - face_embedding      : face feature vector (or None if not available)
    - face_quality        : 0–1 quality score for face
    - gait_embedding      : gait feature vector
    - gait_quality        : 0–1 quality score for gait
    - appearance_embedding: clothing / color features
    - appearance_quality  : 0–1 quality score for appearance
    """
    track_id: int

    face_embedding: Optional[np.ndarray] = None
    face_quality: float = 0.0

    gait_embedding: Optional[np.ndarray] = None
    gait_quality: float = 0.0

    appearance_embedding: Optional[np.ndarray] = None
    appearance_quality: float = 0.0
