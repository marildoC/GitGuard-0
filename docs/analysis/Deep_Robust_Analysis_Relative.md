# Deep Robust Analysis: The "Relative Geometry" Paradigm

This document addresses the user's request for a "deep robust analysis" of why the system failed and the logic behind the final fix.

## 1. The Root Cause Analysis
The system failure was caused by a conflict between **Idealized Theory** and **Real-World Data**.

*   **Theory (Absolute Geometry)**: "All humans have a Leg-to-Torso ratio of ~1.3. Anything else is noise/error and should be rejected to protect the database."
*   **Reality**: The user's specific camera setup (likely high angle/CCTV) creates **Perspective Foreshortening**.
    *   Result: Marildo's legs appear extremely short (Ratio 0.30).
*   **The Failure Chain**:
    1.  Enrollment `enroll --name marildo` processed the video.
    2.  Smart Chunking found valid motion.
    3.  **Geometry Gate** computed Ratio 0.31.
    4.  The Gate said "0.31 < 0.60 (Minimum Human)", so it **REJECTED** the template.
    5.  Result: Marildo had **NO Templates** for Front/Back views.
    6.  **Inference Time**: Marildo walks Front. System sees "Front Gait".
    7.  Gallery Search:
        *   Marildo? (No Front template).
        *   Johny? (Has Front template).
    8.  **Outcome**: Matching fails over to Johny.

## 2. The Deep Robust Solution: Relative Geometry

We are shifting the logic from **Prescriptive** (telling the system what humans look like) to **Descriptive** (learning what *this specific human* looks like *in this specific camera*).

### Logic Change
*   **Old Logic**: `if ratio < 0.60: reject()`
*   **New Logic**: `if ratio < 0.60: learn_it_anyway()`

### Why this is Robust
1.  **Camera Invariance**: If Marildo enrolls as "0.30 Ratio" (Short Legs due to view), and then walks in the same area, he will *again* appear as "0.30 Ratio".
2.  **Uniqueness**: "Johny" might enroll as "0.50 Ratio" (maybe he stood further back).
3.  **Matching**:
    *   Query (Marildo Live): 0.30
    *   Target (Marildo Stored): 0.30 -> **Distance 0.0**. MATCH!
    *   Target (Johny Stored): 0.50 -> **Distance 0.2**. PENALTY!
4.  **Verification**: Even though 0.30 is "biologically weird", it is **biometrically consistent**. The system uses the "Weirdness" as a unique signature to distinguish Marildo from others.

## 3. System Health Checklist
*   **Kinematic Gate**: Active (Prevents turns/stops).
*   **Motion Gate**: Active (Prevents stills).
*   **Geometry Gate**: **Adaptive** (Now captures camera-specific body shapes).
*   **Policy Gate**: Active (Strict margins, Active Demotion).

## Conclusion
The system is now "Deeply Connected". Enrollment feeds "Ground Truth" (however distorted by camera) into the Gallery. The Gallery uses that specific distortion as a feature, not a bug, to penalize impostors who don't match that specific distortion.
