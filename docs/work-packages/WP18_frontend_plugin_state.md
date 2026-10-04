# WP18: plugin state bugs (Screening, Long-term, Device, device picker)

Follow-up to WP17 for the plugin scripts. The same front-end review reproduced five state problems in
`static/plugins/*.js`. As in WP17, nothing here changes a number; each leaves the page showing something untrue or
lets a click do the wrong thing.

## Who does what

- Claude (orchestrator) wrote `tests/frontend/test_state_plugins.py` from this spec before any implementation. The
  tests drive Chromium against the real app and are red on the current code (8 red). You (Antigravity, the
  implementer) must NOT edit, delete, rename, skip or weaken them, and must not edit any other file under `tests/`.
  If you think a test is wrong, say so under "Requests to the orchestrator".
- The orchestrator re-runs everything and checks the result in a browser.

## Rule: change only what is listed

Do not change any behaviour that is not named below. In particular do not switch tabs, move focus, scroll, rename
elements, change wording other than where a requirement gives it, or restyle anything. A change you think is
needed but is not listed goes under "Requests to the orchestrator", not into the code. (In WP17 an unrequested tab
switch had to be removed again.)

## Files you own

`static/plugins/screening.js`, `static/plugins/longterm.js`, `static/plugins/devices.js`, `static/plugins/device.js`,
and ONE line in `static/app.js` (R1, `openJob`). Do not touch other parts of `app.js`, `templates/`, Python files or
`tests/`. Never use `innerHTML` with data.

## Requirements

### R1 (I5). The Screening tab never shows another job's statistics

`mount` in `screening.js` is asynchronous and writes into the shared panel and into module-level state
(`screeningData`, `comparedNodes`, `compareContainerEl`, `mapStat`) with no check that the job is still the one
on screen. If job A's screening request is slow and the user opens job B, A's answer overwrites B's panel.

- At the start of `mount` reset that state (`screeningData = null`, `comparedNodes = []`, `compareContainerEl =
  null`, `mapStat` back to its initial value) and remember `ctx.jobId`.
- After the `await`, if `EraExplorer.context().jobId !== ctx.jobId`, return without touching the panel or the
  map colours. The same check applies in any callback that runs after an await.
- In `static/app.js` `openJob`, reset the plugin colouring of the map nodes before `clearNodes()` (set
  `nodeColours = null`), so the colours and legend Screening set for the previous job are not shown for the new
  one. This is the only change allowed in `app.js`.

Tests: `test_a_slow_screening_response_for_the_previous_job_does_not_overwrite_the_new_job`,
`test_compared_nodes_and_map_colours_do_not_survive_a_job_switch`.

### R2 (I14). The Long-term status line returns to normal after an error

On an error `longterm.js` sets `status.className = 'form-error'`, replacing the `lt-status` class, and never
resets it. Use `'lt-status form-error'` for the error state and set the class back to `'lt-status'` at the start of
every load. Nothing else changes.

Test: `test_the_longterm_status_line_is_not_left_in_its_error_style`.

### R3 (I12). The picker's notes are shown once, and worded correctly

`renderList` in `devices.js` inserts the "catalogue folder" note and the "N files were skipped" `<details>` into the
sidebar every time the list is rendered, and nothing removes them, so they pile up when the picker is opened again.
Mark the inserted notes with a class (for example `device-picker-note`) and remove every element with that class
from the sidebar at the start of `renderList`. Word the summary as `1 file was skipped` for one and
`N files were skipped` otherwise.

Tests: `test_picker_notes_do_not_pile_up_when_it_is_opened_again`,
`test_the_skipped_files_note_uses_the_right_singular_and_plural`.

### R4 (I13). The heatmap cursor is reset when another device is selected

`focusedCell` in `devices.js` is module-level and `renderDetail` does not reset it, so after selecting a device with
a smaller matrix the first arrow key uses a cell that no longer exists and throws. Set `focusedCell = null` at the
start of `renderDetail`.

Test: `test_arrow_keys_on_a_newly_selected_device_do_not_use_the_previous_cursor`.

### R5 (M7). The Device form: one request at a time, and errors are announced

In `device.js`:

- while an Assess request is running, set the Assess button `disabled` (and `aria-busy="true"`), ignore further
  clicks, and restore it when the request ends, whether it succeeded or failed;
- give the form's error element `role="alert"`.

Tests: `test_a_second_click_on_assess_while_one_is_running_sends_no_second_request`,
`test_a_device_form_error_is_announced`.

## Not changed here (decisions)

- Double-clicking a device while its detail is still loading can pick the previous one (review M2): not seen with
  a local server, about 8 ms per detail request.
- Overlapping Long-term Compute requests letting an older answer win: the reviewer could not reproduce it.

## Acceptance

- `python -m pytest tests/frontend/test_state_plugins.py -q` passes, unchanged (about a minute; it needs
  Playwright's Chromium, installed on this machine).
- `python -m pytest -q` passes, and `node --check` reports nothing for each changed `.js` file.
- Report the literal tails of the first two commands, the output of the `node --check` runs, the list of files you
  changed (it must be only the files named above), and anything you were unsure about. Only report what you ran
  and saw.
- Do not commit. Do not run `pip install` or change the Python environment (Playwright and Chromium are already
  installed). Do not start a server on port 5000. Do not touch `downloads/` or `devices/`.
