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
from typing import Any, Callable, Dict, Hashable, List, Sequence, Tuple

_COUNTER = re.compile(r"\[\d+\]")


def shape(e: Dict[str, Any]) -> str:
    """An element's key without its sibling counters - what it IS, not which one it is."""
    return _COUNTER.sub("", e["key"])


def _common(a: Sequence[Hashable], b: Sequence[Hashable]) -> List[Tuple[int, int]]:
    """Index pairs of a longest common sequence of a and b (difflib, no junk heuristic)."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return [(m.a + k, m.b + k) for m in sm.get_matching_blocks() for k in range(m.size)]


def align(fl: List[Dict[str, Any]], fp: List[Dict[str, Any]],
          compared: Callable[[Dict[str, Any]], bool]) -> Dict[int, int]:
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
            for x, y in _common([shape(fl[i]) for i in ga], [shape(fp[j]) for j in gb]):
                pairs[ga[x]] = gb[y]
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
