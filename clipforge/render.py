"""Cutting a clip and making it vertical, with captions burned in."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import Settings
from .ffmpeg import MediaInfo, MediaError, ffmpeg_exe, has_filter, run
from .highlight import Candidate
from .subtitles import CaptionLine, write_ass, write_srt
from .transcribe import Segment

Progress = Callable[[float, str], None] | None


def output_size(settings: Settings) -> tuple[int, int]:
    if settings.vertical:
        return settings.width, settings.height
    return 1920, 1080


def _video_graph(settings: Settings, *, subtitles: bool) -> str:
    """Blurred-pad vertical reframe (or a plain scale for horizontal)."""
    width, height = output_size(settings)
    if settings.vertical:
        graph = (
            f"[0:v]split=2[bgs][fgs];"
            f"[bgs]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=28,"
            f"eq=brightness=-0.16:saturation=0.88[bg];"
            f"[fgs]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs2];"
            f"[bg][fgs2]overlay=(W-w)/2:(H-h)/2[v0]"
        )
    else:
        graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[v0]"
        )
    if subtitles:
        graph += ";[v0]setsar=1[v1];[v1]ass=subs.ass[v]"
    else:
        graph += ";[v0]setsar=1[v]"
    return graph


def render_clip(
    source: Path,
    info: MediaInfo,
    cand: Candidate,
    out_path: Path,
    settings: Settings,
    *,
    segments: list[Segment] | None = None,
    subtitle_lines: list[CaptionLine] | None = None,
    progress: Progress = None,
) -> dict:
    """Render one clip. Returns metadata about the files written."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    start = max(0.0, min(cand.start, max(0.0, info.duration - 1.0)))
    duration = max(1.0, min(cand.duration, info.duration - start))
    if duration < 1.0:
        raise MediaError(f"clip at {start:.1f}s is too short to render")

    work = out_path.parent
    subs_path = work / "subs.ass"
    want_subs = settings.subtitles and subtitle_lines and has_filter("ass")
    if want_subs:
        width, height = output_size(settings)
        write_ass(subtitle_lines, subs_path, width=width, height=height,
                  font=settings.sub_font)
        write_srt(subtitle_lines, out_path.with_suffix(".srt"))

    if progress:
        progress(0.1, f"rendering {out_path.name}")

    inputs = ["-ss", f"{start:.3f}", "-i", str(source.resolve())]
    if not info.has_audio:
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}",
                   "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    graph = _video_graph(settings, subtitles=bool(want_subs))
    audio_index = "0:a" if info.has_audio else "1:a"
    graph += (
        f";[{audio_index}]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]"
    )

    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-t", f"{duration:.3f}",
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path.resolve()),
    ]

    # run inside the clip folder so the ass= filter gets a bare filename
    run(cmd, cwd=str(work), timeout=1800)

    if not out_path.exists() or out_path.stat().st_size < 10_000:
        raise MediaError(f"render produced an empty file: {out_path.name}")

    thumb = out_path.with_suffix(".jpg")
    try:
        run(
            [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{min(1.0, duration / 3):.2f}", "-i", str(out_path.resolve()),
             "-frames:v", "1", "-q:v", "3", str(thumb.resolve())],
            cwd=str(work),
            timeout=120,
        )
    except MediaError:
        thumb = None  # a missing poster is not worth failing the clip

    if progress:
        progress(1.0, f"rendered {out_path.name}")

    return {
        "file": out_path.name,
        "path": str(out_path),
        "thumbnail": thumb.name if thumb else None,
        "srt": out_path.with_suffix(".srt").name if want_subs else None,
        "start": round(start, 2),
        "end": round(start + duration, 2),
        "duration": round(duration, 2),
        "size_bytes": out_path.stat().st_size,
        "captions": bool(want_subs),
    }


def safe_name(text: str, fallback: str, limit: int = 48) -> str:
    """Filesystem-safe slug from a clip title."""
    cleaned = "".join(
        ch if (ch.isalnum() or ch in " -_") else " " for ch in text
    ).strip()
    cleaned = "-".join(cleaned.split())
    cleaned = cleaned.strip("-_")
    if not cleaned:
        return fallback
    return cleaned[:limit].rstrip("-_") or fallback
