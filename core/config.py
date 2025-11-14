"""
core/config.py

Loads YAML configuration and exposes a small AppConfig object.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml 


# ---------- dataclasses ---------- #

@dataclass
class CameraConfig:
    index: int
    width: int
    height: int
    fps: int


@dataclass
class PathsConfig:
    models_dir: Path
    logs_dir: Path
    evidence_dir: Path


@dataclass
class RuntimeConfig:
    use_gpu: bool
    save_evidence: bool


@dataclass
class AppConfig:
    camera: CameraConfig
    paths: PathsConfig
    runtime: RuntimeConfig


# ---------- loader ---------- #

def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(path: Path | str = "config/default.yaml") -> AppConfig:
    """
    Load configuration from YAML and return a typed AppConfig.

    Default path is config/default.yaml relative to project root.
    """
    path = Path(path)
    data = _read_yaml(path)

    cam = data.get("camera", {})
    paths = data.get("paths", {})
    rt = data.get("runtime", {})

    camera_cfg = CameraConfig(
        index=int(cam.get("index", 0)),
        width=int(cam.get("width", 640)),
        height=int(cam.get("height", 480)),
        fps=int(cam.get("fps", 30)),
    )

    # Resolve paths relative to the YAML file directory
    base = path.parent
    paths_cfg = PathsConfig(
        models_dir=(base / paths.get("models_dir", "models")).resolve(),
        logs_dir=(base / paths.get("logs_dir", "logs")).resolve(),
        evidence_dir=(base / paths.get("evidence_dir", "evidence")).resolve(),
    )

    runtime_cfg = RuntimeConfig(
        use_gpu=bool(rt.get("use_gpu", True)),
        save_evidence=bool(rt.get("save_evidence", False)),
    )

    return AppConfig(
        camera=camera_cfg,
        paths=paths_cfg,
        runtime=runtime_cfg,
    )
