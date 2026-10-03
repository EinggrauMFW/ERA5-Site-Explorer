"""Acceptance tests for docs/work-packages/WP16_numerics_minor.md.

Five smaller numerical problems from the review: per-file spectra sampling, thin sensitivity fits, no warning for
sub-hourly sampling, NaN directions counted as outside a sector, and return periods shorter than the mean time
between events. Expected values are derived here from the data.
"""

import numpy as np
import pandas as pd
import pytest

import analysis
import longterm
import screening
import wavecalc as wc
from tests.test_analysis import spectra_dataset
from tests.test_longterm_numbers import full_index, noisy_frame

NODE = (0.0, 95.0)


# --- requirement 1: the spectra sampling stride is global and the note is exact ------------------------

@pytest.fixture
def three_spectra_files(tmp_path):
    """Three consecutive files of 8 six-hourly records; Hm0 is 2, 4 and 6 m in the files (24 unique records)."""
    paths = []
    for i, hm0 in enumerate((2.0, 4.0, 6.0)):
        times = pd.date_range("2020-04-01", periods=8, freq="6h") + pd.Timedelta(hours=48 * i)
        path = tmp_path / f"era5-spectra_{i}.nc"
        spectra_dataset(hours=8, hm0=hm0).assign_coords(valid_time=times).to_netcdf(path)
        paths.append(path)
    return paths


def test_stride_is_global_across_files_and_the_note_counts_what_was_used(three_spectra_files, monkeypatch):
    # 24 records x 9 nodes = 216 work items; a budget of 72 gives a stride of 3.
    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 72)
    summary = analysis.node_summary(three_spectra_files, "wave-spectra", *NODE)
    assert summary["note"] == "Spectra summary uses one record in every 3 (8 of 24 records)."
    # Global records 0, 3, 6 | 9, 12, 15 | 18, 21 are used: 3 from the 2 m file, 3 from the 4 m file, 2 from the 6 m file.
    expected = (3 * 2.0 + 3 * 4.0 + 2 * 6.0) / 8                      # 3.75; restarting in each file would give 4.0
    assert analysis.find_node(summary, *NODE)["hm0"] == pytest.approx(expected, rel=0.03)


def test_screening_uses_exactly_the_number_of_records_the_note_reports(three_spectra_files, monkeypatch):
    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 72)
    result = screening.compute_screening(three_spectra_files, "wave-spectra", *NODE)
    node = next(n for n in result["nodes"] if (n["lat"], n["lon"]) == NODE)
    assert result["sampling_note"] == "Spectra summary uses one record in every 3 (8 of 24 records)."
    assert node["n_records"] == 8                                      # restarting the stride in every file used 9


def test_a_single_file_is_sampled_as_before(tmp_path, monkeypatch):
    path = tmp_path / "era5-spectra_a.nc"
    spectra_dataset(hours=8, hm0=2.0).to_netcdf(path)
    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 24)               # 8 x 9 = 72 work items -> stride 3
    summary = analysis.node_summary([path], "wave-spectra", *NODE)
    assert summary["note"] == "Spectra summary uses one record in every 3 (3 of 8 records)."
    assert analysis.find_node(summary, *NODE)["hm0"] == pytest.approx(2.0, rel=0.03)


# --- requirement 2: the sensitivity table needs enough peaks -------------------------------------------

def test_sensitivity_rows_below_the_minimum_peak_count_are_not_fitted():
    frame = noisy_frame(full_index())
    counts = {s["percentile"]: s["n_peaks"]
              for s in longterm.compute_longterm(frame, bootstrap=0, threshold_pct=95.0,
                                                 min_exceedances=1)["extremes"]["sensitivity"]}
    minimum = counts[95.0]                                             # the main fit just has enough peaks
    assert counts[97.5] < minimum <= counts[90.0]                      # the data really separate the rows
    result = longterm.compute_longterm(frame, bootstrap=0, threshold_pct=95.0, min_exceedances=minimum)
    assert result["extremes"]["status"] == "ok"
    for row in result["extremes"]["sensitivity"]:
        if row["n_peaks"] < minimum:
            assert row["xi"] is None and row["sigma"] is None and row["level_m"] is None
            assert row["threshold_m"] is not None and row["percentile"] == 97.5
        else:
            assert row["level_m"] is not None and row["xi"] is not None


# --- requirement 3: a warning when Hm0 is sampled less often than hourly --------------------------------

def sampling_warnings(step_hours):
    index = pd.date_range("2010-01-01", "2021-12-31 23:00", freq=f"{step_hours}h")
    # 99.9th percentile: the exceedances of independent noise stay apart whatever the step, so each step has peaks.
    result = longterm.compute_longterm(noisy_frame(index), bootstrap=0, threshold_pct=99.9, min_exceedances=5)
    assert result["extremes"]["status"] == "ok", result["extremes"]
    return [w for w in result["warnings"] if "sampled every" in w]


@pytest.mark.parametrize("step", [3, 6])
def test_sub_hourly_sampling_is_reported(step):
    found = sampling_warnings(step)
    assert len(found) == 1
    assert found[0].startswith(f"Hm0 is sampled every {step} h: storm peaks between samples are missed")
    assert "synthetic data only" in found[0]


def test_hourly_data_gets_no_sampling_warning():
    assert sampling_warnings(1) == []


# --- requirement 4: hours without a direction are not outside the sector --------------------------------

def sector_frame(directions):
    n_bins = len(wc.spectra_axes()[4])
    columns = {f"flux_dir_{d:02d}": np.ones(len(directions)) for d in range(n_bins)}
    columns["dm_from"] = np.array(directions, dtype=float)
    columns["flux"] = np.ones(len(directions))
    return pd.DataFrame(columns, index=pd.date_range("2020-04-01", periods=len(directions), freq="6h"))


def hours_inside(directions):
    section = analysis.sector_section(sector_frame(directions), 180.0, 30.0)
    return next(r for r in section["rows"] if r["label"] == "Hours with mean direction inside the sector")["value"]


def test_hours_without_a_direction_are_left_out_of_the_percentage():
    assert hours_inside([180.0, 180.0, np.nan, np.nan]) == "100.0 %"       # 2 of 2 hours with a direction
    assert hours_inside([180.0, 0.0, np.nan, np.nan]) == "50.0 %"          # 1 of 2, not 1 of 4


def test_all_directions_missing_gives_no_percentage():
    assert hours_inside([np.nan, np.nan, np.nan]) == "—"


def test_complete_directions_are_unchanged():
    assert hours_inside([180.0, 0.0, 170.0, 190.0]) == "75.0 %"


# --- requirement 5: return periods shorter than the mean time between events ----------------------------

def sparse_events():
    """A 99.9th-percentile threshold on 12 years leaves about 1.5 events a year."""
    frame = noisy_frame(full_index())
    return longterm.compute_longterm(frame, bootstrap=10, threshold_pct=99.9, min_exceedances=5,
                                     return_years=[0.5, 1, 10, 100])


def test_levels_below_the_mean_time_between_events_are_not_reported():
    result = sparse_events()
    ext = result["extremes"]
    assert ext["status"] == "ok" and 1.0 < ext["rate_per_yr"] < 2.0            # 0.5 yr is short, 1 yr is not
    short = [lv for lv in ext["levels"] if ext["rate_per_yr"] * lv["return_period_yr"] < 1]
    long = [lv for lv in ext["levels"] if ext["rate_per_yr"] * lv["return_period_yr"] >= 1]
    assert [lv["return_period_yr"] for lv in short] == [0.5] and len(long) == 3
    for lv in short:
        assert lv["level_m"] is None and lv["ci_low_m"] is None and lv["ci_high_m"] is None
        assert lv["note"] == f"shorter than the mean time between events (1 / rate = {1 / ext['rate_per_yr']:.2f} yr)"
    for lv in long:
        assert "note" not in lv
        assert lv["level_m"] >= ext["threshold_m"] and lv["ci_low_m"] is not None


def test_the_report_prints_n_a_for_a_level_that_is_not_reported():
    md = longterm.report_markdown(sparse_events())
    assert "n/a" in md
    assert "None" not in md
