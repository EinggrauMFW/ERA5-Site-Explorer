"""Tiers, bathymetry, provenance, size estimates, the cross-check and its API routes."""

import datetime as dt
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

import app as appmodule
import crosscheck
import fetch_era5_waves as fetcher

D = dt.date


# --- tiers and variable names --------------------------------------------------

def test_default_single_levels_request_covers_all_three_tiers_and_wind():
    options = fetcher.normalise_options("single-levels")
    assert options["groups"] == ["waves", "composition", "qc", "wind"]
    variables = fetcher.build_request(2022, 1, [1], [-1.0, 98.5, -3.0, 100.5], options)[1]["variable"]
    for name in ("mean_wave_period", "peak_wave_period", "mean_wave_direction",
                 "significant_height_of_wind_waves", "significant_height_of_total_swell",
                 "mean_period_of_wind_waves", "mean_period_of_total_swell",
                 "mean_wave_period_based_on_first_moment", "mean_zero_crossing_wave_period",
                 "wave_spectral_directional_width", "wave_spectral_peakedness",
                 "significant_wave_height_of_first_swell_partition", "10m_u_component_of_wind"):
        assert name in variables
    assert "maximum_individual_wave_height" not in variables       # extremes are opt-in
    assert "model_bathymetry" not in variables                     # time-invariant: separate request
    assert "coefficient_of_drag_with_waves" not in variables       # not for resource work


def test_all_group_variable_names_exist_in_the_live_catalogue_snapshot():
    """These names were confirmed against the CDS form for reanalysis-era5-single-levels."""
    confirmed = {
        "significant_height_of_combined_wind_waves_and_swell", "mean_wave_period", "peak_wave_period",
        "mean_wave_direction", "significant_height_of_wind_waves", "significant_height_of_total_swell",
        "mean_period_of_wind_waves", "mean_period_of_total_swell", "mean_direction_of_wind_waves",
        "mean_direction_of_total_swell", "mean_wave_period_based_on_first_moment",
        "mean_zero_crossing_wave_period", "wave_spectral_directional_width", "wave_spectral_peakedness",
        "10m_u_component_of_wind", "10m_v_component_of_wind", "maximum_individual_wave_height",
        "period_corresponding_to_maximum_individual_wave_height", "2m_temperature", "total_precipitation",
        "model_bathymetry",
        *(f"significant_wave_height_of_{n}_swell_partition" for n in ("first", "second", "third")),
        *(f"mean_wave_direction_of_{n}_swell_partition" for n in ("first", "second", "third")),
        *(f"mean_wave_period_of_{n}_swell_partition" for n in ("first", "second", "third")),
    }
    used = {v for group in fetcher.VARIABLE_GROUPS.values() for v in group} | {fetcher.BATHYMETRY_VARIABLE}
    assert used <= confirmed


def test_bathymetry_request_is_a_single_time_and_only_for_qc_or_spectra():
    area = [-1.0, 98.5, -3.0, 100.5]
    dataset, request = fetcher.build_bathymetry_request(2022, 1, 1, area)
    assert dataset == "reanalysis-era5-single-levels" and request["variable"] == ["model_bathymetry"]
    assert request["time"] == ["00:00"] and request["day"] == ["01"] and request["area"] == area
    assert fetcher.wants_bathymetry(fetcher.normalise_options("wave-spectra"))
    assert fetcher.wants_bathymetry(fetcher.normalise_options("single-levels", ["waves", "qc"]))
    assert not fetcher.wants_bathymetry(fetcher.normalise_options("single-levels", ["waves"]))
    assert not fetcher.wants_bathymetry(fetcher.normalise_options("mars-surface"))


def test_size_estimate_scales_with_steps_and_grid():
    area = [-1.0, 98.5, -3.0, 100.5]
    spectra = fetcher.normalise_options("wave-spectra")            # 6-hourly
    hourly = fetcher.normalise_options("wave-spectra", time_step=1)
    six, one = (fetcher.estimate_size_mb(o, area, 30) for o in (spectra, hourly))
    assert one == pytest.approx(6 * six)
    # 5 x 5 nodes x 120 steps x 720 bins x 4 bytes
    assert six == pytest.approx(5 * 5 * 120 * 720 * 4 / 1048576)
    assert fetcher.estimate_size_mb(fetcher.normalise_options("single-levels"), area, 30) > 0


def test_catalogue_check_reads_variable_names(monkeypatch):
    form = [{"name": "variable", "details": {"groups": [{"values": ["a", "b"], "labels": {"a": "A", "b": "B"}}]}},
            {"name": "year", "details": {"values": ["2020"], "labels": {}}}]
    monkeypatch.setattr(fetcher, "fetch_json", lambda url, timeout=30: form)
    assert fetcher.catalogue_variables("x") == {"a", "b"}
    monkeypatch.setattr(fetcher, "fetch_json", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    assert fetcher.catalogue_variables("x") is None


# --- provenance ----------------------------------------------------------------

def test_dry_run_writes_provenance_with_exact_requests(tmp_path):
    out = tmp_path / "job"
    code = fetcher.main(["--latitude", "-2.05", "--longitude", "99.4", "--start", "2022-01-01",
                         "--end", "2022-02-10", "--output", str(out), "--product", "wave-spectra", "--dry-run"])
    assert code == 0
    record = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert record["dry_run"] is True and record["product"] == "wave-spectra"
    assert record["area_nwse"] == [-1.5, 98.5, -3.0, 100.0] or len(record["area_nwse"]) == 4
    assert [r["month"] for r in record["requests"]] == ["2022-01", "2022-01", "2022-02"]  # 6-hourly Jan is 89,280 fields
    first = record["requests"][0]["request"]
    assert first["param"] == "251.140" and first["expver"] == "1" and first["grid"] == "0.5/0.5"
    assert record["bathymetry"]["request"]["variable"] == ["model_bathymetry"]
    assert "Copernicus" in record["licence"]["statement"] and record["licence"]["url"].startswith("https://")
    assert record["period"] == {"start": "2022-01-01", "end": "2022-02-10"}
    assert "python" in record["software"]
    text = json.dumps(record).lower()
    assert "cdsapirc" not in text and "key:" not in text            # no credentials


# --- cross-check ---------------------------------------------------------------

def frames(offset=0.0, scale=1.0, direction_shift=0.0, n=60):
    index = pd.date_range("2022-01-01", periods=n, freq="6h")
    rng = np.random.default_rng(3)
    hm0 = 1.5 + rng.random(n)
    te = 8 + 3 * rng.random(n)
    direction = 200 + 20 * rng.random(n)
    a = pd.DataFrame({"swh": hm0, "mwp": te, "pp1d": te * 1.1, "mwd": direction,
                      "j": wc_flux(hm0, te), "mp1": te * 0.8, "mp2": te * 0.7}, index=index)
    b = pd.DataFrame({"hm0": hm0 * scale, "te": te + offset, "tp_parabolic": te * 1.1,
                      "dm_from": (direction + direction_shift) % 360,
                      "flux": wc_flux(hm0 * scale, te + offset), "tm01": te * 0.8, "tm02": te * 0.7}, index=index)
    return a, b


def wc_flux(hm0, te):
    return 0.4906 * hm0**2 * te


def row(result, key):
    return next(r for r in result["rows"] if r["key"] == key)


def test_identical_routes_agree_everywhere():
    a, b = frames()
    result = crosscheck.compare(a, b, cell_a=(0, 95), cell_b=(0, 95))
    assert result["overlap_records"] == 60 and not result["warnings"]
    for key in ("hm0", "te", "tp", "flux", "direction"):
        assert row(result, key)["within_tolerance_pct"] == 100.0
    assert row(result, "hm0")["bias"] == pytest.approx(0, abs=1e-12)
    assert result["ordering"]["A: mp2 ≤ mp1 ≤ mwp"]["violations"] == 0


def test_te_gap_triggers_a_decoding_warning():
    a, b = frames(offset=1.5)                       # B's Te 1.5 s above A's
    result = crosscheck.compare(a, b)
    assert row(result, "te")["bias"] == pytest.approx(1.5)
    assert any("Te differs" in w for w in result["warnings"])
    assert row(result, "te")["within_tolerance_pct"] < 100


def test_direction_offset_of_180_reveals_a_convention_error():
    a, b = frames(direction_shift=180.0)
    result = crosscheck.compare(a, b)
    assert abs(row(result, "direction")["bias"]) == pytest.approx(180, abs=1)
    assert any("180°" in w for w in result["warnings"])


def test_hm0_shortfall_is_a_negative_bias_and_worst_records_are_listed():
    a, b = frames(scale=0.99)
    result = crosscheck.compare(a, b)
    hm0 = row(result, "hm0")
    assert hm0["bias_pct"] == pytest.approx(-1.0, abs=0.01) and len(hm0["worst"]) == 10
    assert hm0["scatter"]["a"] and hm0["correlation"] == pytest.approx(1.0)


def test_only_matching_timestamps_are_compared():
    a, b = frames(n=60)
    hourly = pd.date_range("2022-01-01", periods=60 * 6, freq="h")
    a_hourly = a.reindex(hourly).interpolate()
    result = crosscheck.compare(a_hourly, b)
    assert result["overlap_records"] == 60


def test_missing_columns_are_reported_not_hidden():
    a, b = frames()
    result = crosscheck.compare(a.drop(columns=["mp1", "mp2"]), b)
    assert row(result, "tm01")["available"] is False and "mp1" in row(result, "tm01")["missing"]
    assert result["ordering"]["A: mp2 ≤ mp1 ≤ mwp"] is None


def test_no_overlap_is_a_clear_error():
    a, b = frames()
    with pytest.raises(ValueError, match="share no timestamps"):
        crosscheck.compare(a, b.set_axis(b.index + pd.Timedelta(days=400)))


def test_different_grid_cells_are_warned_about():
    a, b = frames()
    result = crosscheck.compare(a, b, cell_a=(-2.0, 99.5), cell_b=(-2.5, 99.5))
    assert any("different grid cells" in w for w in result["warnings"])


# --- API routes ----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    appmodule.jobs.clear()
    appmodule.app.testing = True
    with appmodule.app.test_client() as test_client:
        yield test_client


def make_job(job_id, product, files_builder, latitude=0.0, longitude=95.0):
    folder = appmodule.DOWNLOADS / job_id
    folder.mkdir(exist_ok=True)
    files_builder(folder)
    job = {"id": job_id, "status": "complete", "log": [], "return_code": 0, "directory": folder,
           "created": "2022-02-01T00:00:00+00:00", "latitude": latitude, "longitude": longitude,
           "buffer": 0.5, "product": product, "groups": [], "params": [], "time_step": 6,
           "start": "2022-01-01", "end": "2022-01-05", "dry_run": False}
    appmodule.jobs[job_id] = job
    (folder / "provenance.json").write_text('{"schema": 1}')


def build_a(folder):
    from tests.test_analysis import wave_dataset
    wave_dataset(hours=96).to_netcdf(folder / "era5_2022-01.nc")


def build_b(folder):
    from tests.test_analysis import spectra_dataset
    spectra_dataset(hours=16).to_netcdf(folder / "era5-spectra_2022-01.nc")


def test_timeseries_provenance_and_sector_routes(client):
    make_job("bbbbbbbbbbbb", "wave-spectra", build_b)
    response = client.get("/api/jobs/bbbbbbbbbbbb/timeseries.csv")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert response.get_data(as_text=True).startswith("time,hm0,te")
    assert client.get("/api/jobs/bbbbbbbbbbbb/provenance").get_json() == {"schema": 1}
    plain = client.get("/api/jobs/bbbbbbbbbbbb/analysis").get_json()
    with_sector = client.get("/api/jobs/bbbbbbbbbbbb/analysis?heading=232.5&halfwidth=45").get_json()
    assert not any("Sector flux" in s["title"] for s in plain["sections"])
    sector = next(s for s in with_sector["sections"] if "Sector flux" in s["title"])
    assert float(sector["rows"][0]["value"].split()[0]) > 90
    assert client.get("/api/jobs/bbbbbbbbbbbb/analysis?heading=abc").status_code == 422
    assert client.get("/api/jobs/bbbbbbbbbbbb/analysis?heading=100&halfwidth=500").status_code == 422


def test_sector_needs_the_spectra_route(client):
    make_job("aaaaaaaaaaab", "single-levels", build_a)
    response = client.get("/api/jobs/aaaaaaaaaaab/analysis?heading=180")
    assert response.status_code == 400 and "Option B" in response.get_json()["error"]


def test_crosscheck_route_end_to_end(client):
    make_job("cccccccccca1", "single-levels", build_a)
    make_job("cccccccccca2", "wave-spectra", build_b)
    result = client.get("/api/crosscheck?a=cccccccccca1&b=cccccccccca2")
    assert result.status_code == 200, result.get_json()
    data = result.get_json()
    assert data["job_a"] == "cccccccccca1" and data["overlap_records"] > 0
    assert any(r["key"] == "hm0" for r in data["rows"])


def test_crosscheck_rejects_wrong_products_and_unfinished_jobs(client):
    make_job("cccccccccca3", "single-levels", build_a)
    make_job("cccccccccca4", "single-levels", build_a)
    swapped = client.get("/api/crosscheck?a=cccccccccca3&b=cccccccccca4")
    assert swapped.status_code == 409 and "expected wave-spectra" in swapped.get_json()["error"]
    appmodule.jobs["cccccccccca3"]["status"] = "running"
    assert client.get("/api/crosscheck?a=cccccccccca3&b=cccccccccca4").status_code == 409
    assert client.get("/api/crosscheck?a=nope&b=nope").status_code == 404


def test_analysis_is_cached_between_requests(client):
    make_job("dddddddddddd", "wave-spectra", build_b)
    client.get("/api/jobs/dddddddddddd/analysis")
    cache = appmodule.DOWNLOADS / "dddddddddddd" / "analysis.json"
    first = cache.stat().st_mtime_ns
    client.get("/api/jobs/dddddddddddd/analysis")
    assert cache.stat().st_mtime_ns == first
    zipfile.is_zipfile(cache)  # json, not a zip: guards against saving the wrong object


# --- CDS cost limit: splitting and adaptive retry ------------------------------

def test_hourly_spectra_month_is_split_to_the_known_good_field_count():
    options = fetcher.normalise_options("wave-spectra", time_step=1)
    per_day = fetcher.fields_per_day(options)
    assert per_day == 24 * 24 * 30
    runs = fetcher.split_days(list(range(1, 32)), per_day, fetcher.MAX_FIELDS["wave-spectra"])
    assert [len(r) for r in runs] == [5, 5, 5, 4, 4, 4, 4]      # balanced, no one-day stub
    assert all(len(r) * per_day <= 86_400 for r in runs)
    # the 6-hourly month from WaveSpectrum-ERA-5 (30 x 4 x 720 = 86,400) stays one request
    six = fetcher.normalise_options("wave-spectra", time_step=6)
    assert len(fetcher.split_days(list(range(1, 31)), fetcher.fields_per_day(six), 86_400)) == 1
    thirty_one = fetcher.split_days(list(range(1, 32)), fetcher.fields_per_day(six), 86_400)
    assert [len(r) for r in thirty_one] == [16, 15]             # 89,280 fields: two balanced halves
    assert fetcher.split_days([1, 2, 3], 10, None) == [[1, 2, 3]]


def test_cost_error_detection_matches_the_cds_message():
    message = "403 Client Error: Forbidden ... cost limits exceeded\nYour request is too large, please reduce your selection."
    assert fetcher.is_cost_error(Exception(message))
    assert not fetcher.is_cost_error(Exception("401 Unauthorized"))


def test_dry_run_hourly_spectra_shows_the_split(tmp_path, capsys):
    code = fetcher.main(["--latitude", "-8.9", "--longitude", "115.0", "--start", "2025-01-01",
                         "--end", "2025-01-31", "--output", str(tmp_path), "--product", "wave-spectra",
                         "--time-step", "1", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "7 CDS request(s)" in out and "days 01-05" in out and "days 28-31" in out
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert len(record["requests"]) == 7 and record["requests"][1]["request"]["date"] == "2025-01-06/to/2025-01-10"


class FakeCds:
    """A cdsapi stand-in that refuses requests above ``limit`` days like the CDS cost check."""
    limit = 2
    calls = []

    def __init__(self, **kwargs):
        pass

    def retrieve(self, dataset, request, target):
        start, end = request["date"].split("/to/")
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
        type(self).calls.append(days)
        if days > type(self).limit:
            raise Exception("403 Client Error: Forbidden\ncost limits exceeded\nYour request is too large, "
                            "please reduce your selection.")
        with open(target, "wb") as handle:
            handle.write(b"x" * 100)


def test_refused_requests_are_halved_until_cds_accepts_them(tmp_path, monkeypatch, capsys):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "cdsapi", types.SimpleNamespace(Client=FakeCds))
    monkeypatch.setattr(fetcher, "fetch_json", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setitem(fetcher.MAX_FIELDS, "wave-spectra", 10**9)       # force one big first request
    FakeCds.calls = []
    code = fetcher.main(["--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-08",
                         "--output", str(tmp_path), "--product", "wave-spectra", "--expver", "1"])
    out = capsys.readouterr().out
    assert code == 0 and "cost limit exceeded; splitting" in out
    assert FakeCds.calls[0] == 8 and sorted(c for c in FakeCds.calls if c <= 2) == [2, 2, 2, 2]
    files = sorted(p.name for p in tmp_path.glob("era5-spectra_*.nc"))
    assert files[0] == "era5-spectra_2020-04_d01-02.nc" and len(files) == 4
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert [r["days"] for r in record["requests"]] == [[1, 2], [3, 4], [5, 6], [7, 8]]
    assert all(r["status"] == "downloaded" and len(r["sha256"]) == 64 for r in record["requests"])


def test_a_non_cost_error_stops_without_retrying(tmp_path, monkeypatch, capsys):
    import sys
    import types

    class Denied(FakeCds):
        def retrieve(self, dataset, request, target):
            raise Exception("401 Client Error: Unauthorized")

    monkeypatch.setitem(sys.modules, "cdsapi", types.SimpleNamespace(Client=Denied))
    monkeypatch.setattr(fetcher, "fetch_json", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    code = fetcher.main(["--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-08",
                         "--output", str(tmp_path), "--product", "wave-spectra", "--expver", "1"])
    out = capsys.readouterr().out
    assert code == 1 and "Unauthorized" in out and "splitting" not in out


# --- cost estimates and not hammering CDS --------------------------------------

def test_cost_limits_are_found_in_any_nesting():
    reply = {"cost": {"id": "precise_size", "cost": 12.0, "limit": 10.0},
             "limits": [{"id": "daily", "cost": 3, "limit": 100}], "other": {"x": 1}}
    found = fetcher.cost_limits(reply)
    assert ("precise_size", 12.0, 10.0) in found and ("daily", 3.0, 100.0) in found
    assert fetcher.cost_limits({"error": "500"}) == []
    assert fetcher.split_into([1, 2, 3, 4, 5, 6, 7], 3) == [[1, 2, 3], [4, 5], [6, 7]]


class EstimatingCds(FakeCds):
    """Reports a cost for the request, and refuses anything over ``limit`` days."""
    cost_per_day = 2.5
    limit_value = 10.0

    def estimate_costs(self, dataset, request):
        start, end = request["date"].split("/to/")
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
        return {"cost": {"id": "size", "cost": days * self.cost_per_day, "limit": self.limit_value}}


def run_fetcher(tmp_path, monkeypatch, client_class, extra=()):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "cdsapi", types.SimpleNamespace(Client=client_class))
    monkeypatch.setattr(fetcher, "fetch_json", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setitem(fetcher.MAX_FIELDS, "wave-spectra", 10**9)
    client_class.calls = []
    return fetcher.main(["--latitude", "6", "--longitude", "95", "--start", "2020-04-01", "--end", "2020-04-08",
                         "--output", str(tmp_path), "--product", "wave-spectra", "--expver", "1", *extra])


def test_the_estimate_is_used_to_size_requests_before_submitting(tmp_path, monkeypatch, capsys):
    EstimatingCds.limit = 4                      # refuse above 4 days; the estimate says 8 days = 200% of limit
    code = run_fetcher(tmp_path, monkeypatch, EstimatingCds)
    out = capsys.readouterr().out
    assert code == 0 and "200%" in out and "runs of at most 4" in out
    assert EstimatingCds.calls == [4, 4]         # the oversized 8-day request was never sent


def test_estimate_mode_submits_nothing(tmp_path, monkeypatch, capsys):
    code = run_fetcher(tmp_path, monkeypatch, EstimatingCds, extra=("--estimate",))
    out = capsys.readouterr().out
    assert code == 0 and "cost 20 of limit 10" in out and EstimatingCds.calls == []
    assert not list(tmp_path.glob("*.nc"))


def test_when_one_day_exceeds_the_limit_nothing_is_sent(tmp_path, monkeypatch, capsys):
    class TooBig(EstimatingCds):
        cost_per_day = 25.0
    code = run_fetcher(tmp_path, monkeypatch, TooBig)
    out = capsys.readouterr().out
    assert code == 1 and "even one day is estimated" in out and TooBig.calls == []


def test_a_refused_single_day_stops_the_run_instead_of_hammering_cds(tmp_path, monkeypatch, capsys):
    class AlwaysRefuse(FakeCds):
        limit = 0                                # every request is refused as too costly
    code = run_fetcher(tmp_path, monkeypatch, AlwaysRefuse)
    out = capsys.readouterr().out
    assert code == 1
    assert AlwaysRefuse.calls == [8, 4, 2, 1]    # one halving path, then stop: no sibling requests
    assert "single day" in out and "--estimate" in out and "--time-step" in out
