"""Acceptance tests for docs/work-packages/WP18_frontend_plugin_state.md (static/plugins/*.js, one line in app.js).

State bugs in the Screening, Long-term, Device and device-picker tabs found by the front-end review.
"""

import json
import re
import time

import pytest

import plugin_device
import plugin_screening
from tests.frontend.conftest import JOB_A, JOB_B, open_job, wait_for_analysis

BIG = {"name": "Big device", "rated_kw": 100.0, "period_type": "Te",
       "hs_m": [1, 2, 3, 4, 5, 6], "period_s": [4, 5, 6, 7, 8, 9],
       "power_kw": [[10 * (i + 1) + j for j in range(6)] for i in range(6)]}
SMALL = {"name": "Small device", "period_type": "Te", "hs_m": [1, 2], "period_s": [4, 5], "power_kw": [[1, 2], [3, 4]]}


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """A DEVICES_DIR with a 6x6 and a 2x2 device and one file the loader skips (so the picker shows a note)."""
    (tmp_path / "devices.json").write_text(json.dumps({"big": BIG, "small": SMALL}))
    (tmp_path / "malformed.csv").write_text("Hm0\\Te,1,2,\n1,1,1,\n2,1,1,")
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    return tmp_path


def click_recent(page, job_id):
    page.click(f".recent-item[href='#job={job_id}']")


def wait_for_job_loaded(page, job_id):
    """The job's own analysis and nodes are on screen (the header text alone can still be the previous job's)."""
    page.wait_for_function("id => { const c = EraExplorer.context(); return c.jobId === id && c.analysis !== null && c.nodeData !== null; }",
                           arg=job_id)


def settle_heatmap(page):
    """The heatmap redraws when its container is resized; give that time to finish before using the keyboard on it."""
    page.wait_for_timeout(800)


def open_picker(page):
    page.click("#tab-device")
    page.click("#device_catalogue")
    page.wait_for_selector("dialog.device-picker-dialog[open] li.device-picker-item")


def close_picker(page):
    page.keyboard.press("Escape")
    page.wait_for_selector("dialog.device-picker-dialog:not([open])", state="attached")


# --- I5: the Screening tab never shows another job's statistics ------------------------------------------

def test_a_slow_screening_response_for_the_previous_job_does_not_overwrite_the_new_job(page, live, monkeypatch):
    real = plugin_screening._load_or_compute

    def slow_for_job_a(view):
        result = real(view)                                  # computed under the NetCDF lock, then held back outside it
        if JOB_A in str(view.directory):
            time.sleep(4.0)
        return result

    monkeypatch.setattr(plugin_screening, "_load_or_compute", slow_for_job_a)
    open_job(page, live, JOB_A)                              # a 96 h record ends on 2020-04-04
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-screening")                             # job A's screening request takes 4 s
    click_recent(page, JOB_B)                                # a 48 h record ends on 2020-04-02
    wait_for_job_loaded(page, JOB_B)
    page.click("#tab-screening")
    page.wait_for_function("document.querySelector('#panel-screening').textContent.includes('downloaded record')")
    page.wait_for_timeout(4500)                              # job A's answer has arrived by now
    text = page.inner_text("#panel-screening")
    ends = re.findall(r"downloaded record \([\d-]+ to ([\d-]+)\)", text)
    assert ends == ["2020-04-02"], f"the panel shows the record end(s) {ends}; job B ends on 2020-04-02"
    assert page.errors == []


def test_compared_nodes_and_map_colours_do_not_survive_a_job_switch(page, live):
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-screening")
    page.wait_for_selector("#panel-screening .screening-table button:text('Compare')")
    page.locator("#panel-screening .screening-table button:text('Compare')").nth(0).click()
    page.locator("#panel-screening .screening-table button:text('Compare')").nth(1).click()
    assert page.locator("#panel-screening .compare-chip").count() == 2
    assert "Mean flux" in page.inner_text("#legend-label")                 # the Screening colouring is on the map
    click_recent(page, JOB_B)
    wait_for_job_loaded(page, JOB_B)
    assert page.inner_text("#legend-label") == "Mean energy flux J (kW/m)"   # the built-in colouring again
    page.click("#tab-screening")
    page.wait_for_function("document.querySelector('#panel-screening').textContent.includes('2020-04-02')")   # job B's own panel
    assert page.locator("#panel-screening .compare-chip").count() == 0


# --- I14: the Long-term status line returns to normal after an error ------------------------------------

LT_STATUS = "#panel-longterm .lt-params + p"                  # the status line: it follows the parameter row


def test_the_longterm_status_line_is_not_left_in_its_error_style(page, live):
    open_job(page, live, JOB_A)
    page.click("#tab-longterm")
    page.wait_for_function(f"document.querySelector('{LT_STATUS}') && document.querySelector('{LT_STATUS}').textContent === ''")
    page.fill("#lt-thresh", "10")                            # below the allowed 50: the server refuses it
    page.click(".lt-params button:text('Compute')")
    page.wait_for_function(f"document.querySelector('{LT_STATUS}').textContent.startsWith('Error')")
    assert "form-error" in page.get_attribute(LT_STATUS, "class")
    page.fill("#lt-thresh", "95")
    page.click(".lt-params button:text('Compute')")
    page.wait_for_function(f"document.querySelector('{LT_STATUS}').textContent === ''")
    assert page.get_attribute(LT_STATUS, "class") == "lt-status"


# --- I12: the picker's notes are shown once, however often it opens --------------------------------------

def test_picker_notes_do_not_pile_up_when_it_is_opened_again(page, live, catalogue):
    open_job(page, live, JOB_A)
    open_picker(page)
    notes = "dialog.device-picker-dialog .device-picker-sidebar details"
    assert page.locator(notes).count() == 1
    for _ in range(2):
        close_picker(page)
        page.click("#device_catalogue")
        page.wait_for_selector("dialog.device-picker-dialog[open] li.device-picker-item")
    assert page.locator(notes).count() == 1


def test_the_skipped_files_note_uses_the_right_singular_and_plural(page, live, catalogue):
    (catalogue / "broken-too.csv").write_text("Hm0\\Te,1,2,\n1,1,1,\n2,1,1,")
    open_job(page, live, JOB_A)
    open_picker(page)
    assert page.inner_text("dialog.device-picker-dialog .device-picker-sidebar details summary") == "2 files were skipped"
    (catalogue / "broken-too.csv").unlink()
    close_picker(page)
    page.reload()
    wait_for_analysis(page)
    open_picker(page)
    assert page.inner_text("dialog.device-picker-dialog .device-picker-sidebar details summary") == "1 file was skipped"


# --- I13: the heatmap cursor is reset when another device is selected ------------------------------------

def test_arrow_keys_on_a_newly_selected_device_do_not_use_the_previous_cursor(page, live, catalogue):
    open_job(page, live, JOB_A)
    open_picker(page)
    page.click("li.device-picker-item[data-id='big']")
    page.wait_for_selector(".heatmap-svg[aria-label^='Big device']")
    settle_heatmap(page)
    page.focus(".heatmap-svg")
    for key in ["ArrowRight"] + ["ArrowUp"] * 5 + ["ArrowRight"] * 5:      # the cursor ends on the top right cell
        page.keyboard.press(key)
    assert page.inner_text(".heatmap-readout") != ""                       # the cursor really is on a cell
    page.click("li.device-picker-item[data-id='small']")                    # a 2 x 2 matrix: that cell does not exist
    page.wait_for_selector(".heatmap-svg[aria-label^='Small device']")
    settle_heatmap(page)
    page.focus(".heatmap-svg")
    page.keyboard.press("ArrowDown")
    assert page.errors == []
    assert page.inner_text(".heatmap-readout") != ""                       # the cursor starts again at the first cell


# --- M7: the Device form reports errors to assistive technology and ignores a second click while busy ----

def test_a_second_click_on_assess_while_one_is_running_sends_no_second_request(page, live, monkeypatch):
    real = plugin_device.assess_device

    def slow(*args, **kwargs):
        time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(plugin_device, "assess_device", slow)
    open_job(page, live, JOB_A)
    page.click("#tab-device")
    page.click("#use_example_csv")
    page.fill("#device_name", "Example device")
    posts = []
    page.on("request", lambda request: posts.append(request.url) if request.method == "POST" and "/device" in request.url else None)
    assess = page.locator(".device-form button:text('Assess'), #panel-device button:text('Assess')").first
    assess.click()
    assess.click(force=True)
    page.wait_for_selector("#panel-device .device-result :text('AEP')", timeout=20000)
    assert len(posts) == 1
    assert assess.is_enabled()                                           # usable again once the answer is in


def test_a_device_form_error_is_announced(page, live):
    open_job(page, live, JOB_A)
    page.click("#tab-device")
    page.click("#panel-device button:text('Assess')")                    # no name and no matrix
    error = page.locator("#panel-device .form-error").first
    error.wait_for(state="visible")
    assert error.get_attribute("role") == "alert"
