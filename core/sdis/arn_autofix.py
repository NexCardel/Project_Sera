"""
core/sdis/arn_autofix.py - the ARN-row fixer, run by the app on its own
========================================================================
A few seconds after the app starts on the admin PC (or a stand-alone PC), once: find the GST rows
that hold only an ARN (SGT's session-boundary / ARN bugs), look their client, form and period up in
the SDIS corpus, and fix the ones that come out `sure` - no human review. Nothing else is touched:
`client only`, `needs pick` and `not found` rows stay as they are and are tried again at the next
start, when the excavator may have brought more corpus in.

Safe-guards: rows younger than MIN_AGE are left (a live SGT session may still fix its own row);
every run that changes anything first writes the full report (mr_fixer/auto_report_<time>.csv, with
client names, local only) and an undo file, so `python tools/mr_fixer.py undo <that report>` puts
everything back; creating a file named `disabled` in the mr_fixer folder turns the whole thing off.
Logs and the sync panel line print counts only. Engine: core/sdis/arn_fixer.py (plan:
docs/gst_arn_recovery/mr-fixer-plan.md).
"""

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

MIN_AGE = timedelta(hours=1)
START_DELAY_S = 45                  # start-up speed first: the fixer waits until the window is long up
DISABLE_FLAG = "disabled"


def run_once(db: Any, app_dir: Path, log: Callable[[str], None] = print, min_age: timedelta = MIN_AGE,
             roots: Optional[Sequence[Path]] = None) -> Dict[str, int]:
    """One pass. Returns counts: rows (targets), sure, fixed, left (rows not fixed)."""
    from core.sdis import arn_fixer as fx
    counts = {"rows": 0, "sure": 0, "fixed": 0, "left": 0}
    if (fx.fixer_dir(app_dir) / DISABLE_FLAG).exists():
        log("ARN fixer: switched off (mr_fixer/disabled)")
        return counts
    arn_re = fx.arn_regex()
    targets = fx.find_targets(db, arn_re, older_than=min_age)
    counts["rows"] = len(targets)
    if not targets:
        return counts
    corpus = fx.Corpus(roots if roots is not None else fx.default_roots(app_dir),
                       fx.load_registry([fx.BUILTIN_FIELDS_PATH]), arn_re)
    builder = fx.PayloadBuilder()
    rows = fx.make_report(db, corpus, targets, builder)
    counts["sure"] = sum(1 for r in rows if r["result"] == fx.SURE)
    del corpus
    if not counts["sure"]:
        counts["left"] = len(rows)
        log(f"ARN fixer: {len(rows)} row(s), none sure yet; tried again at the next start")
        return counts
    report = fx.write_report(rows, fx.fixer_dir(app_dir) / f"auto_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    result = fx.apply_report(app_dir, lambda: db, report, include_client_only=False, confirm=lambda: True,
                             log=log, builder=builder)
    counts["fixed"] = result["fixed"]
    counts["left"] = len(rows) - result["fixed"]
    log(f"ARN fixer: {counts['rows']} row(s), {counts['sure']} sure, {counts['fixed']} fixed, {counts['left']} left")
    return counts


def start_background(db: Any, app_dir: Path, on_event: Optional[Callable[[str, str, str], None]] = None,
                     on_done: Optional[Callable[[int], None]] = None, delay_s: float = START_DELAY_S,
                     log: Callable[[str], None] = print) -> Optional[threading.Thread]:
    """Runs run_once on a low-key daemon thread, on the admin PC only. on_done(fixed) is called when
    rows changed (the caller refreshes its tracker view on its own thread)."""
    try:
        import sync_admin
        if not sync_admin.is_admin_pc(app_dir):
            return None
    except Exception as e:
        log(f"ARN fixer: not started ({type(e).__name__})")
        return None

    def work() -> None:
        time.sleep(delay_s)
        try:
            c = run_once(db, app_dir, log=log)
            if c["fixed"]:
                if on_event:
                    on_event("SDIS", "Failed GST rows fixed", f"{c['fixed']} of {c['rows']} (ARN-only rows, from the corpus)")
                if on_done:
                    on_done(c["fixed"])
        except Exception as e:                       # the app must never notice a failure here
            log(f"ARN fixer failed: {type(e).__name__}: {e}")

    t = threading.Thread(target=work, name="arn-autofix", daemon=True)
    t.start()
    return t
