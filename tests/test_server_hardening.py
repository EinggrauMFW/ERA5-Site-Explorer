"""Offline acceptance tests derived from WP12_server_hardening.md.

Orchestrator decisions: the old reload test contradicts the new read-only default;
required persisted keys are not enumerated (here: id, status, log); JSON numbers
exclude strings and booleans, explicit null is a wrong type, omitted options stay
optional. Origin tests use explicit identical ports, avoiding unspecified default
port canonicalisation. Only create_job currently consumes JSON in app.py; plugin
JSON routes are outside that wording. CSS checks verify the specified author rules,
not appearance or the otherwise unspecified promise of no layout changes.
"""

import ast
import inspect
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
import xarray as xr

import app as appmodule
import devices_catalogue
import fetch_era5_waves as fetcher
from tests.test_analysis import wave_dataset


ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "123456abcdef"
PAYLOAD = {"latitude": 0.0, "longitude": 95.0, "buffer": 0.5,
           "product": "single-levels", "start": "2020-04-01", "end": "2020-04-02",
           "time_step": 6, "expver": "auto"}
DATA_ROUTES = ["analysis", "nodes", "timeseries.csv", "screening", "screening.csv",
               "longterm?bootstrap=0", "export", "export/report.md",
               "export/tables/monthly.csv", "export/figures/rose.svg", "export/bundle.zip"]


@pytest.fixture(autouse=True)
def isolated_app(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads))
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path / "devices"))
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(appmodule, "DOWNLOADS", downloads)
    monkeypatch.setattr(appmodule, "jobs", {})
    monkeypatch.setattr(appmodule, "processes", {})
    monkeypatch.setattr(appmodule.app, "testing", True)
    # Exercise HTTP error handling, including 500, instead of propagating exceptions.
    monkeypatch.setitem(appmodule.app.config, "PROPAGATE_EXCEPTIONS", False)
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    monkeypatch.setattr(fetcher, "latest_available", lambda *args, **kwargs: fetcher.dt.date(2026, 1, 1))

    def offline(*args, **kwargs):
        raise AssertionError("WP12 acceptance tests must never contact CDS or the network")

    monkeypatch.setattr(fetcher, "fetch_json", offline)
    return submitted


@pytest.fixture
def client(isolated_app):
    with appmodule.app.test_client() as test_client:
        test_client.submitted = isolated_app
        yield test_client


def assert_error(response, status):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.is_json, "The error response must be JSON"
    body = response.get_json()
    assert isinstance(body, dict) and set(body) == {"error"}, body
    assert isinstance(body["error"], str) and body["error"].strip(), body
    assert "Traceback" not in body["error"]
    return body["error"]


def stored_job(status="complete", job_id=JOB_ID):
    folder = appmodule.DOWNLOADS / job_id
    folder.mkdir()
    record = {"id": job_id, "status": status, "log": [], "created": "2020-04-01T00:00:00+00:00",
              "return_code": None, "dry_run": False, "probe": False, **PAYLOAD}
    (folder / "job.json").write_text(json.dumps(record), encoding="utf-8")
    appmodule.jobs[job_id] = {**record, "directory": folder}
    return folder, record


def snapshot(folder):
    return {str(path.relative_to(folder)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in folder.rglob("*") if path.is_file()}


def require_load_flag():
    assert hasattr(appmodule, "load_jobs"), "app.load_jobs() is required by the spec"
    assert "mark_interrupted" in inspect.signature(appmodule.load_jobs).parameters, (
        "The spec requires load_jobs(mark_interrupted=False), callable with either boolean")


def require_main():
    assert hasattr(appmodule, "main"), "app.main() is required by the spec"
    assert callable(appmodule.main), "app.main must be callable"


@pytest.fixture
def occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        assert port != 5000, "Use an ephemeral port, never port 5000 (the user's running app)"
        yield port


def child_env(monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    return dict(os.environ)


def second_copy(env):
    try:
        return subprocess.run([sys.executable, str(ROOT / "app.py")], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail("The second copy must reject an occupied port and exit, not start a server")


def configured_client(monkeypatch):
    """Read environment at import, allowing either startup or request-time config."""
    spec = importlib.util.spec_from_file_location("wp12_configured_app", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    monkeypatch.setattr(module.executor, "submit", lambda *args, **kwargs: None)
    return module.app.test_client()


# 1. Host and Origin are checked before dispatch, even for non-API/error routes.

@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("path", ["/api/jobs", "/", "/static/plugins/devices.css", "/missing"])
def test_foreign_host_is_rejected_before_every_route_and_method(client, method, path):
    response = client.open(path, method=method, headers={"Host": "evil.example:5099"}, json=PAYLOAD)
    assert response.status_code == 403
    if method == "HEAD":
        # HTTP HEAD cannot carry a body; its GET counterpart checks the JSON contract.
        assert response.mimetype == "application/json"
        assert response.data == b""
    else:
        assert_error(response, 403)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST:5099", "127.0.0.1", "127.0.0.1:65535",
                                 "[::1]", "[::1]:5099"])
def test_loopback_hosts_with_any_port_and_case_remain_allowed(client, host):
    response = client.get("/api/jobs", headers={"Host": host})
    assert response.status_code == 200 and response.get_json() == []


@pytest.mark.parametrize("host", ["localhost.evil.example", "evil.localhost", "127.0.0.1.evil.example",
                                 "127.0.0.2", "[::2]"])
def test_lookalike_hosts_and_other_loopback_addresses_are_rejected(client, host):
    assert_error(client.get("/api/jobs", headers={"Host": host}), 403)


def test_missing_host_header_is_rejected(client):
    assert_error(client.get("/api/jobs", environ_overrides={"HTTP_HOST": ""}), 403)
    with appmodule.app.test_request_context("/api/jobs") as context:
        context.request.environ.pop("HTTP_HOST", None)
        assert_error(appmodule.app.make_response(appmodule.app.full_dispatch_request()), 403)


@pytest.mark.parametrize("setting", [None, "", ",,"])
def test_unset_or_empty_allowed_hosts_adds_no_host(client, monkeypatch, setting):
    if setting is not None:
        monkeypatch.setenv("ALLOWED_HOSTS", setting)
    with configured_client(monkeypatch) as configured:
        assert_error(configured.get("/api/jobs", headers={"Host": "other.example"}), 403)


@pytest.mark.parametrize("host", ["office.example", "OFFICE.EXAMPLE:5099", "second.example:61000"])
def test_allowed_hosts_comma_list_is_case_insensitive_and_allows_any_port(client, monkeypatch, host):
    monkeypatch.setenv("ALLOWED_HOSTS", "Office.Example,second.example")
    with configured_client(monkeypatch) as configured:
        assert configured.get("/api/jobs", headers={"Host": host}).status_code == 200
        assert_error(configured.get("/api/jobs", headers={"Host": "unlisted.example"}), 403)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("origin", ["http://evil.example:5099", "https://localhost:5099",
                                   "http://127.0.0.1:5099", "http://localhost:5100", "null", ""])
def test_mutating_methods_reject_foreign_scheme_host_port_or_invalid_origin(client, method, origin):
    assert_error(client.open("/api/jobs", method=method, headers={
        "Host": "localhost:5099", "Origin": origin}, json=PAYLOAD), 403)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("method,expected", [("POST", 202), ("PUT", 405), ("PATCH", 405), ("DELETE", 405)])
@pytest.mark.parametrize("origin", [None, "http://localhost:5099"])
def test_mutating_methods_allow_absent_or_same_origin(client, method, expected, origin):
    headers = {"Host": "localhost:5099"}
    if origin is not None:
        headers["Origin"] = origin
    assert client.open("/api/jobs", method=method, headers=headers, json=PAYLOAD).status_code == expected


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("origin", ["http://evil.example", "null", ""])
def test_get_and_head_ignore_origin_for_allowed_host(client, method, origin):
    assert client.open("/api/jobs", method=method, headers={"Origin": origin}).status_code == 200


@pytest.mark.parametrize("scheme,host", [("http", "127.0.0.1:5099"), ("http", "[::1]:5099"),
                                         ("https", "localhost:5099"), ("http", "office.example:5099")])
def test_same_origin_uses_actual_request_scheme_and_host_including_configured_hosts(monkeypatch, scheme, host):
    monkeypatch.setenv("ALLOWED_HOSTS", "office.example")
    with configured_client(monkeypatch) as configured:
        assert configured.post("/api/jobs", base_url=f"{scheme}://{host}",
                               headers={"Origin": f"{scheme}://{host}"}, json=PAYLOAD).status_code == 202


def test_allowed_hosts_extra_does_not_waive_cross_origin_rejection(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "office.example")
    with configured_client(monkeypatch) as configured:
        assert_error(configured.post("/api/jobs", base_url="http://office.example:5099",
                                     headers={"Origin": "http://evil.example:5099"}, json=PAYLOAD), 403)


def test_flask_default_localhost_client_can_still_create_and_list_jobs(client):
    response = client.post("/api/jobs", json=PAYLOAD)
    assert response.status_code == 202
    assert len(client.submitted) == 1
    assert client.get("/api/jobs").get_json()[0]["id"] == response.get_json()["id"]


def test_allowed_hosts_is_documented_in_env_example():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*(?:#\s*)?ALLOWED_HOSTS\s*=", text), "Document ALLOWED_HOSTS in .env.example"


def test_readme_security_section_documents_allowed_hosts_and_local_machine_only():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = re.search(r"(?ms)^## Jobs, files and security\s*\n(.*?)(?=^## |\Z)", text)
    assert section, 'Keep the README section "Jobs, files and security"'
    assert "ALLOWED_HOSTS" in section[1], "Document ALLOWED_HOSTS in that section"
    assert re.search(r"(?i)(local machine only|only (?:for |on )?(?:the |your )?(?:local )?machine|"
                     r"(?:local|your) machine[^.\n]*only)", section[1]), "Say the app is for the local machine only"


# 2. Read-only import/reload and the explicit executable startup contract.

@pytest.mark.parametrize("status", ["queued", "running", "complete", "failed", "cancelled"])
def test_import_app_never_changes_download_files_or_job_status(monkeypatch, status):
    folder, _ = stored_job(status)
    (folder / "sentinel.bin").write_bytes(b"untouched data")
    before = snapshot(appmodule.DOWNLOADS)
    result = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT,
                            env=child_env(monkeypatch), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert snapshot(appmodule.DOWNLOADS) == before, "Import must not rewrite any downloads file"


@pytest.mark.parametrize("status", ["queued", "running", "complete"])
@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit-false"])
def test_load_jobs_default_and_false_preserve_status_log_and_disk(status, explicit):
    require_load_flag()
    _, record = stored_job(status)
    before = snapshot(appmodule.DOWNLOADS)
    appmodule.jobs.clear()
    if explicit:
        appmodule.load_jobs(mark_interrupted=False)
    else:
        appmodule.load_jobs()
    loaded = appmodule.jobs[JOB_ID]
    assert loaded["status"] == status and loaded["log"] == record["log"]
    assert snapshot(appmodule.DOWNLOADS) == before


@pytest.mark.parametrize("status", ["queued", "running"])
def test_load_jobs_true_marks_each_interrupted_status_failed_and_persists_restart_log(status):
    require_load_flag()
    folder, _ = stored_job(status)
    appmodule.jobs.clear()
    appmodule.load_jobs(mark_interrupted=True)
    loaded = appmodule.jobs[JOB_ID]
    persisted = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert loaded["status"] == persisted["status"] == "failed"
    assert loaded["log"] == persisted["log"]
    assert any("restart" in line.lower() or "interrupt" in line.lower() for line in persisted["log"])


@pytest.mark.parametrize("status", ["complete", "failed", "cancelled"])
def test_load_jobs_true_does_not_rewrite_finished_or_inactive_jobs(status):
    require_load_flag()
    stored_job(status)
    before = snapshot(appmodule.DOWNLOADS)
    appmodule.jobs.clear()
    appmodule.load_jobs(mark_interrupted=True)
    assert appmodule.jobs[JOB_ID]["status"] == status
    assert snapshot(appmodule.DOWNLOADS) == before


def test_main_returns_one_with_exact_stdout_on_occupied_port_without_loading_or_serving(
        occupied_port, monkeypatch, capsys):
    require_main()
    stored_job("queued")
    before = snapshot(appmodule.DOWNLOADS)
    monkeypatch.setenv("PORT", str(occupied_port))
    calls = []
    monkeypatch.setattr(appmodule, "load_jobs", lambda *args, **kwargs: calls.append("load"))
    monkeypatch.setattr(appmodule.app, "run", lambda *args, **kwargs: calls.append("run"))
    result = appmodule.main()
    assert type(result) is int and result == 1
    assert capsys.readouterr().out == (
        f"error: port {occupied_port} is already in use (another copy of the app?). Stop it or set PORT.\n")
    assert calls == [] and snapshot(appmodule.DOWNLOADS) == before


def test_executable_second_copy_exits_one_with_exact_error_and_does_not_rewrite_jobs(occupied_port, monkeypatch):
    require_main()
    stored_job("running")
    before = snapshot(appmodule.DOWNLOADS)
    monkeypatch.setenv("PORT", str(occupied_port))
    result = second_copy(child_env(monkeypatch))
    assert result.returncode == 1, result.stderr
    assert result.stdout == (
        f"error: port {occupied_port} is already in use (another copy of the app?). Stop it or set PORT.\n")
    assert "Traceback" not in result.stderr
    assert snapshot(appmodule.DOWNLOADS) == before


@pytest.mark.parametrize("use_default", [False, True], ids=["PORT-env", "default-5000-mocked"])
def test_main_checks_loopback_port_before_marking_jobs_then_runs_threaded_and_returns_zero(monkeypatch, use_default):
    require_main()
    require_load_flag()
    events = []
    port = 5000 if use_default else 55123
    if use_default:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", str(port))

    class Probe:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def settimeout(self, *args):
            pass

        def connect_ex(self, address):
            assert address == ("127.0.0.1", port)
            events.append("checked-free-port")
            return 10061  # Connection refused on Windows: no listener.

        def close(self):
            pass

    original_socket = socket.socket
    monkeypatch.setattr(socket, "socket", Probe)
    # Also support an implementation that imported the constructor directly.
    for name, value in list(vars(appmodule).items()):
        if value is original_socket:
            monkeypatch.setattr(appmodule, name, Probe)

    def load(mark_interrupted=False):
        assert events == ["checked-free-port"]
        assert mark_interrupted is True
        events.append("marked-interrupted")

    def run(*args, **kwargs):
        assert events == ["checked-free-port", "marked-interrupted"]
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == port
        assert kwargs.get("threaded") is True
        events.append("server-stopped")

    monkeypatch.setattr(appmodule, "load_jobs", load)
    monkeypatch.setattr(appmodule.app, "run", run)
    result = appmodule.main()
    assert type(result) is int and result == 0
    assert events == ["checked-free-port", "marked-interrupted", "server-stopped"]


def test_main_module_guard_exits_with_main_return_value():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    guards = [node for node in tree.body if isinstance(node, ast.If)
              and isinstance(node.test, ast.Compare)
              and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
              and any(isinstance(value, ast.Constant) and value.value == "__main__"
                      for value in node.test.comparators)]
    assert len(guards) == 1
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == "sys"
               and node.func.attr == "exit" and len(node.args) == 1
               and isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Name)
               and node.args[0].func.id == "main" for node in ast.walk(guards[0])), (
                   'The __main__ block must use sys.exit(main())')


def test_real_server_on_scratch_high_port_rejects_foreign_host_and_second_copy(monkeypatch):
    require_main()
    # Reserve a high port ourselves before giving it to the child server.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    assert port != 5000
    monkeypatch.setenv("PORT", str(port))
    env = child_env(monkeypatch)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with subprocess.Popen([sys.executable, str(ROOT / "app.py")], cwd=ROOT, env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as server:
        try:
            deadline = time.monotonic() + 20
            while True:
                assert server.poll() is None, "The scratch server stopped before accepting requests"
                try:
                    with opener.open(f"http://127.0.0.1:{port}/api/jobs", timeout=0.5) as response:
                        assert response.status == 200
                    break
                except (urllib.error.URLError, TimeoutError):
                    assert time.monotonic() < deadline, "The scratch server never became ready"
                    time.sleep(0.05)
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/jobs", headers={"Host": "evil.example"})
            try:
                response = opener.open(request, timeout=2)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                assert response.status == 403
                body = json.loads(response.read())
                assert isinstance(body.get("error"), str) and body["error"]
            second = second_copy(env)
            assert second.returncode == 1, second.stderr
            assert second.stdout == (
                f"error: port {port} is already in use (another copy of the app?). Stop it or set PORT.\n")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


# 3. JSON type validation at the HTTP boundary, before normalise_options.

@pytest.mark.parametrize("body", [[1], "x", 5, [], None, True], ids=["list-one", "string", "integer", "empty-list", "null", "boolean"])
def test_non_object_json_body_is_400_json_without_creating_job(client, body):
    assert_error(client.post("/api/jobs", data=json.dumps(body), content_type="application/json"), 400)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("raw", ['{"latitude":', '{bad', ''])
def test_truncated_invalid_or_empty_json_body_is_400_json_without_creating_job(client, raw):
    assert_error(client.post("/api/jobs", data=raw, content_type="application/json"), 400)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("field", ["groups", "params"])
@pytest.mark.parametrize("bad", [5, "a", [5], None, {}, ["waves", 5]],
                         ids=["integer", "string", "integer-element", "null", "object", "mixed-elements"])
def test_groups_and_params_require_lists_of_strings_for_every_product(client, field, bad):
    # Even an option ignored by the selected product must not evade type checks.
    assert_error(client.post("/api/jobs", json={**PAYLOAD, "product": "wave-spectra", field: bad}), 400)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("field,product", [("groups", "single-levels"), ("params", "mars-surface")])
@pytest.mark.parametrize("bad", [5, "a", [5]], ids=["integer", "string", "integer-element"])
def test_used_groups_and_params_reject_each_spec_named_bad_type(client, field, product, bad):
    assert_error(client.post("/api/jobs", json={**PAYLOAD, "product": product, field: bad}), 400)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("field", ["product", "expver", "start", "end"])
@pytest.mark.parametrize("bad", [5, [], {}, None, True], ids=["integer", "list", "object", "null", "boolean"])
def test_product_expver_start_and_end_require_strings(client, field, bad):
    assert_error(client.post("/api/jobs", json={**PAYLOAD, field: bad}), 400)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


@pytest.mark.parametrize("field", ["latitude", "longitude", "buffer", "time_step"])
@pytest.mark.parametrize("bad", ["6", "x", [], {}, None, True],
                         ids=["numeric-string", "nonnumeric-string", "list", "object", "null", "boolean"])
def test_latitude_longitude_buffer_and_time_step_require_json_numbers(client, field, bad):
    assert_error(client.post("/api/jobs", json={**PAYLOAD, field: bad}), 400)
    assert client.submitted == [] and snapshot(appmodule.DOWNLOADS) == {}


def test_invalid_option_types_are_rejected_before_calling_fetcher_normalise_options(client, monkeypatch):
    called = []

    def normalise(*args, **kwargs):
        called.append(True)
        raise AssertionError("Wrong types must be rejected before fetcher.normalise_options")

    monkeypatch.setattr(fetcher, "normalise_options", normalise)
    assert_error(client.post("/api/jobs", json={**PAYLOAD, "groups": [5]}), 400)
    assert called == []


@pytest.mark.parametrize("product,options", [("single-levels", {"groups": ["waves"]}),
                                            ("mars-surface", {"params": ["167.128"]}),
                                            ("wave-spectra", {"expver": "1"})])
def test_valid_string_lists_strings_and_numbers_still_create_jobs(client, product, options):
    assert client.post("/api/jobs", json={**PAYLOAD, "product": product, **options}).status_code == 202
    assert len(client.submitted) == 1


def test_omitted_optional_fields_still_use_defaults_and_empty_string_lists_are_valid(client):
    minimal = {key: PAYLOAD[key] for key in ("latitude", "longitude", "start", "end")}
    assert client.post("/api/jobs", json=minimal).status_code == 202
    assert client.post("/api/jobs", json={**PAYLOAD, "groups": [], "params": []}).status_code == 202


@pytest.mark.parametrize("field,value", [("latitude", 0), ("longitude", 95), ("buffer", 1), ("time_step", 6.0)])
def test_json_integer_and_float_numbers_remain_allowed(client, field, value):
    assert client.post("/api/jobs", json={**PAYLOAD, field: value}).status_code == 202


# 4. Bad records cannot hide good records; data errors must be safe to show publicly.

@pytest.mark.parametrize("raw", ['[1]', '"x"', '5', 'null', 'true', '{}'],
                         ids=["list", "string", "integer", "null", "boolean", "empty-object"])
@pytest.mark.parametrize("mark", [None, False, True], ids=["default", "read-only", "startup"])
def test_non_object_or_empty_job_record_is_logged_skipped_and_does_not_hide_valid_job(caplog, raw, mark):
    bad_folder, _ = stored_job("complete", "000000000001")
    (bad_folder / "job.json").write_text(raw, encoding="utf-8")
    stored_job("complete")
    appmodule.jobs.clear()
    before = snapshot(appmodule.DOWNLOADS)
    if mark is not None:
        require_load_flag()
    with caplog.at_level(logging.DEBUG):
        try:
            if mark is None:
                appmodule.load_jobs()
            else:
                appmodule.load_jobs(mark_interrupted=mark)
        except Exception as error:
            pytest.fail(f"Malformed job records must be skipped, not raise {type(error).__name__}: {error}")
    assert set(appmodule.jobs) == {JOB_ID}
    assert caplog.records, "Skipping a non-object or missing-key job requires a logging line"
    assert snapshot(appmodule.DOWNLOADS) == before


@pytest.mark.parametrize("missing", ["id", "status", "log"])
@pytest.mark.parametrize("mark", [None, False, True], ids=["default", "read-only", "startup"])
def test_job_record_missing_required_key_is_logged_skipped_and_does_not_hide_valid_job(caplog, missing, mark):
    bad_folder, record = stored_job("complete", "000000000001")
    record.pop(missing)
    (bad_folder / "job.json").write_text(json.dumps(record), encoding="utf-8")
    stored_job("complete")
    appmodule.jobs.clear()
    if mark is not None:
        require_load_flag()
    with caplog.at_level(logging.DEBUG):
        try:
            if mark is None:
                appmodule.load_jobs()
            else:
                appmodule.load_jobs(mark_interrupted=mark)
        except Exception as error:
            pytest.fail(f"Missing {missing} must be skipped, not raise {type(error).__name__}: {error}")
    assert set(appmodule.jobs) == {JOB_ID}
    assert caplog.records, "Skipping a missing-key job requires a logging line"


@pytest.mark.parametrize("raw", ['{"id":', '{bad'])
def test_truncated_or_invalid_job_json_keeps_being_skipped_and_valid_job_loads(raw):
    bad_folder, _ = stored_job("complete", "000000000001")
    (bad_folder / "job.json").write_text(raw, encoding="utf-8")
    stored_job("complete")
    appmodule.jobs.clear()
    appmodule.load_jobs()
    assert set(appmodule.jobs) == {JOB_ID}


def test_import_with_non_object_job_record_still_succeeds(monkeypatch):
    folder, _ = stored_job()
    (folder / "job.json").write_text('[1]', encoding="utf-8")
    result = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT,
                            env=child_env(monkeypatch), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, "A malformed saved record must never stop startup: " + result.stderr


def device_entry(name="Good device"):
    return {"name": name, "period_type": "Te", "hs_m": [1.0, 2.0], "period_s": [4.0, 5.0],
            "power_kw": [[10, 20], [30, 40]]}


def catalogue_file(tmp_path, entries):
    folder = tmp_path / "catalogue"
    folder.mkdir()
    (folder / "devices.json").write_text(json.dumps(entries), encoding="utf-8")
    return folder


def test_catalogue_400_digit_integer_cell_is_warned_and_skipped_without_hiding_valid_device(tmp_path):
    bad = device_entry("Bad giant device")
    bad["power_kw"][0][0] = int("9" * 400)
    folder = catalogue_file(tmp_path, {"giant-device": bad, "good-device": device_entry()})
    try:
        cat = devices_catalogue.load_catalogue(str(folder))
    except Exception as error:
        pytest.fail(f"400-digit cells must not abort catalogue loading: {type(error).__name__}: {error}")
    ids = {device.id for device in cat.devices}
    assert "good-device" in ids and "giant-device" not in ids and "synthetic-example" in ids
    assert any("giant-device" in warning for warning in cat.warnings)


@pytest.mark.parametrize("error_type", [ValueError, TypeError, OverflowError])
def test_catalogue_validation_exception_is_caught_per_entry_and_warning_names_device(tmp_path, monkeypatch, error_type):
    folder = catalogue_file(tmp_path, {"bad-device": device_entry(), "good-device": device_entry()})
    original = devices_catalogue._entry_to_device

    def validate(device_id, entry):
        if device_id == "bad-device":
            raise error_type("cell validation failed")
        return original(device_id, entry)

    monkeypatch.setattr(devices_catalogue, "_entry_to_device", validate)
    try:
        cat = devices_catalogue.load_catalogue(str(folder))
    except error_type as error:
        pytest.fail(f"{error_type.__name__} must be caught per entry: {error}")
    assert "good-device" in {device.id for device in cat.devices}
    assert "bad-device" not in {device.id for device in cat.devices}
    assert any("bad-device" in warning for warning in cat.warnings)


def test_catalogue_unrelated_runtime_error_is_not_swallowed_by_bare_exception(tmp_path, monkeypatch):
    folder = catalogue_file(tmp_path, {"bad-device": device_entry()})

    def unexpected(*args):
        raise RuntimeError("programming fault")

    monkeypatch.setattr(devices_catalogue, "_entry_to_device", unexpected)
    with pytest.raises(RuntimeError, match="programming fault"):
        devices_catalogue.load_catalogue(str(folder))
    tree = ast.parse((ROOT / "devices_catalogue.py").read_text(encoding="utf-8"))
    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        assert handler.type is not None, "The catalogue must not catch bare exceptions"
        assert not any(isinstance(node, ast.Name) and node.id in {"Exception", "BaseException"}
                       for node in ast.walk(handler.type)), "Catch named validation exceptions, not Exception"


@pytest.mark.parametrize("error_type", [ValueError, TypeError, OverflowError])
def test_catalogue_csv_validation_failure_warns_and_keeps_other_devices(tmp_path, monkeypatch, error_type):
    folder = tmp_path / "catalogue-csv"
    folder.mkdir()
    (folder / "bad-device.csv").write_text("bad matrix", encoding="utf-8")
    (folder / "good-device.csv").write_text("Hm0,4,5\n1,10,20\n2,30,40\n", encoding="utf-8")
    original = devices_catalogue.parse_power_matrix

    def parse(text, *args, **kwargs):
        if text == "bad matrix":
            raise error_type("matrix validation failed")
        return original(text, *args, **kwargs)

    monkeypatch.setattr(devices_catalogue, "parse_power_matrix", parse)
    try:
        cat = devices_catalogue.load_catalogue(str(folder))
    except error_type as error:
        pytest.fail(f"{error_type.__name__} must be caught per CSV entry: {error}")
    assert "good-device" in {device.id for device in cat.devices}
    assert "bad-device" not in {device.id for device in cat.devices}
    assert any("bad-device" in warning for warning in cat.warnings)


@pytest.mark.parametrize("route", DATA_ROUTES)
@pytest.mark.parametrize("error_type", [OSError, ValueError])
@pytest.mark.parametrize("second_file", [False, True], ids=["only-file", "second-file"])
def test_data_reader_errors_on_each_route_return_422_filename_only(client, monkeypatch, route, error_type, second_file):
    folder, _ = stored_job()
    target = folder / "era5_2022-01.nc"
    target.write_bytes(b"reader failure is injected below")
    if second_file:
        wave_dataset().to_netcdf(folder / "era5_2021-12.nc")
    seen = []
    original = xr.open_dataset

    def unreadable(path, *args, **kwargs):
        seen.append(Path(path))
        if Path(path) == target:
            raise error_type(f"failed reading {target.resolve()}: private reader details")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(xr, "open_dataset", unreadable)
    response = client.get(f"/api/jobs/{JOB_ID}/{route}")
    assert target in seen, "The request must reach the unreadable file's NetCDF reader"
    message = assert_error(response, 422)
    assert target.name in message, "The safe error must identify the unreadable data file by name"
    assert str(folder) not in message and str(target.resolve()) not in message
    assert "private reader details" not in message
    assert not re.search(r"(?:[A-Za-z]:[\\/]|/[^\s]+/|\\\\)", message), message
    assert any(word in message.lower() for word in ("read", "corrupt", "incomplete", "unreadable")), message


@pytest.mark.parametrize("route", DATA_ROUTES)
def test_actual_corrupt_netcdf_on_each_route_returns_422_with_filename_only(client, route):
    folder, _ = stored_job()
    target = folder / "era5_2022-01.nc"
    target.write_bytes(b"not a NetCDF file\x00\xff")
    message = assert_error(client.get(f"/api/jobs/{JOB_ID}/{route}"), 422)
    assert target.name in message
    assert str(folder) not in message and str(target.resolve()) not in message


@pytest.mark.parametrize("route", DATA_ROUTES)
def test_valid_netcdf_remains_readable_on_each_route(client, route):
    folder, _ = stored_job()
    wave_dataset(hours=96).to_netcdf(folder / "era5_2022-01.nc")
    response = client.get(f"/api/jobs/{JOB_ID}/{route}")
    assert response.status_code == 200, response.get_data(as_text=True)[:500]


def test_invalid_node_query_remains_specific_422_and_is_not_mislabelled_as_corrupt_data(client):
    folder, _ = stored_job()
    wave_dataset().to_netcdf(folder / "era5_2022-01.nc")
    message = assert_error(client.get(f"/api/jobs/{JOB_ID}/analysis?node_lat=abc&node_lon=95"), 422)
    assert "number" in message.lower()
    assert "corrupt" not in message.lower() and "era5_2022-01.nc" not in message


# 5. Static author-rule checks; browser appearance remains the orchestrator's job.

def css_rules():
    text = (ROOT / "static/plugins/devices.css").read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return [(re.sub(r"\s+", "", selector), dict(re.findall(r"([\w-]+)\s*:\s*([^;{}]+)", body)))
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text)]


def test_closed_device_picker_has_explicit_display_none_author_rule():
    rules = css_rules()
    assert any(selector == ".device-picker-dialog:not([open])" and props.get("display", "").strip() == "none"
               for selector, props in rules), "Closed device picker needs an explicit display: none rule"


def test_device_picker_display_flex_applies_only_to_open_dialog():
    rules = css_rules()
    flex_rules = [(selector, props) for selector, props in rules
                  if "device-picker-dialog" in selector and props.get("display", "").strip() == "flex"]
    assert flex_rules, "The open mobile dialog must retain display: flex"
    assert all(selector == ".device-picker-dialog[open]" for selector, _ in flex_rules), (
        "display: flex must apply only to .device-picker-dialog[open]")


def test_closed_picker_fix_does_not_hide_open_dialog_or_other_picker_elements():
    for selector, props in css_rules():
        if props.get("display", "").strip() == "none" and "device-picker" in selector:
            assert selector == ".device-picker-dialog:not([open])", (
                "The explicit hiding rule must affect only the closed dialog")
