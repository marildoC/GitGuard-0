# Phase D: FPS/Load-Aware Scheduler Implementation Guide

## Overview

Phase D implements intelligent face processing scheduling to maintain predictable system behavior under adverse FPS/load conditions. The scheduler:

- **Dynamically allocates face processing budget** based on actual FPS
- **Prioritizes which tracks get processed** each frame (PENDING > UNKNOWN > CONFIRMED)
- **Ensures temporal smoothing** for unscheduled tracks (uses cached features)
- **Maintains fairness** via minimum check intervals and time decay
- **Remains fully observable** with comprehensive metrics

## Problem Statement

Without scheduling, all active tracks compete for face processing each frame:
- At high FPS (30+): No problem, all tracks get processed
- At medium FPS (5-15): System must choose ~50% of tracks
- At low FPS (3-5): System must choose ~20% of tracks
- **Without prioritization**: Important tracks (PENDING identity decisions) may be skipped
- **Without fairness**: Some tracks might never get processed (starvation)
- **Result**: Unpredictable identity decisions, system appears "stuck"

Phase D solves this by making scheduling explicit and policy-driven.

---

## System Architecture

### Component Interaction

```
Main Loop (every frame)
    ├─ Measure actual FPS
    ├─ Call Scheduler.compute_schedule()
    │  ├─ Compute budget (based on FPS)
    │  ├─ Score tracks (priority, time decay)
    │  ├─ Select top-K tracks
    │  └─ Return ScheduleContext
    │
    ├─ Pass ScheduleContext to Face Route
    │  ├─ If track in scheduled_track_ids:
    │  │  └─ Extract face features (expensive)
    │  └─ Else:
    │     └─ Use cached face features (free)
    │
    └─ Pass ScheduleContext to Identity Engine
       ├─ If face_embedding present:
       │  └─ Process with new evidence (binding update)
       └─ Else:
          └─ Use temporal smoothing (no binding update)
```

### Data Flow

```
Track {id, binding_state, last_face_ts}
    ↓
Scheduler.compute_schedule()
    ├─ _compute_budget(fps) → budget (int)
    ├─ _compute_priority_scores() → scores {tid: float}
    ├─ _select_top_k(budget) → selected {tid}
    └─ _record_schedule(selected_ts) → update_internal_state()
    ↓
ScheduleContext {scheduled_track_ids, budget_allocated, fps}
    ├─ Face Route uses: if tid in scheduled_track_ids
    └─ Identity Engine uses: if face_embedding is not None
```

---

## Key Components

### 1. SchedulerConfig

Configuration dataclass controlling scheduler behavior:

```python
@dataclass
class SchedulerConfig:
    enabled: bool = True
    budget_policy: str = "adaptive"  # "fixed" or "adaptive"
    fixed_budget_per_frame: int = 10
    
    # Budget thresholds for adaptive policy
    fps_high: float = 15.0      # All faces at high FPS
    fps_medium: float = 5.0     # 50% faces at medium FPS
    fps_low: float = 3.0        # 20% faces at low FPS
    
    # Priority weights (configurable per state)
    priority_weight_unknown: float = 50.0
    priority_weight_pending: float = 80.0
    priority_weight_confirmed_weak: float = 20.0
    priority_weight_confirmed_strong: float = 10.0
    
    # Fairness parameters
    min_check_interval_sec: float = 0.5  # Don't recheck same track too fast
    time_decay_rate: float = 20.0  # Priority delta per second since last check
    
    # Safety
    min_budget: int = 1  # Always schedule at least one track
    max_budget_ratio: float = 0.95  # Never schedule more than 95% of tracks
```

### 2. FaceScheduler

Main scheduling engine:

```python
class FaceScheduler:
    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.track_states: Dict[int, TrackScheduleState] = {}  # Per-track state
    
    def compute_schedule(
        self,
        track_ids: List[int],
        binding_states: Dict[int, str],
        current_ts: float,
        actual_fps: float,
    ) -> ScheduleContext:
        """Main API: Compute which tracks to process this frame."""
        
    def _compute_budget(self, num_tracks: int, actual_fps: float) -> int:
        """Compute how many tracks can be processed this frame."""
        
    def _compute_priority_scores(
        self,
        track_ids: List[int],
        binding_states: Dict[int, str],
        current_ts: float,
    ) -> Dict[int, float]:
        """Compute priority score for each track."""
        
    def _select_top_k(
        self,
        track_ids: List[int],
        scores: Dict[int, float],
        budget: int,
    ) -> Set[int]:
        """Select top-budget tracks by score."""
        
    def _record_schedule(self, scheduled_ids: Set[int], current_ts: float):
        """Update internal state after scheduling decision."""
```

### 3. TrackScheduleState

Per-track scheduling state:

```python
@dataclass
class TrackScheduleState:
    track_id: int
    last_scheduled_ts: float = -1.0
    schedule_count: int = 0
    priority_boost: float = 0.0  # For time decay
```

### 4. ScheduleContext

Return value from compute_schedule():

```python
@dataclass
class ScheduleContext:
    scheduled_track_ids: Set[int]
    budget_allocated: int
    actual_fps: float
    num_tracks: int
    scheduled_ratio: float  # % of tracks scheduled
```

---

## Priority Scoring Algorithm

The scheduler scores each track based on:

1. **Binding State** (primary driver)
   - PENDING: 80 (high priority, identity decision in progress)
   - UNKNOWN: 50 (medium priority, not yet identified)
   - CONFIRMED_WEAK: 20 (low priority, likely identified but weak evidence)
   - CONFIRMED_STRONG: 10 (lowest priority, firmly identified)

2. **Time Decay** (fairness mechanism)
   ```
   time_since_last_check = current_ts - last_scheduled_ts
   decay_bonus = min(time_since_last_check * decay_rate, max_decay)
   ```
   Tracks not checked recently automatically get priority boost

3. **Minimum Interval** (thrash prevention)
   - Tracks checked within min_check_interval_sec get score penalty
   - Prevents re-checking same track every single frame

4. **Final Score**
   ```
   score = base_priority[state] + decay_bonus - min_interval_penalty
   ```

### Example: 5 tracks, budget=2

```
Track 1: PENDING, last_checked=-10s → score = 80 + (10 * 20) = 280
Track 2: UNKNOWN, last_checked=-0.1s → score = 50 + 2 - 30 = 22 (penalized)
Track 3: CONFIRMED_WEAK, last_checked=-5s → score = 20 + (5 * 20) = 120
Track 4: CONFIRMED_STRONG, last_checked=-1s → score = 10 + (1 * 20) = 30
Track 5: UNKNOWN, last_checked=-20s → score = 50 + (20 * 20) = 450

Selection (top 2): Track 5 (450), Track 1 (280)
```

---

## Integration Steps

### Step 1: Add Configuration to config/default.yaml

```yaml
governance:
  scheduler:
    enabled: true
    budget_policy: "adaptive"  # "fixed" or "adaptive"
    fixed_budget_per_frame: 10  # Used if budget_policy="fixed"
    
    # Budget thresholds
    fps_high: 15.0
    fps_medium: 5.0
    fps_low: 3.0
    
    # Priority weights
    priority_weights:
      unknown: 50
      pending: 80
      confirmed_weak: 20
      confirmed_strong: 10
    
    # Fairness
    min_check_interval_sec: 0.5
    time_decay_rate: 20.0
    
    # Safety
    min_budget: 1
    max_budget_ratio: 0.95
```

### Step 2: Initialize Scheduler in main_loop.py

```python
from core.scheduler import create_scheduler_from_config

class MainLoop:
    def __init__(self, config):
        # ... existing init ...
        
        # Phase D: Initialize scheduler
        scheduler_cfg = config.get("governance", {}).get("scheduler", {})
        self.scheduler = create_scheduler_from_config(scheduler_cfg)
    
    def run_frame(self, frame):
        # Measure actual FPS
        current_ts = time.time()
        if self.prev_frame_ts > 0:
            frame_time = current_ts - self.prev_frame_ts
            self.actual_fps = 1.0 / frame_time if frame_time > 0 else 30.0
        self.prev_frame_ts = current_ts
        
        # Phase D: Compute schedule
        schedule_context = self.scheduler.compute_schedule(
            track_ids=list(self.active_tracks.keys()),
            binding_states={
                tid: self.binding_manager.get_state(tid)
                for tid in self.active_tracks.keys()
            },
            current_ts=current_ts,
            actual_fps=self.actual_fps,
        )
        
        # ... rest of frame ...
```

### Step 3: Adapt Face Route to Use Scheduler

In `face/route.py`:

```python
def process_tracks(self, tracks, schedule_context=None):
    """Process face features for scheduled tracks."""
    
    for track in tracks:
        # Phase D: Check if track is scheduled
        if schedule_context is not None and not self.config.scheduler.enabled:
            # Scheduler disabled: process all
            is_scheduled = True
        elif schedule_context is None:
            # No scheduler info: process all
            is_scheduled = True
        else:
            # Scheduler active: respect schedule
            is_scheduled = track.id in schedule_context.scheduled_track_ids
        
        if is_scheduled:
            # Extract face features (expensive ~5ms)
            face_sample = self.extract_face_features(track)
            if face_sample is not None:
                track.face_sample = face_sample
        else:
            # Use cached face features (free)
            cached = self.face_cache.get(track.id)
            if cached is not None and self._is_still_fresh(cached, track):
                track.face_sample = cached
            else:
                # No features available for this frame
                track.face_sample = None
```

### Step 4: Adapt Identity Engine to Handle Unscheduled Tracks

In `identity/identity_engine.py`:

```python
def process_tracks(self, tracks, signals, schedule_context=None):
    """Process identity decisions, with temporal smoothing for unscheduled tracks."""
    
    for sig in signals:
        track_id = sig.track_id
        
        # Check if face features available
        has_new_face = sig.face_embedding is not None
        
        if has_new_face:
            # Standard identity decision with new evidence
            decision = self._decide_with_new_embedding(
                sig.face_embedding,
                track_id,
                sig.pose,
            )
        else:
            # Phase D: Temporal smoothing for unscheduled tracks
            decision = self._decide_with_temporal_smoothing(
                track_id,
                current_ts=sig.timestamp,
            )
        
        yield decision

def _decide_with_temporal_smoothing(self, track_id, current_ts):
    """
    Make identity decision using only temporal evidence (no new face).
    Used for unscheduled tracks during low-FPS conditions.
    """
    # Get previous binding state
    prev_state = self.binding_manager.get_state(track_id)
    
    if prev_state == "CONFIRMED_STRONG":
        # Strong confirmation maintained until contradicted
        return self._create_decision(
            track_id=track_id,
            identity=self.binding_manager.get_identity(track_id),
            confidence=0.95,
            method="temporal_smoothing_strong",
        )
    elif prev_state == "CONFIRMED_WEAK":
        # Weak confirmation decays if not reinforced
        time_since_confirmation = current_ts - self.binding_manager.last_evidence_ts(track_id)
        if time_since_confirmation < 2.0:  # Configurable
            return self._create_decision(
                track_id=track_id,
                identity=self.binding_manager.get_identity(track_id),
                confidence=0.85,
                method="temporal_smoothing_weak",
            )
        else:
            # Decay to UNKNOWN
            return self._create_decision(
                track_id=track_id,
                identity=None,
                confidence=0.0,
                method="temporal_smoothing_decay",
            )
    else:
        # UNKNOWN/PENDING: cannot decide without face
        return self._create_decision(
            track_id=track_id,
            identity=None,
            confidence=0.0,
            method="no_face_data",
        )
```

---

## Behavior Specifications

### High FPS (30+)
- Budget = 100% of tracks
- All tracks processed every frame
- Scheduler is transparent (doesn't affect behavior)
- **Result**: Same as Phase C behavior

### Medium FPS (5-15)
- Budget = 50% of tracks
- PENDING tracks prioritized (usually scheduled every frame)
- UNKNOWN tracks scheduled most frames
- CONFIRMED_WEAK sometimes skipped
- CONFIRMED_STRONG often skipped
- **Result**: Identity decisions continue steadily, confirmed identities maintain smoothly

### Low FPS (3-5)
- Budget = 20% of tracks
- Only most important tracks (PENDING) guaranteed
- UNKNOWN tracks scheduled regularly but not every frame
- CONFIRMED identities maintained via temporal smoothing
- **Result**: System remains responsive, no decision stalls

### Timeout/No FPS Data
- Assume 30 FPS (safe fallback)
- Process all tracks
- **Result**: Graceful degradation

---

## Testing Strategy

### Unit Tests (test_scheduler.py)

1. **Budget Computation**
   - High FPS → 100% budget
   - Medium FPS → 50% budget
   - Low FPS → 20% budget
   - Budget always ≥ 1

2. **Priority Scoring**
   - PENDING > UNKNOWN > CONFIRMED_WEAK > CONFIRMED_STRONG
   - Time decay increases older tracks' scores
   - Min interval prevents thrashing

3. **Fair Scheduling**
   - All tracks eventually scheduled over multiple frames
   - PENDING scheduled more frequently than CONFIRMED
   - No starvation

4. **Edge Cases**
   - Empty track list
   - Single track
   - Unknown binding states
   - Missing binding states

### Integration Tests

1. **Main Loop Integration**
   - Scheduler initializes from config
   - compute_schedule() called each frame
   - ScheduleContext passed to face route and identity engine

2. **Face Route Integration**
   - Scheduled tracks: face features extracted
   - Unscheduled tracks: face features from cache
   - Graceful fallback if cache miss

3. **Identity Engine Integration**
   - Scheduled tracks: normal identity decision
   - Unscheduled tracks: temporal smoothing decision
   - No binding state updates without face evidence

### System Tests

1. **Low FPS Scenario (3 FPS, 50 people)**
   - No decision stalls (identity decisions continue)
   - No unbounded compute spikes
   - Fair scheduling across all tracks
   - Confirmed identities maintained

2. **Dropout Scenario (30 FPS → 3 FPS → 30 FPS)**
   - System adapts to FPS changes
   - Budget allocation changes dynamically
   - No lost state on FPS change
   - Recovers fully at high FPS

3. **Priority Validation**
   - PENDING tracks processed within 2 frames
   - UNKNOWN tracks processed within 10 frames
   - CONFIRMED_STRONG tracks processed within 30 frames

---

## Metrics and Observability

### Key Metrics

1. **Budget Metrics**
   - `schedule/budget_allocated` (per frame)
   - `schedule/fps_measured` (per frame)
   - `schedule/budget_policy` (enum: fixed|adaptive)

2. **Scheduling Metrics**
   - `schedule/tracks_scheduled` (per frame)
   - `schedule/scheduled_ratio` (per frame)
   - `schedule/tracks_deferred` (per frame)

3. **Per-Track Metrics**
   - `schedule/track_schedule_count` (cumulative)
   - `schedule/track_last_scheduled_ts` (per track)
   - `schedule/track_time_since_scheduled` (per frame)

4. **State Distribution Metrics**
   - `schedule/state_distribution` (histogram: UNKNOWN|PENDING|CONFIRMED_WEAK|CONFIRMED_STRONG)
   - `schedule/priority_scores` (histogram of scores)

### Logging Strategy

```python
# High-level decision events
logger.info({
    "event": "schedule_computed",
    "budget": 10,
    "tracks": 50,
    "scheduled": 10,
    "fps": 5.2,
    "policy": "adaptive",
})

# Per-track events (for debugging)
logger.debug({
    "event": "track_scheduled",
    "track_id": 123,
    "binding_state": "PENDING",
    "priority_score": 250.5,
    "reason": "high_priority_state",
})

logger.debug({
    "event": "track_deferred",
    "track_id": 456,
    "binding_state": "CONFIRMED_STRONG",
    "priority_score": 15.2,
    "reason": "low_priority_budget_exhausted",
})
```

---

## Configuration Examples

### Aggressive Face Processing (High Compute Available)

```yaml
scheduler:
  budget_policy: "fixed"
  fixed_budget_per_frame: 50
  min_check_interval_sec: 0.1
```

Result: 50 tracks processed every frame regardless of FPS

### Conservative Face Processing (Low Compute)

```yaml
scheduler:
  budget_policy: "fixed"
  fixed_budget_per_frame: 2
  min_check_interval_sec: 2.0
```

Result: Only 2 tracks processed per frame, minimum 2 seconds between recheck

### Adaptive (Recommended)

```yaml
scheduler:
  budget_policy: "adaptive"
  fps_high: 20.0        # 30+ FPS: all faces
  fps_medium: 8.0       # 8-20 FPS: 50% faces
  fps_low: 4.0          # 4-8 FPS: 20% faces
  min_check_interval_sec: 0.5
  time_decay_rate: 15.0
```

Result: Budget scales with available FPS, maintains fairness

---

## Rollback/Safety

### How to Disable Phase D

Option 1: Config file
```yaml
governance:
  scheduler:
    enabled: false
```

Option 2: Environment variable
```bash
PHASE_D_ENABLED=false python main.py
```

Option 3: Code runtime
```python
# In main_loop.py
if not self.config.governance.scheduler.enabled:
    # Don't use scheduler, process all tracks
    schedule_context = None
```

### Verification

After disabling:
- All tracks processed every frame (same as Phase C)
- No performance improvement but no regression either
- System returns to original behavior

---

## Performance Expectations

### CPU Impact
- Scheduler algorithm: ~0.1ms per frame (negligible)
- Mostly benefit from reduced face extraction (5ms × skipped_tracks)
- Example: 50 tracks, 10 scheduled = 40 × 5ms = 200ms saved per frame

### Memory Impact
- TrackScheduleState per track: ~50 bytes
- For 100 tracks: ~5KB (negligible)

### Accuracy Impact
- Zero impact if system has sufficient FPS
- Low FPS: slight delay in identity confirmation (due to fewer samples)
  - Mitigated by temporal smoothing
  - PENDING tracks get most samples anyway

---

## Success Criteria for Phase D

✅ **Predictable Behavior**
- System behavior scales with FPS in documented way
- No sudden stalls or regressions

✅ **Fair Scheduling**
- All tracks eventually processed over multiple frames
- No starvation (even CONFIRMED_STRONG gets checked occasionally)

✅ **Priority Respected**
- PENDING tracks scheduled most frequently
- Confirmed identities maintained via smoothing

✅ **Graceful Degradation**
- System works at any FPS (3-30+)
- Scheduler transparent at high FPS
- Budget scales with load

✅ **Observable**
- All scheduling decisions logged
- Metrics available for monitoring
- Debugging data rich and structured

✅ **No Regression**
- Phase A (observability) still works
- Phase B (evidence gating) still works
- Phase C (binding) still works
- All three phases interact correctly with Phase D

---

## Conclusion

Phase D completes the core robustness infrastructure:

| Phase | Purpose | Status |
|-------|---------|--------|
| A | Observability & Config | ✅ Complete |
| B | Evidence Gating | ✅ Complete |
| C | Binding State Machine | ✅ Complete |
| D | FPS/Load-Aware Scheduler | 🟡 In Progress |
| E | Handoff Merge Manager | ⏳ Planned |
| F | Simultaneous Merge | ⏳ Optional |

Phase D enables production-grade system behavior under adverse conditions, maintaining identity decision accuracy while scaling intelligently with available compute.

Next: Proceed to Phase D Integration Steps 2-6 (main loop, face route, identity engine, tests, metrics).
