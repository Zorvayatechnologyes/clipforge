"""The end-to-end job: URL -> transcript -> ranked moments -> vertical clips."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import audio, download, ffmpeg, highlight, render, transcribe
from .config import Settings

Progress = Callable[[float, str], None] | None


def _scaled(progress: Progress, lo: float, hi: float):
    """Map a child's 0..1 progress into the [lo, hi] slice of the whole job."""

    def inner(fraction: float, message: str) -> None:
        if progress:
            span = hi - lo
            progress(lo + span * max(0.0, min(1.0, fraction)), message)

    return inner


def _write_manifest(out_dir: Path, payload: dict) -> None:
    (out_dir / "clips.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run(
    source: str,
    settings: Settings,
    *,
    job_id: str,
    progress: Progress = None,
    reuse_transcript: bool = True,
) -> dict:
    """Run the whole pipeline. Returns a manifest dict (also written to disk)."""
    started = time.time()
    settings.ensure_dirs()
    job_dir = settings.work_dir / job_id
    out_dir = settings.output_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []

    # ---------------- 1. get the video ----------------
    if progress:
        progress(0.01, "preparing")
    source_path = download.fetch(
        source,
        job_dir,
        max_height=settings.max_source_height,
        cookies_from_browser=settings.cookies_from_browser,
        progress=_scaled(progress, 0.01, 0.22),
    )
    info = ffmpeg.probe(source_path)
    if info.duration < 30:
        raise ValueError(
            f"that video is only {info.duration:.0f}s long — too short to find clips in"
        )
    if not info.has_audio:
        raise ValueError("the video has no audio track, so there is nothing to analyse")

    # ---------------- 2. transcribe ----------------
    cache = job_dir / "transcript.json"
    segments: list[transcribe.Segment] = []
    if reuse_transcript and cache.exists():
        try:
            segments = transcribe.from_dict(json.loads(cache.read_text(encoding="utf-8")))
            if progress:
                progress(0.55, f"reused cached transcript ({len(segments)} segments)")
        except (json.JSONDecodeError, KeyError, TypeError):
            segments = []

    if not segments:
        if progress:
            progress(0.23, "extracting audio")
        wav = ffmpeg.extract_audio(source_path, job_dir / "audio.wav")
        segments = transcribe.transcribe(
            wav,
            model_name=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute,
            language=settings.language,
            progress=_scaled(progress, 0.24, 0.58),
        )
        cache.write_text(
            json.dumps(transcribe.to_dict(segments), ensure_ascii=False), encoding="utf-8"
        )
        if not settings.keep_source:
            wav.unlink(missing_ok=True)

    if not segments:
        warnings.append(
            "No speech was detected — ranking used audio energy only. "
            "Clips will still be cut, but titles are less meaningful."
        )

    # ---------------- 3. audio signals ----------------
    if progress:
        progress(0.60, "analysing audio energy")
    wav = job_dir / "audio.wav"
    if not wav.exists():
        wav = ffmpeg.extract_audio(source_path, wav)
    try:
        track = audio.analyse(wav)
    except ValueError as exc:
        raise ValueError(f"could not analyse the audio track: {exc}") from exc
    if not settings.keep_source:
        wav.unlink(missing_ok=True)

    # ---------------- 4. pick the moments ----------------
    if progress:
        progress(0.70, "finding the funny bits")
    if not highlight.ollama_available(settings):
        warnings.append(
            "No local LLM reachable at "
            f"{settings.ollama_url} — ranked with audio + cue words only. "
            "Start Ollama for smarter picks."
        )
    candidates = highlight.select_clips(
        segments, track, info.duration, settings,
        progress=_scaled(progress, 0.70, 0.80),
    )
    if not candidates:
        raise ValueError("no clip-worthy moments were found in this video")

    # ---------------- 5. render ----------------
    clips: list[dict] = []
    words = [w for seg in segments for w in seg.words]
    for index, cand in enumerate(candidates, start=1):
        base = f"clip_{index:02d}_{render.safe_name(cand.title, 'highlight')}"
        lines = None
        if settings.subtitles:
            from .subtitles import group_words

            lines = group_words(words, cand.start, cand.end)
        try:
            result = render.render_clip(
                source_path, info, cand, out_dir / f"{base}.mp4", settings,
                segments=segments, subtitle_lines=lines,
                progress=_scaled(
                    progress,
                    0.80 + 0.19 * (index - 1) / len(candidates),
                    0.80 + 0.19 * index / len(candidates),
                ),
            )
        except ffmpeg.MediaError as exc:
            warnings.append(f"clip {index} failed to render: {exc}")
            continue
        result.update(
            {
                "rank": index,
                "title": cand.title,
                "reason": cand.reason,
                "score": round(cand.score, 4),
                "source": cand.source,
                "parts": {k: round(v, 3) for k, v in cand.parts.items()},
            }
        )
        clips.append(result)
        if progress:
            progress(0.80 + 0.19 * index / len(candidates),
                     f"rendered {index} of {len(candidates)}")

    if not clips:
        raise ValueError("every clip failed to render")

    manifest = {
        "job_id": job_id,
        "source": source,
        "source_duration": round(info.duration, 2),
        "source_resolution": f"{info.width}x{info.height}",
        "clip_count": len(clips),
        "clips": clips,
        "warnings": warnings,
        "settings": {
            "vertical": settings.vertical,
            "subtitles": settings.subtitles,
            "min_clip": settings.min_clip,
            "max_clip": settings.max_clip,
            "whisper_model": settings.whisper_model,
            "llm": settings.ollama_model if settings.use_llm else None,
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    _write_manifest(out_dir, manifest)

    download.cleanup_source(source_path, job_dir, keep=settings.keep_source)
    if progress:
        progress(1.0, f"done — {len(clips)} clips in {manifest['elapsed_seconds']:.0f}s")
    return manifest
