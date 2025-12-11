"""
ui/overlay.py
Visualization overlay with category-based coloring.
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

def draw_overlay(
    frame: Frame,
    tracks: List[Tracklet],
    decisions: List[IdentityDecision],
    events: List[EventFlags],
    alerts: List[Alert],
) -> np.ndarray:
    """
    Draws bounding boxes, identity labels, and status info onto the frame.
    
    Features:
    - Color-coded boxes based on identity category (Resident, Visitor, etc.).
    - Identity labels with name and confidence.
    - **Visual Debug:** Overlays the extracted silhouette (mask) on the tracked person
      to visualize what the gait model sees.
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
        
        # Choose Color based on category
        color = CATEGORY_COLORS.get(category.lower(), (255, 255, 255))
        
        # Draw Bounding Box
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        
        # --- SILHOUETTE VISUALIZATION (VISUAL DEBUG) ---
        # Displays the binary mask used for gait recognition directly on the video
        if t.gait_sequence_data:
            # Get the latest silhouette (64x64)
            silhouette = t.gait_sequence_data[-1]
            
            # Convert to BGR to draw it (source is grayscale uint8)
            # We tint it with the category color
            sil_color = cv2.cvtColor(silhouette, cv2.COLOR_GRAY2BGR)
            
            # Apply color mask: wherever the mask is white (255), apply 'color'
            mask = silhouette > 128
            sil_color[mask] = color 
            
            # Get dimensions
            sh, sw = silhouette.shape
            
            # Position: Top-Right corner of the bounding box
            # Ensure we don't draw outside the image
            draw_y = max(0, y1 - sh)
            draw_x = min(img.shape[1] - sw, x2)
            
            # Overlay logic
            roi = img[draw_y:draw_y+sh, draw_x:draw_x+sw]
            if roi.shape[:2] == (sh, sw):
                # Simple Alpha blending (50% original image + 50% silhouette)
                blended = cv2.addWeighted(roi, 0.5, sil_color, 0.5, 0)
                img[draw_y:draw_y+sh, draw_x:draw_x+sw] = blended

        # Text Label
        label = f"{identity_text} ({category})"
        if confidence > 0:
            label += f" {confidence:.2f}"
            
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        
        # Text background
        cv2.rectangle(img, (x1, y1 - 20), (x1 + tw, y1), color, -1)
        # Text
        cv2.putText(img, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

    return img