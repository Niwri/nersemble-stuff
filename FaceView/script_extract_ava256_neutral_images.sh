#!/usr/bin/env bash
set -euo pipefail

# Pre-extracts each Ava-256 capture's neutral-frame camera PNGs for the
# "Neutral frame · ear tops" tab (optional -- the backend extracts lazily too).
# Extra arguments go to the script, e.g. --captures 20210810--1306--FXN596 --workers 4
cd "$(dirname "${BASH_SOURCE[0]}")"

# Paths come from backend/.env (AVA256_DATA_ROOT, AVA256_LANDMARK_ROOT, AVA256_NEUTRAL_IMAGES_ROOT);
# a variable already set in the shell wins (same as the backend's own Settings), e.g. for a sandbox test.
while IFS='=' read -r key value; do
  [[ "${key}" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
  [[ -n "${!key+x}" ]] && continue
  value="${value%\"}"; value="${value#\"}"
  export "${key}=${value}"
done < backend/.env
VENV_PYTHON="${VENV_PYTHON:-python}"
"${VENV_PYTHON}" scripts/preprocess/extract_ava256_neutral_images.py "$@"
