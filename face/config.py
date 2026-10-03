"""
face/config.py

Central configuration for the Phase-2A Face Route.

This module does **not** run any heavy models; it only defines
typed configuration objects and a helper to build a sane default
FaceConfig using the existing device-selection logic.

Other modules (detector_align, route, gallery, identity, etc.)
should **only** depend on these dataclasses instead of hard-coding
paths, thresholds, or device strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import logging

from core.device import select_device

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level configs
# ---------------------------------------------------------------------------


@dataclass
class FaceDeviceConfig:
    """
    Device configuration for all face models.

    Attributes
    ----------
    device : str
        Device string understood by PyTorch / InsightFace
        ("cuda", "cpu", "mps", "0", "0,1", ...).
    use_half : bool
        If True, models that support it may use FP16 on GPU.
        For safety we will normally keep face embeddings in FP32 and allow
        the detector part to use FP16 where it is safe.
    """

    device: str = "cpu"
    use_half: bool = False


@dataclass
class FaceModelConfig:
    """
    Model identifiers / paths for the face route.

    These are **names or paths** understood by InsightFace / your
    chosen backend. We keep them as strings so they can be either:

        - built-in InsightFace model packs (e.g. "buffalo_l"), or
        - explicit filesystem paths to ONNX / PyTorch weights.

    In the current design, `retinaface_name` is used as the *pack name*
    for InsightFace's FaceAnalysis (e.g. "buffalo_l"), which already
    includes detection + embedding. `arcface_name` is kept for future
    use if you ever want a separate embedding model, but is not used
    by the buffalo_l-based pipeline.

    Attributes
    ----------
    retinaface_name : str
        InsightFace pack name (e.g. "buffalo_l") or detector model name/path.
    arcface_name : str
        Embedding model name or path (512-D ArcFace-style).
        Currently unused when using buffalo_l, but kept for compatibility.
    """

    # For the buffalo_l pipeline, this acts as the FaceAnalysis pack name.
    retinaface_name: str = "buffalo_l"

    # Kept for future extensibility; not used by buffalo_l-based flow.
    arcface_name: str = "arcface_r100_v1"


@dataclass
class FaceThresholdConfig:
    """
    All numeric thresholds used in the face pipeline.

    You can tune these later without touching model code.
    """

    # ----- geometric / size thresholds -----
    # Minimum detected face height (in pixels) for a candidate face.
    min_face_height_px: int = 40          # discard tinier faces

    # Person bbox (from Phase-1) must be at least this tall before we even
    # try to run face detection to avoid wasting compute on tiny people.
    min_box_height_for_face_px: int = 80

    # ----- detector / quality thresholds -----
    # Raw InsightFace detection confidence.
    min_det_score: float = 0.6

    # Final q_face in [0,1] required before we accept a face embedding
    # and push it into the per-track buffer / gallery search.
    min_quality_for_embed: float = 0.6

    # Pose limits (beyond these angles, quality is strongly penalised).
    max_yaw_deg: float = 40.0   # left/right turn
    max_pitch_deg: float = 30.0 # up/down tilt

    # Blur / sharpness heuristic (Laplacian-of-variance normalised to [0,1]).
    # Below this we treat the face as too blurry for reliable matching.
    min_sharpness: float = 0.25

    # ----- gallery / matching thresholds -----
    # Distances here refer to **cosine distance** if you use cos similarity,
    # or L2 distances if you choose that metric. We keep names generic.
    #
    # In the default setup we use cosine similarity via FAISS IndexFlatIP,
    # and convert it to a "distance" for thresholds: d = 1 - sim.
    max_match_distance: float = 0.9       # below → strong match
    max_weak_match_distance: float = 1.0  # reserved band for future use
    # everything above max_weak_match_distance is treated as UNKNOWN


@dataclass
class FaceRouteConfig:
    """
    Behaviour of the FaceRoute (how often and how far back we look).

    Attributes
    ----------
    max_seconds_lookback : float
        Time window (in seconds) to search for the best recent face
        per track. Typical value: 1–2 seconds.
    max_entries_per_track : int
        Hard cap on how many face entries we keep for one track.
    process_every_n_frames : int
        Run heavy face detection at most once every N frames per track.
        Example: N=5 on a 25–30 FPS stream gives ~5–6 FPS on the face route.
    """

    max_seconds_lookback: float = 2.0
    max_entries_per_track: int = 30
    process_every_n_frames: int = 5


@dataclass
class FaceGalleryConfig:
    """
    Configuration for the face gallery (FAISS + encrypted storage).

    Attributes
    ----------
    dim : int
        Embedding dimensionality (ArcFace / buffalo_l is usually 512).
    metric : str
        Similarity metric for FAISS ("cosine" or "l2").
    gallery_path : Path
        Path to the encrypted gallery file on disk.
    encryption_key_env : str
        Name of the environment variable containing the AES key
        (base64 or hex encoded) used by crypto.py.
    """

    dim: int = 512
    metric: str = "cosine"
    gallery_path: Path = Path("data/face_gallery.enc")
    encryption_key_env: str = "GAITGUARD_FACE_KEY"


# ---------------------------------------------------------------------------
# Aggregate top-level config
# ---------------------------------------------------------------------------


@dataclass
class FaceConfig:
    """
    Aggregated configuration object for the entire face route.

    This is what higher-level components (FaceRoute, FaceGallery,
    FaceIdentityEngine) should receive in their constructors.
    """

    device: FaceDeviceConfig
    models: FaceModelConfig
    thresholds: FaceThresholdConfig
    route: FaceRouteConfig
    gallery: FaceGalleryConfig


# ---------------------------------------------------------------------------
# Factory for a sane default configuration
# ---------------------------------------------------------------------------


def default_face_config(
    prefer_gpu: bool = True,
    base_dir: Optional[Path] = None,
) -> FaceConfig:
    """
    Build a default FaceConfig.

    Parameters
    ----------
    prefer_gpu : bool
        If True, try to use CUDA (or other accelerator) when available.
        Falls back to CPU automatically.
    base_dir : Optional[Path]
        Base directory for derived paths (e.g. gallery file). If None,
        current working directory is used.

    Returns
    -------
    FaceConfig
        Ready-to-use configuration object.
    """
    # Decide on device (reuses the same logic as the rest of the system)
    device_str, use_half = select_device(prefer_gpu=prefer_gpu)
    device_cfg = FaceDeviceConfig(device=device_str, use_half=use_half)

    # Model configuration – we use a single InsightFace pack (buffalo_l)
    # that already includes detection + embedding. `arcface_name` is kept
    # for potential future use but is not required for the buffalo_l flow.
    model_cfg = FaceModelConfig(
        retinaface_name="buffalo_l",
        arcface_name="arcface_r100_v1",
    )

    thresholds_cfg = FaceThresholdConfig()
    route_cfg = FaceRouteConfig()

    # Resolve gallery path
    if base_dir is None:
        base_dir = Path(".")
    gallery_path = (base_dir / "data" / "face_gallery.enc").resolve()

    gallery_cfg = FaceGalleryConfig(
        dim=512,
        metric="cosine",
        gallery_path=gallery_path,
        encryption_key_env="GAITGUARD_FACE_KEY",
    )

    cfg = FaceConfig(
        device=device_cfg,
        models=model_cfg,
        thresholds=thresholds_cfg,
        route=route_cfg,
        gallery=gallery_cfg,
    )

    logger.info(
        "FaceConfig initialised | device=%s half=%s | gallery=%s",
        cfg.device.device,
        cfg.device.use_half,
        cfg.gallery.gallery_path,
    )

    return cfg
