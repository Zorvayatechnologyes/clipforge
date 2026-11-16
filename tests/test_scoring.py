"""Unit tests for the ranking, subtitle and naming logic. No ffmpeg needed."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipforge import highlight, subtitles  # noqa: E402
from clipforge.audio import AudioTrack, window_features  # noqa: E402
from clipforge.render import safe_name  # noqa: E402
from clipforge.transcribe import Segment, Word  # noqa: E402


def make_segments(texts: list[str], step: float = 3.0) -> list[Segment]:
    segments = []
    for index, text in enumerate(texts):
        start = index * step
        words = text.split()
        span = step / max(len(words), 1)
        segments.append(
            Segment(
                start=start,
                end=start + step,
                text=text,
                words=[
                    Word(start=start + i * span, end=start + (i + 1) * span, text=w)
                    for i, w in enumerate(words)
                ],
            )
        )
    return segments


# ------------------------------------------------------------------ keywords
def test_cue_words_beat_neutral_text():
    hype = highlight.keyword_score("no way hahaha clip that bro what", 7)
    neutral = highlight.keyword_score("we will resume the schedule shortly", 7)
    assert hype > 0.5
    assert neutral == 0.0
    assert hype > neutral


def test_keyword_score_is_bounded():
    screaming = " ".join(["hahaha clip that no way"] * 40)
    assert 0.0 <= highlight.keyword_score(screaming, 160) <= 1.0


def test_keyword_score_handles_empty():
    assert highlight.keyword_score("", 0) == 0.0
    assert highlight.keyword_score("   ", 3) == 0.0


# ----------------------------------------------------------------- candidates
def test_candidates_cover_the_whole_video():
    cands = highlight.build_candidates(120.0, 18.0, 40.0, 3.0)
    assert cands
    assert min(c.start for c in cands) == 0.0
    assert max(c.end for c in cands) <= 120.0 + 1e-6
    assert all(c.duration > 0 for c in cands)


def test_candidates_empty_for_short_video():
    assert highlight.build_candidates(4.0, 18.0, 40.0, 3.0) == []


def test_longest_window_is_capped_by_duration():
    cands = highlight.build_candidates(30.0, 18.0, 55.0, 3.0)
    assert cands
    assert all(c.end <= 30.0 + 1e-6 for c in cands)


# ---------------------------------------------------------------- suppression
def test_suppress_removes_overlapping_neighbours():
    cands = [
        highlight.Candidate(0.0, 30.0, score=0.9),
        highlight.Candidate(2.0, 32.0, score=0.8),   # heavy overlap -> dropped
        highlight.Candidate(100.0, 130.0, score=0.5),
    ]
    picked = highlight.suppress(cands, limit=5, max_overlap=0.3)
    assert len(picked) == 2
    assert picked[0].score == 0.9


def test_suppress_respects_limit():
    cands = [highlight.Candidate(i * 100.0, i * 100.0 + 20.0, score=1 - i / 10)
             for i in range(10)]
    assert len(highlight.suppress(cands, limit=3)) == 3


def test_speech_score_rewards_normal_talking():
    assert highlight.speech_score(60, 20.0) > 0.8     # 3 words/sec
    assert highlight.speech_score(2, 20.0) == 0.0     # near silence
    assert highlight.speech_score(0, 20.0) == 0.0


# -------------------------------------------------------------------- audio
def flat_track(value: float = 0.2, seconds: float = 60.0) -> AudioTrack:
    t = np.arange(0, seconds, 0.025)
    rms = np.full_like(t, value)
    return AudioTrack(t=t, rms=rms, flux=np.zeros_like(t), peaks=np.zeros_like(t),
                      duration=seconds)


def test_window_features_are_normalised():
    track = flat_track()
    features = window_features(track, 5.0, 25.0)
    assert set(features) == {"energy", "dynamics", "bursts", "flux", "coverage"}
    assert all(0.0 <= v <= 1.0 for v in features.values())


def test_loud_bursty_window_scores_higher_than_flat_one():
    t = np.arange(0, 60, 0.025)
    quiet = np.full_like(t, 0.1)
    loud = quiet.copy()
    loud[(t > 30) & (t < 50)] = 0.9
    peaks = np.zeros_like(t)
    loud_idx = np.where((t > 30) & (t < 50))[0][::20]
    peaks[loud_idx] = 1.0
    track = AudioTrack(t=t, rms=loud, flux=np.zeros_like(t), peaks=peaks, duration=60)

    calm = window_features(track, 2.0, 20.0)
    wild = window_features(track, 31.0, 49.0)
    assert wild["energy"] > calm["energy"]
    assert wild["bursts"] > calm["bursts"]


def test_empty_window_does_not_crash():
    features = window_features(flat_track(), 500.0, 520.0)
    assert features["energy"] == 0.0


# ------------------------------------------------------------------- snapping
def test_snap_moves_boundaries_onto_words():
    segments = make_segments(["one two three four", "five six seven eight"])
    starts, ends = highlight._word_edges(segments)
    cand = highlight.Candidate(0.4, 5.6)
    highlight.snap_candidate(cand, starts, ends, duration=6.0, min_len=2.0, max_len=20.0)
    # both edges must land exactly on a word boundary, near where they were
    assert cand.start in set(starts.tolist())
    assert cand.end in set(ends.tolist())
    assert abs(cand.start - 0.4) <= 3.5
    assert abs(cand.end - 5.6) <= 3.5


def test_snap_enforces_minimum_length():
    segments = make_segments(["a b c d e f"])
    starts, ends = highlight._word_edges(segments)
    cand = highlight.Candidate(0.0, 0.9)
    highlight.snap_candidate(cand, starts, ends, duration=3.0, min_len=2.5, max_len=20.0)
    assert cand.duration >= 2.5 - 1e-6


# ---------------------------------------------------------------- captions
def test_group_words_splits_into_short_lines():
    segments = make_segments(["this is a much longer sentence than four words long"])
    words = [w for s in segments for w in s.words]
    lines = subtitles.group_words(words, 0.0, 3.0)
    assert lines
    assert all(len(line.text.split()) <= subtitles.MAX_WORDS for line in lines)
    assert all(line.end > line.start for line in lines)


def test_group_words_ignores_words_outside_the_clip():
    segments = make_segments(["alpha beta", "gamma delta"])
    words = [w for s in segments for w in s.words]
    assert subtitles.group_words(words, 3.0, 6.0)
    assert subtitles.group_words(words, 50.0, 60.0) == []


def test_caption_timings_do_not_overlap():
    segments = make_segments(["one two three four five six seven eight nine ten"] * 3)
    words = [w for s in segments for w in s.words]
    lines = subtitles.group_words(words, 0.0, 9.0)
    for first, second in zip(lines, lines[1:]):
        assert first.end <= second.start + 1e-6


def test_write_ass_escapes_and_keeps_style(tmp_path):
    segments = make_segments(["hello {world} there"])
    words = [w for s in segments for w in s.words]
    lines = subtitles.group_words(words, 0.0, 3.0)
    path = subtitles.write_ass(lines, tmp_path / "subs.ass", width=1080, height=1920)
    text = path.read_text(encoding="utf-8")
    assert "[V4+ Styles]" in text
    assert "PlayResY: 1920" in text
    assert "{" not in text.split("[Events]")[1]          # braces escaped
    assert text.count("Dialogue:") == len(lines)


def test_write_srt(tmp_path):
    segments = make_segments(["hello there"])
    words = [w for s in segments for w in s.words]
    lines = subtitles.group_words(words, 0.0, 3.0)
    path = subtitles.write_srt(lines, tmp_path / "subs.srt")
    text = path.read_text(encoding="utf-8")
    assert "-->" in text
    assert text.strip().startswith("1")


# ------------------------------------------------------------------- naming
@pytest.mark.parametrize(
    "title,expected_ok",
    [
        ("no way hahaha", True),
        ("", False),
        ("///", False),
        ("clip: that! now?", True),
    ],
)
def test_safe_name(title, expected_ok):
    name = safe_name(title, "highlight")
    assert name
    assert all(ch.isalnum() or ch in "-_" for ch in name)
    assert (name != "highlight") or not expected_ok


def test_llm_json_extraction_handles_fenced_and_noisy_output():
    fenced = '```json\n{"clips": [{"start_seconds": 1, "end_seconds": 20}]}\n```'
    assert highlight._extract_json(fenced)["clips"][0]["start_seconds"] == 1
    noisy = 'Sure! Here you go: {"clips": []} hope that helps'
    assert highlight._extract_json(noisy) == {"clips": []}
    assert highlight._extract_json("not json at all") is None


def test_apply_llm_rewards_matching_windows():
    cands = [
        highlight.Candidate(100.0, 130.0, score=0.4),
        highlight.Candidate(300.0, 330.0, score=0.4),
    ]
    moments = [{"start": 102.0, "end": 128.0, "title": "the bit", "reason": "funny",
                "score": 9.0}]
    highlight.apply_llm(cands, moments)
    assert cands[0].score > cands[1].score
    assert cands[0].title == "the bit"
    assert cands[1].parts.get("llm", 0.0) == 0.0
