# PRODUCTION CONFIGURATION & DEPLOYMENT GUIDE
**GaitGuard 5-Phase Identity System**  
**Quick Reference for Deployment & Operations**

---

## ⚡ QUICK START (Minimum Setup)

### 1. Navigate to Workspace Root
```powershell
cd "C:\Users\ildi\Desktop\GaitGuard - 2o"
```

### 2. Activate Environment
```powershell
# If using venv
.\.venv310\Scripts\Activate.ps1

# Or conda
conda activate gaitguard
```

### 3. Run Tests (Verify System)
```powershell
cd tests
python test_runner.py
# Expected: 86/86 PASS ✅
```

### 4. Run Main Pipeline
```powershell
# From workspace root
python -m core.main_loop
# System will start processing video from camera
# Press ESC to exit
```

---

## 🔧 CONFIGURATION CHECKLIST

### Status Check 1: Governance Configuration
```bash
# Check current config
type config\default.yaml | findstr -A 5 "scheduler:"
type config\default.yaml | findstr -A 5 "merge:"
```

**Current Status**: ⚠️ May show warnings about scheduler/merge config

**What to Look For**:
```yaml
governance:
  scheduler:
    enabled: true
    # Should be dict, not null or string
  merge:
    enabled: true
    # Should be dict, not null or string
```

**If Missing**: These phases will be disabled (non-critical)

---

### Status Check 2: Face Gallery Encryption
```bash
echo %GAITGUARD_FACE_KEY%
# If blank: key not set (expected in development)
```

**Current Status**: ℹ️ Key not set (normal for discovery mode)

**For Enrolled Faces** (if you have face_gallery.enc):
```powershell
# Set environment variable
$env:GAITGUARD_FACE_KEY = "your-strong-random-key-here"

# Then run
python -m core.main_loop
```

**Generate a Key** (Windows):
```powershell
# Using Python
python -c "import secrets; print(secrets.token_hex(32))"

# Save output and set it
$env:GAITGUARD_FACE_KEY = "<output-from-above>"
```

---

### Status Check 3: GPU Availability
```powershell
# Check GPU during startup
python -m core.main_loop
# Look for: [INFO] gaitguard.device: Using CUDA device: ...
```

**Expected Output**:
```
[INFO] gaitguard.device: Using CUDA device: NVIDIA GeForce RTX 3050 6GB Laptop GPU
[INFO] gaitguard.device: GPU Memory Capacity: 6.44 GB
[INFO] gaitguard.main: Runtime device=cuda | half=True
```

**If GPU Not Found**: System falls back to CPU (slower but functional)

---

## 📊 PERFORMANCE TUNING

### Current Performance
```
- FPS: 8.0 (stable)
- GPU Memory: 0.05GB / 6.44GB (0.8%)
- Latency: ~125ms per frame
- Throughput: 3000+ binding ops/sec
```

### To Increase FPS (Target 12-15 FPS)

#### Option 1: Use Faster YOLO Model
**Current**: `yolo11n.pt` (nano - fastest)  
**Alternative**: Keep as-is (already optimal)

#### Option 2: Reduce Inference Batch Size
Edit [core/main_loop.py](core/main_loop.py):
```python
# Find this line around line 250
perception = Phase1PerceptionEngine(batch_size=1)  # Change from default
```

#### Option 3: Reduce Quality Threshold
Edit [face/config.py](face/config.py):
```python
q_runtime: float = 0.50  # Lower from 0.55 (more lenient)
```
**Trade-off**: More false positives, higher throughput

### To Reduce Latency (Target <100ms)

#### Option 1: Enable GPU Half-Precision
Already enabled ✅
```
[INFO] gaitguard.main: Runtime device=cuda | half=True
```

#### Option 2: Reduce Evidence Buffer Size
Edit [identity/identity_engine_multiview.py](identity/identity_engine_multiview.py):
```python
max_evidence_len: int = 10  # Reduce from 15
```
**Trade-off**: Fewer samples per identity decision

---

## 🛡️ SAFETY CHECKS

### Before Each Deployment

**Checklist**:
```
☐ Run full test suite: python test_runner.py
  Expected: 86/86 PASS

☐ Test startup: python -m core.main_loop
  Expected: [INFO] GaitGuard pipeline started...

☐ Check GPU memory: GPU: 0.05GB/6.44GB (or similar)
  Expected: <2GB normal operation

☐ Monitor frame processing: FPS=8.0+ | tracks visible
  Expected: Stable FPS for 1+ minute

☐ Test ESC shutdown: Press ESC to exit cleanly
  Expected: No errors, clean shutdown
```

---

## 📈 MONITORING METRICS

### What to Track During Operation

```
Every second, look for:
[INFO] gaitguard.main: FPS=X | tracks=Y | alerts=Z | GPU: A/B
```

**Healthy Ranges**:
| Metric | Min | Target | Max |
|--------|-----|--------|-----|
| **FPS** | 1 | 8-15 | 30 |
| **Tracks** | 0 | 5-20 | 50+ |
| **GPU Memory** | 0MB | 500MB | 2GB |
| **GPU Utilization** | 0.1% | 20-40% | 90% |

### Governance Metrics
```
[INFO] core.governance_metrics: Governance Metrics: 
faces=X (accept=A, hold=H, reject=R) | binding: STATES | scheduler: S | merge: M
```

**What's Normal**:
- `faces=0` (no matches) ✅ if no gallery
- `accept=0, hold=0, reject=0` ✅ if no gallery
- `binding: {None: 2}` ✅ all tracks unknown
- `scheduler: 0/0` ⚠️ disabled (can enable)
- `merge: 0/0` ⚠️ disabled (can enable)

---

## ⚙️ CONFIGURATION FILE LOCATIONS

### Core Files to Understand

| File | Purpose | Status |
|------|---------|--------|
| `config/default.yaml` | Main system configuration | ✅ Loaded |
| `core/main_loop.py` | Entry point, pipeline orchestration | ✅ Running |
| `core/config.py` | Config loading & validation | ✅ Working |
| `face/config.py` | Face detection parameters | ✅ Working |
| `identity/identity_engine_multiview.py` | Identity matching logic | ✅ Active |
| `perception/perception_engine.py` | YOLO + tracking | ✅ Active |

### Configuration Structure

```
config/
  default.yaml          ← Main configuration file
    runtime:            ← GPU/CPU settings
    camera:             ← Camera input settings
    paths:              ← Log/data directories
    governance:         ← Safety mechanisms
      evidence_gate:    ← Phase B config
      binding:          ← Phase C config
      scheduler:        ← Phase D config (⚠️)
      merge:            ← Phase E config (⚠️)
    face:               ← Face detection params
    identity:           ← Identity matching params
```

---

## 🐛 TROUBLESHOOTING

### Problem 1: ModuleNotFoundError
```
ModuleNotFoundError: No module named 'core'
```
**Cause**: Running from wrong directory  
**Solution**: 
```powershell
cd "C:\Users\ildi\Desktop\GaitGuard - 2o"  # Workspace root!
python -m core.main_loop
```

---

### Problem 2: CUDA Out of Memory
```
RuntimeError: CUDA out of memory
```
**Cause**: GPU memory exhausted  
**Solution**: 
```powershell
# Reduce batch size in code
# Or reduce max tracks
# Or use CPU fallback
python -m core.main_loop  # Auto-fallback to CPU
```

---

### Problem 3: FPS Dropping
```
[INFO] FPS=1.0 | tracks=50
```
**Cause**: Too many tracks, CPU/GPU overloaded  
**Solution**:
```powershell
# Enable scheduler to manage load
# Or reduce quality threshold
# Or disable non-critical features
```

---

### Problem 4: No Face Detections
```
faces=0 (accept=0, hold=0, reject=0)
```
**Cause**: Faces in video don't meet quality threshold  
**Solution**:
- Check camera has good lighting
- Move closer to camera
- Check `q_runtime` threshold in `face/config.py`

---

### Problem 5: Identity Always "Unknown"
```
binding: {None: 5}
```
**Cause**: No enrolled faces in gallery OR GAITGUARD_FACE_KEY not set  
**Solution**:
```powershell
# Option 1: Set encryption key
$env:GAITGUARD_FACE_KEY = "<key-here>"
python -m core.main_loop

# Option 2: Enroll faces
python identity/enrollment_cli.py
```

---

## 📋 DEPLOYMENT CHECKLIST

### Pre-Deployment (One Time)
```
☐ Verify all tests pass (86/86)
☐ Check config file is valid YAML
☐ Set GAITGUARD_FACE_KEY if using gallery
☐ Configure GPU settings (use_gpu: true)
☐ Set camera index correctly
☐ Test on staging environment
```

### Runtime (Every Session)
```
☐ Check workspace directory: C:\...\GaitGuard - 2o\
☐ Activate virtual environment
☐ Verify GPU available (or accept CPU)
☐ Start main_loop
☐ Monitor FPS and metrics
☐ Graceful shutdown (press ESC)
```

### Post-Deployment
```
☐ Review metrics logs in logs/ directory
☐ Check for any ERROR level messages
☐ Verify no memory leaks over time
☐ Tune parameters based on performance
```

---

## 🎯 SUCCESS CRITERIA

**System is working correctly when**:
1. ✅ `python test_runner.py` shows 86/86 PASS
2. ✅ `python -m core.main_loop` starts without errors
3. ✅ FPS is 8.0+ and stable
4. ✅ GPU memory <2GB
5. ✅ Tracks detected and tracked smoothly
6. ✅ ESC exits cleanly

**System needs attention if**:
- ❌ Tests fail
- ❌ Main loop crashes
- ❌ FPS drops to 1 and stays there
- ❌ GPU memory continuously growing
- ❌ ERROR level messages in logs

---

## 📞 QUICK REFERENCE

### Emergency Commands
```powershell
# Kill running process
Get-Process python | Stop-Process -Force

# Clear GPU memory
python -c "import torch; torch.cuda.empty_cache()"

# Rerun full test suite
cd tests; python test_runner.py; cd ..

# Generate fresh config
python -c "from core.config import load_config; load_config()"
```

### Useful Aliases (PowerShell)
```powershell
# Add to $PROFILE
function test-all { cd tests; python test_runner.py; cd .. }
function run-main { python -m core.main_loop }
function check-env { python --version; echo "GPU:"; nvidia-smi | grep -E "^|MEMORY|Processes" }
```

---

## 📚 RELATED DOCUMENTATION

- `DEEP_RUNTIME_ANALYSIS.md` - Comprehensive system analysis
- `SYSTEM_VERIFICATION_COMPLETE.md` - Test verification report
- `COMPLETE_SYSTEM_ARCHITECTURE.md` - Architecture overview
- `TESTING_SUITE_COMPLETE.md` - Test suite documentation

---

*Last Updated: 2025-12-24*  
*Status: ✅ Production Ready*  
*Confidence: High*
