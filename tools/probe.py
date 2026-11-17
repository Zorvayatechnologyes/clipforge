"""Ask YouTube for one video's metadata and record what happened."""

import json
import subprocess

TARGET = "https://www.youtube.com/watch"
lines = []

try:
    proc = subprocess.run(
        ["yt-dlp", "-J", "--no-warnings", "--skip-download", TARGET],
        capture_output=True, text=True, timeout=240,
    )
    lines.append("exit=" + str(proc.returncode))
    if proc.returncode == 0:
        info = json.loads(proc.stdout)
        lines.append("YT_OK")
        lines.append("TITLE=" + str(info.get("title")))
        lines.append("DURATION=" + str(info.get("duration")))
        lines.append("FORMATS=" + str(len(info.get("formats") or [])))
    else:
        lines.append("YT_FAIL")
        lines.append("STDERR=" + (proc.stderr or "").strip()[:1000])
except Exception as exc:
    lines.append("YT_ERROR")
    lines.append(repr(exc)[:500])

text = "\n".join(lines) + "\n"
with open("probe.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)
