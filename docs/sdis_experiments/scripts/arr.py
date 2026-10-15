"""Prototype pieces of the arrangement idea, used by the measurement scripts. Counts only are printed."""
from collections import Counter, defaultdict
from common import rect

LOOK = {"Button", "CheckBox", "ComboBox", "Edit", "Image", "RadioButton"}
TOL = 3


def leaves(fl, present_only=True):
    """Leaf elements: nodes that carry a text, or a look (button, box, image), with a non-empty box,
    present in the comp page. Document order."""
    out = []
    for i, e in enumerate(fl):
        if present_only and e.get("present") is False:
            continue
        if not (e["text"] or e["type"] in LOOK):
            continue
        r = rect(e)
        if r is None:
            continue
        out.append(i)
    return out


def step(ra, rb):
    """Direction from box ra to box rb: R (same line, to the right), L (same line, to the left / back),
    O (same line, overlapping), D/DL/DR (next line below), U/UL/UR (above)."""
    xa, ya, wa, ha = ra
    xb, yb, wb, hb = rb
    vover = min(ya + ha, yb + hb) - max(ya, yb)
    if vover > 0:
        if xb >= xa + wa - TOL:
            return "R"
        if xb + wb <= xa + TOL:
            return "L"
        return "O"
    d = "D" if yb > ya else "U"
    if xb > xa + TOL:
        return d + "R"
    if xb < xa - TOL:
        return d + "L"
    return d


def tokens(fl, lv):
    """One token per leaf: its kind and the step to the next leaf ('END' for the last)."""
    out = []
    for k, i in enumerate(lv):
        kind = fl[i]["type"]
        if k + 1 < len(lv):
            d = step(rect(fl[i]), rect(fl[lv[k + 1]]))
        else:
            d = "END"
        out.append((kind, d))
    return out


def tandem_runs(S, min_k=2):
    """Maximal exact tandem repeats of S: (start, period L, count k), S[start + iL : start + (i+1)L] equal
    for i < k, k >= min_k, not extendable left or right, period primitive (not a multiple of a shorter one)."""
    n = len(S)
    runs = []
    for L in range(1, n // 2 + 1):
        s = 0
        while s + 2 * L <= n:
            if S[s:s + L] == S[s + L:s + 2 * L]:
                k = 2
                while s + (k + 1) * L <= n and S[s:s + L] == S[s + k * L:s + (k + 1) * L]:
                    k += 1
                # primitive: the period itself must not be a tandem repeat of a divisor
                prim = True
                for d in range(1, L):
                    if L % d == 0 and all(S[s:s + d] == S[s + j * d:s + (j + 1) * d] for j in range(1, L // d)):
                        prim = False
                        break
                if prim and k >= min_k:
                    runs.append((s, L, k))
                s += k * L
            else:
                s += 1
    return runs


def _eq_relaxed(S, a, b, L):
    """S[a:a+L] matches S[b:b+L] with the LAST position compared by kind only: a member's last step leads
    out of the member (to the next member, or to whatever follows the last one), so it is not part of the
    member's own arrangement."""
    return S[a:a + L - 1] == S[b:b + L - 1] and S[a + L - 1][0] == S[b + L - 1][0]


def tandem_runs_relaxed(S, min_k=2, min_L=2):
    """Maximal tandem repeats of S with the relaxed last position (see _eq_relaxed), periods >= min_L
    (a one-leaf period carries no arrangement), primitive, not extendable."""
    n = len(S)
    runs = []
    for L in range(min_L, n // 2 + 1):
        s = 0
        while s + 2 * L <= n:
            if _eq_relaxed(S, s, s + L, L):
                k = 2
                while s + (k + 1) * L <= n and _eq_relaxed(S, s, s + k * L, L):
                    k += 1
                prim = True
                for d in range(1, L):
                    if L % d == 0 and all(S[s:s + d] == S[s + j * d:s + (j + 1) * d] for j in range(1, L // d)):
                        prim = False
                        break
                if prim and k >= min_k:
                    runs.append((s, L, k))
                s += k * L
            else:
                s += 1
    return runs


def relaxed(seq):
    """A member's tokens with the last direction dropped (kind only), for likeness comparisons."""
    return list(seq[:-1]) + [(seq[-1][0], "*")] if seq else []


def runs_report(runs, top=12):
    runs = sorted(runs, key=lambda r: (-r[1] * r[2], r[0]))
    for s, L, k in runs[:top]:
        print(f"    start={s} period={L} members={k} span={L * k}")
    return runs
