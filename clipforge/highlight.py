"""Finding the funny / interesting moments.

Two layers:

1. A heuristic ranker that needs no model at all -- audio energy, dynamics,
   burst rate, onset flux, plus cue words in the transcript ("no way", "clip
   that", "hahaha"). Fast, runs on anything, surprisingly decent.
2. An optional local LLM pass through Ollama. Only the most promising stretch
   of the transcript is sent, so a 3-hour stream costs a couple of minutes
   instead of an hour. When Ollama is not running we simply skip this layer.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .audio import AudioTrack, window_features
from .config import Settings
from .transcribe import Segment

Progress = Callable[[float, str], None] | None

# --------------------------------------------------------------------------
# Cue words. Weight 1.0 = mild signal, 3.0 = someone shouting "CLIP THAT".
# --------------------------------------------------------------------------
KEYWORDS: dict[str, float] = {
    # laughter written into the transcript
    "hahaha": 2.2, "hahah": 2.0, "haha": 1.6, "lol": 1.6, "lmao": 2.0,
    "hehe": 1.2, "hah": 1.0, "laughing": 1.8, "laugh": 1.2,
    # the streamer or chat flagging it themselves
    "clip that": 3.0, "clip it": 2.8, "clip this": 2.8, "clip that shit": 3.0,
    "chat": 1.0, "chat is": 1.4, "in the chat": 1.2, "look at chat": 1.6,
    # shock / disbelief
    "no way": 2.4, "what the": 1.8, "oh my god": 2.2, "omg": 2.0,
    "are you serious": 2.2, "you're kidding": 2.0, "bruh": 2.2, "bro what": 2.2,
    "what happened": 1.6, "what is happening": 1.8, "wait what": 2.0,
    "no shot": 2.2, "impossible": 1.8, "unbelievable": 2.0, "insane": 1.8,
    "crazy": 1.6, "holy": 1.6, "actually insane": 2.2,
    # hype
    "let's go": 2.0, "lets go": 2.0, "yooo": 1.8, "yoo": 1.4, "oh no": 1.8,
    "rip": 1.4, "we did it": 1.8, "clutch": 2.0, "ace": 1.2, "one hp": 2.0,
    # narrative hooks
    "watch this": 1.8, "look at this": 1.6, "one more": 1.2, "last try": 1.4,
    "final": 1.0, "i can't believe": 2.0, "i cant believe": 2.0,
    "i'm dead": 2.2, "im dead": 2.2, "i'm crying": 2.0, "im crying": 2.0,
    # story beats that usually pay off
    "funny": 1.4, "joke": 1.2, "story": 1.0, "so basically": 1.2,
    "and then": 0.8, "turns out": 1.2, "plot twist": 2.0,
}

_WEIGHTS = {
    "keywords": 0.28,
    "energy": 0.16,
    "dynamics": 0.12,
    "bursts": 0.18,
    "flux": 0.08,
    "speech": 0.10,
    "coverage": 0.08,
}

MAX_PROMPT_CHARS = 14000
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Candidate:
    start: float
    end: float
    parts: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    title: str = ""
    reason: str = ""
    source: str = "heuristic"
    rank: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "score": round(self.score, 4),
            "title": self.title,
            "reason": self.reason,
            "source": self.source,
            "rank": self.rank,
            "parts": {k: round(v, 4) for k, v in self.parts.items()},
        }


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------
def build_candidates(duration: float, min_len: float, max_len: float,
                     step: float) -> list[Candidate]:
    """Sliding windows at three lengths -- short, medium, full."""
    if duration < 6:
        return []
    lengths = sorted({round(min_len, 2), round((min_len + max_len) / 2, 2),
                      round(max_len, 2)})
    step = max(1.0, step)
    out: list[Candidate] = []
    for length in lengths:
        if length > duration:
            length = round(duration, 2)
        t = 0.0
        while t + length <= duration + 1e-6:
            out.append(Candidate(start=round(t, 2), end=round(t + length, 2)))
            t += step
    return out


def _segments_in(segments: list[Segment], start: float, end: float) -> list[Segment]:
    return [s for s in segments if s.end > start and s.start < end]


def text_for_window(segments: list[Segment], start: float, end: float) -> tuple[str, int]:
    parts = []
    words = 0
    for seg in _segments_in(segments, start, end):
        parts.append(seg.text)
        words += max(1, len(seg.text.split()))
    return " ".join(parts).strip(), words


def keyword_score(text: str, word_count: int) -> float:
    """Weighted cue-word hits per 100 words, saturating at 6."""
    if word_count <= 0 or not text:
        return 0.0
    lowered = " " + re.sub(r"\s+", " ", text.lower()) + " "
    hits = 0.0
    for phrase, weight in KEYWORDS.items():
        count = lowered.count(f" {phrase}")
        if not count:
            count = lowered.count(phrase)
        if count:
            hits += count * weight
    per_hundred = hits * 100.0 / max(word_count, 1)
    return float(min(1.0, per_hundred / 6.0))


def speech_score(words: int, duration: float) -> float:
    """~3 words/second is lively talking; near-silence scores badly."""
    if duration <= 0:
        return 0.0
    rate = words / duration
    if rate < 0.6:
        return 0.0
    return float(np.clip(1.0 - abs(rate - 3.0) / 3.0, 0.0, 1.0))


def score_heuristic(cand: Candidate, segments: list[Segment], track: AudioTrack) -> None:
    features = window_features(track, cand.start, cand.end)
    text, words = text_for_window(segments, cand.start, cand.end)
    parts = dict(features)
    parts["keywords"] = keyword_score(text, words)
    parts["speech"] = speech_score(words, cand.duration)
    cand.parts = parts
    cand.score = float(sum(_WEIGHTS[k] * parts.get(k, 0.0) for k in _WEIGHTS))


# --------------------------------------------------------------------------
# Overlap, snapping, naming
# --------------------------------------------------------------------------
def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    inter = min(a_end, b_end) - max(a_start, b_start)
    if inter <= 0:
        return 0.0
    shortest = max(min(a_end - a_start, b_end - b_start), 1e-6)
    return inter / shortest


def suppress(cands: list[Candidate], limit: int, max_overlap: float = 0.3) -> list[Candidate]:
    """Greedy non-maximum suppression: best first, no near-duplicates."""
    chosen: list[Candidate] = []
    for cand in sorted(cands, key=lambda c: c.score, reverse=True):
        if any(_overlap(cand.start, cand.end, c.start, c.end) > max_overlap for c in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= limit:
            break
    return chosen


def _word_edges(segments: list[Segment]) -> tuple[np.ndarray, np.ndarray]:
    starts, ends = [], []
    for seg in segments:
        for word in seg.words:
            starts.append(word.start)
            ends.append(word.end)
    if not starts:
        return np.empty(0), np.empty(0)
    return np.array(starts, dtype=np.float64), np.array(ends, dtype=np.float64)


def _snap(value: float, edges: np.ndarray, tolerance: float) -> float | None:
    if edges.size == 0:
        return None
    idx = int(np.searchsorted(edges, value))
    options = [edges[i] for i in (idx - 1, idx) if 0 <= i < edges.size]
    if not options:
        return None
    best = min(options, key=lambda e: abs(e - value))
    return float(best) if abs(best - value) <= tolerance else None


def snap_candidate(cand: Candidate, starts: np.ndarray, ends: np.ndarray,
                   duration: float, min_len: float, max_len: float) -> None:
    """Move cut points onto word boundaries so speech is not sliced in half."""
    new_start = _snap(cand.start, starts, 3.5)
    new_end = _snap(cand.end, ends, 3.5)
    if new_start is not None:
        cand.start = new_start
    if new_end is not None:
        cand.end = new_end
    if cand.end - cand.start < min_len:
        cand.end = min(cand.start + min_len, duration)
        if cand.end - cand.start < min_len:
            cand.start = max(0.0, cand.end - min_len)
    if cand.end - cand.start > max_len:
        cand.end = cand.start + max_len
    cand.start = max(0.0, round(cand.start, 2))
    cand.end = min(duration, round(cand.end, 2))


def _collapse_repeats(words: list[str]) -> list[str]:
    """'no way no way no way' -> 'no way'. Keeps fallback titles readable."""
    for size in (4, 3, 2, 1):
        out: list[str] = []
        index = 0
        while index < len(words):
            if len(out) >= size and out[-size:] == words[index:index + size]:
                index += size
                continue
            out.append(words[index])
            index += 1
        words = out
    return words


def make_title(segments: list[Segment], cand: Candidate) -> str:
    text, _ = text_for_window(segments, cand.start, cand.end)
    if not text:
        return f"Clip at {int(cand.start // 60):02d}:{int(cand.start % 60):02d}"
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        sentences = [text]
    best = max(sentences, key=lambda s: keyword_score(s, max(1, len(s.split()))))
    words = _collapse_repeats(re.sub(r"\s+", " ", best).strip(" .,-").split())
    if not words:
        return f"Clip at {int(cand.start // 60):02d}:{int(cand.start % 60):02d}"
    trimmed = " ".join(words[:9]).strip(" .,-")
    if len(trimmed) > 68:
        trimmed = trimmed[:65].rsplit(" ", 1)[0] + "..."
    return trimmed or "Highlight"


# --------------------------------------------------------------------------
# Local LLM pass (Ollama)
# --------------------------------------------------------------------------
def ollama_available(settings: Settings) -> bool:
    if not settings.use_llm:
        return False
    try:
        with urllib.request.urlopen(f"{settings.ollama_url}/api/tags", timeout=4) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _stamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _transcript_block(segments: list[Segment], start: float, end: float) -> str:
    lines = []
    for seg in _segments_in(segments, start, end):
        lines.append(f"[{_stamp(seg.start)}] {seg.text}")
    return "\n".join(lines)


def _regions(cands: list[Candidate], duration: float, limit: int = 8,
             pad: float = 25.0, cap_seconds: float = 1500.0) -> list[tuple[float, float]]:
    """Pick the most promising stretches to show the model, merged and capped."""
    seeds = suppress(cands, limit=limit, max_overlap=0.4)
    if not seeds:
        return []
    raw = sorted((max(0.0, c.start - pad), min(duration, c.end + pad)) for c in seeds)
    merged: list[list[float]] = []
    for start, end in raw:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out: list[tuple[float, float]] = []
    used = 0.0
    for start, end in merged:
        span = end - start
        if used + span > cap_seconds:
            span = cap_seconds - used
            if span < 40:
                break
            end = start + span
        out.append((start, end))
        used += span
        if used >= cap_seconds:
            break
    return out

_PROMPT = """You are a short-form video editor for a livestream channel. \
Below is a transcript of part of a stream, with [MM:SS] timestamps.

Find the moments that would make the best standalone vertical clips: funny, \
surprising, heated, wholesome, or genuinely interesting. A clip must make sense \
on its own without earlier context, and must be between {min_len} and {max_len} \
seconds long. Prefer a moment with a clear payoff over a slow build-up.

Reply with JSON only, exactly this shape:
{{"clips": [{{"start_seconds": 123.0, "end_seconds": 155.0, "title": "short punchy title, max 8 words", "reason": "one sentence on why it works", "score": 8.5}}]}}

"score" is 0-10 for how strong the moment is. Return at most {want} clips. \
If nothing is worth clipping, return {{"clips": []}}.

Transcript:
{transcript}
"""


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"clips": data}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {"clips": data}
        except json.JSONDecodeError:
            return None
    return None


def _ask_ollama(prompt: str, settings: Settings) -> dict | None:
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1200},
    }
    request = urllib.request.Request(
        f"{settings.ollama_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.llm_timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return _extract_json(str(body.get("response", "")))


def llm_moments(segments: list[Segment], candidates: list[Candidate],
                duration: float, settings: Settings,
                progress: Progress = None) -> list[dict]:
    """Ask the local model for clip-worthy moments. Empty list on any failure."""
    regions = _regions(candidates, duration)
    if not regions:
        return []

    blocks: list[str] = []
    for start, end in regions:
        block = _transcript_block(segments, start, end)
        if block:
            blocks.append(block)

    # pack blocks into as few calls as the context budget allows
    batches: list[str] = []
    current = ""
    for block in blocks:
        candidate_text = f"{current}\n\n---\n\n{block}" if current else block
        if len(candidate_text) > MAX_PROMPT_CHARS and current:
            batches.append(current)
            current = block
        else:
            current = candidate_text
    if current:
        batches.append(current)

    moments: list[dict] = []
    for index, batch in enumerate(batches, start=1):
        if progress:
            progress(0.15 + 0.7 * index / max(len(batches), 1),
                     f"asking {settings.ollama_model} ({index}/{len(batches)})")
        prompt = _PROMPT.format(
            min_len=int(settings.min_clip),
            max_len=int(settings.max_clip),
            want=max(3, settings.max_clips),
            transcript=batch,
        )
        data = _ask_ollama(prompt, settings)
        if not data:
            continue
        for raw in data.get("clips") or []:
            if not isinstance(raw, dict):
                continue
            try:
                start = float(raw.get("start_seconds"))
                end = float(raw.get("end_seconds"))
                score = float(raw.get("score", 7.0))
            except (TypeError, ValueError):
                continue
            start = max(0.0, min(start, duration))
            end = max(0.0, min(end, duration))
            if end - start < 5:
                continue
            if end - start > settings.max_clip + 10:
                end = start + settings.max_clip
            moments.append(
                {
                    "start": start,
                    "end": end,
                    "title": str(raw.get("title") or "").strip()[:90],
                    "reason": str(raw.get("reason") or "").strip()[:200],
                    "score": max(0.0, min(10.0, score)),
                }
            )

    # drop duplicates the model may have returned across batches
    unique: list[dict] = []
    for moment in sorted(moments, key=lambda m: m["score"], reverse=True):
        if any(_overlap(moment["start"], moment["end"], u["start"], u["end"]) > 0.5
               for u in unique):
            continue
        unique.append(moment)
    return unique


def apply_llm(cands: list[Candidate], moments: list[dict]) -> None:
    for cand in cands:
        best, best_title, best_reason = 0.0, "", ""
        for moment in moments:
            overlap = _overlap(cand.start, cand.end, moment["start"], moment["end"])
            if overlap < 0.2:
                continue
            value = (moment["score"] / 10.0) * min(1.0, overlap / 0.6)
            if value > best:
                best, best_title, best_reason = value, moment["title"], moment["reason"]
        cand.parts["llm"] = round(best, 4)
        if best > 0:
            cand.score = 0.65 * best + 0.35 * cand.score
            cand.source = "llm+audio"
            if best_title:
                cand.title = best_title
            if best_reason:
                cand.reason = best_reason


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def select_clips(
    segments: list[Segment],
    track: AudioTrack,
    duration: float,
    settings: Settings,
    progress: Progress = None,
) -> list[Candidate]:
    """Return the best clips, best first, with boundaries snapped to speech."""
    if progress:
        progress(0.05, "scoring candidate windows")

    cands = build_candidates(duration, settings.min_clip, settings.max_clip,
                             settings.window_step)
    if not cands:
        return []

    for index, cand in enumerate(cands):
        score_heuristic(cand, segments, track)
        if progress and index % 200 == 0:
            progress(0.05 + 0.35 * index / len(cands), "scoring candidate windows")

    if progress:
        progress(0.45, "scoring candidate windows")

    if ollama_available(settings):
        moments = llm_moments(segments, cands, duration, settings, progress)
        if moments:
            # let the model's own windows compete with the sliding ones
            for moment in moments:
                extra = Candidate(start=moment["start"], end=moment["end"],
                                  source="llm")
                score_heuristic(extra, segments, track)
                extra.parts["llm"] = round(moment["score"] / 10.0, 4)
                extra.score = 0.65 * (moment["score"] / 10.0) + 0.35 * extra.score
                if moment["title"]:
                    extra.title = moment["title"]
                extra.reason = moment["reason"]
                cands.append(extra)
            apply_llm(cands, moments)

    if progress:
        progress(0.85, "picking the best moments")

    picked = suppress(cands, limit=max(1, settings.max_clips), max_overlap=0.3)
    starts, ends = _word_edges(segments)
    for cand in picked:
        snap_candidate(cand, starts, ends, duration, settings.min_clip,
                       settings.max_clip)
        if not cand.title:
            cand.title = make_title(segments, cand)
        if not cand.reason:
            top = sorted(cand.parts.items(), key=lambda kv: kv[1], reverse=True)[:2]
            cand.reason = "strong " + " and ".join(name for name, _ in top)

    picked.sort(key=lambda c: c.score, reverse=True)
    for rank, cand in enumerate(picked, start=1):
        cand.rank = rank

    if progress:
        progress(1.0, f"selected {len(picked)} clips")
    return picked
