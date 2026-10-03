# Round 3 work packages: read this first, every time

You are implementing ONE work package of `ERA5-Site-Explorer`. The orchestrator (Claude) reviews and approves
your work. This file adds to `ROUND2.md`, which you must read in full, and to `CONTRACT.md` (the interfaces of
`plugins.py` and `window.EraExplorer`). Where they conflict, this file wins.

Repo root: the folder that contains `app.py` (Windows, Python 3.13, Git Bash or PowerShell). The suite
(`python -m pytest -q`) passes today with 217 tests and must still pass when you finish.

## Rules that earlier agents broke (the orchestrator checks every one)

1. **Never run a git command that changes anything**: no `checkout`, `restore`, `stash`, `reset`, `clean`, `rm`,
   `add`, `commit`. An earlier agent reverted its own work with one of them and then reported the work as done.
   Everything in the repo is committed. If you break something, fix it by editing.
2. **Never report work you did not do.** Your final report must contain the literal output of these commands,
   run at the very end:
   - `git status --short`
   - `git diff --stat`
   - `node --check <file>` for every JavaScript file you created or edited
   - `python -m pytest -q` (the tail, with the counts)
   - `git diff -U0 | grep -nP "^\+.*[ \t]+$"` (trailing whitespace on added lines; it must print nothing; the
     `-P` flag matters, plain `-E` reads `\t` as the letter t). New untracked files: `grep -nP "[ \t]+$" <file>`.
   A file you say you created or edited must appear in `git status --short`. A test you say you wrote must
   appear in `python -m pytest --collect-only -q`.
3. **Ownership.** Edit only the files your package lists. A new file must be listed too.
4. **Tests are required, not optional.** Offline, deterministic, written against independently computed
   values. A test file with no tests is a failure.
5. **No invented facts.** About data, sources, formats or library behaviour: say what you observed, and list
   what you could not verify.
6. **Style.** Match the surrounding code. Comments say why, not what. No dead code, no leftover reasoning in
   comments. `textContent`, never `innerHTML` with data. Plugin scripts are wrapped in an IIFE. Colours come
   from the CSS variables, so both themes work. Every number shown to a user states its unit.
7. **Do not touch running things.** Never use port 5000; to try the app use port 5090 or above with
   `DOWNLOADS_DIR` and `DEVICES_DIR` pointing into your scratch folder; stop it when done. Never read or write
   the repo's `downloads/` folder. Never read or modify the user's own data folder
   `D:\forthewave\claude_works\ecmwf-map-app-devices`: copy what you need into your scratch folder.
