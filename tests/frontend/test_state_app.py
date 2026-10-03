"""Acceptance tests for docs/work-packages/WP17_frontend_state.md (static/app.js, templates/index.html).

Each test reproduces a state bug found by the front-end review, in a real browser.
"""

import pytest

import app as appmodule
from tests.frontend.conftest import (JOB_A, JOB_B, JOB_SPECTRA, NODE_FAST, NODE_SLOW, analysis_node, open_app, open_job,
                                     slow_or_failing_analysis, wait_for_analysis)
from tests.test_provenance_crosscheck import make_job


def wait_for_nodes(page):
    page.wait_for_function("EraExplorer.context().nodeData !== null")


def select_node(page, node):
    page.evaluate(f"EraExplorer.selectNode({node[0]}, {node[1]})")


def click_recent(page, job_id):
    page.click(f".recent-item[href='#job={job_id}']")


# --- I1: a failed analysis does not leave the page half-updated -----------------------------------------

def test_a_failed_node_analysis_clears_the_skeleton_and_restores_the_previous_node(page, live, monkeypatch):
    slow_or_failing_analysis(monkeypatch, failures={NODE_FAST})
    open_job(page, live, JOB_A)
    wait_for_nodes(page)
    select_node(page, NODE_FAST)
    page.wait_for_function("document.querySelector('#analysis-meta').textContent.includes('Analysis unavailable')")
    assert page.locator("#metrics .skeleton").count() == 0                       # no placeholders left shimmering
    assert page.evaluate("EraExplorer.context().node") is None                   # the nearest ocean cell is still in use
    assert page.locator("#node-reset").is_hidden()
    assert page.errors == []


def test_a_failed_node_analysis_keeps_the_last_chosen_node_and_its_way_back(page, live, monkeypatch):
    slow_or_failing_analysis(monkeypatch, failures={NODE_FAST})
    open_job(page, live, JOB_A)
    wait_for_nodes(page)
    select_node(page, NODE_SLOW)
    page.wait_for_function("document.querySelector('#analysis-meta').textContent.includes('1.000, 96.000')")
    select_node(page, NODE_FAST)
    page.wait_for_function("document.querySelector('#analysis-meta').textContent.includes('Analysis unavailable')")
    assert page.evaluate("EraExplorer.context().node") == {"lat": 1.0, "lon": 96.0}
    assert page.locator("#node-reset").is_visible()                              # the way back to the nearest cell


# --- I2: overlapping node loads: the last request wins --------------------------------------------------

def test_a_slow_earlier_node_analysis_cannot_replace_a_newer_one(page, live, monkeypatch):
    slow_or_failing_analysis(monkeypatch, delays={NODE_SLOW: 2.5})
    open_job(page, live, JOB_A)
    wait_for_nodes(page)
    select_node(page, NODE_SLOW)              # answers after 2.5 s
    select_node(page, NODE_FAST)              # answers at once
    page.wait_for_function("document.querySelector('#analysis-meta').textContent.includes('0.000, 96.000')")
    page.wait_for_timeout(3500)               # the slow answer has arrived by now
    assert analysis_node(page) == [0.0, 96.0]
    assert page.evaluate("EraExplorer.context().node") == {"lat": 0.0, "lon": 96.0}


# --- I3: the theme toggle redraws; it does not refetch or reset the user's input -------------------------

def test_toggling_the_theme_redraws_charts_without_refetching_or_losing_input(page, live):
    open_job(page, live, JOB_A)
    page.click("#tab-longterm")
    page.wait_for_function("document.querySelector('#lt-thresh') && document.querySelector('.lt-status').textContent === ''")
    page.fill("#lt-thresh", "90")
    chart = "#charts .u-wrap canvas"
    before = page.evaluate(f"document.querySelector('{chart}').toDataURL()")
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.click("#theme-toggle")               # automatic -> light
    page.click("#theme-toggle")               # light -> dark
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    page.wait_for_timeout(800)
    assert [url for url in requests if any(part in url for part in ("/analysis", "/nodes", "/longterm"))] == []
    assert page.input_value("#lt-thresh") == "90"
    assert page.evaluate(f"document.querySelector('{chart}').toDataURL()") != before        # drawn again in dark colours
    assert page.locator("#charts .chart").count() >= 3


# --- I4: the advanced charts are drawn once, whenever the section is opened -----------------------------

def reload_analysis(page):
    with page.expect_response(lambda response: "/analysis" in response.url):
        page.evaluate("document.querySelector('#sector-form').requestSubmit()")
    page.wait_for_function("document.querySelectorAll('#metrics .skeleton').length === 0")


def advanced_series_count(page):
    return page.evaluate("""() => { const a = EraExplorer.context().analysis;
        return a.order.filter(name => a.series[name].advanced && a.series[name].values.some(v => v !== null)).length; }""")


def test_opening_advanced_after_several_loads_draws_each_chart_once(page, live):
    open_job(page, live, JOB_SPECTRA)
    expected = advanced_series_count(page)
    assert expected > 0
    for _ in range(3):
        reload_analysis(page)
    page.evaluate("document.querySelector('#advanced').open = true")
    page.wait_for_function(f"document.querySelectorAll('#charts-advanced .chart').length >= {expected}")
    page.wait_for_timeout(500)
    assert page.locator("#charts-advanced .chart").count() == expected


def test_reloading_with_advanced_already_open_draws_its_charts_again(page, live):
    open_job(page, live, JOB_SPECTRA)
    expected = advanced_series_count(page)
    page.evaluate("document.querySelector('#advanced').open = true")
    page.wait_for_function(f"document.querySelectorAll('#charts-advanced .chart').length === {expected}")
    reload_analysis(page)
    page.wait_for_function(f"document.querySelectorAll('#charts-advanced .chart').length === {expected}")


# --- I8: one old job record does not blank the history ---------------------------------------------------

def test_an_old_job_record_without_coordinates_does_not_empty_the_history(page, live):
    make_job("aaaaaaaaaa04", "single-levels", lambda folder: None)
    appmodule.jobs["aaaaaaaaaa04"].update(latitude=None, longitude=None, start=None, end=None, product=None)
    open_app(page, live)
    page.wait_for_function("document.querySelectorAll('#recent-list .recent-item').length >= 4")
    assert page.locator("#recent-list .recent-item").count() == 4
    assert page.errors == []


# --- M7: a refused Cancel or Delete is reported, and the page stays as it was ---------------------------

def refuse(page, method, url_part, message):
    def handler(route):
        if route.request.method == method:
            route.fulfill(status=409, content_type="application/json", body=f'{{"error": "{message}"}}')
        else:
            route.fallback()
    page.route(url_part, handler)


def test_a_refused_delete_is_reported_and_keeps_the_job_on_screen(page, live):
    refuse(page, "DELETE", f"**/api/jobs/{JOB_A}", "Cancel the job before deleting it")
    page.on("dialog", lambda dialog: dialog.accept())
    open_job(page, live, JOB_A)
    page.click("#delete")
    page.wait_for_function("(() => { const e = document.querySelector('#action-error'); return !!e && !e.hidden && e.textContent.includes('Cancel the job before deleting it'); })()")
    assert page.locator("#status-card").is_visible() and page.locator("#analysis").is_visible()
    assert page.locator(f".recent-item[href='#job={JOB_A}']").count() == 1
    assert page.locator("#action-error").get_attribute("role") == "alert"


def test_an_accepted_delete_still_clears_the_page(page, live):
    page.on("dialog", lambda dialog: dialog.accept())
    open_job(page, live, JOB_A)
    page.click("#delete")
    page.wait_for_function("document.querySelector('#status-card').hidden")
    assert page.locator("#analysis").is_hidden()


def test_a_refused_cancel_is_reported_and_stays_visible_while_polling(page, live):
    appmodule.jobs[JOB_A]["status"] = "running"
    refuse(page, "POST", f"**/api/jobs/{JOB_A}/cancel", "The job is already finishing")
    open_app(page, live, f"#job={JOB_A}")
    page.wait_for_selector("#cancel", state="visible")
    page.click("#cancel")
    page.wait_for_function("(() => { const e = document.querySelector('#action-error'); return !!e && !e.hidden && e.textContent.includes('already finishing'); })()")
    page.wait_for_timeout(3500)               # two polls later it is still there
    assert page.locator("#action-error").is_visible()


# --- M3: a faulty plugin cannot break the tab list or the actions ----------------------------------------

def test_a_plugin_whose_available_or_href_throws_does_not_stop_the_tabs(page, live):
    open_app(page, live)
    page.evaluate("""() => {
        EraExplorer.registerTab({id: 'broken', label: 'Broken', available() { throw new Error('available boom'); }, mount() {}});
        EraExplorer.registerAction({id: 'broken-action', label: 'Broken action', href() { throw new Error('href boom'); }});
    }""")
    page.wait_for_selector(f".recent-item[href='#job={JOB_A}']")
    click_recent(page, JOB_A)
    wait_for_analysis(page)
    assert page.locator("#tab-overview").count() == 1 and page.locator("#tab-series").count() == 1
    assert page.locator("#tab-broken").count() == 0
    assert page.errors == []


def test_a_plugin_whose_mount_rejects_shows_its_error_in_its_panel(page, live):
    open_app(page, live)
    page.evaluate("""EraExplorer.registerTab({id: 'rejecting', label: 'Rejecting', available() { return true; },
                                               async mount() { throw new Error('async boom'); }})""")
    page.wait_for_selector(f".recent-item[href='#job={JOB_A}']")
    click_recent(page, JOB_A)
    wait_for_analysis(page)
    page.click("#tab-rejecting")
    page.wait_for_function("document.querySelector('#panel-rejecting .form-error')")
    assert "async boom" in page.inner_text("#panel-rejecting .form-error")
    assert page.errors == []


# --- M11: the previous job's grid marker is removed when another job is opened --------------------------

def test_the_grid_marker_of_the_previous_job_goes_when_another_job_opens(page, live, monkeypatch):
    slow_or_failing_analysis(monkeypatch, delays={None: 1.5})
    open_job(page, live, JOB_A)
    assert page.locator(".maplibregl-marker").count() == 2                         # the site marker and the grid marker
    click_recent(page, JOB_B)                                                      # job B's analysis takes 1.5 s
    page.wait_for_function("document.querySelectorAll('.maplibregl-marker').length === 1", timeout=1200)
