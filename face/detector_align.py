# face/detector_align.py
#
# Face detection wrapper using InsightFace FaceAnalysis (buffalo_l pack).
# With buffalo_l we get detection + landmarks + 512-D embeddings in one shot.

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import FaceConfig, default_face_config

logger = logging.getLogger(__name__)

try:
    from insightface.app import FaceAnalysis
except Exception as exc:  # pragma: no cover
    FaceAnalysis = None  # type: ignore[assignment]
    logger.error("Failed to import insightface.app.FaceAnalysis: %s", exc)


@dataclass
class FaceCandidate:
    """
    One detected face candidate produced by FaceAnalysis (buffalo_l).

    Attributes
    ----------
    bbox : (x1, y1, x2, y2) in image coordinates (float).
    det_score : raw detector confidence.
    landmarks : (5, 2) array of facial keypoints.
    embedding : 512-D float32, L2-normalised face vector.
    yaw/pitch/roll : optional rough pose estimates in degrees.
    """

    bbox: Tuple[float, float, float, float]
    det_score: float
    landmarks: np.ndarray
    embedding: np.ndarray
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None


class FaceDetectorAligner:
    """
    Wrapper around InsightFace FaceAnalysis (buffalo_l).

    Responsibility:
      - Initialise FaceAnalysis with the configured pack (buffalo_l).
      - Given a BGR image, return a list of FaceCandidate with:
          bbox, landmarks, det_score, embedding, yaw/pitch/roll.

    Note: We no longer do a separate "alignment + ArcFace" step here.
    The buffalo_l pack already handles alignment and embedding internally.
    """

    def __init__(
        self,
        cfg: Optional[FaceConfig] = None,
        det_size: Tuple[int, int] = (640, 640),
    ) -> None:
        if FaceAnalysis is None:
            raise RuntimeError(
                "InsightFace FaceAnalysis is not available. "
                "Install 'insightface' and ensure dependencies are satisfied."
            )

        self.cfg = cfg or default_face_config()
        self.det_size = det_size

        device_str = self.cfg.device.device
        # InsightFace convention: ctx_id >= 0 uses GPU, -1 uses CPU.
        if device_str.startswith("cuda") or device_str.isdigit():
            ctx_id = 0
        else:
            ctx_id = -1

        model_name = self.cfg.models.retinaface_name  # now "buffalo_l" by default

        # Providers for ONNX Runtime backend. We prefer CUDA if available but
        # always include CPU as fallback.
        providers: Optional[list[str]]
        if ctx_id >= 0:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        logger.info(
            "Initialising FaceDetectorAligner with pack=%s ctx_id=%d det_size=%s providers=%s",
            model_name,
            ctx_id,
            det_size,
            providers,
        )

        try:
            self._app = FaceAnalysis(name=model_name, providers=providers)
            self._app.prepare(ctx_id=ctx_id, det_size=det_size)
        except Exception as exc:
            logger.exception("Failed to initialise InsightFace FaceAnalysis: %s", exc)
            raise RuntimeError(
                f"Failed to initialise InsightFace FaceAnalysis with pack '{model_name}'"
            ) from exc

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def warmup(self, img_shape: Tuple[int, int, int] = (480, 640, 3)) -> None:
        """
        Run a dummy inference to ensure models are loaded and JIT caches are warmed.
        """
        dummy = np.zeros(img_shape, dtype=np.uint8)
        _ = self.detect_and_align(dummy)

    def detect_and_align(self, image: np.ndarray) -> List[FaceCandidate]:
        """
        Detect faces in a BGR image and return FaceCandidate objects.

        Even though the name still says "align" for backward compatibility,
        buffalo_l already performs internal alignment and gives us a ready-to-use
        512-D embedding. We therefore *do not* perform our own geometric warp.

        Parameters
        ----------
        image : np.ndarray
            HxWx3 BGR image (OpenCV format).

        Returns
        -------
        List[FaceCandidate]
            One candidate per detected face (filtered by thresholds), sorted
            by detection score descending.
        """
        if image is None or image.size == 0:
            logger.warning("detect_and_align received empty image")
            return []

        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(
                f"detect_and_align expects HxWx3 BGR image, got shape {image.shape}"
            )

        faces = self._safe_get(image)
        if not faces:
            return []

        candidates: List[FaceCandidate] = []
        h, w = image.shape[:2]
        th = self.cfg.thresholds
        dim = self.cfg.gallery.dim

        for face in faces:
            # bbox: [x1, y1, x2, y2]
            bbox_arr = getattr(face, "bbox", None)
            if bbox_arr is None:
                continue
            bbox = np.asarray(bbox_arr, dtype=np.float32).reshape(-1)
            if bbox.size != 4:
                continue
            x1, y1, x2, y2 = bbox
            box_h = float(y2 - y1)

            # Filter by minimum face size
            if box_h < th.min_face_height_px:
                continue

            # Detection score (InsightFace versions expose slightly different attributes)
            score = float(
                getattr(
                    face,
                    "det_score",
                    getattr(face, "det_prob", getattr(face, "score", 0.0)),
                )
            )
            if score < th.min_det_score:
                continue

            # Landmarks: expected shape (5, 2)
            kps_arr = getattr(face, "kps", None)
            if kps_arr is None:
                continue
            kps = np.asarray(kps_arr, dtype=np.float32).reshape(-1, 2)
            if kps.shape != (5, 2):
                continue

            # Embedding: expected 512-D float32, L2-normalised.
            emb_arr = getattr(face, "embedding", None)
            if emb_arr is None:
                continue
            emb = self._normalise_embedding(emb_arr, dim=dim)

            yaw, pitch, roll = self._estimate_pose_simple(kps, w, h)

            candidates.append(
                FaceCandidate(
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    det_score=score,
                    landmarks=kps,
                    embedding=emb,
                    yaw=yaw,
                    pitch=pitch,
                    roll=roll,
                )
            )

        # Sort by detector score so callers can easily pick "best" face.
        candidates.sort(key=lambda c: c.det_score, reverse=True)
        return candidates

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _safe_get(self, image: np.ndarray):
        """
        Call FaceAnalysis.get with defensive error handling.
        """
        try:
            faces = self._app.get(image)
            return faces
        except Exception as exc:
            logger.exception("InsightFace FaceAnalysis.get failed: %s", exc)
            return []

    @staticmethod
    def _normalise_embedding(embedding: np.ndarray, dim: int) -> np.ndarray:
        """
        Ensure embedding is float32, 1-D, length=dim, and L2-normalised.
        """
        emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if emb.size != dim:
            logger.warning(
                "Face embedding dim mismatch: expected %d, got %d. "
                "Will still normalise and use it.",
                dim,
                emb.size,
            )
        # If embedding dimension differs, we still normalise whatever we have.
        norm = float(np.linalg.norm(emb))
        if norm > 1e-6:
            emb /= norm
        else:
            emb[:] = 0.0
        return emb

    def _estimate_pose_simple(
        self,
        landmarks: np.ndarray,
        img_w: int,
        img_h: int,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Very rough yaw/pitch/roll estimate from 5-point landmarks.

        This is a heuristic used only for quality scoring; if it fails,
        we simply return (None, None, None) and quality code will handle it.
        """
        try:
            left_eye = landmarks[0]
            right_eye = landmarks[1]
            nose = landmarks[2]
            mouth_left = landmarks[3]
            mouth_right = landmarks[4]

            # Yaw: angle of the eye line.
            dx = right_eye[0] - left_eye[0]
            dy = right_eye[1] - left_eye[1]
            yaw = float(np.degrees(np.arctan2(dy, dx)))

            # Pitch: nose vertical offset from eye line.
            mid_eye_y = 0.5 * (left_eye[1] + right_eye[1])
            pitch = float(
                np.degrees(
                    np.arctan2(nose[1] - mid_eye_y, max(abs(dx), 1e-6))
                )
            )

            # Roll: angle of the mouth line.
            mouth_dx = mouth_right[0] - mouth_left[0]
            mouth_dy = mouth_right[1] - mouth_left[1]
            roll = float(np.degrees(np.arctan2(mouth_dy, mouth_dx)))

            return yaw, pitch, roll
        except Exception:
            return None, None, None
