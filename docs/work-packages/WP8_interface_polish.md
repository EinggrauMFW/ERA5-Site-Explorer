# WP8: interface polish (tables, cards, coordinates)

Goal: fix the layout and formatting defects the orchestrator found by looking at the running app. No
behaviour or numbers change; this is presentation only.

The defects (all seen in the browser on a real single-levels job):

1. **Coordinates.** The Screening table shows latitudes and longitudes as `-9.5`, `-9`, `119`, `118.5`:
   inconsistent decimals. Everywhere a latitude or longitude of a grid node is shown (Screening table, Seasonal
   view labels, node comparison, Grid nodes table, map tooltips if any, Device and Long-term headings if any)
   it must use one formatter: the fewest decimals that keep the value exact, but at least one and at most
   three (`-9` becomes `-9.0`, `-9.25` stays `-9.25`, `119.5` stays `119.5`). Add it once in `app.js`
   (`fmtCoord`) and expose it on `window.EraExplorer` so the plugins use the same function; do not copy it.
2. **Screening actions.** The Compare and Analyse buttons wrap onto two lines and make every table row tall.
   Put them side by side, compact, without wrapping (`white-space: nowrap`), and keep the table horizontally
   scrollable on a phone. The "Colour the map by:" label wraps to two lines next to its select: keep it on
   one line at desktop widths.
3. **Device metric cards.** Five cards wrap as four plus one orphan, and labels such as "Capture Width (Energy
   Weighted)" wrap to different heights. Use an auto-fit grid so the cards fill rows evenly at any width
   (for example `grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))`), give all cards the same
   minimum height, and keep a long label on at most two lines without pushing the value around.
4. **Month labels.** Device's monthly table shows `1`..`12`; Screening shows `Jan`..`Dec`. Use month names
   everywhere a month is a row or column label (also check Long-term and Export). Reuse one list; do not
   redefine it.
5. **Numeric table headers.** In `table.grid-table` the numbers are right-aligned but their header cells are
   left-aligned, so headers float away from their columns (Device estimator table, Device monthly table,
   Grid nodes, Screening, Long-term). A header over a numeric column must be right-aligned. Do it with a class
   on the `th` set where the table is built (or one small helper that builds a header row), not with
   `nth-child` rules.
6. **Grid nodes table.** The `nearest ocean` pill sits inside the Longitude cell and pushes the number out of
   line with the column. Put the pill in its own place (for example in the first cell next to latitude, or as
   a badge on the row) so every longitude value stays aligned.

## Files you own

- `static/app.js` (only: the grid-nodes table code, the helper additions, and the `window.EraExplorer`
  export block)
- `static/app.css`
- `static/plugins/screening.js`, `static/plugins/screening.css`
- `static/plugins/device.js`, `static/plugins/device.css`
- `static/plugins/longterm.js`, `static/plugins/longterm.css`
- `static/plugins/export.js`
- `templates/index.html` only if a change cannot be done otherwise (say why)

Do not touch any Python file or any test: if a Python change seems needed, put it under "Requests to the
orchestrator".

## Requirements

- Colours come from the existing CSS variables; the result must look right in both light and dark themes.
- Use `textContent`, never `innerHTML` with data. Plugin scripts stay wrapped in their IIFE. `node --check`
  every JavaScript file you edit and report the result.
- No new dependencies and no inline styles for things a class can do.
- Layout must work at 375 px wide (phone) and at 1280 px: wide tables scroll inside their own container, the
  page itself never scrolls sideways.
- Keep the diff small and consistent with the existing style. No dead code, no leftover comments, no trailing
  whitespace (verify with `grep -nE "[ \t]+$"` on the lines you added and report what it printed).

## Verification you can do

You cannot see the page, so be exact about what you could and could not check. You may start the app on port
5090 or above with `DOWNLOADS_DIR` pointing at a scratch folder holding a copy of a job, and fetch the
JavaScript and CSS to check they load; you cannot judge appearance. State plainly that the orchestrator will
check the appearance in a browser, and list for each defect above which selector or function you changed.
