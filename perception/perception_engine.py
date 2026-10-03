# perception/perception_engine.py
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from schemas import Frame, Tracklet
from core.interfaces import PerceptionEngine

from .detector import Detector, Detection
from .tracker_ocsort import OCSortTracker, Track
from .appearance import AppearanceExtractor
from .ring_buffer import RingBuffer, RingBufferConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal per-track state
# ---------------------------------------------------------------------------

class TrackState:
    """
    Holds:
      - public Tracklet object (required by schemas)
      - appearance_features (EMA updated inside tracker_ocsort)
    """
    def __init__(self, track_id: int, camera_id: str):
        self.tracklet = Tracklet(
            track_id=track_id,
            camera_id=camera_id,
            last_frame_id=0,
            last_box=(0, 0, 0, 0),
            confidence=0.0,
            age_frames=0,
            lost_frames=0,
            history_boxes=[]
        )
        self.appearance_feature: Optional[np.ndarray] = None


# ---------------------------------------------------------------------------
# Main Perception Engine (Phase 1)
# ---------------------------------------------------------------------------

class Phase1PerceptionEngine(PerceptionEngine):
    """
    The brain of the Phase-1 perception pipeline.

    Steps per frame:
      1. Detection (YOLO)
      2. Appearance extraction (cheap classic CV)
      3. Tracking (OC-SORT + IoU + optional appearance fusion)
      4. Update Tracklets
      5. Update Ring Buffer
      6. Remove dead tracks

    Output:
      List[Tracklet]
    """

    def __init__(
        self,
        detector: Optional[Detector] = None,
        tracker: Optional[OCSortTracker] = None,
        appearance: Optional[AppearanceExtractor] = None,
        ring_buffer: Optional[RingBuffer] = None,
        max_lost_frames: int = 30,
    ):
        super().__init__()

        # Modules
        self.detector = detector or Detector()
        self.tracker = tracker or OCSortTracker()
        self.appearance = appearance or AppearanceExtractor()
        self.ring_buffer = ring_buffer or RingBuffer(RingBufferConfig())

        # Internal state for each track_id
        self._states: Dict[int, TrackState] = {}

        # When to drop a lost track
        self.max_lost_frames = max_lost_frames

        logger.info("Phase-1 PerceptionEngine initialized.")

    # ------------------------------------------------------------------ #
    # REQUIRED API
    # ------------------------------------------------------------------ #

    def process_frame(self, frame: Frame) -> List[Tracklet]:
        """
        Main function called every frame by main_loop.py.

        Returns:
            List[Tracklet] for all active (confirmed) tracks.
        """
        if frame.image is None:
            logger.warning("process_frame: empty frame.image")
            return []

        # ---- Step 1: YOLO Detection ----
        detections: List[Detection] = self.detector.detect(frame)

        # ---- Step 2: Appearance Features (one per detection) ----
        features = self.appearance.compute_features_for_detections(
            frame, detections
        )

        # ---- Step 3: OC-SORT Tracking ----
        tracks: List[Track] = self.tracker.update(detections, features)

        # ---- Step 4: Update Track States ----
        active_ids = set()
        for tr in tracks:
            active_ids.add(tr.track_id)
            self._update_track_state(frame, tr)

        # ---- Step 5: Mark & Remove Lost Tracks ----
        self._increment_lost_and_prune(active_ids)

        # ---- Step 6: Update Ring Buffer ----
        for tr in tracks:
            self.ring_buffer.add(
                track_id=tr.track_id,
                ts=frame.ts,
                frame_index=frame.frame_id,
                bbox=tuple(tr.bbox.tolist()),
                crop=None,
                appearance=None,
                pose=None,
            )

        # Return list of active tracklets
        return [state.tracklet for state in self._states.values()]

    # ------------------------------------------------------------------ #
    # INTERNAL HELPERS
    # ------------------------------------------------------------------ #

    def _update_track_state(self, frame: Frame, tr: Track) -> None:
        """
        Create/update TrackState and Tracklet for each tracked object.
        """
        tid = tr.track_id

        # Create if new
        if tid not in self._states:
            self._states[tid] = TrackState(
                track_id=tid,
                camera_id=frame.camera_id,
            )

        state = self._states[tid]
        t = state.tracklet

        # Update public Tracklet
        t.last_frame_id = frame.frame_id
        t.last_box = tuple(tr.bbox.tolist())
        t.confidence = tr.score
        t.age_frames += 1
        t.lost_frames = 0

        # Keep history (limit to 60)
        t.history_boxes.append(t.last_box)
        if len(t.history_boxes) > 60:
            t.history_boxes.pop(0)

        # Update appearance feature from tracker’s EMA
        state.appearance_feature = None  # not used directly in Phase 1

    def _increment_lost_and_prune(self, active_ids: set) -> None:
        """
        Increase lost counters for inactive tracks and remove dead ones.
        """
        to_remove = []

        for tid, state in self._states.items():
            if tid not in active_ids:
                state.tracklet.lost_frames += 1
                if state.tracklet.lost_frames > self.max_lost_frames:
                    to_remove.append(tid)

        # Remove from internal state + ring buffer
        for tid in to_remove:
            self._states.pop(tid, None)
            self.ring_buffer.remove_track(tid)
            logger.debug(f"Removed track {tid} (lost too long).")
