# Nersemble pipeline — handoff

The code needed to label, propagate and wrap Nersemble. It was copied on 2026-10-12 out of the
original `/scratch/ondemand32/irwinngo/{FaceView,wrap_script}` working folders on the Medusa cluster,
which also contain a lot of unrelated experiments. `MANIFEST.txt` lists everything that was copied.

Plan for the new cluster: start fresh from **actor 300 onward**. Earlier actors' labels, landmarks and
wraps stay on Medusa. They were not transferred: `landmarks/` is about 50 M small files, too slow to tar
over NFS.

## Layout

```
FaceView/
  backend/                     FastAPI labelling backend (app/), reads backend/.env
  frontend/                    React/Vite labelling UI, reads frontend/.env
  script_process_sequence_sapiens.sh   batch driver for the Sapiens landmark step (sources backend/.env)
  scripts/preprocess/process_sequence_sapiens.py   delegation videos + Sapiens 2D landmarks
  scripts/local_labeling/      optional offline labeller + merge-back (see its README)
  co-tracker/                  CoTracker code + checkpoints/scaled_offline.pth
  configs/ flame_model/ sapiens2/ default_cameras.json whitelist.json completed_sequences_sapiens.json
wrap_script/
  run_neutral_cotracker_propagation_batch.py  -> run_neutral_cotracker_propagation.py  (propagation)
  run_pipeline_batch_all.py                   -> run_pipeline_all.py                   (wrapping)
  nersemble_paths.py           loads every path from wrap_script/.env (raises if a key is missing)
  backend/                     small FaceView-style config/services used by propagation
  wrap_template/ blendshape/ flame_model/ static_face_mask/ keypoint_*.txt *_correspondence*.json
```

## Configuration — three `.env` files (all paths live here, none in code)

| File | Read by | Must edit on the new cluster |
|---|---|---|
| `wrap_script/.env` | `nersemble_paths.py`, `run_wrap_script*.py` | everything: tools, data/work roots, `FACEVIEW_ROOT`, `WRAP_SCRIPT_ROOT`, models, venv paths |
| `FaceView/backend/.env` | `backend/app/core/config.py`, `script_process_sequence_sapiens.sh` | data/output/landmarks/processed roots, `LABEL_TRACKER_PATH`, `WRAP_SCRIPT_ROOT`, `SAPIENS_*`, `NERSEMBLE_TRACKING_*`, `TRACKER_PYTHON_BIN` |
| `FaceView/frontend/.env` | `vite.config.ts` | `BACKEND_URL` (default `http://localhost:8000`) |

- `.env.example` files sit next to each `.env` as templates.
- A variable already set in the shell overrides the `.env` value. This is handy for sandbox tests,
  e.g. `OUTPUT_ROOT=/tmp/test python run_pipeline_all.py ...`.
- The two backend configs use `extra = "ignore"`, so the backend `.env` can carry keys meant for other scripts.
- `FACESCAPE_*` / `AVA256_*` keys in the backend `.env` are only for other datasets. Point them anywhere,
  e.g. an empty dir, if those datasets don't exist on the new cluster.

## External dependencies (NOT in this folder)

- **Faceform Wrap**: `WrapCmd` + license file (`WrapCmd`, `WrapLicense`). The license is tied to the
  account/machine; check that it is valid on the new cluster.
- **Python envs**: full package lists and rebuild steps are in `envs/README.md`
  (`envs/*.freeze.txt`).
  - `VENV_PYTHON` (faceview_venv): torch, pytorch3d, nvdiffrast, backend requirements
    (`FaceView/backend/requirements.txt`).
  - `SAPIENS_PYTHON` (faceview_sapiens_venv): the `sapiens` package is installed inside this venv
    (`src/sapiens`), not imported from `FaceView/sapiens2/`.
  - `TRACKER_PYTHON_BIN`: MediaPipe/CoTracker subprocess interpreter. On Medusa it points at another
    user's conda env, so build your own.
  - Medusa build scripts for reference: `/scratch/ondemand32/irwinngo/envs/build_faceview_*.sh`
    and `*_requirements.txt`.
- **Node** 20.x for the frontend (`npm ci`; `node_modules` is not copied).
- **Models**:
  - `SAPIENS_CHECKPOINT_ROOT` (Sapiens2 checkpoints);
  - `MODELS_ROOT` (SAM, RAFT, …);
  - `LANDMARK_LINES_PATH` (`landmark_lines.npy`).
- **Data**:
  - `DATA_ROOT`: raw Nersemble videos/calibration.
  - `NERSEMBLE_TRACKING_ROOT` / `NERSEMBLE_TRACKING_LOCAL_ROOT`: FLAME tracking fits (`fit.npz`) used by
    the Sapiens step.
  - `MESHROOM_DATA_ROOT`: per-frame photogrammetry scans `<actor>/<sequence>/frame_<f>/mesh.obj.gz`
    (+ `output_meshroom.mg`, `release_manifest.json`). Both propagation (neutral-frame wrap) and
    wrapping need them.
    - **On Medusa these exist only for actors 017–298.** They were produced on Killarney and rsynced in
      (see `meshroom-tracking-full/README.md` and `TRANSFER_PROVENANCE.json` there).
    - Actors 300+ need scans generated first. The Medusa generator (`wrap_script/run_meshroom_batch.py`
      + `meshroom_template/`, needs `MeshroomCompute`) is **not** in this copy.

## Run order (one actor/sequence pair moves through these states in `label_tracker.json`)

1. **Sapiens landmarks + delegation videos.** Set `MIN_ACTOR` / `MAX_ACTOR` / `CAMERAS` /
   `TARGET_SEQUENCES` at the top of the script, then:
   ```
   bash FaceView/script_process_sequence_sapiens.sh
   ```
   - Writes `PROCESSED_ROOT/<actor>/<seq>/cam_<id>_delegation.mp4` (every 5th raw frame, 550×802).
     Every frame index in the UI, landmarks and CoTracker is in this delegation-frame space.
   - Writes Sapiens `LANDMARKS_ROOT/<actor>/<seq>/cam_<id>/frame_<n>.json`.
   - Progress is tracked in `FaceView/completed_sequences_sapiens.json`.
2. **Label in FaceView** (neutral frame + landmarks per actor):
   ```
   cd FaceView/backend && $VENV_PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   cd FaceView/frontend && npm ci && npm run dev
   ```
3. **Propagate.** Picks up `unlabeled` pairs that have scans and delegation videos for every default camera.
   ```
   cd wrap_script && $VENV_PYTHON run_neutral_cotracker_propagation_batch.py --parallel 2
   ```
   - The first pair of each actor wraps that actor's neutral frame and builds its face mask.
   - Writes `skin/ear/nosebridge/undernose/eyebrow/chin_frame_<n>.json` to `LANDMARKS_ROOT`.
4. **Review propagation** in the UI. "Confirm Labeled" moves a pair `unlabeled → unreviewed`.
5. **Wrap.** Polls forever for `unreviewed` pairs.
   ```
   cd wrap_script && $VENV_PYTHON run_pipeline_batch_all.py --parallel 8 --ignore-list --wrap-max-threads 4 --min-actor 300
   ```
   - Writes `OUTPUT_ROOT/<actor>/<seq>/<frame>/wrapped_mesh.obj`, plus overlay renders in
     `LINE_RENDERS_ROOT` / `POINT_OVERLAY_RENDERS_ROOT`.
6. **Review wraps** in the UI.
   - Confirm → `confirmed`, and the sequence is exported to `FINAL_WRAPPED_DATA_ROOT` (`.pt`).
   - Unconfirm → `unconfirmed`, which can be re-wrapped later (`run_pipeline_batch_all.py` has
     `--confirmed-only` / unconfirmed re-wrap modes; see its docstring).

## Fresh-start checklist for the new cluster

- [ ] Edit all three `.env` files (`FACEVIEW_ROOT` / `WRAP_SCRIPT_ROOT` = this folder's new location).
- [ ] Start with a **new, empty** `label_tracker.json` at `LABEL_TRACKER_PATH`.
- [ ] Reset the progress files copied from Medusa, which still list Medusa's actors:
  - `FaceView/completed_sequences_sapiens.json`;
  - `wrap_script/completed_sequences*.json`;
  - `FaceView/scripts/local_labeling/label_tracker.json`, a stale snapshot used only by the offline labeller.
- [ ] Review `wrap_script/ignore_actors.json`, `ignore_actor_sequence_pairs.json` and `whitelist*.json`.
- [ ] `MIN_ACTOR="300"` in `script_process_sequence_sapiens.sh`, and `--min-actor 300` for the batches.
- [ ] Get Meshroom scans for actors 300+ into `MESHROOM_DATA_ROOT`.
- [ ] Smoke test before batching. These were all verified on Medusa from this copy on 2026-10-12:
  - `python wrap_script/run_neutral_cotracker_propagation.py <actor> <seq> --dry-run`;
  - `OUTPUT_ROOT=<scratch dir> python wrap_script/run_pipeline_all.py <actor> <seq> <frame> 3208 --face-mask-path <face_list.txt>`;
  - backend import (`python -c "import app.main"` from `FaceView/backend`);
  - `npx vite build` in `FaceView/frontend`.

## Current tuning (as of 2026-10-12)

- Faceform BlendWrapping for Nersemble: smoothness 1.5, control-point weight 10.
- QA scripts that stayed on Medusa (`flag_skin_error.py`, `auto_confirm.py`) are not part of this copy.
  `flag_skin_error.py` flags a pair when a frame's mean skin-landmark error is over 4 px, or the chin is
  more than 40 px from Sapiens2's raw chin (landmark 50) in any camera.
