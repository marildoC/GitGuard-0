# Deep Logic Analysis: What Exactly Remains to be Done for System Robustness

**Based on**: Robust_PLan.md requirements vs. What was implemented
**Focus**: Exact coding logic gaps, not timelines or deployment
**Reference**: All 5 Phases (A-E) from the deep plan

---

## 🎯 COMPARISON: PLAN vs. IMPLEMENTATION

### What We DID Implement (Phase E only)

```
✅ Phase E: Handoff Merge Manager (identity/merge_manager.py)
   ├─ Alias mapping logic (track_id → canonical_id)
   ├─ 7-criterion merge scoring
   ├─ Merge execution and reversal
   ├─ Metrics tracking
   └─ Config parameters

✅ Phase E Integration (core/main_loop.py)
   ├─ MergeManager initialization
   ├─ Tracklet lifecycle hooks
   ├─ Periodic merge checking
   └─ Canonical ID resolution
```

### What the PLAN Required (All Phases A-E)

```
Phase A: Observability & Config switches
Phase B: Evidence Gating with quality contract
Phase C: Binding State Machine (anti-flip logic)
Phase D: FPS/Load-aware Scheduler
Phase E: Handoff Merge Manager (what we did)

❌ Phases A-D: NOT IMPLEMENTED YET
```

---

## ⚠️ CRITICAL LOGIC GAP: Missing Phases A-D

The problem: **Phase E alone does NOT make the system robust**

Why? Because Phase E depends on Phases B, C, and D to work correctly:

```
Phase B (Evidence Gating) provides:
  → Quality-filtered face samples that Phase E's 7 criteria depend on
  → Face quality, yaw/pitch/roll, blur, brightness
  → Without it: Phase E might merge on garbage evidence

Phase C (Binding State Machine) provides:
  → Stable identity decisions that Phase E respects
  → Anti-lock-in logic to prevent wrong merges sticking
  → Without it: Phase E might merge incorrectly and never recover

Phase D (Scheduler) provides:
  → Fair face processing budget
  → Without it: tracks starve, merges can't validate properly
  → Without it: under crowd load, identity decisions become unreliable
```

**The current situation:**
- Phase E code exists ✅
- But it's being called with inputs that lack Phase B quality validation
- And it's updating bindings that lack Phase C's state machine logic
- And it's running in an unscheduled environment (Phase D missing)

---

## 🔴 LOGIC MISSING: Phase A - Observability & Config

### What Phase A Should Do (Currently Missing)

Phase A is the **foundation layer**. Without it, you can't properly measure or control Phases B-E.

#### A1: Config Governance Section (MISSING)

**Current Reality:**
- `config/default.yaml` has merge parameters
- But NO central `governance` flag section
- No enable/disable switches for B, C, D

**What's Missing (Logic):**
```python
# In config/default.yaml, should have:

governance:
  enabled: true/false                      # Master switch
  evidence_gate_enabled: true/false        # Phase B control
  binding_enabled: true/false              # Phase C control
  scheduler_enabled: true/false            # Phase D control
  handoff_merge_enabled: true/false        # Phase E control (exists)
  simul_merge_enabled: false               # Phase F (optional)
  
  debug:
    log_reasons: true/false                # All decisions logged with reason codes
    ui_show_state: true/false              # UI displays binding/gating state
    show_raw_vs_decision: true/false       # Side-by-side raw match vs binding

# Core to robustness: if Phase B behaves badly, you can disable it with:
# governance.evidence_gate_enabled: false
# → Falls back to Phase A (raw matching, no gating)
```

**Implementation Logic (Current Gap):**
```python
# core/main_loop.py should read and respect:
if not config.governance.evidence_gate_enabled:
    # Phase B disabled, skip gating
    face_sample_decision = "ACCEPT"  # Accept all
else:
    # Phase B enabled
    face_sample_decision = evidence_gate.decide(...)

if not config.governance.binding_enabled:
    # Phase C disabled, use raw match
    identity_output = raw_match_result
else:
    # Phase C enabled
    identity_output = binding_state_machine.get(...)
```

**Currently missing:**
- No such control flow exists
- Phase E is integrated but B, C, D are not even hooked in
- Can't roll back to prior behavior

#### A2: Metrics Instrumentation (PARTIAL)

**Current Reality:**
- Some metrics exist in main_loop.py
- But NOT complete governance metrics

**What's Missing (Logic):**
```python
# In core/metrics.py, need to track:

governance_metrics = {
    # Phase B: Evidence Gating
    "faces_accepted": 0,
    "faces_held": 0,
    "faces_rejected": 0,
    "reject_reason_counts": {
        "low_quality": 0,
        "high_yaw": 0,
        "high_blur": 0,
        "low_brightness": 0,
        # ... etc
    },
    
    # Phase C: Binding State Machine
    "binding_state_counts": {
        "UNKNOWN": 0,
        "PENDING": 0,
        "CONFIRMED_WEAK": 0,
        "CONFIRMED_STRONG": 0,
        "SWITCH_PENDING": 0,
        "STALE": 0,
    },
    "switch_attempts": 0,
    "switch_success": 0,
    "switch_failure_reasons": {},
    
    # Phase D: Scheduler
    "scheduler_budget_used": 0,
    "scheduler_budget_available": 0,
    "scheduler_fairness_starved": 0,  # Tracks that got no face processing
    
    # Phase E: Merge
    "merge_attempts": 0,
    "merge_success": 0,  # (already exists in merge_manager)
}

# Each phase must EMIT to these counters
```

**Currently missing:**
- Phase B, C, D metrics emission points
- No counters being tracked for quality gate reasons
- No binding state distribution tracking
- No scheduler fairness metrics

---

## 🔴 LOGIC MISSING: Phase B - Evidence Gating

### What Phase B Should Do (Completely Missing)

Phase B is the **quality filter**. It's the "don't poison identity with garbage" layer.

#### B1: Quality Contract on FaceSample (PARTIAL)

**Current Reality:**
- FaceSample exists in schemas
- But missing quality metadata fields

**What's Missing (Logic):**
```python
# In core/schemas.py (or schemas/face_sample.py)
# FaceSample should include (currently missing):

@dataclass
class FaceSample:
    # ... existing fields ...
    
    # MISSING: Quality Contract Fields
    quality: float                    # 0.0 to 1.0 (quality gate will use)
    yaw_deg: float                    # Head rotation left/right
    pitch_deg: float                  # Head rotation up/down
    roll_deg: float                   # Head tilt
    bbox_size_px: int                 # min(width, height) - face size
    blur_score: float                 # Laplacian variance (higher = sharper)
    brightness: float                 # Mean intensity
    occlusion_hint: Optional[str]     # "none", "partial", "heavy", or None
    landmark_confidence: float        # How confident were landmarks detected
    
    # Why needed:
    # - Evidence gate will use yaw/pitch/roll to decide "is this usable for identity?"
    # - blur_score to reject motion blur / out-of-focus
    # - brightness to reject very dark / very bright
    # - bbox_size to reject too small faces
    # - quality aggregates all above
```

**Currently missing:**
- `yaw_deg`, `pitch_deg`, `roll_deg` not in FaceSample
- `blur_score` not computed/stored
- `brightness` not computed/stored
- `bbox_size_px` not in FaceSample
- `occlusion_hint` not present
- `landmark_confidence` not in FaceSample

#### B2: Evidence Gate Module (COMPLETELY MISSING)

**Current Reality:**
- `identity/evidence_gate.py` exists but is NEVER CALLED
- No gating happens in the pipeline

**What's Missing (Logic):**

```python
# New file: identity/evidence_gate.py
# CORE FUNCTION (currently does not exist):

class EvidenceGate:
    def __init__(self, config):
        self.config = config
        # Quality thresholds, state-aware policies, etc.
    
    def decide(self, 
               face_sample: FaceSample,
               binding_state: str,           # UNKNOWN/PENDING/CONFIRMED_WEAK/CONFIRMED_STRONG
               track_age_seconds: float,
               last_accept_time: float,
               fps_hint: float) -> Tuple[str, str]:
        """
        Decision logic (MISSING):
        
        Returns:
          ("ACCEPT", reason_code)   -> Face can update identity
          ("HOLD", reason_code)     -> Keep track but don't update identity yet
          ("REJECT", reason_code)   -> Ignore this face
        """
        
        # LOGIC REQUIRED:
        # 1. Quality checks (currently missing)
        if face_sample.blur_score < self.config.blur_threshold:
            return ("REJECT", "blur_too_high")
        
        if face_sample.quality < self.config.quality_threshold:
            return ("REJECT", "quality_too_low")
        
        # 2. State-aware thresholds (currently missing)
        if binding_state == "UNKNOWN":
            # Strict: require very good quality to start confirming
            if face_sample.quality < 0.7:
                return ("HOLD", "quality_insufficient_for_unknown")
            if abs(face_sample.yaw_deg) > 45:
                return ("HOLD", "pose_too_extreme_for_unknown")
        
        elif binding_state == "CONFIRMED_WEAK":
            # Moderate: can accept moderate quality to maintain
            if face_sample.quality < 0.5:
                return ("HOLD", "quality_insufficient_for_weak_confirm")
        
        elif binding_state == "CONFIRMED_STRONG":
            # Loose: accept even lower quality (maintenance only)
            if face_sample.quality < 0.3:
                return ("REJECT", "quality_too_low_for_strong_confirm")
        
        # 3. Pose policies (currently missing)
        if self.config.multiview_mode and binding_state in ["PENDING", "CONFIRMED_WEAK"]:
            # Side poses OK if quality is HIGH
            if abs(face_sample.yaw_deg) > 30 and face_sample.quality < 0.8:
                return ("HOLD", "sidepose_insufficient_quality")
        
        # 4. Brightness/occlusion (currently missing)
        if face_sample.brightness < 30 or face_sample.brightness > 220:
            return ("HOLD", "brightness_extreme")
        
        if face_sample.occlusion_hint == "heavy":
            return ("HOLD", "heavy_occlusion")
        
        # All checks passed
        return ("ACCEPT", "quality_sufficient")
```

**Currently missing:**
- `EvidenceGate.decide()` is NEVER CALLED in the pipeline
- Face samples skip gating entirely
- Identity engine receives ALL face samples, even poor quality
- No HOLD mechanism (would prevent identity from updating but keep track alive)

#### B3: Integration Hook (MISSING)

**Current Reality:**
- `face/route.py` selects faces and sends to identity engine
- No gating happens

**What's Missing (Logic):**

```python
# In face/route.py (currently missing this flow):

def process_face_for_track(track, frame):
    # ... existing code to extract face ...
    face_sample = FaceSample(
        # ... existing fields ...
        # NEED TO ADD:
        yaw_deg = compute_yaw(landmarks),
        pitch_deg = compute_pitch(landmarks),
        blur_score = compute_blur(face_patch),
        brightness = compute_brightness(face_patch),
        bbox_size_px = min(face_box.width, face_box.height),
    )
    
    # MISSING: Call evidence gate
    gate_decision, reason = evidence_gate.decide(
        face_sample = face_sample,
        binding_state = track.binding_state,  # From Phase C
        track_age_seconds = track.age_seconds,
        last_accept_time = track.last_face_accept_time,
        fps_hint = current_fps
    )
    
    if gate_decision == "REJECT":
        # Don't process this face at all
        track.metrics.faces_rejected += 1
        return None
    
    if gate_decision == "HOLD":
        # Process face (extract embedding) but don't update identity
        track.metrics.faces_held += 1
        # Will need logic to accept embedding without updating binding
        return ("HOLD", face_sample)
    
    if gate_decision == "ACCEPT":
        # Normal flow: extract embedding and forward to identity engine
        track.metrics.faces_accepted += 1
        embedding = embedder.embed(face_sample)
        return ("ACCEPT", embedding)
```

**Currently missing:**
- No `gate_decision` logic
- All faces go straight to identity engine
- No distinction between ACCEPT/HOLD/REJECT
- No binding_state awareness in face processing

---

## 🔴 LOGIC MISSING: Phase C - Binding State Machine

### What Phase C Should Do (Completely Missing)

Phase C is the **identity stability layer**. It's the "don't flip on a bad frame" and "recover from wrong initial guess" layer.

#### C1: Binding State Machine (COMPLETELY MISSING)

**Current Reality:**
- `identity/binding.py` exists but is NEVER USED
- Identity engine directly outputs match result
- No state machine, no margin logic, no anti-lock-in

**What's Missing (Logic):**

```python
# In identity/binding.py
# CORE CLASS (currently not integrated):

class BindingStateMachine:
    def __init__(self, config):
        self.config = config
        self.track_states = {}  # track_id -> binding_state
        self.evidence_buffer = {}  # track_id -> list of (person_id, score, margin, ts)
    
    def update(self, 
               track_id: int,
               match_result: Dict,          # from identity_engine.match()
               face_sample: FaceSample,     # quality info
               current_time: float) -> Dict:
        """
        LOGIC REQUIRED (completely missing):
        
        Input:
          - match_result: {
              'best_id': int or None,
              'best_score': float,
              'second_best_score': float,
              'margin': float,  # best - second
            }
          - current binding state for this track
          - quality of face sample
        
        Output:
          - Updated binding state
          - Final person_id to use
          - Reason for any state change
        """
        
        state = self.track_states.get(track_id, {
            'status': 'UNKNOWN',
            'person_id': None,
            'confidence': 0.0,
            'evidence_buffer': [],  # Last N accepted samples
            'last_update_time': current_time,
            'contradiction_count': 0,
            'switch_pending_count': 0,
        })
        
        # BUFFER management (currently missing)
        if match_result['best_id'] is not None:
            state['evidence_buffer'].append({
                'person_id': match_result['best_id'],
                'score': match_result['best_score'],
                'margin': match_result['margin'],
                'quality': face_sample.quality,
                'timestamp': current_time,
            })
            # Keep only last N samples within time window
            max_buffer = self.config.binding.evidence_buffer_size  # e.g., 8
            max_window = self.config.binding.evidence_window_seconds  # e.g., 5
            state['evidence_buffer'] = [
                e for e in state['evidence_buffer']
                if current_time - e['timestamp'] < max_window
            ][-max_buffer:]
        
        # STATE MACHINE LOGIC (completely missing)
        if state['status'] == 'UNKNOWN':
            # Require K wins for same person within time window
            if len(state['evidence_buffer']) >= self.config.binding.confirm_require_samples:
                # Check if all agree
                person_ids = [e['person_id'] for e in state['evidence_buffer']]
                if len(set(person_ids)) == 1:
                    # All same person
                    avg_score = sum(e['score'] for e in state['evidence_buffer']) / len(state['evidence_buffer'])
                    avg_margin = sum(e['margin'] for e in state['evidence_buffer']) / len(state['evidence_buffer'])
                    
                    if avg_score > self.config.binding.confirm_score_threshold and \
                       avg_margin > self.config.binding.confirm_margin_threshold:
                        # CONFIRM
                        state['status'] = 'CONFIRMED_WEAK'
                        state['person_id'] = person_ids[0]
                        state['confidence'] = avg_score
                        return {'status': 'CONFIRMED_WEAK', 'person_id': person_ids[0], 'confidence': avg_score}
                    else:
                        # Margin too weak, stay UNKNOWN
                        state['status'] = 'PENDING'
                        return {'status': 'PENDING', 'person_id': None, 'confidence': 0.0}
                else:
                    # Multiple people detected in buffer, stay UNKNOWN
                    return {'status': 'UNKNOWN', 'person_id': None, 'confidence': 0.0}
            else:
                # Not enough samples, stay UNKNOWN
                return {'status': 'UNKNOWN', 'person_id': None, 'confidence': 0.0}
        
        elif state['status'] == 'CONFIRMED_WEAK' or state['status'] == 'CONFIRMED_STRONG':
            # ANTI-LOCK-IN LOGIC (missing):
            # If new evidence contradicts, downgrade
            if match_result['best_id'] != state['person_id'] and match_result['best_score'] > 0.8:
                # Strong evidence for different person
                state['contradiction_count'] += 1
                
                if state['contradiction_count'] > self.config.binding.switch_contradiction_threshold:
                    # Too many contradictions, allow switch
                    state['status'] = 'SWITCH_PENDING'
                    state['switch_pending_count'] = 0
                    return {'status': 'SWITCH_PENDING', 'person_id': None, 'confidence': 0.0}
                else:
                    # Keep current, but count contradictions
                    return {'status': state['status'], 'person_id': state['person_id'], 'confidence': state['confidence']}
            else:
                # Evidence matches or inconclusive, reset contradiction counter
                state['contradiction_count'] = 0
                return {'status': state['status'], 'person_id': state['person_id'], 'confidence': state['confidence']}
        
        elif state['status'] == 'SWITCH_PENDING':
            # Require sustained wins for new person before switching
            if match_result['best_id'] is not None and match_result['best_id'] != state['person_id']:
                state['switch_pending_count'] += 1
                if state['switch_pending_count'] > self.config.binding.switch_confirm_samples:
                    # Switch approved
                    state['person_id'] = match_result['best_id']
                    state['status'] = 'CONFIRMED_WEAK'
                    state['confidence'] = match_result['best_score']
                    state['switch_pending_count'] = 0
                    return {'status': 'CONFIRMED_WEAK', 'person_id': match_result['best_id'], 'confidence': match_result['best_score']}
                else:
                    return {'status': 'SWITCH_PENDING', 'person_id': None, 'confidence': 0.0}
            else:
                # Evidence disappeared or went back to old person
                state['status'] = 'CONFIRMED_WEAK'
                state['switch_pending_count'] = 0
                return {'status': 'CONFIRMED_WEAK', 'person_id': state['person_id'], 'confidence': state['confidence']}
        
        self.track_states[track_id] = state
        return {'status': state['status'], 'person_id': state['person_id'], 'confidence': state['confidence']}
```

**Currently missing:**
- No `BindingStateMachine.update()` logic
- No evidence buffering (accumulating samples over time)
- No margin enforcement (best vs second-best comparison)
- No anti-lock-in (contradiction counter to allow downgrades)
- No SWITCH_PENDING state (switch requires sustained evidence)
- Identity engine output directly used, no binding layer between it and the UI

#### C2: Integration Hook (MISSING)

**Current Reality:**
- Identity engine returns match result
- Directly used for display/alerts

**What's Missing (Logic):**

```python
# In identity/identity_engine.py (or wherever match() is called):

def match(face_embedding) -> Dict:
    # ... existing gallery search code ...
    match = {
        'best_id': best_person_id,
        'best_score': best_score,
        'second_best_score': second_best_score,
        'margin': best_score - second_best_score,
    }
    return match


# In core/main_loop.py (where identity is decided, LOGIC MISSING):

# Currently this might look like:
identity_result = identity_engine.match(embedding)
identity_decision = IdentityDecision(
    person_id = identity_result['best_id'],
    confidence = identity_result['best_score'],
)

# SHOULD LOOK LIKE (with binding):
raw_match = identity_engine.match(embedding)

binding_output = binding_state_machine.update(
    track_id = track.id,
    match_result = raw_match,
    face_sample = face_sample,
    current_time = current_time,
)

identity_decision = IdentityDecision(
    person_id = binding_output['person_id'],
    confidence = binding_output['confidence'],
    binding_state = binding_output['status'],  # MISSING: This field
)
```

**Currently missing:**
- `BindingStateMachine.update()` never called
- Raw match result used directly
- No binding_state in IdentityDecision
- No margin enforcement
- No anti-flip logic

---

## 🔴 LOGIC MISSING: Phase D - Scheduler

### What Phase D Should Do (Completely Missing)

Phase D is the **compute governance layer**. It's the "be fair about face processing under load" layer.

#### D1: Scheduler Module (COMPLETELY MISSING)

**Current Reality:**
- No scheduler exists
- All tracklets compete equally for face processing
- Under crowd load, no fairness or starvation prevention

**What's Missing (Logic):**

```python
# New file: core/scheduler.py
# COMPLETELY MISSING:

class FaceProcessingScheduler:
    def __init__(self, config):
        self.config = config
        # Budget: max faces/second or max/frame
        self.budget_faces_per_second = config.scheduler.max_faces_per_second
        self.budget_time_window = 1.0  # 1 second
        self.budget_used_this_window = 0
        self.window_start_time = time.time()
        
        # Fairness: track when each tracklet last got face processing
        self.track_last_process_time = {}  # track_id -> timestamp
    
    def select(self,
               tracklets: List[Tracklet],
               fps: float,
               binding_states: Dict) -> List[int]:  # Returns track_ids to process this frame
        """
        LOGIC REQUIRED (completely missing):
        
        Priority rules:
        1. UNKNOWN/PENDING without recent ACCEPT → highest
        2. About to expire (age > max_age) → high
        3. Watchlist/suspicious → very high
        4. Not processed in X seconds (fairness) → medium
        5. CONFIRMED_STRONG just updated → lowest
        
        Returns: List of track_ids that should get face processing this frame
        """
        
        # Compute budget (dynamic based on FPS)
        current_time = time.time()
        if current_time - self.window_start_time >= self.budget_time_window:
            self.budget_used_this_window = 0
            self.window_start_time = current_time
        
        budget_remaining = self.budget_faces_per_second - self.budget_used_this_window
        
        if budget_remaining <= 0:
            # No budget left this frame
            return []
        
        # Score each tracklet by priority
        priority_scores = []
        
        for track in tracklets:
            score = 0
            reason = ""
            
            binding_state = binding_states.get(track.id, 'UNKNOWN')
            
            # Rule 1: UNKNOWN/PENDING without recent accept (highest)
            if binding_state in ['UNKNOWN', 'PENDING']:
                if track.last_accept_time is None or \
                   (current_time - track.last_accept_time) > self.config.scheduler.refresh_interval:
                    score += 100
                    reason = "UNKNOWN/PENDING_needs_confirm"
            
            # Rule 2: About to expire (high)
            track_age = current_time - track.creation_time
            max_track_age = self.config.scheduler.max_track_age_before_expire
            if track_age > max_track_age * 0.8:  # 80% of max age = "about to expire"
                score += 80
                reason = "about_to_expire"
            
            # Rule 3: Watchlist/suspicious (very high)
            if track.is_watchlist or track.watchlist_suspicion > 0.5:
                score += 120
                reason = "watchlist_priority"
            
            # Rule 4: Fairness: not processed in X seconds (medium)
            last_process = self.track_last_process_time.get(track.id, current_time - 10)
            time_since_process = current_time - last_process
            starvation_threshold = self.config.scheduler.max_seconds_without_process  # e.g., 5 sec
            if time_since_process > starvation_threshold:
                score += 50
                reason = "fairness_starving"
            
            # Rule 5: CONFIRMED_STRONG just updated (lowest)
            if binding_state == 'CONFIRMED_STRONG':
                if track.last_accept_time is not None and \
                   (current_time - track.last_accept_time) < self.config.scheduler.refresh_interval:
                    score += 10
                    reason = "confirmed_strong_maintenance"
            
            priority_scores.append((track.id, score, reason))
        
        # Sort by priority (descending)
        priority_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Select top N within budget
        selected = []
        for track_id, score, reason in priority_scores:
            if len(selected) < int(budget_remaining):
                selected.append(track_id)
                self.track_last_process_time[track_id] = current_time
                self.budget_used_this_window += 1
            else:
                break
        
        return selected
```

**Currently missing:**
- No scheduler exists
- All tracklets treated equally
- No fairness mechanism
- No starvation prevention
- Identity engine gets called for every track every frame (no budget control)

#### D2: Integration Hook (MISSING)

**Current Reality:**
- Main loop iterates all tracklets
- Each gets face processing

**What's Missing (Logic):**

```python
# In core/main_loop.py (LOGIC MISSING):

# Currently probably looks like:
for track in all_tracklets:
    face = extract_face(track, frame)
    if face is not None:
        identity_result = identity_engine.match(embedding)
        # ... update UI ...

# SHOULD LOOK LIKE (with scheduler):
selected_tracklets = scheduler.select(
    tracklets = all_tracklets,
    fps = current_fps,
    binding_states = {t.id: binding_sm.get_state(t.id) for t in all_tracklets}
)

for track in all_tracklets:
    if track.id not in selected_tracklets:
        # This track is not selected this frame
        # Keep it alive in UI, but don't process face
        continue
    
    # This track IS selected
    face = extract_face(track, frame)
    if face is not None:
        # ... process as normal ...
        identity_result = identity_engine.match(embedding)
```

**Currently missing:**
- Scheduler never called
- No selection logic
- No budget enforcement
- All tracks processed regardless of load

---

## ✅ WHAT WAS PROPERLY IMPLEMENTED: Phase E

Phase E exists and works, BUT it's like building a luxury building on a sandy foundation:

```
Phase E: Merge Manager ✅ Built
    ├─ Alias mapping
    ├─ 7-criterion scoring
    └─ Integration in main_loop

PROBLEM: Depends on B, C, D which don't exist
    ├─ B (Evidence Gate): Not filtering garbage before merge
    ├─ C (Binding): Not providing stable identities to merge
    └─ D (Scheduler): Not providing fair face processing budget
```

---

## 🎯 THE EXACT LOGIC THAT NEEDS TO BE BUILT

To complete the robust system from the Robust_PLan.md, here's the EXACT LOGIC needed (in order):

### 1. **Phase A Logic (Enable/Disable + Metrics)**

**Must add:**
```python
# In core/main_loop.py:
if config.governance.evidence_gate_enabled:
    gate_decision, reason = evidence_gate.decide(...)
    if gate_decision != "ACCEPT":
        continue  # Skip identity update
else:
    gate_decision = "ACCEPT"  # No gating

if config.governance.binding_enabled:
    binding_output = binding_sm.update(...)
else:
    binding_output = raw_match

# Track metrics for each phase
metrics.track(phase='gating', decision=gate_decision)
metrics.track(phase='binding', state=binding_output['status'])
```

**Logic gap:** Control flow to enable/disable each phase

---

### 2. **Phase B Logic (Quality Gate)**

**Must add:**
```python
# Compute quality fields on FaceSample
face_sample.yaw_deg = compute_yaw(landmarks)
face_sample.blur_score = compute_blur(patch)
face_sample.brightness = compute_brightness(patch)
face_sample.bbox_size_px = min(width, height)

# Call evidence gate
decision, reason = evidence_gate.decide(
    face_sample=face_sample,
    binding_state=track.binding_state,
    track_age=track.age,
    last_accept_time=track.last_accept_time,
    fps=fps
)

# Respect decision
if decision == "REJECT":
    return None
if decision == "HOLD":
    return ("HOLD", embedding)  # Don't update identity
if decision == "ACCEPT":
    return ("ACCEPT", embedding)  # Normal update
```

**Logic gap:** Quality computation, gating decision, HOLD handling

---

### 3. **Phase C Logic (Binding State Machine)**

**Must add:**
```python
# Buffer last N samples
evidence_buffer.append((person_id, score, margin, quality, timestamp))
evidence_buffer = [e for e in evidence_buffer if not too_old(e)]

# Confirmation logic
if len(evidence_buffer) >= K:
    if all_agree_on_person(evidence_buffer):
        if avg_score > threshold and avg_margin > margin_threshold:
            status = "CONFIRMED_WEAK"

# Anti-lock-in logic
if status == "CONFIRMED" and contradiction_counter > threshold:
    status = "SWITCH_PENDING"

# Switching logic
if status == "SWITCH_PENDING" and sustained_evidence(new_person):
    status = "CONFIRMED_WEAK"
    person_id = new_person
```

**Logic gap:** Buffering, confirmation, anti-lock-in, switching

---

### 4. **Phase D Logic (Scheduler)**

**Must add:**
```python
# Priority scoring
for track in tracklets:
    if binding_state == "UNKNOWN":
        priority = 100
    elif about_to_expire(track):
        priority = 80
    elif time_since_process > fairness_threshold:
        priority = 50
    elif binding_state == "CONFIRMED_STRONG":
        priority = 10

# Select by priority within budget
selected = sorted_by_priority[:budget_remaining]

# Only process selected tracks
for track in selected:
    face_process(track)
```

**Logic gap:** Priority computation, budget enforcement, fairness

---

### 5. **Phase E (Already done)**

Phase E works but depends on B, C, D to be truly robust.

---

## 📊 DEPENDENCY CHAIN

```
Phase A (Config + Metrics)
    ↓
Phase B (Evidence Gate)
    ↓ provides: quality-filtered samples
    ↓
Phase C (Binding State Machine)
    ↓ provides: stable identity decisions
    ↓
Phase D (Scheduler)
    ↓ provides: fair face processing
    ↓
Phase E (Merge Manager)
    ↓ all logic depends on A-D working
```

**Current state:**
```
Phase A: 20% done (config exists, metrics partial)
Phase B: 0% done (logic missing entirely)
Phase C: 0% done (logic missing entirely)
Phase D: 0% done (logic missing entirely)
Phase E: 100% done ✅
```

**Result:** E exists but is unstable without B, C, D.

---

## 🔧 CONCRETE MISSING CODE PATTERNS

### Pattern 1: Evidence Gate Call (MISSING everywhere)

```python
# This pattern should be in face/route.py but DOESN'T EXIST:
gate_decision, reason = evidence_gate.decide(
    face_sample=sample,
    binding_state=get_track_state(track_id),
    ...
)
if gate_decision != "ACCEPT":
    return  # Don't update identity
```

### Pattern 2: Binding Update (MISSING everywhere)

```python
# This pattern should be in main_loop.py but DOESN'T EXIST:
binding_output = binding_sm.update(
    track_id=track_id,
    match_result=identity_engine.match(...),
    face_sample=sample,
    current_time=time.time()
)
# Use binding_output['person_id'], not raw match
```

### Pattern 3: Scheduler Selection (MISSING everywhere)

```python
# This pattern should be in main_loop.py but DOESN'T EXIST:
selected = scheduler.select(tracklets, fps, binding_states)
for track in tracklets:
    if track.id not in selected:
        continue
    # Process this track
```

### Pattern 4: Phase Enable/Disable (MISSING everywhere)

```python
# This pattern should be in main_loop.py but DOESN'T EXIST:
if config.governance.binding_enabled:
    identity = binding_sm.get(track_id)
else:
    identity = raw_match
```

---

## 🎯 SUMMARY: EXACT LOGIC MISSING

| Phase | Status | Logic Gap |
|-------|--------|-----------|
| A | 20% | Config governance section exists, metrics infrastructure partial |
| B | 0% | Evidence gate never called, quality fields not computed, HOLD not handled |
| C | 0% | Binding state machine never called, no buffer, no margin, no anti-lock-in |
| D | 0% | Scheduler never called, no budget, no fairness, all tracks processed equally |
| E | 100% | Done, but depends on A-D |

**Bottom line:** Phase E code exists but the system is **fundamentally incomplete** without the control logic from Phases A-D wired in.

To make the system robust as designed in Robust_PLan.md, you need to implement the MISSING LOGIC of Phases A-D, which means:

1. **Core logic**: Evidence gate, binding state machine, scheduler decision logic
2. **Integration hooks**: Calling these at the right places in main_loop, face/route, identity_engine
3. **Data flow**: Quality fields → gating → binding → scheduling → merging
4. **Control flow**: Enable/disable switches, fallback logic, state transitions
5. **Metrics**: Tracking decisions from all phases for observability
