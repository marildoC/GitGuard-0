# QUICK REFERENCE: CRITICAL GAPS & FIXES

---

## THE 5 CRITICAL GAPS

### Gap 1: Evidence Gate DISABLED
- **Location**: `identity/evidence_gate.py` line 161
- **Problem**: `self.enabled = False` (default)
- **Impact**: No quality filtering, all faces accepted
- **Fix**: Change to `True`, add config section
- **Time**: 15 min

### Gap 2: Binding Manager NOT CALLED
- **Location**: `identity/identity_engine_multiview.py` method `decide()`
- **Problem**: Matches made but binding never invoked
- **Impact**: No binding state machine, all tracks stay UNKNOWN
- **Fix**: Add `binding_manager.process_evidence()` call
- **Time**: 45 min

### Gap 3: Quality NOT SMOOTHED
- **Location**: `identity/evidence_gate.py` lines 90-110
- **Problem**: Smoothing code exists but gate is disabled, so never runs
- **Impact**: Evidence window sees oscillating quality (±0.08 variance)
- **Fix**: Enable gate, ensure smoothing executes
- **Time**: 20 min

### Gap 4: User BLIND to Binding State
- **Location**: `ui/overlay.py` function `_identity_label()`
- **Problem**: Shows identity but not binding state or confidence
- **Impact**: User cannot tell if match is confirmed or tentative
- **Fix**: Display binding_state + confidence, color code
- **Time**: 20 min

### Gap 5: SA Engine ALWAYS Uncertain
- **Location**: `config/default.yaml`
- **Problem**: SA engine enabled but outputs all UNC (no signal)
- **Impact**: Wasted 50ms per frame, no spoof detection
- **Fix**: Set `source_auth.enabled = false`
- **Time**: 5 min

---

## OBSERVED SYMPTOMS

### In Test Logs:
```
✗ binding: {None: 1, None: 2, None: 3}  ← All unbound
✗ faces (accept=0, hold=0, reject=0)    ← Gate not working
✗ sa_states UNC=3.2 REAL=0.0 SPOOF=0.0  ← All uncertain
✗ confidence=0.765 (raw score, not binding) ← No state tracking
```

### Expected After Fixes:
```
✓ binding: {1: PENDING, 2: CONFIRMED_WEAK, 3: UNKNOWN}
✓ faces (accept=2, hold=1, reject=0)
✓ sa_states disabled (save 50ms)
✓ confidence=0.85 (from binding machine, grows over time)
```

---

## QUICK FIX CHECKLIST

### Fix 1: Enable Evidence Gate (15 min)
```yaml
# Add to config/default.yaml
governance:
  evidence_gate:
    enabled: true        # ← Changed from disabled
    thresholds:
      unknown_min_quality: 0.70
      confirmed_min_quality: 0.60
```

```python
# In identity/evidence_gate.py line ~161
self.enabled = getattr(eg_config, 'enabled', True)  # ← Changed to True
```

**Verification**: `assert eg.enabled == True`

---

### Fix 2: Integrate Binding Manager (45 min)

**File**: `identity/identity_engine_multiview.py` method `decide()`

**Before**:
```python
decision = IdentityDecision(
    track_id=signal.track_id,
    identity_id=best_person,
    confidence=best_score,  # Raw score
    binding_state=None,     # ← Always None
)
```

**After**:
```python
binding_result = self.binding_manager.process_evidence(
    track_id=signal.track_id,
    person_id=best_person,
    score=best_score,
    second_best_score=second_best_score,
    quality=signal.quality,
    timestamp=signal.ts,
)

decision = IdentityDecision(
    track_id=signal.track_id,
    identity_id=binding_result.person_id,
    confidence=binding_result.confidence,      # From binding
    binding_state=binding_result.binding_state, # UNKNOWN/PENDING/CONFIRMED
)
```

**Verification**: Check logs show "binding_state=PENDING" then "binding_state=CONFIRMED_WEAK"

---

### Fix 3: Enable Quality Smoothing (20 min)

**File**: `identity/evidence_gate.py` function `decide()`

**Key Change**: Return quality_smoothed instead of raw quality

```python
# Initialize smoothing buffer
if track_id not in self.quality_buffers:
    self.quality_buffers[track_id] = deque(maxlen=5)

# Add to buffer and compute moving average
self.quality_buffers[track_id].append(quality_raw)
quality_smoothed = sum(self.quality_buffers[track_id]) / len(self.quality_buffers[track_id])

# Use smoothed for decision
if quality_smoothed < min_quality:
    return (HOLD, reason, quality_smoothed)

# Return smoothed quality
return (ACCEPT, reason, quality_smoothed)
```

**Verification**: Log should show `quality_raw=0.60 → quality_smoothed=0.67`

---

### Fix 4: Disable SA Engine (5 min)

**File**: `config/default.yaml`

```yaml
source_auth:
  enabled: false  # ← Change from true
```

**Verification**: FPS should improve from 5.0 to 5.5

---

### Fix 5: Improve UI Feedback (20 min)

**File**: `ui/overlay.py` function `_identity_label()`

```python
def _identity_label(decision):
    binding_state = getattr(decision, 'binding_state', 'UNKNOWN')
    confidence = getattr(decision, 'confidence', 0.0) * 100
    
    if binding_state == "CONFIRMED_STRONG":
        return f"{decision.identity_id} ✓ ({confidence:.0f}%)", (0, 255, 0)  # Green
    elif binding_state == "CONFIRMED_WEAK":
        return f"{decision.identity_id} ≈ ({confidence:.0f}%)", (0, 165, 255)  # Orange
    elif binding_state == "PENDING":
        return f"{decision.identity_id} ? ({confidence:.0f}%)", (0, 255, 255)  # Yellow
    else:
        return f"{decision.identity_id} ○ ({confidence:.0f}%)", (200, 200, 200)  # Gray
```

**Verification**: Overlay shows "p_0005 ✓ (92%)" in green (not just "p_0005")

---

## EXPECTED IMPROVEMENTS

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| **Binding States Used** | 0 | 4 | ✓ 100% improvement |
| **Confidence Growth** | 0.0 → 0.0 | 0.0 → 0.9 | ✓ New feature |
| **Quality Noise** | ±0.08 | ±0.01 | ✓ -87% |
| **FPS** | 5.0 | 5.5 | ✓ +10% |
| **User Visibility** | Hidden | Explicit | ✓ Visible |
| **False Positive Risk** | High | Low | ✓ Protected |

---

## TESTING QUICK VALIDATION

### Test 1: Evidence Gate Enabled
```python
from identity.evidence_gate import EvidenceGate
eg = EvidenceGate(cfg)
assert eg.enabled == True
```

### Test 2: Quality Smoothing Works
```python
sample = FaceSample(quality=0.60)
decision, reason, q_smooth = eg.decide(sample, {'track_id': 1})
assert q_smooth > 0.0
# After 5 frames: assert q_smooth ≈ 0.67
```

### Test 3: Binding Integration
```python
decisions = engine.decide(signals)
assert decisions[0].binding_state != None
assert decisions[0].confidence > 0.0  # After confirmation
```

### Test 4: UI Shows Binding
```
Overlay should show:
"p_0005 ✓ (92%)"  ← binding state + confidence visible
```

### Test 5: FPS Improvement
```
5.0 FPS → 5.5 FPS with SA disabled
```

---

## IMPLEMENTATION TIMELINE

```
Time Block 1 (30 min):
  Fix 1: Enable Evidence Gate (15 min)
  Fix 4: Disable SA Engine (5 min)
  Fix 5: UI Feedback (20 min) [can overlap]

Time Block 2 (45 min):
  Fix 2: Integrate Binding Manager (45 min)

Time Block 3 (20 min):
  Fix 3: Quality Smoothing (20 min)

Verification (20 min):
  Run quick tests (10 min)
  Manual camera testing (10 min)

TOTAL: 2-3 hours
```

---

## RED FLAGS DURING IMPLEMENTATION

### Red Flag 1: "binding_state still None"
- **Check**: Is `binding_manager.process_evidence()` being called?
- **Check**: Is it in the `decide()` method?
- **Check**: Is result being used in IdentityDecision?

### Red Flag 2: "FPS didn't improve"
- **Check**: Did you set `source_auth.enabled = false`?
- **Check**: Did you restart the application?

### Red Flag 3: "Quality still oscillating"
- **Check**: Is evidence gate enabled?
- **Check**: Is `quality_smoothed` being returned and used?
- **Check**: Is buffer being populated?

### Red Flag 4: "Overlay doesn't show binding state"
- **Check**: Is IdentityDecision object populating `binding_state`?
- **Check**: Is `_identity_label()` receiving the object?
- **Check**: Is color being applied in overlay draw?

---

## ROLLBACK PROCEDURE

If anything breaks:

```python
# Revert config
source_auth:
  enabled: true        # Back to original
evidence_gate:
  enabled: false       # Back to original

# Comment out binding integration
# In identity_engine_multiview.py, remove:
# binding_result = self.binding_manager.process_evidence(...)

# Revert UI change
# In ui/overlay.py, revert to simple label
```

---

## SUCCESS CONFIRMATION

After implementation, you should see in logs:

```
✓ EvidenceGate enabled | unknown_min_q=0.70
✓ Binding: track=1 person=p_0005 state=PENDING confidence=0.35
✓ Binding: track=1 person=p_0005 state=CONFIRMED_WEAK confidence=0.67
✓ Binding: track=1 person=p_0005 state=CONFIRMED_STRONG confidence=0.92
✓ FPS=5.5 (improved from 5.0)
✓ Overlay shows: "p_0005 ✓ (92%)" in green
```

---

## DOCUMENT REFERENCE

For more details:
- **DEEP_SYSTEM_ANALYSIS.md** - Full analysis of all gaps
- **IMPLEMENTATION_FIXES_GUIDE.md** - Detailed code changes with context
- **EFFICIENCY_ROBUSTNESS_METRICS.md** - Performance metrics and comparisons
- **EXECUTIVE_SUMMARY_ANALYSIS.md** - Decision-maker summary

---

**Ready to implement?** Start with Fix 1 (enable evidence gate), takes 15 minutes.

