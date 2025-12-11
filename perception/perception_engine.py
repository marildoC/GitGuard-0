# perception/perception_engine.py
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple
import cv2

import numpy as np
import torch

from collections import deque

from schemas import Frame, Tracklet
from core.interfaces import PerceptionEngine

from perception.detector import Detection
from .tracker_ocsort import OCSortTracker, Track
from .appearance import AppearanceExtractor
from .ring_buffer import RingBuffer, RingBufferConfig

from ultralytics import YOLO
from gait.config import GaitConfig, default_gait_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal per-track state
# ---------------------------------------------------------------------------

class TrackState:
    """
    Internal state for silhouette-based tracking.
    Stores a history of binary masks (silhouettes) for gait analysis.
    """
    def __init__(self, track_id: int, camera_id: str):
        self.tracklet = Tracklet(
            track_id=track_id,
            camera_id=camera_id,
            last_frame_id=0,
            last_box=(0, 0, 0, 0),
            confidence=0.0,
            age_frames=0,
            lost_frames=0,
            history_boxes=[]
        )
        self.appearance_feature: Optional[np.ndarray] = None
        
        # CHANGED: Stores sequence of silhouettes (64x64 images)
        # Max length 40 frames provides a sufficient window for GaitSet
        self.silhouette_history: deque[np.ndarray] = deque(maxlen=40)

# ---------------------------------------------------------------------------
# Helpers for Segmentation
# ---------------------------------------------------------------------------

def process_mask(mask_data: np.ndarray, bbox: List[float], output_size=(64, 64)) -> np.ndarray:
    """
    Extracts the silhouette from the full image mask based on the bbox.
    
    Logic:
    1. Crops the specific person's mask using the bounding box coordinates.
    2. Resizes the crop to fit the output_size (64x64) while strictly preserving 
       aspect ratio.
    3. Pads the remaining area with black pixels (centering the person).
    
    Args:
        mask_data: Full frame binary mask or specific instance mask.
        bbox: [x1, y1, x2, y2]
        output_size: (W, H) target size (e.g., 64x64).
    
    Returns:
        np.ndarray: (64, 64) uint8 binary image (0 or 255).
    """
    # Cast bbox to int
    x1, y1, x2, y2 = map(int, bbox)
    h_img, w_img = mask_data.shape[:2]
    
    # Clamp coordinates to image bounds
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w_img, x2); y2 = min(h_img, y2)
    
    # If bbox is invalid
    if x2 <= x1 or y2 <= y1:
        return np.zeros(output_size, dtype=np.uint8)

    # Crop the mask
    crop = mask_data[y1:y2, x1:x2]
    
    # Resize maintaining Aspect Ratio (Pad with black)
    h, w = crop.shape
    target_h, target_w = output_size
    scale = min(target_w / w, target_h / h)
    
    new_w = int(w * scale)
    new_h = int(h * scale)
    
    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    
    # Create black canvas
    canvas = np.zeros((target_h, target_w), dtype=np.uint8)
    
    # Center the image
    y_offset = (target_h - new_h) // 2
    x_offset = (target_w - new_w) // 2
    
    canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized
    
    # Ensure binary 0-255 format
    # YOLO masks might be float 0..1 or 0..255. Normalize to uint8 255.
    if canvas.max() <= 1.0:
        canvas = (canvas * 255).astype(np.uint8)
        
    return canvas

# ---------------------------------------------------------------------------
# Main Perception Engine (Phase 1) - SEGMENTATION VERSION
# ---------------------------------------------------------------------------

class Phase1PerceptionEngine(PerceptionEngine):
    def __init__(
        self,
        tracker: Optional[OCSortTracker] = None,
        appearance: Optional[AppearanceExtractor] = None,
        ring_buffer: Optional[RingBuffer] = None,
        max_lost_frames: int = 30,
        # Legacy params kept for compatibility, but unused for silhouettes
        keypoint_ema_alpha: float = 0.65, 
        keypoint_history_length: int = 30,
        gait_config: Optional[GaitConfig] = None
    ):
        super().__init__()

        from gait.config import GaitConfig, default_gait_config
        self.gait_config = gait_config if gait_config is not None else default_gait_config()

        # CHANGED: Load YOLOv8-SEGMENTATION model instead of Pose
        # Ensure config points to "yolov8n-seg.pt" or force it here
        seg_model_name = "yolov8n-seg.pt" 
        logger.info(f"Loading Segmentation Model: {seg_model_name}")
        
        self.yolo_model = YOLO(seg_model_name)
        
        # Device Setup
        if self.gait_config.device.use_half and self.gait_config.device.device == "cuda":
            self.yolo_model.to("cuda")
        else:
            self.yolo_model.to("cpu")

        # Modules
        self.tracker = tracker or OCSortTracker()
        self.appearance = appearance or AppearanceExtractor()
        self.ring_buffer = ring_buffer or RingBuffer(RingBufferConfig())
        self._states: Dict[int, TrackState] = {}
        self.max_lost_frames = max_lost_frames
        
        logger.info("Phase-1 PerceptionEngine (Silhouette Mode) initialized.")

    def process_frame(self, frame: Frame) -> List[Tracklet]:
        """
        Main pipeline: Detect -> Track -> Associate Masks -> Update History
        """
        if frame.image is None:
            return []

        # ---- Step 1: YOLOv8 Segmentation ----
        # Run inference with task="segment"
        results = self.yolo_model.predict(
            source=frame.image,
            device=self.gait_config.device.device,
            imgsz=self.gait_config.route.img_size,
            conf=self.gait_config.thresholds.min_visibility,
            classes=[0], # Person class
            half=self.gait_config.device.use_half,
            verbose=False,
            task="segment",
            retina_masks=True # Better mask quality
        )
        
        result = results[0]
        boxes = []
        scores = []
        masks_list = []

        if result.boxes is not None:
            xyxy = result.boxes.xyxy.cpu().numpy()
            confs = result.boxes.conf.cpu().numpy()
            
            # Extract Masks if available
            if result.masks is not None:
                # data is usually (N, H, W) masks
                raw_masks = result.masks.data.cpu().numpy() 
            else:
                raw_masks = None

            for i in range(len(xyxy)):
                boxes.append(xyxy[i].tolist())
                scores.append(float(confs[i]))
                if raw_masks is not None:
                    masks_list.append(raw_masks[i])
                else:
                    masks_list.append(None)

        # Prepare for Tracker (tracker only cares about boxes)
        detections_for_ocsort = [
            Detection(x1=b[0], y1=b[1], x2=b[2], y2=b[3], score=s, class_id=0, class_name="person")
            for b, s in zip(boxes, scores)
        ]

        # ---- Step 2: Appearance Features ----
        features = self.appearance.compute_features_for_detections(
            frame, detections_for_ocsort
        )

        # ---- Step 3: OC-SORT Tracking ----
        tracks: List[Track] = self.tracker.update(detections_for_ocsort, features)

        # We need to associate the original YOLO masks with the confirmed Tracks.
        # Since the tracker might reorder or filter detections, we cannot assume index matching.
        # We must match confirmed tracks back to raw detections via Intersection over Union (IoU).
        
        # Create a helper list of (box, mask) from YOLO
        yolo_dets = []
        for i in range(len(boxes)):
            yolo_dets.append({
                'box': boxes[i], 
                'mask': masks_list[i] if len(masks_list) > i else None
            })

        # ---- Step 4: Update Track States ----
        active_ids = set()
        current_tracklets: List[Tracklet] = [] 

        for tr in tracks:
            tid = tr.track_id
            active_ids.add(tid)
            
            if tid not in self._states:
                self._states[tid] = TrackState(track_id=tid, camera_id=frame.camera_id)
            
            state = self._states[tid]
            t = state.tracklet

            # Update Metadata
            t.last_frame_id = frame.frame_id
            t.last_box = tuple(tr.bbox.tolist())
            t.confidence = tr.score
            t.age_frames += 1
            t.lost_frames = 0
            t.history_boxes.append(t.last_box)
            if len(t.history_boxes) > 60: t.history_boxes.pop(0)

            # --- Associate Mask ---
            best_iou = 0.0
            associated_mask = None
            
            # Find the YOLO detection that matches this Track
            for y_det in yolo_dets:
                iou = self._calculate_iou(t.last_box, y_det['box'])
                if iou > best_iou:
                    best_iou = iou
                    associated_mask = y_det['mask']
            
            # If we found a mask and IoU is strong enough
            if associated_mask is not None and best_iou > 0.3:
                # Process the mask: Crop -> Resize 64x64 -> Pad
                processed_silhouette = process_mask(associated_mask, t.last_box, output_size=(64, 64))
                state.silhouette_history.append(processed_silhouette)
            
            # Update Tracklet Data for downstream consumers (Gait Engine)
            t.gait_sequence_data = list(state.silhouette_history)
            current_tracklets.append(t)

        # ---- Step 5: Prune Lost Tracks ----
        self._increment_lost_and_prune(active_ids)

        # ---- Step 6: Ring Buffer ----
        for t in current_tracklets:
            # We store the latest silhouette in the 'pose' field of the ring buffer
            # to allow retrospective analysis if needed.
            self.ring_buffer.add(
                track_id=t.track_id,
                ts=frame.ts,
                frame_index=frame.frame_id,
                bbox=t.last_box,
                pose=t.gait_sequence_data[-1] if t.gait_sequence_data else None 
            )

        return current_tracklets

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _calculate_iou(self, boxA, boxB):
        """Calculates Intersection over Union between two boxes."""
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter_area = max(0, xB - xA) * max(0, yB - yA)
        boxA_area = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxB_area = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        union_area = float(boxA_area + boxB_area - inter_area)
        return inter_area / union_area if union_area > 0 else 0.0

    def _increment_lost_and_prune(self, active_ids: set) -> None:
        """Removes tracks that have been lost for too many frames."""
        to_remove = []
        for tid, state in self._states.items():
            if tid not in active_ids:
                state.tracklet.lost_frames += 1
                if state.tracklet.lost_frames > self.max_lost_frames:
                    to_remove.append(tid)
        for tid in to_remove:
            self._states.pop(tid, None)
            self.ring_buffer.remove_track(tid)