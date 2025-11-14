from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class Tracklet:
    """
    One moving person/object tracked over time.

    - track_id      : unique ID assigned by tracker
    - camera_id     : which camera this track is from
    - last_frame_id : id of the last Frame where it was seen
    - last_box      : (x1, y1, x2, y2) in pixel coordinates
    - confidence    : tracker/detector confidence (0–1)
    - age_frames    : how many frames this track has existed
    - lost_frames   : how many frames since it was last seen
    - history_boxes : optional past boxes for this track
    """
    track_id: int
    camera_id: str
    last_frame_id: int
    last_box: Tuple[float, float, float, float]
    confidence: float

    age_frames: int = 0
    lost_frames: int = 0
    history_boxes: List[Tuple[float, float, float, float]] = field(
        default_factory=list
    )
