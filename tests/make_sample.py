"""Build a fake 4-minute 'stream' so the pipeline can be tested offline.

Three loud stretches (40-75s, 120-150s, 190-215s) stand in for the moments
where a streamer shouts or the room laughs, which is exactly what the audio
ranker looks for.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipforge.ffmpeg import ffmpeg_exe, run  # noqa: E402

LOUD_REGIONS = [(40, 75), (120, 150), (190, 215)]
DURATION = 240


def build(target: str | Path, duration: int = DURATION) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 100_000:
        return target

    gain = "0.10"
    for start, end in LOUD_REGIONS:
        gain += f"+0.80*between(t\\,{start}\\,{end})"

    graph = (
        f"[0:a]aeval=exprs='val(0)*({gain})':c=mono,"
        f"aformat=channel_layouts=stereo[a];"
        f"[1:v]drawtext=text='sample stream %{{pts\\:hms}}':"
        f"fontcolor=white:fontsize=36:x=40:y=40:box=1:boxcolor=black@0.5[v]"
    )

    run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=180:sample_rate=16000:duration={duration}",
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={duration}",
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            "-shortest", str(target),
        ],
        timeout=600,
    )
    return target


if __name__ == "__main__":
    out = build(Path(__file__).parent / "sample" / "stream.mp4")
    size = out.stat().st_size / 1e6
    print(f"wrote {out} ({size:.1f} MB)")
    print("try: python -m clipforge", out, "--no-llm -n 3")
