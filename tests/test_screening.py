"""Tests for site screening: per-node statistics, payload shape, caching and the HTTP routes."""
import pytest
import numpy as np
import analysis
import wavecalc as wc
from screening import compute_screening
from tests.test_analysis import wave_dataset, spectra_dataset, bathymetry_file
from tests.test_provenance_crosscheck import make_job
import app as appmodule

def build_a(folder):
    ds = wave_dataset(hours=96)
    scale = np.arange(9, dtype=float).reshape(3, 3) * 0.1 + 1.0
    ds["swh"] = ds["swh"] * scale[None, :, :]
    ds.to_netcdf(folder / "era5_2022-01.nc")

def test_zero_hm0_flux_cov_none(tmp_path):
    path = tmp_path / "era5_2022-01.nc"
    ds = wave_dataset(hours=24)
    # Hm0 is 0 everywhere -> flux is 0
    ds["swh"][:] = 0.0
    ds["mwp"][:] = 1.0
    ds.to_netcdf(path)
    result = compute_screening([path], "single-levels", 0.0, 95.0)
    ocean = [n for n in result["nodes"] if n["valid"]][0]
    assert ocean["flux_cov"] is None

def test_option_a_synthetic(tmp_path):
    path = tmp_path / "era5_2022-01.nc"
    ds = wave_dataset(hours=96)
    scale = np.arange(9, dtype=float).reshape(3, 3) * 0.1 + 1.0
    ds["swh"] = ds["swh"] * scale[None, :, :]
    ds.to_netcdf(path)
    
    result = compute_screening([path], "single-levels", 0.0, 95.0)
    assert result["n_ocean"] == 8
    assert result["n_land"] == 1
    
    # 3x3, cell [1,1] is land
    nodes = result["nodes"]
    land = [n for n in nodes if not n["valid"]][0]
    assert land["valid"] is False
    assert land["flux_mean_kw_m"] is None
    
    ocean = [n for n in nodes if n["valid"]][0]
    assert ocean["flux_mean_kw_m"] > 0
    # verify flux formula
    swh_cell = ds["swh"].isel(latitude=0, longitude=0).values
    mwp_cell = ds["mwp"].isel(latitude=0, longitude=0).values
    flux = wc.deep_water_flux(swh_cell, mwp_cell)
    assert ocean["flux_mean_kw_m"] == pytest.approx(flux.mean(), rel=1e-3)
    
    assert result["route"] == "single-levels"
    assert result["requested_coordinate"] == {"latitude": 0.0, "longitude": 95.0}
    assert "T" in result["record"]["start"]
    assert "T" in result["record"]["end"]

def test_no_mwp(tmp_path):
    path = tmp_path / "era5_2022-01.nc"
    ds = wave_dataset(hours=96).drop_vars(["mwp"])
    ds.to_netcdf(path)
    result = compute_screening([path], "single-levels", 0.0, 95.0)
    assert any("No mean_wave_period" in w for w in result["warnings"])
    nodes = result["nodes"]
    ocean = [n for n in nodes if n["valid"]][0]
    assert ocean["flux_mean_kw_m"] is None

def test_monthly_and_seasonal_means(tmp_path):
    path = tmp_path / "era5_multi.nc"
    ds = wave_dataset(hours=4*30*24)
    import pandas as pd
    time_dim = analysis.time_name_of(ds)
    times = pd.date_range("2021-12-01", periods=ds.sizes[time_dim], freq="h")
    ds[time_dim] = times
    swh = np.ones_like(ds["swh"].values)
    mwp = np.ones_like(ds["mwp"].values)
    
    for i, t in enumerate(times):
        if t.month == 12:
            h = 1.0
        elif t.month == 1:
            h = 2.0
        elif t.month == 2:
            h = 3.0
        elif t.month == 3:
            h = 4.0
        else:
            h = 5.0
        swh[i] = h
        mwp[i] = 1.0 / (wc.FLUX_COEFFICIENT * h**2) * h # trick to make flux=h
        
    ds["swh"][:] = swh
    ds["mwp"][:] = mwp
    ds.to_netcdf(path)
    
    result = compute_screening([path], "single-levels", 0.0, 95.0)
    ocean = [n for n in result["nodes"] if n["valid"]][0]
    
    assert ocean["monthly_flux_kw_m"][11] == pytest.approx(1.0) # Dec
    assert ocean["monthly_flux_kw_m"][0] == pytest.approx(2.0)  # Jan
    assert ocean["monthly_flux_kw_m"][1] == pytest.approx(3.0)  # Feb
    assert ocean["monthly_flux_kw_m"][2] == pytest.approx(4.0)  # Mar
    assert ocean["monthly_flux_kw_m"][3] is None # Apr
    
    assert ocean["season_flux_kw_m"]["DJF"] == pytest.approx((1.0*31 + 2.0*31 + 3.0*28) / (31+31+28))
    assert ocean["season_flux_kw_m"]["MAM"] == pytest.approx(4.0) # Only March has data
    assert ocean["season_flux_kw_m"]["JJA"] is None
    
    assert ocean["seasonality_ratio"] is None
    
    assert "Calendar seasons by month: DJF = December, January, February; MAM = March-May; JJA = June-August; SON = September-November." in result["season_definition"]
    assert any("Seasonal statistics from a partial year are biased: the record does not cover all 12 months." in w for w in result["warnings"])

def test_mean_flux_is_not_flux_of_means(tmp_path):
    path = tmp_path / "era5_2022-01.nc"
    ds = wave_dataset(hours=2)
    swh = np.ones_like(ds["swh"].values)
    swh[0] = 1.0
    swh[1] = 3.0
    mwp = np.ones_like(ds["mwp"].values)
    mwp[:] = 1.0
    ds["swh"][:] = swh
    ds["mwp"][:] = mwp
    ds.to_netcdf(path)
    
    result = compute_screening([path], "single-levels", 0.0, 95.0)
    ocean = [n for n in result["nodes"] if n["valid"]][0]
    
    assert ocean["flux_mean_kw_m"] == pytest.approx(wc.FLUX_COEFFICIENT * 5.0)
    assert ocean["hm0_mean_m"] == pytest.approx(2.0)
    assert ocean["te_mean_s"] == pytest.approx(1.0)

def test_flux_cov_and_p95():
    from screening import _node_stats
    import numpy as np
    hm0 = np.array([1, 1, 1, 1])
    te = np.array([1, 1, 1, 1])
    flux = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    months = np.ones_like(flux)
    
    res = _node_stats(hm0, te, flux, months)
    assert res["flux_p95_kw_m"] == pytest.approx(np.percentile(flux, 95))
    assert res["flux_cov"] == pytest.approx(np.std(flux) / np.mean(flux))

def test_option_b_spectra(tmp_path, monkeypatch):
    import screening
    monkeypatch.setattr(screening, "MAX_RECORDS", 10)
    
    path = tmp_path / "era5_spectra.nc"
    spectra_dataset(hours=24, land=True).to_netcdf(path)
    bathymetry_file(tmp_path / "era5_bathymetry.nc", depth=40.0)
    
    result = compute_screening([path, tmp_path / "era5_bathymetry.nc"], "wave-spectra", 0.0, 95.0)
    assert result["n_land"] == 1
    assert "every 3th record" in result["sampling_note"]
    
    ocean = [n for n in result["nodes"] if n["valid"]][0]
    assert ocean["depth_m"] == 40.0
    assert ocean["flux_mean_kw_m"] > 0

    assert result["route"] == "wave-spectra"
    assert result["requested_coordinate"] == {"latitude": 0.0, "longitude": 95.0}
    assert "T" in result["record"]["start"]
    assert "T" in result["record"]["end"]

from tests.test_nodes import client
@pytest.fixture
def test_client(client):
    return client

def test_http_routes(test_client, monkeypatch):
    def builder(folder):
        ds = wave_dataset(hours=96)
        ds.to_netcdf(folder / "era5_2022-01.nc")
        
    make_job("eeeeeeeeeee1", "single-levels", builder)
    
    # First GET of screening.csv writes screening.json
    csv_res = test_client.get("/api/jobs/eeeeeeeeeee1/screening.csv")
    assert csv_res.status_code == 200
    text = csv_res.get_data(as_text=True)
    assert "lat,lon,depth_m,distance_km,hm0_mean_m" in text
    
    folder = appmodule.DOWNLOADS / "eeeeeeeeeee1"
    assert (folder / "screening.json").exists()
    
    call_count = [0]
    import screening
    orig_compute = screening.compute_screening
    def mock_compute(*args, **kwargs):
        call_count[0] += 1
        return orig_compute(*args, **kwargs)
    monkeypatch.setattr(screening, "compute_screening", mock_compute)
    
    res2 = test_client.get("/api/jobs/eeeeeeeeeee1/screening")
    assert res2.status_code == 200
    assert call_count[0] == 0
    data = res2.get_json()
    assert "nodes" in data
    assert "_version" not in data
    assert "_mtime" not in data
    
    assert test_client.get("/api/jobs/unknown_job/screening").status_code == 404

    def builder_dry(folder):
        pass
    make_job("eeeeeeeeeee2", "single-levels", builder_dry)
    appmodule.jobs["eeeeeeeeeee2"]["dry_run"] = True
    assert test_client.get("/api/jobs/eeeeeeeeeee2/screening").status_code == 409
