# WP15: overlapping files are counted once in the node table and in screening

An independent review reproduced it: when two downloaded files cover the same hours, the main analysis counts each
hour once, but the grid-node table and the screening map count the overlap twice, so their means are wrong (about
11 % high on the review's test case) and disagree with the main analysis for the same node.

## The problem, with evidence

`analysis.collect()` (Option A) and the Option B analysis merge the files into one series and drop repeated
timestamps (`joined[~joined.index.duplicated()]`, the first file in the list wins). Three other places do not:

- `analysis.node_summary`, Option A branch: `add()` sums every time step of every file.
- `analysis.compute_spectra_nodes` (used by `node_summary` for Option B and by `screening._compute_spectra`):
  concatenates every file's records. Its caller `node_summary` also passes `total_records = sum of file lengths`,
  which decides the sampling stride and the sampling note, so overlap also changes how many records are used.
- `screening._compute_bulk` (Option A screening): concatenates every file's `swh`, `mwp` and months.

`screening.compute_screening` already reports `record.records` as the number of unique timestamps, so the header
says one number of records while the node statistics use another.

## Who does what

- Claude (orchestrator) wrote `tests/test_overlapping_files.py` from this spec before any implementation. It is
  red on the current code. You (Antigravity, the implementer) must NOT edit, delete, rename, skip or weaken it, and
  must not edit any other file under `tests/`.
- If you think a test is wrong, say so under "Requests to the orchestrator".
- The orchestrator re-runs everything.

## Files you own

`analysis.py` (`node_summary`, `compute_spectra_nodes`, and one small shared helper), `screening.py`
(`_compute_bulk` only), `plugin_screening.py` (version number only), `docs/numerics.md` (one or two sentences, see
below). Do not touch `longterm.py` (WP14), `app.py`, `device.py`, or the code that WP11 locked
(`netcdf_safety.py`; keep every `xr.open_dataset` inside `with NETCDF_LOCK:`, there is a test for it).

## Requirement

### One rule: each timestamp is counted once, from the first file that has it

Process the files in the order they are given (`netcdf_paths` keeps that order). A timestamp that appears in more
than one file, or more than once within one file, contributes exactly one record: the first occurrence. This is the
rule `collect()` already applies, so all three routes now agree.

Add one small helper in `analysis.py` for it, for example a function that takes a file's timestamps and the set of
timestamps already used, returns a boolean keep-mask (`True` only for the first occurrence overall) and records the
kept timestamps. Use it in all four places below. Keep it cheap: it needs only each file's time coordinate, not
the data.

1. `node_summary`, Option A (`single-levels` / generic): select the kept time steps of each file before
   `add(...)`.
2. `compute_spectra_nodes`: for each file read only the kept records. The sampling stride applies to the kept
   records of that file (take every `stride`-th kept record, starting with the first), not to the raw file. The
   `months` array it returns must line up with the records it kept.
3. `node_summary`, Option B: `total_records` is the number of unique timestamps across the spectra files (not the
   sum of the file lengths). This decides `stride` and the sampling note; with no overlap the result is unchanged.
4. `screening._compute_bulk`: select the kept time steps of each file before concatenating `swh`, `mwp` and
   months.

Files whose variables are not used for the route (for example a wind-only file in a wave route) must not mark
timestamps as seen: a timestamp is "used" only by a file that supplies records for that route.

Do not change what a file with no overlap produces: all existing node, screening and analysis tests must keep
passing without edits.

### Cache versions

Cached node and screening results must not survive: set `ANALYSIS_VERSION = 8` in `analysis.py` (the node cache
stores it) and `SCREENING_VERSION = 3` in `plugin_screening.py`.

### Docs

In `docs/numerics.md`, where the node table and screening are described, add one sentence: when two files cover the
same timestamp it is counted once and the first file in name order is used, as in the main analysis.

## Acceptance

- `python -m pytest -q` passes, including `tests/test_overlapping_files.py` unchanged.
- Report the literal tails of `python -m pytest tests/test_overlapping_files.py -q` and `python -m pytest -q`, the
  list of files you changed, and anything you were unsure about. Only report what you ran and saw.
- Do not commit. Do not run a server. Do not touch `downloads/` or `devices/`.
