"""
tools/sgt_replay.py - replay the pages SGT recorded on this PC
==============================================================
Before a spec change goes live, see what it changes on real pages:

    python tools/sgt_replay.py baseline              # remember what SGT writes today
    ... edit core/sgt/sgt_fields.json (or the override file) ...
    python tools/sgt_replay.py diff                  # what the edit changes, per session
    python tools/sgt_replay.py diff --specs my.json  # try a spec file without installing it
    python tools/sgt_replay.py show                  # what SGT writes, per session
    python tools/sgt_replay.py health                # pages read, OCR share, spec hits (7 days)

Options: --days N (only the last N days of recordings), --corpus DIR.
The corpus and the baseline hold client data; they stay in ~/AmanAssociates_Sera and must never
be copied into the repository.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sgt.sgt_corpus import corpus_dir, load_pages          # noqa: E402
from core.sgt.sgt_health import SpecStats                       # noqa: E402
from core.sgt.sgt_replay import diff, replay                    # noqa: E402
from core.sgt.sgt_shadow import shadow_dir                      # noqa: E402
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore, override_path  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baseline", "diff", "show", "health"])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--corpus", type=Path, default=None)
    ap.add_argument("--specs", type=Path, default=None, help="a spec file to try in place of the override file")
    args = ap.parse_args(argv)

    if args.command == "health":
        summary = SpecStats(shadow_dir()).summary(date.today())
        print(json.dumps(summary, indent=2) if summary else "No SGT reads recorded yet.")
        return 0

    directory = args.corpus or corpus_dir()
    pages = load_pages(directory, args.days)
    if not pages:
        print(f"No recorded pages in {directory} - switch SGT on (Settings -> Tracker) and use the portals.")
        return 1
    paths = [BUILTIN_FIELDS_PATH, args.specs or override_path()]
    store = SpecStore(paths, log=lambda m: print(m) if "refused" in m else None)
    print(f"Replaying {len(pages)} page(s)...", file=sys.stderr)
    result = replay(pages, store)
    baseline_path = directory / "replay_baseline.json"

    if args.command == "baseline":
        baseline_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        rows = sum(len(r["rows"]) for r in result.values())
        print(f"Baseline saved: {len(result)} session(s), {rows} dataset row(s) -> {baseline_path}")
        return 0
    if args.command == "show":
        for sid, r in result.items():
            print(f"session {sid}: {len(r['rows'])} row(s), {len(r['held'])} held")
            for key, row in r["rows"].items():
                print(f"    {key}  " + ", ".join(f"{k}={v}" for k, v in row.items() if v))
            for h in r["held"]:
                print(f"    HELD {h['values']}  <- {'; '.join(h['problems'] or [])}")
        return 0
    if not baseline_path.exists():
        print("No baseline yet - run `python tools/sgt_replay.py baseline` first.")
        return 1
    before = json.loads(baseline_path.read_text(encoding="utf-8"))
    changes = diff(before, result)
    print("\n".join(changes) if changes else "No difference from the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
