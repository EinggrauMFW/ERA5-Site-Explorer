# Work-package contract (read this first, every time)

You are implementing ONE work package of `ERA5-Site-Explorer`, a local Flask + vanilla-JS tool that
downloads ERA5 wave data and analyses it for wave-energy resource work. The orchestrator (Claude)
reviews and approves your work. Other agents work in parallel on other packages in the same folder.

Repo root: `D:\forthewave\claude_works\ecmwf-map-app` (Windows, Python 3.13, run commands from the
repo root, shell is Git Bash or PowerShell). Run tests with `python -m pytest -q` (all tests) or
`python -m pytest tests/test_<yours>.py -q`. The full suite must pass when you finish.

## Hard rules

1. **Ownership.** Create or edit ONLY the files listed as yours in your work-package file. Do not
   edit any other file, including `app.py`, `analysis.py`, `plugins.py`, `static/app.js`,
   `static/app.css`, `templates/*`, existing tests, `README.md`. If you need a change elsewhere,
   do not make it: describe it in your final report under "Requests to the orchestrator".
2. **No git.** Do not run `git add`, `commit`, `checkout`, `reset`, `stash` or anything that changes
   history or other agents' files.
3. **Do not invent.** No invented citations, standards, definitions or numbers. Where a definition is
   given in your work package, implement exactly that. Where something is not defined, choose the
   simplest defensible option, say so in the code comment and in your report, and expose it as a
   parameter. Anything you could not verify goes in the report's "Unverified" list.
4. **No network.** Tests must run offline on synthetic data. No new dependencies except `scipy`
   (already in requirements) and the standard library, numpy and pandas.
5. **Units everywhere.** Every number in an API result and every UI label states its unit.
   Hm0 in m, periods in s, energy flux J in kW/m, power in kW, energy in MWh, directions in degrees
   coming-from. Te (= Tm-1) and Tp are different quantities and never share a label.
6. **No credentials, no hard-coded paths or coordinates.**
7. **Match the surrounding code**: same style, comment density and naming as `analysis.py`,
   `wavecalc.py` and `static/app.js`. Comments explain why, not what. No dead code, no TODO stubs.
8. **JSON-safe output.** API results use `None` for NaN/inf (no `NaN` literals). Use
   `plugins._json_default` semantics (numpy scalars ok) or convert explicitly. Round for display
   (3 significant decimals) but never round inside calculations.
9. **Report back honestly.** If a test fails or something does not work, say so. Do not weaken a test
   to make it pass.

## The interfaces you build on (already implemented, do not change)

### Python: `plugins.py`

Your plugin module `plugin_<name>.py` (in the repo root) defines `register(app, ctx)`. Routes live
under `/api/jobs/<job_id>/<feature>`. Get the job with `view = ctx.job(job_id)` (or
`ctx.job(job_id, "wave-spectra")` to require a product; wrong product gives HTTP 409, unknown job
404, unfinished job 409). `ctx.job` reads the optional query parameters `node_lat` and `node_lon`
itself: if present it analyses that grid node, otherwise the nearest ocean cell. You do not parse them.

`view` (a `plugins.JobView`) offers:

| member | meaning |
|---|---|
| `view.id`, `view.product`, `view.directory`, `view.latitude`, `view.longitude`, `view.files`, `view.node` | the job; `view.node` is `(lat, lon)` or `None` |
| `view.frame()` | canonical per-record `DataFrame` (below) for this node |
| `view.raw_frame()` | the route's own frame (original column names, e.g. `swh`, `mwp`, `flux_dir_00`) |
| `view.analysis()` | the analysis JSON payload shown on the Analysis page (`series`, `sections`, `notes`, `warnings`, `grid_coordinate`, `depth_m`, ...) |
| `view.nodes()` | grid-node summary: `nodes` list of `{lat, lon, valid, hm0, te, flux, depth, distance_km}` |
| `view.provenance()` | parsed `provenance.json` (may be `{}`) |
| `view.save_json(name, obj)` / `view.load_json(name, default)` | per-job results in `<job dir>/results/` |
| `view.save_report_section(name, markdown)` / `view.report_sections()` | markdown fragments appended to `report.md` by the export package (file name sorts the order) |

Canonical frame columns (UTC `DatetimeIndex`, sorted, unique; NaN where unavailable):

| column | unit | Option A (single levels) | Option B (spectra) |
|---|---|---|---|
| `hm0` | m | `swh` | 4 sqrt(m0) |
| `te` | s | `mwp` (= Tm-1) | m-1/m0 |
| `tp` | s | `pp1d` | parabolic-fit peak |
| `dir_from` | deg | `mwd` | energy-weighted mean |
| `flux` | kW/m | 0.4906 Hm0^2 Te | finite-depth integral |

`frame.attrs` has `product`, `route`, `time_step_hours`. Option B frames also keep `flux_dir_00..23`.
Constants: `plugins.HOURS_PER_YEAR = 8766.0`; flux coefficient is `wavecalc.FLUX_COEFFICIENT` (0.4906).
Read-only helpers you may import: `wavecalc`, `analysis` (e.g. `analysis.MIN_RECORD_YEARS`,
`analysis.haversine_km`, `analysis.split_files`, `analysis.netcdf_paths`, `analysis.spectra_variable`,
`analysis.read_depth_grid`), `plugins`. Raise `ValueError("clear message")` for bad input (HTTP 422).

### JavaScript: `window.EraExplorer` (defined in `static/app.js`)

Your script `static/plugins/<name>.js` is loaded after `app.js` and registers itself:

```js
EraExplorer.registerTab({ id, label, order, available(ctx) { return true; }, mount(panel, ctx) { /* build DOM in panel */ } });
EraExplorer.registerAction({ id, label, order, href(ctx) { return url_or_null; } });   // header links
```

`mount` runs once, the first time the tab is shown for the analysis on screen. `ctx` is
`{jobId, product, route, node, analysis, nodeData}` (`route` is `"single-levels"` or `"wave-spectra"`).
Helpers: `EraExplorer.api(path, options)` (fetch JSON, appends the selected grid node, throws `Error`
with the server message), `EraExplorer.nodeQuery()`, `EraExplorer.colourNodes({label, values})`
(`values` keyed by `EraExplorer.nodeKey(lat, lon)`; `null` resets), `EraExplorer.selectNode(lat, lon)`,
`EraExplorer.chartTheme()` (colours for uPlot), `EraExplorer.trackChart(chart)` (makes a uPlot chart
follow resizes), `EraExplorer.palette()` (four theme-aware colours), `EraExplorer.fmtNum(v)`,
`EraExplorer.fmt(v, unit)`, `EraExplorer.node(tag, className, text)` (create element).
uPlot is available as a global; charts must read colours from `EraExplorer.chartTheme()` so they work
in light and dark. Use `textContent` (never `innerHTML` with data). **Wrap the whole file in an IIFE** (`(function () { 'use strict'; ... })();`): all scripts share the page's global scope, and a top-level `const node` or `function fmt` collides with `app.js` and stops the plugin from loading (`node --check` cannot see this). Existing CSS classes you can use:
`.card`, `.section-block`, `.sections`, `table.kv`, `table.grid-table` (+ `td.num`), `.field`, `.btn`
(`.secondary`, `.primary`, `.ghost`), `.hint`, `.warnings`, `.form-error`, `.pill`, `.scroll`,
`.metrics`/`.metric`, `.chart`, `.charts`. Colours come from CSS variables (`--ink`, `--muted`,
`--line`, `--surface`, `--surface-2`, `--surface-3`, `--accent`, `--sea`, `--chart-line`, ...); never
hard-code colours. Extra styles go in your own `static/plugins/<name>.css` (loaded automatically if it
exists), written with those variables so dark mode works.

## Test conventions

* Synthetic data only. Helpers exist: `tests/test_analysis.py` has `wave_dataset`, `spectra_dataset`,
  `wind_dataset`, `bathymetry_file`; `tests/test_provenance_crosscheck.py` has `make_job(job_id,
  product, builder)`, `build_a`, `build_b` and shows the Flask `client` fixture pattern (copy the
  fixture into your own test file; also see `tests/test_nodes.py`, `tests/test_plugins.py`).
* Test the numerics against independently computed values (hand-derived small cases), not against
  the function's own output. Include edge cases: all-NaN, empty, one record, NaN gaps.
* Test the HTTP routes with the Flask test client, including error cases (bad input gives 422).
* Frontend code cannot be unit-tested here; keep it small and defensive, run `node --check` on it.

## What to include in your final reply

1. Files created (and confirmation that you touched nothing else).
2. How each requirement of the work package is met (one line each) and where.
3. The command you ran and its result: `python -m pytest -q` (full output tail).
4. Decisions you had to make where the spec was silent.
5. "Unverified" list and "Requests to the orchestrator" list.
