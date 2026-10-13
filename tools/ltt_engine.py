"""LTT engine command line: previous / current / upcoming period of a service.

    python tools/ltt_engine.py                                   (interactive)
    python tools/ltt_engine.py --deadline 20-10-2026 --every monthly
    python tools/ltt_engine.py -d 2026-03-31 -e 3m --on 2026-10-04 --count 2
    python tools/ltt_engine.py -d 31-07-2026 -e yearly --covers fy_end --grace 153 --on 2026-10-04

Dates: YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY (day first). Gap: daily, weekly, fortnightly,
monthly, bimonthly, quarterly, half-yearly, yearly, or custom "<n>d", "<n>w", "<n>m", "<n>y".
The engine itself lives in core/ltt/engine.py.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.ltt.engine import COVERS, LTTEngine, parse_date, parse_gap, render  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LTT engine: previous / current / upcoming period.")
    ap.add_argument("-d", "--deadline", help="a known deadline of the service")
    ap.add_argument("-e", "--every", help="period gap, e.g. monthly, quarterly, 45d")
    ap.add_argument("--on", help="reference date (default: today)")
    ap.add_argument("--lag", type=int, default=1,
                    help="months between the end of the covered period and the deadline month (default 1)")
    ap.add_argument("--fy-start", type=int, default=4, choices=range(1, 13), metavar="MONTH",
                    help="first month of the financial year (default 4 = April)")
    ap.add_argument("--grace", type=int, default=0, metavar="DAYS",
                    help="days a period stays current after its deadline (late-filing window)")
    ap.add_argument("--extend", action="append", default=[], metavar="OLD=NEW",
                    help="a notified extension, e.g. --extend 20-10-2026=25-10-2026 (repeatable)")
    ap.add_argument("--holiday", action="append", default=[], metavar="DATE",
                    help="a date a deadline cannot fall on; it moves to the next day (repeatable)")
    ap.add_argument("--roll-sundays", action="store_true", help="a deadline on a Sunday moves to Monday")
    ap.add_argument("--month-end", action="store_true", help="every deadline is the last day of its month")
    ap.add_argument("--covers", choices=COVERS, help="which month a deadline files for (replaces --lag)")
    ap.add_argument("-n", "--count", type=int, default=1, help="periods shown each side (default 1)")
    a = ap.parse_args(argv)

    try:
        dl_text = a.deadline or input("Deadline (e.g. 20-10-2026): ")
        gap_text = a.every or input("Period gap (monthly / quarterly / 45d ...): ")
        deadline, gap = parse_date(dl_text), parse_gap(gap_text)
        ref = parse_date(a.on) if a.on else date.today()
        if a.lag < 0:
            raise ValueError("--lag cannot be negative")
        if a.count < 1:
            raise ValueError("--count must be at least 1")
    except (ValueError, EOFError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        overrides = {}
        for item in a.extend:
            old, _, new = item.partition("=")
            overrides[parse_date(old)] = parse_date(new)
        engine = LTTEngine(deadline, gap, a.lag, a.fy_start, grace_days=a.grace, overrides=overrides,
                           holidays=[parse_date(h) for h in a.holiday], roll_sundays=a.roll_sundays,
                           month_end=a.month_end, covers=a.covers)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(render(engine, ref, a.count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
