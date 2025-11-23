from __future__ import annotations

import time
import logging

import cv2
from schemas import Frame
from .camera import CameraSource
from .dummies import (
    DummyEventsEngine,
    DummyAlertEngine,
)
from .config import load_config
from .logging_setup import setup_logging
from .device import select_device
from ui.overlay import draw_overlay

# Phase-1 perception engine (YOLO + OC-SORT + appearance + ring buffer)
from perception.perception_engine import Phase1PerceptionEngine

# Phase-2A: real face-based IdentityEngine
from identity.identity_engine import FaceIdentityEngine

#Phase -2B: GaitEngine
from gait.gait_engine import GaitEngine
from gait.config import GaitConfig, default_gait_config

def run() -> None:
    """
    Executes the GaitGuard pipeline, integrating perception, gait recognition,
    and a UI overlay for real-time visualization.

    How it works:
    The `run` function orchestrates the entire GaitGuard application loop.
    1.  **Configuration & Logging**: Loads system configuration and sets up logging.
    2.  **Device Selection**: Determines the optimal compute device (GPU/CPU) for operations.
    3.  **Engine Instantiation**: Initializes various core components:
        *   `Phase1PerceptionEngine`: Handles object detection (YOLOv8-pose), tracking (OC-SORT),
            appearance feature extraction, and manages pose history.
        *   `GaitEngine`: Manages gait embedding extraction and identity matching against a gallery.
        *   `DummyEventsEngine` and `DummyAlertEngine`: Placeholder modules for event and alert generation.
    4.  **Camera Source**: Sets up and starts the video camera feed.
    5.  **Main Loop**: Continuously reads frames from the camera:
        *   Wraps the raw image into a `Frame` schema.
        *   Passes the `Frame` to `perception.process_frame` to get `Tracklet`s.
        *   Passes `Tracklet`s to `gait_engine.update_signals` to get `IdSignal`s.
        *   Converts `IdSignal`s into `IdentityDecision`s using `gait_engine.decide`.
        *   Updates dummy event and alert engines.
        *   Generates a visual overlay on the frame using `draw_overlay`.
        *   Displays the annotated frame and logs FPS.
        *   Exits if the 'ESC' key is pressed.
    6.  **Cleanup**: Stops the camera and closes all OpenCV windows upon exit.

    Pipeline Flow:
        Frame (from camera) ->
        Perception Engine (detection, pose estimation, tracking, pose history) ->
        Gait Engine (gait embedding extraction, gallery search) ->
        IdSignals ->
        IdentityDecisions ->
        (Dummy) Events Engine ->
        (Dummy) Alerts Engine ->
        UI Overlay (display)

    Press ESC in the displayed window to exit the application.
    """
    # ---- load config & logging ----
    cfg = load_config()
    setup_logging(cfg.paths.logs_dir)
    log = logging.getLogger("gaitguard.main")

    # ---- decide device (GPU/CPU + FP16) ----
    # This is mainly for logging / future phases.
    # The Detector inside Phase1PerceptionEngine and the face route
    # will auto-select CUDA if available.
    device, use_half = select_device(prefer_gpu=cfg.runtime.use_gpu)
    log.info("Runtime device=%s | half=%s", device, use_half)

    # ---- instantiate engines ----
    gait_config = default_gait_config()
    # Phase-1: real perception engine (YOLO + OC-SORT + appearance + ring buffer).
    perception = Phase1PerceptionEngine(
        keypoint_ema_alpha=gait_config.route.keypoint_ema_alpha, # Passed from gait_config
        keypoint_history_length=gait_config.route.keypoint_history_length, # Passed from gait_config
        gait_config=gait_config, # The full gait config is passed for model loading and other parameters
    )

    # Phase-2A: real face-based identity engine (FaceRoute + FaceGallery + temporal smoothing).
    # Currently commented out as gait is the primary focus.
    # identity = FaceIdentityEngine()

    # Phase-2B: Gait Engine for gait recognition.
    gait_engine=GaitEngine() 

    # Events / alerts still dummy for now.
    events_engine = DummyEventsEngine()
    alert_engine = DummyAlertEngine()

    # Optional warmup hooks (if implemented on these classes).
    if hasattr(perception, "warmup"):
        try:
            log.info("Warming up perception engine (if supported)...")
            perception.warmup()  # type: ignore[call-arg]
        except Exception:
            log.exception("Perception warmup failed")

    #if hasattr(identity, "warmup"):
    #    try:
    #        log.info("Warming up identity engine (if supported)...")
    #        identity.warmup()  # type: ignore[call-arg]
    #    except Exception:
    #        log.exception("Identity warmup failed")

    # ---- camera source ----
    src = CameraSource(
        cam_index=cfg.camera.index,
        w=cfg.camera.width,
        h=cfg.camera.height,
        fps=cfg.camera.fps,
        buffersize=1,
    )
    src.start()

    log.info("GaitGuard pipeline (Phase-1 + Gait 1.3) started. Press ESC to exit.")

    frame_id = 0
    camera_id = "cam0"

    # Use monotonic clock for FPS / latency measurements.
    t0 = time.perf_counter()
    frames = 0

    try:
        while True:
            img = src.read_latest(timeout=1.0)
            if img is None:
                continue

            # Monotonic timestamp for internal timing (stable even if system time changes)
            ts = time.perf_counter()
            h, w = img.shape[:2]

            # Wrap raw image into our Frame schema.
            frame = Frame(
                frame_id=frame_id,
                ts=ts,
                camera_id=camera_id,
                size=(w, h),
                image=img,
            )
            frame_id += 1

            # ---- full pipeline ----
            # Perception: detect + track (including pose estimation and history management)
            tracks = perception.process_frame(frame)

            # Identity: Face route (commented out)
            # signals = identity.update_signals(frame, tracks)
            # decisions = identity.decide(signals)

            # Identity: Gait route -> IdSignals -> IdentityDecision
            gait_signals = gait_engine.update_signals(frame, tracks)
            gait_decisions = gait_engine.decide(gait_signals)

            # Events / alerts (still dummy)
            events = events_engine.update(frame, tracks, gait_decisions)
            alerts = alert_engine.update(frame, events, gait_decisions)

            # ---- UI overlay ----
            display_img = draw_overlay(frame, tracks, gait_decisions, events, alerts)

            frames += 1
            if frames % 30 == 0:
                elapsed = ts - t0
                fps = frames / max(elapsed, 1e-6)
                log.info(
                    "FPS=%.1f | tracks=%d | alerts=%d",
                    fps,
                    len(tracks),
                    len(alerts),
                )

            cv2.imshow("GaitGuard 1.0 - Phase 1 + Gait", display_img)

            # ESC to exit
            if cv2.waitKey(1) & 0xFF == 27:
                break

    finally:
        src.stop()
        cv2.destroyAllWindows()
        log.info("GaitGuard pipeline stopped.")


if __name__ == "__main__":
    run()
