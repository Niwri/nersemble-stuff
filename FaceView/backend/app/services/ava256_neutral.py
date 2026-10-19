"""Ava-256 neutral-frame multiview + manual ear tops (index 2 = left ear top,
index 9 = right ear top) for the Ava-256 tab's "Neutral frame · ear tops" view.

keypoints_3d has no ear-top keypoint, so 2/9 never get a target in
wrap_script_ava256 (landmark_map.ALWAYS_EXCLUDED_INDICES). A reviewer clicks
them in >= 2 camera views of the capture's neutral frame; save_ear_point()
triangulates one world point (same space as landmarks_ava-256's
<group>_<frame>.json points, i.e. before dynamic_transform) and stores it in
<capture>/ear_tops.json -- a file run_neutral_skin_propagation.py never
writes, so a rerun can't clobber it (and wrap_script_ava256 doesn't read it
yet).

The neutral frame is resolve_neutral_frame()'s, the same rule (and the same
neutral_frame.json cache) as wrap_script_ava256/neutral_frame.py. That
folder isn't shipped with this repo, so the few pieces needed here (the
keypoints_3d id table, camera loading) are copied from it.
"""
from __future__ import annotations

import csv
import fcntl
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np
from PIL import Image

try:  # registers the AVIF decoder with Pillow (pillow-avif-plugin)
    import pillow_avif  # noqa: F401
except ImportError:  # Pillow >= 11.2 may decode AVIF natively
    pass

from ..core.config import get_settings

_WRAP_SCRIPT_ROOT = get_settings().wrap_script_root  # same triangulation as facescape_ear_points.py
if str(_WRAP_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_WRAP_SCRIPT_ROOT))
from triangulate import triangulate_point  # noqa: E402

LEFT_EAR_INDEX = 2
RIGHT_EAR_INDEX = 9
EAR_INDEX_BY_SIDE: Dict[str, int] = {"left": LEFT_EAR_INDEX, "right": RIGHT_EAR_INDEX}
Side = Literal["left", "right"]

NEUTRAL_SEG_ID = "EXP_neutral_peak"
AVIF_SCALE = 0.25  # camera_calibration.json K is for the full-res sensor; the AVIFs are 1/4 size
EAR_TOPS_NAME = "ear_tops.json"

# Copy of wrap_script_ava256/landmark_map.py's table (standard index -> keypoints_3d id).
STANDARD_INDEX_TO_KEYPOINT3D_ID: Dict[int, int] = {
    1: 207, 4: 196, 6: 233, 11: 88, 13: 86, 14: 222, 15: 38, 16: 35, 17: 40,
    18: 119, 19: 116, 20: 128, 21: 144, 23: 123, 25: 125, 26: 148, 27: 132,
    30: 23, 32: 105, 33: 103, 34: 62, 35: 59, 36: 64, 37: 118, 38: 113,
    39: 127, 40: 143, 42: 122, 44: 124, 45: 147, 46: 131, 47: 13, 50: 7,
    54: 5, 55: 121, 56: 137, 57: 2, 58: 136, 59: 120, 60: 1, 61: 109,
    65: 50, 69: 8, 71: 26, 72: 17,
}

# Camera split around the head (classify_cameras). FRONT_ANGLE_DEG matches
# wrap_script_ava256/camera_classify.py. Cameras whose azimuth is more than
# BEHIND_AZIMUTH_DEG from straight ahead look at the back of the head and are
# never shown (side cameras up to ~115 deg still see the ear tops well).
# The side preset prefers cameras near SIDE_PRESET_AZIMUTH_DEG at eye height.
FRONT_ANGLE_DEG = 35.0
BEHIND_AZIMUTH_DEG = 115.0
SIDE_PRESET_AZIMUTH_DEG = 85.0
SIDE_PRESET_MIN_AZIMUTH_DEG = 55.0
PRESET_PER_SIDE = 4
NOSE_TIP_INDEX = 57
LEFT_EARLOBE_INDEX = 1
RIGHT_EARLOBE_INDEX = 6


class Ava256NeutralError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- paths / raw data -------------------------------------------------------

def _actor_dir(capture_id: str) -> Path:
    path = get_settings().ava256_data_root / capture_id
    if not path.is_dir():
        raise FileNotFoundError(f"No Ava-256 capture folder at {path}")
    return path


def _landmark_dir(capture_id: str) -> Path:
    return get_settings().ava256_landmark_root / capture_id


def keypoints_3d_by_id(actor_dir: Path, frame_id: str) -> Dict[int, np.ndarray]:
    """keypoint_id -> (x, y, z); .npy columns are [id, x, y, z, conf, inliers]
    (same as wrap_script_ava256/mesh_utils.keypoints_3d_by_id)."""
    path = actor_dir / "decoder" / "keypoints_3d" / f"{frame_id}.npy"
    if not path.exists():
        return {}
    return {int(row[0]): np.asarray(row[1:4], dtype=np.float64) for row in np.load(path)}


@lru_cache(maxsize=32)
def _load_cameras_cached(calib_path: str, mtime: float) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    with open(calib_path) as f:
        calib = json.load(f)
    cameras = {}
    for entry in calib["KRT"]:
        K = np.array(entry["K"], dtype=np.float64).T  # stored column-major (render_overlay.load_camera)
        K[0, 0] *= AVIF_SCALE
        K[1, 1] *= AVIF_SCALE
        K[0, 2] *= AVIF_SCALE
        K[1, 2] *= AVIF_SCALE
        Rt = np.array(entry["T"], dtype=np.float64).T  # 4x4 world -> camera
        cameras[str(entry["cameraId"])] = (K, Rt)
    return cameras


def load_cameras(capture_id: str) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """camera_id -> (K at AVIF resolution, 4x4 world-to-camera Rt)."""
    calib_path = _actor_dir(capture_id) / "decoder" / "camera_calibration.json"
    if not calib_path.exists():
        raise FileNotFoundError(f"No camera calibration at {calib_path}")
    return _load_cameras_cached(str(calib_path), calib_path.stat().st_mtime)


def _project(xyz: np.ndarray, K: np.ndarray, Rt: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(N,3) world -> ((N,2) pixels, (N,) camera depth)."""
    cam = xyz @ Rt[:3, :3].T + Rt[:3, 3]
    z = cam[:, 2]
    safe = np.where(np.abs(z) < 1e-8, 1e-8, z)
    px = np.stack([cam[:, 0] / safe * K[0, 0] + K[0, 2], cam[:, 1] / safe * K[1, 1] + K[1, 2]], axis=-1)
    return px, z


# --- neutral frame (same rule as wrap_script_ava256/neutral_frame.py) --------

def _locked_atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _compute_neutral_frame(capture_id: str, actor_dir: Path) -> dict:
    frame_list_path = actor_dir / "decoder" / "frame_list.csv"
    if not frame_list_path.exists():
        raise FileNotFoundError(f"No frame_list.csv at {frame_list_path}")
    with frame_list_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    candidates = sorted((r["frame_id"].strip().zfill(6) for r in rows if r["seg_id"] == NEUTRAL_SEG_ID), key=int)
    if not candidates:
        raise ValueError(f"No {NEUTRAL_SEG_ID} rows in {frame_list_path}")

    needed_ids = set(STANDARD_INDEX_TO_KEYPOINT3D_ID.values())
    best_frame_id: Optional[str] = None
    best_coverage = -1
    for frame_id in candidates:  # ascending order -> first-seen wins ties
        coverage = len(needed_ids & set(keypoints_3d_by_id(actor_dir, frame_id)))
        if coverage > best_coverage:
            best_frame_id, best_coverage = frame_id, coverage
    return {
        "capture_id": capture_id,
        "frame_id": best_frame_id,
        "seg_id": NEUTRAL_SEG_ID,
        "coverage": best_coverage,
        "needed_total": len(needed_ids),
        "candidates_considered": candidates,
        "tie_break": "lowest_frame_id",
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def resolve_neutral_frame(capture_id: str) -> dict:
    """Cached <landmark_root>/<capture>/neutral_frame.json if present (trusted
    as-is, like the pipeline), else computed and cached -- so a later
    propagation run uses the frame the ear tops were clicked on."""
    cache_path = _landmark_dir(capture_id) / "neutral_frame.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # fall through and recompute
    payload = _compute_neutral_frame(capture_id, _actor_dir(capture_id))
    _locked_atomic_write(cache_path, payload)
    return payload


# --- cameras around the head -------------------------------------------------

def _standard_points(actor_dir: Path, frame_id: str) -> Dict[int, np.ndarray]:
    kp = keypoints_3d_by_id(actor_dir, frame_id)
    return {i: kp[k] for i, k in STANDARD_INDEX_TO_KEYPOINT3D_ID.items() if k in kp}


def classify_cameras(capture_id: str, frame_id: str) -> List[dict]:
    """Every camera not behind the head: [{camera_id, side, azimuth_deg,
    elevation_deg, angle_deg}], sorted left -> right. Face frame from the
    neutral keypoints_3d: forward = nose tip - earlobe midpoint, right = right
    earlobe - left earlobe (the actor's own right/left), up = towards the brows.
    Without those keypoints every camera is kept and labelled "unknown"."""
    actor_dir = _actor_dir(capture_id)
    cameras = load_cameras(capture_id)
    std = _standard_points(actor_dir, frame_id)
    needed = (NOSE_TIP_INDEX, LEFT_EARLOBE_INDEX, RIGHT_EARLOBE_INDEX)
    if not all(i in std for i in needed):
        return [{"camera_id": c, "side": "unknown", "azimuth_deg": 0.0, "elevation_deg": 0.0, "angle_deg": 0.0}
                for c in sorted(cameras)]

    lobe_mid = (std[LEFT_EARLOBE_INDEX] + std[RIGHT_EARLOBE_INDEX]) / 2
    forward = std[NOSE_TIP_INDEX] - lobe_mid
    forward /= np.linalg.norm(forward)
    right = std[RIGHT_EARLOBE_INDEX] - std[LEFT_EARLOBE_INDEX]
    right -= (right @ forward) * forward
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    if 30 in std and 50 in std and up @ (std[30] - std[50]) < 0:  # brow above chin
        up = -up

    out = []
    for cam_id, (_K, Rt) in cameras.items():
        center = -Rt[:3, :3].T @ Rt[:3, 3]
        rel = center - lobe_mid
        rel /= np.linalg.norm(rel)
        azimuth = float(np.degrees(np.arctan2(rel @ right, rel @ forward)))  # < 0: actor's left
        if abs(azimuth) > BEHIND_AZIMUTH_DEG:
            continue
        elevation = float(np.degrees(np.arcsin(np.clip(rel @ up, -1.0, 1.0))))
        angle = float(np.degrees(np.arccos(np.clip(rel @ forward, -1.0, 1.0))))
        side = "front" if angle <= FRONT_ANGLE_DEG else ("right" if azimuth > 0 else "left")
        out.append({"camera_id": cam_id, "side": side, "azimuth_deg": round(azimuth, 1),
                    "elevation_deg": round(elevation, 1), "angle_deg": round(angle, 1)})
    out.sort(key=lambda c: c["azimuth_deg"])
    return out


def default_cameras(cameras: List[dict]) -> List[str]:
    """PRESET_PER_SIDE cameras per side, left -> right: the most frontal ones, and
    for each ear the side cameras closest to a level profile view."""
    if cameras and cameras[0]["side"] == "unknown":
        return [c["camera_id"] for c in cameras[:3 * PRESET_PER_SIDE]]

    def side_preset(sign: int) -> List[dict]:
        side = [c for c in cameras if sign * c["azimuth_deg"] >= SIDE_PRESET_MIN_AZIMUTH_DEG]
        side.sort(key=lambda c: abs(c["elevation_deg"]) + abs(abs(c["azimuth_deg"]) - SIDE_PRESET_AZIMUTH_DEG))
        return side[:PRESET_PER_SIDE]

    left, right = side_preset(-1), side_preset(1)
    front = sorted((c for c in cameras if c["side"] == "front"), key=lambda c: c["angle_deg"])[:PRESET_PER_SIDE]
    picked = {c["camera_id"] for c in left + front + right}
    return [c["camera_id"] for c in cameras if c["camera_id"] in picked]


def neutral_info(capture_id: str) -> dict:
    neutral = resolve_neutral_frame(capture_id)
    frame_id = neutral["frame_id"]
    cameras = classify_cameras(capture_id, frame_id)
    ear_tops = load_ear_tops(capture_id, frame_id)
    return {
        "capture_id": capture_id,
        "frame_id": frame_id,
        "coverage": int(neutral.get("coverage", -1)),
        "needed_total": int(neutral.get("needed_total", len(STANDARD_INDEX_TO_KEYPOINT3D_ID))),
        "cameras": cameras,
        "default_cameras": default_cameras(cameras),
        "ear_tops": sorted(ear_tops),
    }


def _visible_camera_ids(capture_id: str, frame_id: str) -> set:
    return {c["camera_id"] for c in classify_cameras(capture_id, frame_id)}


# --- neutral images (PNG cache) ----------------------------------------------

def _decode_avif(actor_dir: Path, camera_id: str, frame_id: str) -> Image.Image:
    zip_path = actor_dir / "decoder" / "image" / f"cam{camera_id}.zip"
    if not zip_path.exists():
        raise FileNotFoundError(f"No image zip for camera {camera_id}: {zip_path}")
    with zipfile.ZipFile(zip_path) as zf:
        member = f"cam{camera_id}/{frame_id}.avif"
        try:
            data = zf.read(member)
        except KeyError as exc:
            raise FileNotFoundError(f"{member} not in {zip_path}") from exc
    return Image.open(io.BytesIO(data)).convert("RGB")


def write_neutral_png(capture_id: str, frame_id: str, camera_id: str) -> Path:
    """<images_root>/<capture>/<frame>/<camera>.png, decoded once from the AVIF zip."""
    path = get_settings().ava256_neutral_images_root / capture_id / frame_id / f"{camera_id}.png"
    if path.exists():
        return path
    image = _decode_avif(_actor_dir(capture_id), camera_id, frame_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{datetime.now().timestamp():.6f}.tmp")
    image.save(tmp_path, format="PNG")
    tmp_path.replace(path)
    return path


def neutral_image_path(capture_id: str, camera_id: str) -> Path:
    frame_id = resolve_neutral_frame(capture_id)["frame_id"]
    if camera_id not in _visible_camera_ids(capture_id, frame_id):
        raise ValueError(f"Camera {camera_id} is behind the head (or unknown) for {capture_id}")
    return write_neutral_png(capture_id, frame_id, camera_id)


def _image_size(capture_id: str, frame_id: str, camera_id: str) -> Tuple[int, int]:
    with Image.open(write_neutral_png(capture_id, frame_id, camera_id)) as image:
        return image.size  # (width, height)


# --- ear tops ----------------------------------------------------------------

def _ear_tops_path(capture_id: str) -> Path:
    return _landmark_dir(capture_id) / EAR_TOPS_NAME


def _read_ear_tops_file(capture_id: str) -> Optional[dict]:
    path = _ear_tops_path(capture_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_ear_tops(capture_id: str, frame_id: str) -> Dict[int, np.ndarray]:
    """index -> world xyz of the saved ear tops, if saved on this neutral frame."""
    payload = _read_ear_tops_file(capture_id)
    if not payload or payload.get("neutral_frame_id") != frame_id:
        return {}
    return {int(p["index"]): np.array([p["x"], p["y"], p["z"]], dtype=np.float64) for p in payload.get("points", [])}


def save_ear_point(capture_id: str, ear: Side, clicks: Dict[str, Tuple[float, float]]) -> dict:
    """Triangulates one ear top from normalized [0,1] clicks in >= 2 cameras of
    the neutral frame and merges it into ear_tops.json (replacing that side)."""
    if len(clicks) < 2:
        raise ValueError("Need clicks in at least 2 cameras")
    frame_id = resolve_neutral_frame(capture_id)["frame_id"]
    cameras = load_cameras(capture_id)
    missing = [c for c in clicks if c not in cameras]
    if missing:
        raise ValueError(f"Camera(s) not in camera_calibration.json for {capture_id}: {missing}")

    camera_ids = sorted(clicks)
    sizes = {c: _image_size(capture_id, frame_id, c) for c in camera_ids}
    intrinsic_array = np.array([cameras[c][0] for c in camera_ids])
    extrinsic_list = [cameras[c][1] for c in camera_ids]
    point_list = []
    for i, cam_id in enumerate(camera_ids):
        x_norm, y_norm = clicks[cam_id]
        width, height = sizes[cam_id]
        point_list.append((i, np.array([x_norm * width, y_norm * height]), 0.0 < x_norm < 1.0 and 0.0 < y_norm < 1.0))
    widths = [sizes[c][0] for c in camera_ids]
    heights = [sizes[c][1] for c in camera_ids]
    world_xyz = triangulate_point(point_list, intrinsic_array, extrinsic_list, widths, heights)
    if world_xyz is None:
        raise ValueError("Triangulation failed -- no in-bounds click among the given cameras")
    world_xyz = np.asarray(world_xyz, dtype=np.float64).reshape(3)

    # Reprojection error per clicked camera, kept for reference.
    errors = {}
    for cam_id in camera_ids:
        K, Rt = cameras[cam_id]
        px, _z = _project(world_xyz[None], K, Rt)
        width, height = sizes[cam_id]
        x_norm, y_norm = clicks[cam_id]
        errors[cam_id] = round(float(np.hypot(px[0, 0] - x_norm * width, px[0, 1] - y_norm * height)), 2)

    index = EAR_INDEX_BY_SIDE[ear]
    path = _ear_tops_path(capture_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            payload = _read_ear_tops_file(capture_id) or {}
            if payload.get("neutral_frame_id") not in (None, frame_id):
                raise Ava256NeutralError(
                    409,
                    f"{path} holds ear tops for neutral frame {payload['neutral_frame_id']}, but the neutral "
                    f"frame is now {frame_id} -- move that file aside to start over",
                )
            points = [p for p in payload.get("points", []) if int(p["index"]) != index]
            points.append({
                "index": index,
                "x": float(world_xyz[0]), "y": float(world_xyz[1]), "z": float(world_xyz[2]),
                "clicks": {c: [float(clicks[c][0]), float(clicks[c][1])] for c in camera_ids},
                "reprojection_error_px": errors,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            })
            points.sort(key=lambda p: p["index"])
            out = {
                "capture_id": capture_id,
                "neutral_frame_id": frame_id,
                "space": "world (landmarks_ava-256 group files / keypoints_3d), before dynamic_transform",
                "points": points,
            }
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    return {"world_xyz": world_xyz.tolist(), "cameras_used": camera_ids, "reprojection_error_px": errors}


# --- landmarks for display ---------------------------------------------------

def _neutral_world_points(capture_id: str, frame_id: str) -> Dict[int, np.ndarray]:
    """Standard (< 74) points of the neutral frame: the propagated group files
    if present, else keypoints_3d; then the saved ear tops on top."""
    points: Dict[int, np.ndarray] = {}
    for path in sorted(_landmark_dir(capture_id).glob(f"*_{frame_id}.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for p in payload.get("points", []) if isinstance(payload, dict) else []:
            if int(p["index"]) < 74:
                points[int(p["index"])] = np.array([p["x"], p["y"], p["z"]], dtype=np.float64)
    if not points:
        points = _standard_points(_actor_dir(capture_id), frame_id)
    points.update(load_ear_tops(capture_id, frame_id))
    return points


def neutral_landmarks(capture_id: str, camera_id: str) -> dict:
    """LandmarkFrame (normalized x/y, camera depth z) of the neutral frame's
    points projected into one camera."""
    frame_id = resolve_neutral_frame(capture_id)["frame_id"]
    cameras = load_cameras(capture_id)
    if camera_id not in cameras:
        raise FileNotFoundError(f"Camera {camera_id} not in camera_calibration.json for {capture_id}")
    world = _neutral_world_points(capture_id, frame_id)
    if not world:
        return {"frame_idx": 0, "points": []}
    indices = sorted(world)
    K, Rt = cameras[camera_id]
    px, z = _project(np.stack([world[i] for i in indices]), K, Rt)
    width, height = _image_size(capture_id, frame_id, camera_id)
    points = [
        {"index": idx, "x": float(px[j, 0] / width), "y": float(px[j, 1] / height), "z": float(z[j]), "visibility": 1.0}
        for j, idx in enumerate(indices) if z[j] > 1e-8
    ]
    return {"frame_idx": 0, "points": points}
