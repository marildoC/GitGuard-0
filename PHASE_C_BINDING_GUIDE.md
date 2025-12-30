# Phase C: Binding State Machine Implementation Guide

## Overview

Phase C implements a **Binding State Machine** that converts noisy per-frame gallery match evidence into stable, margin-enforced identity decisions. This prevents false positives (locks) and flip-flopping between identities.

### Key Innovation

Rather than accepting individual frame evidence, the binding engine:
1. **Accumulates evidence** over multiple frames
2. **Enforces margin rules** (best score must exceed 2nd best by threshold)
3. **Detects contradictions** (anti-lock-in: if evidence contradicts binding)
4. **Controls switching** (new identity requires margin advantage)

### State Machine States

```
UNKNOWN
   ↓ (3+ strong samples in window)
PENDING
   ↓ (3+ maintained samples)
CONFIRMED_WEAK → CONFIRMED_STRONG
   ↓              ↓
   (contradictions accumulate)
   ↓
SWITCH_PENDING (alternative identity building evidence)
   ↓ (4+ sustained samples of new identity)
CONFIRMED_STRONG (switched identity)
   ↓
   PENDING (contradictions → downgrade)
```

## Architecture

### Key Components

#### 1. **BindingManager** (identity/binding.py)
- Main state machine engine
- Manages per-track binding state
- Applies state transition rules
- Records metrics

#### 2. **TrackBindingState** (identity/binding.py)
- Per-track state data
- Evidence buffer (recent samples)
- Contradiction counter
- Margin tracking

#### 3. **Integration Point** (identity/identity_engine.py)
- Called after strong gallery match is confirmed
- Processes evidence through binding state machine
- May override identity_id or confidence based on binding state

## Configuration

Binding is controlled via `config/default.yaml` in `governance.binding` section:

```yaml
governance:
  binding:
    enabled: true
    confirmation:
      min_samples_strong: 3        # Samples needed to lock
      min_samples_weak: 5
      window_seconds: 3.0          # Evidence accumulation window
      min_avg_score: 0.75          # Min average gallery score
      min_avg_margin: 0.08         # Min evidence margin (score - 2nd_best)
      min_quality_for_strong: 0.60 # Face quality threshold
    
    switching:
      min_sustained_samples: 4     # Samples needed for alternative
      margin_advantage: 0.12       # How much better alt must be
      window_seconds: 2.0
      timeout_seconds: 5.0         # Max time in SWITCH_PENDING
    
    contradiction:
      threshold: 0.15              # Score below this = contradiction
      counter_max: 5               # Downgrade after this many
      decay_per_second: 1.0        # Counter decay rate
```

## State Transitions

### UNKNOWN → PENDING

**Triggered when:**
- 3+ strong samples arrive within 3-second window
- All samples match same person
- All have score ≥ 0.75
- All have margin ≥ 0.08
- All have quality ≥ 0.60

**Action:**
- Lock identity to proposed person
- Set confidence to 0.5

### PENDING → CONFIRMED_WEAK

**Triggered when:**
- 3+ maintained samples of same person
- Within 3-second confirmation window
- Average score ≥ 0.75
- Average margin ≥ 0.08

**Action:**
- Upgrade state
- Set confidence to 0.75

### CONFIRMED_WEAK → CONFIRMED_STRONG

**Triggered when:**
- Sustained strong evidence continues
- 3+ recent samples all matching
- Average score ≥ 0.75

**Action:**
- Upgrade to strongest binding
- Set confidence to 0.95

### CONFIRMED → SWITCH_PENDING

**Triggered when:**
- Alternative person gets sustained evidence
- 4+ samples of alternative within 2-second window
- Alternative margin advantage ≥ 0.12

**Action:**
- Enter switch mode
- Store pending_person_id
- Monitor for completion

### SWITCH_PENDING → CONFIRMED_STRONG

**Triggered when:**
- 4+ sustained samples of pending person
- All within 2-second window

**Action:**
- Complete switch
- Update person_id to new identity
- Reset contradiction counter

### Downgrade: CONFIRMED → PENDING → UNKNOWN

**Triggered by:**
- Contradiction detection (binding weakens evidence)
- Contradiction counter exceeds threshold
- Timeout in SWITCH_PENDING

**Action:**
- Drop to lower state
- Lose identity binding
- Allow re-binding to correct identity

## Anti-Lock-In Mechanism

**Problem:** Face similarity (e.g., twins) can cause false locks.

**Solution:** Contradiction detection
- Track accumulated evidence contradicting current binding
- If current person gets low score repeatedly → contradiction counter++
- When counter exceeds threshold (5) → downgrade state
- Allows system to break spurious locks and rebind

**Example:**
```
Frame 1-3: Strong match to Alice (0.9 score)
  → LOCKED to Alice

Frame 4-6: Alice gets low scores (0.15)
  → contradiction_counter = 1, 2, 3

Frame 7-9: Bob appears with strong scores (0.85)
  → Enter SWITCH_PENDING

Frame 10-12: Bob scores drop but Alice improves (0.78)
  → Switch attempt timeout, back to Alice

Frame 13-15: Alice scores collapse (0.08, 0.12, 0.14)
  → contradiction_counter = 4, 5, 6
  → DOWNGRADE to PENDING
  → Now accepts any high-quality match (even if contradicts)
```

## Integration with Identity Engine

### Call Point

In `identity/identity_engine.py`, after strong gallery match confirmed:

```python
# After creating the initial decision with gallery match
decision = IdentityDecision(...)

# PHASE C: Apply binding state machine
binding_result = self.binding_manager.process_evidence(
    track_id=track_id,
    person_id=res.person_id,
    score=conf,
    second_best_score=...,
    quality=q,
    timestamp=ts,
)

# May override decision based on binding state
if binding_result.person_id is not None:
    decision.identity_id = binding_result.person_id
    decision.confidence = binding_result.confidence
```

### Output in Decision Reason

The binding state is logged in `IdentityDecision.reason`:

```
face_match_strong:person_123:d=0.08:q=0.88:s=0.95:mode=refresh:samples=3:avg_q=0.85
binding=CONFIRMED_STRONG:PENDING → CONFIRMED_STRONG
```

## Metrics & Monitoring

BindingManager records:
- State count per track
- Confirmation events (PENDING → CONFIRMED_WEAK/STRONG)
- Downgrades (violations of binding)
- Switch attempts (successes/failures)
- Anti-lock triggers

Access via `metrics_collector.metrics`:
```python
metrics.binding_state_counts['CONFIRMED_STRONG']  # Number of tracks
metrics.binding_confirmations                      # Total confirmations
metrics.binding_downgrades                         # Total downgrades
metrics.binding_switches_success                   # Switches completed
metrics.binding_anti_lock_triggers                 # Anti-lock activations
```

## Testing Strategy

### Unit Tests (identity/tests/test_binding.py)

1. **State transitions:**
   - UNKNOWN → PENDING (3 strong samples)
   - PENDING → CONFIRMED_WEAK (sustained samples)
   - CONFIRMED_WEAK → CONFIRMED_STRONG
   - CONFIRMED → SWITCH_PENDING → CONFIRMED_STRONG

2. **Margin enforcement:**
   - Reject samples with margin < min_avg_margin
   - Require margin advantage for switching
   - Track margin values in evidence buffer

3. **Anti-lock-in:**
   - Contradiction counter increments on low scores
   - Downgrade triggered after counter_max
   - Counter decay works over time

4. **Configuration:**
   - Thresholds properly applied
   - Custom configs override defaults
   - Safe fallback on config errors

### Integration Tests (perception/tests/test_identity_integration.py)

1. **End-to-end flow:**
   - Gallery match → binding application → decision override
   - Identity stable across frames
   - Switching only on strong evidence

2. **Pipeline compatibility:**
   - Binding doesn't crash on bad inputs
   - Works with multiview engine
   - Metrics properly recorded

3. **Real scenario tests:**
   - Person appears, leaves, reappears (rebinding)
   - Two similar people (switching)
   - Face quality changes (margin handling)

## Diagnostics

### Log Messages

Enable debug logging to see binding state machine:
```python
logging.getLogger('identity.binding').setLevel(logging.DEBUG)
```

Output examples:
```
Track 123 UNKNOWN → PENDING person_456 (margin=0.092)
Track 123 PENDING → CONFIRMED_WEAK person_456 (conf=0.75)
Track 123 SWITCH_PENDING → person_789 (margin=0.135)
Track 123 downgraded due to contradiction
Track 123 switch attempt timed out
```

### Reason String Analysis

In each IdentityDecision.reason:
```
binding=CONFIRMED_WEAK:PENDING → CONFIRMED_WEAK
```

Parse this to understand:
- Current binding state
- Previous state (if just transitioned)
- Evidence accumulation details

### Metrics Dashboard

Track binding health:
```python
def check_binding_health(metrics):
    total_tracks = sum(metrics.binding_state_counts.values())
    confirmed = (
        metrics.binding_state_counts.get('CONFIRMED_STRONG', 0) +
        metrics.binding_state_counts.get('CONFIRMED_WEAK', 0)
    )
    locked_ratio = confirmed / total_tracks if total_tracks > 0 else 0
    
    print(f"Locked tracks: {locked_ratio:.1%}")
    print(f"Anti-lock triggers: {metrics.binding_anti_lock_triggers}")
    print(f"Switch attempts: {metrics.binding_switches_success}")
```

## Troubleshooting

### Issue: Binding stuck in UNKNOWN

**Causes:**
- Evidence below thresholds (low quality/score/margin)
- Too few samples
- Evidence window expired

**Solution:**
- Check min_avg_score, min_avg_margin thresholds
- Verify face quality ≥ 0.60
- Reduce evidence window_seconds or increase samples

### Issue: Excessive downgrades

**Causes:**
- Contradiction counter threshold too low
- Face similarity between identities

**Solution:**
- Increase contradiction.counter_max
- Increase margin_advantage threshold for switching
- Verify gallery matches aren't ambiguous

### Issue: Too slow to lock

**Causes:**
- min_samples_strong too high
- window_seconds too short
- Quality/score/margin thresholds too strict

**Solution:**
- Reduce min_samples_strong to 2
- Increase window_seconds to 4-5
- Lower min_avg_score threshold

### Issue: Binding disabled (BYPASS state)

**Cause:**
- Config not loaded properly
- BindingManager init failed

**Solution:**
- Check config/default.yaml exists
- Verify governance.binding.enabled = true
- Check logs for BindingManager init errors

## Performance Impact

- **Memory:** ~0.5KB per active track (evidence buffer + state)
- **CPU:** ~0.1ms per track (state machine transitions)
- **Latency:** 0ms (applied within identity_engine.decide())

Binding is negligible overhead compared to gallery search.

## Future Enhancements

1. **Temporal weighting:** Decay old evidence within buffer
2. **Pose-aware binding:** Separate state per pose angle
3. **Multiview binding:** Aggregate evidence across views
4. **Adaptive thresholds:** Learn from False Positives/Negatives
5. **Hierarchical binding:** Family-level binding for grouped IDs
