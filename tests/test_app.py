import datetime as dt
import json

import pytest

import app as appmodule
import fetch_era5_waves as fetcher


@pytest.fixture
def client(monkeypatch):
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        test_client.submitted = submitted
        yield test_client


def payload(**overrides):
    end = fetcher.latest_available()
    body = {"latitude": -6.9, "longitude": 107.6, "buffer": 0.5,
            "start": (end - dt.timedelta(days=20)).isoformat(), "end": end.isoformat()}
    body.update(overrides)
    return body


def test_create_job_validates_input(client):
    assert client.post("/api/jobs", json=payload(latitude=95)).status_code == 400
    assert client.post("/api/jobs", json=payload(buffer="x")).status_code == 400
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    response = client.post("/api/jobs", json=payload(end=tomorrow))
    assert response.status_code == 400 and "latest usable end date" in response.get_json()["error"]
    assert not client.submitted


def test_pacific_site_no_longer_rejected_near_date_line(client):
    assert client.post("/api/jobs", json=payload(longitude=179.8, buffer=1)).status_code == 202


def test_job_is_persisted_and_survives_reload(client):
    job = client.post("/api/jobs", json=payload()).get_json()
    saved = appmodule.DOWNLOADS / job["id"] / "job.json"
    assert json.loads(saved.read_text())["status"] == "queued"
    appmodule.jobs.clear()
    appmodule.load_jobs()
    reloaded = client.get(f"/api/jobs/{job['id']}").get_json()
    assert reloaded["status"] == "failed"  # it was queued when the "app" stopped
    assert any("restart" in line for line in reloaded["log"])
    assert job["id"] in [item["id"] for item in client.get("/api/jobs").get_json()]


def test_cancel_queued_job_prevents_it_from_running(client):
    job = client.post("/api/jobs", json=payload()).get_json()
    assert client.post(f"/api/jobs/{job['id']}/cancel").get_json()["status"] == "cancelled"
    fn, args = client.submitted[0]
    fn(*args)  # the worker picks it up afterwards and must do nothing
    assert client.get(f"/api/jobs/{job['id']}").get_json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_dry_run_job_end_to_end(client):
    job = client.post("/api/jobs", json=payload(dry_run=True)).get_json()
    fn, args = client.submitted[0]
    fn(*args)  # runs the real fetcher subprocess in dry-run mode
    result = client.get(f"/api/jobs/{job['id']}").get_json()
    assert result["status"] == "complete", result["log"]
    assert any("Dry run complete" in line for line in result["log"])
    assert client.get(f"/api/jobs/{job['id']}/analysis").status_code == 409


def test_delete_job_removes_files(client):
    job = client.post("/api/jobs", json=payload()).get_json()
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 409  # still queued
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not (appmodule.DOWNLOADS / job["id"]).exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_download_rejects_non_netcdf_and_path_tricks(client):
    job = client.post("/api/jobs", json=payload()).get_json()
    assert client.get(f"/api/jobs/{job['id']}/files/job.json").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/files/..%2Fjob.nc").status_code == 404
