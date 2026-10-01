# ECMWF ERA5 Map App

A local Flask interface for choosing a site on a MapLibre map, downloading ERA5 data through
Copernicus CDS, and analysing it at the nearest ocean grid point. Two data routes, **Option A**
(single-level integrated parameters) and **Option B** (2D wave spectra), are kept separate; a
cross-check panel compares them but never merges them. The basemap uses OpenFreeMap with
OpenStreetMap data and needs no map account or token.

## Setup

1. Configure CDS credentials in `~/.cdsapirc` and accept the ERA5 licence. Credentials never go in
   this repository.
2. Install and run:

```bash
cd ecmwf-map-app
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/python app.py
```

Open <http://127.0.0.1:5000>. Click or drag the marker to select the site. **Preview request only**
prints the exact CDS requests, the grid area, an uncompressed size estimate and writes
`provenance.json`, without contacting CDS.

Optional environment variables (see `.env.example`; not loaded automatically): `PORT`, `MAX_JOBS`
(concurrent downloads, default 2), `DOWNLOADS_DIR`. Tests: `pip install -r requirements-dev.txt && pytest`.
`wavespectra` is optional (spectral partitioning and one cross-check test); without it those parts
say they are unavailable.

## Download options

| Product | CDS dataset | What you get |
|---|---|---|
| **Option A · single levels** (default) | `reanalysis-era5-single-levels` | Tier 1: Hm0 (`swh`), Te (`mwp`), Tp (`pp1d`), mean direction (`mwd`). Tier 2: wind-sea and total-swell height, period and direction, three swell partitions. Tier 3: Tm01 (`mp1`), Tm02 (`mp2`), directional width, peakedness, plus model depth. Optional: 10 m wind, Hmax/Tmax (extremes only), 2 m temperature, precipitation. |
| **Option B · 2D wave spectra** | `reanalysis-era5-complete`, stream `wave`, param `251.140` | 24 directions × 30 frequencies on the 0.5° grid plus model depth. Every parameter, the flux and its direction are computed from E(f, θ). Buffer ≤ 2° and ≤ 2 years per request. |
| ERA5 MARS · surface fields | `reanalysis-era5-complete`, stream `oper` | Analysis fields by GRIB code (2 m temperature, 10 m wind, pressure, SST). |

The time step (1, 3, 6, 12 or 24 h) is selectable. The MARS products choose `expver` automatically
(`1` for months older than about 100 days, `5` for newer; override with `--expver`). The fetcher
checks every single-levels variable name against the live CDS catalogue and stops listing any it
does not accept. Each job folder holds one NetCDF per month, `era5_bathymetry.nc` (model depth, one
small request) and `provenance.json` (exact requests, dataset metadata and DOI, expver, grid,
period, file hashes, software versions, attribution; no credentials). The fetcher also runs on its
own: `python fetch_era5_waves.py --help`.

**CDS cost limit.** CDS refuses MARS requests above a cost limit and answers "cost limits exceeded".
A probe on 2026-10-01 (`--probe`, below) showed that **one day of 6-hourly spectra is accepted for either
`expver` and for old and recent dates, while one day of hourly spectra is refused**. So the number of
time steps in a request matters by itself, not only the number of fields: a 6-hourly month (86,400
fields) works and a single hourly day (17,280 fields) does not. What exactly CDS counts is not known.
The fetcher therefore adapts as it goes and remembers what CDS accepts for the rest of the run:

1. spectra months are cut into near-equal runs of days of at most 86,400 fields;
2. a refused request first has its time steps reduced (24 → 12 → 6 → 4; four is the largest count seen
   accepted), then its days halved, and the learned shape is applied to every later request, so a
   refusal is not repeated. An hourly month becomes requests of 4 time steps each, named like
   `era5-spectra_2026-07_d01-08_h00-03.nc`; the analysis merges the files by time;
3. it stops at the first request of one day and one small time list that CDS still refuses, and says what
   to change (a larger `--time-step`, the ERA5 version `--expver 1` or `5`; the app has an **ERA5
   version** selector for the MARS products);
4. `--estimate` asks CDS for a cost estimate and sizes requests from it if the reply has a cost and a
   limit (the costing endpoint answered HTTP 500 without credentials, and `cdsapi`'s wrapped client is
   needed to reach it); `--probe` submits five one-day test requests, reports which are accepted or
   refused, and cancels each accepted one at once, so nothing is downloaded.

Hourly spectra are slow because of this (about six requests per day). Unless you need hourly spectra, use
a 3-hour or 6-hour step.

```bash
python fetch_era5_waves.py --latitude -8.88 --longitude 114.89 --start 2026-07-01 --end 2026-07-31     --output downloads/test --product wave-spectra --time-step 6 --probe
```

## Definitions used everywhere

`m_n = ∫ fⁿ E(f) df`. These are different quantities and are never given the same label:

| Symbol | Definition | ERA5 name |
|---|---|---|
| Hm0 | 4√m0 | `swh` |
| **Te = Tm-1** | m-1/m0, the energy period | `mwp` (ECMWF defines `mwp` as this) |
| Tm01 | m0/m1 | `mp1` |
| Tm02 | √(m0/m2), zero-crossing | `mp2` |
| Tp | 1/fp, parabolic fit at the peak | `pp1d` |

Deep-water flux **J = ρg²/(64π) · Hm0² · Te = 0.4906 · Hm0² · Te kW/m** (ρ = 1025 kg/m³, g = 9.81 m/s²).
J is computed per record and then averaged; never from mean Hm0 and mean Te. Option A uses `mwp`
directly as Te, so no Tp→Te ratio is applied, and `pp1d` is deliberately not used in the flux.
Directions are coming-from, degrees true clockwise. ERA5 spectra are stored going-to and converted
in one place. Ordering `Tm02 ≤ Tm01 ≤ Te` must hold for every record and is checked.

## Analysis

**Option A** (per record, nearest ocean cell, distance and model depth shown): deep-water flux
statistics, monthly climatology, Hm0–Te scatter (0.5 m × 1 s bins, half-open, hours and energy shares,
out-of-table counts), wave rose by hours and by flux, wind-sea/swell composition (swell-dominated
hours, swell energy share, a `swh² ≈ shww² + shts²` check), Tm02 ≤ Tm01 ≤ Te check, Te/Tp, steepness,
a deep-water validity check (`depth < L0/2`, L0 = g·Te²/2π), a record-length warning under 10 years
(the project target, not a standard) and an hourly vs 6-hourly sampling check.

**Option B** adds, from the spectrum: Hm0, Te, Tm01, Tm02, ε0, grid and parabolic Tp, energy-weighted
mean direction, the ECMWF directional width, the **finite-depth energy-flux vector** (`cg` from the
dispersion relation at the model depth; deep water if no depth), flux direction θJ and directionality,
flux by period (cumulative curve) and by 15° direction bin, Hm0–Te and Hm0–Tp scatter tables, a
**fixed-heading sector flux** (enter a heading and half-width: share of flux inside the sector, taken
from the spectrum, versus filtering hours by mean direction), PTM3 watershed partitioning (per-system
Hm0, Tp, Te, direction, J; share of multi-system hours), a JONSWAP γ fit and Te/Tp as diagnostics.

**Grid-node picker.** A box downloads every ERA5 grid node inside it (the WaveSpectrum-ERA-5 example
box, 6°N–0°N and 95°E–100°E at 0.5°, holds 13 × 11 = 143 nodes, 42 of them land). After a download the
map draws all nodes, coloured by mean flux, Hm0 or Te, with land or ice nodes (no data) in grey, and a
table lists them with depth, the mean values and the distance from the site. Click a node on the map or
a row to rerun the whole analysis for that node, or **Back to nearest ocean cell** to return to the
default. Means are over the record, with flux computed per record first; for spectra the summary uses at
most 300 records spread over the period and says so. No new download is needed: the data for every node
is already in the files. Each node's analysis and time series are cached separately
(`analysis_<lat>_<lon>.json`, `timeseries_<lat>_<lon>.csv`), the sector flux and the CSV download follow
the selected node, and the cross-check can compare both routes at that node. If the OpenFreeMap basemap
cannot be reached the map falls back to a plain background so the nodes still draw.

Every analysis writes `timeseries.csv` (the full per-record series, downloadable) beside the data, and
the page lists the definitions and limits of the route.

## Cross-check (Option A vs Option B)

Pick a finished Option A job and a finished Option B job for the same site and period. Records are
matched on timestamp. For Hm0, Te, Tp, Tm01, Tm02, mean direction and J the panel shows bias (B − A),
RMSE, correlation, share within tolerance (defaults 5% for Hm0, Te, Tm01, Tm02 and J, 10% for Tp, 15°
for direction; configurable defaults, not standards), a scatter plot and the worst 10 records. It warns
on a Te gap beyond tolerance (decoding or unit error), a near-180° direction offset (convention
error), different grid cells and model depth. Option B Hm0 is expected slightly below `swh` because
the 30 bins stop at 0.548 Hz and no tail is added.

## Limits to read with every number

- ERA5 is a reanalysis, not a measurement. Validate against buoys before quoting a resource.
- The wave model grid (about 0.36°, delivered on 0.5°) does not resolve shoaling, refraction, islands or
  the nearshore. A coastal site is represented by an offshore cell.
- Spectra stop at 0.548 Hz with no tail; direction is resolved to 15° bins; the encoding floor drops
  very low-energy bins.
- Sector flux assumes flux is uniform inside a 15° bin when a sector edge cuts through it.
- A single month is a demonstration; interannual variability needs at least the project's 10 years.

The full list of what was verified and what is still unresolved is in
[docs/verification.md](docs/verification.md).

## Jobs and files

Each request lives in `downloads/<job-id>/`. Jobs are reloaded on start (a job interrupted by a
restart is shown as failed). Open one directly with `/#job=<job-id>`. Use **Cancel request** to stop a
queued or running download and **Delete files** to remove a job and its data. ERA5 is available from
1940-01-01 up to about six days ago; at most five years per request (two for spectra); each month is a
separate CDS request. The box is clipped at ±180° longitude instead of wrapped.

The server binds to `127.0.0.1` and has no authentication. Do not expose it beyond your machine:
anyone who can reach it can start CDS downloads as you.

## Attribution

Contains modified Copernicus Climate Change Service information. ERA5 data: Hersbach, H. et al. (2023),
*ERA5 hourly data on single levels from 1940 to present*, C3S Climate Data Store,
<https://doi.org/10.24381/cds.adbb2d47>. Spectra come from the complete ERA5 archive,
<https://doi.org/10.24381/cds.143582cf>. Use is subject to the
[Copernicus licence](https://cds.climate.copernicus.eu/licences).
