"""Rank every grown candidate run of a page: leaves covered, total member-box area, label positions.
Does the geometric rule (coverage, then smallest area) pick the right cards without reading any text?"""
import json
from collections import Counter
from common import comp, rect, STAMPS, REPO
from arr import leaves, tokens, tandem_runs_relaxed, relaxed
from s6_proto import union, area, extend
from core.sdis.keys import flatten

CUT = 0.9


def grown(fl, lv, S):
    out = []
    for s, L, k in tandem_runs_relaxed(S):
        for rot in range(L):
            if s + rot + k * L > len(S):
                kk = k - 1
            else:
                kk = k
            if kk < 2:
                continue
            members = extend(S, s + rot, L, kk, CUT)
            boxes = [union(fl, lv, range(a, b)) for a, b in members]
            tot = sum(area(u) for u in boxes)
            cover = sum(b - a for a, b in members)
            Lmin = min(b - a for a, b in members)
            labels = sum(1 for p in range(Lmin) if len({fl[lv[a + p]]["text"] for a, b in members}) == 1 and fl[lv[members[0][0] + p]]["text"])
            out.append((cover, tot, -labels, members[0][0], (s, L, k, rot), [b - a for a, b in members], labels))
    # one entry per distinct segmentation
    seen, uniq = set(), []
    for row in sorted(out, key=lambda r: (-r[0], r[1], r[2], r[3])):
        key = tuple(row[5]) + (row[3],)
        if key not in seen:
            seen.add(key)
            uniq.append(row)
    return uniq


def show(name, fl, lv, want=None):
    S = tokens(fl, lv)
    rows = grown(fl, lv, S)
    print(f"== {name}: {len(rows)} distinct grown segmentations")
    for cover, tot, neglab, start, seed, lens, labels in rows[:6]:
        print(f"   cover={cover:3d} area={tot:8d} labels={labels:2d} start leaf={start:3d} (node {lv[start]}) lengths={lens} seed={seed}")
    if want:
        best = rows[0]
        print(f"   -> best starts at node {lv[best[3]]} with {len(best[5])} members; expected first node {want}: {'OK' if lv[best[3]] == want else 'WRONG'}")


for st, want in zip(STAMPS, (91, 91)):
    fl = comp(st)["nodes"]
    show(st, fl, leaves(fl), want)
FIX = REPO / "tests" / "class_diff_align"
for client in ("A", "B"):
    rec = json.loads((FIX / f"client_{client}_msedge.json").read_text(encoding="utf-8"))
    fl = flatten(rec)
    show(f"fixture {client}", fl, leaves(fl, present_only=False))
