"""Command line interface: python -m clipforge <url|file> [options]."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import load_settings
from .pipeline import run


def _bar(fraction: float, message: str) -> None:
    width = 28
    filled = int(width * max(0.0, min(1.0, fraction)))
    bar = "#" * filled + "-" * (width - filled)
    sys.stderr.write(f"\r[{bar}] {fraction * 100:5.1f}%  {message[:52]:<52}")
    sys.stderr.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipforge",
        description="Cut the funny, interesting moments out of a YouTube stream.",
    )
    parser.add_argument("source", help="YouTube URL, any yt-dlp supported URL, or a local file")
    parser.add_argument("-n", "--clips", type=int, default=None, help="how many clips to make")
    parser.add_argument("--min", dest="min_clip", type=float, default=None,
                        help="shortest clip in seconds (default 18)")
    parser.add_argument("--max", dest="max_clip", type=float, default=None,
                        help="longest clip in seconds (default 55)")
    parser.add_argument("--horizontal", action="store_true",
                        help="keep the original 16:9 framing instead of vertical")
    parser.add_argument("--no-subs", action="store_true", help="do not burn in captions")
    parser.add_argument("--no-llm", action="store_true", help="skip the Ollama ranking pass")
    parser.add_argument("--model", default=None,
                        help="whisper model: tiny/base/small/medium/large-v3")
    parser.add_argument("--language", default=None, help="force a language, e.g. en or hi")
    parser.add_argument("-o", "--out", default=None, help="output folder")
    parser.add_argument("--job-id", default=None, help="reuse a previous job folder")
    parser.add_argument("--json", action="store_true", help="print the manifest as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()

    overrides: dict = {}
    if args.clips is not None:
        overrides["max_clips"] = max(1, args.clips)
    if args.min_clip is not None:
        overrides["min_clip"] = args.min_clip
    if args.max_clip is not None:
        overrides["max_clip"] = args.max_clip
    if args.horizontal:
        overrides["vertical"] = False
    if args.no_subs:
        overrides["subtitles"] = False
    if args.no_llm:
        overrides["use_llm"] = False
    if args.model:
        overrides["whisper_model"] = args.model
    if args.language:
        overrides["language"] = args.language
    if args.out:
        overrides["output_dir"] = Path(args.out)

    try:
        settings = settings.with_overrides(**overrides)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    job_id = args.job_id or f"cli-{int(time.time())}"
    try:
        manifest = run(args.source, settings, job_id=job_id, progress=_bar)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI reports, does not traceback
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    sys.stderr.write("\n")
    if args.json:
        import json

        print(json.dumps(manifest, indent=2))
        return 0

    out_dir = settings.output_dir / job_id
    print(f"\n{manifest['clip_count']} clips written to {out_dir}")
    for clip in manifest["clips"]:
        print(f"  {clip['rank']:>2}. {clip['file']}")
        print(f"      {clip['title']}")
        print(f"      {clip['duration']:.0f}s from {clip['start']:.0f}s  "
              f"(score {clip['score']:.2f}, {clip['source']})")
    for warning in manifest.get("warnings", []):
        print(f"\nnote: {warning}")
    print(f"\ntook {manifest['elapsed_seconds']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
