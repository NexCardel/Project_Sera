"""Reference prototype of the designed pipeline, run on the two ITR pages and the two fixture pages:
leaves -> tokens -> relaxed tandem seeds -> phase by smallest member area (before extension) -> approximate
extension -> rank by coverage then area -> accept non-overlapping or nested -> period multiple by the label
witness -> roles. Counts only."""
import json
import time
from collections import Counter
from difflib import SequenceMatcher
from common import comp, rect, STAMPS, REPO
from arr import leaves, tokens, tandem_runs_relaxed, relaxed
from s6_proto import union, area, extend
from core.sdis.keys import flatten

EDITS_PER_TOKEN = 0.185   # middle of the measured gap: true members 1/27 = 0.037, nearest confusions 1/3 = 0.333


def edits(period, window):
    """Edit distance in tokens between the period and a window (relaxed: last direction free)."""
    a, b = relaxed(period), relaxed(window)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def extend_edits(S, s, L, k):
    period = S[s:s + L]
    allow = int(EDITS_PER_TOKEN * L)
    members = [(s + m * L, s + (m + 1) * L) for m in range(k)]
    changed = True
    while changed:
        changed = False
        a = members[0][0]
        best = None
        for l in range(max(1, L - allow), L + allow + 1):
            if a - l < 0:
                continue
            e = edits(period, S[a - l:a])
            if e <= allow and (best is None or e < best[0]):
                best = (e, l)
        if best:
            members.insert(0, (a - best[1], a))
            changed = True
        b = members[-1][1]
        best = None
        for l in range(max(1, L - allow), L + allow + 1):
            if b + l > len(S):
                continue
            e = edits(period, S[b:b + l])
            if e <= allow and (best is None or e < best[0]):
                best = (e, l)
        if best:
            members.append((b, b + best[1]))
            changed = True
    return members


def phase(fl, lv, S, s, L, k):
    best = None
    for rot in range(L):
        if s + rot + k * L > len(S):
            continue
        tot = sum(area(union(fl, lv, range(s + rot + m * L, s + rot + (m + 1) * L))) for m in range(k))
        if best is None or tot < best[0]:
            best = (tot, rot)
    return best[1] if best else 0


def label_period(fl, lv, members):
    """Smallest multiple of the member count's divisors at which some position is a label (same text in
    every merged member); members merged accordingly. Only for runs of equal-length members."""
    k = len(members)
    for m in range(1, k // 2 + 1):
        if k % m:
            continue
        merged = [(members[j * m][0], members[(j + 1) * m - 1][1]) for j in range(k // m)]
        P = min(b - a for a, b in merged)
        for p in range(P):
            t = [fl[lv[a + p]]["text"] for a, b in merged]
            if t[0] and len(set(t)) == 1:
                return merged, m
    return members, 1


def roles(fl, lv, members):
    P = min(b - a for a, b in members)
    out = []
    for p in range(P):
        t = [fl[lv[a + p]]["text"] for a, b in members]
        out.append("label" if t[0] and len(set(t)) == 1 else ("distinct" if len(set(t)) == len(t) else "mixed"))
    return out


def pipeline(fl, lv):
    S = tokens(fl, lv)
    cands = []
    for s, L, k in tandem_runs_relaxed(S):
        rot = phase(fl, lv, S, s, L, k)
        kk = k if s + rot + k * L <= len(S) else k - 1
        if kk < 2:
            continue
        members = extend_edits(S, s + rot, L, kk)
        tot = sum(area(union(fl, lv, range(a, b))) for a, b in members)
        cands.append((-sum(b - a for a, b in members), tot, members[0][0], members))
    cands.sort()
    accepted = []
    for _, tot, _, members in cands:
        lo, hi = members[0][0], members[-1][1]
        ok = True
        parent = None
        for gi, g in enumerate(accepted):
            glo, ghi = g["members"][0][0], g["members"][-1][1]
            if hi <= glo or lo >= ghi:
                continue
            inside = [m for m in g["members"] if m[0] <= lo and hi <= m[1]]
            if inside:
                parent = (gi, g["members"].index(inside[0]))
                continue
            ok = False
            break
        if not ok:
            continue
        members, mult = label_period(fl, lv, members)
        accepted.append({"members": members, "area": tot, "parent": parent, "merged": mult})
    return S, accepted


for name, fl, lv in [(st, comp(st)["nodes"], leaves(comp(st)["nodes"])) for st in STAMPS] + [
        (f"fixture {c}", (fl := flatten(json.loads((REPO / "tests" / "class_diff_align" / f"client_{c}_msedge.json").read_text(encoding="utf-8")))), leaves(fl, present_only=False)) for c in ("A", "B")]:
    t0 = time.perf_counter()
    S, groups = pipeline(fl, lv)
    dt = time.perf_counter() - t0
    print(f"== {name}: nodes={len(fl)} leaves={len(lv)} groups={len(groups)} time={dt * 1000:.0f} ms")
    for gi, g in enumerate(groups):
        ms = g["members"]
        rs = roles(fl, lv, ms)
        print(f"   group {gi}: members={len(ms)} lengths={[b - a for a, b in ms]} first node={lv[ms[0][0]]} last node={lv[ms[-1][1] - 1]} "
              f"parent={g['parent']} merged x{g['merged']} roles={dict(Counter(rs))}")
