# WP16: five smaller numerical problems from the review

The numerics reviewer listed these after the four main problems fixed in WP14 and WP15. None changes a headline
number on typical data, but each can mislead or misreport in some case. Items the review listed that this package
does NOT change are named at the end, with the reason.

## Who does what

- Claude (orchestrator) wrote `tests/test_numerics_minor.py` from this spec before any implementation. It is red
  on the current code. You (Antigravity, the implementer) must NOT edit, delete, rename, skip or weaken it, and must
  not edit any other file under `tests/`.
- If you think a test is wrong, say so under "Requests to the orchestrator".
- The orchestrator re-runs everything.

## Files you own

`analysis.py` (`compute_spectra_nodes`, `sampling_note`, `sector_section`, and `ANALYSIS_VERSION`), `longterm.py`
(`_extremes` and `report_markdown` only), `plugin_longterm.py` (version number only), `docs/numerics.md` (only the
sentences named below). Do not touch `screening.py`, `device.py`, `app.py`, `static/` or any other file. Keep every
`xr.open_dataset` inside `with NETCDF_LOCK:` (a test checks it).

## Requirement 1: the spectra sampling stride is global, and the note is exact

### The problem

`compute_spectra_nodes` applies the stride to each file separately (`every stride-th record, starting at the first
record of that file`), but `sampling_note` assumes one stride over the whole record. With 3 files of 8 records and a
stride of 3 the note says "8 of 24 records" while 9 records were used, and every file is sampled at the same phase.

### The fix

Number the kept records (after WP15's first-occurrence filtering) globally, in file order, starting at 0. Use the
record if and only if its global number is divisible by `stride`. Carry the running count across files. Then the
number of records used is exactly `ceil(total_records / stride)`, which is the number `sampling_note` already
prints, and the sample does not restart in every file. With one file, or with `stride == 1`, nothing changes.

Update `ANALYSIS_VERSION` to 9.

## Requirement 2: the threshold-sensitivity table needs enough peaks

### The problem

In `_extremes` a sensitivity row is fitted when it has at least 2 peaks, while the main fit needs
`min_exceedances` (30 by default). A noisy fit on a handful of peaks is shown beside the main one.

### The fix

A sensitivity row with fewer than `min_exceedances` peaks gets `xi`, `sigma` and `level_m` set to `None` (its
`percentile`, `threshold_m` and `n_peaks` stay), exactly as the existing "fewer than 2 peaks" and "fit failed" rows
already look. Rows with enough peaks are unchanged.

## Requirement 3: a warning when Hm0 is sampled less often than hourly

### The problem

Extremes from 6-hourly records can miss storm peaks that fall between samples. In a synthetic test the 100-year
level was 12.61 m from hourly data and 11.06 m from every 6th record. That was not reproduced on real ERA5, so the
size of the effect is unproven, but the reader should be told.

### The fix

When `_extremes` runs a fit (status `"ok"`) and the median time step of the record is more than 1 hour, append this
warning (with the step written with `:g`): `"Hm0 is sampled every {step:g} h: storm peaks between samples are
missed, so extreme levels may be biased low. The size of the bias was shown on synthetic data only, not on real
ERA5."` No such warning when the step is 1 hour or less. Use the median step the function already computes.

## Requirement 4: hours without a direction are not "outside the sector"

### The problem

`sector_section` computes "Hours with mean direction inside the sector" as `nanmean` of a boolean array. A NaN
direction compares as `False`, so hours with no direction stay in the denominator and count as outside the sector.

### The fix

Restrict that percentage to hours with a finite mean direction (`dm_from`). If there are none, the value is NaN (it
renders as "—" through `fmt`). Do not change the other three rows of the section.

## Requirement 5: return periods shorter than the mean time between events

### The problem

With a low event rate `lam` (for example a 99.9th-percentile threshold), a return period `T` with `lam * T < 1`
gives a return level below the threshold, which is not a statement about an exceedance at all. The plugin accepts
`T` down to 0.5 years.

### The fix

For each requested `T` with `lam * T < 1`, in `levels`: `level_m`, `ci_low_m` and `ci_high_m` are `None`, and the
entry gets `"note": "shorter than the mean time between events (1 / rate = {1/lam:.2f} yr)"`. Entries with
`lam * T >= 1` are unchanged (and have no `"note"` key). The fitted curve and the empirical points are not
changed. `report_markdown` must print `n/a` instead of `None` for a missing level or interval bound and must not
raise. Update `LONGTERM_VERSION` in `plugin_longterm.py` to 3.

## Docs

In `docs/numerics.md` add or adjust, in the matching sections, one sentence each: the global stride (and that the
sampling note is exact); that sensitivity rows below the minimum peak count are not fitted; the sub-hourly
sampling warning; that the sector percentage uses only hours with a direction; that return periods shorter than
`1/rate` are not reported. Also change the overlapping-files sentence added in WP15 so it says the first file wins
even when its value is missing (NaN), so the result can depend on file order.

## Not changed here (decisions)

- **Merging overlapping files keeps the first value even if it is NaN** (review item 6): this is now the stated
  rule for all three routes (docs only). Taking the first finite value would need every route to hold values
  across files before reducing, which is a design change for a rare case.
- **Bilinear device lookup counts the outer half-bin as outside** (item 11): documented behaviour; changing it
  is a modelling choice for the user.
- **`sector_fraction` for half-widths above about 172 degrees** (item 12): irrelevant in practice.
- **Hard-coded 0.4906 in `plugins.py`, nearest-depth-cell tolerance** (item 13): cosmetic or not reproduced.

## Acceptance

- `python -m pytest -q` passes, including `tests/test_numerics_minor.py` unchanged.
- Report the literal tails of `python -m pytest tests/test_numerics_minor.py -q` and `python -m pytest -q`, the
  list of files you changed, and anything you were unsure about. Only report what you ran and saw.
- Do not commit. Do not run a server. Do not touch `downloads/` or `devices/`.
