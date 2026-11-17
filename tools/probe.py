"""Try several YouTube player clients and report which ones return formats."""

import json
import subprocess

VIDEOS = {
    "me_at_the_zoo": "https://www.youtube.com/watch",
    "popular_music": "https://www.youtube.com/watch",
}
CLIENTS = ["tv", "android", "ios", "web_safari", "mweb", "web_embedded"]

lines = []
for name, url in VIDEOS.items():
    for client in CLIENTS:
        cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download",
               "--extractor-args", "youtube:player_client=" + client, url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            lines.append(name + "/" + client + " TIMEOUT")
            continue
        if proc.returncode == 0:
            try:
                info = json.loads(proc.stdout)
            except Exception:
                lines.append(name + "/" + client + " BADJSON")
                continue
            formats = info.get("formats") or []
            lines.append(
                name + "/" + client + " OK formats=" + str(len(formats))
                + " duration=" + str(info.get("duration"))
                + " title=" + str(info.get("title"))[:40]
            )
        else:
            err = (proc.stderr or "").strip().splitlines()
            lines.append(name + "/" + client + " FAIL " + (err[-1][:90] if err else "?"))

text = "\n".join(lines) + "\n"
with open("probe.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)
