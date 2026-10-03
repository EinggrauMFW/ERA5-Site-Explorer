# WP5: continuous integration, dependency bounds, line endings, warnings

Goal: the repository tests itself on every push, installs the same way for a stranger as for the author,
and runs the test suite without noise.

## Files you own

- `.github/workflows/ci.yml` (new)
- `.gitattributes` (new)
- `requirements.txt`, `requirements-dev.txt`
- `pytest.ini` (new, only if you need `filterwarnings`)
- `crosscheck.py` (only guards for warnings, see 4) and `tests/test_provenance_crosscheck.py`

## Requirements

### 1. GitHub Actions workflow

`.github/workflows/ci.yml`: run on `push` to `main` and on every `pull_request`. A matrix of
`ubuntu-latest` and `windows-latest` crossed with Python `3.12` and `3.13`. Steps: checkout, set up Python
with pip caching, `pip install -r requirements-dev.txt`, `python -m pytest -q`. `permissions: contents: read`,
and cancel superseded runs of the same ref. You cannot run Actions here: validate the YAML by parsing it with
Python, and say plainly in your report that the workflow itself has not been executed. Use only official
actions (`actions/checkout`, `actions/setup-python`) at major versions you are sure exist.

### 2. Dependency bounds

- `cdsapi>=0.7.7,<1` (the CDS guide recommends 0.7.7 or newer).
- The suite has only been run with the newest packages (numpy 2.5, pandas 3.0, scipy 1.18, xarray 2026.9,
  netCDF4 1.7, Flask 3.1). In a throwaway virtual environment outside the repo, install the OLDEST version of
  each package that has a Python 3.13 wheel and still satisfies the current lower bound (for example numpy
  2.1, pandas 2.2.3, scipy 1.14, xarray 2025.1, netCDF4 1.7, Flask 3.0) and run the suite. If it passes, keep
  the bounds you tested as the lower bounds. If it fails, raise the bound to the oldest version that passes
  and report the failure. Add an upper bound of the next major version only for numpy (`<3`), pandas (`<4`),
  scipy (`<2`) and netCDF4 (`<2`); leave xarray without one.
- Report exactly which versions you tested as lower bounds and which as latest.
- `requirements-dev.txt` keeps `-r requirements.txt`, pytest and the optional `wavespectra`.

### 3. Line endings

`.gitattributes` with `* text=auto eol=lf` and `*.jpg`, `*.png`, `*.nc`, `*.zip` marked `binary`. (The author
works on Windows with `core.autocrlf=true`, which makes git print "LF will be replaced by CRLF" on every
commit. You cannot run git; the orchestrator will check the effect.)

### 4. Warnings

A clean install produces about 235 warnings in the suite (5 in the author's environment). Find out where
they come from: run `python -m pytest -q -rw` in a clean virtual environment with the latest packages and
group them by warning class and by source file. Then:

- A warning raised by our own code on a legitimate edge case (for example a correlation of a constant series)
  is fixed at the source with a guard that returns the same value the code returns today for that case
  (`None` or NaN as it already does). You may do this ONLY in `crosscheck.py`. Prove with a test that the
  result is unchanged and the warning is gone.
- A warning raised from our code in a file you do not own: list it with file, line, class and count under
  "Requests to the orchestrator". Do not edit that file.
- A warning raised inside a third-party library that we cannot fix: filter it in `pytest.ini` with the most
  specific `filterwarnings` entry possible (class, message and module) and a comment saying why. Do not use a
  blanket `ignore`.
- Never turn warnings into errors globally.

Report a before and after table: warning class, source, count.

## Acceptance

- `python -m pytest -q` passes in the repo's own environment and in the clean environment.
- The workflow YAML parses; `.gitattributes` exists; bounds are justified by the runs you did.
- No numerical result changes.
