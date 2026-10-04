"""
tools/sdis_regress.py - SDIS regression runner: the engine over the real captures, COUNTS ONLY
===============================================================================================
Runs link_map (one map per session and page link) and memory (every client's map of a link, in
both client orders) over a folder of captures and prints numbers: never a text, never a value.

  per map      snapshots merged, text nodes, DOUBLE COUNT (text nodes the merged map holds more
               often than the one snapshot that showed them most), LOST (texts some snapshot
               showed that no entry keeps), MULTI (entries holding more than one value)
  per link     (2+ clients) each client's matched %, the verdict counts, 'orders agree'

    python tools/sdis_regress.py                          # ../APP/tools/pre_dev/class_diff/output
    python tools/sdis_regress.py --captures DIR --save docs/sdis/sdis-regress-baseline.txt

The captures are read in place and never copied. Every WP runs this before and after its change.
"""

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURES = REPO.parent / "APP" / "tools" / "pre_dev" / "class_diff" / "output"


def _map_report(sources: List[Any], lm: Any, flatten: Any, shape: Any) -> Dict[str, int]:
    flat = lm.to_flat()
    merged = Counter((shape(e), e["text"]) for e in flat if e["text"])
    best: Counter = Counter()
    shown: set = set()
    for rec in sources:
        snap = [e for e in flatten(rec) if e["text"]]
        shown.update(e["text"] for e in snap)
        for k, n in Counter((shape(e), e["text"]) for e in snap).items():
            best[k] = max(best[k], n)
    kept = {v for ent in lm.entries.values() for v in ent["values"]}
    return {"snapshots": len(lm.reads), "text nodes": sum(merged.values()),
            "double count": sum(max(0, n - best[k]) for k, n in merged.items()),
            "lost": len(shown - kept),
            "multi": sum(1 for ent in lm.entries.values() if len(ent["values"]) > 1)}


def run(captures: Path, out: List[str]) -> bool:
    os.environ["SDIS_DATA_DIR"] = str(captures)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from core.sdis import link_map, memory
    from core.sdis.align import shape
    from core.sdis.keys import flatten
    link_map.OUT_DIR = captures

    def say(line: str = "") -> None:
        out.append(line)
        print(line)

    by_map: Dict[Any, List[Any]] = {}
    for _stamp, session, page, rec in link_map.all_sources():
        by_map.setdefault((session, page), []).append(rec)
    maps = link_map.build_maps()
    say(f"== maps: {len(maps)} (session, page link) ==")
    totals: Counter = Counter()
    for (session, page), lm in sorted(maps.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        r = _map_report(by_map.get((session, page), []), lm, flatten, shape)
        totals.update(r)
        say(f"Page: {page} | {session}")
        say("  " + "  ".join(f"{k} {v}" for k, v in r.items()))
    say(f"TOTAL  {'  '.join(f'{k} {v}' for k, v in totals.items())}")

    pages = {p: cm for p, cm in memory.client_maps().items() if len(cm) >= 2}
    say()
    say(f"== memory: {len(pages)} page links with 2+ clients (N = 2) ==")
    agree_all = True
    for page in sorted(pages):
        cm = pages[page]
        order = sorted(cm, key=lambda c: cm[c].reads[0])
        say(f"Page: {page} | clients {len(cm)}")
        sums = []
        for label, o in (("forward", order), ("reversed", order[::-1])):
            m = memory.build(page, cm, o, 2)
            sums.append(m.summary())
            say(f"  {label}: memory {len(m.order)} nodes, pending {len(m.pending)}")
            for r in m.log:
                say(f"    {r['client']:22s} nodes {r['nodes']:4d}  matched {r['matched']:4d}"
                    f" ({100 * r['matched'] / max(1, r['nodes']):5.1f}%)  {r['kind']}")
            for (st, v), k in sorted(sums[-1].items()):
                say(f"      {st:9s} {v:26s} {k:5d}")
        agree = sums[0] == sums[1]
        agree_all = agree_all and agree
        say(f"  orders agree: {'yes' if agree else 'no'}")
    say()
    say(f"orders agree: {'yes' if agree_all else 'no'}")
    return agree_all


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SDIS regression runner: counts only.")
    ap.add_argument("--captures", type=Path, default=DEFAULT_CAPTURES, help="folder with the captures")
    ap.add_argument("--save", type=Path, help="also write the printout to this file")
    args = ap.parse_args(argv)
    if not args.captures.is_dir():
        print("no captures")
        return 0
    t0 = time.time()
    out: List[str] = []
    run(args.captures, out)
    out.append(f"total seconds: {time.time() - t0:.1f}")
    print(out[-1])
    if args.save:
        args.save.write_text("\n".join(out) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
