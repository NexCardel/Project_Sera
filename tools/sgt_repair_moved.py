"""
tools/sgt_repair_moved.py - plan the repair of GST rows SGT moved to another period (2026-10-06 bug)
=====================================================================================================
Run on EVERY PC that ran the faulty version (this reads only that PC's SGT log; no database, no key):

    python tools/sgt_repair_moved.py            # writes sgt_repair_plan_<pc>_<stamp>.json/.csv
    python tools/sgt_repair_moved.py --out DIR  # somewhere else (default ~/AmanAssociates_Sera/sgt_repair/out)

Then copy each plan .json into  ~/AmanAssociates_Sera/sgt_repair/plans/  ON THE ADMIN PC and start the app:
it writes a DRY-RUN report to sgt_repair/reports/ and changes nothing. After you have read the report,
create an empty file  sgt_repair/APPLY  and start the app again: it backs the rows up to sgt_repair/backups/,
puts each false row back, and moves the plan to sgt_repair/done/. Remove APPLY afterwards.
The CSV also lists "review" rows: GST rows marked Submitted & Verified with no ARN, to check by hand.
Nothing is ever deleted from the SGT logs.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sgt.sgt_repair import build_plan, repair_dir, write_plan   # noqa: E402
from core.sgt.sgt_shadow import shadow_dir                           # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--log-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    plan = build_plan(args.log_dir or shadow_dir(), pc=os.environ.get("COMPUTERNAME", ""))
    out = args.out or (repair_dir() / "out")
    paths = write_plan(plan, out)
    print(f"{len(plan['items'])} row(s) to put back, {len(plan['review'])} to review by hand.")
    for it in plan["items"]:
        print(f"  {it['ts']}  {it['name'] or it['gstin']}  {it['form']}  "
              f"{it['false']['period']} -> {it['restore']['period']} ({it['restore'].get('status')})")
    for p in paths:
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
