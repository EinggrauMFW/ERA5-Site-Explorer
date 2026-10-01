"""Analytic checks for wavecalc: monochromatic waves, JONSWAP, finite depth, conventions."""

import numpy as np
import pytest

import wavecalc as wc

FREQS, DFREQ, DTHETA, DIR_TO, DIR_FROM = wc.spectra_axes()


def one_bin_spectrum(freq_index, dir_index, hm0, depth=None):
    """A spectrum with all energy in one frequency x direction bin carrying a given Hm0."""
    density = np.zeros((1, 24, 30))
    m0 = (hm0 / 4) ** 2
    density[0, dir_index, freq_index] = m0 / (DFREQ[freq_index] * DTHETA)
    return density


def jonswap_density(fp, hm0, gamma=3.3, directional=None):
    """JONSWAP E(f) scaled to Hm0, spread over 24 bins with ``directional`` weights."""
    shape = wc.jonswap_shape(FREQS, fp, gamma)
    e_f = shape * (hm0 / 4) ** 2 / (shape * DFREQ).sum()
    weights = np.zeros(24) if directional is None else np.asarray(directional, dtype=float)
    if directional is None:
        weights[0] = 1.0
    weights = weights / weights.sum() / DTHETA
    return (weights[None, :, None] * e_f[None, None, :])


def test_flux_coefficient_is_rho_g2_over_64pi():
    assert wc.FLUX_COEFFICIENT == pytest.approx(0.4906, abs=5e-5)


def test_frequency_axis_matches_ecmwf_description():
    assert FREQS[0] == pytest.approx(0.03453) and FREQS[-1] == pytest.approx(0.5478, abs=2e-4)
    assert 1 / FREQS[0] == pytest.approx(28.96, abs=0.01) and 1 / FREQS[-1] == pytest.approx(1.83, abs=0.01)
    assert DIR_TO[0] == 7.5 and np.allclose(np.diff(DIR_TO), 15) and DTHETA == pytest.approx(0.2618, abs=1e-4)


def test_monochromatic_wave_has_exact_height_period_and_flux():
    index = 12
    spectrum = one_bin_spectrum(index, 3, hm0=2.0)
    bulk = wc.spectral_bulk(spectrum, FREQS, DFREQ, DTHETA, DIR_TO)  # deep water
    period = 1 / FREQS[index]
    assert bulk["hm0"][0] == pytest.approx(2.0)
    for name in ("te", "tm01", "tm02", "tp_grid"):
        assert bulk[name][0] == pytest.approx(period)
    # flux = rho g^2 H^2 T / (32 pi) for a regular wave, with H = sqrt(8 m0) = Hm0 / sqrt(2) * 2 sqrt(2)... use Hm0 form
    assert bulk["flux"][0] == pytest.approx(wc.FLUX_COEFFICIENT * 2.0**2 * period, rel=1e-9)


def test_deep_water_identity_flux_equals_049_hm0_squared_te():
    spectrum = jonswap_density(fp=0.1, hm0=3.0, directional=np.exp(-0.5 * ((DIR_TO - 52.5) / 25) ** 2))
    bulk = wc.spectral_bulk(spectrum, FREQS, DFREQ, DTHETA, DIR_TO)
    assert bulk["flux"][0] == pytest.approx(bulk["flux_deep"][0], rel=1e-9)
    assert abs(bulk["flux"][0] / bulk["flux_deep"][0] - 1) < 0.005  # the < 0.5% acceptance check


def test_jonswap_te_over_tp_is_about_090():
    spectrum = jonswap_density(fp=0.1, hm0=3.0, gamma=3.3)
    bulk = wc.spectral_bulk(spectrum, FREQS, DFREQ, DTHETA, DIR_TO)
    assert bulk["te"][0] / bulk["tp_parabolic"][0] == pytest.approx(0.90, abs=0.03)
    assert bulk["hm0"][0] == pytest.approx(3.0, rel=1e-9)


def test_parabolic_peak_is_between_bins_and_beats_grid_peak():
    fp_true = 0.1
    spectrum = jonswap_density(fp=fp_true, hm0=2.0)
    e_f = spectrum.sum(axis=1) * DTHETA
    parabolic = 1 / wc.parabolic_peak_period(e_f, FREQS)[0]
    grid = FREQS[e_f.argmax()]
    assert abs(parabolic - fp_true) < abs(grid - fp_true)
    assert abs(parabolic - fp_true) / fp_true < 0.03


def test_peak_at_axis_edge_falls_back_to_bin_centre():
    e_f = np.zeros((1, 30))
    e_f[0, 0] = 1.0
    assert wc.parabolic_peak_period(e_f, FREQS)[0] == pytest.approx(1 / FREQS[0])


def test_gamma_fit_recovers_peak_enhancement_roughly():
    fp = 0.1
    e_f = jonswap_density(fp=fp, hm0=2.0, gamma=3.3).sum(axis=1) * DTHETA
    assert 2.0 <= wc.fit_gamma(e_f, FREQS, [fp])[0] <= 5.0
    flat = jonswap_density(fp=fp, hm0=2.0, gamma=1.0).sum(axis=1) * DTHETA
    assert wc.fit_gamma(flat, FREQS, [fp])[0] < wc.fit_gamma(e_f, FREQS, [fp])[0]


def test_moment_ordering_holds_for_random_spectra():
    rng = np.random.default_rng(7)
    spectra = rng.random((200, 24, 30)) ** 6  # spiky, non-negative
    spectra[rng.random(spectra.shape) < 0.5] = 0.0
    bulk = wc.spectral_bulk(spectra, FREQS, DFREQ, DTHETA, DIR_TO)
    bad, checked, _ = wc.ordering_violations(bulk["tm02"], bulk["tm01"], bulk["te"], tolerance=1e-9)
    assert checked == 200 and bad == 0


def test_ordering_violation_is_detected():
    bad, checked, flags = wc.ordering_violations([8.0, 12.0], [9.0, 9.0], [10.0, 10.0])
    assert (bad, checked) == (1, 2) and flags.tolist() == [False, True]


def test_direction_helpers_wrap_and_use_vector_mean():
    assert wc.to_from(7.5) == 187.5 and wc.to_from(300) == 120
    assert wc.vector_mean_direction([350, 10]) == pytest.approx(0, abs=1e-9) or \
        wc.vector_mean_direction([350, 10]) == pytest.approx(360, abs=1e-9)
    assert wc.vector_mean_direction([90, 90, 270], weights=[1, 1, 0.5]) == pytest.approx(90)
    assert wc.angular_difference(5, 355) == pytest.approx(10)
    assert wc.angular_difference(355, 5) == pytest.approx(-10)
    assert wc.angular_difference(0, 180) == 180


def test_spectrum_direction_is_reported_coming_from():
    spectrum = one_bin_spectrum(12, 3, hm0=1.0)  # going-to bin centre 52.5 degrees
    bulk = wc.spectral_bulk(spectrum, FREQS, DFREQ, DTHETA, DIR_TO)
    assert bulk["dm_from"][0] == pytest.approx(232.5)
    assert bulk["theta_j_from"][0] == pytest.approx(232.5)
    assert bulk["directionality"][0] == pytest.approx(1.0)
    assert bulk["wdw"][0] == pytest.approx(0.0, abs=1e-6)


def test_directional_width_of_a_uniform_spectrum_is_root_two():
    density = np.ones((1, 24, 30)) * 1e-3
    bulk = wc.spectral_bulk(density, FREQS, DFREQ, DTHETA, DIR_TO)
    assert bulk["wdw"][0] == pytest.approx(np.sqrt(2), abs=1e-6)
    assert bulk["directionality"][0] == pytest.approx(0.0, abs=1e-9)


def test_group_velocity_deep_and_shallow_limits():
    deep = wc.group_velocity(FREQS, depth=5000.0)
    assert np.allclose(deep, wc.G / (4 * np.pi * FREQS), rtol=1e-6)
    shallow = wc.group_velocity(np.array([0.02]), depth=2.0)
    assert shallow[0] == pytest.approx(np.sqrt(wc.G * 2.0), rel=0.02)
    assert wc.group_velocity(FREQS, depth=None) == pytest.approx(wc.G / (4 * np.pi * FREQS))


def test_dispersion_relation_is_satisfied():
    k = wc.wavenumber(FREQS, depth=30.0)
    omega = 2 * np.pi * FREQS
    assert np.allclose(omega**2, wc.G * k * np.tanh(k * 30.0), rtol=1e-9)


def test_finite_depth_changes_flux_for_long_periods_in_either_direction_allowed():
    spectrum = jonswap_density(fp=0.08, hm0=3.0)
    deep = wc.spectral_bulk(spectrum, FREQS, DFREQ, DTHETA, DIR_TO)["flux"][0]
    shallow = wc.spectral_bulk(spectrum, FREQS, DFREQ, DTHETA, DIR_TO, depth=25.0)["flux"][0]
    assert shallow != pytest.approx(deep, rel=0.01)


def test_empty_spectrum_is_nan_not_calm():
    bulk = wc.spectral_bulk(np.zeros((1, 24, 30)), FREQS, DFREQ, DTHETA, DIR_TO)
    assert all(np.isnan(bulk[name][0]) for name in ("hm0", "te", "tm01", "dm_from", "flux"))


def test_encoding_round_trip_and_missing_bins():
    original = np.array([0.5, 1e-4, 3.0])
    assert np.allclose(wc.decode_log10(np.log10(original)), original)
    assert wc.decode_log10([np.nan, -2.0]).tolist() == [0.0, 0.01]


def test_integration_is_in_radians_not_degrees():
    """Integrating over degrees instead of radians would inflate m0 by 180/pi."""
    bulk = wc.spectral_bulk(one_bin_spectrum(5, 0, hm0=1.0), FREQS, DFREQ, DTHETA, DIR_TO)
    assert bulk["hm0"][0] == pytest.approx(1.0)
    assert bulk["m0"][0] == pytest.approx(1 / 16)


def test_scatter_table_uses_half_open_bins_and_counts_outliers():
    table = wc.scatter_table([0.5, 0.49, 1.0, 9.0, np.nan], [1.0, 1.0, 2.5, 1.0, 1.0],
                             [1, 1, 1, 1, 1], [0, 0.5, 1.0, 1.5], [0, 2, 4])
    assert table["counts"][1, 0] == 1       # 0.5 falls in [0.5, 1.0), not [0, 0.5)
    assert table["counts"][0, 0] == 1       # 0.49
    assert table["counts"][2, 1] == 1       # 1.0 -> [1.0, 1.5); 2.5 -> [2, 4)
    assert table["outside"] == 1 and table["invalid"] == 1 and table["inside"] == 3


def test_sector_fraction_with_partial_bins():
    flux_dir = np.zeros((1, 24))
    flux_dir[0, 12] = 1.0                   # the bin centred on 187.5 (coming-from) after to_from
    centres = DIR_FROM
    assert wc.sector_fraction(flux_dir, centres, centres[12], 7.5)[0] == pytest.approx(1.0)
    assert wc.sector_fraction(flux_dir, centres, centres[12] + 90, 22.5)[0] == pytest.approx(0.0)
    half = wc.sector_fraction(flux_dir, centres, centres[12] + 7.5, 7.5)[0]
    assert half == pytest.approx(0.5)       # sector covers half of the 15 degree bin


def test_frequency_width_methods_are_stated_and_close():
    gradient = wc.frequency_widths(FREQS, "gradient")
    geometric = wc.frequency_widths(FREQS, "geometric")
    assert np.allclose(gradient, np.gradient(FREQS))          # the wavespectra convention
    assert np.allclose(gradient[1:-1] / geometric[1:-1], 1.0, atol=2e-3)
    spectrum = jonswap_density(fp=0.1, hm0=3.0)
    hm0 = [wc.spectral_bulk(spectrum, FREQS, wc.frequency_widths(FREQS, m), DTHETA, DIR_TO)["hm0"][0]
           for m in ("gradient", "geometric")]
    assert abs(hm0[0] / hm0[1] - 1) < 0.005                    # the choice moves Hm0 by well under 0.5%
    with pytest.raises(ValueError):
        wc.frequency_widths(FREQS, "trapezoid")
