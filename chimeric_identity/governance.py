# chimeric_identity/governance.py
# ============================================================================
# GOVERNANCE LAYER - Learning Gates & Confidence Policies (Rule E)
# ============================================================================
#
# Purpose:
#   Implement Rule E: Learning only happens when face confirms AND evidence
#   aligned. Prevents gait drift and spoof training.
#
# Design Principle:
#   Conservative gating: "When in doubt, don't learn."
#
# Key Responsibility:
#   Given chimeric decision, determine if learning is allowed and emit
#   learning suggestions for external enrollment pipeline.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

from chimeric_identity.config import ChimericConfig, default_chimeric_config
from chimeric_identity.types import (
    ChimericDecision,
    ChimericState,
    ChimericReason,
    FaceEvidence,
    GaitEvidence,
    SourceAuthEvidence,
    EvidenceStatus,
)

logger = logging.getLogger(__name__)


# ============================================================================
# LEARNING SUGGESTION (Output to Enrollment Pipeline)
# ============================================================================

@dataclass
class LearningSuggestion:
    """
    Suggestion to update identity template (learning).
    
    This is emitted as event/log. External enrollment pipeline
    can consume it to decide whether to update templates.
    
    Never directly modifies templates (read-only chimeric).
    """
    track_id: int
    identity_id: str
    modality: str  # "face", "gait", or "both"
    confidence: float  # Chimeric confidence behind suggestion
    
    # Evidence quality
    face_quality: Optional[float] = None
    gait_quality: Optional[float] = None
    
    # Reason why learning is suggested
    reason: str = "chimeric_suggests_learn"
    
    # Timestamp
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()
    
    def __str__(self) -> str:
        return (
            f"LearningSuggestion(track={self.track_id}, id={self.identity_id}, "
            f"modality={self.modality}, confidence={self.confidence:.3f})"
        )


# ============================================================================
# GOVERNANCE ENGINE
# ============================================================================

class GovernanceEngine:
    """
    Learning governance: Controls when templates can be updated.
    
    Responsibility:
        - Evaluate if decision should trigger learning
        - Emit learning suggestions
        - Provide explanations for learning decisions
    
    Key Methods:
        - evaluate_learning_permission(decision): bool, reason
        - should_learn_face(decision): bool
        - should_learn_gait(decision): bool
        - emit_learning_suggestion(decision): LearningSuggestion or None
    
    Design:
        - Uses chimeric decision + config to make governance decisions
        - Returns suggestions (doesn't modify templates directly)
        - Provides clear reasons for every decision
    """
    
    def __init__(self, config: Optional[ChimericConfig] = None):
        """
        Initialize governance engine.
        
        Args:
            config: ChimericConfig (or use defaults)
        """
        self.config = config or default_chimeric_config()
        logger.info(
            f"[GOVERNANCE] Initialized with face_learn_q={self.config.learning_gate.learning_face_min_quality}, "
            f"gait_learn_q={self.config.learning_gate.learning_gait_min_quality}"
        )
    
    def evaluate_learning_permission(
        self,
        decision: ChimericDecision
    ) -> tuple[bool, str]:
        """
        Evaluate if learning should be allowed for this decision.
        
        Main entry point for learning permission.
        
        Args:
            decision: ChimericDecision
        
        Returns:
            (allowed: bool, reason: str)
        
        Design:
            Uses decision state, evidence quality, and config to decide.
            Provides clear reason for every decision (critical for auditing).
        """
        # Rule E: Learning only when face confirms
        if decision.state != ChimericState.CONFIRMED:
            reason = f"state_not_confirmed ({decision.state.value})"
            return (False, reason)
        
        # Face evidence must exist and be strong
        if decision.face_evidence is None:
            return (False, "no_face_evidence")
        
        # Face quality must be high
        if decision.face_evidence.quality < self.config.learning_gate.learning_face_min_quality:
            return (
                False,
                f"face_quality_low ({decision.face_evidence.quality:.2f} < "
                f"{self.config.learning_gate.learning_face_min_quality})"
            )
        
        # Source auth must not indicate spoof
        if decision.source_auth_evidence and decision.source_auth_evidence.is_spoof():
            return (False, "source_auth_spoof")
        
        # Check face confidence
        if decision.chimeric_confidence < self.config.learning_gate.learning_min_chimeric_confidence:
            return (
                False,
                f"chimeric_confidence_low ({decision.chimeric_confidence:.2f} < "
                f"{self.config.learning_gate.learning_min_chimeric_confidence})"
            )
        
        # All gates passed
        reason = "learning_allowed"
        return (True, reason)
    
    def should_learn_face(
        self,
        decision: ChimericDecision,
        allow_override: bool = False
    ) -> bool:
        """
        Determine if face templates should be learned.
        
        Args:
            decision: ChimericDecision
            allow_override: Ignore some gates for testing
        
        Returns:
            True if face learning is allowed
        
        Design:
            Face learning is allowed if:
            1. Chimeric state is CONFIRMED
            2. Face evidence exists and is high quality
            3. Source auth is not SPOOF
        """
        if not decision.face_evidence:
            return False
        
        if decision.state != ChimericState.CONFIRMED:
            return False
        
        if decision.face_evidence.quality < self.config.learning_gate.learning_face_min_quality:
            return False
        
        if decision.source_auth_evidence and decision.source_auth_evidence.is_spoof():
            return False
        
        return True
    
    def should_learn_gait(
        self,
        decision: ChimericDecision,
        allow_override: bool = False
    ) -> bool:
        """
        Determine if gait templates should be learned.
        
        Args:
            decision: ChimericDecision
            allow_override: Ignore some gates for testing
        
        Returns:
            True if gait learning is allowed
        
        Design:
            Gait learning is MOST conservative. Requires:
            1. Face confirms (anchor)
            2. Gait matches same identity
            3. Both face and gait high quality
            4. Margin is safe (not risky)
            5. Not a spoof
        """
        # Face anchor required
        if decision.state != ChimericState.CONFIRMED:
            return False
        
        if not decision.face_evidence:
            return False
        
        # Gait evidence required
        if not decision.gait_evidence:
            return False  # No gait to learn
        
        # Gait must match face (no disagreement)
        if decision.face_evidence.identity_id != decision.gait_evidence.identity_id:
            return False  # Conflict, don't learn
        
        # Face quality
        if decision.face_evidence.quality < self.config.learning_gate.learning_face_min_quality:
            return False
        
        # Gait quality
        if decision.gait_evidence.quality < self.config.learning_gate.learning_gait_min_quality:
            return False
        
        # Gait margin safety
        if decision.gait_evidence.margin < self.config.learning_gate.learning_gait_margin_min:
            return False  # Margin too tight
        
        # No spoof
        if decision.source_auth_evidence and decision.source_auth_evidence.is_spoof():
            return False
        
        return True
    
    def emit_learning_suggestion(
        self,
        decision: ChimericDecision
    ) -> Optional[LearningSuggestion]:
        """
        Emit learning suggestion if appropriate.
        
        External enrollment pipeline consumes these suggestions.
        
        Args:
            decision: ChimericDecision
        
        Returns:
            LearningSuggestion or None
        
        Design:
            Suggests learning modality (face, gait, or both) based on
            evidence quality and alignment.
        """
        if decision.final_identity is None:
            return None
        
        should_learn_face = self.should_learn_face(decision)
        should_learn_gait = self.should_learn_gait(decision)
        
        if not (should_learn_face or should_learn_gait):
            return None
        
        # Determine modality
        if should_learn_face and should_learn_gait:
            modality = "both"
        elif should_learn_face:
            modality = "face"
        else:
            modality = "gait"
        
        suggestion = LearningSuggestion(
            track_id=decision.track_id,
            identity_id=decision.final_identity,
            modality=modality,
            confidence=decision.chimeric_confidence,
            face_quality=decision.face_evidence.quality if decision.face_evidence else None,
            gait_quality=decision.gait_evidence.quality if decision.gait_evidence else None,
            reason=f"chimeric_suggests_{modality}_learn"
        )
        
        logger.info(f"[GOVERNANCE] {suggestion}")
        
        return suggestion
    
    def get_confidence_progression(
        self,
        identity_id: str,
        recent_confidences: list[float]
    ) -> str:
        """
        Evaluate confidence progression over time.
        
        Used for telemetry: is confidence improving, stable, or declining?
        
        Args:
            identity_id: Person ID
            recent_confidences: Last N confidence values
        
        Returns:
            One of: "IMPROVING", "STABLE", "DECLINING", "UNKNOWN"
        """
        if len(recent_confidences) < 2:
            return "UNKNOWN"
        
        # Compare last to first
        first = recent_confidences[0]
        last = recent_confidences[-1]
        
        if last > first + 0.05:
            return "IMPROVING"
        elif last < first - 0.05:
            return "DECLINING"
        else:
            return "STABLE"
    
    def log_governance_decision(
        self,
        decision: ChimericDecision,
        allowed: bool,
        reason: str
    ):
        """
        Log governance decision for audit.
        
        Args:
            decision: ChimericDecision
            allowed: Whether learning is allowed
            reason: Reason for decision
        """
        status = "ALLOWED" if allowed else "BLOCKED"
        logger.info(
            f"[GOVERNANCE] track_id={decision.track_id} LEARNING_{status}: "
            f"{reason} (id={decision.final_identity}, confidence={decision.chimeric_confidence:.3f})"
        )
    
    # ================================================================
    # PHASE 3B: FACE-GAIT BINDING (NEW)
    # ================================================================
    
    def bind_face_to_gait(
        self,
        decision: ChimericDecision,
        now: float
    ) -> tuple[bool, str]:
        """
        Determine if face-gait binding should be created/updated.
        
        Deep Biometric Logic:
            When face confirms identity with high confidence, we should
            "bind" gait modality to strengthen future gait proposals.
            This creates a probabilistic link: if gait sees similar
            pattern, confidence is boosted because face already confirmed
            this person's biometric profile.
        
        Conservative Gates:
            1. State must be CONFIRMED (face has confirmed)
            2. Face evidence must exist and be high quality
            3. Gait must be available (collecting data)
            4. No spoof detected (source auth check)
            5. Not too frequently binding (prevent noise)
        
        Args:
            decision: ChimericDecision (must be CONFIRMED state)
            now: Current timestamp
        
        Returns:
            (should_bind: bool, reason: str)
        
        Design Rationale:
            - Only bind when face is confident and high quality
            - Binding strengthens incrementally (Bayesian)
            - Multiple observations of same face → stronger binding
            - Conflicts weaken binding (gait disagrees)
            - Temporal decay (old bindings less useful)
        """
        # Gate 1: Must be confirmed state
        if decision.state != ChimericState.CONFIRMED:
            return (False, f"state_not_confirmed ({decision.state.value})")
        
        # Gate 2: Must have face evidence
        if not decision.face_evidence:
            return (False, "no_face_evidence")
        
        # Gate 3: Face must be strong
        if decision.face_evidence.quality < self.config.learning_gate.learning_face_min_quality:
            return (
                False,
                f"face_quality_low ({decision.face_evidence.quality:.2f})"
            )
        
        # Gate 4: Must have gait evidence to bind to
        if not decision.gait_evidence:
            return (False, "no_gait_evidence")
        
        # Gate 5: Gait must be collecting or tentative (not confirmed directly)
        if decision.gait_evidence.status not in [
            EvidenceStatus.COLLECTING,
            EvidenceStatus.EVALUATING,
            EvidenceStatus.TENTATIVE,
        ]:
            return (False, f"gait_not_bindable ({decision.gait_evidence.status.value})")
        
        # Gate 6: Spoof check
        if decision.source_auth_evidence and decision.source_auth_evidence.is_spoof():
            return (False, "source_auth_spoof_detected")
        
        # Gate 7: Confidence must be strong
        if decision.chimeric_confidence < self.config.learning_gate.learning_min_chimeric_confidence:
            return (
                False,
                f"confidence_low ({decision.chimeric_confidence:.2f})"
            )
        
        # All gates passed
        reason = "binding_allowed"
        return (True, reason)
    
    def evaluate_binding_strength_boost(
        self,
        decision: ChimericDecision,
        binding_strength: float,
        max_boost: float = 0.15
    ) -> tuple[float, str]:
        """
        Evaluate confidence boost from binding.
        
        Deep Biometric Logic:
            When gait proposes an identity, check if it's bound to
            a previously confirmed face. If yes, boost confidence.
            Boost is proportional to:
            1. Binding strength (how sure is the binding)
            2. Gait quality (better gait → larger boost)
            3. Consistency (no conflicts → larger boost)
        
        Conservative Design:
            - Never exceed high confidence (0.95)
            - Binding is hint, not override
            - Decay if evidence ages
        
        Args:
            decision: ChimericDecision
            binding_strength: Effective binding strength (0-1)
            max_boost: Maximum confidence boost (default 15%)
        
        Returns:
            (boosted_confidence: float, reason: str)
        """
        if not decision.gait_evidence:
            return (decision.chimeric_confidence, "no_gait_evidence")
        
        # Binding must be strong enough to boost
        if binding_strength < 0.3:
            return (decision.chimeric_confidence, "binding_too_weak")
        
        # Base boost from binding strength
        boost = binding_strength * max_boost
        
        # Quality-weighted boost (higher gait quality → bigger boost)
        gait_quality_factor = decision.gait_evidence.quality  # 0-1
        boost *= gait_quality_factor
        
        # Apply boost (capped at max)
        boosted_confidence = min(
            1.0,
            decision.chimeric_confidence + boost
        )
        
        reason = f"binding_boost ({binding_strength:.2f} × {gait_quality_factor:.2f})"
        return (boosted_confidence, reason)


# ================================================================
# END OF PHASE 3B BINDING METHODS
# ================================================================


# ============================================================================
# CONFIDENCE PROGRESSION TRACKER
# ============================================================================

class ConfidenceProgressionTracker:
    """
    Track confidence progression over time per track.
    
    Useful for:
        - Detecting confidence growth (sign of learning working)
        - Detecting confidence collapse (sign of quality issue)
        - Telemetry
    """
    
    def __init__(self, window_size: int = 20):
        """
        Initialize tracker.
        
        Args:
            window_size: Keep last N confidence values
        """
        self.window_size = window_size
        self.track_confidences: Dict[int, list[float]] = {}
    
    def add_confidence(self, track_id: int, confidence: float):
        """Add confidence value for track."""
        if track_id not in self.track_confidences:
            self.track_confidences[track_id] = []
        
        self.track_confidences[track_id].append(confidence)
        
        # Keep window
        if len(self.track_confidences[track_id]) > self.window_size:
            self.track_confidences[track_id].pop(0)
    
    def get_progression(self, track_id: int) -> str:
        """Get progression for track."""
        if track_id not in self.track_confidences:
            return "UNKNOWN"
        
        values = self.track_confidences[track_id]
        if len(values) < 2:
            return "UNKNOWN"
        
        mean_first_half = sum(values[:len(values)//2]) / (len(values)//2 or 1)
        mean_second_half = sum(values[len(values)//2:]) / (len(values) - len(values)//2 or 1)
        
        if mean_second_half > mean_first_half + 0.05:
            return "IMPROVING"
        elif mean_second_half < mean_first_half - 0.05:
            return "DECLINING"
        else:
            return "STABLE"


# ============================================================================
# END OF GOVERNANCE MODULE
# ============================================================================
