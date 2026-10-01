# WP2: site screening (map of all nodes, seasonal view, node comparison)

Read `CONTRACT.md` first. A download covers a box of grid nodes; the app already analyses one node at a
time. This package screens **all nodes at once**: colour the map by a chosen statistic, show seasonality
per node, and compare two or three nodes side by side.

## Files you own (create only these)

`screening.py`, `plugin_screening.py`, `static/plugins/screening.js`, `static/plugins/screening.css`,
`tests/test_screening.py`.

## 1. Per-node statistics (`screening.compute_screening`)

```python
compute_screening(files, product, latitude, longitude) -> dict
```

Reads the job's NetCDF files directly (the per-node frames are not cached, and the canonical frame is
for one node). Reuse, read-only, the helpers in `analysis.py` for opening files and picking grids:
`analysis.split_files`, `analysis.netcdf_paths`, `analysis.time_name_of`, `analysis.spectra_variable`,
`analysis.read_depth_grid`, `analysis._depth_lookup`, `analysis.haversine_km`, and `wavecalc`
(`spectra_axes`, `decode_log10`, `spectral_bulk`, `deep_water_flux`). Look at
`analysis.node_summary` for how nodes are enumerated and what counts as land (a node with no finite
wave data is land/ice: list it with `valid: false`, no statistics).

For every ocean node compute, from per-record values (compute flux per record first, then average;
never from mean Hm0 and mean Te):

* Option A (single levels, `product != "wave-spectra"`): per record `hm0 = swh`, `te = mwp`,
  `flux = 0.4906 * swh^2 * mwp` (`wavecalc.deep_water_flux`). If the download has no `mwp`, flux and Te
  are `None` for all nodes and the result carries a warning; do not substitute `pp1d`.
* Option B (`wave-spectra`): per record `wavecalc.spectral_bulk` with the node's model depth when a
  bathymetry file exists (as `analysis.node_summary` does). To bound the cost use at most
  `MAX_RECORDS = 1500` records per node, taken with a constant stride over the whole record, and say so
  in `sampling_note`.

Per node output (`None` where not computable):

| key | meaning |
|---|---|
| `lat`, `lon`, `valid`, `depth_m`, `distance_km` | as in `analysis.node_summary` (distance from the requested site) |
| `hm0_mean_m`, `te_mean_s`, `flux_mean_kw_m` | means over records |
| `flux_p95_kw_m` | 95th percentile of per-record flux |
| `flux_cov` | std/mean of per-record flux |
| `monthly_flux_kw_m` | list of 12 values: mean flux of all records in each calendar month (index 0 = January); `None` for a month with no records |
| `season_flux_kw_m` | `{"DJF": .., "MAM": .., "JJA": .., "SON": ..}` mean flux over the records of each season (Dec-Feb, Mar-May, Jun-Aug, Sep-Nov; calendar seasons, name them as such), `None` if no records |
| `seasonality_ratio` | max season / min season, or `None` if any season is missing or the minimum is 0 |
| `n_records` | records used |

Top level: `product`, `route`, `requested_coordinate`, `record` (`{start, end, years, step_hours,
records}` over the files), `nodes` (all nodes, ocean and land), `default_node` (`{lat, lon}`, nearest ocean
node to the site), `n_ocean`, `n_land`, `season_definition` (string), `sampling_note` (or `None`),
`warnings` (list). Warn when the record is shorter than `analysis.MIN_RECORD_YEARS` years, and when the
record does not cover all 12 months ("seasonal statistics from a partial year are biased").

Efficiency: vectorise over nodes for Option A (arrays are small); for Option B loop over nodes with the
sampled records. Never load more than one file's array at a time. A 13 x 11 node, 120-record spectra
file must finish in a few seconds.

## 2. API (`plugin_screening.register`)

* `GET /api/jobs/<id>/screening` returns the result of `compute_screening` for the job (this endpoint
  ignores `node_lat`/`node_lon`: it always covers all nodes, and calls `ctx.job(job_id)` only to obtain
  files and product). Cache in `view.directory / "screening.json"` with a version number constant
  `SCREENING_VERSION` and invalidate when any data file is newer, the same way `app.cached_nodes` does.
* `GET /api/jobs/<id>/screening.csv` returns the ocean nodes as CSV, one row per node, columns named
  with units (`lat`, `lon`, `depth_m`, `distance_km`, `hm0_mean_m`, `te_mean_s`, `flux_mean_kw_m`,
  `flux_p95_kw_m`, `flux_cov`, `seasonality_ratio`, `flux_djf_kw_m` ..., `flux_jan_kw_m` ...), attachment.
* No endpoint for the comparison: the browser compares from the `screening` payload.

## 3. UI (`static/plugins/screening.js`, `screening.css`)

Tab id `screening`, label "Screening", `order: 10`, available when `ctx.nodeData` exists and has at
least two ocean nodes.

1. **Map colouring.** A "Colour the map by" select: mean flux J, mean Hm0, mean Te, 95th-percentile flux,
   flux variability (COV), seasonality ratio (max/min season), mean flux in a chosen season (DJF/MAM/
   JJA/SON), mean flux in a chosen month (a month select). Applying it calls
   `EraExplorer.colourNodes({label, values})` with a label that states the unit; a "Reset map colours"
   button calls `EraExplorer.colourNodes(null)`. On mount, colour by mean flux.
2. **Ranking table.** Ocean nodes sorted by the selected statistic (descending), with lat, lon, depth,
   distance from site, mean flux, mean Hm0, mean Te, COV, seasonality ratio. Row actions: "Compare"
   (adds to the comparison, at most 3) and "Analyse" (`EraExplorer.selectNode`). Mark the nearest ocean
   node. The table scrolls inside a card with a sticky header.
3. **Seasonal view.** A heat table for the top 10 nodes by the selected statistic: rows nodes, columns
   the 12 months, cells mean flux (kW/m), heat via `color-mix` on `--chart-line` like the scatter table
   in `static/app.js`; a second line below with the four season means.
4. **Comparison.** With 2 or 3 nodes chosen: chips showing the chosen nodes (removable), a side-by-side
   table (rows: depth, distance, mean flux, delta vs the first node in %, P95 flux, COV, mean Hm0, mean
   Te, seasonality ratio, season fluxes), and a uPlot line chart of the 12-month climatology of mean
   flux (kW/m) with one series per node (colours from `EraExplorer.palette()`, x axis the months 1..12).
   The chart must be recreated when the selection changes and tracked with `EraExplorer.trackChart`.
5. Show `warnings` and `sampling_note`, and a hint that statistics are over the downloaded record and a
   short record is not a resource estimate.
6. A "Download table (CSV)" link to `screening.csv`.

## 4. Tests (`tests/test_screening.py`), at least

* Option A: a 3 x 3 synthetic dataset where each cell has a known constant Hm0/Te (and one land cell):
  assert `n_ocean`, `n_land`, per-node `flux_mean_kw_m = 0.4906 * Hm0^2 * Te` exactly, land node
  `valid: false`.
* Monthly and seasonal means against hand-computed values for a dataset spanning several months with
  different Hm0 per month (e.g. hourly records over 4 months); a month without records is `None`; DJF
  uses December of the earlier year with January/February (just average the records whose month is in
  the season; no year logic is required, say so in `season_definition`).
* Mean of per-record flux is not the flux of the means (construct records where they differ).
* `flux_cov` and P95 against numpy on the same values.
* Option B: spectra with a land cell; flux equals `wavespectra`-free analytic value for a single-bin
  spectrum (see `tests/test_wavecalc.py::one_bin_spectrum`); `sampling_note` appears with a long record
  (patch `MAX_RECORDS` small); depth from a bathymetry file changes flux (finite depth).
* No `mwp`: flux `None` and a warning.
* HTTP: GET returns the payload and creates `screening.json`; the second GET does not recompute (patch
  `compute_screening` with a counter or compare mtime); `screening.csv` headers and row count; unknown
  job 404; a dry-run job 409.
