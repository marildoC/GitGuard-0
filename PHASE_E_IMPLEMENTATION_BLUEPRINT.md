# Phase E: Handoff Merge Manager - Deep Implementation Blueprint

**Status**: Deep robust implementation blueprint created
**Phase**: E (Identity Deduplication via Handoff Merge)
**Objective**: Reduce ghost duplicates through intelligent track aliasing and canonical entity mapping

---

## 1. Core Problem & Solution

### Problem Statement
When tracker fragments a person (e.g., occlusion, exit/re-entry, low confidence), it creates multiple tracklet IDs.
These fragments trigger separate identity decisions, creating ghost duplicate entities in UI and alerts.

### Solution Approach: Handoff Merge Manager
- **NOT** merging actual tracks (perception layer stays independent)
- **YES** aliasing track fragments to canonical entity identifiers
- **Mapping**: Multiple tracklets → One canonical identity
- **UI/Alerts**: Use canonical identity, not raw tracklet ID
- **Strategy**: Conservative, explainable, reversible

### Key Architectural Principles
1. **Aliasing Pattern**: Map tracklet_id → canonical_id (many-to-one)
2. **Evidence-Based**: Only merge if meeting explicit criteria
3. **Time-Exclusive Only**: Never merge simultaneous tracks (Phase E only)
4. **Binding-Aware**: Respect binding state for each tracklet
5. **Reversible**: Maintain trace of why merge happened

---

## 2. System Architecture (Phase E Integration)

```
Main Loop Flow (Updated with Phase E):

┌─────────────────────────────────────────────────────────┐
│ 1. Frame In (boxes, tracklets)                          │
├─────────────────────────────────────────────────────────┤
│ 2. Perception: Track assignment, new tracklets formed   │
├─────────────────────────────────────────────────────────┤
│ 3. Face Extract & Route (with quality gating) [Phase B] │
├─────────────────────────────────────────────────────────┤
│ 4. Scheduler: Select which tracks to process [Phase D]  │
├─────────────────────────────────────────────────────────┤
│ 5. Identity: Match & Binding decision [Phases B+C]      │
├─────────────────────────────────────────────────────────┤
│ 6. **PHASE E: Merge Manager**                           │
│    - Check for completed tracklets                      │
│    - Search for candidate merge partners                │
│    - Create/update aliases                              │
│    - Store merge evidence in history                    │
├─────────────────────────────────────────────────────────┤
│ 7. Canonicalization: Apply aliases for display          │
├─────────────────────────────────────────────────────────┤
│ 8. Output: Canonical identities to UI/alerts            │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Deep Implementation Specification

### 3.1 Merge Manager Data Structures

**MergeCandidate** (class)
```python
tracklet_id: str
binding_state: BindingOutput
person_id: Optional[str]
confidence: float
end_time: float
last_position: np.ndarray (x, y)
motion_vector: Optional[np.ndarray]
appearance_features: np.ndarray (embedding)
track_length: int
quality_samples: int (accepted face samples)
```

**MergeEvidence** (class)
```python
source_tracklet_id: str
target_tracklet_id: str  # canonical target
merge_time: float
merge_reason: str  # e.g., "spatial_temporal_continuity"
confidence: float
details: dict  # thresholds used, distances computed, etc.
reversed: bool = False  # if reversed after false merge
reversal_reason: Optional[str]
```

**CanonicalMapping** (class)
```python
canonical_id: str  # base tracklet_id
aliases: List[str]  # other tracklets merged into this
primary_binding: BindingOutput  # binding state of canonical
merge_history: List[MergeEvidence]
last_update_time: float
```

### 3.2 Core Algorithm: Merge Decision

**When to Consider Merge** (tracklet lifecycle events):
1. Tracklet becomes inactive (no detections for >2 seconds)
2. Tracklet ends (tracker ends it due to low confidence)
3. New tracklet appears near recently-ended tracklet

**Merge Criteria** (all must be satisfied for conservative approach):

#### Criterion 1: Time Exclusivity (Must NOT overlap)
```
tracklet_A.end_time < tracklet_B.start_time
(at least 0.5s gap recommended)
```

#### Criterion 2: Spatial Continuity
```
distance_pixels = norm(tracklet_A.last_position - tracklet_B.first_position)
max_distance_allowed = 150 + (time_gap_seconds * velocity_estimate)

Acceptance: distance < max_distance_allowed
```

#### Criterion 3: Motion Coherence
```
trajectory_A_velocity = tracklet_A.motion_vector
trajectory_B_velocity = tracklet_B.motion_vector
velocity_similarity = cosine_similarity(trajectory_A, trajectory_B)

# Both moving same direction or both stationary
Acceptance: velocity_similarity > 0.6 OR both velocities < 0.1
```

#### Criterion 4: Appearance Consistency
```
embedding_distance = euclidean_distance(
    tracklet_A.appearance_features,
    tracklet_B.appearance_features
)

# Conservative threshold (high confidence)
max_embedding_distance = 0.35  # tunable per gallery

Acceptance: embedding_distance < max_embedding_distance
```

#### Criterion 5: Binding State Compatibility
```
# Important: only merge if binding allows it
tracklet_A_binding = CONFIRMED_STRONG || CONFIRMED_WEAK || PENDING || UNKNOWN
tracklet_B_binding = must match or weaker

# Never merge two conflicting identities
if tracklet_A.person_id != tracklet_B.person_id:
    require very high confidence (e.g., > 0.95)
    AND require explicit decision from binding
```

#### Criterion 6: Quality Threshold
```
# Only merge if both tracklets have sufficient evidence
tracklet_A.quality_samples >= 2  (at least 2 good faces)
tracklet_B.quality_samples >= 2

# Or either is CONFIRMED (sticky state)
OR (tracklet_A.binding_state == CONFIRMED)
OR (tracklet_B.binding_state == CONFIRMED)
```

#### Criterion 7: No Recent Merge
```
# Prevent merge chains or thrashing
time_since_last_merge > 2.0 seconds
max_merges_per_canonical_id < 5 (within time window)
```

### 3.3 Merge Scoring Function

**Conservative Handoff Merge Score**:
```
score = 0.0

# Time continuity bonus (essential)
time_gap = tracklet_B.start_time - tracklet_A.end_time
if 0.3 < time_gap < 3.0:
    score += 25  # temporal continuity exists
elif time_gap < 0.3 or time_gap > 5.0:
    score = 0  # too close or too far, reject

# Spatial continuity score
spatial_score = max(0, 1.0 - (distance / max_distance_allowed))
score += spatial_score * 25

# Motion coherence score
motion_score = velocity_similarity if velocity_similarity > -0.5 else 0.0
score += max(0, motion_score) * 20

# Appearance consistency (critical for false positive prevention)
appearance_score = max(0, 1.0 - (embedding_distance / 0.5))
score += appearance_score * 20

# Binding confidence multiplier
if tracklet_A.binding_state == CONFIRMED:
    score *= 1.2  # confirmed identity is easier to extend
if tracklet_B.binding_state == CONFIRMED:
    score *= 1.15

# Final decision threshold
if score >= 60:
    return MERGE (high confidence)
elif 40 <= score < 60:
    return MERGE_TENTATIVE (monitor for reversal)
elif score < 40:
    return HOLD (do not merge)
```

### 3.4 Merge Reversal (Error Recovery)

**When to Reverse a Merge**:
1. New evidence contradicts merge (e.g., same two canonical IDs confirmed different)
2. Canonical track gets contradictory binding evidence
3. Merge was marked TENTATIVE and evidence doesn't strengthen after 5 seconds

**Reversal Process**:
```
1. Un-alias tracklet_B from canonical
2. Create separate canonical for tracklet_B
3. Log reversal with reason
4. Update binding to account for split
5. Adjust UI/alerts accordingly
```

---

## 4. File Structure & Implementation Details

### 4.1 New File: `identity/merge_manager.py`

**Class: MergeManager**

```python
class MergeManager:
    """
    Manages handoff merges (time-exclusive track aliasing).
    
    Responsibilities:
    - Track lifecycle monitoring (start, end events)
    - Merge candidate detection
    - Evidence-based merge scoring
    - Canonical mapping maintenance
    - Reversal capability
    """
    
    def __init__(self, config: MergeConfig):
        self.config = config
        self.canonical_mappings: Dict[str, CanonicalMapping] = {}
        self.merge_history: List[MergeEvidence] = []
        self.inactive_tracklets: Dict[str, MergeCandidate] = {}
        self.metrics = MergeMetrics()
    
    # Core API
    def on_tracklet_updated(
        self,
        tracklet_id: str,
        binding_state: BindingOutput,
        appearance_features: np.ndarray,
        last_position: Tuple[float, float],
        track_length: int,
        quality_samples: int,
        timestamp: float
    ) -> None:
        """Called when a tracklet is updated or ended."""
        
    def on_tracklet_ended(
        self,
        tracklet_id: str,
        binding_state: BindingOutput,
        appearance_features: np.ndarray,
        last_position: Tuple[float, float],
        end_time: float,
        track_length: int,
        quality_samples: int
    ) -> None:
        """Called when tracker ends a tracklet."""
        
    def on_tracklet_started(
        self,
        tracklet_id: str,
        first_position: Tuple[float, float],
        timestamp: float
    ) -> None:
        """Called when tracker creates new tracklet."""
        
    def get_canonical_id(self, tracklet_id: str) -> str:
        """Returns canonical ID for any tracklet (maps aliases)."""
        
    def get_all_tracklets_for_canonical(self, canonical_id: str) -> List[str]:
        """Returns all tracklets aliased to canonical."""
        
    def get_merge_decision(
        self,
        tracklet_a: MergeCandidate,
        tracklet_b: MergeCandidate,
        all_tracklets: Dict[str, MergeCandidate]
    ) -> MergeDecision:
        """Evaluates if two tracklets should be merged."""
        
    def execute_merge(
        self,
        canonical_id: str,
        tracklet_to_merge: str,
        evidence: MergeEvidence
    ) -> None:
        """Execute merge and update canonical mapping."""
        
    def reverse_merge(
        self,
        tracklet_id: str,
        reason: str
    ) -> None:
        """Reverse a previous merge if needed."""
        
    def get_metrics(self) -> MergeMetrics:
        """Return merge statistics."""
        
    def cleanup_old_tracklets(self, threshold_seconds: float) -> None:
        """Remove old inactive tracklets from memory."""
```

### 4.2 Configuration: `config/default.yaml` Extension

```yaml
governance:
  handoff_merge:
    enabled: true
    
    # Merge scoring thresholds
    thresholds:
      merge_confidence_min: 60      # score >= 60 to merge
      tentative_threshold: 40        # 40-60 range = monitor
      hold_threshold: 40             # score < 40 = don't merge
      
    # Time constraints
    temporal:
      min_gap_seconds: 0.3          # minimum gap between tracks
      max_gap_seconds: 5.0          # maximum gap for merge
      
    # Spatial constraints
    spatial:
      max_distance_pixels: 150      # base spatial threshold
      velocity_influence: 20         # pixels/second consideration
      
    # Appearance constraints
    appearance:
      max_embedding_distance: 0.35  # conservative threshold
      quality_min_samples: 2        # min faces for merge
      
    # Motion constraints
    motion:
      velocity_similarity_min: 0.6  # cosine similarity threshold
      
    # Binding constraints
    binding:
      allow_different_identities: false  # strict mode
      confidence_required_for_diff: 0.95
      
    # Stability constraints
    stability:
      max_merges_per_canonical: 5
      merge_reversal_window: 5.0    # seconds to monitor tentative
      min_time_between_merges: 2.0
      
    # Metrics and logging
    logging:
      log_merge_reasons: true
      log_reversal_reasons: true
      debug_mode: false
```

### 4.3 Integration Points

#### 4.3.1 File: `core/main_loop.py`

**Addition Point 1: Merge Manager Initialization**
```python
# In main_loop initialization
from identity.merge_manager import MergeManager

self.merge_manager = MergeManager(config.governance.handoff_merge)
```

**Addition Point 2: Track Event Capture**
```python
# After perception/tracker step
# When tracker reports new tracklet:
self.merge_manager.on_tracklet_started(
    tracklet_id=tracklet.id,
    first_position=(tracklet.x, tracklet.y),
    timestamp=frame_time
)

# When tracklet is ended/lost:
if tracklet_ended_event:
    # Get tracklet's last binding and appearance state
    binding = self.binding_manager.get(tracklet.id)
    features = tracklet.appearance_features  # from tracker
    
    self.merge_manager.on_tracklet_ended(
        tracklet_id=tracklet.id,
        binding_state=binding,
        appearance_features=features,
        last_position=(tracklet.x, tracklet.y),
        end_time=frame_time,
        track_length=tracklet.frame_count,
        quality_samples=tracklet.quality_sample_count
    )
```

**Addition Point 3: Merge Processing**
```python
# After identity decisions
self.merge_manager.on_tracklet_updated(
    tracklet_id=tracklet.id,
    binding_state=binding_output,
    appearance_features=tracklet.appearance_features,
    last_position=(tracklet.x, tracklet.y),
    track_length=tracklet.frame_count,
    quality_samples=tracklet.quality_sample_count,
    timestamp=frame_time
)

# Execute merge checks
# (This may happen at lower frequency, e.g., every 10 frames)
if frame_count % 10 == 0:
    self.merge_manager.check_and_execute_merges()
```

**Addition Point 4: Canonicalization**
```python
# Before outputting to UI/alerts, map tracklet ID to canonical ID
canonical_id = self.merge_manager.get_canonical_id(tracklet.id)

# Use canonical_id for:
# - UI display
# - Alert generation
# - Metrics reporting
# - Binding state storage (optional: can use canonical as key)
```

#### 4.3.2 File: `identity/binding.py`

**Modification**: Store binding state by canonical ID

```python
# Option A: Add canonicalization layer (minimal change)
class BindingManager:
    def __init__(self, merge_manager: MergeManager):
        self.merge_manager = merge_manager
        self.binding_states: Dict[str, BindingOutput] = {}
    
    def get(self, tracklet_id: str) -> BindingOutput:
        canonical = self.merge_manager.get_canonical_id(tracklet_id)
        return self.binding_states.get(canonical)
    
    def update(self, tracklet_id: str, binding: BindingOutput) -> None:
        canonical = self.merge_manager.get_canonical_id(tracklet_id)
        self.binding_states[canonical] = binding

# Option B: Keep separate, merge_manager manages mapping
# (Less invasive, current approach)
```

#### 4.3.3 File: `identity/identity_engine.py` & `identity/identity_engine_multiview.py`

**Modification**: Use canonical ID for output

```python
# When returning identity decision to main loop:
def get_identity_decision(self, tracklet_id: str) -> IdentityDecision:
    canonical_id = self.merge_manager.get_canonical_id(tracklet_id)
    
    decision = IdentityDecision(
        tracklet_id=tracklet_id,
        canonical_id=canonical_id,  # NEW: add canonical for UI
        person_id=...,
        ...
    )
    return decision
```

#### 4.3.4 File: `ui/overlay.py`

**Modification**: Display canonical entities

```python
# When drawing identity labels:
canonical_id = self.merge_manager.get_canonical_id(tracklet.id)

# Get all tracklets for this canonical (for debugging)
all_tracklets = self.merge_manager.get_all_tracklets_for_canonical(canonical_id)

# Display canonical as primary label
label = f"ID: {canonical_id}"  # instead of tracklet.id

# Optional: show merge status in debug mode
if debug_mode and len(all_tracklets) > 1:
    label += f" [merged {len(all_tracklets)} tracklets]"
```

---

## 5. Detailed Merge Candidate Discovery Algorithm

### 5.1 Candidate Pool Construction

**Every 10 frames** (or lower frequency):

```python
def check_and_execute_merges(self):
    current_time = get_current_timestamp()
    
    # Step 1: Find inactive tracklets (ended recently)
    candidates = [
        t for t in self.inactive_tracklets.values()
        if current_time - t.end_time < 5.0  # within 5s window
    ]
    
    # Step 2: Find new tracklets
    active = self.get_active_tracklets()
    new_tracklets = [
        t for t in active
        if t.track_length < 0.5  # started recently (< 0.5 sec old)
    ]
    
    # Step 3: Find candidate pairs (N² search with spatial pruning)
    for old_candidate in candidates:
        for new_tracklet in new_tracklets:
            # Spatial pre-filter (fast)
            distance = self.compute_distance(
                old_candidate.last_position,
                new_tracklet.first_position
            )
            if distance > 300:  # rough threshold
                continue
            
            # Detailed merge evaluation
            decision = self.get_merge_decision(
                old_candidate,
                new_tracklet,
                all_tracklets=active
            )
            
            if decision.should_merge:
                evidence = MergeEvidence(
                    source_tracklet_id=old_candidate.tracklet_id,
                    target_tracklet_id=new_tracklet.tracklet_id,
                    merge_time=current_time,
                    merge_reason=decision.reason,
                    confidence=decision.score,
                    details=decision.details
                )
                self.execute_merge(new_tracklet.tracklet_id, evidence)
```

### 5.2 Candidate Scoring Details

**Detailed scoring with all criteria**:

```python
def get_merge_decision(self, tracklet_a, tracklet_b, all_tracklets):
    score = 0.0
    details = {}
    
    # Criterion 1: Time exclusivity check
    time_gap = tracklet_b.start_time - tracklet_a.end_time
    if not (0.3 < time_gap < 5.0):
        return MergeDecision(should_merge=False, reason="time_gap_invalid")
    details['time_gap'] = time_gap
    score += 25
    
    # Criterion 2: Spatial continuity
    distance = norm(tracklet_a.last_position - tracklet_b.first_position)
    velocity_estimate = norm(tracklet_a.motion_vector) * time_gap if tracklet_a.motion_vector else 0
    max_distance = 150 + velocity_estimate
    
    if distance > max_distance:
        return MergeDecision(should_merge=False, reason="distance_too_large")
    
    spatial_score = max(0, 1.0 - (distance / max(1, max_distance)))
    score += spatial_score * 25
    details['distance'] = distance
    details['max_distance'] = max_distance
    
    # Criterion 3: Motion coherence
    if tracklet_a.motion_vector is not None and tracklet_b.motion_vector is not None:
        velocity_similarity = cosine_similarity(
            tracklet_a.motion_vector,
            tracklet_b.motion_vector
        )
        details['velocity_similarity'] = velocity_similarity
        
        if velocity_similarity < -0.5:
            return MergeDecision(should_merge=False, reason="opposite_motion")
        
        motion_score = max(0, velocity_similarity) * 0.8 + 0.2  # min 0.2
        score += motion_score * 20
    else:
        score += 10  # partial credit if velocity unknown
    
    # Criterion 4: Appearance consistency (CRITICAL)
    embedding_distance = euclidean_distance(
        tracklet_a.appearance_features,
        tracklet_b.appearance_features
    )
    
    if embedding_distance > 0.5:  # hard reject threshold
        return MergeDecision(should_merge=False, reason="appearance_mismatch")
    
    appearance_score = max(0, 1.0 - (embedding_distance / 0.5))
    score += appearance_score * 20
    details['embedding_distance'] = embedding_distance
    
    # Criterion 5: Binding state compatibility
    if tracklet_a.person_id != tracklet_b.person_id:
        if tracklet_a.person_id and tracklet_b.person_id:
            # Two different confirmed identities - high bar for merge
            if score < 80:
                return MergeDecision(
                    should_merge=False,
                    reason="different_identities_low_confidence"
                )
    
    details['binding_compatible'] = True
    
    # Criterion 6: Quality threshold
    if tracklet_a.quality_samples < 2 or tracklet_b.quality_samples < 2:
        if not (tracklet_a.binding_state == CONFIRMED or tracklet_b.binding_state == CONFIRMED):
            return MergeDecision(should_merge=False, reason="insufficient_quality")
    
    score += 5  # quality bonus
    
    # Criterion 7: No recent merge
    last_merge_time = max([
        m.merge_time for m in self.merge_history
        if m.source_tracklet_id == tracklet_a.tracklet_id
        or m.target_tracklet_id == tracklet_a.tracklet_id
    ], default=0)
    
    if self.current_time - last_merge_time < 2.0:
        return MergeDecision(
            should_merge=False,
            reason="recent_merge_already"
        )
    
    # Binding state multiplier
    if tracklet_a.binding_state == CONFIRMED:
        score *= 1.2
    if tracklet_b.binding_state == CONFIRMED:
        score *= 1.15
    
    details['final_score'] = score
    
    # Final decision
    if score >= 60:
        return MergeDecision(
            should_merge=True,
            reason="merge_confident",
            score=score,
            details=details
        )
    elif 40 <= score < 60:
        return MergeDecision(
            should_merge=True,
            reason="merge_tentative",
            score=score,
            details=details,
            tentative=True,
            reversal_deadline=self.current_time + 5.0
        )
    else:
        return MergeDecision(
            should_merge=False,
            reason="insufficient_evidence",
            score=score,
            details=details
        )
```

---

## 6. Metrics & Observability

### 6.1 MergeMetrics Class

```python
class MergeMetrics:
    def __init__(self):
        self.merge_attempts: int = 0
        self.merges_executed: int = 0
        self.merges_confident: int = 0
        self.merges_tentative: int = 0
        self.merge_reversals: int = 0
        self.average_merge_score: float = 0.0
        self.merge_failure_reasons: Dict[str, int] = {}  # e.g., "distance_too_large": 5
        self.active_canonical_ids: int = 0
        self.current_tracklets_aliased: int = 0
```

### 6.2 Metrics Reporting

**In main loop**:
```python
# Every 30 frames or on demand
metrics = self.merge_manager.get_metrics()
logger.info(f"Merge Manager: {metrics.merges_executed} executed, "
            f"{metrics.merge_reversals} reversals, "
            f"score avg: {metrics.average_merge_score:.2f}")
```

---

## 7. Acceptance Gates (Validation Checklist)

### Gate E1: Core Merge Manager Functionality
- [ ] MergeManager instantiates without errors
- [ ] Canonical ID mapping works (aliases resolve correctly)
- [ ] Merge candidate detection identifies valid candidates
- [ ] Merge scoring produces reasonable scores
- [ ] Metrics are emitted correctly
- [ ] Config loads and applies thresholds

### Gate E2: Integration with Main Loop
- [ ] Tracklet lifecycle events captured correctly
- [ ] No regressions in perception/binding
- [ ] Canonical IDs propagated to identity engine
- [ ] UI displays canonical entities
- [ ] No crashes with merge state updates

### Gate E3: Functional Validation
- [ ] Single person scenario: stable canonical identity
- [ ] Two people crossing: no false merge
- [ ] Track fragmentation scenario: fragments aliased to one canonical
- [ ] Merge reversal: tentative merges reversed when contradicted
- [ ] Ghost duplicate count reduced without false positives increasing

### Gate E4: Robustness Validation
- [ ] Handles rapid track creation/destruction
- [ ] Handles same person multiple simultaneous tracks (no merge)
- [ ] Handles conflicting binding states (merge blocked)
- [ ] Handles NULL/missing appearance features gracefully
- [ ] Handles empty tracklet lists without crashes

---

## 8. Test Suite (Before Implementation)

**Tests to be implemented in `core/tests/test_merge_manager.py`**:

1. **test_merge_manager_init** - Instantiation
2. **test_canonical_id_mapping** - Alias resolution
3. **test_merge_scoring_high_confidence** - Score >= 60
4. **test_merge_scoring_tentative** - Score in [40, 60)
5. **test_merge_scoring_reject** - Score < 40
6. **test_spatial_rejection** - Distance too large
7. **test_motion_rejection** - Opposite motion
8. **test_appearance_rejection** - Embedding distance too large
9. **test_binding_compatibility** - Conflicting identities
10. **test_quality_threshold** - Insufficient samples
11. **test_time_exclusivity** - Overlap detection
12. **test_merge_reversal** - Tentative merge reversal
13. **test_canonical_consolidation** - Multiple merges to same canonical
14. **test_metrics_accuracy** - Metrics counting
15. **test_config_loading** - YAML config applies
16. **test_integration_with_binding** - Works with BindingManager
17. **test_ui_canonicalization** - UI gets canonical IDs
18. **test_edge_cases** - NULL features, empty lists, rapid events

---

## 9. Implementation Checklist

**Before writing code**:
- [ ] Review merge scoring function (section 3.3)
- [ ] Verify data structure definitions (section 3.1)
- [ ] Confirm integration points (section 4.3)
- [ ] Understand binding state compatibility (section 3.2, Criterion 5)

**During implementation**:
- [ ] Implement MergeManager class with core API
- [ ] Implement merge scoring algorithm exactly as specified
- [ ] Add config loading with all parameters
- [ ] Implement metrics collection
- [ ] Add logging for all decisions

**After implementation**:
- [ ] Run full test suite
- [ ] Run validation script
- [ ] Single-person scenario test
- [ ] Two-person crossing test
- [ ] Crowd scenario test
- [ ] Check for regressions in Phases A-D

---

## 10. Rollback Strategy

**If Phase E causes issues**:

1. **Disable immediately**: Set `governance.handoff_merge.enabled: false` in YAML
2. **System behavior**: Falls back to raw tracklet IDs (no aliasing)
3. **No data loss**: Merge history logged, but not applied
4. **No UI crashes**: UI adapter handles both modes

**Partial rollback** (if specific merge type causes issues):
- Disable TENTATIVE merges while keeping CONFIDENT merges
- Set `tentative_threshold: 100` (disables [40, 60) range)

---

## 11. Success Criteria for Phase E

**Metrics-based validation**:
- Ghost duplicate count decreases by 30-50% (measured in crowd)
- False positive rate remains <= baseline (no new false identities)
- Merge reversals are < 5% of merges executed (tentative merges stable)
- System responsiveness unchanged (same FPS as Phase D)
- Binding state stability unchanged

**Qualitative validation**:
- UI crowd view is more readable (fewer duplicate labels)
- Merge reasons in logs are explainable
- No single merge appears that shouldn't exist
- Confirmed identities remain stable through merges

---

## 12. Phase E Dependencies & Ordering

**Must be complete before Phase E**:
- ✅ Phase A (Config + Metrics)
- ✅ Phase B (Evidence Gating)
- ✅ Phase C (Binding State Machine)
- ✅ Phase D (Scheduler)

**Parallelizable with Phase E**:
- Nothing (Phase E is sequential)

**Phase E blocks**:
- Phase F (Simultaneous Merge) - optional, requires Phase E foundation

---

## 13. Documentation Requirements

**To be created during Phase E implementation**:
1. **Merge Manager API documentation** (50+ lines)
2. **Merge scoring algorithm explanation** (100+ lines)
3. **Integration guide for main_loop** (50+ lines)
4. **Config parameter explanation** (50+ lines)
5. **Troubleshooting guide** (30+ lines)

**Total expected documentation**: 300+ lines

---

## 14. Timeline Estimate

- **Implementation**: 2-3 hours (core + integration)
- **Testing**: 1-2 hours (unit tests + validation)
- **Debugging/tuning**: 1-2 hours (based on initial results)
- **Documentation**: 1 hour
- **Total**: 5-8 hours

---

## Summary

**Phase E Implementation is ready to proceed with**:
✅ Complete data structure specifications
✅ Detailed merge scoring algorithm
✅ Clear integration points
✅ Comprehensive acceptance gates
✅ Full test suite outline
✅ Rollback strategy
✅ Success criteria
✅ Configuration specifications

**Next Step**: Implement `identity/merge_manager.py` with all specifications above.

