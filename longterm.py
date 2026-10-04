"""Long-record statistics: variability indices, interannual variation, peaks-over-threshold extremes.

Computes climatology, variability indices (COV, MVI, SVI), interannual variation of annual mean
flux for complete years, and extreme Hm0 return levels using a Generalised Pareto Distribution
fitted to declustered peaks over a threshold.

All calculations use per-record values from the canonical frame; flux is never recomputed from
mean Hm0 and mean Te.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import genpareto

import analysis
import plugins

# 365.25 days per year, consistent with analysis.py
_DAYS_PER_YEAR = 365.25
_SECONDS_PER_YEAR = _DAYS_PER_YEAR * 86400.0
_HOURS_PER_YEAR = plugins.HOURS_PER_YEAR  # 8766.0

# Threshold sensitivity percentiles
_SENSITIVITY_PERCENTILES = [90.0, 92.5, 95.0, 97.5]


def _safe(value: Any) -> Any:
    """Convert numpy scalars and NaN/inf to JSON-safe Python types."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else round(float(value), 3)
    return value


def _round3(value: float | None) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), 3)


def compute_longterm(frame: pd.DataFrame, *, threshold_pct: float = 95.0,
                     decluster_hours: float = 48.0,
                     return_years: tuple | list = (1, 10, 50, 100),
                     bootstrap: int = 300, seed: int = 1,
                     min_exceedances: int = 30) -> dict:
    """Compute long-record statistics from the canonical frame.

    The frame must have columns ``hm0``, ``te``, ``flux``
    and a UTC DatetimeIndex.
    """
    warnings: list[str] = []
    notes: list[str] = []
    unverified: list[str] = []

    record = _record_summary(frame, warnings)
    climatology, clim_raw = _climatology(frame)
    indices = _variability_indices(climatology, clim_raw, frame, unverified)
    interannual = _interannual(frame, record)

    # Compute exact (unrounded) span for rate calculations
    idx = frame.index
    exact_years = float((idx[-1] - idx[0]).total_seconds() / _SECONDS_PER_YEAR)

    extremes = _extremes(frame, record, exact_years=exact_years,
                         threshold_pct=threshold_pct,
                         decluster_hours=decluster_hours,
                         return_years=return_years, bootstrap=bootstrap,
                         seed=seed, min_exceedances=min_exceedances,
                         warnings=warnings)

    notes.append("Climate-mode influence (ENSO, IOD) is not assessed; no climate index data is available.")

    return {
        "record": record,
        "climatology": climatology,
        "indices": indices,
        "interannual": interannual,
        "extremes": extremes,
        "warnings": warnings,
        "notes": notes,
        "unverified": unverified,
        "parameters": {
            "threshold_pct": threshold_pct,
            "decluster_hours": decluster_hours,
            "return_years": list(return_years),
            "bootstrap": bootstrap,
            "seed": seed,
            "min_exceedances": min_exceedances,
        },
    }


# ---------------------------------------------------------------------------
# Record summary
# ---------------------------------------------------------------------------

def _record_summary(frame: pd.DataFrame, warnings: list[str]) -> dict:
    idx = frame.index
    start = idx[0]
    end = idx[-1]
    years = float((end - start).total_seconds() / _SECONDS_PER_YEAR)

    # Median step in hours
    if len(idx) > 1:
        step_hours = float(np.median(np.diff(idx.to_numpy()).astype("timedelta64[s]").astype(float)) / 3600.0)
    else:
        step_hours = None

    # Finite records: finite flux or finite hm0
    finite_mask = np.isfinite(frame["flux"].to_numpy(float)) | np.isfinite(frame["hm0"].to_numpy(float))
    records = int(finite_mask.sum())

    # Per calendar year stats
    per_year = []
    full_years = []
    cal_years = sorted(set(idx.year))
    idx_years = idx.year.to_numpy()
    idx_months = idx.month.to_numpy()
    for yr in cal_years:
        year_mask = idx_years == yr
        year_records = int(finite_mask[year_mask].sum())
        days_in_yr = 366 if calendar.isleap(yr) else 365
        if step_hours and step_hours > 0:
            expected_yr = days_in_yr * 24.0 / step_hours
        else:
            expected_yr = max(year_records, 1)
        coverage = min(100.0 * year_records / expected_yr, 100.0) if expected_yr > 0 else 0.0
        per_year.append({"year": int(yr), "records": year_records, "coverage_pct": _round3(coverage)})

        # Full year: records(Y, M) >= 0.9 * expected(Y, M) for every month M = 1..12
        is_full = True
        for m in range(1, 13):
            days_in_m = calendar.monthrange(yr, m)[1]
            if step_hours and step_hours > 0:
                expected_ym = days_in_m * 24.0 / step_hours
            else:
                expected_ym = 1.0
            month_mask = year_mask & (idx_months == m)
            month_records = int(finite_mask[month_mask].sum())
            if month_records < 0.9 * expected_ym:
                is_full = False
                break
        if is_full:
            full_years.append(int(yr))

    if years < analysis.MIN_RECORD_YEARS:
        warnings.append(
            f"The record is {years:.2f} years, under the {analysis.MIN_RECORD_YEARS}-year project target: "
            "not a resource estimate."
        )

    # Overall coverage
    if step_hours and step_hours > 0:
        total_expected = (end - start).total_seconds() / 3600.0 / step_hours + 1
        overall_coverage = records / total_expected if total_expected > 0 else 0.0
    else:
        overall_coverage = 1.0
    if overall_coverage < 0.9:
        warnings.append(f"Overall data coverage is {overall_coverage * 100:.1f}% (below 90%).")

    return {
        "start": str(start),
        "end": str(end),
        "years": _round3(years),
        "step_hours": _safe(step_hours),
        "records": records,
        "per_year": per_year,
        "full_years": full_years,
    }


# ---------------------------------------------------------------------------
# Climatology
# ---------------------------------------------------------------------------

def _climatology(frame: pd.DataFrame) -> tuple[dict, dict]:
    """Compute monthly and seasonal climatology.

    Returns (output, raw) where output is the JSON-safe result and raw holds
    unrounded monthly_flux (list of 12 float|None) and seasonal_flux
    (dict of 4 float|None) for use by _variability_indices.
    """
    flux = frame["flux"].to_numpy(float)
    hm0 = frame["hm0"].to_numpy(float)
    te = frame["te"].to_numpy(float)
    months = frame.index.month
    years = frame.index.year

    monthly = []
    raw_monthly_flux: list[float | None] = []
    for m in range(1, 13):
        mask = months == m
        n = int(mask.sum())
        if n == 0:
            monthly.append({
                "month": m, "n": 0, "flux_mean_kw_m": None, "hm0_mean_m": None, "te_mean_s": None,
                "flux_p10_kw_m": None, "flux_p50_kw_m": None, "flux_p90_kw_m": None,
            })
            raw_monthly_flux.append(None)
            continue
        flux_m = flux[mask]
        hm0_m = hm0[mask]
        te_m = te[mask]
        # Compute means from finite values with explicit masks (no np.nanmean, avoids
        # RuntimeWarning on all-NaN slices)
        f_finite = flux_m[np.isfinite(flux_m)]
        h_finite = hm0_m[np.isfinite(hm0_m)]
        t_finite = te_m[np.isfinite(te_m)]
        flux_mean_raw = float(f_finite.mean()) if f_finite.size > 0 else None
        hm0_mean_raw = float(h_finite.mean()) if h_finite.size > 0 else None
        te_mean_raw = float(t_finite.mean()) if t_finite.size > 0 else None
        raw_monthly_flux.append(flux_mean_raw)

        # Per-year monthly mean flux, then 10th/50th/90th percentile across years
        year_set = sorted(set(years[mask]))
        year_means = []
        for yr in year_set:
            yr_month_mask = mask & (years == yr)
            yr_flux = flux[yr_month_mask]
            finite = yr_flux[np.isfinite(yr_flux)]
            if finite.size > 0:
                year_means.append(float(finite.mean()))
        if len(year_means) >= 3:
            arr = np.array(year_means)
            flux_p10 = _round3(float(np.percentile(arr, 10)))
            flux_p50 = _round3(float(np.percentile(arr, 50)))
            flux_p90 = _round3(float(np.percentile(arr, 90)))
        else:
            flux_p10 = flux_p50 = flux_p90 = None

        monthly.append({
            "month": m, "n": n,
            "flux_mean_kw_m": _round3(flux_mean_raw), "hm0_mean_m": _round3(hm0_mean_raw),
            "te_mean_s": _round3(te_mean_raw),
            "flux_p10_kw_m": flux_p10, "flux_p50_kw_m": flux_p50, "flux_p90_kw_m": flux_p90,
        })

    # Seasonal: DJF, MAM, JJA, SON
    # December is grouped with the following January/February of the *same calendar year's* records,
    # i.e. simply by month number (Dec month 12, Jan month 1, Feb month 2 all belong to whatever
    # calendar year they fall in; the grouping is by month, not by shifting December to the next year).
    season_map = {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11]}
    # Unrounded season means (None when no finite flux records in that season)
    season_means_raw: dict[str, float | None] = {}
    for name, month_list in season_map.items():
        mask = np.isin(months, month_list)
        season_flux = flux[mask]
        finite = season_flux[np.isfinite(season_flux)]
        season_means_raw[name] = float(finite.mean()) if finite.size > 0 else None

    # Share denominator: sum of the four season means.
    # The share is defined against the sum of the four season means; if fewer than
    # 4 seasons have data the share is undefined (None) because the annual baseline
    # is incomplete.
    seasons_with_data = [v for v in season_means_raw.values() if v is not None]
    all_four = len(seasons_with_data) == 4
    seasonal = []
    for name in season_map:
        raw = season_means_raw[name]
        if raw is None:
            seasonal.append({
                "season": name,
                "flux_mean_kw_m": None,
                "share_of_annual_pct": None,
                "reason": "no finite flux records in this season",
            })
        elif not all_four:
            seasonal.append({
                "season": name,
                "flux_mean_kw_m": _round3(raw),
                "share_of_annual_pct": None,
                "reason": "not all 4 seasons have data, so the annual baseline is incomplete",
            })
        else:
            denom = float(np.sum(seasons_with_data))
            share = 100.0 * raw / denom if denom > 0 else None
            seasonal.append({
                "season": name,
                "flux_mean_kw_m": _round3(raw),
                "share_of_annual_pct": _round3(share),
            })

    raw = {"monthly_flux": raw_monthly_flux, "seasonal_flux": season_means_raw}
    return {"monthly": monthly, "seasonal": seasonal}, raw


# ---------------------------------------------------------------------------
# Variability indices
# ---------------------------------------------------------------------------

def _variability_indices(climatology: dict, clim_raw: dict,
                         frame: pd.DataFrame, unverified: list[str]) -> dict:
    """Compute COV, annual mean flux, MVI, and SVI from unrounded internal values.

    ``clim_raw`` contains the unrounded monthly and seasonal flux means so that
    indices are never computed from already-rounded numbers (CONTRACT RULE 8).
    """
    flux = frame["flux"].to_numpy(float)
    finite_flux = flux[np.isfinite(flux)]

    # COV = population std / mean of per-record flux
    if finite_flux.size > 0 and finite_flux.mean() > 0:
        cov = float(finite_flux.std(ddof=0) / finite_flux.mean())
        cov_entry = {"value": _round3(cov), "formula": "std(flux, ddof=0) / mean(flux)", "reason": None}
    else:
        cov_entry = {"value": None, "formula": "std(flux, ddof=0) / mean(flux)", "reason": "no finite flux records"}

    # Annual mean flux = mean of the 12 climatological monthly means (unrounded)
    raw_monthly = clim_raw["monthly_flux"]  # list of 12 float|None
    all_twelve = all(v is not None for v in raw_monthly)
    if all_twelve:
        annual_mean = float(np.mean(raw_monthly))
        annual_entry = {"value": _round3(annual_mean), "formula": "mean of 12 climatological monthly means",
                        "reason": None}
    else:
        annual_mean = None
        annual_entry = {"value": None, "formula": "mean of 12 climatological monthly means",
                        "reason": "not all 12 months have data"}

    # MVI = (max monthly mean − min monthly mean) / annual_mean_flux (unrounded)
    if all_twelve and annual_mean and annual_mean > 0:
        mvi_val = float((max(raw_monthly) - min(raw_monthly)) / annual_mean)
        mvi_entry = {"value": _round3(mvi_val),
                     "formula": "(max_monthly_mean − min_monthly_mean) / annual_mean_flux", "reason": None}
    else:
        mvi_entry = {"value": None,
                     "formula": "(max_monthly_mean − min_monthly_mean) / annual_mean_flux",
                     "reason": "annual mean flux unavailable" if not all_twelve else "annual mean flux is zero"}

    # SVI = (max seasonal mean − min seasonal mean) / annual_mean_flux (unrounded)
    raw_seasonal = clim_raw["seasonal_flux"]  # dict of 4 float|None
    seasonal_vals = [v for v in raw_seasonal.values() if v is not None]
    all_four_seasons = len(seasonal_vals) == 4
    if all_four_seasons and annual_mean and annual_mean > 0:
        svi_val = float((max(seasonal_vals) - min(seasonal_vals)) / annual_mean)
        svi_entry = {"value": _round3(svi_val),
                     "formula": "(max_seasonal_mean − min_seasonal_mean) / annual_mean_flux", "reason": None}
    elif not all_four_seasons:
        svi_entry = {"value": None,
                     "formula": "(max_seasonal_mean − min_seasonal_mean) / annual_mean_flux",
                     "reason": "not all 4 seasons have data"}
    else:
        svi_entry = {"value": None,
                     "formula": "(max_seasonal_mean − min_seasonal_mean) / annual_mean_flux",
                     "reason": "annual mean flux unavailable"}

    unverified.append(
        "MVI and SVI follow the definition described by Kamranzad et al. (2016, Energy): the range of the "
        "monthly or seasonal mean flux divided by the annual mean flux (they cite Cornett 2008 and Zheng et al. "
        "2013; those papers were not read). Here the annual mean is the mean of the 12 monthly means. The "
        "definition of COV used here (std / mean of per-record flux) was not checked against a source."
    )

    return {
        "cov": cov_entry,
        "annual_mean_flux_kw_m": annual_entry,
        "mvi": mvi_entry,
        "svi": svi_entry,
    }


# ---------------------------------------------------------------------------
# Interannual variation
# ---------------------------------------------------------------------------

def _interannual(frame: pd.DataFrame, record: dict) -> dict | None:
    full_years = record["full_years"]
    if len(full_years) < 3:
        return {"data": None, "reason": "fewer than 3 complete years"}

    flux = frame["flux"].to_numpy(float)
    hm0 = frame["hm0"].to_numpy(float)
    years = frame.index.year

    annual_data = []
    for yr in full_years:
        mask = years == yr
        yr_flux = flux[mask]
        yr_hm0 = hm0[mask]
        finite_flux = yr_flux[np.isfinite(yr_flux)]
        finite_hm0 = yr_hm0[np.isfinite(yr_hm0)]
        annual_data.append({
            "year": int(yr),
            "flux_mean_kw_m": float(finite_flux.mean()) if finite_flux.size > 0 else None,
            "hm0_mean_m": float(finite_hm0.mean()) if finite_hm0.size > 0 else None,
        })

    flux_means = [d["flux_mean_kw_m"] for d in annual_data if d["flux_mean_kw_m"] is not None]
    if len(flux_means) < 3:
        return {"data": None, "reason": "fewer than 3 complete years with finite flux"}

    mean_of_annuals = float(np.mean(flux_means))

    # Compute summary statistics from unrounded values first (CONTRACT RULE 8)
    cov = float(np.std(flux_means, ddof=0) / mean_of_annuals) if mean_of_annuals > 0 else None
    # Find max/min from unrounded data
    max_entry = max(annual_data, key=lambda d: d["flux_mean_kw_m"] if d["flux_mean_kw_m"] is not None else -1)
    min_entry = min(annual_data, key=lambda d: d["flux_mean_kw_m"] if d["flux_mean_kw_m"] is not None else float("inf"))
    range_ratio = (max_entry["flux_mean_kw_m"] / min_entry["flux_mean_kw_m"]
                   if min_entry["flux_mean_kw_m"] and min_entry["flux_mean_kw_m"] > 0 else None)
    max_year = max_entry["year"]
    min_year = min_entry["year"]

    # Now round for output
    for d in annual_data:
        if d["flux_mean_kw_m"] is not None and mean_of_annuals > 0:
            d["anomaly_pct"] = _round3(100.0 * (d["flux_mean_kw_m"] - mean_of_annuals) / mean_of_annuals)
        else:
            d["anomaly_pct"] = None
        d["flux_mean_kw_m"] = _round3(d["flux_mean_kw_m"])
        d["hm0_mean_m"] = _round3(d["hm0_mean_m"])

    return {
        "data": annual_data,
        "cov_of_annual_means": _round3(cov),
        "max_year": max_year,
        "min_year": min_year,
        "range_ratio": _round3(range_ratio),
        "notes": "Climate-mode influence (ENSO, IOD) is not assessed.",
    }


# ---------------------------------------------------------------------------
# Extremes: peaks over threshold for Hm0
# ---------------------------------------------------------------------------

def _decluster(times: np.ndarray, values: np.ndarray, threshold: float,
               decluster_hours: float) -> tuple[np.ndarray, np.ndarray]:
    """Runs declustering: maximal runs of exceedances where consecutive ones are < decluster_hours apart.

    Returns (peak_times, peak_values) — the cluster maxima.
    """
    exceed_mask = values > threshold
    exceed_idx = np.where(exceed_mask)[0]
    if len(exceed_idx) == 0:
        return np.array([], dtype="datetime64[ns]"), np.array([], dtype=float)

    # Sort by time (should already be sorted, but be safe)
    order = np.argsort(times[exceed_idx])
    exceed_idx = exceed_idx[order]

    clusters: list[list[int]] = []
    current_cluster = [exceed_idx[0]]
    for i in range(1, len(exceed_idx)):
        gap_hours = (times[exceed_idx[i]] - times[exceed_idx[i - 1]]) / np.timedelta64(1, "h")
        if gap_hours < decluster_hours:
            current_cluster.append(exceed_idx[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [exceed_idx[i]]
    clusters.append(current_cluster)

    peak_times = []
    peak_values = []
    for cluster in clusters:
        vals = values[cluster]
        best = np.argmax(vals)
        peak_times.append(times[cluster[best]])
        peak_values.append(vals[best])

    return np.array(peak_times), np.array(peak_values)


def _return_level(u: float, sigma: float, xi: float, lam: float, T: float) -> float:
    """GPD return level for return period T years."""
    if abs(xi) >= 1e-6:
        return u + (sigma / xi) * ((lam * T) ** xi - 1.0)
    else:
        return u + sigma * math.log(lam * T)


def _fit_gpd_and_levels(excesses: np.ndarray, u: float, lam: float,
                        return_years: list[float]) -> dict | None:
    """Fit GPD by MLE and compute return levels.  Returns None on fit failure."""
    try:
        c, _, scale = genpareto.fit(excesses, floc=0)
    except Exception:
        return None
    levels = {}
    for T in return_years:
        levels[str(T)] = _round3(_return_level(u, scale, c, lam, T))
    return {"xi": float(c), "sigma": float(scale), "levels": levels}


def _extremes(frame: pd.DataFrame, record: dict, *, exact_years: float, threshold_pct: float,
              decluster_hours: float, return_years: list | tuple,
              bootstrap: int, seed: int, min_exceedances: int,
              warnings: list[str]) -> dict:
    hm0 = frame["hm0"].to_numpy(float)
    times = frame.index.to_numpy()
    finite_mask = np.isfinite(hm0)
    hm0_finite = hm0[finite_mask]

    if hm0_finite.size == 0:
        return {"status": "insufficient", "reason": "no finite Hm0 values", "n_peaks": 0}

    # Step 1: threshold
    u = float(np.percentile(hm0_finite, threshold_pct))

    # Step 2: decluster
    peak_times, peak_values = _decluster(times[finite_mask], hm0_finite, u, decluster_hours)
    n_peaks = len(peak_values)

    # Step 3: insufficient peaks
    if n_peaks < min_exceedances:
        return {"status": "insufficient", "reason": f"only {n_peaks} peaks, need {min_exceedances}",
                "n_peaks": n_peaks}

    # Step 4: excesses
    excesses = peak_values - u

    # Step 5: rate over observed time
    n_finite = int(finite_mask.sum())
    step_hours = record.get("step_hours") if record else None
    if (step_hours is None or step_hours <= 0) and len(times) > 1:
        diffs = np.diff(times).astype("timedelta64[s]").astype(float)
        step_hours = float(np.median(diffs) / 3600.0)

    if step_hours and step_hours > 0:
        observed_years = (n_finite - 1) * step_hours / (24.0 * _DAYS_PER_YEAR)
    else:
        observed_years = exact_years

    lam = n_peaks / observed_years if observed_years > 0 else 0.0

    # Step 4: fit GPD
    try:
        c, _, sigma = genpareto.fit(excesses, floc=0)
    except Exception:
        return {"status": "fit_failed", "reason": "GPD MLE fit failed", "n_peaks": n_peaks}
    xi = float(c)
    sigma = float(sigma)

    # Shape warning
    if abs(xi) > 0.5:
        warnings.append("GPD shape parameter |ξ| > 0.5: the maximum-likelihood estimator is outside "
                        "the range where it is regular.")

    if observed_years < 0.95 * exact_years:
        warnings.append(
            f"The record has gaps: Hm0 was observed for {observed_years:.1f} of {exact_years:.1f} years. "
            "The event rate uses the observed time, not the calendar span."
        )

    if step_hours is not None and step_hours > 1.0:
        warnings.append(
            f"Hm0 is sampled every {step_hours:g} h: storm peaks between samples are missed, "
            "so extreme levels may be biased low. The size of the bias was shown on synthetic data only, "
            "not on real ERA5."
        )

    # Step 6: return levels
    return_years_list = [float(t) for t in return_years]
    levels = []
    for T in return_years_list:
        x_T = _return_level(u, sigma, xi, lam, T)
        if T > 3 * observed_years:
            warnings.append(f"Return period {T} yr exceeds 3× the record length ({observed_years:.1f} yr): "
                            "extrapolation beyond three times the record length is unreliable.")
        levels.append({"return_period_yr": T, "level_m": _round3(x_T), "ci_low_m": None, "ci_high_m": None})

    # Step 7: bootstrap
    bootstrap_failures = 0
    if bootstrap > 0:
        rng = np.random.default_rng(seed)
        boot_levels = {str(T): [] for T in return_years_list}
        for _ in range(bootstrap):
            n_star = max(10, int(rng.poisson(lam * observed_years)))
            resample = rng.choice(excesses, size=n_star, replace=True)
            try:
                c_b, _, sigma_b = genpareto.fit(resample, floc=0)
            except Exception:
                bootstrap_failures += 1
                continue
            lam_star = n_star / observed_years if observed_years > 0 else 0.0
            for T in return_years_list:
                x_T_b = _return_level(u, float(sigma_b), float(c_b), lam_star, T)
                boot_levels[str(T)].append(x_T_b)

        for entry in levels:
            T_key = str(entry["return_period_yr"])
            samples = boot_levels[T_key]
            if len(samples) >= 2:
                entry["ci_low_m"] = _round3(float(np.percentile(samples, 5)))
                entry["ci_high_m"] = _round3(float(np.percentile(samples, 95)))

        if bootstrap_failures > 0.05 * bootstrap:
            warnings.append(f"Bootstrap: {bootstrap_failures} of {bootstrap} replicates failed to fit "
                            f"({100 * bootstrap_failures / bootstrap:.1f}%).")

    rate_out = _round3(lam)
    note_rate = rate_out if rate_out > 0 else lam
    for entry in levels:
        T = entry["return_period_yr"]
        if lam * T < 1.0:
            entry["level_m"] = None
            entry["ci_low_m"] = None
            entry["ci_high_m"] = None
            entry["note"] = f"shorter than the mean time between events (1 / rate = {1 / note_rate:.2f} yr)"

    # Step 8: threshold sensitivity
    sensitivity = []
    max_return = max(return_years_list)
    for pct in _SENSITIVITY_PERCENTILES:
        u_s = float(np.percentile(hm0_finite, pct))
        pt_s, pv_s = _decluster(times[finite_mask], hm0_finite, u_s, decluster_hours)
        n_s = len(pv_s)
        if n_s < min_exceedances:
            sensitivity.append({"percentile": pct, "threshold_m": _round3(u_s), "n_peaks": n_s,
                                "xi": None, "sigma": None, "level_m": None})
            continue
        exc_s = pv_s - u_s
        try:
            c_s, _, sigma_s = genpareto.fit(exc_s, floc=0)
        except Exception:
            sensitivity.append({"percentile": pct, "threshold_m": _round3(u_s), "n_peaks": n_s,
                                "xi": None, "sigma": None, "level_m": None})
            continue
        lam_s = n_s / observed_years if observed_years > 0 else 0.0
        x_T_s = _return_level(u_s, float(sigma_s), float(c_s), lam_s, max_return)
        sensitivity.append({
            "percentile": pct, "threshold_m": _round3(u_s), "n_peaks": n_s,
            "xi": _round3(float(c_s)), "sigma": _round3(float(sigma_s)),
            "level_m": _round3(x_T_s),
        })

    # Step 9: return-level plot data
    # Empirical: peaks sorted descending with Weibull plotting positions
    sorted_idx = np.argsort(peak_values)[::-1]
    sorted_values = peak_values[sorted_idx]
    n = len(sorted_values)
    ranks = np.arange(1, n + 1)
    # T_i = 1 / (lambda * (i / (n + 1)))
    empirical_T = 1.0 / (lam * (ranks / (n + 1))) if lam > 0 else np.full(n, np.nan)
    empirical = [{"return_period_yr": _round3(float(empirical_T[i])),
                  "hm0_m": _round3(float(sorted_values[i]))}
                 for i in range(n)]

    # Fitted curve at 40 log-spaced T
    T_min = 1.0 / lam if lam > 0 else 0.1
    T_max = max(return_years_list) * 1.2
    T_curve = np.logspace(np.log10(max(T_min, 0.01)), np.log10(T_max), 40)
    curve = [{"return_period_yr": _round3(float(t)),
              "level_m": _round3(_return_level(u, sigma, xi, lam, float(t)))}
             for t in T_curve]

    method = (f"Peaks over threshold: runs declustering ({decluster_hours} h), "
              f"GPD fitted by MLE (scipy.stats.genpareto.fit, floc=0), "
              f"bootstrap {bootstrap} replicates (Poisson rate, resampled excesses), seed={seed}.")

    return {
        "status": "ok",
        "threshold_m": _round3(u),
        "threshold_pct": threshold_pct,
        "n_peaks": n_peaks,
        "rate_per_yr": _round3(lam),
        "observed_years": _round3(observed_years),
        "record_years": _round3(exact_years),
        "xi": _round3(xi),
        "sigma": _round3(sigma),
        "levels": levels,
        "bootstrap_failures": bootstrap_failures,
        "bootstrap_count": bootstrap,
        "sensitivity": sensitivity,
        "empirical": empirical,
        "curve": curve,
        "method": method,
    }


# ---------------------------------------------------------------------------
# Caching helpers for the plugin
# ---------------------------------------------------------------------------

def params_hash(version: int, threshold_pct: float, decluster_hours: float, return_years: list,
                bootstrap: int, seed: int, node: tuple | None) -> str:
    """Deterministic short hash of the computation parameters and grid node."""
    key = json.dumps({
        "v": version, "t": threshold_pct, "d": decluster_hours, "r": sorted(return_years),
        "b": bootstrap, "s": seed, "n": list(node) if node else None,
    }, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def report_markdown(result: dict) -> str:
    """Render the key tables as a markdown report section."""
    lines = ["## Long-term statistics\n"]

    # Record
    rec = result["record"]
    lines.append(f"**Record:** {rec['start']} to {rec['end']} ({rec['years']} years, "
                 f"{rec['records']} records, {len(rec['full_years'])} full years)\n")

    # Monthly climatology
    lines.append("### Monthly climatology\n")
    lines.append("| Month | N | Flux mean (kW/m) | Hm0 mean (m) | Te mean (s) |")
    lines.append("|-------|---|-------------------|--------------|-------------|")
    for m in result["climatology"]["monthly"]:
        lines.append(f"| {m['month']:02d} | {m['n']} | {m['flux_mean_kw_m']} | "
                     f"{m['hm0_mean_m']} | {m['te_mean_s']} |")

    # Indices
    lines.append("\n### Variability indices\n")
    idx = result["indices"]
    for name in ("cov", "mvi", "svi"):
        entry = idx[name]
        val = entry["value"] if entry["value"] is not None else f"N/A ({entry['reason']})"
        lines.append(f"- **{name.upper()}** = {val}  ({entry['formula']})")
    ann = idx["annual_mean_flux_kw_m"]
    lines.append(f"- **Annual mean flux** = {ann['value']} kW/m" if ann["value"] is not None
                 else f"- **Annual mean flux** = N/A ({ann['reason']})")

    # Interannual
    lines.append("\n### Interannual variation\n")
    ia = result["interannual"]
    if ia.get("data") is None:
        lines.append(f"Not computed: {ia.get('reason', 'unknown')}\n")
    else:
        lines.append(f"COV of annual means: {ia['cov_of_annual_means']}, "
                     f"max year: {ia['max_year']}, min year: {ia['min_year']}, "
                     f"range ratio: {ia['range_ratio']}\n")
        lines.append("| Year | Flux mean (kW/m) | Hm0 mean (m) | Anomaly (%) |")
        lines.append("|------|-------------------|--------------|-------------|")
        for d in ia["data"]:
            lines.append(f"| {d['year']} | {d['flux_mean_kw_m']} | {d['hm0_mean_m']} | {d['anomaly_pct']} |")

    # Extremes
    lines.append("\n### Extreme Hm0 return levels\n")
    ext = result["extremes"]
    if ext["status"] != "ok":
        lines.append(f"Not computed: {ext.get('reason', 'unknown')}\n")
    else:
        lines.append(f"Threshold: {ext['threshold_m']} m (P{ext['threshold_pct']}), "
                     f"{ext['n_peaks']} peaks, rate {ext['rate_per_yr']}/yr, "
                     f"GPD ξ={ext['xi']}, σ={ext['sigma']} m\n")
        lines.append("| Return period (yr) | Level (m) | 90% CI low (m) | 90% CI high (m) |")
        lines.append("|--------------------|-----------|----------------|-----------------|")
        for lv in ext["levels"]:
            level_str = "n/a" if lv.get("level_m") is None else lv["level_m"]
            ci_low_str = "n/a" if lv.get("ci_low_m") is None else lv["ci_low_m"]
            ci_high_str = "n/a" if lv.get("ci_high_m") is None else lv["ci_high_m"]
            lines.append(f"| {lv['return_period_yr']} | {level_str} | "
                         f"{ci_low_str} | {ci_high_str} |")
        lines.append(f"\nMethod: {ext['method']}")

    # Warnings
    if result["warnings"]:
        lines.append("\n### Warnings\n")
        for w in result["warnings"]:
            lines.append(f"- {w}")

    lines.append("")
    return "\n".join(lines)
