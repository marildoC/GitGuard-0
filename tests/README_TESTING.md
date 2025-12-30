# tests/README_TESTING.md
# GaitGuard Verification & Testing Suite

Complete testing framework for validating all 5 phases of the system.

## Quick Start

```bash
# Run all tests
python test_runner.py

# Run specific test suite
python -m pytest test_phase_a_config.py -v

# Run specific test
python -m pytest test_phase_a_config.py::TestPhaseAConfigLoad::test_config_loads -v

# Run with markers
python -m pytest -m phase_a -v
python -m pytest -m performance -v
python -m pytest -m stress -v
```

## Test Organization

### Phase Tests (Unit Level)

1. **test_phase_a_config.py**
   - Config loading and parsing
   - Governance section existence
   - Flag accessibility and types
   - Config integrity

2. **test_phase_b_evidence_gate.py**
   - Evidence gate initialization
   - Quality filtering (ACCEPT/HOLD/REJECT)
   - High-quality face acceptance
   - Low-quality face rejection
   - Blur and brightness checks
   - Pose/geometric checks
   - Scale/size checks
   - Marginal quality handling

3. **test_phase_c_binding.py**
   - Binding state machine basics
   - State transitions (UNKNOWN → PENDING → CONFIRMED)
   - Consecutive sample requirements
   - Flip-flop prevention
   - High-quality sample preference
   - Multiple track independence
   - Error handling

4. **test_phase_d_scheduler.py**
   - Scheduler initialization
   - Schedule computation
   - Track selection
   - Graceful degradation under load
   - Priority ordering
   - FPS monitoring
   - Error handling (empty tracks, zero FPS)

5. **test_phase_e_merge_manager.py**
   - Merge manager initialization
   - Identical embedding merging
   - Similar embedding handling
   - Different embedding rejection
   - Confidence thresholds
   - Simultaneous track prevention (handoff-only)
   - Time-exclusive merge candidates
   - 7-criterion scoring
   - Alias mapping
   - **CRITICAL: No false merges**

### Integration Tests (End-to-End)

**test_e2e_integration.py**
- Single person, high-quality scenario
- Quality variation handling
- Multiple independent tracks
- Identity switching
- Handoff merge scenario
- **CRITICAL: No false merges**

### Performance Tests

**test_perf_load.py**
- Evidence gate throughput (>1000 samples/sec)
- Binding manager throughput (>5000 ops/sec)
- Scheduler throughput (>100 schedules/sec)
- Load handling (many tracks)
- FPS maintenance under load
- Memory characteristics
- Latency profiling (P95)

### Stress Tests

**test_stress.py**
- Rapid track lifecycle (100 tracks)
- Bursty quality variation
- Persistent low quality
- Extreme pose angles
- Extreme scheduler load (100+ tracks, 5 FPS)
- Many merge evaluations
- Graceful degradation
- Conflicting identity evidence
- Error recovery

## Test Fixtures (conftest.py)

Available fixtures for all tests:

```python
# Configuration
test_config          # Loaded config object
metrics_collector    # Metrics collection

# Data generation
test_face_evidence   # Create test face evidence
test_tracklet        # Create test tracklet

# Utilities
logger              # Logger for test output
numpy_seed          # Set reproducible random seed
reset_metrics       # Reset metrics before test

# Monitoring
timer               # Time operations (ms precision)
fps_monitor         # Monitor FPS
memory_tracker      # Track memory usage

# Test results
test_results        # Collect test results
```

## Running Tests

### Method 1: Test Runner (Comprehensive)

```bash
cd tests
python test_runner.py
```

This runs all test suites and generates a JSON report.

### Method 2: Pytest (Individual)

```bash
# All tests
python -m pytest -v

# Specific phase
python -m pytest test_phase_a_config.py -v

# Specific test class
python -m pytest test_phase_b_evidence_gate.py::TestPhaseB_HighQualityAccepted -v

# With markers
python -m pytest -m phase_a -v
python -m pytest -m performance -v
```

### Method 3: Direct Execution

```bash
# Run test file directly
python test_phase_a_config.py

# With verbose output
python test_phase_a_config.py -v
```

## Test Markers

```bash
# By phase
pytest -m phase_a
pytest -m phase_b
pytest -m phase_c
pytest -m phase_d
pytest -m phase_e

# By type
pytest -m e2e
pytest -m performance
pytest -m stress
```

## Expected Results

### Success Criteria by Phase

**Phase A: Config**
- ✅ Config loads without errors
- ✅ governance section exists
- ✅ All 5 flags are accessible
- ✅ All flags are boolean type

**Phase B: Evidence Gate**
- ✅ High quality → ACCEPTED (>80% of high-quality samples)
- ✅ Blurry/dark → REJECTED or HELD
- ✅ Marginal quality → HELD
- ✅ Throughput: >1000 samples/sec

**Phase C: Binding**
- ✅ New tracks start UNKNOWN
- ✅ Transitions to PENDING after evidence
- ✅ Requires N consecutive samples for CONFIRMED
- ✅ Prevents flip-flop to lower confidence
- ✅ Throughput: >5000 ops/sec

**Phase D: Scheduler**
- ✅ Initializes from config
- ✅ Computes schedules for active tracks
- ✅ Degrades gracefully under high load
- ✅ Maintains minimum FPS (>15 FPS with 100 tracks)
- ✅ Throughput: >100 schedules/sec

**Phase E: Merge Manager**
- ✅ **CRITICAL: No false merges**
- ✅ Identical embeddings → MERGE
- ✅ Different embeddings → NO MERGE
- ✅ Low confidence → NO MERGE
- ✅ Simultaneous tracks → NO MERGE (handoff-only)
- ✅ Time-exclusive → MERGE CANDIDATE

**Performance**
- ✅ Phase B: >1000 samples/sec
- ✅ Phase C: >5000 ops/sec
- ✅ Phase D: >100 schedules/sec
- ✅ FPS: >30 FPS at 30 tracks, >15 FPS at 100 tracks
- ✅ P95 latency: <50ms

**Stress**
- ✅ Handles 100+ tracks
- ✅ Memory growth: <20MB for 100 track lifecycle
- ✅ Handles conflicting evidence
- ✅ Recovers from invalid inputs

## Troubleshooting

### Test Import Errors

```bash
# Make sure you're in tests directory
cd tests

# Install pytest if not present
pip install pytest

# Check conftest.py is present
ls conftest.py
```

### Import Path Issues

Conftest.py automatically adds parent directory to path. If you still get import errors:

```python
# In test file, add:
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
```

### Config Not Found

Tests expect config at `../config/default.yaml`. Verify:

```bash
ls ../config/default.yaml
```

### Fixture Errors

If fixtures are not available, check:

```bash
# conftest.py must be in tests directory
ls conftest.py

# Restart pytest
python -m pytest --co  # List all tests (should work if conftest is found)
```

## Adding New Tests

To add a new test:

1. Create `test_<phase>_<feature>.py` in tests directory
2. Import fixtures from conftest
3. Write test class(es) with `test_*` methods
4. Add pytest marker: `@pytest.mark.<phase>`
5. Run: `python -m pytest test_<phase>_<feature>.py -v`

Example:

```python
# tests/test_my_feature.py
import pytest

class TestMyFeature:
    def test_basic_functionality(self, logger):
        """Test description"""
        logger.info("✅ Test passed")
        assert True
    
    def test_with_config(self, test_config, logger):
        """Test that uses config"""
        assert test_config is not None
        logger.info("✅ Config available")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
```

## Continuous Integration

To integrate into CI/CD:

```bash
# Run all tests
python tests/test_runner.py

# Exit code: 0 = all pass, 1 = any failed

# Check specific phase
python -m pytest tests/test_phase_e_merge_manager.py -v

# Generate coverage
pip install pytest-cov
python -m pytest tests/ --cov --cov-report=html
```

## Metrics & Reporting

Test runner generates JSON report:

```
tests/test_report_20241224_120000.json
```

Contains:
- Start/end times
- Total passed/failed/skipped
- Per-suite results
- Return codes

## Questions?

Refer to VERIFICATION_TESTING_PLAN.md for detailed test descriptions.
