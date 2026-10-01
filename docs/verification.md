# Verification record (VERIFY items)

Checked on 2026-10-01. "Resolved" means the source below was read or the check was run. Anything
else is in the Unresolved list; none of it is assumed in the code.

## Resolved

| Item | Result | Source |
|---|---|---|
| CDS variable names, Tier 1–3 (wave group, wind/swell, three swell partitions, QC, Hmax/Tmax, `model_bathymetry`) | All accepted by the CDS form for `reanalysis-era5-single-levels`. The fetcher re-checks them against the live catalogue on every real run and stops, listing the rejected names, if one is missing. | CDS catalogue `…/collections/reanalysis-era5-single-levels/form.json` |
| `mwp` definition | `mwp` (140232) is `Tm-1 = m-1/m0`, the energy period. The same definition applies to `mpww` (140236), `mpts` (140239) and the swell-partition periods (140123/126/129), so they are valid energy periods for flux weighting. | ECMWF `wave_parameters.pdf` (J.-R. Bidlot), section 3.2, eq. 5 |
| `mp1`, `mp2` | `mp1` (140220) = `Tm1 = m0/m1`; `mp2` (140221) = `Tm2 = √(m0/m2)`, the zero-crossing period. | same, eq. 7–8 |
| `pp1d` | Reciprocal of the peak frequency, from a parabolic fit around the discretised maximum of the 2D spectrum. Option B uses a parabola around the 1D maximum, so the two differ slightly by construction. | same, section 3.3 |
| Deep-water flux | `P = ρ g²/(64π) · Tm-1 · Hs²`. The app's coefficient is `ρ g²/(64π)/1000 = 0.4906 kW/(m·s·m²)` with ρ = 1025 kg/m³ and g = 9.81 m/s². | same, eq. 6 |
| `mwd` convention | Meteorological, coming-from (0 = from north, 90 = from east). It is `atan(SF/CF)` over the whole spectrum, so energy-weighted and not flux-weighted. | same, section 3.4 |
| ECMWF directional width `wdw` | `σθ = √(2(1 − M1))` with the mean direction taken per frequency. Option B implements exactly this (dimensionless, 0 to √2). | same, eq. 10–12 |
| High-frequency tail | ECMWF integrated parameters use an `f⁻⁵` Phillips tail beyond the last bin. The 30-bin spectrum has no tail, so spectral Hm0 ≤ `swh` is expected. The size of the gap on real data is not measured here. | same, section 3 |
| 2D spectra axes and encoding | Directions are going-to (0 = towards north), first bin centre 7.5°; frequencies `f(n) = f(n-1)·1.1`, first 0.0345 Hz; values are `log10`; units m² s/rad. | ECMWF "2D wave spectra" documentation page |
| `wavespectra.read_era5` direction | It converts to coming-from: the first bin becomes 187.5°. Confirmed in the 4.9.0 source and numerically. | `wavespectra/input/era5.py`; `tests/test_analysis.py` |
| Δf convention | `wavespectra` uses `np.gradient(freq)`. The app uses the same, so Hm0, Tm01 and Tm02 match `wavespectra(tail=False)` to 1e-6 (tested). Exact geometric bin edges would change Hm0 by about 0.06%. | `wavespectra/specarray.py`; `tests/test_wavecalc.py` |
| Partitioning API | `spec.partition.ptm3(parts=…)` needs no wind or depth and orders watershed systems by Hm0 (no sea/swell label). PTM1, PTM2 and HP01 need wind and depth, which Option B does not have. The app uses PTM3. | `wavespectra/partition/partition.py` |
| Dataset DOIs and licence id | Single levels 10.24381/cds.adbb2d47; complete 10.24381/cds.143582cf; licence CC-BY-4.0 per the catalogue record. Stored in `provenance.json` from the live catalogue. | CDS catalogue records |
| CDS request keys | `data_format` (`grib`/`netcdf`) and `download_format` (`unarchived`/`zip`) exist on the form. The form labels NetCDF as "NetCDF4 (Experimental)". | `form.json` |
| Real-file check | On the WaveSpectrum-ERA-5 `output.nc` (April 2020, 6°N 95°E) the app gives mean Hm0 1.299 m, max 1.956 m and mean Tp 14.04 s (grid peak), matching that repo's README. | run on 2026-10-01 |

## Unresolved

1. **Exact encoding floor of the spectra.** ECMWF says values below "approximately 10⁻⁴" are not encoded, but the reference file contains values down to 1.1×10⁻⁷, so the floor is not 10⁻⁴ for this file. Missing bins are set to 0; the energy they omit is not quantified.
2. **Altimeter assimilation.** The ECMWF wave document lists gridded altimeter wave-height fields (140246–140248) but this check did not find an explicit statement that ERA5 assimilates altimeter data. The UI says it is unverified. Read the ERA5 documentation before claiming it.
3. **Delivered wave grid for single levels.** The spectra file is on 0.5°, and ECMWF states that the wave model uses a reduced lat/lon grid of about 0.36° that CDS interpolates to a regular grid. That the single-levels wave variables are also on 0.5° was not checked against a real download.
4. **Hourly availability of the 2D spectra.** The time step is configurable (1, 3, 6, 12, 24 h) but hourly spectra were not requested. 6-hourly is the tested default.
5. **IEC TS 62600-101 naming.** The spectral width `ε0 = √(m0·m-2/m-1² − 1)` and the flux directionality ratio `|J⃗|/J` are labelled by their formulas, not by the standard's names, because the standard was not read.
6. **The 19% group-velocity figure** (10 s waves at intermediate depth) from the brief was not reproduced. The finite-depth code is tested only against its deep and shallow limits and the dispersion relation.
7. **No real CDS download has been run by this app.** The MARS requests copy the keys of the request that produced `output.nc`; the single-levels request uses the keys on the live form. The variable-name check is automatic; everything else about a real request (queue time, ZIP layout of the wave and oper streams, the `expver` cut-off of about 100 days) is untested.
8. **Option A vs Option B on real data** has not been compared: only a synthetic Option A built from the spectra was used to test the cross-check panel, so its agreement there is by construction.
9. **Hourly vs 6-hourly sampling check** is implemented but has only run on synthetic hourly data.
10. **Dateline.** The CDS form allows longitudes from −360 to 360, so a true wrap-around request may work. The app clips the box at ±180° instead and has not tried a wrapped request.
11. **What CDS counts as cost for spectra.** Probed on 2026-10-01 with `--probe` for -8.885, 114.895 in July 2026: one day at 6-hourly was accepted with `expver` 5 and with `expver` 1, and also for 2020-04-01 with `expver` 1; one day at hourly was refused as "cost limits exceeded". So the time list matters on its own (a 6-hourly month of 86,400 fields works; an hourly day of 17,280 does not), which a pure field-count rule does not explain. The largest number of time steps known to be accepted is 4; intermediate counts (for example 8 or 12) were not tested. The public costing endpoint returns HTTP 500, and the wrapped-client estimate was not obtained. The fetcher splits on the time axis down to 4 and remembers what CDS accepts.
