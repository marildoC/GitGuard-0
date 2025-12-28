"""
Central configuration for the Gait Recognition system.
Optimized for Angle-Invariant CNN-GRU models and OpenVINO execution.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import logging
import torch
from core.device import select_device

logger = logging.getLogger(__name__)

@dataclass
class GaitDeviceConfig:
    """Hardware acceleration settings."""
    device: str = "cpu" 
    use_half: bool = False 

@dataclass
class GaitModelConfig:
    """Paths for pose estimation and gait embedding models."""
    pose_model_name: str = "yolov8n-pose_openvino_model/"
    gait_embedding_model_path: str = "models/gait_temporal_encoder"

@dataclass
class GaitThresholdConfig:
    """Numeric thresholds for filtering and identity matching."""
    min_visibility: float = 0.4        # Min confidence for individual keypoints
    min_valid_joints: int = 10         # Min joints required per frame
    max_match_distance: float = 0.80   # Cutoff for strong matches (Cosine Distance)
    max_weak_match_distance: float = 0.75 
    min_gait_quality: float = 0.5      # Min average confidence for a sequence
    min_match_margin: float = 0.15

@dataclass
class GaitRouteConfig:
    """Operational parameters for the real-time processing pipeline."""
    process_every_n_frames: int = 3
    max_seconds_lookback: float = 3.0
    max_entries_per_track: int = 60 
    min_sequence_length: int = 30      # Required frames for GRU temporal analysis
    keypoint_ema_alpha: float = 0.65   # Smoothing factor for keypoint jitter
    keypoint_history_length: int = 45 
    img_size: int = 640

@dataclass
class GaitGalleryConfig:
    """Settings for the identity database and vector search."""
    dim: int = 256                   # Embedding vector dimension
    metric: str = "cosine"
    gallery_path: Path = Path("data/gait_gallery.pkl")
    encryption_key_env: str = "GAITGUARD_GAIT_KEY"
    ema_alpha: float = 0.5           # Update rate for identity templates

@dataclass
class GaitConfig:
    """Aggregate configuration object."""
    device: GaitDeviceConfig
    models: GaitModelConfig
    thresholds: GaitThresholdConfig
    route: GaitRouteConfig
    gallery: GaitGalleryConfig

def default_gait_config(
    prefer_gpu: bool = True,
    base_dir: Optional[Path] = None,
) -> GaitConfig:
    """
    Factory function to initialize the configuration with optimized defaults.
    Handles path resolution and checks for OpenVINO model availability.
    """
    device_str, use_half = select_device(prefer_gpu=prefer_gpu)
    device_cfg = GaitDeviceConfig(device=device_str, use_half=use_half)

    base_dir = base_dir or Path(".")
    
    # Path resolution
    gait_model_path = (base_dir / "models" / "gait_temporal_encoder.pth").resolve().as_posix()
    openvino_path = (base_dir / "yolov8n-pose_openvino_model/").resolve()
    
    # Fallback to .pt if OpenVINO export is missing
    pose_model = str(openvino_path) if openvino_path.exists() else "yolov8n-pose.pt"
    if not openvino_path.exists():
        logger.warning(f"OpenVINO model not found at {openvino_path}. Falling back to PyTorch.")

    return GaitConfig(
        device=device_cfg,
        models=GaitModelConfig(pose_model_name=pose_model, gait_embedding_model_path=gait_model_path),
        thresholds=GaitThresholdConfig(max_match_distance=0.70, max_weak_match_distance=0.80),
        route=GaitRouteConfig(min_sequence_length=30, keypoint_history_length=45),
        gallery=GaitGalleryConfig(dim=256, gallery_path=(base_dir / "data" / "gait_gallery.pkl").resolve())
    )