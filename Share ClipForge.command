#!/bin/bash
# Start ClipForge and give it a public HTTPS address, so you can use it from
# your phone or another computer.
#
# The engine still runs on THIS machine. That is deliberate: YouTube blocks
# cloud servers, so a hosted engine cannot download videos. Your home
# connection can.
cd "$(dirname "$0")" || exit 1

pause_exit() {
  echo
  read -r -p "Press return to close this window." _
  exit "${1:-0}"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  pause_exit 1
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

# ---- the tunnel helper -------------------------------------------------
BIN=".cloudflared/cloudflared"
if [ ! -x "$BIN" ]; then
  mkdir -p .cloudflared
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)   ASSET=cloudflared-darwin-arm64.tgz ;;
    Darwin-x86_64)  ASSET=cloudflared-darwin-amd64.tgz ;;
    Linux-x86_64)   ASSET=cloudflared-linux-amd64 ;;
    Linux-aarch64)  ASSET=cloudflared-linux-arm64 ;;
    *) echo "Unsupported platform: $(uname -s)-$(uname -m)"; pause_exit 1 ;;
  esac
  echo "Downloading the tunnel helper ($ASSET)..."
  URL="https://github.com/cloudflare/cloudflared/releases/latest/download/$ASSET"
  case "$ASSET" in
    *.tgz)
      curl -sSL "$URL" -o .cloudflared/cf.tgz \
        && tar -xzf .cloudflared/cf.tgz -C .cloudflared cloudflared \
        && rm -f .cloudflared/cf.tgz
      ;;
    *)
      curl -sSL "$URL" -o "$BIN"
      ;;
  esac
  chmod +x "$BIN" 2>/dev/null
  if [ ! -x "$BIN" ]; then
    echo "Could not download the tunnel helper. Check your connection."
    pause_exit 1
  fi
fi

# ---- engine ------------------------------------------------------------
echo "Starting the engine..."
python -m clipforge serve > .cloudflared/engine.log 2>&1 &
ENGINE_PID=$!
trap 'kill $ENGINE_PID 2>/dev/null' EXIT INT TERM

ready=""
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    ready="yes"
    break
  fi
  sleep 1
done

if [ -z "$ready" ]; then
  echo "The engine did not start. Last lines of .cloudflared/engine.log:"
  tail -n 15 .cloudflared/engine.log
  pause_exit 1
fi
echo "Engine is up on http://127.0.0.1:8000"

# ---- public address ----------------------------------------------------
echo
echo "Opening a public tunnel. Your address appears below in a moment."
echo

"$BIN" tunnel --url http://127.0.0.1:8000 --no-autoupdate 2>&1 |
while IFS= read -r line; do
  url=$(printf '%s' "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1)
  if [ -n "$url" ] && [ "$url" != "$LAST_URL" ]; then
    LAST_URL="$url"
    printf '\n  ============================================================\n'
    printf '   PUBLIC ADDRESS:  %s\n' "$url"
    printf '   Paste that into the Engine box on the ClipForge page.\n'
    printf '   Leave this window open while you use it.\n'
    printf '  ============================================================\n\n'
  fi
  case "$line" in
    *ERR*) printf '%s\n' "$line" ;;
  esac
done

echo "Tunnel closed."
pause_exit 0
