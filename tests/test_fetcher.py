import datetime as dt
import io
import json
import sys

import pytest

import fetch_era5_waves as fetcher

D = dt.date


def test_build_area_snaps_outward_to_wave_grid():
    north, west, south, east = fetcher.build_area(-6.9175, 107.6191, 0.5)
    assert (north, west, south, east) == (-6.0, 107.0, -7.5, 108.5)


def test_build_area_zero_buffer_still_spans_a_wave_cell():
    north, west, south, east = fetcher.build_area(6.1, 95.1, 0)
    assert north - south == 0.5 and east - west == 0.5


def test_build_area_clips_at_date_line_and_poles():
    north, west, south, east = fetcher.build_area(89.9, 179.9, 1)
    assert north == 90 and east == 180
    assert fetcher.build_area(-89.9, -179.9, 1)[1] == -180


def test_month_chunks_split_partial_months():
    chunks = fetcher.month_chunks(D(2019, 12, 20), D(2020, 2, 10))
    assert [(y, m) for y, m, _ in chunks] == [(2019, 12), (2020, 1), (2020, 2)]
    assert chunks[0][2] == list(range(20, 32))
    assert chunks[1][2] == list(range(1, 32))
    assert chunks[2][2] == list(range(1, 11))


def test_month_chunks_single_day_and_leap_february():
    assert fetcher.month_chunks(D(2020, 2, 29), D(2020, 2, 29)) == [(2020, 2, [29])]
    assert len(fetcher.month_chunks(D(2020, 2, 1), D(2020, 2, 29))[0][2]) == 29


def test_build_request_matches_cds_schema():
    dataset, request = fetcher.build_request(2020, 4, [1, 2], [6, 95, 0, 100])
    assert dataset == "reanalysis-era5-single-levels"
    assert request["day"] == ["01", "02"] and request["month"] == ["04"]
    assert len(request["time"]) == 24 and request["area"] == [6, 95, 0, 100]
    assert "significant_height_of_combined_wind_waves_and_swell" in request["variable"]
    assert request["data_format"] == "netcdf"


def test_validate_period_rules():
    today = D(2026, 10, 1)
    fetcher.validate_period(D(2026, 9, 1), D(2026, 9, 25), today)
    with pytest.raises(ValueError, match="latest usable end date is 2026-09-25"):
        fetcher.validate_period(D(2026, 9, 1), D(2026, 9, 26), today)
    with pytest.raises(ValueError, match="before end"):
        fetcher.validate_period(D(2020, 2, 1), D(2020, 1, 1), today)
    with pytest.raises(ValueError, match="1940"):
        fetcher.validate_period(D(1930, 1, 1), D(1930, 2, 1), today)
    with pytest.raises(ValueError, match="years"):
        fetcher.validate_period(D(2010, 1, 1), D(2020, 1, 1), today)


def test_dry_run_prints_requests_without_cdsapi(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "cdsapi", None)  # an import would raise
    code = fetcher.main([
        "--latitude", "-6.9", "--longitude", "107.6", "--start", "2020-04-01",
        "--end", "2020-05-02", "--output", str(tmp_path / "out"), "--dry-run",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 month(s)" in out and "--- 2020-05 " in out
    april = out.split("--- 2020-04 ")[1].split("---\n", 1)[1].split("--- 2020-05 ")[0]
    assert json.loads(april)["month"] == ["04"]
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["provenance.json"]  # no data files


def test_main_prints_non_ascii_on_a_cp1252_console(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="cp1252", write_through=True)
    monkeypatch.setattr(sys, "stdout", console)
    code = fetcher.main([
        "--latitude", "-6.9", "--longitude", "107.6", "--start", "2020-04-01",
        "--end", "2020-04-30", "--output", str(tmp_path / "out"), "--dry-run",
    ])
    assert code == 0
    assert "≈" in buffer.getvalue().decode("utf-8")  # "Estimated size ≈ ..."


def test_main_rejects_future_end_date(capsys):
    code = fetcher.main([
        "--latitude", "0", "--longitude", "0", "--start", "2020-01-01",
        "--end", "2999-01-01", "--output", "unused", "--dry-run",
    ])
    assert code == 2 and "latest usable end date" in capsys.readouterr().out
