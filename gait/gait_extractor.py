# gait/gait_extractor.py

import numpy as np
from typing import List, Optional, Tuple
import logging
import torch
import torch.nn as nn
import torch.nn.functional as F
from pathlib import Path
import traceback

from gait.config import GaitConfig

logger = logging.getLogger(__name__)

# ============================================================
#  GAITSET PLUS (RESNET-BASED) ARCHITECTURE
#  Must match the training script exactly!
# ============================================================

class BasicConv2d(nn.Module):
    """
    Standard 2D Convolution block with Batch Normalization and ReLU.
    """
    def __init__(self, in_c, out_c, kernel_size, stride=1, padding=0):
        super(BasicConv2d, self).__init__()
        self.conv = nn.Conv2d(in_c, out_c, kernel_size, stride, padding, bias=False)
        self.bn = nn.BatchNorm2d(out_c)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x): 
        return self.relu(self.bn(self.conv(x)))

class ResBlock(nn.Module):
    """
    Residual Block standard for ResNet architectures.
    Includes a shortcut connection to allow gradient flow through deep networks.
    """
    def __init__(self, in_c, out_c):
        super(ResBlock, self).__init__()
        self.conv1 = BasicConv2d(in_c, out_c, 3, 1, 1)
        self.conv2 = nn.Sequential(
            nn.Conv2d(out_c, out_c, 3, 1, 1, bias=False), 
            nn.BatchNorm2d(out_c)
        )
        self.shortcut = nn.Sequential()
        if in_c != out_c: 
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_c, out_c, 1, 1, 0, bias=False), 
                nn.BatchNorm2d(out_c)
            )
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x): 
        return self.relu(self.conv1(x) + self.shortcut(x))

class GaitSetPlus(nn.Module):
    """
    A lightweight Neural Network for Gait Recognition.
    
    Architecture:
    1. Treats the input (Batch, Time, Height, Width) as independent 2D frames initially.
    2. Passes frames through a ResNet-like backbone to extract features.
    3. Uses Max Pooling over the temporal dimension (Set Pooling) to aggregate
       features across time, making the model invariant to the number of frames.
    4. Produces a normalized embedding vector.
    """
    def __init__(self, emb_dim=256):
        super(GaitSetPlus, self).__init__()
        self.conv1 = BasicConv2d(1, 64, 3, 1, 1)
        
        self.layer1 = ResBlock(64, 64)
        self.pool1 = nn.MaxPool2d(2, 2)
        
        self.layer2 = ResBlock(64, 128)
        self.pool2 = nn.MaxPool2d(2, 2)
        
        self.layer3 = ResBlock(128, 256)
        self.pool3 = nn.MaxPool2d(2, 2)
        
        self.layer4 = ResBlock(256, 512)
        
        self.fc = nn.Linear(512 * 8 * 8, emb_dim)
        self.bn_head = nn.BatchNorm1d(emb_dim)
        
    def forward(self, x):
        # x shape: [Batch, Time, Height, Width] (Grayscale masks)
        b, t, h, w = x.size()
        
        # Merge Batch and Time for 2D CNN processing
        x = x.view(-1, 1, h, w) 
        
        x = self.conv1(x)
        x = self.pool1(self.layer1(x))
        x = self.pool2(self.layer2(x))
        x = self.pool3(self.layer3(x))
        x = self.layer4(x)
        
        # Restore time dimension
        _, c, h2, w2 = x.size()
        x = x.view(b, t, c, h2, w2)
        
        # Set Pooling (Max): Aggregate temporal information
        x = x.max(dim=1)[0] 
        
        x = x.view(b, -1)
        # Fully Connected + Batch Norm + L2 Normalization
        return F.normalize(self.bn_head(self.fc(x)), p=2, dim=1)

# ============================================================
#  EXTRACTOR CLASS (RUNTIME WRAPPER)
# ============================================================

class GaitExtractor:
    def __init__(self, config: GaitConfig):
        """
        Wrapper to handle model initialization, weight loading, and inference.
        """
        self.config = config
        self.device = torch.device(config.device.device)
        self.model: Optional[GaitSetPlus] = None 

        try:
            # 1. Instantiate the architecture
            self.model = GaitSetPlus(emb_dim=config.gallery.dim).to(self.device)
            
            # 2. Load weights
            model_path = Path(config.models.gait_embedding_model_path)
            if model_path.exists():
                state_dict = torch.load(model_path, map_location=self.device)
                self.model.load_state_dict(state_dict)
                logger.info(f"GaitSetPlus model loaded successfully from {model_path}")
            else:
                logger.warning(f"GaitSetPlus model NOT found at {model_path}. Recognition will not work.")

            self.model.eval() 
            
        except Exception as e:
            logger.error(f"Error initializing GaitExtractor: {e}")
            import traceback
            traceback.print_exc()
            self.model = None

    def _calculate_quality(self, silhouettes: List[np.ndarray]) -> float:
        """
        Calculates a quality score based on silhouette integrity.
        
        Criteria:
        - A silhouette is valid if the foreground (white pixels) occupies 
          between 2% and 90% of the bounding box area.
        - Returns the percentage of valid frames in the sequence.
        """
        if not silhouettes: return 0.0
        scores = []
        for sil in silhouettes:
            area = np.sum(sil > 0)
            total = sil.size
            ratio = area / total
            # Check if ratio is reasonable (not empty, not full block)
            if 0.02 < ratio < 0.9: 
                scores.append(1.0)
            else:
                scores.append(0.0)
        return sum(scores) / len(scores)

    def extract_gait_embedding_and_quality(self, sequence: List[np.ndarray]) -> Tuple[Optional[np.ndarray], float]:
        """
        Converts a sequence of binary silhouettes into a gait embedding vector.
        
        Args:
            sequence: List of np.ndarray (64x64 uint8). 0=bg, 255=fg.
            
        Returns:
            Tuple(embedding, quality): 
                - embedding: (256,) float array or None if failed.
                - quality: float 0.0 to 1.0.
        """
        if not sequence or self.model is None: 
            return None, 0.0

        # 1. Quality Filter
        quality = self._calculate_quality(sequence)
        if quality < self.config.thresholds.min_gait_quality:
            return None, quality
       
        try:
            # 2. Preprocessing: [0, 255] uint8 -> [0.0, 1.0] float32
            frames = np.array(sequence, dtype=np.float32) / 255.0
            
            # Create Tensor: (1, T, 64, 64) -> Batch size 1
            tensor = torch.from_numpy(frames).unsqueeze(0).to(self.device)

            with torch.no_grad():
                # 3. Forward Pass
                emb = self.model(tensor)
                
                # 4. Test Time Augmentation (Horizontal Flip)
                # Helps invariance to direction of walking
                tensor_flipped = torch.flip(tensor, dims=[-1])
                emb_flipped = self.model(tensor_flipped)
                
                # Average the embeddings
                final_emb = (emb + emb_flipped) / 2.0
                
                # 5. Final L2 Normalization
                final_emb = F.normalize(final_emb, p=2, dim=1)

            return final_emb.cpu().numpy()[0], quality

        except Exception as e:
            logger.error(f"Extraction Error: {e}")
            return None, quality