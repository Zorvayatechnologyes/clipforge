"""Locating and driving ffmpeg / ffprobe."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class MediaError(RuntimeError):
    """ffmpeg or ffprobe failed, or the media could not be understood."""


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """Find an ffmpeg binary: explicit override, PATH, then the pip fallback."""
    import os

    override = os.getenv("CLIPFORGE_FFMPEG")
    if override:
        if not Path(override).exists():
            raise MediaError(f"CLIPFORGE_FFMPEG points at a missing file: {override}")
        return override

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg  # optional dependency

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment specific
        raise MediaError(
            "ffmpeg was not found. Install it (apt install ffmpeg / brew install "
            "ffmpeg / winget install ffmpeg) or `pip install imageio-ffmpeg`, "
            "or set CLIPFORGE_FFMPEG to the binary path."
        ) from exc


@lru_cache(maxsize=1)
def ffprobe_exe() -> str | None:
    import os

    override = os.getenv("CLIPFORGE_FFPROBE")
    if override and Path(override).exists():
        return override
    found = shutil.which("ffprobe")
    if found:
        return found
    # imageio-ffmpeg ships no ffprobe; sit next to ffmpeg if it happens to exist
    try:
        sibling = Path(ffmpeg_exe()).with_name("ffprobe")
        if sibling.exists():
            return str(sibling)
    except MediaError:
        pass
    return None


def run(cmd: list[str], *, timeout: float | None = None, cwd: str | None = None) -> str:
    """Run a command, return stdout, raise MediaError with the stderr tail."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"command timed out after {timeout}s: {cmd[0]}") from exc

    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise MediaError(f"{Path(cmd[0]).name} failed ({proc.returncode}):\n{tail}")
    return proc.stdout


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    has_video: bool

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")


def probe(path: str | Path) -> MediaInfo:
    """Read duration, dimensions and audio presence. Works without ffprobe."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"input file does not exist: {path}")

    probe_bin = ffprobe_exe()
    if probe_bin:
        raw = run(
            [
                probe_bin,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if data:
            streams = data.get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
            duration = 0.0
            for source in (data.get("format", {}), video or {}):
                try:
                    duration = float(source.get("duration") or 0.0)
                except (TypeError, ValueError):
                    duration = 0.0
                if duration:
                    break
            fps = 0.0
            if video:
                try:
                    num, _, den = str(video.get("avg_frame_rate", "0/1")).partition("/")
                    fps = float(num) / float(den or 1)
                except (TypeError, ValueError, ZeroDivisionError):
                    fps = 0.0
            return MediaInfo(
                path=path,
                duration=duration,
                width=int((video or {}).get("width") or 0),
                height=int((video or {}).get("height") or 0),
                fps=fps,
                has_audio=audio is not None,
                has_video=video is not None,
            )

    # Fallback: scrape `ffmpeg -i` stderr.
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    text = proc.stderr or ""
    duration = 0.0
    if m := _DURATION_RE.search(text):
        h, mnt, sec = m.groups()
        duration = int(h) * 3600 + int(mnt) * 60 + float(sec)
    width = height = 0
    if m := _VIDEO_RE.search(text):
        width, height = int(m.group(1)), int(m.group(2))
    fps = 0.0
    if m := _FPS_RE.search(text):
        fps = float(m.group(1))
    if duration <= 0 and width == 0:
        raise MediaError(f"could not read media info from {path.name}")
    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio="Audio:" in text,
        has_video="Video:" in text,
    )


@lru_cache(maxsize=32)
def has_filter(name: str) -> bool:
    """True when the ffmpeg build exposes a filter (e.g. 'subtitles', 'gblur')."""
    try:
        out = run([ffmpeg_exe(), "-hide_banner", "-filters"])
    except MediaError:
        return False
    return any(re.search(rf"\s{re.escape(name)}\s", line) for line in out.splitlines())


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    """Decode any input to mono 16-bit PCM WAV, which whisper and numpy both like."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )
    if not dst.exists() or dst.stat().st_size < 1024:
        raise MediaError("audio extraction produced an empty file (no audio track?)")
    return dst
