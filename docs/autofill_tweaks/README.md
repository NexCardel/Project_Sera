# Autofill tweaks

Fixes and redesign for every way Sera fills or offers a portal login: Fast Autofill, SMTI, MECP,
SCA and SCC (moving to SGT's UIA reads). Run the SGT-overhaul way: one fresh agent session per work
package, tracked in CSVs, with a live Excel viewer.

| File | What | Who writes it |
| :--- | :--- | :--- |
| `autofill-tweaks-blueprint.md` | the design: rules, findings #1–#18, Parts A–H, decisions D1–D8, hand-off notes | you (design) / workers (hand-off notes) |
| `autofill-tweaks-plan.json` | 25 work packages: deps, model, instructions, CLI permissions, deadline | edit by hand only to re-plan |
| `autofill-tweaks-runner.md` | the prompt every worker session gets | edit by hand |
| `autofill-tweaks-status.csv` | one row per WP | `tools/autofill_tweaks.py` only |
| `autofill-tweaks-runs.csv` | one row per agent run: model, minutes, tokens, cost, outcome | tool only |
| `autofill-tweaks-questions.csv` | questions workers asked you, and the answers | tool only |
| `autofill-tweaks-decisions.csv` | choices workers made or that you answered | tool only |
| `autofill-tweaks-checks.csv` | hands-on checks only a person can do | tool only (you mark results) |
| `autofill-tweaks-report.md` | summary, rebuilt after every run | tool only |

The dispatcher is the SGT overhaul's own (`tools/sgt_overhaul.py`), pointed at this folder by
`tools/autofill_tweaks.py`, so it behaves identically: retries (3 attempts, then Blocked), sleeps
through usage limits with a live countdown, 150-minute cap per run, pop-up questions that fall back
to the default after 4 minutes, a lock so only one dispatcher runs, and workers can never push
(the repository's pre-push hook).

## Setup (done 2026-09-28)

Worktree `..\APP-autofill` on branch `autofill-tweaks`, created from `main` at c1f924c (Part H of the
blueprint was already on `main`). The first commit on the branch adds this folder, the dispatcher
wrapper, the launchers and the `use_project()` switch in `tools/sgt_overhaul.py`.
Deadline: **2026-09-29 01:30 IST** (`autofill-tweaks-plan.json`); the dispatcher stops there and
whatever is not Done stays Not started / Retry for a later run.

## Running

From `..\APP-autofill`:

* `run_autofill_tweaks.bat` — starts the dispatcher (restarts itself if it crashes).
* `watch_autofill_worker.bat` — live view of what the current worker is doing.
* `..\APP\venv\Scripts\python.exe tools\autofill_tweaks.py viewer` — builds the live Excel viewer once
  (`autofill-tweaks-agents.xlsx`; close it in Excel before rebuilding).
* `... tools\autofill_tweaks.py show [WP]`, `next`, `report`.
* Answer a question without the pop-up: `... tools\autofill_tweaks.py answer <Q> "<choice>"`.
* Record a hands-on check: `... tools\autofill_tweaks.py check <n> --result Pass --notes "..."`.

Nothing merges to `main` automatically. W6-R writes the merge-readiness note and the list of checks
to run by hand before you merge.
