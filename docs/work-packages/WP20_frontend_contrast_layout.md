# WP20: readability and layout

Part 2 of the front-end review, second half: text that is too faint to read, numbers formatted badly, and three
layout problems. The tests measure real colour contrast in a browser (WCAG AA is 4.5:1 for body text); the
numbers below are what the reviewer found and the tests reproduced.

## Who does what

- Claude (orchestrator) wrote `tests/frontend/test_layout_contrast.py` from this spec before any implementation.
  The tests drive Chromium against the real app and are red on the current code (20 red, 16 controls that must keep
  passing). You (Antigravity, the implementer) must NOT edit, delete, rename, skip or weaken them, and must not edit
  any other file under `tests/`. If you think a test is wrong, say so under "Requests to the orchestrator".
- WP19 (accessibility) is already done and committed: do not undo it.
- The orchestrator re-runs everything and checks the result in a browser.

## Rule: change only what is listed

Do not change any behaviour or appearance that is not named below: no tab switching, no focus moves, no scrolling,
no other colours, no wording changes, no layout changes outside the elements named. Anything else you think is
needed goes under "Requests to the orchestrator", not into the code. Never use `innerHTML` with data.

## Files you own

`static/app.css`, `static/app.js` (only `fmtNum`, `fmtCoord`, the scatter table colours in `renderScatter`, and the
chart minimum widths in `addChart`/`resizeCharts`), `static/plugins/screening.js` and `screening.css`,
`static/plugins/devices.js` and `devices.css`. Do not touch `templates/`, Python files or `tests/`.

## Requirements

### R1. Faint text and the running pill meet AA in both themes

Measured today: `--faint` on `--surface` is 3.63:1 in the light theme (3.19 on `--bg`, 3.25 on `--surface-2`, 2.94 on
`--surface-3`) and 4.36 on `--surface-3` in the dark theme; the light-theme `pill-running` is 4.34. `--faint` is used
for secondary text (`.metric small`, `.job-id`, `.group-title`, `.unit`, `.muted`, `table.kv small`, `.segmented
small`, `.foot`).

Change the colour values in `app.css` (both the `:root[data-theme="dark"]` block and the
`prefers-color-scheme: dark` copy, and the light block) so that `--faint` is at least 4.5:1 on `--bg`, `--surface`,
`--surface-2` and `--surface-3` in both themes, and the light `pill-running` text on its background is at least 4.5:1.
Keep the hierarchy: `--faint` must stay less prominent than `--muted` (lower contrast on `--surface`). Change only
the colour values (no new tokens, no other properties).

Tests: `test_faint_text_meets_aa_on_every_surface[...]`, `test_the_running_pill_meets_aa[light]`; guards
`test_faint_text_stays_less_prominent_than_muted_text[...]` and the passing dark cases.

### R2. Heat-cell text is readable at every value, in both themes

Measured: in the Screening seasonal table the text (`--ink`) on the strongest cell is 1.41:1 (light) and 1.46:1
(dark); in the scatter table (Distributions) the flip to the surface colour at 55 % of the peak gives 3.25 to 4.7:1
in light and 3.9 in dark. Both tables colour a cell with `color-mix(in srgb, var(--chart-line) N%, transparent)`.

Make every numeric cell of both tables reach 4.5:1 against its own composited background, in both themes, **and still
after the user toggles the theme without reloading** (the cells are not rebuilt on a toggle, so the colours must be
decided by CSS variables or by a fill that is safe in both themes, not by JavaScript that bakes in the colours of
the theme at creation time). One approach that fits: cap the fill share so the normal text colour always passes
(roughly 45 %), and remove the text-colour flip in `renderScatter`. The heat still has to show the ordering of the
values.

Test: `test_heat_cell_text_stays_readable_in_both_themes`.

### R3. The Screening comparison chart fits the screen

At a 375 px wide viewport comparing two nodes makes the page 483 px wider than the screen (301 px at 557 px): the
chart is created 800 px wide and not resized. Take its width from its container (`compare-chart-container`) and let
it follow the container when the window changes (`trackChart` already makes the chart follow `resizeCharts`).
`addChart` and `resizeCharts` in `app.js` use `Math.max(280, ...)` as the minimum chart width: that minimum must never
make a chart wider than its container (use a smaller minimum, for example 200). The page must not scroll
horizontally at 375 px or 557 px.

Tests: `test_the_comparison_chart_does_not_widen_the_page[375]` and `[557]`.

### R4. The seasonal values have their own table

In the Screening "Seasonal View" the "Seasons" row sits in the month table with `colSpan = 3` cells, so DJF, MAM, JJA
and SON appear under January to December (MAM under Apr to Jun) and December/January/February are not adjacent.
Remove the "Seasons" row and the `colSpan` cells from the month table (one row per node, as now but without the
seasons row). Add a second table with classes `heat-table seasons`, header row `Node`, `DJF`, `MAM`, `JJA`, `SON`,
one row per node in the same order as the month table, each cell showing the season's mean flux (or `—`) with the
same heat colouring as the month cells (R2 applies to it too). Put it directly after the month table, in the same
card.

Test: `test_seasons_are_shown_in_their_own_table_with_season_headings`.

### R5. Number formatting

In `app.js`:

- `fmtNum` returns `—` for a missing or non-numeric value, including an empty or whitespace-only string (it prints
  `0.00` today). It never prints a negative zero: a value that rounds to zero prints as `0.00` (`fmtNum(-0.001)`,
  `fmtNum(-0)`). The number of decimals follows the magnitude of the **rounded** value: 100 or more has none, 10 or
  more has one, otherwise two (so `99.96` prints `100`, `-99.96` prints `-100`, `9.996` prints `10.0`, and
  `99.94` still prints `99.9`). Thousands separators stay as they are (`1,235`).
- `fmtCoord` returns `—` for an empty string too, and never prints a negative zero (`fmtCoord(-0.0004)` prints
  `0.0`). Other values stay as they are.

Tests: `test_number_formatting[...]` (the cases that already pass must keep passing).

### R6. The heatmap tooltip appears at the pointer

The device picker's tooltip is positioned with offsets measured from the SVG but its container is not the
positioning context, so it appears about 285 px away from the pointer in a wide dialog. Make `.heatmap-container`
the positioning context (`position: relative` in `devices.css`) and compute the tooltip's `left` and `top` in
`devices.js` relative to that container, so it sits at the pointer (within 60 px) wherever the dialog is.

Test: `test_the_heatmap_tooltip_is_next_to_the_pointer`.

## Not changed here (decisions)

- Colour contrast of non-text parts (chart lines, hatch patterns) and the hard-coded `rgba(120,150,60,.9)` and
  `%235b6863` colours (review M4): not measured, left.
- Touch target sizes (review M8): left for the owner.

## Acceptance

- `python -m pytest tests/frontend/test_layout_contrast.py -q` passes, unchanged (about 70 seconds; it needs
  Playwright's Chromium, installed on this machine).
- `python -m pytest -q` passes (including WP17-WP19's browser tests), and `node --check` reports nothing for each
  changed `.js` file.
- Report the literal tails of the first two commands, the output of the `node --check` runs, the list of files you
  changed (only the files named above), and anything you were unsure about. Only report what you ran and saw.
- Do not commit. Do not run `pip install` or change the Python environment. Do not start a server on port 5000.
  Do not touch `downloads/` or `devices/`.
