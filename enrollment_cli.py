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
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

def extract_raw_sequences_from_video(video_path: Path, pose_model: YOLO, min_len: int = 24) -> List[List[np.ndarray]]:
    """
    Processes a video file to extract continuous sequences of skeleton keypoints.
    Returns a list of sequences, where each sequence is a list of (17, 3) keypoint arrays.
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
        
        results = pose_model(frame, verbose=False, conf=conf_thresh)
        kps_raw = None
        
        # Check for valid keypoint detections
        if (len(results) > 0 and 
            results[0].keypoints is not None and 
            results[0].keypoints.xyn.shape[0] > 0):
            
            kps = results[0].keypoints
            # Extract first detected person using normalized [0, 1] coordinates
            xy = kps.xyn[0].cpu().numpy()
            conf = kps.conf[0].cpu().numpy()[:, None] if kps.conf is not None else np.ones((xy.shape[0], 1))
            kps_raw = np.hstack([xy, conf])
        
        # Accumulate frames into continuous sequences. 
        # Breaks the sequence if tracking is lost.
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
    'enroll' command logic:
    1. Scans 'data/gait_videos' for subdirectories (each folder = one identity).
    2. Extracts all valid gait sequences from video files within those folders.
    3. Computes a mean normalized embedding for each person.
    4. Updates the persistent gait gallery.
    """
    _setup_logging()
    cfg = default_gait_config()
    extractor = GaitExtractor(cfg)
    gallery = GaitGallery(cfg)
    pose_model = YOLO(cfg.models.pose_model_name)
    
    root_data_dir = Path("data/gait_videos")
    
    # Filter by specific name if provided, otherwise process all subdirectories
    if args.name:
        target_dir = root_data_dir / args.name
        if not target_dir.exists():
            logger.error(f"Directory not found: {target_dir}")
            return
        persons_to_process = [(args.name, target_dir)]
    else:
        if not root_data_dir.exists():
            logger.error("Root gait_videos directory not found.")
            return
        persons_to_process = [(p.name, p) for p in root_data_dir.iterdir() if p.is_dir()]

    for name, person_dir in persons_to_process:
        logger.info(f"Processing identity: {name}")
        video_files = [f for f in person_dir.iterdir() if f.suffix.lower() in [".mp4", ".mov", ".avi"]]
            
        valid_embeddings = []
        for v_file in video_files:
            raw_sequences = extract_raw_sequences_from_video(v_file, pose_model, min_len=cfg.route.min_sequence_length)
            for seq in raw_sequences:
                emb, _ = extractor.extract_gait_embedding_and_quality(seq)
                if emb is not None:
                    valid_embeddings.append(emb)
        
        if not valid_embeddings:
            logger.warning(f"No valid embeddings for {name}")
            continue
            
        # Average multiple embeddings to create a robust identity template
        avg_embedding = np.mean(np.stack(valid_embeddings), axis=0)
        norm = np.linalg.norm(avg_embedding)
        if norm > 1e-6: 
            avg_embedding /= norm
            gallery.add_gait_embedding(identity_id=name, new_embedding=avg_embedding, 
                                       category=args.category or "resident", confirmed=True)
            logger.info(f"✅ Successfully enrolled {name}")

    gallery.save_gallery()

def cmd_list(args: argparse.Namespace) -> None:
    """Displays all enrolled identities in the gallery."""
    _setup_logging()
    gallery = GaitGallery(default_gait_config())
    persons = gallery.list_persons()
    
    if not persons:
        print("Gallery is empty.")
        return
    print(f"{'person_id':<36} {'category':<12} {'name':<20}")
    print("-" * 70)
    for p in persons:
        print(f"{p.person_id:<36} {p.category:<12} {p.name:<20}")

def cmd_delete(args: argparse.Namespace) -> None:
    """Removes a person from the database by ID."""
    _setup_logging()
    gallery = GaitGallery(default_gait_config())
    person_id = args.person_id or input("Enter person_id to delete: ").strip()
    if gallery.delete_person(person_id):
        print(f"Deleted {person_id}")
    else:
        print("Identity not found.")

def main(argv: Optional[list[str]] = None) -> None:
    """Main entry point for gait database administration."""
    if argv is None: argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="gait-enroll")
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser("enroll")
    p_enroll.add_argument("--name", type=str, help="Specific folder name to process")
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