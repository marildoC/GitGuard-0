"""
CLI tool for Gait Identity Management.
Supports batch enrollment from video files, identity listing, and deletion.
"""

from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional
import cv2
import numpy as np
from ultralytics import YOLO

from gait.config import default_gait_config
from gait.gait_gallery import GaitGallery
from gait.gait_extractor import GaitExtractor

logger = logging.getLogger(__name__)

def _setup_logging(level: int = logging.INFO) -> None:
    """Configures the console logger if not already initialized."""
    root = logging.getLogger()
    if root.handlers: return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

def extract_raw_sequences_from_video(video_path: Path, pose_model: YOLO, min_len: int = 24) -> List[List[np.ndarray]]:
    """
    Processes a video file to extract continuous sequences of skeleton keypoints.
    
    CRITICAL CHANGE:
    We now extract Pixel Coordinates (.xy) instead of Normalized Coordinates (.xyn).
    The new Gait Extractor requires absolute pixel values to correctly calculate 
    height and centroids for normalization.
    
    Returns:
        List of sequences, where each sequence is a list of (17, 3) keypoint arrays [x, y, conf].
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning(f"Unable to open video: {video_path}")
        return []

    frames_buffer = []
    sequences = []
    conf_thresh = 0.5 

    while True:
        ret, frame = cap.read()
        if not ret: break
        
        # Inference (Ultralytics handles OpenVINO automatically if model path is a folder)
        results = pose_model(frame, verbose=False, conf=conf_thresh)
        kps_raw = None
        
        # Check for valid keypoint detections to avoid IndexErrors
        if (len(results) > 0 and 
            results[0].keypoints is not None and 
            results[0].keypoints.data.shape[1] > 0): # Check if points exist
            
            kps = results[0].keypoints
            
            # Extract first detected person using PIXEL coordinates (.xy)
            # .xy gives [x, y] in original image pixels.
            # .xyn gives [0-1], which breaks the new height calculation logic.
            xy = kps.xy[0].cpu().numpy() 
            
            # Extract confidence scores for the 17 points
            if kps.conf is not None:
                conf = kps.conf[0].cpu().numpy()[:, None]
            else:
                # If confidence is missing, assume 1.0 (reliable)
                conf = np.ones((xy.shape[0], 1))
                
            # Stack into (17, 3) -> [x, y, confidence]
            kps_raw = np.hstack([xy, conf]) 
        
        # Logic to accumulate frames into continuous sequences
        if kps_raw is not None:
            frames_buffer.append(kps_raw)
        else:
            # If a frame is dropped (tracking lost), save current buffer if long enough
            if len(frames_buffer) >= min_len:
                sequences.append(frames_buffer)
            frames_buffer = [] # Reset buffer

    # Save the final sequence if valid
    if len(frames_buffer) >= min_len:
        sequences.append(frames_buffer)
        
    cap.release()
    return sequences

def cmd_enroll(args: argparse.Namespace) -> None:
    """
    'enroll' command logic:
    1. Initializes the new 256-dim Gait Extractor.
    2. Processes video files from 'data/gait_videos/{name}' or all folders.
    3. Extracts robust sequences and computes a mean embedding vector (Centroid).
    4. Saves the identity to the FAISS gallery.
    """
    _setup_logging()
    cfg = default_gait_config()
    
    # Initialize Core Components
    extractor = GaitExtractor(cfg)
    gallery = GaitGallery(cfg)
    
    logger.info(f"Loading Pose model: {cfg.models.pose_model_name}")
    pose_model = YOLO(cfg.models.pose_model_name)
    
    root_data_dir = Path("data/gait_videos")
    
    # Determine which folders to process
    if args.name:
        target_dir = root_data_dir / args.name
        if not target_dir.exists():
            logger.error(f"Directory not found: {target_dir}")
            return
        persons_to_process = [(args.name, target_dir)]
    else:
        if not root_data_dir.exists():
            logger.error("Root 'data/gait_videos' directory not found.")
            return
        persons_to_process = [(p.name, p) for p in root_data_dir.iterdir() if p.is_dir()]

    for name, person_dir in persons_to_process:
        logger.info(f"Processing identity: {name}")
        video_files = [f for f in person_dir.iterdir() if f.suffix.lower() in [".mp4", ".mov", ".avi"]]
            
        valid_embeddings = []
        for v_file in video_files:
            logger.info(f"  - Extracting from {v_file.name}...")
            raw_sequences = extract_raw_sequences_from_video(v_file, pose_model, min_len=cfg.route.min_sequence_length)
            
            for seq in raw_sequences:
                # Use the new Extractor logic (includes TTA and PowerNorm)
                emb, _ = extractor.extract_gait_embedding_and_quality(seq)
                if emb is not None:
                    valid_embeddings.append(emb)
        
        if not valid_embeddings:
            logger.warning(f"No valid embeddings extracted for {name}")
            continue
            
        # Create a robust template by averaging all valid embeddings.
        # This creates an "Angle-Invariant Centroid" for the person.
        avg_embedding = np.mean(np.stack(valid_embeddings), axis=0)
        
        # Mandatory L2 Normalization after averaging
        norm = np.linalg.norm(avg_embedding)
        if norm > 1e-6: 
            avg_embedding /= norm
            
            # Save to Gallery
            gallery.add_gait_embedding(
                identity_id=name, 
                new_embedding=avg_embedding, 
                category=args.category, 
                confirmed=True
            )
            logger.info(f"✅ Identity {name} enrolled successfully with {len(valid_embeddings)} sequences.")

    gallery.save_gallery()

def cmd_list(args: argparse.Namespace) -> None:
    """Lists all enrolled identities in the gallery."""
    _setup_logging()
    gallery = GaitGallery(default_gait_config())
    persons = gallery.list_persons()
    
    if not persons:
        print("Gallery is empty.")
        return
    print(f"{'person_id':<25} | {'category':<12} | {'templates':<10}")
    print("-" * 55)
    for p in persons:
        print(f"{p.person_id:<25} | {p.category:<12} | {p.num_templates:<10}")

def cmd_delete(args: argparse.Namespace) -> None:
    """Deletes an identity from the gallery."""
    _setup_logging()
    gallery = GaitGallery(default_gait_config())
    person_id = args.person_id or input("Enter person_id to delete: ").strip()
    if gallery.delete_person(person_id):
        print(f"✅ Deleted {person_id}")
    else:
        print("❌ Identity not found.")

def main(argv: Optional[list[str]] = None) -> None:
    if argv is None: argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="gait-enroll")
    sub = parser.add_subparsers(dest="command", required=True)

    # Enroll Command
    p_enroll = sub.add_parser("enroll")
    p_enroll.add_argument("--name", type=str, help="Specific folder name in data/gait_videos")
    p_enroll.add_argument("--category", type=str, default="resident")

    # List Command
    sub.add_parser("list")
    
    # Delete Command
    p_del = sub.add_parser("delete")
    p_del.add_argument("person_id", nargs="?", help="ID of the person to delete")

    args = parser.parse_args(argv)
    if args.command == "enroll": cmd_enroll(args)
    elif args.command == "list": cmd_list(args)
    elif args.command == "delete": cmd_delete(args)

if __name__ == "__main__":
    main()