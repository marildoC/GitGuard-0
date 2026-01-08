# GaitGuard

GaitGuard is a real-time identity and risk intelligence system designed for public-space security use cases.  
It aims to detect and track people in crowds, identify them reliably using face and gait, and raise tiered alerts for high-risk events (e.g., weapons, fights, fallen person) while prioritizing low false-alarm behavior.

This repository represents an evolving implementation and design of that concept. The focus is on building a system that is operationally useful: conservative decisions, clear categories, and real-time feedback.

---

## What GaitGuard Is

GaitGuard has two primary missions:

1. **Real-time identification** of people in video streams (single camera first, scalable later).
2. **Real-time understanding of risk events** around those people, with actionable alerts.

GaitGuard is **identity-centric**: it does not only detect “events” in video; it tries to answer:
- **Who is this person (if known)?**
- **What is happening right now (if risky)?**
- **How confident is the system, and should it alert or stay conservative?**

---

## Core Idea: Identity in Crowds

GaitGuard operates in scenes with multiple people, not just one subject.  
The system continuously:

- Detects people
- Tracks them across frames (stable track IDs)
- Extracts identity signals per track
- Fuses the signals into a decision (or refuses to decide when uncertain)

A key design rule is:

> If confidence is weak, the system should avoid forcing an identity decision.  
> It is better to remain “unchecked/unknown” than to generate a false alarm.

---

## Enrollment Concept (Passport-Style)

GaitGuard assumes a concept of **enrollment**, where identities are added to the system in a controlled way.

A typical enrollment session can include:

- **Face captures** under multiple variations (neutral/expression, with/without glasses, with/without cap, slight yaw/pitch)
- **A short walking capture** (5–10 seconds) to seed a gait/motion identity
- **Basic profile metadata** (e.g., internal ID, approximate height)

The enrollment produces compact biometric templates that the live pipeline can match against later.

---

## Live Pipeline (High-Level)

At runtime, every camera frame is processed with this logic:

1. **Detect & Track People**
   - Every visible person becomes a tracked “tracklet” with short history.

2. **Face Route (when face is visible)**
   - Extract face features and attempt identification against the enrolled face gallery.

3. **Gait / Motion Route (when face is weak/occluded)**
   - Use body pose / movement patterns over a short window to attempt identity matching.

4. **Evidence Fusion**
   - Combine multiple signals (face, gait/motion, appearance) into a single confidence decision.
   - If confidence is not sufficient, the system stays conservative.

5. **Category Overlay**
   - Each tracked person is labeled with a category color in real time.

6. **Risk Event Understanding (parallel)**
   - Detect weapons / fights / fallen-person conditions using multi-frame confirmation logic.

7. **Alert + Evidence**
   - When thresholds are met, generate alerts with context (clip, time, location/camera).

---

## Categories (Operational Semantics)

GaitGuard uses clear, operator-friendly categories:

- **Green** — enrolled resident / known safe identity
- **Blue** — enrolled visitor/tourist identity (registered when entering the country/system)
- **Red / Dark-Red** — watch-list subject (severity encoded)
- **White** — unknown identity (no match when sufficient biometric signal exists)

Important note:

- **“White” is not the same as “uncertain.”**  
  If the system cannot see enough signal (face/gait not visible), it should remain conservative rather than automatically labeling “White”.

---

## Continuous Learning (System Improves Over Time)

When an identity is confirmed (by internal high confidence or by an operator), the system can update templates over time.  
This supports real-world variation:

- different clothes, shoes, bags
- different speeds and walking styles
- different camera viewpoints (front/side/behind)

The goal is gradual adaptation while still controlling false positives.

---

## Risk Events (Weapons, Fights, Fallen Person)

Risk detection runs **in parallel** with identity recognition.

Key principle:

> Event decisions must be multi-frame and conservative to avoid false alarms.

Examples:
- **Weapon detection** requires repeated confirmation across frames.
- **Fight/violence detection** should rely on short temporal windows, not single images.
- **Fallen person detection** should use posture + stillness duration (to distinguish from sitting/resting).

Alerts are tiered based on:
- category (e.g., watch-list severity)
- identity confidence
- event type and confirmation strength

---

## Current Status (Practical Milestone)

This repository is being developed incrementally with a clear pipeline target.  
A current milestone is:

- **Real-time person detection from webcam is validated**
- The system is ready for the next stages:
  - face identity gallery + matching
  - gait/pose extraction and gait identity route
  - fusion logic + category overlay
  - event modules + alert state machine

---

## Goals

GaitGuard is designed to be:

- **Robust in crowds**
- **Accurate-first**, but mindful of real-time performance
- **Conservative by default** (avoid misidentification and false alarms)
- **Operationally usable**, with clear categories and evidence-based alerts
- **Extensible**, so modules can evolve (face-only → face+gait → full identity+events)

---

## Repository Notes

- This project evolves through iterations and branches.
- The README describes the **system concept and logic** (not an exhaustive technical specification).
- Implementation details and technical documentation may be added progressively as modules stabilize.

---

## License / Usage

This project is a research and development effort.  
If you plan to deploy similar systems in real environments, ensure compliance with applicable laws and ethical requirements regarding biometric identification, privacy, and surveillance.
