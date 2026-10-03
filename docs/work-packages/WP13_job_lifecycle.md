# WP13: job lifecycle: wedges, orphans, races and the missing-credentials message

Goal: a long download job cannot silently stall, cannot leave an orphan fetcher that races a resumed one, and a
first-time user who has no CDS credentials is told what to do instead of seeing a traceback.

Findings behind this package come from an independent review of `main` at 3e5535b (reproduced there with a
fake fetcher): a failed `job.json` save inside the output-reading loop leaves the child blocked on a full pipe
and the job stuck at "running"; a hard kill of the app leaves the fetcher alive for one in-flight CDS request,
and Resume then starts a second fetcher on the same folder; Cancel followed by Resume can mark the resumed job
failed while its child runs; a missing `~/.cdsapirc` prints a full traceback containing the user's home path.

## Who does what (read this)

- **Codex (independent verifier)** writes the acceptance tests in `tests/test_job_lifecycle.py` from THIS spec,
  before any implementation exists. They are red on the current code where behaviour is missing.
- **Antigravity (you, the implementer)** makes them pass. You must NOT edit, delete, rename or weaken
  `tests/test_job_lifecycle.py`. If you think a test is wrong, say so under "Requests to the orchestrator".
- **Claude (orchestrator)** reviews and re-runs everything.

## Files you own (implementation)

`app.py` (only `run_job`, `resume_job`, `cancel_job`, `load_jobs`, `build_command` and helpers you add for them),
`fetch_era5_waves.py` (only `main` start-up and the client creation), `README.md` (only the lines named below).
WP12 edits other parts of `app.py` at the same time as a separate package: keep your changes inside the functions
named above so the two merge cleanly.

## Requirements

### 1. The output reader never stops draining

In `run_job`, an exception while saving `job.json` (`OSError`, including `PermissionError` from `os.replace` on
Windows when a scanner holds the file) must not end the loop that reads the child's output. Catch `OSError` around
`save_job` in that loop, `logging.warning` once per distinct failure, keep draining stdout, and retry the save on
later lines. The job must still reach its true final status. Do not let a failure to save change a job's status.

### 2. No orphans, and no second fetcher on a live folder

- Store the fetcher's process id in the job record (`pid`) when it starts, and remove it when it exits.
- `POST /api/jobs/<id>/resume` returns 409 `{"error": "A fetcher for this job (pid N) is still running. Wait for
  it to finish or stop it."}` while that pid is alive. Provide `pid_alive(pid)` in `app.py` WITHOUT a new
  dependency: `os.kill(pid, 0)` on POSIX, and on Windows `ctypes` `OpenProcess` +
  `GetExitCodeProcess` (alive when the exit code is `STILL_ACTIVE`, 259). A pid that is not an int is not alive.
- The fetcher accepts `--parent-pid <int>` (optional). When given, a daemon watchdog thread started in `main`
  polls whether that process is alive every 2 seconds and, when it is gone, prints `parent process ended; stopping`
  and exits with `os._exit(3)`. `app.build_command` passes `--parent-pid os.getpid()`. A job whose parent died
  therefore ends within seconds instead of finishing an in-flight CDS request. (`--dry-run` and `--probe`
  ignore the watchdog.)
- `load_jobs(mark_interrupted=True)` (WP12 owns the flag): for an interrupted job whose recorded `pid` is still
  alive, write the log line `The fetcher from the earlier run (pid N) is still running; Resume is blocked until it
  exits.` instead of only the "Interrupted" line.

### 3. Cancel then Resume cannot mislabel a run

Give each (re)start of a job a generation number (`job["run"]`, incremented in `create_job` and `resume_job`).
`run_job` captures it and writes the final status, return code and `pid` removal ONLY if `job["run"]` still
equals the captured value; otherwise the finished run is a stale one and must not change the job. Cancelled jobs
keep status `cancelled` as today.

### 4. A missing `~/.cdsapirc` and an unaccepted licence are explained, not dumped

In `fetch_era5_waves.main`, creating the CDS client (`cdsapi.Client(...)`) must be wrapped. If it raises because the
configuration file is missing or incomplete, print exactly
`error: CDS credentials were not found. Create ~/.cdsapirc as described in the README section "CDS API access",
then run again.` and return exit status 2, with NO traceback and NO absolute path in the output. If a request
fails with a message that mentions a licence that must be accepted, print
`error: CDS says a licence has not been accepted for this dataset. Open the dataset page on the CDS website,
accept its terms while logged in, then run again.` followed by the CDS message, and return exit status 1.
Everything else keeps its current behaviour.

## Acceptance

- `python -m pytest -q` passes with the new tests unchanged.
- Report the literal outputs ROUND3.md requires, plus a manual check on a free port (5090 or above, scratch
  folders): start the app, start a job with a fake long-running fetcher if you have one, kill the app with
  `taskkill /F`, and state what happened to the child within 10 seconds (it must exit by itself).
