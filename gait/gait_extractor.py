# gait/gait_extractor.py

import numpy as np
from typing import List, Optional, Tuple
import logging
import torch
import torch.nn as nn
import torch.nn.functional as F
from pathlib import Path
from torchvision import models
from gait.config import GaitConfig

logger = logging.getLogger(__name__)

# ============================================================
#  GAIT RESNET-18 ARCHITECTURE (TRANSFER LEARNING)
#  This MUST match the architecture used in the Kaggle training script!
# ============================================================

class GaitResNet18(nn.Module):
    """
    Modified ResNet-18 architecture for Gait Recognition.
    Adapted from ImageNet pre-trained weights to handle grayscale silhouettes.
    """
    def __init__(self, emb_dim=256):
        super(GaitResNet18, self).__init__()
        
        # 1. Load base ResNet18 structure (weights=None because we load custom weights later)
        # Note: In training we use weights=IMAGENET1K_V1, but here we load our own .pth
        base = models.resnet18(weights=None) 
        
        # 2. Adapt the first convolutional layer (RGB 3 channels -> Grayscale 1 channel)
        # Standard ResNet expects 3 channels, our silhouettes have 1.
        self.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        
        # Copy standard ResNet layers
        self.bn1 = base.bn1
        self.relu = base.relu
        self.maxpool = base.maxpool
        self.layer1 = base.layer1
        self.layer2 = base.layer2
        self.layer3 = base.layer3
        self.layer4 = base.layer4
        
        # 3. Custom Head for Gait Embedding
        # track_running_stats=False is CRUCIAL to avoid "Model Collapse" during single-person inference.
        # It forces the Batch Norm to calculate stats on the fly or behave statically.
        self.bn_head = nn.BatchNorm1d(emb_dim, affine=False, track_running_stats=False) 
        self.fc = nn.Linear(512, emb_dim) # ResNet18 output feature map depth is 512

    def forward(self, x):
        # Input Tensor Shape: [Batch, Time, Height, Width] (Grayscale)
        b, t, h, w = x.size()
        
        # Merge Batch and Time dimensions to process every frame as a 2D image
        # New Shape: [Batch * Time, 1, Height, Width]
        x = x.view(-1, 1, h, w) 

        # Pass through ResNet Backbone
        x = self.conv1(x); x = self.bn1(x); x = self.relu(x); x = self.maxpool(x)
        x = self.layer1(x); x = self.layer2(x); x = self.layer3(x); x = self.layer4(x)
        
        # Temporal Pooling (Max Pooling over time)
        # ResNet18 output is [B*T, 512, H', W']. We want a single vector per sequence.
        x = F.adaptive_max_pool2d(x, 1) # Spatial pooling -> [B*T, 512, 1, 1]
        x = x.view(b, t, 512)           # Restore Time dimension -> [Batch, Time, 512]
        x = x.max(dim=1)[0]             # Max Pool over Time -> [Batch, 512]
        
        # Embedding Projection Head
        x = self.fc(x) # -> [Batch, 256]
        
        # Final Batch Normalization
        # Only applied if batch size > 1 to prevent crashes during single inference
        if x.size(0) > 1: 
            x = self.bn_head(x)
        
        # L2 Normalization (Essential for Cosine Distance)
        return F.normalize(x, p=2, dim=1)

# ============================================================
#  EXTRACTOR WRAPPER CLASS
# ============================================================

class GaitExtractor:
    """
    High-level wrapper to handle model initialization, loading weights,
    preprocessing input sequences, and extracting embeddings.
    """
    def __init__(self, config: GaitConfig):
        self.config = config
        self.device = torch.device(config.device.device)
        self.model: Optional[GaitResNet18] = None 

        try:
            # 1. Instantiate the correct model architecture
            self.model = GaitResNet18(emb_dim=config.gallery.dim).to(self.device)
            
            # 2. Load the trained weights (.pth file)
            model_path = Path(config.models.gait_embedding_model_path)
            
            if model_path.exists():
                # strict=False helps ignore minor mismatch issues (e.g., internal torchvision layer names)
                state_dict = torch.load(model_path, map_location=self.device)
                self.model.load_state_dict(state_dict, strict=False)
                logger.info(f"✅ GaitResNet18 weights loaded from {model_path}")
            else:
                logger.warning(f"❌ Model file NOT found at {model_path}. Using random weights (Model will fail)!")

            self.model.eval() # Set to evaluation mode
            
        except Exception as e:
            logger.error(f"Error initializing GaitExtractor: {e}")
            import traceback
            traceback.print_exc()
            self.model = None

    def _calculate_quality(self, silhouettes: List[np.ndarray]) -> float:
        """
        Calculates a simple quality score based on silhouette integrity.
        Checks if the silhouette is too small (empty) or too full (artifacts).
        """
        if not silhouettes: return 0.0
        scores = []
        for sil in silhouettes:
            area = np.sum(sil > 0)
            total = sil.size
            ratio = area / total
            # A person should occupy between 2% and 90% of the bounding box area
            if 0.02 < ratio < 0.9: 
                scores.append(1.0)
            else:
                scores.append(0.0)
        return sum(scores) / len(scores)

    def extract_gait_embedding_and_quality(self, sequence: List[np.ndarray]) -> Tuple[Optional[np.ndarray], float]:
        """
        Main inference method.
        Input: sequence List[np.ndarray] (64x64 uint8 images, 0=bg, 255=fg)
        Output: embedding (256,), quality (0-1)
        """
        if not sequence or self.model is None: 
            return None, 0.0

        # Quality Filter
        quality = self._calculate_quality(sequence)
        if quality < self.config.thresholds.min_gait_quality:
            return None, quality
       
        try:
            # Preprocessing: Convert [0, 255] uint8 -> [0.0, 1.0] float32
            frames = np.array(sequence, dtype=np.float32) / 255.0
            
            # Convert to Tensor: [1, Time, 64, 64] (Batch=1, Time, Height, Width)
            tensor = torch.from_numpy(frames).unsqueeze(0).to(self.device)

            with torch.no_grad():
                # Forward Pass through the network
                final_emb = self.model(tensor)

            # Return as 1D numpy array (256,)
            return final_emb.cpu().numpy()[0], quality

        except Exception as e:
            logger.error(f"Extraction Error: {e}")
            return None, quality