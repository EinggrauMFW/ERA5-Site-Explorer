"""Tests for long-term statistics: climatology, indices, interannual variation, extremes and the HTTP route."""

import json
import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import genpareto

import app as appmodule
import longterm
from tests.test_provenance_crosscheck import build_a, make_job


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_frame(years=3, step_hours=6, flux_by_month=None):
    """Build a canonical frame with known per-month flux.

    ``flux_by_month`` maps month (1..12) to a constant flux value (kW/m).
    If None a default monotonic set is used.  Hm0 and Te are set so flux ≈ 0.4906 * hm0² * te
    for each record, but the flux column is assigned directly (never recomputed from Hm0/Te).
    """
    if flux_by_month is None:
        flux_by_month = {m: float(m * 5) for m in range(1, 13)}  # 5, 10, ..., 60 kW/m
    start = pd.Timestamp("2010-01-01")
    end = start + pd.DateOffset(years=years) - pd.Timedelta(hours=step_hours)
    index = pd.date_range(start, end, freq=f"{step_hours}h")
    n = len(index)
    flux = np.array([flux_by_month[m] for m in index.month], dtype=float)
    hm0 = np.sqrt(flux / (0.4906 * 10.0))  # back-derive Hm0 for Te=10 s
    te = np.full(n, 10.0)
    frame = pd.DataFrame({"hm0": hm0, "te": te, "tp": te * 1.1, "dir_from": 180.0, "flux": flux}, index=index)
    frame.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": step_hours})
    return frame


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: None)
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Climatology and variability indices
# ---------------------------------------------------------------------------

def test_monthly_climatology_means_exact():
    flux_by_month = {m: float(m * 5) for m in range(1, 13)}
    frame = _make_frame(years=3, flux_by_month=flux_by_month)
    result = longterm.compute_longterm(frame, bootstrap=0)
    clim = result["climatology"]

    for m in range(1, 13):
        entry = clim["monthly"][m - 1]
        assert entry["month"] == m
        assert entry["flux_mean_kw_m"] == pytest.approx(flux_by_month[m], rel=1e-6)
        assert entry["n"] > 0

    # Percentiles need ≥ 3 years → should be present for a 3-year record
    for entry in clim["monthly"]:
        assert entry["flux_p10_kw_m"] is not None


def test_annual_mean_and_indices():
    flux_by_month = {m: float(m * 5) for m in range(1, 13)}
    frame = _make_frame(years=3, flux_by_month=flux_by_month)
    result = longterm.compute_longterm(frame, bootstrap=0)
    idx = result["indices"]

    monthly_means = [m * 5.0 for m in range(1, 13)]
    expected_annual = float(np.mean(monthly_means))
    assert idx["annual_mean_flux_kw_m"]["value"] == pytest.approx(expected_annual, rel=1e-6)

    # MVI = (max − min) / annual_mean
    expected_mvi = (60.0 - 5.0) / expected_annual
    assert idx["mvi"]["value"] == pytest.approx(expected_mvi, rel=1e-3)

    # SVI uses seasonal means
    seasonal_means = [
        np.mean([flux_by_month[12], flux_by_month[1], flux_by_month[2]]),   # DJF
        np.mean([flux_by_month[3], flux_by_month[4], flux_by_month[5]]),    # MAM
        np.mean([flux_by_month[6], flux_by_month[7], flux_by_month[8]]),    # JJA
        np.mean([flux_by_month[9], flux_by_month[10], flux_by_month[11]]),  # SON
    ]
    expected_svi = (max(seasonal_means) - min(seasonal_means)) / expected_annual
    assert idx["svi"]["value"] == pytest.approx(expected_svi, rel=1e-3)

    # COV = population std / mean of per-record flux
    flux = frame["flux"].to_numpy(float)
    expected_cov = float(flux.std(ddof=0) / flux.mean())
    assert idx["cov"]["value"] == pytest.approx(expected_cov, rel=1e-3)


def test_missing_month_gives_none_annual_mean():
    """A record missing a full month means annual_mean = None."""
    flux_by_month = {m: float(m * 5) for m in range(1, 13)}
    frame = _make_frame(years=3, flux_by_month=flux_by_month)
    # Drop all March records
    frame = frame[frame.index.month != 3]
    result = longterm.compute_longterm(frame, bootstrap=0)
    assert result["indices"]["annual_mean_flux_kw_m"]["value"] is None
    assert result["indices"]["annual_mean_flux_kw_m"]["reason"] is not None
    assert result["indices"]["mvi"]["value"] is None


# ---------------------------------------------------------------------------
# Interannual variation
# ---------------------------------------------------------------------------

def test_interannual_with_known_annual_means():
    # Use different flux each year to get known annual means
    flux_by_month = {m: float(10.0) for m in range(1, 13)}
    frame = _make_frame(years=4, flux_by_month=flux_by_month)
    # Scale year 2 by 1.1 and year 3 by 0.9
    mask_2011 = frame.index.year == 2011
    mask_2012 = frame.index.year == 2012
    frame.loc[mask_2011, "flux"] *= 1.1
    frame.loc[mask_2012, "flux"] *= 0.9
    result = longterm.compute_longterm(frame, bootstrap=0)
    ia = result["interannual"]
    assert ia["data"] is not None
    flux_means = {d["year"]: d["flux_mean_kw_m"] for d in ia["data"]}
    # Year 2011 should be ~11, year 2012 should be ~9
    assert flux_means[2011] == pytest.approx(11.0, rel=1e-3)
    assert flux_means[2012] == pytest.approx(9.0, rel=1e-3)
    assert ia["max_year"] == 2011
    assert ia["min_year"] == 2012
    assert ia["range_ratio"] is not None and ia["range_ratio"] > 1.0


def test_incomplete_year_excluded_from_interannual():
    """A year with < 90% coverage should be excluded from full_years."""
    flux_by_month = {m: 10.0 for m in range(1, 13)}
    frame = _make_frame(years=4, flux_by_month=flux_by_month)
    # Remove most of the last year (2013)
    frame = frame[~((frame.index.year == 2013) & (frame.index.month >= 3))]
    result = longterm.compute_longterm(frame, bootstrap=0)
    assert 2013 not in result["record"]["full_years"]


def test_fewer_than_3_full_years_returns_none():
    frame = _make_frame(years=2)
    result = longterm.compute_longterm(frame, bootstrap=0)
    ia = result["interannual"]
    assert ia.get("data") is None
    assert "fewer than 3" in ia["reason"]


# ---------------------------------------------------------------------------
# Declustering
# ---------------------------------------------------------------------------

def test_declustering_two_peaks_24h_apart_and_third_200h_later():
    """Two exceedances 24 h apart → one cluster; a third 200 h later → second cluster."""
    times = np.array([
        np.datetime64("2020-01-01T00:00"),
        np.datetime64("2020-01-02T00:00"),  # 24 h after first
        np.datetime64("2020-01-10T08:00"),  # 200 h after second
    ])
    values = np.array([5.0, 6.0, 4.5])
    threshold = 4.0

    pt, pv = longterm._decluster(times, values, threshold, decluster_hours=48.0)
    assert len(pv) == 2
    # First cluster max is 6.0, second is 4.5
    assert pv[0] == 6.0
    assert pv[1] == 4.5


# ---------------------------------------------------------------------------
# GPD fit and return levels
# ---------------------------------------------------------------------------

def test_gpd_fit_recovers_known_parameters():
    """Draw excesses from a known GPD and verify the fit recovers the shape and scale."""
    rng = np.random.default_rng(42)
    true_xi = 0.1
    true_sigma = 0.5
    n_excesses = 500
    excesses = genpareto.rvs(c=true_xi, scale=true_sigma, size=n_excesses, random_state=rng)

    # Place excesses above a threshold in a long synthetic Hm0 series
    threshold = 3.0
    n_records = 20000
    step_hours = 6
    # Background Hm0 below threshold
    hm0 = rng.uniform(0.5, threshold - 0.01, size=n_records)
    # Insert exceedances at widely spaced intervals (> 48 h apart = every 9+ steps)
    positions = np.linspace(100, n_records - 100, n_excesses, dtype=int)
    for i, pos in enumerate(positions):
        hm0[pos] = threshold + excesses[i]

    index = pd.date_range("2000-01-01", periods=n_records, freq=f"{step_hours}h")
    frame = pd.DataFrame({
        "hm0": hm0,
        "te": np.full(n_records, 10.0),
        "tp": np.full(n_records, 11.0),
        "dir_from": np.full(n_records, 180.0),
        "flux": 0.4906 * hm0**2 * 10.0,
    }, index=index)
    frame.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": step_hours})

    # Use the exact threshold percentile that yields close to `threshold`
    pct_rank = 100.0 * np.searchsorted(np.sort(hm0), threshold) / n_records
    result = longterm.compute_longterm(
        frame, threshold_pct=pct_rank, decluster_hours=48.0,
        return_years=[1, 10, 50, 100], bootstrap=100, seed=42,
    )
    ext = result["extremes"]
    assert ext["status"] == "ok"
    # The fit should recover xi and sigma within tolerance
    assert ext["xi"] == pytest.approx(true_xi, abs=0.15)
    assert ext["sigma"] == pytest.approx(true_sigma, abs=0.15)

    # Return levels should agree with analytic GPD return level within the bootstrap CI
    rec_years = result["record"]["years"]
    lam = ext["n_peaks"] / rec_years
    for lv in ext["levels"]:
        T = lv["return_period_yr"]
        analytic = threshold + (true_sigma / true_xi) * ((lam * T) ** true_xi - 1.0)
        if lv["ci_low_m"] is not None and lv["ci_high_m"] is not None:
            assert lv["ci_low_m"] <= analytic <= lv["ci_high_m"] or \
                   abs(lv["level_m"] - analytic) / max(analytic, 1e-6) < 0.3


def test_xi_near_zero_uses_log_branch():
    """When excesses are exponential (xi = 0), the log formula branch is used."""
    rng = np.random.default_rng(99)
    # Exponential distribution = GPD with xi=0
    true_sigma = 1.0
    n_excesses = 200
    excesses = rng.exponential(true_sigma, size=n_excesses)

    threshold = 2.0
    n_records = 10000
    step_hours = 6
    hm0 = rng.uniform(0.5, threshold - 0.01, size=n_records)
    positions = np.linspace(100, n_records - 100, n_excesses, dtype=int)
    for i, pos in enumerate(positions):
        hm0[pos] = threshold + excesses[i]

    index = pd.date_range("2000-01-01", periods=n_records, freq=f"{step_hours}h")
    frame = pd.DataFrame({
        "hm0": hm0,
        "te": np.full(n_records, 10.0),
        "tp": np.full(n_records, 11.0),
        "dir_from": np.full(n_records, 180.0),
        "flux": 0.4906 * hm0**2 * 10.0,
    }, index=index)
    frame.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": step_hours})

    pct_rank = 100.0 * np.searchsorted(np.sort(hm0), threshold) / n_records
    result = longterm.compute_longterm(frame, threshold_pct=pct_rank, bootstrap=0)
    ext = result["extremes"]
    assert ext["status"] == "ok"
    # xi should be near zero
    assert abs(ext["xi"]) < 0.2

    # Verify the log branch is used for the near-zero case
    lam = ext["rate_per_yr"]
    for lv in ext["levels"]:
        T = lv["return_period_yr"]
        if abs(ext["xi"]) < 1e-6:
            expected = ext["threshold_m"] + ext["sigma"] * math.log(lam * T)
            assert lv["level_m"] == pytest.approx(expected, rel=1e-2)


def test_insufficient_peaks_returns_status():
    frame = _make_frame(years=3)
    # Set all Hm0 to the same value so no peaks above threshold
    frame["hm0"] = 1.0
    result = longterm.compute_longterm(frame, threshold_pct=99.9, bootstrap=0, min_exceedances=30)
    ext = result["extremes"]
    assert ext["status"] == "insufficient"
    assert ext["n_peaks"] < 30


def test_bootstrap_zero_omits_intervals():
    frame = _make_frame(years=5)
    result = longterm.compute_longterm(frame, bootstrap=0)
    ext = result["extremes"]
    if ext["status"] == "ok":
        for lv in ext["levels"]:
            assert lv["ci_low_m"] is None
            assert lv["ci_high_m"] is None


def test_same_seed_gives_identical_output():
    frame = _make_frame(years=5)
    r1 = longterm.compute_longterm(frame, bootstrap=50, seed=42)
    r2 = longterm.compute_longterm(frame, bootstrap=50, seed=42)
    assert r1["extremes"] == r2["extremes"]


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def test_short_record_warning():
    frame = _make_frame(years=3)
    result = longterm.compute_longterm(frame, bootstrap=0)
    assert any("not a resource estimate" in w for w in result["warnings"])


def test_return_period_beyond_3x_record_warning():
    """50-year return period with a 3-year record should warn about extrapolation."""
    frame = _make_frame(years=3)
    # Add random noise so peaks exist above the threshold
    rng = np.random.default_rng(77)
    frame["hm0"] = frame["hm0"] + rng.standard_normal(len(frame)) * 0.5
    frame["flux"] = 0.4906 * frame["hm0"] ** 2 * frame["te"]
    result = longterm.compute_longterm(frame, return_years=[1, 50], bootstrap=0,
                                       threshold_pct=90.0, min_exceedances=5)
    assert result["extremes"]["status"] == "ok", f"extremes not ok: {result['extremes']}"
    assert any("three times the record" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------

def test_longterm_route_defaults(client):
    make_job("lt_test_00001", "single-levels", build_a)
    response = client.get("/api/jobs/lt_test_00001/longterm")
    assert response.status_code == 200
    data = response.get_json()
    assert "record" in data and "climatology" in data and "indices" in data
    assert "interannual" in data and "extremes" in data and "warnings" in data
    assert "parameters" in data
    # Parameters are defaults
    assert data["parameters"]["threshold_pct"] == 95.0
    assert data["parameters"]["bootstrap"] == 300


def test_longterm_route_writes_cache_and_report(client):
    make_job("lt_test_00002", "single-levels", build_a)
    client.get("/api/jobs/lt_test_00002/longterm")
    folder = appmodule.DOWNLOADS / "lt_test_00002"
    # Cache file exists
    cache_files = list((folder / "results").glob("longterm_*.json"))
    assert len(cache_files) >= 1
    # Report section exists
    report_files = list((folder / "report_sections").glob("40_longterm.md"))
    assert len(report_files) == 1


def test_longterm_route_reuses_cache(client):
    make_job("lt_test_00003", "single-levels", build_a)
    client.get("/api/jobs/lt_test_00003/longterm")
    folder = appmodule.DOWNLOADS / "lt_test_00003"
    cache_files = list((folder / "results").glob("longterm_*.json"))
    assert len(cache_files) >= 1
    mtime = cache_files[0].stat().st_mtime_ns
    # Second call should reuse the cache
    client.get("/api/jobs/lt_test_00003/longterm")
    assert cache_files[0].stat().st_mtime_ns == mtime


def test_longterm_route_validates_params(client):
    make_job("lt_test_00004", "single-levels", build_a)
    # threshold_pct out of range
    r = client.get("/api/jobs/lt_test_00004/longterm?threshold_pct=101")
    assert r.status_code == 422
    # decluster_hours out of range
    r = client.get("/api/jobs/lt_test_00004/longterm?decluster_hours=0")
    assert r.status_code == 422
    # return_years bad
    r = client.get("/api/jobs/lt_test_00004/longterm?return_years=abc")
    assert r.status_code == 422
    # too many return_years
    r = client.get("/api/jobs/lt_test_00004/longterm?return_years=1,2,3,4,5,6,7,8,9")
    assert r.status_code == 422
    # bootstrap out of range
    r = client.get("/api/jobs/lt_test_00004/longterm?bootstrap=5000")
    assert r.status_code == 422
    # Non-numeric
    r = client.get("/api/jobs/lt_test_00004/longterm?threshold_pct=abc")
    assert r.status_code == 422


def test_longterm_unverified_and_notes():
    frame = _make_frame(years=3)
    result = longterm.compute_longterm(frame, bootstrap=0)
    assert len(result["unverified"]) > 0
    assert any("COV" in u for u in result["unverified"])
    assert len(result["notes"]) > 0


def test_all_nan_frame():
    """A frame with all NaN values should not crash."""
    index = pd.date_range("2010-01-01", periods=100, freq="6h")
    frame = pd.DataFrame({
        "hm0": np.nan, "te": np.nan, "tp": np.nan, "dir_from": np.nan, "flux": np.nan,
    }, index=index)
    frame.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": 6})
    result = longterm.compute_longterm(frame, bootstrap=0)
    assert result["extremes"]["status"] == "insufficient"
    assert result["indices"]["cov"]["value"] is None


def test_record_summary_full_years():
    frame = _make_frame(years=4)
    result = longterm.compute_longterm(frame, bootstrap=0)
    rec = result["record"]
    # 4 complete years: 2010, 2011, 2012, 2013
    assert len(rec["full_years"]) >= 3
    assert rec["years"] == pytest.approx(4.0, abs=0.02)


def test_report_markdown_not_empty():
    frame = _make_frame(years=4)
    result = longterm.compute_longterm(frame, bootstrap=0)
    md = longterm.report_markdown(result)
    assert "## Long-term statistics" in md
    assert "Monthly climatology" in md
    assert "Variability indices" in md

# --- additional tests ---
import warnings
from unittest.mock import patch

def test_missing_seasons():
    # 3-year frame with months 6, 7, 8 removed
    frame = _make_frame(years=3)
    frame = frame[~frame.index.month.isin([6, 7, 8])]
    result = longterm.compute_longterm(frame, bootstrap=0)
    seasonal = result["climatology"]["seasonal"]
    jja = next(s for s in seasonal if s["season"] == "JJA")
    assert jja["flux_mean_kw_m"] is None
    assert jja["share_of_annual_pct"] is None
    assert "reason" in jja
    # all seasons must have share_of_annual_pct = None because baseline is incomplete
    for s in seasonal:
        assert s["share_of_annual_pct"] is None
    
    assert result["indices"]["svi"]["value"] is None
    assert "not all 4 seasons" in result["indices"]["svi"]["reason"]


def test_exact_mvi_and_rate():
    flux_by_month = {m: 10.0 + m + 0.0004 for m in range(1, 13)}
    flux_by_month[1] += 1.23456789
    frame = _make_frame(years=3, flux_by_month=flux_by_month)
    result = longterm.compute_longterm(frame, bootstrap=0, threshold_pct=50, decluster_hours=1, min_exceedances=1)
    
    # exact_mvi computed from unrounded numpy means
    means = list(flux_by_month.values())
    exact_mvi = (max(means) - min(means)) / np.mean(means)
    assert result["indices"]["mvi"]["value"] == pytest.approx(round(exact_mvi, 3), rel=1e-12)

    idx = frame.index
    exact_years = (idx[-1] - idx[0]).total_seconds() / (365.25 * 86400)
    n_peaks = result["extremes"]["n_peaks"]
    assert result["extremes"]["rate_per_yr"] == pytest.approx(round(n_peaks / exact_years, 3), rel=1e-12)


def test_runtime_warnings_means():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        # all-NaN frame
        index = pd.date_range("2010-01-01", periods=100, freq="6h")
        frame_nan = pd.DataFrame({
            "hm0": np.nan, "te": np.nan, "tp": np.nan, "dir_from": np.nan, "flux": np.nan,
        }, index=index)
        frame_nan.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": 6})
        longterm.compute_longterm(frame_nan, bootstrap=0)

        # frame with one all-NaN month (e.g. Jan)
        frame2 = _make_frame(years=3)
        frame2.loc[frame2.index.month == 1, "flux"] = np.nan
        longterm.compute_longterm(frame2, bootstrap=0)


def test_plugin_validation_before_frame(client, monkeypatch):
    import plugins
    make_job("lt_val_001", "single-levels", build_a)
    
    def mock_frame(*args, **kwargs):
        raise RuntimeError("frame() should not be called before validation")
    
    monkeypatch.setattr(plugins.JobView, "frame", mock_frame)
    # trigger a validation error
    r = client.get("/api/jobs/lt_val_001/longterm?threshold_pct=101")
    assert r.status_code == 422


def test_plugin_cache_rewrite_markdown(client, monkeypatch):
    make_job("lt_md_001", "single-levels", build_a)
    folder = appmodule.DOWNLOADS / "lt_md_001"
    
    # Mock _extremes to ensure 'ok' status and threshold_pct output in markdown
    orig_extremes = longterm._extremes
    def mock_extremes(*args, **kwargs):
        return {
            "status": "ok", "threshold_m": 1.0, "threshold_pct": kwargs.get("threshold_pct", 95.0),
            "n_peaks": 30, "rate_per_yr": 1.0, "xi": 0.1, "sigma": 0.5, "levels": [],
            "method": "mocked", "bootstrap_failures": 0, "bootstrap_count": 0,
            "sensitivity": [], "empirical": [], "curve": []
        }
    monkeypatch.setattr(longterm, "_extremes", mock_extremes)
    
    # Request A
    r1 = client.get("/api/jobs/lt_md_001/longterm?threshold_pct=90.0")
    assert r1.status_code == 200
    report_file = folder / "report_sections" / "40_longterm.md"
    assert "90.0" in report_file.read_text()
    
    # Request B
    r2 = client.get("/api/jobs/lt_md_001/longterm?threshold_pct=95.0")
    assert r2.status_code == 200
    assert "95.0" in report_file.read_text()
    
    # Request A again (cache hit)
    r3 = client.get("/api/jobs/lt_md_001/longterm?threshold_pct=90.0")
    assert r3.status_code == 200
    assert "90.0" in report_file.read_text()


def test_gpd_recovery_known_truth():
    index = pd.date_range("1990-01-01", periods=int(30*1461), freq="6h")
    n = len(index)
    rng = np.random.default_rng(42)
    is_exc = rng.random(n) < 0.05
    hm0 = np.zeros(n)
    hm0[~is_exc] = rng.uniform(0.2, 2.99, size=(~is_exc).sum())
    hm0[is_exc] = 3.0 + genpareto.rvs(c=0.10, scale=0.50, size=is_exc.sum(), random_state=7)
    
    frame = pd.DataFrame({
        "hm0": hm0,
        "te": 9.0,
        "tp": 10.0,
        "dir_from": 200.0,
        "flux": 0.4906 * hm0**2 * 9.0
    }, index=index)
    frame.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": 6})
    
    result = longterm.compute_longterm(frame, threshold_pct=95.0, decluster_hours=1.0, bootstrap=200, seed=1)
    
    ext = result["extremes"]
    assert ext["status"] == "ok"
    assert abs(ext["threshold_m"] - 3.0) < 0.02
    assert abs(ext["xi"] - 0.10) < 0.03
    
    exact_years = (index[-1] - index[0]).total_seconds() / (365.25 * 86400)
    lam_true = 0.05 * n / exact_years
    assert abs(ext["rate_per_yr"] - lam_true) / lam_true < 0.02
    
    levels = {lv["return_period_yr"]: lv for lv in ext["levels"]}
    for T in (1, 10, 50, 100):
        analytic = 3.0 + (0.5 / 0.1) * ((lam_true * T)**0.1 - 1)
        lv = levels[T]
        assert lv["ci_low_m"] <= analytic <= lv["ci_high_m"]
