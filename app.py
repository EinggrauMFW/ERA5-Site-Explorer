#!/usr/bin/env python3
"""Web interface for selecting a site and downloading ERA5 wave data."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from werkzeug.exceptions import HTTPException
from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory

import crosscheck
import fetch_era5_waves
from analysis import ANALYSIS_VERSION, analyse, find_node, node_summary, sector_section


APP_DIR = Path(__file__).resolve().parent
FETCHER = APP_DIR / "fetch_era5_waves.py"
DOWNLOADS = Path(os.environ.get("DOWNLOADS_DIR", APP_DIR / "downloads"))
DOWNLOADS.mkdir(parents=True, exist_ok=True)
MAX_JOBS = max(1, int(os.environ.get("MAX_JOBS", "2")))
MAX_LOG_LINES = 500
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
# Fields kept out of the JSON the browser receives.
PRIVATE_FIELDS = {"directory"}

app = Flask(__name__)
jobs: dict[str, dict] = {}
processes: dict[str, subprocess.Popen] = {}
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix="era5-job")


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


@app.errorhandler(HTTPException)
def json_errors(error: HTTPException):
    """The browser code expects JSON from every /api route."""
    if request.path.startswith("/api/"):
        return jsonify(error=error.description or error.name), error.code
    return error


# --- job storage -----------------------------------------------------------------

def save_job(job: dict) -> None:
    """Persist a job next to its data. Call with ``jobs_lock`` held."""
    record = {key: value for key, value in job.items() if key not in PRIVATE_FIELDS}
    target = job["directory"] / "job.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(target)


def load_jobs() -> None:
    """Rebuild the job list from disk; jobs cut off by a restart become failed."""
    for folder in DOWNLOADS.iterdir():
        record_path = folder / "job.json"
        if not (JOB_ID.match(folder.name) and record_path.is_file()):
            continue
        try:
            job = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        job["directory"] = folder
        if job.get("status") in ("queued", "running"):
            job["status"] = "failed"
            job["log"].append("Interrupted by an app restart. Submit the request again to resume.")
            save_job(job)
        jobs[job["id"]] = job


def append_log(job: dict, line: str) -> None:
    job["log"].append(line)
    del job["log"][:-MAX_LOG_LINES]


def set_status(job_id: str, status: str, **extra) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = status
        job.update(extra)
        save_job(job)


def run_job(job_id: str, command: list[str]) -> None:
    with jobs_lock:
        job = jobs[job_id]
        if job["status"] != "queued":  # cancelled or deleted while waiting
            return
        job["status"] = "running"
        save_job(job)
    try:
        process = subprocess.Popen(
            command,
            cwd=APP_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            bufsize=1,
        )
    except OSError as exc:
        with jobs_lock:
            append_log(jobs[job_id], f"Unable to start fetcher: {exc}")
        set_status(job_id, "failed")
        return
    with jobs_lock:
        processes[job_id] = process
        if jobs[job_id]["status"] == "cancelled":  # cancelled while the process was starting
            process.terminate()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            with jobs_lock:
                append_log(jobs[job_id], line.rstrip())
        return_code = process.wait()
    finally:
        with jobs_lock:
            processes.pop(job_id, None)
    with jobs_lock:
        cancelled = jobs[job_id]["status"] == "cancelled"
    if not cancelled:
        set_status(job_id, "complete" if return_code == 0 else "failed", return_code=return_code)


def public_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        result = {key: value for key, value in job.items() if key not in PRIVATE_FIELDS}
        result["log"] = list(job["log"])
        directory = job["directory"]
    result["files"] = [
        {"name": path.name, "size": path.stat().st_size}
        for path in sorted(directory.glob("*.nc"))
    ]
    return result


def node_tag(node: tuple[float, float] | None) -> str:
    return "" if node is None else f"_{node[0]:.3f}_{node[1]:.3f}"


def cached_analysis(directory: Path, files: list[Path], latitude: float, longitude: float,
                    product: str, node: tuple[float, float] | None = None) -> dict:
    """Run (or reuse) the analysis; the full per-record frame is saved as timeseries*.csv.

    The default analysis is the nearest ocean cell to the site; a chosen grid node is cached
    separately so that switching between nodes is instant the second time.
    """
    tag = node_tag(node)
    cache = directory / f"analysis{tag}.json"
    series_file = directory / f"timeseries{tag}.csv"
    newest = max(path.stat().st_mtime for path in files)
    if cache.is_file() and series_file.is_file() and cache.stat().st_mtime >= newest:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("version") == ANALYSIS_VERSION:
                return cached
        except (OSError, ValueError):
            pass
    payload, frame = analyse(files, latitude, longitude, product, node)
    frame.to_csv(series_file, index_label="time")
    result = {"version": ANALYSIS_VERSION, "product": product, **payload}
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def cached_nodes(directory: Path, files: list[Path], latitude: float, longitude: float, product: str) -> dict:
    cache = directory / "nodes.json"
    newest = max(path.stat().st_mtime for path in files)
    if cache.is_file() and cache.stat().st_mtime >= newest:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("version") == ANALYSIS_VERSION:
                return cached
        except (OSError, ValueError):
            pass
    result = {"version": ANALYSIS_VERSION, **node_summary(files, product, latitude, longitude)}
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def requested_node(job: dict, files: list[Path]) -> tuple[float, float] | None:
    """Parse ?node_lat=&node_lon= and check it is a real ocean node of this download."""
    raw_lat, raw_lon = request.args.get("node_lat", "").strip(), request.args.get("node_lon", "").strip()
    if not raw_lat and not raw_lon:
        return None
    lat = parse_number(raw_lat, "Node latitude", -90, 90)
    lon = parse_number(raw_lon, "Node longitude", -180, 360)
    summary = cached_nodes(job["directory"], files, job["latitude"], job["longitude"], job["product"])
    node = find_node(summary, lat, lon)
    if node is None:
        raise ValueError(f"{lat:g}, {lon:g} is not a grid node of this download")
    if not node["valid"]:
        raise ValueError(f"Node {lat:g}, {lon:g} has no data (land or ice) and cannot be analysed")
    if summary["default"] and abs(node["lat"] - summary["default"]["lat"]) < 1e-3 \
            and abs(node["lon"] - summary["default"]["lon"]) < 1e-3:
        return None  # the nearest ocean cell is the default analysis
    return node["lat"], node["lon"]


def load_timeseries(directory: Path, node: tuple[float, float] | None = None) -> pd.DataFrame:
    return pd.read_csv(directory / f"timeseries{node_tag(node)}.csv", index_col="time", parse_dates=True)


def completed_job(job_id: str, product: str | None = None) -> dict:
    """Return a snapshot of a finished, non-preview job or abort with a useful message."""
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        snapshot = dict(job)
    if snapshot["status"] != "complete":
        abort(409, description="The download is not complete")
    if snapshot["dry_run"]:
        abort(409, description="Dry runs do not create data to analyse")
    snapshot.setdefault("product", fetch_era5_waves.DEFAULT_PRODUCT)
    if product and snapshot["product"] != product:
        abort(409, description=f"Job {job_id} is {snapshot['product']}, expected {product}")
    return snapshot


def data_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.nc"))
    if not files:
        abort(422, description="No NetCDF files were downloaded")
    return files


# --- routes ----------------------------------------------------------------------

@app.get("/")
def index():
    return render_template(
        "index.html",
        latest_date=fetch_era5_waves.latest_available().isoformat(),
        earliest_date=fetch_era5_waves.EARLIEST.isoformat(),
        products=fetch_era5_waves.PRODUCTS,
        groups=fetch_era5_waves.VARIABLE_GROUPS,
        group_labels=fetch_era5_waves.GROUP_LABELS,
        mars_params=fetch_era5_waves.MARS_SURFACE_PARAMS,
        default_groups=fetch_era5_waves.DEFAULT_GROUPS,
        default_params=fetch_era5_waves.DEFAULT_MARS_PARAMS,
        time_steps=fetch_era5_waves.TIME_STEPS,
        default_steps=fetch_era5_waves.DEFAULT_TIME_STEP,
        max_buffer=fetch_era5_waves.MAX_BUFFER,
        max_years={k: v // 366 for k, v in fetch_era5_waves.MAX_RANGE_DAYS.items()},
    )


@app.get("/api/jobs")
def list_jobs():
    with jobs_lock:
        summary = [
            {key: job.get(key) for key in (
                "id", "status", "created", "latitude", "longitude", "start", "end", "dry_run",
                "product")}
            for job in jobs.values()
        ]
    return jsonify(sorted(summary, key=lambda job: job["created"] or "", reverse=True))


@app.post("/api/jobs")
def create_job():
    payload = request.get_json(silent=True) or {}
    try:
        latitude = parse_number(payload.get("latitude"), "Latitude", -90, 90)
        longitude = parse_number(payload.get("longitude"), "Longitude", -180, 180)
        options = fetch_era5_waves.normalise_options(
            payload.get("product"), payload.get("groups"), payload.get("params"),
            payload.get("time_step"))
        product = options["product"]
        buffer = parse_number(payload.get("buffer", 0.5), "Buffer", 0,
                              fetch_era5_waves.MAX_BUFFER[product])
        start = parse_date(payload.get("start"), "Start date")
        end = parse_date(payload.get("end"), "End date")
        fetch_era5_waves.validate_period(start, end, product=product)
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
        "--product", product,
        "--time-step", str(options["time_step"]),
    ]
    if options["groups"]:
        command += ["--groups", ",".join(options["groups"])]
    if options["params"]:
        command += ["--params", ",".join(options["params"])]
    dry_run = bool(payload.get("dry_run"))
    if dry_run:
        command.append("--dry-run")

    with jobs_lock:
        job = jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "log": [],
            "return_code": None,
            "directory": output,
            "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "latitude": latitude,
            "longitude": longitude,
            "buffer": buffer,
            "product": product,
            "groups": options["groups"],
            "params": options["params"],
            "time_step": options["time_step"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "dry_run": dry_run,
        }
        save_job(job)
    executor.submit(run_job, job_id, command)
    return jsonify(public_job(job_id)), 202


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    return jsonify(public_job(job_id))


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        if job["status"] not in ("queued", "running"):
            return jsonify(error="The job is not active"), 409
        job["status"] = "cancelled"
        append_log(job, "Cancelled by user.")
        save_job(job)
        process = processes.get(job_id)
    if process is not None:
        process.terminate()
    return jsonify(public_job(job_id))


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        if job["status"] in ("queued", "running"):
            return jsonify(error="Cancel the job before deleting it"), 409
        directory = jobs.pop(job_id)["directory"]
    shutil.rmtree(directory, ignore_errors=True)
    return "", 204


@app.get("/api/jobs/<job_id>/analysis")
def get_analysis(job_id: str):
    job = completed_job(job_id)
    directory, product = job["directory"], job["product"]
    files = data_files(directory)
    heading = request.args.get("heading", "").strip()
    try:
        node = requested_node(job, files)
        result = cached_analysis(directory, files, job["latitude"], job["longitude"], product, node)
        if heading:
            if product != "wave-spectra":
                return jsonify(error="Sector flux needs the wave-spectra route (Option B)"), 400
            heading_from = parse_number(heading, "Heading", 0, 360)
            half_width = parse_number(request.args.get("halfwidth", "22.5"), "Half-width", 1, 90)
            section = sector_section(load_timeseries(directory, node), heading_from, half_width)
            sections = list(result["sections"])
            sections.insert(2, section)  # next to the flux summary
            result = {**result, "sections": sections}
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        return jsonify(error=str(exc)), 422


@app.get("/api/jobs/<job_id>/timeseries.csv")
def get_timeseries(job_id: str):
    job = completed_job(job_id)
    files = data_files(job["directory"])
    try:
        node = requested_node(job, files)
        cached_analysis(job["directory"], files, job["latitude"], job["longitude"], job["product"], node)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return jsonify(error=str(exc)), 422
    where = node_tag(node)
    return send_file(job["directory"] / f"timeseries{where}.csv", mimetype="text/csv", as_attachment=True,
                     download_name=f"era5_{job['product']}_{job_id}{where}_timeseries.csv")


@app.get("/api/jobs/<job_id>/nodes")
def get_nodes(job_id: str):
    """All grid nodes in the download with mean Hm0 / Te / flux, so one can be picked for analysis."""
    job = completed_job(job_id)
    files = data_files(job["directory"])
    try:
        return jsonify(cached_nodes(job["directory"], files, job["latitude"], job["longitude"], job["product"]))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return jsonify(error=str(exc)), 422


@app.get("/api/jobs/<job_id>/provenance")
def get_provenance(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        directory = job["directory"]
    path = directory / "provenance.json"
    if not path.is_file():
        return jsonify(error="No provenance.json for this job"), 404
    return send_file(path, mimetype="application/json")


@app.get("/api/crosscheck")
def get_crosscheck():
    """Compare Option A (single levels) with Option B (spectra) from two finished jobs.

    With ?node_lat=&node_lon= both routes are compared at that grid node instead of the
    nearest ocean cell; the node must be an ocean node of both downloads.
    """
    job_a = completed_job(request.args.get("a", ""), "single-levels")
    job_b = completed_job(request.args.get("b", ""), "wave-spectra")
    try:
        payloads, frames = [], []
        for job in (job_a, job_b):
            files = data_files(job["directory"])
            node = requested_node(job, files)
            payloads.append(cached_analysis(job["directory"], files, job["latitude"], job["longitude"],
                                            job["product"], node))
            frames.append(load_timeseries(job["directory"], node))
        cells = [(p["grid_coordinate"]["latitude"], p["grid_coordinate"]["longitude"]) for p in payloads]
        result = crosscheck.compare(frames[0], frames[1], cell_a=cells[0], cell_b=cells[1],
                                    depth_m=payloads[1].get("depth_m"))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return jsonify(error=str(exc)), 422
    result["job_a"], result["job_b"] = job_a["id"], job_b["id"]
    result["node"] = {"latitude": cells[1][0], "longitude": cells[1][1]}
    return jsonify(result)


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


load_jobs()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
