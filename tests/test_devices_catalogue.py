import json
import os
from pathlib import Path
import pytest
import numpy as np
import app as appmodule
from tests.test_nodes import client
from tests.test_provenance_crosscheck import make_job, build_b
from devices_catalogue import load_catalogue

def test_catalogue_valid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    devices_json = {
        "test-dev-1": {
            "name": "Test Device 1",
            "rated_kw": 100.0,
            "width_m": 10.0,
            "period_type": "Te",
            "bin_convention": "centres",
            "source": "A Source",
            "provenance": "Some provenance",
            "notes": ["Note 1"],
            "hs_m": [1.0, 2.0, 3.0],
            "period_s": [4.0, 5.0, 6.0],
            "power_kw": [
                [0.0, 10.0, None],
                [20.0, 50.0, 100.0],
                [10.0, 99.5, 100.0]
            ]
        },
        "test-dev-2": {
            "name": "Test PT",
            "period_type": "junk",
            "hs_m": [1.0, 2.0],
            "period_s": [4.0, 5.0],
            "power_kw": [[10, 20], [30, 40]]
        },
        "test-dev-3": {
            "name": "Test PT Tp",
            "period_type": "Tp",
            "hs_m": [1.0, 2.0],
            "period_s": [4.0, 5.0],
            "power_kw": [[10, 20], [30, 40]]
        }
    }
    (tmp_path / "devices.json").write_text(json.dumps(devices_json))

    cat = load_catalogue()
    assert cat.configured is True
    assert cat.directory == tmp_path.name

    dev1 = next(d for d in cat.devices if d.id == "test-dev-1")
    assert dev1.n_hm0 == 3
    assert dev1.n_period == 3
    assert dev1.hm0_range == [1.0, 3.0]
    assert dev1.period_range == [4.0, 6.0]
    assert dev1.power_max_kw == 100.0
    assert dev1.total_cells == 9
    assert dev1.defined_cells == 8
    assert dev1.defined_pct == (8/9)*100
    assert dev1.near_max_cells == 3
    assert dev1.period_type == "te"

    dev2 = next(d for d in cat.devices if d.id == "test-dev-2")
    assert dev2.period_type == "unknown"

    dev3 = next(d for d in cat.devices if d.id == "test-dev-3")
    assert dev3.period_type == "tp"

def test_catalogue_rejection_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))

    def check_warn(entry, expected_warn, expected_count=0):
        (tmp_path / "devices.json").write_text(json.dumps({"test-fail": entry}))
        cat = load_catalogue()
        assert len([d for d in cat.devices if not d.synthetic]) == expected_count
        assert any(expected_warn in w for w in cat.warnings), f"Missing warning: {expected_warn} in {cat.warnings}"

    check_warn([], "not an object")

    base = {
        "name": "a",
        "hs_m": [1.0, 2.0],
        "period_s": [4.0, 5.0],
        "power_kw": [[10, 20], [30, 40]]
    }

    b = base.copy(); b["hs_m"] = [2.0, 1.0]
    check_warn(b, "hs_m not strictly increasing")

    b = base.copy(); b["power_kw"] = [[10, 20], [30]]
    check_warn(b, "ragged or invalid row")

    b = base.copy(); b["power_kw"] = [[10, -20], [30, 40]]
    check_warn(b, "invalid cell value")

    b = base.copy(); b["power_kw"] = [[10, "abc"], [30, 40]]
    check_warn(b, "invalid cell value")

    b = base.copy(); b["power_kw"] = [[None, None], [None, None]]
    check_warn(b, "all cells undefined")

    (tmp_path / "devices.json").write_text(json.dumps({"A"*61: base}))
    cat = load_catalogue()
    assert any("Invalid device id" in w for w in cat.warnings)

    b = base.copy(); b["rated_kw"] = -100
    check_warn(b, "invalid rated_kw")

def test_catalogue_file_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    (tmp_path / "devices.json").write_text("{bad")
    cat = load_catalogue()
    assert any("not valid JSON" in w for w in cat.warnings)

    large_text = '{"a": {"name": "a", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]], "notes": ["' + 'A' * (5 * 1024 * 1024) + '"]}}'
    (tmp_path / "devices.json").write_text(large_text)
    cat2 = load_catalogue()
    assert any("exceeds 5 MB limit" in w for w in cat2.warnings)

def test_catalogue_duplicates_and_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))

    json_str = '{"dev1": {"name": "D1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]], "file": "mydev.csv"}}'
    (tmp_path / "devices.json").write_text(json_str)

    (tmp_path / "mydev.csv").write_text("Hm0\\Te,1,2\n1,1,1\n2,1,1")
    (tmp_path / "dev1.csv").write_text("Hm0\\Te,1,2\n1,1,1\n2,1,1")
    (tmp_path / "stray.csv").write_text("Hm0\\Te,1,2\n1,1,1\n2,1,1")
    (tmp_path / "malformed.csv").write_text("Hm0\\Te,1,2,\n1,1,1,\n2,1,1,")

    cat = load_catalogue()
    ids = [d.id for d in cat.devices]

    assert "stray" in ids
    assert "malformed" not in ids
    assert any("malformed.csv" in w for w in cat.warnings)       # skipped with a warning that names the file
    assert "mydev" not in ids

    assert ids.count("dev1") == 1
    assert any("Duplicate device id 'dev1'" in w for w in cat.warnings)

    json_str_path = '{"dev2": {"name": "D2", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]], "file": "../mydev.csv"}}'
    (tmp_path / "devices.json").write_text(json_str_path)
    cat2 = load_catalogue()
    assert "mydev" in [d.id for d in cat2.devices]

def test_a_row_may_end_in_undefined_cells(tmp_path, monkeypatch):
    # Blank cells at the end of a row (no data at long periods for high waves) are undefined, not missing columns.
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    (tmp_path / "ends-blank.csv").write_text("Hm0\\Te,4,5,6\n1,10,20,30\n2,40,,\n3,50,60,")
    device = next(d for d in load_catalogue().devices if d.id == "ends-blank")
    assert device.power_kw == [[10.0, 20.0, 30.0], [40.0, None, None], [50.0, 60.0, None]]
    assert device.defined_cells == 6 and device.total_cells == 9


def test_the_csv_of_a_rejected_entry_does_not_become_an_unlabelled_device(tmp_path, monkeypatch):
    # Otherwise a matrix whose metadata failed validation would load without its provenance and caveats.
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    entry = {"name": "Bad rating", "rated_kw": -5, "hs_m": [1, 2], "period_s": [1, 2],
             "power_kw": [[1, 1], [1, 1]], "file": "bad-rating.csv"}
    (tmp_path / "devices.json").write_text(json.dumps({"bad-rating": entry}))
    (tmp_path / "bad-rating.csv").write_text("Hm0\\Te,1,2\n1,1,1\n2,1,1")
    cat = load_catalogue()
    assert [d.id for d in cat.devices if not d.synthetic] == []
    assert any("bad-rating" in w and "rated_kw" in w for w in cat.warnings)


def test_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    json_str = '{"bad-dev": {"name": "bad"}, "good-dev": {"name": "good", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]]}}'
    (tmp_path / "devices.json").write_text(json_str)
    cat = load_catalogue()
    ids = [d.id for d in cat.devices]
    assert "good-dev" in ids
    assert "bad-dev" not in ids
    bad_warnings = [w for w in cat.warnings if "bad-dev" in w]
    assert len(bad_warnings) == 1

def test_notes_and_null_source(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    json_str = '{"dev1": {"name": "d1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]], "notes": 5, "source": null}}'
    (tmp_path / "devices.json").write_text(json_str)
    cat = load_catalogue()
    dev = next(d for d in cat.devices if d.id == "dev1")
    assert dev.notes == []
    assert dev.source == ""

    json_str2 = '{"dev1": {"name": "d1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]], "notes": "text"}}'
    (tmp_path / "devices.json").write_text(json_str2)
    cat2 = load_catalogue()
    dev2 = next(d for d in cat2.devices if d.id == "dev1")
    assert dev2.notes == []

def test_invalid_types_and_names(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    json_str = '{"dev-nan": {"name": "d1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,NaN],[1,1]]}}'
    (tmp_path / "devices.json").write_text(json_str)
    cat = load_catalogue()
    assert any("dev-nan" in w and "invalid cell value" in w for w in cat.warnings)

    json_str_bool = '{"dev-bool": {"name": "d1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,true],[1,1]]}}'
    (tmp_path / "devices.json").write_text(json_str_bool)
    cat = load_catalogue()
    assert any("dev-bool" in w and "invalid cell value" in w for w in cat.warnings)

    json_str_name1 = '{"dev-name1": {"name": "", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]]}}'
    (tmp_path / "devices.json").write_text(json_str_name1)
    cat = load_catalogue()
    assert any("dev-name1" in w and "name length" in w for w in cat.warnings)

    json_str_name2 = '{"dev-name2": {"name": "' + "a" * 61 + '", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]]}}'
    (tmp_path / "devices.json").write_text(json_str_name2)
    cat = load_catalogue()
    assert any("dev-name2" in w and "name length" in w for w in cat.warnings)

    json_str_name3 = '{"dev-name3": {"name": "\\n\\t\\r", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]]}}'
    (tmp_path / "devices.json").write_text(json_str_name3)
    cat = load_catalogue()
    assert any("dev-name3" in w and "name length" in w for w in cat.warnings)

def test_environment_and_http(client, tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICES_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices" / "devices.json").write_text('{"dev1": {"name": "d1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]]}}')

    cat = load_catalogue()
    assert cat.configured is False
    assert cat.directory == "devices"

    res = client.get("/api/devices")
    data = res.get_json()
    assert data["configured"] is False
    assert data["directory"] == "devices"

    monkeypatch.setenv("DEVICES_DIR", str(tmp_path / "devices"))
    cat2 = load_catalogue()
    assert cat2.configured is True

    res2 = client.get("/api/devices")
    data2 = res2.get_json()
    assert data2["configured"] is True

def test_synthetic_example(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    (tmp_path / "devices.json").write_text('{"dev1": {"name": "d1", "hs_m": [1,2], "period_s": [1,2], "power_kw": [[1,1],[1,1]]}}')
    cat = load_catalogue()

    assert cat.devices[-1].id == "synthetic-example"
    assert cat.devices[-1].synthetic is True
    assert cat.devices[-1].rated_kw is None
    assert cat.devices[-1].period_type == "unknown"

def test_detail_edges(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    json_str = '''{
        "dev-centres": {"name": "dc", "bin_convention": "centres", "hs_m": [1,2,4], "period_s": [1,2,4], "power_kw": [[1,1,1],[1,1,1],[1,1,1]]},
        "dev-edges": {"name": "de", "bin_convention": "lower_edges", "hs_m": [1,2,4], "period_s": [1,2,4], "power_kw": [[1,1,1],[1,1,1],[1,1,1]]}
    }'''
    (tmp_path / "devices.json").write_text(json_str)

    res_c = client.get("/api/devices/dev-centres")
    assert res_c.get_json()["hm0_edges"] == [0.5, 1.5, 3.0, 5.0]

    res_e = client.get("/api/devices/dev-edges")
    assert res_e.get_json()["hm0_edges"] == [1.0, 2.0, 4.0, 6.0]

def test_csv_file_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))

    (tmp_path / "large.csv").write_text("Hm0\\Te,1\n1," + "1" * (2 * 1024 * 1024))
    (tmp_path / "bad_utf8.csv").write_bytes(b"Hm0\\Te,1\n1,\xff")
    (tmp_path / "good.csv").write_text("Hm0\\Te,1,2\n1,1,1\n2,1,1")

    cat = load_catalogue()
    ids = [d.id for d in cat.devices]
    assert "good" in ids
    assert "large" not in ids
    assert "bad_utf8" not in ids

    assert any("large" in w for w in cat.warnings)
    assert any("bad_utf8" in w for w in cat.warnings)

def test_assess_with_device_id_and_convention(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICES_DIR", str(tmp_path))
    make_job("eeeeeeeeeea1", "wave-spectra", build_b)

    csv = "Hm0\\Te, 1, 2\n1, 10, 20\n2, 30, 40"
    (tmp_path / "mydev.csv").write_text(csv)

    payload_csv = {
        "name": "csvdev",
        "csv_text": csv,
        "period_type": "unknown",
        "bin_convention": "lower_edges"
    }
    r1 = client.post("/api/jobs/eeeeeeeeeea1/device", json=payload_csv)
    aep1 = r1.get_json()["indexings"]["te"]["bin"]["aep_mwh"]

    payload_cat = {
        "name": "catdev",
        "device_id": "mydev",
        "period_type": "unknown",
        "bin_convention": "lower_edges"
    }
    r2 = client.post("/api/jobs/eeeeeeeeeea1/device", json=payload_cat)
    aep2 = r2.get_json()["indexings"]["te"]["bin"]["aep_mwh"]

    assert aep1 == aep2


def test_the_page_loads_the_script_and_style_of_every_plugin_that_has_them(client):
    html = client.get("/").get_data(as_text=True)
    for name in ("device", "devices", "screening", "longterm", "export"):
        assert f"plugins/{name}.js" in html, name
    for name in ("device", "devices", "screening", "longterm"):
        assert f"plugins/{name}.css" in html, name
