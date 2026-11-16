"""Speech-to-text with faster-whisper (runs locally, no API key)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

Progress = Callable[[float, str], None] | None

_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


class TranscriptionError(RuntimeError):
    """Whisper could not be loaded or failed to transcribe."""


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


def _load_model(name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise TranscriptionError(
            "faster-whisper is not installed. Run: pip install -r requirements.txt"
        ) from exc

    key = (name, device, compute_type)
    if key not in _MODEL_CACHE:
        try:
            _MODEL_CACHE[key] = WhisperModel(
                name, device=device, compute_type=compute_type
            )
        except Exception as exc:  # bad compute type, no GPU, etc.
            if compute_type != "int8" or device != "auto":
                # retry once with the most portable settings
                try:
                    _MODEL_CACHE[key] = WhisperModel(name, device="cpu", compute_type="int8")
                    return _MODEL_CACHE[key]
                except Exception:
                    pass
            raise TranscriptionError(f"could not load whisper model '{name}': {exc}") from exc
    return _MODEL_CACHE[key]


def transcribe(
    audio_path: str | Path,
    *,
    model_name: str = "small",
    device: str = "auto",
    compute_type: str = "int8",
    language: str | None = None,
    progress: Progress = None,
) -> list[Segment]:
    """Transcribe an audio file, returning segments with word-level timings."""
    if progress:
        progress(0.02, f"loading whisper model '{model_name}'")

    model = _load_model(model_name, device, compute_type)
    if progress:
        progress(0.15, "transcribing audio")

    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=True,
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4],
        )
    except Exception as exc:
        raise TranscriptionError(f"transcription failed: {exc}") from exc

    total = float(getattr(info, "duration", 0.0) or 0.0)
    out: list[Segment] = []
    for seg in segments_iter:
        words = [
            Word(
                start=float(w.start),
                end=float(w.end),
                text=str(w.word).strip(),
                probability=float(getattr(w, "probability", 1.0) or 1.0),
            )
            for w in (seg.words or [])
            if str(w.word).strip()
        ]
        text = str(seg.text).strip()
        if not text:
            continue
        out.append(
            Segment(
                start=float(seg.start),
                end=float(seg.end),
                text=text,
                words=words,
            )
        )
        if progress and total > 0:
            progress(0.15 + 0.85 * min(1.0, float(seg.end) / total), "transcribing audio")

    if progress:
        progress(1.0, f"transcribed {len(out)} segments")
    return out


def to_dict(segments: list[Segment]) -> list[dict]:
    return [
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
            "words": [asdict(w) for w in s.words],
        }
        for s in segments
    ]


def from_dict(items: list[dict]) -> list[Segment]:
    return [
        Segment(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
            words=[Word(**w) for w in item.get("words", [])],
        )
        for item in items
    ]


def full_text(segments: list[Segment]) -> str:
    return " ".join(s.text for s in segments).strip()
