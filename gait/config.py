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
from core.device import select_device

def select_device(prefer_gpu: bool = True):
    if prefer_gpu and torch.cuda.is_available():
        return "cuda", True
    return "cpu", False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level configs
# ---------------------------------------------------------------------------

@dataclass
class GaitDeviceConfig:
    """
    Device configuration for all gait models.

    How it works:
    This dataclass specifies the hardware device (e.g., CPU, GPU) on which
    gait-related models should run, and whether to use half-precision
    (FP16) floating-point calculations if supported by the device.

    Attributes:
    - device (str): PyTorch device string ("cuda", "cpu", "mps", etc.). Defaults to "cpu".
    - use_half (bool): If True, models may run in FP16 on GPUs that support it,
                       potentially speeding up inference and reducing memory usage.
                       Defaults to False.
    """
    device: str = "cpu"
    use_half: bool = False


@dataclass
class GaitModelConfig:
    """
    Identifiers or paths for gait-related models.

    How it works:
    This dataclass provides the names or file paths for the machine learning
    models used in the gait recognition pipeline. This allows for easy
    configuration and swapping of models (e.g., different pose estimators or
    gait embedding networks) without modifying core logic.

    Attributes:
    - pose_model_name (str): Name or path of the pose estimation model
                             (e.g., "yolov8n-pose.pt" for YOLOv8-pose).
    - gait_model_name (str): Path or name of the gait embedding network
                             (e.g., ONNX or PyTorch model file). Placeholder
                             value is provided until a real model is integrated.
    """

    pose_model_name: str = "yolov8n-pose.pt"
    gait_embedding_model_path: str = "models/gait_temporal_encoder.pth"


@dataclass
class GaitThresholdConfig:
    """
    Numeric thresholds used throughout the gait recognition pipeline.

    How it works:
    This dataclass defines various numerical thresholds that govern the
    behavior and decision-making logic of the gait system. These include
    minimum visibility for keypoints, minimum quality for gait embeddings,
    and distance thresholds for matching identities in the gallery. Centralizing
    these values makes it easy to tune the system's sensitivity and performance.

    Attributes:
    - min_visibility (float): Minimum confidence score (0–1) for individual
                              keypoints to be considered valid or "visible". Defaults to 0.25.
    - min_valid_joints (int): Minimum number of valid (above `min_visibility`)
                              keypoints required in a pose for it to be accepted for analysis.
                              Defaults to 10.
    - max_match_distance (float): The maximum distance (e.g., cosine distance, 0-1)
                                  allowed for a strong, confident identity match in the gallery.
                                  Lower values indicate higher similarity. Defaults to 0.8.
    - max_weak_match_distance (float): The maximum distance allowed for a weak or
                                       less confident identity match. Matches within
                                       this but above `max_match_distance` are considered weak.
                                       Defaults to 0.95.
    - min_gait_quality (float): Minimum overall quality score (0-1) for a gait
                                embedding to be considered usable for recognition.
                                Embeddings below this quality are discarded. Defaults to 0.5.
    """

    # Minimum visibility score of landmarks (0–1).
    min_visibility: float = 0.3

    # Minimum number of valid joints required to accept analysis.
    min_valid_joints: int = 10

    # Max distance allowed for a strong match (lower is better)
    max_match_distance: float = 0.03

    max_weak_match_distance: float = 0.05

    #Minimum quality for a gait embedding to be considered usable (0-1)
    min_gait_quality: float = 0.5

@dataclass
class GaitRouteConfig:
    """
    Configuration for the operational behavior of the gait recognition route.

    How it works:
    This dataclass defines parameters that control how the gait pipeline
    processes frames and manages pose sequences. It includes settings for
    processing frequency, look-back windows for historical data, and the
    required length for pose sequences used in gait analysis, as well
    as smoothing parameters for keypoints.

    Attributes:
    - process_every_n_frames (int): How often the gait analysis should run (e.g.,
                                    process every 3rd frame). Defaults to 3.
    - max_seconds_lookback (float): The maximum duration (in seconds) of historical
                                    data to consider for gait sequence construction.
                                    Defaults to 3.0.
    - max_entries_per_track (int): Maximum number of entries (e.g., poses) to store
                                   per track in history buffers. Defaults to 50.
    - min_sequence_length (int): The minimum number of pose frames required in a
                                 sequence to attempt gait embedding extraction.
                                 Defaults to 24.
    - keypoint_ema_alpha (float): Alpha parameter for Exponential Moving Average (EMA)
                                  smoothing of keypoints. Controls the weight given
                                  to the new observation vs. the old EMA. Defaults to 0.65.
    - keypoint_history_length (int): Maximum length of the keypoint history deque
                                     maintained for each track. Defaults to 30.
    - img_size (int): The image resolution (width and height) used for model inference
                      (e.g., by the pose estimation model). Defaults to 640.
    """

    process_every_n_frames: int = 3
    max_seconds_lookback: float = 3.0
    max_entries_per_track: int = 50
    min_sequence_length: int = 24 
    keypoint_ema_alpha: float = 0.65
    keypoint_history_length: int=30
    img_size: int = 640


@dataclass
class GaitGalleryConfig:
    """
    Configuration for the gait identity gallery storage and search.

    How it works:
    This dataclass defines parameters related to how gait embeddings are stored,
    indexed, and searched within the `GaitGallery`. It includes the embedding
    dimensionality, the similarity metric, the storage path, and parameters for
    Exponential Moving Average (EMA) updates of gallery entries.

    Attributes:
    - dim (int): The dimensionality of the gait embedding vectors. Defaults to 256.
    - metric (str): The similarity metric used for searching embeddings (e.g., "cosine").
                    Defaults to "cosine".
    - gallery_path (Path): The file system path where the gait gallery data
                           (known identities and embeddings) is stored. Defaults to
                           "data/gait_gallery.enc".
    - encryption_key_env (str): The name of the environment variable that holds
                                the encryption key for the gallery file (if encrypted).
                                Defaults to "GAITGUARD_GAIT_KEY".
    - ema_alpha (float): The smoothing factor (0-1) for Exponential Moving Average
                         updates when adding new embeddings to an existing identity's
                         profile in the gallery. Lower values result in slower updates.
                         Defaults to 0.1.
    """

    dim: int = 256                   # typical gait embedding size
    metric: str = "cosine"
    gallery_path: Path = Path("data/gait_gallery.enc")
    encryption_key_env: str = "GAITGUARD_GAIT_KEY"
    ema_alpha:float = 0.1 #Smoothing factor for EMA updates(0-1,lower=slower updates)


# ---------------------------------------------------------------------------
# Aggregate top-level config
# ---------------------------------------------------------------------------

@dataclass
class GaitConfig:
    """
    Aggregated configuration object for the entire gait recognition route.

    How it works:
    This dataclass combines all the lower-level configuration objects
    (`GaitDeviceConfig`, `GaitModelConfig`, `GaitThresholdConfig`,
    `GaitRouteConfig`, `GaitGalleryConfig`) into a single, comprehensive
    configuration structure. This provides a convenient way to pass all
    necessary settings throughout the gait recognition system.

    Attributes:
    - device (GaitDeviceConfig): Device configuration.
    - models (GaitModelConfig): Model paths/identifiers.
    - thresholds (GaitThresholdConfig): Numeric thresholds.
    - route (GaitRouteConfig): Operational behavior of the gait route.
    - gallery (GaitGalleryConfig): Gallery storage and search configuration.
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
    This factory function simplifies the creation of a `GaitConfig` instance.
    It first uses `core.device.select_device` to automatically determine the
    optimal PyTorch device (e.g., CUDA if available and preferred, otherwise CPU)
    and whether to use half-precision. It then instantiates each of the
    lower-level configuration dataclasses with default values, applying any
    overrides provided (like `base_dir` for gallery path resolution).
    Finally, it aggregates these into a top-level `GaitConfig` and logs the
    initialization details.

    Args:
    - prefer_gpu (bool, optional): If True, attempts to select a GPU device;
                                   otherwise, defaults to CPU. Defaults to True.
    - base_dir (Optional[Path], optional): Base directory for resolving relative
                                           paths, such as the `gallery_path`.
                                           If None, uses the current working directory.

    Returns:
        GaitConfig: A fully configured `GaitConfig` object.
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
