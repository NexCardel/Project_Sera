"""
Mockups/overhaul/ui_overhaul.py
-------------------------------
Tracker and dispatcher for the UI overhaul (Mockups/IMPLEMENTATION.md).

The same machinery as the SGT overhaul (tools/sgt_overhaul.py), pointed at this project's folder:
one fresh CLI session per work package, model per WP, retries, usage-limit sleeps with a live timer,
questions by pop-up (4 minutes, then the default), decisions and hands-on checks in CSVs, a live
Excel viewer.

Everything lives in Mockups/overhaul/ (this folder):
  ui-overhaul-plan.json       fixed plan: WPs, deps, models, CLI permissions   (edit by hand)
  ui-overhaul-runner.md       the prompt every local worker session gets       (edit by hand)
  ui-overhaul-runner-cloud.md the same for a cloud session (one WP per session) (edit by hand)
  fleet-redesign-cloud.md     the prompt of the cloud-only design WP (W0-D)     (edit by hand)
  ui-overhaul-status.csv / -runs.csv / -decisions.csv / -checks.csv / -questions.csv   (this tool only)
  ui-overhaul-report.md / ui-overhaul-agents.xlsx   read-only views
  logs/, .ui-overhaul.lock    dispatcher run logs and lock (not committed)

A WP of kind "cloud" is never started by `run`: the owner starts it in a cloud session with the
text from `prompt <WP>`. Its dependants wait until it is Done (pull the branch after the cloud
session pushes).

  PY=../APP/venv/Scripts/python.exe
  $PY Mockups/overhaul/ui_overhaul.py show [WP]
  $PY Mockups/overhaul/ui_overhaul.py next
  $PY Mockups/overhaul/ui_overhaul.py prompt [WP] [--cloud]  (print the filled-in worker prompt; default WP = next)
  $PY Mockups/overhaul/ui_overhaul.py run [--once] [--dry-run]  (local only: starts a claude CLI session per WP)
  $PY Mockups/overhaul/ui_overhaul.py watch | report | viewer
  $PY Mockups/overhaul/ui_overhaul.py ask W3-2 --question "..." --options "A|B" --default A
  $PY Mockups/overhaul/ui_overhaul.py answer 3 "A"
  $PY Mockups/overhaul/ui_overhaul.py decide | check-add | check | finish | block | reset   (as in sgt_overhaul.py)
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))
import sgt_overhaul as dispatcher  # noqa: E402

dispatcher.use_project(docs=HERE, prefix="ui-overhaul", title="UI overhaul",
                       cli="Mockups/overhaul/ui_overhaul.py")
dispatcher.LOG_DIR = HERE / "logs"                  # keep run logs and the lock inside this folder
dispatcher.LOCK = HERE / ".ui-overhaul.lock"
dispatcher.WAIT_FILE = dispatcher.LOG_DIR / "waiting.json"


def _next_local(self):
    """The next ready WP the local dispatcher may start: kind "cloud" WPs are left to the owner."""
    for wp in self.order:
        if self.wps[wp]["kind"] == "cloud":
            continue
        if self.status[wp]["Status"] in ("Not started", "Retry") and self.deps_done(wp):
            return wp
    return None


dispatcher.Tracker.next_ready = _next_local



def _prompt(argv):
    """Prints the worker prompt for one WP (the next ready one by default)."""
    sys.stdout.reconfigure(encoding="utf-8")        # the prompts use → and ×; a cp1252 console can't print them
    names = [a for a in argv if not a.startswith("--")]
    t = dispatcher.Tracker()
    wp = names[0] if names else t.next_ready()
    if not wp:
        print("no work package is ready", file=sys.stderr)
        return 2
    w, s = t.wp(wp), t.status[wp]
    if "--cloud" not in argv and w["kind"] != "cloud":
        print(dispatcher.build_prompt(t, wp))
        return 0
    key = t.plan["tiers"][w["model"]][-1]
    m = t.plan["models"][key]
    text = (t.docs / w.get("prompt", "ui-overhaul-runner-cloud.md")).read_text(encoding="utf-8")
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
