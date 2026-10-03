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


def run() -> None:
    """
    Run the GaitGuard pipeline (Phase-1 + Phase-2A Face).

    Pipeline:
        Frame (camera) ->
        Perception (detect + track + ring buffers) ->
        Identity (face route + gallery) ->
        Events (dummy) ->
        Alerts (dummy) ->
        UI overlay

    Press ESC in the window to exit.
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
    # Phase-1: real perception engine (YOLO + OC-SORT + appearance + ring buffer).
    perception = Phase1PerceptionEngine()

    # Phase-2A: real face-based identity engine (FaceRoute + FaceGallery + temporal smoothing).
    identity = FaceIdentityEngine()

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

    if hasattr(identity, "warmup"):
        try:
            log.info("Warming up identity engine (if supported)...")
            identity.warmup()  # type: ignore[call-arg]
        except Exception:
            log.exception("Identity warmup failed")

    # ---- camera source ----
    src = CameraSource(
        cam_index=cfg.camera.index,
        w=cfg.camera.width,
        h=cfg.camera.height,
        fps=cfg.camera.fps,
        buffersize=1,
    )
    src.start()

    log.info("GaitGuard pipeline (Phase-1 + Face 2A) started. Press ESC to exit.")

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
            # Perception: detect + track
            tracks = perception.process_frame(frame)

            # Identity: face route + gallery -> IdSignals -> IdentityDecision
            signals = identity.update_signals(frame, tracks)
            decisions = identity.decide(signals)

            # Events / alerts (still dummy)
            events = events_engine.update(frame, tracks, decisions)
            alerts = alert_engine.update(frame, events, decisions)

            # ---- UI overlay ----
            display_img = draw_overlay(frame, tracks, decisions, events, alerts)

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

            cv2.imshow("GaitGuard 1.0 - Phase 1 + Face 2A", display_img)

            # ESC to exit
            if cv2.waitKey(1) & 0xFF == 27:
                break

    finally:
        src.stop()
        cv2.destroyAllWindows()
        log.info("GaitGuard pipeline stopped.")


if __name__ == "__main__":
    run()
