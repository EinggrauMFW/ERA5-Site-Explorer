# Round 2 work packages: read this first, every time

You are implementing ONE work package of `ERA5-Site-Explorer` (a local Flask + vanilla-JS app that
downloads ERA5 wave data and analyses it). The orchestrator (Claude) reviews and approves your work.
Other agents work in parallel on other packages in the same folder. This file adds to, and where it
conflicts overrides, `CONTRACT.md` (round 1). Read `CONTRACT.md` too: its interface description of
`plugins.py` and `window.EraExplorer` still holds.

Repo root: the folder that contains `app.py` (Windows, Python 3.13, Git Bash or PowerShell). Run
`python -m pytest -q` from the repo root. The full suite must pass when you finish (193 pass today).

## Hard rules

1. **Ownership.** Edit ONLY the files your package lists as yours. Create new files only where it says so.
   If you need a change elsewhere, do not make it: put it under "Requests to the orchestrator".
2. **No git.** Do not run `git add`, `commit`, `checkout`, `reset`, `stash`, `push` or anything that changes
   history or the working tree outside your files. Reading (`git status`, `git diff`, `git log`) is fine.
3. **Do not touch running things.** The user may have the app or a CDS download running on port 5000. Never
   stop, restart or kill any process you did not start, and never bind port 5000. To try the app, use port
   5090 or above with `PORT=<n> DOWNLOADS_DIR=<scratch folder> python app.py`, and stop it when done. Never use
   the repo's `downloads/` folder for experiments: it holds the user's real data. Copy what you need to a
   scratch folder.
4. **No network, no credentials.** Tests run offline and never contact CDS. Do not read or write
   `~/.cdsapirc`. The one exception is package 5, which may `pip install` into a throwaway virtual
   environment outside the repo.
5. **Do not invent.** No invented citations, standards, definitions, numbers or API behaviour. If you could not
   verify something, say so in your report. If a library behaves differently from what you assumed, say what
   you observed.
6. **Do not weaken tests.** Never delete or loosen an existing test to make it pass. If an existing test is
   wrong because the behaviour is changing on purpose, change it minimally and say why in your report.
7. **Match the surrounding code**: same style, comment density and naming. Comments explain why, not what.
   No dead code, no debugging prints, no TODO stubs, no leftover reasoning in comments (such as "But wait,
   ..."). Use `textContent` and never `innerHTML` with data in JavaScript. Wrap any new plugin script in an
   IIFE. Every number shown to a user states its unit.
8. **Units and JSON.** Hm0 in m, periods in s, flux in kW/m. API results use `None` for NaN or inf.
9. **Old data must keep working.** Existing `job.json` files and cached `analysis*.json` files do not have
   your new fields. Code must treat every new field as optional.
10. **Report honestly.** If something does not work or you could not test it, say so. Do not claim a manual
    check you did not do.

## What to include in your final reply

1. Files created and files edited (confirm nothing else was touched).
2. For each requirement of your package, one line: how it is met and where (file and function).
3. The full tail of `python -m pytest -q` (passed, failed, skipped, runtime).
4. Decisions you made where the spec was silent.
5. "Unverified" (what you could not test) and "Requests to the orchestrator".
