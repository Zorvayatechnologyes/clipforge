"""API and job-queue plumbing. No ffmpeg, no whisper, no network."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("CLIPFORGE_OUTPUT", str(tmp_path / "clips"))
    monkeypatch.setenv("CLIPFORGE_USE_LLM", "false")

    import importlib

    import clipforge.api as api_module

    importlib.reload(api_module)
    from fastapi.testclient import TestClient

    with TestClient(api_module.app) as test_client:
        yield test_client


def test_health_reports_engine_state(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert "ffmpeg" in body and "whisper" in body and "ollama" in body
    assert "defaults" in body
    assert body["defaults"]["max_clips"] >= 1


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ClipForge" in response.text


def test_cors_lets_a_separately_hosted_page_call_the_api(client):
    origin = {"Origin": "https://clipforge.netlify.app"}

    response = client.get("/api/health", headers=origin)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in {"*", origin["Origin"]}

    preflight = client.options(
        "/api/jobs",
        headers={
            **origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert "POST" in preflight.headers.get("access-control-allow-methods", "")


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_job_rejects_impossible_lengths(client):
    response = client.post("/api/jobs", json={"url": "x.mp4", "min_clip": 40, "max_clip": 20})
    assert response.status_code == 400
    assert "max_clip" in response.json()["detail"]


def test_create_job_returns_a_queued_job(client):
    response = client.post("/api/jobs", json={"url": "https://youtu.be/abc", "max_clips": 3})
    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert body["status"] in {"queued", "running", "error"}
    assert body["options"]["max_clips"] == 3

    listed = client.get("/api/jobs").json()["jobs"]
    assert any(job["id"] == body["id"] for job in listed)


def test_archive_needs_finished_clips(client):
    created = client.post("/api/jobs", json={"url": "https://youtu.be/abc"}).json()
    assert client.get(f"/api/jobs/{created['id']}/archive").status_code == 409


def test_job_store_runs_pipeline_and_persists(tmp_path, monkeypatch):
    from clipforge import jobs as jobs_module
    from clipforge.config import load_settings

    settings = load_settings().with_overrides(
        work_dir=tmp_path / "work", output_dir=tmp_path / "clips", use_llm=False
    )
    settings.ensure_dirs()

    def fake_run(source, settings, *, job_id, progress=None, reuse_transcript=True):
        if progress:
            progress(0.5, "halfway")
        return {
            "job_id": job_id,
            "source": source,
            "clip_count": 1,
            "clips": [{"file": "clip_01_x.mp4", "title": "a moment", "duration": 20.0}],
            "warnings": ["no llm"],
            "source_duration": 3600.0,
            "source_resolution": "1920x1080",
            "elapsed_seconds": 12.0,
        }

    monkeypatch.setattr(jobs_module, "run_pipeline", fake_run)

    store = jobs_module.JobStore(settings)
    job = store.create("https://youtu.be/abc", {"max_clips": 2})

    deadline = time.time() + 30
    while time.time() < deadline:
        current = store.get(job.id)
        if current and current.status in {"done", "error"}:
            break
        time.sleep(0.05)

    final = store.get(job.id)
    assert final is not None
    assert final.status == "done", final.error
    assert final.progress == 1.0
    assert final.clips[0]["title"] == "a moment"
    assert final.warnings == ["no llm"]
    assert final.meta["source_duration"] == 3600.0

    # state survives a restart (the write lands a moment after the status flip)
    reloaded = jobs_module.JobStore(settings)
    saved = None
    deadline = time.time() + 15
    while time.time() < deadline:
        reloaded.load()
        saved = reloaded.get(job.id)
        if saved and saved.status == "done":
            break
        time.sleep(0.1)
    assert saved is not None and saved.status == "done"
    store.shutdown()
    reloaded.shutdown()


def test_job_store_records_failures(tmp_path, monkeypatch):
    from clipforge import jobs as jobs_module
    from clipforge.config import load_settings

    settings = load_settings().with_overrides(
        work_dir=tmp_path / "work", output_dir=tmp_path / "clips"
    )
    settings.ensure_dirs()

    def boom(*args, **kwargs):
        raise RuntimeError("download failed: nope")

    monkeypatch.setattr(jobs_module, "run_pipeline", boom)

    store = jobs_module.JobStore(settings)
    job = store.create("https://youtu.be/abc")

    deadline = time.time() + 30
    while time.time() < deadline:
        current = store.get(job.id)
        if current and current.status in {"done", "error"}:
            break
        time.sleep(0.05)

    final = store.get(job.id)
    assert final.status == "error"
    assert "nope" in final.error
    store.shutdown()
