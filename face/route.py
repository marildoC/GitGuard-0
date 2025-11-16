# face/route.py
# High-level face route: per-track face history, detect+embed, best evidence.

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from schemas import Frame, Tracklet
from .config import FaceConfig, default_face_config
from .detector_align import FaceDetectorAligner, FaceCandidate
from .quality import compute_full_quality

logger = logging.getLogger(__name__)


@dataclass
class FaceEvidence:
    """
    Best recent face evidence for a given track over a short time window.

    Attributes
    ----------
    track_id : int
        ID of the tracked person (from Phase-1 tracker).
    ts : float
        Timestamp of the frame where this evidence was captured.
    frame_id : int
        Frame index (monotonically increasing).
    quality : float
        Scalar quality score in [0, 1]; higher is better.
    embedding : np.ndarray
        512-D L2-normalised face embedding (float32).
    bbox_in_frame : (x1, y1, x2, y2)
        Region in the original frame used as the "head" crop.
    yaw/pitch/roll : Optional[float]
        Rough pose estimates in degrees (if available).
    """

    track_id: int
    ts: float
    frame_id: int
    quality: float
    embedding: np.ndarray
    bbox_in_frame: Tuple[float, float, float, float]
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None


class FaceRoute:
    """
    FaceRoute connects Phase-1 tracks with the face engine (buffalo_l).

    Per frame:
      - For each Tracklet, decide whether to run face detection (throttled).
      - Crop an approximate head-region from the frame.
      - Run FaceDetectorAligner (buffalo_l) on that crop.
      - Compute a quality score for each detected face and keep the best one.
      - Store best face as FaceEvidence in a short per-track buffer.

    On query (run / get_best_evidence):
      - For each track, return the best-quality FaceEvidence within a
        configurable lookback window (seconds).

    This route does *not* talk to the gallery; it is purely about
    converting tracks → high-quality face embeddings over time.
    """

    def __init__(
        self,
        cfg: Optional[FaceConfig] = None,
        detector: Optional[FaceDetectorAligner] = None,
    ) -> None:
        self.cfg = cfg or default_face_config()
        self.detector = detector or FaceDetectorAligner(self.cfg)

        # Per-track buffer of FaceEvidence (short history).
        self._buffers: Dict[int, Deque[FaceEvidence]] = {}
        # Per-track last frame index where we ran heavy face processing.
        self._last_processed_frame: Dict[int, int] = {}

        logger.info(
            "FaceRoute initialised | lookback=%.1fs max_entries=%d every_n_frames=%d",
            self.cfg.route.max_seconds_lookback,
            self.cfg.route.max_entries_per_track,
            self.cfg.route.process_every_n_frames,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        """
        Clear all per-track buffers and state.
        """
        self._buffers.clear()
        self._last_processed_frame.clear()

    def run(
        self,
        frame: Frame,
        tracklets: List[Tracklet],
    ) -> Dict[int, FaceEvidence]:
        """
        Process a frame + list of Tracklets and update per-track face buffers.

        Returns a dictionary mapping track_id → best FaceEvidence in the
        current lookback window (if any).
        """
        if frame.image is None or frame.image.size == 0:
            return {}

        img = frame.image
        th = self.cfg.thresholds
        route_cfg = self.cfg.route

        for trk in tracklets:
            # 1. Basic geometric filter: person bbox must be tall enough.
            x1, y1, x2, y2 = trk.last_box
            box_h = float(y2 - y1)
            if box_h < th.min_box_height_for_face_px:
                continue

            # 2. Per-track frame throttling: don't run face detection on
            #    every single frame to save compute.
            last_fid = self._last_processed_frame.get(trk.track_id, -1)
            if (
                last_fid >= 0
                and frame.frame_id - last_fid < route_cfg.process_every_n_frames
            ):
                continue

            # 3. Crop a generous head/upper-body region.
            head_crop, head_box = self._crop_head_region(img, trk.last_box)
            if head_crop is None:
                continue

            # 4. Run face engine (buffalo_l) on the head crop.
            candidates = self.detector.detect_and_align(head_crop)
            if not candidates:
                self._last_processed_frame[trk.track_id] = frame.frame_id
                continue

            # 5. Among candidates, pick the one with best quality.
            best_cand, best_q = self._select_best_candidate(
                head_crop, head_box, candidates
            )

            if best_cand is None:
                self._last_processed_frame[trk.track_id] = frame.frame_id
                continue

            # 6. Only keep evidence above the "embed-worthy" quality threshold.
            if best_q < th.min_quality_for_embed:
                self._last_processed_frame[trk.track_id] = frame.frame_id
                continue

            # 7. Use embedding directly from candidate (buffalo_l).
            emb = self._ensure_embedding(best_cand.embedding)

            ev = FaceEvidence(
                track_id=trk.track_id,
                ts=frame.ts,
                frame_id=frame.frame_id,
                quality=best_q,
                embedding=emb,
                bbox_in_frame=head_box,
                yaw=best_cand.yaw,
                pitch=best_cand.pitch,
                roll=best_cand.roll,
            )

            # 8. Update per-track buffer.
            buf = self._buffers.setdefault(trk.track_id, deque())
            buf.append(ev)
            self._prune_buffer(trk.track_id, frame.ts)

            if len(buf) > route_cfg.max_entries_per_track:
                buf.popleft()

            self._last_processed_frame[trk.track_id] = frame.frame_id

        # 9. For each track, expose only the best evidence in current window.
        best: Dict[int, FaceEvidence] = {}
        for trk in tracklets:
            ev = self.get_best_evidence(trk.track_id, frame.ts)
            if ev is not None:
                best[trk.track_id] = ev

        return best

    def get_best_evidence(
        self,
        track_id: int,
        current_ts: Optional[float] = None,
    ) -> Optional[FaceEvidence]:
        """
        Return the best-quality FaceEvidence for a track within the
        configured lookback window, or None if none exists.
        """
        buf = self._buffers.get(track_id)
        if not buf:
            return None

        route_cfg = self.cfg.route
        if current_ts is None:
            current_ts = buf[-1].ts

        min_ts = current_ts - route_cfg.max_seconds_lookback
        best_ev: Optional[FaceEvidence] = None
        best_q = -1.0

        for ev in buf:
            if ev.ts < min_ts:
                continue
            if ev.quality > best_q:
                best_q = ev.quality
                best_ev = ev

        return best_ev

    def get_all_best(
        self,
        current_ts: Optional[float] = None,
    ) -> Dict[int, FaceEvidence]:
        """
        Return best FaceEvidence for all tracks that have any evidence
        in their buffer within the lookback window.
        """
        out: Dict[int, FaceEvidence] = {}
        for tid, buf in self._buffers.items():
            if not buf:
                continue
            ts = current_ts if current_ts is not None else buf[-1].ts
            ev = self.get_best_evidence(tid, ts)
            if ev is not None:
                out[tid] = ev
        return out

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _prune_buffer(self, track_id: int, current_ts: float) -> None:
        """
        Drop entries older than max_seconds_lookback from a track buffer.
        """
        buf = self._buffers.get(track_id)
        if not buf:
            return

        max_age = self.cfg.route.max_seconds_lookback
        while buf and (current_ts - buf[0].ts) > max_age:
            buf.popleft()

    def _crop_head_region(
        self,
        image: np.ndarray,
        box: Tuple[float, float, float, float],
    ) -> Tuple[Optional[np.ndarray], Tuple[float, float, float, float]]:
        """
        Crop a region around the upper part of the person bbox.

        We assume the detector bbox is roughly "full person" or upper body.
        We bias the crop towards the top (head/shoulders) to reduce
        background and make face detection easier.
        """
        h, w = image.shape[:2]
        x1, y1, x2, y2 = box
        x1 = float(x1)
        y1 = float(y1)
        x2 = float(x2)
        y2 = float(y2)

        bw = max(0.0, x2 - x1)
        bh = max(0.0, y2 - y1)
        if bw <= 1.0 or bh <= 1.0:
            return None, (0.0, 0.0, 0.0, 0.0)

        # Extend slightly above head and capture upper body.
        head_top = y1 - 0.15 * bh
        head_bottom = y1 + 0.6 * bh

        hx1 = max(0, int(np.floor(x1)))
        hx2 = min(w, int(np.ceil(x2)))
        hy1 = max(0, int(np.floor(head_top)))
        hy2 = min(h, int(np.ceil(head_bottom)))

        if hx2 <= hx1 or hy2 <= hy1:
            return None, (0.0, 0.0, 0.0, 0.0)

        crop = image[hy1:hy2, hx1:hx2]

        bbox_in_frame = (float(hx1), float(hy1), float(hx2), float(hy2))
        return crop, bbox_in_frame

    def _select_best_candidate(
        self,
        head_crop: np.ndarray,
        head_box_in_frame: Tuple[float, float, float, float],
        candidates: List[FaceCandidate],
    ) -> Tuple[Optional[FaceCandidate], float]:
        """
        Among detected faces in the head crop, choose the one with the
        highest quality score according to compute_full_quality().
        """
        if not candidates:
            return None, 0.0

        th = self.cfg.thresholds
        best_cand: Optional[FaceCandidate] = None
        best_q = 0.0

        for cand in candidates:
            q = compute_full_quality(
                image=head_crop,
                bbox=cand.bbox,
                det_score=cand.det_score,
                yaw=cand.yaw,
                pitch=cand.pitch,
                cfg=self.cfg,
            )

            if q >= best_q:
                best_q = q
                best_cand = cand

        if best_cand is None or best_q < th.min_quality_for_embed:
            return None, best_q

        return best_cand, best_q

    def _ensure_embedding(self, emb: np.ndarray) -> np.ndarray:
        """
        Ensure embedding is float32, 1-D and (re)normalized.

        buffalo_l usually already outputs L2-normalised 512-D vectors,
        but we enforce normalisation once more for safety.
        """
        e = np.asarray(emb, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(e))
        if norm > 1e-6:
            e /= norm
        else:
            e[:] = 0.0
        return e
