# chimeric_identity/state_machine.py
# ============================================================================
# CHIMERIC STATE MACHINE - Conservative Identity Transitions
# ============================================================================
#
# Purpose:
#   Manage per-track state transitions for chimeric fusion. Implements
#   conservative flow where face dominates, gait proposes, and conflicts
#   halt decisions pending explicit resolution.
#
# Design Principles:
#   1. FACE DOMINANCE: Face-confirmed identity cannot be overridden by gait
#   2. GAIT PROPOSAL: Gait can suggest identity when face absent/weak
#   3. CONFLICT HOLD: Disagreements freeze output until resolution
#   4. TEMPORAL DECAY: Stale evidence triggers state downgrades
#   5. HYSTERESIS: Stricter evidence required to switch than to confirm
#
# State Flow:
#   UNKNOWN → TENTATIVE (gait proposes, no face)
#   UNKNOWN → CONFIRMED (face confirms)
#   TENTATIVE → CONFIRMED (face matches gait)
#   CONFIRMED → HOLD_CONFLICT (face/gait disagree)
#   HOLD_CONFLICT → CONFIRMED (face re-confirms strongly)
#   CONFIRMED → TENTATIVE (face stales, gait remains)
#   ANY → UNKNOWN (all evidence expires)
#
# Key Innovation:
#   State machine is EVIDENCE-AWARE, not just score-aware. Transitions
#   depend on evidence status (CONFIRMED_STRONG vs TENTATIVE) rather than
#   raw numerical thresholds.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict

from .types import (
    ChimericState,
    ChimericReason,
    FaceEvidence,
    GaitEvidence,
    SourceAuthEvidence,
    EvidenceStatus,
    SourceAuthState,
)

logger = logging.getLogger(__name__)


# ============================================================================
# TRACK STATE MEMORY
# ============================================================================

@dataclass
class TrackChimericState:
    """
    Per-track chimeric state machine memory.
    
    Tracks current state, identity hypothesis, confidence, and temporal
    metadata for state transition logic.
    
    Design Rationale:
        - Lightweight: Only essential state for transitions
        - Timestamp tracking: Enables temporal decay and stale detection
        - Conflict counter: Tracks how long in HOLD_CONFLICT
        - Identity history: Prevents flicker via hysteresis
        - Phase 3B: Binding storage for face-gait associations
    """
    track_id: int
    state: ChimericState = ChimericState.UNKNOWN
    
    # Current identity hypothesis
    current_identity: Optional[str] = None
    current_confidence: float = 0.0
    
    # Temporal tracking
    state_enter_ts: float = field(default_factory=time.time)
    last_update_ts: float = field(default_factory=time.time)
    last_face_update_ts: float = 0.0
    last_gait_update_ts: float = 0.0
    
    # Conflict tracking
    conflict_frame_count: int = 0
    conflict_face_id: Optional[str] = None
    conflict_gait_id: Optional[str] = None
    
    # Hysteresis tracking (prevent flicker)
    previous_identity: Optional[str] = None
    identity_switch_count: int = 0
    
    # State transition history (for debugging)
    transition_history: list = field(default_factory=list)
    
    # ================================================================
    # PHASE 3B: FACE-GAIT BINDING STORAGE (NEW)
    # ================================================================
    # Bindings are stored per face_identity_id discovered for this track
    # Maps face_id → FaceGaitBinding object
    # Bindings enable efficient gait confidence boosting
    face_gait_bindings: Dict = field(default_factory=dict)
    
    # Track binding lifecycle events
    last_binding_created_ts: float = 0.0
    last_binding_updated_ts: float = 0.0
    binding_update_count: int = 0
    
    def time_in_state(self, now: float) -> float:
        """Return seconds spent in current state."""
        return now - self.state_enter_ts
    
    def time_since_face(self, now: float) -> float:
        """Return seconds since last face evidence."""
        if self.last_face_update_ts == 0.0:
            return float('inf')
        return now - self.last_face_update_ts
    
    def time_since_gait(self, now: float) -> float:
        """Return seconds since last gait evidence."""
        if self.last_gait_update_ts == 0.0:
            return float('inf')
        return now - self.last_gait_update_ts
    
    def record_transition(
        self,
        from_state: ChimericState,
        to_state: ChimericState,
        reason: str,
        now: float
    ):
        """Record state transition for audit trail."""
        transition = {
            "from": from_state.value,
            "to": to_state.value,
            "reason": reason,
            "timestamp": now,
            "time_in_prev_state": now - self.state_enter_ts
        }
        self.transition_history.append(transition)
        
        # Keep last 10 transitions only (prevent memory growth)
        if len(self.transition_history) > 10:
            self.transition_history.pop(0)
    
    def enter_state(self, new_state: ChimericState, now: float):
        """Enter new state and update timestamp."""
        self.state = new_state
        self.state_enter_ts = now
        self.last_update_ts = now


# ============================================================================
# STATE MACHINE LOGIC
# ============================================================================

class ChimericStateMachine:
    """
    Conservative state machine for chimeric fusion.
    
    Implements rules A-E from chimeric plan:
        Rule A: Face dominance (gait cannot override face-confirmed)
        Rule B: Gait proposal (when face absent/weak)
        Rule C: Conflict hold (face ≠ gait → freeze)
        Rule D: Temporal decay (stale evidence → downgrade)
        Rule E: Learning gate (only when aligned + high quality)
    
    Methods:
        - update_state: Main transition logic
        - _handle_unknown: Transitions from UNKNOWN
        - _handle_tentative: Transitions from TENTATIVE
        - _handle_confirmed: Transitions from CONFIRMED
        - _handle_conflict: Transitions from HOLD_CONFLICT
    """
    
    def __init__(
        self,
        face_stale_threshold_sec: float = 6.0,
        gait_stale_threshold_sec: float = 6.0,
        conflict_hold_max_frames: int = 100,
        conflict_hold_max_sec: float = 10.0,
    ):
        """
        Initialize state machine with configurable thresholds.
        
        Args:
            face_stale_threshold_sec: Seconds without face before stale
            gait_stale_threshold_sec: Seconds without gait before stale
            conflict_hold_max_frames: Max frames in HOLD_CONFLICT
            conflict_hold_max_sec: Max seconds in HOLD_CONFLICT
        """
        self.face_stale_threshold = face_stale_threshold_sec
        self.gait_stale_threshold = gait_stale_threshold_sec
        self.conflict_hold_max_frames = conflict_hold_max_frames
        self.conflict_hold_max_sec = conflict_hold_max_sec
        
        # Track states (keyed by track_id)
        self.track_states: Dict[int, TrackChimericState] = {}
        
        logger.info(
            f"ChimericStateMachine initialized: "
            f"face_stale={face_stale_threshold_sec}s, "
            f"gait_stale={gait_stale_threshold_sec}s, "
            f"conflict_max={conflict_hold_max_sec}s"
        )
    
    def get_or_create_track_state(self, track_id: int) -> TrackChimericState:
        """Get existing track state or create new one."""
        if track_id not in self.track_states:
            self.track_states[track_id] = TrackChimericState(track_id=track_id)
            logger.debug(f"[CHIMERIC-SM] Created new track state: track_id={track_id}")
        return self.track_states[track_id]
    
    def update_state(
        self,
        track_id: int,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        source_auth_ev: Optional[SourceAuthEvidence],
        now: Optional[float] = None
    ) -> tuple[ChimericState, ChimericReason, Optional[str]]:
        """
        Update state machine based on new evidence.
        
        Main entry point for state transitions. Implements conservative
        decision flow with face dominance and conflict handling.
        
        Args:
            track_id: Track identifier
            face_ev: Face evidence (or None)
            gait_ev: Gait evidence (or None)
            source_auth_ev: Source auth evidence (or None)
            now: Current timestamp (or use time.time())
        
        Returns:
            (new_state, reason, identity)
        
        State Transition Logic:
            1. Check for spoof (source_auth) → HOLD_CONFLICT
            2. Check for stale evidence → downgrade or UNKNOWN
            3. Dispatch to state-specific handler
            4. Return new state + reason + identity
        """
        if now is None:
            now = time.time()
        
        track_state = self.get_or_create_track_state(track_id)
        old_state = track_state.state
        
        # Update evidence timestamps
        if face_ev and face_ev.is_fresh(now):
            track_state.last_face_update_ts = face_ev.timestamp
        if gait_ev and gait_ev.is_fresh(now):
            track_state.last_gait_update_ts = gait_ev.timestamp
        
        # ===================================================================
        # PRIORITY 1: Spoof Detection (blocks all decisions)
        # ===================================================================
        if source_auth_ev and source_auth_ev.is_spoof():
            if track_state.state != ChimericState.HOLD_CONFLICT:
                track_state.record_transition(
                    old_state, ChimericState.HOLD_CONFLICT,
                    "SPOOF_DETECTED", now
                )
                track_state.enter_state(ChimericState.HOLD_CONFLICT, now)
                track_state.conflict_face_id = face_ev.identity_id if face_ev else None
                logger.warning(
                    f"[CHIMERIC-SM] track_id={track_id} → HOLD_CONFLICT "
                    f"(spoof detected: {source_auth_ev.state.value})"
                )
            return (
                ChimericState.HOLD_CONFLICT,
                ChimericReason.POSSIBLE_SPOOF_DETECTED,
                None
            )
        
        # ===================================================================
        # PRIORITY 2: Check for stale evidence (temporal decay)
        # ===================================================================
        face_stale = track_state.time_since_face(now) > self.face_stale_threshold
        gait_stale = track_state.time_since_gait(now) > self.gait_stale_threshold
        both_stale = face_stale and gait_stale
        
        if both_stale and track_state.state != ChimericState.UNKNOWN:
            # All evidence expired → UNKNOWN
            track_state.record_transition(
                old_state, ChimericState.UNKNOWN,
                "EVIDENCE_EXPIRED", now
            )
            track_state.enter_state(ChimericState.UNKNOWN, now)
            track_state.current_identity = None
            track_state.current_confidence = 0.0
            logger.debug(
                f"[CHIMERIC-SM] track_id={track_id} → UNKNOWN "
                f"(all evidence expired)"
            )
            return (
                ChimericState.UNKNOWN,
                ChimericReason.EVIDENCE_EXPIRED,
                None
            )
        
        # ===================================================================
        # PRIORITY 3: Dispatch to state-specific handler
        # ===================================================================
        if track_state.state == ChimericState.UNKNOWN:
            new_state, reason, identity = self._handle_unknown(
                track_state, face_ev, gait_ev, now
            )
        elif track_state.state == ChimericState.TENTATIVE:
            new_state, reason, identity = self._handle_tentative(
                track_state, face_ev, gait_ev, face_stale, now
            )
        elif track_state.state == ChimericState.CONFIRMED:
            new_state, reason, identity = self._handle_confirmed(
                track_state, face_ev, gait_ev, face_stale, now
            )
        elif track_state.state == ChimericState.HOLD_CONFLICT:
            new_state, reason, identity = self._handle_conflict(
                track_state, face_ev, gait_ev, now
            )
        else:
            # Fallback (should never happen)
            logger.error(
                f"[CHIMERIC-SM] track_id={track_id} in unknown state: "
                f"{track_state.state}"
            )
            new_state = ChimericState.UNKNOWN
            reason = ChimericReason.INSUFFICIENT_EVIDENCE
            identity = None
        
        # ===================================================================
        # PRIORITY 4: Apply transition and update track state
        # ===================================================================
        if new_state != old_state:
            track_state.record_transition(old_state, new_state, reason.value, now)
            track_state.enter_state(new_state, now)
            logger.info(
                f"[CHIMERIC-SM] track_id={track_id} state transition: "
                f"{old_state.value} → {new_state.value} "
                f"(reason={reason.value})"
            )
        
        # Update identity tracking
        if identity != track_state.current_identity:
            track_state.previous_identity = track_state.current_identity
            track_state.current_identity = identity
            track_state.identity_switch_count += 1
        
        track_state.last_update_ts = now
        
        return (new_state, reason, identity)
    
    # ========================================================================
    # STATE-SPECIFIC HANDLERS
    # ========================================================================
    
    def _handle_unknown(
        self,
        track_state: TrackChimericState,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        now: float
    ) -> tuple[ChimericState, ChimericReason, Optional[str]]:
        """
        Handle transitions from UNKNOWN state.
        
        Possible transitions:
            - UNKNOWN → CONFIRMED (face confirms)
            - UNKNOWN → TENTATIVE (gait proposes, face absent)
            - UNKNOWN → UNKNOWN (insufficient evidence)
        
        Priority: Face evidence over gait evidence (Rule A)
        """
        # Check face first (face dominance)
        if face_ev and face_ev.is_confirmed():
            return (
                ChimericState.CONFIRMED,
                ChimericReason.FACE_CONFIRMED_DOMINATES,
                face_ev.identity_id
            )
        
        # If face absent/weak, check gait (Rule B)
        if gait_ev and gait_ev.is_confirmed():
            # Gait proposes tentative identity
            return (
                ChimericState.TENTATIVE,
                ChimericReason.GAIT_TENTATIVE_NO_FACE_ANCHOR,
                gait_ev.identity_id
            )
        
        # Insufficient evidence, stay UNKNOWN
        return (
            ChimericState.UNKNOWN,
            ChimericReason.INSUFFICIENT_EVIDENCE,
            None
        )
    
    def _handle_tentative(
        self,
        track_state: TrackChimericState,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        face_stale: bool,
        now: float
    ) -> tuple[ChimericState, ChimericReason, Optional[str]]:
        """
        Handle transitions from TENTATIVE state.
        
        TENTATIVE = gait hypothesis without face anchor.
        
        Possible transitions:
            - TENTATIVE → CONFIRMED (face arrives and matches gait)
            - TENTATIVE → HOLD_CONFLICT (face arrives but conflicts)
            - TENTATIVE → UNKNOWN (gait stales or quality drops)
            - TENTATIVE → TENTATIVE (gait updates, no face)
        """
        # Priority 1: Face evidence arrives
        if face_ev and face_ev.is_confirmed():
            current_gait_id = track_state.current_identity
            
            # Check if face matches gait hypothesis
            if face_ev.identity_id == current_gait_id:
                # Fast upgrade: face confirms gait hypothesis
                return (
                    ChimericState.CONFIRMED,
                    ChimericReason.FACE_CONFIRMED_WITH_GAIT_SUPPORT,
                    face_ev.identity_id
                )
            else:
                # Conflict: face proposes different identity
                track_state.conflict_face_id = face_ev.identity_id
                track_state.conflict_gait_id = current_gait_id
                track_state.conflict_frame_count = 0
                return (
                    ChimericState.HOLD_CONFLICT,
                    ChimericReason.FACE_GAIT_CONFLICT_HOLD,
                    None  # No output during conflict
                )
        
        # Priority 2: No face, check gait update
        if gait_ev and gait_ev.is_confirmed():
            # Gait updates hypothesis (may change identity)
            return (
                ChimericState.TENTATIVE,
                ChimericReason.GAIT_TENTATIVE_NO_FACE_ANCHOR,
                gait_ev.identity_id
            )
        
        # Priority 3: Gait quality dropped or stale
        if not gait_ev or not gait_ev.is_confirmed():
            return (
                ChimericState.UNKNOWN,
                ChimericReason.REJECT_LOW_GAIT_QUALITY,
                None
            )
        
        # Fallback: stay TENTATIVE
        return (
            ChimericState.TENTATIVE,
            ChimericReason.GAIT_TENTATIVE_NO_FACE_ANCHOR,
            track_state.current_identity
        )
    
    def _handle_confirmed(
        self,
        track_state: TrackChimericState,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        face_stale: bool,
        now: float
    ) -> tuple[ChimericState, ChimericReason, Optional[str]]:
        """
        Handle transitions from CONFIRMED state.
        
        CONFIRMED = face-anchored identity (high confidence).
        
        Possible transitions:
            - CONFIRMED → CONFIRMED (face re-confirms)
            - CONFIRMED → HOLD_CONFLICT (face/gait conflict)
            - CONFIRMED → TENTATIVE (face stales, gait remains)
            - CONFIRMED → UNKNOWN (all evidence stales)
        
        Rule A: Gait cannot override face-confirmed identity.
        """
        current_id = track_state.current_identity
        
        # Priority 1: Face proposes DIFFERENT identity (conflict!)
        if face_ev and face_ev.is_confirmed() and face_ev.identity_id != current_id:
            track_state.conflict_face_id = face_ev.identity_id
            track_state.conflict_gait_id = current_id
            track_state.conflict_frame_count = 0
            logger.warning(
                f"[CHIMERIC-SM] track_id={track_state.track_id} "
                f"CONFIRMED → HOLD_CONFLICT "
                f"(face changed: {current_id} → {face_ev.identity_id})"
            )
            return (
                ChimericState.HOLD_CONFLICT,
                ChimericReason.FACE_GAIT_CONFLICT_HOLD,
                None
            )
        
        # Priority 2: Gait conflicts with confirmed face (even if face re-confirms)
        if gait_ev and gait_ev.is_confirmed() and gait_ev.identity_id != current_id:
            track_state.conflict_face_id = current_id
            track_state.conflict_gait_id = gait_ev.identity_id
            track_state.conflict_frame_count = 0
            logger.warning(
                f"[CHIMERIC-SM] track_id={track_state.track_id} "
                f"CONFIRMED → HOLD_CONFLICT "
                f"(gait conflicts: {current_id} vs {gait_ev.identity_id})"
            )
            return (
                ChimericState.HOLD_CONFLICT,
                ChimericReason.FACE_GAIT_CONFLICT_HOLD,
                None
            )
        
        # Priority 3: Face re-confirms same identity
        if face_ev and face_ev.is_confirmed() and face_ev.identity_id == current_id:
            # Face still strong, stay CONFIRMED
            reason = (
                ChimericReason.FACE_CONFIRMED_WITH_GAIT_SUPPORT
                if (gait_ev and gait_ev.identity_id == current_id)
                else ChimericReason.FACE_CONFIRMED_GAIT_ABSENT
            )
            return (ChimericState.CONFIRMED, reason, current_id)
        
        # Priority 4: Face stale but gait remains
        if face_stale and gait_ev and gait_ev.is_confirmed():
            logger.info(
                f"[CHIMERIC-SM] track_id={track_state.track_id} "
                f"CONFIRMED → TENTATIVE (face stale, gait continues)"
            )
            return (
                ChimericState.TENTATIVE,
                ChimericReason.FACE_STALE_DOWNGRADE_TENTATIVE,
                gait_ev.identity_id
            )
        
        # Priority 5: Face weak/absent, no gait
        if (not face_ev or not face_ev.is_confirmed()) and \
           (not gait_ev or not gait_ev.is_confirmed()):
            return (
                ChimericState.UNKNOWN,
                ChimericReason.EVIDENCE_EXPIRED,
                None
            )
        
        # Fallback: stay CONFIRMED (face still fresh)
        return (
            ChimericState.CONFIRMED,
            ChimericReason.FACE_CONFIRMED_GAIT_ABSENT,
            current_id
        )
    
    def _handle_conflict(
        self,
        track_state: TrackChimericState,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        now: float
    ) -> tuple[ChimericState, ChimericReason, Optional[str]]:
        """
        Handle transitions from HOLD_CONFLICT state.
        
        HOLD_CONFLICT = face and gait disagree on identity.
        
        Resolution strategies:
            1. Face re-confirms strongly → CONFIRMED (face wins)
            2. Timeout expires → TENTATIVE (accept gait hypothesis)
            3. Evidence expires → UNKNOWN
        
        During conflict, NO identity output (prevents wrong decisions).
        """
        track_state.conflict_frame_count += 1
        time_in_conflict = track_state.time_in_state(now)
        
        # Priority 1: Face re-confirms strongly (conflict resolved)
        if face_ev and face_ev.is_strong():
            logger.info(
                f"[CHIMERIC-SM] track_id={track_state.track_id} "
                f"HOLD_CONFLICT → CONFIRMED (face re-confirmed strongly)"
            )
            return (
                ChimericState.CONFIRMED,
                ChimericReason.CONFLICT_RESOLVED_FACE_STRONG,
                face_ev.identity_id
            )
        
        # Priority 2: Timeout in conflict (accept gait hypothesis as tentative)
        if (track_state.conflict_frame_count >= self.conflict_hold_max_frames or
            time_in_conflict >= self.conflict_hold_max_sec):
            if gait_ev and gait_ev.is_confirmed():
                logger.warning(
                    f"[CHIMERIC-SM] track_id={track_state.track_id} "
                    f"HOLD_CONFLICT → TENTATIVE (timeout, accept gait)"
                )
                return (
                    ChimericState.TENTATIVE,
                    ChimericReason.CONFLICT_TIMEOUT_ACCEPT_GAIT,
                    gait_ev.identity_id
                )
            else:
                # No gait either, go to UNKNOWN
                return (
                    ChimericState.UNKNOWN,
                    ChimericReason.EVIDENCE_EXPIRED,
                    None
                )
        
        # Priority 3: Both evidence weak/expired
        if (not face_ev or not face_ev.is_confirmed()) and \
           (not gait_ev or not gait_ev.is_confirmed()):
            return (
                ChimericState.UNKNOWN,
                ChimericReason.EVIDENCE_EXPIRED,
                None
            )
        
        # Fallback: stay in HOLD_CONFLICT
        return (
            ChimericState.HOLD_CONFLICT,
            ChimericReason.FACE_GAIT_CONFLICT_HOLD,
            None
        )
    
    def cleanup_stale_tracks(self, active_track_ids: set, now: float):
        """
        Remove state for tracks that no longer exist.
        
        Prevents memory growth for long-running processes.
        
        Args:
            active_track_ids: Set of currently active track IDs
            now: Current timestamp
        """
        stale_tracks = [
            tid for tid in self.track_states.keys()
            if tid not in active_track_ids
        ]
        
        for tid in stale_tracks:
            del self.track_states[tid]
        
        if stale_tracks:
            logger.debug(
                f"[CHIMERIC-SM] Cleaned up {len(stale_tracks)} stale track states"
            )


# ============================================================================
# END OF STATE MACHINE MODULE
# ============================================================================
