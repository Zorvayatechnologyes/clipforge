#!/bin/bash
# Double-click this file in Finder to start ClipForge.
cd "$(dirname "$0")" || exit 1

pause_exit() {
  echo
  read -r -p "Press return to close this window." _
  exit "${1:-0}"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required."
  echo "Install it from https://www.python.org/downloads/ and run this again."
  pause_exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Note: ffmpeg is not installed. ClipForge will use a bundled copy,"
  echo "      which works but is slower. For best results: brew install ffmpeg"
  echo
fi

if [ ! -d .venv ]; then
  echo "Setting up ClipForge for the first time. This takes a few minutes."
  python3 -m venv .venv || pause_exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate || pause_exit 1

echo "Installing dependencies..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || pause_exit 1

echo
echo "Starting ClipForge. Your browser will open in a moment."
echo "Keep this window open while you use it. Close it to stop."
echo

( sleep 3; open "http://127.0.0.1:8000" ) &
python -m clipforge serve

echo
echo "ClipForge stopped."
pause_exit 0
