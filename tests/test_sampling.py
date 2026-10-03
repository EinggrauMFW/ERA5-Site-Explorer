import numpy as np
import pandas as pd
import pytest
import xarray as xr

import analysis
import wavecalc as wc
from screening import compute_screening
from tests.test_analysis import spectra_dataset


def test_budget_boundaries(tmp_path, monkeypatch):
    path = tmp_path / "spectra.nc"
    ds = spectra_dataset(hours=2)
    ds.to_netcdf(path)
    # 2 records, 3x3 nodes = 18 work items

    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 18)
    res_nodes = analysis.node_summary([path], "wave-spectra", 0.0, 0.0)
    assert res_nodes["note"] is None

    res_screen = compute_screening([path], "wave-spectra", 0.0, 0.0)
    assert res_screen["sampling_note"] is None

    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 17)
    res_nodes = analysis.node_summary([path], "wave-spectra", 0.0, 0.0)
    assert "one record in every 2 (1 of 2 records)" in res_nodes["note"]

    res_screen = compute_screening([path], "wave-spectra", 0.0, 0.0)
    assert "one record in every 2 (1 of 2 records)" in res_screen["sampling_note"]


def test_chunking_exact_equals_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "CHUNK_SIZE", 3)
    path = tmp_path / "spectra_7.nc"
    ds = spectra_dataset(hours=7)

    var, dir_dim, freq_dim = analysis.spectra_variable(ds)
    ds[var].values[3, :, :, 0, 0] = np.nan
    ds[var].values[:, :, :, 0, 1] = np.nan
    ds.to_netcdf(path)

    freqs, dfreq, dtheta, dir_to, _ = wc.spectra_axes()
    with xr.open_dataset(path) as ds_open:
        raw = ds_open[var].transpose("valid_time", dir_dim, freq_dim, "latitude", "longitude").values.astype(float)

        col00 = raw[:, :, :, 0, 0]
        bulk00 = wc.spectral_bulk(wc.decode_log10(col00), freqs, dfreq, dtheta, dir_to, None)

        col10 = raw[:, :, :, 1, 0]
        bulk10 = wc.spectral_bulk(wc.decode_log10(col10), freqs, dfreq, dtheta, dir_to, None)

    res = analysis.node_summary([path], "wave-spectra", 0.0, 0.0)
    assert res["note"] is None

    def find_node(lat, lon):
        return next(n for n in res["nodes"] if abs(n["lat"] - lat) < 1e-3 and abs(n["lon"] - lon) < 1e-3)

    lats = ds["latitude"].values
    lons = ds["longitude"].values

    node00 = find_node(lats[0], lons[0])
    assert node00["valid"]
    assert node00["hm0"] == pytest.approx(round(float(np.nanmean(bulk00["hm0"])), 3))
    assert node00["te"] == pytest.approx(round(float(np.nanmean(bulk00["te"])), 3))
    assert node00["flux"] == pytest.approx(round(float(np.nanmean(bulk00["flux"])), 3))

    node10 = find_node(lats[1], lons[0])
    assert node10["valid"]
    assert node10["hm0"] == pytest.approx(round(float(np.nanmean(bulk10["hm0"])), 3))

    node01 = find_node(lats[0], lons[1])
    assert not node01["valid"]


def test_two_files(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "CHUNK_SIZE", 3)
    path1 = tmp_path / "spectra_part1.nc"
    path2 = tmp_path / "spectra_part2.nc"

    ds1 = spectra_dataset(hours=2)
    ds1["valid_time"] = pd.date_range("2020-01-31 22:00:00", periods=2, freq="1h")
    ds1.to_netcdf(path1)

    ds2 = spectra_dataset(hours=2)
    ds2["valid_time"] = pd.date_range("2020-02-01 00:00:00", periods=2, freq="1h")
    ds2.to_netcdf(path2)

    res = analysis.node_summary([path1, path2], "wave-spectra", 0.0, 0.0)
    assert res["note"] is None

    node = res["nodes"][0]
    assert node["valid"]
