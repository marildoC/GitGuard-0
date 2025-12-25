import numpy as np
import torch
import torch.nn as nn
import logging
import traceback
from pathlib import Path
from typing import List, Optional, Tuple
from gait.config import GaitConfig

logger = logging.getLogger(__name__)

class GaitModel(nn.Module):
    """
    Angle-Invariant Architecture combining CNN for local spatial features 
    and GRU for temporal sequence modeling.
    """
    def __init__(self, input_dim=64, hidden_dim=256, embedding_dim=256, dropout=0.5):
        super().__init__()
        # Spatial feature extraction
        self.cnn = nn.Sequential(
            nn.Conv1d(input_dim, 128, kernel_size=3, padding=1),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Conv1d(128, 128, kernel_size=3, padding=1),
            nn.BatchNorm1d(128),
            nn.ReLU()
        )
        # Temporal sequence modeling
        self.gru = nn.GRU(128, hidden_dim, num_layers=2, batch_first=True,
                          bidirectional=True, dropout=dropout)
        # Feature reduction and normalization neck
        self.bottleneck = nn.Sequential(
            nn.Linear(hidden_dim * 2, 512),
            nn.BatchNorm1d(512),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, embedding_dim)
        )
        self.bn_neck = nn.BatchNorm1d(embedding_dim)
        self.bn_neck.bias.requires_grad_(False)

    def forward(self, x, return_feature=False):
        x = x.transpose(1, 2)
        x = self.cnn(x).transpose(1, 2)
        out, _ = self.gru(x)
        # Global pooling (Mean + Max) to capture sequence statistics
        global_feat = out.mean(dim=1) + out.max(dim=1)[0]
        feat = self.bottleneck(global_feat)
        return feat if return_feature else self.bn_neck(feat)


class GaitExtractor:
    """
    Handles pose sequence preprocessing and embedding extraction.
    """
    def __init__(self, config: GaitConfig):
        self.config = config
        self.device = torch.device(config.device.device)
        # Target COCO indices: Shoulders (5,6), Hips (11,12), Knees (13,14), Ankles (15,16)
        self.target_indices = [5, 6, 11, 12, 13, 14, 15, 16]

        try:
            self.model = GaitModel(input_dim=64, embedding_dim=config.gallery.dim).to(self.device)
            model_path = Path(config.models.gait_embedding_model_path)
            if model_path.exists():
                self.model.load_state_dict(torch.load(model_path, map_location=self.device))
                self.model.eval()
                logger.info(f"Gait model loaded from {model_path}")
            else:
                logger.error("Gait model weights not found!")
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            self.model = None

    def _calculate_pose_quality(self, pose_sequence: List[np.ndarray]) -> float:
        """Computes the average confidence score of target joints across the sequence."""
        if not pose_sequence: return 0.0
        confs = [np.mean(pose[self.target_indices, 2]) for pose in pose_sequence]
        return float(np.mean(confs))

    def _preprocess_sequence(self, raw_sequence: List[np.ndarray]) -> torch.Tensor:
        """
        Engineers 64-dimensional feature vectors from raw keypoints:
        - Position (16), Velocity (16), Pairwise Distances (28), and Joint Angles (4).
        Includes height normalization and centering.
        """
        seq_np = np.array(raw_sequence)
        kp = seq_np[:, self.target_indices, :2] 
        
        # Height normalization (centered on hips)
        centers = (kp[:, 2] + kp[:, 3]) / 2.0
        top = np.mean(kp[:, 0:2, 1], axis=1) 
        bot = np.mean(kp[:, 6:8, 1], axis=1) 
        h = np.mean(np.abs(bot - top)) + 1e-6
        kp = (kp - centers[:, None, :]) / h

        T, K, _ = kp.shape
        pos = kp.reshape(T, K * 2)
        vel = np.diff(pos, axis=0, prepend=pos[:1]) * 5.0
        
        # Euclidean distances between all target joint pairs
        dists = [np.linalg.norm(kp[:, i] - kp[:, j], axis=-1, keepdims=True) 
                 for i in range(8) for j in range(i + 1, 8)]
        dist_feat = np.concatenate(dists, axis=1)
        
        # Joint angle calculations (Hip-Knee-Ankle and Shoulder-Hip-Knee)
        def ang(p1, p2, p3):
            v1, v2 = p1 - p2, p3 - p2
            cos = np.sum(v1 * v2, axis=-1, keepdims=True) / (
                np.linalg.norm(v1, axis=-1, keepdims=True) * np.linalg.norm(v2, axis=-1, keepdims=True) + 1e-6)
            return np.arccos(np.clip(cos, -1, 1))
            
        angles = [ang(kp[:, 2], kp[:, 4], kp[:, 6]), ang(kp[:, 3], kp[:, 5], kp[:, 7]),
                  ang(kp[:, 0], kp[:, 2], kp[:, 4]), ang(kp[:, 1], kp[:, 3], kp[:, 5])]
        
        features = np.concatenate([pos, vel, dist_feat] + angles, axis=1)
        return torch.from_numpy(features).float()

    def extract_gait_embedding_and_quality(self, pose_sequence: List[np.ndarray]) -> Tuple[Optional[np.ndarray], float]:
        """
        Main API: Validates sequence quality and extracts a normalized gait signature.
        """
        if not pose_sequence or self.model is None: 
            return None, 0.0

        quality = self._calculate_pose_quality(pose_sequence)
        if quality < self.config.thresholds.min_gait_quality or len(pose_sequence) < self.config.route.min_sequence_length:
            return None, quality

        try:
            features = self._preprocess_sequence(pose_sequence).unsqueeze(0).to(self.device)
            with torch.no_grad():
                embedding = self.model(features, return_feature=False)
            return embedding.cpu().numpy().flatten(), quality
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return None, quality