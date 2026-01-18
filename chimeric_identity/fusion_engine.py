# chimeric_identity/fusion_engine.py
# ============================================================================
# CHIMERIC FUSION ENGINE - Core Decision Logic
# ============================================================================
#
# Purpose:
#   Combine face, gait, and source auth evidence into single identity decision.
#   Implements rules A-E through state machine + governance.
#
# Design:
#   - Orchestrates adapters, state machine, accumulator, and governance
#   - Clean separation: adapters → state machine → governance
#   - Single responsibility: synthesis + decision output
#
# Input: Tracklet + Face/Gait/SourceAuth decisions from engines
# Output: ChimericDecision (with learning_allowed flag)
#
# Architecture (Data Flow):
#   Tracklet + Engines' Decisions
#   → Adapters (normalize)
#   → Evidence Accumulator (buffer + hysteresis)
#   → State Machine (state transition)
#   → Governance (learning gate)
#   → ChimericDecision

from __future__ import annotations

import logging
import time
from typing import Optional, Dict, Tuple

from schemas.tracklet import Tracklet
from schemas.identity_decision import IdentityDecision

from chimeric_identity.types import (
    ChimericState,
    ChimericReason,
    ChimericDecision,
    FaceEvidence,
    GaitEvidence,
    SourceAuthEvidence,
    EvidenceStatus,
    SourceAuthState,
)
from chimeric_identity.state_machine import ChimericStateMachine
from chimeric_identity.evidence_accumulator import (
    AccumulatorManager,
    HysteresisConfig,
)
from chimeric_identity.adapters.face_adapter import FaceAdapter
from chimeric_identity.adapters.gait_adapter import GaitAdapter
from chimeric_identity.adapters.source_auth_adapter import SourceAuthAdapter
from chimeric_identity.config import ChimericConfig, default_chimeric_config
from chimeric_identity.governance import GovernanceEngine, LearningSuggestion
from chimeric_identity.bindings import BindingManager, GaitTemplate

logger = logging.getLogger(__name__)


# ============================================================================
# FUSION ENGINE
# ============================================================================

class ChimericFusionEngine:
    """
    Main fusion orchestrator.
    
    Combines face, gait, and source auth evidence into chimeric decision.
    
    Responsibility:
        - Manage adapters (stateless)
        - Manage evidence accumulator (per-track buffers + hysteresis)
        - Manage state machine (per-track state transitions)
        - Manage governance (learning permissions)
        - Produce ChimericDecision output
    
    Design:
        - Stateful per-track (accumulator, state machine)
        - Stateless adapters (pure conversion)
        - Clear data flow: evidence → state → decision
    
    Key Methods:
        - fuse(tracklet, face_decision, gait_decision, source_auth_scores)
        - cleanup_stale_tracks(active_track_ids)
    """
    
    def __init__(self, config: Optional[ChimericConfig] = None):
        """
        Initialize fusion engine.
        
        Args:
            config: ChimericConfig (or use defaults)
        """
        self.config = config or default_chimeric_config()
        
        # Adapters (stateless)
        self.face_adapter = FaceAdapter()
        self.gait_adapter = GaitAdapter()
        self.source_auth_adapter = SourceAuthAdapter(
            enabled=self.config.source_auth_enabled
        )
        
        # State machine (per-track state transitions)
        self.state_machine = ChimericStateMachine(
            face_stale_threshold_sec=self.config.temporal.face_stale_threshold_sec,
            gait_stale_threshold_sec=self.config.temporal.gait_stale_threshold_sec,
            conflict_hold_max_frames=self.config.conflict.conflict_hold_max_frames,
            conflict_hold_max_sec=self.config.conflict.conflict_hold_max_sec,
        )
        
        # Evidence accumulator (per-track buffering + hysteresis)
        hysteresis_cfg = HysteresisConfig(
            confirm_threshold=self.config.confirmation.face_confirm_threshold,
            switch_threshold=self.config.confirmation.face_switch_threshold,
            margin_bonus=self.config.confirmation.face_switch_margin_bonus,
        )
        self.accumulator_manager = AccumulatorManager(
            face_window_sec=self.config.temporal.face_evidence_window_sec,
            gait_window_sec=self.config.temporal.gait_evidence_window_sec,
            hysteresis_config=hysteresis_cfg,
        )
        
        # Governance (learning permissions)
        self.governance = GovernanceEngine(self.config)
        
        # BINDING MANAGER (Phase 3B - Face-Gait Bindings)
        self.binding_manager = BindingManager()
        
        # STATE MACHINE MANAGER (per-track) - Phase 3A
        self._state_machines_per_track: Dict[int, ChimericStateMachine] = {}
        self._track_metadata: Dict[int, Dict] = {}
        
        # Metrics
        self.decision_count = 0
        self.learning_allowed_count = 0
        self.conflict_resolution_count = 0
        
        logger.info(
            f"[CHIMERIC-FUSION-PHASE3A-3B] Engine initialized (DEEP ROBUST WIRING + BINDINGS)\\n"
            f"  Face evidence window: {self.config.temporal.face_evidence_window_sec}s\\n"
            f"  Gait evidence window: {self.config.temporal.gait_evidence_window_sec}s\\n"
            f"  Face anchor logic: ENABLED (gait cannot override)\\n"
            f"  Temporal reliability: ENABLED (real confirm_streak wired)\\n"
            f"  Per-track state machines: ENABLED\\n"
            f"  Face-gait bindings: ENABLED (Phase 3B - Confidence Boost)"
        )
    
    def _get_or_create_state_machine(self, track_id: int) -> ChimericStateMachine:
        """
        Get or create per-track state machine (Phase 3A - CRITICAL).
        
        Design Rationale:
            - Each track needs independent state history
            - Prevents cross-track contamination
            - Efficient: create on demand, cleanup when stale
            - Enables correct conflict hold logic per track
        
        Args:
            track_id: Unique track identifier
        
        Returns:
            ChimericStateMachine instance for this track
        """
        if track_id not in self._state_machines_per_track:
            sm = ChimericStateMachine(
                face_stale_threshold_sec=self.config.temporal.face_stale_threshold_sec,
                gait_stale_threshold_sec=self.config.temporal.gait_stale_threshold_sec,
                conflict_hold_max_frames=self.config.conflict.conflict_hold_max_frames,
                conflict_hold_max_sec=self.config.conflict.conflict_hold_max_sec,
            )
            self._state_machines_per_track[track_id] = sm
            
            # Initialize track metadata for temporal tracking
            self._track_metadata[track_id] = {
                "created_ts": time.time(),
                "last_update_ts": time.time(),
                "state_transitions": [],
                "conflict_count": 0,
            }
        
        # Update last access time
        self._track_metadata[track_id]["last_update_ts"] = time.time()
        
        return self._state_machines_per_track[track_id]
    
    def fuse(
        self,
        tracklet: Tracklet,
        face_identity_decision: Optional[IdentityDecision],
        gait_identity_decision: Optional[IdentityDecision],
        gait_track_state: Optional[object],
        source_auth_scores: Optional[object],
        now: Optional[float] = None
    ) -> ChimericDecision:
        """
        Main fusion method: Convert face/gait inputs to chimeric decision.
        
        Complete data flow:
        1. Normalize inputs via adapters
        2. Add to evidence accumulator (buffers + hysteresis)
        3. Update state machine
        4. Synthesize confidence
        5. Check learning permission
        6. Return ChimericDecision
        
        Args:
            tracklet: From perception (with gait_sequence_data)
            face_identity_decision: From FaceIdentityEngine
            gait_identity_decision: From GaitEngine
            gait_track_state: GaitTrackState from GaitEngine
            source_auth_scores: From SourceAuthEngine
            now: Current timestamp (or use time.time())
        
        Returns:
            ChimericDecision with state, identity, confidence, learning_allowed
        
        Design:
            Each step can be disabled/bypassed (e.g., gait can be None).
            Result is always a valid ChimericDecision (never None).
        """
        if now is None:
            now = time.time()
        
        track_id = tracklet.track_id
        
        # ===================================================================
        # STEP 1: NORMALIZE VIA ADAPTERS
        # ===================================================================
        
        face_ev: Optional[FaceEvidence] = None
        gait_ev: Optional[GaitEvidence] = None
        source_auth_ev: Optional[SourceAuthEvidence] = None
        
        try:
            # Face adapter
            if self.config.adapters_enabled and face_identity_decision:
                face_ev = self.face_adapter.adapt_decision(
                    tracklet, face_identity_decision, now
                )
            
            # Gait adapter
            if self.config.adapters_enabled and gait_track_state:
                gait_ev = self.gait_adapter.adapt_decision(
                    tracklet, gait_identity_decision, gait_track_state, now
                )
            
            # Source auth adapter
            if self.config.source_auth_enabled and source_auth_scores:
                source_auth_ev = self.source_auth_adapter.adapt_scores(
                    source_auth_scores, now
                )
        
        except Exception as e:
            logger.exception(
                f"[CHIMERIC-FUSION] track_id={track_id} adapter failed: {e}"
            )
            # Graceful degradation: return UNKNOWN
            return ChimericDecision(
                track_id=track_id,
                final_identity=None,
                chimeric_confidence=0.0,
                state=ChimericState.UNKNOWN,
                decision_reason=ChimericReason.INSUFFICIENT_EVIDENCE,
                learning_allowed=False,
                timestamp=now,
                face_evidence=face_ev,
                gait_evidence=gait_ev,
                source_auth_evidence=source_auth_ev,
            )
        
        # ===================================================================
        # STEP 2: ADD TO ACCUMULATOR
        # ===================================================================
        
        if face_ev:
            self.accumulator_manager.add_face_evidence(track_id, face_ev, now)
        
        if gait_ev:
            self.accumulator_manager.add_gait_evidence(track_id, gait_ev, now)
        
        # ===================================================================
        # STEP 3: UPDATE STATE MACHINE (Phase 3A - Per-Track)
        # ===================================================================
        
        # Get per-track state machine (creates on first use)
        state_machine = self._get_or_create_state_machine(track_id)
        
        # Update state machine with current evidence
        new_state, reason, new_identity = state_machine.update_state(
            track_id, face_ev, gait_ev, source_auth_ev, now
        )
        
        # Record state transition for debugging
        if new_identity:
            self._track_metadata[track_id]["state_transitions"].append({
                "ts": now,
                "to_state": new_state,
                "identity": new_identity,
                "reason": reason,
            })
        
        # Track conflict count for metrics
        if new_state == ChimericState.HOLD_CONFLICT:
            self._track_metadata[track_id]["conflict_count"] += 1
            self.conflict_resolution_count += 1
        
        # Update accumulator's confirmed identity (for hysteresis)
        # This ensures hysteresis logic uses real confirmed identity from state machine
        accumulator = self.accumulator_manager.get_or_create_accumulator(track_id)
        if new_state == ChimericState.CONFIRMED:
            accumulator.update_confirmed_identity(new_identity)
        
        # ===================================================================
        # STEP 4: SYNTHESIZE CONFIDENCE
        # ===================================================================
        
        chimeric_confidence = self._synthesize_confidence_quality_weighted(
            state=new_state,
            face_ev=face_ev,
            gait_ev=gait_ev,
            source_auth_ev=source_auth_ev,
            track_id=track_id
        )
        
        # ===================================================================
        # PHASE 3B: FACE-GAIT BINDING LOGIC (NEW)
        # ===================================================================
        
        # Step 3B.1: Check if we should create/update binding
        should_bind, bind_reason = self.governance.bind_face_to_gait(
            ChimericDecision(
                track_id=track_id,
                final_identity=new_identity,
                chimeric_confidence=chimeric_confidence,
                state=new_state,
                decision_reason=reason,
                learning_allowed=False,  # Not yet determined
                timestamp=now,
                face_evidence=face_ev,
                gait_evidence=gait_ev,
                source_auth_evidence=source_auth_ev,
                category="unknown",
                quality=max(face_ev.quality if face_ev else 0.0, gait_ev.quality if gait_ev else 0.0),
            ),
            now
        )
        
        binding_confidence_boost = 0.0
        if should_bind and face_ev and gait_ev and new_identity:
            # Step 3B.2: Create or retrieve binding
            gait_template = GaitTemplate(
                gait_id=gait_ev.identity_id,
                sequence_length=gait_ev.sequence_length,
                quality=gait_ev.quality,
                margin=gait_ev.margin,
                timestamp=now,
            )
            binding = self.binding_manager.create_binding(
                track_id=track_id,
                face_identity_id=new_identity,
                gait_template=gait_template,
                face_quality=face_ev.quality,
                face_confidence=chimeric_confidence,
            )
            logger.debug(
                f"[BINDING-CREATE] track={track_id} face_id={new_identity} "
                f"(strength={binding.strength.value}, reason={bind_reason})"
            )
        
        # Step 3B.3: Apply binding boost to gait if gait proposes
        if gait_ev and gait_ev.identity_id and new_state in [ChimericState.TENTATIVE, ChimericState.CONFIRMED]:
            binding = self.binding_manager.get_binding(track_id, gait_ev.identity_id, now)
            if binding:
                # Gait proposed an identity that has a binding
                effective_strength = binding.get_effective_strength(now)
                
                # Boost gait confidence using binding
                original_confidence = chimeric_confidence
                boosted_confidence, boost_reason = self.governance.evaluate_binding_strength_boost(
                    ChimericDecision(
                        track_id=track_id,
                        final_identity=new_identity,
                        chimeric_confidence=chimeric_confidence,
                        state=new_state,
                        decision_reason=reason,
                        learning_allowed=False,
                        timestamp=now,
                        face_evidence=face_ev,
                        gait_evidence=gait_ev,
                        source_auth_evidence=source_auth_ev,
                        category="unknown",
                        quality=max(face_ev.quality if face_ev else 0.0, gait_ev.quality if gait_ev else 0.0),
                    ),
                    effective_strength,
                    max_boost=0.15
                )
                
                binding_confidence_boost = boosted_confidence - original_confidence
                if binding_confidence_boost > 0.001:  # Non-negligible boost
                    chimeric_confidence = boosted_confidence
                    logger.debug(
                        f"[BINDING-BOOST] track={track_id} gait_id={gait_ev.identity_id} "
                        f"boost={binding_confidence_boost:.3f} (strength={effective_strength:.2f})"
                    )
                
                # Record observation for binding
                if gait_ev.identity_id == new_identity:
                    self.binding_manager.record_gait_match(track_id, new_identity, now)
                else:
                    self.binding_manager.record_gait_conflict(track_id, new_identity, now)
        
        # ===================================================================
        # END PHASE 3B BINDING LOGIC
        # ===================================================================
        
        # ===================================================================
        # STEP 5: DETERMINE LEARNING PERMISSION
        # ===================================================================
        
        # Build preliminary decision (before learning check)
        prelim_decision = ChimericDecision(
            track_id=track_id,
            final_identity=new_identity,
            chimeric_confidence=chimeric_confidence,
            state=new_state,
            decision_reason=reason,
            learning_allowed=False,  # Will update below
            timestamp=now,
            face_evidence=face_ev,
            gait_evidence=gait_ev,
            source_auth_evidence=source_auth_ev,
            category="unknown",
            quality=max(
                face_ev.quality if face_ev else 0.0,
                gait_ev.quality if gait_ev else 0.0
            ),
        )
        
        # Check learning permission
        learning_allowed, learning_reason = self.governance.evaluate_learning_permission(
            prelim_decision
        )
        
        self.governance.log_governance_decision(
            prelim_decision, learning_allowed, learning_reason
        )
        
        # Update decision with learning permission
        final_decision = ChimericDecision(
            track_id=track_id,
            final_identity=new_identity,
            chimeric_confidence=chimeric_confidence,
            state=new_state,
            decision_reason=reason,
            learning_allowed=learning_allowed,
            timestamp=now,
            face_evidence=face_ev,
            gait_evidence=gait_ev,
            source_auth_evidence=source_auth_ev,
            category=self._map_category(new_identity),
            quality=prelim_decision.quality,
            debug_trace={
                "learning_reason": learning_reason,
                "binding_boost": binding_confidence_boost,
                "accumulator_stats": accumulator.get_buffer_stats(),
            }
        )
        
        # ===================================================================
        # STEP 6: METRICS & LOGGING
        # ===================================================================
        
        self.decision_count += 1
        if learning_allowed:
            self.learning_allowed_count += 1
        
        self._log_decision_summary(final_decision)
        
        # Emit learning suggestion if applicable
        if learning_allowed:
            suggestion = self.governance.emit_learning_suggestion(final_decision)
            if suggestion:
                logger.info(f"[CHIMERIC-FUSION] Learning suggestion: {suggestion}")
        
        return final_decision
    
    def _compute_quality_boost(
        self,
        quality_score: float,
        quality_threshold: float,
        penalty_slope: float = 2.0
    ) -> float:
        """
        Compute quality-to-boost smooth function.
        
        KEY INSIGHT (Phase 2):
            - Above threshold: boost ≈ 1.0 (no penalty)
            - Below threshold: smooth quadratic falloff (graceful degradation)
            - Prevents cliff effects where quality just below threshold → useless
        
        Args:
            quality_score: Measured quality (0-1)
            quality_threshold: Quality ≥ this → full boost
            penalty_slope: How aggressively to penalize below threshold (1.5-2.5)
        
        Returns:
            Quality boost factor (0.0 to 1.0)
        
        Example:
            quality_boost(0.75, threshold=0.70, slope=2.0) = 1.0
            quality_boost(0.60, threshold=0.70, slope=2.0) ≈ 0.25
            quality_boost(0.50, threshold=0.70, slope=2.0) ≈ 0.02
        """
        if quality_score >= quality_threshold:
            return 1.0  # Full confidence above threshold
        
        if quality_score <= 0.0:
            return 0.0  # No boost if quality is zero
        
        # Below threshold: smooth penalty (not cliff)
        # As we go below threshold, penalty increases from 0 to 1
        normalized = quality_score / quality_threshold
        # normalized is in (0, 1) when quality < threshold
        penalty = (1.0 - normalized) ** penalty_slope
        boost = 1.0 - penalty
        
        return max(0.0, min(1.0, boost))
    
    def _compute_adaptive_weights(
        self,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        source_auth_ev: Optional[SourceAuthEvidence]
    ) -> Dict[str, float]:
        """
        Compute adaptive weights based on biometric quality and availability.
        
        KEY INSIGHT (Phase 2):
            - When face quality is HIGH: w_face → 0.80, w_gait → 0.15
            - When face quality is LOW: w_face → 0.50, w_gait → 0.40
            - When gait quality ≈ face quality: boost gait weight
            - Graceful degradation: if modality missing, redistribute weights
        
        Biometric Logic:
            - Face: accurate (98%+) but sensitive to angle/lighting
            - Gait: medium accuracy (92-96%) but robust to pose variations
            - Complementary: when one weak, other can strengthen
        
        Args:
            face_ev: Face evidence (or None)
            gait_ev: Gait evidence (or None)
            source_auth_ev: SourceAuth evidence (or None)
        
        Returns:
            Dict with 'w_face', 'w_gait', 'w_auth' (sum ≈ 1.0)
        """
        # Base weights
        w_face_base = 0.70
        w_gait_base = 0.20
        w_auth_base = 0.10
        
        # Check availability
        has_face = face_ev is not None and face_ev.status != EvidenceStatus.UNKNOWN
        has_gait = gait_ev is not None and gait_ev.status != EvidenceStatus.UNKNOWN
        
        # If missing modalities, redistribute weights
        if not has_face and has_gait:
            # Gait-only: boost gait weight
            return {'w_face': 0.0, 'w_gait': 0.90, 'w_auth': 0.10}
        
        if has_face and not has_gait:
            # Face-only: boost face weight
            return {'w_face': 0.90, 'w_gait': 0.0, 'w_auth': 0.10}
        
        if not has_face and not has_gait:
            # Neither available: even split with higher auth weight
            return {'w_face': 0.0, 'w_gait': 0.0, 'w_auth': 1.0}
        
        # Both available: Adaptive weighting based on quality
        face_quality = face_ev.quality  # 0-1
        gait_quality = gait_ev.quality  # 0-1
        
        # Quality ratio: how good is gait relative to face?
        quality_ratio = gait_quality / (face_quality + 1e-6)
        
        # Phase 2: Quality-aware weight adjustment
        if quality_ratio > 0.85:
            # Gait quality is nearly as good as face (≥85%) → boost gait weight
            w_gait_adjusted = min(
                w_gait_base + 0.15,
                self.config.adaptive_weights.w_gait_max if hasattr(self.config, 'adaptive_weights') else 0.40
            )
            w_face_adjusted = w_face_base - (w_gait_adjusted - w_gait_base)
        
        elif quality_ratio > 0.70:
            # Gait quality is decent (70-85%) → slightly boost gait weight
            w_gait_adjusted = w_gait_base + 0.08
            w_face_adjusted = w_face_base - 0.08
        
        elif face_quality < 0.60:
            # Face quality is low → boost gait weight for robustness
            w_gait_adjusted = min(
                w_gait_base + 0.20,
                self.config.adaptive_weights.w_gait_max if hasattr(self.config, 'adaptive_weights') else 0.40
            )
            w_face_adjusted = w_face_base - 0.15
        
        else:
            # Normal case: face-dominant
            w_gait_adjusted = w_gait_base
            w_face_adjusted = w_face_base
        
        # Ensure bounds
        w_auth_adjusted = w_auth_base
        
        # Normalize to sum = 1.0
        total = w_face_adjusted + w_gait_adjusted + w_auth_adjusted
        weights = {
            'w_face': w_face_adjusted / total,
            'w_gait': w_gait_adjusted / total,
            'w_auth': w_auth_adjusted / total,
        }
        
        logger.debug(
            f"[CHIMERIC-FUSION-PHASE2] Adaptive weights: "
            f"face_q={face_quality:.2f}, gait_q={gait_quality:.2f}, "
            f"w_face={weights['w_face']:.3f}, w_gait={weights['w_gait']:.3f}, w_auth={weights['w_auth']:.3f}"
        )
        
        return weights
    
    def _compute_temporal_reliability(
        self,
        gait_ev: Optional[GaitEvidence],
        track_id: Optional[int] = None
    ) -> float:
        """
        Compute gait temporal reliability (Phase 3A - WIRED ROBUST).
        
        DEEP BIOMETRIC INSIGHT:
            Gait is INHERENTLY TEMPORAL. Cannot make confident decision from
            single frame. Need 30+ frames (1-2 seconds) for reliable eval.
        
        Reliability Progression:
            - 0-30 frames: 0.30 (collecting, unreliable)
            - 30-60 frames: 0.40-0.60 (stabilizing)
            - 60-100 frames: 0.60-0.80 (good accumulation)
            - 100+ frames: 0.85-1.0 (highly reliable, +streak)
        
        Implementation (Phase 3A - NOW WIRED):
            - Gets REAL confirm_streak from accumulator
            - Uses gait_ev.sequence_length from gait engine
            - Prevents over-weighting premature gait decisions
            - Respects biometric temporal requirements
        
        Args:
            gait_ev: Gait evidence (or None)
            track_id: Track ID for accumulator lookup (Phase 3A wiring)
        
        Returns:
            Temporal reliability (0.3 to 1.0)
        """
        if gait_ev is None:
            return 0.0
        
        if gait_ev.quality is None or gait_ev.quality < 0.5:
            return 0.3  # Low quality → minimum reliability
        
        # =================================================================
        # FACTOR 1: SEQUENCE LENGTH (65% weight)
        # =================================================================
        # Frames needed for confident evaluation
        MIN_SEQUENCE_FOR_FULL = 100      # ~3-4 sec at 30fps (HIGH confidence)
        MIN_SEQUENCE_FOR_START = 30      # ~1 sec (start paying attention)
        
        seq_len = gait_ev.sequence_length or 0
        
        if seq_len < MIN_SEQUENCE_FOR_START:
            # Collecting phase: gait not reliable yet
            seq_reliability = 0.0
        elif seq_len >= MIN_SEQUENCE_FOR_FULL:
            # Sufficient accumulation: full reliability
            seq_reliability = 1.0
        else:
            # Ramping: linear interpolation from 0 to 1
            seq_reliability = (
                (seq_len - MIN_SEQUENCE_FOR_START) / 
                (MIN_SEQUENCE_FOR_FULL - MIN_SEQUENCE_FOR_START)
            )
        
        # =================================================================
        # FACTOR 2: CONFIRM STREAK (35% weight)
        # =================================================================
        # Consecutive confirms = gait is stable (Phase 3A: NOW WIRED)
        
        streak = 0
        if track_id is not None:
            try:
                accumulator = self.accumulator_manager.get_accumulator(track_id)
                if accumulator and hasattr(accumulator, 'confirm_streak'):
                    streak = accumulator.confirm_streak
                else:
                    streak = 0
            except Exception as e:
                logger.debug(
                    f"[CHIMERIC] track_id={track_id} streak lookup error: {e}"
                )
                streak = 0
        
        MIN_STREAK_FOR_FULL = 10  # ~300ms of consistent votes
        streak_reliability = min(1.0, streak / MIN_STREAK_FOR_FULL)
        
        # =================================================================
        # COMBINE WITH WEIGHTED AVERAGE (DEEP BIOMETRIC LOGIC)
        # =================================================================
        # Sequence length PRIMARY (gait must accumulate)
        # Streak SECONDARY (stability bonus)
        SEQ_WEIGHT = 0.65
        STREAK_WEIGHT = 0.35
        
        temporal_reliability = (
            SEQ_WEIGHT * seq_reliability + 
            STREAK_WEIGHT * streak_reliability
        )
        
        # Clamp: Never completely ignore gait (0.3 minimum)
        temporal_reliability = max(0.3, min(1.0, temporal_reliability))
        
        # DEBUG: Detailed logging if enabled
        if self.config.debug_trace_enabled and track_id is not None:
            logger.debug(
                f"[CHIMERIC-TEMPORAL] track_id={track_id} "
                f"seq_len={seq_len} seq_rel={seq_reliability:.2f} "
                f"streak={streak} streak_rel={streak_reliability:.2f} "
                f"→ temporal_rel={temporal_reliability:.2f}"
            )
        
        return temporal_reliability
    
    def _synthesize_confidence_quality_weighted(
        self,
        state: ChimericState,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        source_auth_ev: Optional[SourceAuthEvidence],
        track_id: Optional[str] = None
    ) -> float:
        """
        Synthesize chimeric_confidence using quality-weighted adaptive fusion.
        
        PHASE 2 ALGORITHM:
        1. Compute adaptive weights based on quality
        2. Apply quality boosts to individual confidences
        3. Compute temporal reliability for gait
        4. Fuse with conflict penalty
        5. Apply state modulation
        
        KEY INSIGHT:
            - Not equal-weight average (old method)
            - Instead: quality-aware weighting that adapts to biometric properties
            - Face: accurate, fast, quality-gated
            - Gait: robust, slow, temporal-gated
            - Together: complementary strengths create robust fusion
        
        Args:
            state: Current chimeric state
            face_ev: Face evidence (or None)
            gait_ev: Gait evidence (or None)
            source_auth_ev: SourceAuth evidence (or None)
            track_id: Track ID (for temporal reliability lookup)
        
        Returns:
            Confidence (0.0 to 1.0)
        """
        
        # ===== STEP 1: ADAPTIVE WEIGHTS =====
        weights = self._compute_adaptive_weights(face_ev, gait_ev, source_auth_ev)
        w_face = weights['w_face']
        w_gait = weights['w_gait']
        w_auth = weights['w_auth']
        
        # ===== STEP 2: INDIVIDUAL CONFIDENCES WITH QUALITY BOOST =====
        
        # Face confidence with quality boost
        face_conf = 0.0
        if face_ev and face_ev.status != EvidenceStatus.UNKNOWN:
            # Use similarity as base confidence for face (0-1)
            base_face_conf = face_ev.similarity
            
            # Get thresholds from config (with fallback to defaults)
            face_quality_threshold = 0.70
            face_penalty_slope = 2.0
            if hasattr(self.config, 'quality_thresholds'):
                face_quality_threshold = self.config.quality_thresholds.face_quality_threshold
                face_penalty_slope = self.config.quality_thresholds.face_quality_penalty_slope
            
            face_quality_boost = self._compute_quality_boost(
                face_ev.quality,
                quality_threshold=face_quality_threshold,
                penalty_slope=face_penalty_slope
            )
            face_conf = base_face_conf * face_quality_boost
            
            logger.debug(
                f"[CHIMERIC-FUSION-PHASE2] track_id={track_id} "
                f"face: base_conf={base_face_conf:.3f}, quality={face_ev.quality:.3f}, "
                f"boost={face_quality_boost:.3f}, final_conf={face_conf:.3f}"
            )
        
        # Gait confidence with quality boost + temporal reliability
        gait_conf = 0.0
        if gait_ev and gait_ev.status != EvidenceStatus.UNKNOWN:
            base_gait_conf = gait_ev.confidence  # From adapter (0-1)
            
            # Get thresholds from config (with fallback to defaults)
            gait_quality_threshold = 0.65
            gait_penalty_slope = 1.5
            if hasattr(self.config, 'quality_thresholds'):
                gait_quality_threshold = self.config.quality_thresholds.gait_quality_threshold
                gait_penalty_slope = self.config.quality_thresholds.gait_quality_penalty_slope
            
            gait_quality_boost = self._compute_quality_boost(
                gait_ev.quality,
                quality_threshold=gait_quality_threshold,
                penalty_slope=gait_penalty_slope
            )
            gait_temporal_reliability = self._compute_temporal_reliability(gait_ev, track_id)
            gait_conf = base_gait_conf * gait_quality_boost * gait_temporal_reliability
            
            logger.debug(
                f"[CHIMERIC-FUSION-PHASE2] track_id={track_id} "
                f"gait: base_conf={base_gait_conf:.3f}, quality={gait_ev.quality:.3f}, "
                f"quality_boost={gait_quality_boost:.3f}, temporal_rel={gait_temporal_reliability:.3f}, "
                f"final_conf={gait_conf:.3f}"
            )
        
        # Source auth confidence (safety gate)
        auth_conf = 0.0
        if source_auth_ev and source_auth_ev.state != SourceAuthState.UNCERTAIN:
            # Map SourceAuthState to confidence
            if source_auth_ev.state == SourceAuthState.REAL:
                auth_conf = 0.95  # High confidence it's real (not spoof)
            elif source_auth_ev.state == SourceAuthState.SPOOF:
                auth_conf = -0.80  # Strong penalty for spoof (large negative)
            else:  # UNCERTAIN
                auth_conf = 0.5  # Neutral
        
        # ===== STEP 3: WEIGHTED FUSION =====
        weighted_conf = (
            w_face * face_conf +
            w_gait * gait_conf +
            w_auth * auth_conf
        )
        
        # ===== STEP 4: CONFLICT PENALTY =====
        # If face and gait disagree on identity, reduce confidence
        # (they may both be tentative, so not fatal, but concerning)
        conflict_penalty = 0.0
        if (face_ev and gait_ev and 
            face_ev.identity_id and gait_ev.identity_id and
            face_ev.identity_id != gait_ev.identity_id):
            
            # Conflict detected: reduce confidence
            conflict_penalty = 0.15  # ~15% confidence penalty
            logger.warning(
                f"[CHIMERIC-FUSION-PHASE2] track_id={track_id} CONFLICT: "
                f"face_id={face_ev.identity_id[:8]} vs gait_id={gait_ev.identity_id[:8]}"
            )
        
        fused_conf = weighted_conf - conflict_penalty
        
        # ===== STEP 5: STATE MODULATION =====
        # Adjust confidence based on state machine decision
        if state == ChimericState.CONFIRMED:
            # Confirmed state: high confidence (face-anchored + evidence)
            state_boost = 1.0
        elif state == ChimericState.CONFIRMED_WEAK:
            # Weak confirmation: medium confidence (lower margin or lower quality)
            state_boost = 0.75
        elif state == ChimericState.TENTATIVE:
            # Tentative: low-medium confidence (gait-only without face anchor)
            state_boost = 0.55
        elif state == ChimericState.UNKNOWN:
            # Unknown: minimal confidence (no strong evidence)
            state_boost = 0.30
        elif state == ChimericState.HOLD_CONFLICT:
            # Conflict hold: very low confidence
            state_boost = 0.10
        else:
            state_boost = 0.0
        
        final_conf = fused_conf * state_boost
        
        # Clamp to [0, 1]
        final_conf = max(0.0, min(1.0, final_conf))
        
        logger.debug(
            f"[CHIMERIC-FUSION-PHASE2] track_id={track_id} confidence synthesis: "
            f"weighted={weighted_conf:.3f}, conflict_penalty={conflict_penalty:.3f}, "
            f"state_boost={state_boost:.3f}, final={final_conf:.3f}"
        )
        
        return final_conf
    
    def _synthesize_confidence(
        self,
        state: ChimericState,
        face_ev: Optional[FaceEvidence],
        gait_ev: Optional[GaitEvidence],
        source_auth_ev: Optional[SourceAuthEvidence]
    ) -> float:
        """
        Synthesize chimeric confidence from face + gait (PHASE 2 VERSION).
        
        DEPRECATED: Calls new quality-weighted method.
        Kept for compatibility, but internally uses Phase 2 logic.
        
        Args:
            state: Current chimeric state
            face_ev: Face evidence (or None)
            gait_ev: Gait evidence (or None)
            source_auth_ev: Spoof evidence (or None)
        
        Returns:
            Confidence (0-1)
        """
        # Delegate to Phase 2 quality-weighted version (no track_id available here)
        return self._synthesize_confidence_quality_weighted(
            state=state,
            face_ev=face_ev,
            gait_ev=gait_ev,
            source_auth_ev=source_auth_ev,
            track_id=None
        )
    
    def make_decision(
        self,
        track_id: str,
        current_state: ChimericState,
        face_evidence: Optional[FaceEvidence],
        gait_evidence: Optional[GaitEvidence],
        source_auth_evidence: Optional[SourceAuthEvidence],
        now: Optional[float] = None
    ) -> ChimericDecision:
        """
        Test-friendly decision API that accepts evidence objects directly.
        
        This method bypasses the adapter layer and provides a simpler interface
        for unit testing where evidence objects are already constructed.
        
        Args:
            track_id: Unique track identifier
            current_state: Current chimeric state for this track (ignored, state machine manages state)
            face_evidence: Pre-constructed FaceEvidence (or None)
            gait_evidence: Pre-constructed GaitEvidence (or None)
            source_auth_evidence: Pre-constructed SourceAuthEvidence (or None)
            now: Current timestamp (or None for time.time())
        
        Returns:
            ChimericDecision with identity, confidence, learning flag
        """
        if now is None:
            now = time.time()
        
        # Step 1: Add evidence to accumulator (for hysteresis tracking)
        if face_evidence:
            self.accumulator_manager.add_face_evidence(track_id, face_evidence, now)
        if gait_evidence:
            self.accumulator_manager.add_gait_evidence(track_id, gait_evidence, now)
        
        # Step 2: Update state machine (it determines new state from evidence)
        new_state, reason, new_identity = self.state_machine.update_state(
            track_id=track_id,
            face_ev=face_evidence,
            gait_ev=gait_evidence,
            source_auth_ev=source_auth_evidence,
            now=now
        )
        
        # Step 3: Update accumulator's confirmed identity (for hysteresis)
        accumulator = self.accumulator_manager.get_or_create_accumulator(track_id)
        if new_state == ChimericState.CONFIRMED:
            accumulator.update_confirmed_identity(new_identity)
        
        # Step 4: Synthesize confidence
        chimeric_confidence = self._synthesize_confidence_quality_weighted(
            state=new_state,
            face_ev=face_evidence,
            gait_ev=gait_evidence,
            source_auth_ev=source_auth_evidence,
            track_id=track_id
        )
        
        # Step 5: Map category
        category = self._map_category(new_identity)
        
        # Step 6: Create preliminary decision (needed for learning permission check)
        prelim_decision = ChimericDecision(
            track_id=track_id,
            state=new_state,
            decision_reason=reason,
            final_identity=new_identity,
            chimeric_confidence=chimeric_confidence,
            face_evidence=face_evidence,
            gait_evidence=gait_evidence,
            source_auth_evidence=source_auth_evidence,
            learning_allowed=False,  # Will be updated
            category=category,
            timestamp=now,
            quality=max(
                face_evidence.quality if face_evidence else 0.0,
                gait_evidence.quality if gait_evidence else 0.0
            ),
        )
        
        # Step 7: Check learning permission
        learning_allowed, learning_reason = self.governance.evaluate_learning_permission(
            prelim_decision
        )
        
        # Step 8: Create final decision with learning permission
        decision = ChimericDecision(
            track_id=track_id,
            state=new_state,
            decision_reason=reason,
            final_identity=new_identity,
            chimeric_confidence=chimeric_confidence,
            face_evidence=face_evidence,
            gait_evidence=gait_evidence,
            source_auth_evidence=source_auth_evidence,
            learning_allowed=learning_allowed,
            category=category,
            timestamp=now,
            quality=prelim_decision.quality,
        )
        
        # Step 9: Update metrics
        self.decision_count += 1
        if learning_allowed:
            self.learning_allowed_count += 1
        
        # Log
        self._log_decision_summary(decision)
        
        return decision
    
    def _map_category(self, identity_id: Optional[str]) -> str:
        """
        Map identity_id to category.
        
        Stub: In future, could query identity database.
        For now, return "unknown".
        
        Args:
            identity_id: Person ID (or None)
        
        Returns:
            Category: resident / visitor / watchlist / unknown
        """
        if identity_id is None:
            return "unknown"
        
        # TODO: Query identity database for actual category
        return "unknown"
    
    def _log_decision_summary(self, decision: ChimericDecision):
        """Log concise decision summary."""
        if not self.config.logging.one_line_summary:
            return
        
        face_info = (
            f"face={decision.face_evidence.identity_id[:8] if decision.face_evidence and decision.face_evidence.identity_id else 'N'}"
            if decision.face_evidence else "N"
        )
        
        gait_info = (
            f"gait={decision.gait_evidence.identity_id[:8] if decision.gait_evidence and decision.gait_evidence.identity_id else 'N'}"
            if decision.gait_evidence else "N"
        )
        
        learn_info = "LEARN" if decision.learning_allowed else "BLOCK"
        
        logger.info(
            f"[CHIMERIC] track={decision.track_id} state={decision.state.value} "
            f"id={decision.final_identity[:8] if decision.final_identity else 'None'} "
            f"conf={decision.chimeric_confidence:.2f} {face_info} {gait_info} {learn_info}"
        )
    
    def cleanup_stale_tracks(self, active_track_ids: set, now: Optional[float] = None):
        """
        Clean up state for tracks that no longer exist.
        
        Prevents memory growth in long-running processes.
        Includes Phase 3B binding cleanup.
        
        Args:
            active_track_ids: Set of currently active track IDs
            now: Current timestamp
        """
        if now is None:
            now = time.time()
        
        # Phase 3A: Use per-track cleanup
        stale_tracks = [
            tid for tid in self._state_machines_per_track.keys()
            if tid not in active_track_ids
        ]
        
        for tid in stale_tracks:
            del self._state_machines_per_track[tid]
            if tid in self._track_metadata:
                del self._track_metadata[tid]
            # Phase 3B: Clean up bindings for stale track
            self.binding_manager.cleanup_track(tid)
        
        self.accumulator_manager.cleanup_stale_tracks(active_track_ids)
        
        # Phase 3B: Clean up expired bindings (background task)
        self.binding_manager.cleanup_stale(now, max_age_sec=3600.0)
    
    def get_metrics(self) -> Dict[str, int]:
        """
        Get fusion engine metrics (Phase 3A-3B enhanced).
        
        Returns:
            Dict with decision_count, learning_allowed_count, binding stats
        """
        binding_stats = self.binding_manager.get_stats()
        
        return {
            "decision_count": self.decision_count,
            "learning_allowed_count": self.learning_allowed_count,
            "conflict_resolution_count": self.conflict_resolution_count,
            "active_tracks": len(self._state_machines_per_track),
            # Phase 3B binding metrics
            "total_bindings": binding_stats.total_bindings,
            "active_bindings": binding_stats.active_bindings,
            "binding_observations": binding_stats.total_observations,
            "binding_conflicts": binding_stats.total_conflicts,
            "avg_binding_strength": f"{binding_stats.avg_strength:.2f}",
        }


# ============================================================================
# END OF FUSION ENGINE MODULE
# ============================================================================
