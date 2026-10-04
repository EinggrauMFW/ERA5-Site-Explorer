"""Acceptance tests for docs/work-packages/WP19_frontend_accessibility.md.

Keyboard and screen-reader access found lacking by the front-end review: unnamed form fields and icon buttons,
a data-product choice that arrow keys cannot change, smooth scrolling that ignores reduced motion, greyed-out
navigation links that stay focusable, and table rows that only react to Enter.
"""

import pytest

from tests.frontend.conftest import JOB_A, NODE_DEFAULT, analysis_node, open_app, open_job, wait_for_analysis
from tests.frontend.test_state_plugins import catalogue, open_picker  # noqa: F401  (catalogue is a fixture)

# The accessible name of a form control: its <label>, aria-label or aria-labelledby (not placeholder or title).
NAME_JS = """el => {
    const fromLabels = el.labels ? [...el.labels].map(l => l.textContent.trim()).filter(Boolean).join(' ') : '';
    const aria = (el.getAttribute('aria-label') || '').trim();
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
    const fromIds = ids.map(id => (document.getElementById(id) || {}).textContent || '').join(' ').trim();
    return fromLabels || aria || fromIds || '';
}"""


def unnamed_controls(page, root):
    """Descriptions of the form controls inside `root` that have no accessible name."""
    return page.evaluate("""([root, nameOf]) => {
        const name = eval(nameOf);
        return [...document.querySelectorAll(`${root} input, ${root} select, ${root} textarea`)]
            .filter(el => el.type !== 'hidden' && !el.closest('[aria-hidden=true]') && !name(el))
            .map(el => `${el.tagName.toLowerCase()}${el.type ? '[' + el.type + ']' : ''}#${el.id || '(no id)'} in ${(el.closest('[class]') || el).className}`);
    }""", [root, NAME_JS])


def icon_buttons_without_label(page, root):
    return page.evaluate("""root => [...document.querySelectorAll(`${root} button`)]
        .filter(b => /^[×✕xX]$/.test(b.textContent.trim()) && !(b.getAttribute('aria-label') || '').trim())
        .map(b => `button '${b.textContent.trim()}' in ${(b.parentElement || b).className}`)""", root)


# --- R1: every form control and icon-only button has a name ----------------------------------------------

def test_the_request_form_and_cross_check_controls_have_names(page, live):
    open_app(page, live)
    assert unnamed_controls(page, "#fetch-form") == []
    assert unnamed_controls(page, "#crosscheck") == []


def test_the_device_form_controls_have_names(page, live):
    open_job(page, live, JOB_A)
    page.click("#tab-device")
    assert unnamed_controls(page, "#panel-device") == []


def test_the_picker_settings_controls_and_list_have_names(page, live, catalogue):
    open_job(page, live, JOB_A)
    open_picker(page)
    page.wait_for_selector("dialog.device-picker-dialog .device-settings-form input")
    assert unnamed_controls(page, "dialog.device-picker-dialog") == []
    listbox_name = page.evaluate("""() => { const l = document.querySelector('dialog.device-picker-dialog [role=listbox]');
        return (l.getAttribute('aria-label') || '').trim() || (l.getAttribute('aria-labelledby') || '').trim(); }""")
    assert listbox_name != ""


def test_the_screening_controls_have_names(page, live):
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-screening")
    page.wait_for_selector("#panel-screening .screening-controls select")
    assert unnamed_controls(page, "#panel-screening") == []


def test_icon_only_buttons_say_what_they_do(page, live, catalogue):
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-screening")
    page.wait_for_selector("#panel-screening .screening-table button:text('Compare')")
    page.locator("#panel-screening .screening-table button:text('Compare')").first.click()
    assert page.locator("#panel-screening .compare-chip button").count() == 1
    assert icon_buttons_without_label(page, "#panel-screening") == []
    page.click("#tab-device")                                    # the catalogue chip's clear button is in the DOM, hidden
    assert icon_buttons_without_label(page, "#panel-device") == []
    open_picker(page)
    assert icon_buttons_without_label(page, "dialog.device-picker-dialog") == []


def test_no_label_in_the_device_form_contains_a_button_or_link(page, live):
    open_job(page, live, JOB_A)
    page.click("#tab-device")
    assert page.evaluate("""() => [...document.querySelectorAll('#panel-device label')]
        .filter(l => l.querySelector('button, a, input, select')).map(l => l.textContent.trim().slice(0, 40))""") == []


# --- R2: the data product can be chosen with the keyboard -------------------------------------------------

def product_state(page):
    return page.evaluate("""() => ({ value: document.querySelector('#product').value,
        focused: document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.value : null,
        checked: [...document.querySelectorAll('#product-segmented button[aria-checked=true]')].map(b => b.dataset.value) })""")


def test_arrow_keys_home_and_end_change_the_data_product(page, live):
    open_app(page, live)
    options = page.eval_on_selector_all("#product option", "els => els.map(e => e.value)")
    assert len(options) >= 3
    page.focus("#product-segmented button[aria-checked='true']")
    first = options.index(page.input_value("#product"))
    for key, expected in [("ArrowRight", options[(first + 1) % len(options)]),
                          ("ArrowLeft", options[first]),
                          ("End", options[-1]),
                          ("Home", options[0]),
                          ("ArrowLeft", options[-1]),            # wraps round
                          ("ArrowDown", options[0]),
                          ("ArrowUp", options[-1])]:
        page.keyboard.press(key)
        assert product_state(page) == {"value": expected, "focused": expected, "checked": [expected]}, key


# --- R3: smooth scrolling respects reduced motion --------------------------------------------------------

SCROLL_SPY = """window.__scrolls = [];
const original = Element.prototype.scrollIntoView;
Element.prototype.scrollIntoView = function (arg) { window.__scrolls.push(arg === undefined ? null : arg); return original.call(this, arg); };"""


def scroll_behaviours(new_page, live, **options):
    page = new_page(**options)
    page.add_init_script(SCROLL_SPY)
    open_job(page, live, JOB_A)
    return [(call or {}).get("behavior") for call in page.evaluate("window.__scrolls")]


def test_the_page_scrolls_smoothly_by_default(new_page, live):
    assert "smooth" in scroll_behaviours(new_page, live)                      # the spy works, and this is the default


def test_the_page_does_not_scroll_smoothly_when_the_user_asks_for_reduced_motion(new_page, live):
    behaviours = scroll_behaviours(new_page, live, reduced_motion="reduce")
    assert behaviours != [] and "smooth" not in behaviours


# --- R4: a greyed-out navigation link is not a tab stop ---------------------------------------------------

def test_navigation_links_that_are_off_are_not_focusable_and_say_so(page, live):
    open_app(page, live)
    off = page.eval_on_selector_all(".topnav a.is-off", "els => els.map(a => [a.textContent.trim(), a.getAttribute('tabindex'), a.getAttribute('aria-disabled')])")
    assert [row[0] for row in off] == ["Status", "Analysis"]
    assert all(row[1] == "-1" and row[2] == "true" for row in off), off


def test_navigation_links_become_available_again_when_their_section_appears(page, live):
    open_job(page, live, JOB_A)
    state = page.eval_on_selector_all(".topnav a[data-needs]", "els => els.map(a => [a.textContent.trim(), a.classList.contains('is-off'), a.getAttribute('tabindex'), a.getAttribute('aria-disabled')])")
    assert [row[0] for row in state if not row[1]] == ["Status", "Analysis", "History"]
    for _, off, tabindex, disabled in state:
        assert off is False and tabindex != "-1" and disabled in (None, "false")


# --- R5: node table rows react to Space as well as Enter ---------------------------------------------------

def test_space_on_a_node_row_analyses_that_node(page, live):
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-nodes")
    rows = page.locator("#nodes-table tr[tabindex='0']")
    target = None
    for index in range(rows.count()):
        cells = rows.nth(index).locator("td").all_inner_texts()
        lat, lon = float(cells[0].split()[0]), float(cells[1])
        if (lat, lon) != NODE_DEFAULT:
            target = (index, lat, lon)
            break
    assert target is not None
    rows.nth(target[0]).focus()
    page.keyboard.press("Space")
    page.wait_for_function("t => document.querySelector('#analysis-meta').textContent.includes(`${t[0].toFixed(3)}, ${t[1].toFixed(3)}`)",
                           arg=[target[1], target[2]])
    assert page.evaluate("EraExplorer.context().node") == {"lat": target[1], "lon": target[2]}
