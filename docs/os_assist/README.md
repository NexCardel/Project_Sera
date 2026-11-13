# OS Assist

SMTI, MECP and SCA as docked Sera desktop cards that look and behave like today's in-page cards, but
fill login boxes from outside the browser: UI Automation `SetValue`, then `SendInput` keystrokes as
the fallback. No extension needed for these three (Fast Autofill stays on the extension). Run the
SGT-overhaul way: one fresh agent session per work package, tracked in CSVs, with a live Excel viewer.

| File | What | Who writes it |
| :--- | :--- | :--- |
| `os-assist-blueprint.md` | the design: rules §0, what exists §2, design §4, decisions §6, owner checks §7, hand-off notes §9 | design / workers (§9 notes) |
| `os-assist-plan.json` | 17 work packages (O0-1 … O4-R): deps, model, instructions, CLI permissions, deadline | edit by hand only to re-plan |
| `os-assist-runner.md` | the prompt every worker session gets | edit by hand |
| `os-assist-status.csv` | one row per WP | `tools/os_assist.py` only |
| `os-assist-runs.csv` | one row per agent run: model, minutes, tokens, cost, outcome | tool only |
| `os-assist-questions.csv` | questions workers asked you, and the answers | tool only |
| `os-assist-decisions.csv` | choices workers made or that you answered | tool only |
| `os-assist-checks.csv` | hands-on checks only a person can do | tool only (you mark results) |
| `os-assist-report.md` | summary, rebuilt after every run | tool only |

Reference code from the WP0 spike: `tools/pre_dev/os_assist/fill_spike.py` and `test_login.html`.

The dispatcher is the SGT overhaul's own (`tools/sgt_overhaul.py`), pointed at this folder by
`tools/os_assist.py`: retries (3 attempts, then Blocked), sleeps through usage limits, 150-minute cap
per run, pop-up questions that fall back to the default after 4 minutes, one dispatcher at a time,
and workers can never push.

## Phases

| Phase | WPs | Models |
|---|---|---|
| 0 | O0-1 baseline | haiku |
| 1 core | O1-1 worker + locate, O1-2 fill + one-writer test, O1-3 secure clipboard, O1-R review | sonnet, opus review |
| 2 cards | O2-1 dock base, O2-2 SMTI, O2-3 MECP, O2-4 SCA card + chip, O2-R review + shots | opus, sonnet |
| 3 wiring | O3-1 engine setting, O3-2 SMTI/MECP launch, O3-3 SCA engine, O3-4 SCC card, O3-R review | sonnet, opus |
| 4 finish | O4-1 full suite + leak/idle tests, O4-R final review + merge note | sonnet, opus |

All decisions are settled (D3, 2026-11-08: the ✎ button keeps opening the SCK widget), so no
question pops up by design; workers may still ask about something the blueprint doesn't settle. Deadline in the plan: **2026-11-15 23:00 IST** — change `deadline` in the plan if needed.

## Setup (once)

1. Commit this folder, `tools/os_assist.py`, `run_os_assist.bat` and `watch_os_assist_worker.bat` on
   `main`. The tracker CSVs are matched by `.gitignore`'s `*.csv`, so add them with `git add -f`.
2. Create the worktree from `main`:
   `git worktree add ..\APP-os-assist -b os-assist main`

## Running

From `..\APP-os-assist`:

* `run_os_assist.bat` — starts the dispatcher (restarts itself if it crashes).
* `watch_os_assist_worker.bat` — live view of what the current worker is doing.
* `..\APP\venv\Scripts\python.exe tools\os_assist.py viewer` — builds the live Excel viewer once.
* `... tools\os_assist.py show [WP]`, `next`, `report`.
* Answer a question without the pop-up: `... tools\os_assist.py answer <Q> "<choice>"`.
* Record a hands-on check: `... tools\os_assist.py check <n> --result Pass --notes "..."`.

Nothing merges to `main` automatically. O4-R writes `merge-readiness.md` and the list of checks to
run by hand before you merge. Until then the engine setting defaults to **Extension**, so even a
merged build behaves as today until you switch it to OS.
