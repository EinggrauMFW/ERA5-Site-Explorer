"""The plugin interface: canonical frame, JobView storage, loader and the app wiring."""

import sys
import types

import numpy as np
import pandas as pd
import pytest

import app as appmodule
import plugins
from tests.test_analysis import spectra_dataset, wave_dataset
from tests.test_provenance_crosscheck import build_a, build_b, make_job


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: None)
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        yield test_client


def test_canonical_frame_maps_both_routes_to_the_same_columns():
    index = pd.date_range("2022-01-01", periods=4, freq="6h")
    bulk = pd.DataFrame({"swh": [1, 2, 3, 4.0], "mwp": [8, 9, 10, 11.0], "pp1d": [10, 11, 12, 13.0],
                         "mwd": [200, 210, 220, 230.0], "j": [3, 4, 5, 6.0]}, index=index)
    spectra = pd.DataFrame({"hm0": [1, 2, 3, 4.0], "te": [8, 9, 10, 11.0], "tp_parabolic": [10, 11, 12, 13.0],
                            "dm_from": [200, 210, 220, 230.0], "flux": [3, 4, 5, 6.0], "flux_dir_00": [1, 1, 1, 1.0]},
                           index=index)
    a, b = plugins.canonical_frame(bulk, "single-levels"), plugins.canonical_frame(spectra, "wave-spectra")
    assert list(a.columns) == list(plugins.CANONICAL_COLUMNS)
    assert list(b.columns) == list(plugins.CANONICAL_COLUMNS) + ["flux_dir_00"]
    assert np.allclose(a["tp"], b["tp"]) and np.allclose(a["flux"], b["flux"])
    assert a.attrs["time_step_hours"] == 6.0 and a.attrs["route"] == "integrated" and b.attrs["route"] == "spectra"


def test_missing_columns_are_nan_and_flux_is_derived_when_possible():
    index = pd.date_range("2022-01-01", periods=3, freq="h")
    frame = pd.DataFrame({"swh": [2.0, 2.0, 2.0], "mwp": [10.0, 10.0, 10.0]}, index=index)   # no pp1d, mwd, j
    out = plugins.canonical_frame(frame, "single-levels")
    assert out["tp"].isna().all() and out["dir_from"].isna().all()
    assert out["flux"].iloc[0] == pytest.approx(0.4906 * 4 * 10)


def test_canonical_frame_sorts_and_drops_duplicate_times():
    index = pd.to_datetime(["2022-01-02", "2022-01-01", "2022-01-01"])
    frame = pd.DataFrame({"swh": [3.0, 1.0, 9.0], "mwp": [8.0, 8.0, 8.0]}, index=index)
    out = plugins.canonical_frame(frame, "single-levels")
    assert list(out.index) == [pd.Timestamp("2022-01-01"), pd.Timestamp("2022-01-02")] and out["hm0"].iloc[0] == 1.0


def test_job_view_from_the_app_gives_frame_analysis_nodes_and_storage(client):
    make_job("ffffffffffa1", "wave-spectra", build_b)
    with appmodule.app.test_request_context("/"):
        view = appmodule.job_view("ffffffffffa1")
    assert view.product == "wave-spectra" and view.node is None
    frame = view.frame()
    assert list(frame.columns[:5]) == list(plugins.CANONICAL_COLUMNS) and len(frame) == 16
    assert view.analysis()["route"] == "wave-spectra" and view.nodes()["n_ocean"] > 0
    view.save_json("thing.json", {"x": np.float64(1.5), "y": np.array([1, 2])})
    assert view.load_json("thing.json") == {"x": 1.5, "y": [1, 2]} and view.load_json("none.json", 7) == 7
    view.save_report_section("20_device", "## Device\n")
    view.save_report_section("10_other", "## Other\n")
    assert [name for name, _ in view.report_sections()] == ["10_other", "20_device"]
    assert view.provenance() == {"schema": 1}


def test_job_view_honours_the_requested_node(client):
    make_job("ffffffffffa2", "single-levels", build_a)
    with appmodule.app.test_request_context("/?node_lat=0.0&node_lon=96.0"):
        view = appmodule.job_view("ffffffffffa2")
    assert view.node == (0.0, 96.0) and view.analysis()["grid_coordinate"] == {"latitude": 0.0, "longitude": 96.0}


def test_job_view_rejects_unfinished_and_wrong_product(client):
    make_job("ffffffffffa3", "single-levels", build_a)
    with appmodule.app.test_request_context("/"):
        with pytest.raises(Exception) as caught:
            appmodule.job_view("ffffffffffa3", "wave-spectra")
    assert getattr(caught.value, "code", None) == 409


def test_loader_registers_present_plugins_and_survives_a_broken_one(monkeypatch):
    calls = []
    good = types.ModuleType("plugin_device")
    good.register = lambda app, ctx: calls.append("device")
    broken = types.ModuleType("plugin_screening")
    broken.register = lambda app, ctx: 1 / 0
    monkeypatch.setitem(sys.modules, "plugin_device", good)
    monkeypatch.setitem(sys.modules, "plugin_screening", broken)
    monkeypatch.setitem(sys.modules, "plugin_longterm", None)     # an ImportError for another module name
    ctx = plugins.PluginContext(job=lambda *a, **k: None, downloads=appmodule.DOWNLOADS)
    loaded = plugins.load_plugins(appmodule.app, ctx)
    assert "plugin_device" in loaded and "plugin_screening" not in loaded and calls == ["device"]


# Registered at import time: Flask refuses new routes after it has served a request.
@appmodule.app.get("/api/_test_value_error")
def _boom():
    raise ValueError("bad matrix")


def test_value_errors_from_plugin_routes_become_422_json(client):
    response = client.get("/api/_test_value_error")
    assert response.status_code == 422 and response.get_json() == {"error": "bad matrix"}


def test_index_lists_only_plugin_scripts_that_exist(client):
    html = client.get("/").get_data(as_text=True)
    assert "app.js" in html and "window.EraExplorer" not in html     # the API is defined in app.js, not inline
    for name in ("device", "screening", "longterm", "export"):
        path = appmodule.APP_DIR / "static" / "plugins" / f"{name}.js"
        assert (f"plugins/{name}.js" in html) == path.is_file()
