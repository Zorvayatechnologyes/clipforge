"""Runtime configuration, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str | None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or None


@dataclass(frozen=True)
class Settings:
    """All knobs for one run. Immutable so a job can hold its own copy."""

    work_dir: Path
    output_dir: Path

    # --- transcription (faster-whisper) ---
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute: str = "int8"
    language: str | None = None

    # --- highlight selection ---
    use_llm: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    llm_timeout: float = 180.0

    # --- clip shaping ---
    min_clip: float = 18.0
    max_clip: float = 55.0
    max_clips: int = 6
    window_step: float = 3.0
    # spread clips across the whole video instead of clustering them
    # around the single loudest stretch
    spread: bool = True

    # --- output ---
    vertical: bool = True
    subtitles: bool = True
    sub_font: str = "DejaVu Sans"
    width: int = 1080
    height: int = 1920
    crf: int = 20
    preset: str = "veryfast"

    # --- download ---
    max_source_height: int = 1080
    cookies_from_browser: str | None = None
    # path to a Netscape cookies.txt. Required when the engine runs on a
    # cloud host, because YouTube refuses to serve datacenter IPs.
    cookies_file: str | None = None
    keep_source: bool = False

    # --- binaries ---
    ffmpeg_path: str | None = None

    # --- hosting ---
    # comma-separated origins allowed to call the API from a browser,
    # e.g. "https://clipforge.netlify.app". "*" allows any (no auth anyway).
    allowed_origins: str = "*"

    def with_overrides(self, **kwargs: object) -> "Settings":
        """Return a copy with only the non-None values replaced."""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        unknown = set(clean) - {f for f in self.__dataclass_fields__}
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        return replace(self, **clean)

    def ensure_dirs(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Build settings from CLIPFORGE_* environment variables."""
    settings = Settings(
        work_dir=Path(_env_str("CLIPFORGE_WORK", "data/work") or "data/work"),
        output_dir=Path(_env_str("CLIPFORGE_OUTPUT", "data/clips") or "data/clips"),
        whisper_model=_env_str("CLIPFORGE_WHISPER_MODEL", "small") or "small",
        whisper_device=_env_str("CLIPFORGE_WHISPER_DEVICE", "auto") or "auto",
        whisper_compute=_env_str("CLIPFORGE_WHISPER_COMPUTE", "int8") or "int8",
        language=_env_str("CLIPFORGE_LANGUAGE", None),
        use_llm=_env_bool("CLIPFORGE_USE_LLM", True),
        ollama_url=_env_str("CLIPFORGE_OLLAMA_URL", "http://127.0.0.1:11434")
        or "http://127.0.0.1:11434",
        ollama_model=_env_str("CLIPFORGE_OLLAMA_MODEL", "qwen2.5:7b-instruct")
        or "qwen2.5:7b-instruct",
        llm_timeout=_env_float("CLIPFORGE_LLM_TIMEOUT", 180.0),
        min_clip=_env_float("CLIPFORGE_MIN_CLIP", 18.0),
        max_clip=_env_float("CLIPFORGE_MAX_CLIP", 55.0),
        max_clips=_env_int("CLIPFORGE_MAX_CLIPS", 6),
        window_step=_env_float("CLIPFORGE_WINDOW_STEP", 3.0),
        spread=_env_bool("CLIPFORGE_SPREAD", True),
        vertical=_env_bool("CLIPFORGE_VERTICAL", True),
        subtitles=_env_bool("CLIPFORGE_SUBTITLES", True),
        sub_font=_env_str("CLIPFORGE_SUB_FONT", "DejaVu Sans") or "DejaVu Sans",
        width=_env_int("CLIPFORGE_WIDTH", 1080),
        height=_env_int("CLIPFORGE_HEIGHT", 1920),
        crf=_env_int("CLIPFORGE_CRF", 20),
        preset=_env_str("CLIPFORGE_PRESET", "veryfast") or "veryfast",
        max_source_height=_env_int("CLIPFORGE_MAX_SOURCE_HEIGHT", 1080),
        cookies_from_browser=_env_str("CLIPFORGE_COOKIES_FROM_BROWSER", None),
        cookies_file=_env_str("CLIPFORGE_COOKIES_FILE", None),
        keep_source=_env_bool("CLIPFORGE_KEEP_SOURCE", False),
        ffmpeg_path=_env_str("CLIPFORGE_FFMPEG", None),
        allowed_origins=_env_str("CLIPFORGE_ALLOWED_ORIGINS", "*") or "*",
    )
    if settings.min_clip < 5:
        raise ValueError("CLIPFORGE_MIN_CLIP must be at least 5 seconds")
    if settings.max_clip < settings.min_clip:
        raise ValueError("CLIPFORGE_MAX_CLIP must be >= CLIPFORGE_MIN_CLIP")
    return settings
