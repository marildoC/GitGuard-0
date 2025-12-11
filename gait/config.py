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
import torch
from core.device import select_device as core_select_device # Renamed to avoid conflict

logger = logging.getLogger(__name__)

def select_device(prefer_gpu: bool = True):
    """
    Locally defined device selection helper.

    How it works:
    Checks if GPU is requested and available via PyTorch.
    
    Returns:
        tuple: (device_string, use_half_precision_bool)
        e.g., ("cuda", True) or ("cpu", False)
    """
    if prefer_gpu and torch.cuda.is_available():
        return "cuda", True
    return "cpu", False


# ---------------------------------------------------------------------------
# Low-level configs
# ---------------------------------------------------------------------------

@dataclass
class GaitDeviceConfig:
    """
    Device configuration for all gait models.

    How it works:
    Specifies the hardware device and precision settings.
    
    Attributes:
    - device (str): PyTorch device string ("cuda", "cpu", "mps", etc.). Defaults to "cpu".
    - use_half (bool): If True, models may run in FP16 on GPUs that support it,
                       potentially speeding up inference and reducing memory usage.
    """
    device: str = "cpu"
    use_half: bool = False


@dataclass
class GaitModelConfig:
    """
    Identifiers or paths for gait-related models.

    How it works:
    Stores paths/names for the neural networks used in the pipeline.

    Attributes:
    - pose_model_name (str): Name or path of the pose estimation model.
    - gait_embedding_model_path (str): Path to the trained gait embedding network weights.
    """
    pose_model_name: str = "yolov8n-seg.pt"
    gait_embedding_model_path: str = "models/gait_temporal_encoder.pth"


@dataclass
class GaitThresholdConfig:
    """
    Numeric thresholds used throughout the gait recognition pipeline.

    How it works:
    Defines sensitivity for detection, matching, and quality control.

    Attributes:
    - min_visibility (float): Minimum confidence score (0–1) for individual keypoints.
    - min_valid_joints (int): Minimum number of valid keypoints required in a pose.
    - max_match_distance (float): Max distance (e.g., cosine) for a strong identity match.
    - max_weak_match_distance (float): Max distance for a weak identity match.
    - min_gait_quality (float): Minimum quality score (0-1) for an embedding to be valid.
    """

    # Minimum visibility score of landmarks (0–1).
    min_visibility: float = 0.3

    # Minimum number of valid joints required to accept analysis.
    min_valid_joints: int = 10

    # Max distance allowed for a strong match (lower is better, e.g. Cosine Distance)
    max_match_distance: float = 0.45

    # Max distance allowed for a weak/potential match
    max_weak_match_distance: float = 0.60

    # Minimum quality for a gait embedding to be considered usable (0-1)
    min_gait_quality: float = 0.5

@dataclass
class GaitRouteConfig:
    """
    Configuration for the operational behavior of the gait recognition route.

    How it works:
    Controls data collection windows, smoothing, and processing frequency.

    Attributes:
    - process_every_n_frames (int): Frequency of analysis (e.g., every 3rd frame).
    - max_seconds_lookback (float): Duration of historical data for sequence construction.
    - max_entries_per_track (int): Max poses to store per track.
    - min_sequence_length (int): Min frames required to attempt gait extraction.
    - keypoint_ema_alpha (float): Smoothing factor for keypoints (higher = faster adaptation).
    - keypoint_history_length (int): Max length of the raw keypoint buffer.
    - img_size (int): Resolution used for model inference.
    """
    process_every_n_frames: int = 3
    max_seconds_lookback: float = 3.0
    max_entries_per_track: int = 50
    min_sequence_length: int = 24 
    keypoint_ema_alpha: float = 0.65
    keypoint_history_length: int=60
    img_size: int = 320


@dataclass
class GaitGalleryConfig:
    """
    Configuration for the gait identity gallery storage and search.

    How it works:
    Configures the vector database (gallery) parameters.

    Attributes:
    - dim (int): Dimensionality of the gait embedding vectors.
    - metric (str): Distance metric (e.g., "cosine").
    - gallery_path (Path): File path for storing the gallery.
    - encryption_key_env (str): Environment variable name for the encryption key.
    - ema_alpha (float): Smoothing factor for updating existing identity embeddings.
    """

    dim: int = 256                   # typical gait embedding size
    metric: str = "cosine"
    gallery_path: Path = Path("data/gait_gallery.enc")
    encryption_key_env: str = "GAITGUARD_GAIT_KEY"
    ema_alpha:float = 0.1 # Smoothing factor for EMA updates (0-1, lower=slower updates)


# ---------------------------------------------------------------------------
# Aggregate top-level config
# ---------------------------------------------------------------------------

@dataclass
class GaitConfig:
    """
    Aggregated configuration object for the entire gait recognition route.

    How it works:
    Combines all sub-configs into a single object passed to the engines.
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
    Builds a `GaitConfig` object with sensible default values.

    How it works:
    1. Detects available hardware (GPU/CPU).
    2. Resolves file paths relative to `base_dir`.
    3. Assembles the hierarchy of config dataclasses.

    Args:
    - prefer_gpu: If True, tries to use CUDA.
    - base_dir: Root directory for relative paths.

    Returns:
        GaitConfig: A fully configured object.
    """
    # device
    device_str, use_half = select_device(prefer_gpu=prefer_gpu)
    device_cfg = GaitDeviceConfig(device=device_str, use_half=use_half)

    # models
    if base_dir is None:
        base_dir = Path(".")
    gait_embedding_model_resolved_path = (base_dir / "models" / "gait_temporal_encoder.pth").resolve().as_posix()

    model_cfg = GaitModelConfig(
        pose_model_name="yolov8n-pose.pt",
        gait_embedding_model_path=gait_embedding_model_resolved_path,
    )
    
    # thresholds / route
    thresholds_cfg = GaitThresholdConfig()
    route_cfg = GaitRouteConfig(
        min_sequence_length=24, 
        keypoint_ema_alpha=0.65,
        keypoint_history_length=30,
        img_size=640
    )

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