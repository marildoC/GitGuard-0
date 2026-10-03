"""
core/logging_setup.py

Central logging configuration.
Writes to console and to a log file under logs_dir.
"""

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "gaitguard.log"

    # Clear any existing handlers (if run multiple times in same process)
    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
