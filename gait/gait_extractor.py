# gait/extractor.py

import numpy as np
from typing import List, Optional, Tuple
import logging
import torch
from pathlib import Path
import torch.nn as nn
import torch.nn.functional as F
import traceback

from gait.config import GaitConfig

logger = logging.getLogger(__name__)

# ============================================================
#  LSTM MODEL
# ============================================================
class LSTMGaitEncoder(nn.Module):
    """
    Neural Network definition for Gait Recognition.
    Uses an LSTM to process temporal sequences of pose keypoints and
    outputs a fixed-size embedding vector.
    """
    def __init__(self, input_dim=51, hidden_dim=128, emb_dim=256, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_dim, emb_dim)
        
    def forward(self, x):
        """
        Forward pass of the network.
        Takes a sequence of keypoints, processes via LSTM, applies a linear layer,
        and normalizes the output.
        """
        _, (hn, _) = self.lstm(x)
        last_hidden = hn[-1] 
        emb = self.fc(last_hidden)
        # Mandatory L2 normalization for Cosine Similarity
        return F.normalize(emb, p=2, dim=1)


#  ROBUST EXTRACTOR (Mirror Invariant)

class GaitExtractor:
    def __init__(self, config: GaitConfig):
        """
        Initializes the GaitExtractor.
        Sets up the device (CPU/GPU), instantiates the LSTM model,
        and loads pre-trained weights from disk.
        """
        self.config = config
        self.device = torch.device(config.device.device)
        self.model: Optional[LSTMGaitEncoder] = None 

        try:
            pose_input_dim = 17 * 3
            hidden_dim = 128 
            output_dim = config.gallery.dim # 256

            # Instantiate LSTM model
            self.model = LSTMGaitEncoder(pose_input_dim, hidden_dim, output_dim).to(self.device)
            
            # Load weights
            model_path = Path(config.models.gait_embedding_model_path)
            if model_path.exists():
                state_dict = torch.load(model_path, map_location=self.device)
                self.model.load_state_dict(state_dict)
                logger.info(f"Gait embedding model loaded from {model_path}")
            else:
                logger.warning(f"Gait embedding model NOT found at {model_path}. System will not work.")

            self.model.eval() 
            
        except Exception as e:
            logger.error(f"Error initializing GaitExtractor: {e}\n{traceback.format_exc()}")
            self.model = None

    def _calculate_pose_quality(self, pose_sequence: List[np.ndarray]) -> float:
        """
        Calculates a quality score for the sequence based on the confidence
        values of the detected keypoints.
        """
        if not pose_sequence: return 0.0
        total_quality = 0.0
        min_vis = self.config.thresholds.min_visibility
        for pose in pose_sequence:
            # pose shape (17, 3) -> [x, y, conf]
            valid_keypoints = np.sum(pose[:, 2] >= min_vis)
            pose_quality = valid_keypoints / pose.shape[0]
            total_quality += pose_quality
        return total_quality / len(pose_sequence)

    def _simple_normalize(self, pose: np.ndarray) -> np.ndarray:
        """
        Scales pixels into an approximate [0, 1] range.
        Divides by 2000.0 (Safety margin for 1920x1080 resolution).
        """
        norm_pose = pose.copy()
        norm_pose[:, 0] = norm_pose[:, 0] / 2000.0 # X
        norm_pose[:, 1] = norm_pose[:, 1] / 2000.0 # Y
        return norm_pose

    def _flip_pose_sequence(self, sequence: List[np.ndarray]) -> List[np.ndarray]:
        """
        Generates a mirrored version of the sequence to make the embedding
        invariant to walking direction (Left/Right) and camera mirroring.
        """
        flipped_seq = []
        # COCO Keypoints indices to swap Left <-> Right
        # 1=L_Eye, 2=R_Eye ... 15=L_Ankle, 16=R_Ankle
        left_idx = [1, 3, 5, 7, 9, 11, 13, 15]
        right_idx = [2, 4, 6, 8, 10, 12, 14, 16]
        
        for pose in sequence:
            new_pose = pose.copy()
            
            # 1. Invert X (Geometric Mirroring)
            # Note: Since we divided by 2000 (small positive values), 
            # we invert the sign to match training augmentation.
            new_pose[:, 0] = -new_pose[:, 0]
            
            # 2. Swap limbs (Semantic Mirroring)
            # Temporarily save left side
            temp_left = new_pose[left_idx, :].copy()
            # Right -> Left
            new_pose[left_idx, :] = new_pose[right_idx, :]
            # Left (saved) -> Right
            new_pose[right_idx, :] = temp_left
            
            flipped_seq.append(new_pose)
            
        return flipped_seq

    def _get_embedding_from_sequence(self, raw_sequence: List[np.ndarray]) -> Optional[np.ndarray]:
        """
        Internal helper to process a single list of poses through the model.
        Handles normalization, tensor conversion, and inference.
        """
        try:
            # Normalize
            normalized_sequence = [self._simple_normalize(p) for p in raw_sequence]
            
            # Flatten & Tensor
            flattened_poses = [pose.flatten() for pose in normalized_sequence]
            pose_tensor = torch.tensor(np.array(flattened_poses), dtype=torch.float32).to(self.device)
            pose_tensor = pose_tensor.unsqueeze(0) # Batch dim

            with torch.no_grad():
                raw_embedding = self.model(pose_tensor)

            embedding = raw_embedding.squeeze(0).cpu().numpy()
            return embedding
        except Exception:
            return None

    def extract_gait_embedding_and_quality(self, pose_sequence: List[np.ndarray]) -> Tuple[Optional[np.ndarray], float]:
        """
        Main method to compute the gait vector.
        Performs Test Time Augmentation (TTA) by averaging the embedding
        of the original sequence and its mirrored version.
        """
        if not pose_sequence or self.model is None: 
            return None, 0.0

        gait_quality = self._calculate_pose_quality(pose_sequence)

        # Minimum quality filter
        if gait_quality < self.config.thresholds.min_gait_quality:
            return None, gait_quality
       
        try:
            # 1. Calculate Normal Embedding
            emb_orig = self._get_embedding_from_sequence(pose_sequence)
            
            # 2. Calculate Mirrored Embedding (Flipped)
            flipped_sequence = self._flip_pose_sequence(pose_sequence)
            emb_flip = self._get_embedding_from_sequence(flipped_sequence)
            
            if emb_orig is None or emb_flip is None:
                return None, 0.0
            
            # 3. Average of the two vectors (Test Time Augmentation)
            # This makes the result identical whether the person walks right or left
            final_emb = (emb_orig + emb_flip) / 2.0
            
            # Final L2 renormalization
            norm = np.linalg.norm(final_emb)
            if norm > 1e-6: 
                final_emb /= norm
            
            return final_emb, gait_quality

        except Exception as e:
            logger.error(f"Error during extraction: {e}\n{traceback.format_exc()}")
            return None, gait_quality