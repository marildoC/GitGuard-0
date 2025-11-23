"""
ui/overlay.py

This module provides functions to draw an overlay on a video frame, displaying
tracking information and identity recognition results.

Overlay for visualizing:
  - Number of active tracks
  - YOLOv8-pose keypoints extracted for each person
  - Identity recognition decisions (gait-based)
  - System status and alerts
"""

from __future__ import annotations

from typing import List, Tuple
import logging
import cv2
import numpy as np


from schemas import Frame, Tracklet, IdentityDecision, EventFlags, Alert

from schemas.identity_decision import IdentityDecision
logger= logging.getLogger(__name__)

#--- Definition of skeleton pairs for YOLOv8-pose (COCO format) ---
# These are the standard keypoints and connections that YOLOv8-pose produces.
# The indices correspond to the keypoints that YOLO returns.
YOLO_POSE_SKELETON_PAIRS = [
    (15, 13), (13, 11),  # right leg: ankle -> knee -> hip
    (16, 14), (14, 12),  # left leg: ankle -> knee -> hip
    (11, 12),            # hips: left hip -> right hip
    (5, 11), (6, 12),    # torso: left shoulder -> left hip, right shoulder -> right hip
    (5, 6),              # shoulders: left shoulder -> right shoulder
    (5, 7), (7, 9),      # right arm: right shoulder -> elbow -> wrist
    (6, 8), (8, 10),     # left arm: left shoulder -> elbow -> wrist
    (0, 1), (0, 2), (1, 3), (2, 4), # face/head: nose -> left eye, nose -> right eye, left eye -> left ear, right eye -> right ear
]

#--- Defining keypoint names (for debugging or reference) ---
YOLO_POSE_KEYPOINT_NAMES = [
    'nose', 'eye_left', 'eye_right', 'ear_left', 'ear_right', # 0-4
    'shoulder_left', 'shoulder_right', 'elbow_left', 'elbow_right', # 5-8
    'wrist_left', 'wrist_right', 'hip_left', 'hip_right', # 9-12
    'knee_left', 'knee_right', 'ankle_left', 'ankle_right' # 13-16
]

def draw_skeleton_generic(img: np.ndarray, kps: np.ndarray, color: Tuple[int, int, int], skeleton: List[Tuple[int, int]] = YOLO_POSE_SKELETON_PAIRS, conf_threshold: float = 0.01): 
    """
    Draws keypoints and skeleton lines on an image given normalized keypoint coordinates.

    How it works:
    The function first iterates through each keypoint in `kps`. If a keypoint's confidence
    exceeds `conf_threshold`, its normalized coordinates are converted to pixel coordinates
    relative to the image's dimensions, and a circle is drawn at that location.
    Next, it iterates through the `skeleton` pairs. For each pair, it checks if both
    connected keypoints exist and their confidences are above the threshold. If so,
    a line (bone) is drawn connecting their respective pixel coordinates.

    Args:
        img (np.ndarray): The image (frame) onto which the skeleton will be drawn.
        kps (np.ndarray): An array of keypoints, typically in (x_norm, y_norm, confidence) format.
                          Coordinates are normalized (0-1).
        color (Tuple[int, int, int]): An RGB tuple representing the color for drawing keypoints and lines.
        skeleton (List[Tuple[int, int]], optional): A list of tuples, where each tuple defines
                                                     a pair of keypoint indices to connect with a line (a bone).
                                                     Defaults to YOLO_POSE_SKELETON_PAIRS.
        conf_threshold (float, optional): The minimum confidence score for a keypoint to be drawn
                                          and considered for bone connections. Defaults to 0.01.
    """
    h, w, _ = img.shape
    drawn_kps_count = 0
    drawn_bones_count = 0

    # Draw keypoints
    for i in range(kps.shape[0]):
        x_norm, y_norm, c = kps[i]
        if c > conf_threshold:
            x, y = int(x_norm * w), int(y_norm * h)
            cv2.circle(img, (x, y), 3, color, -1)
            drawn_kps_count += 1
    
    # Draw bones
    for a, b in skeleton:
        if a < kps.shape[0] and b < kps.shape[0]:
            xa_norm, ya_norm, ca = kps[a]
            xb_norm, yb_norm, cb = kps[b]
            if ca > conf_threshold and cb > conf_threshold:
                xa,ya = int(xa_norm*w), int(ya_norm*h)
                xb,yb = int(xb_norm*w), int(yb_norm*h)
                cv2.line(img, (xa, ya), (xb, yb), color, 2)
                drawn_bones_count += 1
    
def draw_overlay(
    frame: Frame,
    tracks: List[Tracklet],
    decisions: List[IdentityDecision],
    events: List[EventFlags],
    alerts: List[Alert],
) -> np.ndarray:
    """Draws a comprehensive overlay on the given frame, including track information,
    YOLOv8-pose skeletons, identity decisions, and status text.

    How it works:
    1.  **Initial Setup**: Creates a copy of the input `frame.image` to draw on.
    2.  **Status Text**: Calculates and displays overall status information at the top-left,
        including the number of active tracks, recognized gaits, active alerts,
        and the average gait quality across valid tracks.
    3.  **Track-specific Details**: Iterates through each `Tracklet` in the `tracks` list:
        *   **Bounding Box**: Draws a bounding box around the detected person based on `t.last_box`.
        *   **YOLOv8-pose Skeleton**: If `t.gait_sequence_data` is available (containing smoothed
            YOLOv8-pose keypoints), it extracts the latest pose and uses `draw_skeleton_generic`
            to render the skeleton on the image, utilizing `YOLO_POSE_SKELETON_PAIRS`.
        *   **Label Text**: Constructs a label displaying the `track_id`, recognized gait
            `identity_id` (if available from `decisions`), and `gait_quality`. It also indicates
            "UNKNOWN" if a gait confidence is available but no specific identity is assigned.
        *   **Text Overlay**: Places this label near the bounding box.

    Args:
        frame (Frame): The current video frame object, containing the image to be annotated.
        tracks (List[Tracklet]): A list of active `Tracklet` objects, representing tracked
                                 persons with their associated data (e.g., bounding boxes, gait data).
        decisions (List[IdentityDecision]): A list of `IdentityDecision` objects, providing
                                            identity recognition results for tracks.
        events (List[EventFlags]): A list of `EventFlags` (not directly used for drawing in this version,
                                   but could be used for indicating events on the overlay).
        alerts (List[Alert]): A list of `Alert` objects, used to display the count of active alerts.

    Returns:
        np.ndarray: The input image with the overlay elements drawn upon it.

    Raises:
        ValueError: If `frame.image` is None.
    """
    if frame.image is None:
        raise ValueError("Frame.image is None inside draw_overlay")

    img = frame.image.copy()

    # ---------------------------------------------
    # 1) Status text
    # ---------------------------------------------
    num_gait_decisions = len([d for d in decisions if d.identity_id is not None])
    valid_gait_qualities = [t.gait_quality for t in tracks if t.gait_quality > 0]
    avg_gait_quality = np.mean([t.gait_quality for t in tracks if t.gait_quality > 0]) if tracks else 0.0
    
    status = f"tracks: {len(tracks)} | gait_rec: {num_gait_decisions} | alerts: {len(alerts)}"
    if avg_gait_quality > 0:
        status += f" | avg_g_q: {avg_gait_quality:.2f}" # ADD Average quality to the UI
    
    cv2.putText(
        img,
        status,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (0, 255, 255),
        2,
    )
    decisions_map = {d.track_id: d for d in decisions}
    # ---------------------------------------------
    # 2) Draw YOLOv8-pose keypoints and track info on each track
    # ---------------------------------------------
    for t in tracks:

        # Bounding Box
        x1, y1, x2, y2 = map(int, t.last_box)
        track_color = (0, 255, 0) 
        cv2.rectangle(img, (x1, y1), (x2, y2), track_color, 2)

        if t.gait_sequence_data: #If smoothed pose data is available
            keypoints_smoothed = t.gait_sequence_data[-1] # Last smoothed pose (YOLOv8-pose format)
            logger.debug(f"Track {t.track_id}: Overlay drawing with smoothed KPs: {keypoints_smoothed.shape}, first kp: {keypoints_smoothed[0]}")
            draw_skeleton_generic(img, keypoints_smoothed, track_color, YOLO_POSE_SKELETON_PAIRS)
        
            label_text = f"Track:{t.track_id}"
        
        decision = decisions_map.get(t.track_id)

        if decision and decision.identity_id:
            label_text += f" | Gait:{decision.identity_id} ({decision.confidence:.2f})"
        else:
            # Display gait confidence even if no specific identity is recognized (e.g., as 'UNKNOWN')
            if t.gait_identity_id is None and t.gait_confidence is not None:
                 label_text += f" | Gait:UNKNOWN ({t.gait_confidence:.2f})"
            else:
                 label_text += f" | Gait:UNKNOWN"
        
        if t.gait_quality > 0:
            label_text += f" (Q:{t.gait_quality:.2f})"

        cv2.putText(
            img,
            label_text,
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )

    return img