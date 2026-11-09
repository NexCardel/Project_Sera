"""Fictional fixtures through the tree method and the arrangement prototype: the align fixture (period choice
by the label witness), a Firefox-shaped dashboard (wrapper-less tile, extra inner Group, four unrelated
single-text boxes), and the IFF / GSTR-1 trap. Fictional data only."""
import json
import sys
from collections import Counter
from common import REPO, children, rect
from arr import leaves, tokens, tandem_runs, tandem_runs_relaxed, relaxed, step
from s6_proto import union, area, extend
from core.sdis.keys import flatten
import cards

sys.path.insert(0, str(REPO / "tests"))
from test_class_diff_cards import Page, _top_tiles, _tile_page, TEST_THETA  # noqa: E402

FIX = REPO / "tests" / "class_diff_align"


def period_by_labels(fl, lv, s, L, k):
    """The smallest multiple m*L of the arrangement period at which some position holds the same text in
    every member (a label). None when no multiple does (anonymous rows)."""
    for m in range(1, k // 2 + 1):
        P = m * L
        kk = k // m
        members = [range(s + j * P, s + (j + 1) * P) for j in range(kk)]
        for p in range(P):
            t = [fl[lv[r[p]]]["text"] for r in members]
            if t[0] and len(set(t)) == 1:
                return P, kk, p
    return None


def top_runs(fl, lv):
    S = tokens(fl, lv)
    runs = tandem_runs_relaxed(S)
    return S, sorted(runs, key=lambda r: (-r[1] * r[2], r[0]))


print("== align fixture: period choice by the label witness ==")
for client in ("A", "B"):
    rec = json.loads((FIX / f"client_{client}_msedge.json").read_text(encoding="utf-8"))
    fl = flatten(rec)
    lv = leaves(fl, present_only=False)
    S, runs = top_runs(fl, lv)
    s, L, k = runs[0]
    print(f"  client {client}: top run L={L} k={k}; label witness -> {period_by_labels(fl, lv, s, L, k)} (period, members, first label position)")
    print(f"  other runs: {[(l, kk, period_by_labels(fl, lv, st, l, kk)) for st, l, kk in runs[1:4]]}")


def firefox_dashboard():
    """Five tiles as direct children of the document. Tile 3 has NO wrapper (its parts lie loose at the top
    level, bug 7); tile 4 has an extra inner Group (bug 8); plus four unrelated single-text boxes (bug 9)."""
    p = Page()
    p.add(-1, "Group", cls="header", rect=[0, 0, 600, 40])
    # four unrelated single-text boxes, stacked and scattered
    p.add(-1, "ComboBox", name="English", rect=[500, 50, 80, 20])
    p.add(-1, "Button", name="File Returns", rect=[10, 50, 100, 20])
    p.add(-1, "Text", name="ticker ticker ticker", rect=[10, 80, 400, 16])
    p.add(-1, "Text", name="* mandatory fields", rect=[10, 100, 150, 16])
    names = ["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"]
    for k, name in enumerate(names):
        x, y = k * 120, 140
        holder = -1 if k == 2 else p.add(-1, "Group", rect=[x, y, 110, 100])
        link_holder = p.add(holder, "Group", rect=[x, y, 110, 40]) if k == 3 else holder
        link = p.add(link_holder, "Hyperlink", name=f"About {name}", rect=[x, y, 100, 30])
        code = p.add(link, "Group", rect=[x, y + 10, 100, 14])
        p.add(code, "Text", name=name, rect=[x, y + 10, 100, 14])
        p.add(holder, "Text", name="Status-", rect=[x, y + 40, 40, 14])
        p.add(holder, "Text", name=f"state {k}", rect=[x + 45, y + 40, 50, 14])
        p.add(holder, "Button", name="View", rect=[x, y + 70, 50, 20])
    return p


print("\n== Firefox-shaped dashboard fixture ==")
p = firefox_dashboard()
fl, docs = cards.load_flat(p.read())
gs = cards.find_groups(fl, docs, TEST_THETA)
print(f"  tree method: groups={[(len(g['members']), [len(m['items']) for m in g['members']]) for g in gs]}  (5 tiles expected; the wrapper-less and the extra-Group tiles?)")
lv = leaves(fl, present_only=False)
S, runs = top_runs(fl, lv)
print(f"  leaves={len(lv)} tokens of the tile region: {S[4:8]} ...")
print(f"  arrangement runs (start, period, count): {runs[:4]}")
s, L, k = runs[0]
print(f"  top run covers leaf nodes {lv[s]}..{lv[s + L * k - 1]}: members={k} period={L}; label witness={period_by_labels(fl, lv, s, L, k)}")
loose = [i for i in lv[s:s + L * k] if fl[i]['parent'] < 0]
print(f"  leaves of the run that sit loose at the top level (the wrapper-less tile's parts): {len(loose)}")
pre = [t for t in S[:s]]
print(f"  the four unrelated boxes: tokens {pre[:4]} -> in a run? {any(st <= 0 < st + l * kk for st, l, kk in runs)}")

print("\n== IFF / GSTR-1 trap through the arrangement: cards paired by name position only ==")
A = _tile_page(["IFF", "GSTR-2A"], "a").read()
B = _tile_page(["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"], "b").read()
out = []
for rec in (A, B):
    fl, _ = cards.load_flat(rec)
    lv = leaves(fl, present_only=False)
    S, runs = top_runs(fl, lv)
    s, L, k = runs[0]
    members = [range(s + j * L, s + (j + 1) * L) for j in range(k)]
    roles = []
    for q in range(L):
        t = [fl[lv[m[q]]]["text"] for m in members]
        roles.append("label" if len(set(t)) == 1 and t[0] else ("distinct" if len(set(t)) == len(t) else "mixed"))
    out.append((fl, lv, members, roles))
    print(f"  page with {k} tiles: period {L}, roles {roles}")
(fa, la, ma, ra), (fb, lb, mb, rb) = out
cands = [q for q in range(len(ra)) if ra[q] == rb[q] == "distinct"]
shared = {q: len({fa[la[m[q]]]["text"] for m in ma} & {fb[lb[m[q]]]["text"] for m in mb}) for q in cands}
print(f"  distinct-on-both positions and shared texts: {shared} -> name position = earliest with the most shared")
name_q = min(cands, key=lambda q: (-shared[q], q))
pairs = [(i, j) for i, m in enumerate(ma) for j, n in enumerate(mb) if fa[la[m[name_q]]]["text"] == fb[lb[n[name_q]]]["text"]]
print(f"  pairs by name: {pairs} (1 expected: GSTR-2A), IFF paired? {any(fa[la[ma[i][name_q]]]['text'] == 'IFF' for i, _ in pairs)}")
