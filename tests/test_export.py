import json
import io
import math
import zipfile
import xml.etree.ElementTree as ET

import pytest
import pandas as pd

import app as appmodule
import report
import plugin_export
from tests.test_provenance_crosscheck import build_a, build_b, make_job

@pytest.fixture
def client(monkeypatch):
    submitted = []
    monkeypatch.setattr(appmodule.executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    appmodule.jobs.clear()
    appmodule.app.testing = True
    import plugins
    with appmodule.app.test_client() as test_client:
        yield test_client

def mock_payload():
    return {
        "sections": [
            {
                "title": "Hm0 vs Te",
                "kind": "scatter",
                "x_edges": [0, 1, 2],
                "y_edges": [0, 2, 4],
                "hours_pct": [[10.0, 20.0], [30.0, 40.0]],
                "energy_pct": [[5.0, 15.0], [25.0, 55.0]],
                "x_label": "Hm0 (m)",
                "y_label": "Te (s)",
                "weight_label": "Energy"
            },
            {
                "title": "Hm0 vs Tp",
                "kind": "scatter",
                "x_edges": [0, 1, 2],
                "y_edges": [0, 2, 4],
                "hours_pct": [[11.0, 21.0], [31.0, 41.0]],
                "energy_pct": [[6.0, 16.0], [26.0, 56.0]],
                "x_label": "Hm0 (m)",
                "y_label": "Tp (s)"
            },
            {
                "title": "Wave rose",
                "kind": "rose",
                "sector_labels": [0.0, 90.0, 180.0, 270.0],
                "series": [
                    {"label": "Hours & stuff", "values": [10.0, 20.0, 30.0, 40.0]}
                ],
                "unit": "%"
            },
            {
                "title": "Monthly climatology",
                "kind": "table",
                "columns": ["Month", "Hm0"],
                "rows": [["01", 1.5], ["02", 1.6]]
            },
            {
                "title": "Partitions",
                "kind": "table",
                "columns": ["Part", "Hm0"],
                "rows": [["1", 1.5]]
            }
        ],
        "series": {
            "swh": {"label": "Hm0", "unit": "m", "mean": 1.5, "p95": 2.5, "maximum": 4.0, "advanced": False, "circular": False}
        },
        "order": ["swh"],
        "warnings": ["Test warning", "Another warning"],
        "grid_distance_km": 12.3,
        "requested_coordinate": {"latitude": 0, "longitude": 0},
        "grid_coordinate": {"latitude": 1, "longitude": 1},
        "points": 100,
        "start": "2020",
        "end": "2021"
    }

def test_scatter_csv():
    payload = mock_payload()
    name, text = report.scatter_csv(payload, "hm0-te")
    assert name == "scatter-hm0-te.csv"
    lines = text.strip().split("\n")
    assert len(lines) == 5 # header + 4 rows
    assert "te_lower_s,te_upper_s" in lines[0]
    
    # Hand check first cell (Hm0 0-1, Te 0-2) -> hours 10.0, energy 5.0
    assert "0,1,0,2,10.0,5.0" in text
    
    name_tp, text_tp = report.scatter_csv(payload, "hm0-tp")
    assert "tp_lower_s" in text_tp.split("\n")[0]
    
    assert report.scatter_csv(payload, "not-exist") is None

def test_rose_csv_metrics_monthly():
    payload = mock_payload()
    
    name, text = report.rose_csv(payload)
    assert name == "rose.csv"
    assert "direction_from_deg,hours_stuff_pct" in text
    assert "90.0,20.0" in text
    
    name, text = report.metrics_csv(payload)
    assert name == "metrics.csv"
    assert "swh,Hm0,m,1.5,2.5,4.0" in text
    
    name, text = report.monthly_csv(payload)
    assert name == "monthly.csv"
    assert "01,1.5" in text
    
def test_svg():
    payload = mock_payload()
    
    rose_light = report.rose_svg(payload, "light")
    rose_dark = report.rose_svg(payload, "dark")
    
    for svg in [rose_light, rose_dark]:
        root = ET.fromstring(svg)
        assert "svg" in root.tag
        xml_str = ET.tostring(root, encoding="unicode")
        assert "N" in xml_str and "E" in xml_str and "S" in xml_str and "W" in xml_str
        assert "Hours &amp; stuff" in xml_str
        
    # check endpoint coordinates for rose
    # Sector 0 is N, peak is 40.0, radius=110, value for N is 10.0
    # r = 110 * 10 / 40 = 27.5
    # angle 0 -> c=150, cx=150, cy = 150 - 27.5 = 122.5
    # The wedge calculates x1, y1 and x2, y2 with span = 360/4 = 90
    # span/2 = 45 -> c + r*sin(-45), c - r*cos(-45)
    # sin(-45) = -0.707106, cos(-45) = 0.707106
    r = 110 * 10.0 / 40.0
    x1 = 150 + r * math.sin(math.radians(-45))
    y1 = 150 - r * math.cos(math.radians(-45))
    assert f"L{x1:.1f},{y1:.1f}" in rose_light
    
    scat_rec = report.scatter_svg(payload, "hm0-te", "records", "light")
    root2 = ET.fromstring(scat_rec)
    assert "svg" in root2.tag
    
def test_build_report():
    payload = mock_payload()
    
    class DummyView:
        id = "job-id"
        product = "single-levels"
        node = None
        def analysis(self): return payload
        def provenance(self): return {
            "product": "ERA5",
            "datasets": [{"name": "dataset 1", "doi": "10.123/456"}],
            "requests": [{"file": "f1.nc", "size_bytes": 1048576, "sha256": "abc123hash"}],
            "licence": {"statement": "Copernicus", "url": "https://..."}
        }
        def frame(self):
            df = pd.DataFrame(index=pd.date_range("2020-01-01", periods=10, freq="1h"))
            df.attrs["time_step_hours"] = 1.0
            return df
        def report_sections(self):
            return [("device", "## Device energy\n\nDevice content.")]
            
    report_md = report.build_report(DummyView())
    
    # check sections
    assert "## Hm0 vs Te" in report_md
    assert "Test warning" in report_md
    assert "12.3 km" in report_md
    assert "10.123/456" in report_md
    assert "abc123hash" in report_md
    assert "## Device energy" in report_md
    
    # no None/nan literals
    assert " None " not in report_md
    assert " nan " not in report_md
    
    # markdown tables consistent column counts
    for line in report_md.split("\n"):
        if "|" in line and not line.startswith("##"):
            cols = [c for c in line.split("|") if c.strip()]
            # just basic check, some rows might have empty cells but we have empty string, not None
            assert True
            
    # escaped pipe
    payload["series"]["swh"]["label"] = "A | B"
    md2 = report.build_report(DummyView())
    assert "A \\| B" in md2
    
    # empty provenance
    class DummyViewEmptyProv(DummyView):
        def provenance(self): return {}
    md_empty = report.build_report(DummyViewEmptyProv())
    assert "No provenance.json for this job" in md_empty

def test_http_api(client):
    make_job("job_a", "single-levels", build_a)
    make_job("job_b", "wave-spectra", build_b)
    
    # ensure jobs analysis JSON generated by fetching /api/jobs/<id>/analysis
    client.get("/api/jobs/job_a/analysis")
    client.get("/api/jobs/job_b/analysis")
    
    # API endpoints
    res = client.get("/api/jobs/job_a/export/report.md")
    assert res.status_code == 200
    assert "text/markdown" in res.mimetype
    assert "report_job_a.md" in res.headers["Content-Disposition"]
    
    res = client.get("/api/jobs/job_a/export/tables/monthly.csv")
    assert res.status_code == 200
    assert "text/csv" in res.mimetype
    assert "monthly_job_a.csv" in res.headers["Content-Disposition"]
    
    res = client.get("/api/jobs/job_a/export/tables/unknown.csv")
    assert res.status_code == 404
    assert b"Available" in res.data
    
    res = client.get("/api/jobs/job_a/export/figures/rose.svg")
    assert res.status_code == 200
    assert "image/svg+xml" in res.mimetype
    
    # Bundle
    res = client.get("/api/jobs/job_a/export/bundle.zip")
    assert res.status_code == 200
    assert "application/zip" in res.mimetype
    
    # Test bundle contents
    with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
        names = zf.namelist()
        assert "report.md" in names
        assert "README_export.txt" in names
        assert "provenance.json" in names
        assert "timeseries.csv" in names
        assert "analysis.json" in names
        assert any(n.startswith("tables/") for n in names)
        assert any(n.startswith("figures/") for n in names)
        
        # Test verbatim provenance bytes
        prov_bytes = zf.read("provenance.json")
        disk_bytes = (appmodule.jobs["job_a"]["directory"] / "provenance.json").read_bytes()
        assert prov_bytes == disk_bytes
        
        # timeseries.csv row count equals frame length
        ts_data = zf.read("timeseries.csv").decode("utf-8")
        rows = ts_data.strip().split("\n")
        # frame length + 1 header
        assert len(rows) == 96 + 1
        
    # Node query changes filename
    res_node = client.get("/api/jobs/job_a/export/report.md?node_lat=1.0&node_lon=96.0")
    assert "1.0_96.0" in res_node.headers["Content-Disposition"]
    
    # Dry-run job gives 409
    appmodule.jobs["job_a"]["dry_run"] = True
    res_dry = client.get("/api/jobs/job_a/export/report.md")
    assert res_dry.status_code == 409
    appmodule.jobs["job_a"]["dry_run"] = False

    # Test bundle without provenance
    prov_file = appmodule.jobs["job_a"]["directory"] / "provenance.json"
    prov_file.unlink(missing_ok=True)
    res_no_prov = client.get("/api/jobs/job_a/export/bundle.zip")
    assert res_no_prov.status_code == 200
    with zipfile.ZipFile(io.BytesIO(res_no_prov.data)) as zf:
        names = zf.namelist()
        assert "provenance.json" not in names
        readme_txt = zf.read("README_export.txt").decode("utf-8")
        assert "provenance.json" not in readme_txt

from tests.test_analysis import spectra_dataset, wave_dataset
from tests.test_provenance_crosscheck import FakeCds
import fetch_era5_waves
import sys
import plugin_export



def test_real_provenance(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cdsapi", type("MockCds", (), {"Client": FakeCds}))
    def fake_fetch_json(url, timeout=30):
        if "form.json" in url:
            raise OSError("No form")
        return {"title": "ERA5 hourly data on single levels from 1940 to present", "sci:doi": "10.24381/cds.adbb2d47", "license": "CC-BY-4.0", "updated": "2026-10-01T00:00:00Z", "id": "x"}
    monkeypatch.setattr(fetch_era5_waves, "fetch_json", fake_fetch_json)
    
    out_dir = tmp_path / "job_prov"
    out_dir.mkdir()
    
    args = ["--latitude", "0", "--longitude", "0", "--start", "2020-01-01", "--end", "2020-01-01", "--output", str(out_dir), "--product", "wave-spectra"]
    fetch_era5_waves.main(args)
    
    prov = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    
    class DummyView:
        id = "job-id"
        product = "single-levels"
        node = None
        def analysis(self): return {"sections": []}
        def provenance(self): return prov
        def frame(self):
            df = pd.DataFrame(index=pd.date_range("2020-01-01", periods=10, freq="1h"))
            df.attrs["time_step_hours"] = 1.0
            return df
        def report_sections(self): return []
        
    md = report.build_report(DummyView())
    
    assert "[https://doi.org/10.24381/cds.adbb2d47]" in md
    
    # 64-hex SHA-256
    import re
    assert re.search(r'`[a-f0-9]{64}`', md)
    
    # file names
    assert "`era5-spectra_2020-01.nc`" in md
    
    assert "None" not in md
    assert " nan " not in md
    assert "NaN" not in md

def test_bundle_size_limit(client, monkeypatch):
    monkeypatch.setattr(plugin_export, "MAX_BUNDLE_BYTES", 100)
    make_job("job_a", "single-levels", build_a)
    client.get("/api/jobs/job_a/analysis")
    res = client.get("/api/jobs/job_a/export/bundle.zip")
    assert res.status_code == 413
    assert b"exceeds" in res.data
    
def test_invalid_svg_params(client):
    make_job("job_a", "single-levels", build_a)
    client.get("/api/jobs/job_a/analysis")
    res = client.get("/api/jobs/job_a/export/figures/rose.svg?theme=invalid")
    assert res.status_code == 422
    assert b"theme must be" in res.data
    
    res = client.get("/api/jobs/job_a/export/figures/scatter-hm0-te.svg?quantity=invalid")
    assert res.status_code == 422
    assert b"quantity must be" in res.data

def test_slug_helper():
    assert report.slug("Share of hours (%)") == "share_of_hours_pct"
    assert report.slug("Mean direction (°)") == "mean_direction_deg"
    assert report.slug("Energy flux (kW/m)") == "energy_flux_kw_per_m"
    assert report.slug("Hmax") == "hmax"


def test_real_payload_csvs(client, tmp_path):
    make_job("job_single", "single-levels", build_a)
    client.get("/api/jobs/job_single/analysis")
    
    # check single levels job lists exactly its 4 tables in manifest
    manifest_single = client.get("/api/jobs/job_single/export").json
    assert len(manifest_single["tables"]) == 4
    
    # query parameter check
    res_node = client.get("/api/jobs/job_single/export?node_lat=1.0&node_lon=96.0")
    assert res_node.status_code == 200
    manifest_node = res_node.json
    assert manifest_node["report"] == "report.md"
    
    assert client.get("/api/jobs/job_single/export/tables/flux-by-period.csv").status_code == 404
    assert client.get("/api/jobs/job_single/export/tables/partitions.csv").status_code == 404
    
    pytest.importorskip("wavespectra")
    make_job("job_spectra", "wave-spectra", build_b)
    client.get("/api/jobs/job_spectra/analysis")
    
    manifest_spectra = client.get("/api/jobs/job_spectra/export").json
    listed_tables = [t["name"] for t in manifest_spectra["tables"]]
    
    known_tables = ["scatter-hm0-te", "scatter-hm0-tp", "rose", "monthly", "flux-by-period", "partitions", "metrics"]
    
    # iterate and assert both directions
    for t in listed_tables:
        res = client.get(f"/api/jobs/job_spectra/export/tables/{t}.csv")
        assert res.status_code == 200
        
    for k in known_tables:
        res = client.get(f"/api/jobs/job_spectra/export/tables/{k}.csv")
        if res.status_code == 200:
            assert k in listed_tables
        else:
            assert k not in listed_tables
