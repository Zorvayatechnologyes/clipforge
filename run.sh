#!/usr/bin/env bash
# One-command start: creates a venv, installs dependencies, opens the web UI.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "creating virtual environment..."
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "installing dependencies (first run only)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "note: no system ffmpeg found, using the bundled imageio-ffmpeg binary."
  echo "      for best results install ffmpeg properly (apt/brew/winget)."
fi

exec python -m clipforge serve
