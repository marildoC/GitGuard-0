# chimeric_identity/bindings.py
# ============================================================================
# PHASE 3B: FACE-GAIT BINDING SYSTEM
# ============================================================================
#
# Purpose:
#   Establish robust biometric bindings between face and gait modalities.
#   When face confirms an identity, store that binding to boost gait
#   confidence in future observations of the same person.
#
# Design Innovation:
#   1. TEMPORAL BINDING: Bindings decay with time (stale bindings less useful)
#   2. QUALITY-WEIGHTED: Bindings stronger if created from high-quality evidence
#   3. CONSISTENCY TRACKING: Observe how often gait matches face-bound identity
#   4. CONFIDENCE PROPAGATION: Use binding strength to boost gait confidence
#   5. CONFLICT DETECTION: Bindings help detect when gait disagrees with face
#
# Biometric Logic:
#   - When face_id is confirmed with quality Q, create binding: face_id → gait_template
#   - When gait proposes identity, check if bound to face: if yes, boost confidence
#   - If gait disagrees with face binding, trigger conflict detection
#   - Bindings strengthen with consistent observations (Bayesian update)
#   - Old bindings weaken (temporal decay) until invalidated by conflict
#
# Key Benefit:
#   Gait becomes ANCHORED to face observations, improving confidence while
#   maintaining conservative decision policy. Gait alone cannot change identity,
#   but CAN strengthen belief when aligned with face.

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMERATIONS
# ============================================================================

class BindingStrength(str, Enum):
    """Enumeration of binding strength levels (Bayesian scale)."""
    WEAK = "WEAK"                    # Single observation, low quality
    MODERATE = "MODERATE"           # Few consistent observations
    STRONG = "STRONG"               # Many consistent observations, high quality
    VERY_STRONG = "VERY_STRONG"     # Extensive history, perfect consistency


class BindingStatus(str, Enum):
    """Status of a face-gait binding."""
    ACTIVE = "ACTIVE"               # Binding is current and valid
    WEAKENED = "WEAKENED"           # Binding age or conflicts reducing strength
    INVALIDATED = "INVALIDATED"     # Gait disagreed with binding (conflict)
    EXPIRED = "EXPIRED"             # Too old to be useful


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class GaitTemplate:
    """
    Lightweight representation of gait template at binding time.
    
    Design:
        - Stores essential features for matching without full template
        - Timestamp enables temporal decay calculations
        - Quality metadata for confidence weighting
    """
    gait_id: Optional[str]              # Gait subsystem's gait_id or None
    sequence_length: int = 0            # Frames in sequence when binding created
    quality: float = 0.0                # Quality score at binding time (0-1)
    margin: float = 0.0                 # Margin (distance to 2nd best)
    
    # Temporal metadata
    timestamp: float = field(default_factory=time.time)
    
    def age_seconds(self, now: float) -> float:
        """Return age in seconds."""
        return now - self.timestamp
    
    def is_stale(self, now: float, max_age_sec: float = 300.0) -> bool:
        """Check if gait template is too old (default: 5 minutes)."""
        return self.age_seconds(now) > max_age_sec


@dataclass
class FaceGaitBinding:
    """
    Binding between confirmed face identity and gait template.
    
    Created when face confirms an identity with high confidence.
    Used to boost gait confidence when gait proposes same identity.
    
    Design Rationale:
        - Lightweight storage (reference to gait template, not full data)
        - Strength tracked probabilistically (Bayesian approach)
        - Status enables lifecycle management
        - History allows analyzing binding reliability
    """
    # Core binding
    face_identity_id: str               # Person's identity (from face)
    gait_template: GaitTemplate         # Gait at binding time
    
    # Binding metadata
    binding_id: str = ""                # Unique binding identifier
    created_timestamp: float = field(default_factory=time.time)
    
    # Strength tracking (Bayesian)
    strength: BindingStrength = BindingStrength.WEAK
    strength_value: float = 0.25        # Bayesian prior for WEAK
    
    # Quality of binding (from face evidence quality at creation time)
    face_quality_at_binding: float = 0.9
    face_confidence_at_binding: float = 0.85
    
    # Observation tracking
    observations: int = 0              # How many times gait matched this binding
    consistency_count: int = 0         # Consecutive matches
    conflict_count: int = 0            # Times gait disagreed
    
    # Status lifecycle
    status: BindingStatus = BindingStatus.ACTIVE
    last_observed_timestamp: float = field(default_factory=time.time)
    invalidated_timestamp: Optional[float] = None
    
    # Extra metadata
    extra: Dict = field(default_factory=dict)
    
    def age_seconds(self, now: float) -> float:
        """Return age of binding in seconds."""
        return now - self.created_timestamp
    
    def time_since_observation(self, now: float) -> float:
        """Return seconds since last observation."""
        return now - self.last_observed_timestamp
    
    def is_active(self, now: Optional[float] = None) -> bool:
        """Check if binding is still active."""
        if self.status == BindingStatus.ACTIVE:
            return True
        if self.status == BindingStatus.INVALIDATED:
            return False
        # WEAKENED bindings can still be active but with reduced boost
        return self.status != BindingStatus.EXPIRED
    
    def temporal_decay_factor(self, now: float, half_life_sec: float = 3600.0) -> float:
        """
        Calculate temporal decay factor (exponential decay).
        
        Design:
            - Bindings decay over time following exponential model
            - Half-life (default 1 hour): binding strength drops to 50% after 1h
            - Formula: decay = 0.5 ^ (age / half_life)
            - Min value: 0.1 (binding never completely disappears)
        
        Args:
            now: Current timestamp
            half_life_sec: Time for binding to decay to 50% strength (default 1h)
        
        Returns:
            Decay factor (0.1 to 1.0)
        """
        age_sec = self.age_seconds(now)
        # Exponential decay: 0.5^(t/half_life)
        decay = 0.5 ** (age_sec / half_life_sec)
        # Clamp to reasonable minimum (don't let binding disappear completely)
        return max(0.1, decay)
    
    def observation_recency_factor(self, now: float, memory_window_sec: float = 60.0) -> float:
        """
        Boost binding strength if recently observed (recency heuristic).
        
        Design:
            - Recent observations strengthen binding temporarily
            - Encourages consistency tracking
            - Formula: recency = 1.0 if within window, decays after
            - Window default: 60 seconds (enough for multi-frame gait)
        
        Args:
            now: Current timestamp
            memory_window_sec: Time window for recency boost
        
        Returns:
            Recency boost factor (0.5 to 1.5)
        """
        time_since = self.time_since_observation(now)
        if time_since <= memory_window_sec:
            # Recently observed: 1.5x boost at time=0, decays to 1.0 at window end
            return 1.0 + (0.5 * (1.0 - time_since / memory_window_sec))
        else:
            # Old observation: gradual decay
            return max(0.5, 1.0 - (time_since / (memory_window_sec * 10)))
    
    def consistency_factor(self) -> float:
        """
        Boost binding based on consistency (% of observations that matched).
        
        Design:
            - If gait has observed N times, and matched N-C times, consistency = 1 - C/N
            - High consistency (>90%): 1.2x boost
            - Moderate consistency (70-90%): 1.0x (neutral)
            - Low consistency (<70%): 0.8x penalty
        
        Returns:
            Consistency factor (0.8 to 1.2)
        """
        if self.observations == 0:
            return 1.0  # No observations yet, neutral
        
        consistency_ratio = max(0.0, 1.0 - (self.conflict_count / max(1, self.observations)))
        
        if consistency_ratio >= 0.9:
            return 1.2  # High consistency boost
        elif consistency_ratio >= 0.7:
            return 1.0  # Moderate consistency, neutral
        else:
            return 0.8  # Low consistency penalty
    
    def get_effective_strength(self, now: float) -> float:
        """
        Calculate current effective binding strength combining all factors.
        
        Design (Deep Biometric Logic):
            effective_strength = base_strength × temporal_decay × recency × consistency
        
        This creates a sophisticated binding strength that accounts for:
            1. How many observations support this binding
            2. How long ago binding was created (temporal decay)
            3. How recently gait was observed matching this binding (recency)
            4. How consistent gait matching has been (conflict history)
        
        Returns:
            Effective strength (0.0 to 1.0)
        """
        # Base Bayesian strength
        base = self.strength_value
        
        # Apply temporal decay (older bindings weaker)
        decay = self.temporal_decay_factor(now)
        
        # Apply recency boost (recently observed stronger)
        recency = self.observation_recency_factor(now)
        
        # Apply consistency factor (frequent conflicts weaken binding)
        consistency = self.consistency_factor()
        
        # Combined: all multiplicative
        effective = base * decay * recency * consistency
        
        # Clamp to valid range
        return max(0.0, min(1.0, effective))
    
    def boost_gait_confidence(
        self,
        base_confidence: float,
        now: float,
        max_boost: float = 0.15
    ) -> float:
        """
        Boost gait confidence based on binding strength.
        
        Design (Deep Biometric Logic):
            - Only boost if binding is active and consistent
            - Boost amount = effective_strength × max_boost × quality_factor
            - Prevents over-reliance on binding (cap boost at 15%)
            - Conservative: binding enhances but doesn't replace gait quality
        
        Args:
            base_confidence: Gait's original confidence (0-1)
            now: Current timestamp
            max_boost: Maximum confidence boost (default 15%)
        
        Returns:
            Boosted confidence (base_confidence to base_confidence + max_boost)
        """
        if not self.is_active(now):
            return base_confidence
        
        effective_strength = self.get_effective_strength(now)
        
        # Apply quality gate: only boost if effective strength > threshold
        if effective_strength < 0.3:
            return base_confidence  # Too weak to boost
        
        # Conservative boost: scale by effective strength
        # scaling: 0.3-0.5 → 0-25%, 0.5-0.8 → 25-50%, 0.8+ → 50-100%
        strength_scale = max(0.0, (effective_strength - 0.3) / 0.7)  # Normalize to 0-1
        boost = strength_scale * max_boost
        
        # Conservative: never exceed 1.0 with boost
        return min(1.0, base_confidence + boost)
    
    def record_match(self, now: float):
        """Record observation where gait matched this binding."""
        self.observations += 1
        self.consistency_count += 1
        self.last_observed_timestamp = now
        
        # Strengthen binding (Bayesian update)
        # Observation supports binding: increase strength_value
        self.strength_value = min(1.0, self.strength_value + 0.1)
        self._update_strength_enum()
    
    def record_conflict(self, now: float):
        """Record observation where gait disagreed with this binding."""
        self.observations += 1
        self.conflict_count += 1
        self.consistency_count = 0  # Reset consistency streak
        self.last_observed_timestamp = now
        
        # Conflict weakens binding (Bayesian update)
        self.strength_value = max(0.0, self.strength_value - 0.15)
        self._update_strength_enum()
        
        # If too many conflicts, invalidate binding
        if self.conflict_count >= 3:
            self.status = BindingStatus.INVALIDATED
            self.invalidated_timestamp = now
    
    def _update_strength_enum(self):
        """Update strength enum based on strength_value."""
        if self.strength_value < 0.35:
            self.strength = BindingStrength.WEAK
        elif self.strength_value < 0.60:
            self.strength = BindingStrength.MODERATE
        elif self.strength_value < 0.85:
            self.strength = BindingStrength.STRONG
        else:
            self.strength = BindingStrength.VERY_STRONG


# ============================================================================
# BINDING MANAGER
# ============================================================================

@dataclass
class BindingStats:
    """Statistics about bindings for observability."""
    total_bindings: int = 0
    active_bindings: int = 0
    weakened_bindings: int = 0
    invalidated_bindings: int = 0
    expired_bindings: int = 0
    
    avg_age_seconds: float = 0.0
    avg_strength: float = 0.0
    total_observations: int = 0
    total_conflicts: int = 0


class BindingManager:
    """
    Manage face-gait bindings per track.
    
    Responsibility:
        - Create bindings when face confirms with high confidence
        - Track observation matches/conflicts
        - Calculate binding strength and decay
        - Boost gait confidence based on active bindings
        - Clean up stale/expired bindings
        - Provide observability via stats
    
    Design:
        - Per-track: bindings stored in track state
        - Non-invasive: doesn't modify face/gait engines
        - Efficient: lightweight Bayesian tracking
        - Conservative: only boosts, doesn't override
    """
    
    def __init__(self):
        """Initialize binding manager."""
        self.bindings_by_track: Dict[int, Dict[str, FaceGaitBinding]] = {}
        logger.info("[BINDING-MANAGER] Initialized (Phase 3B)")
    
    def create_binding(
        self,
        track_id: int,
        face_identity_id: str,
        gait_template: GaitTemplate,
        face_quality: float = 0.9,
        face_confidence: float = 0.85
    ) -> FaceGaitBinding:
        """
        Create new face-gait binding.
        
        Called when face confirms identity with high confidence.
        
        Args:
            track_id: Track ID
            face_identity_id: Confirmed identity ID
            gait_template: Gait representation at binding time
            face_quality: Quality of face evidence
            face_confidence: Chimeric confidence at binding
        
        Returns:
            Created FaceGaitBinding
        """
        if track_id not in self.bindings_by_track:
            self.bindings_by_track[track_id] = {}
        
        # Create binding
        binding_id = f"{track_id}_{face_identity_id}_{time.time():.0f}"
        binding = FaceGaitBinding(
            binding_id=binding_id,
            face_identity_id=face_identity_id,
            gait_template=gait_template,
            face_quality_at_binding=face_quality,
            face_confidence_at_binding=face_confidence,
            strength=BindingStrength.WEAK,
            strength_value=0.25,  # Start conservative
        )
        
        # Store binding
        self.bindings_by_track[track_id][face_identity_id] = binding
        
        logger.debug(
            f"[BINDING] Created binding {binding_id} for track {track_id} → {face_identity_id} "
            f"(face_q={face_quality:.2f}, conf={face_confidence:.2f})"
        )
        
        return binding
    
    def get_binding(
        self,
        track_id: int,
        face_identity_id: str,
        now: float
    ) -> Optional[FaceGaitBinding]:
        """Get active binding for face identity."""
        if track_id not in self.bindings_by_track:
            return None
        
        binding = self.bindings_by_track[track_id].get(face_identity_id)
        if binding is None:
            return None
        
        if binding.is_active(now):
            return binding
        
        return None
    
    def record_gait_match(
        self,
        track_id: int,
        face_identity_id: str,
        now: float
    ):
        """Record gait observation matching this binding."""
        binding = self.get_binding(track_id, face_identity_id, now)
        if binding:
            binding.record_match(now)
            logger.debug(
                f"[BINDING] Match recorded for binding {binding.binding_id} "
                f"(observations={binding.observations}, strength={binding.strength.value})"
            )
    
    def record_gait_conflict(
        self,
        track_id: int,
        face_identity_id: str,
        now: float
    ):
        """Record gait observation conflicting with this binding."""
        binding = self.get_binding(track_id, face_identity_id, now)
        if binding:
            binding.record_conflict(now)
            logger.debug(
                f"[BINDING] Conflict recorded for binding {binding.binding_id} "
                f"(conflicts={binding.conflict_count}, strength={binding.strength.value})"
            )
    
    def cleanup_track(self, track_id: int):
        """Clean up bindings for track (called on track removal)."""
        if track_id in self.bindings_by_track:
            count = len(self.bindings_by_track[track_id])
            del self.bindings_by_track[track_id]
            logger.debug(f"[BINDING] Cleaned up {count} bindings for track {track_id}")
    
    def cleanup_stale(self, now: float, max_age_sec: float = 3600.0):
        """Clean up expired bindings (background task)."""
        tracks_to_clean = []
        total_expired = 0
        
        for track_id, bindings in self.bindings_by_track.items():
            expired_ids = []
            for face_id, binding in bindings.items():
                if binding.age_seconds(now) > max_age_sec:
                    binding.status = BindingStatus.EXPIRED
                    expired_ids.append(face_id)
                    total_expired += 1
            
            # Clean up expired
            for face_id in expired_ids:
                del self.bindings_by_track[track_id][face_id]
            
            # Clean up empty track entries
            if not self.bindings_by_track[track_id]:
                tracks_to_clean.append(track_id)
        
        for track_id in tracks_to_clean:
            del self.bindings_by_track[track_id]
        
        if total_expired > 0:
            logger.debug(f"[BINDING] Cleaned up {total_expired} expired bindings")
    
    def get_stats(self) -> BindingStats:
        """Get binding statistics for monitoring."""
        stats = BindingStats()
        all_bindings: List[FaceGaitBinding] = []
        
        for bindings in self.bindings_by_track.values():
            for binding in bindings.values():
                all_bindings.append(binding)
                stats.total_bindings += 1
                
                if binding.status == BindingStatus.ACTIVE:
                    stats.active_bindings += 1
                elif binding.status == BindingStatus.WEAKENED:
                    stats.weakened_bindings += 1
                elif binding.status == BindingStatus.INVALIDATED:
                    stats.invalidated_bindings += 1
                else:
                    stats.expired_bindings += 1
                
                stats.total_observations += binding.observations
                stats.total_conflicts += binding.conflict_count
        
        if all_bindings:
            ages = [b.age_seconds(time.time()) for b in all_bindings]
            stats.avg_age_seconds = sum(ages) / len(ages)
            strengths = [b.strength_value for b in all_bindings]
            stats.avg_strength = sum(strengths) / len(strengths)
        
        return stats
