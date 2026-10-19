"""Pre-extract every Ava-256 capture's neutral-frame camera images as PNGs for
the Ava-256 "Neutral frame · ear tops" tab, so the grid loads without decoding
AVIFs on demand (backend/app/services/ava256_neutral.py does that lazily
otherwise -- same files, same cache).

For each capture under AVA256_DATA_ROOT (or --captures): resolve the neutral
frame (cached neutral_frame.json, else computed and cached, as
wrap_script_ava256/neutral_frame.py does) and write
AVA256_NEUTRAL_IMAGES_ROOT/<capture>/<frame>/<camera>.png for every camera that
isn't behind the head. Existing PNGs are skipped.

Usage (cwd = FaceView/): python scripts/preprocess/extract_ava256_neutral_images.py [--captures A B ...] [--workers 8]
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import get_settings  # noqa: E402
from backend.app.services import ava256_neutral  # noqa: E402


def extract_capture(capture_id: str) -> str:
    try:
        frame_id = ava256_neutral.resolve_neutral_frame(capture_id)["frame_id"]
        cameras = ava256_neutral.classify_cameras(capture_id, frame_id)
        written = failed = 0
        for camera in cameras:
            path = get_settings().ava256_neutral_images_root / capture_id / frame_id / f"{camera['camera_id']}.png"
            if path.exists():
                continue
            try:
                ava256_neutral.write_neutral_png(capture_id, frame_id, camera["camera_id"])
                written += 1
            except (FileNotFoundError, OSError) as exc:
                failed += 1
                print(f"  {capture_id} {camera['camera_id']}: {exc}", flush=True)
        return f"{capture_id} {frame_id}: {written} written, {len(cameras) - written - failed} existing, {failed} failed"
    except Exception as exc:  # one bad capture shouldn't stop the batch
        return f"{capture_id}: FAILED {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--captures", nargs="*", help="Capture ids (default: every folder in AVA256_DATA_ROOT)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    data_root = get_settings().ava256_data_root
    captures = args.captures or sorted(p.name for p in data_root.iterdir() if p.is_dir() and "--" in p.name)
    print(f"{len(captures)} capture(s) -> {get_settings().ava256_neutral_images_root}", flush=True)
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for line in pool.map(extract_capture, captures):
            print(line, flush=True)


if __name__ == "__main__":
    main()
