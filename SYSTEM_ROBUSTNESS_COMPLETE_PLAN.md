# GaitGuard System Robust Implementation Plan - Complete

**Version**: 1.0  
**Date**: December 24, 2025  
**Status**: Phase D Ready for Implementation

---

## Executive System Overview

The GaitGuard system is a real-time crowd surveillance identity engine that must maintain perfect reliability under adverse conditions (low FPS, many people, poor quality, similar faces).

### Core System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MAIN LOOP (core/main_loop.py)             │
│  Frame In → Perception → Identity → Governance → Alert → UI Out │
└─────────────────────────────────────────────────────────────────┘
         │              │           │           │
         ↓              ↓           ↓           ↓
    YOLO + SORT    FaceRoute   BindingEngine  Scheduler
    (Detection)    (Quality)   (Confirmation) (Compute)
```

### System Robustness Tiers

1. **Tier 0: Safety Invariants** (must never violate)
   - No low-quality evidence creates false positives
   - Confirmed identity cannot flip on single frame
   - Encryption keys protected
   - Real-time performance maintained

2. **Tier 1: Stability Layer** (Phase A-C)
   - Evidence gating (quality contracts)
   - Binding state machine (anti-lock-in)
   - State-aware confirmation rules

3. **Tier 2: Compute Governance** (Phase D-E)
   - Scheduler (prioritize under load)
   - Merge manager (safe identity deduplication)

4. **Tier 3: Optimization** (Phase F)
   - Simultaneous dedup merge (optional)
   - Pose-aware binding (optional)

---

## Phase Implementation Sequence (Step-by-Step)

### Phase A: Observability & Config Switches ✅ COMPLETE
**Status**: Implemented  
**Impact**: Zero behavior change, all-rollback capability

**Files Modified**:
- `core/config.py` - Added governance section
- `config/default.yaml` - All flags and thresholds
- `core/metrics.py` - Counters for all governance events
- `core/logging_setup.py` - Debug event logging

**Deliverables**: Config framework, metrics collection, debug logging

---

### Phase B: Evidence Gating (Quality Contract) ✅ COMPLETE
**Status**: Implemented  
**Impact**: Filters low-quality evidence before identity processing

**Files Created**:
- `identity/evidence_gate.py` - Gate decision engine

**Files Modified**:
- `face/route.py` - Integrates gate, forwards decisions
- `identity/identity_engine.py` - Consumes gate output
- `core/schemas.py` - Enhanced FaceSample

**Deliverables**: Evidence quality contract, state-aware gating rules

**Key Logic**:
```
Input: FaceSample with (quality, yaw, pitch, bbox_size, blur, brightness, ...)
Process:
  - Check track context (age, binding_state)
  - Apply state-specific thresholds
  - Output: ACCEPT | HOLD | REJECT with reason

Output: Filtered evidence stream
```

---

### Phase C: Binding State Machine ✅ COMPLETE
**Status**: Implemented  
**Impact**: Prevents identity flipping, enforces margin rules

**Files Created**:
- `identity/binding.py` - Core binding engine
- `identity/tests/test_binding.py` - 40+ unit tests

**Files Modified**:
- `identity/identity_engine.py` - Binding application

**Deliverables**: State machine, evidence accumulation, anti-lock-in

**State Transitions**:
```
UNKNOWN (no identity)
  ├─ 3 strong samples → PENDING
  └─ weak evidence loops

PENDING (candidate locked)
  ├─ 3 sustained samples → CONFIRMED_WEAK
  └─ contradictions build → downgrade

CONFIRMED_WEAK (moderate lock)
  ├─ continued strong evidence → CONFIRMED_STRONG
  ├─ contradictions → downgrade
  └─ margin advantage switch → SWITCH_PENDING

CONFIRMED_STRONG (strong lock)
  ├─ evidence continues → maintains
  ├─ contradictions accumulate → CONFIRMED_WEAK
  └─ new person + margin advantage → SWITCH_PENDING

SWITCH_PENDING (switching attempt)
  ├─ sustained new evidence → CONFIRMED_STRONG (new person)
  └─ timeout or old evidence recovers → fallback to previous

STALE (track lost)
  └─ garbage collection
```

---

### Phase D: FPS/Load-Aware Scheduling (CURRENT - IN PROGRESS)
**Status**: Ready for implementation  
**Impact**: Predictable system behavior under compute pressure

**Goals**:
- Prioritize high-value faces when FPS drops
- Prevent stalls in heavy crowd scenarios
- Distribute compute fairly but intelligently

**What This Phase Solves**:
- 3 FPS with 50 people: face route runs N=5 times/person/sec, not all simultaneously
- Prevents frame stalls from expensive face processing
- Allocates compute based on track importance

**Files to Create**:
- `core/scheduler.py` - Face processing scheduler
- `core/tests/test_scheduler.py` - Scheduler tests

**Files to Modify**:
- `core/main_loop.py` - Integrate scheduler
- `face/route.py` - Respect budget constraints
- `core/metrics.py` - Track scheduling decisions

**Deliverables**: Dynamic face budget, priority queue, scheduling logic

---

### Phase E: Handoff Merge Manager (PLANNED)
**Status**: Design ready, implementation after Phase D

**Goals**:
- Safe identity deduplication (merge aliases)
- Time-exclusive merge only (conservative)
- Maintain identity lineage for audit

**Files to Create**:
- `identity/merge_manager.py` - Merge decision engine
- `identity/tests/test_merge_manager.py` - Tests

**Files to Modify**:
- `identity/identity_engine.py` - Integrate merge
- `core/schemas.py` - Merge record types

---

### Phase F: Simultaneous Merge (OPTIONAL - POST-VALIDATION)
**Status**: Design only, implement if Phase E metrics allow

**Goals**:
- Advanced dedup for overlapping tracks
- Only after Phase E proven safe

---

## System Integration Map

```
┌──────────────────────────────────────────────────────────────────────┐
│                      MAIN LOOP                                       │
│  (core/main_loop.py)                                                 │
└──────────────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┼─────────────┐
                ↓             ↓             ↓
          ┌─────────┐   ┌─────────┐   ┌──────────┐
          │PERCEPTION│   │SCHEDULER│   │ IDENTITY │
          │          │   │         │   │          │
          │YOLO+SORT │   │Phase D  │   │Phase B-C │
          │Detection │   │Budget   │   │Evidence  │
          │Tracking  │   │Priority │   │Binding   │
          └─────────┬┘   └────┬────┘   └────┬─────┘
                    │         │             │
                    └────┬────┴────┬────────┘
                         ↓        ↓
                    ┌─────────────────────┐
                    │  FACE ROUTE         │
                    │  (face/route.py)    │
                    │                     │
                    │  Phase A: Config    │
                    │  Phase B: Gate      │
                    │  Phase D: Budget    │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  IDENTITY ENGINE    │
                    │  (identity/*)       │
                    │                     │
                    │  Phase B: Gate      │
                    │  Phase C: Binding   │
                    │  Phase E: Merge     │
                    └────────────┬────────┘
                                 ↓
                    ┌─────────────────────┐
                    │  GOVERNANCE ENGINE  │
                    │  (all phases)       │
                    │  Metrics, Logging   │
                    └─────────────────────┘
```

---

## Critical System Invariants (Non-Negotiable)

### I0.1: Safety Invariants

**I0.1.1 Quality Contract**
- No FaceSample with quality < 0.50 may be processed
- Low quality → HOLD or REJECT, never confirmed identity
- Enforcement: Evidence gate at face/route.py entry point

**I0.1.2 Confirmation Stability**
- Once CONFIRMED_WEAK, identity cannot flip on single frame
- Switch requires sustained evidence + margin advantage
- Enforcement: Binding state machine, margin validation

**I0.1.3 Identity Distinctness**
- Two people cannot merge unless evidence overwhelming
- Merge requires explicit confirmation (Phase E)
- Enforcement: Merge manager with conservative thresholds

**I0.1.4 Real-Time Performance**
- No unbounded loops or memory growth
- Frame processing < max_frame_budget_ms
- Enforcement: Scheduler (Phase D) + metrics monitoring

### I0.2: Functional Invariants

**I0.2.1 Backward Compatibility**
- All new phases can be disabled via config
- Disabling any phase returns to previous behavior
- Enforcement: `phase_*.enabled` flags in config

**I0.2.2 Existing Features Preserved**
- Multiview identity mode works unchanged
- Encrypted gallery load/save unchanged
- UI overlay functionality preserved
- Enforcement: No removals, only additions

**I0.2.3 Structured Traceability**
- Every decision includes reason code + debug info
- All state transitions logged
- All thresholds recorded in decision
- Enforcement: Enhanced IdentityDecision schema

### I0.3: Engineering Invariants

**I0.3.1 Time Normalization**
- All time-based logic uses seconds, never frames
- FPS variations don't break thresholds
- Enforcement: Explicit `ts` parameter in all APIs

**I0.3.2 Comprehensive Logging**
- All governance decisions logged
- Debug mode includes full state dumps
- Enforcement: Logging at decision points

**I0.3.3 Configuration Auditability**
- All used thresholds recorded
- Config changes logged
- Enforcement: Metrics checkpoint on startup

---

## Phase D Implementation Details (NEXT STEP)

### Phase D: FPS/Load-Aware Scheduling

**Problem It Solves**:
- At 3 FPS with 50 people, running face feature extraction for every person every frame causes stalls
- We need predictable behavior: "I will check faces fairly but within budget"

**Design Principle**:
- Compute budget per frame = function(actual_fps, load)
- Scheduler assigns face processing priority
- Face route respects "do this many faces max per frame"

**Architecture**:

```python
# In core/scheduler.py
class FaceScheduler:
    def __init__(self, config):
        self.config = config
        self.track_last_face_ts = {}  # track_id → last time face processed
        self.priority_queue = []        # sorted by priority
    
    def compute_budget(self, actual_fps, num_tracks):
        """
        Compute max faces to process this frame.
        Goal: balance freshness with compute load.
        """
        if actual_fps >= 15:
            return num_tracks  # No constraint, process all
        elif actual_fps >= 5:
            return max(1, int(num_tracks * 0.5))  # Process 50%
        else:
            return max(1, int(num_tracks * 0.2))  # Process 20%
    
    def prioritize(self, tracks, current_ts):
        """
        Return list of track_ids to process this frame.
        Priority = function(binding_state, time_since_last, confidence)
        """
        # CONFIRMED_STRONG: low priority (stable, less urgent)
        # PENDING: high priority (gathering evidence)
        # UNKNOWN: medium (need to know)
        
        priority_score = {}
        for track in tracks:
            score = 0
            # Binding state priority
            if track.binding_state == "UNKNOWN":
                score += 50
            elif track.binding_state == "PENDING":
                score += 80
            elif track.binding_state in ["CONFIRMED_WEAK", "STALE"]:
                score += 20
            else:  # CONFIRMED_STRONG
                score += 10
            
            # Time since last check (decay)
            time_since_last = current_ts - self.track_last_face_ts.get(track.id, 0)
            score += min(100, time_since_last * 10)  # Decays as time increases
            
            priority_score[track.id] = score
        
        # Sort and return top K
        budget = self.compute_budget(...)
        sorted_tracks = sorted(priority_score.items(), key=lambda x: x[1], reverse=True)
        return [tid for tid, _ in sorted_tracks[:budget]]
```

**Integration Points**:

1. **core/main_loop.py**:
   ```python
   scheduler = FaceScheduler(config)
   
   def run():
       while True:
           tracks_in_frame = detector.get_tracks()
           
           # Schedule which tracks get face processing
           face_candidates = scheduler.prioritize(tracks_in_frame, ts)
           
           # Process only scheduled tracks
           for track_id in face_candidates:
               face_samples = face_route.run(frame, [track for t in tracks if t.id == track_id])
           
           # Continue with identity decision on ALL tracks (using cached)
   ```

2. **face/route.py**:
   ```python
   def run(self, frame, tracks, budget_context=None):
       """
       Process faces respecting scheduler budget.
       
       If track not in budget_context['scheduled_ids']:
           - Skip feature extraction
           - Return None (no new evidence)
           - Use last cached face for identity decision
       """
       if budget_context and track.id not in budget_context['scheduled_ids']:
           # Return cached evidence or None
           return self.get_last_cached_face(track.id)
       
       # Otherwise process normally
       return self._extract_features(frame, tracks)
   ```

3. **identity/identity_engine.py**:
   ```python
   def decide(self, signals, scheduler_context):
       """
       Make identity decision with scheduler context.
       
       If no new evidence (evidence.face_embedding is None):
           - Use temporal smoothing (existing logic)
           - Binding state can decay if too old
       """
       for sig in signals:
           if sig.face_embedding is not None:
               # New evidence: apply binding
               decision = self._decide_with_new_embedding(...)
           else:
               # Cached / no budget: use smoothing
               decision = self._decide_with_smoothing_only(...)
   ```

---

## Phase D: Detailed Implementation Steps

### Step 1: Create Scheduler Core (`core/scheduler.py`)

**File**: `/core/scheduler.py`

**Responsibilities**:
- Compute dynamic face budget
- Maintain per-track priority scores
- Return scheduled face candidates each frame

**Key Functions**:
- `compute_budget(actual_fps, num_tracks)` → max_faces_int
- `prioritize(tracks, current_ts)` → [track_ids]
- `record_processed(track_id, ts)` → None

**Configuration**:
```yaml
scheduler:
  enabled: true
  budget_policy: "adaptive"  # or "fixed"
  fixed_budget_per_frame: 10  # if fixed
  min_check_interval_sec: 0.5  # minimum time between checks per track
  priority_weights:
    unknown: 50
    pending: 80
    confirmed_weak: 20
    stale: 10
```

### Step 2: Integrate Scheduler in Main Loop

**File**: `core/main_loop.py`

**Changes**:
- Create FaceScheduler instance
- Call scheduler.prioritize() before face_route
- Pass scheduler context to face_route
- Pass scheduler context to identity engine

### Step 3: Adapt Face Route

**File**: `face/route.py`

**Changes**:
- Accept `scheduler_context` parameter
- Check if track scheduled before expensive extraction
- Return cached face if not scheduled
- Record processing in scheduler

### Step 4: Adapt Identity Engine

**File**: `identity/identity_engine.py`

**Changes**:
- Accept `scheduler_context` parameter
- Handle None evidence (no new face data)
- Use temporal smoothing when no new evidence
- Allow binding decay for stale evidence

### Step 5: Tests for Phase D

**File**: `core/tests/test_scheduler.py`

**Test Cases**:
- Budget computation at various FPS values
- Priority scoring correctness
- Round-robin fairness
- Fallback to smoothing when unscheduled
- Configuration validation

### Step 6: Metrics Integration

**File**: `core/metrics.py`

**New Counters**:
- `scheduler_faces_scheduled_total`
- `scheduler_faces_skipped_total`
- `scheduler_budget_allocated_per_frame` (histogram)
- `scheduler_priority_distribution` (by state)

---

## Phase D: Failure Modes & Mitigations

| Failure Mode | Symptom | Cause | Mitigation |
|---|---|---|---|
| All tracks unscheduled | Identity never updates | Budget = 0 | Enforce min(1, ...) |
| PENDING never confirmed | Evidence too sparse | Low budget | Boost PENDING priority |
| Stale data old | Decision based on 10s ago | Unscheduled too long | Force-include old tracks |
| Scheduler overhead | FPS drops from scheduling | Too much priority math | Cache priority scores |
| Fair scheduling breaks | One person always checked | Aging not working | Decay formula too aggressive |

---

## System Integration Checklist (Phase D)

- [ ] Create `core/scheduler.py` with full implementation
- [ ] Modify `core/main_loop.py` to instantiate and use scheduler
- [ ] Modify `face/route.py` to respect scheduler context
- [ ] Modify `identity/identity_engine.py` to handle unscheduled tracks
- [ ] Add metrics tracking for scheduler events
- [ ] Create comprehensive tests
- [ ] Document scheduler behavior in config
- [ ] Validate no regressions in existing functionality
- [ ] Benchmark performance at various FPS/loads
- [ ] Validate on real-world scenarios (crowded, low FPS)
- [ ] Create Phase D deployment checklist
- [ ] Enable Phase D in config/default.yaml (with defaults)

---

## Full System Robustness Achieved After All Phases

**Phase A**: All governance events observable and configurable
**Phase B**: Evidence quality validated before processing
**Phase C**: Identity confirmed only with sustained, margin-strong evidence
**Phase D**: System behavior predictable under compute pressure
**Phase E**: Identity deduplication safe and auditable
**Phase F**: Advanced dedup optional for high-performance scenarios

**Result**: GaitGuard is a production-grade system that:
- Never produces false positives from low-quality evidence
- Confirms identity only with sustained, high-confidence evidence
- Behaves predictably under all load conditions
- Safely handles identity merging without data loss
- Is fully observable and debuggable
- Can be rolled back at any phase

---

## Success Metrics (Measured Throughout)

| Metric | Target | Phase Achieved |
|--------|--------|---|
| False positive rate | <1% | Phase B |
| Identity flip rate | <5% per track | Phase C |
| Mean time to confirm | <5 sec | Phase C |
| System behavior predictable | Yes | Phase D |
| Merge collisions | Zero | Phase E |
| Real-time performance | 100% | Phase D |
| Observability | Complete | Phase A |
| Rollback capability | 100% | All phases |

---

**Next: Detailed Phase D Implementation (In Progress)**
