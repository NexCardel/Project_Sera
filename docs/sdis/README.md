# Sera Distill (SDIS)

An engine that finds a portal page's useful datapoints by itself, by comparing the same page
across clients with counting and statistics only (no AI). You pick the datapoints in the **Distill…**
dialog (Tracker dump → Tools ▾), and the ones you pick are captured on every PC. Run the way the
SGT overhaul and Autofill tweaks were: one fresh agent session per work package, tracked in CSVs, with
a live Excel viewer.

| File | What | Who writes it |
| :--- | :--- | :--- |
| `sdis-blueprint.md` | the design: rules, problems, Parts A–U (R = Firefox, S = containers, T = portal registration, U = field library), decisions D1–D23, build order, hand-off notes | you (design) / workers (hand-off notes, taken decisions) |
| `make_plan.py` → `sdis-plan.json` | 30 work packages: deps, model, step-by-step instructions, CLI permissions, deadline | edit `make_plan.py` and run it to re-plan |
| `sdis-runner.md` | the prompt every worker session gets | edit by hand |
| `sdis-status.csv` | one row per WP | `tools/sdis.py` only |
| `sdis-runs.csv` | one row per agent run: model, minutes, tokens, cost, outcome | tool only |
| `sdis-questions.csv` | questions workers asked you, and the answers | tool only |
| `sdis-decisions.csv` | choices workers made or that you answered | tool only |
| `sdis-checks.csv` | hands-on checks only a person can do | tool only (you mark results) |
| `sdis-report.md` | summary, rebuilt after every run | tool only |
| `sdis-regress-baseline.txt` | the regression runner's counts before any change (W0-1) | W0-1 |

The dispatcher is the SGT overhaul's own (`tools/sgt_overhaul.py`), pointed at this folder by
`tools/sdis.py`, so it behaves identically: retries (3 attempts, then Blocked), sleeps through usage
limits with a live countdown, 150-minute cap per run, pop-up questions that fall back to the default
after 4 minutes, a lock so only one dispatcher runs, and workers can never push.

## Setup (done 2026-10-03)

Worktree `..\APP-sdis` on branch `sdis`, created from `main` at `0f30543` (the pre-dev engine:
alignment merge, client grouping, page memory, 14 tests). The first commit on the branch adds this
folder, the dispatcher wrapper and the launchers. Deadline: **2026-10-06 01:30 IST** (Tuesday)
(`sdis-plan.json`); the dispatcher stops there and whatever is not Done stays Not started / Retry for
a later run.

The real captures the regression runner reads stay in `..\APP\tools\pre_dev\class_diff\output\`
(git-ignored, client data). Workers only read them, and print counts.

## Running

From `..\APP-sdis`:

* `run_sdis.bat` — starts the dispatcher (restarts itself if it crashes).
* `watch_sdis_worker.bat` — live view of what the current worker is doing.
* `..\APP\venv\Scripts\python.exe tools\sdis.py viewer` — builds the live Excel viewer once
  (`sdis-agents.xlsx`; close it in Excel before rebuilding).
* `... tools\sdis.py show [WP]`, `next`, `report`.
* Answer a question without the pop-up: `... tools\sdis.py answer <Q> "<choice>"`.
* Record a hands-on check: `... tools\sdis.py check <n> --result Pass --notes "..."`.

Nothing merges to `main` automatically. W5-R writes the merge-readiness note and the list of checks
to run by hand before you merge.
