# WP7: exact node summaries instead of fixed sampling

Goal: the node table and the screening statistics are computed from every record whenever that is affordable,
and say plainly when they are not.

Today two caps drop records silently for spectra downloads:

- `analysis.py`: `NODE_SAMPLE_STEPS = 300` in `node_summary` (the grid-node table) keeps every Nth record
  so at most about 300 are used.
- `screening.py`: `MAX_RECORDS = 1500` keeps every Nth record per node (stride sampling, with a note).

The effect on means, percentiles and seasonal statistics over a multi-year record was never measured
(`docs/verification.md` item 15). Single-level downloads are not affected.

## Files you own

- `screening.py`, `plugin_screening.py` (only if needed)
- `analysis.py`: ONLY `node_summary`, its constants and helpers it uses exclusively. Do not change any other
  function, the analysis payloads or `ANALYSIS_VERSION` unless the node summary output format changes in a
  way that makes cached `nodes*.json` stale; if so, say so and bump only what is needed.
- `tests/test_screening.py`, `tests/test_nodes.py`, and a new `tests/test_sampling.py`
- `docs/verification.md`: ONLY item 15

Another agent edits `fetch_era5_waves.py`, `app.py` and the front end at the same time: do not touch those.

## Requirements

### 1. Measure first

Write a throwaway benchmark script in your scratch folder (not in the repo) that builds a synthetic spectra
dataset with the same structure the real files have (look at `tests/test_analysis.py::spectra_dataset` and the
spectra reader in `analysis.py`): a seasonal cycle, at least 40 grid nodes, and at least 3 years at 6-hourly
steps. Run the current code and report, for the node table and for screening: wall time, peak memory
(`tracemalloc` is acceptable for the NumPy allocations; say what you used), and the error that sampling
introduces versus the exact answer for: mean flux, mean Hm0, mean Te, the 95th percentile of flux, and the
max/min season ratio. Put the numbers in your report and in item 15 of `docs/verification.md`.

### 2. Exact when affordable

Replace the fixed record caps with a work budget: compute from every record when `nodes x records` is at or
below a constant (name it, for example `MAX_NODE_RECORDS`), and otherwise fall back to a stride that brings the
work down to the budget, with the same style of note as today stating the stride and the counts. Choose the
budget from your measurements so that a first (uncached) load of a typical download stays within about 60
seconds on this machine, and say what budget you chose and the time it implies. Both places (node table and
screening) use the same logic; share it rather than copying it.

To keep memory bounded, read the time axis in chunks (not all records of all nodes at once) and accumulate.
State the chunk size and the peak memory you measured for it.

The statistics must be the same quantities as today (flux computed per record, then averaged; percentiles
over records; seasons by calendar months). For the exact case, prove with a test against an independent
computation on a small synthetic dataset that the chunked result equals a direct all-at-once calculation to
floating point precision, including when the record count is not a multiple of the chunk size.

### 3. Notes and cleanup

- The note shown to the user says whether the summary used all records or a stride, with counts, and never
  claims exactness when it sampled.
- `screening.py` contains a leftover comment ("But wait, memory can hold subsampled arrays per node."). Remove
  it and any other dead or confused comment or code in the files you own, and report each one you removed.
- Cached results (`screening.json`, `nodes*.json`) must be invalidated correctly if their contents change
  meaning (look at how the cache key or version works today and follow it).

## Acceptance

- Full suite passes. New tests cover the budget boundary (exactly at, one over), a record count that is not a
  multiple of the chunk size, an all-NaN node, a node with a gap, and the note text for both cases.
- Your report states, with numbers, how much sampling changed the results (item 1) and what the new code costs
  in time and memory.
