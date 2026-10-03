# identity/enrollment_cli.py - CLI tool to enroll / list / delete persons in the face gallery.

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

import cv2
import numpy as np

from face.config import default_face_config
from face.detector_align import FaceDetectorAligner
from face.quality import compute_full_quality
from identity.face_gallery import FaceGallery, PersonSummary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------


def _open_camera(
    index: int = 0,
    width: int = 640,
    height: int = 480,
    fps: int = 30,
) -> cv2.VideoCapture:
    """
    Open a camera device with basic configuration.
    """
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera index {index}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


def _setup_logging(level: int = logging.INFO) -> None:
    """
    Ensure root logger is configured for CLI use.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


# ---------------------------------------------------------------------------
# Enrollment logic
# ---------------------------------------------------------------------------


def _ensure_embedding(emb: np.ndarray) -> np.ndarray:
    """
    Ensure a 1-D float32, L2-normalised embedding.

    buffalo_l usually outputs L2-normalised 512-D vectors, but we
    enforce normalisation again for safety and consistency.
    """
    e = np.asarray(emb, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(e))
    if norm > 1e-6:
        e /= norm
    else:
        e[:] = 0.0
    return e


def _collect_face_embeddings_interactive(
    detector: FaceDetectorAligner,
    max_samples: int,
    camera_index: int,
) -> List[np.ndarray]:
    """
    Interactive capture loop:

    - Opens webcam.
    - On SPACE: runs face detector (buffalo_l) on the full frame,
      picks the best-quality face, and stores its embedding.
    - On ENTER: finish and return collected embeddings.
    - On ESC: abort and return empty list.
    """
    cfg = default_face_config()
    th = cfg.thresholds

    cap = _open_camera(camera_index)
    logger.info(
        "Camera opened on index %d. Press SPACE to capture, ENTER to finish, ESC to abort.",
        camera_index,
    )

    embeddings: List[np.ndarray] = []
    last_info = ""

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("Failed to read frame from camera")
                continue

            display = frame.copy()
            h, w = display.shape[:2]

            # HUD text
            if last_info:
                cv2.putText(
                    display,
                    last_info,
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2,
                )

            cv2.putText(
                display,
                f"samples: {len(embeddings)}/{max_samples}",
                (10, h - 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )

            cv2.imshow("GaitGuard Enrollment", display)
            key = cv2.waitKey(1) & 0xFF

            if key == 27:  # ESC
                logger.info("Enrollment aborted by user (ESC).")
                embeddings.clear()
                break

            if key == 13:  # ENTER
                logger.info("Enrollment finished by user (ENTER).")
                break

            if key == 32:  # SPACE -> capture
                logger.info("Capture requested (SPACE). Running face detector.")
                last_info = "Detecting face..."

                # Run buffalo_l-based detector on the full frame.
                candidates = detector.detect_and_align(frame)
                if not candidates:
                    last_info = "No face detected. Try again."
                    logger.info(last_info)
                    continue

                # Select best candidate by our quality metric.
                best_cand = None
                best_q = 0.0
                for cand in candidates:
                    q = compute_full_quality(
                        image=frame,
                        bbox=cand.bbox,
                        det_score=cand.det_score,
                        yaw=cand.yaw,
                        pitch=cand.pitch,
                        cfg=cfg,
                    )
                    if q > best_q:
                        best_q = q
                        best_cand = cand

                if best_cand is None:
                    last_info = "Face detection failed. Try again."
                    logger.info(last_info)
                    continue

                if best_q < th.min_quality_for_embed:
                    last_info = (
                        f"Face quality too low (q={best_q:.2f}). "
                        "Move closer / look at camera."
                    )
                    logger.info(last_info)
                    continue

                if best_cand.embedding is None:
                    last_info = "Detected face has no embedding. See logs."
                    logger.error("Best candidate returned without embedding.")
                    continue

                try:
                    emb = _ensure_embedding(best_cand.embedding)
                except Exception as exc:
                    logger.exception("Embedding normalisation failed: %s", exc)
                    last_info = "Embedding failed. See logs."
                    continue

                embeddings.append(emb)
                last_info = f"Captured sample #{len(embeddings)} (q={best_q:.2f})"
                logger.info(last_info)

                if len(embeddings) >= max_samples:
                    logger.info("Reached max_samples=%d", max_samples)
                    break

        return embeddings
    finally:
        cap.release()
        cv2.destroyAllWindows()


def cmd_enroll(args: argparse.Namespace) -> None:
    """
    Enroll a new person into the FaceGallery using live webcam capture.
    """
    _setup_logging()
    cfg = default_face_config()
    gallery = FaceGallery(cfg.gallery)
    detector = FaceDetectorAligner(cfg)

    logger.info("Starting enrollment process.")

    name = args.name or input("Name (optional, can be empty): ").strip()
    category = args.category or input(
        "Category [resident/visitor/watchlist] (default=resident): "
    ).strip()
    if not category:
        category = "resident"

    notes = args.notes or input("Notes (optional): ").strip()
    condition = args.condition or input(
        "Condition [neutral/glasses/cap/etc] (default=neutral): "
    ).strip()
    if not condition:
        condition = "neutral"

    max_samples = args.samples
    camera_index = args.camera

    embs = _collect_face_embeddings_interactive(
        detector=detector,
        max_samples=max_samples,
        camera_index=camera_index,
    )

    if not embs:
        logger.warning("No embeddings collected. Person will not be enrolled.")
        return

    emb_array = np.stack(embs, axis=0)
    person_id = gallery.enroll_person(
        embeddings=emb_array,
        name=name if name else None,
        category=category,
        condition=condition,
        notes=notes if notes else None,
    )
    gallery.save()

    print("\nEnrollment complete.")
    print(f"  person_id : {person_id}")
    print(f"  name      : {name or '(none)'}")
    print(f"  category  : {category}")
    print(f"  condition : {condition}")
    print(f"  samples   : {emb_array.shape[0]}")


# ---------------------------------------------------------------------------
# List / delete commands
# ---------------------------------------------------------------------------


def _print_persons(persons: List[PersonSummary]) -> None:
    if not persons:
        print("No persons enrolled in the gallery.")
        return

    print(f"{'person_id':<16} {'category':<10} {'name':<24} {'templates':>9}")
    print("-" * 64)
    for p in persons:
        name = p.name or ""
        print(f"{p.person_id:<16} {p.category:<10} {name:<24} {p.num_templates:>9}")


def cmd_list(args: argparse.Namespace) -> None:
    _setup_logging()
    cfg = default_face_config()
    gallery = FaceGallery(cfg.gallery)
    persons = gallery.list_persons()
    _print_persons(persons)


def cmd_delete(args: argparse.Namespace) -> None:
    _setup_logging()
    cfg = default_face_config()
    gallery = FaceGallery(cfg.gallery)

    person_id = args.person_id or input("person_id to delete: ").strip()
    if not person_id:
        print("No person_id provided.")
        return

    confirm = input(
        f"Are you sure you want to delete '{person_id}'? [y/N]: "
    ).strip().lower()
    if confirm not in ("y", "yes"):
        print("Deletion cancelled.")
        return

    ok = gallery.delete_person(person_id)
    if not ok:
        print(f"Person '{person_id}' not found.")
        return

    gallery.save()
    print(f"Person '{person_id}' deleted.")


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaitguard-enroll",
        description="GaitGuard Face Gallery enrollment CLI",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # enroll
    p_enroll = sub.add_parser("enroll", help="Enroll a new person via webcam")
    p_enroll.add_argument("--name", type=str, default="", help="Person name (optional)")
    p_enroll.add_argument(
        "--category",
        type=str,
        default="",
        help="Person category [resident/visitor/watchlist] (default=resident)",
    )
    p_enroll.add_argument(
        "--condition",
        type=str,
        default="",
        help="Face condition label [neutral/glasses/cap/etc] (default=neutral)",
    )
    p_enroll.add_argument("--notes", type=str, default="", help="Optional notes")
    p_enroll.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Maximum number of face samples to capture (default=5)",
    )
    p_enroll.add_argument(
        "--camera",
        type=int,
        default=0,
        help="Camera index (default=0)",
    )
    p_enroll.set_defaults(func=cmd_enroll)

    # list
    p_list = sub.add_parser("list", help="List enrolled persons")
    p_list.set_defaults(func=cmd_list)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a person from the gallery")
    p_delete.add_argument("person_id", nargs="?", help="person_id to delete")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    func(args)


if __name__ == "__main__":
    main()
