# WP14: long-term statistics that give wrong numbers

Three independent reviewers reproduced three results in `longterm.py` that look plausible and are wrong. This
package fixes all three. All are in `longterm.py` (plus the cache version and the docs).

## Who does what

- Claude (orchestrator) wrote `tests/test_longterm_numbers.py` from this spec before any implementation. It is red
  on the current code. You (Antigravity, the implementer) must NOT edit, delete, rename, skip or weaken it, and must
  not edit any other file under `tests/` except where "Existing tests that change" below says so.
- If you think a test is wrong, say so under "Requests to the orchestrator". Do not work around it.
- The orchestrator re-runs everything.

## Files you own

`longterm.py`, `plugin_longterm.py` (version number only), `docs/numerics.md` (only the long-term sections named
below), `static/plugins/longterm.js` (only if a requirement below says so). Do not touch `analysis.py`,
`screening.py`, `device.py` or `app.py` (WP15 and the next batch own those).

## Requirement 1: the event rate uses time actually observed

### The problem

`_extremes` computes `lam = n_peaks / exact_years`, where `exact_years` is the calendar span from the first to the
last record. When the record has gaps (months or years missing, or NaN Hm0), the span includes time in which no
peak could be seen, so the rate is too low and every return level is too low. Reproduced: with 10 of 30 years
missing, the 100-year Hm0 came out at 8.7 m instead of 12.6 m, about 31% low. Only a generic coverage warning
appeared.

### The fix

Define the exposure time of the record as

```
observed_years = (n_finite - 1) * step_hours / (24 * 365.25)
```

where `n_finite` is the number of records with a finite Hm0 and `step_hours` is the median spacing of the frame's
index in hours (the same quantity `record["step_hours"]` reports). For a series with no gaps this equals the
current `(last - first)` span exactly, so gap-free results do not change. With gaps it is shorter by the time the
missing records would have covered.

Use `observed_years` everywhere `_extremes` used `rec_years`/`exact_years` as a duration:

- the event rate `lam = n_peaks / observed_years`;
- the bootstrap (`rng.poisson(lam * observed_years)` and `lam_star = n_star / observed_years`);
- the sensitivity table (`lam_s = n_s / observed_years`);
- the empirical return periods and the fitted-curve range (through `lam`);
- the "Return period T exceeds 3x the record length" warning: compare `T` with `3 * observed_years` and print
  `observed_years` in the message.

`compute_longterm` may keep computing `exact_years` for its own use, but `_extremes` must receive what it needs to
compute `observed_years` (or compute it itself from `frame`). Do not change the public signature of
`compute_longterm`.

Add to the `extremes` result (when `status` is `"ok"`):

- `"observed_years"`: the value above, rounded with `_round3`;
- `"record_years"`: the first-to-last span in years, rounded with `_round3`.

When `observed_years < 0.95 * record_years`, append one warning to the result's `warnings` containing the word
`gaps`, for example: `"The record has gaps: Hm0 was observed for 20.0 of 30.0 years. The event rate uses the
observed time, not the calendar span."`. No such warning when there are no gaps.

Update the `method` string and `report_markdown` only if they state the rate's denominator; do not otherwise
rewrite them.

## Requirement 2: seasonal shares add up to 100 %

### The problem

`share_of_annual_pct` is `100 * season_mean / mean(four season means)`. Each value is therefore about 100 and the
four add up to about 400. For constant flux each season prints 100.0 where a reader expects 25.

### The fix

```
share_of_annual_pct = 100 * season_mean / sum(four season means)
```

so the four shares add up to 100 (before rounding). Everything else about the season entries stays: the key name,
`None` with a `reason` when a season has no data, and `None` for every season (with the existing reason) when not
all four seasons have data. The seasonal variability index (SVI) is not changed. Describe the share in the
`docs/numerics.md` long-term section as "the season's share of the annual flux, treating the four seasons as
equal in length".

## Requirement 3: a complete year is a whole calendar year

### The problem

`_record_summary` measures a year's coverage only over the part of the year the record spans, and accepts a year
when "all 12 months are present". A record that starts on 31 January 2000 therefore has 100 % coverage for 2000
and a (one-day) January, so 2000 counts as a full year. Its annual mean is then 15.4 kW/m against a true 17.5, an
artefact of -8.6 % that inflates the interannual variation.

### The fix

For every calendar year `Y` in the record, with `step_hours` as above:

- `expected(Y) = days_in_year(Y) * 24 / step_hours` and, for each month `M`,
  `expected(Y, M) = days_in_month(Y, M) * 24 / step_hours`. Do not limit these to the span of the record.
- `records(Y)` and `records(Y, M)` count the records with finite flux or finite Hm0 (the existing
  `finite_mask`).
- `coverage_pct` in `per_year` becomes `min(100 * records(Y) / expected(Y), 100)` (still `_round3`). A partial
  first or last year therefore shows its true, low coverage.
- `Y` is in `full_years` if and only if `records(Y, M) >= 0.9 * expected(Y, M)` for every month `M = 1..12`.
  (This implies at least 90 % coverage for the year and rules out a year in which any month is mostly missing.)

Everything that reads `full_years` (the interannual block, the report, the UI count) follows from this; do not
change those consumers.

## Cache versions

Cached long-term results must not survive this change: set `LONGTERM_VERSION = 2` in `plugin_longterm.py` (it is
part of the cache file name).

## Existing tests that change

None are expected to change. If an existing test in `tests/test_longterm.py` fails after your change, do not edit
it: report it under "Requests to the orchestrator" with the assertion and why you think the old expectation is
wrong.

## Acceptance

- `python -m pytest -q` passes, including `tests/test_longterm_numbers.py` unchanged.
- Report the literal tails of `python -m pytest tests/test_longterm_numbers.py -q` and `python -m pytest -q`, the
  list of files you changed, and anything you were unsure about. Only report what you ran and saw.
- Do not commit. Do not run a server. Do not touch `downloads/` or `devices/`.
