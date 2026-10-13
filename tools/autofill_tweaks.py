"""
tools/autofill_tweaks.py
------------------------
Tracker and dispatcher for the Autofill tweaks project
(docs/autofill_tweaks/autofill-tweaks-blueprint.md).

The same machinery as the SGT overhaul (tools/sgt_overhaul.py), pointed at this project's folder:
one fresh CLI session per work package, model per WP, retries, usage-limit sleeps with a live timer,
questions by pop-up (4 minutes, then the default), decisions and hands-on checks in CSVs, a live
Excel viewer.

Files (all in docs/autofill_tweaks/):
  autofill-tweaks-plan.json       fixed plan: WPs, deps, models, CLI permissions   (edit by hand)
  autofill-tweaks-runner.md       the prompt every worker session gets              (edit by hand)
  autofill-tweaks-status.csv / -runs.csv / -decisions.csv / -checks.csv / -questions.csv   (this tool only)
  autofill-tweaks-report.md / autofill-tweaks-agents.xlsx   read-only views

  PY=../APP/venv/Scripts/python.exe
  $PY tools/autofill_tweaks.py show [WP]
  $PY tools/autofill_tweaks.py next
  $PY tools/autofill_tweaks.py run [--once] [--dry-run]
  $PY tools/autofill_tweaks.py watch | report | viewer
  $PY tools/autofill_tweaks.py ask W2-2 --question "..." --options "A|B" --default A
  $PY tools/autofill_tweaks.py answer 3 "A"
  $PY tools/autofill_tweaks.py decide | check-add | check | finish | block | reset   (as in sgt_overhaul.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sgt_overhaul as dispatcher  # noqa: E402

dispatcher.use_project(docs=dispatcher.REPO / "docs" / "autofill_tweaks", prefix="autofill-tweaks",
                       title="Autofill tweaks", cli="tools/autofill_tweaks.py")

if __name__ == "__main__":
    sys.exit(dispatcher.main())
