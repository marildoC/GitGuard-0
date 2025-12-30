# Installation Instructions - Safe Step-by-Step Guide

## ✅ Current Status
- ✅ Fixed: OpenCV conflicting packages removed from requirements.txt
- ✅ Fixed: PyTorch CUDA suffix issue resolved (changed to standard PyPI versions)
- ✅ Fixed: Added missing config fields to UiConfig

## 📋 Step-by-Step Installation (Safe & Correct Order)

### Step 1: Activate Your Virtual Environment

```powershell
# Navigate to project directory (if not already there)
cd "C:\Users\ildi\Desktop\GaitGuard - Copy"

# Activate virtual environment
.venv310\Scripts\Activate.ps1
```

**Verify activation:** Your prompt should show `(.venv310)` at the beginning.

### Step 2: Upgrade pip (Recommended - Avoids Compatibility Issues)

```powershell
python -m pip install --upgrade pip
```

This upgrades pip from 23.0.1 to latest, which handles dependencies better.

### Step 3: Install Requirements (This Should Work Now)

```powershell
pip install -r requirements.txt
```

**Expected behavior:**
- ✅ All packages should install successfully
- ✅ No more "torch==2.6.0+cu124" error
- ✅ OpenCV (cv2) will be installed

**If you see any errors:** Stop and note which package failed.

### Step 4: Verify Critical Packages Are Installed

```powershell
# Check OpenCV (cv2)
python -c "import cv2; print('OpenCV version:', cv2.__version__)"

# Check PyTorch
python -c "import torch; print('PyTorch version:', torch.__version__)"

# Check numpy
python -c "import numpy; print('NumPy version:', numpy.__version__)"
```

All three should print version numbers without errors.

### Step 5: Test the Application

```powershell
python -m core.main_loop
```

**Expected behavior:**
- ✅ Should start without "ModuleNotFoundError: No module named 'cv2'"
- ✅ Should initialize and show camera feed (if camera is available)
- ✅ Press ESC to exit

## 🔄 If Something Goes Wrong

### If pip install fails on a specific package:

1. **Note the exact error message**
2. **Try installing that package alone first:**
   ```powershell
   pip install [package-name]
   ```
3. **Then retry full installation:**
   ```powershell
   pip install -r requirements.txt
   ```

### If you want to start fresh:

```powershell
# Deactivate current environment
deactivate

# Remove virtual environment (CAUTION: This deletes it)
# Remove-Item -Recurse -Force .venv310

# Create new virtual environment
python -m venv .venv310

# Activate it
.venv310\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip

# Install requirements
pip install -r requirements.txt
```

## 📝 What We Fixed

1. **OpenCV**: Removed conflicting packages (`opencv-contrib-python` and `opencv-python-headless`), kept only `opencv-python`
2. **PyTorch**: Removed `+cu124` suffix from torch, torchaudio, torchvision (PyPI doesn't support this format)
3. **Config**: Added missing `show_source_auth_tag` and `show_source_auth_border` fields to UiConfig

## ⚠️ Important Notes

- **GPU Support**: The standard PyTorch versions will automatically detect and use GPU if CUDA drivers are installed
- **No Breaking Changes**: All fixes maintain backward compatibility
- **Virtual Environment**: Always work within the `.venv310` virtual environment

## 🎯 Success Criteria

You'll know everything works when:
1. ✅ `pip install -r requirements.txt` completes without errors
2. ✅ `python -c "import cv2"` runs without errors
3. ✅ `python -m core.main_loop` starts the application

