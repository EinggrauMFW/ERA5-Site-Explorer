"""Wave-spectrum and energy-flux physics shared by both analysis routes.

Pure numpy, no I/O. Definitions follow ECMWF "wave parameters" (J.-R. Bidlot):

    Hm0  = 4 sqrt(m0)               ERA5 swh
    Te   = Tm-1 = m-1 / m0          ERA5 mwp (param 140232), the energy period
    Tm01 = m0 / m1                  ERA5 mp1 (140220)
    Tm02 = sqrt(m0 / m2)            ERA5 mp2 (140221), zero-crossing period
    Tp   = 1 / fp                   ERA5 pp1d (140231), parabolic fit around the maximum

with m_n = integral f^n E(f) df and E(f) = integral E(f, theta) dtheta.
Deep-water flux: J = rho g^2 / (64 pi) Hm0^2 Te.

Direction conventions: ERA5 single-level ``mwd`` is coming-from (meteorological,
0 = from north, 90 = from east). The ERA5 2D spectra direction bins are
going-to (oceanographic). Everything in this module that returns a direction
for display returns coming-from, via ``to_from``.
"""

from __future__ import annotations

import numpy as np

RHO = 1025.0   # seawater density, kg/m3
G = 9.81       # gravity, m/s2
# kW per metre of crest for Hm0 in m and Te in s: rho g^2 / (64 pi) / 1000 = 0.4906
FLUX_COEFFICIENT = RHO * G**2 / (64 * np.pi) / 1000.0

# ERA5 2D spectra grid: f_n = 0.03453 * 1.1**n (n = 0..29); 24 bins of 15 degrees.
SPECTRA_F0 = 0.03453
SPECTRA_RATIO = 1.1
SPECTRA_N_FREQ = 30
SPECTRA_N_DIR = 24


def deep_water_flux(hm0, te):
    """Deep-water energy flux in kW/m from Hm0 [m] and Te [s]."""
    return FLUX_COEFFICIENT * np.square(hm0) * te


# --- directions ----------------------------------------------------------------

def to_from(degrees):
    """Convert going-to <-> coming-from (the operation is its own inverse)."""
    return (np.asarray(degrees, dtype=float) + 180.0) % 360.0


def vector_mean_direction(degrees, weights=None, axis=None):
    """Weighted vector mean of directions in degrees, in [0, 360). Never an arithmetic mean."""
    radians = np.radians(np.asarray(degrees, dtype=float))
    weights = np.ones_like(radians) if weights is None else np.asarray(weights, dtype=float)
    sine = np.sum(weights * np.sin(radians), axis=axis)
    cosine = np.sum(weights * np.cos(radians), axis=axis)
    return np.degrees(np.arctan2(sine, cosine)) % 360.0


def angular_difference(a, b):
    """Signed smallest difference a - b in degrees, in (-180, 180]."""
    diff = (np.asarray(a, dtype=float) - np.asarray(b, dtype=float) + 180.0) % 360.0 - 180.0
    return np.where(diff == -180.0, 180.0, diff)


# --- spectral grid -------------------------------------------------------------

def frequency_widths(freqs, method="gradient"):
    """Frequency bin widths Df in Hz. One helper, so the choice is stated in one place.

    ``gradient`` (default) is ``np.gradient(freqs)``, the same convention as the wavespectra
    package, so Hm0, Tm01 and Tm02 match it. ``geometric`` uses the exact bin edges of a
    geometric grid, f * (r^0.5 - r^-0.5). The two differ by about 0.1% in the bin widths and
    about 0.06% in Hm0 on a typical spectrum.
    """
    freqs = np.asarray(freqs, dtype=float)
    if method == "gradient":
        return np.gradient(freqs)
    if method == "geometric":
        ratio = freqs[1] / freqs[0]
        return freqs * (ratio**0.5 - ratio**-0.5)
    raise ValueError("method must be 'gradient' or 'geometric'")


def spectra_axes(n_freq=SPECTRA_N_FREQ, n_dir=SPECTRA_N_DIR, f0=SPECTRA_F0, ratio=SPECTRA_RATIO,
                 df_method="gradient"):
    """Return (freqs Hz, dfreq Hz, dtheta rad, dir_to deg, dir_from deg)."""
    freqs = f0 * ratio ** np.arange(n_freq)
    dfreq = frequency_widths(freqs, df_method)
    dtheta = 2 * np.pi / n_dir
    dir_to = (np.arange(n_dir) + 0.5) * 360.0 / n_dir
    return freqs, dfreq, dtheta, dir_to, to_from(dir_to)


def decode_log10(values):
    """ERA5 stores log10 of E in m2 s/rad. Missing bins (below the encoding floor) become 0."""
    values = np.asarray(values, dtype=float)
    return np.where(np.isfinite(values), 10.0**values, 0.0)


# --- finite-depth linear wave theory -------------------------------------------

def wavenumber(freqs, depth=None):
    """Solve omega^2 = g k tanh(k h) for k [rad/m]; ``depth`` None or inf means deep water."""
    omega = 2 * np.pi * np.asarray(freqs, dtype=float)
    k0 = omega**2 / G
    if depth is None or not np.isfinite(depth) or depth <= 0:
        return k0
    k = k0 / np.sqrt(np.tanh(k0 * depth))  # Eckart's starting guess, refined by Newton
    for _ in range(12):
        t = np.tanh(k * depth)
        value = G * k * t - omega**2
        slope = G * t + G * k * depth * (1 - t**2)
        k = np.maximum(k - value / slope, 1e-12)
    return k


def group_velocity(freqs, depth=None):
    """Linear-theory group velocity in m/s: g/(4 pi f) in deep water, sqrt(g h) in the shallow limit."""
    freqs = np.asarray(freqs, dtype=float)
    if depth is None or not np.isfinite(depth) or depth <= 0:
        return G / (4 * np.pi * freqs)
    k = wavenumber(freqs, depth)
    omega = 2 * np.pi * freqs
    kh2 = np.minimum(2 * k * depth, 700.0)
    return 0.5 * (omega / k) * (1 + kh2 / np.sinh(kh2))


def deep_water_wavelength(te):
    """L0 = g Te^2 / (2 pi) in metres."""
    return G * np.square(te) / (2 * np.pi)


# --- spectral reduction --------------------------------------------------------

def parabolic_peak_period(e_f, freqs):
    """Peak period [s] from a parabola through the maximum of E(f) and its two neighbours.

    Falls back to the bin centre when the maximum is at either end of the axis
    or the three points are degenerate. ``e_f`` has shape (time, freq).
    """
    e_f = np.atleast_2d(e_f)
    steps = np.arange(e_f.shape[0])
    peak = e_f.argmax(axis=1)
    tp = 1.0 / freqs[peak]
    inner = (peak > 0) & (peak < len(freqs) - 1) & (e_f.max(axis=1) > 0)
    i = steps[inner]
    p = peak[inner]
    x1, x2, x3 = freqs[p - 1], freqs[p], freqs[p + 1]
    y1, y2, y3 = e_f[i, p - 1], e_f[i, p], e_f[i, p + 1]
    numerator = (x2 - x1) ** 2 * (y2 - y3) - (x2 - x3) ** 2 * (y2 - y1)
    denominator = (x2 - x1) * (y2 - y3) - (x2 - x3) * (y2 - y1)
    with np.errstate(divide="ignore", invalid="ignore"):
        vertex = x2 - 0.5 * numerator / denominator
    good = np.isfinite(vertex) & (vertex > x1) & (vertex < x3)
    refined = tp[inner]
    refined[good] = 1.0 / vertex[good]
    tp[inner] = refined
    return tp


def spectral_bulk(density, freqs, dfreq, dtheta, dir_to, depth=None) -> dict:
    """Reduce spectra shaped (time, direction, frequency), in m2 s/rad, to bulk parameters.

    Returns arrays of length ``time``. ``flux_dir`` is (time, direction): the flux
    carried by each direction bin, labelled by the going-to ``dir_to`` it came in.
    Spectra with no energy (land, ice) give NaN, not zero.
    """
    density = np.asarray(density, dtype=float)
    steps = density.shape[0]
    theta = np.radians(dir_to)

    e_f = density.sum(axis=1) * dtheta  # (time, freq), m2/Hz
    m0 = (e_f * dfreq).sum(axis=1)
    m1 = (e_f * freqs * dfreq).sum(axis=1)
    m2 = (e_f * freqs**2 * dfreq).sum(axis=1)
    m_1 = (e_f / freqs * dfreq).sum(axis=1)
    m_2 = (e_f / freqs**2 * dfreq).sum(axis=1)
    valid = m0 > 0
    nan = np.full(steps, np.nan)

    def where(values):
        return np.where(valid, values, nan)

    with np.errstate(divide="ignore", invalid="ignore"):
        hm0 = where(4 * np.sqrt(m0))
        te = where(m_1 / m0)
        tm01 = where(m0 / m1)
        tm02 = where(np.sqrt(m0 / m2))
        eps0 = where(np.sqrt(np.maximum(m0 * m_2 / m_1**2 - 1.0, 0.0)))

    # Energy-weighted mean direction over the whole spectrum (ECMWF eq. 9), coming-from.
    weight = density * dfreq[None, None, :]
    sin_sum = (weight * np.sin(theta)[None, :, None]).sum(axis=(1, 2))
    cos_sum = (weight * np.cos(theta)[None, :, None]).sum(axis=(1, 2))
    dm_from = where(to_from(np.degrees(np.arctan2(sin_sum, cos_sum)) % 360.0))

    # ECMWF directional width (eq. 10-12): the mean direction is taken per frequency.
    sf = (density * np.sin(theta)[None, :, None]).sum(axis=1)
    cf = (density * np.cos(theta)[None, :, None]).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        m1_ratio = (np.hypot(sf, cf) * dfreq).sum(axis=1) / (density.sum(axis=1) * dfreq).sum(axis=1)
        wdw = where(np.sqrt(2 * np.maximum(1 - m1_ratio, 0.0)))

    # Energy flux per direction bin with finite-depth group velocity, kW/m.
    cg = group_velocity(freqs, depth)
    flux_dir = RHO * G * (density * (cg * dfreq)[None, None, :]).sum(axis=2) * dtheta / 1000.0
    flux_f = RHO * G * (density.sum(axis=1) * dtheta * cg * dfreq[None, :]) / 1000.0  # (time, freq)
    flux = flux_dir.sum(axis=1)
    flux_north = (flux_dir * np.cos(theta)[None, :]).sum(axis=1)
    flux_east = (flux_dir * np.sin(theta)[None, :]).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        directionality = np.hypot(flux_north, flux_east) / flux
    flux_valid = flux > 0
    theta_j_from = np.where(
        flux_valid, to_from(np.degrees(np.arctan2(flux_east, flux_north)) % 360.0), nan)

    tp_grid = where(1.0 / freqs[e_f.argmax(axis=1)])
    tp_parabolic = where(parabolic_peak_period(e_f, freqs))
    return {
        "hm0": hm0, "te": te, "tm01": tm01, "tm02": tm02, "eps0": eps0,
        "tp_grid": tp_grid, "tp_parabolic": tp_parabolic,
        "dm_from": dm_from, "wdw": wdw,
        "flux": np.where(flux_valid, flux, nan),
        "flux_deep": where(deep_water_flux(hm0, te)),
        "theta_j_from": theta_j_from,
        "directionality": np.where(flux_valid, directionality, nan),
        "flux_dir": np.where(flux_valid[:, None], flux_dir, np.nan),
        "flux_f": np.where(flux_valid[:, None], flux_f, np.nan),
        "m0": m0,
    }


# --- diagnostics ---------------------------------------------------------------

def jonswap_shape(freqs, fp, gamma):
    """Unscaled JONSWAP shape f^-5 exp(-5/4 (fp/f)^4) gamma^r (peak-normalised)."""
    freqs = np.asarray(freqs, dtype=float)
    sigma = np.where(freqs <= fp, 0.07, 0.09)
    r = np.exp(-((freqs - fp) ** 2) / (2 * sigma**2 * fp**2))
    shape = freqs**-5 * np.exp(-1.25 * (fp / freqs) ** 4) * gamma**r
    return shape / shape.max()


def fit_gamma(e_f, freqs, fp, gammas=None):
    """Least-squares JONSWAP peak-enhancement factor for each spectrum, as a diagnostic only.

    Compares the peak-normalised spectrum with peak-normalised JONSWAP shapes on
    0.7 fp .. 3 fp and returns the best gamma on a coarse grid (NaN if the peak
    sits at the edge of the axis). Resolution is limited by the 10% frequency steps.
    """
    gammas = np.arange(1.0, 10.01, 0.25) if gammas is None else np.asarray(gammas)
    e_f = np.atleast_2d(e_f)
    fp = np.atleast_1d(fp)
    result = np.full(e_f.shape[0], np.nan)
    for i in range(e_f.shape[0]):
        if not np.isfinite(fp[i]) or e_f[i].max() <= 0:
            continue
        band = (freqs >= 0.7 * fp[i]) & (freqs <= 3 * fp[i])
        if band.sum() < 5:
            continue
        observed = e_f[i, band] / e_f[i].max()
        errors = [np.sum((observed - jonswap_shape(freqs[band], fp[i], g) * observed.max()) ** 2)
                  for g in gammas]
        result[i] = gammas[int(np.argmin(errors))]
    return result


def ordering_violations(tm02, tm01, te, tolerance=0.01):
    """Count records breaking Tm02 <= Tm01 <= Te (Cauchy-Schwarz) beyond a relative tolerance."""
    tm02, tm01, te = (np.asarray(x, dtype=float) for x in (tm02, tm01, te))
    ok = np.isfinite(tm02) & np.isfinite(tm01) & np.isfinite(te)
    bad = ok & ((tm02 > tm01 * (1 + tolerance)) | (tm01 > te * (1 + tolerance)))
    return int(bad.sum()), int(ok.sum()), bad


def steepness(hm0, tm02):
    """Wave steepness 2 pi Hm0 / (g Tm02^2), dimensionless."""
    return 2 * np.pi * np.asarray(hm0, dtype=float) / (G * np.square(tm02))


# --- tables --------------------------------------------------------------------

def scatter_table(x, y, weights, x_edges, y_edges) -> dict:
    """2-D table on half-open bins [lower, upper). ``weights`` are summed per cell.

    Records outside the table or with non-finite values are counted, not dropped silently.
    """
    x, y, weights = (np.asarray(a, dtype=float) for a in (x, y, weights))
    x_edges, y_edges = np.asarray(x_edges, dtype=float), np.asarray(y_edges, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(weights)
    xi = np.searchsorted(x_edges, x, side="right") - 1
    yi = np.searchsorted(y_edges, y, side="right") - 1
    inside = finite & (xi >= 0) & (xi < len(x_edges) - 1) & (yi >= 0) & (yi < len(y_edges) - 1)
    counts = np.zeros((len(x_edges) - 1, len(y_edges) - 1))
    summed = np.zeros_like(counts)
    np.add.at(counts, (xi[inside], yi[inside]), 1)
    np.add.at(summed, (xi[inside], yi[inside]), weights[inside])
    return {"counts": counts, "weights": summed, "inside": int(inside.sum()),
            "outside": int(finite.sum() - inside.sum()), "invalid": int((~finite).sum())}


def sector_fraction(flux_dir, dir_from_centres, heading_from, half_width, bin_width=15.0):
    """Share of flux arriving within +-``half_width`` of ``heading_from`` (coming-from, degrees).

    ``flux_dir`` is (time, direction). Bins that straddle the sector edge are weighted
    by their overlap, which assumes flux is uniform inside a 15 degree bin.
    """
    offset = np.abs((np.asarray(dir_from_centres) - heading_from + 180.0) % 360.0 - 180.0)
    overlap = np.clip(bin_width / 2 + half_width - offset, 0.0, min(bin_width, 2 * half_width))
    weight = overlap / bin_width
    flux_dir = np.atleast_2d(flux_dir)
    total = flux_dir.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (flux_dir * weight[None, :]).sum(axis=1) / total
