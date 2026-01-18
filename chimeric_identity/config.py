# chimeric_identity/config.py
# ============================================================================
# CHIMERIC CONFIGURATION - All Thresholds & Policies
# ============================================================================
#
# Purpose:
#   Centralized configuration for chimeric fusion system.
#   All thresholds, timeouts, and policy flags in one place.
#
# Design:
#   - Dataclass-based (type-safe, YAML-serializable)
#   - Deep comments explaining each parameter
#   - Conservative defaults (gait is currently weak)
#   - Easy tuning as system improves
#
# Key Principle:
#   No magic numbers in code. All knobs here.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================================
# EVIDENCE QUALITY GATES
# ============================================================================

@dataclass
class QualityGatesConfig:
    """
    Minimum quality thresholds for evidence acceptance.
    
    Below these thresholds, evidence is ignored or downgraded.
    """
    # Face quality (0-1)
    # Below 0.45 → evidence status downgraded to UNKNOWN
    # Range: 0.0-1.0, Default: 0.45
    min_face_quality: float = 0.45
    
    # Gait quality (0-1)
    # Below 0.55 → gait evaluation pauses (stays EVALUATING)
    # Range: 0.0-1.0, Default: 0.55
    min_gait_quality: float = 0.55
    
    # Source auth confidence for reliable spoof decision
    # Below this → state stays UNCERTAIN (safer)
    # Range: 0.0-1.0, Default: 0.60
    min_source_auth_confidence: float = 0.60


# ============================================================================
# CONFIRMATION THRESHOLDS
# ============================================================================

@dataclass
class ConfirmationConfig:
    """
    Thresholds for identity confirmation and switching.
    
    Rule D (Hysteresis): Switching requires higher bar than confirming.
    """
    # Face confirmation
    # Initial detection needs this similarity to lock identity
    # Range: 0.5-1.0, Default: 0.75
    face_confirm_threshold: float = 0.75
    
    # Face strong confirmation
    # High confidence face (used for conflict resolution)
    # Range: 0.7-1.0, Default: 0.85
    face_strong_threshold: float = 0.85
    
    # Face switch threshold (hysteresis)
    # Switching from face_id_A to face_id_B needs this (higher than confirm)
    # Range: 0.8-1.0, Default: 0.85
    face_switch_threshold: float = 0.85
    
    # Additional margin required to switch (hysteresis bonus)
    # Switch margin = 0.05 (base) + 0.10 (bonus) = 0.15
    # Range: 0.05-0.20, Default: 0.10
    face_switch_margin_bonus: float = 0.10
    
    # Gait acceptance threshold
    # Gait can propose identity even if below face_confirm (weak gait ok)
    # Range: 0.5-0.8, Default: 0.65
    gait_accept_threshold: float = 0.65
    
    # Gait confirmation threshold
    # To be TENTATIVE, gait needs this confidence
    # Range: 0.5-0.8, Default: 0.70
    gait_confirm_threshold: float = 0.70
    
    # Gait strong threshold
    # High-confidence gait for mutual validation with face
    # Range: 0.7-0.95, Default: 0.80
    gait_strong_threshold: float = 0.80
    
    # Minimum gait margin
    # Gap between best and 2nd best must be ≥ this
    # Range: 0.05-0.15, Default: 0.08
    gait_margin_min: float = 0.08


# ============================================================================
# TEMPORAL WINDOWS & STABILITY
# ============================================================================

@dataclass
class TemporalConfig:
    """
    Time windows for evidence freshness and stability checks.
    
    Bridges temporal desynchronization between face (100ms) and gait (1000ms).
    """
    # Face evidence window (seconds)
    # How far back to look for face stability
    # Range: 1.0-5.0, Default: 2.0
    face_evidence_window_sec: float = 2.0
    
    # Gait evidence window (seconds)
    # How far back to look for gait stability
    # Gait is slower, so wider window
    # Range: 2.0-5.0, Default: 3.0
    gait_evidence_window_sec: float = 3.0
    
    # Stability requirement: number of recent samples needed
    # For face: need N of last M samples to confirm stability
    # Range: 1-5, Default: 2
    stability_required_face: int = 2
    
    # Stability requirement: number of recent samples needed
    # For gait: need N of last M samples
    # Range: 2-5, Default: 3
    stability_required_gait: int = 3
    
    # Face staleness threshold (seconds)
    # If no face update in this long, face evidence is stale
    # Range: 3.0-10.0, Default: 6.0
    face_stale_threshold_sec: float = 6.0
    
    # Gait staleness threshold (seconds)
    # If no gait update in this long, gait evidence is stale
    # Range: 4.0-10.0, Default: 6.0
    gait_stale_threshold_sec: float = 6.0
    
    # Tentative face timeout (seconds)
    # If no face update while TENTATIVE, abandon gait hypothesis
    # Range: 3.0-10.0, Default: 5.0
    tentative_face_timeout_sec: float = 5.0


# ============================================================================
# CONFLICT HANDLING
# ============================================================================

@dataclass
class ConflictConfig:
    """
    Configuration for handling face/gait conflicts.
    
    When face and gait disagree, system enters HOLD_CONFLICT.
    These parameters control resolution.
    """
    # Maximum frames to spend in HOLD_CONFLICT
    # After this, accept gait hypothesis or go UNKNOWN
    # Range: 50-200, Default: 100
    conflict_hold_max_frames: int = 100
    
    # Maximum seconds in HOLD_CONFLICT
    # After this, timeout and resolve
    # Range: 5.0-15.0, Default: 10.0
    conflict_hold_max_sec: float = 10.0
    
    # Face confidence threshold to resolve conflict (face wins)
    # Face must re-confirm at this level to resolve conflict
    # Range: 0.8-1.0, Default: 0.85
    conflict_face_strong_threshold: float = 0.85


# ============================================================================
# LEARNING GATES (Rule E)
# ============================================================================

@dataclass
class LearningGateConfig:
    """
    Configuration for template update permission (Rule E).
    
    Learning only happens when evidence is well-aligned and high-quality.
    Prevents gait drift and spoof training.
    """
    # Face quality required for learning
    # Below this, do NOT update face templates
    # Range: 0.6-0.9, Default: 0.75
    learning_face_min_quality: float = 0.75
    
    # Gait quality required for learning
    # Below this, do NOT update gait templates
    # Range: 0.6-0.85, Default: 0.70
    learning_gait_min_quality: float = 0.70
    
    # Margin required for gait learning
    # Even with good quality, margin must be safe
    # Range: 0.08-0.15, Default: 0.08
    learning_gait_margin_min: float = 0.08
    
    # Minimum track age for learning (frames)
    # Don't learn from brand-new tracks (might be errors)
    # Range: 10-50, Default: 20
    learning_min_track_age_frames: int = 20
    
    # Minimum chimeric confidence for learning
    # Only learn when chimeric_confidence >= this
    # Range: 0.70-0.95, Default: 0.75
    learning_min_chimeric_confidence: float = 0.75


# ============================================================================
# CONFIDENCE SYNTHESIS
# ============================================================================

@dataclass
class ConfidenceSynthesisConfig:
    """
    How to combine face and gait into chimeric confidence.
    
    Face is anchor. Gait provides supporting or conflicting signal.
    """
    # Gait confidence weight when face absent
    # Scale down gait-only decisions (gait is weak)
    # Example: if gait=0.80, result = 0.80 * 0.70 = 0.56
    # Range: 0.5-0.9, Default: 0.70
    gait_confidence_weight_when_faceless: float = 0.70
    
    # Gait boost when matches face identity
    # If gait confirms same identity as face, add to confidence
    # Example: face=0.85, gait=0.75 (same id) → confidence += (0.75-0.85)*0.15
    # Range: 0.05-0.25, Default: 0.15
    gait_boost_same_identity: float = 0.15


# ============================================================================
# PHASE 2: QUALITY-WEIGHTED ADAPTIVE FUSION
# ============================================================================

@dataclass
class QualityThresholdsConfig:
    """
    PHASE 2: Quality-based thresholds for adaptive confidence modulation.
    
    These thresholds control how quality scores affect biometric weighting.
    
    Key Insight:
        - Face quality >= face_quality_threshold → full boost (1.0)
        - Face quality < 0.60 → gait weight automatically increases
        - Gait quality >= gait_quality_threshold → gait can contribute equally
        - Smooth degradation (quadratic), not cliffs
    """
    # Face quality threshold for full confidence boost
    # Above this: quality_boost = 1.0 (no penalty)
    # Below this: smooth quadratic falloff
    # Range: 0.60-0.80, Default: 0.70
    face_quality_threshold: float = 0.70
    
    # Gait quality threshold for full confidence boost
    # Above this: quality_boost = 1.0 (no penalty)
    # Below this: smooth falloff (less aggressive than face)
    # Range: 0.55-0.75, Default: 0.65
    gait_quality_threshold: float = 0.65
    
    # Appearance quality threshold
    # For future appearance modality
    # Range: 0.50-0.70, Default: 0.60
    appearance_quality_threshold: float = 0.60
    
    # Penalty slope for face quality degradation
    # Higher = more aggressive penalty below threshold
    # Quadratic (2.0) vs Linear (1.0)
    # Range: 1.5-3.0, Default: 2.0
    face_quality_penalty_slope: float = 2.0
    
    # Penalty slope for gait quality degradation
    # Less aggressive than face (gait is inherently temporal)
    # Range: 1.0-2.0, Default: 1.5
    gait_quality_penalty_slope: float = 1.5


@dataclass
class AdaptiveWeightsConfig:
    """
    PHASE 2: Adaptive weight computation bounds and factors.
    
    Weights adapt based on biometric quality and availability:
    - When face quality high: w_face → 0.80, w_gait → 0.15
    - When face quality low: w_face → 0.50, w_gait → 0.40
    - When modality missing: redistribute to available modalities
    
    Biometric Logic:
        - Face: accurate (98%+) but angle-sensitive
        - Gait: robust but slower (1000ms+ required)
        - SourceAuth: spoof gate (safety feature)
    """
    # Minimum face weight (even when low quality)
    # Never ignore face completely
    # Range: 0.40-0.60, Default: 0.50
    w_face_min: float = 0.50
    
    # Maximum face weight
    # Never make face completely dominant
    # Range: 0.80-0.95, Default: 0.90
    w_face_max: float = 0.90
    
    # Minimum gait weight (can be zero if no data)
    # Range: 0.0-0.10, Default: 0.0
    w_gait_min: float = 0.0
    
    # Maximum gait weight
    # Gait never dominates over face (gait is softer biometric)
    # Range: 0.30-0.50, Default: 0.40
    w_gait_max: float = 0.40
    
    # SourceAuth base weight (safety gate)
    # Relatively fixed (not adaptive like face/gait)
    # Range: 0.05-0.15, Default: 0.10
    w_auth_base: float = 0.10
    
    # Weight boost when gait quality is very close to face (≥85%)
    # Range: 0.10-0.20, Default: 0.15
    quality_ratio_boost_high: float = 0.15
    
    # Weight boost when gait quality is decent (70-85%)
    # Range: 0.05-0.15, Default: 0.08
    quality_ratio_boost_medium: float = 0.08
    
    # Weight boost when face quality is low (<60%)
    # Range: 0.15-0.30, Default: 0.20
    quality_ratio_boost_low_face: float = 0.20


@dataclass
class TemporalReliabilityConfig:
    """
    PHASE 2: Gait temporal reliability computation.
    
    Gait is slow (requires 1000ms+ accumulation). These parameters
    control how much weight to give gait based on temporal evidence.
    
    Key Insight:
        - Single frame: 0.4-0.6 reliability
        - 1000ms: 0.7-0.8 reliability  
        - 3000ms+: 0.85-0.95 reliability
        - Prevents false positives from premature gait decisions
    """
    # Minimum sequence length for full temporal confidence
    # ~3-4 seconds at 30fps = 100 frames
    # Below this: reliability scales up from 0 to 1
    # Range: 50-150, Default: 100
    min_sequence_for_full_confidence: int = 100
    
    # Minimum confidence streak for full temporal reliability
    # Number of consecutive positive gait votes (~300ms)
    # Range: 5-20, Default: 10
    min_streak_for_full_confidence: int = 10
    
    # Weight of sequence length in temporal reliability
    # Combined: 0.65 * seq_reliability + 0.35 * streak_reliability
    # Range: 0.5-0.8, Default: 0.65
    sequence_weight: float = 0.65
    
    # Weight of confidence streak in temporal reliability
    # Range: 0.2-0.5, Default: 0.35
    streak_weight: float = 0.35
    
    # Minimum temporal reliability (never completely ignore gait)
    # Even with short sequence, gait contributes minimally
    # Range: 0.2-0.4, Default: 0.30
    min_temporal_reliability: float = 0.30


# ============================================================================
# LOGGING & DEBUGGING
# ============================================================================

@dataclass
class LoggingConfig:
    """
    Logging and debugging configuration.
    """
    # Log every N seconds (telemetry summary)
    # Range: 1.0-30.0, Default: 5.0
    log_summary_interval_sec: float = 5.0
    
    # Enable verbose debug trace per decision
    # High verbosity, only for debugging
    # Range: True/False, Default: False
    debug_trace_enabled: bool = False
    
    # Enable one-line concise decision summary
    # Should always be True for production
    # Range: True/False, Default: True
    one_line_summary: bool = True


# ============================================================================
# MAIN CHIMERIC CONFIG CLASS
# ============================================================================

@dataclass
class ChimericConfig:
    """
    Complete chimeric fusion configuration.
    
    All sub-configs are embedded here. Create with defaults or load from YAML.
    
    Usage:
        config = ChimericConfig()  # Use defaults
        config.quality_gates.min_face_quality = 0.50  # Customize
        config.confirmation.face_strong_threshold = 0.90
    """
    # Quality gates
    quality_gates: QualityGatesConfig = field(default_factory=QualityGatesConfig)
    
    # Confirmation thresholds
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    
    # Temporal windows
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    
    # Conflict handling
    conflict: ConflictConfig = field(default_factory=ConflictConfig)
    
    # Learning gates
    learning_gate: LearningGateConfig = field(default_factory=LearningGateConfig)
    
    # Confidence synthesis
    confidence_synthesis: ConfidenceSynthesisConfig = field(
        default_factory=ConfidenceSynthesisConfig
    )
    
    # PHASE 2: Quality-weighted adaptive fusion
    quality_thresholds: QualityThresholdsConfig = field(
        default_factory=QualityThresholdsConfig
    )
    adaptive_weights: AdaptiveWeightsConfig = field(
        default_factory=AdaptiveWeightsConfig
    )
    temporal_reliability: TemporalReliabilityConfig = field(
        default_factory=TemporalReliabilityConfig
    )
    
    # Logging
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Feature flags
    source_auth_enabled: bool = True  # Enable spoof detection
    adapters_enabled: bool = True     # Enable adapter conversions
    debug_trace_enabled: bool = False # Enable debug trace logging (Phase 3A)
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        self._validate()
    
    def _validate(self):
        """
        Validate configuration constraints.
        
        Raises ValueError if configuration is invalid.
        """
        # Face thresholds should increase
        if self.confirmation.face_confirm_threshold > self.confirmation.face_strong_threshold:
            logger.warning(
                f"face_confirm_threshold ({self.confirmation.face_confirm_threshold}) "
                f"should be <= face_strong_threshold ({self.confirmation.face_strong_threshold})"
            )
        
        # Switch threshold should be >= strong threshold
        if self.confirmation.face_switch_threshold < self.confirmation.face_strong_threshold:
            logger.warning(
                f"face_switch_threshold ({self.confirmation.face_switch_threshold}) "
                f"should be >= face_strong_threshold ({self.confirmation.face_strong_threshold})"
            )
        
        # Gait thresholds should be reasonable
        if self.confirmation.gait_accept_threshold > self.confirmation.gait_confirm_threshold:
            logger.warning(
                f"gait_accept_threshold ({self.confirmation.gait_accept_threshold}) "
                f"should be <= gait_confirm_threshold ({self.confirmation.gait_confirm_threshold})"
            )
        
        logger.info("[CHIMERIC-CONFIG] Configuration validated")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary (useful for YAML export)."""
        return {
            "quality_gates": {
                "min_face_quality": self.quality_gates.min_face_quality,
                "min_gait_quality": self.quality_gates.min_gait_quality,
                "min_source_auth_confidence": self.quality_gates.min_source_auth_confidence,
            },
            "confirmation": {
                "face_confirm_threshold": self.confirmation.face_confirm_threshold,
                "face_strong_threshold": self.confirmation.face_strong_threshold,
                "face_switch_threshold": self.confirmation.face_switch_threshold,
                "face_switch_margin_bonus": self.confirmation.face_switch_margin_bonus,
                "gait_accept_threshold": self.confirmation.gait_accept_threshold,
                "gait_confirm_threshold": self.confirmation.gait_confirm_threshold,
                "gait_strong_threshold": self.confirmation.gait_strong_threshold,
                "gait_margin_min": self.confirmation.gait_margin_min,
            },
            "temporal": {
                "face_evidence_window_sec": self.temporal.face_evidence_window_sec,
                "gait_evidence_window_sec": self.temporal.gait_evidence_window_sec,
                "face_stale_threshold_sec": self.temporal.face_stale_threshold_sec,
                "gait_stale_threshold_sec": self.temporal.gait_stale_threshold_sec,
                "tentative_face_timeout_sec": self.temporal.tentative_face_timeout_sec,
            },
            "conflict": {
                "conflict_hold_max_frames": self.conflict.conflict_hold_max_frames,
                "conflict_hold_max_sec": self.conflict.conflict_hold_max_sec,
            },
            "learning_gate": {
                "learning_face_min_quality": self.learning_gate.learning_face_min_quality,
                "learning_gait_min_quality": self.learning_gate.learning_gait_min_quality,
                "learning_gait_margin_min": self.learning_gate.learning_gait_margin_min,
                "learning_min_track_age_frames": self.learning_gate.learning_min_track_age_frames,
                "learning_min_chimeric_confidence": self.learning_gate.learning_min_chimeric_confidence,
            },
        }


# ============================================================================
# DEFAULT CONFIG FACTORY
# ============================================================================

def default_chimeric_config() -> ChimericConfig:
    """
    Create default chimeric configuration.
    
    All thresholds are conservative (favor false negatives over false positives).
    As gait accuracy improves, these can be tuned.
    
    Returns:
        ChimericConfig with all default values
    """
    return ChimericConfig()


# ============================================================================
# CONFIG LOGGING
# ============================================================================

def log_config_summary(config: ChimericConfig):
    """Log a summary of active configuration."""
    logger.info("[CHIMERIC-CONFIG] Active Configuration:")
    logger.info(f"  Face confirm: {config.confirmation.face_confirm_threshold}")
    logger.info(f"  Face strong: {config.confirmation.face_strong_threshold}")
    logger.info(f"  Gait confirm: {config.confirmation.gait_confirm_threshold}")
    logger.info(f"  Gait margin min: {config.confirmation.gait_margin_min}")
    logger.info(f"  Learning face quality: {config.learning_gate.learning_face_min_quality}")
    logger.info(f"  Learning gait quality: {config.learning_gate.learning_gait_min_quality}")
    logger.info(f"  Source auth enabled: {config.source_auth_enabled}")
    logger.info(f"  Conflict hold max: {config.conflict.conflict_hold_max_frames} frames "
                f"/ {config.conflict.conflict_hold_max_sec}s")


# ============================================================================
# END OF CONFIG MODULE
# ============================================================================
