# WP1: device performance (power matrix, annual energy, capture width)

Read `CONTRACT.md` first. This package lets the user upload a power matrix CSV and see what a device
would produce from the resource at the selected grid node.

## Files you own (create only these)

`device.py`, `plugin_device.py`, `static/plugins/device.js`, `static/plugins/device.css`,
`tests/test_device.py`.

## 1. Power matrix CSV (`device.parse_power_matrix`)

Wide format. First row: one header cell (ignored, e.g. `Hm0\Te`) followed by the **period values**
(s). Each later row: the **Hm0 value** (m) followed by the power (kW) for each period. Blank or `NaN`
or `-` cells mean "undefined" (not zero). Delimiter: comma, semicolon or tab (detect from the first
line). Decimal point only (a decimal comma in a comma-delimited file is an error with a clear message).
Ignore blank lines and surrounding whitespace; strip a UTF-8 BOM.

```
Hm0\Te, 6, 7, 8, 9, 10
0.5, 0, 0, 0, 0, 0
1.0, 5, 8, 10, 11, 10
1.5, 12, 18, 22, 24, 22
```

`bin_convention` parameter: `"centres"` (default; header values are bin centres) or `"lower_edges"`
(header values are the lower edges of half-open bins). Build bin edges from the axes:

* centres: interior edges are midpoints between neighbouring centres; the outer edges extend by half
  the adjacent spacing.
* lower_edges: the values are the lower edges; the final upper edge is the last value plus the last
  spacing.

Return a dataclass `PowerMatrix(hm0_edges, period_edges, power_kw, hm0_centres, period_centres)`
(numpy arrays; `power_kw` has shape `(n_hm0, n_period)` with NaN for undefined cells).
Validation (raise `ValueError` with a specific message): at least 2 periods and 2 wave heights;
axes strictly increasing; numeric cells only; power >= 0; at most 200 x 200 cells.

## 2. Looking up power (`device.lookup_power`)

`lookup_power(matrix, hm0, period, method)` vectorised over numpy arrays, returns
`(power_kw, inside)` where `inside` is a boolean array.

* `method="bin"`: the cell is found with half-open bins `[lower, upper)` (a value exactly on an
  interior edge belongs to the upper bin; the last upper edge is exclusive too). A record is
  **inside** if it falls in the matrix extent AND the cell is not undefined. Outside records get
  power 0 and `inside=False`.
* `method="bilinear"`: bilinear interpolation between the four neighbouring cell **centres**. A
  record is inside only if it lies within the extent of the centres AND all four neighbours are
  defined. Outside records get power 0 and `inside=False`.

NaN in `hm0` or `period` gives `inside=False`, power 0 and is counted as an invalid record, not as
"outside the matrix" (see counts below).

## 3. Assessment (`device.assess_device`)

```python
assess_device(frame, matrix, *, name, rated_kw=None, width_m=None, period_type="unknown",
              min_flux_kw_m=1.0) -> dict
```

`frame` is the canonical frame (`hm0`, `te`, `tp`, `flux`). `period_type` is `"te"`, `"tp"` or
`"unknown"`. **If `"unknown"`, evaluate both**: Te-indexed and Tp-indexed, and report the spread as the
bound of the ambiguity. If `"te"` or `"tp"`, evaluate only that one. Never blend them.

For each period indexing (`"te"`, `"tp"`) and each method (`"bin"`, `"bilinear"`) report:

* `records_valid`: records with finite `hm0`, period and `flux`.
* `records_inside`, `share_inside_pct`, `share_outside_pct` (of valid records).
* `flux_outside_support_pct`: sum of `flux` over valid records outside, divided by the sum over all
  valid records, times 100. This is the share of the *resource* the matrix cannot see.
* `mean_power_kw`: mean over valid records of the looked-up power (outside records count as 0 kW).
* `aep_mwh`: `mean_power_kw * 8766 / 1000` (annual energy production, MWh/year, from the sampled
  record; records are equally weighted, so state the sampling in `notes`).
* `capacity_factor_pct`: `100 * mean_power_kw / rated_kw`, or `None` without `rated_kw`.
* Capture width (m), two estimators, each with its definition string in the result:
  * `capture_width_energy_weighted_m` = `sum(P) / sum(J)` over valid records with `flux > 0`
    (ratio of totals; P in kW, J in kW/m, result in m).
  * `capture_width_mean_of_ratios_m` = mean of `P / J` over valid records with
    `flux >= min_flux_kw_m` (excludes near-calm records that make the ratio explode; report how many
    records were used).
  * If `width_m` is given, also the capture width ratios `... / width_m` (dimensionless) for both.
    The definition of "characteristic width" is the user's: say so in the result notes.
* `occupancy_pct`: the share of valid records falling in each matrix cell (same shape as the matrix,
  using the `"bin"` lookup extents; records outside are not in this table), and `energy_pct`: the
  share of the *flux* in each cell. Include `hm0_edges`, `period_edges` so the UI can draw them.
* `monthly`: for calendar months 1..12: records, mean power (kW), share of the annual energy (%).

Top-level result keys: `name`, `period_type`, `period_labels` (`{"te": "Te = Tm-1 ...", "tp": "Tp ..."}`
naming the source variable of this job's route, e.g. Option A `mwp`/`pp1d`), `indexings` (dict keyed
`"te"`/`"tp"`), `ambiguity` (only when both ran: `aep_mwh_min`, `aep_mwh_max`, `aep_ratio_tp_over_te`),
`matrix` (`{n_hm0, n_period, hm0_range, period_range, power_max_kw}`), `record` (`{start, end, years,
step_hours, records}`), `warnings` (list), `notes` (list), `unverified` (list).

Warnings to generate (strings, not exceptions): record shorter than `analysis.MIN_RECORD_YEARS`
years ("AEP from a partial record is not an annual estimate"); fewer than 12 calendar months
present (seasonal bias); more than 20% of valid records outside the matrix; `rated_kw` smaller than
the matrix maximum power; time step not constant (gaps larger than 1.5 x the median step).
Notes must say: ERA5 is a reanalysis at an offshore node; power matrices are device- and
site-specific; the flux used is the route's own flux (Option A deep-water proxy from `mwp`, Option B
spectrum integral).

`device.example_matrix_csv()` returns a short synthetic matrix as CSV text whose first line is a
comment-free header and whose content is clearly artificial: generate it from a simple formula
`P = min(rated, 0.2 * 0.4906 * Hm0^2 * T * 8)` kW with a cut-in (Hm0 < 0.75 m gives 0) and `rated =
100 kW`, Te-indexed, Hm0 0.5..6.0 step 0.5, Te 4..16 step 1. The UI labels it "synthetic example, not a
real device".

## 4. API (`plugin_device.register`)

* `POST /api/jobs/<id>/device` JSON `{name, csv_text, bin_convention?, period_type?, rated_kw?,
  width_m?}` runs `assess_device` on `view.frame()` and returns the result JSON. Validate: `name`
  1..60 chars, becomes a slug `[a-z0-9_-]+` (lowercase, other characters to `-`); `csv_text` at most
  200 kB; `rated_kw`/`width_m` positive numbers or empty; `period_type` in te/tp/unknown (default
  unknown); `bin_convention` in centres/lower_edges. Save with `view.save_json(f"device_{slug}.json",
  result)` and write a markdown report fragment with
  `view.save_report_section(f"30_device_{slug}", markdown)` containing a heading, a table of
  AEP / capacity factor / capture width / share outside per indexing and estimator with units, the
  warnings and notes. The result is for the node in the request (query `node_lat`/`node_lon`); store
  the node in the saved JSON (`"node": [lat, lon] or null`) and in the markdown heading.
* `GET /api/jobs/<id>/device` returns `{"devices": [{slug, name, node, aep_mwh_range, saved}]}` from
  the saved files.
* `GET /api/jobs/<id>/device/<slug>` returns the saved result; unknown slug is 404.
* `DELETE /api/jobs/<id>/device/<slug>` deletes the saved JSON and the report fragment (204).
* `GET /api/device/example.csv` returns `device.example_matrix_csv()` as `text/csv`.

## 5. UI (`static/plugins/device.js`, `device.css`)

Tab id `device`, label "Device", `order: 20`, available for both routes.

* Form: device name, CSV file input (read with `FileReader` as text) plus a "use the synthetic example"
  link, rated power (kW, optional), characteristic width (m, optional), period axis selector
  (`Te (energy period)`, `Tp (peak period)`, `Unknown: evaluate both`), bin convention selector,
  "Assess" button. Show server errors in a `.form-error`.
* Results card per indexing (two cards side by side when both ran): key numbers as `.metric` cards
  (AEP MWh/yr, capacity factor %, mean power kW, capture width and ratio, share of records outside %,
  share of flux outside %), estimator comparison (bin vs bilinear) in a small table, the monthly
  table, warnings in a `.warnings` box, notes as a list.
* An occupancy heat table over the matrix cells (rows Hm0 descending, columns period) with a toggle
  between "share of records" and "share of energy"; colour cells with
  `color-mix(in srgb, var(--chart-line) X%, transparent)` and flip text colour to `var(--surface)`
  when the cell is dark, like the scatter table in `static/app.js` (`renderScatter`).
* When both indexings ran, a prominent line: "AEP lies between A and B MWh/yr depending on the period
  axis; confirm the device's period definition with its source."
* Saved devices list (GET) with open and delete buttons; the tab remounts on node changes, so reload
  the list when mounted.
* The "Assess" request must go through `EraExplorer.api` so the selected node is used.

## 6. Tests (`tests/test_device.py`), at least

* Parsing: centres and lower-edge conventions give the expected edges; delimiter detection; BOM;
  blank/NaN cells become NaN; each validation error.
* `lookup_power`: half-open boundaries (value exactly on an interior edge goes up; on the last upper
  edge is outside); undefined cell is outside; bilinear gives the exact midpoint value in a hand-built
  2x2 case and outside beyond the centres' extent; NaN inputs.
* `assess_device` on a hand-built frame where you can compute AEP by hand (for example 4 records, a
  3x3 matrix): mean power, AEP = mean*8766/1000, share outside, flux outside, both capture-width
  estimators and ratios, capacity factor, monthly shares sum to 100.
* `period_type` unknown returns both indexings and an `ambiguity` block; known returns one.
* A constant-power matrix and constant resource: AEP equals power*8766/1000 exactly.
* Warnings: short record, missing months, many records outside.
* HTTP: POST/GET/DELETE round trip with a synthetic job (`make_job` with `build_b`), bad CSV gives 422,
  oversize gives 422, wrong slug 404, the report fragment file exists after POST and is gone after
  DELETE, node query changes the stored `node`.
