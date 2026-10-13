# Nersemble pipeline — notes for Claude

Read `HANDOFF.md` first: layout, `.env` configuration, external dependencies, run order, fresh-start
checklist. Python environments are covered in `envs/README.md`.

## How this repo is wired
- Every filesystem path comes from three `.env` files:
  - `wrap_script/.env`, loaded by `wrap_script/nersemble_paths.py` (a missing key raises);
  - `FaceView/backend/.env`, loaded by the pydantic `Settings` in `backend/app/core/config.py`;
  - `FaceView/frontend/.env`.
- Don't hard-code absolute paths. Add a key to the right `.env` and its `.env.example` instead.
- A shell variable overrides `.env`. Use that for sandbox runs, e.g. `OUTPUT_ROOT=<scratch dir> python wrap_script/run_pipeline_all.py ...`.
- Frame indices everywhere (UI, landmarks JSON, CoTracker, neutral frame) are delegation-video frames:
  every 5th raw frame.
- Pair status lives in `label_tracker.json` (`LABEL_TRACKER_PATH`):
  `unlabeled → unreviewed → unconfirmed/confirmed`. Propagation picks `unlabeled`; wrapping picks `unreviewed`.
- Wrapping needs per-frame Meshroom scans in `MESHROOM_DATA_ROOT/<actor>/<seq>/frame_<f>/mesh.obj.gz`.

## Working rules (from the user)
- Test in a sandbox (separate `OUTPUT_ROOT` / tracker copy) before touching real outputs.
- Don't commit or push unless asked.
- Leave the user's running batch jobs alone.
- Confirm before anything destructive or production-changing (unconfirming, deleting exports,
  overwriting the tracker). Back up before deleting.
- Kill processes by PID, never by pattern. `pkill -f` / `pgrep -f` match the tool's own shell, so a
  pattern kill can kill your shell and a `pgrep -f` wait loop never ends.
- With `tar | zstd` (or any pipe) use `set -o pipefail`. Otherwise a killed `tar` still produces a
  "finished" archive.
- Shared NFS scratch is very slow for small files (~0.1 s/file under load). Avoid `du`/`find`/`tar`
  over `landmarks/` (millions of JSONs); sample one actor and extrapolate.
