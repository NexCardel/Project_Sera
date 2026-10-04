"""
core/sdis/align.py - pair two pages' elements by content and shape
===================================================================
A key (keys.py) ends in sibling counters - "Group.card[2] / Text[3]" - so one extra element on one
client's page (a notice, one more list row) renumbers its look-alike siblings, and comparing by key
pairs them with the wrong partner. Alignment pairs them the way a text diff lines up two files:

  1. ANCHORS - elements with the same SHAPE (the key without its [n] counters: element types, ids,
     classes) AND the same text, matched in page order by longest common sequence. Headings,
     labels, a period both lists show... An anchor never crosses another: order is kept.
  2. BETWEEN two anchors, the remaining elements are paired by shape alone, again in page order -
     the value next to "Financial Year" pairs with the value next to "Financial Year".

What pairs nothing is only on that side (one more row, a notice). Nothing here knows any portal,
class name or wording: shapes and texts only.

Known limit: two look-alike values with no anchor between them, one missing on one side - nothing
can tell which one is missing; the earlier one is paired.
"""

import re
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence, Tuple

AMBIGUOUS_MARGIN = 0.5
_MAX_CELLS = 40000

_COUNTER = re.compile(r"\[\d+\]")


def _pattern(text: str) -> str:
    r"""collapse whitespace, digits -> '9', letters (regex [^\W\d_]) -> 'A', then collapse runs of the same char"""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = re.sub(r"\d", "9", t)
    t = re.sub(r"[^\W\d_]", "A", t)
    return re.sub(r"(.)\1+", r"\1", t)


def _column(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """sideways overlap of node rects ([x, y, w, h], from e['node']['rect']) divided by the smaller width, 0.0 when a rect is missing or a width <= 0."""
    na = a.get("node") if isinstance(a, dict) else None
    nb = b.get("node") if isinstance(b, dict) else None
    ra = na.get("rect") if isinstance(na, dict) else (a.get("rect") if isinstance(a, dict) else None)
    rb = nb.get("rect") if isinstance(nb, dict) else (b.get("rect") if isinstance(b, dict) else None)
    if not ra or not rb or len(ra) < 4 or len(rb) < 4 or ra[2] <= 0 or rb[2] <= 0:
        return 0.0
    overlap = min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0])
    return max(0.0, overlap) / min(ra[2], rb[2])


def _score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    pat_eq = 1.0 if _pattern(a.get("text", "")) == _pattern(b.get("text", "")) else 0.0
    return pat_eq + _column(a, b)


def _best_injection(score: List[List[float]],
                    banned: Optional[Tuple[int, int]] = None) -> Tuple[float, List[int]]:
    k = len(score)
    if k == 0:
        return 0.0, []
    n = len(score[0])
    if k > n:
        return -float("inf"), []

    dp = [[-float("inf")] * (n + 1) for _ in range(k + 1)]
    dp[0] = [0.0] * (n + 1)

    for i in range(1, k + 1):
        for j in range(i, n + 1):
            s = -float("inf") if banned == (i - 1, j - 1) else score[i - 1][j - 1]
            prev = dp[i - 1][j - 1]
            take = prev + s if (prev > -float("inf") and s > -float("inf")) else -float("inf")
            skip = dp[i][j - 1]
            dp[i][j] = max(skip, take)

    total = dp[k][n]
    if total == -float("inf"):
        return -float("inf"), []

    chosen = [0] * k
    i, j = k, n
    while i > 0:
        if j > i and dp[i][j] == dp[i][j - 1]:
            j -= 1
        else:
            chosen[i - 1] = j - 1
            i -= 1
            j -= 1
    return total, chosen


def _look_alikes(xs: List[int], ys: List[int],
                 fl: List[Dict[str, Any]], fp: List[Dict[str, Any]],
                 pairs: Dict[int, int], ambiguous: Optional[set] = None) -> None:
    if not xs or not ys:
        return

    shorter_is_fl = len(xs) <= len(ys)
    if shorter_is_fl:
        k, n = len(xs), len(ys)
        score = [[_score(fl[xs[r]], fp[ys[c]]) for c in range(n)] for r in range(k)]
    else:
        k, n = len(ys), len(xs)
        score = [[_score(fl[xs[c]], fp[ys[r]]) for c in range(n)] for r in range(k)]

    best, chosen = _best_injection(score)
    if best == -float("inf"):
        return

    if k * n * k > _MAX_CELLS:
        unsure = [True] * k
    else:
        unsure = []
        for r in range(k):
            alt, _ = _best_injection(score, banned=(r, chosen[r]))
            unsure.append((best - alt) < AMBIGUOUS_MARGIN)

    for r in range(k):
        if shorter_is_fl:
            fl_idx = xs[r]
            fp_idx = ys[chosen[r]]
        else:
            fl_idx = xs[chosen[r]]
            fp_idx = ys[r]
        pairs[fl_idx] = fp_idx
        if unsure[r] and bool(fl[fl_idx].get("text")) and ambiguous is not None:
            ambiguous.add(fl_idx)


def shape(e: Dict[str, Any]) -> str:
    """An element's key without its sibling counters - what it IS, not which one it is."""
    return _COUNTER.sub("", e["key"])


def _common(a: Sequence[Hashable], b: Sequence[Hashable]) -> List[Tuple[int, int]]:
    """Index pairs of a longest common sequence of a and b (difflib, no junk heuristic)."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return [(m.a + k, m.b + k) for m in sm.get_matching_blocks() for k in range(m.size)]


def align(fl: List[Dict[str, Any]], fp: List[Dict[str, Any]],
          compared: Callable[[Dict[str, Any]], bool],
          ambiguous: Optional[set] = None) -> Dict[int, int]:
    """Pairs {index in fl: index in fp} for the elements `compared` accepts, in page order."""
    a = [i for i, e in enumerate(fl) if compared(e)]
    b = [j for j, e in enumerate(fp) if compared(e)]
    anchors = [(a[x], b[y]) for x, y in _common([(shape(fl[i]), fl[i]["text"]) for i in a],
                                                [(shape(fp[j]), fp[j]["text"]) for j in b])]
    pairs = dict(anchors)
    bounds = [(-1, -1)] + anchors + [(len(fl), len(fp))]
    for (i0, j0), (i1, j1) in zip(bounds, bounds[1:]):
        ga = a[bisect_right(a, i0):bisect_left(a, i1)]
        gb = b[bisect_right(b, j0):bisect_left(b, j1)]
        if ga and gb:
            shapes_a: Dict[str, List[int]] = {}
            for i in ga:
                shapes_a.setdefault(shape(fl[i]), []).append(i)
            shapes_b: Dict[str, List[int]] = {}
            for j in gb:
                shapes_b.setdefault(shape(fp[j]), []).append(j)

            uneven = {sh for sh in shapes_a if sh in shapes_b and len(shapes_a[sh]) != len(shapes_b[sh])}

            for x, y in _common([shape(fl[i]) for i in ga], [shape(fp[j]) for j in gb]):
                sh = shape(fl[ga[x]])
                if sh not in uneven:
                    pairs[ga[x]] = gb[y]

            for sh in sorted(uneven, key=lambda s: ga.index(shapes_a[s][0])):
                _look_alikes(shapes_a[sh], shapes_b[sh], fl, fp, pairs, ambiguous)
    return pairs


def pair_moved(fl: List[Dict[str, Any]], fp: List[Dict[str, Any]], pairs: Dict[int, int],
               compared: Callable[[Dict[str, Any]], bool]) -> Dict[int, int]:
    """Alignment keeps page order, so a block that MOVED (a footer the page re-rendered elsewhere)
    pairs with nothing. Pair what is left on both sides, in any order:
      1. the same key AND the same text - the very same element;
      2. a (shape, text) that is UNIQUE among the leftovers of each side.
    Never by shape alone: two look-alike values out of order cannot be told apart."""
    used = set(pairs.values())
    left_a = [i for i, e in enumerate(fl) if compared(e) and i not in pairs]
    left_b = {j for j, e in enumerate(fp) if compared(e) and j not in used}
    by_key: Dict[Tuple[str, str], List[int]] = {}
    for j in sorted(left_b):
        by_key.setdefault((fp[j]["key"], fp[j]["text"]), []).append(j)
    rest = []
    for i in left_a:
        cand = by_key.get((fl[i]["key"], fl[i]["text"]))
        if cand:
            j = cand.pop(0)
            pairs[i] = j
            left_b.discard(j)
        else:
            rest.append(i)
    face_a: Dict[Tuple[str, str], List[int]] = {}
    face_b: Dict[Tuple[str, str], List[int]] = {}
    for i in rest:
        face_a.setdefault((shape(fl[i]), fl[i]["text"]), []).append(i)
    for j in left_b:
        face_b.setdefault((shape(fp[j]), fp[j]["text"]), []).append(j)
    for f, ia in face_a.items():
        jb = face_b.get(f)
        if len(ia) == 1 and jb and len(jb) == 1:
            pairs[ia[0]] = jb[0]
    return pairs
