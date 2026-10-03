"""Acceptance tests for docs/work-packages/WP15_overlapping_files.md.

When two files cover the same hours the main analysis counts each timestamp once (first file wins). The node table
and the screening map used to count the overlap twice. Expected values are computed here straight from the
datasets with the first-file-wins rule.
"""

import numpy as np
import pandas as pd
import pytest

import analysis
import screening
import wavecalc as wc
from tests.test_analysis import spectra_dataset, wave_dataset

NODE = (0.0, 95.0)              # latitude index 2, longitude index 0 of the 3x3 test grid


# --- Option A (single levels) --------------------------------------------------------------------------

def shifted(dataset, start):
    return dataset.assign_coords(valid_time=pd.date_range(start, periods=dataset.sizes["valid_time"], freq="h"))


@pytest.fixture
def overlapping_a(tmp_path):
    """File a: 1-2 April, Te 10 s. File b: 2-3 April, Te 12 s and 1 m higher. They share the 24 hours of 2 April."""
    a = wave_dataset(48)
    b = shifted(wave_dataset(48, te=12.0), "2020-04-02")
    b["swh"] = b["swh"] + 1.0
    pa, pb = tmp_path / "a.nc", tmp_path / "b.nc"
    a.to_netcdf(pa)
    b.to_netcdf(pb)
    return a, b, pa, pb


def node_series(dataset, name):
    return dataset[name].isel(latitude=2, longitude=0).values.astype(float)


def expected_first_wins(first, second):
    """Records of ``first`` plus those of ``second`` whose timestamp ``first`` does not have."""
    new = ~np.isin(second["valid_time"].values, first["valid_time"].values)
    swh = np.concatenate([node_series(first, "swh"), node_series(second, "swh")[new]])
    mwp = np.concatenate([node_series(first, "mwp"), node_series(second, "mwp")[new]])
    return swh, mwp


@pytest.mark.parametrize("order", ["a_first", "b_first"])
def test_node_summary_counts_an_overlapping_hour_once_first_file_wins(overlapping_a, order):
    a, b, pa, pb = overlapping_a
    files, (first, second) = ([pa, pb], (a, b)) if order == "a_first" else ([pb, pa], (b, a))
    swh, mwp = expected_first_wins(first, second)
    assert len(swh) == 72                                    # 48 + 24 new hours, not 96
    node = analysis.find_node(analysis.node_summary(files, "single-levels", *NODE), *NODE)
    assert node["hm0"] == pytest.approx(swh.mean(), abs=2e-3)
    assert node["te"] == pytest.approx(mwp.mean(), abs=2e-3)
    assert node["flux"] == pytest.approx(wc.deep_water_flux(swh, mwp).mean(), rel=1e-3)


def test_node_summary_agrees_with_the_main_analysis_for_overlapping_files(overlapping_a):
    _, _, pa, pb = overlapping_a
    payload, _ = analysis.analyse([pa, pb], *NODE)
    node = analysis.find_node(analysis.node_summary([pa, pb], "single-levels", *NODE), *NODE)
    assert node["hm0"] == pytest.approx(payload["series"]["swh"]["mean"], abs=2e-3)


@pytest.mark.parametrize("order", ["a_first", "b_first"])
def test_bulk_screening_counts_an_overlapping_hour_once_first_file_wins(overlapping_a, order):
    a, b, pa, pb = overlapping_a
    files, (first, second) = ([pa, pb], (a, b)) if order == "a_first" else ([pb, pa], (b, a))
    swh, mwp = expected_first_wins(first, second)
    flux = wc.deep_water_flux(swh, mwp)
    result = screening.compute_screening(files, "single-levels", *NODE)
    node = next(n for n in result["nodes"] if (n["lat"], n["lon"]) == NODE)
    assert result["record"]["records"] == 72
    assert node["n_records"] == 72
    assert node["hm0_mean_m"] == pytest.approx(swh.mean(), rel=1e-6)
    assert node["te_mean_s"] == pytest.approx(mwp.mean(), rel=1e-6)
    assert node["flux_mean_kw_m"] == pytest.approx(flux.mean(), rel=1e-6)
    assert node["monthly_flux_kw_m"][3] == pytest.approx(flux.mean(), rel=1e-6)          # all of it is April
    assert node["season_flux_kw_m"]["MAM"] == pytest.approx(flux.mean(), rel=1e-6)


def test_a_file_that_does_not_supply_the_route_does_not_hide_later_records(tmp_path):
    """A wind-only file with the same hours must not stop the wave file's records from being used."""
    wind = shifted(wave_dataset(48), "2020-04-01").drop_vars(["swh", "pp1d", "mwp", "mwd"])
    wind["u10"] = (wind["valid_time"].dims + ("latitude", "longitude"), np.full((48, 3, 3), 3.0))
    pw, pa = tmp_path / "a_wind.nc", tmp_path / "b_wave.nc"          # the wind file sorts first
    wind.to_netcdf(pw)
    wave_dataset(48).to_netcdf(pa)
    node = analysis.find_node(analysis.node_summary([pw, pa], "single-levels", *NODE), *NODE)
    assert node["hm0"] == pytest.approx(node_series(wave_dataset(48), "swh").mean(), abs=2e-3)
    result = screening.compute_screening([pw, pa], "single-levels", *NODE)
    assert next(n for n in result["nodes"] if (n["lat"], n["lon"]) == NODE)["n_records"] == 48


def test_files_that_do_not_overlap_give_the_same_result_as_before(tmp_path):
    a = wave_dataset(48)
    b = shifted(wave_dataset(48, te=12.0), "2020-04-03")
    b["swh"] = b["swh"] + 1.0
    pa, pb = tmp_path / "a.nc", tmp_path / "b.nc"
    a.to_netcdf(pa)
    b.to_netcdf(pb)
    swh = np.concatenate([node_series(a, "swh"), node_series(b, "swh")])
    node = analysis.find_node(analysis.node_summary([pa, pb], "single-levels", *NODE), *NODE)
    assert node["hm0"] == pytest.approx(swh.mean(), abs=2e-3)
    result = screening.compute_screening([pa, pb], "single-levels", *NODE)
    assert next(n for n in result["nodes"] if (n["lat"], n["lon"]) == NODE)["n_records"] == 96


# --- Option B (2D spectra) -----------------------------------------------------------------------------

@pytest.fixture
def overlapping_b(tmp_path):
    """File a: 8 six-hourly records of Hm0 2 m from 1 April. File b: 8 records of Hm0 3 m from 2 April.
    The last 4 records of a share their timestamps with the first 4 of b: 12 unique records, 8 from a and 4 from b."""
    a = spectra_dataset(hours=8, hm0=2.0)
    b = spectra_dataset(hours=8, hm0=3.0).assign_coords(valid_time=pd.date_range("2020-04-02", periods=8, freq="6h"))
    pa, pb = tmp_path / "era5-spectra_a.nc", tmp_path / "era5-spectra_b.nc"
    a.to_netcdf(pa)
    b.to_netcdf(pb)
    return pa, pb


EXPECTED_B_HM0 = (8 * 2.0 + 4 * 3.0) / 12        # 2.333; counting the overlap twice would give 2.5


def test_spectra_node_summary_counts_an_overlapping_record_once(overlapping_b):
    pa, pb = overlapping_b
    node = analysis.find_node(analysis.node_summary([pa, pb], "wave-spectra", *NODE), *NODE)
    assert node["hm0"] == pytest.approx(EXPECTED_B_HM0, rel=0.03)         # the log10 round trip is good to about 3 %


def test_spectra_node_summary_agrees_with_the_main_analysis_for_overlapping_files(overlapping_b):
    pa, pb = overlapping_b
    payload, _ = analysis.analyse([pa, pb], *NODE, "wave-spectra")
    assert payload["points"] == 12
    node = analysis.find_node(analysis.node_summary([pa, pb], "wave-spectra", *NODE), *NODE)
    assert node["flux"] == pytest.approx(payload["series"]["flux"]["mean"], abs=2e-3)


def test_spectra_screening_counts_an_overlapping_record_once(overlapping_b):
    pa, pb = overlapping_b
    result = screening.compute_screening([pa, pb], "wave-spectra", *NODE)
    node = next(n for n in result["nodes"] if (n["lat"], n["lon"]) == NODE)
    assert result["record"]["records"] == 12
    assert node["n_records"] == 12
    assert node["hm0_mean_m"] == pytest.approx(EXPECTED_B_HM0, rel=0.03)


def test_the_sampling_stride_is_decided_by_unique_records(overlapping_b, monkeypatch):
    pa, pb = overlapping_b
    # 12 unique records x 9 nodes = 108 work items fit a budget of 120; counting the overlap (16 records) would not.
    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 120)
    summary = analysis.node_summary([pa, pb], "wave-spectra", *NODE)
    assert summary["note"] is None
    assert analysis.find_node(summary, *NODE)["hm0"] == pytest.approx(EXPECTED_B_HM0, rel=0.03)


# --- generic (surface-field) downloads in several files ------------------------------------------------

def test_a_surface_download_split_over_several_files_is_summarised_over_all_of_them(tmp_path):
    """Added by the orchestrator: the node table used to read only the first non-wave file."""
    paths = []
    for name, start, kelvin in (("a.nc", "2020-04-01", 300.0), ("b.nc", "2020-04-03", 310.0)):
        ds = shifted(wave_dataset(48), start).drop_vars(["swh", "pp1d", "mwp", "mwd"])
        ds["t2m"] = (("valid_time", "latitude", "longitude"), np.full((48, 3, 3), kelvin))
        paths.append(tmp_path / name)
        ds.to_netcdf(paths[-1])
    summary = analysis.node_summary(paths, "mars-surface", *NODE)
    assert summary["kind"] == "generic"
    assert analysis.find_node(summary, *NODE)["value"] == pytest.approx((300.0 + 310.0) / 2 - 273.15, abs=2e-3)
