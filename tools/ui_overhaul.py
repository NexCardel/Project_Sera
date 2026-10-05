"""
tools/ui_overhaul.py
--------------------
Tracker and dispatcher for the UI overhaul (Mockups/IMPLEMENTATION.md).

The same machinery as the SGT overhaul (tools/sgt_overhaul.py), pointed at this project's folder:
one fresh CLI session per work package, model per WP, retries, usage-limit sleeps with a live timer,
questions by pop-up (4 minutes, then the default), decisions and hands-on checks in CSVs, a live
Excel viewer.

Files (all in docs/ui_overhaul/):
  ui-overhaul-plan.json       fixed plan: WPs, deps, models, CLI permissions   (edit by hand)
  ui-overhaul-runner.md       the prompt every local worker session gets       (edit by hand)
  ui-overhaul-runner-cloud.md the same for a cloud session (one WP per session) (edit by hand)
  ui-overhaul-status.csv / -runs.csv / -decisions.csv / -checks.csv / -questions.csv   (this tool only)
  ui-overhaul-report.md / ui-overhaul-agents.xlsx   read-only views

  PY=../APP/venv/Scripts/python.exe
  $PY tools/ui_overhaul.py show [WP]
  $PY tools/ui_overhaul.py next
  $PY tools/ui_overhaul.py prompt [WP] [--cloud]       (print the filled-in worker prompt; default WP = next)
  $PY tools/ui_overhaul.py run [--once] [--dry-run]    (local only: starts a claude CLI session per WP)
  $PY tools/ui_overhaul.py watch | report | viewer
  $PY tools/ui_overhaul.py ask W3-2 --question "..." --options "A|B" --default A
  $PY tools/ui_overhaul.py answer 3 "A"
  $PY tools/ui_overhaul.py decide | check-add | check | finish | block | reset   (as in sgt_overhaul.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sgt_overhaul as dispatcher  # noqa: E402

dispatcher.use_project(docs=dispatcher.REPO / "docs" / "ui_overhaul", prefix="ui-overhaul",
                       title="UI overhaul", cli="tools/ui_overhaul.py")


def _prompt(argv):
    """Prints the worker prompt for one WP (the next ready one by default)."""
    cloud = "--cloud" in argv
    names = [a for a in argv if not a.startswith("--")]
    t = dispatcher.Tracker()
    wp = names[0] if names else t.next_ready()
    if not wp:
        print("no work package is ready", file=sys.stderr)
        return 2
    if not cloud:
        print(dispatcher.build_prompt(t, wp))
        return 0
    w, s = t.wp(wp), t.status[wp]
    key = t.plan["tiers"][w["model"]][-1]
    m = t.plan["models"][key]
    text = (t.docs / "ui-overhaul-runner-cloud.md").read_text(encoding="utf-8")
    for k, v in {"{WP}": wp, "{WHAT}": w["what"], "{FOCUS}": w["focus"], "{KIND}": w["kind"],
                 "{MODEL}": m["id"], "{COAUTHOR}": t.plan["runners"][m["runner"]]["coauthor"],
                 "{ATTEMPT}": str(int(s["Attempts"] or 0)), "{PREVIOUS}": s["Notes"] or "none",
                 "{DEADLINE}": t.plan["deadline"]}.items():
        text = text.replace(k, v)
    print(text)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "prompt":
        sys.exit(_prompt(sys.argv[2:]))
    sys.exit(dispatcher.main())
