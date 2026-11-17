"""Download option plumbing. No network, no yt-dlp install required."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipforge import download  # noqa: E402

URL = "https://youtu.be/abc123"


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL and records the options it was given."""

    opts: dict = {}
    video: Path | None = None

    def __init__(self, options):
        FakeYoutubeDL.opts = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        return {"id": "abc123", "_type": "video", "title": "a video", "ext": "mp4"}

    def prepare_filename(self, info):
        return str(FakeYoutubeDL.video)


@pytest.fixture()
def fake_ytdlp(tmp_path, monkeypatch):
    video = tmp_path / "abc123.mp4"
    video.write_bytes(b"0" * 200_000)
    FakeYoutubeDL.video = video

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    return video


def test_cookies_file_is_handed_to_yt_dlp(tmp_path, fake_ytdlp):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n")

    path = download.fetch(
        URL, tmp_path,
        cookies_file=str(cookies),
    )
    assert path == fake_ytdlp
    assert FakeYoutubeDL.opts["cookiefile"] == str(cookies)


def test_missing_cookies_file_fails_loudly(tmp_path, fake_ytdlp):
    with pytest.raises(download.DownloadError, match="cookies file not found"):
        download.fetch(
            URL, tmp_path,
            cookies_file=str(tmp_path / "nope.txt"),
        )


def test_browser_cookies_are_handed_to_yt_dlp(tmp_path, fake_ytdlp):
    download.fetch(URL, tmp_path,
                   cookies_from_browser="chrome")
    assert FakeYoutubeDL.opts["cookiesfrombrowser"] == ("chrome",)
    assert "cookiefile" not in FakeYoutubeDL.opts


def test_no_cookies_means_no_cookie_options(tmp_path, fake_ytdlp):
    download.fetch(URL, tmp_path)
    assert "cookiefile" not in FakeYoutubeDL.opts
    assert "cookiesfrombrowser" not in FakeYoutubeDL.opts


def test_format_selection_prefers_a_capped_merge(tmp_path, fake_ytdlp):
    download.fetch(URL, tmp_path, max_height=720)
    assert "720" in FakeYoutubeDL.opts["format"]
    assert FakeYoutubeDL.opts["merge_output_format"] == "mp4"
