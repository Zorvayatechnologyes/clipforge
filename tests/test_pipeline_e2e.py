"""End-to-end render test with no network: a synthetic video plus a fake transcript.

Transcription is skipped by pre-seeding the transcript cache, which is exactly
what the pipeline does when you re-run a job. Everything after that -- audio
analysis, ranking, snapping, caption building, the vertical ffmpeg render -- is
the real code path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from clipforge import ffmpeg  # noqa: E402
from clipforge.config import load_settings  # noqa: E402
from clipforge.pipeline import run as run_pipeline  # noqa: E402
from clipforge.transcribe import Segment, Word, to_dict  # noqa: E402

from make_sample import DURATION, LOUD_REGIONS, build  # noqa: E402


def _has_ffmpeg() -> bool:
    try:
        ffmpeg.ffmpeg_exe()
        return True
    except ffmpeg.MediaError:
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not available")


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("sample") / "stream.mp4"
    return build(target, duration=DURATION)


def fake_transcript() -> list[Segment]:
    """Chatter everywhere, cue words only inside the loud stretches."""
    segments: list[Segment] = []
    step = 4.0
    index = 0
    while index * step < DURATION - step:
        start = index * step
        loud = any(lo <= start <= hi for lo, hi in LOUD_REGIONS)
        text = (
            "oh my god no way hahaha clip that bro what"
            if loud
            else "so anyway we were talking about the schedule for next week"
        )
        words = text.split()
        span = step / len(words)
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
        index += 1
    return segments


def test_pipeline_produces_vertical_clips(tmp_path, sample_video):
    settings = load_settings().with_overrides(
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "clips",
        use_llm=False,
        max_clips=2,
        min_clip=12.0,
        max_clip=25.0,
        window_step=3.0,
        vertical=True,
        subtitles=True,
        preset="ultrafast",
        crf=30,
        keep_source=True,
    )
    settings.ensure_dirs()

    job_id = "e2e"
    job_dir = settings.work_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "transcript.json").write_text(
        json.dumps(to_dict(fake_transcript())), encoding="utf-8"
    )

    manifest = run_pipeline(str(sample_video), settings, job_id=job_id)

    assert manifest["clip_count"] >= 1
    out_dir = settings.output_dir / job_id
    assert (out_dir / "clips.json").exists()

    for clip in manifest["clips"]:
        path = out_dir / clip["file"]
        assert path.exists(), clip["file"]
        assert path.stat().st_size > 50_000
        info = ffmpeg.probe(path)
        assert (info.width, info.height) == (1080, 1920), "should be vertical 9:16"
        assert info.has_audio
        assert 10 <= info.duration <= 30
        assert clip["start"] >= 0
        assert clip["title"]
        if clip["captions"]:
            assert (out_dir / clip["srt"]).exists()

    # the loud stretches are where the cue words and the energy are
    loud_mids = [sum(region) / 2 for region in LOUD_REGIONS]
    picked_mids = [(c["start"] + c["end"]) / 2 for c in manifest["clips"]]
    assert any(min(abs(mid - loud) for loud in loud_mids) < 30 for mid in picked_mids), (
        f"clips {picked_mids} ignored the loud regions {loud_mids}"
    )


def test_pipeline_rejects_a_short_video(tmp_path, sample_video):
    from clipforge import ffmpeg as media

    short = tmp_path / "short.mp4"
    media.run(
        [
            media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=200:duration=5",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            "-pix_fmt", "yuv420p", "-shortest", str(short),
        ]
    )
    settings = load_settings().with_overrides(
        work_dir=tmp_path / "work", output_dir=tmp_path / "clips", use_llm=False
    )
    settings.ensure_dirs()
    with pytest.raises(ValueError, match="too short"):
        run_pipeline(str(short), settings, job_id="short")


def test_horizontal_mode_keeps_sixteen_by_nine(tmp_path, sample_video):
    settings = load_settings().with_overrides(
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "clips",
        use_llm=False,
        max_clips=1,
        min_clip=10.0,
        max_clip=15.0,
        vertical=False,
        subtitles=False,
        preset="ultrafast",
        crf=32,
        keep_source=True,
    )
    settings.ensure_dirs()
    job_id = "wide"
    job_dir = settings.work_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "transcript.json").write_text(
        json.dumps(to_dict(fake_transcript())), encoding="utf-8"
    )

    manifest = run_pipeline(str(sample_video), settings, job_id=job_id)
    clip = manifest["clips"][0]
    info = ffmpeg.probe(settings.output_dir / job_id / clip["file"])
    assert (info.width, info.height) == (1920, 1080)
    assert clip["captions"] is False
