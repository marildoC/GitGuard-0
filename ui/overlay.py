"""
ui/overlay.py
Visualization module handling the drawing of bounding boxes,
skeletons, text, and color-coded categories on the video frame.
"""

from __future__ import annotations
from typing import List, Tuple, Dict
import logging
import cv2
import numpy as np

from schemas import Frame, Tracklet, IdentityDecision, EventFlags, Alert

logger = logging.getLogger(__name__)

# --- CATEGORY COLORS (BGR) ---
CATEGORY_COLORS = {
    "resident": (0, 255, 0),    # Green
    "visitor": (255, 0, 0),     # Blue
    "watchlist": (0, 0, 255),   # Red
    "unknown": (255, 255, 255)  # White
}

# Standard COCO Keypoint pairs to draw lines between joints
YOLO_POSE_SKELETON_PAIRS = [
    (15, 13), (13, 11), (16, 14), (14, 12), (11, 12),
    (5, 11), (6, 12), (5, 6), (5, 7), (7, 9),
    (6, 8), (8, 10), (0, 1), (0, 2), (1, 3), (2, 4)
]

def draw_skeleton_generic(img: np.ndarray, kps: np.ndarray, color: Tuple[int, int, int], skeleton: List[Tuple[int, int]] = YOLO_POSE_SKELETON_PAIRS, conf_threshold: float = 0.3): 
    """
    Draws the pose skeleton on the image.
    
    Args:
        img: The image frame to draw on.
        kps: Keypoints array (N, 3) -> [x_norm, y_norm, confidence].
        color: Color tuple (B, G, R).
        skeleton: List of pairs of indices connecting joints.
        conf_threshold: Minimum confidence to draw a point/line.
    """
    h, w, _ = img.shape
    for i in range(kps.shape[0]):
        x_norm, y_norm, c = kps[i]
        if c > conf_threshold:
            x, y = int(x_norm * w), int(y_norm * h)
            cv2.circle(img, (x, y), 3, color, -1)
    
    for a, b in skeleton:
        if a < kps.shape[0] and b < kps.shape[0]:
            xa, ya, ca = kps[a]
            xb, yb, cb = kps[b]
            if ca > conf_threshold and cb > conf_threshold:
                p1 = (int(xa * w), int(ya * h))
                p2 = (int(xb * w), int(yb * h))
                cv2.line(img, p1, p2, color, 2)

def draw_overlay(
    frame: Frame,
    tracks: List[Tracklet],
    decisions: List[IdentityDecision],
    events: List[EventFlags],
    alerts: List[Alert],
) -> np.ndarray:
    """
    Main visualization function.
    Draws tracking info, identification results, and status summaries.
    """
    if frame.image is None:
        raise ValueError("Frame.image is None inside draw_overlay")

    img = frame.image.copy()
    decisions_map = {d.track_id: d for d in decisions}

    # 1. General Info
    status = f"Tracks: {len(tracks)} | Alerts: {len(alerts)}"
    cv2.putText(img, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    # 2. Draw Tracks
    for t in tracks:
        x1, y1, x2, y2 = map(int, t.last_box)
        
        # Retrieve identity decision
        decision = decisions_map.get(t.track_id)
        
        identity_text = "Unknown"
        category = "unknown"
        confidence = 0.0
        
        if decision:
            category = decision.category
            if decision.identity_id:
                identity_text = decision.identity_id
                confidence = decision.confidence
        
        # Select Color based on Category
        # If not found, default to white
        color = CATEGORY_COLORS.get(category.lower(), (255, 255, 255))
        
        # Draw Bounding Box
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        
        # Draw Skeleton (using same color as box)
        if t.gait_sequence_data:
            draw_skeleton_generic(img, t.gait_sequence_data[-1], color)

        # Text Label
        label = f"{identity_text} ({category})"
        if confidence > 0:
            label += f" {confidence:.2f}"
            
        # Text Background (for readability)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        cv2.rectangle(img, (x1, y1 - 20), (x1 + tw, y1), color, -1)
        
        # Text (Black for contrast on bright colors)
        cv2.putText(img, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

    return img