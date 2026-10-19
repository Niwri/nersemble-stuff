"""Ava-256 capture review schemas (see services/ava256_labels.py)."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


class Ava256FrameCounts(BaseModel):
    Valid: int = 0
    Invalid: int = 0
    Rerun: int = 0
    unconfirmed: int = 0


class Ava256CaptureDescriptor(BaseModel):
    capture_id: str
    label_status: str
    has_video: bool
    num_frames: int
    counts: Ava256FrameCounts
    is_done: bool


class Ava256DatasetResponse(BaseModel):
    captures: List[Ava256CaptureDescriptor]


class Ava256FrameRow(BaseModel):
    frame_id: str
    status: str
    # Mean skin-landmark pixel error of this frame's wrap
    # (check_ava256_skin_landmark_errors.py); None if not measured.
    skin_error_px: Optional[float] = None


class Ava256FramesResponse(BaseModel):
    capture_id: str
    frames: List[Ava256FrameRow]
    is_done: bool
    warnings: List[str] = Field(default_factory=list)


class Ava256LabelRangesRequest(BaseModel):
    """{"Valid": [[1, 100], [150, 300]], "Invalid": [[101, 149]], "Rerun": [[301, 380]]} --
    inclusive, 1-based frame positions; any subset of the three labels."""

    ranges: Dict[str, List[List[int]]]


class Ava256DoneResponse(BaseModel):
    capture_id: str
    export_path: Optional[str]
    labels_path: str
    counts: Dict[str, int]


# --- Neutral frame · ear tops (services/ava256_neutral.py) ---

class Ava256NeutralCamera(BaseModel):
    camera_id: str
    side: Literal["left", "front", "right", "unknown"]
    azimuth_deg: float  # < 0: the actor's left
    elevation_deg: float  # > 0: above eye level
    angle_deg: float  # from the face's forward direction


class Ava256NeutralResponse(BaseModel):
    capture_id: str
    frame_id: str
    coverage: int
    needed_total: int
    cameras: List[Ava256NeutralCamera]  # cameras behind the head left out; sorted left -> right
    default_cameras: List[str]
    ear_tops: List[int]  # saved ear-top indices (2 = left, 9 = right)


class Ava256SaveEarPointRequest(BaseModel):
    ear: Literal["left", "right"]
    # camera_id -> normalized [0, 1] (x, y) click on that camera's neutral image
    clicks: Dict[str, Tuple[float, float]] = Field(min_length=2)


class Ava256SaveEarPointResponse(BaseModel):
    status: Literal["ok"] = "ok"
    world_xyz: List[float]
    cameras_used: List[str]
    reprojection_error_px: Dict[str, float]
