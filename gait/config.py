"""
gait/config.py

Central configuration for the Gait Recognition Route.

This module does NOT run heavy models; it only defines typed configuration
objects and a helper that builds a sane default GaitConfig, using the same
device-selection logic used in the rest of the system.

Other gait modules (detector, extractor, route, gallery, identity, etc.)
should depend ONLY on these dataclasses instead of hard-coding paths,
thresholds or device strings.
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
class GaitDeviceConfig:
    """
    Device configuration for all gait models.

    Attributes
    ----------
    device : str
        PyTorch device string ("cuda", "cpu", "mps", etc.).
    use_half : bool
        If True, models may run in FP16 on GPUs that support it.
    """
    device: str = "cpu"
    use_half: bool = False


@dataclass
class GaitModelConfig:
    """
    Identifiers / paths for gait models.

    Attributes
    ----------
    pose_model_name : str
        Name or path of the pose estimation model (e.g. "mediapipe_pose").
    gait_model_name : str
        Path or name of the gait embedding network (e.g. ONNX or PyTorch).
    """

    pose_model_name: str = "mediapipe_pose"
    gait_model_name: str = "gaitbase_v1.onnx"   # placeholder until you tell me real name


@dataclass
class GaitThresholdConfig:
    """
    Numeric thresholds used in gait recognition.
    """

    # Minimum visibility score of landmarks (0–1).
    min_visibility: float = 0.6

    # Minimum number of valid joints required to accept analysis.
    min_valid_joints: int = 10

    # Similarity thresholds for matching embeddings.
    max_match_distance: float = 0.8
    max_weak_match_distance: float = 0.95


@dataclass
class GaitRouteConfig:
    """
    Behaviour of the gait route.
    """

    process_every_n_frames: int = 3
    max_seconds_lookback: float = 3.0
    max_entries_per_track: int = 50


@dataclass
class GaitGalleryConfig:
    """
    Configuration for gait gallery storage.
    """

    dim: int = 256                   # typical gait embedding size
    metric: str = "cosine"
    gallery_path: Path = Path("data/gait_gallery.enc")
    encryption_key_env: str = "GAITGUARD_GAIT_KEY"


# ---------------------------------------------------------------------------
# Aggregate top-level config
# ---------------------------------------------------------------------------

@dataclass
class GaitConfig:
    """
    Aggregated configuration object for the entire gait route.
    """

    device: GaitDeviceConfig
    models: GaitModelConfig
    thresholds: GaitThresholdConfig
    route: GaitRouteConfig
    gallery: GaitGalleryConfig


# ---------------------------------------------------------------------------
# Factory for sane defaults
# ---------------------------------------------------------------------------

def default_gait_config(
    prefer_gpu: bool = True,
    base_dir: Optional[Path] = None,
) -> GaitConfig:
    """
    Build a default GaitConfig.

    Parameters
    ----------
    prefer_gpu : bool
        Try to use CUDA or other accelerator.
    base_dir : Optional[Path]
        Base directory for gallery path resolution.

    Returns
    -------
    GaitConfig
    """
    # device
    device_str, use_half = select_device(prefer_gpu=prefer_gpu)
    device_cfg = GaitDeviceConfig(device=device_str, use_half=use_half)

    # models
    model_cfg = GaitModelConfig(
        pose_model_name="mediapipe_pose",
        gait_model_name="gaitbase_v1.onnx",  # placeholder
    )

    # thresholds / route
    thresholds_cfg = GaitThresholdConfig()
    route_cfg = GaitRouteConfig()

    # gallery path resolution
    if base_dir is None:
        base_dir = Path(".")
    gallery_path = (base_dir / "data" / "gait_gallery.enc").resolve()

    gallery_cfg = GaitGalleryConfig(
        dim=256,
        metric="cosine",
        gallery_path=gallery_path,
        encryption_key_env="GAITGUARD_GAIT_KEY",
    )

    cfg = GaitConfig(
        device=device_cfg,
        models=model_cfg,
        thresholds=thresholds_cfg,
        route=route_cfg,
        gallery=gallery_cfg,
    )

    logger.info(
        "GaitConfig initialised | device=%s half=%s | gallery=%s",
        cfg.device.device,
        cfg.device.use_half,
        cfg.gallery.gallery_path,
    )

    return cfg
