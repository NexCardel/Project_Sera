"""Validate the design pieces on the ITR pages: phase rule, approximate extension, nested-run classification,
recurrence per slot across the two clients, value stability, whole-pipeline timing. Counts only."""
import time
from collections import Counter
from difflib import SequenceMatcher
from common import comp, children, rect, STAMPS
from arr import leaves, tokens, step, tandem_runs, tandem_runs_relaxed, relaxed
from s6_proto import union, area, extend, sim  # noqa: F401  (s6 prints its own run first)


def best_phase_dir(fl, lv, s, L, k):
    rows = []
    for rot in range(L):
        ms = [range(s + rot + m * L, s + rot + (m + 1) * L) for m in range(k)]
        if ms[-1][-1] >= len(lv):
            continue
        us = [union(fl, lv, m) for m in ms]
        rows.append((sum(area(u) for u in us), rot))
    rows.sort()
    return rows


def roles_of(fl, lv, members):
    L = min(b - a for a, b in members)
    out = []
    for p in range(L):
        t = [fl[lv[a + p]]["text"] for a, b in members]
        out.append("label" if len(set(t)) == 1 and t[0] else ("distinct" if len(set(t)) == len(t) else ("mixed" if t[0] or any(t) else "look")))
    return out


print("\n\n########## s7 ##########")
results = {}
for st in STAMPS:
    t0 = time.perf_counter()
    fl = comp(st)["nodes"]
    lv = leaves(fl)
    S = tokens(fl, lv)
    runs = sorted(tandem_runs_relaxed(S), key=lambda r: (-r[1] * r[2], r[0]))
    s, L, k = runs[0]
    ph = best_phase_dir(fl, lv, s, L, k)
    rot = ph[0][1]
    members = extend(S, s + rot, L, k if s + rot + k * L <= len(S) else k - 1, 0.9)
    t1 = time.perf_counter()
    print(f"== {st}: top run start={s} L={L} k={k}; phase areas (area, rotation) best 3: {ph[:3]}; chosen rotation {rot}")
    print(f"   members after extension: {len(members)} lengths={[b - a for a, b in members]} first node={lv[members[0][0]]} last node={lv[members[-1][1] - 1]}")
    print(f"   pipeline time (leaves, tokens, runs, phase, extension): {(t1 - t0) * 1000:.0f} ms for {len(fl)} nodes / {len(lv)} leaves")
    roles = roles_of(fl, lv, members)
    print(f"   roles by position: {Counter(roles)}; label positions {[p for p, r in enumerate(roles) if r == 'label']}")
    print(f"   distinct positions {[p for p, r in enumerate(roles) if r == 'distinct']}, mixed {[p for p, r in enumerate(roles) if r == 'mixed']}")
    # nested runs inside member 0, and their classification
    a0, b0 = members[0]
    inner = tandem_runs_relaxed(S[a0:b0])
    print(f"   nested runs in member 0 (offset, period, count): {inner}")
    for st2, l, kk in inner:
        # W-roles: within member 0 across the inner members; A-roles: across outer members at the same position
        rows = [range(a0 + st2 + j * l, a0 + st2 + (j + 1) * l) for j in range(kk)]
        wr, ar = [], []
        for p in range(l):
            t = [fl[lv[r[p]]]["text"] for r in rows]
            wr.append("const" if len(set(t)) == 1 else "varies")
            ar.append(all(roles[st2 + j * l + p] == "label" for j in range(kk)))
        field_block = any(w == "varies" and a for w, a in zip(wr, ar))
        print(f"     run offset {st2} period {l} x{kk}: within-member {wr}, label-across-cards {ar} -> {'FIELD BLOCK' if field_block else 'LIST'}")
    results[st] = (fl, lv, members, roles)

print("\n== recurrence of each distinct/mixed position across the two clients, and value stability ==")
(flA, lvA, mA, rA), (flB, lvB, mB, rB) = results["121513"], results["121834"]
L = min(len(rA), len(rB))
for p in range(L):
    if rA[p] == "label" and rB[p] == "label":
        continue
    ta = [flA[lvA[a + p]]["text"] for a, b in mA]
    tb = [flB[lvB[a + p]]["text"] for a, b in mB]
    shared = len(set(ta) & set(tb))
    occ, dist, mx = len(ta) + len(tb), len(set(ta) | set(tb)), max(len(ta), len(tb))
    R = 1 - (dist - mx) / (occ - mx) if occ > mx else 0.0
    vals = max(len(flA[lvA[a + p]].get("values") or []) for a, b in mA)
    print(f"   position {p:2d} kind={flA[lvA[mA[0][0] + p]]['type']:9s} roles A/B={rA[p]}/{rB[p]}: shared texts={shared} r_A={shared / len(ta):.2f} r_B={shared / len(tb):.2f} corpus R={R:.2f} max values per node={vals}")
print("   label positions whose text differs between the two pages (should be 0):",
      sum(1 for p in range(L) if rA[p] == rB[p] == "label" and flA[lvA[mA[0][0] + p]]["text"] != flB[lvB[mB[0][0] + p]]["text"]))
print("   name candidates = distinct on both pages, every node stable, highest corpus R: see table above")

print("\n== card pairing by the name position across the two pages ==")
name_p = max((p for p in range(L) if rA[p] == rB[p] == "distinct"),
             key=lambda p: len({flA[lvA[a + p]]["text"] for a, b in mA} & {flB[lvB[a + p]]["text"] for a, b in mB}))
na = [flA[lvA[a + name_p]]["text"] for a, b in mA]
nb = [flB[lvB[a + name_p]]["text"] for a, b in mB]
pairs = [(i, nb.index(t)) for i, t in enumerate(na) if t in nb]
print(f"   name position={name_p}; pairs (A member, B member)={pairs}; only A={[i for i in range(len(na)) if na[i] not in nb]}; only B={[j for j in range(len(nb)) if nb[j] not in na]}")
# inside each paired card: slot by slot
fixed = var = 0
for i, j in pairs:
    a, b = mA[i], mB[j]
    sm = SequenceMatcher(None, [t[:2] for t in tokens(flA, lvA)[a[0]:a[1]]], [t[:2] for t in tokens(flB, lvB)[b[0]:b[1]]], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for d in range(i2 - i1):
                fixed += flA[lvA[a[0] + i1 + d]]["text"] == flB[lvB[b[0] + j1 + d]]["text"]
                var += flA[lvA[a[0] + i1 + d]]["text"] != flB[lvB[b[0] + j1 + d]]["text"]
print(f"   slots inside paired cards: fixed={fixed} variable={var} (each value once)")
