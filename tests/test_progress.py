"""Progress lines, resuming a download, one-line logging, and the app's ETA arithmetic."""

import json
import re
import subprocess
import sys
import types

import app as appmodule
import fetch_era5_waves as fetcher
from tests.test_provenance_crosscheck import TimeLimitedCds

PROGRESS = "::progress:: "


def progress_lines(text):
    return [json.loads(line[len(PROGRESS):]) for line in text.splitlines() if line.startswith(PROGRESS)]


def hourly_spectra_args(output):
    return ["--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-30",
            "--output", str(output), "--product", "wave-spectra", "--time-step", "1", "--expver", "1"]


def use_fake_cds(monkeypatch):
    monkeypatch.setitem(sys.modules, "cdsapi", types.SimpleNamespace(Client=TimeLimitedCds))
    monkeypatch.setattr(fetcher, "fetch_json", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    TimeLimitedCds.calls, TimeLimitedCds.times = [], []


# --- fetcher -------------------------------------------------------------------

def test_progress_starts_at_zero_counts_every_request_and_ends_at_the_total(tmp_path, monkeypatch, capsys):
    use_fake_cds(monkeypatch)
    assert fetcher.main(hourly_spectra_args(tmp_path)) == 0
    lines = progress_lines(capsys.readouterr().out)
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert lines[0]["done"] == 0 and lines[0]["skipped"] == 0
    assert [line["done"] for line in lines] == list(range(len(lines)))
    assert lines[-1]["done"] == lines[-1]["total"] == len(record["requests"])
    assert all(line["skipped"] == 0 for line in lines)
    # CDS refused the first shapes, so the plan grew while the run went on: the total is re-planned
    assert lines[0]["total"] < lines[-1]["total"]
    assert all(line["total"] >= line["done"] for line in lines)


def test_a_resumed_run_replays_the_same_plan_and_downloads_nothing_twice(tmp_path, monkeypatch, capsys):
    """Skipping works by file name, so a rerun must plan the same requests as the run that wrote the files.

    The shape CDS accepts is learned during a run (the first files here hold 3 time steps, later ones 4), so
    the rerun repeats the refusals to reach the same shape. Remembering the final shape instead would plan
    different files, download the month again and leave overlapping records on disk.
    """
    use_fake_cds(monkeypatch)
    assert fetcher.main(hourly_spectra_args(tmp_path)) == 0
    first = progress_lines(capsys.readouterr().out)[-1]
    files = sorted(p.name for p in tmp_path.glob("era5-spectra_*.nc"))

    TimeLimitedCds.calls, TimeLimitedCds.times = [], []
    assert fetcher.main(hourly_spectra_args(tmp_path)) == 0
    lines = progress_lines(capsys.readouterr().out)
    assert all(steps > 4 for steps in TimeLimitedCds.times)       # only the refused shapes are sent again
    assert sorted(p.name for p in tmp_path.glob("era5-spectra_*.nc")) == files
    assert lines[-1]["done"] == lines[-1]["skipped"] == lines[-1]["total"] == first["total"]


# --- logging ---------------------------------------------------------------------

def test_a_library_message_is_printed_once_with_a_timestamp():
    script = (
        "import fetch_era5_waves as f, cdsapi\n"
        "f.setup_logging(); f.setup_logging()\n"
        "client = cdsapi.Client(url='https://cds.climate.copernicus.eu/api',\n"
        "                       key='00000000-0000-0000-0000-000000000000', progress=False, retry_max=1)\n"
        "client.info('Request ID is TEST')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=appmodule.APP_DIR, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    lines = [line for line in (result.stdout + result.stderr).splitlines() if "Request ID is TEST" in line]
    assert len(lines) == 1, result.stdout + result.stderr
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ INFO ", lines[0])


# --- the app's ETA arithmetic ------------------------------------------------------

def feed(lines_and_times):
    state, logged = None, []
    for line, now in lines_and_times:
        state, log_line = appmodule.progress_update(state, line, now)
        if log_line is not None:
            logged.append(log_line)
    return state, logged


def line(done, total, skipped=0):
    return PROGRESS + json.dumps({"done": done, "total": total, "skipped": skipped}) + "\n"


def test_progress_lines_are_not_logged_and_ordinary_lines_are():
    state, logged = feed([("Saving file\n", 0.0), (line(0, 4), 1.0), ("another line\n", 2.0)])
    assert logged == ["Saving file", "another line"]
    assert state["done"] == 0 and state["total"] == 4 and state["eta_seconds"] is None


def test_eta_needs_two_downloaded_requests_and_uses_the_median():
    state, _ = feed([(line(0, 5), 0.0), (line(1, 5), 100.0)])
    assert state["eta_seconds"] is None                       # one downloaded request is not enough
    state, _ = feed([(line(0, 5), 0.0), (line(1, 5), 100.0), (line(2, 5), 400.0)])
    assert state["eta_seconds"] == 200.0 * 3                  # durations 100 and 300: median 200, three left
    state, _ = feed([(line(0, 6), 0.0), (line(1, 6), 100.0), (line(2, 6), 400.0), (line(3, 6), 450.0)])
    assert state["eta_seconds"] == 100.0 * 3                  # median of 100, 300, 50 is 100


def test_skipped_requests_do_not_enter_the_median():
    state, _ = feed([(line(0, 6), 0.0), (line(1, 6, 1), 1.0), (line(2, 6, 2), 2.0), (line(3, 6, 2), 302.0),
                     (line(4, 6, 2), 402.0)])
    assert state["eta_seconds"] == 200.0 * 2                  # only 300 s and 100 s count; two requests left


def test_a_malformed_progress_line_is_kept_in_the_log():
    state, logged = feed([(PROGRESS + "{not json\n", 0.0), (PROGRESS + '{"done": 1}\n', 1.0)])
    assert state is None
    assert logged == [PROGRESS + "{not json", PROGRESS + '{"done": 1}']
