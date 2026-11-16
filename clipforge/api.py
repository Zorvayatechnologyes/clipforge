"""HTTP API + web UI."""

from __future__ import annotations

import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ffmpeg, highlight
from .config import Settings, load_settings
from .jobs import JobStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

settings: Settings = load_settings()
settings.ensure_dirs()
store = JobStore(settings)
store.load()

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    store.shutdown()


app = FastAPI(
    title="ClipForge",
    version="1.0.0",
    description="Turn a YouTube stream into vertical highlight clips.",
    lifespan=lifespan,
)


_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition"],
)


class ClipRequest(BaseModel):
    url: str = Field(..., min_length=3, description="YouTube URL or a local file path")
    max_clips: int | None = Field(None, ge=1, le=20)
    min_clip: float | None = Field(None, ge=5, le=120)
    max_clip: float | None = Field(None, ge=8, le=180)
    vertical: bool | None = None
    subtitles: bool | None = None
    use_llm: bool | None = None


@app.get("/api/health")
def health() -> dict:
    try:
        binary = ffmpeg.ffmpeg_exe()
        version = ffmpeg.run([binary, "-version"]).splitlines()[0]
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        binary, version = None, f"unavailable: {exc}"

    try:
        import faster_whisper  # noqa: F401

        whisper = "installed"
    except ImportError:
        whisper = "missing (pip install faster-whisper)"

    try:
        import yt_dlp  # noqa: F401

        ytdlp = "installed"
    except ImportError:
        ytdlp = "missing (pip install yt-dlp)"

    return {
        "ok": binary is not None,
        "ffmpeg": binary,
        "ffmpeg_version": version,
        "yt_dlp": ytdlp,
        "whisper": whisper,
        "libass": ffmpeg.has_filter("ass"),
        "ollama": highlight.ollama_available(settings),
        "ollama_model": settings.ollama_model,
        "defaults": {
            "max_clips": settings.max_clips,
            "min_clip": settings.min_clip,
            "max_clip": settings.max_clip,
            "vertical": settings.vertical,
            "subtitles": settings.subtitles,
            "use_llm": settings.use_llm,
            "whisper_model": settings.whisper_model,
        },
    }


@app.post("/api/jobs")
def create_job(request: ClipRequest) -> JSONResponse:
    options = {
        key: value
        for key, value in {
            "max_clips": request.max_clips,
            "min_clip": request.min_clip,
            "max_clip": request.max_clip,
            "vertical": request.vertical,
            "subtitles": request.subtitles,
            "use_llm": request.use_llm,
        }.items()
        if value is not None
    }
    low = options.get("min_clip", settings.min_clip)
    high = options.get("max_clip", settings.max_clip)
    if high <= low:
        raise HTTPException(400, "max_clip must be greater than min_clip")

    job = store.create(request.url.strip(), options)
    return JSONResponse(job.public(), status_code=202)


@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"jobs": [job.public() for job in store.list()[:50]]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job.public()


@app.get("/api/jobs/{job_id}/archive")
def download_archive(job_id: str) -> FileResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job.status != "done" or not job.clips:
        raise HTTPException(409, "job has no finished clips yet")

    source_dir = settings.output_dir / job_id
    archive = settings.work_dir / f"{job_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for clip in job.clips:
            for name in (clip["file"], clip.get("srt"), clip.get("thumbnail")):
                if not name:
                    continue
                path = source_dir / name
                if path.exists():
                    bundle.write(path, arcname=name)
        manifest = source_dir / "clips.json"
        if manifest.exists():
            bundle.write(manifest, arcname="clips.json")
    return FileResponse(archive, filename=f"clipforge-{job_id}.zip",
                        media_type="application/zip")


# rendered clips
app.mount("/media", StaticFiles(directory=str(settings.output_dir)), name="media")

# The website itself. Mounted last so every /api and /media route above wins;
# html=True serves index.html for "/" and 404.html for misses.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="site")
else:

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(
            "<h1>ClipForge</h1><p>The web/ folder is missing, so there is no "
            "interface to serve. The API is still running at /api/health.</p>",
            status_code=500,
        )
