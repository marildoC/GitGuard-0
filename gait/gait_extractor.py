"""
The logic for transforming a sequence
of poses into a walking (gait) embedding.
"""
import numpy as np
from typing import List, Optional,Tuple
import logging

from gait.config import GaitConfig

logger=logging.getLogger(__name__)

class GaitExtractor:
    """
    Extracts a gait embedding and an associated quality score from a sequence of pose keypoints.

    How it works:
    This class is designed to process a temporal sequence of smoothed poses
    (provided by the perception engine) and convert them into a fixed-size
    gait embedding suitable for identity recognition. It also calculates a
    quality score for the gait sequence, which helps in determining the
    reliability of the extracted embedding. The current implementation
    uses a simulated temporal encoder, which is a placeholder for a real
    deep learning model.

    Attributes:
    - config (GaitConfig): Configuration object containing parameters
                           like embedding dimension, quality thresholds, etc.
    """
    def __init__(self,config: GaitConfig):
        self.config = config
        logger.info(f"GaitExtractor initialized. Target embedding dimension:{self.config.gallery.dim}")

    def _calculate_pose_quality(self,pose_sequence: List[np.ndarray])->float:
        """
        Calculates a cumulative quality score for an entire sequence of poses.

        How it works:
        For each individual pose (a NumPy array of keypoints) within the
        `pose_sequence`, it counts the number of keypoints whose confidence
        score (the third value in the keypoint data, `pose[:,2]`) is above
        `self.config.thresholds.min_visibility`. This count is then divided
        by the total number of keypoints in a pose to get a per-pose quality.
        These per-pose quality scores are averaged across the entire sequence
        to yield a final gait quality score for the sequence. An empty
        sequence results in a quality of 0.0.

        Args:
            pose_sequence (List[np.ndarray]): A list of NumPy arrays, where each array
                                              represents a pose (keypoints and confidences).

        Returns:
            float: The average quality score (0-1) of the pose sequence.
        """
        if not pose_sequence:
            return 0.0
        total_quality = 0.0
        for pose in pose_sequence:
            # np.sum counts True values (keypoints with confidence >= min_visibility)
            valid_keypoints = np.sum(pose[:,2]>=self.config.thresholds.min_visibility)
            # pose.shape[0] is the total number of keypoints in a single pose
            pose_quality = valid_keypoints / pose.shape[0]
            total_quality += pose_quality
        return total_quality / len(pose_sequence)

    def extract_gait_embedding_and_quality(self,pose_sequence: List[np.ndarray]) -> Tuple[Optional[np.ndarray],float]:
        """
        Converts a sequence of poses into a single gait embedding and calculates its quality.

        How it works:
        1.  **Quality Calculation**: First, it calls `_calculate_pose_quality` to determine
            the overall quality of the input `pose_sequence`.
        2.  **Quality Thresholding**: If the calculated `gait_quality` falls below
            `self.config.thresholds.min_gait_quality`, the function immediately returns
            `None` for the embedding (indicating an invalid embedding) and the calculated quality.
        3.  **Temporal Encoder Simulation (Placeholder)**: This is the critical point
            where a real deep learning temporal encoder (e.g., a GRU or Transformer-based model)
            would process the `pose_sequence` to generate a meaningful gait embedding.
            In this simulated version, it calculates a mean of flattened pose values
            to seed a random number generator, then produces a random NumPy array
            of the specified `config.gallery.dim` (e.g., 256) as a placeholder embedding.
        4.  **Normalization**: The generated embedding is normalized to have a unit L2 norm.
            If the embedding is a zero vector, it remains a zero vector after normalization
            to prevent division by zero errors.

        The resulting embedding is a 1D NumPy array of `config.gallery.dim` elements.

        Args:
            pose_sequence (List[np.ndarray]): A list of NumPy arrays, where each array
                                              represents a smoothed pose (keypoints and confidences).

        Returns:
            Tuple[Optional[np.ndarray], float]:
                - Optional[np.ndarray]: The extracted gait embedding (1D NumPy array)
                                        or `None` if quality is too low or sequence is empty.
                - float: The quality score (0-1) of the gait sequence.
        """
        if not pose_sequence:
            logger.debug("Attempted to extract embedding from empty pose sequence.")
            return None,0.0
        
        #calculate the quality by the average quality of the poses in the sequence
        gait_quality = self._calculate_pose_quality(pose_sequence)

        #if the quality is under the minimum treshold, do not produce a valid embedding
        if gait_quality < self.config.thresholds.min_gait_quality:
            logger.debug(f"Gait quality {gait_quality:.2f} below threshold {self.config.thresholds.min_gait_quality:.2f}. Not returning valid embedding.")
            return None, gait_quality
       
        # --- Temporal Encoder Simulation (Placeholder) ---
        # This is the point where you would INTEGRATE YOUR REAL DEEP LEARNING MODEL.
        # For now, we generate a random embedding of the specified dimension (e.g., 256).

        # For a better simulation, we could use the average values of the flattened keypoints
        # to "seed" the random generation, making the embedding less truly random.
        # To simulate a "temporal encoder", you might flatten the sequence
        # [pose1, pose2, ..., poseN] into a single vector (N*K*3) and then hash that.

        # Using a fixed seed based on the first pose's values for deterministic simulation
        
        seed_val = int(np.sum(pose_sequence[0] * 1000)) % (2**32 - 1)
        np.random.seed(seed_val)
        embedding = np.random.rand(self.config.gallery.dim).astype(np.float32)

        #Normalize the embedding
        norm=np.linalg.norm(embedding)
        if norm>0:
            embedding = embedding/norm
        else:
            logger.warning("Simulated embedding is a zero vector, cannot normalize.")
            embedding = np.zeros(self.config.gallery.dim, dtype=np.float32)

        logger.debug(f"Extracted simulated gait embedding of shape {embedding.shape} with quality {gait_quality:.2f}.")
        return embedding, gait_quality