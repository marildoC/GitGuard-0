"""
enrollment_cli.py
A script to register new gaits.
"""
import cv2
import os
import logging
import numpy as np
from pathlib import Path
import time
import traceback
from tqdm import tqdm


from schemas import Frame, Tracklet
from perception.perception_engine import Phase1PerceptionEngine
from gait.gait_extractor import GaitExtractor
from gait.gait_gallery import GaitGallery
from gait.gait_engine import GaitEngine
from gait.config import default_gait_config 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def enroll_from_video(
    video_path: Path,
    identity_id: str,
    gait_engine: GaitEngine, 
    min_sequence_length: int = 24,
    max_lost_frames_in_sequence: int = 5 
) -> bool:
    """
    Processes a video to extract poses, compute the gait embedding, and register it
    in the gallery for a given identity_id.
    """
    if not video_path.exists():
        logger.error(f"Video file not found: {video_path}")
        return False

    logger.info(f"Starting enrollment for identity '{identity_id}' from video '{video_path.name}'...")

    # Instantiate a new perception pipeline for this enrollment video,
    # using the configs from the passed gait_engine for consistency.
    # This extracts poses in the same way as the main_loop.
    perception_engine = Phase1PerceptionEngine(
        keypoint_ema_alpha=gait_engine.config.route.keypoint_ema_alpha,
        keypoint_history_length=gait_engine.config.route.keypoint_history_length,
        gait_config=gait_engine.config, 
        max_lost_frames=max_lost_frames_in_sequence
    )
    
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error(f"Failed to open video {video_path}")
        return False

    frame_id = 0
    # Reset tracker for the new video
    perception_engine.tracker.reset() 
    perception_engine._states = {} # Reset internal track states
    
    enrollment_sequence_data = None # Will contain the final sequence used for enrollment
    found_track_id = -1

    pbar = tqdm(desc="Processing video frames for pose extraction")

    while True:
        ret, frame_img = cap.read()
        if not ret:
            break
        
        ts = time.perf_counter()
        h, w = frame_img.shape[:2]

        frame = Frame(
            frame_id=frame_id,
            ts=ts,
            camera_id="enrollment_cam",
            size=(w, h),
            image=frame_img,
        )
        
        current_tracks = perception_engine.process_frame(frame)
        
        # Try to take the first track with a valid pose sequence
        if current_tracks:
            # Sort tracks by age, selecting the longest-lasting or the one with most frames
            current_tracks.sort(key=lambda t: t.age_frames, reverse=True)
            
            # Choose the track with the longest valid sequence for enrollment
            best_track_for_enrollment = None
            for tracklet in current_tracks:
                if len(tracklet.gait_sequence_data) >= min_sequence_length:
                    best_track_for_enrollment = tracklet
                    break
            
            if best_track_for_enrollment:
                enrollment_sequence_data = best_track_for_enrollment.gait_sequence_data
                found_track_id = best_track_for_enrollment.track_id
            
        frame_id += 1
        pbar.update(1)

    pbar.close()
    cap.release()
    
    if enrollment_sequence_data is None:
        logger.error(f"Could not extract a valid pose sequence (min {min_sequence_length} frames) from video {video_path.name} for {identity_id}.")
        return False

    if len(enrollment_sequence_data) < min_sequence_length:
        logger.error(f"Extracted sequence for {identity_id} is too short ({len(enrollment_sequence_data)} frames). Minimum required: {min_sequence_length}.")
        return False
    
    logger.info(f"Successfully extracted sequence of {len(enrollment_sequence_data)} frames for '{identity_id}' (Track ID: {found_track_id}).")

    # Extract the embedding and quality using the GaitExtractor
    gait_embedding, gait_quality = gait_engine.extractor.extract_gait_embedding_and_quality(enrollment_sequence_data)

    if gait_embedding is None:
        logger.error(f"Failed to extract gait embedding for '{identity_id}' (Quality: {gait_quality:.2f}). Check min_gait_quality threshold.")
        return False

    # Add embedding to the gallery. `confirmed=True` because this is an explicit enrollment.
    gait_engine.gallery.add_gait_embedding(identity_id, gait_embedding, confirmed=True)
    logger.info(f"Identity '{identity_id}' successfully enrolled with gait embedding (Quality: {gait_quality:.2f}).")
    
    return True


if __name__ == "__main__":
    from gait.gait_engine import GaitEngine 

    # Initialize GaitEngine (this loads the model and gallery)
    gait_engine = GaitEngine()

    video_base_path = Path("data/gait_videos") # Your folder with person videos
    
    if not video_base_path.exists():
        logger.error(f"Video root directory {video_base_path} does not exist. Please create it and add videos like data/gait_videos/person_001/walk_01.mp4.")
        exit()

    
    successful_enrollments = 0
    total_enrollments_attempted = 0

    for identity_folder in video_base_path.iterdir():
        if identity_folder.is_dir():
            identity_id = identity_folder.name
            
            # Take the first available video for enrollment of this identity
            videos_for_id = list(identity_folder.glob("*.mp4"))
            if not videos_for_id:
                logger.warning(f"No video found for identity '{identity_id}'. Skipping enrollment for this ID.")
                continue

            video_to_use = videos_for_id[0] # Use the first video found
            total_enrollments_attempted += 1

            if enroll_from_video(video_to_use, identity_id, gait_engine):
                successful_enrollments += 1
            else:
                logger.error(f"Enrollment of '{identity_id}' failed for video {video_to_use.name}.")
    
    logger.info(f"Enrollment process finished. Successfully enrolled {successful_enrollments}/{total_enrollments_attempted} identities.")