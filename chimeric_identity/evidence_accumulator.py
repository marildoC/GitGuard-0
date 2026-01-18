# chimeric_identity/evidence_accumulator.py
# ============================================================================
# EVIDENCE ACCUMULATOR - Temporal Buffering & Hysteresis
# ============================================================================
#
# Purpose:
#   Bridge temporal desynchronization between face (100ms) and gait (1000ms).
#   Implement rolling evidence windows with hysteresis to prevent identity
#   flicker and enforce stable decision-making.
#
# Design Principles:
#   1. TEMPORAL WINDOWING: Keep recent evidence only (2-3 sec lookback)
#   2. HYSTERESIS: Stricter threshold to switch than to confirm
#   3. QUALITY WEIGHTING: Higher quality evidence has more influence
#   4. AUTOMATIC PRUNING: Old evidence auto-removed (memory safety)
#   5. STABILITY TRACKING: Count recent samples per identity
#
# Key Innovation:
#   Separate face/gait buffers with different time windows account for
#   modality speed differences. Hysteresis prevents single high-confidence
#   frame from causing identity switch.
#
# Anti-Pattern Avoided:
#   Naive averaging of face + gait scores (ignores temporal dynamics and
#   evidence quality differences).

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple, List

from .types import FaceEvidence, GaitEvidence

logger = logging.getLogger(__name__)


# ============================================================================
# EVIDENCE BUFFER STRUCTURES
# ============================================================================

@dataclass
class EvidenceWindow:
    """
    Rolling time-windowed buffer for one evidence type.
    
    Automatically prunes evidence older than window_sec.
    Tracks stability (how many recent samples match primary identity).
    
    Design Rationale:
        - Deque for O(1) append + O(1) popleft
        - Time-based pruning (not frame-based) for temporal accuracy
        - Quality tracking enables weighted voting
    """
    window_sec: float                   # Lookback window (e.g. 2.0s for face)
    evidence_deque: deque = field(default_factory=deque)
    max_size: int = 100                 # Hard limit (prevent memory growth)
    
    def add(
        self,
        evidence: FaceEvidence | GaitEvidence,
        now: Optional[float] = None
    ):
        """
        Add evidence to buffer and prune old entries.
        
        Args:
            evidence: Face or Gait evidence
            now: Current timestamp (or use time.time())
        """
        if now is None:
            now = time.time()
        
        # Add new evidence
        self.evidence_deque.append((evidence.timestamp, evidence))
        
        # Prune old evidence (outside time window)
        cutoff_ts = now - self.window_sec
        while (self.evidence_deque and 
               self.evidence_deque[0][0] < cutoff_ts):
            self.evidence_deque.popleft()
        
        # Enforce max size (safety)
        while len(self.evidence_deque) > self.max_size:
            self.evidence_deque.popleft()
    
    def get_recent(
        self,
        now: Optional[float] = None,
        lookback_sec: Optional[float] = None
    ) -> List[FaceEvidence | GaitEvidence]:
        """
        Get recent evidence within lookback window.
        
        Args:
            now: Current timestamp
            lookback_sec: Override window_sec if provided
        
        Returns:
            List of recent evidence (newest first)
        """
        if now is None:
            now = time.time()
        if lookback_sec is None:
            lookback_sec = self.window_sec
        
        cutoff_ts = now - lookback_sec
        recent = [
            ev for (ts, ev) in self.evidence_deque
            if ts >= cutoff_ts
        ]
        return list(reversed(recent))  # Newest first
    
    def get_stability(
        self,
        now: Optional[float] = None
    ) -> Tuple[Optional[str], int, float]:
        """
        Get primary identity and stability count.
        
        Returns:
            (primary_id, count_recent, mean_quality)
        
        Design:
            - Primary identity = most frequent in recent window
            - Count = how many recent samples match primary
            - Mean quality = average quality of matching samples
        """
        recent = self.get_recent(now)
        if not recent:
            return (None, 0, 0.0)
        
        # Count identity frequency
        id_counts: Dict[str, int] = {}
        id_qualities: Dict[str, List[float]] = {}
        
        for ev in recent:
            if ev.identity_id is None:
                continue
            
            id_counts[ev.identity_id] = id_counts.get(ev.identity_id, 0) + 1
            
            if ev.identity_id not in id_qualities:
                id_qualities[ev.identity_id] = []
            id_qualities[ev.identity_id].append(ev.quality)
        
        if not id_counts:
            return (None, 0, 0.0)
        
        # Find most frequent identity
        primary_id = max(id_counts, key=id_counts.get)
        count = id_counts[primary_id]
        
        # Calculate mean quality for primary identity
        qualities = id_qualities.get(primary_id, [0.0])
        mean_quality = sum(qualities) / len(qualities)
        
        return (primary_id, count, mean_quality)
    
    def clear(self):
        """Clear all evidence from buffer."""
        self.evidence_deque.clear()
    
    def size(self) -> int:
        """Return current buffer size."""
        return len(self.evidence_deque)


# ============================================================================
# HYSTERESIS LOGIC
# ============================================================================

@dataclass
class HysteresisConfig:
    """
    Hysteresis configuration for preventing identity flicker.
    
    Key Idea:
        - Confirm threshold: Initial acceptance (e.g. 0.75)
        - Switch threshold: Higher bar to change identity (e.g. 0.85)
        - Margin bonus: Extra margin required (e.g. +0.10)
    
    Example:
        - First match at 0.76 similarity → CONFIRMED
        - To switch to new person, need 0.85 similarity + 0.10 margin
        - Prevents flicker from noisy frames
    """
    confirm_threshold: float = 0.75     # Initial accept threshold
    switch_threshold: float = 0.85      # Higher threshold to switch
    margin_bonus: float = 0.10          # Extra margin required to switch
    stability_required: int = 2         # N recent samples needed
    
    def can_confirm(self, similarity: float, margin: float) -> bool:
        """Check if evidence passes confirm threshold."""
        return (similarity >= self.confirm_threshold and
                margin >= 0.05)  # Minimum margin safety
    
    def can_switch(
        self,
        new_similarity: float,
        new_margin: float,
        current_identity: Optional[str]
    ) -> bool:
        """
        Check if evidence is strong enough to switch identity.
        
        Requires:
            - Higher similarity than initial confirm
            - Extra margin bonus
        
        Args:
            new_similarity: Similarity for new identity
            new_margin: Margin for new identity
            current_identity: Current confirmed identity (or None)
        
        Returns:
            True if switch allowed, False otherwise
        """
        if current_identity is None:
            # No current identity, use confirm threshold
            return self.can_confirm(new_similarity, new_margin)
        
        # Switching requires stricter evidence
        return (new_similarity >= self.switch_threshold and
                new_margin >= (0.05 + self.margin_bonus))


# ============================================================================
# MAIN EVIDENCE ACCUMULATOR
# ============================================================================

class EvidenceAccumulator:
    """
    Per-track evidence accumulation with temporal buffering and hysteresis.
    
    Maintains separate buffers for face and gait, accounting for different
    time scales (face 2s window, gait 3s window).
    
    Key Methods:
        - add_face_evidence: Add face evidence to buffer
        - add_gait_evidence: Add gait evidence to buffer
        - get_fused_evidence: Get stability-weighted evidence
        - check_hysteresis: Validate identity switch against hysteresis
    
    Design Pattern:
        - One accumulator instance per track
        - Lightweight (only buffers, no heavy compute)
        - Stateless (can be recreated from scratch if needed)
    """
    
    def __init__(
        self,
        track_id: int,
        face_window_sec: float = 2.0,
        gait_window_sec: float = 3.0,
        hysteresis_config: Optional[HysteresisConfig] = None
    ):
        """
        Initialize evidence accumulator for one track.
        
        Args:
            track_id: Track identifier
            face_window_sec: Rolling window for face evidence
            gait_window_sec: Rolling window for gait evidence
            hysteresis_config: Hysteresis thresholds (or use defaults)
        """
        self.track_id = track_id
        
        # Evidence buffers
        self.face_window = EvidenceWindow(window_sec=face_window_sec)
        self.gait_window = EvidenceWindow(window_sec=gait_window_sec)
        
        # Hysteresis config
        self.hysteresis = hysteresis_config or HysteresisConfig()
        
        # Current confirmed identity (for hysteresis check)
        self.confirmed_identity: Optional[str] = None
        
        # Gait streak tracking (Phase 3A wiring)
        # confirm_streak: consecutive confirms of same identity
        self.confirm_streak: int = 0
        self.last_confirmed_identity: Optional[str] = None
        
        logger.debug(
            f"[CHIMERIC-ACCUM] Initialized for track_id={track_id}: "
            f"face_window={face_window_sec}s, gait_window={gait_window_sec}s"
        )
    
    def add_face_evidence(
        self,
        evidence: FaceEvidence,
        now: Optional[float] = None
    ):
        """
        Add face evidence to buffer.
        
        Args:
            evidence: Face evidence to add
            now: Current timestamp (or use time.time())
        """
        self.face_window.add(evidence, now)
        logger.debug(
            f"[CHIMERIC-ACCUM] track_id={self.track_id} added face evidence: "
            f"id={evidence.identity_id}, sim={evidence.similarity:.3f}, "
            f"status={evidence.status.value}"
        )
    
    def add_gait_evidence(
        self,
        evidence: GaitEvidence,
        now: Optional[float] = None
    ):
        """
        Add gait evidence to buffer.
        
        Args:
            evidence: Gait evidence to add
            now: Current timestamp (or use time.time())
        """
        self.gait_window.add(evidence, now)
        logger.debug(
            f"[CHIMERIC-ACCUM] track_id={self.track_id} added gait evidence: "
            f"id={evidence.identity_id}, sim={evidence.similarity:.3f}, "
            f"status={evidence.status.value}"
        )
    
    def get_face_stability(
        self,
        now: Optional[float] = None
    ) -> Tuple[Optional[str], int, float]:
        """
        Get face evidence stability metrics.
        
        Returns:
            (primary_id, count_recent, mean_quality)
        """
        return self.face_window.get_stability(now)
    
    def get_gait_stability(
        self,
        now: Optional[float] = None
    ) -> Tuple[Optional[str], int, float]:
        """
        Get gait evidence stability metrics.
        
        Returns:
            (primary_id, count_recent, mean_quality)
        """
        return self.gait_window.get_stability(now)
    
    def check_hysteresis(
        self,
        new_identity: str,
        new_similarity: float,
        new_margin: float
    ) -> bool:
        """
        Check if new identity passes hysteresis threshold.
        
        If no confirmed identity yet, use confirm threshold.
        If confirmed identity exists, use stricter switch threshold.
        
        Args:
            new_identity: Proposed new identity
            new_similarity: Similarity score for new identity
            new_margin: Margin (sim1 - sim2)
        
        Returns:
            True if hysteresis allows this identity, False otherwise
        """
        if self.confirmed_identity is None:
            # No confirmed identity yet, use confirm threshold
            allowed = self.hysteresis.can_confirm(new_similarity, new_margin)
            logger.debug(
                f"[CHIMERIC-ACCUM] track_id={self.track_id} hysteresis check: "
                f"no confirmed identity, confirm_threshold → {allowed}"
            )
            return allowed
        
        if new_identity == self.confirmed_identity:
            # Same identity, no hysteresis needed
            return True
        
        # Switching identity, apply stricter threshold
        allowed = self.hysteresis.can_switch(
            new_similarity, new_margin, self.confirmed_identity
        )
        logger.info(
            f"[CHIMERIC-ACCUM] track_id={self.track_id} hysteresis check: "
            f"switch from {self.confirmed_identity} to {new_identity} → {allowed} "
            f"(sim={new_similarity:.3f}, margin={new_margin:.3f})"
        )
        return allowed
    
    def update_confirmed_identity(self, identity: Optional[str]):
        """
        Update confirmed identity (for hysteresis tracking).
        
        Phase 3A Enhancement:
            - Tracks confirm_streak for temporal reliability
            - Resets streak if identity changes
            - Increments streak if identity confirmed again
        
        Args:
            identity: New confirmed identity (or None)
        """
        if identity is None:
            self.confirmed_identity = None
            self.confirm_streak = 0
            self.last_confirmed_identity = None
        elif identity == self.last_confirmed_identity:
            # Same identity confirmed again → increment streak
            self.confirm_streak += 1
            self.confirmed_identity = identity
        else:
            # New identity → reset streak
            self.last_confirmed_identity = identity
            self.confirmed_identity = identity
            self.confirm_streak = 1  # First confirm of this identity
        
        logger.debug(
            f"[CHIMERIC-ACCUM] track_id={self.track_id} "
            f"confirmed_identity={identity} confirm_streak={self.confirm_streak}"
        )
    
    def get_recent_face(
        self,
        now: Optional[float] = None,
        lookback_sec: Optional[float] = None
    ) -> List[FaceEvidence]:
        """Get recent face evidence within lookback window."""
        return self.face_window.get_recent(now, lookback_sec)
    
    def get_recent_gait(
        self,
        now: Optional[float] = None,
        lookback_sec: Optional[float] = None
    ) -> List[GaitEvidence]:
        """Get recent gait evidence within lookback window."""
        return self.gait_window.get_recent(now, lookback_sec)
    
    def clear(self):
        """Clear all evidence buffers."""
        self.face_window.clear()
        self.gait_window.clear()
        self.confirmed_identity = None
        logger.debug(
            f"[CHIMERIC-ACCUM] track_id={self.track_id} buffers cleared"
        )
    
    def get_buffer_stats(self) -> Dict[str, int]:
        """
        Get buffer statistics for diagnostics.
        
        Returns:
            Dict with face_count, gait_count
        """
        return {
            "face_count": self.face_window.size(),
            "gait_count": self.gait_window.size(),
        }


# ============================================================================
# ACCUMULATOR MANAGER (Multi-Track)
# ============================================================================

class AccumulatorManager:
    """
    Manage evidence accumulators for multiple tracks.
    
    Design Pattern:
        - Lazy creation: Accumulator created on first evidence
        - Auto-cleanup: Remove accumulators for stale tracks
        - Shared config: All tracks use same hysteresis settings
    
    Usage:
        manager = AccumulatorManager()
        manager.add_face_evidence(track_id=123, evidence=face_ev)
        manager.add_gait_evidence(track_id=123, evidence=gait_ev)
        accum = manager.get_accumulator(track_id=123)
    """
    
    def __init__(
        self,
        face_window_sec: float = 2.0,
        gait_window_sec: float = 3.0,
        hysteresis_config: Optional[HysteresisConfig] = None
    ):
        """
        Initialize accumulator manager.
        
        Args:
            face_window_sec: Default face window for all tracks
            gait_window_sec: Default gait window for all tracks
            hysteresis_config: Shared hysteresis config
        """
        self.face_window_sec = face_window_sec
        self.gait_window_sec = gait_window_sec
        self.hysteresis_config = hysteresis_config or HysteresisConfig()
        
        # Track accumulators (keyed by track_id)
        self.accumulators: Dict[int, EvidenceAccumulator] = {}
        
        logger.info(
            f"[CHIMERIC-ACCUM-MGR] Initialized: "
            f"face_window={face_window_sec}s, "
            f"gait_window={gait_window_sec}s"
        )
    
    def get_or_create_accumulator(self, track_id: int) -> EvidenceAccumulator:
        """Get existing accumulator or create new one."""
        if track_id not in self.accumulators:
            self.accumulators[track_id] = EvidenceAccumulator(
                track_id=track_id,
                face_window_sec=self.face_window_sec,
                gait_window_sec=self.gait_window_sec,
                hysteresis_config=self.hysteresis_config
            )
        return self.accumulators[track_id]
    
    def add_face_evidence(
        self,
        track_id: int,
        evidence: FaceEvidence,
        now: Optional[float] = None
    ):
        """Add face evidence for a track."""
        accum = self.get_or_create_accumulator(track_id)
        accum.add_face_evidence(evidence, now)
    
    def add_gait_evidence(
        self,
        track_id: int,
        evidence: GaitEvidence,
        now: Optional[float] = None
    ):
        """Add gait evidence for a track."""
        accum = self.get_or_create_accumulator(track_id)
        accum.add_gait_evidence(evidence, now)
    
    def get_accumulator(self, track_id: int) -> Optional[EvidenceAccumulator]:
        """Get accumulator for track (or None if not exists)."""
        return self.accumulators.get(track_id)
    
    def cleanup_stale_tracks(self, active_track_ids: set):
        """
        Remove accumulators for tracks no longer active.
        
        Args:
            active_track_ids: Set of currently active track IDs
        """
        stale_tracks = [
            tid for tid in self.accumulators.keys()
            if tid not in active_track_ids
        ]
        
        for tid in stale_tracks:
            del self.accumulators[tid]
        
        if stale_tracks:
            logger.debug(
                f"[CHIMERIC-ACCUM-MGR] Cleaned up {len(stale_tracks)} "
                f"stale accumulators"
            )
    
    def get_all_stats(self) -> Dict[int, Dict[str, int]]:
        """
        Get buffer statistics for all tracks.
        
        Returns:
            Dict mapping track_id to buffer_stats
        """
        return {
            tid: accum.get_buffer_stats()
            for tid, accum in self.accumulators.items()
        }


# ============================================================================
# END OF EVIDENCE ACCUMULATOR MODULE
# ============================================================================
