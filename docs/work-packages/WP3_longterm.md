# WP3: long-record statistics (variability indices, interannual variation, extremes)

Read `CONTRACT.md` first. A resource assessment needs the long-term picture: seasonal and monthly
variability, year-to-year variation, and extreme wave heights. This package computes them from the
selected node's record.

## Files you own (create only these)

`longterm.py`, `plugin_longterm.py`, `static/plugins/longterm.js`, `static/plugins/longterm.css`,
`tests/test_longterm.py`.

## 1. Statistics (`longterm.compute_longterm`)

```python
compute_longterm(frame, *, threshold_pct=95.0, decluster_hours=48.0, return_years=(1, 10, 50, 100),
                 bootstrap=300, seed=1, min_exceedances=30) -> dict
```

`frame` is the canonical frame (`hm0`, `te`, `flux`, UTC index). All calculations use per-record
values; flux is never recomputed from mean Hm0 and mean Te.

### 1.1 Record summary (`record`)

`start`, `end`, `years` (float, `(end - start)` in years of 365.25 days), `step_hours` (median),
`records` (finite `flux` or `hm0`), per calendar year `{year, records, coverage_pct}` where coverage is
records divided by the expected number for that year at the median step (limited to the span of the
record inside that year), and `full_years`: years with coverage >= 90% **and** all 12 months present.

### 1.2 Climatology (`climatology`)

* `monthly`: for months 1..12: `n`, `flux_mean_kw_m`, `hm0_mean_m`, `te_mean_s` (means over all records
  of that month) and, across calendar years, the 10th/50th/90th percentile of the per-year monthly mean
  flux (`flux_p10_kw_m`, `flux_p50_kw_m`, `flux_p90_kw_m`; `None` when fewer than 3 years have that month).
* `seasonal`: DJF, MAM, JJA, SON (calendar seasons, Dec-Feb etc., document that December is grouped with
  the following January and February of the *same calendar year's* records, i.e. simply by month): mean flux
  and `share_of_annual_pct` = season mean / the mean of the four season means.

### 1.3 Variability indices (`indices`)

Each value comes with its formula string and `None` plus a `reason` when it cannot be computed.

* `cov` = std / mean of per-record flux (population std, `ddof=0`).
* `annual_mean_flux_kw_m` = mean of the 12 climatological monthly means (needs all 12 months).
* `mvi` (monthly variability index) = (max monthly mean - min monthly mean) / `annual_mean_flux_kw_m`,
  using the 12 climatological monthly means.
* `svi` (seasonal variability index) = (max seasonal mean - min seasonal mean) / `annual_mean_flux_kw_m`,
  using the 4 seasonal means.
* Note in the result that the formulas are stated here and the index *names* were not verified against a
  published definition (put it in `unverified`).

### 1.4 Interannual variation (`interannual`)

Using only `full_years`: per year `{year, flux_mean_kw_m, hm0_mean_m, anomaly_pct}` with anomaly
`100 * (annual - mean of annuals) / mean of annuals`; summary `cov_of_annual_means`, `max_year`,
`min_year`, `range_ratio` (max/min). With fewer than 3 full years return `None` and a `reason`
("fewer than 3 complete years"). Do not fit trends and do not mention ENSO/IOD as results (no climate
index data is available); a `notes` line may say that climate-mode influence is not assessed.

### 1.5 Extremes: peaks over threshold (`extremes`) for `hm0`

1. Threshold `u` = the `threshold_pct` percentile of finite Hm0 values.
2. Decluster: sort records in time; a cluster is a maximal run of exceedances in which consecutive
   exceedances are less than `decluster_hours` apart; keep the maximum of each cluster (its value and
   time). The cluster maxima are the peaks. (Runs declustering; say so in the output.)
3. If fewer than `min_exceedances` peaks, return `{"status": "insufficient", "reason": ..., "n_peaks": n}`
   and nothing else computed.
4. Excesses `y = peak - u`. Fit a Generalised Pareto distribution by maximum likelihood with
   `scipy.stats.genpareto.fit(y, floc=0)`: shape `xi` (scipy's `c`) and scale `sigma`.
5. Rate `lambda` = peaks per year = `n_peaks / record years`.
6. Return level for return period `T` years:
   `x_T = u + (sigma / xi) * ((lambda * T) ** xi - 1)` if `abs(xi) >= 1e-6`, else
   `x_T = u + sigma * ln(lambda * T)`.
7. Bootstrap confidence interval for every `x_T`: for each of `bootstrap` replicates draw
   `N* ~ Poisson(lambda * years)` (at least 10), resample `N*` excesses with replacement, refit,
   recompute `x_T` with `lambda* = N* / years`. Report the 5th and 95th percentiles as a 90% interval,
   the number of replicates that failed to fit, and use `numpy.random.default_rng(seed)`. If `bootstrap`
   is 0 skip the interval.
8. Threshold sensitivity: repeat steps 1-6 (no bootstrap) for thresholds at percentiles
   `[90, 92.5, 95, 97.5]`, reporting `threshold_m`, `n_peaks`, `xi`, `sigma` and the 50-year level (or the
   largest requested return period) so the user can see stability.
9. Return-level plot data: the peaks sorted descending with empirical return periods
   `T_i = 1 / (lambda * (i / (n + 1)))` for `i = 1..n` (Weibull plotting position, `i` the rank by
   descending value), and the fitted curve `x_T` at 40 log-spaced `T` between `1 / lambda` and
   `max(return_years) * 1.2`.

Warnings (strings): record under `analysis.MIN_RECORD_YEARS` years ("not a resource estimate"); a return
period longer than 3 x the record years ("extrapolation beyond three times the record length is
unreliable"); `abs(xi) > 0.5` ("shape outside the range where the maximum-likelihood estimator is
regular"); bootstrap failures above 5%; many missing records (coverage < 90% overall).
`result["extremes"]["method"]` is a string naming the method (declustering, GPD, MLE, bootstrap) and the
parameters used.

Top level keys: `record`, `climatology`, `indices`, `interannual`, `extremes`, `warnings`, `notes`,
`unverified`, `parameters` (the inputs used).

## 2. API (`plugin_longterm.register`)

`GET /api/jobs/<id>/longterm` with optional query `threshold_pct` (50-99.9, default 95),
`decluster_hours` (1-720, default 48), `return_years` (comma list of numbers 0.5-1000, default
`1,10,50,100`, at most 8 values), `bootstrap` (0-1000, default 300), `seed` (integer, default 1).
Validate and raise `ValueError` for out-of-range values. Compute from `view.frame()` (node aware). Cache by
a hash of the parameters and node in `view.results_path("longterm_<hash>.json")`; reuse the cache when it is
newer than all data files. Also write `view.save_report_section("40_longterm", markdown)` with the key
tables (record summary, monthly climatology, indices with formulas, interannual summary, return levels with
intervals, warnings, method) in markdown with units.

## 3. UI (`static/plugins/longterm.js`, `longterm.css`)

Tab id `longterm`, label "Long-term", `order: 30`, always available.

* A short header showing the record length with a `.warnings` box when the API reports a short record.
* Parameter row (threshold percentile, declustering hours, return periods, bootstrap count) with a
  "Compute" button; results load on mount with defaults. Show a loading state.
* Cards: climatology as a 12-month bar/line chart (uPlot) of monthly mean flux (kW/m) with the P10-P90
  band across years when available; seasonal shares table; indices table (COV, MVI, SVI with formulas);
  interannual chart/table (annual mean flux per full year, anomalies) or the reason it is unavailable;
  extremes: threshold, peaks, rate, GPD shape and scale, a return-level table with 90% intervals, a
  return-level plot (uPlot: empirical points as markers, fitted curve line, log x axis if easy, else a
  linear axis labelled clearly) and the threshold sensitivity table.
* Every number shows its unit. Show `unverified` and `notes` at the bottom.
* Use `EraExplorer.api` so the selected node applies.

## 4. Tests (`tests/test_longterm.py`), at least

* Climatology and indices on a constructed record whose monthly mean flux is known (for example 3 years
  of 6-hourly data, flux set per month): monthly means exact, `annual_mean` = mean of the 12, `mvi`, `svi`,
  `cov` against hand/numpy values; a record missing a month gives `None` with a reason.
* Interannual: years with known annual means; an incomplete year is excluded; fewer than 3 full years
  returns `None` with a reason.
* Declustering: a hand-built series with two peaks 24 h apart and a third 200 h later gives 2 peaks when
  `decluster_hours=48`, and the kept values are the cluster maxima.
* GPD/return level: draw excesses from a known GPD (`scipy.stats.genpareto.rvs(c=0.1, scale=0.5, size=...,
  random_state=...)`) placed above a threshold in a long synthetic Hm0 series with a fixed seed; the fit
  recovers `xi` and `sigma` within a stated tolerance and `x_T` agrees with the analytic return level of the
  generating distribution within the bootstrap interval; the formula branch for `xi` near 0 is exercised
  with `xi = 0` exactly (exponential excesses: `x_T = u + sigma*ln(lambda*T)`).
* Insufficient peaks returns `status: "insufficient"`; `bootstrap=0` omits intervals; the same seed gives
  identical output.
* Warnings for a short record and for a return period beyond 3x the record.
* HTTP: GET with defaults returns all sections for a synthetic job, writes the cache and the report
  fragment, parameter validation errors give 422, a second identical GET reuses the cache.
