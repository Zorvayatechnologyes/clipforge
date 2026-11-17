@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python was not found.
  echo Install it from https://www.python.org/downloads/ and tick
  echo "Add Python to PATH" during setup, then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo Setting up ClipForge for the first time. This takes a few minutes.
  python -m venv .venv
)

call ".venv\Scripts\activate.bat"

echo Installing dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo Note: ffmpeg is not installed. ClipForge will use a bundled copy,
  echo       which works but is slower. For best results install ffmpeg:
  echo       winget install Gyan.FFmpeg
  echo.
)

echo.
echo Starting ClipForge. Your browser will open in a moment.
echo Keep this window open while you use it. Close it to stop.
echo.

start "" http://127.0.0.1:8000
python -m clipforge serve

echo.
echo ClipForge stopped.
pause
