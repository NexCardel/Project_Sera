"""
tools/pre_dev/class_diff/exp_anchor_y.py - experiment: an anchor-relative Y term in the look-alike score
=========================================================================================================
core/sdis/align.py scores look-alikes (same shape, uneven counts between two anchors) by text pattern +
sideways rect overlap (_column). Raw Y is useless across clients (an extra row shifts everything below
it; scroll and window size differ), but Y measured FROM THE ANCHOR BEFORE THE GAP should survive both.

This script leaves core/sdis untouched: it runs compare.compare_flat with compare.align swapped for a
copy of align() whose look-alike score adds   W * 1 / (1 + |dy_latest - dy_previous| / row_height)
where dy = node top - anchor top on its own side. W = 0 is the baseline (today's engine, checked equal).

For every pair of comp pages of one link from different sessions, both directions, per W it counts:
  ambiguous   - rows flagged "ambiguous pairing - look-alikes, one side has fewer"
  types differ- rows flagged "types differ - likely misaligned (list shifted?)"
  furniture   - rows flagged "furniture-shaped - wording may have changed"
  var-align   - rows with status VARIABLE_ALIGNMENT
  repaired    - look-alike pairs that differ from the baseline's
Prints no page text. The repaired pairs (real values) go to output/exp_anchor_y.csv - keep it on this PC.

Usage:  python exp_anchor_y.py [--weights 0,0.5,1,2]
"""
import argparse
import itertools
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import compare as C                                               # noqa: E402
import core.sdis.align as A                                       # noqa: E402

AMBIG = "ambiguous pairing - look-alikes, one side has fewer"
TYPES = "types differ - likely misaligned (list shifted?)"
FURN = "furniture-shaped - wording may have changed"


def _rect(e):
    r = (e.get("node") or {}).get("rect") if isinstance(e, dict) else None
    return r if r and len(r) >= 4 and (r[2] > 0 or r[3] > 0) else None


def _yterm(a, b, ra_anchor, rb_anchor):
    """1 when both sit the same distance below their anchor, falling off by row heights; 0 without rects."""
    ra, rb = _rect(a), _rect(b)
    if not (ra and rb and ra_anchor and rb_anchor):
        return 0.0
    dya, dyb = ra[1] - ra_anchor[1], rb[1] - rb_anchor[1]
    h = max(min(ra[3], rb[3]), 8)
    return 1.0 / (1.0 + abs(dya - dyb) / h)


def make_align(weight, log):
    """A copy of core.sdis.align.align whose look-alike score adds weight * _yterm. log collects
    (fl index, fp index) of every look-alike pair it makes."""

    def look_alikes(xs, ys, fl, fp, pairs, ambiguous, anc_a, anc_b):
        base = A._score
        A._score = lambda a, b: base(a, b) + weight * _yterm(a, b, anc_a, anc_b)
        try:
            before = dict(pairs)
            A._look_alikes(xs, ys, fl, fp, pairs, ambiguous)
        finally:
            A._score = base
        log.extend((i, j) for i, j in pairs.items() if before.get(i) != j)

    def align(fl, fp, compared, ambiguous=None):
        a = [i for i, e in enumerate(fl) if compared(e)]
        b = [j for j, e in enumerate(fp) if compared(e)]
        anchors = [(a[x], b[y]) for x, y in A._common([(A.shape(fl[i]), fl[i]["text"]) for i in a],
                                                      [(A.shape(fp[j]), fp[j]["text"]) for j in b])]
        pairs = dict(anchors)
        bounds = [(-1, -1)] + anchors + [(len(fl), len(fp))]
        for (i0, j0), (i1, j1) in zip(bounds, bounds[1:]):
            ga = a[bisect_right(a, i0):bisect_left(a, i1)]
            gb = b[bisect_right(b, j0):bisect_left(b, j1)]
            if not (ga and gb):
                continue
            # the reference: the anchor before the gap, else the one after it (gap at the page top)
            if i0 >= 0:
                anc_a, anc_b = _rect(fl[i0]), _rect(fp[j0])
            elif i1 < len(fl):
                anc_a, anc_b = _rect(fl[i1]), _rect(fp[j1])
            else:
                anc_a = anc_b = None
            shapes_a, shapes_b = {}, {}
            for i in ga:
                shapes_a.setdefault(A.shape(fl[i]), []).append(i)
            for j in gb:
                shapes_b.setdefault(A.shape(fp[j]), []).append(j)
            uneven = {sh for sh in shapes_a if sh in shapes_b and len(shapes_a[sh]) != len(shapes_b[sh])}
            for x, y in A._common([A.shape(fl[i]) for i in ga], [A.shape(fp[j]) for j in gb]):
                if A.shape(fl[ga[x]]) not in uneven:
                    pairs[ga[x]] = gb[y]
            for sh in sorted(uneven, key=lambda s: ga.index(shapes_a[s][0])):
                look_alikes(shapes_a[sh], shapes_b[sh], fl, fp, pairs, ambiguous, anc_a, anc_b)
        return pairs

    return align


def run(fl, fp, weight):
    log = []
    C.align = make_align(weight, log)
    try:
        rows = C.compare_flat(fl, fp)
    finally:
        C.align = A.align
    checks = Counter(r["check"] for r in rows)
    m = {"ambiguous": checks[AMBIG], "types differ": checks[TYPES], "furniture": checks[FURN],
         "var-align": sum(1 for r in rows if r["status"] == C.VARIABLE_ALIGNMENT)}
    return m, dict(log), rows


def comp_pairs():
    by_page = {}
    for p in sorted(C.OUT_DIR.glob("comp_*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("comp_page"):
            by_page.setdefault(C.page_key(rec), []).append((p, rec))
    for page, cs in sorted(by_page.items()):
        for (lp, lrec), (pp, prec) in itertools.permutations(cs, 2):
            if lrec.get("session") != prec.get("session"):
                yield page, lp, lrec, pp, prec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--weights", default="0,0.5,1,2")
    a = ap.parse_args(argv)
    weights = [float(w) for w in a.weights.split(",")]
    if weights[0] != 0:
        weights.insert(0, 0.0)

    # baseline check: W = 0 must reproduce today's engine exactly
    detail, totals = [], {w: Counter() for w in weights}
    for page, lp, lrec, pp, prec in comp_pairs():
        fl, fp = C.flat_of(lrec), C.flat_of(prec)
        ref = C.compare_flat(fl, fp)
        print(f"{lrec.get('session')} vs {prec.get('session')}  {page[:60]}  ({len(fl)} / {len(fp)} nodes)")
        base_pairs = None
        for w in weights:
            m, la, rows = run(fl, fp, w)
            if w == 0:
                assert [(r["status"], r["check"]) for r in rows] == [(r["status"], r["check"]) for r in ref], \
                    "W=0 copy differs from core.sdis.align - the experiment's align() is stale"
                base_pairs, m["repaired"] = la, 0
            else:
                changed = {i: j for i, j in la.items() if base_pairs.get(i) != j}
                m["repaired"] = len(changed)
                for i, j in changed.items():
                    bj = base_pairs.get(i)
                    detail.append({"latest": lp.name, "previous": pp.name, "weight": w, "shape": A.shape(fl[i]),
                                   "latest_text": fl[i]["text"],
                                   "baseline_partner": fp[bj]["text"] if bj is not None else "",
                                   "new_partner": fp[j]["text"],
                                   "latest_rect": (fl[i].get("node") or {}).get("rect"),
                                   "baseline_rect": (fp[bj].get("node") or {}).get("rect") if bj is not None else "",
                                   "new_rect": (fp[j].get("node") or {}).get("rect")})
            m["look-alike pairs"] = len(la)
            totals[w].update(m)
            print(f"  W={w:<4} " + "  ".join(f"{k} {v:4d}" for k, v in m.items()))
        print()
    print("TOTAL")
    for w in weights:
        print(f"  W={w:<4} " + "  ".join(f"{k} {v:4d}" for k, v in totals[w].items()))
    if detail:
        out = C.write_csv(C.OUT_DIR / "exp_anchor_y.csv", list(detail[0]), detail)
        print(f"Repaired pairs: {out.name}  (real page values - keep on this PC)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
