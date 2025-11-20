from dataclasses import dataclass, field
from typing import List, Tuple, Optional #added Optional
import numpy as np #imported by (Francesco and orio)

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

    """
    NEW FIELD FOR GAIT_RECOGNITION(Francesco and Vittorio)
    This field will contain the sequences of poses of this person
    Each element of the list will be a np.ndarray which represent a pose.
    """
    gait_sequence_data: List[np.ndarray] = field(default_factory=list)

    #this field will contain the numeric embedding of the gait of a single person
    gait_embedding: Optional[np.ndarray] = None

    #This field will contain the id of the person recognized by walking
    gait_identity_id: Optional[str] = None

    #This field will contain the confidence of the walk recognition
    gait_confidence: Optional[float]=None