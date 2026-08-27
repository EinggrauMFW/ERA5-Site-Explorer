#!/usr/bin/env python3
"""Web interface for selecting a site and downloading ERA5 wave data."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory


APP_DIR = Path(__file__).resolve().parent
FETCHER = APP_DIR.parent / "fetch_era5_waves.py"
DOWNLOADS = APP_DIR / "downloads"
DOWNLOADS.mkdir(exist_ok=True)

app = Flask(__name__)
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

WAVE_FIELDS = {
    "swh": ("Significant wave height", "m"),
    "mwp": ("Mean wave period", "s"),
    "pp1d": ("Peak wave period", "s"),
    "mwd": ("Mean wave direction", "°"),
}


def parse_number(value: object, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return number


def parse_date(value: object, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def run_job(job_id: str, command: list[str]) -> None:
    with jobs_lock:
        jobs[job_id]["status"] = "running"
    try:
        process = subprocess.Popen(
            command,
            cwd=APP_DIR.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            with jobs_lock:
                jobs[job_id]["log"].append(line.rstrip())
        return_code = process.wait()
        with jobs_lock:
            jobs[job_id]["status"] = "complete" if return_code == 0 else "failed"
            jobs[job_id]["return_code"] = return_code
    except Exception as exc:  # Keep failures visible to the browser.
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["log"].append(f"Unable to start fetcher: {exc}")


def public_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        result = {key: value for key, value in job.items() if key != "directory"}
        directory = job["directory"]
    result["files"] = [
        {"name": path.name, "size": path.stat().st_size}
        for path in sorted(directory.glob("*.nc"))
    ]
    return result


def analyse_files(files: list[Path], latitude: float, longitude: float) -> dict:
    """Extract a nearest-grid-point wave time series from CDS NetCDF output."""
    try:
        import numpy as np
        import xarray as xr
    except ImportError as exc:
        raise RuntimeError("Install xarray and netCDF4 to analyse ERA5 data") from exc

    samples: dict[str, list] = {name: [] for name in WAVE_FIELDS}
    times: list = []
    grid_lat = grid_lon = None

    with tempfile.TemporaryDirectory(prefix="era5-analysis-") as temp_name:
        temp_dir = Path(temp_name)
        datasets: list[Path] = []
        for index, source in enumerate(files):
            if zipfile.is_zipfile(source):
                with zipfile.ZipFile(source) as archive:
                    members = [n for n in archive.namelist() if n.endswith(".nc") and "wave" in n]
                    for member_index, member in enumerate(members):
                        target = temp_dir / f"{index}-{member_index}-{Path(member).name}"
                        with archive.open(member) as incoming, target.open("wb") as outgoing:
                            while chunk := incoming.read(1024 * 1024):
                                outgoing.write(chunk)
                        datasets.append(target)
            else:
                datasets.append(source)

        if not datasets:
            raise ValueError("No wave NetCDF stream was found in the downloaded files")

        for dataset_path in datasets:
            with xr.open_dataset(dataset_path, engine="netcdf4") as dataset:
                time_name = "valid_time" if "valid_time" in dataset.coords else "time"
                point = dataset.sel(latitude=latitude, longitude=longitude, method="nearest")
                if grid_lat is None:
                    grid_lat = float(point.latitude.values)
                    grid_lon = float(point.longitude.values)
                file_times = point[time_name].values.astype("datetime64[s]")
                times.extend(file_times.tolist())
                for name in WAVE_FIELDS:
                    if name in point:
                        samples[name].extend(np.asarray(point[name].values, dtype=float).tolist())

    if not times or not samples["swh"]:
        raise ValueError("The NetCDF files do not contain a significant-wave-height time series")

    order = np.argsort(np.asarray(times, dtype="datetime64[s]"))
    iso_times = np.asarray(times, dtype="datetime64[s]")[order]
    series = {}
    for name, (label, unit) in WAVE_FIELDS.items():
        values = np.asarray(samples[name], dtype=float)
        if values.size != order.size:
            continue
        values = values[order]
        valid = values[np.isfinite(values)]
        series[name] = {
            "label": label,
            "unit": unit,
            "values": [None if not np.isfinite(v) else round(float(v), 3) for v in values],
            "mean": round(float(np.mean(valid)), 3) if valid.size else None,
            "maximum": round(float(np.max(valid)), 3) if valid.size else None,
            "p95": round(float(np.percentile(valid, 95)), 3) if valid.size else None,
        }

    # Common deep-water approximation using mean period as the energy period proxy.
    if "mwp" in series:
        height = np.asarray(samples["swh"], dtype=float)[order]
        period = np.asarray(samples["mwp"], dtype=float)[order]
        power = 0.49 * height**2 * period
        valid = power[np.isfinite(power)]
        series["power"] = {
            "label": "Wave power proxy", "unit": "kW/m",
            "values": [None if not np.isfinite(v) else round(float(v), 3) for v in power],
            "mean": round(float(np.mean(valid)), 3),
            "maximum": round(float(np.max(valid)), 3),
            "p95": round(float(np.percentile(valid, 95)), 3),
        }

    # Keep browser payloads responsive for multi-year requests.
    stride = max(1, (len(iso_times) + 1499) // 1500)
    indices = list(range(0, len(iso_times), stride))
    return {
        "requested_coordinate": {"latitude": latitude, "longitude": longitude},
        "grid_coordinate": {"latitude": grid_lat, "longitude": grid_lon},
        "points": len(iso_times),
        "displayed_points": len(indices),
        "start": str(iso_times[0]),
        "end": str(iso_times[-1]),
        "times": [str(iso_times[i]) for i in indices],
        "series": {
            name: {**data, "values": [data["values"][i] for i in indices]}
            for name, data in series.items()
        },
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/jobs")
def create_job():
    payload = request.get_json(silent=True) or {}
    try:
        latitude = parse_number(payload.get("latitude"), "Latitude", -90, 90)
        longitude = parse_number(payload.get("longitude"), "Longitude", -180, 180)
        buffer = parse_number(payload.get("buffer", 0.5), "Buffer", 0, 10)
        start = parse_date(payload.get("start"), "Start date")
        end = parse_date(payload.get("end"), "End date")
        if start > end:
            raise ValueError("Start date must be on or before end date")
        if longitude - buffer < -180 or longitude + buffer > 180:
            raise ValueError("The selected area crosses the date line; reduce the buffer")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = uuid.uuid4().hex[:12]
    output = DOWNLOADS / job_id
    output.mkdir()
    command = [
        sys.executable,
        "-u",
        str(FETCHER),
        "--latitude", str(latitude),
        "--longitude", str(longitude),
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--buffer", str(buffer),
        "--output", str(output),
    ]
    if payload.get("dry_run"):
        command.append("--dry-run")

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "log": [],
            "return_code": None,
            "directory": output,
            "latitude": latitude,
            "longitude": longitude,
            "buffer": buffer,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "dry_run": bool(payload.get("dry_run")),
        }
    threading.Thread(target=run_job, args=(job_id, command), daemon=True).start()
    return jsonify(public_job(job_id)), 202


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    return jsonify(public_job(job_id))


@app.get("/api/jobs/<job_id>/analysis")
def get_analysis(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        if job["status"] != "complete":
            return jsonify(error="The download is not complete"), 409
        if job["dry_run"]:
            return jsonify(error="Dry runs do not create data to analyse"), 409
        files = sorted(job["directory"].glob("*.nc"))
        latitude, longitude = job["latitude"], job["longitude"]
    try:
        return jsonify(analyse_files(files, latitude, longitude))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return jsonify(error=str(exc)), 422


@app.get("/api/jobs/<job_id>/files/<path:filename>")
def download_file(job_id: str, filename: str):
    if Path(filename).name != filename or not filename.endswith(".nc"):
        abort(404)
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        directory = job["directory"]
    return send_from_directory(directory, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
