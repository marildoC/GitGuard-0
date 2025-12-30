# DEEP ROBUST IMPLEMENTATION GUIDE
## GaitGuard Production-Grade Robustness Architecture

**Date**: December 24, 2025  
**Status**: Blueprint for Phase A-E Implementation  
**Target**: Transform 27% reliability → 87% reliability through systematic, deep architectural improvements

---

## I. CORE PHILOSOPHY: ROBUSTNESS THROUGH ARCHITECTURE

### The Three Principles

**Principle 1: Invariants First**
- Every change must preserve critical safety/functional invariants
- Invariants are listed explicitly; violations are caught immediately
- No "best effort" or "usually works" behavior

**Principle 2: State Machines, Not Heuristics**
- Replace frame-by-frame noisy decisions with persistent state machines
- State transitions are explicit, logged, and reversible (via rollback)
- Every state has clear entry/exit conditions

**Principle 3: Governance Layers, Not Tool Fixes**
- Don't try to make the detector/tracker perfect (impossible in crowds)
- Instead, add layers of governance ABOVE perception
- Each governance layer addresses one category of failure

---

## II. SYSTEM INVARIANTS (Non-Negotiable Rules)

### Safety Invariants (SI)

**SI.1**: No low-quality evidence → positive identity
- Low-quality faces produce HOLD or UNKNOWN, NEVER confident match

**SI.2**: Confirmed identity requires margin + sustained evidence
- Single frame anomaly cannot flip identity
- Identity switch requires sustained contradiction

**SI.3**: Merge never collapses distinct people
- Handoff merge only (time-exclusive), conservative thresholds
- Never merge CONFIRMED→DIFFERENT without >0.95 confidence

### Functional Invariants (FI)

**FI.1**: All existing features continue to work
- Multiview mode unchanged
- Encrypted gallery unchanged  
- SourceAuth pipeline intact
- Ring buffer pruning automatic

**FI.2**: Every layer disable-able via config
- Hard rollback without code changes
- governance_enabled = false → original behavior

**FI.3**: Unbounded memory growth impossible
- Ring buffer auto-prunes
- Binding buffer max 8 samples per track
- Merge records cleaned on track death

### Engineering Invariants (EI)

**EI.1**: All decisions produce structured debug metadata
- reason_code: string enum (exact rejection reason)
- confidence: float [0, 1]
- thresholds_used: dict of parameters applied
- state_transition: before → after

**EI.2**: Time-normalized, not frame-normalized
- All windows in seconds (not frames)
- Allows FPS-agnostic decisions

**EI.3**: No silent failures
- Every anomaly logged with context
- Fallback to UNKNOWN/HOLD, never undefined state

---

## III. ARCHITECTURE LAYERS (How Robustness Is Built)

### Layer Stack

```
┌─────────────────────────────────────────────────────────┐
│ LAYER 6: Display + Alerts (UI Overlay)                 │
│          Shows canonical identity + binding state       │
│          Aggregates duplicate tracklets                 │
└─────────────┬───────────────────────────────────────────┘
              │ Uses canonical_id(track_id)
              │
┌─────────────▼───────────────────────────────────────────┐
│ LAYER 5: Identity Governance Stack                      │
│  ├─ Merge Manager (E): Alias mapping (track fragments)  │
│  ├─ Binding Machine (C): State + margin + contradiction │
│  ├─ Evidence Gate (B): Accept/Hold/Reject decision      │
│  └─ Scheduler (D): Who gets face compute?               │
└─────────────┬───────────────────────────────────────────┘
              │ Outputs IdentityDecision (stable)
              │
┌─────────────▼───────────────────────────────────────────┐
│ LAYER 4: Face Route (Route)                             │
│  - Extract faces, compute embeddings                    │
│  - Produce FaceSample with full quality contract        │
└─────────────┬───────────────────────────────────────────┘
              │ Outputs FaceSample (evidence)
              │
┌─────────────▼───────────────────────────────────────────┐
│ LAYER 3: Gallery + Matching                             │
│  - FAISS search: embedding vs gallery templates         │
│  - Output: SearchResult (best_id, score, margin)        │
└─────────────┬───────────────────────────────────────────┘
              │ Evidence + context
              │
┌─────────────▼───────────────────────────────────────────┐
│ LAYER 2: Perception (Tracker)                           │
│  - YOLO detection, OC-SORT tracking                     │
│  - Outputs: List[Tracklet] with boxes + features        │
└─────────────┬───────────────────────────────────────────┘
              │ Physical observations
              │
┌─────────────▼───────────────────────────────────────────┐
│ LAYER 1: Camera + Config                                │
│  - Frame acquisition, configuration, metrics            │
└─────────────────────────────────────────────────────────┘
```

### Key Insight: Governance ≠ Perfection

```
Current flawed approach:
YOLO → Tracker → Identity → UI
  ↓ (try to fix tracker)
Makes tracker "less bad" at fast motion
Result: Minor improvement, adds latency

Robust approach:
YOLO → Tracker → [ GOVERNANCE LAYERS ] → Identity → UI
          ↓             ↓
    Accepts        Handles
    fragmentation  fragmentation
    (inevitable)   (gracefully)
Result: System works despite tracker issues
```

---

## IV. PHASE A: OBSERVABILITY + CONFIG SWITCHES (Deep Implementation)

**Duration**: 2-3 hours  
**Risk**: Minimal (no behavior change)  
**Outcome**: Foundation for all future phases

### A.1: Governance Config Section

**File**: `config/default.yaml`

**Purpose**: Make every governance layer enable/disable-able without code changes

**Deep Design**:
```yaml
# ============================================================================
# GOVERNANCE CONFIGURATION
# ============================================================================
# Master switch: disable all governance at once (hard rollback)
governance:
  enabled: true
  
  # Evidence-based gating (Phase B)
  evidence_gate:
    enabled: true
    
    # Quality thresholds (state-aware)
    thresholds:
      # For UNKNOWN/PENDING tracks: strict
      unknown_min_quality: 0.68          # 68% quality minimum
      unknown_min_size_px: 80            # Minimum face size
      unknown_min_margin: 0.15           # Margin between best/second-best
      
      # For CONFIRMED tracks: maintenance only (allow lower quality)
      confirmed_min_quality: 0.55        # 55% for maintenance refresh
      confirmed_min_margin: 0.08         # Lower margin for stable tracks
      
      # Pose constraints (yaw in degrees)
      max_yaw_unknown: 40               # Stricter for unconfirmed
      max_yaw_confirmed: 60             # Relaxed for confirmed
      
      # Brightness bounds (0-255 scale, after normalization)
      min_brightness_normalized: 0.2
      max_brightness_normalized: 0.9
      
      # Blur (Laplacian variance, tuned for 512x512 face crops)
      min_blur_score: 200                # Higher = sharper
    
    # Decision behavior
    accept_ratio_target: 0.85            # Target 85% accept rate in stable conditions
    hold_ratio_target: 0.10              # 10% HOLD (borderline)
    reject_ratio_target: 0.05            # 5% REJECT (bad evidence)
    
    # Reason codes (logged for analysis)
    log_reasons: true
    log_level: "DEBUG"                   # INFO, DEBUG
  
  # Binding state machine (Phase C)
  binding:
    enabled: true
    
    # Confirmation rules
    confirmation:
      min_samples_strong: 3              # 3 strong samples to confirm
      min_samples_weak: 5                # 5 weak samples to confirm
      window_seconds: 3.0                # Within 3 seconds
      min_avg_score: 0.75                # Average score must be 0.75+
    
    # Switching rules
    switching:
      min_sustained_samples: 4           # New person needs 4 samples
      margin_advantage: 0.12             # New must score 0.12 better than old
      window_seconds: 2.0
    
    # Anti-lock-in (contradiction handling)
    contradiction:
      threshold: 0.15                    # If score drops 0.15+ below expected
      counter_max: 5                     # 5 contradictions → allow downgrade
      downgrade_factor: 0.8              # Reduce confidence by 20%
    
    # Staleness
    stale_threshold_sec: 8.0             # After 8 seconds no update → STALE
    stale_recovery_samples_needed: 2     # Need 2 samples to recover from STALE
  
  # Load-aware scheduling (Phase D)
  scheduler:
    enabled: true
    
    budget:
      # Option 1: Per-frame limit
      max_faces_per_frame: 10
      
      # Option 2: Time-based budget (faces/second)
      max_faces_per_second: 30
      
      # Use which? ("frame" or "time")
      budget_mode: "time"
    
    priority_rules:
      # Priority score = weighted sum
      unknown_pending_weight: 1.0        # Highest priority
      expiring_weight: 0.9               # Track about to die
      watchlist_weight: 1.2              # Even higher if watchlist
      confirmed_refresh_weight: 0.3      # Periodic refresh
      confirmed_strong_weight: 0.1       # Lowest: already stable
    
    fairness:
      # Prevent track starvation
      starvation_threshold_sec: 15.0     # After 15 sec, force process
      starved_priority_boost: 2.0        # Boost priority 2x
  
  # Handoff merge manager (Phase E)
  merge:
    enabled: true
    handoff_merge_enabled: true          # Merge track fragments over time
    simul_merge_enabled: false           # Do NOT merge simultaneous tracks (unsafe)
    
    thresholds:
      # Spatial: between last_box(A) and first_box(B)
      max_spatial_distance_px: 100       # Within 100px
      
      # Appearance: HSV histogram similarity
      min_appearance_sim: 0.7            # 70%+ similarity
      
      # Face embedding: if both confirmed to same person
      min_embedding_sim: 0.80            # 80%+ cosine similarity
      
      # Time window: A lost and B appeared within this window
      handoff_window_sec: 3.0            # 3 seconds
    
    merge_modes:
      # Safe mode: only merge if CONFIRMED to same person
      conservative: true
      
      # Allow merge if both PENDING and embeddings similar
      loose_pending: false               # Default OFF for safety

# ============================================================================
# DEBUG & TELEMETRY
# ============================================================================
debug:
  # Emit detailed logs for governance decisions
  evidence_gate_decisions: true          # Log every gate decision
  binding_state_transitions: true        # Log every state change
  merge_attempts: true                   # Log merge attempts
  scheduler_selections: true             # Log scheduler budget usage
  
  # UI display toggles
  ui:
    show_binding_state: true             # Display UNKNOWN/PENDING/CONFIRMED
    show_evidence_gate_reason: true      # Show why face was rejected
    show_merge_alias: true               # Show canonical track id
    show_scheduler_budget: false         # Show remaining budget
```

### A.2: Enhanced Metrics Counters

**File**: `core/metrics.py`

**Purpose**: Instrument every governance decision for measurement and tuning

**New Dataclass**:
```python
@dataclass
class GovernanceMetrics:
    """
    Per-second aggregation of governance decisions.
    
    Reset each second; emitted to telemetry logger.
    """
    # Evidence gate
    faces_total: int = 0
    faces_accepted: int = 0
    faces_held: int = 0
    faces_rejected: int = 0
    
    reject_reason_counts: Dict[str, int] = field(default_factory=dict)
    # Examples: {"quality_too_low": 5, "blur_too_much": 2, "brightness_out_range": 1}
    
    hold_reason_counts: Dict[str, int] = field(default_factory=dict)
    # Examples: {"pending_strict_threshold": 3}
    
    # Binding state
    binding_state_counts: Dict[str, int] = field(default_factory=dict)
    # Examples: {"UNKNOWN": 15, "PENDING": 5, "CONFIRMED_STRONG": 25}
    
    binding_confirmations: int = 0       # Tracks that confirmed this second
    binding_downgrades: int = 0          # Tracks downgraded (contradiction)
    binding_switches: int = 0            # Tracks that switched person
    binding_switch_failures: int = 0     # Attempted but rejected
    
    # Scheduler
    scheduler_budget_available: int = 0  # Faces available this frame
    scheduler_selected: int = 0          # Selected for processing
    scheduler_skipped: int = 0           # Deferred to next frame
    scheduler_starved: int = 0           # Forced process due to starvation
    
    # Merge
    merge_attempts: int = 0              # Attempted handoff merges
    merge_success: int = 0               # Successful merges
    merge_collision_risk: int = 0        # Rejected due to collision risk
    
    # FPS & load
    fps_estimate: float = 0.0            # Estimated FPS
    track_count: int = 0                 # Active tracklets
    unknown_rate: float = 0.0            # % of tracks in UNKNOWN state
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/telemetry"""
        return {
            "timestamp": time.time(),
            "faces": {
                "total": self.faces_total,
                "accepted": self.faces_accepted,
                "held": self.faces_held,
                "rejected": self.faces_rejected,
                "reject_reasons": self.reject_reason_counts,
                "hold_reasons": self.hold_reason_counts,
            },
            "binding": {
                "state_counts": self.binding_state_counts,
                "confirmations": self.binding_confirmations,
                "downgrades": self.binding_downgrades,
                "switches": self.binding_switches,
                "switch_failures": self.binding_switch_failures,
            },
            "scheduler": {
                "budget": self.scheduler_budget_available,
                "selected": self.scheduler_selected,
                "skipped": self.scheduler_skipped,
                "starved": self.scheduler_starved,
            },
            "merge": {
                "attempts": self.merge_attempts,
                "success": self.merge_success,
                "collision_risk_rejected": self.merge_collision_risk,
            },
            "system": {
                "fps": self.fps_estimate,
                "track_count": self.track_count,
                "unknown_rate": self.unknown_rate,
            }
        }
```

**Integration Point**: `core/main_loop.py`

```python
def run() -> None:
    # ... existing setup ...
    
    # NEW: Per-second metrics aggregation
    metrics_window = GovernanceMetrics()
    metrics_last_emit = time.time()
    
    while True:
        frame = camera.next_frame()
        
        # Existing pipeline
        tracks = perception.process_frame(frame)
        signals = identity.update_signals(frame, tracks)
        decisions = identity.decide(signals)
        
        # NEW: Collect governance metrics
        for track in tracks:
            metrics_window.track_count = len(tracks)
        
        # Emit metrics every 1 second
        now = time.time()
        if now - metrics_last_emit >= 1.0:
            log.info("Governance Metrics: %s", metrics_window.to_dict())
            metrics_window = GovernanceMetrics()
            metrics_last_emit = now
```

### A.3: UI Debug Display

**File**: `ui/overlay.py`

**Purpose**: Display governance state in real-time overlay for analysis

**New Debug HUD**:
```python
def draw_governance_hud(frame, metrics, bindings, gate_decisions):
    """
    Draw governance state on frame (top-right corner).
    
    Shows:
    - Binding state distribution (bar chart)
    - Evidence gate accept/hold/reject ratio
    - Scheduler budget usage
    - Recent merge actions
    """
    y_offset = 20
    
    # Gate statistics
    cv2.putText(frame, f"GATE: A={metrics.faces_accepted}/{metrics.faces_total}",
                (frame.shape[1] - 300, y_offset), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 255, 0), 1)
    y_offset += 25
    
    # Binding state pie (simplified: bar)
    unknown_pct = metrics.binding_state_counts.get("UNKNOWN", 0) / max(1, metrics.track_count)
    pending_pct = metrics.binding_state_counts.get("PENDING", 0) / max(1, metrics.track_count)
    confirmed_pct = 1.0 - unknown_pct - pending_pct
    
    cv2.putText(frame, f"BIND: U={unknown_pct:.0%} P={pending_pct:.0%} C={confirmed_pct:.0%}",
                (frame.shape[1] - 300, y_offset), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (200, 200, 0), 1)
    y_offset += 25
    
    # Scheduler load
    budget_pct = metrics.scheduler_selected / max(1, metrics.scheduler_budget_available)
    cv2.putText(frame, f"SCHED: {budget_pct:.0%} used ({metrics.scheduler_selected}/{metrics.scheduler_budget_available})",
                (frame.shape[1] - 300, y_offset), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (100, 200, 255), 1)
```

### A.4: Acceptance Criteria

**The system passes Phase A when**:

✅ **Configuration**
- `governance/*` config sections parse without errors
- All boolean flags are respected (enabling/disabling doesn't crash)
- Default values are sensible

✅ **Metrics**
- Counters are emitted every second
- No NaN/inf values in metrics
- Logs show baseline stats (typical values: 25 faces/sec, 3 FPS, 40 tracks)

✅ **No Regression**
- Single-person scenario: identification works identically to before
- Crowd scenario: FPS/track count unchanged
- UI still displays all tracks
- No spurious errors in logs

✅ **Debug Display**
- Overlay shows governance HUD without crashing
- Metrics visible in terminal logs
- Can toggle all debug flags via config

---

## V. PHASE A IMPLEMENTATION CHECKLIST

### File-by-File Modifications

**config/default.yaml**:
- [ ] Add `governance:` top-level section
- [ ] Add all sub-sections (evidence_gate, binding, scheduler, merge)
- [ ] Add `debug:` section
- [ ] Test YAML parsing (no syntax errors)

**core/metrics.py**:
- [ ] Add `GovernanceMetrics` dataclass
- [ ] Add `to_dict()` method for serialization
- [ ] Update `FaceMetrics` to include governance counters

**core/main_loop.py**:
- [ ] Create `GovernanceMetrics()` instance at startup
- [ ] Emit metrics every 1 second
- [ ] Load governance config into memory
- [ ] Pass config to identity engine (for later phases)

**ui/overlay.py**:
- [ ] Add `draw_governance_hud()` function
- [ ] Call it if `ui.show_binding_state` enabled
- [ ] No crashes if binding data is None

**core/config.py**:
- [ ] Add `GovernanceConfig` dataclass matching YAML structure
- [ ] Update main `Config` to include `governance` field
- [ ] Validate thresholds are within sensible ranges (0-1 for scores, etc.)

---

## VI. TESTING PHASE A

### Test 1: Configuration Loading

```bash
# Verify YAML is valid and config loads
python -c "
from core.config import load_config
cfg = load_config()
assert hasattr(cfg, 'governance'), 'Missing governance config'
assert cfg.governance.evidence_gate.enabled in [True, False]
print('✓ Config loads successfully')
"
```

### Test 2: Metrics Emission

```bash
# Run system for 5 seconds; check metrics appear in logs
# Should see GovernanceMetrics output every 1 second
# Typical output:
# "Governance Metrics: {'timestamp': ..., 'faces': {'total': 25, ...}}"
```

### Test 3: No Regression

```bash
# Single-person stable identity test
# Expected: Person enters frame, confirmed as known ID within ~3-4 seconds
# Binding should show CONFIRMED_STRONG
```

---

This is the **DEEP PHASE A IMPLEMENTATION** with exact files, exact fields, exact config keys, and exact integration points. Every governance layer that follows will use these foundations.

**Next**: Once Phase A passes all tests, we proceed to Phase B (Evidence Gating).

