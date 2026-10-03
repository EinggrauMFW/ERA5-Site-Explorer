#!/usr/bin/env python3
"""Web interface for selecting a site and downloading ERA5 wave data."""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import statistics
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import xarray as xr
from werkzeug.exceptions import HTTPException
from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory

import crosscheck
import fetch_era5_waves
from netcdf_safety import NETCDF_LOCK, atomic_publish, atomic_write_text
import plugins
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


def _is_json_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def is_preview(job: dict) -> bool:
    return bool(job.get("dry_run") or job.get("probe"))


@app.before_request
def check_host_and_origin():
    raw_host = request.headers.get("Host")
    if not raw_host or not raw_host.strip():
        return jsonify(error="Missing or empty Host header"), 403

    raw_host = raw_host.strip()
    if raw_host.startswith("["):
        bracket_end = raw_host.find("]")
        if bracket_end == -1:
            return jsonify(error="Invalid Host header"), 403
        hostname = raw_host[:bracket_end + 1].lower()
        remainder = raw_host[bracket_end + 1:]
        if remainder and not remainder.startswith(":"):
            return jsonify(error="Invalid Host header"), 403
    else:
        hostname = raw_host.split(":", 1)[0].lower()

    allowed = {"127.0.0.1", "localhost", "[::1]"}
    extra_hosts = os.environ.get("ALLOWED_HOSTS", "")
    if extra_hosts:
        for h in extra_hosts.split(","):
            h = h.strip().lower()
            if h:
                allowed.add(h)

    if hostname not in allowed:
        return jsonify(error=f"Forbidden host: {hostname}"), 403

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin is not None:
            expected_origin = f"{request.scheme}://{raw_host}".lower()
            if not origin or origin.strip().lower() != expected_origin:
                return jsonify(error="Forbidden cross-origin request"), 403


def _safe_data_file_error(exc: Exception, job_id: str | None = None):
    if not job_id and request.view_args:
        job_id = request.view_args.get("job_id")
    if not job_id:
        m = re.search(r"/api/jobs/([0-9a-f]{12})", request.path)
        if m:
            job_id = m.group(1)

    folder = None
    if job_id:
        with jobs_lock:
            if job_id in jobs:
                folder = jobs[job_id].get("directory")
        if not folder and (DOWNLOADS / job_id).is_dir():
            folder = DOWNLOADS / job_id

    bad_name = None
    if folder:
        nc_files = sorted(folder.glob("*.nc"))
        exc_str = str(exc)
        for f in nc_files:
            if f.name in exc_str:
                bad_name = f.name
                break
        if not bad_name:
            for f in nc_files:
                try:
                    with NETCDF_LOCK:
                        with xr.open_dataset(f, engine="netcdf4"):
                            pass
                except Exception:
                    bad_name = f.name
                    break
        if not bad_name and isinstance(exc, OSError) and len(nc_files) == 1:
            bad_name = nc_files[0].name

    if bad_name:
        return jsonify(error=f"The data file {bad_name} could not be read: it may be incomplete or corrupt."), 422
    if isinstance(exc, OSError):  # an OS error message carries the full file path: keep it out of the response
        return jsonify(error="A data file could not be read: it may be incomplete or corrupt."), 422

    return jsonify(error=str(exc)), 422


@app.errorhandler(HTTPException)
def json_errors(error: HTTPException):
    """The browser code expects JSON from every /api route."""
    if request.path.startswith("/api/"):
        return jsonify(error=error.description or error.name), error.code
    return error


@app.errorhandler(ValueError)
def value_errors(error: ValueError):
    """Plugin routes raise ValueError for bad input; show it as a 422 like the core routes do."""
    if request.path.startswith("/api/"):
        return _safe_data_file_error(error)
    raise error


@app.errorhandler(OSError)
def os_errors(error: OSError):
    """NetCDF and file errors on API routes return 422 with a safe filename-only message."""
    if request.path.startswith("/api/"):
        return _safe_data_file_error(error)
    raise error


# --- job storage -----------------------------------------------------------------

def save_job(job: dict) -> None:
    """Persist a job next to its data. Call with ``jobs_lock`` held."""
    record = {key: value for key, value in job.items() if key not in PRIVATE_FIELDS}
    target = job["directory"] / "job.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(target)


pid_alive = fetch_era5_waves.pid_alive  # one implementation, shared with the fetcher's parent watchdog


def load_jobs(mark_interrupted: bool = False) -> None:
    """Rebuild the job list from disk; jobs cut off by a restart become failed if mark_interrupted=True."""
    for folder in DOWNLOADS.iterdir():
        record_path = folder / "job.json"
        if not (JOB_ID.match(folder.name) and record_path.is_file()):
            continue
        try:
            job = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(job, dict):
            logging.warning("Skipping job record %s: not a JSON object", record_path)
            continue
        if not all(k in job for k in ("id", "status", "log")):
            logging.warning("Skipping job record %s: missing required keys", record_path)
            continue
        if not isinstance(job["id"], str) or not isinstance(job["status"], str) or not isinstance(job["log"], list):
            logging.warning("Skipping job record %s: invalid field types", record_path)
            continue
        job["directory"] = folder
        if mark_interrupted and job.get("status") in ("queued", "running"):
            job["status"] = "failed"
            pid = job.get("pid")
            if pid is not None and pid_alive(pid):
                job["log"].append(f"The fetcher from the earlier run (pid {pid}) is still running; "
                                  "Resume is blocked until it exits.")
            else:
                job["log"].append("Interrupted by an app restart. Resume continues it.")
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


def progress_update(state: dict | None, line: str, now: float) -> tuple[dict | None, str | None]:
    """Parse a ::progress:: line. Returns (new_state, log_line)."""
    if not line.startswith("::progress::"):
        return state, line.rstrip()
    try:
        prog = json.loads(line.split("::progress::", 1)[1].strip())
        done, total, skipped = prog["done"], prog["total"], prog["skipped"]
    except (ValueError, KeyError, TypeError):
        return state, line.rstrip()

    state = state or {"durations": []}
    durations = state["durations"]

    if "last_time" in state and state.get("skipped") == skipped:
        durations.append(now - state["last_time"])

    eta_seconds = None
    if len(durations) >= 2:
        eta_seconds = statistics.median(durations) * (total - done)

    state.update({
        "done": done,
        "total": total,
        "skipped": skipped,
        "eta_seconds": eta_seconds,
        "last_time": now
    })
    return state, None


def run_job(job_id: str, command: list[str]) -> None:
    seen_save_errors: set[tuple[type, str]] = set()

    def try_save(job_record: dict) -> None:
        try:
            save_job(job_record)
        except OSError as exc:
            key = (type(exc), str(exc))
            if key not in seen_save_errors:
                seen_save_errors.add(key)
                logging.warning("Unable to save job %s: %s", job_id, exc)

    with jobs_lock:
        job = jobs.get(job_id)
        if job is None or job["status"] != "queued":  # cancelled or deleted while waiting
            return
        captured_run = job.get("run", 1)
        job["status"] = "running"
        try_save(job)
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
            current_job = jobs.get(job_id)
            is_current = bool(current_job) and current_job.get("run") == captured_run
            if is_current:
                append_log(current_job, f"Unable to start fetcher: {exc}")
        if is_current:
            set_status(job_id, "failed")
        return
    with jobs_lock:
        current_job = jobs.get(job_id)
        if current_job and current_job.get("run") == captured_run:
            processes[job_id] = process
            current_job["pid"] = process.pid
            try_save(current_job)
            if current_job.get("status") == "cancelled":  # cancelled while the process was starting
                process.terminate()
        else:  # a newer run (or a delete) took over while this process was starting: do not leave it running
            process.terminate()
    try:
        assert process.stdout is not None
        prog_state = None
        for line in process.stdout:
            prog_state, log_line = progress_update(prog_state, line, time.monotonic())
            with jobs_lock:
                current_job = jobs.get(job_id)
                if current_job and current_job.get("run") == captured_run:
                    if prog_state:
                        current_job["progress"] = {
                            k: v for k, v in prog_state.items()
                            if k in ("done", "total", "skipped", "eta_seconds")
                        }
                    if log_line is not None:
                        append_log(current_job, log_line)
                    try_save(current_job)
        return_code = process.wait()
    finally:
        with jobs_lock:
            if processes.get(job_id) is process:
                processes.pop(job_id, None)
    with jobs_lock:
        current_job = jobs.get(job_id)
        if current_job and current_job.get("run") == captured_run:
            current_job.pop("pid", None)
            if current_job["status"] != "cancelled":
                current_job["status"] = "complete" if return_code == 0 else "failed"
                current_job["return_code"] = return_code
            try_save(current_job)


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
    with NETCDF_LOCK:
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
        atomic_publish(series_file, lambda temp_path: frame.to_csv(temp_path, index_label="time"))
        result = {"version": ANALYSIS_VERSION, "product": product, **payload}
        atomic_write_text(cache, json.dumps(result), encoding="utf-8")
        return result


def cached_nodes(directory: Path, files: list[Path], latitude: float, longitude: float, product: str) -> dict:
    with NETCDF_LOCK:
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
        atomic_write_text(cache, json.dumps(result), encoding="utf-8")
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
    if is_preview(snapshot):
        abort(409, description="Previews do not create data to analyse")
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

def plugin_assets(extension: str) -> list[str]:
    """Names of the plugins that ship a script or stylesheet, in the order the plugins register."""
    names = (module.removeprefix("plugin_") for module in plugins.PLUGIN_MODULES)
    return [name for name in names if (APP_DIR / "static" / "plugins" / f"{name}.{extension}").is_file()]


@app.get("/")
def index():
    return render_template(
        "index.html",
        latest_date=fetch_era5_waves.latest_available().isoformat(),
        earliest_date=fetch_era5_waves.EARLIEST.isoformat(),
        plugin_scripts=plugin_assets("js"),
        plugin_styles=plugin_assets("css"),
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
                "id", "status", "created", "latitude", "longitude", "start", "end", "dry_run", "probe",
                "product")}
            for job in jobs.values()
        ]
    return jsonify(sorted(summary, key=lambda job: job["created"] or "", reverse=True))


def build_command(job: dict) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(FETCHER),
        "--latitude", str(job["latitude"]),
        "--longitude", str(job["longitude"]),
        "--start", job["start"],
        "--end", job["end"],
        "--buffer", str(job["buffer"]),
        "--output", str(job["directory"]),
        "--product", job["product"],
        "--time-step", str(job.get("time_step") or fetch_era5_waves.DEFAULT_TIME_STEP[job["product"]]),
        "--parent-pid", str(os.getpid()),
    ]
    if job["product"] != "single-levels":
        command += ["--expver", str(job.get("expver", "auto"))]
    if job.get("groups"):
        command += ["--groups", ",".join(job["groups"])]
    if job.get("params"):
        command += ["--params", ",".join(job["params"])]
    if job.get("dry_run"):
        command.append("--dry-run")
    if job.get("probe"):
        command.append("--probe")
    return command


@app.post("/api/jobs")
def create_job():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Request body must be a JSON object"), 400

    for num_field, required in (("latitude", True), ("longitude", True), ("buffer", False), ("time_step", False)):
        if num_field in payload:
            if not _is_json_number(payload[num_field]):
                return jsonify(error=f"{num_field} must be a number"), 400
        elif required:
            return jsonify(error=f"{num_field} is required"), 400

    for str_field, required in (("start", True), ("end", True), ("product", False), ("expver", False)):
        if str_field in payload:
            if not isinstance(payload[str_field], str):
                return jsonify(error=f"{str_field} must be a string"), 400
        elif required:
            return jsonify(error=f"{str_field} is required"), 400

    for list_field in ("groups", "params"):
        if list_field in payload:
            val = payload[list_field]
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                return jsonify(error=f"{list_field} must be a list of strings"), 400

    try:
        latitude = parse_number(payload["latitude"], "Latitude", -90, 90)
        longitude = parse_number(payload["longitude"], "Longitude", -180, 180)
        options = fetch_era5_waves.normalise_options(
            payload.get("product"), payload.get("groups"), payload.get("params"),
            payload.get("time_step"))
        product = options["product"]
        expver = str(payload.get("expver") or "auto")
        if expver not in ("auto", "1", "5"):
            raise ValueError("ERA5 version must be auto, 1 (final) or 5 (preliminary ERA5T)")
        buffer = parse_number(payload.get("buffer", 0.5), "Buffer", 0,
                              fetch_era5_waves.MAX_BUFFER[product])
        start = parse_date(payload["start"], "Start date")
        end = parse_date(payload["end"], "End date")
        fetch_era5_waves.validate_period(start, end, product=product)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = uuid.uuid4().hex[:12]
    output = DOWNLOADS / job_id
    output.mkdir()
    dry_run = bool(payload.get("dry_run"))
    probe = bool(payload.get("probe"))

    with jobs_lock:
        job = jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "run": 1,
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
            "expver": expver,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "dry_run": dry_run,
            "probe": probe,
        }
        save_job(job)

    command = build_command(job)
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


@app.post("/api/jobs/<job_id>/resume")
def resume_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        if job["status"] not in ("failed", "cancelled"):
            return jsonify(error=f"Cannot resume a {job['status']} job"), 409
        if is_preview(job):
            return jsonify(error="Cannot resume a preview job"), 409

        pid = job.get("pid")
        if pid is not None and pid_alive(pid):
            return jsonify(error=f"A fetcher for this job (pid {pid}) is still running. Wait for it to finish or stop it."), 409

        job["run"] = job.get("run", 0) + 1
        job["status"] = "queued"
        job["return_code"] = None
        job.pop("pid", None)
        job.pop("progress", None)
        append_log(job, "Resumed: files already on disk are kept.")
        save_job(job)
    command = build_command(job)
    executor.submit(run_job, job_id, command)
    return jsonify(public_job(job_id)), 202


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
            section = {**sector_section(load_timeseries(directory, node), heading_from, half_width),
                       "group": "direction"}
            sections = list(result["sections"])
            sections.insert(2, section)  # next to the flux summary
            result = {**result, "sections": sections}
        return jsonify(result)
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        return _safe_data_file_error(exc, job_id)


def _read_bytes_safe(path: Path) -> bytes:
    for attempt in range(50):
        try:
            return path.read_bytes()
        except (PermissionError, FileNotFoundError):
            if attempt == 49:
                raise
            time.sleep(0.005 * (attempt + 1))


@app.get("/api/jobs/<job_id>/timeseries.csv")
def get_timeseries(job_id: str):
    job = completed_job(job_id)
    files = data_files(job["directory"])
    try:
        node = requested_node(job, files)
        where = node_tag(node)
        path = job["directory"] / f"timeseries{where}.csv"
        with NETCDF_LOCK:
            cached_analysis(job["directory"], files, job["latitude"], job["longitude"], job["product"], node)
            data = _read_bytes_safe(path)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return _safe_data_file_error(exc, job_id)
    return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                     download_name=f"era5_{job['product']}_{job_id}{where}_timeseries.csv")


@app.get("/api/jobs/<job_id>/nodes")
def get_nodes(job_id: str):
    """All grid nodes in the download with mean Hm0 / Te / flux, so one can be picked for analysis."""
    job = completed_job(job_id)
    files = data_files(job["directory"])
    try:
        return jsonify(cached_nodes(job["directory"], files, job["latitude"], job["longitude"], job["product"]))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return _safe_data_file_error(exc, job_id)


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
        return _safe_data_file_error(exc)
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


def job_view(job_id: str, product: str | None = None) -> plugins.JobView:
    """A finished job, with the grid node from ?node_lat=&node_lon=, for plugin routes."""
    job = completed_job(job_id, product)
    files = data_files(job["directory"])
    node = requested_node(job, files)
    directory, latitude, longitude, kind = job["directory"], job["latitude"], job["longitude"], job["product"]
    return plugins.JobView(
        id=job["id"], product=kind, directory=directory, latitude=latitude, longitude=longitude,
        files=files, node=node,
        _analysis=lambda: cached_analysis(directory, files, latitude, longitude, kind, node),
        _frame=lambda: (cached_analysis(directory, files, latitude, longitude, kind, node),
                        load_timeseries(directory, node))[1],
        _nodes=lambda: cached_nodes(directory, files, latitude, longitude, kind),
    )


load_jobs()
LOADED_PLUGINS = plugins.load_plugins(app, plugins.PluginContext(job=job_view, downloads=DOWNLOADS))

def main() -> int:
    try:
        port = int(os.environ.get("PORT", "5000"))
    except ValueError:
        port = 5000
    host = "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        if s.connect_ex((host, port)) == 0:
            print(f"error: port {port} is already in use (another copy of the app?). Stop it or set PORT.")
            return 1
    load_jobs(mark_interrupted=True)
    app.run(host=host, port=port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
