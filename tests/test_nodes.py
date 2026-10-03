"""Grid-node picker: per-node summaries, analysis at a chosen node, and the API routes."""

import numpy as np
import pytest

import analysis
import app as appmodule
import wavecalc as wc
from tests.test_analysis import DIMS, bathymetry_file, spectra_dataset, wave_dataset
from tests.test_provenance_crosscheck import build_a, build_b, make_job


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: None)
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        yield test_client


def varied_wave_dataset(hours=48):
    """Each of the 9 cells has its own Hm0 so nodes can be told apart; the middle cell is land."""
    ds = wave_dataset(hours=hours)
    scale = np.arange(9, dtype=float).reshape(3, 3) * 0.1 + 1.0       # 1.0 .. 1.8
    ds["swh"] = ds["swh"] * scale[None, :, :]
    return ds


def test_bulk_node_summary_lists_every_node_and_flags_land(tmp_path):
    path = tmp_path / "era5_2020-04.nc"
    varied_wave_dataset().to_netcdf(path)
    summary = analysis.node_summary([path], "single-levels", 0.2, 95.1)
    assert len(summary["nodes"]) == 9 and summary["n_ocean"] == 8 and summary["n_land"] == 1
    land = analysis.find_node(summary, 0.5, 95.5)
    assert land["valid"] is False and land["hm0"] is None and land["flux"] is None
    ocean = analysis.find_node(summary, 0.0, 95.0)
    assert ocean["valid"] and ocean["te"] == pytest.approx(10.0)
    # J per record first, then averaged
    hm0 = ocean["hm0"]
    assert ocean["flux"] == pytest.approx(wc.FLUX_COEFFICIENT * 10.0 * np.mean(
        (varied_wave_dataset()["swh"].isel(latitude=2, longitude=0).values) ** 2), rel=1e-3)
    assert hm0 > 0
    assert summary["default"] == {"lat": 0.0, "lon": 95.0}               # nearest ocean node to the site
    assert summary["lat_step"] == 0.5 and summary["lon_step"] == 0.5


def test_node_summary_default_skips_a_land_node(tmp_path):
    path = tmp_path / "era5_2020-04.nc"
    varied_wave_dataset().to_netcdf(path)
    summary = analysis.node_summary([path], "single-levels", 0.5, 95.5)   # the site is on the land node
    assert summary["default"] != {"lat": 0.5, "lon": 95.5}
    assert analysis.find_node(summary, summary["default"]["lat"], summary["default"]["lon"])["valid"]
    assert analysis.find_node(summary, 9.0, 9.0) is None


def test_analysis_at_a_chosen_node_uses_that_node(tmp_path):
    path = tmp_path / "era5_2020-04.nc"
    varied_wave_dataset().to_netcdf(path)
    default, _ = analysis.analyse([path], 0.0, 95.0)
    chosen, _ = analysis.analyse([path], 0.0, 95.0, "single-levels", node=(0.0, 96.0))
    assert chosen["node_selected"] is True and default["node_selected"] is False
    assert chosen["grid_coordinate"] == {"latitude": 0.0, "longitude": 96.0}
    assert chosen["series"]["swh"]["mean"] > default["series"]["swh"]["mean"]
    assert chosen["grid_distance_km"] == pytest.approx(
        analysis.haversine_km(0.0, 95.0, 0.0, 96.0), abs=0.1)              # distance is from the site, not the node
    summary = analysis.node_summary([path], "single-levels", 0.0, 95.0)
    assert analysis.find_node(summary, 0.0, 96.0)["hm0"] == pytest.approx(chosen["series"]["swh"]["mean"], abs=2e-3)


def test_spectra_node_summary_matches_node_analysis(tmp_path):
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset(land=True).to_netcdf(path)
    bathymetry_file(tmp_path / "era5_bathymetry.nc", depth=40.0)
    files = [path, tmp_path / "era5_bathymetry.nc"]
    summary = analysis.node_summary(files, "wave-spectra", 0.0, 95.0)
    assert summary["kind"] == "wave" and summary["n_land"] == 1
    node = analysis.find_node(summary, 0.0, 96.0)
    assert node["depth"] == 40.0 and node["hm0"] == pytest.approx(2.0, rel=0.03)
    payload, _ = analysis.analyse(files, 0.0, 95.0, "wave-spectra", node=(0.0, 96.0))
    assert payload["series"]["flux"]["mean"] == pytest.approx(node["flux"], abs=2e-3)   # finite depth in both


def test_spectra_summary_subsamples_long_records(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "MAX_NODE_RECORDS", 1000)
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset(hours=700).to_netcdf(path)
    summary = analysis.node_summary([path], "wave-spectra", 0.0, 95.0)
    # 700 * 9 = 6300 work items. 6300 // 1000 = 6, so stride = 7.
    assert "one record in every 7" in summary["note"] and summary["n_ocean"] == 9


def test_surface_only_download_gives_generic_node_values(tmp_path):
    path = tmp_path / "surface.nc"
    ds = wave_dataset().drop_vars(["swh", "pp1d", "mwp", "mwd"])
    ds["t2m"] = (DIMS, np.full((48, 3, 3), 300.0))
    ds.to_netcdf(path)
    summary = analysis.node_summary([path], "mars-surface", 0.0, 95.0)
    assert summary["kind"] == "generic" and summary["value_label"] == "2 m temperature (°C)"
    assert summary["nodes"][0]["value"] == pytest.approx(26.85)


# --- API -----------------------------------------------------------------------

def test_nodes_route_and_node_analysis(client):
    make_job("eeeeeeeeeee1", "single-levels", lambda folder: varied_wave_dataset(96).to_netcdf(folder / "era5_2022-01.nc"))
    nodes = client.get("/api/jobs/eeeeeeeeeee1/nodes").get_json()
    assert nodes["n_ocean"] == 8 and nodes["default"] == {"lat": 0.0, "lon": 95.0}
    default = client.get("/api/jobs/eeeeeeeeeee1/analysis").get_json()
    chosen = client.get("/api/jobs/eeeeeeeeeee1/analysis?node_lat=1.0&node_lon=96.0").get_json()
    assert chosen["node_selected"] and chosen["grid_coordinate"] == {"latitude": 1.0, "longitude": 96.0}
    assert chosen["series"]["swh"]["mean"] != default["series"]["swh"]["mean"]
    folder = appmodule.DOWNLOADS / "eeeeeeeeeee1"
    assert (folder / "analysis_1.000_96.000.json").is_file() and (folder / "timeseries_1.000_96.000.csv").is_file()
    csv = client.get("/api/jobs/eeeeeeeeeee1/timeseries.csv?node_lat=1.0&node_lon=96.0")
    assert csv.status_code == 200 and "1.000_96.000" in csv.headers["Content-Disposition"]


def test_the_default_node_reuses_the_default_cache(client):
    make_job("eeeeeeeeeee2", "single-levels", lambda folder: varied_wave_dataset(96).to_netcdf(folder / "era5_2022-01.nc"))
    result = client.get("/api/jobs/eeeeeeeeeee2/analysis?node_lat=0.0&node_lon=95.0").get_json()
    assert result["node_selected"] is False
    assert not list((appmodule.DOWNLOADS / "eeeeeeeeeee2").glob("analysis_*.json"))


def test_land_and_unknown_nodes_are_rejected(client):
    make_job("eeeeeeeeeee3", "single-levels", lambda folder: varied_wave_dataset(96).to_netcdf(folder / "era5_2022-01.nc"))
    land = client.get("/api/jobs/eeeeeeeeeee3/analysis?node_lat=0.5&node_lon=95.5")
    assert land.status_code == 422 and "no data" in land.get_json()["error"]
    unknown = client.get("/api/jobs/eeeeeeeeeee3/analysis?node_lat=45&node_lon=10")
    assert unknown.status_code == 422 and "not a grid node" in unknown.get_json()["error"]
    assert client.get("/api/jobs/eeeeeeeeeee3/analysis?node_lat=abc&node_lon=1").status_code == 422


def test_sector_flux_works_at_a_chosen_node(client):
    make_job("eeeeeeeeeee4", "wave-spectra", build_b)
    result = client.get("/api/jobs/eeeeeeeeeee4/analysis?node_lat=0.0&node_lon=96.0&heading=232.5&halfwidth=45").get_json()
    assert result["node_selected"] and any("Sector flux" in s["title"] for s in result["sections"])


def test_crosscheck_at_a_chosen_node(client):
    make_job("eeeeeeeeeea5", "single-levels", build_a)
    make_job("eeeeeeeeeeb5", "wave-spectra", build_b)
    result = client.get("/api/crosscheck?a=eeeeeeeeeea5&b=eeeeeeeeeeb5&node_lat=0.0&node_lon=96.0")
    assert result.status_code == 200, result.get_json()
    data = result.get_json()
    assert data["node"] == {"latitude": 0.0, "longitude": 96.0} and not any("different grid cells" in w for w in data["warnings"])
    missing = client.get("/api/crosscheck?a=eeeeeeeeeea5&b=eeeeeeeeeeb5&node_lat=0.5&node_lon=95.5")   # land in A
    assert missing.status_code == 422


def test_sections_are_grouped_into_analysis_tabs(tmp_path):
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset().to_netcdf(path)
    payload, _ = analysis.analyse([path], 0.0, 95.0, "wave-spectra")
    groups = {s["title"]: s["group"] for s in payload["sections"]}
    assert groups["Record and sampling"] == "overview" and groups["Monthly climatology"] == "overview"
    assert groups["Scatter diagram Hm0 vs Te (primary)"] == "distributions"
    assert groups["Cumulative energy flux vs period"] == "distributions"
    assert groups["Direction: energy mean vs flux"] == "direction"
    assert groups["Spectral shape diagnostics"] == "quality"
    assert {s["group"] for s in payload["sections"]} <= {"overview", "distributions", "direction", "quality"}
    assert analysis.section_group("Sector flux: heading 200") == "direction"
    assert analysis.section_group("Anything else") == "overview"
