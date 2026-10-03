"""Real-browser tests for the page's JavaScript (static/app.js and static/plugins/*.js).

They drive Chromium through Playwright against the real Flask app, started in a thread on a free port with small
synthetic jobs. They are skipped, not failed, when Playwright or its Chromium build is not installed, or when the
page's CDN libraries (MapLibre, uPlot from unpkg) cannot be loaded. The basemap is replaced by a plain style and
every other outside request is blocked, so the tests do not depend on the network beyond those two libraries.
"""

import os
import threading
import time
from dataclasses import dataclass

import pytest
from werkzeug.serving import make_server

import app as appmodule
from tests.test_nodes import varied_wave_dataset
from tests.test_provenance_crosscheck import build_b, make_job

sync_api = pytest.importorskip("playwright.sync_api")

# CI sets REQUIRE_BROWSER=1 on the job that installs Chromium: there a missing browser or library is a failure,
# not a skip, so the job cannot go green without running the tests.
REQUIRED = bool(os.environ.get("REQUIRE_BROWSER"))


def unavailable(reason):
    (pytest.fail if REQUIRED else pytest.skip)(reason)

PLAIN_STYLE = '{"version":8,"sources":{},"layers":[{"id":"background","type":"background","paint":{"background-color":"#d6e8dd"}}]}'

JOB_A, JOB_B, JOB_SPECTRA = "aaaaaaaaaa01", "aaaaaaaaaa02", "aaaaaaaaaa03"
NODE_DEFAULT, NODE_SLOW, NODE_FAST = (0.0, 95.0), (1.0, 96.0), (0.0, 96.0)     # valid cells of the 3x3 test grid


@dataclass
class Live:
    url: str


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/frontend" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.browser)


@pytest.fixture
def live(monkeypatch):
    """The app on a free port with three finished jobs: A (96 h) and B (48 h) single-levels, and a spectra job."""
    appmodule.jobs.clear()
    make_job(JOB_A, "single-levels", lambda f: varied_wave_dataset(96).to_netcdf(f / "era5_2022-01.nc"))
    make_job(JOB_B, "single-levels", lambda f: varied_wave_dataset(48).to_netcdf(f / "era5_2022-01.nc"))
    make_job(JOB_SPECTRA, "wave-spectra", build_b)
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: None)
    server = make_server("127.0.0.1", 0, appmodule.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Live(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        appmodule.jobs.clear()


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch(headless=True)
        except Exception as error:                      # not installed: `playwright install chromium`
            unavailable(f"Chromium is not available for Playwright: {str(error).splitlines()[0]}")
        yield chromium
        chromium.close()


@pytest.fixture
def new_page(browser):
    """A factory: new_page(width=1280, height=900, **context_options) -> page with .errors (uncaught JS errors)."""
    contexts = []

    def factory(width=1280, height=900, **options):
        context = browser.new_context(viewport={"width": width, "height": height}, **options)
        contexts.append(context)

        def route(handler):
            url = handler.request.url
            if url.startswith(("http://127.0.0.1", "http://localhost", "data:")):
                handler.continue_()
            elif "tiles.openfreemap.org/styles" in url:
                handler.fulfill(status=200, content_type="application/json", body=PLAIN_STYLE)
            elif "unpkg.com" in url:
                handler.continue_()
            else:
                handler.abort()

        context.route("**/*", route)
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(str(error)))
        page.set_default_timeout(15000)
        return page

    yield factory
    for context in contexts:
        context.close()


@pytest.fixture
def page(new_page):
    return new_page()


def open_app(page, live, hash_=""):
    """Load the page; skip the test when the CDN libraries cannot be loaded."""
    page.goto(f"{live.url}/{hash_}")
    try:
        page.wait_for_function("typeof maplibregl !== 'undefined' && typeof uPlot !== 'undefined' && !!window.EraExplorer",
                               timeout=20000)
    except sync_api.TimeoutError:
        unavailable("MapLibre/uPlot could not be loaded from unpkg.com (offline?)")


def open_job(page, live, job_id):
    """Open a finished job and wait until its analysis is on screen."""
    open_app(page, live, f"#job={job_id}")
    wait_for_analysis(page)


def wait_for_analysis(page, text="nearest ERA5 grid", timeout=30000):
    page.wait_for_function("t => document.querySelector('#analysis-meta').textContent.includes(t)", arg=text,
                           timeout=timeout)


def analysis_node(page):
    """The 'lat, lon' the page says it analysed (from the header line)."""
    return page.evaluate("""() => {
        const m = /nearest ERA5 grid (-?[\\d.]+), (-?[\\d.]+)/.exec(document.querySelector('#analysis-meta').textContent);
        return m ? [Number(m[1]), Number(m[2])] : null;
    }""")


def slow_or_failing_analysis(monkeypatch, delays=None, failures=()):
    """Make /analysis for a grid node (lat, lon; None = nearest ocean cell) slow, or fail it with a 422."""
    delays = delays or {}
    real = appmodule.cached_analysis

    def wrapper(*args):
        node = args[5] if len(args) > 5 else None
        key = None if node is None else (round(node[0], 3), round(node[1], 3))
        time.sleep(delays.get(key, 0))
        if key in failures:
            raise ValueError("analysis failed on purpose (test)")
        return real(*args)

    monkeypatch.setattr(appmodule, "cached_analysis", wrapper)
