"""Acceptance tests for docs/work-packages/WP20_frontend_contrast_layout.md.

Readability and layout problems found by the front-end review, measured in a real browser: text contrast in both
themes (WCAG AA is 4.5:1), the Screening comparison chart overflowing a phone screen, the seasons row under the
wrong months, number formatting, and the heatmap tooltip's position.
"""

import pytest

from tests.frontend.conftest import JOB_A, open_job
from tests.frontend.test_state_plugins import catalogue, open_picker, settle_heatmap  # noqa: F401  (catalogue is a fixture)

AA = 4.5

# Installs window.__contrast(element): the WCAG contrast ratio of the element's text colour over the colour it is
# really drawn on (every translucent background layer up the tree is composited; colours are resolved by a canvas,
# so color-mix() and color(srgb ...) values work).
CONTRAST_JS = """() => {
  if (window.__contrast) return;
  const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const rgba = css => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = css; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (top, bottom) => { const a = top[3] + bottom[3] * (1 - top[3]);
    return a === 0 ? [0, 0, 0, 0] : [0, 1, 2].map(i => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a).concat(a); };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  window.__contrast = el => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) { const c = rgba(getComputedStyle(e).backgroundColor);
      if (c[3] > 0) layers.push(c); if (c[3] === 1) break; }
    let bg = [255, 255, 255, 1];
    for (const c of layers.reverse()) bg = over(c, bg);
    const fg = over(rgba(getComputedStyle(el).color), bg);
    const a = lum(fg), b = lum(bg);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
}"""


def install_contrast(page):
    page.evaluate(CONTRAST_JS)


def token_contrast(page, theme, foreground, background):
    install_contrast(page)
    return page.evaluate("""([theme, fg, bg]) => {
        document.documentElement.dataset.theme = theme;
        const el = document.createElement('div');
        el.style.cssText = `color: var(${fg}); background: var(${bg})`; el.textContent = 'x';
        document.body.append(el);
        const ratio = window.__contrast(el); el.remove(); return ratio; }""", [theme, foreground, background])


# --- R1: faint text and the running pill meet AA -----------------------------------------------------------

@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("surface", ["--bg", "--surface", "--surface-2", "--surface-3"])
def test_faint_text_meets_aa_on_every_surface(page, live, theme, surface):
    open_job(page, live, JOB_A)
    assert token_contrast(page, theme, "--faint", surface) >= AA


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_faint_text_stays_less_prominent_than_muted_text(page, live, theme):
    """Raising --faint to AA must not flatten the hierarchy: it stays below --muted in contrast."""
    open_job(page, live, JOB_A)
    assert token_contrast(page, theme, "--faint", "--surface") < token_contrast(page, theme, "--muted", "--surface")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_running_pill_meets_aa(page, live, theme):
    open_job(page, live, JOB_A)
    install_contrast(page)
    ratio = page.evaluate("""theme => { document.documentElement.dataset.theme = theme;
        const el = document.createElement('span'); el.className = 'pill pill-running'; el.textContent = 'running';
        document.body.append(el); const r = window.__contrast(el); el.remove(); return r; }""", theme)
    assert ratio >= AA


# --- R2: heat-cell text is readable at every value, in both themes, also after the theme is toggled ----------

def heat_ratios(page):
    install_contrast(page)
    return page.evaluate("""() => {
        const read = sel => [...document.querySelectorAll(sel)].filter(c => /\\d/.test(c.textContent)).map(c => window.__contrast(c));
        return { screening: read('#panel-screening .heat-cell'), scatter: read('#panel-distributions table.scatter td') };
    }""")


def test_heat_cell_text_stays_readable_in_both_themes(page, live):
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-screening")
    page.wait_for_selector("#panel-screening .heat-cell")
    seen = {}
    for theme in ("light", "dark"):
        if theme == "dark":
            page.click("#theme-toggle")
            page.click("#theme-toggle")                    # automatic -> light -> dark, without reloading anything
            page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
        ratios = heat_ratios(page)
        assert ratios["screening"] and ratios["scatter"], "the heat tables have no numeric cells"
        seen[theme] = {name: round(min(values), 2) for name, values in ratios.items()}
    assert all(value >= AA for theme in seen.values() for value in theme.values()), f"lowest contrast per table: {seen}"


# --- R3: the Screening comparison chart fits the screen -----------------------------------------------------

@pytest.mark.parametrize("width", [375, 557])
def test_the_comparison_chart_does_not_widen_the_page(new_page, live, width):
    page = new_page(width=width, height=900)
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.evaluate("document.querySelector('#tab-screening').click()")
    page.wait_for_selector("#panel-screening .screening-table button:text('Compare')")
    for index in range(2):
        page.locator("#panel-screening .screening-table button:text('Compare')").nth(index).dispatch_event("click")
    page.wait_for_selector("#panel-screening .compare-chart-container canvas", state="attached")
    page.wait_for_timeout(500)
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1
    assert page.evaluate("""() => { const holder = document.querySelector('#panel-screening .compare-chart-container');
        return holder.querySelector('.uplot').getBoundingClientRect().width - holder.getBoundingClientRect().width; }""") <= 1


# --- R4: the seasonal values have their own table --------------------------------------------------------

def test_seasons_are_shown_in_their_own_table_with_season_headings(page, live):
    open_job(page, live, JOB_A)
    page.wait_for_function("EraExplorer.context().nodeData !== null")
    page.click("#tab-screening")
    page.wait_for_selector("#panel-screening .heat-table")
    assert page.locator("#panel-screening table.heat-table td[colspan], #panel-screening table.heat-table th[colspan]").count() == 0
    assert page.eval_on_selector("#panel-screening table.heat-table.seasons thead",
                                 "t => [...t.querySelectorAll('th')].map(th => th.textContent.trim())") == ["Node", "DJF", "MAM", "JJA", "SON"]
    months_rows = page.locator("#panel-screening table.heat-table:not(.seasons) tbody tr").count()
    assert months_rows >= 1
    assert page.locator("#panel-screening table.heat-table.seasons tbody tr").count() == months_rows


# --- R5: numbers are formatted without negative zero or empty-string zeros -----------------------------

@pytest.mark.parametrize("call,expected", [
    ("fmtNum(-0.001)", "0.00"), ("fmtNum(-0)", "0.00"), ("fmtNum(0.004)", "0.00"),
    ("fmtNum('')", "—"), ("fmtNum('  ')", "—"), ("fmtNum(null)", "—"), ("fmtNum(undefined)", "—"), ("fmtNum('abc')", "—"),
    ("fmtNum(99.96)", "100"), ("fmtNum(99.94)", "99.9"), ("fmtNum(-99.96)", "-100"), ("fmtNum(9.996)", "10.0"),
    ("fmtNum(1234.5)", "1,235"), ("fmtNum(-2.5)", "-2.50"),
    ("fmtCoord(-0.0004)", "0.0"), ("fmtCoord(-1.5)", "-1.5"), ("fmtCoord(0)", "0.0"), ("fmtCoord('')", "—"), ("fmtCoord(95)", "95.0"),
])
def test_number_formatting(page, live, call, expected):
    open_job(page, live, JOB_A)
    assert page.evaluate(f"EraExplorer.{call}") == expected


# --- R6: the heatmap tooltip appears at the pointer ---------------------------------------------------------

def test_the_heatmap_tooltip_is_next_to_the_pointer(page, live, catalogue):
    open_job(page, live, JOB_A)
    open_picker(page)
    page.click("li.device-picker-item[data-id='big']")
    page.wait_for_selector(".heatmap-svg[aria-label^='Big device']")
    settle_heatmap(page)
    box = page.locator(".heatmap-svg rect").nth(14).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x - 5, y - 5)
    page.mouse.move(x, y)
    page.wait_for_function("getComputedStyle(document.querySelector('.heatmap-tooltip')).display === 'block'")
    tip = page.locator(".heatmap-tooltip").bounding_box()
    assert abs(tip["x"] - x) < 60 and abs(tip["y"] - y) < 60, f"pointer at ({x:.0f}, {y:.0f}), tooltip at ({tip['x']:.0f}, {tip['y']:.0f})"
