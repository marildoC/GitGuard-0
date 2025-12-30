# tests/QUICK_START.md
# Quick Start: Running Tests

## 1. Setup (First Time)

```powershell
# Navigate to tests directory
cd tests

# Install testing dependencies
pip install -r requirements-test.txt
```

## 2. Run All Tests

```powershell
# Option A: Comprehensive report
python test_runner.py

# Option B: Pytest directly
python -m pytest -v

# Option C: Individual phase
python test_phase_a_config.py
```

## 3. Run Specific Tests

```powershell
# Phase A only
python -m pytest test_phase_a_config.py -v

# Phase B only
python -m pytest test_phase_b_evidence_gate.py -v

# Single test class
python -m pytest test_phase_c_binding.py::TestPhaseCStateTransitions -v

# Single test method
python -m pytest test_phase_e_merge_manager.py::TestPhaseE_IdenticalEmbeddings::test_identical_embeddings_merge -v
```

## 4. Filter by Type

```powershell
# All performance tests
python -m pytest test_perf_load.py -v

# All E2E tests
python -m pytest test_e2e_integration.py -v

# All stress tests
python -m pytest test_stress.py -v
```

## 5. Expected Output

### Success Output
```
✅ Phase A: Config Governance
  ✅ test_config_loads PASSED
  ✅ test_governance_section_exists PASSED
  ✅ test_governance_flags_exist PASSED
  
Total: 3 passed
```

### Failure Example
```
❌ Phase E: Merge Manager
  ❌ test_identical_embeddings_merge FAILED
  
AssertionError: Identical embeddings should merge
```

## 6. Troubleshooting

### ImportError: cannot import name 'X'

```powershell
# Make sure you're in tests directory
cd tests

# Verify conftest.py exists
dir conftest.py

# Try installing again
pip install -r requirements-test.txt
```

### No module named 'pytest'

```powershell
# Install pytest
pip install pytest

# Verify installation
pytest --version
```

### Config not found

```powershell
# Verify config exists
dir ../config/default.yaml

# If not, check project root
dir ../config/
```

## 7. Output Files

Tests create report files:

```
tests/test_report_20241224_120000.json
```

View the report:

```powershell
# Open in text editor
notepad test_report_*.json

# Or use Python
python -m json.tool test_report_*.json
```

## 8. Run with Verbose Output

```powershell
# Very detailed output
python -m pytest test_phase_a_config.py -vv -s

# Show print statements
python -m pytest test_phase_a_config.py -s

# Show local variables on failure
python -m pytest test_phase_a_config.py -l
```

## 9. Run Specific Test Patterns

```powershell
# Tests matching pattern
python -m pytest -k "high_quality" -v

# Tests matching multiple patterns
python -m pytest -k "accept or reject" -v

# Skip certain tests
python -m pytest -k "not stress" -v
```

## 10. Continuous Test Execution

```powershell
# Watch for changes and re-run (requires pytest-watch)
pip install pytest-watch
ptw

# Run tests in parallel (requires pytest-xdist)
python -m pytest -n auto -v
```

## Command Cheat Sheet

```powershell
# Most common commands

# Run all
python test_runner.py

# Run phase
python -m pytest test_phase_a_config.py -v

# Run one test
python -m pytest test_phase_a_config.py::TestPhaseAConfigLoad::test_config_loads -v

# Run with output
python -m pytest test_phase_a_config.py -v -s

# Run performance only
python -m pytest test_perf_load.py -v

# Run stress only
python -m pytest test_stress.py -v

# Show passed/failed summary
python -m pytest --tb=short -v

# Stop on first failure
python -m pytest -x -v

# Show 10 slowest tests
python -m pytest --durations=10 -v
```

## Next Steps

1. **Run Phase A tests** to verify config is working
2. **Run Phase B-E tests** to verify each phase
3. **Run E2E tests** to verify phases work together
4. **Run performance tests** to measure throughput
5. **Run stress tests** to verify robustness
6. **Review results** and adjust config as needed

## Getting Help

- See `README_TESTING.md` for detailed test documentation
- See `VERIFICATION_TESTING_PLAN.md` for test descriptions and expected behavior
- Check individual test files for specific test logic

---

**Happy Testing! 🚀**
