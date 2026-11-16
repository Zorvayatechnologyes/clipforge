"""Fetching the source video: YouTube (or any yt-dlp supported site), or a local file."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from .ffmpeg import MediaError

Progress = Callable[[float, str], None] | None

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts"}


class DownloadError(RuntimeError):
    """yt-dlp could not fetch the video."""


def is_url(value: str) -> bool:
    return bool(_URL_RE.match(value.strip()))


def fetch(source: str, target_dir: Path, *, max_height: int = 1080,
          cookies_from_browser: str | None = None, progress: Progress = None) -> Path:
    """Return a local video path for `source` (URL or existing file)."""
    source = source.strip()
    if not source:
        raise DownloadError("no URL or file path given")

    if not is_url(source):
        local = Path(source).expanduser()
        if not local.exists():
            raise DownloadError(f"file not found: {source}")
        if local.suffix.lower() not in VIDEO_SUFFIXES:
            raise DownloadError(
                f"unsupported file type '{local.suffix}' — use one of "
                f"{', '.join(sorted(VIDEO_SUFFIXES))}"
            )
        if progress:
            progress(1.0, f"using local file {local.name}")
        return local

    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise DownloadError(
            "yt-dlp is not installed. Run: pip install -r requirements.txt"
        ) from exc

    target_dir.mkdir(parents=True, exist_ok=True)

    def hook(payload: dict) -> None:
        if not progress:
            return
        status = payload.get("status")
        if status == "downloading":
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
            done = payload.get("downloaded_bytes") or 0
            if total:
                progress(min(0.99, done / total), "downloading")
            else:
                progress(0.5, "downloading")
        elif status == "finished":
            progress(1.0, "merging audio and video")

    fmt = (
        f"bv*[height<={max_height}][vcodec!*=av01]+ba/"
        f"b[height<={max_height}]/"
        f"bv*[height<={max_height}]+ba/b"
    )
    options: dict = {
        "format": fmt,
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "restrictfilenames": True,
    }
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    if progress:
        progress(0.01, "resolving video")

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(source, download=True)
            if info is None:
                raise DownloadError("yt-dlp returned no video info")
            if info.get("_type") == "playlist":
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise DownloadError("playlist had no playable entries")
                info = entries[0]
            path = Path(ydl.prepare_filename(info))
    except DownloadError:
        raise
    except Exception as exc:  # yt-dlp raises many subclasses
        message = str(exc).strip().splitlines()
        detail = message[0] if message else exc.__class__.__name__
        if "live" in detail.lower() and "not" in detail.lower():
            raise DownloadError(
                f"this looks like a stream that is still live — {detail}. "
                "Wait for the VOD to be published, or record it first."
            ) from exc
        raise DownloadError(f"download failed: {detail}") from exc

    if not path.exists():
        # yt-dlp sometimes reports a different extension than it wrote
        stem = info.get("id")
        candidates = [p for p in target_dir.glob(f"{stem}.*") if p.suffix.lower() in VIDEO_SUFFIXES]
        if not candidates:
            raise DownloadError("download finished but no video file was written")
        path = max(candidates, key=lambda p: p.stat().st_size)

    if progress:
        progress(1.0, f"downloaded {path.name}")
    return path


def cleanup_source(path: Path, work_dir: Path, *, keep: bool) -> None:
    """Delete the downloaded video unless asked to keep it."""
    if keep:
        return
    try:
        path.resolve().relative_to(work_dir.resolve())
    except ValueError:
        return  # never delete something outside our work dir
    if path.exists() and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def ensure_disk_space(path: Path, needed_bytes: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < needed_bytes:
        raise DownloadError(
            f"not enough disk space in {path}: {free / 1e9:.1f} GB free, "
            f"need about {needed_bytes / 1e9:.1f} GB"
        )


__all__ = ["DownloadError", "MediaError", "fetch", "is_url", "cleanup_source",
           "ensure_disk_space", "VIDEO_SUFFIXES"]
