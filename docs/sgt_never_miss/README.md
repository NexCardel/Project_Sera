# SGT never-miss: tracker and worker

The build of `docs/sgt-never-miss-blueprint.md` (with its section 10 owner answers and section 10a
review corrections), run unattended: one fresh Claude Code session per work package, on `main` in
the APP folder (owner decision 2026-10-14: no separate worktree or branch).

| File | What it is |
| :--- | :--- |
| `sgt-never-miss-plan.json` | the fixed plan: 17 WPs, dependencies, model per WP, CLI permissions, deadline |
| `sgt-never-miss-runner.md` | the prompt every worker session gets |
| `sgt-never-miss-status.csv` | one row per WP (written by the tool only) |
| `sgt-never-miss-runs.csv` | one row per worker run: time, tokens, outcome |
| `sgt-never-miss-decisions.csv` | choices workers made or asked you about |
| `sgt-never-miss-checks.csv` | owner steps the workers recorded for you (probes, soaks, replay diffs) |
| `sgt-never-miss-questions.csv` | questions asked by pop-up and their answers |
| `sgt-never-miss-report.md` | a summary, rebuilt after every run |

## Start, watch, stop

- **Start:** double-click `run_sgt_never_miss.bat` in the APP folder. It restarts itself if it
  crashes, sleeps through usage limits, and stops at the deadline (2026-10-17 23:00 IST).
- **Stop:** close its window. The WP that was running is retried next time.
- **Watch:** `venv\Scripts\python.exe tools\sgt_never_miss.py show`, or the report file, or a live
  Excel view (`... tools\sgt_never_miss.py viewer`, once, then open `sgt-never-miss-agents.xlsx`).
- **Questions:** a pop-up gives you 4 minutes; after that the worker's default is used. Answer
  later with `... tools\sgt_never_miss.py answer <Q> "<choice>"`.

## Restore point

Before the first WP ran, the code was tagged `restore-point/pre-sgt-never-miss`, and a full copy of
the repository was saved as a git bundle in `..\restore_points\`. To see everything the workers
changed: `git diff restore-point/pre-sgt-never-miss`. To go back, stop the dispatcher first, then
ask for help: going back throws away every commit made after the tag.
