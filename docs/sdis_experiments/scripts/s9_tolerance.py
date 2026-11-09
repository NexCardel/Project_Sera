"""How close the direction classes sit to their pixel boundaries (the tolerance that would need measuring):
vertical overlap / gap between consecutive leaves (same line or not), and dx for the steps to another line
(same x, left, right). Also the likeness distribution with the relaxed compare. Counts only."""
from collections import Counter
from difflib import SequenceMatcher
from common import comp, rect, STAMPS
from arr import leaves, tokens, tandem_runs_relaxed, relaxed

for st in STAMPS:
    fl = comp(st)["nodes"]
    lv = leaves(fl)
    vo, dxs = [], []
    for a, b in zip(lv, lv[1:]):
        ra, rb = rect(fl[a]), rect(fl[b])
        vover = min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1])
        vo.append(vover)
        if vover <= 0:
            dxs.append(rb[0] - ra[0])
    print(f"== {st}")
    neg = sorted(v for v in vo if v <= 0)
    pos = sorted(v for v in vo if v > 0)
    print(f"   vertical overlap of consecutive leaves: same-line values min={pos[0]} (next {pos[1]}, {pos[2]}); other-line gap values: "
          f"largest overlap(=smallest gap) {neg[-1]}, next {neg[-2]}; empty band between {neg[-1]} and {pos[0]} px")
    ad = sorted(abs(d) for d in dxs)
    nz = [d for d in ad if d > 0]
    print(f"   |dx| for steps to another line: zeros={ad.count(0)}; smallest non-zero {nz[:6]}; empty band between 0 and {nz[0]} px")
    print(f"   |dx| histogram (10 px bands): {sorted(Counter(min(d // 10 * 10, 300) for d in ad).items())}")
    # likeness distribution with the relaxed compare: true members vs non-member windows vs phase shifts
    S = tokens(fl, lv)
    runs = sorted(tandem_runs_relaxed(S), key=lambda r: (-r[1] * r[2], r[0]))
    s, L, k = runs[0]
    period = relaxed(S[s:s + L])
    def sim(w):
        return SequenceMatcher(None, period, relaxed(w), autojunk=False).ratio()
    members = [S[s + m * L:s + (m + 1) * L] for m in range(k)]
    true = [round(sim(m), 3) for m in members]
    span = range(s, s + k * L)
    outside = [round(sim(S[x:x + L]), 3) for x in range(0, len(S) - L + 1) if not any(j in span for j in range(x, x + L))]
    shifted = [round(sim(S[s + d:s + d + L]), 3) for d in range(1, L)]
    print(f"   top run start={s} L={L} k={k}; likeness of true members {true}")
    print(f"   likeness of non-member windows: max={max(outside)} hist={sorted(Counter(round(v, 1) for v in outside).items())}")
    print(f"   likeness of phase-shifted windows (shift 1..L-1): max={max(shifted)} top5={sorted(shifted, reverse=True)[:5]}")
