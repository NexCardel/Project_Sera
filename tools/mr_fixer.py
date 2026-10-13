"""
tools/mr_fixer.py - find, report and fix failed GST ARN rows (command line)
===========================================================================
The engine is core/sdis/arn_fixer.py (the app runs it on its own at start-up on the admin PC:
core/sdis/arn_autofix.py). Plan: docs/gst_arn_recovery/mr-fixer-plan.md.

    python tools/mr_fixer.py scan   [--from D] [--to D] [--corpus DIR]...
    python tools/mr_fixer.py report [--from D] [--to D] [--corpus DIR]... [--expected expected.json]
    python tools/mr_fixer.py apply  <report.csv> [--include-client-only]
    python tools/mr_fixer.py undo   <report.csv>

scan and report change nothing. apply reads the reviewed report (including any `pick` you typed) and
works nothing out again. undo puts the rows back with their original ids. apply and undo refuse
while Sera is open. The report holds client names and GSTINs: it stays on this PC, and the console
and logs print counts, ids, ARNs and times only - never page text, names or GSTINs.
"""

import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.sera_tool_common import app_dir_default, app_is_running, open_database    # noqa: E402
from core.sdis import arn_fixer as _engine                                           # noqa: E402
from core.sdis.arn_fixer import *                                                    # noqa: E402,F401,F403
from core.sdis.arn_fixer import (BUILTIN_FIELDS_PATH, Corpus, FixerError, PayloadBuilder, _ROW_COLUMNS,   # noqa: E402,F401
                                 apply_report, arn_regex, default_roots, fixer_dir, load_registry, run_report,
                                 scan, undo_report)


def _date_arg(argv: List[str], flag: str) -> Optional[date]:
    if flag in argv:
        return date.fromisoformat(argv[argv.index(flag) + 1])
    return None


def _multi_arg(argv: List[str], flag: str) -> List[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("scan", "report", "apply", "undo"):
        print(__doc__.strip().splitlines()[0])
        print("usage: python tools/mr_fixer.py scan|report|apply <report.csv>|undo <report.csv>")
        return 2
    cmd, rest = argv[0], argv[1:]
    app_dir = app_dir_default()
    try:
        if cmd in ("apply", "undo"):
            positional = [a for a in rest if not a.startswith("--")]
            if not positional:
                raise FixerError("give the report file")
            report = Path(positional[0])
            if cmd == "undo":
                undo_report(app_dir, lambda: open_database(app_dir), report, running=app_is_running)
                print("undo done. Open Sera to check the rows.")
                return 0
            counts = apply_report(app_dir, lambda: open_database(app_dir), report, running=app_is_running,
                                  include_client_only="--include-client-only" in rest,
                                  confirm=lambda: input("type YES to start: ").strip() == "YES")
            print(f"done: {counts}")
            return 0
        roots = [Path(p).expanduser() for p in _multi_arg(rest, "--corpus")] or default_roots(app_dir)
        arn_re = arn_regex()
        registry = load_registry([BUILTIN_FIELDS_PATH])
        corpus = Corpus(roots, registry, arn_re)
        db = open_database(app_dir)
        d_from, d_to = _date_arg(rest, "--from"), _date_arg(rest, "--to")
        if cmd == "scan":
            scan(db, corpus, arn_re, d_from, d_to)
            return 0
        exp = _multi_arg(rest, "--expected")
        _path, rc = run_report(app_dir, db, corpus, arn_re, d_from, d_to, Path(exp[0]).expanduser() if exp else None)
        return rc
    except FixerError as e:
        print(f"refused: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
