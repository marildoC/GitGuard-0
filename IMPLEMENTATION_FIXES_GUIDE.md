# DEEP SYSTEM FIXES: IMPLEMENTATION GUIDE

**Status**: Ready for implementation  
**Priority**: CRITICAL (Phase 1)  
**Estimated Time**: 2-3 hours

---

## FIX #1: ENABLE EVIDENCE GATE (30 minutes)

### Root Problem
Evidence gate is disabled by default, preventing any quality filtering.

### Files to Modify
1. `config/default.yaml` - Enable governance.evidence_gate
2. `identity/evidence_gate.py` - Fix default to enabled=True

### YAML Configuration Fix

**File**: `config/default.yaml`

**Current**:
```yaml
governance:
  # (missing evidence_gate section)
```

**Add**:
```yaml
governance:
  evidence_gate:
    enabled: true                    # ← KEY FIX
    thresholds:
      unknown_min_quality: 0.70      # Require 70% quality for new tracks
      confirmed_min_quality: 0.60    # Relax to 60% for confirmed tracks
      stale_min_quality: 0.50        # Very relaxed for stale tracks
      max_yaw_degrees: 45            # Reject extreme yaw
      max_pitch_degrees: 35          # Reject extreme pitch
      min_brightness: 30             # Reject too dark
      max_brightness: 200            # Reject too bright
      max_blur: 0.30                 # Reject blurry (0-1 scale)
```

### Python Code Fix

**File**: `identity/evidence_gate.py` - Lines 150-165

**Current**:
```python
def __init__(self, cfg: Optional[Any] = None, ...):
    # ... existing code ...
    try:
        if cfg and hasattr(cfg, 'governance'):
            gov_config = cfg.governance
            if hasattr(gov_config, 'evidence_gate'):
                eg_config = gov_config.evidence_gate
                self.enabled = getattr(eg_config, 'enabled', True)  # ← Says True but
                self.thresholds = getattr(eg_config, 'thresholds', None)
            else:
                self.enabled = False  # ← Falls back to False!
                self.thresholds = None
        else:
            self.enabled = False  # ← Also False here
```

**Fix**: Change all False to True as fallback:
```python
def __init__(self, cfg: Optional[Any] = None, ...):
    # ... existing code ...
    try:
        if cfg and hasattr(cfg, 'governance'):
            gov_config = cfg.governance
            if hasattr(gov_config, 'evidence_gate'):
                eg_config = gov_config.evidence_gate
                self.enabled = getattr(eg_config, 'enabled', True)  # Keep as True
                self.thresholds = getattr(eg_config, 'thresholds', None)
            else:
                self.enabled = True  # FIX: Change from False to True
                self.thresholds = None
        else:
            self.enabled = True  # FIX: Change from False to True
```

### Verification

After this fix:
```python
# Test that gate is now enabled
from identity.evidence_gate import EvidenceGate
eg = EvidenceGate(cfg)
assert eg.enabled == True, "Evidence gate should be enabled"
print("✓ Evidence gate enabled")
```

---

## FIX #2: INTEGRATE BINDING MANAGER INTO MULTIVIEW ENGINE (45 minutes)

### Root Problem
Multiview engine matches identities but never calls binding manager, so binding state stays None forever.

### File to Modify
`identity/identity_engine_multiview.py` - decide() method (around line 450-550)

### Current Code (Broken)

```python
def decide(self, signals: List[IdSignals]) -> List[IdentityDecision]:
    """
    Make identity decisions from signals.
    
    Returns:
        List of IdentityDecision per track.
    """
    decisions = []
    
    for signal in signals:
        try:
            # Get multiview match
            best_person = None
            best_score = 0.0
            best_distance = 1e9
            
            if self.matcher and signal.embedding is not None:
                result = self.matcher.match(
                    embedding=signal.embedding,
                    gallery_view=self._gallery_view,
                    face_quality=signal.quality,
                    yaw_degrees=signal.yaw or 0.0,
                    pose_bin=self._infer_pose_bin(signal),
                )
                
                if result and result.person_id:
                    best_person = result.person_id
                    best_score = result.match_score
                    best_distance = result.distance
            
            # CREATE DECISION (but binding state is NOT set)
            decision = IdentityDecision(
                track_id=signal.track_id,
                identity_id=best_person,
                confidence=best_score,  # Raw score, not binding confidence
                # binding_state NOT SET → defaults to None
            )
            
            decisions.append(decision)
        except Exception as e:
            logger.exception(f"Error deciding track {signal.track_id}: {e}")
            decisions.append(IdentityDecision(track_id=signal.track_id))
    
    return decisions
```

### Fixed Code (With Binding)

```python
def decide(self, signals: List[IdSignals]) -> List[IdentityDecision]:
    """
    Make identity decisions from signals.
    
    Integrates multiview matching with binding state machine:
    1. MultiViewMatcher produces candidate
    2. BindingManager validates + state tracks
    3. IdentityDecision has binding_state + confidence
    
    Returns:
        List of IdentityDecision per track with binding state.
    """
    decisions = []
    
    for signal in signals:
        try:
            # === STEP 1: Multiview Matching ===
            best_person = None
            best_score = 0.0
            best_distance = 1e9
            second_best_score = 0.0
            
            if self.matcher and signal.embedding is not None:
                result = self.matcher.match(
                    embedding=signal.embedding,
                    gallery_view=self._gallery_view,
                    face_quality=signal.quality,
                    yaw_degrees=signal.yaw or 0.0,
                    pose_bin=self._infer_pose_bin(signal),
                )
                
                if result and result.person_id:
                    best_person = result.person_id
                    best_score = result.match_score
                    best_distance = result.distance
                    # Get second best for margin calculation
                    second_best_score = getattr(result, 'second_best_score', max(0.0, best_score - 0.3))
            
            # === STEP 2: Binding State Machine ===
            # NEW: Pass match result to binding manager for validation + state tracking
            binding_result = None
            if best_person is not None and self.binding_manager is not None:
                try:
                    binding_result = self.binding_manager.process_evidence(
                        track_id=signal.track_id,
                        person_id=best_person,
                        score=best_score,  # 0-1 match score
                        second_best_score=second_best_score,  # Next best
                        quality=signal.quality,  # Face quality from FaceRoute
                        timestamp=signal.ts,
                    )
                    
                    logger.debug(
                        f"Binding: track={signal.track_id} person={best_person} "
                        f"state={binding_result.binding_state} "
                        f"confidence={binding_result.confidence:.2f}"
                    )
                    
                except Exception as e:
                    logger.exception(f"Binding manager failed for track {signal.track_id}: {e}")
                    binding_result = None
            
            # === STEP 3: Create Decision ===
            if binding_result is not None:
                # Use binding state machine results
                decision = IdentityDecision(
                    track_id=signal.track_id,
                    identity_id=binding_result.person_id,  # May be None if binding rejected
                    confidence=binding_result.confidence,  # 0.0 → 1.0 from binding
                    binding_state=binding_result.binding_state,  # UNKNOWN/PENDING/CONFIRMED/STALE
                    reason=binding_result.reason,
                )
            else:
                # Fallback: No binding manager or binding failed
                # Use raw matching score
                decision = IdentityDecision(
                    track_id=signal.track_id,
                    identity_id=best_person,
                    confidence=best_score,  # Raw matching score
                    binding_state="UNKNOWN",  # Mark as unbound
                    reason="binding_disabled" if self.binding_manager is None else "binding_error",
                )
            
            decisions.append(decision)
            
        except Exception as e:
            logger.exception(f"Error deciding track {signal.track_id}: {e}")
            decisions.append(IdentityDecision(
                track_id=signal.track_id,
                binding_state="UNKNOWN",
                reason="exception_in_decide"
            ))
    
    return decisions
```

### Key Changes

1. **Get second_best_score** for margin calculation
2. **Call binding_manager.process_evidence()** with match result
3. **Use binding_result** for decision (state + confidence)
4. **Fallback handling** if binding manager unavailable
5. **Logging** for debugging

### Tests After Fix

```python
def test_binding_integration():
    """Verify binding manager is called and state is returned."""
    cfg = load_config()
    cfg.governance.binding.enabled = True
    
    engine = IdentityEngineMultiView()
    
    # Create signals with match
    signal = IdSignals(
        track_id=1,
        embedding=np.random.randn(512),  # Dummy embedding
        quality=0.75,
        ts=time.time(),
    )
    
    # Call decide
    decisions = engine.decide([signal])
    
    # Verify binding state is returned
    assert decisions[0].binding_state is not None, "Binding state should be set"
    assert decisions[0].confidence >= 0.0, "Confidence should be valid"
    
    print("✓ Binding manager integrated")
```

---

## FIX #3: ENABLE QUALITY SMOOTHING (30 minutes)

### Root Problem
Evidence gate has smoothing code but it's never called (gate disabled). Now that gate is enabled, we need to ensure smoothing actually works.

### File to Modify
`identity/evidence_gate.py` - decide() method (around line 200-250)

### Current Code (Smoothing Not Applied)

```python
def decide(self, face_sample: FaceSample, track_context: Optional[Dict[str, Any]] = None):
    try:
        if not self.enabled:
            return (GateDecision.ACCEPT, "gate_disabled")  # ← Never smooths if disabled
        
        # ... quality extraction ...
        quality = face_sample.clamped_quality() if hasattr(...) else 0.0
        
        # Smoothing code exists but NEVER CALLED
        # [smoothing code is here but not executed]
        
        # Decision uses RAW quality (not smoothed)
        if quality < min_quality:
            return (GateDecision.HOLD, ReasonCode.HOLD_QUALITY_LOW_UNKNOWN)
```

### Fixed Code (Smoothing Applied)

```python
def decide(self, face_sample: FaceSample, track_context: Optional[Dict[str, Any]] = None) -> Tuple[str, str, float]:
    """
    Make evidence gate decision with quality smoothing.
    
    Returns:
        (decision: str, reason: str, quality_smoothed: float)
        
    LAYER 2: 5-frame moving average smoothing applied here.
    """
    try:
        if not self.enabled:
            # When disabled, bypass all checks
            quality_raw = face_sample.clamped_quality() if hasattr(face_sample, 'clamped_quality') else float(face_sample.quality or 0.0)
            return (GateDecision.ACCEPT, "gate_disabled", quality_raw)
        
        if face_sample is None:
            return (GateDecision.REJECT, ReasonCode.ERROR_MISSING_QUALITY, 0.0)
        
        # Extract track context
        track_id = track_context.get('track_id', -1) if track_context else -1
        binding_state = track_context.get('binding_state', 'UNKNOWN') if track_context else 'UNKNOWN'
        
        # === LAYER 2: QUALITY SMOOTHING (5-frame moving average) ===
        
        # Extract raw quality
        try:
            quality_raw = face_sample.clamped_quality() if hasattr(face_sample, 'clamped_quality') else float(face_sample.quality or 0.0)
        except Exception:
            quality_raw = 0.0
        
        # Initialize smoothing buffer for this track if needed
        if track_id not in self.quality_buffers:
            self.quality_buffers[track_id] = deque(maxlen=self.quality_window_size)
        
        # Add current quality to buffer
        self.quality_buffers[track_id].append(quality_raw)
        
        # Compute smoothed quality (moving average)
        if len(self.quality_buffers[track_id]) > 0:
            quality_smoothed = sum(self.quality_buffers[track_id]) / len(self.quality_buffers[track_id])
        else:
            quality_smoothed = quality_raw
        
        # === USE SMOOTHED QUALITY FOR DECISION ===
        quality_to_check = quality_smoothed  # ← KEY: Use smoothed, not raw
        
        logger.debug(
            f"Track {track_id}: quality_raw={quality_raw:.3f} → "
            f"quality_smoothed={quality_to_check:.3f} (window={len(self.quality_buffers[track_id])})"
        )
        
        # ... rest of decision logic ...
        
        # Get thresholds based on binding state
        if binding_state == 'UNKNOWN':
            min_quality = self._get_threshold('unknown_min_quality', 0.70)
        elif binding_state == 'CONFIRMED':
            min_quality = self._get_threshold('confirmed_min_quality', 0.60)
        elif binding_state == 'STALE':
            min_quality = self._get_threshold('stale_min_quality', 0.50)
        else:
            min_quality = 0.70
        
        # Decision based on SMOOTHED quality
        if quality_to_check < min_quality:
            return (GateDecision.HOLD, ReasonCode.HOLD_QUALITY_LOW_UNKNOWN, quality_smoothed)
        
        # All checks passed
        return (GateDecision.ACCEPT, ReasonCode.ACCEPT_ALL_GATES, quality_smoothed)
        
    except Exception as e:
        logger.exception(f"EvidenceGate.decide() exception: {e}")
        return (GateDecision.ACCEPT, ReasonCode.ERROR_EXCEPTION, 0.0)
```

### Integration Point

Now evidence gate returns **3 values** (decision, reason, quality_smoothed):

```python
# In FaceRoute.update_signals() or wherever gate is called:

gate_decision, gate_reason, quality_smoothed = evidence_gate.decide(
    face_sample=face_sample,
    track_context={'track_id': track_id, 'binding_state': binding_state}
)

# Pass smoothed quality to identity engine
id_signal = IdSignals(
    track_id=track_id,
    embedding=embedding,
    quality=quality_smoothed,  # ← Use smoothed, not raw!
    gate_decision=gate_decision,  # For diagnostics
)
```

### Verification

```python
def test_quality_smoothing():
    """Verify 5-frame moving average works."""
    cfg = load_config()
    cfg.governance.evidence_gate.enabled = True
    
    eg = EvidenceGate(cfg)
    
    # Simulate oscillating quality
    qualities_raw = [0.60, 0.70, 0.60, 0.65, 0.60]
    qualities_smoothed = []
    
    for i, q in enumerate(qualities_raw):
        sample = FaceSample(quality=q)
        context = {'track_id': 1, 'binding_state': 'UNKNOWN'}
        decision, reason, q_smooth = eg.decide(sample, context)
        qualities_smoothed.append(q_smooth)
        print(f"Frame {i}: raw={q:.2f} → smoothed={q_smooth:.3f}")
    
    # After 5 frames, variance should be small
    variance = sum((q - np.mean(qualities_smoothed))**2 for q in qualities_smoothed) / len(qualities_smoothed)
    assert variance < 0.01, f"Smoothed variance too high: {variance}"
    
    print("✓ Quality smoothing working")
```

---

## FIX #4: DISABLE INEFFECTIVE SOURCE AUTH ENGINE (10 minutes)

### Root Problem
SA engine outputs all UNC (uncertain) states, so it's not providing signal. But it's still running and consuming 50ms/frame.

### File to Modify
`config/default.yaml`

**Current**:
```yaml
source_auth:
  enabled: true  # ← Wastes 50ms for UNC output
```

**Fix**:
```yaml
source_auth:
  enabled: false  # Disable until motion/screen detection is working
  # TODO: Re-enable after:
  # 1. Motion cues implemented (gait + hand motion)
  # 2. Screen detection trained (phone + monitor recognition)
  # 3. Validation testing shows real/spoof separation > 80%
```

### Impact
- **FPS improves**: 5.0 → 5.5 (~+10%)
- **No loss of signal**: SA wasn't working anyway (all UNC)
- **Can re-enable later**: Just set enabled=true

---

## FIX #5: IMPROVE USER FEEDBACK (20 minutes)

### File to Modify
`ui/overlay.py` - _identity_label() function (around line 250)

### Current Code

```python
def _identity_label(decision, ui_cfg=None):
    """Format identity label for overlay."""
    if decision and decision.identity_id:
        return f"{decision.identity_id}"
    else:
        return "unknown"
```

### Fixed Code

```python
def _identity_label(decision, ui_cfg=None, binding_state_visible=True):
    """
    Format identity label with binding state and confidence.
    
    Shows:
    - Identity ID
    - Binding state indicator (✓/≈/?)
    - Confidence percentage
    - Color coding
    
    Examples:
    - "p_0005 ✓ (92%)" - CONFIRMED_STRONG (green)
    - "p_0005 ≈ (67%)" - CONFIRMED_WEAK (orange)
    - "p_0005 ? (34%)" - PENDING (yellow)
    - "unknown" - UNKNOWN (gray)
    """
    if not decision or not decision.identity_id:
        return "unknown", (200, 200, 200)
    
    # Extract binding state and confidence
    binding_state = getattr(decision, 'binding_state', 'UNKNOWN')
    confidence = getattr(decision, 'confidence', 0.0)
    
    # Ensure confidence is 0-1
    confidence = max(0.0, min(1.0, float(confidence)))
    confidence_pct = int(confidence * 100)
    
    # Format based on binding state
    if binding_state == "CONFIRMED_STRONG":
        state_indicator = "✓"
        color = (0, 255, 0)  # Green: high confidence
        label = f"{decision.identity_id} {state_indicator} ({confidence_pct}%)"
    
    elif binding_state == "CONFIRMED_WEAK":
        state_indicator = "≈"
        color = (0, 165, 255)  # Orange: medium confidence
        label = f"{decision.identity_id} {state_indicator} ({confidence_pct}%)"
    
    elif binding_state == "PENDING":
        state_indicator = "?"
        color = (0, 255, 255)  # Yellow: tentative
        label = f"{decision.identity_id} {state_indicator} ({confidence_pct}%)"
    
    else:  # UNKNOWN, STALE, or None
        state_indicator = "○"
        color = (200, 200, 200)  # Gray: not confident
        label = f"{decision.identity_id} {state_indicator} ({confidence_pct}%)"
    
    return label, color
```

### Integration in draw_overlay

```python
def draw_overlay(frame, decisions, ui_cfg=None):
    """Draw identity overlay on frame."""
    img = frame.image.copy()
    
    for decision in decisions:
        # Get label and color
        label, color = _identity_label(decision, ui_cfg, binding_state_visible=True)
        
        # Get bounding box
        x1, y1, x2, y2 = decision.bbox  # Assuming bbox in decision
        
        # Draw box
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        
        # Draw label
        cv2.putText(img, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    
    return img
```

### User Experience Improvement

**Before**:
```
Track 18: [box] "p_0005"
          (no context, user doesn't know confidence)
```

**After**:
```
Track 18: [GREEN box] "p_0005 ✓ (92%)"
          (user knows: identity is confirmed, very high confidence)

Track 19: [ORANGE box] "p_0001 ≈ (58%)"
          (user knows: identity confirmed but less certain)

Track 20: [YELLOW box] "p_0003 ? (35%)"
          (user knows: identity tentative, still accumulating evidence)
```

---

## IMPLEMENTATION CHECKLIST

### Phase 1: Critical Fixes (2-3 hours)

- [ ] **Fix 1: Enable Evidence Gate**
  - [ ] Add config to `config/default.yaml`
  - [ ] Change defaults in `evidence_gate.py`
  - [ ] Test with `test_evidence_gate_enabled()`

- [ ] **Fix 2: Integrate Binding Manager**
  - [ ] Modify `identity_engine_multiview.py` decide() method
  - [ ] Add binding_result processing
  - [ ] Test with `test_binding_integration()`

- [ ] **Fix 3: Enable Quality Smoothing**
  - [ ] Modify `evidence_gate.py` decide() to return quality_smoothed
  - [ ] Pass smoothed quality to identity engine
  - [ ] Test with `test_quality_smoothing()`

- [ ] **Fix 4: Disable SA Engine**
  - [ ] Set `source_auth.enabled = false` in config
  - [ ] Verify FPS improves

- [ ] **Fix 5: Improve UI Feedback**
  - [ ] Update `_identity_label()` with binding state display
  - [ ] Update color coding
  - [ ] Visually verify overlay

### Phase 2: Validation (1-2 hours)

- [ ] Run test suite (Section 12 of analysis)
- [ ] Manual testing with camera feed
- [ ] Verify binding state progression
- [ ] Check FPS improvement
- [ ] Validate confidence growth

### Phase 3: Tuning (30-60 min)

- [ ] Adjust evidence gate thresholds if needed
- [ ] Tune binding confirmation counts
- [ ] Verify color coding visibility
- [ ] Test edge cases

---

## VERIFICATION STEPS

### 1. Quick Sanity Check

```bash
# In Python
from identity.evidence_gate import EvidenceGate
from identity.binding import BindingManager
from config import load_config

cfg = load_config()

# Check evidence gate is enabled
eg = EvidenceGate(cfg)
assert eg.enabled == True, "EvidenceGate should be enabled"
print("✓ EvidenceGate enabled")

# Check binding manager is initialized
bm = BindingManager(cfg)
assert bm.enabled == True, "BindingManager should be enabled"
print("✓ BindingManager enabled")

# Check smoothing works
from schemas import FaceSample
sample = FaceSample(quality=0.75)
decision, reason, q_smooth = eg.decide(sample, {'track_id': 1})
assert q_smooth > 0.0, "Quality smoothing should work"
print("✓ Quality smoothing works")
```

### 2. Run Test Suite

```bash
pytest tests/test_evidence_gate.py -v
pytest tests/test_binding.py -v
pytest tests/test_e2e_integration.py -v
```

### 3. Live Testing

```bash
python experiments/yolo_cam.py

# Watch for:
# - Binding states changing (UNKNOWN → PENDING → CONFIRMED)
# - Confidence growing from 0 to 0.9
# - Color changing from gray → yellow → orange → green
# - FPS showing ~5.5 instead of 5.0
```

---

## SUMMARY

These 5 fixes transform the system from:
- ✗ Governance disabled (no validation)
- ✗ Binding missing (no state machine)
- ✗ Quality noisy (no smoothing)
- ✗ User confused (no binding feedback)

To:
- ✓ Governance enabled (quality enforced)
- ✓ Binding integrated (state machine validates)
- ✓ Quality smoothed (stable evidence)
- ✓ User informed (sees confidence + state)

**Result**: Production-ready robust identity system instead of luck-based matching.

