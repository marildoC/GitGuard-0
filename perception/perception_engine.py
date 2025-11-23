# perception/perception_engine.py
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple
import cv2

import numpy as np
import torch

from collections import deque #IMPORT added by Francesco

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
    Manages the internal state for a single tracked entity within the perception engine.

    How it works:
    This class acts as a private container for data that needs to be maintained
    for each track, beyond what's exposed in the `Tracklet` schema. It stores
    the public `Tracklet` object, appearance features, and critical gait-related
    information like an Exponential Moving Average (EMA) of keypoints and a
    history of smoothed poses. This separation allows for richer internal state
    management without bloating the public `Tracklet` schema.

    Attributes:
    - tracklet (Tracklet): The public `Tracklet` object representing this track,
                           which gets updated and eventually returned by the engine.
    - appearance_feature (Optional[np.ndarray]): A feature vector representing the
                                                 visual appearance of the tracked person.
                                                 Updated by the tracker.
    - kp_ema (Optional[np.ndarray]): Exponential Moving Average of the latest keypoints.
                                     Used to smooth out noisy pose detections frame-to-frame.
    - kp_history (deque[np.ndarray]): A double-ended queue storing a sequence of
                                       smoothed keypoint arrays (poses) for gait analysis.
                                       Its `maxlen` determines the length of the gait sequence.
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
        #Keypoint EMA and history for the temporal encoder
        self.kp_ema: Optional[np.ndarray]=None
        self.kp_history: deque[np.ndarray]=deque(maxlen=30) #Maxlen for pose's sequences
        
# ---------------------------------------------------------------------------
# Drawing Helpers (NEW: Extracted from yolov8n_pose_conID.py for internal or future use)
# --- Definition of skeleton pairs for YOLOv8-pose (COCO format) ---
# MediaPipe and COCO have different indices. These are the standard COCO.
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

def extract_yolo_boxes_keypoints(yolo_result) -> Tuple[List[List[float]], List[float], List[np.ndarray]]:
    """
    Extracts bounding boxes, confidence scores, and keypoints from a YOLOv8-pose prediction result.

    How it works:
    This function takes the raw output object from a YOLOv8-pose `model.predict` call.
    It checks for the presence of `boxes` and `keypoints` attributes within the result.
    If present, it extracts `xyxy` (bounding box coordinates) and `conf` (confidence scores)
    from `yolo_result.boxes`, converting them to standard Python lists.
    For keypoints, it retrieves the `data` attribute from `yolo_result.keypoints`, which
    contains normalized (x, y, confidence) coordinates for each keypoint. These are then
    added to a list of NumPy arrays, one array per detected person. Error handling is included
    to gracefully manage cases where attributes might be missing or data extraction fails.

    Args:
        yolo_result: The result object obtained from `model.predict` (e.g., `results[0]`).

    Returns:
        Tuple[List[List[float]], List[float], List[np.ndarray]]:
            - boxes: A list of bounding boxes, each as [x1, y1, x2, y2] in pixel coordinates.
            - scores: A list of confidence scores corresponding to each detected box.
            - keypoints: A list of NumPy arrays. Each array is of shape (K, 3), where K is
                         the number of keypoints, and each row is (x_norm, y_norm, confidence)
                         in normalized [0,1] image coordinates.
    """
    boxes = []
    scores = []
    keypoints_list = [] # List of (K,3) numpy arrays
    try:
        if hasattr(yolo_result, "boxes") and yolo_result.boxes is not None:
            xyxy = yolo_result.boxes.xyxy.cpu().numpy() # x1,y1,x2,y2 pixel coords
            confs = yolo_result.boxes.conf.cpu().numpy()
            for i in range(xyxy.shape[0]):
                boxes.append([float(xyxy[i,0]), float(xyxy[i,1]), float(xyxy[i,2]), float(xyxy[i,3])])
                scores.append(float(confs[i]))
        
        if hasattr(yolo_result, "keypoints") and yolo_result.keypoints is not None:
            # keypoints are in normalized [0,1] coordinates relative to the image
            # and usually come as (N, K, 3) where last dim is (x,y,confidence)
            kp_data = yolo_result.keypoints.data.cpu().numpy() # N x K x 3 (x,y,conf)
            for i in range(kp_data.shape[0]):
                keypoints_list.append(kp_data[i])
    except Exception as e:
        logger.error(f"Error extracting YOLOv8-pose results: {e}")
        boxes = []
        scores = []
        keypoints_list = []

    return boxes, scores, keypoints_list


# ---------------------------------------------------------------------------
# Main Perception Engine (Phase 1)
# ---------------------------------------------------------------------------

class Phase1PerceptionEngine(PerceptionEngine):
    """
    The main perception pipeline responsible for detecting people, estimating their poses,
    tracking them, and managing their associated data over time.

    How it works:
    This engine integrates several modules: a YOLOv8-pose model for combined detection
    and pose estimation, an OCSort tracker for maintaining persistent identities across frames,
    and an AppearanceExtractor for generating appearance features. It processes each
    incoming `Frame`, performs detection and tracking, updates the internal state for
    each `Tracklet` (including smoothed pose history via EMA), and maintains a `RingBuffer`
    for historical data. Lost tracks are pruned after a specified number of frames.

    Steps per frame:
      1. Combined YOLOv8-pose Detection + Pose Estimation: Identifies persons and their keypoints.
      2. Appearance Features: Extracts visual features for each detected person.
      3. Tracking (OC-SORT): Associates new detections with existing tracks or initializes new ones.
      4. Update Track States: Updates public `Tracklet` objects, applies EMA smoothing to keypoints,
         and stores smoothed pose history.
      5. Mark & Remove Lost Tracks: Increments lost counters for tracks not seen in the current frame
         and removes those that have been lost for too long.
      6. Update Ring Buffer: Stores recent track data (including poses) for further processing.

    Attributes:
    - gait_config (GaitConfig): Configuration object for gait-related parameters.
    - yolo_pose_model (YOLO): The loaded YOLOv8-pose model for detection and pose estimation.
    - tracker (OCSortTracker): The object tracker instance.
    - appearance (AppearanceExtractor): The appearance feature extraction module.
    - ring_buffer (RingBuffer): A buffer to store historical data for active tracks.
    - _states (Dict[int, TrackState]): Internal dictionary mapping `track_id` to its `TrackState`.
    - max_lost_frames (int): The maximum number of frames a track can be lost before being removed.
    - keypoint_ema_alpha (float): Alpha parameter for Exponential Moving Average (EMA) smoothing of keypoints.
    - keypoint_history_length (int): Maximum length of the keypoint history deque.
    """

    def __init__(
        self,
        
        tracker: Optional[OCSortTracker] = None,
        appearance: Optional[AppearanceExtractor] = None,
        ring_buffer: Optional[RingBuffer] = None,
        max_lost_frames: int = 30,

        #added: param for smoothing EMA of keypoint (Francesco and Vittorio)
        keypoint_ema_alpha: float = 0.65, #Default value of the experiment
        keypoint_history_length:int=30, #Default
        gait_config: Optional[GaitConfig]=None
    ):
        super().__init__()

        from gait.config import GaitConfig, default_gait_config
        self.gait_config = gait_config if gait_config is not None else default_gait_config()

        #YOLOv8-pose Model per detection e pose estimation
        self.yolo_pose_model = YOLO(self.gait_config.models.pose_model_name) #"yolov8n-pose.pt"
        try:
            self.yolo_pose_model.fuse()
        except Exception:
            logger.warning("YOLO model fuse failed, continuing without fusing layers.")
            pass # Non tutti i modelli possono essere fusi (es. se già fusi)

        # Warmup (if CUDA available)
        if self.gait_config.device.use_half and self.gait_config.device.device == "cuda":
            try:
                self.yolo_pose_model.to("cuda")
                _ = self.yolo_pose_model.predict(
                    source=torch.zeros(1, 3, self.gait_config.route.img_size, self.gait_config.route.img_size).cuda(),
                    imgsz=self.gait_config.route.img_size,
                    half=True,
                    verbose=False
                )
                logger.info("YOLOv8-pose model warmed up on GPU.")
            except Exception as e:
                logger.error(f"YOLOv8-pose GPU warmup failed: {e}")
                self.yolo_pose_model.to("cpu") # Fallback to CPU
        else:
            self.yolo_pose_model.to("cpu")
            logger.info("YOLOv8-pose model loaded on CPU.")

        # Modules
        self.tracker = tracker or OCSortTracker()
        self.appearance = appearance or AppearanceExtractor()
        self.ring_buffer = ring_buffer or RingBuffer(RingBufferConfig())

        # Internal state for each track_id
        self._states: Dict[int, TrackState] = {}

        # When to drop a lost track
        self.max_lost_frames = max_lost_frames

        #Keypoint smoothing parameters (Added by Fra and Vit)
        self.keypoint_ema_alpha = keypoint_ema_alpha
        self.keypoint_history_length = keypoint_history_length
        
        logger.info("Phase-1 PerceptionEngine initialized.")

    # ------------------------------------------------------------------ #
    # REQUIRED API
    # ------------------------------------------------------------------ #

    def process_frame(self, frame: Frame) -> List[Tracklet]:
        """
        Processes a single video frame to detect persons, estimate poses, track them,
        and update their associated states.

        How it works:
        1.  **YOLOv8-pose Inference**: The `yolo_pose_model` performs detection and pose
            estimation on the input `frame.image`. It returns bounding boxes,
            confidence scores, and normalized keypoint data.
        2.  **Appearance Feature Extraction**: `AppearanceExtractor` computes a feature
            vector for each detected person's bounding box.
        3.  **OC-SORT Tracking**: The `tracker` uses the detections and appearance
            features to update its internal tracks, associating new detections with
            existing tracks or initializing new ones.
        4.  **Track State Update**: For each active track reported by the tracker:
            *   Its corresponding `TrackState` is retrieved or initialized.
            *   The public `Tracklet` (`state.tracklet`) is updated with the latest
                bounding box, confidence, and age from the tracker.
            *   Keypoints from YOLOv8-pose are associated with the track (based on IoU with its bbox).
            *   If associated keypoints are found, an Exponential Moving Average (EMA)
                is applied to smooth them. These smoothed keypoints are then added
                to the track's `kp_history` deque.
            *   The `gait_sequence_data` of the public `Tracklet` is updated with this history.
        5.  **Lost Track Pruning**: The `_increment_lost_and_prune` helper function
            identifies tracks that were not updated in the current frame, increments their
            `lost_frames` counter, and removes tracks that exceed `max_lost_frames`.
        6.  **Ring Buffer Update**: Data for all currently active `Tracklet`s (including
            their latest smoothed pose) is added to the `RingBuffer` for historical context.

        Args:
            frame (Frame): The current frame object to be processed, containing the image data.

        Returns:
            List[Tracklet]: A list of all currently active `Tracklet` objects,
                            each representing a tracked person with its updated data.

        Raises:
            ValueError: If `frame.image` is None.

        """
        
        if frame.image is None:
            logger.warning("process_frame: empty frame.image")
            return []

        # ---- Step 1: Combined YOLOv8-pose Detection + Pose Estimation ----
        # YOLOv8-pose directly returns boxes and keypoints
        yolo_results = self.yolo_pose_model.predict(
            source=frame.image,
            device=self.gait_config.device.device,
            imgsz=self.gait_config.route.img_size, # Img size from config
            conf=self.gait_config.thresholds.min_visibility, # Use min_visibility as conf_thresh for YOLO
            iou=0.45, # IOU threshold for YOLO's NMS
            classes=[0], # Only detect person class
            half=self.gait_config.device.use_half,
            verbose=False,
            task="pose"
        )
        boxes, scores, keypoints_data = extract_yolo_boxes_keypoints(yolo_results[0])
        detections_for_tracker = []
        
        for i in range(len(boxes)):
            detections_for_tracker.append(
                [boxes[i][0], boxes[i][1], boxes[i][2], boxes[i][3], scores[i]]
            )
        detections_for_tracker = np.array(detections_for_tracker) if detections_for_tracker else np.empty((0,5))

        detections_for_ocsort = [
            Detection(x1=b[0], y1=b[1], x2=b[2], y2=b[3], score=s, class_id=0, class_name="person")
            for b, s in zip(boxes, scores)
        ]
        # ---- Step 2: Appearance Features (one per detection) ----
        features = self.appearance.compute_features_for_detections(
            frame, detections_for_ocsort
        )

        # ---- Step 3: OC-SORT Tracking ----
        tracks: List[Track] = self.tracker.update(detections_for_ocsort, features)

        # Create a map from YOLO detection index to its keypoints data
        det_idx_to_keypoints_map: Dict[int, np.ndarray] = {i: kp for i, kp in enumerate(keypoints_data)}
        # Create a map of detection boxes for easy association with tracks
        boxes_xyxy_map: Dict[int, Tuple[float, float, float, float]] = {
            i: tuple(b) for i, b in enumerate(boxes)
        }

        # ---- Step 4: Update Track States ----
        active_ids = set()
        current_tracklets: List[Tracklet] = [] 

        for tr in tracks:
            tid=tr.track_id
            active_ids.add(tid)
            
            if tid not in self._states:
                self._states[tid] = TrackState(
                    track_id=tid,
                    camera_id=frame.camera_id,
                )
                self._states[tid].kp_history = deque(maxlen=self.keypoint_history_length)
            
            state = self._states[tid]
            t = state.tracklet

            # --- Update public Tracklet with tracker data ---
            t.last_frame_id = frame.frame_id
            t.last_box = tuple(tr.bbox.tolist()) # Bounding box from the tracker
            t.confidence = tr.score
            t.age_frames += 1
            t.lost_frames = 0
            t.history_boxes.append(t.last_box)
            if len(t.history_boxes) > 60: # Keep history short for performance/memory
                t.history_boxes.pop(0)

            best_iou_with_yolo_det = 0.0
            associated_kp_data: Optional[np.ndarray] = None

            # Find the best matching YOLOv8-pose detection (and its keypoints) for the current track's bbox

            for det_idx, yolo_bbox in boxes_xyxy_map.items():
                iou_val = self._calculate_iou(t.last_box, yolo_bbox)
                if iou_val > best_iou_with_yolo_det:
                    best_iou_with_yolo_det = iou_val
                    associated_kp_data = det_idx_to_keypoints_map.get(det_idx)
            
            # Apply EMA to keypoints if found and valid
            if associated_kp_data is not None and associated_kp_data.shape[0] > 0:
                # Normalizza i keypoint se non lo sono già
                # yolo_results.orig_shape è (H, W) del frame originale
                img_h, img_w = frame.image.shape[:2] # Usa le dimensioni del frame attuale

                # Crea una copia per non modificare l'array originale in-place se viene riutilizzato altrove
                normalized_kp_data = associated_kp_data.copy()
                normalized_kp_data[:, 0] = normalized_kp_data[:, 0] / img_w # Normalizza X
                normalized_kp_data[:, 1] = normalized_kp_data[:, 1] / img_h # Normalizza Y

                if state.kp_ema is None:
                    state.kp_ema = normalized_kp_data.copy()
                else:
                    if state.kp_ema.shape == normalized_kp_data.shape:
                        state.kp_ema = self.keypoint_ema_alpha * normalized_kp_data + \
                                    (1.0 - self.keypoint_ema_alpha) * state.kp_ema
                    else:
                        logger.warning(f"Keypoint shape mismatch for track {tid}. Resetting EMA.")
                        state.kp_ema = normalized_kp_data.copy()
                
                state.kp_history.append(state.kp_ema.copy())
            else:
                logger.debug(f"Track {tid}: No valid keypoint data associated or extracted.")
            
            # Update the public Tracklet with the smoothed pose history
            t.gait_sequence_data = list(state.kp_history)
            current_tracklets.append(t)

        # ---- Step 5: Mark & Remove Lost Tracks ----
        self._increment_lost_and_prune(active_ids)

        # ---- Step 6: Update Ring Buffer ----
        # The ring buffer needs data for each track, including the latest smoothed pose.
        for t in current_tracklets: # Iterate over public Tracklets that have been updated
            # We need the appearance feature from the internal TrackState
            # Find the corresponding state for the current Tracklet t
            state_for_ring_buffer = self._states.get(t.track_id)
            appearance_feature_for_rb = state_for_ring_buffer.appearance_feature if state_for_ring_buffer else None
            self.ring_buffer.add(
                track_id=t.track_id,
                ts=frame.ts,
                frame_index=frame.frame_id,
                bbox=t.last_box,
                crop=None, # Crop is not used here but is part of the schema
                appearance=appearance_feature_for_rb,
                pose=t.gait_sequence_data[-1] if t.gait_sequence_data else None,
            )
        # Return list of active tracklets
        return current_tracklets

    # ------------------------------------------------------------------ #
    # INTERNAL HELPERS
    # ------------------------------------------------------------------ #

    def _calculate_iou(self, boxA: Tuple[float, ...], boxB: Tuple[float, ...]) -> float:
        """
        Calculates the Intersection over Union (IoU) between two bounding boxes.

        How it works:
        It takes two bounding boxes, each defined by `(x1, y1, x2, y2)` coordinates.
        It first determines the coordinates of the intersection rectangle. If there's no
        overlap, the intersection area is zero. Otherwise, it calculates the area of
        the intersection and the area of each individual bounding box. The IoU is then
        computed as the ratio of the intersection area to the union area (sum of individual
        areas minus intersection area).

        Args:
            boxA (Tuple[float, ...]): The first bounding box as (x1, y1, x2, y2).
            boxB (Tuple[float, ...]): The second bounding box as (x1, y1, x2, y2).

        Returns:
            float: The IoU score, a value between 0.0 and 1.0. Returns 0.0 if union area is 0.
        """
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter_w = max(0, xB - xA)
        inter_h = max(0, yB - yA)
        inter_area = inter_w * inter_h
        boxA_area = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxB_area = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        union_area = float(boxA_area + boxB_area - inter_area)
        return inter_area / union_area if union_area > 0 else 0.0

    def _increment_lost_and_prune(self, active_ids: set) -> None:
        """
        Increments the `lost_frames` counter for tracks that were not updated
        in the current frame and removes tracks that have been lost for too long.

        How it works:
        It iterates through all currently known `TrackState` objects. For each
        track, if its `track_id` is not present in the `active_ids` set (meaning
        it wasn't seen in the current frame), its `lost_frames` counter is incremented.
        If `lost_frames` exceeds `self.max_lost_frames`, the track's ID is added
        to a `to_remove` list. After checking all tracks, those IDs in `to_remove`
        are then purged from the internal `_states` dictionary and the `ring_buffer`.

        Args:
            active_ids (set): A set of `track_id`s that were successfully updated
                              or created in the current frame.
        """
        to_remove = []

        for tid, state in self._states.items():
            if tid not in active_ids:
                state.tracklet.lost_frames += 1
                if state.tracklet.lost_frames > self.max_lost_frames:
                    to_remove.append(tid)

        # Remove from internal state + ring buffer
        for tid in to_remove:
            self._states.pop(tid, None)
            self.ring_buffer.remove_track(tid)
            logger.debug(f"Removed track {tid} (lost too long).")
