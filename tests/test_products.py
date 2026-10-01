"""Download-option tests: request shapes per product and how the API passes them on."""

import datetime as dt

import pytest

import app as appmodule
import fetch_era5_waves as fetcher

D = dt.date


# --- fetcher ---------------------------------------------------------------------

def test_single_levels_variable_groups_and_time_step():
    options = fetcher.normalise_options("single-levels", ["temperature", "waves"], None, 3)
    assert options["groups"] == ["waves", "temperature"]  # canonical order
    _, request = fetcher.build_request(2020, 4, [1], [6, 95, 0, 100], options)
    assert request["time"] == ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]
    assert "2m_temperature" in request["variable"]
    assert "10m_u_component_of_wind" not in request["variable"]


def test_wave_spectra_request_matches_reference_repo():
    options = fetcher.normalise_options("wave-spectra")
    dataset, request = fetcher.build_request(2020, 4, list(range(1, 31)), [6, 95, 0, 100], options, "1")
    assert dataset == "reanalysis-era5-complete"
    assert request["param"] == "251.140" and request["stream"] == "wave" and request["domain"] == "g"
    assert request["date"] == "2020-04-01/to/2020-04-30" and request["time"] == "00/06/12/18"
    assert request["direction"].split("/") == [str(i) for i in range(1, 25)]
    assert request["frequency"].split("/") == [str(i) for i in range(1, 31)]
    assert request["grid"] == "0.5/0.5" and request["format"] == "netcdf" and request["type"] == "an"


def test_mars_surface_request_uses_oper_stream_and_codes():
    options = fetcher.normalise_options("mars-surface", None, "167.128,165.128", None)
    dataset, request = fetcher.build_request(2020, 4, [1, 2], [6, 95, 0, 100], options, "5")
    assert dataset == "reanalysis-era5-complete" and request["expver"] == "5"
    assert request["param"] == "167.128/165.128" and request["stream"] == "oper"
    assert request["levtype"] == "sfc" and request["date"] == "2020-04-01/to/2020-04-02"
    assert request["grid"] == "0.25/0.25"


def test_output_names_differ_per_product():
    names = {fetcher.output_name(p, 2020, 4) for p in fetcher.PRODUCTS}
    assert len(names) == 3 and all(name.endswith("2020-04.nc") for name in names)


def test_option_validation():
    with pytest.raises(ValueError, match="Unknown product"):
        fetcher.normalise_options("bogus")
    with pytest.raises(ValueError, match="Unknown variable group"):
        fetcher.normalise_options("single-levels", ["waves", "lava"])
    with pytest.raises(ValueError, match="GRIB codes"):
        fetcher.normalise_options("mars-surface", None, "167.128; echo hi")
    with pytest.raises(ValueError, match="Time step"):
        fetcher.normalise_options("single-levels", None, None, 5)


def test_spectra_have_tighter_limits():
    with pytest.raises(ValueError, match="2 years"):
        fetcher.validate_period(D(2018, 1, 1), D(2020, 6, 1), D(2026, 10, 1), "wave-spectra")
    fetcher.validate_period(D(2018, 1, 1), D(2020, 6, 1), D(2026, 10, 1), "single-levels")


def test_expver_is_final_for_old_months_and_era5t_for_recent():
    today = D(2026, 10, 1)
    assert fetcher.auto_expver(D(2020, 4, 30), today) == "1"
    assert fetcher.auto_expver(D(2026, 9, 1), today) == "5"


def test_dry_run_spectra_prints_mars_request(tmp_path, capsys):
    code = fetcher.main([
        "--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-30",
        "--output", str(tmp_path), "--product", "wave-spectra", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert code == 0 and "reanalysis-era5-complete" in out and '"param": "251.140"' in out


def test_spectra_buffer_limit_enforced_by_cli(tmp_path, capsys):
    code = fetcher.main([
        "--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-30",
        "--output", str(tmp_path), "--product", "wave-spectra", "--buffer", "5", "--dry-run",
    ])
    assert code == 2 and "Buffer must be between 0 and 2" in capsys.readouterr().out


# --- API -------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        test_client.submitted = submitted
        yield test_client


def payload(**overrides):
    end = fetcher.latest_available()
    body = {"latitude": -6.9, "longitude": 107.6, "buffer": 0.5,
            "start": (end - dt.timedelta(days=20)).isoformat(), "end": end.isoformat()}
    body.update(overrides)
    return body


def test_product_options_reach_the_fetcher_command(client):
    job = client.post("/api/jobs", json=payload(
        product="mars-surface", params=["167.128"], time_step=12)).get_json()
    assert job["product"] == "mars-surface" and job["params"] == ["167.128"]
    command = client.submitted[0][1][1]
    assert command[command.index("--product") + 1] == "mars-surface"
    assert command[command.index("--params") + 1] == "167.128"
    assert command[command.index("--time-step") + 1] == "12"


def test_invalid_product_options_are_rejected(client):
    assert client.post("/api/jobs", json=payload(product="nope")).status_code == 400
    assert client.post("/api/jobs", json=payload(groups=["lava"])).status_code == 400
    assert client.post("/api/jobs", json=payload(product="wave-spectra", buffer=5)).status_code == 400
    end = fetcher.latest_available()
    long = payload(product="wave-spectra", start=(end - dt.timedelta(days=900)).isoformat())
    assert client.post("/api/jobs", json=long).status_code == 400
    assert not client.submitted


def test_spectra_dry_run_end_to_end(client):
    job = client.post("/api/jobs", json=payload(product="wave-spectra", dry_run=True)).get_json()
    fn, args = client.submitted[0]
    fn(*args)
    result = client.get(f"/api/jobs/{job['id']}").get_json()
    assert result["status"] == "complete", result["log"]
    assert any("251.140" in line for line in result["log"])


def test_index_renders_product_options(client):
    html = client.get("/").get_data(as_text=True)
    assert 'value="wave-spectra"' in html and 'name="groups"' in html and 'name="params"' in html


def test_expver_choice_reaches_the_fetcher_for_mars_products_only(client):
    spectra = client.post("/api/jobs", json=payload(product="wave-spectra", expver="5")).get_json()
    command = client.submitted[0][1][1]
    assert spectra["expver"] == "5" and command[command.index("--expver") + 1] == "5"
    client.post("/api/jobs", json=payload(product="single-levels", expver="5"))
    assert "--expver" not in client.submitted[1][1][1]
    client.post("/api/jobs", json=payload(product="wave-spectra"))
    assert client.submitted[2][1][1][client.submitted[2][1][1].index("--expver") + 1] == "auto"
    assert client.post("/api/jobs", json=payload(product="wave-spectra", expver="2")).status_code == 400
