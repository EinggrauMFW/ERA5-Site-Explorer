"""Option A (single levels) and Option B (2D spectra) analysis on synthetic ERA5-style files."""

import zipfile

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import analysis
import wavecalc as wc

DIMS = ("valid_time", "latitude", "longitude")
FREQS, DFREQ, DTHETA, DIR_TO, DIR_FROM = wc.spectra_axes()


def wave_dataset(hours=48, land_cell=(1, 1), te=10.0, extra=None):
    """3x3 wave grid (ERA5 latitudes descend) whose middle cell is land (all NaN)."""
    lats, lons = [1.0, 0.5, 0.0], [95.0, 95.5, 96.0]
    times = pd.date_range("2020-04-01", periods=hours, freq="h")
    shape = (hours, 3, 3)
    swh = np.full(shape, 2.0) + np.arange(hours)[:, None, None] * 0.01
    pp1d = np.full(shape, 12.0)
    mwp = np.full(shape, te)
    mwd = np.full(shape, 350.0)
    mwd[1::2] = 10.0  # alternates 350 / 10: the circular mean is north, not 180
    for array in (swh, pp1d, mwp, mwd):
        array[:, land_cell[0], land_cell[1]] = np.nan
    variables = {"swh": (DIMS, swh), "pp1d": (DIMS, pp1d), "mwp": (DIMS, mwp), "mwd": (DIMS, mwd)}
    variables.update(extra or {})
    return xr.Dataset(variables, coords={"valid_time": times, "latitude": lats, "longitude": lons})


def wind_dataset(hours=48):
    lats = np.arange(1.0, -0.01, -0.25)
    lons = np.arange(95.0, 96.01, 0.25)
    times = pd.date_range("2020-04-01", periods=hours, freq="h")
    shape = (hours, len(lats), len(lons))
    return xr.Dataset(
        {"u10": (DIMS, np.full(shape, 3.0)), "v10": (DIMS, np.full(shape, 4.0))},
        coords={"valid_time": times, "latitude": lats, "longitude": lons},
    )


def bathymetry_file(path, depth=1500.0):
    lats, lons = [1.0, 0.5, 0.0], [95.0, 95.5, 96.0]
    xr.Dataset({"wmb": (("latitude", "longitude"), np.full((3, 3), depth))},
               coords={"latitude": lats, "longitude": lons}).to_netcdf(path)


@pytest.fixture
def zipped(tmp_path):
    wave, wind = tmp_path / "wave.nc", tmp_path / "oper.nc"
    wave_dataset().to_netcdf(wave)
    wind_dataset().to_netcdf(wind)
    archive = tmp_path / "era5_2020-04.nc"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(wave, "data_stream-wave_stepType-instant.nc")
        handle.write(wind, "data_stream-oper_stepType-instant.nc")
    return archive


def run(files, lat, lon, product="single-levels"):
    payload, frame = analysis.analyse(files, lat, lon, product)
    return payload, frame


def section(payload, title_part):
    return next(s for s in payload["sections"] if title_part in s["title"])


# --- Option A ------------------------------------------------------------------

def test_circular_mean_wraps_around_north():
    mean = analysis.circular_mean([350, 10])
    assert min(mean, 360 - mean) == pytest.approx(0, abs=1e-6)
    assert analysis.circular_mean([90, 90]) == pytest.approx(90)
    assert analysis.circular_mean([np.nan]) is None


def test_land_cell_falls_back_to_nearest_ocean_cell(tmp_path):
    path = tmp_path / "wave.nc"
    wave_dataset().to_netcdf(path)
    payload, _ = run([path], 0.5, 95.5)  # exactly on the land cell
    assert payload["moved_to_ocean"] is True
    grid = payload["grid_coordinate"]
    assert (grid["latitude"], grid["longitude"]) != (0.5, 95.5)
    assert payload["grid_distance_km"] > 0 and payload["coverage"] == 1.0


def test_ocean_point_is_not_moved(tmp_path):
    path = tmp_path / "wave.nc"
    wave_dataset().to_netcdf(path)
    payload, _ = run([path], 0.0, 95.0)
    assert payload["moved_to_ocean"] is False
    assert payload["grid_coordinate"] == {"latitude": 0.0, "longitude": 95.0}


def test_flux_uses_mwp_as_te_never_peak_period(zipped):
    payload, frame = run([zipped], 0.0, 95.0)
    hm0 = 2.0 + np.arange(48) * 0.01
    expected = wc.FLUX_COEFFICIENT * hm0**2 * 10.0   # Te = mwp = 10 s, not 0.9 * pp1d = 10.8 s
    assert np.allclose(frame["j"].to_numpy(), expected)
    # J is averaged per record, not computed from the mean Hm0 and mean Te
    assert payload["series"]["j"]["mean"] == pytest.approx(expected.mean(), rel=1e-4)
    assert payload["series"]["j"]["mean"] > wc.FLUX_COEFFICIENT * hm0.mean() ** 2 * 10.0
    assert payload["series"]["wind"]["mean"] == pytest.approx(5.0)  # hypot(3, 4)
    assert payload["atmos_grid_coordinate"] == {"latitude": 0.0, "longitude": 95.0}


def test_no_mwp_means_no_flux_and_a_warning(tmp_path):
    path = tmp_path / "wave.nc"
    ds = wave_dataset().drop_vars("mwp")
    ds.to_netcdf(path)
    payload, frame = run([path], 0.0, 95.0)
    assert "j" not in frame.columns and "j" not in payload["series"]
    assert any("pp1d is deliberately not used" in w for w in payload["warnings"])


def test_direction_uses_circular_statistics(zipped):
    mwd = run([zipped], 0.0, 95.0)[0]["series"]["mwd"]
    assert mwd["circular"] and mwd["p95"] is None and mwd["maximum"] is None
    assert min(mwd["mean"], 360 - mwd["mean"]) < 1  # near north, not 180


def test_scatter_and_rose_sections_are_present(zipped):
    payload, _ = run([zipped], 0.0, 95.0)
    scatter = section(payload, "Scatter diagram Hm0 vs Te")
    assert "half-open" in scatter["note"] and scatter["x_label"] == "Hm0 (m)"
    energy = np.array(scatter["energy_pct"])
    assert energy.sum() == pytest.approx(100, abs=0.1)
    rose = section(payload, "Wave rose")
    assert len(rose["sector_labels"]) == 16 and sum(rose["series"][0]["values"]) == pytest.approx(100, abs=0.1)


def test_record_length_warning_for_short_records(zipped):
    payload, _ = run([zipped], 0.0, 95.0)
    assert any("demonstration, not a resource estimate" in w for w in payload["warnings"])


def test_deep_water_validity_uses_model_bathymetry(tmp_path):
    wave = tmp_path / "era5_2020-04.nc"
    wave_dataset(te=10.0).to_netcdf(wave)
    bathymetry_file(tmp_path / "era5_bathymetry.nc", depth=30.0)   # L0/2 for 10 s is 78 m
    payload, _ = run([wave, tmp_path / "era5_bathymetry.nc"], 0.0, 95.0)
    assert payload["depth_m"] == 30.0
    assert any("below L0/2" in w for w in payload["warnings"])
    deep = tmp_path / "deep"
    deep.mkdir()
    wave_dataset(te=10.0).to_netcdf(deep / "era5_2020-04.nc")
    bathymetry_file(deep / "era5_bathymetry.nc", depth=2000.0)
    payload, _ = run([deep / "era5_2020-04.nc", deep / "era5_bathymetry.nc"], 0.0, 95.0)
    assert not any("below L0/2" in w for w in payload["warnings"])


def test_composition_qc_and_energy_share(tmp_path):
    path = tmp_path / "wave.nc"
    shape = (48, 3, 3)
    ds = wave_dataset(extra={
        "shww": (DIMS, np.full(shape, 1.0)),
        "shts": (DIMS, np.sqrt(np.maximum((2.0 + np.arange(48)[:, None, None] * 0.01) ** 2 - 1.0, 0) * np.ones(shape))),
        "mpww": (DIMS, np.full(shape, 6.0)),
        "mpts": (DIMS, np.full(shape, 12.0)),
    })
    ds.to_netcdf(path)
    payload, _ = run([path], 0.0, 95.0)
    composition = section(payload, "Sea-state composition")
    labels = {r["label"]: r["value"] for r in composition["rows"]}
    assert "100.0% of records within 5%" in labels["QC: swh² ≈ shww² + shts²"]
    assert labels["Swell-dominated hours (shts² > shww²)"].startswith("100.0")
    assert float(labels["Mean swell share of energy"].split()[0]) == pytest.approx(88.7, abs=0.5)


def test_ordering_qc_flags_wrong_variables(tmp_path):
    path = tmp_path / "wave.nc"
    shape = (48, 3, 3)
    wave_dataset(extra={"mp1": (DIMS, np.full(shape, 9.0)), "mp2": (DIMS, np.full(shape, 12.0))}).to_netcdf(path)
    payload, _ = run([path], 0.0, 95.0)
    assert any("break Tm02 ≤ Tm01 ≤ Te" in w for w in payload["warnings"])


def test_hourly_vs_six_hourly_sampling_check(tmp_path):
    path = tmp_path / "wave.nc"
    wave_dataset(hours=72).to_netcdf(path)
    payload, _ = run([path], 0.0, 95.0)
    assert "Sampling check" in " ".join(s["title"] for s in payload["sections"])


def test_zip_with_wave_and_wind_streams_reads_both_grids(zipped):
    payload, _ = run([zipped], 0.0, 95.0)
    assert payload["points"] == 48 and payload["stride"] == 1


def test_multiple_files_are_merged_sorted_and_deduplicated(tmp_path):
    first, second = tmp_path / "a.nc", tmp_path / "b.nc"
    wave_dataset(48).to_netcdf(first)
    later = wave_dataset(48).assign_coords(valid_time=pd.date_range("2020-04-02", periods=48, freq="h"))
    later.to_netcdf(second)
    payload, _ = run([second, first], 0.0, 95.0)  # given out of order
    assert payload["points"] == 72  # 24 h overlap removed
    assert payload["start"].startswith("2020-04-01") and payload["end"].startswith("2020-04-03")


def test_downsampling_keeps_block_maxima(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "MAX_DISPLAY_POINTS", 10)
    path = tmp_path / "wave.nc"
    ds = wave_dataset(40)
    ds["swh"][7, :, :] = 9.0  # a single-hour peak
    ds.to_netcdf(path)
    payload, _ = run([path], 0.0, 95.0)
    series = payload["series"]["swh"]
    assert payload["stride"] == 4 and len(payload["times"]) == 10
    assert series["maximum"] == 9.0 and max(series["max"]) == 9.0  # the peak survives
    assert max(series["values"]) < 9.0  # block means are smoothed


def test_wind_only_download_is_analysed_without_waves(tmp_path):
    path = tmp_path / "wind.nc"
    wind_dataset().to_netcdf(path)
    payload, _ = run([path], 0.0, 95.0)
    assert payload["order"] == ["wind"] and "j" not in payload["series"]


def test_surface_fields_are_converted_to_display_units(tmp_path):
    path = tmp_path / "surface.nc"
    ds = wind_dataset()
    ds["t2m"] = ds["u10"] * 0 + 300.0       # kelvin
    ds["msl"] = ds["u10"] * 0 + 101300.0    # pascal
    ds.to_netcdf(path)
    series = run([path], 0.0, 95.0)[0]["series"]
    assert series["t2m"]["mean"] == pytest.approx(26.85) and series["t2m"]["unit"] == "°C"
    assert series["msl"]["mean"] == pytest.approx(1013.0)


# --- Option B ------------------------------------------------------------------

def spectra_dataset(hours=4, land=False, hm0=2.0, fp=0.1, dir_to_peak=52.5):
    """ERA5-style log10 spectra using the dimension names CDS writes (JONSWAP, one direction)."""
    lats, lons = [1.0, 0.5, 0.0], [95.0, 95.5, 96.0]
    times = pd.date_range("2020-04-01", periods=hours, freq="6h")
    shape = wc.jonswap_shape(FREQS, fp, 3.3)
    e_f = shape * (hm0 / 4) ** 2 / (shape * DFREQ).sum()
    weights = np.exp(-0.5 * ((DIR_TO - dir_to_peak) / 20) ** 2)
    weights = weights / weights.sum() / DTHETA
    density = weights[:, None] * e_f[None, :]
    log_density = np.log10(np.where(density > 1e-6, density, np.nan))
    cube = np.broadcast_to(log_density[None, :, :, None, None], (hours, 24, 30, 3, 3)).astype("float32").copy()
    if land:
        cube[:, :, :, 1, 1] = np.nan
    return xr.Dataset(
        {"d2fd": (("valid_time", "directionNumber", "frequencyNumber", "latitude", "longitude"), cube)},
        coords={"valid_time": times, "directionNumber": np.arange(1, 25),
                "frequencyNumber": np.arange(1, 31), "latitude": lats, "longitude": lons},
    )


def test_analyse_spectra_end_to_end(tmp_path):
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset().to_netcdf(path)
    payload, frame = run([path], 0.0, 95.0, "wave-spectra")
    assert payload["route"] == "wave-spectra" and payload["points"] == 4
    assert payload["order"][:2] == ["hm0", "te"] and "flux" in payload["series"]
    assert payload["series"]["hm0"]["mean"] == pytest.approx(2.0, rel=0.03)  # log10 round trip, 1e-6 floor
    flux = payload["series"]["flux"]["mean"]
    assert flux == pytest.approx(payload["series"]["flux_deep"]["mean"], rel=0.005)  # deep-water identity
    assert payload["series"]["dm_from"]["circular"]
    assert payload["series"]["dm_from"]["mean"] == pytest.approx((52.5 + 180) % 360, abs=3)
    assert payload["series"]["theta_j_from"]["mean"] == pytest.approx((52.5 + 180) % 360, abs=3)
    assert frame["flux_dir_03"].notna().all()


def test_spectra_finite_depth_changes_flux_and_is_reported(tmp_path):
    deep, shallow = tmp_path / "deep", tmp_path / "shallow"
    for folder, depth in ((deep, None), (shallow, 20.0)):
        folder.mkdir()
        spectra_dataset(fp=0.08).to_netcdf(folder / "era5-spectra_2020-04.nc")
        if depth:
            bathymetry_file(folder / "era5_bathymetry.nc", depth)
    files = lambda folder: sorted(folder.glob("*.nc"))  # noqa: E731
    deep_payload, _ = run(files(deep), 0.0, 95.0, "wave-spectra")
    shallow_payload, _ = run(files(shallow), 0.0, 95.0, "wave-spectra")
    assert deep_payload["depth_m"] is None and shallow_payload["depth_m"] == 20.0
    assert shallow_payload["series"]["flux"]["mean"] != pytest.approx(deep_payload["series"]["flux"]["mean"], rel=0.01)
    assert any("deep-water" in n for n in deep_payload["notes"])
    assert section(shallow_payload, "Energy flux")["title"].startswith("Energy flux (finite-depth")


def test_spectra_land_cell_moves_to_ocean(tmp_path):
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset(land=True).to_netcdf(path)
    payload, _ = run([path], 0.5, 95.5, "wave-spectra")
    assert payload["moved_to_ocean"] is True and payload["grid_distance_km"] > 0


def test_spectra_download_without_spectra_variable_errors(tmp_path):
    path = tmp_path / "wind.nc"
    wind_dataset().to_netcdf(path)
    with pytest.raises(ValueError, match="2D wave-spectra"):
        run([path], 0.0, 95.0, "wave-spectra")


def test_sector_flux_from_the_spectrum(tmp_path):
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset(dir_to_peak=52.5).to_netcdf(path)
    _, frame = run([path], 0.0, 95.0, "wave-spectra")
    on_heading = analysis.sector_section(frame, (52.5 + 180) % 360, 45.0)
    off_heading = analysis.sector_section(frame, 52.5, 22.5)  # the opposite side of the compass
    inside = float(on_heading["rows"][0]["value"].split()[0])
    outside = float(off_heading["rows"][0]["value"].split()[0])
    assert inside > 95 and outside < 1
    assert float(on_heading["rows"][1]["value"].split()[0]) == pytest.approx(100 - inside, abs=0.2)


def test_partitions_are_additive_and_resolve_two_systems(tmp_path):
    pytest.importorskip("wavespectra")
    freqs, dfreq, dtheta, dir_to, _ = wc.spectra_axes()
    swell = wc.jonswap_shape(freqs, 0.07, 6.0)
    sea = wc.jonswap_shape(freqs, 0.22, 2.0)
    e_swell = swell * (2.0 / 4) ** 2 / (swell * dfreq).sum()
    e_sea = sea * (1.0 / 4) ** 2 / (sea * dfreq).sum()

    def spread(centre, width):
        w = np.exp(-0.5 * (np.abs((dir_to - centre + 180) % 360 - 180) / width) ** 2)
        return w / w.sum() / dtheta

    density = (spread(60, 15)[:, None] * e_swell[None, :] + spread(200, 25)[:, None] * e_sea[None, :])[None]
    total = wc.spectral_bulk(density, freqs, dfreq, dtheta, dir_to)
    parts = analysis.partition_spectra(density, freqs, dfreq, dtheta, dir_to, None)
    assert parts is not None and len(parts) == 3
    assert parts[0]["hm0"][0] > parts[1]["hm0"][0] > 0.5        # two systems, ordered by Hm0
    assert parts[0]["hm0"][0] == pytest.approx(2.0, rel=0.1) and parts[1]["hm0"][0] == pytest.approx(1.0, rel=0.15)
    summed = sum(np.nan_to_num(p["flux"][0]) for p in parts)
    assert summed == pytest.approx(total["flux"][0], rel=0.03)    # moments add over disjoint parts


def test_hm0_tm01_tm02_match_wavespectra_on_the_same_file(tmp_path):
    ws = pytest.importorskip("wavespectra")
    path = tmp_path / "era5-spectra_2020-04.nc"
    spectra_dataset(hours=3).to_netcdf(path)
    reference = ws.read_era5(str(path)).isel(lat=0, lon=0)
    _, frame = run([path], 1.0, 95.0, "wave-spectra")
    assert np.allclose(frame["hm0"], reference.spec.hs(tail=False).values, rtol=1e-6)
    assert np.allclose(frame["tm01"], reference.spec.tm01().values, rtol=1e-6)
    assert np.allclose(frame["tm02"], reference.spec.tm02().values, rtol=1e-6)
    # wavespectra converts going-to to coming-from in read_era5: first bin 7.5 -> 187.5
    assert float(reference.dir[0]) == pytest.approx(187.5)
