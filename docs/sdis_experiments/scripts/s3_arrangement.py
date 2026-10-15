"""Arrangement measurements on the two ITR comp pages. Counts, shapes and pixel numbers only."""
import time
from collections import Counter, defaultdict
from common import comp, children, rect, tshape, STAMPS
from arr import leaves, step, tokens, tandem_runs, runs_report, LOOK

pages = {s: comp(s)["nodes"] for s in STAMPS}

print("=== (a) leaf selection ===")
for s, fl in pages.items():
    lv = leaves(fl)
    texts = sum(1 for i in lv if fl[i]["text"])
    looks = sum(1 for i in lv if not fl[i]["text"])
    nobox = sum(1 for e in fl if (e["text"] or e["type"] in LOOK) and rect(e) is None)
    print(f"{s}: nodes={len(fl)} leaves={len(lv)} (with text {texts}, look-only {looks}); text/look nodes without a box: {nobox}")
    print("   leaf kinds:", Counter(fl[i]["type"] for i in lv).most_common())
    hs = sorted(rect(fl[i])[3] for i in lv if fl[i]["type"] == "Text")
    med = hs[len(hs) // 2]
    tall = sum(1 for h in hs if h > 1.5 * med)
    print(f"   Text box heights: median={med} min={hs[0]} max={hs[-1]}; taller than 1.5x median (wrapped): {tall} of {len(hs)}")

print("\n=== (b) steps between consecutive leaves ===")
for s, fl in pages.items():
    lv = leaves(fl)
    dirs = Counter()
    dy_down, dx_right, vgap_same, vgap_down = [], [], [], []
    for a, b in zip(lv, lv[1:]):
        ra, rb = rect(fl[a]), rect(fl[b])
        d = step(ra, rb)
        dirs[d] += 1
        if d == "R":
            dx_right.append(rb[0] - (ra[0] + ra[2]))
        if d.startswith("D"):
            dy_down.append(rb[1] - (ra[1] + ra[3]))
    print(f"{s}: directions {dict(dirs)}")
    print(f"   R gaps (px from a's right edge to b's left): min={min(dx_right)} max={max(dx_right)} hist={sorted(Counter(min(x // 10 * 10, 200) for x in dx_right).items())}")
    print(f"   D gaps (px from a's bottom to b's top): min={min(dy_down)} max={max(dy_down)} hist={sorted(Counter(max(-50, min(y // 10 * 10, 200)) for y in dy_down).items())}")
    # how close is 'same line' to 'next line': the vertical distance for pairs with/without overlap
    ov, nov = [], []
    for a, b in zip(lv, lv[1:]):
        ra, rb = rect(fl[a]), rect(fl[b])
        vover = min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1])
        (ov if vover > 0 else nov).append(vover)
    print(f"   vertical overlap of consecutive leaves: overlapping {len(ov)} (min overlap {min(ov)} px), not {len(nov)} (max overlap {max(nov)} px, i.e. gap >= {-max(nov)})")

print("\n=== (c) exact tandem repeats of the token string (kind + direction) ===")
for s, fl in pages.items():
    lv = leaves(fl)
    S = tokens(fl, lv)
    t0 = time.perf_counter()
    runs = tandem_runs(S)
    dt = time.perf_counter() - t0
    print(f"{s}: leaves={len(S)} distinct tokens={len(set(S))} runs={len(runs)} time={dt * 1000:.0f} ms")
    runs = runs_report(runs)
    # which leaves fall in the biggest run; what are its period's kinds
    s0, L, k = runs[0]
    print(f"   biggest run period kinds: {Counter(S[j][0] for j in range(s0, s0 + L)).most_common()}")
    print(f"   biggest run: first leaf node={lv[s0]} last node={lv[s0 + L * k - 1]}")
    inner = [(st, l, kk) for st, l, kk in runs if s0 <= st and st + l * kk <= s0 + L * k and l < L]
    print(f"   runs nested inside the biggest run's span: {len(inner)}; by period: {Counter(l for _, l, _ in inner)}")
    # by period within the FIRST member only
    first = [(st, l, kk) for st, l, kk in inner if st + l * kk <= s0 + L]
    print(f"   inside the first member: {[(st - s0, l, kk) for st, l, kk in first]}")

print("\n=== (d) pixel spread of the repeated arrangement (biggest run) ===")
for s, fl in pages.items():
    lv = leaves(fl)
    S = tokens(fl, lv)
    runs = sorted(tandem_runs(S), key=lambda r: (-r[1] * r[2], r[0]))
    s0, L, k = runs[0]
    members = [lv[s0 + m * L: s0 + (m + 1) * L] for m in range(k)]
    boxes = []
    for mem in members:
        rs = [rect(fl[i]) for i in mem]
        x0, y0 = min(r[0] for r in rs), min(r[1] for r in rs)
        x1, y1 = max(r[0] + r[2] for r in rs), max(r[1] + r[3] for r in rs)
        boxes.append((x0, y0, x1 - x0, y1 - y0))
    print(f"{s}: member union boxes (x, y, w, h): {boxes}")
    tops = [b[1] for b in boxes]
    print(f"   vertical pitch between members: {[b - a for a, b in zip(tops, tops[1:])]}")
    # per position in the period: offset of the leaf relative to the member's union box origin
    spread_x, spread_y, spread_w, spread_h = [], [], [], []
    for pos in range(L):
        offs = []
        for m, mem in enumerate(members):
            r = rect(fl[mem[pos]])
            offs.append((r[0] - boxes[m][0], r[1] - boxes[m][1], r[2], r[3]))
        spread_x.append(max(o[0] for o in offs) - min(o[0] for o in offs))
        spread_y.append(max(o[1] for o in offs) - min(o[1] for o in offs))
        spread_w.append(max(o[2] for o in offs) - min(o[2] for o in offs))
        spread_h.append(max(o[3] for o in offs) - min(o[3] for o in offs))
    print(f"   per-position spread across members, x offset: max={max(spread_x)} hist={sorted(Counter(spread_x).items())}")
    print(f"   per-position spread across members, y offset: max={max(spread_y)} hist={sorted(Counter(min(v // 5 * 5, 50) for v in spread_y).items())}")
    print(f"   per-position spread, width: max={max(spread_w)} positions with spread>TOL: {sum(1 for v in spread_w if v > 3)} of {L}")
    print(f"   per-position spread, height: max={max(spread_h)} positions with spread>TOL: {sum(1 for v in spread_h if v > 3)} of {L}")
    # text repeated across members at the same position = label
    lab = sum(1 for pos in range(L) if len({fl[mem[pos]]["text"] for mem in members}) == 1 and fl[members[0][pos]]["text"])
    dist = sum(1 for pos in range(L) if len({fl[mem[pos]]["text"] for mem in members}) == k)
    print(f"   positions with the same text in every member (labels): {lab}; a different text in every member: {dist}; other: {L - lab - dist}")

print("\n=== (e) does a text that wraps change the tree or the tokens? ===")
for s, fl in pages.items():
    kids = children(fl)
    lv = leaves(fl)
    hs = sorted(rect(fl[i])[3] for i in lv if fl[i]["type"] == "Text")
    med = hs[len(hs) // 2]
    wrapped = [i for i in lv if fl[i]["type"] == "Text" and rect(fl[i])[3] > 1.5 * med]
    print(f"{s}: wrapped texts: {len(wrapped)} -> depth of each: {[fl[i]['depth'] for i in wrapped]}, text length: {[len(fl[i]['text']) for i in wrapped]}")
