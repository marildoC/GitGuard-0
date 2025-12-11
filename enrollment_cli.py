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
    root = logging.getLogger()
    if root.handlers: return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

def get_smart_crop(mask: np.ndarray, box: List[float], target_size=(64, 64)) -> np.ndarray:
    """
    Cropping and resizing logic identical to training/perception engine.
    
    1. Crops the mask using the bounding box.
    2. Resizes to target_size (64x64) maintaining aspect ratio.
    3. Pads with black to center the silhouette.
    """
    x1, y1, x2, y2 = map(int, box)
    h_img, w_img = mask.shape
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w_img, x2), min(h_img, y2)
    
    if x2 <= x1 or y2 <= y1: 
        return np.zeros(target_size, dtype=np.uint8)
    
    crop = mask[y1:y2, x1:x2]
    h, w = crop.shape
    
    # Resize keeping aspect ratio
    scale = min(target_size[0]/w, target_size[1]/h)
    nw, nh = int(w*scale), int(h*scale)
    
    resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros(target_size, dtype=np.uint8)
    
    # Calculate centering offsets
    dx = (target_size[0] - nw) // 2
    dy = (target_size[1] - nh) // 2
    canvas[dy:dy+nh, dx:dx+nw] = resized
    
    # Normalize to 0-255 uint8
    if canvas.max() <= 1:
        canvas = (canvas * 255).astype(np.uint8)
        
    return canvas

def extract_silhouettes_from_video(video_path: Path, seg_model: YOLO, min_len: int = 30) -> List[List[np.ndarray]]:
    """
    Extracts silhouette sequences (64x64) from a video using YOLO-Seg.
    Uses batch processing for efficiency.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning(f"Cannot open video {video_path}")
        return []

    frames_buffer = []
    sequences = []
    
    # Buffer for batch inference
    batch_frames = []
    saved_debug_img= False
    while True:
        ret, frame = cap.read()
        if not ret: break
        
        batch_frames.append(frame)
        
        # Process every 16 frames to avoid memory saturation
        if len(batch_frames) == 16:
            results = seg_model.predict(batch_frames, verbose=False, classes=[0], retina_masks=True, conf=0.3)
            
            for res in results:
                silhouette = None
                if res.masks is not None:
                    # Pick the person with the highest confidence in the frame
                    # (Assumes enrollment video contains only the target subject)
                    best_idx = np.argmax(res.boxes.conf.cpu().numpy())
                    mask = res.masks.data[best_idx].cpu().numpy()
                    box = res.boxes.xyxy[best_idx].cpu().numpy()
                    
                    silhouette = get_smart_crop(mask, box)

                    # --- DEBUG SNIPPET ---
                    if not saved_debug_img and silhouette is not None:
                        # Save the first found silhouette to check extraction quality
                        debug_path = "DEBUG_silhouette.png"
                        cv2.imwrite(debug_path, silhouette)
                        logger.info(f"📸 [DEBUG] Saved test silhouette to: {debug_path}")
                        saved_debug_img = True
                    # --- END DEBUG ---

                if silhouette is not None:
                    frames_buffer.append(silhouette)
                else:
                    # If tracking is lost, close current sequence and start a new one
                    if len(frames_buffer) >= min_len:
                        sequences.append(frames_buffer)
                    frames_buffer = []
            
            batch_frames = []

    # Process remaining frames in buffer
    if batch_frames:
        results = seg_model.predict(batch_frames, verbose=False, classes=[0], retina_masks=True, conf=0.3)
        for res in results:
            silhouette = None
            if res.masks is not None:
                best_idx = np.argmax(res.boxes.conf.cpu().numpy())
                mask = res.masks.data[best_idx].cpu().numpy()
                box = res.boxes.xyxy[best_idx].cpu().numpy()
                silhouette = get_smart_crop(mask, box)
            
            if silhouette is not None:
                frames_buffer.append(silhouette)
            else:
                if len(frames_buffer) >= min_len:
                    sequences.append(frames_buffer)
                frames_buffer = []

    if len(frames_buffer) >= min_len:
        sequences.append(frames_buffer)
        
    cap.release()
    return sequences

def cmd_enroll(args: argparse.Namespace) -> None:
    """Command to enroll a person from video files."""
    _setup_logging()
    cfg = default_gait_config()
    
    # Init Extractor (loads GaitSetPlus) and Gallery
    extractor = GaitExtractor(cfg)
    gallery = GaitGallery(cfg)

    # Load Segmentation Model
    seg_model_name = "yolov8n-seg.pt" 
    logger.info(f"Loading Segmentation model: {seg_model_name}")
    seg_model = YOLO(seg_model_name)
    
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
        
        valid_embeddings = []
        for v_file in video_files:
            logger.info(f"  - Extracting silhouettes from {v_file.name}...")
            # Extraction based on Silhouette sequences
            raw_sequences = extract_silhouettes_from_video(v_file, seg_model, min_len=cfg.route.min_sequence_length)
            
            for seq in raw_sequences:
                emb, quality = extractor.extract_gait_embedding_and_quality(seq)
                if emb is not None:
                    valid_embeddings.append(emb)
        
        if not valid_embeddings:
            logger.warning(f"No valid gait embeddings extracted for {name}")
            continue
            
        # Average the embeddings for a stable template
        embedding_matrix = np.stack(valid_embeddings)
        avg_embedding = np.mean(embedding_matrix, axis=0)
        norm = np.linalg.norm(avg_embedding)
        if norm > 1e-6: avg_embedding /= norm

        category = args.category if args.category else "resident"
        
        gallery.add_gait_embedding(
            identity_id=name,
            new_embedding=avg_embedding,
            category=category,
            confirmed=True
        )
        logger.info(f" Enrolled {name}")

    gallery.save_gallery()
    logger.info("Gallery saved successfully.")

def cmd_list(args: argparse.Namespace) -> None:
    """Command to list all enrolled identities."""
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
    """Command to delete an identity."""
    _setup_logging()
    cfg = default_gait_config()
    gallery = GaitGallery(cfg)
    person_id = args.person_id or input("person_id: ").strip()
    if gallery.delete_person(person_id):
        print(f"Deleted {person_id}")
    else:
        print("Person not found")

def main(argv: Optional[list[str]] = None) -> None:
    if argv is None: argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="gait-enroll")
    sub = parser.add_subparsers(dest="command", required=True)
    
    p_enroll = sub.add_parser("enroll", help="Enroll a person from videos in data/gait_videos/")
    p_enroll.add_argument("--name", type=str, help="Specific folder name to enroll (optional)")
    p_enroll.add_argument("--category", type=str, default="resident", help="Category: resident, visitor, etc.")
    
    sub.add_parser("list", help="List enrolled persons")
    
    p_del = sub.add_parser("delete", help="Delete a person from gallery")
    p_del.add_argument("person_id", nargs="?", help="Identity ID to delete")
    
    args = parser.parse_args(argv)
    
    if args.command == "enroll": cmd_enroll(args)
    elif args.command == "list": cmd_list(args)
    elif args.command == "delete": cmd_delete(args)

if __name__ == "__main__":
    main()