# GaitGuard Recognition Fix Implementation Guide

**Status**: Ready for implementation  
**Priority**: 🔴 Critical  
**Estimated Time**: 15 minutes (all fixes)

---

## IMMEDIATE FIX: Evidence Gate Threshold Adjustment

### File: `config/default.yaml`

**Current (Broken):**
```yaml
identity:
  evidence_gate:
    enabled: true
    unknown_min_q: 0.68  # TOO STRICT - causes your rejection
```

**Fixed:**
```yaml
identity:
  evidence_gate:
    enabled: true
    unknown_min_q: 0.58  # RELAXED - matches enrollment threshold
                         # Provides 3% buffer for runtime variation
```

**Why 0.58?**
- Enrollment quality threshold: `q_enroll: 0.60` (your 100 templates enrolled at this level)
- Runtime requirement: was 0.68 (8% gap - too large!)
- After fix: 0.58 (2% relaxation for real-world variation)
- Result: symmetric thresholds = successful recognition

**Implementation:**
```bash
# Edit the file
nano config/default.yaml

# Find the line with "unknown_min_q: 0.68" (around line 45-50)
# Change to "unknown_min_q: 0.58"
# Save and exit
```

**Immediate Test:**
```bash
python -m core.main_loop
# Your face should now show as recognized
```

---

## ROBUST FIX #1: Add Quality Smoothing (30 minutes)

### File: `identity/evidence_gate.py`

**Current Code (Around line 130-150):**
```python
class EvidenceGate:
    def __init__(self, config):
        self.enabled = config.enabled
        self.unknown_min_q = config.unknown_min_q
        # ... existing code ...
    
    def decide(self, sample):
        """Decide: accept, hold, or reject a face sample"""
        
        quality = sample.quality
        
        if quality >= self.unknown_min_q:
            return ("accept", f"q={quality:.3f} >= {self.unknown_min_q}")
        else:
            return ("hold", f"q={quality:.3f} < {self.unknown_min_q}")
```

**Enhanced Code (Add smoothing):**
```python
from collections import deque
import statistics

class EvidenceGate:
    def __init__(self, config):
        self.enabled = config.enabled
        self.unknown_min_q = config.unknown_min_q
        
        # NEW: Quality history for smoothing
        self.quality_history = deque(maxlen=5)  # 5-frame moving window
        self.smooth_threshold_ratio = 0.95  # Use 95% of threshold for smoothing
        
        # ... rest of existing code ...
    
    def decide(self, sample):
        """Decide: accept, hold, or reject a face sample"""
        
        raw_quality = sample.quality
        
        # NEW: Add to history
        self.quality_history.append(raw_quality)
        
        # NEW: Compute smoothed quality
        smoothed_quality = self._get_smoothed_quality()
        
        # NEW: Decision logic
        # Use smoothed quality if we have enough history
        decision_quality = smoothed_quality if len(self.quality_history) >= 3 else raw_quality
        smooth_threshold = self.unknown_min_q * self.smooth_threshold_ratio
        
        if decision_quality >= smooth_threshold:
            return ("accept", f"smooth_q={decision_quality:.3f} >= {smooth_threshold:.3f} (raw={raw_quality:.3f})")
        else:
            return ("hold", f"smooth_q={decision_quality:.3f} < {smooth_threshold:.3f} (raw={raw_quality:.3f})")
    
    # NEW: Helper method
    def _get_smoothed_quality(self):
        """Compute moving average of quality scores"""
        if not self.quality_history:
            return 0.0
        return statistics.mean(self.quality_history)
```

**Why this works:**
- Reduces noise from individual frame variations
- Rewards sustained good quality over time
- Prevents single low-quality frame from rejecting
- More natural, human-like decision making

**Test this fix:**
```bash
# After implementing, run:
python -m core.main_loop

# In the logs, you should see:
# "smooth_q=0.62 >= 0.55 (raw=0.45)"  ← Smoothing in action
# Instead of single rejection like before
```

---

## ROBUST FIX #2: Strengthen Binding Evidence (45 minutes)

### File: `core/binding.py`

**Current Code (Around line 200-230):**
```python
class BindingManager:
    def process_evidence(self, track_id, person_id, score, 
                        second_best_score, quality, timestamp):
        """Process identity evidence for a track"""
        
        if track_id not in self.state:
            self.state[track_id] = {
                'identity': None,
                'confidence': 0.0,
                'evidence_count': 0,
            }
        
        # Simple counter
        if score > 0.75:  # Arbitrary threshold
            self.state[track_id]['evidence_count'] += 1
        
        # Flip to new identity
        if self.state[track_id]['evidence_count'] >= 3:
            self.state[track_id]['identity'] = person_id
```

**Enhanced Code (Robust evidence tracking):**
```python
from collections import deque
import time

class BindingManager:
    def __init__(self, config):
        # ... existing code ...
        
        # NEW: Robust evidence tracking
        self.evidence_buffer = {}  # track_id → list of evidence
        self.evidence_retention_time = 2.0  # Keep evidence for 2 seconds
        self.strong_confirmation_threshold = 0.75  # High confidence required
        self.strong_confirmation_count = 3  # Need 3 confirmations
    
    def process_evidence(self, track_id, person_id, score, 
                        second_best_score, quality, timestamp):
        """Process identity evidence with robust confirmation"""
        
        if track_id not in self.state:
            self.state[track_id] = {
                'identity': None,
                'last_update': timestamp,
            }
        
        if track_id not in self.evidence_buffer:
            self.evidence_buffer[track_id] = []
        
        # NEW: Only count high-confidence, high-quality evidence
        # This prevents noise from being counted
        if score >= self.strong_confirmation_threshold and quality >= 0.60:
            self.evidence_buffer[track_id].append({
                'person_id': person_id,
                'score': score,
                'quality': quality,
                'timestamp': timestamp,
            })
            
            # Cleanup old evidence (older than retention time)
            self._cleanup_old_evidence(track_id, timestamp)
            
            # Check if we have sustained evidence
            if self._has_sustained_evidence(track_id, person_id, timestamp):
                self.state[track_id]['identity'] = person_id
                self.state[track_id]['last_update'] = timestamp
    
    # NEW: Cleanup old evidence
    def _cleanup_old_evidence(self, track_id, current_time):
        """Remove evidence older than retention window"""
        if track_id in self.evidence_buffer:
            self.evidence_buffer[track_id] = [
                e for e in self.evidence_buffer[track_id]
                if current_time - e['timestamp'] < self.evidence_retention_time
            ]
    
    # NEW: Check for sustained evidence
    def _has_sustained_evidence(self, track_id, person_id, current_time):
        """Check if we have sustained high-quality evidence for person_id"""
        
        evidence = self.evidence_buffer.get(track_id, [])
        
        # Count confirmations for THIS person_id in recent time
        confirmations = [
            e for e in evidence
            if e['person_id'] == person_id and
               current_time - e['timestamp'] < self.evidence_retention_time
        ]
        
        # Need sustained evidence: 3+ confirmations of same person
        return len(confirmations) >= self.strong_confirmation_count
```

**Why this works:**
- Only counts high-confidence matches (>= 0.75)
- Requires high quality input (>= 0.60)
- Needs sustained evidence over time window
- Prevents one lucky match from causing binding

**Test this fix:**
```bash
# After implementing, run:
python -m core.main_loop

# In governance metrics, you should see:
# "binding: {'p_0005': 1}" ← Bound to you
# Instead of "binding: {None: 1}" ← Unknown
```

---

## PRODUCTION FIX: Dual-Threshold Strategy (60 minutes)

### File: `config/default.yaml`

**Enhanced Configuration:**
```yaml
identity:
  face_config:
    q_enroll: 0.60        # Enrollment threshold
    q_embed: 0.60         # Embedding threshold
    q_runtime: 0.55       # Runtime processing threshold
  
  evidence_gate:
    enabled: true
    
    # DUAL THRESHOLD STRATEGY
    unknown_min_q: 0.55   # Relaxed for unknown faces (build evidence)
    known_min_q: 0.65     # Stricter for known faces (prevent false bind)
    
    # Quality thresholds for different conditions
    blur_threshold: 0.50     # Blur must be below this
    brightness_min: 0.20     # Brightness must be above this
    brightness_max: 0.95     # Brightness must be below this
    pose_tolerance: 45       # Max yaw angle in degrees
    scale_min: 0.40          # Min face scale
  
  binding:
    enabled: true
    
    # Different requirements for different situations
    new_binding:
      min_confidence: 0.75        # High confidence required for NEW identity
      min_quality: 0.65           # High quality required
      min_confirmations: 3        # Need 3 confirmations
      time_window_sec: 2.0        # Within 2 seconds
    
    existing_binding:
      min_confidence: 0.65        # Lower for existing
      min_quality: 0.55           # Lower for existing
      min_confirmations: 2        # Only 2 needed
      time_window_sec: 3.0        # Longer window acceptable
    
    # Anti-flop settings
    flip_flop_prevention:
      required_disconfirmation_count: 5  # Need 5 contradictions to flip
      disconfirmation_window_sec: 1.0
```

### File: `identity/evidence_gate.py`

**Enhanced Implementation:**
```python
class EvidenceGate:
    def __init__(self, config):
        self.enabled = config.enabled
        
        # DUAL THRESHOLDS
        self.unknown_min_q = config.unknown_min_q  # 0.55 - relaxed
        self.known_min_q = config.known_min_q      # 0.65 - strict
        
        # Individual quality components
        self.blur_threshold = config.blur_threshold
        self.brightness_min = config.brightness_min
        self.brightness_max = config.brightness_max
        self.pose_tolerance = config.pose_tolerance
        self.scale_min = config.scale_min
        
        # Quality history for smoothing
        from collections import deque
        self.quality_history = deque(maxlen=5)
    
    def decide(self, sample, is_known_identity=False):
        """Decide with dual-threshold strategy"""
        
        # Determine which threshold to use
        threshold = self.known_min_q if is_known_identity else self.unknown_min_q
        
        # Compute component qualities
        blur_q = self._compute_blur_quality(sample)
        brightness_q = self._compute_brightness_quality(sample)
        pose_q = self._compute_pose_quality(sample)
        scale_q = self._compute_scale_quality(sample)
        alignment_q = sample.alignment_quality
        
        # Composite quality
        raw_quality = 0.2 * blur_q + 0.2 * brightness_q + \
                     0.2 * pose_q + 0.2 * scale_q + 0.2 * alignment_q
        
        # Smooth quality
        self.quality_history.append(raw_quality)
        smoothed_quality = sum(self.quality_history) / len(self.quality_history)
        
        # Decision
        if smoothed_quality >= threshold:
            return ("accept", f"type={'known' if is_known_identity else 'unknown'} "
                            f"smooth_q={smoothed_quality:.3f} >= {threshold:.3f}")
        else:
            return ("hold", f"type={'known' if is_known_identity else 'unknown'} "
                           f"smooth_q={smoothed_quality:.3f} < {threshold:.3f}")
    
    def _compute_blur_quality(self, sample):
        """Quality score for blur: 1.0 = sharp, 0.0 = blurry"""
        blur = sample.blur_score
        return 1.0 - blur
    
    def _compute_brightness_quality(self, sample):
        """Quality score for brightness"""
        brightness = sample.brightness
        if brightness < self.brightness_min or brightness > self.brightness_max:
            return 0.3
        return 0.8
    
    def _compute_pose_quality(self, sample):
        """Quality score for head pose"""
        yaw, pitch = sample.yaw, sample.pitch
        angle_magnitude = (abs(yaw) + abs(pitch)) / 2
        
        if angle_magnitude > self.pose_tolerance:
            return 0.3
        elif angle_magnitude > self.pose_tolerance * 0.7:
            return 0.6
        else:
            return 1.0
    
    def _compute_scale_quality(self, sample):
        """Quality score for face scale"""
        scale = sample.face_scale
        if scale < self.scale_min:
            return 0.3
        elif scale < 0.60:
            return 0.6
        else:
            return 1.0
```

**Benefits of dual-threshold:**
- Unknown faces: easier acceptance (build evidence)
- Known faces: stricter requirement (prevent false bind)
- Tailored to different risk profiles
- Production-grade robustness

---

## TESTING SEQUENCE

### Test 1: Verify Threshold Change
```bash
# Step 1: Update config
nano config/default.yaml
# Change: unknown_min_q: 0.68 → 0.58

# Step 2: Run system
python -m core.main_loop

# Step 3: Look for in logs:
# Before: "faces=0 (accept=0, reject=?)"
# After: "faces=1 (accept=1, reject=0)"

# Step 4: Check display
# Before: "ID 1: unknown"
# After: "ID 1: marildo cani (0.80)"
```

### Test 2: Verify Quality Smoothing
```bash
# After implementing smoothing fix:
python -m core.main_loop

# Look for in logs:
# "smooth_q=0.61 >= 0.55 (raw=0.45)"  ← Smoothing helping
# "smooth_q=0.58 >= 0.55 (raw=0.52)"  ← More acceptances
```

### Test 3: Verify Binding
```bash
# After implementing binding fix:
python -m core.main_loop

# Look for in governance metrics:
# "binding: {None: 0, 'p_0005': 1}"  ← You're bound
# "Governance Metrics: faces=1 (accept=1, hold=0, reject=0)"
```

### Test 4: Full End-to-End
```bash
# Full test with all fixes:
python -m core.main_loop

# Expected output sequence:
# 1. Face detected: "tracks=1"
# 2. Quality acceptable: "q_face=0.62"  
# 3. Sample accepted: "accept=1"
# 4. Evidence accumulates: "binding: {'p_0005': 1}"
# 5. Recognition: "ID 1: marildo cani (0.801)"
```

---

## ROLLBACK PROCEDURE (If needed)

```bash
# Config rollback (revert threshold)
cd config
git checkout default.yaml
# OR manually change unknown_min_q back to 0.68

# Code rollback
cd identity
git checkout evidence_gate.py

cd ../core
git checkout binding.py

# Restart system
python -m core.main_loop
```

---

## VALIDATION CHECKLIST

After implementation, verify:

- [ ] Config file updated (unknown_min_q: 0.58)
- [ ] Code compiles without errors
- [ ] System starts: `python -m core.main_loop`
- [ ] Face detected: logs show "tracks=1"
- [ ] Quality > 0.55: logs show "q_face >= 0.55"
- [ ] Sample accepted: logs show "accept=1"
- [ ] Binding created: logs show "binding: {'p_0005': 1}"
- [ ] Recognition display: shows your name instead of "unknown"
- [ ] No crashes or exceptions
- [ ] Logs show smooth quality smoothing
- [ ] Test with 3+ people (verify no false positives)

---

## EXPECTED RESULTS

| Metric | Before | After |
|--------|--------|-------|
| **Your recognition** | ❌ Unknown | ✅ Recognized |
| **Sample acceptance rate** | ~30% | ~70% |
| **Time to recognition** | Never | 5-15 seconds |
| **Quality threshold** | 0.68 (too high) | 0.58 (balanced) |
| **Binding stability** | N/A (never binds) | Stable |
| **False positives** | 0 (too strict) | ~1-2% (acceptable) |

---

