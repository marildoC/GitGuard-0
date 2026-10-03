
#face/quality.py
# Face quality utilities: sharpness, pose, size, combined q_face.

from __future__ import annotations

import logging
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import FaceConfig, FaceThresholdConfig, default_face_config

logger = logging.getLogger(__name__)

# Weights for combining quality components
_W_DET = 0.4
_W_SHARP = 0.25
_W_POSE = 0.2
_W_SIZE = 0.15


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def estimate_sharpness(image: np.ndarray) -> float:
    """
    Estimate sharpness using Laplacian-of-variance, mapped to [0, 1].
    0 → very blurry, 1 → very sharp.
    """
    if image is None or image.size == 0:
        return 0.0

    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    lap = cv2.Laplacian(gray, cv2.CV_64F)
    var = float(lap.var())

    # Map variance to [0,1] with a soft knee so extremely large values saturate.
    # The denominator controls how fast we saturate; tuned empirically later.
    k = 200.0
    sharp = var / (var + k)
    return _clamp01(sharp)


def compute_size_score(
    bbox: Tuple[float, float, float, float],
    img_shape: Tuple[int, int, int],
    th: FaceThresholdConfig,
) -> float:
    """
    Size score based on face height relative to image height.
    0 → too small to be useful, 1 → comfortably large.
    """
    x1, y1, x2, y2 = bbox
    face_h = max(0.0, float(y2 - y1))
    img_h = float(img_shape[0])

    if face_h <= 0.0 or img_h <= 0.0:
        return 0.0

    if face_h < th.min_face_height_px:
        return 0.0

    # Ideal: face occupies ~1/6–1/3 of the image height. Above that, saturate.
    ratio = face_h / img_h
    ideal = 0.25
    max_ratio = 0.6

    if ratio >= max_ratio:
        return 1.0

    # Simple linear scaling up to ideal, then gentle plateau.
    if ratio <= ideal:
        return _clamp01(ratio / ideal)
    else:
        # Between ideal and max_ratio, keep score close to 1 but slightly lower
        # so extremely zoomed faces don't dominate everything.
        extra = (ratio - ideal) / max(max_ratio - ideal, 1e-6)
        return _clamp01(0.9 + 0.1 * extra)


def compute_pose_score(
    yaw: Optional[float],
    pitch: Optional[float],
    th: FaceThresholdConfig,
) -> float:
    """
    Pose score in [0,1] where 1 is frontal and 0 is extreme angles.
    Uses simple clamped linear penalties based on yaw and pitch.
    """
    if yaw is None or pitch is None:
        # If we can't estimate pose, be conservative but not too harsh.
        return 0.6

    ay = abs(float(yaw))
    ap = abs(float(pitch))

    # Yaw penalty
    if ay >= th.max_yaw_deg:
        sy = 0.0
    else:
        sy = 1.0 - ay / max(th.max_yaw_deg, 1e-6)

    # Pitch penalty
    if ap >= th.max_pitch_deg:
        sp = 0.0
    else:
        sp = 1.0 - ap / max(th.max_pitch_deg, 1e-6)

    # Slightly more weight to yaw (side turns are more problematic than small nods).
    score = 0.6 * sy + 0.4 * sp
    return _clamp01(score)


def combine_face_quality(
    det_score: float,
    sharpness: float,
    pose_score: float,
    size_score: float,
    th: Optional[FaceThresholdConfig] = None,
) -> float:
    """
    Combine detector confidence, sharpness, pose and size into q_face ∈ [0,1].

    This does not itself apply MIN_QUALITY_FOR_EMBED; callers can compare the
    returned value against FaceThresholdConfig.min_quality_for_embed.
    """
    th = th or default_face_config().thresholds

    ds = _clamp01(det_score)
    sh = _clamp01(sharpness)
    ps = _clamp01(pose_score)
    sz = _clamp01(size_score)

    # If detector score is below minimum, strongly penalise everything.
    if ds < th.min_det_score:
        return 0.0

    q = (
        _W_DET * ds
        + _W_SHARP * sh
        + _W_POSE * ps
        + _W_SIZE * sz
    )

    return _clamp01(q)


def compute_full_quality(
    image: np.ndarray,
    bbox: Tuple[float, float, float, float],
    det_score: float,
    yaw: Optional[float],
    pitch: Optional[float],
    cfg: Optional[FaceConfig] = None,
) -> float:
    """
    Convenience helper: from raw inputs (image, bbox, det score, pose)
    compute full q_face in one call.
    """
    cfg = cfg or default_face_config()
    th = cfg.thresholds

    sharp = estimate_sharpness(image)
    size = compute_size_score(bbox, image.shape, th)
    pose = compute_pose_score(yaw, pitch, th)

    return combine_face_quality(
        det_score=det_score,
        sharpness=sharp,
        pose_score=pose,
        size_score=size,
        th=th,
    )
