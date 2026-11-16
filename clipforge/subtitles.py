"""Turning word timings into burned-in captions (ASS + SRT)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .transcribe import Word

MAX_WORDS = 4
MAX_CHARS = 24
MIN_SHOW = 0.55
MAX_SHOW = 4.5
BREAK_CHARS = ".!?,:;"


@dataclass
class CaptionLine:
    start: float
    end: float
    text: str


def _clean(text: str) -> str:
    text = text.replace("\n", " ").strip()
    for bad, good in (("{", "("), ("}", ")"), ("\\", "/")):
        text = text.replace(bad, good)
    return text


def group_words(words: list[Word], clip_start: float, clip_end: float) -> list[CaptionLine]:
    """Split words inside the clip into short, readable caption lines."""
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    if not inside:
        return []

    lines: list[CaptionLine] = []
    current: list[Word] = []

    def flush() -> None:
        if not current:
            return
        text = _clean(" ".join(w.text for w in current))
        if text:
            lines.append(
                CaptionLine(
                    start=max(0.0, current[0].start - clip_start),
                    end=min(clip_end - clip_start, current[-1].end - clip_start),
                    text=text,
                )
            )
        current.clear()

    for word in inside:
        current.append(word)
        joined = " ".join(w.text for w in current)
        ends_sentence = bool(word.text) and word.text[-1] in BREAK_CHARS
        if len(current) >= MAX_WORDS or len(joined) >= MAX_CHARS or ends_sentence:
            flush()
    flush()

    # clamp timings: no zero-length cards, no overlap with the next line
    for index, line in enumerate(lines):
        if line.end - line.start < MIN_SHOW:
            line.end = line.start + MIN_SHOW
        if line.end - line.start > MAX_SHOW:
            line.end = line.start + MAX_SHOW
        if index + 1 < len(lines) and line.end > lines[index + 1].start:
            line.end = max(line.start + 0.2, lines[index + 1].start)
    if lines:
        lines[-1].end = min(lines[-1].end, clip_end - clip_start)
    return [line for line in lines if line.end > line.start and line.text]


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def write_ass(
    lines: list[CaptionLine],
    path: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
    font: str = "DejaVu Sans",
) -> Path:
    """Write a vertical-friendly ASS file with a bold, outlined caption style."""
    path = Path(path)
    font_size = max(28, int(round(height * 0.0375)))
    outline = max(3, int(round(font_size * 0.085)))
    margin_v = int(round(height * 0.30))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0.6,0,1,{outline},{max(1, outline // 2)},2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "".join(
        f"Dialogue: 0,{_ass_time(line.start)},{_ass_time(line.end)},Cap,,0,0,0,,{line.text}\n"
        for line in lines
    )
    path.write_text(header + body, encoding="utf-8")
    return path


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs, millis = secs + 1, 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(lines: list[CaptionLine], path: str | Path) -> Path:
    path = Path(path)
    chunks = []
    for index, line in enumerate(lines, start=1):
        chunks.append(
            f"{index}\n{_srt_time(line.start)} --> {_srt_time(line.end)}\n{line.text}\n"
        )
    path.write_text("\n".join(chunks), encoding="utf-8")
    return path
