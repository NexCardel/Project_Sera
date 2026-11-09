"""Prototype: enriched tokens (kind, direction, text when the text repeats on the page), exact tandem seeds,
approximate extension of a run by alignment, the phase rule (smallest member boxes), nearest-label
directions, the 'O' steps, and timing. Counts only."""
import json
import time
from collections import Counter
from difflib import SequenceMatcher
from common import comp, children, rect, tshape, STAMPS, REPO
from arr import leaves, step, tandem_runs, runs_report, relaxed
from core.sdis.keys import flatten

FIX = REPO / "tests" / "class_diff_align"


def enriched(fl, lv):
    cnt = Counter(fl[i]["text"] for i in lv if fl[i]["text"])
    out = []
    for k, i in enumerate(lv):
        d = step(rect(fl[i]), rect(fl[lv[k + 1]])) if k + 1 < len(lv) else "END"
        t = fl[i]["text"]
        out.append((fl[i]["type"], d, t if (t and cnt[t] >= 2) else ""))
    return out


def sim(a, b):
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def union(fl, lv, ks):
    rs = [rect(fl[lv[k]]) for k in ks]
    return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[0] + r[2] for r in rs), max(r[1] + r[3] for r in rs))


def area(u):
    return (u[2] - u[0]) * (u[3] - u[1])


def extend(S, s, L, k, cut):
    """Grow an exact run (s, L, k) left and right by windows of length L-2..L+2 whose likeness to the period
    is >= cut. Returns the member ranges."""
    period = relaxed(S[s:s + L])
    members = [(s + m * L, s + (m + 1) * L) for m in range(k)]
    changed = True
    while changed:
        changed = False
        a = members[0][0]
        best = None
        for l in range(max(1, L - 3), L + 4):
            if a - l < 0:
                continue
            v = sim(period, relaxed(S[a - l:a]))
            if v >= cut and (best is None or v > best[0]):
                best = (v, l)
        if best:
            members.insert(0, (a - best[1], a))
            changed = True
        b = members[-1][1]
        best = None
        for l in range(max(1, L - 3), L + 4):
            if b + l > len(S):
                continue
            v = sim(period, relaxed(S[b:b + l]))
            if v >= cut and (best is None or v > best[0]):
                best = (v, l)
        if best:
            members.append((b, b + best[1]))
            changed = True
    return members


def best_phase(fl, lv, s, L, k):
    """Among the L rotations of an exact run, the one whose member union boxes have the smallest total area
    (ties: the earliest). Returns (rotation, total area, disjoint?)."""
    rows = []
    for rot in range(L):
        ms = [range(s + rot + m * L, s + rot + (m + 1) * L) for m in range(k)]
        if ms[-1][-1] >= len(lv) or ms[0][0] < 0:
            continue
        us = [union(fl, lv, m) for m in ms]
        tot = sum(area(u) for u in us)
        disjoint = all(max(0, min(u[2], v[2]) - max(u[0], v[0])) * max(0, min(u[3], v[3]) - max(u[1], v[1])) == 0
                       for i, u in enumerate(us) for v in us[i + 1:])
        rows.append((tot, rot, disjoint))
    rows.sort()
    return rows


def analyse(name, fl, lv, cut=0.9):
    S = enriched(fl, lv)
    t0 = time.perf_counter()
    runs = tandem_runs(S)
    runs = sorted(runs, key=lambda r: (-r[1] * r[2], r[0]))
    t1 = time.perf_counter()
    print(f"== {name}: leaves={len(S)} distinct enriched tokens={len(set(S))} exact runs={len(runs)} ({(t1 - t0) * 1000:.0f} ms)")
    runs_report(runs, top=6)
    s, L, k = runs[0]
    ph = best_phase(fl, lv, s, L, k)
    print(f"   phase by smallest member area: rotation={ph[0][1]} area={ph[0][0]} disjoint={ph[0][2]}; next: {[(r, a, d) for a, r, d in ph[1:4]]}")
    rot = ph[0][1]
    t2 = time.perf_counter()
    members = extend(S, s + rot, L, k if s + rot + k * L <= len(S) else k - 1, cut)
    t3 = time.perf_counter()
    print(f"   after approximate extension (cut {cut}): {len(members)} members, lengths {[b - a for a, b in members]}, first leaf node={lv[members[0][0]]} last={lv[members[-1][1] - 1]} ({(t3 - t2) * 1000:.0f} ms)")
    return S, members


for st in STAMPS:
    fl = comp(st)["nodes"]
    lv = leaves(fl)
    S, members = analyse(st, fl, lv)
    # nearest preceding label (same text in every member at that position) for each distinct position
    L = min(b - a for a, b in members)
    texts_at = [[fl[lv[a + p]]["text"] for a, b in members if a + p < b] for p in range(L)]
    roles = ["label" if len(set(t)) == 1 and t[0] else ("distinct" if len(set(t)) == len(t) else "mixed") for t in texts_at]
    print(f"   roles by position: {Counter(roles)}")
    a0, b0 = members[0]
    out = Counter()
    for p in range(L):
        if roles[p] == "label":
            continue
        q = p - 1
        while q >= 0 and roles[q] != "label":
            q -= 1
        if q < 0:
            out[(roles[p], "no label before it in the member")] += 1
            continue
        d = step(rect(fl[lv[a0 + q]]), rect(fl[lv[a0 + p]]))
        out[(roles[p], f"label at -{p - q}", d)] += 1
    print(f"   value positions -> nearest preceding label and the direction label->value: {dict(out)}")
    # the 'O' steps: which kinds overlap on one line
    o = Counter()
    for a, b in zip(lv, lv[1:]):
        if step(rect(fl[a]), rect(fl[b])) == "O":
            o[(fl[a]["type"], fl[b]["type"], fl[b]["parent"] == a or fl[a]["parent"] == fl[b]["parent"])] += 1
    print(f"   'O' steps (kind a, kind b, parent-child or siblings): {dict(o)}")
    grp = Counter((fl[i]["type"], fl[i]["node"].get("role") or "", fl[i]["depth"]) for i in lv if fl[i]["type"] == "Group")
    print(f"   Group leaves (type, role, depth): {dict(grp)}")

print()
for client in ("A", "B"):
    rec = json.loads((FIX / f"client_{client}_msedge.json").read_text(encoding="utf-8"))
    fl = flatten(rec)
    lv = leaves(fl, present_only=False)
    analyse(f"fixture client {client} (msedge)", fl, lv)
