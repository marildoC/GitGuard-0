"""
core/device.py

Central place to decide which device to use ("cuda" or "cpu")
and whether FP16 is allowed.
"""

from __future__ import annotations

import logging
from typing import Tuple

import torch 

log = logging.getLogger("gaitguard.device")


def select_device(prefer_gpu: bool = True) -> Tuple[str, bool]:
    """
    Decide which device string to use ("cuda" or "cpu") and
    whether half precision (FP16) is allowed.

    Returns:
        device (str): "cuda" or "cpu"
        use_half (bool): True if FP16 should be used
    """
    if prefer_gpu and torch.cuda.is_available():
        device = "cuda"
        use_half = True  # safe for our GPU models
        name = torch.cuda.get_device_name(0)
        log.info("Using CUDA device: %s", name)
    else:
        device = "cpu"
        use_half = False
        if prefer_gpu:
            log.warning("CUDA requested but not available. Falling back to CPU.")
        else:
            log.info("Using CPU (GPU explicitly disabled).")

    return device, use_half
