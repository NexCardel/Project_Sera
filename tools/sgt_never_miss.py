"""
tools/sgt_never_miss.py
-----------------------
Tracker and dispatcher for the SGT never-miss project (docs/sgt-never-miss-blueprint.md).

The same machinery as the SGT overhaul, Autofill tweaks and SDIS (tools/sgt_overhaul.py), pointed at
this project's folder: one fresh CLI session per work package, model per WP, retries, usage-limit
sleeps with a live timer, questions by pop-up (4 minutes, then the default), decisions and hands-on
checks in CSVs, a live Excel viewer. Workers run in the main APP folder, on main (owner decision
2026-10-14: no separate worktree or branch).

Files (all in docs/sgt_never_miss/):
  sgt-never-miss-plan.json     fixed plan: WPs, deps, models, CLI permissions   (edit by hand)
  sgt-never-miss-runner.md     the prompt every worker session gets              (edit by hand)
  sgt-never-miss-status.csv / -runs.csv / -decisions.csv / -checks.csv / -questions.csv   (this tool only)
  sgt-never-miss-report.md / sgt-never-miss-agents.xlsx   read-only views

  PY=venv/Scripts/python.exe
  $PY tools/sgt_never_miss.py show [WP]
  $PY tools/sgt_never_miss.py next
  $PY tools/sgt_never_miss.py run [--once] [--dry-run]
  $PY tools/sgt_never_miss.py watch | report | viewer
  $PY tools/sgt_never_miss.py ask WP3 --question "..." --options "A|B" --default A
  $PY tools/sgt_never_miss.py answer 3 "A"
  $PY tools/sgt_never_miss.py decide | check-add | check | finish | block | reset   (as in sgt_overhaul.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sgt_overhaul as dispatcher  # noqa: E402

dispatcher.use_project(docs=dispatcher.REPO / "docs" / "sgt_never_miss", prefix="sgt-never-miss",
                       title="SGT never-miss", cli="tools/sgt_never_miss.py")

if __name__ == "__main__":
    sys.exit(dispatcher.main())
