# chimeric_identity/phase3c_integration.py
"""
PHASE 3C INTEGRATION - Deep Biometric Fusion Logic

This module handles the core Phase 3C logic for real-time fusion of:
- Face evidence (fast, 100-200ms)
- Gait evidence (slow, 1000ms+)
- State machine decisions (per-track identity)
- Binding system (confidence boost)

DESIGN PHILOSOPHY (Deep & Robust):
  1. TEMPORAL AWARENESS: Gait needs time to accumulate (1000ms+)
  2. EVIDENCE QUALITY: Conservative gates on all decisions
  3. CONFLICT RESOLUTION: Face-gait conflicts properly tracked
  4. LEARNING VALIDATION: Track when/why learning happens
  5. MEMORY STABILITY: Long-running stability

This is NOT micro-testing. This is testing what actually matters.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

from chimeric_identity.types import (
    ChimericState,
    ChimericReason,
    ChimericDecision,
    FaceEvidence,
    GaitEvidence,
    EvidenceStatus,
)
from chimeric_identity.fusion_engine import ChimericFusionEngine
from chimeric_identity.bindings import BindingManager
from schemas.identity_decision import IdentityDecision


logger = logging.getLogger(__name__)


# ============================================================================
# PHASE 3C INTEGRATION - DEEP BIOMETRIC LOGIC
# ============================================================================

@dataclass
class TrackBiometricContext:
    """
    Per-track biometric context for Phase 3C.
    
    Tracks:
    - Evidence accumulation over time
    - Temporal dynamics (gait needs time)
    - Binding lifecycle (creation, strengthening, invalidation)
    - Learning opportunities
    """
    
    track_id: int
    created_at: float
    last_face_time: float = 0.0
    last_gait_time: float = 0.0
    
    # Face observations (for temporal awareness)
    face_observations: list = field(default_factory=list)  # [(time, confidence, identity_id), ...]
    
    # Gait observations (for temporal awareness)
    gait_observations: list = field(default_factory=list)  # [(time, confidence, gait_id), ...]
    
    # Binding tracking
    current_binding_id: Optional[str] = None
    binding_change_count: int = 0
    
    # Conflict tracking
    conflict_count: int = 0
    last_conflict_time: float = 0.0
    
    # Learning tracking
    face_learning_available: bool = False
    gait_learning_available: bool = False
    learning_fired_count: int = 0
    
    # Decision tracking
    decision_history: list = field(default_factory=list)  # Recent decisions
    state_transition_count: int = 0
    
    def add_face_observation(self, confidence: float, identity_id: Optional[str], now: float):
        """Record face observation."""
        self.face_observations.append((now, confidence, identity_id))
        self.last_face_time = now
        # Keep only last 10 observations
        if len(self.face_observations) > 10:
            self.face_observations.pop(0)
    
    def add_gait_observation(self, confidence: float, gait_id: Optional[str], now: float):
        """Record gait observation."""
        self.gait_observations.append((now, confidence, gait_id))
        self.last_gait_time = now
        # Keep only last 10 observations
        if len(self.gait_observations) > 10:
            self.gait_observations.pop(0)
    
    def get_face_stability(self) -> Tuple[bool, float]:
        """
        Check if face observations are stable (same identity).
        
        Returns:
            (is_stable, confidence_trend)
        """
        if len(self.face_observations) < 3:
            return False, 0.0
        
        # All last 3 must be same identity and high confidence
        recent = self.face_observations[-3:]
        identities = [obs[2] for obs in recent]
        confidences = [obs[1] for obs in recent]
        
        if len(set(identities)) != 1:
            return False, 0.0  # Identity changed
        
        if any(c < 0.70 for c in confidences):
            return False, min(confidences)  # Low confidence
        
        return True, sum(confidences) / len(confidences)
    
    def get_gait_stability(self) -> Tuple[bool, float]:
        """
        Check if gait observations are stable (same person).
        
        Returns:
            (is_stable, confidence_trend)
        """
        if len(self.gait_observations) < 2:
            return False, 0.0
        
        # All recent must be same gait pattern
        recent = self.gait_observations[-2:]
        gait_ids = [obs[2] for obs in recent]
        confidences = [obs[1] for obs in recent]
        
        if len(set(gait_ids)) != 1:
            return False, 0.0  # Gait changed
        
        if any(c < 0.60 for c in confidences):
            return False, min(confidences)  # Low confidence
        
        return True, sum(confidences) / len(confidences)


class Phase3CIntegration:
    """
    Deep robust Phase 3C integration for real-time chimeric biometric fusion.
    
    Responsibility:
    - Manage per-track biometric context
    - Orchestrate temporal evidence accumulation
    - Track binding lifecycle in real conditions
    - Validate learning gates
    - Provide deep metrics for validation
    
    This is the "glue" between raw evidence and chimeric decisions.
    """
    
    def __init__(self, fusion_engine: ChimericFusionEngine):
        """Initialize Phase 3C integration."""
        self.fusion_engine = fusion_engine
        self.binding_manager = fusion_engine.binding_manager
        self.contexts: Dict[int, TrackBiometricContext] = {}
        
        logger.info("[PHASE3C-INTEGRATION] Initialized for real-time fusion")
    
    def process_track_evidence(
        self,
        track_id: int,
        face_decision: Optional[IdentityDecision],
        gait_decision: Optional[IdentityDecision],
        now: float
    ) -> Tuple[Optional[ChimericDecision], Dict]:
        """
        Process evidence for a track (deep robust logic).
        
        Args:
            track_id: Track ID
            face_decision: Face engine decision (or None)
            gait_decision: Gait engine decision (or None)
            now: Current timestamp
        
        Returns:
            (chimeric_decision, diagnostics_dict)
        """
        
        # Get or create context
        if track_id not in self.contexts:
            self.contexts[track_id] = TrackBiometricContext(
                track_id=track_id,
                created_at=now
            )
        
        ctx = self.contexts[track_id]
        diagnostics = {}
        
        # ====================================================================
        # STEP 1: RECORD OBSERVATIONS (Temporal Awareness)
        # ====================================================================
        
        if face_decision and face_decision.decision == 'IDENTIFIED':
            face_conf = getattr(face_decision, 'confidence', 0.0)
            face_id = getattr(face_decision, 'identity_id', None)
            ctx.add_face_observation(face_conf, face_id, now)
            diagnostics['face_confidence'] = face_conf
        
        if gait_decision and gait_decision.decision == 'IDENTIFIED':
            gait_conf = getattr(gait_decision, 'confidence', 0.0)
            gait_id = getattr(gait_decision, 'gait_template_id', None)
            ctx.add_gait_observation(gait_conf, gait_id, now)
            diagnostics['gait_confidence'] = gait_conf
        
        # ====================================================================
        # STEP 2: ANALYZE STABILITY (Deep Robustness)
        # ====================================================================
        
        face_stable, face_confidence_trend = ctx.get_face_stability()
        gait_stable, gait_confidence_trend = ctx.get_gait_stability()
        
        diagnostics['face_stable'] = face_stable
        diagnostics['gait_stable'] = gait_stable
        diagnostics['face_confidence_trend'] = face_confidence_trend
        diagnostics['gait_confidence_trend'] = gait_confidence_trend
        
        # ====================================================================
        # STEP 3: DETECT CONFLICTS (Key Robustness Test)
        # ====================================================================
        
        has_conflict = self._detect_binding_conflict(ctx, now)
        if has_conflict:
            ctx.conflict_count += 1
            ctx.last_conflict_time = now
            diagnostics['conflict_detected'] = True
        
        # ====================================================================
        # STEP 4: CALL CHIMERIC FUSION
        # ====================================================================
        
        try:
            chimeric_decision = self.fusion_engine.fuse(
                track_id=track_id,
                face_evidence=self._build_face_evidence(face_decision, face_stable),
                gait_evidence=self._build_gait_evidence(gait_decision, gait_stable),
                frame_timestamp=now
            )
        except Exception as e:
            logger.error(f"[PHASE3C] Fusion error for track {track_id}: {e}")
            return None, diagnostics
        
        if not chimeric_decision:
            return None, diagnostics
        
        # ====================================================================
        # STEP 5: TRACK BINDING LIFECYCLE (Phase 3B Validation)
        # ====================================================================
        
        # Did a binding get created?
        if chimeric_decision.state == ChimericState.CONFIRMED:
            binding_id = getattr(chimeric_decision, 'binding_id', None)
            if binding_id and binding_id != ctx.current_binding_id:
                ctx.current_binding_id = binding_id
                ctx.binding_change_count += 1
                diagnostics['binding_created'] = True
            
            # Did binding boost confidence?
            if getattr(chimeric_decision, 'binding_boost_applied', False):
                diagnostics['binding_boost'] = getattr(chimeric_decision, 'binding_boost_value', 0.0)
        
        # ====================================================================
        # STEP 6: VALIDATE LEARNING GATES (Critical for Phase 3C)
        # ====================================================================
        
        learning_allowed = getattr(chimeric_decision, 'learning_allowed', False)
        if learning_allowed:
            ctx.learning_fired_count += 1
            diagnostics['learning_allowed'] = True
            
            # Deep tracking: When did learning happen?
            time_since_creation = now - ctx.created_at
            diagnostics['learning_at_sec'] = time_since_creation
        
        # ====================================================================
        # STEP 7: RECORD DECISION
        # ====================================================================
        
        ctx.decision_history.append({
            'time': now,
            'state': chimeric_decision.state,
            'confidence': chimeric_decision.confidence,
        })
        # Keep only last 50 decisions
        if len(ctx.decision_history) > 50:
            ctx.decision_history.pop(0)
        
        # Track state transitions
        if len(ctx.decision_history) >= 2:
            prev_state = ctx.decision_history[-2]['state']
            curr_state = chimeric_decision.state
            if prev_state != curr_state:
                ctx.state_transition_count += 1
                diagnostics['state_transition'] = f"{prev_state} → {curr_state}"
        
        return chimeric_decision, diagnostics
    
    def _detect_binding_conflict(self, ctx: TrackBiometricContext, now: float) -> bool:
        """
        Detect if gait contradicts current binding.
        
        This is a KEY validation test for Phase 3B bindings.
        """
        if ctx.current_binding_id is None:
            return False
        
        # Get binding
        binding = self.binding_manager.get_binding(ctx.current_binding_id)
        if not binding:
            return False
        
        # If recent gait doesn't match binding's gait template
        if not ctx.gait_observations:
            return False
        
        # Check if last 2 gait observations changed
        if len(ctx.gait_observations) >= 2:
            recent_gaits = [obs[2] for obs in ctx.gait_observations[-2:]]
            if len(set(recent_gaits)) > 1:
                # Gait changed - possible conflict
                return True
        
        return False
    
    def _build_face_evidence(
        self,
        face_decision: Optional[IdentityDecision],
        is_stable: bool
    ) -> Optional[FaceEvidence]:
        """Build structured face evidence for fusion."""
        if not face_decision:
            return None
        
        try:
            return FaceEvidence(
                identity_id=getattr(face_decision, 'identity_id', None),
                confidence=getattr(face_decision, 'confidence', 0.0),
                status=EvidenceStatus.CONFIRMED if is_stable else EvidenceStatus.TENTATIVE,
                quality=getattr(face_decision, 'quality', 0.5),
                timestamp=time.time(),
            )
        except Exception as e:
            logger.error(f"[PHASE3C] Face evidence build error: {e}")
            return None
    
    def _build_gait_evidence(
        self,
        gait_decision: Optional[IdentityDecision],
        is_stable: bool
    ) -> Optional[GaitEvidence]:
        """Build structured gait evidence for fusion."""
        if not gait_decision:
            return None
        
        try:
            return GaitEvidence(
                gait_template_id=getattr(gait_decision, 'gait_template_id', None),
                confidence=getattr(gait_decision, 'confidence', 0.0),
                status=EvidenceStatus.CONFIRMED if is_stable else EvidenceStatus.TENTATIVE,
                quality=getattr(gait_decision, 'quality', 0.5),
                sequence_length=getattr(gait_decision, 'sequence_length', 0),
                timestamp=time.time(),
            )
        except Exception as e:
            logger.error(f"[PHASE3C] Gait evidence build error: {e}")
            return None
    
    def cleanup_track(self, track_id: int):
        """Cleanup track context."""
        if track_id in self.contexts:
            ctx = self.contexts[track_id]
            logger.debug(
                f"[PHASE3C] Cleanup track {track_id}: "
                f"binding_changes={ctx.binding_change_count}, "
                f"conflicts={ctx.conflict_count}, "
                f"learning_gates={ctx.learning_fired_count}"
            )
            del self.contexts[track_id]
    
    def get_track_diagnostics(self, track_id: int) -> Dict:
        """Get deep diagnostics for a track (for validation)."""
        if track_id not in self.contexts:
            return {}
        
        ctx = self.contexts[track_id]
        uptime = time.time() - ctx.created_at
        
        return {
            'track_id': track_id,
            'uptime_sec': uptime,
            'face_observations': len(ctx.face_observations),
            'gait_observations': len(ctx.gait_observations),
            'binding_changes': ctx.binding_change_count,
            'conflicts': ctx.conflict_count,
            'learning_gates_fired': ctx.learning_fired_count,
            'state_transitions': ctx.state_transition_count,
            'decisions_recorded': len(ctx.decision_history),
        }
    
    def cleanup_all(self):
        """Cleanup all tracks."""
        for track_id in list(self.contexts.keys()):
            self.cleanup_track(track_id)
        
        logger.info(f"[PHASE3C] All tracks cleaned up")
