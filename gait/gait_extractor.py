"""
The logic for transforming a sequence
of poses into a walking (gait) embedding.
"""
import numpy as np
from typing import List, Optional,Tuple
import logging
import torch
from pathlib import Path
import torch.nn as nn
import torch.nn.functional as F
import traceback
import numpy as np

from gait.config import GaitConfig

logger=logging.getLogger(__name__)

class TemporalGaitEncoder(nn.Module):
    """
    A simple GRU-based temporal encoder for gait embeddings from pose sequences.
    This model takes a sequence of flattened pose keypoints and outputs a fixed-size embedding.

    Input: (batch_size, sequence_length, input_dim)
            where input_dim is 17 keypoints * 3 values (x,y,conf) = 51
    Output: (batch_size, output_dim)
    """
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, num_layers: int = 1):
        super().__init__()
        # Linear layer for mapping the flattened pose input to hidden_dim
        self.input_linear = nn.Linear(input_dim, hidden_dim)
        # GRU layer for processing the temporal sequence
        self.gru = nn.GRU(hidden_dim, hidden_dim, num_layers, batch_first=True)
        # Final linear layer mapping the GRU hidden state to the output embedding
        self.output_linear = nn.Linear(hidden_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x is expected to have shape (batch_size, sequence_length, input_dim)

        # Apply a linear transformation and ReLU activation to every timestep
        x = torch.relu(self.input_linear(x))
        
        # Pass the sequence through the GRU
        # output: output at each time step (not needed for final embedding)
        # hn: final hidden state for the last GRU layer (used for embedding)
        _, hn = self.gru(x)
        
        # hn has shape (num_layers, batch_size, hidden_dim)
        # Use the hidden state from the last layer (-1) as input to the final layer
        embedding = self.output_linear(hn[-1, :, :])
        
        return embedding


class GaitExtractor:
    """
    Extracts a gait embedding and a quality score from a sequence of pose keypoints.
    This implementation uses a temporal encoder (GRU) to process pose sequences.
    It expects smoothed pose sequences from the perception engine.
    """
    def __init__(self,config: GaitConfig):
        self.config = config
        self.device = torch.device(config.device.device)
        self.model: Optional[TemporalGaitEncoder] = None # Will hold the model instance

        # Initialize the Temporal Encoder Model
        try:
            # Each pose is (17 keypoints * 3 values (x,y,conf)) = 51
            pose_input_dim = 17 * 3
            hidden_dim = 128 # Can be tuned
            output_dim = config.gallery.dim # 256
            num_gru_layers = 1 # Can be tuned

            self.model = TemporalGaitEncoder(pose_input_dim, hidden_dim, output_dim, num_gru_layers).to(self.device)
            
            # Load model weights if available
            model_path = Path(config.models.gait_embedding_model_path)
            if model_path.exists():
                self.model.load_state_dict(torch.load(model_path, map_location=self.device))
                logger.info(f"Gait embedding model loaded from {model_path}")
            else:
                logger.warning(f"Gait embedding model not found at {model_path}. Using randomly initialized weights.")

            self.model.eval() # Set model to evaluation mode
            logger.info(f"TemporalGaitEncoder initialized on device: {self.device}")

        except Exception as e:
            logger.error(f"Error initializing GaitExtractor model: {e}\n{traceback.format_exc()}")
            self.model = None # Ensure model is None if initialization fails

        logger.info(f"GaitExtractor initialized. Target embedding dimension:{self.config.gallery.dim}")

    def _calculate_pose_quality(self,pose_sequence: List[np.ndarray])->float:
        """
        Calculates a cumulative quality score for an entire sequence of poses.
        """
        if not pose_sequence:
            return 0.0
        total_quality = 0.0
        for pose in pose_sequence:
            valid_keypoints = np.sum(pose[:,2]>=self.config.thresholds.min_visibility)
            pose_quality = valid_keypoints / pose.shape[0]
            total_quality += pose_quality
        return total_quality / len(pose_sequence)

    def extract_gait_embedding_and_quality(self,pose_sequence: List[np.ndarray]) -> Tuple[Optional[np.ndarray],float]:
        """
        Converts a sequence of poses into a single gait embedding and calculates its quality.
        The resulting embedding is a 1D numpy array of `config.gallery.dim` (256) elements.
        """
        # If no pose sequence or model is not loaded
        if not pose_sequence or self.model is None: 
            logger.debug("Attempted to extract embedding from empty pose sequence or model not loaded.")
            return None,0.0

        gait_quality = self._calculate_pose_quality(pose_sequence)

        # If quality is too low, return only the quality score
        if gait_quality < self.config.thresholds.min_gait_quality:
            logger.debug(f"Gait quality {gait_quality:.2f} below threshold {self.config.thresholds.min_gait_quality:.2f}. Not returning valid embedding.")
            return None, gait_quality
       
        # --- Temporal Encoder Integration ---
        try:
            flattened_poses = [pose.flatten() for pose in pose_sequence]
            
            pose_tensor = torch.tensor(np.array(flattened_poses), dtype=torch.float32).to(self.device)
            pose_tensor = pose_tensor.unsqueeze(0) # Add batch dimension


            with torch.no_grad(): # No gradient computation for inference
                raw_embedding = self.model(pose_tensor)

            embedding = raw_embedding.squeeze(0).cpu().numpy()

            # L2 normalization of the embedding
            # It's safer to normalize in PyTorch before converting to numpy
            embedding = F.normalize(torch.tensor(embedding), p=2, dim=0).numpy()


            logger.debug(f"Extracted gait embedding of shape {embedding.shape} with quality {gait_quality:.2f}.")
            return embedding, gait_quality

        except Exception as e:
            logger.error(f"Error during gait embedding extraction: {e}\n{traceback.format_exc()}")
            return None, gait_quality