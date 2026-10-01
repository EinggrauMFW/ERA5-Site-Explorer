import pytest
import numpy as np
import pandas as pd
import json
import io
import datetime
from device import parse_power_matrix, lookup_power, assess_device, example_matrix_csv, PowerMatrix

from tests.test_provenance_crosscheck import make_job, build_b
import app as appmodule
from tests.test_nodes import client

def test_parse_centres():
    csv_text = "Hm0\\Te, 2, 4, 6\n1, 10, 20, 30\n2, 40, 50, 60\n3, 70, 80, 90"
    m = parse_power_matrix(csv_text, "centres")
    assert np.allclose(m.hm0_centres, [1, 2, 3])
    assert np.allclose(m.hm0_edges, [0.5, 1.5, 2.5, 3.5])
    assert np.allclose(m.period_centres, [2, 4, 6])
    assert np.allclose(m.period_edges, [1, 3, 5, 7])
    assert m.power_kw.shape == (3, 3)
    
def test_parse_lower_edges():
    csv_text = "Hm0\\Te, 2, 4, 6\n1, 10, 20, 30\n2, 40, 50, 60\n3, 70, 80, 90"
    m = parse_power_matrix(csv_text, "lower_edges")
    assert np.allclose(m.hm0_edges, [1, 2, 3, 4])
    assert np.allclose(m.hm0_centres, [1.5, 2.5, 3.5])
    assert np.allclose(m.period_edges, [2, 4, 6, 8])
    assert np.allclose(m.period_centres, [3, 5, 7])

def test_parse_delimiters_and_bom():
    csv_text = "\ufeffHm0\\Te;2;4\n1;-;NaN\n2;nan;10.5"
    m = parse_power_matrix(csv_text)
    assert np.isnan(m.power_kw[0, 0])
    assert np.isnan(m.power_kw[0, 1])
    assert np.isnan(m.power_kw[1, 0])
    assert m.power_kw[1, 1] == 10.5

def test_parse_validation_errors():
    with pytest.raises(ValueError, match="at least 2 wave heights"):
        parse_power_matrix("Hm0\\Te,2,4\n1,10,20")
    with pytest.raises(ValueError, match="at least 2 periods"):
        parse_power_matrix("Hm0\\Te,2\n1,10\n2,20")
    with pytest.raises(ValueError, match="strictly increasing"):
        parse_power_matrix("Hm0\\Te,4,2\n1,10,20\n2,30,40")
    with pytest.raises(ValueError, match="strictly increasing"):
        parse_power_matrix("Hm0\\Te,2,4\n2,10,20\n1,30,40")
    with pytest.raises(ValueError, match="negative"):
        parse_power_matrix("Hm0\\Te,2,4\n1,10,-20\n2,30,40")
    with pytest.raises(ValueError, match="decimal comma"):
        parse_power_matrix("Hm0\\Te,2,4\n1,10,20,5\n2,30,40") # 4 columns but 3 in header because of comma
        
def test_lookup_power():
    hm0_edges = np.array([0, 1, 2])
    p_edges = np.array([0, 1, 2])
    power_kw = np.array([[10, np.nan], [30, 40]], dtype=float)
    hm0_centres = np.array([0.5, 1.5])
    p_centres = np.array([0.5, 1.5])
    m = PowerMatrix(hm0_edges, p_edges, power_kw, hm0_centres, p_centres)
    
    h = np.array([1.0, 2.0, 0.5, np.nan])
    p = np.array([0.5, 0.5, 1.5, 0.5])
    
    pwr, ins = lookup_power(m, h, p, "bin")
    # h=1.0 is on interior edge, goes to upper bin (idx 1)
    # p=0.5 goes to lower bin (idx 0)
    # val = 30
    assert pwr[0] == 30 and ins[0]
    
    # h=2.0 is on upper edge -> outside
    assert not ins[1] and pwr[1] == 0
    
    # h=0.5 (idx 0), p=1.5 (idx 1). val = NaN
    assert not ins[2] and pwr[2] == 0
    
    assert not ins[3] and pwr[3] == 0
    
    h2 = np.array([1.0, 2.0])
    p2 = np.array([1.0, 1.0])
    pwr2, ins2 = lookup_power(m, h2, p2, "bilinear")
    
    # h=1.0, p=1.0 is exact midpoint of 10, nan, 30, 40 -> NaN -> outside
    assert not ins2[0] and pwr2[0] == 0
    
    # h=2.0 is outside centres extent
    assert not ins2[1] and pwr2[1] == 0
    
    # Let's test a valid bilinear exact midpoint
    power_kw2 = np.array([[10, 20], [30, 40]], dtype=float)
    m2 = PowerMatrix(hm0_edges, p_edges, power_kw2, hm0_centres, p_centres)
    pwr3, ins3 = lookup_power(m2, np.array([1.0]), np.array([1.0]), "bilinear")
    assert ins3[0] and pwr3[0] == 25

def test_assess_device():
    # 4 records, 3x3 matrix
    idx = pd.date_range("2020-01-01", periods=4, freq="6h")
    frame = pd.DataFrame({
        "hm0": [1.0, 2.0, 3.0, 4.0],
        "te": [2.0, 4.0, 6.0, 8.0],
        "tp": [2.0, 4.0, 6.0, 8.0],
        "flux": [10.0, 20.0, 30.0, 40.0]
    }, index=idx)
    frame.attrs["time_step_hours"] = 6.0
    
    csv = "Hm0\\Te, 2, 4, 6\n1, 10, 20, 30\n2, 40, 50, 60\n3, 70, 80, 90"
    m = parse_power_matrix(csv)
    
    # Records: 
    # 1: h=1, t=2 -> centre 1,2 -> inside, p=10
    # 2: h=2, t=4 -> centre 2,4 -> inside, p=50
    # 3: h=3, t=6 -> centre 3,6 -> inside, p=90
    # 4: h=4, t=8 -> outside
    
    res = assess_device(frame, m, name="test", rated_kw=100.0, width_m=10.0, period_type="te")
    
    bin_res = res["indexings"]["te"]["bin"]
    assert bin_res["records_valid"] == 4
    assert bin_res["records_inside"] == 3
    assert bin_res["share_outside_pct"] == 25.0
    assert bin_res["flux_outside_support_pct"] == 40.0 # 40 / 100 * 100
    
    mean_p = (10 + 50 + 90 + 0) / 4 # 37.5
    assert bin_res["mean_power_kw"] == mean_p
    assert bin_res["aep_mwh"] == mean_p * 8766 / 1000
    assert bin_res["capacity_factor_pct"] == 37.5
    
    # capture width energy weighted: sum(P) / sum(J) for valid & J>0
    assert bin_res["capture_width_energy_weighted_m"] == 150 / 100
    assert bin_res["capture_width_energy_weighted_ratio"] == 1.5 / 10.0
    
    # capture width mean of ratios: mean of P/J
    # 10/10=1, 50/20=2.5, 90/30=3, 0/40=0 -> mean is 6.5 / 4 = 1.625
    assert bin_res["capture_width_mean_of_ratios_m"] == 1.625
    
    monthly = res["indexings"]["te"]["monthly"]
    assert sum(m["energy_share_pct"] for m in monthly) == pytest.approx(100.0)

def test_period_type_unknown():
    idx = pd.date_range("2020-01-01", periods=1, freq="6h")
    frame = pd.DataFrame({"hm0": [1.0], "te": [2.0], "tp": [4.0], "flux": [10.0]}, index=idx)
    m = parse_power_matrix("Hm0\\Te, 2, 4\n1, 10, 20\n2, 30, 40")
    
    res = assess_device(frame, m, name="test", period_type="unknown")
    assert "te" in res["indexings"] and "tp" in res["indexings"]
    assert "ambiguity" in res
    
    res2 = assess_device(frame, m, name="test", period_type="tp")
    assert "te" not in res2["indexings"] and "tp" in res2["indexings"]
    assert "ambiguity" not in res2

def test_constant_power():
    idx = pd.date_range("2020-01-01", periods=4, freq="6h")
    frame = pd.DataFrame({"hm0": [1.0]*4, "te": [2.0]*4, "tp": [2.0]*4, "flux": [10.0]*4}, index=idx)
    m = parse_power_matrix("Hm0\\Te, 2, 4\n1, 100, 100\n2, 100, 100")
    res = assess_device(frame, m, name="test", period_type="te")
    assert res["indexings"]["te"]["bin"]["aep_mwh"] == 100.0 * 8766 / 1000

def test_warnings():
    idx = pd.date_range("2020-01-01", periods=4, freq="6h")
    frame = pd.DataFrame({"hm0": [10.0]*4, "te": [20.0]*4, "tp": [20.0]*4, "flux": [10.0]*4}, index=idx)
    m = parse_power_matrix("Hm0\\Te, 2, 4\n1, 10, 10\n2, 10, 10")
    res = assess_device(frame, m, name="test", period_type="te")
    w = res["warnings"]
    assert any("AEP from a partial record is not an annual estimate" in x for x in w)
    assert any("Fewer than 12 calendar months" in x for x in w)
    assert any("More than 20% of valid records are outside" in x for x in w)

def test_api(client):
    make_job("eeeeeeeeeea1", "wave-spectra", build_b)
    
    csv_text = example_matrix_csv()
    payload = {
        "name": "Test Device 1",
        "csv_text": csv_text,
        "rated_kw": "100",
        "width_m": "10",
        "period_type": "unknown",
        "bin_convention": "centres"
    }
    
    res = client.post("/api/jobs/eeeeeeeeeea1/device", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["name"] == "Test Device 1"
    
    res2 = client.get("/api/jobs/eeeeeeeeeea1/device")
    assert res2.status_code == 200
    devices = res2.get_json()["devices"]
    assert len(devices) == 1
    assert devices[0]["slug"] == "test-device-1"
    
    res3 = client.get("/api/jobs/eeeeeeeeeea1/device/test-device-1")
    assert res3.status_code == 200
    assert res3.get_json()["name"] == "Test Device 1"
    
    # check report fragment
    job_dir = appmodule.DOWNLOADS / "eeeeeeeeeea1"
    rep_file = job_dir / "report_sections" / "30_device_test-device-1.md"
    assert rep_file.is_file()
    
    res4 = client.delete("/api/jobs/eeeeeeeeeea1/device/test-device-1")
    assert res4.status_code == 204
    assert not rep_file.is_file()
    
    res5 = client.get("/api/jobs/eeeeeeeeeea1/device/test-device-1")
    assert res5.status_code == 404
    
    # Test validation errors
    res6 = client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "", "csv_text": "a"})
    assert res6.status_code == 422
    
    res7 = client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "a", "csv_text": "a" * (200*1024 + 1)})
    assert res7.status_code == 422

def test_regression_security(client):
    make_job("eeeeeeeeeea1", "wave-spectra", build_b)
    
    for slug in ["..", "a..b", "x\\y", "%2e%2e", "Test", "a"*61]:
        res = client.get(f"/api/jobs/eeeeeeeeeea1/device/{slug}")
        assert res.status_code == 404
        res2 = client.delete(f"/api/jobs/eeeeeeeeeea1/device/{slug}")
        assert res2.status_code == 404

def test_regression_crash_zero_valid(client):
    idx = pd.date_range("2020-01-01", periods=1, freq="6h")
    frame = pd.DataFrame({"hm0": [1.0], "te": [2.0], "tp": [np.nan], "flux": [10.0]}, index=idx)
    frame.attrs["time_step_hours"] = 6.0
    m = parse_power_matrix("Hm0\\Te, 2, 4\n1, 10, 20\n2, 30, 40")
    
    res = assess_device(frame, m, name="test", period_type="unknown")
    assert res["indexings"]["tp"]["bin"]["records_valid"] == 0
    assert res["indexings"]["tp"]["bin"]["aep_mwh"] is None
    assert "ambiguity" not in res
    assert any("No valid records for Tp indexing" in w for w in res["warnings"])
    
    frame2 = pd.DataFrame({"hm0": pd.Series([], dtype=float), "te": pd.Series([], dtype=float), "tp": pd.Series([], dtype=float), "flux": pd.Series([], dtype=float)}, index=pd.DatetimeIndex([]))
    frame2.attrs["time_step_hours"] = 6.0
    res2 = assess_device(frame2, m, name="test", period_type="unknown")
    assert res2["indexings"]["tp"]["bin"]["aep_mwh"] is None
    
    frame3 = pd.DataFrame({"hm0": [1.0], "te": [2.0], "tp": [2.0], "flux": [np.nan]}, index=idx)
    frame3.attrs["time_step_hours"] = 6.0
    res3 = assess_device(frame3, m, name="test", period_type="unknown")
    assert res3["indexings"]["tp"]["bin"]["aep_mwh"] is None
    
    make_job("eeeeeeeeeea2", "wave-spectra", build_b)
    res_post = client.post("/api/jobs/eeeeeeeeeea2/device", json={"name": "test", "csv_text": example_matrix_csv()})
    assert res_post.status_code == 200

def test_regression_gappy_frame():
    idx = pd.DatetimeIndex(["2020-01-01 00:00:00", "2020-01-01 06:00:00", "2020-01-01 12:00:00", "2020-01-02 00:00:00"])
    frame = pd.DataFrame({"hm0": [1.0]*4, "te": [2.0]*4, "tp": [2.0]*4, "flux": [10.0]*4}, index=idx)
    frame.attrs["time_step_hours"] = 6.0
    m = parse_power_matrix("Hm0\\Te, 2, 4\n1, 10, 20\n2, 30, 40")
    res = assess_device(frame, m, name="test")
    # Span is 24 hours. Expected records at median step 6 = 24/6 + 1 = 5.
    # Actual records = 4.
    assert res["record"]["coverage_pct"] == 80.0

def test_regression_input_hardening(client):
    make_job("eeeeeeeeeea1", "wave-spectra", build_b)
    
    assert client.post("/api/jobs/eeeeeeeeeea1/device", data="not json").status_code == 422
    assert client.post("/api/jobs/eeeeeeeeeea1/device", json=["not a dict"]).status_code == 422
    assert client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": 123, "csv_text": example_matrix_csv()}).status_code == 422
    assert client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "test", "csv_text": 123}).status_code == 422
    assert client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "test", "csv_text": example_matrix_csv(), "rated_kw": "abc"}).status_code == 422
    assert client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "test", "csv_text": example_matrix_csv(), "rated_kw": "nan"}).status_code == 422
    assert client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "test", "csv_text": example_matrix_csv(), "rated_kw": "-5"}).status_code == 422
    
    res = client.post("/api/jobs/eeeeeeeeeea1/device", json={"name": "a\nb|c", "csv_text": example_matrix_csv()})
    assert res.status_code == 200
    assert res.get_json()["name"] == "ab|c"
    
    job_dir = appmodule.DOWNLOADS / "eeeeeeeeeea1"
    rep_file = job_dir / "report_sections" / "30_device_ab-c.md"
    assert rep_file.is_file()
    assert "ab\\|c" in rep_file.read_text()
