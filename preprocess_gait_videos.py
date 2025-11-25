#preprocess_gait_videos.py
# A script to preprocess videos for training a model
import cv2
import os
import json
import logging
import numpy as np
from pathlib import Path
from tqdm import tqdm 
import pickle 
from typing import List,Dict,Tuple


from schemas import Frame, Tracklet
from perception.perception_engine import Phase1PerceptionEngine
from gait.config import default_gait_config 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def extract_gait_sequences_from_videos(
    video_root_dir: Path,
    output_data_path: Path,
    min_sequence_length: int = 24,
    max_lost_frames_in_sequence: int = 5 # Allows small tracking gaps per sequence
) -> Dict[str, List[List[np.ndarray]]]:
    """
    Processes videos in video_root_dir, extracting pose sequences
    and associating them to an identity_id based on folder structure.

    Returns a dictionary: {identity_id: [list_of_gait_sequences_for_this_id]}
    """
    gait_data_by_id = {}
    
    # Initialize perception engine (only for pose extraction)
    # Uses the same config as the main loop for consistency
    gait_cfg = default_gait_config()
    perception_engine = Phase1PerceptionEngine(
        keypoint_ema_alpha=gait_cfg.route.keypoint_ema_alpha,
        keypoint_history_length=gait_cfg.route.keypoint_history_length,
        gait_config=gait_cfg,
        max_lost_frames=max_lost_frames_in_sequence # Allows small interruptions
    )
    
    for identity_folder in video_root_dir.iterdir():
        if not identity_folder.is_dir():
            continue

        identity_id = identity_folder.name
        gait_data_by_id[identity_id] = []
        logger.info(f"Processing videos for identity: {identity_id}")

        for video_file in identity_folder.glob("*.mp4"): 
            logger.info(f"  Processing video: {video_file.name}")
            cap = cv2.VideoCapture(str(video_file))
            if not cap.isOpened():
                logger.error(f"Failed to open video {video_file}")
                continue

            frame_id = 0
            # Reset the tracker for each video to treat each person as new
            perception_engine.tracker.reset() 
            perception_engine._states = {} # Reset internal track states too

            # Dictionaries to hold active sequences for each track_id in this video
            # A video might contain multiple tracks if there are multiple people
            # or if a person is lost and re-tracked with a new ID
            active_track_sequences = {} # {track_id: current_gait_sequence_data}

            while True:
                ret, frame_img = cap.read()
                if not ret:
                    break
                
                ts = time.perf_counter() # Monotonic timestamp for frame processing
                h, w = frame_img.shape[:2]

                frame = Frame(
                    frame_id=frame_id,
                    ts=ts,
                    camera_id="enrollment_cam", # Dummy camera ID
                    size=(w, h),
                    image=frame_img,
                )
                
                # Process frame through perception engine
                current_tracks = perception_engine.process_frame(frame)
                
                # Update active sequences and collect completed ones
                processed_track_ids = set()
                for tracklet in current_tracks:
                    processed_track_ids.add(tracklet.track_id)
                    # The gait_sequence_data is already smoothed and stored in tracklet
                    # by the perception engine
                    active_track_sequences[tracklet.track_id] = tracklet.gait_sequence_data
                
                # Check for tracks that were active but are now lost
                for tid in list(active_track_sequences.keys()):
                    if tid not in processed_track_ids:
                        # Track lost, finalize its sequence if long enough
                        sequence = active_track_sequences.pop(tid)
                        if len(sequence) >= min_sequence_length:
                            gait_data_by_id[identity_id].append(sequence)
                            logger.debug(f"  Collected a sequence of length {len(sequence)} for {identity_id}")
                        else:
                            logger.debug(f"  Discarded a short sequence ({len(sequence)} frames) for {identity_id}")

                frame_id += 1
            
            # After video ends, finalize any remaining active sequences
            for tid, sequence in active_track_sequences.items():
                if len(sequence) >= min_sequence_length:
                    gait_data_by_id[identity_id].append(sequence)
                    logger.debug(f"  Collected a final sequence of length {len(sequence)} for {identity_id}")
                else:
                    logger.debug(f"  Discarded a short final sequence ({len(sequence)} frames) for {identity_id}")
            
            cap.release()
            logger.info(f"  Finished processing video {video_file.name}. Collected {len(gait_data_by_id[identity_id])} sequences so far.")

    # save the pose dataset
    output_data_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_data_path, 'wb') as f:
        pickle.dump(gait_data_by_id, f)
    logger.info(f"Gait pose dataset saved to {output_data_path}")
    
    return gait_data_by_id


if __name__ == "__main__":
    import time # Import time per perf_counter

    video_base_path = Path("data/gait_videos")
    output_dataset_path = Path("data/gait_pose_dataset.pkl") # Our pose dataset

    # Ensure the video directory exists
    if not video_base_path.exists():
        logger.error(f"Video root directory {video_base_path} does not exist. Please create it and add videos.")
        exit()

    # Run extraction
    all_gait_sequences = extract_gait_sequences_from_videos(
        video_root_dir=video_base_path,
        output_data_path=output_dataset_path,
        min_sequence_length=default_gait_config().route.min_sequence_length
    )

    total_sequences = sum(len(seq_list) for seq_list in all_gait_sequences.values())
    logger.info(f"Total gait sequences collected: {total_sequences}")
    logger.info("Dataset creation complete. You can now use 'gait_pose_dataset.pkl' for training.")