"""ClipForge — turn a YouTube stream into vertical highlight clips."""

from __future__ import annotations

__version__ = "1.0.0"

from .config import Settings, load_settings  # noqa: E402,F401

__all__ = ["Settings", "load_settings", "__version__"]
