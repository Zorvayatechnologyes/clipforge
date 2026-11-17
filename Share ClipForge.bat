@echo off
setlocal
cd /d "%~dp0"

REM Start ClipForge and give it a public HTTPS address, so you can use it from
REM your phone or another computer.
REM
REM The engine still runs on THIS machine. That is deliberate: YouTube blocks
REM cloud servers, so a hosted engine cannot download videos.

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

if not exist ".cloudflared" mkdir ".cloudflared"

if not exist ".cloudflared\cloudflared.exe" (
  echo Downloading the tunnel helper...
  powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile .cloudflared\cloudflared.exe"
)

if not exist ".cloudflared\cloudflared.exe" (
  echo Could not download the tunnel helper. Check your connection.
  pause
  exit /b 1
)

echo Starting the engine...
start "ClipForge engine" /min python -m clipforge serve

timeout /t 8 /nobreak >nul

echo.
echo Opening a public tunnel. Your address appears below in a moment.
echo Paste it into the Engine box on the ClipForge page.
echo Leave this window open while you use it.
echo.

".cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8000 --no-autoupdate

echo.
echo Tunnel closed.
pause
