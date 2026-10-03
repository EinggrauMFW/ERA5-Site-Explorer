"""Resuming an interrupted download, and probe jobs: both reuse the job record and its command."""

import pytest

import app as appmodule
from tests.test_provenance_crosscheck import build_a, make_job

PAYLOAD = {"latitude": -8.75, "longitude": 119.28, "buffer": 0.5, "product": "wave-spectra",
           "start": "2025-01-25", "end": "2025-02-10", "time_step": 6, "expver": "1"}


@pytest.fixture
def client(monkeypatch):
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        test_client.submitted = submitted
        yield test_client


def create(client, **extra):
    response = client.post("/api/jobs", json={**PAYLOAD, **extra})
    assert response.status_code == 202, response.get_json()
    return response.get_json()["id"]


def stored_job(job_id, status, **extra):
    """A job as it would sit in the registry after an earlier run."""
    make_job(job_id, "wave-spectra", build_a)
    job = appmodule.jobs[job_id]
    job.update(status=status, **extra)
    return job


# --- the command is built from the job record --------------------------------------

def test_create_and_resume_build_the_same_command(client):
    job_id = create(client)
    fn, (queued_id, command) = client.submitted[0]
    assert fn is appmodule.run_job and queued_id == job_id
    assert command == appmodule.build_command(appmodule.jobs[job_id])
    assert command[command.index("--product") + 1] == "wave-spectra"
    assert command[command.index("--expver") + 1] == "1"
    assert "--probe" not in command and "--dry-run" not in command


def test_a_job_record_without_newer_fields_still_builds_a_command(client):
    job = stored_job("aaaaaaaaaa01", "failed")
    for field in ("groups", "params", "expver", "probe", "time_step"):
        job.pop(field, None)
    command = appmodule.build_command(job)
    assert command[command.index("--time-step") + 1] == "6"          # the product's default step
    assert command[command.index("--expver") + 1] == "auto"
    assert "--groups" not in command and "--params" not in command


# --- resume ------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_resume_requeues_the_same_job(client, status):
    job = stored_job("aaaaaaaaaa02", status, return_code=1,
                     progress={"done": 3, "total": 9, "skipped": 0, "eta_seconds": 60.0})
    response = client.post("/api/jobs/aaaaaaaaaa02/resume")
    assert response.status_code == 202, response.get_json()
    body = response.get_json()
    assert body["id"] == "aaaaaaaaaa02" and body["status"] == "queued" and body["return_code"] is None
    assert "progress" not in body and body["log"][-1].startswith("Resumed")
    fn, (queued_id, command) = client.submitted[-1]
    assert fn is appmodule.run_job and queued_id == "aaaaaaaaaa02"
    assert command == appmodule.build_command(job) and str(job["directory"]) in command


@pytest.mark.parametrize("status", ["queued", "running", "complete"])
def test_only_failed_or_cancelled_jobs_can_be_resumed(client, status):
    stored_job("aaaaaaaaaa03", status)
    response = client.post("/api/jobs/aaaaaaaaaa03/resume")
    assert response.status_code == 409 and "Cannot resume" in response.get_json()["error"]
    assert client.submitted == [] and appmodule.jobs["aaaaaaaaaa03"]["status"] == status


@pytest.mark.parametrize("flag", ["dry_run", "probe"])
def test_a_preview_cannot_be_resumed(client, flag):
    stored_job("aaaaaaaaaa04", "failed", **{flag: True})
    response = client.post("/api/jobs/aaaaaaaaaa04/resume")
    assert response.status_code == 409 and "preview" in response.get_json()["error"]
    assert client.submitted == []


def test_resuming_an_unknown_job_is_a_404(client):
    assert client.post("/api/jobs/ffffffffffff/resume").status_code == 404


# --- probe jobs ----------------------------------------------------------------------

def test_a_probe_request_runs_the_fetcher_in_probe_mode(client):
    job_id = create(client, probe=True)
    _, (_, command) = client.submitted[0]
    assert command[-1] == "--probe" and "--dry-run" not in command
    job = client.get(f"/api/jobs/{job_id}").get_json()
    assert job["probe"] is True
    assert appmodule.is_preview(job) and not appmodule.is_preview({"dry_run": False})


def test_a_probe_request_is_validated_like_a_download(client):
    response = client.post("/api/jobs", json={**PAYLOAD, "probe": True, "latitude": 95})
    assert response.status_code == 400 and client.submitted == []


def test_a_finished_probe_never_counts_as_data_to_analyse(client):
    stored_job("aaaaaaaaaa05", "complete", probe=True)
    for route in ("analysis", "nodes", "timeseries.csv"):
        response = client.get(f"/api/jobs/aaaaaaaaaa05/{route}")
        assert response.status_code == 409, route
    listed = {job["id"]: job for job in client.get("/api/jobs").get_json()}
    assert listed["aaaaaaaaaa05"]["probe"] is True
