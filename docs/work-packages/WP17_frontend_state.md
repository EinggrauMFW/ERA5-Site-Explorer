# WP17: the page shows the wrong state (static/app.js)

An independent front-end review drove the app in a browser and reproduced eight state bugs in the core script.
Nothing here changes a number; each leaves the page showing something that is not true, or loses what the user did.
The review found no XSS; keep using `textContent` / `replaceChildren` and never `innerHTML` with data.

## Who does what

- Claude (orchestrator) wrote the browser tests `tests/frontend/test_state_app.py` (and the shared fixtures in
  `tests/frontend/conftest.py`) from this spec before any implementation. They drive Chromium through Playwright
  against the real app and are red on the current code (12 red, 1 control that must keep passing). You
  (Antigravity, the implementer) must NOT edit, delete, rename, skip or weaken them, and must not edit any other
  file under `tests/`. If you think a test is wrong, say so under "Requests to the orchestrator".
- The orchestrator re-runs everything and checks the result in a browser.

## Files you own

`static/app.js`, `templates/index.html` (one new element), `static/app.css` (only styling for that element). Do not
touch `static/plugins/*` (WP18 owns them), any Python file, or `tests/`.

## Requirements

Each requirement names the test(s) that check it. Numbers are the review's item numbers.

### R1 (I1). A failed analysis leaves no half-state

`loadAnalysis` replaces the metrics with skeleton placeholders and (for a chosen node) has already moved
`selectedNode`. If the request fails (HTTP error or the server cannot be reached):

- remove the skeleton placeholders from `#metrics` (leave it empty);
- put `selectedNode` back to the node of the last analysis that succeeded (`null` if that was the nearest ocean
  cell), so plugin calls and exports do not keep using the node that failed;
- bring `#node-reset` into line with it (visible exactly when `selectedNode` is not `null`);
- keep the existing "Analysis unavailable: ..." text in `#analysis-meta`.

Tests: `test_a_failed_node_analysis_clears_the_skeleton_and_restores_the_previous_node`,
`test_a_failed_node_analysis_keeps_the_last_chosen_node_and_its_way_back`.

### R2 (I2). The last request wins

Two analysis loads can overlap (a slow one for node 1, then a fast one for node 2). Today the slow answer can arrive
last and replace the newer one while `selectedNode` still says node 2. Give `loadAnalysis` a request counter: after
every `await`, if a newer load has started (or the job changed), return without touching the page or any state,
including the failure handling of R1.

Test: `test_a_slow_earlier_node_analysis_cannot_replace_a_newer_one`.

### R3 (I3). Toggling the theme does not refetch or reset anything

`applyTheme` currently calls `loadAnalysis`, which refetches the analysis, remounts every plugin tab (so the
Long-term threshold, the Device form and results are lost) and recomputes long-term statistics. Change it to redraw
only what depends on colour: destroy and recreate the core charts (the Time-series charts and the Advanced charts
if their section is open) from the analysis already on screen (`lastAnalysis`), with no network request, and leave
plugin tabs and their state alone. Plugin charts may keep their colours until their tab is mounted again; that is
acceptable.

Test: `test_toggling_the_theme_redraws_charts_without_refetching_or_losing_input` (no request to `/analysis`,
`/nodes` or `/longterm`; the typed threshold survives; the chart is drawn again in the new colours).

### R4 (I4). The advanced charts are drawn once, whenever the section is opened

`loadAnalysis` adds a new `toggle` listener on `#advanced` for every load, so after several loads opening the
section draws several copies, and if it is already open when a load finishes nothing is drawn. Use one mechanism:
after a load, if `#advanced` is open draw its charts straight away, otherwise draw them the first time it opens;
never draw twice for the same analysis; never draw data from an earlier load.

Tests: `test_opening_advanced_after_several_loads_draws_each_chart_once`,
`test_reloading_with_advanced_already_open_draws_its_charts_again`.

### R5 (I8). One bad history record does not blank the list

`refreshRecent` calls `job.latitude.toFixed(3)`, which throws on an old record with `latitude: null`, and the empty
`catch` leaves the History section empty. Format coordinates with the existing `fmtCoord`, show `—` for a missing
`start` or `end`, and build each list item inside its own `try`/`catch` so one unusable record is skipped (with a
`console.warn`) without hiding the others.

Test: `test_an_old_job_record_without_coordinates_does_not_empty_the_history`.

### R6 (M7). A refused Cancel or Delete is reported

The Cancel and Delete handlers ignore the response, so Delete clears the page even when the server answered 409.
Add `<p id="action-error" class="form-error" role="alert" hidden></p>` to the status card (next to the action
buttons) in `templates/index.html`. In both handlers check `response.ok`; on failure show
`data.error || "Request failed (<status>)"` there (and "Could not reach the server" if `fetch` throws) and change
nothing else on the page. Hide it when a new Cancel or Delete is attempted and when another job is opened; polling
must not hide it. A successful Delete behaves as today.

Tests: `test_a_refused_delete_is_reported_and_keeps_the_job_on_screen`,
`test_a_refused_cancel_is_reported_and_stays_visible_while_polling`; control
`test_an_accepted_delete_still_clears_the_page`.

### R7 (M3). A faulty plugin cannot break the tab list or the actions

- In `panelHasContent` wrap the call to `plugin.available(...)`; if it throws, log with `console.error` and treat
  the tab as unavailable.
- In `renderActions` wrap `action.href(...)` the same way (the action is skipped).
- In `activateTab`, if `plugin.mount(...)` returns a promise, attach a rejection handler that puts the same
  "<label> failed to load: <message>" error into the panel as the synchronous path does. No unhandled rejection.

Tests: `test_a_plugin_whose_available_or_href_throws_does_not_stop_the_tabs`,
`test_a_plugin_whose_mount_rejects_shows_its_error_in_its_panel`.

### R8 (M11). The previous job's grid marker goes when another job opens

`gridMarker` is removed only when the next analysis arrives, so after switching jobs it sits on the map at the old
job's location until then. Remove it (and clear the reference) in `openJob`, and also when a job is deleted.

Test: `test_the_grid_marker_of_the_previous_job_goes_when_another_job_opens`.

## Acceptance

- `python -m pytest tests/frontend/test_state_app.py -q` passes, unchanged. (It needs Playwright's Chromium, which is
  installed on this machine; the file takes about two minutes.)
- `python -m pytest -q` passes, and `node --check static/app.js` reports nothing.
- Report the literal tails of the first two commands, the list of files you changed, and anything you were unsure
  about. Only report what you ran and saw.
- Do not commit. Do not start a server on port 5000. Do not touch `downloads/` or `devices/`.
