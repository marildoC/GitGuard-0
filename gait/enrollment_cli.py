# gait/enrollment_cli.py

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
    """
    Configures the root logger to output messages to the console.
    Prevents duplicate handlers if logging is already set up.
    """
    root = logging.getLogger()
    if root.handlers: return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

def extract_raw_sequences_from_video(video_path: Path, pose_model: YOLO, min_len: int = 24) -> List[List[np.ndarray]]:
    """
    Reads a video file frame by frame, runs the pose estimation model (YOLO),
    and aggregates raw keypoints into sequences.
    
    Args:
        video_path: Path to the input video.
        pose_model: Loaded YOLO model for pose estimation.
        min_len: Minimum number of frames required to form a valid sequence.
        
    Returns:
        A list of sequences, where each sequence is a list of numpy arrays (raw keypoints).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning(f"Cannot open video {video_path}")
        return []

    frames_buffer = []
    sequences = []
    conf_thresh = 0.5 

    while True:
        ret, frame = cap.read()
        if not ret: break
        
        results = pose_model(frame, verbose=False, conf=conf_thresh)
        kps_raw = None
        
        if results[0].keypoints is not None:
            kps = results[0].keypoints
            if hasattr(kps, 'data') and kps.data.shape[0] > 0:
                kps_raw = kps.data[0].cpu().numpy() 
        
        if kps_raw is not None:
            frames_buffer.append(kps_raw)
        else:
            if len(frames_buffer) >= min_len:
                sequences.append(frames_buffer)
            frames_buffer = []

    if len(frames_buffer) >= min_len:
        sequences.append(frames_buffer)
        
    cap.release()
    return sequences

def cmd_enroll(args: argparse.Namespace) -> None:
    """
    Handles the 'enroll' command.
    1. Initializes the GaitExtractor and GaitGallery.
    2. Loads the Pose Estimation model.
    3. Iterates through video files in the data directory.
    4. Extracts gait embeddings and averages them per identity.
    5. Saves the confirmed identities to the gallery.
    """
    _setup_logging()
    cfg = default_gait_config()
    
    # Init Extractor (loads the .pth model)
    extractor = GaitExtractor(cfg)
    # Init Gallery (passes full config)
    gallery = GaitGallery(cfg)

    logger.info(f"Loading YOLO model: {cfg.models.pose_model_name}")
    pose_model = YOLO(cfg.models.pose_model_name)
    
    root_data_dir = Path("data/gait_videos")
    
    if args.name:
        target_dir = root_data_dir / args.name
        if not target_dir.exists():
            logger.error(f"Directory not found: {target_dir}")
            return
        persons_to_process = [(args.name, target_dir)]
    else:
        if not root_data_dir.exists():
            logger.error(f"Root directory not found: {root_data_dir}")
            return
        persons_to_process = [(p.name, p) for p in root_data_dir.iterdir() if p.is_dir()]

    logger.info(f"Found {len(persons_to_process)} identities to process.")

    for name, person_dir in persons_to_process:
        logger.info(f"Processing identity: {name}")
        
        video_files = list(person_dir.glob("*.mp4")) + list(person_dir.glob("*.mov")) + list(person_dir.glob("*.avi"))
        if not video_files:
            logger.warning(f"No videos found for {name}")
            continue
            
        valid_embeddings = []
        for v_file in video_files:
            logger.info(f"  - Extracting from {v_file.name}...")
            raw_sequences = extract_raw_sequences_from_video(v_file, pose_model, min_len=cfg.route.min_sequence_length)
            
            for seq in raw_sequences:
                emb, quality = extractor.extract_gait_embedding_and_quality(seq)
                if emb is not None:
                    valid_embeddings.append(emb)
        
        if not valid_embeddings:
            logger.warning(f"No valid gait embeddings extracted for {name}")
            continue
            
        embedding_matrix = np.stack(valid_embeddings)
        avg_embedding = np.mean(embedding_matrix, axis=0)
        norm = np.linalg.norm(avg_embedding)
        if norm > 1e-6: avg_embedding /= norm

        category = args.category if args.category else "resident"
        
        # USE CORRECT GAIT GALLERY METHODS
        gallery.add_gait_embedding(
            identity_id=name,
            new_embedding=avg_embedding,
            category=category,
            confirmed=True
        )
        logger.info(f"✅ Enrolled {name}")

    gallery.save_gallery()
    logger.info("Gallery saved successfully.")

def cmd_list(args: argparse.Namespace) -> None:
    """
    Handles the 'list' command.
    Displays all identities currently stored in the gallery.
    """
    _setup_logging()
    cfg = default_gait_config()
    gallery = GaitGallery(cfg)
    persons = gallery.list_persons()
    
    if not persons:
        print("No persons enrolled.")
        return
    print(f"{'person_id':<36} {'category':<12} {'name':<20}")
    print("-" * 70)
    for p in persons:
        print(f"{p.person_id:<36} {p.category:<12} {p.name:<20}")

def cmd_delete(args: argparse.Namespace) -> None:
    """
    Handles the 'delete' command.
    Removes a specific identity from the gallery based on person_id.
    """
    _setup_logging()
    cfg = default_gait_config()
    gallery = GaitGallery(cfg)

    person_id = args.person_id or input("person_id: ").strip()
    if gallery.delete_person(person_id):
        print(f"Deleted {person_id}")
    else:
        print("Person not found")

def main(argv: Optional[list[str]] = None) -> None:
    """
    Main entry point for the CLI tool.
    Parses arguments and delegates execution to the appropriate command function (enroll, list, delete).
    """
    if argv is None: argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="gait-enroll")
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser("enroll")
    p_enroll.add_argument("--name", type=str)
    p_enroll.add_argument("--category", type=str, default="resident")

    sub.add_parser("list")
    
    p_del = sub.add_parser("delete")
    p_del.add_argument("person_id", nargs="?", help="Identity ID")

    args = parser.parse_args(argv)
    
    if args.command == "enroll": cmd_enroll(args)
    elif args.command == "list": cmd_list(args)
    elif args.command == "delete": cmd_delete(args)

if __name__ == "__main__":
    main()