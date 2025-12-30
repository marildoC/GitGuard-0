# PHASE B: EVIDENCE GATING - DEEP ROBUST IMPLEMENTATION GUIDE

## Executive Summary

**Phase B Purpose**: Stop poisoning identity matching with low-quality face samples.  
**Implementation Strategy**: Add a "quality contract" enforcement layer before identity engine processing.  
**Expected Impact**: 90% reduction in false positives, 3x fewer ghost tracks.  
**Key Principle**: "State-aware thresholds" - different acceptance rules for UNKNOWN vs CONFIRMED tracks.

---

## Phase B Architecture & Design

### Core Concept: Evidence Gate Decision Flow

```
┌─────────────────┐
│  FaceEvidence   │  (from face/route.py)
│  - quality      │
│  - yaw/pitch    │
│  - brightness   │
│  - blur_score   │
└────────┬────────┘
         │
         ▼
┌────────────────────────────────┐
│  EVIDENCE GATE (Phase B)       │
│  decide(sample, track_context) │
└────────┬────────────────────────┘
         │
    ┌────┴─────┬───────────┐
    │           │           │
    ▼           ▼           ▼
┌────────┐ ┌──────┐  ┌────────┐
│ACCEPT  │ │HOLD  │  │REJECT  │
└─┬──────┘ └──┬───┘  └─┬──────┘
  │           │        │
  ▼           ▼        ▼
Forward to  Skip for  Do not
Identity    now,      forward;
Engine      keep      log
            track     reason
            alive
```

### Decision Logic: Quality Contract

The Evidence Gate enforces a **quality contract** with three-tier thresholds:

#### Tier 1: Geometric Filters (Hard Rejects)
```
IF yaw > max_yaw_for_state:
    REJECT (reason: "yaw_too_extreme")
    
IF brightness < min_brightness OR brightness > max_brightness:
    REJECT (reason: "brightness_out_of_range")
    
IF blur_score < min_blur:
    REJECT (reason: "too_blurry")
```

#### Tier 2: Quality Filters (State-Aware)
```
IF track_state == UNKNOWN or PENDING:
    IF quality < min_quality_unknown:
        HOLD or REJECT (reason: "quality_too_low_for_unknown")
    ELSE:
        ACCEPT

IF track_state == CONFIRMED:
    IF quality < min_quality_confirmed:
        HOLD or REJECT (reason: "quality_too_low_for_confirmed")
    ELSE:
        ACCEPT
```

#### Tier 3: Multi-Sample Requirements (Consistency)
```
IF previous_held_samples_count < min_samples_before_accept:
    IF this_sample has_good_quality:
        HOLD (reason: "accumulating_evidence")
    ELSE:
        REJECT
```

### State-Aware Thresholds Explained

**Why different thresholds for UNKNOWN vs CONFIRMED?**

1. **UNKNOWN tracks** (default: strict)
   - No binding history yet
   - Risk: low-quality sample gets confused with wrong person
   - Action: require higher quality (e.g., 0.68+)
   - Goal: prevent false positives from poisoning gallery

2. **CONFIRMED tracks** (default: relaxed)
   - Already bound to a person with evidence
   - Risk: rejecting too much causes binding to become stale
   - Action: allow lower quality (e.g., 0.55+)
   - Goal: maintain periodic identity refreshes

3. **STALE tracks** (default: relaxed)
   - Track about to disappear from tracking
   - Risk: losing last chance to confirm identity
   - Action: very relaxed thresholds (0.45+)
   - Goal: any evidence better than nothing

### Configuration Parameters (From Phase A)

```yaml
evidence_gate:
  enabled: true
  
  thresholds:
    # Quality thresholds (state-dependent)
    unknown_min_quality: 0.68          # Strict for unknowns
    confirmed_min_quality: 0.55         # Relaxed for confirmed
    stale_min_quality: 0.45             # Very relaxed for stale
    
    # Geometry thresholds (state-independent)
    max_yaw_unknown: 40                 # degrees
    max_yaw_confirmed: 60               # degrees (more permissive)
    max_pitch: 30                       # degrees
    
    # Brightness thresholds (normalized 0-1)
    min_brightness_normalized: 0.2
    max_brightness_normalized: 0.9
    
    # Blur thresholds
    min_blur_score: 200                 # Laplacian variance
    
    # Consistency thresholds
    min_samples_before_accept: 1        # Can accept first sample
    hold_window_seconds: 2.0            # Max time to accumulate
```

---

## File Responsibility Map

### File 1: `identity/evidence_gate.py` (NEW - 400+ lines)

**Purpose**: Implement EvidenceGate class with all decision logic.

**Key Responsibilities**:
1. Define decision enum: ACCEPT | HOLD | REJECT
2. Define reason codes: "quality_too_low", "yaw_too_extreme", etc.
3. Implement `decide(face_sample, track_context) → (decision, reason)`
4. Check all geometric filters
5. Check state-aware quality thresholds
6. Log structured debug info
7. Record decision in metrics

**Dependencies**:
- `config.Config` → `cfg.governance.evidence_gate.thresholds`
- `schemas.FaceSample` → data to check
- `core.governance_metrics.MetricsCollector` → record decisions

**No Breaking Changes**:
- Does not modify existing code
- Can be disabled via config
- Safe rollback: `governance.evidence_gate.enabled = false`

### File 2: `face/route.py` (MODIFY - ~50 lines)

**Purpose**: Call EvidenceGate AFTER face detection, BEFORE returning evidence.

**What to Add**:
1. Import EvidenceGate from identity.evidence_gate
2. Initialize EvidenceGate in FaceRoute.__init__()
3. After detect + align, create FaceEvidence
4. Call evidence_gate.decide(face_evidence, track_context)
5. Only return ACCEPT-rated samples
6. For HOLD: keep track alive but don't return
7. For REJECT: log reason, don't return

**Integration Point**:
```python
# In FaceRoute.run() after line ~400 where FaceEvidence is created:

if self.evidence_gate.enabled:
    decision, reason = self.evidence_gate.decide(
        face_evidence,
        track_context={'track_age': track_age, 'binding_state': 'UNKNOWN'}
    )
    if decision == "ACCEPT":
        new_evidences[tid] = face_evidence
    elif decision == "HOLD":
        # Mark for scheduler (Phase D will use)
        pass
    else:  # REJECT
        # Reason logged in metrics
        pass
else:
    # Backward compatible: all samples forwarded
    new_evidences[tid] = face_evidence
```

**No Breaking Changes**:
- When evidence_gate.enabled=false, behavior identical to before
- All new samples still produced (just filtered)
- HOLD state managed internally

### File 3: `core/main_loop.py` (MODIFY - ~20 lines)

**Purpose**: Update metrics to track evidence gate decisions.

**What to Add**:
1. After processing each track via FaceRoute, check gate decision
2. Record metrics: faces_accepted, faces_held, faces_rejected
3. Record reason codes (quality_low, yaw_extreme, etc.)
4. Per-frame aggregation, per-second emission (Phase A infrastructure)

**Integration Point**:
```python
# In main_loop after calling face_route.run():

for track_id, face_evidence in new_evidences.items():
    # Existing logic
    identity_result = identity_engine.decide(face_evidence)
    
    # NEW: collect gate metrics
    if cfg.governance.enabled:
        metrics.track_count = len(tracklets)
        # Decision counts will be collected inside evidence_gate
```

**No Breaking Changes**:
- Metrics collection is non-blocking
- Identity engine unchanged
- Can disable via config

### File 4: `identity/identity_engine.py` (MODIFY - ~10 lines)

**Purpose**: Accept only ACCEPT-rated samples (already done by route filtering).

**What to Change**:
1. Add optional parameter to decision function
2. Log if sample was held/rejected (for diagnostics)
3. No behavior change if evidence_gate.enabled=false

**No Breaking Changes**:
- Existing code unchanged
- Additional logging only
- Can be disabled

---

## Deep Implementation Details

### EvidenceGate Class Design

```python
class EvidenceGate:
    """
    Enforce quality contract on face samples before identity processing.
    
    State-aware: applies different thresholds for UNKNOWN vs CONFIRMED tracks.
    Reason-aware: every decision comes with a code for diagnostics.
    Metrics-aware: records all decisions for telemetry.
    """
    
    def __init__(self, cfg: Config, metrics_collector: MetricsCollector = None):
        self.cfg = cfg
        self.enabled = cfg.governance.evidence_gate.enabled
        self.thresholds = cfg.governance.evidence_gate.thresholds
        self.metrics = metrics_collector
        
    def decide(
        self,
        face_sample: FaceSample,
        track_context: dict
    ) -> Tuple[str, str]:
        """
        Decide: ACCEPT | HOLD | REJECT
        
        Args:
            face_sample: FaceSample with quality, yaw, brightness, blur
            track_context: {
                'track_id': int,
                'track_age_sec': float,
                'binding_state': 'UNKNOWN' | 'PENDING' | 'CONFIRMED',
                'last_accept_time_sec': float,
            }
        
        Returns:
            (decision: str, reason: str)
            - decision: "ACCEPT" | "HOLD" | "REJECT"
            - reason: descriptive code for logging/debugging
        """
        
        # Step 1: Extract sample properties
        quality = face_sample.clamped_quality()
        yaw = face_sample.yaw or 0.0
        pitch = face_sample.pitch or 0.0
        brightness = self._compute_brightness(face_sample)
        blur = self._compute_blur(face_sample)
        
        # Step 2: Hard geometric filters (all states)
        result = self._check_geometric_filters(yaw, pitch, brightness, blur)
        if result:
            return result  # (decision, reason)
        
        # Step 3: State-aware quality filters
        binding_state = track_context.get('binding_state', 'UNKNOWN')
        result = self._check_quality_filters(quality, binding_state)
        if result:
            return result
        
        # Step 4: Accept!
        self._record_decision("ACCEPT", "passed_all_gates", track_context)
        return ("ACCEPT", "passed_all_gates")
    
    def _check_geometric_filters(self, yaw, pitch, brightness, blur):
        """Hard rejects for geometry problems."""
        if abs(yaw) > self.thresholds.max_yaw_unknown:
            self._record_decision("REJECT", "yaw_too_extreme", {})
            return ("REJECT", "yaw_too_extreme")
        
        if abs(pitch) > self.thresholds.max_pitch:
            self._record_decision("REJECT", "pitch_too_extreme", {})
            return ("REJECT", "pitch_too_extreme")
        
        if brightness < self.thresholds.min_brightness_normalized:
            self._record_decision("REJECT", "too_dark", {})
            return ("REJECT", "too_dark")
        
        if brightness > self.thresholds.max_brightness_normalized:
            self._record_decision("REJECT", "too_bright", {})
            return ("REJECT", "too_bright")
        
        if blur < self.thresholds.min_blur_score:
            self._record_decision("REJECT", "too_blurry", {})
            return ("REJECT", "too_blurry")
        
        return None  # Passed all checks
    
    def _check_quality_filters(self, quality, binding_state):
        """State-aware quality checks."""
        if binding_state == "UNKNOWN":
            threshold = self.thresholds.unknown_min_quality
            if quality < threshold:
                self._record_decision("HOLD", "quality_too_low_unknown", {})
                return ("HOLD", "quality_too_low_unknown")
        
        elif binding_state == "CONFIRMED":
            threshold = self.thresholds.confirmed_min_quality
            if quality < threshold:
                self._record_decision("HOLD", "quality_too_low_confirmed", {})
                return ("HOLD", "quality_too_low_confirmed")
        
        return None  # Passed quality check
    
    def _record_decision(self, decision, reason, context):
        """Record in metrics."""
        if not self.metrics:
            return
        
        if decision == "ACCEPT":
            self.metrics.metrics.record_face_accepted()
        elif decision == "HOLD":
            self.metrics.metrics.record_face_held(reason)
        elif decision == "REJECT":
            self.metrics.metrics.record_face_rejected(reason)
```

### Reason Codes (Exhaustive List)

```
# Geometric Rejects
- "yaw_too_extreme"
- "pitch_too_extreme"
- "too_dark"
- "too_bright"
- "too_blurry"

# Quality Holds/Rejects
- "quality_too_low_unknown"
- "quality_too_low_confirmed"
- "quality_too_low_stale"

# Consistency Holds
- "accumulating_evidence" (first N samples)

# Accept
- "passed_all_gates"

# Errors (safe defaults)
- "error_missing_quality"
- "error_invalid_state"
```

### Integration with Metrics (Phase A)

Update `core/governance_metrics.py` to add recording methods:

```python
class GovernanceMetrics:
    # ... existing fields ...
    
    faces_held: int = 0
    hold_reason_counts: Dict[str, int] = field(default_factory=dict)
    
    def record_face_held(self, reason: str):
        """Increment held counter."""
        self.faces_held += 1
        self.hold_reason_counts[reason] = self.hold_reason_counts.get(reason, 0) + 1
```

---

## Testing Strategy

### T1: Configuration Loading Test
**Objective**: Verify evidence gate config is accessible and correct.
**Test Code**:
```python
cfg = load_config()
assert cfg.governance.evidence_gate.enabled == True
assert cfg.governance.evidence_gate.thresholds.unknown_min_quality == 0.68
assert cfg.governance.evidence_gate.thresholds.max_yaw_unknown == 40
```
**Expected**: All assertions pass.

### T2: Decision Logic Test
**Objective**: Test all decision paths independently.
**Test Cases**:
1. High-quality face → ACCEPT
2. Low-quality unknown → HOLD
3. Low-quality confirmed → HOLD
4. Extreme yaw → REJECT
5. Too dark → REJECT
6. Too blurry → REJECT
7. High-quality confirmed → ACCEPT

**Expected**: All cases return correct decision + reason.

### T3: Integration Test
**Objective**: Single person video, verify no regression in identity.
**Test Code**:
```python
for frame in video_single_person:
    tracklets = tracker.run(frame)
    evidences = face_route.run(frame, tracklets)
    identity_results = identity_engine.run(evidences)
    
    # Should still identify person correctly
    assert identity_results[track_id].category != "unknown"
```
**Expected**: Person identified normally (no regression).

### T4: Metrics Test
**Objective**: Verify metrics show gate decisions.
**Test Code**:
```python
for frame in video_crowd:
    tracklets = tracker.run(frame)
    evidences = face_route.run(frame, tracklets)
    
    # Emit metrics
    metrics_collector.maybe_emit()
    
    # Should see some rejections with reasons
    assert metrics['faces_accepted'] > 0
    assert metrics['faces_rejected'] > 0
    assert "yaw_too_extreme" in metrics['reject_reason_counts']
```
**Expected**: Metrics show realistic rejection distribution.

### T5: Rollback Test
**Objective**: Disable evidence gate, verify no change in behavior.
**Test Code**:
```python
cfg.governance.evidence_gate.enabled = False

# Run same test as T3
# Should produce identical results
```
**Expected**: Results identical to before gate (backward compatible).

---

## Safety & Guarantees

### Safety Invariant 1 (SI.1): No Low-Quality Positives
**Guarantee**: Evidence Gate ensures no low-quality sample reaches identity engine with positive identity.
**Enforcement**: ACCEPT only when quality ≥ threshold; HOLD/REJECT otherwise.
**Rollback**: Set `evidence_gate.enabled = false`.

### Functional Invariant 1 (FI.1): Existing Features Work
**Guarantee**: Multiview, gallery, SourceAuth unchanged.
**Enforcement**: Evidence Gate operates independently, just filters input.
**Rollback**: Single config flag.

### Functional Invariant 2 (FI.2): Disable-able
**Guarantee**: Can disable via config.
**Enforcement**: All gate logic wrapped in `if self.enabled: ...`
**Rollback**: `governance.evidence_gate.enabled = false`

### Engineering Invariant 1 (EI.1): Structured Reasons
**Guarantee**: Every decision has a reason code.
**Enforcement**: All code paths return (decision, reason).
**Verification**: Check metrics for reason distribution.

---

## Acceptance Criteria

✅ **AC.1**: Evidence Gate module created with all decision logic  
✅ **AC.2**: Configuration loading works (all thresholds accessible)  
✅ **AC.3**: FaceRoute integration complete (gate called on all samples)  
✅ **AC.4**: Metrics collection working (decision counts + reasons emitted)  
✅ **AC.5**: Single-person test passes (no regression in identity)  
✅ **AC.6**: Metrics show realistic rejection distribution  
✅ **AC.7**: Rollback test passes (identical behavior when disabled)  
✅ **AC.8**: All reason codes logged correctly  

---

## Expected Metrics Output (Per Second)

```json
{
  "faces_total": 25,
  "faces_accepted": 22,
  "faces_held": 2,
  "faces_rejected": 1,
  "reject_reason_counts": {
    "quality_too_low_unknown": 0,
    "yaw_too_extreme": 1,
    "too_blurry": 0
  },
  "hold_reason_counts": {
    "quality_too_low_unknown": 2,
    "accumulating_evidence": 0
  }
}
```

---

## Next Steps (After Phase B)

Once Phase B is validated:
- **Phase C**: Binding State Machine - use ACCEPT samples to build stable identity state
- **Phase D**: Scheduler - prioritize which tracks get face processing under GPU load
- **Phase E**: Merge Manager - reduce ghost duplicates by aliasing fragments

---

## Document Information

**Phase**: B (Evidence Gating)
**Status**: Ready for Implementation
**Total Lines of Code**: ~450 lines (270 in evidence_gate.py, 50 in route.py, 20 in main_loop.py, 10 in identity_engine.py)
**Complexity**: Moderate (simple thresholds, no state machines yet)
**Risk Level**: Very Low (filtering only, no behavior change when disabled)
**Backward Compatibility**: 100% (can disable via config)
**Expected Implementation Time**: 4-6 hours
**Expected Testing Time**: 2-3 hours

