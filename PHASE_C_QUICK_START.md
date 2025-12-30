# Phase C Quick Start Guide

## What is Phase C?

Phase C adds a **Binding State Machine** to the identity engine that prevents identity flip-flopping and false positives by enforcing evidence margins and detecting contradictions.

## How It Works (30 second version)

1. Gallery match arrives with score 0.95
2. Binding engine asks: "Do I have 3+ similar-confidence samples recently?"
3. If YES → Lock to this identity (PENDING state)
4. If more evidence confirms → Move to CONFIRMED (stronger lock)
5. If evidence contradicts → Count contradictions
6. Too many contradictions → Break lock, allow rebinding

## Configuration

Phase C is automatically enabled in `config/default.yaml`. To tune it:

```yaml
governance:
  binding:
    enabled: true  # Set to false to disable entirely
    
    confirmation:
      min_samples_strong: 3        # Need 3 samples to lock
      min_avg_score: 0.75          # Minimum gallery score
      min_avg_margin: 0.08         # Min gap from 2nd best match
      min_quality_for_strong: 0.60 # Face quality threshold
      window_seconds: 3.0          # Evidence time window
    
    switching:
      min_sustained_samples: 4     # Samples needed to switch
      margin_advantage: 0.12       # How much better new must be
      timeout_seconds: 5.0         # Max time to prove switch
    
    contradiction:
      threshold: 0.15              # Score below this = contradiction
      counter_max: 5               # Downgrades after this many
```

## Viewing Binding Decisions

Each identity decision includes binding information in the reason string:

```
face_match_strong:person_alice:d=0.08:q=0.88:s=0.95:mode=refresh
binding=CONFIRMED_WEAK:state_unchanged
```

Parse this as:
- `face_match_strong` = Gallery matched
- `person_alice` = Person ID
- `binding=CONFIRMED_WEAK` = Binding state
- `state_unchanged` = No state transition this frame

## Enable Debug Logging

To see detailed binding state machine transitions:

```python
import logging
logging.getLogger('identity.binding').setLevel(logging.DEBUG)
```

Output will show:
```
Track 123 UNKNOWN → PENDING person_alice (margin=0.092)
Track 123 PENDING → CONFIRMED_WEAK person_alice (conf=0.75)
Track 123 CONFIRMED_WEAK → CONFIRMED_STRONG
```

## Common Scenarios

### Scenario 1: First Time Person Appears

```
Frame 1-3: Alice shows face, gallery score 0.92
  → Binding accumulates evidence
  → Frame 3: PENDING state reached
  
Frame 4-5: More evidence, score 0.90-0.95
  → Frame 5: CONFIRMED_WEAK state reached
  
Frame 6+: Continues to receive confirmation
  → CONFIRMED_STRONG state (stable lock)
```

### Scenario 2: Similar-Looking Person

```
Frame 1-3: Lock to Alice (0.92 score)
  → CONFIRMED_WEAK
  
Frame 4-6: Bob (similar face) gets 0.15-0.20 score
  → Binding counts contradictions (3)
  
Frame 7: Bob gets 0.90 score + sustained evidence
  → Would normally switch, but needs margin advantage
  → Still locked to Alice
```

### Scenario 3: Face Quality Drops

```
Frame 1-3: Alice locked, quality 0.80
  → CONFIRMED_WEAK
  
Frame 4-5: Quality drops to 0.55 (still above min 0.50)
  → Binding monitors margin
  → If no contradictions, stays CONFIRMED
  
Frame 6+: Quality recovers
  → Back to normal confidence
```

### Scenario 4: Sustained Contradiction

```
Frame 1-3: Lock to Alice
  → CONFIRMED_WEAK
  
Frame 4-9: Alice consistently gets score 0.10-0.15
  → contradiction_counter reaches 5
  
Frame 10: Binding downgrades
  → CONFIRMED_WEAK → PENDING (confidence drops)
  
Frame 11+: System can now rebind to correct person
```

## Performance Impact

- **CPU**: <0.1ms per frame (negligible)
- **Memory**: ~0.5KB per track (evidence buffer)
- **Latency**: 0ms added to decision pipeline

## Troubleshooting

### Identity keeps switching

**Problem**: Person labeled as different IDs frequently

**Solution**: Increase `margin_advantage` threshold
- From 0.12 to 0.15 (requires 15% confidence gap to switch)

### Identity won't lock

**Problem**: Person stays in PENDING state too long

**Solution**: Lower `min_samples_strong` or `min_avg_score`
- Try min_samples_strong: 2 (instead of 3)
- Try min_avg_score: 0.70 (instead of 0.75)

### False locks (wrong person ID stuck)

**Problem**: Person locked to wrong identity despite different face

**Solution**: Lower `contradiction.counter_max`
- From 5 to 3 (breaks lock faster)
- OR increase `threshold` from 0.15 to 0.20

### System too conservative

**Problem**: Too many "UNKNOWN" decisions

**Solution**: Lower `min_avg_margin`
- From 0.08 to 0.05 (allows weaker margin evidence)

## Testing

### Run Validation
```bash
python scripts/validate_phase_c.py
```

### Run Unit Tests
```bash
python -m pytest identity/tests/test_binding.py -v
```

### Run Integration Tests
```bash
python -m pytest identity/tests/test_identity_integration.py -v
```

## Disabling Phase C

If you need to disable binding:

```yaml
governance:
  binding:
    enabled: false
```

This makes binding return "BYPASS" state and fall back to original logic. No data is affected.

## Understanding the State Machine

```
UNKNOWN
  └─ 3+ strong samples in window
     └─ PENDING
        └─ 3+ sustained samples
           └─ CONFIRMED_WEAK
              └─ continued strong evidence
                 └─ CONFIRMED_STRONG

All states can downgrade if contradictions accumulate

CONFIRMED states can switch to new person if:
  - New person has score > current + margin_advantage
  - AND sustained for min_sustained_samples frames
  - → SWITCH_PENDING (temporary state)
     → CONFIRMED_STRONG (new person) if sustained
        OR fall back to previous person if timeout
```

## Key Concepts

**Margin**: Score gap from best match to second-best
- Example: best=0.92, second=0.82 → margin=0.10
- Binding requires min 0.08 margin for strong evidence

**Evidence Window**: Recent time window for accumulation
- Default 3 seconds
- All evidence older than window is discarded
- Prevents stale evidence from affecting current decisions

**Contradiction**: Current person gets low score
- Tracked by contradiction_counter
- Increments if person gets score below threshold (0.15)
- Decrements if person gets high score
- When counter exceeds max (5), state downgrades

**Switch Requirement**: New person needs margin advantage
- Current person confidence + margin_advantage = threshold
- New person must exceed this threshold
- Prevents switching to slight improvements
- Requires 4+ samples sustained for 2+ seconds

## Advanced: Custom Tuning

For your specific use case, adjust these:

| Scenario | Parameter | Adjust | Rationale |
|----------|-----------|--------|-----------|
| High false positives | `counter_max` | 3 | Break bad locks faster |
| High identity switching | `margin_advantage` | 0.15 | Require stronger proof |
| Slow to lock | `min_samples_strong` | 2 | Lock faster |
| Quick to switch | `margin_advantage` | 0.20 | Require more proof |
| Quality issues | `min_quality_for_strong` | 0.50 | Lower requirement |
| Crowded scenes | `window_seconds` | 2.0 | Shorter window |

## Getting Help

See full documentation in:
- `PHASE_C_BINDING_GUIDE.md` - Complete reference
- `PHASE_C_IMPLEMENTATION_COMPLETE.md` - Implementation details
- `identity/binding.py` - Source code with comments
