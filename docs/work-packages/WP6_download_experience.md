# WP6: download experience (progress, resume, probe, clean log)

Goal: a long CDS download shows how far it has got, survives an app restart without starting over, can ask
CDS what it will accept before committing hours, and writes a readable log.

Facts from the author's real downloads: each CDS request queued for 3 to 8 minutes; one log line from
`cdsapi` appears twice (once with a timestamp, once plain); a spectra job whose 16 chunk files were all on
disk was left as "failed" after an app restart, with the message "Submit the request again to resume", which
today creates a NEW job and downloads everything again.

## Files you own

- `fetch_era5_waves.py`, `app.py`
- `static/app.js` (only the job-status and request-form code: `renderJob`, `poll`, the form submit and the
  buttons around `#cancel`, `#delete`, `#submit`, `#dry-run`; do not touch map, analysis or tab code)
- `templates/index.html` (only the status card and the request form buttons)
- `static/app.css` (only new classes for the items below)
- `tests/test_fetcher.py`, `tests/test_app.py`, and new `tests/test_progress.py`, `tests/test_resume.py`

Another agent edits `screening.py`, `analysis.py` and their tests at the same time: do not touch those.

## Requirements

### 1. Progress

- The fetcher prints a machine-readable line after every request that is saved or skipped (an existing file),
  and once with `done = 0` before the first: `::progress:: {"done": D, "total": T, "skipped": S}` followed by a
  flush. `total` is `done` plus the number of requests still to make, planned with the shape currently
  accepted (the same `fit_unit` logic the loop uses, applied to the rest of the current queue and to every
  later month). It may change during a run, when CDS refuses a shape and the plan is re-cut. `skipped` counts
  requests whose file already existed.
- `app.py` `run_job` recognises these lines: it stores `job["progress"] = {"done", "total", "skipped",
  "eta_seconds"}` and does NOT append them to the log. `eta_seconds` is the median duration of the requests
  that were actually downloaded in this run (not the skipped ones) times `total - done`, and `None` until at
  least two downloaded requests have completed. Use the median, not the mean, because queue times vary a
  lot. `progress` is saved in `job.json` and returned by `public_job`.
- The UI shows, in the status card, a determinate bar and a line such as `7 of 16 requests · 2 already on
  disk · about 25 min left (estimate: CDS queue times vary)`. The ETA part is omitted when `eta_seconds` is
  `None`. The existing indeterminate `#progress` bar stays for jobs without `progress` (old jobs, dry
  runs, probes). Format durations in minutes, or hours and minutes. No fake precision.

### 2. One log line, not two

Find out why `cdsapi` messages appear twice (the fetcher calls `logging.basicConfig(...)`, and the library
may add its own handler) and make each message appear once, keeping the timestamped form. Move the logging
setup into a small function you can test (for example that the library's logger ends up with exactly one
effective output path). Report what the cause was.

### 3. Remember what CDS accepts

The fetcher learns the largest request shape CDS accepts (`state["hours"]`, `state["cap_days"]`). Write it to
`<output>/learned_shape.json` whenever it changes, and read it at the start of a run if it exists, so a
resumed job does not repeat refusals. Validate on read: integers, `1 <= hours <= len(all_hours)` and
`1 <= cap_days <= 31`; ignore the file if anything is wrong or missing. Test the round trip and the invalid
cases.

### 4. Resume

- Refactor `create_job` so the fetcher command is built by a function from a job record
  (`build_command(job)`), used by both create and resume.
- `POST /api/jobs/<id>/resume`: allowed only for a job whose status is `failed` or `cancelled` and that is not
  a preview (`dry_run` or `probe`). Any other state returns 409 with an error message; an unknown job 404. It
  resets the job in place (same id and folder): status `queued`, `return_code` `None`, `progress` removed, a
  log line `Resumed: files already on disk are kept.`, then submits it to the executor. The fetcher already
  skips non-empty existing files, so no fetcher change is needed for this beyond item 3. Do not delete
  anything.
- Change the message written for a job interrupted by an app restart to say that **Resume** continues it.
- UI: a **Resume download** button in the status card, visible for `failed` and `cancelled` jobs that are not
  previews. It POSTs, then polls as for a new job. Show the server's error text if it fails.
- Tests (offline, with the executor and the fetcher faked): resume of failed and of cancelled works and
  re-queues the same id; running, queued, complete and preview jobs return 409; unknown id 404; the built
  command equals what create produced for the same fields; old `job.json` without new fields resumes.

### 5. Ask CDS before committing (probe)

- `POST /api/jobs` accepts `"probe": true`: the same validation as a normal request, command ends with
  `--probe`, the job record gets `probe: true`. A probe job is a PREVIEW everywhere a `dry_run` job is
  treated as one: it never counts as a finished download, never appears in the analysis or cross-check
  pickers, never triggers `loadAnalysis`, and is labelled `probe` in History. Add one helper (for example
  `is_preview(job)` in Python and the equivalent in `app.js`) and use it at every place that tests `dry_run`
  today; list them in your report.
- UI: a **Check what CDS accepts** button next to the preview checkbox, shown only for the 2D spectra and
  MARS products. It submits the form with `probe: true`. When the job completes, show in the status card a
  short plain-language summary built from the probe's own output lines (which one-day shapes CDS accepted
  and which it refused). Derive the exact strings from `run_probe` in the fetcher; do not guess its format.
  Say in the card that nothing was downloaded.
- Test the command building, the validation, the `probe` flag and that a probe job is excluded wherever a
  preview is.

## Acceptance

- Full suite passes; no network in tests; old jobs load and display.
- Start the app on a port of 5090 or above with `DOWNLOADS_DIR` pointing at a scratch folder, create a dry-run
  job and a probe job through the API using `curl`, and report what came back. You cannot test a real CDS
  download: say so.
- You may not change what the fetcher requests from CDS, the request shapes, file names or `provenance.json`
  layout (apart from adding nothing to it).
