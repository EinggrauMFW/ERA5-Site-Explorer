# WP19: keyboard and screen-reader access

Part 2 of the front-end review, first half. The reviewer checked structure (names, roles, keys); this package fixes
what was missing. Nothing here changes a number or the look of the page.

## Who does what

- Claude (orchestrator) wrote `tests/frontend/test_accessibility.py` from this spec before any implementation.
  The tests drive Chromium against the real app and are red on the current code (9 red, 3 controls that must keep
  passing). You (Antigravity, the implementer) must NOT edit, delete, rename, skip or weaken them, and must not edit
  any other file under `tests/`. If you think a test is wrong, say so under "Requests to the orchestrator".
- The orchestrator re-runs everything and checks the result in a browser.

## Rule: change only what is listed

Do not change any behaviour that is not named below: no tab switching, no focus moves, no scrolling, no restyling,
no wording changes except where a requirement gives the wording. Anything else you think is needed goes under
"Requests to the orchestrator", not into the code. Never use `innerHTML` with data.

## Files you own

`static/app.js`, `static/plugins/device.js`, `static/plugins/devices.js`, `static/plugins/screening.js`, and, only if
moving an element out of a label needs it, `static/plugins/device.css`. Do not touch `templates/`, Python files or
`tests/`.

## Requirements

### R1. Every form control and icon-only button has an accessible name

A control's name comes from a `<label for>` (or a wrapping label), `aria-label` or `aria-labelledby`. Controls that
are `aria-hidden` (the hidden `<select id="product">` behind the segmented control) are exempt.

- **Device tab (`device.js`).** The labels are siblings of their inputs with no `for`. Give every label a `htmlFor`
  that matches its control's id (`device_name`, `device_csv`, `device_rated`, `device_width`, `device_period`,
  `device_bin`). The CSV label currently contains the "use the synthetic example" link and the "Choose from
  catalogue…" button: interactive elements must not sit inside a label. Keep the label text to "Power Matrix CSV" and
  put the link and the button next to it, in the same visual place (a small wrapper element is fine). Keep their ids
  `use_example_csv` and `device_catalogue`.
- **Picker settings (`devices.js`).** The three "Settings used when picked" fields (rated power, width, period axis)
  have labels without `for` and the inputs have no id. Give each input an id unique in the document (for example
  `device-picker-rated`, `-width`, `-period`) and point its label at it.
- **Picker list (`devices.js`).** The `role="listbox"` list gets `aria-label="Devices"`.
- **Screening (`screening.js`).** The "Colour the map by" `<select>` gets `aria-label="Colour the map by"`.
- **Icon-only `×` buttons.** The compare chip's remove button gets `aria-label` "Remove <lat>, <lon> from the
  comparison" (use the coordinates shown in the chip), and the catalogue chip's clear button in `device.js` gets
  `aria-label="Clear the chosen catalogue device"`.

Tests: `test_the_request_form_and_cross_check_controls_have_names` (control),
`test_the_device_form_controls_have_names`, `test_the_picker_settings_controls_and_list_have_names`,
`test_the_screening_controls_have_names`, `test_icon_only_buttons_say_what_they_do`,
`test_no_label_in_the_device_form_contains_a_button_or_link`.

### R2. The data product can be chosen with the keyboard

In `buildSegmented` (`app.js`) the segmented radios use a roving tabindex but have no key handling, so only the
checked one can be focused and arrow keys do nothing. Add a `keydown` handler to each radio button: ArrowRight and
ArrowDown select the next option, ArrowLeft and ArrowUp the previous one (both wrap round), Home the first, End
the last. Selecting means exactly what a click does today, and focus moves to the newly checked button. Call
`preventDefault()` for the keys you handle.

Test: `test_arrow_keys_home_and_end_change_the_data_product`.

### R3. Smooth scrolling respects reduced motion

`app.js` calls `scrollIntoView({ behavior: 'smooth', ... })` in `openJob` and `loadAnalysis`, which overrides the CSS
rule that switches smooth scrolling off for users who ask for reduced motion. Use `'auto'` instead of `'smooth'`
when `window.matchMedia('(prefers-reduced-motion: reduce)').matches`. Everything else about those calls stays.

Tests: `test_the_page_does_not_scroll_smoothly_when_the_user_asks_for_reduced_motion`; control
`test_the_page_scrolls_smoothly_by_default`.

### R4. A greyed-out navigation link is not a tab stop

`updateNav` toggles the `is-off` class on the top navigation links; CSS makes them unclickable but they stay in the
tab order. While a link is off set `tabindex="-1"` and `aria-disabled="true"` on it; when it is on remove both (the
link has no `tabindex` attribute when it is on, and `aria-disabled` is absent). This must also hold at start-up.

Tests: `test_navigation_links_that_are_off_are_not_focusable_and_say_so`; control
`test_navigation_links_become_available_again_when_their_section_appears`.

### R5. Space activates a node row like Enter

In the Grid nodes table the valid rows have `tabindex=0` and react to Enter. Make Space do the same
(`preventDefault()` so the page does not scroll).

Test: `test_space_on_a_node_row_analyses_that_node`.

## Not changed here (decisions)

- Touch target sizes under 40 px (review M8): a spacing decision for the whole page; left for the owner.
- Table semantics of `.device-card table { display: block }` and giving rows a `role`: not changed.

## Acceptance

- `python -m pytest tests/frontend/test_accessibility.py -q` passes, unchanged (about 50 seconds; it needs
  Playwright's Chromium, installed on this machine).
- `python -m pytest -q` passes, and `node --check` reports nothing for each changed `.js` file.
- Report the literal tails of the first two commands, the output of the `node --check` runs, the list of files you
  changed (only the files named above), and anything you were unsure about. Only report what you ran and saw.
- Do not commit. Do not run `pip install` or change the Python environment (Playwright and Chromium are already
  installed). Do not start a server on port 5000. Do not touch `downloads/` or `devices/`.
