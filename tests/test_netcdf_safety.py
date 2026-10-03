"""Acceptance tests for docs/work-packages/WP11_netcdf_safety.md.

netCDF4 is not thread-safe, so every dataset context must be serialised by one lock, and cache files must be
published atomically. These tests are written from the spec: expected values come from it, not from the code.
"""

import ast
import gc
import json
import os
import threading
import time
from pathlib import Path

import pytest

import analysis
import app as appmodule
import plugin_screening
import screening
from tests.test_nodes import client  # noqa: F401  (fixture)
from tests.test_provenance_crosscheck import build_a, make_job

ROOT = Path(__file__).resolve().parent.parent


def safety():
    """The shared module the spec requires; a clear failure instead of an ImportError."""
    try:
        import netcdf_safety
    except ImportError:
        pytest.fail("netcdf_safety.py is required by WP11 requirement 1")
    return netcdf_safety


# --- 1. the shared module -----------------------------------------------------------------------

def test_the_lock_is_one_reentrant_process_wide_lock():
    lock = safety().NETCDF_LOCK
    assert lock is safety().NETCDF_LOCK
    with lock:
        with lock:          # re-entrant: a function holding it may call another that takes it
            pass
    assert type(lock) is type(threading.RLock())


def test_atomic_write_replaces_content_and_leaves_no_temporary_file(tmp_path):
    target = tmp_path / "result.json"
    safety().atomic_write_text(target, "first")
    safety().atomic_write_bytes(target, b"second")
    assert target.read_bytes() == b"second"
    assert [p.name for p in tmp_path.iterdir()] == ["result.json"]


def test_atomic_write_text_uses_the_requested_encoding(tmp_path):
    target = tmp_path / "t.txt"
    safety().atomic_write_text(target, "Hm0 ≈ 2 m", encoding="utf-8")
    assert target.read_bytes() == "Hm0 ≈ 2 m".encode("utf-8")


def test_a_failed_publish_keeps_the_old_file_and_removes_the_temporary_one(tmp_path):
    target = tmp_path / "series.csv"
    target.write_text("old")

    def broken_writer(temp_path):
        Path(temp_path).write_text("half a fi")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        safety().atomic_publish(target, broken_writer)
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["series.csv"]


def test_a_failed_publish_of_a_new_file_leaves_nothing_behind(tmp_path):
    target = tmp_path / "new.csv"

    def broken_writer(temp_path):
        raise OSError("nope")

    with pytest.raises(OSError):
        safety().atomic_publish(target, broken_writer)
    assert list(tmp_path.iterdir()) == []


def test_atomic_publish_hands_the_writer_a_temporary_path_in_the_same_folder(tmp_path):
    target = tmp_path / "out.csv"
    seen = []

    def writer(temp_path):
        seen.append(Path(temp_path))
        Path(temp_path).write_text("done")

    safety().atomic_publish(target, writer)
    assert seen[0].parent == tmp_path and seen[0] != target
    assert target.read_text() == "done"


def test_concurrent_writers_to_one_path_never_share_a_temporary_file(tmp_path):
    target = tmp_path / "shared.json"
    names, barrier = [], threading.Barrier(6)

    def writer_for(i):
        def writer(temp_path):
            names.append(Path(temp_path).name)
            barrier.wait(timeout=10)             # all six are mid-write at once
            Path(temp_path).write_text(str(i) * 1000)
        return writer

    threads = [threading.Thread(target=safety().atomic_publish, args=(target, writer_for(i))) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(set(names)) == 6
    content = target.read_text()
    assert len(content) == 1000 and len(set(content)) == 1       # one writer's complete content, not a mix
    assert [p.name for p in tmp_path.iterdir()] == ["shared.json"]


# --- 2. every open_dataset sits inside `with NETCDF_LOCK` ------------------------------------------

def open_dataset_calls_outside_the_lock(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open_dataset":
            locked, current = False, node
            while current in parents:
                current = parents[current]
                if isinstance(current, ast.With) and any(
                        "NETCDF_LOCK" in ast.unparse(item.context_expr) for item in current.items):
                    locked = True
                    break
            if not locked:
                bad.append(f"{path.name}:{node.lineno}")
    return bad


def test_no_production_open_dataset_runs_outside_the_netcdf_lock():
    files = [p for p in ROOT.glob("*.py") if p.name != "netcdf_safety.py"]
    assert any("open_dataset" in p.read_text(encoding="utf-8") for p in files)
    bad = [site for p in files for site in open_dataset_calls_outside_the_lock(p)]
    assert bad == [], f"open_dataset outside `with NETCDF_LOCK`: {bad}"


# --- 3 and 4. concurrent requests ------------------------------------------------------------------

@pytest.fixture
def job(client):
    make_job("cccccccc0a01", "single-levels", build_a)
    return "cccccccc0a01"


def fire(client, routes):
    """Send all the requests at once from separate threads; return (route, status, body-length, body).

    Each thread uses its own test client: Flask's context-preserving client must not be shared across threads.
    """
    results, barrier = [None] * len(routes), threading.Barrier(len(routes))

    def one(i, route):
        barrier.wait(timeout=20)
        response = appmodule.app.test_client().get(route)
        results[i] = (route, response.status_code, len(response.data), response.data)
        response.close()                     # an unclosed file response keeps the file open (blocks deletes on Windows)

    threads = [threading.Thread(target=one, args=(i, r)) for i, r in enumerate(routes)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert all(results), "a request thread did not finish"
    return results


def clear_caches(job_id):
    gc.collect()                             # drop any response a previous test left holding a cache file open
    directory = appmodule.DOWNLOADS / job_id
    for pattern in ("analysis*.json", "nodes*.json", "screening.json", "timeseries*.csv"):
        for path in directory.glob(pattern):
            path.unlink(missing_ok=True)


def test_one_computation_serves_many_concurrent_requests_with_complete_files(client, job, monkeypatch):
    clear_caches(job)
    calls = []
    real = appmodule.analyse

    def counting(*args, **kwargs):
        calls.append(1)
        time.sleep(0.3)                      # widen the window in which a second request could start its own run
        return real(*args, **kwargs)

    monkeypatch.setattr(appmodule, "analyse", counting)
    routes = [f"/api/jobs/{job}/analysis"] * 3 + [f"/api/jobs/{job}/timeseries.csv"] * 3
    results = fire(client, routes)

    assert [status for _, status, _, _ in results] == [200] * 6
    assert len(calls) == 1                   # exactly one computation for the one key
    analyses = {r[3] for r in results if r[0].endswith("/analysis")}
    series = {r[3] for r in results if r[0].endswith("timeseries.csv")}
    assert len(analyses) == 1 and len(series) == 1
    assert json.loads(next(iter(analyses)))["version"] == analysis.ANALYSIS_VERSION
    header, *rows = next(iter(series)).decode("utf-8").splitlines()
    assert header.startswith("time,") and len(rows) > 10
    assert all(len(row.split(",")) == len(header.split(",")) for row in rows)   # no row cut short


def test_nodes_are_computed_once_for_concurrent_requests(client, job, monkeypatch):
    clear_caches(job)
    calls = []
    real = appmodule.node_summary

    def counting(*args, **kwargs):
        calls.append(1)
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(appmodule, "node_summary", counting)
    results = fire(client, [f"/api/jobs/{job}/nodes"] * 4)
    assert [status for _, status, _, _ in results] == [200] * 4
    assert len(calls) == 1
    assert len({r[3] for r in results}) == 1


def test_screening_is_computed_once_for_concurrent_requests(client, job, monkeypatch):
    clear_caches(job)
    calls = []
    real = screening.compute_screening

    def counting(*args, **kwargs):
        calls.append(1)
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(screening, "compute_screening", counting)
    results = fire(client, [f"/api/jobs/{job}/screening"] * 4)
    assert [status for _, status, _, _ in results] == [200] * 4
    assert len(calls) == 1
    assert len({r[3] for r in results}) == 1


def test_no_two_dataset_contexts_are_ever_open_at_the_same_time(client, job, monkeypatch):
    """The property that prevents the native crash: netCDF4 access is serialised across all routes."""
    clear_caches(job)
    state = {"open": 0, "peak": 0}
    guard = threading.Lock()
    real_open = analysis.xr.open_dataset

    class Tracked:
        def __init__(self, dataset):
            self.dataset = dataset

        def __enter__(self):
            with guard:
                state["open"] += 1
                state["peak"] = max(state["peak"], state["open"])
            time.sleep(0.05)                 # hold the context long enough for any overlap to show
            return self.dataset.__enter__()

        def __exit__(self, *exc):
            try:
                return self.dataset.__exit__(*exc)
            finally:
                with guard:
                    state["open"] -= 1

        def __getattr__(self, name):
            return getattr(self.dataset, name)

    monkeypatch.setattr(analysis.xr, "open_dataset", lambda *a, **k: Tracked(real_open(*a, **k)))
    routes = [f"/api/jobs/{job}/{name}" for name in ("analysis", "nodes", "screening", "timeseries.csv")] * 2
    results = fire(client, routes)
    assert all(status == 200 for _, status, _, _ in results)
    assert state["peak"] >= 1, "the tracker never saw a dataset being opened"
    assert state["peak"] == 1, f"{state['peak']} dataset contexts were open at once"


def test_timeseries_download_is_a_complete_snapshot_even_while_the_cache_is_replaced(client, job):
    first = client.get(f"/api/jobs/{job}/timeseries.csv")
    assert first.status_code == 200
    expected = first.data
    first.close()
    stop = threading.Event()
    netcdf = safety()                      # resolved here: a failure inside the thread below would go unseen

    def churn():
        path = appmodule.DOWNLOADS / job / "timeseries.csv"
        while not stop.is_set():
            netcdf.atomic_write_bytes(path, expected)      # a replacement racing the reads below

    thread = threading.Thread(target=churn)
    thread.start()
    try:
        for _ in range(25):
            response = client.get(f"/api/jobs/{job}/timeseries.csv")
            assert response.status_code == 200
            assert response.data == expected
            response.close()
    finally:
        stop.set()
        thread.join(timeout=10)


def test_publication_leaves_no_temporary_files_and_the_csv_is_published_before_the_json(client, job, monkeypatch):
    clear_caches(job)
    order = []
    netcdf = safety()
    real_replace = os.replace

    def spying_replace(src, dst, *args, **kwargs):
        order.append(Path(dst).name)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(netcdf.os, "replace", spying_replace)
    assert client.get(f"/api/jobs/{job}/analysis").status_code == 200
    assert order.index("timeseries.csv") < order.index("analysis.json")
    leftovers = [p.name for p in (appmodule.DOWNLOADS / job).iterdir() if p.suffix == ".tmp" or p.name.startswith("tmp")]
    assert leftovers == []


# --- 5. responsiveness ------------------------------------------------------------------------------

def test_status_polling_answers_while_an_analysis_holds_the_lock(client, job, monkeypatch):
    clear_caches(job)
    started, release = threading.Event(), threading.Event()
    real = appmodule.analyse

    def blocking(*args, **kwargs):
        started.set()
        assert release.wait(timeout=60)
        return real(*args, **kwargs)

    monkeypatch.setattr(appmodule, "analyse", blocking)
    holder = threading.Thread(target=lambda: appmodule.app.test_client().get(f"/api/jobs/{job}/analysis"))
    holder.start()
    try:
        assert started.wait(timeout=30)
        began = time.monotonic()
        listing = client.get("/api/jobs")
        status = client.get(f"/api/jobs/{job}")
        elapsed = time.monotonic() - began
        assert listing.status_code == 200 and status.status_code == 200
        assert elapsed < 2.0, f"polling took {elapsed:.1f} s while an analysis was running"
    finally:
        release.set()
        holder.join(timeout=60)
