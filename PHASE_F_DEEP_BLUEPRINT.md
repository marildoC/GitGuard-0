# Phase F - Simultaneous Merge Manager: Deep Implementation Blueprint

**Status**: Design Complete, Ready for Implementation
**Complexity**: High (simultaneous track correlation)
**Risk Level**: Medium (trade-off analysis complete)
**Testing Strategy**: Comprehensive (edge case focus)

---

## 1. Phase F Architecture & Algorithms

### 1.1 Core Concept

**Phase E vs Phase F Comparison**

```
PHASE E (Time-Exclusive Merging) ✅ IMPLEMENTED
├─ Merges ended track → new track (handoff scenario)
├─ Very safe (no simultaneous conflicts)
├─ Evidence: Temporal continuity + appearance
└─ Use case: Track swap, re-entry after occlusion

PHASE F (Simultaneous Merging) ⏳ DESIGN COMPLETE
├─ Merges two active simultaneous tracks
├─ Moderate risk (need binding validation)
├─ Evidence: Spatial clustering + strong identity confirmation
└─ Use cases:
   - Tracker ID swap (person visible in 2 boxes briefly)
   - Perception false duplicate (momentary 2-box artifact)
   - Occlusion recovery (person reappears as 2nd track)
```

### 1.2 Phase F Scoring Algorithm (Detailed)

**Input**: Two simultaneous active tracklets T_A, T_B

**Output**: Merge score ∈ [0, 100] + decision (ALLOW/TENTATIVE/REJECT)

**Algorithm**:

```python
def compute_simultaneous_merge_score(track_a, track_b, config):
    """
    Compute similarity score for two simultaneous tracks.
    
    Returns: score (0-100) + detailed reasoning
    """
    
    # ========== PRE-CHECKS (Reject if fail) ==========
    
    # 1. TEMPORAL OVERLAP CHECK
    if track_a.end_time != None or track_b.end_time != None:
        return 0, "Track not simultaneously active"
    
    if track_a.id == track_b.id:
        return 0, "Same track ID"
    
    if track_a.camera_id != track_b.camera_id:
        return 0, "Different cameras (Phase F single-camera)"
    
    # ========== SPATIAL CRITERION ==========
    
    # 1a. PROXIMITY CHECK (High weight, most discriminative)
    dist_pixels = distance_between_centers(track_a.bbox, track_b.bbox)
    max_distance = config.phase_f.spatial_max_distance  # ~50 pixels
    
    if dist_pixels > max_distance * 1.5:  # Too far apart
        return 0, "Too spatially distant"
    
    # Spatial score: 0-40 points
    # Decreases with distance
    spatial_score = 40.0 * max(0, (1.0 - dist_pixels / max_distance))
    # Clamp to [0, 40]
    spatial_score = min(40, spatial_score)
    
    reasoning_spatial = f"Distance {dist_pixels:.1f}px → {spatial_score:.1f}/40"
    
    # ========== APPEARANCE CRITERION ==========
    
    # 2. EMBEDDING SIMILARITY (Critical)
    # Both must have recent face samples
    if len(track_a.face_samples) < 2 or len(track_b.face_samples) < 2:
        return 0, "Insufficient face samples"
    
    # Get most recent embeddings
    embedding_a = track_a.face_samples[-1].embedding
    embedding_b = track_b.face_samples[-1].embedding
    
    embedding_dist = cosine_distance(embedding_a, embedding_b)
    max_embedding_dist = config.phase_f.appearance_max_distance  # ~0.30
    
    if embedding_dist > max_embedding_dist * 1.2:  # Too different
        return 0, "Appearance too different"
    
    # Appearance score: 0-30 points (stricter than Phase E due to simultaneous)
    appearance_score = 30.0 * max(0, (1.0 - embedding_dist / max_embedding_dist))
    appearance_score = min(30, appearance_score)
    
    reasoning_appearance = f"Embedding dist {embedding_dist:.3f} → {appearance_score:.1f}/30"
    
    # ========== MOTION CRITERION ==========
    
    # 3. VELOCITY CONSISTENCY
    # Both must have motion history (or both stationary)
    if len(track_a.motion_history) < 2 or len(track_b.motion_history) < 2:
        # Assume stationary (same motion = 0 velocity)
        motion_score = 10.0
        reasoning_motion = "Insufficient history, assume stationary → 10/15"
    else:
        # Get recent velocity vectors
        vel_a = track_a.motion_history[-1]
        vel_b = track_b.motion_history[-1]
        
        # Handle both stationary
        if magnitude(vel_a) < 2.0 and magnitude(vel_b) < 2.0:
            motion_score = 10.0  # Both stationary = consistent
            reasoning_motion = "Both stationary → 10/15"
        else:
            # Compute velocity similarity (cosine)
            vel_sim = cosine_similarity(vel_a, vel_b)
            min_similarity = config.phase_f.motion_velocity_similarity_min  # ~0.7
            
            if vel_sim < min_similarity * 0.7:  # Too different motion
                return 0, "Motion inconsistent"
            
            # Motion score: 0-15 points
            motion_score = 15.0 * max(0, (vel_sim - 0.3) / 0.7)
            motion_score = min(15, motion_score)
            reasoning_motion = f"Velocity similarity {vel_sim:.2f} → {motion_score:.1f}/15"
    
    # ========== SIZE CONSISTENCY CRITERION ==========
    
    # 4. BOUNDING BOX SIZE RATIO
    area_a = track_a.bbox.width * track_a.bbox.height
    area_b = track_b.bbox.width * track_b.bbox.height
    
    size_ratio = min(area_a, area_b) / max(area_a, area_b)
    min_ratio = config.phase_f.size_consistency_min  # ~0.8
    
    if size_ratio < min_ratio * 0.7:  # Very different sizes
        return 0, "Size very inconsistent"
    
    # Size score: 0-10 points
    size_score = 10.0 * max(0, (size_ratio - 0.5) / 0.5)
    size_score = min(10, size_score)
    
    reasoning_size = f"Size ratio {size_ratio:.2f} → {size_score:.1f}/10"
    
    # ========== SUBTOTAL (0-95) ==========
    score_subtotal = spatial_score + appearance_score + motion_score + size_score
    
    # ========== BINDING STATE VALIDATION MULTIPLIER ==========
    
    # 5. BINDING COMPATIBILITY (Crucial for correctness)
    binding_a = track_a.binding_state
    binding_b = track_b.binding_state
    
    confidence_a = track_a.identity_confidence
    confidence_b = track_b.identity_confidence
    
    # Check for identity conflicts
    identity_a = track_a.suggested_identity
    identity_b = track_b.suggested_identity
    
    multiplier = 1.0
    reasoning_binding = ""
    
    # CASE 1: One CONFIRMED (high confidence), other weak
    if binding_a == "CONFIRMED" and confidence_a > 0.85 and confidence_b < 0.50:
        # Clear case: don't merge, they're different people
        return 0, "A confirmed to different person, B uncertain"
    
    if binding_b == "CONFIRMED" and confidence_b > 0.85 and confidence_a < 0.50:
        # Clear case: don't merge, they're different people
        return 0, "B confirmed to different person, A uncertain"
    
    # CASE 2: Both PENDING or UNKNOWN (no strong identity assertion)
    if binding_a in ["PENDING", "UNKNOWN"] and binding_b in ["PENDING", "UNKNOWN"]:
        # Safe to merge, no identity contradiction
        multiplier = 1.0
        reasoning_binding = "Both PENDING/UNKNOWN, no contradiction"
    
    # CASE 3: One CONFIRMED, other PENDING with same suggested ID
    elif binding_a == "CONFIRMED" and confidence_a > 0.85:
        if binding_b == "PENDING" and identity_b == identity_a and confidence_b > 0.60:
            # Can merge with multiplier boost
            multiplier = 1.5
            reasoning_binding = "A confirmed, B suggests same person"
        elif binding_b == "UNKNOWN":
            # Unknown won't conflict
            multiplier = 1.2
            reasoning_binding = "A confirmed, B unknown"
        else:
            return 0, "Binding conflict: different confirmed identities"
    
    elif binding_b == "CONFIRMED" and confidence_b > 0.85:
        if binding_a == "PENDING" and identity_a == identity_b and confidence_a > 0.60:
            multiplier = 1.5
            reasoning_binding = "B confirmed, A suggests same person"
        elif binding_a == "UNKNOWN":
            multiplier = 1.2
            reasoning_binding = "B confirmed, A unknown"
        else:
            return 0, "Binding conflict: different confirmed identities"
    
    else:
        # No clear conflict
        multiplier = 1.0
        reasoning_binding = "No binding conflict"
    
    # ========== FINAL SCORE ==========
    
    score_final = score_subtotal * multiplier
    score_final = min(100, score_final)  # Cap at 100
    
    reasoning = {
        "spatial": reasoning_spatial,
        "appearance": reasoning_appearance,
        "motion": reasoning_motion,
        "size": reasoning_size,
        "binding": reasoning_binding,
        "subtotal": f"{score_subtotal:.1f}",
        "multiplier": f"{multiplier:.2f}",
        "final": f"{score_final:.1f}",
    }
    
    return score_final, reasoning
```

### 1.3 Decision Logic

```python
def get_simultaneous_merge_decision(score, config):
    """
    Convert score to merge decision.
    
    Decision states:
    - ALLOW: Merge immediately (high confidence)
    - TENTATIVE: Merge but monitor (medium confidence)
    - REJECT: Don't merge (low confidence)
    """
    
    threshold_allow = config.phase_f.score_allow_min  # 80
    threshold_tentative_min = config.phase_f.score_tentative_min  # 60
    
    if score >= threshold_allow:
        return "ALLOW"
    elif score >= threshold_tentative_min:
        return "TENTATIVE"
    else:
        return "REJECT"
```

---

## 2. Phase F Data Structures

### 2.1 SimultaneousMergeCandidate

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

@dataclass
class SimultaneousMergeCandidate:
    """
    Represents a potential merge between two simultaneous tracks.
    
    Used for:
    - Storing merge candidates for batch processing
    - Tracking candidate history
    - Computing merge metrics
    """
    
    # Track IDs
    track_id_a: int
    track_id_b: int
    camera_id: str
    
    # Timing
    detection_time: datetime
    last_updated: datetime
    
    # Score
    merge_score: float  # 0-100
    decision: str  # "ALLOW" | "TENTATIVE" | "REJECT"
    
    # Reasoning (for debugging/monitoring)
    reasoning: Dict[str, str]  # e.g. {"spatial": "40/40", ...}
    
    # Status
    status: str  # "pending" | "approved" | "executed" | "rejected" | "expired"
    
    # Metadata
    metadata: Dict[str, Any] = None

    def is_high_confidence(self) -> bool:
        return self.merge_score >= 80
    
    def is_tentative(self) -> bool:
        return 60 <= self.merge_score < 80
    
    def is_rejected(self) -> bool:
        return self.merge_score < 60
```

### 2.2 SimultaneousMergeState

```python
@dataclass
class SimultaneousMergeState:
    """
    Tracks the state of a tentative simultaneous merge.
    
    Used for:
    - Monitoring tentative merges
    - Detecting contradictions
    - Scheduling reversals
    """
    
    # Merge identity
    canonical_id: int  # Which ID is canonical
    absorbed_id: int   # Which ID was absorbed
    
    # Timing
    merge_time: datetime
    reversal_deadline: datetime
    
    # Monitoring
    status: str  # "tentative" | "confirmed" | "reversed"
    
    # Binding history during monitoring
    binding_history: list  # [(timestamp, binding_state), ...]
    
    # Contradiction flags
    contradicted: bool = False
    contradiction_reason: Optional[str] = None
    
    # Metrics
    monitoring_duration_seconds: Optional[float] = None
    
    def should_reverse(self, current_time: datetime) -> bool:
        """Check if merge should be reversed."""
        return (
            self.status == "tentative"
            and current_time >= self.reversal_deadline
            and not self.contradicted
        )
    
    def is_expired(self, current_time: datetime, max_age_seconds: float = 10.0) -> bool:
        """Check if monitoring has expired."""
        age = (current_time - self.merge_time).total_seconds()
        return age > max_age_seconds
```

### 2.3 SimultaneousMergeMetrics

```python
@dataclass
class SimultaneousMergeMetrics:
    """
    Metrics for Phase F simultaneous merging.
    """
    
    # Attempt tracking
    merge_attempts: int = 0
    merges_allowed: int = 0
    merges_tentative: int = 0
    merges_rejected: int = 0
    
    # Execution tracking
    merges_executed: int = 0
    merges_confirmed: int = 0
    merges_reversed: int = 0
    
    # Quality tracking
    reversals_due_to_binding: int = 0
    reversals_due_to_timeout: int = 0
    reversals_due_to_explicit: int = 0
    
    # Reason tracking
    rejection_reasons: Dict[str, int]  # {"too_distant": 5, ...}
    
    # Timing
    avg_merge_score: float = 0.0
    max_merge_score: float = 0.0
    min_merge_score: float = 0.0
    
    def update_rejection(self, reason: str):
        if reason not in self.rejection_reasons:
            self.rejection_reasons[reason] = 0
        self.rejection_reasons[reason] += 1
```

---

## 3. Phase F Integration with MergeManager

### 3.1 MergeManager Extension

```python
# In identity/merge_manager.py

class MergeManager:
    """Extended with Phase F simultaneous merge support."""
    
    def __init__(self, config):
        # ... existing Phase E code ...
        
        # Phase F state
        self.simultaneous_candidates: Dict[tuple, SimultaneousMergeCandidate] = {}
        self.simultaneous_merges: Dict[int, SimultaneousMergeState] = {}
        self.phase_f_metrics = SimultaneousMergeMetrics()
    
    # ========== PHASE F CORE APIS ==========
    
    def find_simultaneous_merge_candidates(
        self,
        active_tracks: Dict[int, TrackletSnapshot]
    ) -> List[SimultaneousMergeCandidate]:
        """
        Find all potential simultaneous merge pairs.
        
        Algorithm:
        1. Prune tracks far apart (distance > 100px)
        2. For each pair, compute merge score
        3. Filter candidates with score >= 60
        
        Returns: List of SimultaneousMergeCandidate
        """
        candidates = []
        track_ids = list(active_tracks.keys())
        
        # For efficiency, use spatial grid pruning
        spatial_grid = self._build_spatial_grid(active_tracks)
        
        # Check nearby track pairs
        for track_id_a, candidates_near in spatial_grid.items():
            if track_id_a not in active_tracks:
                continue
            
            track_a = active_tracks[track_id_a]
            
            for track_id_b in candidates_near:
                if track_id_b <= track_id_a:  # Avoid duplicates
                    continue
                
                if track_id_b not in active_tracks:
                    continue
                
                track_b = active_tracks[track_id_b]
                
                # Compute merge score
                score, reasoning = self._compute_simultaneous_merge_score(
                    track_a, track_b
                )
                
                # Filter candidates
                if score >= 60:  # Above minimum threshold
                    candidate = SimultaneousMergeCandidate(
                        track_id_a=track_id_a,
                        track_id_b=track_id_b,
                        camera_id=track_a.camera_id,
                        detection_time=datetime.now(),
                        last_updated=datetime.now(),
                        merge_score=score,
                        decision=self._get_merge_decision(score),
                        reasoning=reasoning,
                        status="pending",
                    )
                    
                    candidates.append(candidate)
                    
                    # Store for tracking
                    pair_key = (track_id_a, track_id_b)
                    self.simultaneous_candidates[pair_key] = candidate
                
                else:
                    self.phase_f_metrics.merges_rejected += 1
                    self.phase_f_metrics.update_rejection(
                        reasoning.get("final_reason", "score_too_low")
                    )
        
        return candidates
    
    def execute_simultaneous_merges(
        self,
        candidates: List[SimultaneousMergeCandidate]
    ) -> Dict[str, Any]:
        """
        Execute approved and tentative simultaneous merges.
        
        Returns: Execution summary
        """
        executed = []
        rejected = []
        
        for candidate in candidates:
            if candidate.decision == "REJECT":
                rejected.append(candidate)
                continue
            
            # Determine tentative status
            is_tentative = candidate.decision == "TENTATIVE"
            
            # Execute merge
            merge_result = self._execute_simultaneous_merge_internal(
                track_id_a=candidate.track_id_a,
                track_id_b=candidate.track_id_b,
                merge_score=candidate.merge_score,
                tentative=is_tentative,
                reasoning=candidate.reasoning,
            )
            
            if merge_result["success"]:
                executed.append({
                    "candidate": candidate,
                    "result": merge_result,
                })
                
                if is_tentative:
                    self.phase_f_metrics.merges_tentative += 1
                else:
                    self.phase_f_metrics.merges_allowed += 1
                
                self.phase_f_metrics.merges_executed += 1
            else:
                rejected.append(candidate)
        
        return {
            "executed": len(executed),
            "rejected": len(rejected),
            "details": executed,
        }
    
    def monitor_tentative_simultaneous_merges(self, active_tracks):
        """
        Monitor tentative simultaneous merges for contradictions.
        
        Reverses merge if:
        - Binding conflict detected
        - Timeout reached
        """
        current_time = datetime.now()
        reversals = []
        
        for canonical_id, merge_state in list(self.simultaneous_merges.items()):
            if merge_state.status != "tentative":
                continue
            
            # Check for binding contradiction
            track_a = active_tracks.get(canonical_id)
            track_b = active_tracks.get(merge_state.absorbed_id)
            
            if not track_a or not track_b:
                # Track ended, clean up
                merge_state.status = "expired"
                continue
            
            # Check if binding contradicts merge
            if self._has_binding_contradiction(
                track_a.binding_state,
                track_b.binding_state,
                track_a.identity_id,
                track_b.identity_id,
            ):
                # Reverse merge
                merge_state.contradicted = True
                merge_state.contradiction_reason = "binding_conflict"
                self._reverse_simultaneous_merge(canonical_id)
                reversals.append(("binding_conflict", canonical_id))
                self.phase_f_metrics.reversals_due_to_binding += 1
                self.phase_f_metrics.merges_reversed += 1
            
            # Check timeout
            elif merge_state.should_reverse(current_time):
                # Timeout passed, keep merge
                merge_state.status = "confirmed"
                merge_state.monitoring_duration_seconds = (
                    current_time - merge_state.merge_time
                ).total_seconds()
                self.phase_f_metrics.merges_confirmed += 1
        
        return reversals
```

---

## 4. Phase F Configuration

### 4.1 Config Addition to `default.yaml`

```yaml
governance:
  merge:
    phase_f:
      enabled: false  # Start disabled, enable after Phase E validation
      
      # Scoring thresholds
      score_allow_min: 80  # Merge immediately if >= 80
      score_tentative_min: 60  # Tentative merge if >= 60
      
      # Spatial proximity
      spatial_max_distance: 50  # pixels, score decreases beyond this
      
      # Appearance (stricter than Phase E)
      appearance_max_distance: 0.30  # embedding distance threshold
      
      # Motion consistency
      motion_velocity_similarity_min: 0.7  # cosine similarity
      
      # Size consistency
      size_consistency_min: 0.8  # area ratio
      
      # Binding validation
      binding:
        allow_different_identities: false  # Never merge confirmed different people
        confidence_required_for_diff: 0.85  # Confidence needed to consider "different"
        unknown_tracks_merge_allowed: true  # Can merge UNKNOWN tracks
      
      # Tentative merge monitoring
      tentative_monitoring:
        window_seconds: 3.0  # Monitor for 3 seconds
        reversal_on_contradiction: true
        reversal_on_timeout_action: "confirm"  # or "reverse"
      
      # Frequency
      check_frequency_frames: 20  # Check every 20 frames (reduce overhead)
      
      # Limits
      max_simultaneous_merges_per_track: 1  # Only 1 merge at a time
      
      # Logging
      logging:
        log_merge_attempts: true
        log_merge_reasons: true
        log_reversals: true
        debug_mode: false
```

---

## 5. Phase F Testing Strategy

### 5.1 Unit Tests Coverage

**File**: `core/tests/test_phase_f_merge_manager.py` (600+ lines)

```python
# Test categories

class TestPhaseFFunding:
    """Tests for Phase F merge finding."""
    
    def test_find_simultaneous_candidates_none(self):
        """No candidates if tracks too far apart."""
    
    def test_find_simultaneous_candidates_same_person(self):
        """Finds candidates when same person appears as 2 boxes."""
    
    def test_find_simultaneous_candidates_different_people(self):
        """Rejects candidates when people are actually different."""


class TestPhaseFScoring:
    """Tests for Phase F scoring algorithm."""
    
    def test_spatial_criterion_full_points(self):
        """Tracks at same location get full spatial score."""
    
    def test_spatial_criterion_too_far(self):
        """Rejects if too far apart."""
    
    def test_appearance_criterion_same_person(self):
        """Same appearance gets high score."""
    
    def test_appearance_criterion_different_people(self):
        """Different appearance gets low score."""
    
    def test_motion_criterion_same_direction(self):
        """Matching motion increases score."""
    
    def test_motion_criterion_opposite_direction(self):
        """Opposite motion decreases score."""
    
    def test_size_criterion_consistent(self):
        """Consistent sizes increase score."""
    
    def test_size_criterion_inconsistent(self):
        """Very different sizes reduce score."""
    
    def test_binding_multiplier_confirmed_different(self):
        """Confirmed different people = reject merge."""
    
    def test_binding_multiplier_pending_no_conflict(self):
        """PENDING tracks can merge if no conflict."""
    
    def test_binding_multiplier_boost(self):
        """Confirmed + PENDING of same person boosts score."""


class TestPhaseFExecution:
    """Tests for merge execution."""
    
    def test_execute_high_confidence_merge(self):
        """High-confidence merges execute immediately."""
    
    def test_execute_tentative_merge(self):
        """Tentative merges execute but marked for monitoring."""
    
    def test_execute_rejected_merge(self):
        """Rejected merges don't execute."""
    
    def test_merge_reversal_on_binding_contradiction(self):
        """Merge reverses if binding contradicts."""
    
    def test_merge_reversal_timeout(self):
        """Merge confirmed after monitoring timeout."""


class TestPhaseFEdgeCases:
    """Edge case tests."""
    
    def test_simultaneous_merge_with_no_motion_history(self):
        """Handles tracks without motion history."""
    
    def test_simultaneous_merge_insufficient_face_samples(self):
        """Rejects if face samples insufficient."""
    
    def test_simultaneous_merge_crowded_scene(self):
        """Multiple simultaneous merge candidates handled."""
    
    def test_simultaneous_merge_circular_conflicts(self):
        """Handles complex binding conflicts."""
    
    def test_simultaneous_merge_very_close_boxes(self):
        """Handles overlapping bounding boxes."""


class TestPhaseFIntegration:
    """Integration with existing systems."""
    
    def test_phase_f_respects_phase_e_state(self):
        """Phase F works with Phase E aliases."""
    
    def test_phase_f_binding_state_machine_respect(self):
        """Respects Phase C binding constraints."""
    
    def test_phase_f_scheduler_interaction(self):
        """Works with Phase D scheduler."""
    
    def test_phase_f_config_enable_disable(self):
        """Can enable/disable dynamically."""


class TestPhaseFMetrics:
    """Metrics collection."""
    
    def test_metrics_track_attempts(self):
    
    def test_metrics_track_decisions(self):
    
    def test_metrics_track_reversals(self):
```

### 5.2 Validation Script

**File**: `scripts/validate_phase_f.py` (400+ lines, 15 tests)

```
Test 1: Import Phase F classes ✓
Test 2: Create SimultaneousMergeCandidate ✓
Test 3: Create SimultaneousMergeState ✓
Test 4: Merge score computation ✓
Test 5: Spatial criterion scoring ✓
Test 6: Appearance criterion scoring ✓
Test 7: Motion criterion scoring ✓
Test 8: Binding validation ✓
Test 9: Find merge candidates ✓
Test 10: Execute high-confidence merge ✓
Test 11: Execute tentative merge ✓
Test 12: Monitor tentative merges ✓
Test 13: Reverse merge on contradiction ✓
Test 14: Config loading ✓
Test 15: Metrics collection ✓

Expected: 15/15 tests passing
```

---

## 6. Phase F Rollout Strategy

### 6.1 Staging Phase (After Phase E Production)

```
Timeline: Week 2-3 (after Phase E stable in production)

Config:
  phase_f.enabled: true (in staging only)
  phase_f.score_allow_min: 85  (conservative, higher than production)
  phase_f.phase_f.check_frequency_frames: 50 (less frequent)

Monitoring:
  1. Run for 48 hours
  2. Collect merge attempt/execution data
  3. Verify no false merges
  4. Check performance impact
  5. Validate binding respect
```

### 6.2 Production Rollout (Gradual Canary)

```
Step 1 (Days 1-2): 10% cameras, conservative thresholds
Step 2 (Days 3-4): 25% cameras, normal thresholds
Step 3 (Days 5-7): 50% cameras, monitor closely
Step 4 (Days 8+): 100% cameras

Rollback trigger:
  - False simultaneous merges > 5% of attempts
  - Identity flip rate increases > 1%
  - Any binding contradiction not caught
```

---

## 7. Success Criteria for Phase F

### 7.1 Functional Criteria

- [ ] All 15 validation tests passing
- [ ] 50+ unit tests passing
- [ ] Simultaneous merge candidates found correctly
- [ ] Merge scoring accurate (manual verification)
- [ ] High-confidence merges correct (no false positives)
- [ ] Tentative merges reverse when contradicted
- [ ] Binding state conflicts prevented
- [ ] Config enable/disable works instantly

### 7.2 Quality Criteria

- [ ] False merge rate < 2%
- [ ] Tentative reversal rate 10-30%
- [ ] No identity corruption
- [ ] Binding state transitions explainable
- [ ] Ghost duplicates reduced by 10-15% additional (Phase F on top of E)

### 7.3 Performance Criteria

- [ ] FPS degradation < 1%
- [ ] Memory usage < 30MB additional
- [ ] Merge check latency < 2ms per frame
- [ ] No unbounded memory growth

---

## 8. Risk Assessment & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| False positive merges | Medium | High | Conservative thresholds, binding validation, reversal window |
| Binding contradictions | Low | Critical | Triple-check (pre-merge, monitoring, manual) |
| Performance degradation | Low | Medium | Frequency tuning, spatial pruning |
| Config complexity | Medium | Medium | Clear documentation, sane defaults |
| Merge chaining errors | Low | High | Single-merge-per-track limit, history tracking |

---

## Conclusion

**Phase F Design is Production-Ready** ✅

Ready for implementation once Phase E validated in production (24+ hours).

**Recommended Sequence**:
1. Phase E production deployment (canary rollout)
2. Monitor Phase E for 48+ hours (validate 30%+ ghost duplicate reduction)
3. Phase F implementation (core code, tests)
4. Phase F staging validation (48 hours)
5. Phase F production rollout (same canary approach)

**Total System Timeline**: 
- Phase E deployment: Days 1-3
- Phase E validation: Days 4-7
- Phase F implementation: Days 8-10
- Phase F validation: Days 11-12
- Phase F deployment: Days 13-20
- **Full system production-ready: Week 3**

