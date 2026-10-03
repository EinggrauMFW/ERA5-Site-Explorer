# Numerical methods

This document explains how every number in the app is computed, in the order the data flows. Each section
names the function that does the work, so the text can be checked against the code. It describes what the code
does; it does not claim that each choice is the best one. Choices that are this app's own, and not taken from
a standard or a paper, are collected in [section 11](#11-choices-that-are-this-apps-own).

How it was checked: the formulas below are tested against hand-derived values on small synthetic inputs, and
the extreme-value code against a synthetic process with a known tail (`tests/`). What has *not* been checked
(real long records, real devices, Option A against Option B on real data, buoys) is listed in
[verification.md](verification.md). Read that file with this one.

## Contents

1. [Conventions](#1-conventions)
2. [From files to a per-record series](#2-from-files-to-a-per-record-series)
3. [Spectral reduction (Option B)](#3-spectral-reduction-option-b)
4. [Energy flux](#4-energy-flux)
5. [Integrated parameters and checks (Option A)](#5-integrated-parameters-and-checks-option-a)
6. [Tables, roses and climatologies](#6-tables-roses-and-climatologies)
7. [Grid nodes and screening](#7-grid-nodes-and-screening)
8. [Long-term statistics and extremes](#8-long-term-statistics-and-extremes)
9. [Device performance](#9-device-performance)
10. [Cross-check, Option A against Option B](#10-cross-check-option-a-against-option-b)
11. [Choices that are this app's own](#11-choices-that-are-this-apps-own)
12. [Known approximations](#12-known-approximations)

## 1. Conventions

- **Units.** Hm0 in m, periods in s, energy flux $J$ in kW per metre of wave crest (kW/m), power in kW,
  energy in MWh, directions in degrees true, clockwise.
- **Direction.** Everything shown is *coming-from* (0° = from north). ERA5 `mwd` is coming-from; the ERA5 2D
  spectra are stored going-to, and `to_from(θ) = (θ + 180°) mod 360°` converts them in one place
  (`wavecalc.to_from`).
- **Per record first.** A statistic of a nonlinear quantity is computed per record and then averaged. In
  particular $J$ is never computed from a mean Hm0 and a mean Te, because it is nonlinear in Hm0.
- **Rounding.** Values are rounded for display (usually three decimals) after every calculation. No index or
  statistic is computed from a rounded number.
- **Directions are averaged as vectors.** The mean of angles $\theta_i$ with weights $w_i$ is
  $\operatorname{atan2}(\sum w_i\sin\theta_i,\ \sum w_i\cos\theta_i)$ (`vector_mean_direction`). Percentiles
  and maxima of a bearing are not reported, because they have no meaning on a circle.
- **Percentiles** use NumPy's default linear interpolation. **Standard deviations** are population standard
  deviations ($\mathrm{ddof}=0$) throughout.
- **Missing values.** NaN means "not available" and is excluded from means; it is never treated as zero,
  except where a section says so explicitly (spectral bins, and device records outside the matrix).

## 2. From files to a per-record series

**Cell choice** (`analysis.pick_cell`). The requested site is mapped to the nearest ERA5 grid cell that holds
data. Distance is the haversine great-circle distance on a sphere of radius 6371 km:

$$d = 2R\arcsin\sqrt{\sin^2\tfrac{\Delta\varphi}{2} + \cos\varphi_1\cos\varphi_2\sin^2\tfrac{\Delta\lambda}{2}}$$

Land and ice cells carry no wave values, so cells with no finite value are excluded before the nearest one is
chosen, and the page says when the chosen cell is not simply the nearest one. For spectra only the first time
step is tested, to avoid reading a whole file. A grid node chosen by the user (the node picker) replaces the
requested site in this step; nothing else changes.

**Merging files** (`analysis.collect`, `analyse_spectra`). One NetCDF per month, or per chunk of days or hours
when CDS refused a large request, is read and concatenated by time. Where ERA5 and preliminary ERA5T both
appear along `expver`, they are NaN-complementary and are merged with a NaN-skipping mean. Duplicate
timestamps are dropped, keeping the first. The record is then sorted by time.

**Model depth.** The ERA5 `model_bathymetry` field (variable `wmb`) is read at the wave cell. It is used only
in the group velocity of Option B (section 4) and in a deep-water validity check (section 5). If it is absent,
deep water is assumed and the page says so.

**Time step and record length.** The time step is the median spacing of timestamps. The record length is
last minus first timestamp divided by 365.25 days. Annual energy uses $8766\ \mathrm{h} = 365.25\times 24$.

## 3. Spectral reduction (Option B)

Implemented in `wavecalc.spectral_bulk`. The input is the ERA5 2D spectrum $E(f,\theta)$, parameter 251.140.

**Grid.** There are 30 frequencies $f_n = 0.03453 \times 1.1^{\,n}$ Hz ($n = 0\ldots 29$, up to 0.548 Hz) and 24
directions of 15°, the first centred on 7.5°, going-to. ERA5 stores $\log_{10}E$ in m² s/rad;
`decode_log10` returns $10^{x}$. Bins that are missing (below the encoding floor) become 0 and the share that
is missing is reported. There is no high-frequency tail beyond 0.548 Hz (section 12).

**Frequency widths.** $\Delta f$ is `np.gradient(f)`, the same convention as the `wavespectra` package, so
results agree with it. Exact geometric bin edges, $f\,(r^{1/2}-r^{-1/2})$ with $r = 1.1$, are available as an
option; the code documents a difference of about 0.1% in width and about 0.06% in Hm0.

**One-dimensional spectrum and moments.**

$$E(f) = \sum_\theta E(f,\theta)\,\Delta\theta,\qquad m_n = \sum_f f^{\,n}\,E(f)\,\Delta f,\qquad \Delta\theta = \tfrac{2\pi}{24}$$

evaluated for $n = -2, -1, 0, 1, 2$ as rectangle sums. A spectrum with $m_0 = 0$ (land, ice) gives NaN for
every parameter.

| Quantity | Formula | ERA5 name |
|---|---|---|
| Hm0 | $4\sqrt{m_0}$ | `swh` |
| Te = Tm-1 | $m_{-1}/m_0$ | `mwp` |
| Tm01 | $m_0/m_1$ | `mp1` |
| Tm02 | $\sqrt{m_0/m_2}$ | `mp2` |
| $\varepsilon_0$ | $\sqrt{\max(m_0 m_{-2}/m_{-1}^2 - 1,\ 0)}$ | none (diagnostic) |

The definition of Te as $m_{-1}/m_0$ follows the ECMWF document *Ocean wave model output parameters*
(J.-R. Bidlot, 2020), section 3.2. Tm02 satisfies $T_{m02}\le T_{m01}\le T_e$ for any spectrum (Cauchy–Schwarz),
so the app counts records that break the ordering by more than 1% (`ordering_violations`). A violation means
a wrong variable, wrong units or a decoding error, not a wave condition.

**Peak period** (`parabolic_peak_period`). On the grid, $T_p$ is $1/f$ at the maximum of $E(f)$. The reported
peak period fits a parabola through the maximum and its two neighbours $(x_1,y_1),(x_2,y_2),(x_3,y_3)$ and
takes its vertex:

$$x_v = x_2 - \tfrac12\,\frac{(x_2-x_1)^2(y_2-y_3) - (x_2-x_3)^2(y_2-y_1)}{(x_2-x_1)(y_2-y_3) - (x_2-x_3)(y_2-y_1)},\qquad T_p = 1/x_v$$

It falls back to the bin centre if the maximum is at either end of the axis, the three points are degenerate,
or the vertex lies outside $(x_1,x_3)$. This is a fit to the direction-integrated spectrum $E(f)$. ECMWF's
`pp1d` is a parabolic fit around the maximum of the 2D spectrum, so the two differ slightly; the cross-check
expects that.

**Mean direction.** With $\theta$ going-to and weights $E(f,\theta)\,\Delta f$,
$\bar\theta = \operatorname{atan2}\!\big(\sum w\sin\theta,\ \sum w\cos\theta\big)$, then converted to
coming-from. This is the energy-weighted mean over the whole spectrum.

**Directional width** (`wdw`). The mean direction is taken per frequency. With
$S_f = \sum_\theta E\sin\theta$ and $C_f = \sum_\theta E\cos\theta$,

$$r = \frac{\sum_f \sqrt{S_f^2 + C_f^2}\ \Delta f}{\sum_f\sum_\theta E(f,\theta)\,\Delta f},\qquad \sigma_\theta = \sqrt{2\,(1 - r)}\ \text{(radians)}$$

0 means unidirectional, $\sqrt2$ means uniform over all directions. Directions are resolved only to 15°, so
$\sigma_\theta$ is approximate.

## 4. Energy flux

**Option B: integral over the spectrum.**

$$J = \frac{\rho g}{1000}\sum_f\sum_\theta E(f,\theta)\;c_g(f,h)\;\Delta f\,\Delta\theta\quad[\mathrm{kW/m}]$$

with $\rho = 1025\ \mathrm{kg/m^3}$ and $g = 9.81\ \mathrm{m/s^2}$.

**Group velocity** (`wavenumber`, `group_velocity`). In deep water, or when no depth is available,
$c_g = g/(4\pi f)$. At depth $h$ the wavenumber solves the linear dispersion relation
$\omega^2 = g k \tanh(kh)$ with $\omega = 2\pi f$. The solver starts from
$k_0/\sqrt{\tanh(k_0 h)}$, $k_0 = \omega^2/g$, and runs 12 Newton steps (with $k$ floored at $10^{-12}$). Then

$$c_g = \tfrac12\,\frac{\omega}{k}\Big(1 + \frac{2kh}{\sinh 2kh}\Big)$$

with $2kh$ capped at 700 to avoid overflow. Depth changes only $c_g$. There is no shoaling, refraction,
bottom friction or breaking: the node is an offshore model cell, not a nearshore site.

**Option A: deep-water relation.** ERA5 single levels have no spectrum, so (`wavecalc.deep_water_flux`)

$$J = \frac{\rho g^2}{64\pi\cdot 1000}\,H_{m0}^2\,T_e = 0.4906\;H_{m0}^2\,T_e$$

with Te taken directly from `mwp`. No ratio between peak and energy period is applied, and `pp1d` is
deliberately not used. Example: $H_{m0} = 2$ m and $T_e = 9$ s give $0.4906\times 4\times 9 = 17.66$ kW/m. For
a monochromatic deep-water wave this relation is exact; for a spectrum it holds when the whole spectrum is
in deep water, which is why the app flags records where the depth is below $L_0/2$ (section 5).

**Flux direction and directionality** (Option B). The flux in each direction bin is
$J_\theta = \frac{\rho g}{1000}\sum_f E\,c_g\,\Delta f\,\Delta\theta$. With the going-to bin angles $\theta$,

$$J_N = \sum J_\theta\cos\theta,\quad J_E = \sum J_\theta\sin\theta,\quad
\theta_J = \operatorname{to\_from}\big(\operatorname{atan2}(J_E, J_N)\big),\quad
\text{directionality} = \frac{\sqrt{J_N^2 + J_E^2}}{J}$$

Directionality is 1 for a single direction and falls as energy arrives from several. $\theta_J$ differs from
the mean direction because flux weights long periods more heavily than energy does.

**Flux by period.** For each frequency the flux $J_f = \frac{\rho g}{1000} E(f)\,\Delta\theta\,c_g\,\Delta f$ is
averaged over valid records. The curve shows the share of the mean flux carried by periods $\le T$, with
$T = 1/f$ sorted from short to long. It shows how much energy falls inside a device's response band.

**Sector flux** (`sector_fraction`, `sector_section`). For a heading $\alpha$ (coming-from) and half-width
$w$, each 15° direction bin is weighted by how much of it lies inside $[\alpha - w,\ \alpha + w]$:

$$\text{weight} = \frac{\operatorname{clip}\big(7.5^\circ + w - |\Delta|,\ 0,\ \min(15^\circ, 2w)\big)}{15^\circ},\qquad |\Delta| = \text{wrapped distance from bin centre to }\alpha$$

This assumes flux is uniform inside a bin when a sector edge cuts through it. The share of flux inside is
$\sum_t\sum_\theta \text{weight}\,J_\theta \big/ \sum_t J$. For comparison the page also gives the share of
hours whose *mean* direction is inside the sector, and the flux share of those hours; they differ because swell
and wind sea can lie on opposite sides of the sector edge.

**Partitioning** (`partition_spectra`). When the optional `wavespectra` package is installed, each spectrum
is split into up to three systems with its PTM3 watershed method, after converting the units
(m² s/rad to m²/(Hz·deg)) and directions to coming-from. Each system is reduced with the same
`spectral_bulk` and shown ordered by decreasing Hm0, not classified as sea or swell. For long records at most
3000 spectra per file are partitioned (every Nth record), and the page says so. The share of $m_0$ in the
second system, at or above 20%, marks a multi-system hour (the 20% is this app's choice). These systems will
not match ECMWF's swell partitions in Option A.

**JONSWAP $\gamma$ fit** (`fit_gamma`). A diagnostic only. The peak-normalised spectrum is compared with
peak-normalised JONSWAP shapes on $0.7f_p$ to $3f_p$ by least squares over $\gamma = 1\ldots 10$ in steps of
0.25. With 10% frequency spacing the resolution is coarse, and no result depends on this value.

## 5. Integrated parameters and checks (Option A)

`analysis.analyse_bulk` works on the ERA5 integrated parameters, so there is no spectrum to integrate.

- **Flux.** Section 4, with `swh` and `mwp`.
- **Sea-state composition.** Swell-dominated hours are those with $h_{ts}^2 > h_{ww}^2$ (swell and wind-sea
  heights `shts`, `shww`). The swell share of energy is
  $\dfrac{h_{ts}^2\,T_{ts}}{h_{ww}^2\,T_{ww} + h_{ts}^2\,T_{ts}}$ with the partition periods `mpts`, `mpww`,
  which ECMWF defines as Tm-1, so the weights are consistent with the flux. The check
  $\big|\,h^2 - h_{ww}^2 - h_{ts}^2\,\big|/h^2$ should be small, because energy adds; the share of records
  within 5% is reported and a warning appears below 95%.
- **Ordering check.** $T_{m02}\le T_{m01}\le T_e$ with a 1% tolerance, as in section 3.
- **$T_e/T_p$.** Mean of `mwp/pp1d`, with records outside 0.7 to 1.1 counted as a flag for inspection
  (bimodal seas can leave the range), not an error.
- **Steepness.** $2\pi H_{m0}/(g\,T_{m02}^2)$.
- **Deep-water validity.** The deep-water wavelength is $L_0 = gT_e^2/(2\pi)$. Records with depth $h < L_0/2$
  are counted; there the deep-water flux is only an approximation, and its error has no fixed sign.
- **Sampling check.** For an hourly record, the means of Hm0, Te and $J$ are recomputed using only the records
  at 00, 06, 12 and 18 UTC, so the effect of coarser sampling on this record is visible.

## 6. Tables, roses and climatologies

**Scatter tables** (`wavecalc.scatter_table`, `analysis.scatter_section`). Bins are half-open, $[\text{lower},
\text{upper})$, 1 s wide in period and 0.5 m in Hm0, starting at 0. Each cell has two numbers: the share of
*records* (equal to hours only when the time step is one hour) and the share of *energy*, meaning the sum of $J$
in the cell over the total. Records outside the table or with non-finite values are counted and reported, not
dropped silently. The note also gives how many occupied bins carry 90% of the energy (bins sorted by energy,
cumulative sum).

**Wave roses.** Option A uses 16 sectors of 22.5° centred on north, with the sector index
$\lfloor((\theta + 11.25^\circ)\bmod 360^\circ)/22.5^\circ\rfloor$ of `mwd`. Option B uses the 24 bins of 15°
of the energy-weighted mean direction for the share of hours, and the file's own direction bins for the share
of flux. Shares of energy are weighted by $J$ per record.

**Climatology on the Analysis page.** For each calendar month the mean over *all* records in that month, pooled
over all years. **Mean annual energy per metre of crest** is mean $J \times 8766/1000$ in MWh/m/yr. The
**coefficient of variation** of $J$ is the population standard deviation of the per-record flux divided by its
mean.

## 7. Grid nodes and screening

**Node table** (`analysis.node_summary`). For every node in the download, over the whole record: mean Hm0, mean
Te, mean $J$ (each from per-record values, with spectra reduced by the same `spectral_bulk`), the model depth,
and the haversine distance from the requested site. Land and ice nodes are shown but cannot be analysed.

**Screening** (`screening._node_stats`). For each node with data: mean Hm0, Te and $J$; the 95th percentile of
$J$; $\mathrm{COV} = \sigma_J/\bar J$; the mean $J$ in each calendar month and in each of the four seasons
(DJF = Dec–Feb, MAM, JJA, SON, grouped by month number, not by shifting December into the next year); and the
**seasonality ratio** $\max(\text{season means})/\min(\text{season means})$, defined only when all four seasons
have data and the minimum is positive.

**All records or a stated stride** (`analysis.compute_spectra_nodes`). Reading the spectra of a whole box is
the expensive step. Records are read in chunks of 500 along the time axis (peak memory about 244 MB in the
measurement noted in the code, on a 40-node, 3-year synthetic case) and every record is used while

$$\text{nodes}\times\text{records}\ \le\ 500{,}000 .$$

Above that, one record in every $N$ is used, with $N = \lceil \text{nodes}\times\text{records}/500{,}000 \rceil$,
and the page states $N$ and the counts. The budget keeps a first, uncached load to roughly the tens of
seconds on the development machine. On the synthetic case that was measured, sampling changed the node means
by under 0.1% of their value, but that data had a smooth seasonal cycle, which is the best case for
sampling; no real multi-year spectra record has been measured (verification item 15).

**Overlapping files.** The per-node analysis drops duplicate timestamps (section 2), but the node table and
screening do not: a record that appears in two files is counted twice. This was read from the code, not
measured; a fully duplicated file leaves a mean unchanged, so it would show only with partial overlap, in
the counts and in the weighting of the overlapped records.

## 8. Long-term statistics and extremes

`longterm.compute_longterm`. All results come from the per-record series for one node, and the **record is
treated as a single stationary sample**. A one-month record is a demonstration, not an estimate, and the app
says so.

**Monthly climatology.** For month $m$: the mean of $J$, Hm0 and Te over all records in that month. Across
years, the P10, P50 and P90 of the per-year monthly means (only when at least three years have that month).
**Seasonal** means pool records by season as in section 7, and each season's share of the annual total is
defined only when all four seasons have data.

**Annual mean.** The mean of the twelve monthly means $\bar J_{\mathrm{an}}$ (defined only when all twelve
months have data). It is not the mean of all records: months with more records do not weigh more.

**Variability indices.** As described by Kamranzad, Etemad-Shahidi and Chegini (*Sustainability of wave energy
resources in southern Caspian Sea*, Energy, 2016), who cite Cornett (2008) and Zheng et al. (2013) for them:

$$\mathrm{MVI} = \frac{\max_m \bar J_m - \min_m \bar J_m}{\bar J_{\mathrm{an}}},\qquad
\mathrm{SVI} = \frac{\max_s \bar J_s - \min_s \bar J_s}{\bar J_{\mathrm{an}}}$$

over the twelve monthly means $\bar J_m$ and the four seasonal means $\bar J_s$. Low values mean a stable
resource. Here the annual mean is the mean of the monthly means, and seasons are calendar seasons; the
cited papers were not read, only the description in the first. **COV** of the whole record is
$\sigma_J/\bar J$ over all per-record fluxes; no source for that exact definition was checked.

**Interannual variation.** For *complete* years only, meaning calendar years with all twelve months present
and at least 90% of the expected records, and needing at least three of them: the annual mean flux of each
year, its anomaly in percent of the mean of the annual means, the COV of the annual means, and the ratio
of the largest to the smallest annual mean. It does not assess ENSO or the Indian Ocean Dipole; no climate-index
data are used.

**Extreme Hm0 by peaks over threshold.** The steps, in order:

1. **Threshold.** $u$ is the `threshold_pct` percentile (default 95) of all finite Hm0 in the record.
2. **Declustering** (`_decluster`). Exceedances of $u$ are grouped into clusters: consecutive exceedances less
   than 48 hours apart (the default, adjustable) belong to one cluster. Each cluster is represented by its
   maximum, so storm peaks are treated as independent. The number of cluster peaks is $n$.
3. **Enough peaks.** If $n$ is below 30, no fit is made and the reason is shown.
4. **Excesses and rate.** $y_i = x_i - u$ for each peak $x_i$, and the event rate
   $\lambda = n / (\text{record length in years})$, using the exact span.
5. **Fit.** A generalised Pareto distribution is fitted to the excesses by maximum likelihood,
   `scipy.stats.genpareto.fit(y, floc=0)`, giving shape $\xi$ and scale $\sigma$. A warning is shown if
   $|\xi| > 0.5$, where the estimator is no longer regular.
6. **Return level** for a return period of $T$ years:

$$x_T = u + \frac{\sigma}{\xi}\Big((\lambda T)^{\xi} - 1\Big)\quad(\xi \ne 0),\qquad x_T = u + \sigma\ln(\lambda T)\quad(|\xi| < 10^{-6})$$

   A warning appears when $T$ exceeds three times the record length: the extrapolation is unreliable.
7. **Interval** (bootstrap, 300 replicates by default, fixed seed). In each replicate, the number of peaks is
   drawn from a Poisson distribution with mean $\lambda\times$years (at least 10), the excesses are resampled
   with replacement, the GPD is refitted at the same threshold, and $x_T$ is recomputed with that replicate's
   rate. The 5th and 95th percentiles of the replicates give a 90% interval. A warning appears if more than 5%
   of replicates fail to fit. The threshold and the declustering window are not resampled, so the interval does
   not include their uncertainty.
8. **Threshold sensitivity.** The fit is repeated at the 90, 92.5, 95 and 97.5 percentiles, each with its own
   declustering, and the return level of the *longest* requested period is shown for each. A return level that
   moves with the threshold is a warning sign, and the app shows it but does not choose a threshold.
9. **Return-level plot.** Peaks sorted in descending order are drawn at Weibull plotting positions
   $T_i = 1/\big(\lambda\,i/(n+1)\big)$ against the fitted curve on a log axis.

The method assumes independent, identically distributed peaks over the record. Seasonality and trends in the
storm climate are not modelled.

## 9. Device performance

`device.py`. The input is a power matrix: rows are Hm0 (m), columns are period (s), cells are power (kW). Blank
cells mean "undefined".

**Bins.** Axes are read as bin centres or as lower edges. From centres, interior edges are midpoints and the
outer edges are extended by half the adjacent spacing; from lower edges, the last upper edge is the last lower
edge plus the last spacing.

**Looking up the power** (`lookup_power`). Two methods are always available:

- **Bin.** A record falls in the half-open cell $[\text{lower},\text{upper})$ of both axes. A record outside the
  matrix extent, or in an undefined cell, is *outside*.
- **Bilinear.** Linear interpolation between neighbouring bin *centres* (SciPy `RegularGridInterpolator`). A
  record is inside only within the range of the centres, and only if the interpolated value is defined; any
  undefined corner makes it outside.

**Annual energy.** Over the valid records (finite Hm0, period and $J$), records outside the matrix count as
zero power. The mean power $\bar P$ is the mean over *all* valid records and

$$\mathrm{AEP} = \bar P\times\frac{8766}{1000}\ \text{MWh/yr},\qquad \text{capacity factor} = \frac{\bar P}{P_{\text{rated}}}$$

Counting outside records as zero is a conservative assumption, and the share of records and of flux outside
the matrix is reported next to it. Every record has equal weight, which assumes the record is an even sample of
the year. Example: $\bar P = 6.68$ kW gives $6.68\times 8.766 = 58.6$ MWh/yr. A partial record is not an annual
estimate, and a warning says so.

**Capture width.** Two estimators, both in metres, with the flux of the same route:

$$\overline{CW}_{\mathrm{energy}} = \frac{\sum P}{\sum J}\ \ (J>0),\qquad
\overline{CW}_{\mathrm{ratio}} = \operatorname{mean}\!\Big(\frac{P}{J}\Big)\ \ \text{over records with } J \ge 1\ \mathrm{kW/m}$$

The first weights each record by its energy; the second weights every record equally and drops the calm ones.
If the user gives a characteristic width, each is also shown as a ratio to it. What that width means is the
user's definition.

**Te or Tp.** Published power matrices are indexed by either the energy period or the peak period, and a CSV
does not say which. With the period type set to *unknown*, the whole assessment is done twice, once indexed by
Te and once by Tp, and the two AEPs are shown as the bound on that ambiguity. They are never blended.

## 10. Cross-check, Option A against Option B

`crosscheck.compare`. Records are matched on timestamp, so a 6-hourly spectra record is compared with the
hourly single-levels record at the same instant. For each of Hm0, Te, Tp, Tm01, Tm02, mean direction and $J$,
with A the single-levels value and B the spectra value:

- **Bias** is the mean of $B - A$. For directions the mean of the differences is taken as a vector mean,
  because near ±180° an arithmetic mean cancels out.
- **RMSE** is $\sqrt{\operatorname{mean}((B-A)^2)}$.
- **Correlation** is the Pearson coefficient (relative quantities only), and is not defined for a constant series.
- **Within tolerance** is the share of records with $|B-A| \le \text{tol}\cdot|A|$ (relative quantities) or
  $|B-A|\le \text{tol}$ degrees (direction). Defaults: 5% for Hm0, Te, Tm01, Tm02 and $J$; 10% for Tp; 15° for
  direction.
- **Worst records**: the ten with the largest relative (or absolute) difference.

Warnings are raised when the Te bias is outside tolerance (a decoding or unit error), when the direction bias
exceeds 150° (a coming-from/going-to error), when the two routes used different cells, and when the model
depth makes the finite-depth flux differ from the deep-water one. Option B's Hm0 is expected slightly below
`swh` because the 30 bins stop at 0.548 Hz and no tail is added.

## 11. Choices that are this app's own

These are defaults or thresholds chosen here. They are not from a standard, and they can be changed in the
code or, where marked, in the interface.

| Choice | Value | Where |
|---|---|---|
| Record length treated as a demonstration, not an estimate | under 10 years | `analysis.MIN_RECORD_YEARS` |
| Ordering tolerance $T_{m02}\le T_{m01}\le T_e$ | 1% | `analysis.ORDERING_TOLERANCE` |
| Composition check $h^2 \approx h_{ww}^2 + h_{ts}^2$ | within 5%, warn below 95% of records | `analysis.COMPOSITION_TOLERANCE` |
| $T_e/T_p$ flag range | 0.7 to 1.1 | `analysis.TE_TP_FLAG_RANGE` |
| Multi-system hour | second partition has at least 20% of $m_0$ | `analysis.BIMODAL_ENERGY_SHARE` |
| Scatter bins | 1 s by 0.5 m | `analysis.SCATTER_PERIOD_BIN`, `SCATTER_HM0_BIN` |
| Wave rose sectors (Option A) | 16 of 22.5° | `analysis.ROSE_SECTORS` |
| Cross-check tolerances | 5% / 10% (Tp) / 15° | `crosscheck.TOLERANCES` |
| Node and screening work budget | 500,000 node-records; chunk of 500 | `analysis.MAX_NODE_RECORDS`, `CHUNK_SIZE` |
| Partitions and $\gamma$ fits per file | 3000 and 6000 spectra | `analysis.MAX_PARTITION_STEPS`, `MAX_GAMMA_STEPS` |
| Extreme-value threshold | 95th percentile | `longterm.compute_longterm` (interface) |
| Declustering window | 48 h | same (interface) |
| Minimum cluster peaks for a fit | 30 | same |
| Return periods shown | 1, 10, 50, 100 years | same (interface) |
| Bootstrap | 300 replicates, seed 1, 90% interval | same |
| Capture-width ratio minimum flux | 1 kW/m | `device.assess_device` |
| Hours per year | 8766 | `plugins.HOURS_PER_YEAR` |

## 12. Known approximations

- **ERA5 is a reanalysis**, and the wave model grid (about 0.36°, delivered on 0.5°) does not resolve
  nearshore processes. A coastal site is represented by an offshore cell.
- **No spectral tail.** The 30 bins stop at 0.548 Hz (1.83 s). ECMWF's integrated parameters add an $f^{-5}$
  tail, so Option B's Hm0 is slightly below `swh`. None is added here.
- **Direction resolution** is 15°. Directional width and flux direction are approximate to that resolution.
- **Deep-water flux in Option A** is an approximation where the depth is below $L_0/2$, and the page counts those
  records.
- **Encoding floor.** The ECMWF page gives the floor of the stored spectra as about $10^{-4}$, but the files
  seen here hold values down to about $10^{-7}$, so the floor is not exactly that.
- **Rectangle-rule sums** on a 10%-spaced frequency grid; there is no interpolation inside bins.
- **Short records.** Interannual variability and extreme values need many years. Return levels more than three
  times the record length are flagged as unreliable.
- **Equations cited in code comments.** Comments in `wavecalc.py` cite equation numbers of the ECMWF document
  for the mean direction and the directional width. Only section 3.2 of that document (the definition of Te)
  was re-read when this file was written.
