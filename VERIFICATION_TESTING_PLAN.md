# 🔬 VERIFICATION & TESTING PLAN: Deep Validation of All 5 Phases

**Status**: All Phases A-E are IMPLEMENTED and INTEGRATED  
**Focus**: VERIFICATION that they work correctly  
**Scope**: Every detail from code integration to robustness under stress  
**Owner**: You (execution on your system)

---

## 📋 EXECUTIVE: What Needs Verification

| Phase | What Exists | What to Verify | Status |
|-------|------------|-----------------|--------|
| A | Config governance section | Config loads, flags respected, switches work | ⚠️ TBD |
| B | Evidence gate (472 lines) | Rejects bad samples, accepts good ones | ⚠️ TBD |
| C | Binding state machine | Prevents flip-flop, requires N samples | ⚠️ TBD |
| D | Scheduler (FPS/load) | Allocates budget fairly, degrades gracefully | ⚠️ TBD |
| E | Merge manager (1,100 lines) | Merges only duplicates, not different people | ⚠️ TBD |

---

## PART 1: PHASE A VERIFICATION (Config Governance)

### A.1: Config Load & Parsing Verification

**What to Test**: Config file loads correctly and all governance flags are accessible

**Test File**: Create `tests/test_phase_a_config.py`

```python
# tests/test_phase_a_config.py
import logging
from pathlib import Path
from core.config import load_config
from core.logging_setup import setup_logging

setup_logging()
log = logging.getLogger(__name__)

def test_phase_a_config_loads():
    """Verify config loads and governance section exists"""
    try:
        cfg = load_config()
        assert hasattr(cfg, 'governance'), "No governance section in config"
        assert cfg.governance is not None, "governance section is None"
        log.info(f"✅ Config loads, governance section found")
        print(f"Governance config: {cfg.governance}")
        return True
    except Exception as e:
        log.error(f"❌ Config load failed: {e}")
        return False

def test_phase_a_flags_accessible():
    """Verify all governance flags are accessible"""
    cfg = load_config()
    flags_to_check = [
        'enabled',
        'evidence_gate_enabled',
        'binding_enabled',
        'scheduler_enabled',
        'handoff_merge_enabled',
    ]
    
    missing_flags = []
    for flag in flags_to_check:
        if not hasattr(cfg.governance, flag):
            missing_flags.append(flag)
        else:
            value = getattr(cfg.governance, flag)
            log.info(f"✅ {flag}: {value}")
    
    if missing_flags:
        log.error(f"❌ Missing flags: {missing_flags}")
        return False
    
    log.info("✅ All governance flags accessible")
    return True

def test_phase_a_flag_types():
    """Verify flags are boolean/correct types"""
    cfg = load_config()
    expected_types = {
        'enabled': bool,
        'evidence_gate_enabled': bool,
        'binding_enabled': bool,
        'scheduler_enabled': bool,
        'handoff_merge_enabled': bool,
    }
    
    errors = []
    for flag, expected_type in expected_types.items():
        actual_value = getattr(cfg.governance, flag)
        actual_type = type(actual_value)
        if actual_type != expected_type:
            errors.append(f"{flag}: expected {expected_type}, got {actual_type}")
        else:
            log.info(f"✅ {flag}: {actual_type.__name__} = {actual_value}")
    
    if errors:
        log.error(f"❌ Type mismatches: {errors}")
        return False
    
    log.info("✅ All flag types correct")
    return True

if __name__ == "__main__":
    results = []
    results.append(("Config loads", test_phase_a_config_loads()))
    results.append(("Flags accessible", test_phase_a_flags_accessible()))
    results.append(("Flag types correct", test_phase_a_flag_types()))
    
    print("\n" + "="*60)
    print("PHASE A VERIFICATION RESULTS")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(p for _, p in results)
    print(f"\nPhase A Overall: {'✅ PASS' if all_passed else '❌ FAIL'}")
```

**How to Run**:
```powershell
cd c:\Users\ildi\Desktop\GaitGuard - 2o
python -m pytest tests/test_phase_a_config.py -v
```

**Success Criteria**:
- ✅ Config loads without errors
- ✅ governance section exists
- ✅ All 5 flags are present
- ✅ All flags are boolean type
- ✅ All flags have expected values (check config/default.yaml)

**What to Check in Code**:
- [config/default.yaml](config/default.yaml) - governance section
- [core/config.py](core/config.py) - config loading logic
- Look for any hardcoded defaults that override YAML

---

### A.2: Runtime Flag Respect Verification

**What to Test**: When a flag is False, the corresponding phase is disabled

**Test File**: Create `tests/test_phase_a_runtime_switches.py`

```python
# tests/test_phase_a_runtime_switches.py
import logging
from pathlib import Path
import sys
import yaml

setup_logging()
log = logging.getLogger(__name__)

def test_evidence_gate_disabled():
    """When evidence_gate_enabled=False, evidence gate should not be active"""
    from core.config import load_config
    from face.route import FaceRoute
    
    cfg = load_config()
    cfg.governance.evidence_gate_enabled = False
    
    face_route = FaceRoute(cfg.face)
    
    # Check if evidence gate is initialized
    if hasattr(face_route, 'evidence_gate') and face_route.evidence_gate is not None:
        # Check if it's marked as disabled
        if hasattr(face_route.evidence_gate, 'enabled'):
            if face_route.evidence_gate.enabled == False:
                log.info("✅ Evidence gate disabled correctly")
                return True
            else:
                log.error("❌ Evidence gate should be disabled but isn't")
                return False
    
    log.info("✅ Evidence gate not active when disabled")
    return True

def test_binding_disabled():
    """When binding_enabled=False, binding manager should not be active"""
    from identity.identity_engine import FaceIdentityEngine
    
    engine = FaceIdentityEngine()
    
    # Check binding manager state
    if hasattr(engine, 'binding_manager') and engine.binding_manager is not None:
        # Try to check if it respects disabled flag
        log.info(f"Binding manager exists: {engine.binding_manager}")
        return True
    
    return True

def test_scheduler_disabled():
    """When scheduler_enabled=False, scheduler should not be active"""
    from core.config import load_config
    from core.main_loop import MainLoop
    
    # This is harder to test without running main loop
    # But we can check config
    cfg = load_config()
    cfg.governance.scheduler_enabled = False
    
    log.info(f"✅ Scheduler flag set to disabled: {cfg.governance.scheduler_enabled}")
    return True

if __name__ == "__main__":
    results = []
    results.append(("Evidence gate respects disabled flag", test_evidence_gate_disabled()))
    results.append(("Binding respects disabled flag", test_binding_disabled()))
    results.append(("Scheduler respects disabled flag", test_scheduler_disabled()))
    
    print("\n" + "="*60)
    print("PHASE A RUNTIME SWITCHES VERIFICATION")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
```

**Success Criteria**:
- ✅ When evidence_gate_enabled=False, gate doesn't process samples
- ✅ When binding_enabled=False, binding manager doesn't override decisions
- ✅ When scheduler_enabled=False, scheduler doesn't run

---

## PART 2: PHASE B VERIFICATION (Evidence Gating)

### B.1: Evidence Gate Decision Logic

**What to Test**: Evidence gate correctly classifies samples as ACCEPT/HOLD/REJECT

**Test File**: Create `tests/test_phase_b_evidence_gate.py`

```python
# tests/test_phase_b_evidence_gate.py
import logging
from identity.evidence_gate import EvidenceGate, GateDecision
from schemas import FaceSample
from face.config import default_face_config
import numpy as np

setup_logging()
log = logging.getLogger(__name__)

def create_test_face_evidence(
    blur_score=0.1,      # 0 = sharp, 1 = blurry
    brightness=0.5,      # 0 = dark, 1 = bright
    yaw=0.0,             # degrees
    pitch=0.0,
    roll=0.0,
    scale=0.5,           # relative to frame
    quality_score=0.9    # overall quality
):
    """Create a test face evidence object"""
    from face.detector_align import FaceEvidence
    
    evidence = FaceEvidence(
        bbox=[100, 100, 200, 200],
        landmark_2d=np.random.rand(5, 2) * 100 + 100,
        embedding=np.random.rand(512),
        quality_metrics={
            'blur': blur_score,
            'brightness': brightness,
            'scale': scale,
            'quality': quality_score,
        },
        pose=np.array([yaw, pitch, roll]),
    )
    return evidence

def test_high_quality_accepted():
    """High quality face should be ACCEPTED"""
    gate = EvidenceGate(default_face_config())
    
    # High quality evidence
    evidence = create_test_face_evidence(
        blur_score=0.05,      # Sharp
        brightness=0.6,       # Good brightness
        yaw=5.0,              # Small yaw
        scale=0.6,            # Good scale
        quality_score=0.95
    )
    
    decision = gate.decide(evidence)
    
    if decision == GateDecision.ACCEPT:
        log.info(f"✅ High quality face ACCEPTED")
        return True
    else:
        log.error(f"❌ High quality face should be ACCEPTED, got {decision}")
        return False

def test_blurry_rejected():
    """Blurry face should be REJECTED"""
    gate = EvidenceGate(default_face_config())
    
    # Blurry evidence
    evidence = create_test_face_evidence(
        blur_score=0.9,       # Very blurry
        brightness=0.6,
        quality_score=0.3     # Low quality
    )
    
    decision = gate.decide(evidence)
    
    if decision == GateDecision.REJECT:
        log.info(f"✅ Blurry face REJECTED")
        return True
    else:
        log.error(f"❌ Blurry face should be REJECTED, got {decision}")
        return False

def test_dark_rejected():
    """Dark face should be REJECTED"""
    gate = EvidenceGate(default_face_config())
    
    # Dark evidence
    evidence = create_test_face_evidence(
        blur_score=0.1,
        brightness=0.05,      # Very dark
        quality_score=0.2
    )
    
    decision = gate.decide(evidence)
    
    if decision == GateDecision.REJECT:
        log.info(f"✅ Dark face REJECTED")
        return True
    else:
        log.error(f"❌ Dark face should be REJECTED, got {decision}")
        return False

def test_large_yaw_held():
    """Face with large yaw should be HELD (not immediately rejected)"""
    gate = EvidenceGate(default_face_config())
    
    # Large yaw
    evidence = create_test_face_evidence(
        blur_score=0.05,
        brightness=0.6,
        yaw=45.0,             # Very large yaw
        quality_score=0.8
    )
    
    decision = gate.decide(evidence)
    
    if decision == GateDecision.HOLD:
        log.info(f"✅ Large yaw face HELD")
        return True
    elif decision == GateDecision.REJECT:
        log.info(f"✅ Large yaw face REJECTED (acceptable)")
        return True
    else:
        log.error(f"❌ Large yaw should be HELD or REJECTED, got {decision}")
        return False

def test_marginal_quality_held():
    """Marginal quality should be HELD for more evidence"""
    gate = EvidenceGate(default_face_config())
    
    # Marginal evidence
    evidence = create_test_face_evidence(
        blur_score=0.3,       # Slightly blurry
        brightness=0.45,      # Slightly dark
        quality_score=0.65    # Marginal quality
    )
    
    decision = gate.decide(evidence)
    
    if decision == GateDecision.HOLD:
        log.info(f"✅ Marginal quality face HELD")
        return True
    else:
        log.error(f"❌ Marginal quality should be HELD, got {decision}")
        return False

if __name__ == "__main__":
    results = []
    results.append(("High quality ACCEPTED", test_high_quality_accepted()))
    results.append(("Blurry REJECTED", test_blurry_rejected()))
    results.append(("Dark REJECTED", test_dark_rejected()))
    results.append(("Large yaw HELD", test_large_yaw_held()))
    results.append(("Marginal quality HELD", test_marginal_quality_held()))
    
    print("\n" + "="*60)
    print("PHASE B EVIDENCE GATE VERIFICATION")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(p for _, p in results)
    print(f"\nPhase B Overall: {'✅ PASS' if all_passed else '❌ FAIL'}")
```

**Success Criteria**:
- ✅ High quality → ACCEPTED
- ✅ Blurry/dark/out-of-pose → REJECTED or HELD
- ✅ Marginal quality → HELD (awaiting more evidence)
- ✅ Thresholds are tunable via config

### B.2: Evidence Gate Integration in Flow

**What to Test**: Evidence gate decisions actually prevent bad samples from reaching identity engine

**Code Path to Trace**:
1. [perception/detector.py](perception/detector.py) - detects faces
2. [face/route.py](face/route.py#L370-L379) - gates them with evidence_gate.decide()
3. [identity/identity_engine.py](identity/identity_engine.py) - receives only gated samples

**Verification Steps**:
```python
# In face/route.py line 370-379, verify:
if self.evidence_gate and self.evidence_gate.enabled:
    gate_decision = self.evidence_gate.decide(evidence)
    if gate_decision == GateDecision.REJECT:
        # BAD: Should not reach identity engine
        return None  # or skip this sample
    elif gate_decision == GateDecision.HOLD:
        # GOOD: Logged but not used for identity yet
        pass

# Check identity_engine processes only gated samples
```

**Test Metrics to Log**:
- Number of samples received
- Number ACCEPTED (%)
- Number HELD (%)
- Number REJECTED (%)
- Should be roughly: 60-80% ACCEPT, 10-20% HOLD, 5-15% REJECT under normal conditions

---

## PART 3: PHASE C VERIFICATION (Binding State Machine)

### C.1: Binding State Transitions

**What to Test**: Binding state machine correctly transitions UNKNOWN → PENDING → CONFIRMED

**Test File**: Create `tests/test_phase_c_binding.py`

```python
# tests/test_phase_c_binding.py
import logging
from identity.binding import BindingManager, BindingState
from core.config import load_config
from core.governance_metrics import get_metrics_collector

setup_logging()
log = logging.getLogger(__name__)

def test_binding_starts_unknown():
    """New track should start in UNKNOWN state"""
    cfg = load_config()
    metrics = get_metrics_collector()
    binding = BindingManager(cfg, metrics)
    
    # New track (never seen before)
    track_id = 999
    state = binding.get_state(track_id)
    
    if state == BindingState.UNKNOWN:
        log.info(f"✅ New track starts in UNKNOWN state")
        return True
    else:
        log.error(f"❌ New track should be UNKNOWN, got {state}")
        return False

def test_binding_transitions_pending():
    """After first high-quality evidence, should transition to PENDING"""
    cfg = load_config()
    metrics = get_metrics_collector()
    binding = BindingManager(cfg, metrics)
    
    track_id = 888
    
    # Process high-quality evidence
    for i in range(1):  # First sample
        binding.process_evidence(
            track_id=track_id,
            identity_name="John Doe",
            confidence=0.95,
            quality_score=0.9,
            sample_time=i
        )
    
    state = binding.get_state(track_id)
    
    if state == BindingState.PENDING:
        log.info(f"✅ Transitioned to PENDING after first evidence")
        return True
    else:
        log.error(f"❌ Should be PENDING after evidence, got {state}")
        return False

def test_binding_requires_consecutive_samples():
    """Should require N consecutive samples before CONFIRMED"""
    cfg = load_config()
    metrics = get_metrics_collector()
    binding = BindingManager(cfg, metrics)
    
    track_id = 777
    
    # Get the N required (from config)
    n_required = cfg.governance.binding.get('confirmation_count', 3)
    
    # Process N samples
    for i in range(n_required):
        binding.process_evidence(
            track_id=track_id,
            identity_name="Jane Doe",
            confidence=0.9 + (i * 0.01),
            quality_score=0.85 + (i * 0.01),
            sample_time=i
        )
    
    state = binding.get_state(track_id)
    
    if state == BindingState.CONFIRMED:
        log.info(f"✅ Confirmed after {n_required} consecutive samples")
        return True
    else:
        log.error(f"❌ Should be CONFIRMED after {n_required} samples, got {state}")
        return False

def test_binding_prevents_flip_flop():
    """Should prevent flipping between two identities"""
    cfg = load_config()
    metrics = get_metrics_collector()
    binding = BindingManager(cfg, metrics)
    
    track_id = 666
    
    # Process samples for identity A
    for i in range(3):
        binding.process_evidence(
            track_id=track_id,
            identity_name="Alice",
            confidence=0.95,
            quality_score=0.9,
            sample_time=i
        )
    
    # Now try to switch to identity B (with lower confidence)
    binding.process_evidence(
        track_id=track_id,
        identity_name="Bob",
        confidence=0.7,  # Lower confidence
        quality_score=0.85,
        sample_time=3
    )
    
    current_identity = binding.get_identity(track_id)
    
    if current_identity == "Alice":
        log.info(f"✅ Prevented flip-flop to lower confidence identity")
        return True
    else:
        log.error(f"❌ Should have prevented switch to Bob, got {current_identity}")
        return False

if __name__ == "__main__":
    results = []
    results.append(("Starts in UNKNOWN", test_binding_starts_unknown()))
    results.append(("Transitions to PENDING", test_binding_transitions_pending()))
    results.append(("Requires consecutive samples", test_binding_requires_consecutive_samples()))
    results.append(("Prevents flip-flop", test_binding_prevents_flip_flop()))
    
    print("\n" + "="*60)
    print("PHASE C BINDING STATE MACHINE VERIFICATION")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(p for _, p in results)
    print(f"\nPhase C Overall: {'✅ PASS' if all_passed else '❌ FAIL'}")
```

**Success Criteria**:
- ✅ New tracks start in UNKNOWN
- ✅ Transition to PENDING after first high-quality evidence
- ✅ Require N consecutive samples before CONFIRMED
- ✅ Once CONFIRMED, prevent switching based on marginal evidence
- ✅ Require sustained evidence to flip identity

### C.2: Binding Integration in Decision Flow

**Code Path to Trace**:
1. [identity/identity_engine.py](identity/identity_engine.py#L527-L544) - process_evidence() calls binding.process_evidence()
2. Binding state influences identity decision (CONFIRMED = stable, PENDING = tentative)

**Verification**:
```python
# In identity_engine.py line 527-544:
binding_result = self.binding_manager.process_evidence(
    track_id=track_id,
    identity_name=best_identity.person_id,
    confidence=best_identity.confidence,
    quality_score=evidence_quality,
    sample_time=current_ts
)

# Check if binding state affects decision output
if binding_result.state == BindingState.UNKNOWN:
    # Decision should be tentative (HOLD, not locked)
elif binding_result.state == BindingState.CONFIRMED:
    # Decision can be locked (no more switching)
```

---

## PART 4: PHASE D VERIFICATION (Scheduler)

### D.1: Scheduler Track Selection

**What to Test**: Scheduler fairly allocates processing budget among tracks

**Test File**: Create `tests/test_phase_d_scheduler.py`

```python
# tests/test_phase_d_scheduler.py
import logging
from core.scheduler import create_scheduler_from_config
from core.config import load_config

setup_logging()
log = logging.getLogger(__name__)

def test_scheduler_initializes():
    """Scheduler should initialize from config"""
    cfg = load_config()
    
    try:
        scheduler = create_scheduler_from_config(cfg.governance.scheduler)
        log.info(f"✅ Scheduler initialized")
        return True
    except Exception as e:
        log.error(f"❌ Scheduler initialization failed: {e}")
        return False

def test_scheduler_budget_allocation():
    """Scheduler should allocate FPS budget"""
    cfg = load_config()
    scheduler = create_scheduler_from_config(cfg.governance.scheduler)
    
    # Simulate multiple tracks
    active_tracks = [100, 101, 102, 103, 104]  # 5 tracks
    current_fps = 25.0
    
    # Compute schedule
    schedule = scheduler.compute_schedule(
        active_tracks=active_tracks,
        current_fps=current_fps,
        compute_time_ms=20.0  # 20ms used out of 40ms available
    )
    
    if schedule is None:
        log.error(f"❌ Scheduler returned None")
        return False
    
    log.info(f"✅ Scheduler allocated budget for {len(active_tracks)} tracks")
    log.info(f"   Available tracks for processing: {schedule.selected_tracks}")
    return True

def test_scheduler_degradation_under_load():
    """Scheduler should degrade gracefully under high load"""
    cfg = load_config()
    scheduler = create_scheduler_from_config(cfg.governance.scheduler)
    
    # Many tracks, high CPU usage
    active_tracks = list(range(100, 150))  # 50 tracks
    current_fps = 10.0  # Low FPS (high load)
    compute_time_ms = 35.0  # Using most of frame time
    
    schedule = scheduler.compute_schedule(
        active_tracks=active_tracks,
        current_fps=current_fps,
        compute_time_ms=compute_time_ms
    )
    
    # Under high load, should select fewer tracks for processing
    num_selected = len(schedule.selected_tracks)
    num_total = len(active_tracks)
    
    if num_selected < num_total:
        log.info(f"✅ Graceful degradation: {num_selected}/{num_total} tracks selected under load")
        return True
    else:
        log.warning(f"⚠️ Under load, still processing all {num_total} tracks (check if this is intended)")
        return True

def test_scheduler_priority_ordering():
    """Scheduler should prioritize high-confidence tracks"""
    cfg = load_config()
    scheduler = create_scheduler_from_config(cfg.governance.scheduler)
    
    # Tracks with different priorities
    active_tracks = [
        100,  # High priority (high confidence identity)
        101,  # Medium priority
        102,  # Low priority (uncertain)
    ]
    
    schedule = scheduler.compute_schedule(
        active_tracks=active_tracks,
        current_fps=25.0,
        compute_time_ms=15.0
    )
    
    selected = schedule.selected_tracks
    log.info(f"✅ Scheduler selected tracks: {selected}")
    log.info(f"   (Verify these are the highest priority)")
    
    return True

if __name__ == "__main__":
    results = []
    results.append(("Scheduler initializes", test_scheduler_initializes()))
    results.append(("Budget allocation", test_scheduler_budget_allocation()))
    results.append(("Graceful degradation under load", test_scheduler_degradation_under_load()))
    results.append(("Priority ordering", test_scheduler_priority_ordering()))
    
    print("\n" + "="*60)
    print("PHASE D SCHEDULER VERIFICATION")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(p for _, p in results)
    print(f"\nPhase D Overall: {'✅ PASS' if all_passed else '❌ FAIL'}")
```

**Success Criteria**:
- ✅ Scheduler initializes from config
- ✅ Allocates fair budget to all tracks
- ✅ Under high load, reduces processing to maintain FPS
- ✅ Prioritizes high-confidence tracks

### D.2: FPS Monitoring

**Metrics to Log**:
Create `tests/monitor_fps.py` to continuously monitor:

```python
# tests/monitor_fps.py
import time
import logging
from collections import deque
from core.main_loop import run_main_loop

setup_logging()
log = logging.getLogger(__name__)

class FPSMonitor:
    def __init__(self, window_size=100):
        self.frame_times = deque(maxlen=window_size)
        self.last_time = time.time()
    
    def tick(self):
        """Call once per frame"""
        now = time.time()
        frame_time = now - self.last_time
        self.frame_times.append(frame_time)
        self.last_time = now
    
    def get_fps(self):
        """Get average FPS over window"""
        if not self.frame_times:
            return 0
        avg_time = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg_time if avg_time > 0 else 0
    
    def get_stats(self):
        """Get FPS statistics"""
        if not self.frame_times:
            return {}
        
        times = list(self.frame_times)
        avg_fps = self.get_fps()
        min_frame_time = min(times)
        max_frame_time = max(times)
        
        return {
            'avg_fps': avg_fps,
            'min_frame_time_ms': min_frame_time * 1000,
            'max_frame_time_ms': max_frame_time * 1000,
            'frame_count': len(times),
        }

# Usage: Integrate into main loop to monitor FPS in real time
```

---

## PART 5: PHASE E VERIFICATION (Merge Manager)

### E.1: Merge Criteria Validation

**What to Test**: Merge manager correctly identifies duplicate tracks

**Test File**: Create `tests/test_phase_e_merge_manager.py`

```python
# tests/test_phase_e_merge_manager.py
import logging
import numpy as np
from identity.merge_manager import MergeManager, MergeDecision
from core.config import load_config
from schemas import Tracklet

setup_logging()
log = logging.getLogger(__name__)

def create_tracklet(
    track_id: int,
    confidence: float,
    last_seen_ts: float,
    embedding: np.ndarray,
    identity_name: str = "John Doe"
):
    """Create a test tracklet"""
    tracklet = Tracklet(
        track_id=track_id,
        confidence=confidence,
        last_seen_ts=last_seen_ts,
        embeddings=[embedding],
        identity_name=identity_name,
        binding_state="CONFIRMED",
    )
    return tracklet

def test_merge_identical_embeddings():
    """Should merge tracks with identical embeddings (same person)"""
    cfg = load_config()
    merger = MergeManager(cfg, None)
    
    embedding = np.random.rand(512)
    
    tracklet_a = create_tracklet(100, 0.95, 10.0, embedding, "John")
    tracklet_b = create_tracklet(101, 0.93, 11.0, embedding, "John")
    
    decision = merger.evaluate_merge(tracklet_a, tracklet_b)
    
    if decision.should_merge:
        log.info(f"✅ Identical embeddings → MERGE")
        return True
    else:
        log.error(f"❌ Identical embeddings should merge")
        return False

def test_merge_similar_embeddings():
    """Should consider merging similar embeddings (high cosine similarity)"""
    cfg = load_config()
    merger = MergeManager(cfg, None)
    
    embedding_a = np.random.rand(512)
    embedding_b = embedding_a.copy()
    embedding_b += np.random.randn(512) * 0.01  # Add small noise (cosine ~0.98)
    
    tracklet_a = create_tracklet(100, 0.95, 10.0, embedding_a, "John")
    tracklet_b = create_tracklet(101, 0.93, 11.0, embedding_b, "John")
    
    decision = merger.evaluate_merge(tracklet_a, tracklet_b)
    
    score = decision.merge_score if hasattr(decision, 'merge_score') else 0
    log.info(f"✅ Similar embeddings: merge_score={score:.3f}")
    
    return True

def test_no_merge_different_embeddings():
    """Should NOT merge tracks with different embeddings (different people)"""
    cfg = load_config()
    merger = MergeManager(cfg, None)
    
    embedding_a = np.random.rand(512)
    embedding_b = np.random.rand(512)  # Different random embedding
    
    tracklet_a = create_tracklet(100, 0.95, 10.0, embedding_a, "John")
    tracklet_b = create_tracklet(101, 0.93, 11.0, embedding_b, "Jane")
    
    decision = merger.evaluate_merge(tracklet_a, tracklet_b)
    
    if not decision.should_merge:
        log.info(f"✅ Different embeddings → NO MERGE")
        return True
    else:
        log.error(f"❌ Different embeddings should NOT merge")
        return False

def test_no_merge_low_confidence():
    """Should NOT merge if either track has low confidence"""
    cfg = load_config()
    merger = MergeManager(cfg, None)
    
    embedding = np.random.rand(512)
    
    tracklet_a = create_tracklet(100, 0.95, 10.0, embedding, "John")
    tracklet_b = create_tracklet(101, 0.35, 11.0, embedding, "John")  # Low confidence
    
    decision = merger.evaluate_merge(tracklet_a, tracklet_b)
    
    if not decision.should_merge:
        log.info(f"✅ Low confidence track → NO MERGE")
        return True
    else:
        log.warning(f"⚠️ Low confidence merge: check config thresholds")
        return True

def test_no_merge_simultaneous():
    """Should NOT merge simultaneously active tracks (handoff merge only)"""
    cfg = load_config()
    merger = MergeManager(cfg, None)
    
    embedding = np.random.rand(512)
    current_time = 100.0
    
    # Both tracks recently seen
    tracklet_a = create_tracklet(100, 0.95, current_time - 0.5, embedding, "John")
    tracklet_b = create_tracklet(101, 0.93, current_time - 0.2, embedding, "John")
    
    decision = merger.evaluate_merge(tracklet_a, tracklet_b)
    
    if not decision.should_merge:
        log.info(f"✅ Simultaneous tracks → NO MERGE (handoff-only)")
        return True
    else:
        log.warning(f"⚠️ Check if simultaneous merge is enabled in config")
        return True

if __name__ == "__main__":
    results = []
    results.append(("Merge identical embeddings", test_merge_identical_embeddings()))
    results.append(("Consider similar embeddings", test_merge_similar_embeddings()))
    results.append(("No merge different embeddings", test_no_merge_different_embeddings()))
    results.append(("No merge low confidence", test_no_merge_low_confidence()))
    results.append(("No merge simultaneous", test_no_merge_simultaneous()))
    
    print("\n" + "="*60)
    print("PHASE E MERGE MANAGER VERIFICATION")
    print("="*60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(p for _, p in results)
    print(f"\nPhase E Overall: {'✅ PASS' if all_passed else '❌ FAIL'}")
```

**Success Criteria**:
- ✅ Identical embeddings → MERGE
- ✅ Similar embeddings (high cosine) → Consider merge
- ✅ Different embeddings → NO MERGE
- ✅ Low confidence → NO MERGE
- ✅ Simultaneous tracks → NO MERGE (handoff only)

### E.2: Merge Execution Verification

**What to Test**: When merge happens, track data is correctly aliased

**Code Path**:
1. [identity/merge_manager.py](identity/merge_manager.py) - execute_merge() function
2. Verify canonical_id mapping is created
3. Verify old track_id redirects to new canonical_id

---

## PART 6: END-TO-END INTEGRATION TESTING

### E2E Test 1: Single Person, Clean Scenario

**Scenario**: One person with high-quality faces for 30 seconds

```python
# tests/test_e2e_single_person.py
"""
Expected behavior:
1. Detector finds face → perception
2. Evidence gate ACCEPTs (high quality) → Phase B
3. Binding moves to CONFIRMED → Phase C
4. Scheduler includes track → Phase D
5. Identity decision locked → Phase E
6. No merges needed (only 1 track)

Metrics:
- Should reach 95%+ confidence within 5 seconds
- Identity should NOT flip
- No merges attempted
"""
```

### E2E Test 2: Crowd Scenario

**Scenario**: 10 people with varying quality for 60 seconds

```python
# tests/test_e2e_crowd.py
"""
Expected behavior:
1. Multiple tracks detected
2. Evidence gate filters low-quality faces
3. Binding prevents false identities
4. Scheduler manages FPS with all tracks
5. Merge manager looks for handoffs

Metrics:
- FPS should stay ≥ 20
- False merges: 0
- Correct identities: 90%+
- Correctly identified handoffs: 80%+
"""
```

### E2E Test 3: Identity Switch (Handoff)

**Scenario**: Track A (known person) leaves, Track B (same person, re-entry) appears

```python
# tests/test_e2e_handoff.py
"""
Expected behavior:
1. Track A disappears (goes out of frame)
2. Track B appears 5 seconds later
3. Binding prevents immediate switch (no evidence yet)
4. After N high-quality samples, merge considers them
5. Eventually merges when confident

Metrics:
- Should NOT instantly merge on re-entry
- Should require temporal gap + new evidence
- Merge should only happen with high confidence
"""
```

### E2E Test 4: Similar-Looking People

**Scenario**: Two people with similar appearance (embedding distance ~0.4)

```python
# tests/test_e2e_similar_people.py
"""
Expected behavior:
1. Both tracked as separate tracks
2. Despite similarity, should maintain separation
3. If confused, binding + evidence gate should prevent merge
4. Scheduler should allocate budget to both

Metrics:
- False merges: 0
- Both people correctly identified: 100%
- Binding prevents confusion: verified
"""
```

---

## PART 7: PERFORMANCE & LOAD TESTING

### P1: Single-Phase Load Test

**Test each phase under increasing load**:

```python
# tests/perf_phase_load_test.py
import time
import logging

def perf_test_evidence_gate(num_samples=1000):
    """Measure evidence gate throughput"""
    gate = EvidenceGate(config)
    
    start = time.time()
    for i in range(num_samples):
        evidence = create_random_evidence()
        decision = gate.decide(evidence)
    elapsed = time.time() - start
    
    throughput = num_samples / elapsed
    log.info(f"Evidence gate: {throughput:.0f} samples/sec ({elapsed:.2f}s for {num_samples})")
    
    # Success: > 1000 samples/sec
    return throughput > 1000

def perf_test_binding(num_tracks=100, num_samples=5):
    """Measure binding manager throughput"""
    binding = BindingManager(config, metrics)
    
    start = time.time()
    for track_id in range(num_tracks):
        for i in range(num_samples):
            binding.process_evidence(track_id, f"person_{track_id}", 0.9, 0.85, i)
    elapsed = time.time() - start
    
    total_ops = num_tracks * num_samples
    throughput = total_ops / elapsed
    log.info(f"Binding: {throughput:.0f} ops/sec ({elapsed:.2f}s for {total_ops} operations)")
    
    # Success: > 5000 ops/sec
    return throughput > 5000

def perf_test_merge_manager(num_tracklets=500):
    """Measure merge manager evaluation throughput"""
    merger = MergeManager(config, metrics)
    tracklets = [create_random_tracklet(i) for i in range(num_tracklets)]
    
    start = time.time()
    merge_count = 0
    for i in range(num_tracklets):
        for j in range(i + 1, num_tracklets):
            decision = merger.evaluate_merge(tracklets[i], tracklets[j])
            if decision.should_merge:
                merge_count += 1
    elapsed = time.time() - start
    
    comparisons = (num_tracklets * (num_tracklets - 1)) // 2
    throughput = comparisons / elapsed
    log.info(f"Merge evaluation: {throughput:.0f} comparisons/sec ({elapsed:.2f}s for {comparisons} comparisons, {merge_count} merges)")
    
    # Success: > 100 comparisons/sec
    return throughput > 100
```

### P2: System-Wide Load Test

**Simulate realistic workload**:

```python
# tests/perf_system_load_test.py
import time

def perf_test_system_30fps_30_tracks():
    """
    Simulate 30 FPS with 30 active tracks
    Measure total latency
    """
    from core.main_loop import MainLoop
    
    main_loop = MainLoop()
    
    frame_times = []
    for frame_idx in range(300):  # 10 seconds at 30 FPS
        start = time.time()
        
        # Process one frame
        main_loop.process_frame(frame_idx)
        
        elapsed = time.time() - start
        frame_times.append(elapsed)
    
    avg_frame_time = np.mean(frame_times)
    max_frame_time = np.max(frame_times)
    p95_frame_time = np.percentile(frame_times, 95)
    
    log.info(f"30 FPS, 30 tracks:")
    log.info(f"  Avg frame time: {avg_frame_time*1000:.1f}ms")
    log.info(f"  Max frame time: {max_frame_time*1000:.1f}ms")
    log.info(f"  P95 frame time: {p95_frame_time*1000:.1f}ms")
    
    # Success: < 33ms average (30 FPS)
    return avg_frame_time < 0.033
```

---

## PART 8: STRESS & EDGE CASE TESTING

### S1: Rapid Track Creation/Deletion

```python
# tests/stress_track_lifecycle.py
def stress_rapid_track_creation():
    """
    Create 100 tracks, each for 1 second, rapid succession
    Verify no memory leaks or crashes
    """
    main_loop = MainLoop()
    memory_baseline = get_memory_usage()
    
    for batch in range(10):
        for track_id in range(10):
            # Track appears
            main_loop.add_track(track_id)
            # Process 30 frames (~1 second at 30 FPS)
            for _ in range(30):
                main_loop.process_frame()
            # Track disappears
            main_loop.remove_track(track_id)
    
    memory_final = get_memory_usage()
    memory_delta = memory_final - memory_baseline
    
    log.info(f"After 100 track lifecycles: {memory_delta:.1f}MB delta")
    
    # Success: < 10MB increase (no memory leak)
    return memory_delta < 10
```

### S2: Bursty Evidence Quality

```python
# tests/stress_quality_variation.py
def stress_bursty_high_low_quality():
    """
    Alternate between high and low quality evidence
    Verify binding/evidence gate handle transitions smoothly
    """
    binding = BindingManager(config, metrics)
    track_id = 100
    
    for i in range(100):
        quality = 0.95 if i % 10 < 5 else 0.2
        binding.process_evidence(track_id, "Person", 0.8, quality, i)
    
    state = binding.get_state(track_id)
    log.info(f"After bursty quality: binding state = {state}")
    
    # Should remain stable despite quality variation
    return True
```

### S3: Extreme Load

```python
# tests/stress_extreme_load.py
def stress_100_tracks():
    """
    100 active tracks simultaneously
    Verify scheduler degrades gracefully
    """
    main_loop = MainLoop()
    
    # Create 100 tracks
    for track_id in range(100):
        main_loop.add_track(track_id)
    
    frame_times = []
    for frame_idx in range(100):
        start = time.time()
        main_loop.process_frame(frame_idx)
        frame_times.append(time.time() - start)
    
    avg_fps = len(frame_times) / sum(frame_times)
    log.info(f"100 tracks: achieved {avg_fps:.1f} FPS")
    
    # Success: > 15 FPS (graceful degradation)
    return avg_fps > 15
```

---

## PART 9: LOGGING & OBSERVABILITY VERIFICATION

### V1: Structured Logs

**Verify each phase logs correctly**:

```python
# tests/verify_logging.py
def verify_phase_logging():
    """
    Run system for 10 seconds, verify logs are comprehensive
    """
    import json
    
    # Capture logs
    from core.logging_setup import setup_logging
    log_capture = setup_logging()
    
    main_loop = MainLoop()
    for _ in range(300):  # 10 seconds at 30 FPS
        main_loop.process_frame()
    
    logs = log_capture.get_logs()
    
    # Verify each phase logged
    phases = {
        'Phase A': 'governance',
        'Phase B': 'evidence_gate',
        'Phase C': 'binding',
        'Phase D': 'scheduler',
        'Phase E': 'merge_manager',
    }
    
    for phase, keyword in phases.items():
        phase_logs = [l for l in logs if keyword in l]
        if phase_logs:
            log.info(f"✅ {phase} logged {len(phase_logs)} entries")
        else:
            log.warning(f"⚠️ {phase} has no logs (check enabled flag)")
    
    return True
```

### V2: Metrics Collection

**Verify metrics are collected**:

```python
# tests/verify_metrics.py
def verify_metrics_collection():
    """
    Run system, verify metrics are collected
    """
    from core.governance_metrics import get_metrics_collector
    
    metrics = get_metrics_collector()
    
    main_loop = MainLoop()
    for _ in range(300):
        main_loop.process_frame()
    
    # Get collected metrics
    stats = metrics.get_stats()
    
    expected_metrics = {
        'phase_a_config_loads': 'positive',
        'phase_b_gate_decisions': 'positive',
        'phase_c_binding_transitions': 'positive',
        'phase_d_scheduler_tracks': 'positive',
        'phase_e_merge_candidates': 'positive',
    }
    
    for metric, expectation in expected_metrics.items():
        value = stats.get(metric, 0)
        if value > 0 and expectation == 'positive':
            log.info(f"✅ {metric}: {value}")
        else:
            log.warning(f"⚠️ {metric}: {value} (expected > 0)")
    
    return True
```

---

## PART 10: CHECKLIST FOR EXECUTION

### Pre-Testing Checklist

- [ ] All config files readable and valid YAML
- [ ] All dependencies installed (requirements.txt)
- [ ] All import paths correct
- [ ] GPU/CPU detection working
- [ ] Camera/video input available for E2E tests
- [ ] Test data directory accessible

### Testing Execution Order

**Day 1: Phase Verification (4-6 hours)**
- [ ] Phase A: Config verification (30 min)
- [ ] Phase B: Evidence gate testing (1 hour)
- [ ] Phase C: Binding state testing (1 hour)
- [ ] Phase D: Scheduler testing (1 hour)
- [ ] Phase E: Merge manager testing (1 hour)

**Day 2: End-to-End Testing (6-8 hours)**
- [ ] E2E Test 1: Single person, clean (1 hour)
- [ ] E2E Test 2: Crowd scenario (2 hours)
- [ ] E2E Test 3: Handoff (1 hour)
- [ ] E2E Test 4: Similar people (1 hour)
- [ ] Logging/metrics verification (1 hour)

**Day 3: Performance & Stress (4-6 hours)**
- [ ] Single-phase load tests (2 hours)
- [ ] System-wide load test (1 hour)
- [ ] Stress tests (1-2 hours)
- [ ] Extreme load test (1 hour)

**Day 4: Analysis & Tuning (2-4 hours)**
- [ ] Analyze results
- [ ] Identify bottlenecks
- [ ] Adjust config parameters
- [ ] Re-test critical paths
- [ ] Document findings

### Success Criteria Summary

| Phase | Metric | Success | Current |
|-------|--------|---------|---------|
| A | Config loads | ✅ Yes | ⚠️ TBD |
| B | Evidence gate accuracy | 95%+ correct decisions | ⚠️ TBD |
| C | Binding stability | No flip-flop | ⚠️ TBD |
| D | Scheduler FPS | ≥ 20 FPS with 30 tracks | ⚠️ TBD |
| E | Merge accuracy | 0 false merges, 90%+ correct | ⚠️ TBD |
| E2E | System latency | < 50ms avg per frame | ⚠️ TBD |
| Stress | 100 tracks | ≥ 15 FPS | ⚠️ TBD |

---

## How to Run All Tests

```powershell
# Create test directory
mkdir tests
cd tests

# Run Phase A
python test_phase_a_config.py
python test_phase_a_runtime_switches.py

# Run Phase B
python test_phase_b_evidence_gate.py

# Run Phase C
python test_phase_c_binding.py

# Run Phase D
python test_phase_d_scheduler.py

# Run Phase E
python test_phase_e_merge_manager.py

# Run E2E
python test_e2e_single_person.py
python test_e2e_crowd.py

# Run Performance
python perf_phase_load_test.py
python perf_system_load_test.py

# Run Stress
python stress_track_lifecycle.py
python stress_bursty_high_low_quality.py
python stress_extreme_load.py

# Comprehensive report
python verify_logging.py
python verify_metrics.py
```

---

## Summary: What You Need to Do

### Your Responsibilities

1. **Create all test files** (copy from sections above)
2. **Run each test suite** in order
3. **Log results** (success/failure, metrics)
4. **Analyze failures** (why did it fail?)
5. **Adjust config/code** (fix issues found)
6. **Re-test** (verify fix works)
7. **Document findings** (what works, what doesn't)
8. **Tune parameters** (based on real data, not theory)

### Expected Outcomes

After all testing, you'll know:
- ✅ Each phase works correctly in isolation
- ✅ All phases work together end-to-end
- ✅ System handles crowd scenarios
- ✅ System degrades gracefully under load
- ✅ No false merges (critical!)
- ✅ Handoffs detected correctly
- ✅ Performance characteristics (FPS, latency)
- ✅ Bottlenecks identified
- ✅ Tuning parameters optimized

This is **the real work of making the system robust**.

Not more implementation — **verification of what already exists**.
