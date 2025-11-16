# identity/identity_engine.py - IdentityEngine implementation using FaceRoute + FaceGallery with temporal smoothing.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from core.interfaces import IdentityEngine as IdentityEngineBase
from schemas import Frame, Tracklet, IdSignals, IdentityDecision
from face.config import FaceConfig, default_face_config
from face.route import FaceRoute
from identity.face_gallery import FaceGallery, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class IdentitySmoothingConfig:
    half_life_sec: float = 2.0
    stale_after_sec: float = 6.0
    min_confidence: float = 0.05


class FaceIdentityEngine(IdentityEngineBase):
    def __init__(
        self,
        face_cfg: Optional[FaceConfig] = None,
        face_route: Optional[FaceRoute] = None,
        gallery: Optional[FaceGallery] = None,
        smoothing_cfg: Optional[IdentitySmoothingConfig] = None,
    ) -> None:
        self.face_cfg = face_cfg or default_face_config()
        self.face_route = face_route or FaceRoute(self.face_cfg)
        self.gallery = gallery or FaceGallery(self.face_cfg.gallery)
        self.smoothing_cfg = smoothing_cfg or IdentitySmoothingConfig()

        self._last_decision: Dict[int, IdentityDecision] = {}
        self._last_decision_ts: Dict[int, float] = {}
        self._current_ts: float = time.time()

        self._match_dist_strong: float = self.face_cfg.thresholds.max_match_distance

        logger.info(
            "FaceIdentityEngine initialised | strong_match_dist=%.3f",
            self._match_dist_strong,
        )

    def reset(self) -> None:
        self.face_route.reset()
        self._last_decision.clear()
        self._last_decision_ts.clear()

    # ------------------------------------------------------------------ #
    # IdentityEngine interface                                           #
    # ------------------------------------------------------------------ #

    def update_signals(self, frame: Frame, tracks: List[Tracklet]) -> List[IdSignals]:
        self._current_ts = frame.ts
        evidences = self.face_route.run(frame, tracks)

        signals: List[IdSignals] = []
        active_ids = set[int]()

        for trk in tracks:
            tid = trk.track_id
            active_ids.add(tid)
            ev = evidences.get(tid)

            if ev is not None:
                sig = IdSignals(
                    track_id=tid,
                    face_embedding=ev.embedding,
                    face_quality=float(ev.quality),
                )
            else:
                sig = IdSignals(track_id=tid)

            signals.append(sig)

        self._cleanup_dead_tracks(active_ids, self._current_ts)
        return signals

    def decide(self, signals: List[IdSignals]) -> List[IdentityDecision]:
        ts = self._current_ts
        decisions: List[IdentityDecision] = []

        for sig in signals:
            tid = sig.track_id
            emb = sig.face_embedding

            if emb is not None:
                decision = self._decide_with_new_embedding(tid, emb, sig.face_quality, ts)
            else:
                decision = self._decide_with_smoothing_only(tid, ts)

            decisions.append(decision)

        return decisions

    # ------------------------------------------------------------------ #
    # Decision helpers                                                   #
    # ------------------------------------------------------------------ #

    def _decide_with_new_embedding(
        self,
        track_id: int,
        embedding: np.ndarray,
        quality: float,
        ts: float,
    ) -> IdentityDecision:
        res: Optional[SearchResult] = self.gallery.search_best(embedding, k=5)

        if res is not None and res.distance <= self._match_dist_strong:
            conf = float(res.score) * float(quality)
            decision = IdentityDecision(
                track_id=track_id,
                identity_id=res.person_id,
                category=res.category,
                confidence=conf,
                reason=f"face_match:{res.person_id}:d={res.distance:.3f}:q={quality:.3f}:s={res.score:.3f}",
            )
            self._last_decision[track_id] = decision
            self._last_decision_ts[track_id] = ts
            return decision

        decision = self._decide_with_smoothing_only(track_id, ts)
        if decision.identity_id is None:
            decision.reason = "face_unknown:no_gallery_match"
        return decision

    def _decide_with_smoothing_only(
        self,
        track_id: int,
        ts: float,
    ) -> IdentityDecision:
        last = self._last_decision.get(track_id)
        if last is None:
            return IdentityDecision(
                track_id=track_id,
                identity_id=None,
                category="unknown",
                confidence=0.0,
                reason="face_unknown:no_history",
            )

        last_ts = self._last_decision_ts.get(track_id, ts)
        age = ts - last_ts
        cfg = self.smoothing_cfg

        if age >= cfg.stale_after_sec:
            return IdentityDecision(
                track_id=track_id,
                identity_id=None,
                category="unknown",
                confidence=0.0,
                reason=f"face_unknown:stale_history:{age:.2f}s",
            )

        decay = self._decay_factor(age, cfg.half_life_sec)
        conf = last.confidence * decay

        if conf < cfg.min_confidence:
            return IdentityDecision(
                track_id=track_id,
                identity_id=None,
                category="unknown",
                confidence=0.0,
                reason=f"face_unknown:decayed_below_min:{age:.2f}s",
            )

        return IdentityDecision(
            track_id=track_id,
            identity_id=last.identity_id,
            category=last.category,
            confidence=conf,
            reason=f"{last.reason}|decayed:{age:.2f}s",
        )

    # ------------------------------------------------------------------ #
    # Housekeeping                                                       #
    # ------------------------------------------------------------------ #

    def _cleanup_dead_tracks(self, active_ids: set[int], ts: float) -> None:
        stale_ids: List[int] = []
        for tid in list(self._last_decision.keys()):
            if tid not in active_ids:
                last_ts = self._last_decision_ts.get(tid, ts)
                if ts - last_ts > self.smoothing_cfg.stale_after_sec:
                    stale_ids.append(tid)
        for tid in stale_ids:
            self._last_decision.pop(tid, None)
            self._last_decision_ts.pop(tid, None)

    @staticmethod
    def _decay_factor(age: float, half_life: float) -> float:
        if half_life <= 0.0:
            return 0.0
        return 0.5 ** (age / half_life)
