# WP11: make NetCDF reading and cache writing safe under concurrent requests

Goal: opening a job in the browser (which fires several requests at once) can no longer crash the server or serve
a half-written file.

## The problem, with evidence

Flask serves requests in threads. When a job is opened, `static/app.js` starts `/nodes` and `/analysis` together
(`loadAnalysis()` does not await `loadNodes()`), and tab plugins (screening, long-term, export) add more. All of
them read the job's NetCDF files and write cache files (`analysis.json`, `timeseries.csv`, `nodes.json`,
`screening.json`, plugin results).

Two independent investigations (a Codex diagnosis at medium and at high effort, and a separate check by the
orchestrator, all on this machine: Python 3.13, netCDF4 1.7.4, netCDF-C 4.9.3, HDF5 1.14.6, xarray 2026.9.0)
found the same root cause:

- **netCDF4/netCDF-C is not thread-safe, even for different files, even with a thread-safe HDF5 build.**
  Six threads opening and closing different files in one process died with Windows exit code `0xC0000005`
  (access violation) or HDF errors in 5 of 5 rounds. The same threads behind one shared `threading.RLock`
  passed 5 of 5 rounds. xarray's own locks do not cover its metadata calls (`var.filters()` etc.), so
  xarray does not protect the application.
- A server run end to end died in 2 of 3 rounds on a real 12-file job, and in surviving rounds served a 0-byte
  `timeseries.csv` with HTTP 200, reset connections, or returned 422 "No columns to parse from file", because
  cache files are written in place with no lock and no atomic replace.

## Who does what

- Claude (orchestrator) wrote `tests/test_netcdf_safety.py` from this spec before any implementation. It is red on
  the current code. You (Antigravity, the implementer) must NOT edit, delete, rename or weaken it. If you think a
  test is wrong, say so under "Requests to the orchestrator".
- The orchestrator re-runs everything, including a real-server crash probe.

## Files you own

`netcdf_safety.py` (new), `analysis.py`, `screening.py`, `app.py`, `plugins.py`, `plugin_screening.py`,
`plugin_longterm.py`, `plugin_export.py`, `plugin_device.py`, `report.py`. Edit only what the requirements below
need. WP12 and WP13 edit other parts of `app.py` and `fetch_era5_waves.py` in separate packages: keep to the
functions named here.

## Requirements

### 1. One shared module, `netcdf_safety.py`

It defines exactly these names and nothing that imports `app`:

- `NETCDF_LOCK`: one process-wide `threading.RLock()`.
- `atomic_write_bytes(path, data)`, `atomic_write_text(path, text, encoding="utf-8")` and
  `atomic_publish(path, writer)`: each writes to a UNIQUE temporary file in the SAME folder as `path` (for example
  `tempfile.NamedTemporaryFile(dir=folder, delete=False)`), flushes and closes it, then `os.replace`s it onto `path`.
  `atomic_publish` calls `writer(temp_path)` (for example `frame.to_csv`) instead of writing bytes itself. On any
  exception the temporary file is removed and `path` keeps its previous content (or stays absent). No temporary
  file is ever left behind after a call returns or raises. Two threads writing the same path at the same time
  never share a temporary file name.

### 2. Every NetCDF read happens inside the lock

Every production use of `xr.open_dataset(...)` (the sites are `analysis.py` lines about 188, 230, 758, 1011, 1055,
1072, 1145, 1172 and `screening.py` about 95, 176; search for any others) must sit inside
`with NETCDF_LOCK:` that encloses the whole dataset context, including the metadata calls, every selection and
every read of the data, and the close:

```python
with NETCDF_LOCK:
    with xr.open_dataset(path, engine="netcdf4") as ds:
        ...  # metadata, selection, and .values / .load() of everything you need
```

No lazily-loaded array may escape the `with` block: materialise (`.values`, `.load()`) inside it. Do not wrap
the numerical work that follows in the lock unless it needs the open dataset. The lock is re-entrant, so a
function holding it may call another that takes it.

### 3. Cache computation is serialised per process, publication is atomic

- `app.cached_analysis`, `app.cached_nodes` and `plugin_screening._load_or_compute` hold `NETCDF_LOCK` for their
  whole check-cache / compute / write sequence, so a second request for the same key that arrives while the
  first is computing waits, then finds the cache and returns it without recomputing. Exactly one computation
  runs for N concurrent requests of the same key.
- Every cache or result file is written with the atomic helpers: `analysis*.json`, `timeseries*.csv`
  (`atomic_publish` with `to_csv`), `nodes.json`, `screening.json`, plugin results (`JobView.save_json`) and report
  fragments (`JobView.save_report_section`). For one analysis, publish the CSV first and the JSON last (the JSON
  is the completion marker the cache check looks at).

### 4. Responses are built without holding the lock

- `GET .../timeseries.csv` takes the lock only to make sure the cache exists and to READ THE FILE'S BYTES into
  memory, then releases it and serves those bytes (`io.BytesIO` with `send_file`, same download name and mimetype
  as today). It never serves a path that another request may replace.
- Never hold `NETCDF_LOCK` while sending or streaming a response, and never hold `jobs_lock` during an analysis.

### 5. Responsiveness

`GET /api/jobs`, `GET /api/jobs/<id>` (status polling) and every route that does not read NetCDF files must NOT
take `NETCDF_LOCK`; they must answer while a long analysis holds it.

## Acceptance

- `python -m pytest -q` passes, including the new `tests/test_netcdf_safety.py` unchanged.
- Report the literal outputs ROUND3.md requires, plus: `python -m pytest tests/test_netcdf_safety.py -q` tail, and
  a search proving no `xr.open_dataset` remains outside `with NETCDF_LOCK` (the test also checks this).
- The orchestrator will then run a real-server probe (six concurrent first-time requests, repeated); do not
  try to run a server against the repo's `downloads/` folder yourself.
