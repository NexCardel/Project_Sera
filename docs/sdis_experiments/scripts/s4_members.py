"""Per-card token strings (tree card roots as ground truth), their differences, the likeness distribution
between true members and non-members, the phase rule, and label/value directions. Counts only."""
from collections import Counter
from difflib import SequenceMatcher
from common import comp, children, rect, tshape, STAMPS
from arr import leaves, tokens, step
import cards

CARD_ROOTS = {"121513": [88, 159, 230, 300, 370], "121834": [88, 158, 233, 303]}


def under(fl, kids, root):
    out, stack = set(), [root]
    while stack:
        j = stack.pop()
        out.add(j)
        stack.extend(kids[j])
    return out


def sim(a, b):
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


for s in STAMPS:
    fl = comp(s)["nodes"]
    kids = children(fl)
    lv = leaves(fl)
    S = tokens(fl, lv)
    pos = {i: k for k, i in enumerate(lv)}
    print(f"===== {s}: leaves={len(lv)}")
    members = []
    for r in CARD_ROOTS[s]:
        subtree = under(fl, kids, r)
        ks = [pos[i] for i in lv if i in subtree]
        members.append((ks[0], ks[-1] + 1))
    print("card leaf ranges (start, end) and lengths:", [(a, b, b - a) for a, b in members])
    strs = [S[a:b] for a, b in members]
    # where do the cards differ?
    base = strs[0]
    for m, t in enumerate(strs[1:], 1):
        sm = SequenceMatcher(None, base, t, autojunk=False)
        ops = [(op, i1, i2, j1, j2) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
        print(f"  card 0 vs card {m}: similarity={sm.ratio():.3f} edits={ops}")
        for op, i1, i2, j1, j2 in ops:
            print(f"     card0[{i1}:{i2}]={base[i1:i2]}  card{m}[{j1}:{j2}]={t[j1:j2]}")
    # likeness distribution: true member pairs vs. a member against every other window of the same length
    true_pairs = [sim(strs[a], strs[b]) for a in range(len(strs)) for b in range(a + 1, len(strs))]
    L = len(strs[0])
    inside = {k for a, b in members for k in range(a, b)}
    others = []
    for st in range(0, len(S) - L + 1, 3):
        if any(k in inside for k in range(st, st + L)):
            continue
        others.append(sim(strs[0], S[st:st + L]))
    # also: a member against windows that overlap a member but with the wrong phase
    shifted = [sim(strs[0], S[members[1][0] + d: members[1][0] + d + L]) for d in (3, 7, 13, 20)]
    print(f"  likeness true pairs: min={min(true_pairs):.3f} max={max(true_pairs):.3f} all={[round(v, 3) for v in true_pairs]}")
    print(f"  likeness vs non-member windows (n={len(others)}): max={max(others):.3f} hist={sorted(Counter(round(v, 1) for v in others).items())}")
    print(f"  likeness vs phase-shifted windows of card 1: {[round(v, 3) for v in shifted]}")
    # phase rule: for each rotation of the period of the exact run, are member boxes disjoint?
    a0, b0 = members[0]
    pitch = members[1][0] - members[0][0]
    print(f"  member pitch in leaves: {[members[m + 1][0] - members[m][0] for m in range(len(members) - 1)]}")
    def union(ks):
        rs = [rect(fl[lv[k]]) for k in ks]
        return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[0] + r[2] for r in rs), max(r[1] + r[3] for r in rs))
    def overlap_area(u, v):
        w = min(u[2], v[2]) - max(u[0], v[0])
        h = min(u[3], v[3]) - max(u[1], v[1])
        return max(0, w) * max(0, h)
    rows = []
    for rot in range(0, L):
        ms = [range(a0 + rot + m * pitch, a0 + rot + m * pitch + L) for m in range(2)]
        if ms[-1][-1] >= len(S):
            break
        us = [union(m) for m in ms]
        rows.append((rot, overlap_area(us[0], us[1]), (us[0][2] - us[0][0]) * (us[0][3] - us[0][1])))
    print(f"  phase rule (rotation, overlap area of members 0 and 1, member area): disjoint rotations = {[r for r, o, _ in rows if o == 0]} of {len(rows)}")
    # label / value directions inside a card: a page-repeated text (label) and the step to the next leaf
    page_count = Counter(fl[i]["text"] for i in lv if fl[i]["text"])
    a, b = members[0]
    dirs = Counter()
    for k in range(a, b - 1):
        i, j = lv[k], lv[k + 1]
        lab_i = page_count[fl[i]["text"]] >= 2 and fl[i]["type"] == "Text"
        lab_j = page_count[fl[j]["text"]] >= 2 and fl[j]["type"] == "Text"
        d = step(rect(fl[i]), rect(fl[j]))
        dirs[("label" if lab_i else "other", "label" if lab_j else "other", d)] += 1
    print("  inside card 0, (kind of leaf, kind of next leaf, direction) counts:")
    for key, c in sorted(dirs.items()):
        print(f"     {key}: {c}")
    # the tree method's own view of the cards for reference
    gs = cards.find_groups(fl, None)
    big = max(gs, key=lambda g: max(len(m["items"]) for m in g["members"]))
    print(f"  tree method: biggest group members={len(big['members'])} items={[len(m['items']) for m in big['members']]}")
