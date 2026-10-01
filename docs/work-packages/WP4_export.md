# WP4: report and table export

Read `CONTRACT.md` first. Everything the Analysis page shows currently exists only on screen. This
package exports it: tables as CSV, the rose and scatter diagrams as SVG, a `report.md`, and a bundle that
contains all of it with the provenance.

## Files you own (create only these)

`report.py`, `plugin_export.py`, `static/plugins/export.js`, `tests/test_export.py`.

## 1. Data source

Everything comes from `view.analysis()` (the payload with `sections`, `series`, `notes`, `warnings`,
`grid_coordinate`, `requested_coordinate`, `depth_m`, ...), `view.provenance()`, `view.frame()` /
`view.raw_frame()` and `view.report_sections()` (markdown fragments other packages save, such as device
and long-term results). **Do not recompute any statistic.** Each payload section is
`{kind, title, group, ...}` with `kind` one of `kv` (`rows: [{label, value, note}]`), `table` (`columns`,
`rows`, `note`), `scatter` (`x_edges`, `y_edges`, `hours_pct`, `energy_pct` as matrices `[x_bin][y_bin]`
where x is Hm0 in m and y the period in s, `x_label`, `y_label`, `weight_label`, `note`), `rose`
(`sector_labels` in degrees coming-from, `series: [{label, values}]`, `unit`, `note`) or `line` (`x`,
`y: [{label, values}]`, `x_label`, `note`). Read `analysis.py` (`scatter_section`, `rose_section`,
`_swap_scatter_axes`) to see exactly how they are built; look at `static/app.js` (`renderScatter`,
`renderRose`) for how the browser draws them.

## 2. Tables (`report.py`)

Functions returning `(filename, csv_text)` or `None` when the section is absent:

* `scatter_csv(payload, which)` for `which` in `hm0-te` and `hm0-tp`: long format, one row per cell:
  `hm0_lower_m, hm0_upper_m, period_lower_s, period_upper_s, records_pct, energy_pct` (bins are half-open
  `[lower, upper)`). The CSV itself has no comment lines (it must stay machine-readable); the bin convention
  is stated in `README_export.txt` of the bundle. Match the section by its title
  (`Hm0 vs Te` / `Hm0 vs Tp`); the period column name must say `te` or `tp` accordingly
  (`te_lower_s` ... or `tp_lower_s` ...). Option A has only the Te scatter.
* `rose_csv(payload)`: one row per sector: `direction_from_deg` then one column per series, named from the
  series label with its unit slug.
* `monthly_csv(payload)`, `flux_by_period_csv(payload)`, `partitions_csv(payload)`, `metrics_csv(payload)`
  (the `series` summary: key, label, unit, mean, p95, maximum), `nodes_csv(view)` is NOT yours (screening
  owns it).
* Values are written as plain numbers (not rounded further), units in column names, UTF-8, `\n` line
  endings, `None` as an empty cell. Use the `csv` module.

## 3. Figures (SVG, `report.py`)

Deterministic, dependency-free SVG strings (no matplotlib):

* `rose_svg(payload, theme="light")`: polar bar chart like `renderRose` in `static/app.js` (compass labels
  N/E/S/W, rings, bars for each series, legend, title, caption "Direction is where waves come from;
  clockwise from true north"). `theme` is `light` or `dark` (explicit colours, since an SVG file has no
  page CSS variables). The same angular geometry as the browser: sector centre `c` degrees, bar spans
  `+-width/2`, x = cx + r sin(c), y = cy - r cos(c).
* `scatter_svg(payload, which, quantity="records"|"energy", theme="light")`: a heat map of the matrix with
  axis labels with units, bin edge ticks, a colour scale legend and a caption stating that bins are
  half-open and what the percentages are of.
* Escape all text for XML. The output must be valid XML (tests parse it with `xml.etree.ElementTree`).

## 4. `report.md` (`report.build_report(view) -> str`)

GitHub-flavoured markdown, no HTML. Sections in this order:

1. Title: `# ERA5 wave resource report` and a metadata table: job id, route (Option A single levels /
   Option B 2D spectra), requested site (lat, lon), analysed grid node and its distance (km), model depth
   (m) if known, period, number of records, time step (h), record length (years), data coverage (%),
   generated (UTC, ISO), and whether a non-default grid node was chosen.
2. Warnings (a bullet list) if any, directly under the metadata.
3. Key results: a table of the series in `payload["series"]` that are not `advanced` and not circular:
   name, unit, mean, P95, maximum (empty where `None`).
4. Every payload section in the order of `payload["sections"]`, each as `## <title>` followed by its
   rendering: `kv` as a two-column table with the note in a third column or in italics below; `table` as a
   markdown table (escape `|` in cells, right-align numeric columns with `---:`); `scatter` as two markdown
   tables (share of records %, share of the energy %) with rows Hm0 descending and columns the period bins,
   empty cells for 0, plus the section note; `rose` as a table of sector vs each series; `line` as a table.
   Embed no images; add a line "Figures: `figures/rose.svg`, `figures/scatter-hm0-te-energy.svg` in the
   bundle" only in the bundle version (parameter `bundle=True`).
5. Definitions and limits: the payload `notes` as a list.
6. Provenance: product, datasets with DOI, expver policy, area (N, W, S, E), period, time step, number of
   CDS requests, per-file name, size and SHA-256 (from `provenance["requests"]`), model-depth file, cdsapi and
   Python versions, `created_utc`/`completed_utc`, the licence statement and URL. If `provenance.json` is
   missing say "No provenance.json for this job" and do not invent anything.
7. Additional sections from other tools: for each `(name, markdown)` in `view.report_sections()` append the
   fragment verbatim under a `---` rule.
8. A closing "How to read this report" paragraph: ERA5 is a reanalysis not a measurement; offshore node;
   the energy period Te is Tm-1; direction conventions; a short record is not a resource estimate. Keep it to
   five lines.

Numbers appear exactly as in the payload with units; use `report.fmt_number` (3 significant decimals for
display, thousands separators none) consistently. No statistic may be computed in `report.py` beyond
formatting and the percentage cells already in the payload.

## 5. API (`plugin_export.register`)

All routes are `GET`, node-aware (via `ctx.job`), and send attachments with sensible filenames that
include the job id (and `_<lat>_<lon>` when a node is selected):

* `/api/jobs/<id>/export/report.md` (`text/markdown; charset=utf-8`)
* `/api/jobs/<id>/export/tables/<name>.csv` with `name` in `scatter-hm0-te`, `scatter-hm0-tp`, `rose`,
  `monthly`, `flux-by-period`, `partitions`, `metrics`; unknown or missing-section tables are 404 with a JSON
  error that names the available ones
* `/api/jobs/<id>/export/figures/rose.svg`, `.../figures/scatter-hm0-te.svg`, `.../figures/scatter-hm0-tp.svg`
  with optional `?theme=dark&quantity=energy`
* `/api/jobs/<id>/export/bundle.zip`: `report.md` (bundle version), `README_export.txt` (what each file is,
  the half-open bin convention, units, the direction convention, generation time), `tables/*.csv` (every table
  that exists), `figures/*.svg` (rose and the scatter heat maps, light theme), `provenance.json` (verbatim
  copy), `timeseries.csv` (the per-record series of this node: `view.raw_frame()` as CSV with `time` first
  column), `analysis.json` (the payload). Use `zipfile.ZIP_DEFLATED`, fixed entry timestamps are not
  required. Build in memory with `io.BytesIO`; refuse (HTTP 413 JSON) if the uncompressed size would exceed
  200 MB.

## 6. UI (`static/plugins/export.js`)

Register header actions (no tab) with `EraExplorer.registerAction`: "↓ Report (.md)" and "↓ Bundle (.zip)",
`order` 10 and 11, hrefs built from `ctx.jobId` with the node query (`EraExplorer.nodeQuery()`), shown only
when `ctx.analysis` exists. Because actions are plain links, also add a small "Export" tab
(`registerTab`, id `export`, label "Export", `order: 90`) listing every download with a one-line
description: report, bundle, each table CSV (only those the analysis has: decide from `ctx.analysis.sections`
titles), each figure with a light/dark choice and a records/energy choice for scatter figures. Use
`.section-block` and `table.kv` styling. Links must carry the node query.

## 7. Tests (`tests/test_export.py`), at least

* `scatter_csv`: row count = bins; a hand-checked cell (hours and energy values equal the matrix entries);
  column names carry units; the Tp variant is named `tp_*`; a missing section returns `None`.
* `rose_csv`, `metrics_csv`, `monthly_csv` against the payload.
* SVG: parses as XML for both routes and both themes; contains the title and N/E/S/W labels; text with
  `&`, `<` and quotes in a series label is escaped; a rose bar for a known sector has the expected endpoint
  coordinates (compute by hand).
* `build_report`: contains each section title from the payload, the warning text, the node distance, the
  DOI strings from a provenance fixture, file hashes, the device fragment appended when
  `save_report_section` was used, and no `None`/`nan` literals; markdown tables have consistent column counts
  (parse the pipes); `|` in a cell is escaped; works with an empty provenance.
* HTTP with a synthetic job for both routes (`build_a`, `build_b`): every route returns 200 with the right
  content type and filename; the bundle zip contains the expected entries and its `timeseries.csv` row
  count equals the frame length; unknown table 404 JSON; a node query changes the filename and the report's
  grid node; dry-run job 409.
