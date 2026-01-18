# chimeric_identity/types.py
# ============================================================================
# CHIMERIC EVIDENCE & DECISION DATA STRUCTURES
# ============================================================================
#
# Purpose:
#   Normalize heterogeneous evidence (face, gait, source_auth) into unified
#   schemas for fusion engine. Bridges temporal desynchronization and scale
#   incompatibilities between fast face (100ms) and slow gait (1000ms).
#
# Design Decisions:
#   1. EVIDENCE NORMALIZATION: Each modality has explicit status enum
#   2. SEMANTIC SCORING: Not just 0-1, but with meaning (CONFIRMED vs TENTATIVE)
#   3. MARGIN AWARENESS: Similarity + margin (distance to 2nd best)
#   4. TIMESTAMP TRACKING: All evidence is time-stamped for decay logic
#   5. RICH METADATA: Contains quality, state, confidence for audit trails
#
# Key Innovation:
#   Evidence carries both quantitative (similarity/quality) and qualitative
#   (status/state) information, enabling state-aware fusion rather than
#   naive score averaging.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


# ============================================================================
# ENUMERATIONS - State & Status Labels
# ============================================================================

class ChimericState(str, Enum):
    """
    Per-track chimeric state machine states.
    
    Conservative flow:
        UNKNOWN: No reliable evidence yet
        TENTATIVE: Gait hypothesis without face anchor (low confidence)
        CONFIRMED: Face-anchored identity (high confidence)
        HOLD_CONFLICT: Face vs gait disagree; freeze decisions pending resolution
    
    Design Rationale:
        - TENTATIVE prevents blind trust in weak gait
        - HOLD_CONFLICT prevents silent identity switches
        - CONFIRMED is only achievable via face (gait cannot directly confirm)
    """
    UNKNOWN = "UNKNOWN"
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    HOLD_CONFLICT = "HOLD_CONFLICT"


class ChimericReason(str, Enum):
    """
    Decision reason codes (must be logged for every decision).
    
    These are human-readable enum strings that explain WHY a decision
    was made. Critical for debugging conflicts and drift.
    """
    # Initial / No Evidence
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_FACE_NO_GAIT = "NO_FACE_NO_GAIT"
    
    # Face Dominance (Rule A)
    FACE_CONFIRMED_DOMINATES = "FACE_CONFIRMED_DOMINATES"
    FACE_CONFIRMED_WITH_GAIT_SUPPORT = "FACE_CONFIRMED_WITH_GAIT_SUPPORT"
    FACE_CONFIRMED_GAIT_ABSENT = "FACE_CONFIRMED_GAIT_ABSENT"
    
    # Gait Proposal (Rule B)
    GAIT_TENTATIVE_NO_FACE_ANCHOR = "GAIT_TENTATIVE_NO_FACE_ANCHOR"
    GAIT_TENTATIVE_FACE_TOO_WEAK = "GAIT_TENTATIVE_FACE_TOO_WEAK"
    
    # Conflict Handling (Rule C)
    FACE_GAIT_CONFLICT_HOLD = "FACE_GAIT_CONFLICT_HOLD"
    CONFLICT_RESOLVED_FACE_STRONG = "CONFLICT_RESOLVED_FACE_STRONG"
    CONFLICT_TIMEOUT_ACCEPT_GAIT = "CONFLICT_TIMEOUT_ACCEPT_GAIT"
    
    # Quality Gates
    REJECT_LOW_FACE_QUALITY = "REJECT_LOW_FACE_QUALITY"
    REJECT_LOW_GAIT_QUALITY = "REJECT_LOW_GAIT_QUALITY"
    
    # Spoof Detection
    POSSIBLE_SPOOF_DETECTED = "POSSIBLE_SPOOF_DETECTED"
    SPOOF_CONFIRMED_BLOCK = "SPOOF_CONFIRMED_BLOCK"
    
    # Temporal Decay
    FACE_STALE_DOWNGRADE_TENTATIVE = "FACE_STALE_DOWNGRADE_TENTATIVE"
    EVIDENCE_EXPIRED = "EVIDENCE_EXPIRED"
    
    # Hysteresis
    SWITCH_BLOCKED_HYSTERESIS = "SWITCH_BLOCKED_HYSTERESIS"
    SWITCH_ALLOWED_STRONG_EVIDENCE = "SWITCH_ALLOWED_STRONG_EVIDENCE"


class EvidenceStatus(str, Enum):
    """
    Per-evidence status (normalized from subsystem states).
    
    Maps existing subsystem states into common vocabulary:
        - Face: BindingState → EvidenceStatus
        - Gait: GaitState → EvidenceStatus
    """
    MISSING = "MISSING"              # No evidence available
    UNKNOWN = "UNKNOWN"              # Evidence exists but no match
    COLLECTING = "COLLECTING"        # Gathering data (gait-specific)
    EVALUATING = "EVALUATING"        # Has data, running checks
    TENTATIVE = "TENTATIVE"          # Hypothesis proposed, not confirmed
    CONFIRMED_WEAK = "CONFIRMED_WEAK"    # Low margin / quality confirm
    CONFIRMED_STRONG = "CONFIRMED_STRONG"  # High margin / quality confirm
    STALE = "STALE"                  # Evidence too old, needs refresh


class SourceAuthState(str, Enum):
    """
    Source authenticity (spoof detection) states.
    
    Mirrors existing source_auth engine labels.
    """
    MISSING = "MISSING"
    UNCERTAIN = "UNCERTAIN"
    LIKELY_REAL = "LIKELY_REAL"
    REAL = "REAL"
    LIKELY_SPOOF = "LIKELY_SPOOF"
    SPOOF = "SPOOF"


# ============================================================================
# EVIDENCE DATACLASSES - Normalized from Subsystems
# ============================================================================

@dataclass
class FaceEvidence:
    """
    Normalized face evidence from IdentityEngine + BindingManager.
    
    Fields mirror IdentityDecision but with added temporal metadata.
    
    Key Innovation:
        - status (enum) enables state-aware fusion
        - margin (float) ensures safe matching (not just high similarity)
        - freshness_window_sec defines temporal validity
    
    Adapter Responsibility:
        face_adapter.py reads from IdentityDecision + BindingManager and
        populates this structure.
    """
    identity_id: Optional[str]          # Person ID or None
    similarity: float                   # Cosine similarity (0-1)
    quality: float                      # Face quality (0-1)
    status: EvidenceStatus              # CONFIRMED_STRONG / WEAK / UNKNOWN
    
    # Binding state from Phase C
    binding_state: Optional[str] = None  # Raw binding state string
    
    # Margin safety (distance to 2nd best match)
    margin: float = 0.0
    
    # Second best match info (for conflict detection)
    second_best_id: Optional[str] = None
    second_best_similarity: float = 0.0
    
    # Temporal metadata
    timestamp: float = field(default_factory=time.time)
    freshness_window_sec: float = 2.0   # How long this evidence is "fresh"
    
    # Source authenticity (optional integration)
    source_auth_score: Optional[float] = None
    source_auth_state: Optional[SourceAuthState] = None
    
    # Debug metadata
    extra: Optional[Dict[str, Any]] = None
    
    def is_fresh(self, now: float) -> bool:
        """Check if evidence is still within freshness window."""
        return (now - self.timestamp) <= self.freshness_window_sec
    
    def is_confirmed(self) -> bool:
        """Check if face evidence is in confirmed state."""
        return self.status in [EvidenceStatus.CONFIRMED_WEAK, EvidenceStatus.CONFIRMED_STRONG]
    
    def is_strong(self) -> bool:
        """Check if face evidence is strongly confirmed."""
        return self.status == EvidenceStatus.CONFIRMED_STRONG
    
    def age_seconds(self, now: float) -> float:
        """Return age of evidence in seconds."""
        return now - self.timestamp


@dataclass
class GaitEvidence:
    """
    Normalized gait evidence from GaitEngine + GaitTrackState.
    
    Gait is slower (1000ms minimum) but provides continuity when face absent.
    
    Key Innovation:
        - sequence_length tracks data accumulation (30+ frames needed)
        - confidence combines similarity + margin + streak
        - state_machine_state (COLLECTING/EVALUATING/CONFIRMED/UNSURE)
    
    Adapter Responsibility:
        gait_adapter.py reads from GaitEngine decision + GaitTrackState and
        populates this structure.
    """
    identity_id: Optional[str]          # Person ID or None
    similarity: float                   # Cosine similarity (0-1)
    margin: float                       # sim(best) - sim(2nd_best)
    quality: float                      # Weighted pose quality (0-1)
    status: EvidenceStatus              # TENTATIVE / CONFIRMED / COLLECTING
    
    # Gait-specific state machine
    state_machine_state: Optional[str] = None  # Raw GaitState string
    
    # Confidence (margin + streak + quality weighted)
    confidence: float = 0.0
    
    # Sequence metadata
    sequence_length: int = 0            # Frames in current sequence
    confirm_streak: int = 0             # Consecutive confirms
    
    # Second best match info
    second_best_id: Optional[str] = None
    second_best_similarity: float = 0.0
    
    # Temporal metadata
    timestamp: float = field(default_factory=time.time)
    freshness_window_sec: float = 3.0   # Gait is slower, longer window
    
    # Debug metadata
    extra: Optional[Dict[str, Any]] = None
    
    def is_fresh(self, now: float) -> bool:
        """Check if evidence is still within freshness window."""
        return (now - self.timestamp) <= self.freshness_window_sec
    
    def is_confirmed(self) -> bool:
        """Check if gait evidence is confirmed."""
        return self.status in [EvidenceStatus.CONFIRMED_WEAK, EvidenceStatus.CONFIRMED_STRONG]
    
    def is_collecting(self) -> bool:
        """Check if gait is still collecting sequence data."""
        return self.status == EvidenceStatus.COLLECTING
    
    def has_sufficient_data(self) -> bool:
        """Check if sequence length is sufficient (30+ frames)."""
        return self.sequence_length >= 30
    
    def age_seconds(self, now: float) -> float:
        """Return age of evidence in seconds."""
        return now - self.timestamp


@dataclass
class SourceAuthEvidence:
    """
    Normalized source authenticity (spoof detection) evidence.
    
    Detects if face is real 3D head or screen/photo presentation attack.
    
    Key Innovation:
        - Integrates seamlessly with face/gait fusion
        - Can block learning even if face/gait agree (spoof protection)
    
    Adapter Responsibility:
        source_auth_adapter.py reads from SourceAuth engine and populates.
    """
    realness_score: float               # 0-1 probability of real head
    state: SourceAuthState              # REAL / LIKELY_SPOOF / UNCERTAIN
    
    # Temporal metadata
    timestamp: float = field(default_factory=time.time)
    freshness_window_sec: float = 2.0
    
    # Debug metadata
    reason: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    
    def is_fresh(self, now: float) -> bool:
        """Check if evidence is still within freshness window."""
        return (now - self.timestamp) <= self.freshness_window_sec
    
    def is_spoof(self) -> bool:
        """Check if source auth indicates spoof."""
        return self.state in [SourceAuthState.LIKELY_SPOOF, SourceAuthState.SPOOF]
    
    def is_real(self) -> bool:
        """Check if source auth confirms real head."""
        return self.state in [SourceAuthState.LIKELY_REAL, SourceAuthState.REAL]
    
    def age_seconds(self, now: float) -> float:
        """Return age of evidence in seconds."""
        return now - self.timestamp


# ============================================================================
# DECISION OUTPUT - Chimeric Fusion Result
# ============================================================================

@dataclass
class ChimericDecision:
    """
    Final chimeric identity decision for a track.
    
    This is the output of the fusion engine, combining face/gait/spoof
    evidence into a single conservative decision.
    
    Key Innovation:
        - Transparent evidence summary (audit-ready)
        - Explicit learning gate (prevents drift)
        - Clear decision reason (debugging/diagnostics)
    
    Design Rationale:
        - Compatible with existing IdentityDecision schema
        - Can replace IdentityDecision in main loop
        - Contains all info needed for learning pipeline
    """
    # Core identity decision
    track_id: int
    final_identity: Optional[str]       # Person ID or None
    chimeric_confidence: float          # 0-1 fused confidence
    state: ChimericState                # UNKNOWN / TENTATIVE / CONFIRMED / HOLD
    
    # Evidence summary (transparency)
    face_evidence: Optional[FaceEvidence] = None
    gait_evidence: Optional[GaitEvidence] = None
    source_auth_evidence: Optional[SourceAuthEvidence] = None
    
    # Decision metadata
    decision_reason: ChimericReason = ChimericReason.INSUFFICIENT_EVIDENCE
    learning_allowed: bool = False      # Gate for template updates
    
    # Temporal metadata
    timestamp: float = field(default_factory=time.time)
    
    # Compatibility with existing IdentityDecision
    category: str = "unknown"           # resident/visitor/watchlist/unknown
    quality: float = 0.0                # Max quality from face/gait
    
    # Debug trace (optional verbose logging)
    debug_trace: Optional[Dict[str, Any]] = None
    
    def to_identity_decision(self):
        """
        Convert to existing IdentityDecision schema for backward compatibility.
        
        Allows chimeric to slot into existing pipeline without breaking
        downstream consumers (UI, alerts, metrics).
        """
        from schemas.identity_decision import IdentityDecision
        
        return IdentityDecision(
            track_id=self.track_id,
            identity_id=self.final_identity,
            category=self.category,
            confidence=self.chimeric_confidence,
            reason=self.decision_reason.value,
            binding_state=self.state.value,
            quality=self.quality,
            source_auth_score=(
                self.source_auth_evidence.realness_score 
                if self.source_auth_evidence else None
            ),
            source_auth_state=(
                self.source_auth_evidence.state.value
                if self.source_auth_evidence else None
            ),
            extra={
                "chimeric": True,
                "learning_allowed": self.learning_allowed,
                "face_status": self.face_evidence.status.value if self.face_evidence else None,
                "gait_status": self.gait_evidence.status.value if self.gait_evidence else None,
            }
        )
    
    def is_confirmed(self) -> bool:
        """Check if decision is in confirmed state."""
        return self.state == ChimericState.CONFIRMED
    
    def is_tentative(self) -> bool:
        """Check if decision is tentative (gait hypothesis)."""
        return self.state == ChimericState.TENTATIVE
    
    def is_conflicted(self) -> bool:
        """Check if face and gait are in conflict."""
        return self.state == ChimericState.HOLD_CONFLICT
    
    def should_block_learning(self) -> bool:
        """Check if learning should be blocked (inverse of learning_allowed)."""
        return not self.learning_allowed


# ============================================================================
# MODULE VALIDATION
# ============================================================================

def validate_evidence_consistency(
    face: Optional[FaceEvidence],
    gait: Optional[GaitEvidence]
) -> bool:
    """
    Validate that face and gait evidence are consistent (same person or None).
    
    Used by fusion engine to detect conflicts.
    
    Returns:
        True if consistent (same id or one is None)
        False if conflicting (different non-None ids)
    """
    if face is None or gait is None:
        return True
    
    if face.identity_id is None or gait.identity_id is None:
        return True
    
    return face.identity_id == gait.identity_id


# ============================================================================
# END OF TYPES MODULE
# ============================================================================
