"""
tools/os_assist.py
------------------
Tracker and dispatcher for the OS Assist project (docs/os_assist/os-assist-blueprint.md): SMTI, MECP
and SCA as docked desktop cards that fill through UI Automation instead of the extension.

The same machinery as the SGT overhaul (tools/sgt_overhaul.py), pointed at this project's folder:
one fresh CLI session per work package, model per WP, retries, usage-limit sleeps with a live timer,
questions by pop-up (4 minutes, then the default), decisions and hands-on checks in CSVs, a live
Excel viewer.

Files (all in docs/os_assist/):
  os-assist-plan.json       fixed plan: WPs, deps, models, CLI permissions   (edit by hand)
  os-assist-runner.md       the prompt every worker session gets              (edit by hand)
  os-assist-status.csv / -runs.csv / -decisions.csv / -checks.csv / -questions.csv   (this tool only)
  os-assist-report.md / os-assist-agents.xlsx   read-only views

  PY=../APP/venv/Scripts/python.exe
  $PY tools/os_assist.py show [WP]
  $PY tools/os_assist.py next
  $PY tools/os_assist.py run [--once] [--dry-run]
  $PY tools/os_assist.py watch | report | viewer
  $PY tools/os_assist.py ask O2-2 --question "..." --options "A|B" --default A
  $PY tools/os_assist.py answer 3 "A"
  $PY tools/os_assist.py decide | check-add | check | finish | block | reset   (as in sgt_overhaul.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sgt_overhaul as dispatcher  # noqa: E402

dispatcher.use_project(docs=dispatcher.REPO / "docs" / "os_assist", prefix="os-assist",
                       title="OS Assist", cli="tools/os_assist.py")

if __name__ == "__main__":
    sys.exit(dispatcher.main())
