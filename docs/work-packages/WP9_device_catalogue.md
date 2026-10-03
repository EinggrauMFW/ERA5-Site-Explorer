# WP9: device catalogue (backend)

Goal: the app can list device power matrices stored in a folder on the user's machine, show their metadata and
cells, and assess a site against one of them without uploading a CSV.

The user keeps matrices in their own folder (not in this repository, because their provenance is uncertain and
publishing them is the user's decision). The folder holds:

- `devices.json`: an object keyed by device id. Each entry has `name`, `rated_kw` (number or null), `width_m`
  (number or null), `period_type` (`"Tp"` or `"Te"` as printed on the source), `source`, `provenance`, `notes`
  (list of strings), `file` (name of a CSV with the same data), `hs_m` (list of Hm0 values in m), `period_s`
  (list of periods in s) and `power_kw` (list of rows, one per `hs_m`, each row one value per `period_s`, with
  `null` for a cell that is undefined in the source).
- `*.csv`: the same matrices in the CSV layout the Device tab already reads (first column Hm0 in m, header row
  period in s, cells kW, empty cell = undefined). All four read with the existing
  `device.parse_power_matrix`, which also accepts rows that end in undefined (blank) cells. (An earlier version of
  this spec wrongly said `corpower.csv` has trailing commas; that came from a truncated listing. A CSV that
  really has a trailing separator is malformed and is skipped with a warning.)
- `devices.yaml` and `README.md`: documentation. Do NOT read YAML (no new dependency). `devices.json` carries
  everything.

A copy of four real files for development is in your scratch folder, subfolder `devices` (the orchestrator put
it there). Use that copy; never the original.

## Files you own

- `devices_catalogue.py` (new): loading and validation, no Flask.
- `plugin_devices.py` (new): the two read-only routes below.
- `plugin_device.py`: ONLY the assess route, to accept `device_id` (see 4).
- `device.py`: ONLY to add one small constructor (see 3). Do not change any existing function.
- `plugins.py`: ONLY to add `"plugin_devices"` to `PLUGIN_MODULES`.
- `.env.example` (document `DEVICES_DIR`) and `.gitignore` (add `devices/`).
- `tests/test_devices_catalogue.py` (new). You may add tests to `tests/test_device.py`; do not change existing ones.

## Requirements

### 1. Where the catalogue lives

`DEVICES_DIR` environment variable; default `devices/` next to `app.py`. The folder may be absent: the
catalogue is then empty (plus the synthetic example, below) and nothing fails. Read it fresh on each request
(files are small); no caching to invalidate.

### 2. Loading and validation (`devices_catalogue.py`)

`load_catalogue(directory) -> Catalogue` with the devices in a stable order and a list of human-readable
warnings. Never raise for bad content; a bad entry is skipped and named in `warnings`.

- Read `devices.json` if present (limit 5 MB; invalid JSON gives one warning and the CSV scan still runs).
- Then every `*.csv` in the folder that is not named by the `file` field of a JSON entry becomes a device with
  an id from its file name, parsed with `device.parse_power_matrix(text, "centres")` (limit 1 MB per file).
  Its metadata is absent: `period_type` unknown, `provenance` says there is no metadata entry.
- Always append a synthetic example built from `device.example_matrix_csv()`: id `synthetic-example`,
  `synthetic: true`, source "Synthetic example bundled with the app; not a real device".
- A device id matches `^[a-z0-9_-]{1,60}$` (JSON keys that do not are skipped with a warning). Duplicate ids:
  keep the first, warn about the rest.
- A JSON entry is valid only if: it is an object; `hs_m` and `period_s` are lists of 2 to 200 finite numbers,
  strictly increasing; `power_kw` is rectangular (`len(hs_m)` rows of `len(period_s)` cells) and every cell is a
  finite number >= 0 or `null`; at least one cell is defined; `rated_kw` and `width_m` are finite positive
  numbers or null; `name` is 1 to 60 printable characters (strip control characters). `notes` is a list of
  strings (drop non-strings). `period_type` is normalised to `"tp"`, `"te"` or `"unknown"` (case-insensitive;
  anything else, or missing, is `"unknown"`). A `bin_convention` field is optional and defaults to `"centres"`;
  it must be `"centres"` or `"lower_edges"`.
- Never open a path taken from the JSON content: `file` is compared by name only.

Each device exposes: `id`, `name`, `rated_kw`, `width_m`, `period_type`, `bin_convention`, `source`,
`provenance`, `notes`, `origin` (`"json"`, `"csv"` or `"example"`), `synthetic`, `hs_m`, `period_s`,
`power_kw` (rows, `None` for undefined) and the derived statistics:
`n_hm0`, `n_period`, `hm0_range`, `period_range`, `power_max_kw`, `defined_cells`, `total_cells`,
`defined_pct`, and `near_max_cells` (defined cells with power >= 99% of the table maximum, which is how a
plateau such as rated power or a colour-scale cap shows itself, whatever its cause).

### 3. A matrix from arrays (`device.py`)

Add `power_matrix_from_arrays(hm0_values, period_values, power_rows, bin_convention="centres") -> PowerMatrix`
that builds the same `PowerMatrix` the CSV parser builds (use the existing `_make_edges` and `_make_centres`;
`None` becomes NaN). Test that it equals `parse_power_matrix` of the same data (edges, centres and cells).

### 4. Routes (`plugin_devices.py`)

- `GET /api/devices` returns `{"configured": bool, "directory": <folder NAME only, never the full path>,
  "devices": [summary, ...], "warnings": [...]}`. `configured` is true when `DEVICES_DIR` is set. A summary has
  the identity and metadata fields and the statistics, and `n_notes`, but not the cells.
- `GET /api/devices/<id>` returns the summary plus `notes`, `hs_m`, `period_s`, `power_kw`, `bin_convention`,
  and `hm0_edges` and `period_edges` (length n+1, computed with the same rule the assessment uses for the
  entry's `bin_convention`), so the front end never re-derives bin edges. An id that does not match the pattern
  or does not exist gives a JSON 404, like the existing device routes.
- In `plugin_device.py`, the assess route accepts an optional `device_id`. When present, the matrix comes from
  the catalogue (404 if unknown; a 422 message if `csv_text` is also non-empty: one source only) and
  `bin_convention` defaults to the entry's unless the request gives one. Everything else is unchanged: the
  name, rated power, width and period type still come from the request. The saved result and the response get
  a `"catalogue"` object `{id, name, source, provenance, notes, synthetic}` (absent for uploads), and the
  markdown report section gets a line `Matrix source: ...` with the provenance and the notes, so an exported
  report says where the matrix came from.

### 5. Tests (`tests/test_devices_catalogue.py`)

Offline, with a temporary `DEVICES_DIR` (monkeypatch the environment; do not rely on the real folder). Cover:

- a valid JSON entry loads with correct statistics (hand-computed small case: counts, ranges, max, undefined
  cells, `near_max_cells`); period type normalisation (`Tp`, `te`, missing, junk);
- every rejection rule in 2, one test each, each producing a warning that names the device and not a crash:
  not an object, non-increasing axis, ragged rows, negative cell, NaN or string cell, all cells undefined, bad
  id, bad `rated_kw`, invalid JSON file, oversized file;
- duplicate ids; a JSON `file` field with a path (`../x.csv`) never opens anything; a stray CSV becomes a device
  and a CSV named by a JSON entry does not become a second one; a CSV whose rows end in undefined cells loads, and a malformed CSV is skipped with a warning naming the file;
- the synthetic example is always present and flagged; an empty or missing folder gives only the example and
  `configured` reflects the environment variable;
- the routes: list, detail (cells equal the file's, edges are the midpoint edges, `None` serialises as `null`),
  404 for unknown and malformed ids, the directory is a name and not a path;
- assess with `device_id` gives the same AEP as assess with the equivalent `csv_text` on the same job (use the
  existing job fixtures in `tests/test_device.py`); `device_id` plus `csv_text` is an error; unknown id is 404;
  the saved result and the report section carry the catalogue provenance.

## Acceptance

`python -m pytest -q` passes. With the app on port 5090 or above and `DEVICES_DIR` pointing at your scratch copy,
`curl` the list, one detail and one assess call and put the abbreviated responses in your report. Say plainly
what you could not check.
