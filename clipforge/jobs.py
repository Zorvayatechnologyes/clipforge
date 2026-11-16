"""A tiny in-process job queue with persisted state."""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .pipeline import run as run_pipeline


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    source: str
    status: str = "queued"          # queued | running | done | error
    stage: str = "queued"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    options: dict = field(default_factory=dict)
    clips: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def public(self) -> dict:
        data = asdict(self)
        data["progress"] = round(float(self.progress), 3)
        return data


class JobStore:
    """Runs one heavy job at a time -- transcription already saturates the CPU."""

    def __init__(self, settings: Settings, *, max_workers: int = 1) -> None:
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="clipforge")
        self.state_file = settings.work_dir / "jobs.json"

    # ---------------------------------------------------------------- public
    def create(self, source: str, options: dict | None = None) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, source=source, options=options or {})
        with self._lock:
            self._jobs[job_id] = job
        self._persist()
        self._pool.submit(self._run, job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def load(self) -> None:
        """Restore finished jobs so the UI is not empty after a restart."""
        if not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        with self._lock:
            for item in raw.get("jobs", []):
                try:
                    job = Job(**item)
                except TypeError:
                    continue
                if job.status in {"queued", "running"}:
                    job.status = "error"
                    job.error = "interrupted by a restart"
                self._jobs[job.id] = job

    # --------------------------------------------------------------- internal
    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = _now()

    def _persist(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {"jobs": [asdict(j) for j in self._jobs.values()]}
            self.state_file.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        settings = self.settings.with_overrides(**job.options)
        self._update(job_id, status="running", stage="starting", progress=0.0,
                     message="starting")

        def progress(fraction: float, message: str) -> None:
            self._update(job_id, progress=fraction, stage=message, message=message)

        try:
            manifest = run_pipeline(
                job.source, settings, job_id=job_id, progress=progress
            )
        except Exception as exc:  # surface anything to the UI
            detail = str(exc).strip() or exc.__class__.__name__
            self._update(job_id, status="error", error=detail, stage="error",
                         progress=1.0, message=detail)
            traceback.print_exc()
        else:
            self._update(
                job_id,
                status="done",
                stage="done",
                progress=1.0,
                message=f"{manifest['clip_count']} clips ready",
                clips=manifest["clips"],
                warnings=manifest.get("warnings", []),
                meta={
                    "source_duration": manifest.get("source_duration"),
                    "source_resolution": manifest.get("source_resolution"),
                    "elapsed_seconds": manifest.get("elapsed_seconds"),
                },
            )
        finally:
            self._persist()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
