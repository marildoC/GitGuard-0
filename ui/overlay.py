"""
ui/overlay.py

Minimal Phase-0 overlay.

For now we just:
  - return the original frame image
  - draw a small status text with #tracks and #alerts

Later we will:
  - draw bounding boxes
  - draw color-coded categories (green/blue/red/white)
  - add event icons (weapon/fight/fallen)
"""

from __future__ import annotations

from typing import List

import cv2
import numpy as np

from schemas import Frame, Tracklet, IdentityDecision, EventFlags, Alert


def draw_overlay(
    frame: Frame,
    tracks: List[Tracklet],
    decisions: List[IdentityDecision],
    events: List[EventFlags],
    alerts: List[Alert],
) -> np.ndarray:
    """
    Return an image (BGR) to display.

    In Phase 0 this is just a copy of frame.image with a small status line.
    """
    if frame.image is None:
        raise ValueError("Frame.image is None inside draw_overlay")

    img = frame.image.copy()

    # Status text: how many tracks + alerts
    status = f"tracks: {len(tracks)} | alerts: {len(alerts)}"
    cv2.putText(
        img,
        status,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (0, 255, 255),
        2,
    )

    return img
