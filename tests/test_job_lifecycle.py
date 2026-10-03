"""Offline acceptance contract for docs/work-packages/WP13_job_lifecycle.md.

Interpretations requiring orchestrator review:
* A distinct save failure means (exception class, message), across one run.
* A new/legacy job starts at generation 1; an existing generation increments by 1.
* Missing/incomplete credentials include CDS's plain configuration Exception,
  FileNotFoundError, and missing URL/key messages. Other constructor errors propagate.
* Licence guidance applies to a refused primary download (not a successful licence
  mention). The existing optional-bathymetry/probe failure policy is left open.
* load_jobs(mark_interrupted=...) is a WP12 prerequisite explicitly named by WP13.

Only this file is an artifact. All data and subprocess markers live in tmp_path.
No subprocess here serves HTTP, so none opens a listening port.
"""

import ctypes
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

import pytest

import app as appmodule
import fetch_era5_waves as fetcher


PAYLOAD = {"latitude": 6, "longitude": 95, "buffer": 0.5,
           "product": "single-levels", "start": "2020-04-01", "end": "2020-04-01"}
CREDENTIALS = ('error: CDS credentials were not found. Create ~/.cdsapirc as described in the '
               'README section "CDS API access", then run again.')
LICENCE = ('error: CDS says a licence has not been accepted for this dataset. Open the dataset '
           'page on the CDS website, accept its terms while logged in, then run again.')


@pytest.fixture(autouse=True)
def isolated_lifecycle(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    home = tmp_path / "private-home"
    home.mkdir()
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads))
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path / "devices"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CDSAPI_RC", str(home / ".cdsapirc"))
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setattr(appmodule, "DOWNLOADS", downloads)
    monkeypatch.setattr(appmodule, "jobs", {})
    monkeypatch.setattr(appmodule, "processes", {})
    monkeypatch.setattr(fetcher, "setup_logging", lambda: None)

    def offline(*args, **kwargs):
        raise OSError("offline acceptance test")

    monkeypatch.setattr(fetcher, "fetch_json", offline)


@pytest.fixture
def client(monkeypatch):
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    monkeypatch.setattr(appmodule.app, "testing", True)
    with appmodule.app.test_client() as result:
        result.submitted = submitted
        yield result


def job_record(status="queued", **extra):
    folder = appmodule.DOWNLOADS / "abcde1234567"
    folder.mkdir(exist_ok=True)
    job = {**PAYLOAD, "id": folder.name, "directory": folder, "status": status,
           "created": "2020-04-01T00:00:00+00:00", "log": [], "return_code": None,
           "dry_run": False, "probe": False, "run": 1, **extra}
    appmodule.jobs[job["id"]] = job
    appmodule.save_job(job)
    return job


def disk_job(job):
    return json.loads((job["directory"] / "job.json").read_text(encoding="utf-8"))


class FakeProcess:
    """A finite pipe; hooks run outside the app lock, at read/wait boundaries."""

    def __init__(self, lines=("first\n", "second\n"), code=0, on_read=None, on_wait=None):
        self.pid = 32101
        self.returncode = None
        self.code = code
        self.drained = []
        self.terminated = False
        self.on_wait = on_wait

        def output():
            if on_read:
                on_read()
            for line in lines:
                self.drained.append(line)
                yield line

        self.stdout = output()

    def wait(self, *args, **kwargs):
        if self.on_wait:
            self.on_wait()
        self.returncode = self.code
        return self.code

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


def use_process(monkeypatch, process):
    monkeypatch.setattr(appmodule.subprocess, "Popen", lambda *args, **kwargs: process)


def run_without_save_escape(job):
    try:
        appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    except OSError as exc:
        pytest.fail(f"WP13.1: saving job.json must not stop draining stdout: {exc}")


@pytest.mark.parametrize("error_type", [OSError, PermissionError], ids=["oserror", "permissionerror"])
@pytest.mark.parametrize("exit_code,status", [(0, "complete"), (7, "failed")], ids=["success", "child_failure"])
def test_save_error_keeps_draining_retries_and_preserves_child_final_status(monkeypatch, error_type, exit_code, status):
    job = job_record()
    lines = ["one\n", "two\n", "three\n"]
    process = FakeProcess(lines, exit_code)
    use_process(monkeypatch, process)
    real_save = appmodule.save_job
    attempts = []

    def flaky_save(record):
        attempts.append((record["status"], len(record["log"])))
        if record["status"] == "running" and len(record["log"]) == 1:
            raise error_type("scanner holds job.json")
        real_save(record)

    monkeypatch.setattr(appmodule, "save_job", flaky_save)
    run_without_save_escape(job)
    assert process.drained == lines, "Every output line must be consumed after a failed save"
    assert ("running", 2) in attempts and ("running", 3) in attempts, "Retry saving on later lines"
    assert job["status"] == status and job["return_code"] == exit_code
    assert disk_job(job)["status"] == status


def test_permission_error_from_atomic_replace_does_not_stop_output_reader(monkeypatch):
    job = job_record()
    process = FakeProcess()
    use_process(monkeypatch, process)
    real_replace = os.replace
    denied = []

    def scanner_holds_target(path, target, *args, **kwargs):
        if Path(target) == job["directory"] / "job.json" and len(job["log"]) == 1:
            denied.append(target)
            raise PermissionError("Windows scanner holds job.json")
        return real_replace(path, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", scanner_holds_target)
    run_without_save_escape(job)
    assert denied, "The test must exercise atomic replacement, not a pre-loop save"
    assert process.drained == ["first\n", "second\n"]
    assert disk_job(job)["status"] == "complete"


def test_save_failures_log_one_warning_per_distinct_failure(monkeypatch, caplog):
    job = job_record()
    process = FakeProcess([f"line {n}\n" for n in range(5)])
    use_process(monkeypatch, process)
    real_save = appmodule.save_job
    errors = [PermissionError("scanner A"), PermissionError("scanner A"),
              OSError("disk B"), OSError("disk B")]

    def flaky_save(record):
        n = len(record["log"])
        if record["status"] == "running" and 1 <= n <= 4:
            raise errors[n - 1]
        real_save(record)

    monkeypatch.setattr(appmodule, "save_job", flaky_save)
    with caplog.at_level(logging.WARNING):
        run_without_save_escape(job)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, "Repeated identical save failures warn once; distinct failures each warn"
    assert job["status"] == "complete" and len(process.drained) == 5


def test_non_oserror_in_save_is_not_silently_swallowed(monkeypatch):
    job = job_record()
    use_process(monkeypatch, FakeProcess())
    real_save = appmodule.save_job

    def programming_error(record):
        if record["log"]:
            raise ValueError("deliberate programming error")
        real_save(record)

    monkeypatch.setattr(appmodule, "save_job", programming_error)
    with pytest.raises(ValueError, match="deliberate programming error"):
        appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])


@pytest.mark.parametrize("exit_code", [0, 7], ids=["success", "failure"])
def test_running_pid_is_recorded_on_disk_and_removed_after_exit(monkeypatch, exit_code):
    job = job_record()
    observed = []
    process = FakeProcess(code=exit_code, on_read=lambda: observed.append((job.get("pid"), disk_job(job).get("pid"))))
    use_process(monkeypatch, process)
    appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    assert observed == [(process.pid, process.pid)], "Store and persist the child PID before draining output"
    assert "pid" not in job and "pid" not in disk_job(job), "Remove, rather than null, a finished PID"


def require_pid_alive():
    assert hasattr(appmodule, "pid_alive"), "app.pid_alive(pid) is required by WP13.2"
    return appmodule.pid_alive


@pytest.mark.parametrize("bad_pid", [None, "123", 1.5, [], {}, True, 0, -1],
                         ids=["none", "string", "float", "list", "dict", "bool", "zero", "negative"])
def test_invalid_pid_is_not_alive(bad_pid):
    assert require_pid_alive()(bad_pid) is False


def exited_child_pid():
    """PID of a Python child that has already exited; the context manager reaps it even if the wait times out."""
    with subprocess.Popen([sys.executable, "-c", "pass"]) as process:
        process.wait(timeout=10)
    return process.pid


def test_current_python_pid_is_alive():
    assert require_pid_alive()(os.getpid()) is True


def test_exited_python_child_pid_is_not_alive():
    assert require_pid_alive()(exited_child_pid()) is False


@pytest.mark.parametrize("code,expected", [(259, True), (0, False)], ids=["still_active_259", "exited_0"])
def test_windows_pid_liveness_uses_openprocess_and_exit_code(monkeypatch, code, expected):
    alive = require_pid_alive()
    if os.name != "nt":
        pytest.skip("Windows ctypes contract is tested on Windows")
    calls = []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def open_process(access, inherit, pid):
        calls.append(("open", pid))
        return 42

    def exit_code(handle, pointer):
        calls.append(("exit_code", handle))
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ulong))[0] = code
        return 1

    kernel = types.SimpleNamespace(OpenProcess=Function(open_process), GetExitCodeProcess=Function(exit_code),
                                   CloseHandle=Function(lambda handle: 1))
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    assert alive(32101) is expected
    assert ("open", 32101) in calls and ("exit_code", 42) in calls


def test_posix_pid_liveness_uses_signal_zero(monkeypatch):
    alive = require_pid_alive()
    calls = []
    # Restore os.name before pytest/pathlib runs: only pid_alive executes in this context.
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "posix")
        patch.setattr(sys, "platform", "linux")
        patch.setattr(os, "kill", lambda pid, signal: calls.append((pid, signal)))
        result = alive(32101)
    assert result is True and calls == [(32101, 0)]


def test_posix_nonexistent_pid_is_not_alive(monkeypatch):
    alive = require_pid_alive()

    def gone(pid, signal):
        raise ProcessLookupError("process ended")

    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "posix")
        patch.setattr(sys, "platform", "linux")
        patch.setattr(os, "kill", gone)
        result = alive(32101)
    assert result is False


def test_windows_unopenable_pid_is_not_alive(monkeypatch):
    alive = require_pid_alive()
    if os.name != "nt":
        pytest.skip("Windows ctypes contract is tested on Windows")

    class OpenProcess:
        def __call__(self, *args):
            return 0

    kernel = types.SimpleNamespace(OpenProcess=OpenProcess(), GetExitCodeProcess=lambda *args: 0,
                                   CloseHandle=lambda *args: 1)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    assert alive(32101) is False


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_resume_of_live_pid_is_409_with_exact_message_and_no_mutation(client, monkeypatch, status):
    require_pid_alive()
    job = job_record(status, pid=32101, run=8)
    before = disk_job(job)
    checked = []
    monkeypatch.setattr(appmodule, "pid_alive", lambda pid: checked.append(pid) or True)
    response = client.post(f"/api/jobs/{job['id']}/resume")
    assert response.status_code == 409
    assert response.get_json() == {"error": "A fetcher for this job (pid 32101) is still running. Wait for it to finish or stop it."}
    assert checked == [32101] and not client.submitted
    assert disk_job(job) == before and job["status"] == status and job["run"] == 8


@pytest.mark.parametrize("pid", [None, 32101, "not-an-int"], ids=["absent", "dead", "invalid"])
def test_resume_without_live_pid_remains_allowed(client, monkeypatch, pid):
    if pid is not None:
        require_pid_alive()
    job = job_record("failed", **({} if pid is None else {"pid": pid}))
    if hasattr(appmodule, "pid_alive"):
        monkeypatch.setattr(appmodule, "pid_alive", lambda value: False)
    response = client.post(f"/api/jobs/{job['id']}/resume")
    assert response.status_code == 202 and len(client.submitted) == 1


def test_build_command_passes_current_app_pid_and_preserves_download_arguments(monkeypatch):
    job = job_record()
    monkeypatch.setattr(appmodule.os, "getpid", lambda: 12345)
    command = appmodule.build_command(job)
    assert command.count("--parent-pid") == 1, "Every fetcher command must carry --parent-pid"
    assert command[command.index("--parent-pid") + 1] == "12345"
    for flag, value in [("--latitude", "6"), ("--longitude", "95"), ("--start", "2020-04-01"),
                        ("--end", "2020-04-01"), ("--output", str(job["directory"])),
                        ("--product", "single-levels")]:
        assert command[command.index(flag) + 1] == value
    assert command[0] == sys.executable and "--probe" not in command and "--dry-run" not in command


@pytest.mark.parametrize("status", ["queued", "running"])
def test_interrupted_live_pid_logs_exact_resume_block_message(tmp_path, monkeypatch, status):
    require_pid_alive()
    assert "mark_interrupted" in inspect.signature(appmodule.load_jobs).parameters, "WP12/WP13 require load_jobs(mark_interrupted=True)"
    job = job_record(status, pid=32101)
    monkeypatch.setattr(appmodule, "pid_alive", lambda pid: pid == 32101)
    appmodule.jobs.clear()
    appmodule.load_jobs(mark_interrupted=True)
    loaded = appmodule.jobs[job["id"]]
    message = "The fetcher from the earlier run (pid 32101) is still running; Resume is blocked until it exits."
    assert message in loaded["log"] and message in disk_job(job)["log"]
    assert loaded["status"] == "failed" and loaded["pid"] == 32101


def test_interrupted_dead_pid_keeps_normal_restart_message(monkeypatch):
    require_pid_alive()
    assert "mark_interrupted" in inspect.signature(appmodule.load_jobs).parameters, "WP12/WP13 require load_jobs(mark_interrupted=True)"
    job = job_record("running", pid=32101)
    monkeypatch.setattr(appmodule, "pid_alive", lambda pid: False)
    appmodule.jobs.clear()
    appmodule.load_jobs(mark_interrupted=True)
    loaded = appmodule.jobs[job["id"]]
    assert loaded["status"] == "failed" and any("Interrupted" in line for line in loaded["log"])
    assert not any("still running; Resume is blocked" in line for line in loaded["log"])


def test_load_without_marking_does_not_interrupt_or_warn_about_live_pid(monkeypatch):
    assert "mark_interrupted" in inspect.signature(appmodule.load_jobs).parameters, "WP12/WP13 require load_jobs(mark_interrupted=False)"
    job = job_record("running", pid=32101)
    appmodule.jobs.clear()
    appmodule.load_jobs(mark_interrupted=False)
    assert appmodule.jobs[job["id"]]["status"] == "running"
    assert appmodule.jobs[job["id"]]["log"] == [] and disk_job(job)["log"] == []


def test_create_initializes_and_persists_first_run_generation(client):
    response = client.post("/api/jobs", json=PAYLOAD)
    assert response.status_code == 202
    job = appmodule.jobs[response.get_json()["id"]]
    assert job.get("run") == 1, "create_job must increment the initial generation from zero to one"
    assert disk_job(job)["run"] == 1 and len(client.submitted) == 1


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_resume_increments_and_persists_existing_generation(client, status):
    job = job_record(status, run=8)
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
    assert job["run"] == 9 and disk_job(job)["run"] == 9


def test_resume_legacy_job_without_generation_starts_at_one(client):
    job = job_record("failed")
    job.pop("run")
    appmodule.save_job(job)
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
    assert job.get("run") == 1 and disk_job(job)["run"] == 1


@pytest.mark.parametrize("field", ["status", "return_code", "pid"])
@pytest.mark.parametrize("exit_code", [0, 7], ids=["old_success", "old_failure"])
def test_old_cancelled_run_cannot_overwrite_resumed_run_final_fields(client, monkeypatch, field, exit_code):
    job = job_record()
    expected = {"status": "running", "return_code": None, "pid": 32102}

    def resume_before_old_worker_finishes():
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
        # The OS child has exited, but its reader has not yet committed final status.
        process.returncode = exit_code
        job.pop("pid", None)
        assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
        job.update(expected)  # the new queued worker has now started
        appmodule.save_job(job)

    process = FakeProcess(code=exit_code, on_wait=resume_before_old_worker_finishes)
    use_process(monkeypatch, process)
    appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    assert job.get(field) == expected[field], f"Stale run must not overwrite resumed run's {field}"
    assert disk_job(job).get(field) == expected[field]


def test_cancelled_current_generation_stays_cancelled_and_removes_exited_pid(client, monkeypatch):
    job = job_record()

    def cancel():
        # Seed the specified running record to test PID removal independently of
        # the separate PID-at-start acceptance test (avoid a vacuous absence).
        job["pid"] = process.pid
        appmodule.save_job(job)
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200

    process = FakeProcess(code=7, on_wait=cancel)
    use_process(monkeypatch, process)
    appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    assert process.terminated and job["status"] == "cancelled"
    assert "pid" not in job and "pid" not in disk_job(job)


def cancel_and_resume_during_start(client, job):
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
    assert job["run"] == 2 and job["status"] == "queued"


def test_run_replaced_while_its_process_is_starting_does_not_write_pid_and_stops_the_process(client, monkeypatch):
    job = job_record()
    process = FakeProcess()

    def slow_start(*args, **kwargs):
        cancel_and_resume_during_start(client, job)
        return process

    monkeypatch.setattr(appmodule.subprocess, "Popen", slow_start)
    appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    assert process.terminated, "A superseded run must not leave its fetcher running"
    assert "pid" not in job and "pid" not in disk_job(job)
    assert job["status"] == "queued" and job["return_code"] is None and job["run"] == 2


def test_run_replaced_while_its_process_fails_to_start_does_not_fail_the_new_run(client, monkeypatch):
    job = job_record()

    def failing_start(*args, **kwargs):
        cancel_and_resume_during_start(client, job)
        raise OSError("cannot start")

    monkeypatch.setattr(appmodule.subprocess, "Popen", failing_start)
    appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    assert job["status"] == "queued" and not any("Unable to start" in line for line in job["log"])


def test_queued_cancellation_still_prevents_child_start(client, monkeypatch):
    job = job_record()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    starts = []
    monkeypatch.setattr(appmodule.subprocess, "Popen", lambda *a, **k: starts.append(a))
    appmodule.run_job(job["id"], [sys.executable, "-c", "pass"])
    assert starts == [] and job["status"] == "cancelled" and job["run"] == 1


def fetch_args(output, *extra):
    return ["--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-01",
            "--output", str(output), "--product", "single-levels", "--groups", "wind", *extra]


def stub_client(monkeypatch, constructor):
    monkeypatch.setitem(sys.modules, "cdsapi", types.SimpleNamespace(Client=constructor))


class SuccessfulClient:
    def __init__(self, **kwargs):
        pass

    def retrieve(self, dataset, request, target):
        Path(target).write_bytes(b"offline-test-data")


@pytest.mark.parametrize("kind", ["missing_file", "plain_missing", "missing_url", "missing_key"])
def test_missing_or_incomplete_cds_credentials_have_exact_guidance_exit_two_and_no_private_path(tmp_path, monkeypatch, capsys, kind):
    private = os.environ["CDSAPI_RC"]
    errors = {"missing_file": FileNotFoundError(2, "No such file or directory", private),
              "plain_missing": Exception(f"Missing/incomplete configuration file: {private}"),
              "missing_url": Exception(f"Missing/incomplete configuration file: {private}: missing url"),
              "missing_key": Exception(f"Missing/incomplete configuration file: {private}: missing key")}

    def constructor(**kwargs):
        raise errors[kind]

    stub_client(monkeypatch, constructor)
    try:
        code = fetcher.main(fetch_args(tmp_path / "out"))
    except Exception as exc:
        pytest.fail(f"WP13.4: credentials errors must return 2 with guidance, not escape as {type(exc).__name__}")
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 2 and output.splitlines().count(CREDENTIALS) == 1
    assert "Traceback" not in output and private not in output and str(tmp_path) not in output


@pytest.mark.parametrize("message", ["The required licence must be accepted before downloading.",
                                    "Required licences have not been accepted; visit the dataset page."])
def test_unaccepted_dataset_licence_prints_exact_guidance_then_cds_message_and_returns_one(tmp_path, monkeypatch, capsys, message):
    class RefusedClient(SuccessfulClient):
        def retrieve(self, dataset, request, target):
            raise Exception(message)

    stub_client(monkeypatch, RefusedClient)
    code = fetcher.main(fetch_args(tmp_path / "out"))
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 1
    assert LICENCE in output, "WP13.4 requires actionable dataset licence guidance"
    assert message in output[output.index(LICENCE) + len(LICENCE):]
    assert "Traceback" not in output


def test_other_client_constructor_error_retains_existing_exception_behaviour(tmp_path, monkeypatch):
    def constructor(**kwargs):
        raise RuntimeError("unrelated constructor defect")

    stub_client(monkeypatch, constructor)
    with pytest.raises(RuntimeError, match="unrelated constructor defect"):
        fetcher.main(fetch_args(tmp_path / "out"))


@pytest.mark.parametrize("message", ["upstream service unavailable", "Licence already accepted; upstream service unavailable"])
def test_other_request_error_still_reports_cds_message_without_credentials_or_licence_guidance(tmp_path, monkeypatch, capsys, message):
    class RefusedClient(SuccessfulClient):
        def retrieve(self, dataset, request, target):
            raise Exception(message)

    stub_client(monkeypatch, RefusedClient)
    assert fetcher.main(fetch_args(tmp_path / "out")) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert message in output and LICENCE not in output and CREDENTIALS not in output


def test_valid_client_without_local_credentials_file_can_still_download(tmp_path, monkeypatch, capsys):
    stub_client(monkeypatch, SuccessfulClient)
    assert fetcher.main(fetch_args(tmp_path / "out")) == 0
    captured = capsys.readouterr()
    assert CREDENTIALS not in captured.out + captured.err
    assert list((tmp_path / "out").glob("*.nc"))


def test_parent_pid_is_optional_for_normal_download(tmp_path, monkeypatch):
    stub_client(monkeypatch, SuccessfulClient)
    assert fetcher.main(fetch_args(tmp_path / "out")) == 0


def test_parent_pid_argument_requires_an_integer(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        fetcher.parse_args(fetch_args(tmp_path / "out", "--parent-pid", "not-an-int"))
    assert error.value.code == 2
    assert "invalid int value" in capsys.readouterr().err, "--parent-pid must be recognized and parsed as int"


@pytest.mark.parametrize("mode", ["--dry-run", "--probe"])
def test_preview_ignores_dead_parent_and_never_starts_watchdog(tmp_path, monkeypatch, mode):
    stub_client(monkeypatch, SuccessfulClient)
    monkeypatch.setattr(fetcher, "run_probe", lambda *args: [("offline probe", "accepted")])
    dead_parent_pid = exited_child_pid()

    def forbidden(*args, **kwargs):
        pytest.fail("WP13.2: --dry-run and --probe must ignore the parent watchdog")

    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(os, "_exit", forbidden)
    try:
        code = fetcher.main(fetch_args(tmp_path / "out", mode, "--parent-pid", str(dead_parent_pid)))
    except SystemExit as exc:
        pytest.fail(f"WP13.2: fetcher must accept --parent-pid in {mode} (parser exited {exc.code})")
    assert code == 0


def test_daemon_watchdog_polls_every_two_seconds_and_hard_exits_blocked_request_when_parent_dies(tmp_path):
    """Real parent death and os._exit: a blocked CDS stub cannot return normally.

    Thread/sleep instrumentation records the required daemon flag and polling
    interval without accelerating time or replacing the watchdog implementation.
    """
    marker = tmp_path / "request-blocked"
    script = r'''
import os, sys, threading, time, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import fetch_era5_waves as f
real_start = threading.Thread.start
def start(thread):
    print("THREAD_DAEMON=" + str(thread.daemon), flush=True)
    return real_start(thread)
threading.Thread.start = start
real_sleep = time.sleep
def sleep(seconds):
    if threading.current_thread() is not threading.main_thread():
        print("WATCHDOG_SLEEP=" + str(seconds), flush=True)
    return real_sleep(seconds)
time.sleep = sleep
class Client:
    def __init__(self, **kwargs): pass
    def retrieve(self, dataset, request, target):
        Path(sys.argv[2]).write_text("blocked", encoding="utf-8")
        threading.Event().wait()  # an in-flight request that never finishes
sys.modules["cdsapi"] = types.SimpleNamespace(Client=Client)
def offline(*args, **kwargs): raise OSError("offline")
f.fetch_json = offline
raise SystemExit(f.main(sys.argv[3:]))
'''
    parent = subprocess.Popen([sys.executable, "-c", "import threading; threading.Event().wait()"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = None
    try:
        child = subprocess.Popen([sys.executable, "-u", "-c", script, str(appmodule.APP_DIR), str(marker),
                                  *fetch_args(tmp_path / "out", "--parent-pid", str(parent.pid))],
                                 cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        deadline = time.monotonic() + 10
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not marker.exists():
            if child.poll() is None:
                child.kill()
            output = child.communicate(timeout=5)[0]
            pytest.fail("WP13.2: fetcher must accept --parent-pid and reach the offline blocked request.\n" + output)
        assert child.poll() is None, "A live parent must not be killed by its watchdog"
        parent.kill()  # Windows TerminateProcess, same hard-death condition as taskkill /F
        parent.communicate(timeout=5)
        started = time.monotonic()
        try:
            output = child.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            child.kill()
            output = child.communicate(timeout=5)[0]
            pytest.fail("WP13.2: orphan fetcher did not stop within 10 seconds.\n" + output)
        assert child.returncode == 3, output
        assert time.monotonic() - started < 10
        assert output.splitlines().count("parent process ended; stopping") == 1
        assert "THREAD_DAEMON=True" in output, "main must start a daemon watchdog thread"
        intervals = [float(line.split("=", 1)[1]) for line in output.splitlines() if line.startswith("WATCHDOG_SLEEP=")]
        # The 10 s exit bound above is the behaviour; if the watchdog sleeps via time.sleep it must poll at least every 2 s.
        assert all(interval <= 2 for interval in intervals), "Watchdog polls at least every 2 seconds"
    finally:
        for process in (child, parent):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
