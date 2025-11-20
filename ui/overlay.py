"""
ui/overlay.py

Overlay per visualizzare:
  - numero di track
  - keypoints delle pose (MediaPipe) estratti per ogni persona
"""

from __future__ import annotations

from typing import List

import cv2
import numpy as np
import mediapipe as mp

from schemas import Frame, Tracklet, IdentityDecision, EventFlags, Alert


# MediaPipe drawing utils
mp_drawing = mp.solutions.drawing_utils
mp_pose = mp.solutions.pose


def draw_overlay(
    frame: Frame,
    tracks: List[Tracklet],
    decisions: List[IdentityDecision],
    events: List[EventFlags],
    alerts: List[Alert],
) -> np.ndarray:

    if frame.image is None:
        raise ValueError("Frame.image is None inside draw_overlay")

    img = frame.image.copy()

    # ---------------------------------------------
    # 1) Status text
    # ---------------------------------------------
    status = f"tracks: {len(tracks)} | alerts: {len(alerts)}"
    cv2.putText(
        img,
        status,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (0, 255, 255),
        2,
    )

    # ---------------------------------------------
    # 2) Disegno keypoints MediaPipe su ogni track
    # ---------------------------------------------
    for t in tracks:

        # Bounding Box
        x1, y1, x2, y2 = map(int, t.last_box)

        # Se la sequenza di pose è vuota, salta
        if not t.gait_sequence_data:
            continue

        # Ultimo frame di keypoints (33x3)
        keypoints = t.gait_sequence_data[-1]

        # Ricostruzione oggetto NormalizedLandmarkList
        landmark_list = mp_pose.NormalizedLandmarkList(
            landmark=[
                mp_pose.NormalizedLandmark(x=float(k[0]), y=float(k[1]), z=0.0)
                for k in keypoints
            ]
        )

        # Bounding box decodificata
        w = x2 - x1
        h = y2 - y1
        if w <= 0 or h <= 0:
            continue

        # Estrai la ROI
        crop = img[y1:y2, x1:x2].copy()
        if crop.size == 0:
            continue

        # MediaPipe si aspetta keypoints normalizzati [0,1]
        # e sceglie coordinate rispetto all'immagine su cui disegna.
        # Quindi possiamo usare direttamente draw_landmarks sul crop:
        mp_drawing.draw_landmarks(
            crop,
            landmark_list,
            mp_pose.POSE_CONNECTIONS,
        )

        # Rimetti il crop disegnato nel frame principale
        img[y1:y2, x1:x2] = crop

        # (Opzionale) Disegna la bounding box
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)

    return img
