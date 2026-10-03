# WP12: server hardening and startup safety

Goal: a stranger who clones the repo and runs it on Windows cannot be hurt by a web page they happen to visit,
cannot silently run two copies of the app on one port, and gets a clear error (never a 500 or a traceback) from
bad input and bad files.

Findings behind this package come from an independent review (all reproduced against `main` at 3e5535b):
the server answers requests that claim to come from another website (DNS rebinding), a second copy of the app
starts without error on the same port on Windows and rewrites the first copy's job files, malformed JSON
returns HTTP 500, one odd `job.json` can stop the app starting, and the closed device picker leaves an
invisible full-screen layer over the page at phone width.

## Who does what (read this)

- **Codex (independent verifier)** writes the acceptance tests in `tests/test_server_hardening.py` from THIS spec,
  before any implementation exists. Those tests are red on the current code where behaviour is missing.
- **Antigravity (you, the implementer)** makes the tests pass by changing the implementation files below. You must
  NOT edit, delete, rename or weaken `tests/test_server_hardening.py`. If you believe a test is wrong, say so
  under "Requests to the orchestrator" and leave it alone.
- **Claude (orchestrator)** reviews, re-runs everything and checks the interface in a browser.

## Files you own (implementation)

`app.py`, `devices_catalogue.py`, `static/plugins/devices.css`, `README.md` (only the lines the requirements name),
`.env.example`. Do not edit any other file. `fetch_era5_waves.py` is out of scope here (WP13).

## Requirements

### 1. Host and Origin checks (DNS rebinding)

- Every request is rejected with HTTP 403 and a JSON body `{"error": "..."}` unless the hostname part of its
  `Host` header is `127.0.0.1`, `localhost` or `[::1]` (any port), or is listed in the optional environment
  variable `ALLOWED_HOSTS` (comma-separated hostnames; empty or unset means no extras). Compare hostnames
  case-insensitively; a missing `Host` header is rejected.
- For `POST`, `PUT`, `PATCH` and `DELETE`: if an `Origin` header is present, its `scheme://host[:port]` must equal
  the request's own origin (same `Host`); otherwise 403. A request with no `Origin` header (curl, scripts) is
  allowed. `GET` and `HEAD` ignore `Origin`.
- The Flask test client sends `Host: localhost`; existing tests must keep passing unchanged.
- Document `ALLOWED_HOSTS` in `.env.example` and in one sentence in the README section "Jobs, files and security"
  (it must say the app is for the local machine only).

### 2. One copy per port, and no job rewriting on import

- Importing `app` must not modify any file under the downloads folder. Move the step that marks interrupted
  `queued`/`running` jobs as failed out of import time: `load_jobs(mark_interrupted=False)` loads jobs read-only
  (default), and `main()` (the `__main__` path) calls it with `mark_interrupted=True` only after the port check.
  Keep `app.load_jobs` callable from tests with both values.
- Add `main() -> int` to `app.py` (the `if __name__ == "__main__":` block becomes `sys.exit(main())`). It reads
  `PORT` from the environment (default 5000) and binds `127.0.0.1`. Before starting the server it checks whether
  something already accepts connections on that host and port (`socket.connect_ex`). If so it prints
  `error: port <n> is already in use (another copy of the app?). Stop it or set PORT.` to standard output and
  RETURNS 1 without touching any job file and without starting a server. Otherwise it calls
  `load_jobs(mark_interrupted=True)` and runs the server (`threaded=True`) and returns 0 when it stops. Tests call
  `app.main()` with `PORT` set to a port they have occupied. This matters because Windows lets two processes bind
  the same port without an error.

### 3. Bad input is a 400, not a 500

Any request body or field of the wrong type must give HTTP 400 and `{"error": "<short reason>"}`: a JSON body that
is not an object (`[1]`, `"x"`, `5`), `groups` or `params` that are not lists of strings (`5`, `"a"`, `[5]`), a
non-string `product`, `expver`, `start`, `end`, a non-number `latitude`, `longitude`, `buffer`, `time_step`.
Fix it where the types are first used (`create_job` and the validators it calls, including
`fetch_era5_waves.normalise_options` callers: you may NOT edit `fetch_era5_waves.py`, so validate types in
`app.py` before calling it). The same rule applies to every other JSON-taking route that `app.py` owns.

### 4. Startup and catalogue robustness, and no local paths in errors

- A `job.json` that is valid JSON but not an object, or lacks required keys, is skipped with a log line
  (`logging`), never raises, never stops startup. Truncated or invalid JSON keeps being skipped as today.
- `devices_catalogue.py`: one entry that raises any exception during validation (for example a 400-digit integer
  cell that makes `numpy.isfinite` raise `TypeError`) is skipped with a warning naming the device, and never
  hides another device. Catch `(ValueError, TypeError, OverflowError)` per entry; do not catch bare `Exception`.
- Routes that read a job's NetCDF files (`analysis`, `nodes`, `timeseries.csv`, `screening`, `longterm`,
  `export/*`) must turn an unreadable or corrupt data file (`OSError`, or a `ValueError` from the reader) into
  HTTP 422 with a message that names the file by NAME ONLY (for example `The data file era5_2022-01.nc could
  not be read: it may be incomplete or corrupt.`), never an absolute path, never a bare 500.

### 5. The closed device picker must not cover the page

`static/plugins/devices.css`: at widths of 600 px or less the author rule `display: flex` on
`.device-picker-dialog` overrides the browser's `dialog:not([open]) { display: none }`, so a CLOSED dialog stays
visible as a full-screen invisible layer. Make `display: flex` apply only to `.device-picker-dialog[open]` (and add
an explicit `.device-picker-dialog:not([open]) { display: none; }`). No other layout change.

## Acceptance

- `python -m pytest -q` passes with the new tests unchanged.
- Report the literal outputs ROUND3.md requires, plus: the output of a manual check with the app on a free port
  (5090 or above, with scratch `DOWNLOADS_DIR`): `curl.exe -s -o NUL -w "%{http_code}" -H "Host: evil.example"
  http://127.0.0.1:<port>/api/jobs` must print 403; starting a second copy on the same port must print the
  error message and exit status 1 (state the exit code you saw).
- You cannot judge appearance: the orchestrator verifies item 5 in a browser at 375 px.
