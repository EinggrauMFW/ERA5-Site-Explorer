"""Acceptance tests for docs/work-packages/WP14_longterm_numbers.md.

Three results that looked plausible and were wrong (found by independent review):
the event rate ignored gaps, the seasonal shares added up to 400 %, and partial years counted as complete.
Expected values are derived here from the data, not from the implementation.
"""

import numpy as np
import pandas as pd
import pytest

import longterm
from tests.test_longterm import _make_frame

SECONDS_PER_YEAR = 365.25 * 86400.0
SEASONS = {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11]}


# --- requirement 2: seasonal shares add up to 100 % ---------------------------------------------------

def shares(result):
    return {s["season"]: s["share_of_annual_pct"] for s in result["climatology"]["seasonal"]}


def test_constant_flux_gives_equal_seasonal_shares_of_25_percent():
    frame = _make_frame(years=3, flux_by_month={m: 10.0 for m in range(1, 13)})
    got = shares(longterm.compute_longterm(frame, bootstrap=0))
    assert got == {"DJF": 25.0, "MAM": 25.0, "JJA": 25.0, "SON": 25.0}


def test_seasonal_shares_are_each_season_over_the_sum_of_the_four_and_add_up_to_100():
    frame = _make_frame(years=3)            # flux 5 kW/m in January ... 60 kW/m in December
    got = shares(longterm.compute_longterm(frame, bootstrap=0))
    means = {name: float(frame["flux"][frame.index.month.isin(months)].mean()) for name, months in SEASONS.items()}
    total = sum(means.values())
    for name, mean in means.items():
        assert got[name] == pytest.approx(round(100.0 * mean / total, 3), abs=1e-9)
    assert sum(got.values()) == pytest.approx(100.0, abs=0.005)
    assert max(got.values()) < 100.0        # the old definition printed values near 100 for every season


def test_seasonal_shares_stay_undefined_when_a_season_is_missing():
    frame = _make_frame(years=3)
    frame = frame[~frame.index.month.isin([6, 7, 8])]
    seasonal = longterm.compute_longterm(frame, bootstrap=0)["climatology"]["seasonal"]
    assert all(s["share_of_annual_pct"] is None for s in seasonal)


# --- requirement 3: a complete year is a whole calendar year -------------------------------------------

def full_years(frame):
    return longterm.compute_longterm(frame, bootstrap=0)["record"]["full_years"]


def test_a_record_starting_on_31_january_does_not_count_its_first_year_as_complete():
    frame = _make_frame(years=4)                         # 2010-01-01 .. 2013-12-31, flux differs by month
    frame = frame[frame.index >= "2010-01-31"]           # January 2010 is nearly missing
    result = longterm.compute_longterm(frame, bootstrap=0)
    assert result["record"]["full_years"] == [2011, 2012, 2013]
    data = result["interannual"]["data"]
    assert [d["year"] for d in data] == [2011, 2012, 2013]
    # Whole years have the same monthly flux; only the extra leap day in 2012 moves the annual mean.
    assert max(abs(d["anomaly_pct"]) for d in data) < 0.5


def test_a_record_ending_before_december_does_not_count_its_last_year_as_complete():
    frame = _make_frame(years=4)
    frame = frame[frame.index < "2013-12-01"]
    assert full_years(frame) == [2010, 2011, 2012]


def test_per_year_coverage_is_measured_against_the_whole_calendar_year():
    frame = _make_frame(years=3)
    frame = frame[frame.index >= "2010-07-01"]           # 2010 is observed for its second half only
    per_year = {e["year"]: e["coverage_pct"] for e in longterm.compute_longterm(frame, bootstrap=0)["record"]["per_year"]}
    assert per_year[2010] == pytest.approx(100.0 * 184 / 365, abs=0.1)     # July to December = 184 of 365 days
    assert per_year[2011] == pytest.approx(100.0, abs=0.01)


def test_a_year_with_one_mostly_missing_month_is_not_complete():
    frame = _make_frame(years=4)
    july_2012 = frame.index[(frame.index.year == 2012) & (frame.index.month == 7)]
    frame = frame.drop(july_2012[::2])                   # half of July 2012 gone: the year is still 98 % covered
    assert full_years(frame) == [2010, 2011, 2013]


def test_a_year_with_small_gaps_in_a_month_is_still_complete():
    frame = _make_frame(years=4)
    july_2012 = frame.index[(frame.index.year == 2012) & (frame.index.month == 7)]
    frame = frame.drop(july_2012[::20])                  # 5 % of July 2012 gone, above the 90 % requirement
    assert full_years(frame) == [2010, 2011, 2012, 2013]


# --- requirement 1: the event rate uses the time actually observed ------------------------------------

STEP_HOURS = 6


def noisy_frame(index, seed=7):
    """Independent gamma-distributed Hm0 so that the 90th percentile gives many separated peaks."""
    hm0 = 1.5 + np.random.default_rng(seed).gamma(2.0, 0.4, len(index))
    te = np.full(len(index), 8.0)
    frame = pd.DataFrame({"hm0": hm0, "te": te, "tp": te * 1.1, "dir_from": 180.0, "flux": 0.4906 * hm0**2 * te},
                         index=index)
    frame.attrs.update({"product": "single-levels", "route": "integrated", "time_step_hours": STEP_HOURS})
    return frame


def full_index():
    return pd.date_range("2010-01-01", "2021-12-31 18:00", freq=f"{STEP_HOURS}h")


def extremes(frame, **kwargs):
    result = longterm.compute_longterm(frame, bootstrap=0, threshold_pct=90.0, **kwargs)
    assert result["extremes"]["status"] == "ok", result["extremes"]
    return result


def observed_years(frame):
    n_finite = int(np.isfinite(frame["hm0"].to_numpy()).sum())
    return (n_finite - 1) * STEP_HOURS / (24 * 365.25)


def test_without_gaps_the_rate_is_unchanged_and_there_is_no_gap_warning():
    frame = noisy_frame(full_index())
    result = extremes(frame)
    ext = result["extremes"]
    span = (frame.index[-1] - frame.index[0]).total_seconds() / SECONDS_PER_YEAR
    assert ext["rate_per_yr"] == pytest.approx(ext["n_peaks"] / span, rel=1e-4)
    assert ext["observed_years"] == pytest.approx(span, abs=1e-3)
    assert ext["record_years"] == pytest.approx(span, abs=1e-3)
    assert not any("gaps" in w for w in result["warnings"])


def test_missing_rows_raise_the_rate_to_peaks_per_observed_year():
    full = noisy_frame(full_index())
    frame = full[(full.index.year < 2013) | (full.index.year > 2016)]       # four whole years absent
    result = extremes(frame)
    ext = result["extremes"]
    span = (frame.index[-1] - frame.index[0]).total_seconds() / SECONDS_PER_YEAR
    assert ext["observed_years"] == pytest.approx(observed_years(frame), abs=1e-3)
    assert ext["observed_years"] == pytest.approx(8.0, abs=0.01)
    assert ext["record_years"] == pytest.approx(span, abs=1e-3)
    assert ext["rate_per_yr"] == pytest.approx(ext["n_peaks"] / observed_years(frame), rel=1e-4)
    assert ext["rate_per_yr"] / (ext["n_peaks"] / span) == pytest.approx(span / observed_years(frame), rel=1e-3)
    assert any("gaps" in w for w in result["warnings"])


def test_nan_rows_count_as_gaps_in_the_same_way_as_missing_rows():
    full = noisy_frame(full_index())
    absent = full[(full.index.year < 2013) | (full.index.year > 2016)]
    blanked = full.copy()
    blanked.loc[(blanked.index.year >= 2013) & (blanked.index.year <= 2016), ["hm0", "te", "tp", "flux"]] = np.nan
    ext_nan = extremes(blanked)["extremes"]
    ext_absent = extremes(absent)["extremes"]
    assert ext_nan["observed_years"] == pytest.approx(observed_years(blanked), abs=1e-3)
    assert ext_nan["observed_years"] == pytest.approx(ext_absent["observed_years"], abs=0.01)
    assert ext_nan["rate_per_yr"] == pytest.approx(ext_nan["n_peaks"] / observed_years(blanked), rel=1e-4)


def test_the_extrapolation_warning_compares_with_the_observed_time_not_the_calendar_span():
    full = noisy_frame(full_index())
    frame = full[(full.index.year < 2012) | (full.index.year > 2017)]       # 6 of 12 years observed
    result = extremes(frame, return_years=[20])
    # 20 years is more than 3 x 6 observed years, but less than 3 x 12 calendar years.
    assert any("Return period 20.0 yr exceeds" in w for w in result["warnings"])


def test_a_gap_free_record_gets_no_extrapolation_warning_for_a_return_period_inside_three_times_the_record():
    result = extremes(noisy_frame(full_index()), return_years=[20])
    assert not any("exceeds" in w for w in result["warnings"])
