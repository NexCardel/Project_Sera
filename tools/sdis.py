"""
tools/sdis.py
-------------
Tracker and dispatcher for the Sera Distill (SDIS) project (docs/sdis/sdis-blueprint.md).

The same machinery as the SGT overhaul and Autofill tweaks (tools/sgt_overhaul.py), pointed at this
project's folder: one fresh CLI session per work package, model per WP, retries, usage-limit sleeps
with a live timer, questions by pop-up (4 minutes, then the default), decisions and hands-on checks
in CSVs, a live Excel viewer.

Files (all in docs/sdis/):
  sdis-plan.json       fixed plan: WPs, deps, models, CLI permissions   (re-plan: edit make_plan.py, run it)
  sdis-runner.md       the prompt every worker session gets              (edit by hand)
  sdis-status.csv / -runs.csv / -decisions.csv / -checks.csv / -questions.csv   (this tool only)
  sdis-report.md / sdis-agents.xlsx   read-only views

  PY=../APP/venv/Scripts/python.exe
  $PY tools/sdis.py show [WP]
  $PY tools/sdis.py next
  $PY tools/sdis.py run [--once] [--dry-run]
  $PY tools/sdis.py watch | report | viewer
  $PY tools/sdis.py ask W1-3 --question "..." --options "A|B" --default A
  $PY tools/sdis.py answer 3 "A"
  $PY tools/sdis.py decide | check-add | check | finish | block | reset   (as in sgt_overhaul.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sgt_overhaul as dispatcher  # noqa: E402

dispatcher.use_project(docs=dispatcher.REPO / "docs" / "sdis", prefix="sdis",
                       title="Sera Distill", cli="tools/sdis.py")

if __name__ == "__main__":
    sys.exit(dispatcher.main())
