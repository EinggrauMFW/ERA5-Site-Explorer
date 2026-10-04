"""Capture the README screenshots from a finished Option A job with Playwright.

    python tools/capture_screenshots.py --job-dir path/to/downloads/<job id>

Needs Playwright and its Chromium once:  pip install playwright && python -m playwright install chromium
(`playwright` is in requirements-dev.txt).

What it does
- Copies the job folder to a temporary downloads folder, because the app writes cache files and device results next
  to the data. Your own folder is never modified.
- Starts a throwaway copy of this app on a spare port, opens the job in headless Chromium (dark theme, 1280x992 CSS px
  at 1.25x, so each image is 1600x1240) and saves 14 JPEGs named like the ones in docs/screenshots.
- Uses the app's built-in synthetic example matrix for the two device views, never a device folder of yours.
- Keeps the Status card (its download log contains CDS URLs) out of every frame.

The README captions describe the demonstration job (hourly 2025, 0.93 years, near -8.75, 119.28). A different job gives
different numbers: check the captions before committing new images. Option B (2D spectra) jobs are not supported here.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
WIDE = {"width": 1280, "height": 992}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(downloads: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port), DOWNLOADS_DIR=str(downloads))
    env.pop("DEVICES_DIR", None)                 # no catalogue: the picker offers only the synthetic example
    server = subprocess.Popen([sys.executable, "app.py"], cwd=REPO, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if server.poll() is not None:
            break
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs", timeout=2).read()
            return server
        except Exception:
            time.sleep(1)
    server.terminate()
    raise SystemExit("The app did not start on a spare port.")


def scroll_to(page, selector: str, offset: int, wait: int = 700) -> None:
    # From the top, like a person scrolling: the section highlight in the nav then follows the page.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(350)
    page.evaluate("([s, o]) => window.scrollTo(0, document.querySelector(s).getBoundingClientRect().top + window.scrollY - o)",
                  [selector, offset])
    page.wait_for_timeout(wait)


def open_job(context, url: str, job: str):
    page = context.new_page()
    page.goto(f"{url}/#job={job}")
    page.wait_for_function("typeof maplibregl !== 'undefined' && typeof uPlot !== 'undefined'", timeout=30000)
    page.wait_for_function("document.querySelector('#analysis-meta').textContent.includes('nearest ERA5 grid')", timeout=60000)
    page.wait_for_function("EraExplorer.context().nodeData !== null", timeout=30000)
    page.wait_for_timeout(1500)
    return page


def tab(page, tab_id: str) -> None:
    page.evaluate(f"document.querySelector('#tab-{tab_id}').click()")
    page.wait_for_timeout(500)


def capture(url: str, job: str, out: Path, only: set[str]) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)

    def save(page, name):
        if only and not any(name.startswith(prefix) for prefix in only):
            return
        page.screenshot(path=str(out / name), type="jpeg", quality=84)
        print("saved", out / name)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        wide = browser.new_context(viewport=WIDE, device_scale_factor=1.25, color_scheme="dark")
        page = open_job(wide, url, job)

        page.evaluate("window.scrollTo(0, 0)")                                    # 01 site request, nodes on the map
        page.wait_for_function("map.loaded() && !map.isMoving()", timeout=30000)
        page.wait_for_timeout(2500)
        save(page, "01-site-request-map.jpg")

        scroll_to(page, "#analysis", 82)                                          # 02 overview
        save(page, "02-analysis-overview.jpg")

        tab(page, "distributions")                                                # 03 scatter and wave rose
        page.wait_for_selector("#panel-distributions table.scatter")
        scroll_to(page, "#analysis-tabs", 205)
        save(page, "03-scatter-wave-rose.jpg")

        tab(page, "nodes")                                                        # 04 grid nodes
        page.wait_for_selector("#nodes-table tr")
        scroll_to(page, "#analysis-tabs", 205)
        save(page, "04-grid-nodes.jpg")

        tab(page, "series")                                                       # 05 time series
        page.wait_for_selector("#charts canvas", state="attached")
        scroll_to(page, "#analysis-tabs", 205, 1500)
        save(page, "05-time-series.jpg")

        tab(page, "quality")                                                      # 06 quality
        scroll_to(page, "#analysis-tabs", 205)
        save(page, "06-quality-checks.jpg")

        tab(page, "screening")                                                    # 07 screening
        page.wait_for_selector("#panel-screening .heat-table.seasons", timeout=60000)
        scroll_to(page, "#analysis-tabs", 205, 1200)
        save(page, "07-site-screening.jpg")

        tab(page, "device")                                                       # 08 device, synthetic example
        page.wait_for_selector("#use_example_csv")
        page.evaluate("document.querySelector('#use_example_csv').click()")
        page.wait_for_function("document.querySelector('#device_name').value === 'Synthetic Example'"
                               " && !!document.querySelector('#panel-device').dataset.csvText", timeout=30000)
        page.locator("#panel-device button:text('Assess')").first.click()
        page.wait_for_function("(() => { const r = document.querySelector('#panel-device .device-result');"
                               " return !!r && r.textContent.includes('AEP'); })()", timeout=60000)
        scroll_to(page, "#analysis-tabs", 205, 1200)
        save(page, "08-device-assessment.jpg")

        scroll_to(page, "#analysis-tabs", 205)                                    # 09 device picker
        page.evaluate("document.querySelector('#device_catalogue').click()")
        page.wait_for_selector("dialog.device-picker-dialog[open] .heatmap-svg", timeout=30000)
        page.wait_for_timeout(1500)
        save(page, "09-device-picker.jpg")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        tab(page, "longterm")                                                     # 10 long-term
        page.wait_for_function("(() => { const s = document.querySelector('#panel-longterm .lt-params + p');"
                               " return !!s && s.textContent === ''; })()", timeout=60000)
        scroll_to(page, "#analysis-tabs", 205, 1000)
        save(page, "10-long-term.jpg")

        tab(page, "export")                                                       # 11 export
        scroll_to(page, "#analysis-tabs", 205)
        save(page, "11-export-report.jpg")

        tab(page, "notes")                                                        # 12 notes and limits
        scroll_to(page, "#analysis-tabs", 205)
        save(page, "12-notes-and-limits.jpg")
        page.close()

        light = browser.new_context(viewport=WIDE, device_scale_factor=1.25, color_scheme="light")
        page = open_job(light, url, job)                                          # 13 light theme
        scroll_to(page, "#analysis", 82, 1500)
        save(page, "13-light-theme.jpg")
        page.close()

        phone = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark",
                                    is_mobile=True, has_touch=True)
        page = open_job(phone, url, job)                                          # 14 phone width
        scroll_to(page, "#analysis", 56, 1500)
        save(page, "14-phone-layout.jpg")
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture the README screenshots from a finished Option A job.")
    parser.add_argument("--job-dir", required=True, type=Path,
                        help="a finished job folder (downloads/<12-character id>); it is copied, never modified")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "screenshots", help="output folder (default: docs/screenshots)")
    parser.add_argument("--only", nargs="*", default=[], metavar="PREFIX",
                        help="only the screenshots whose file name starts with one of these, e.g. 05 09")
    args = parser.parse_args()

    job_dir = args.job_dir.resolve()
    if not JOB_ID.match(job_dir.name) or not (job_dir / "job.json").is_file():
        raise SystemExit("--job-dir must be a job folder named by its 12-character id and containing job.json")
    record = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if record.get("status") != "complete" or record.get("product") != "single-levels":
        raise SystemExit("The job must be a finished Option A (single-levels) job; Option B is not supported here.")

    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit("Playwright is missing: pip install playwright && python -m playwright install chromium")

    with tempfile.TemporaryDirectory(prefix="era5-screenshots-") as scratch:
        downloads = Path(scratch)
        shutil.copytree(job_dir, downloads / job_dir.name)
        port = free_port()
        server = start_server(downloads, port)
        try:
            capture(f"http://127.0.0.1:{port}", job_dir.name, args.out, set(args.only))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
