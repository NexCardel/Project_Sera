"""
tools/pre_dev/class_diff/card_match.py - PRE-DEV: how alike the cards of one record set are, node by node
========================================================================================================
shape_map.py finds record sets: 2+ containers of one shape (the cards). This module looks inside them.

Each card is cut into its leaves (shape_map's drawn texts, plus drawn boxes that hold no drawn text, such
as an icon button), in page order. Every leaf gets
  kind   what it is: a control (button, link, edit, combo, check, radio, image) or, for plain text, the
         value type from core/sdis/labels.value_type (text, number, date, period, amount, code, ...).
  path   its place inside the card: its key below the card's key, without [n] counters.

Two cards are compared in layers. Each layer gives a percentage:
  1 composition  how many nodes of each kind: sum over kinds of min(count A, count B) / max(nodes A, nodes B).
                 Same number of nodes and the same kinds = 100 %.
  2 adjacency    the leaves are aligned in page order (longest common subsequence, a pair needs the same
                 kind; same kind and same path is preferred). Each aligned pair scores 1/3 each for:
                 the same path, the left neighbours aligned to each other, the right neighbours aligned to
                 each other (no neighbour on both sides counts as aligned). Sum of pair scores /
                 max(nodes A, nodes B). A node without a partner scores 0.
  overall        the mean of the layers (equal weights; PROVISIONAL, not measured). LAYERS lists the layers,
                 so a further one is one more function and one more entry.

structure() reads the cards of one set together: the card that matches the others best (the medoid) is the
reference; every other card is aligned to it. Each reference leaf becomes a slot with the number of cards
it was found in and whether its text is the same in all of them (template: a label) or differs (data: a
value). A value slot's label is the nearest same-text slot before it in the same block (the card's direct
child it sits in). Leaves no reference leaf took are extra slots, grouped by (kind, path).

Texts are compared in memory only. Nothing returned holds a text: kinds, paths, counts and percentages.
Deterministic: the same input gives the same output.
"""

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO = str(Path(__file__).resolve().parents[3])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from core.sdis.labels import value_type  # noqa: E402

CONTROL_KIND = {"Button": "button", "SplitButton": "button", "MenuItem": "button", "Hyperlink": "link",
                "Edit": "edit", "ComboBox": "combo", "CheckBox": "check", "RadioButton": "radio",
                "Image": "image"}


# ── leaves ────────────────────────────────────────────────────────────────────

def _kind(n: Dict[str, Any], nodes: List[Dict[str, Any]]) -> str:
    """A leaf's kind: the control it is or sits in (a text inside a link is a link), else its value type."""
    if n["type"] in CONTROL_KIND:
        return CONTROL_KIND[n["type"]]
    if n.get("link"):
        return "link"
    p, hops = n["p"], 0
    while p >= 0 and hops < 3:                 # text drawn by the label inside a button or a check box
        t = nodes[p]["type"]
        if t in ("Button", "SplitButton", "CheckBox", "RadioButton"):
            return CONTROL_KIND[t]
        p, hops = nodes[p]["p"], hops + 1
    return value_type(n["text"]) if n["text"] else n["type"].lower()


def _strip(key: str) -> str:
    import re
    return re.sub(r"\[\d+\]", "", key)


def card_leaves(card: int, nodes: List[Dict[str, Any]], flat: List[Dict[str, Any]],
                kids: Dict[int, List[int]]) -> List[Dict[str, Any]]:
    """The card's leaves in page order: kind, path below the card, block (the card's direct child holding
    the leaf), text (memory only) and node index."""
    base = flat[card]["key"] if card < len(flat) else ""
    out: List[Dict[str, Any]] = []

    def drawn_below(i):
        return any(nodes[c]["text_drawn"] or nodes[c]["box_drawn"] or drawn_below(c) for c in kids.get(i, ()))

    def walk(i, block):
        for c in kids.get(i, ()):
            n = nodes[c]
            b = c if block is None else block
            if n["text_drawn"] or (n["box_drawn"] and not drawn_below(c)):
                key = flat[c]["key"] if c < len(flat) else ""
                rel = key[len(base):].lstrip(" /") if key.startswith(base) else key
                out.append({"k": c, "kind": _kind(n, nodes), "path": _strip(rel), "block": b,
                            "text": " ".join(n["text"].split())})
            walk(c, b)
    walk(card, None)
    return out


# ── layers ────────────────────────────────────────────────────────────────────

def composition(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> float:
    """Layer 1: node counts per kind."""
    if not a and not b:
        return 1.0
    ca: Dict[str, int] = {}
    cb: Dict[str, int] = {}
    for x in a:
        ca[x["kind"]] = ca.get(x["kind"], 0) + 1
    for x in b:
        cb[x["kind"]] = cb.get(x["kind"], 0) + 1
    shared = sum(min(v, cb.get(k, 0)) for k, v in ca.items())
    return shared / max(len(a), len(b))


def align(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Longest common subsequence of the two leaf lists: a pair needs the same kind (1 point), the same
    kind and path scores 2. Ties go to the earlier pair in a (fixed back-track order)."""
    na, nb = len(a), len(b)
    S = [[0] * (nb + 1) for _ in range(na + 1)]
    for i in range(na - 1, -1, -1):
        for j in range(nb - 1, -1, -1):
            best = max(S[i + 1][j], S[i][j + 1])
            if a[i]["kind"] == b[j]["kind"]:
                best = max(best, S[i + 1][j + 1] + (2 if a[i]["path"] == b[j]["path"] else 1))
            S[i][j] = best
    pairs, i, j = [], 0, 0
    while i < na and j < nb:
        if a[i]["kind"] == b[j]["kind"] and S[i][j] == S[i + 1][j + 1] + (2 if a[i]["path"] == b[j]["path"] else 1):
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif S[i][j] == S[i + 1][j]:
            i += 1
        else:
            j += 1
    return pairs


def adjacency(a: List[Dict[str, Any]], b: List[Dict[str, Any]],
              pairs: Optional[List[Tuple[int, int]]] = None) -> float:
    """Layer 2: aligned nodes whose path and both neighbours agree."""
    if not a and not b:
        return 1.0
    if pairs is None:
        pairs = align(a, b)
    to_b = dict(pairs)

    def side(i, j, d):
        ni, nj = i + d, j + d
        a_out, b_out = not (0 <= ni < len(a)), not (0 <= nj < len(b))
        if a_out or b_out:
            return 1.0 if a_out and b_out else 0.0
        return 1.0 if to_b.get(ni) == nj else 0.0

    total = sum((float(a[i]["path"] == b[j]["path"]) + side(i, j, -1) + side(i, j, 1)) / 3 for i, j in pairs)
    return total / max(len(a), len(b))


LAYERS: List[Tuple[str, Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], float]]] = [
    ("composition", composition),
    ("adjacency", adjacency),
]


def compare(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every layer's percentage and the overall one (whole percents, one decimal)."""
    layers = {name: round(100 * fn(a, b), 1) for name, fn in LAYERS}
    return {"layers": layers, "overall": round(sum(layers.values()) / len(layers), 1)}


CONTROLS = frozenset(CONTROL_KIND.values())


def structural(leaves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The leaves with every plain text read as one kind ("text"): a field whose value is a date and one
    whose value is a name are the same structure. Controls keep their kind."""
    return [x if x["kind"] in CONTROLS or not x["text"] else {**x, "kind": "text"} for x in leaves]


def structural_score(a: List[Dict[str, Any]], b: List[Dict[str, Any]], floor: float = 0.0) -> float:
    """compare()'s overall percentage on structural leaves (pass structural() lists). Adjacency never
    exceeds composition, so a pair whose composition alone is under floor is returned without aligning."""
    comp = 100 * composition(a, b)
    if comp < floor:
        return round(comp / len(LAYERS), 1)
    return compare(a, b)["overall"]


# ── a record set ──────────────────────────────────────────────────────────────

def structure(leaves: List[List[Dict[str, Any]]], ref: int) -> Dict[str, Any]:
    """The shared structure: reference card's leaves as slots, found-in counts, same/differs, labels, extras."""
    R = leaves[ref]
    n = len(leaves)
    found: List[List[Optional[int]]] = [[None] * n for _ in R]     # slot -> card -> leaf index
    for s in range(len(R)):
        found[s][ref] = s
    extras: Dict[Tuple[str, str], List[int]] = {}
    for c, L in enumerate(leaves):
        if c == ref:
            continue
        pairs = align(R, L)
        for s, j in pairs:
            found[s][c] = j
        taken = {j for _, j in pairs}
        for j, x in enumerate(L):
            if j not in taken:
                extras.setdefault((x["kind"], x["path"]), []).append(c)
    slots = []
    for s, x in enumerate(R):
        texts = [leaves[c][j]["text"] for c, j in enumerate(found[s]) if j is not None]
        k = len(texts)
        text = "same" if k >= 2 and len(set(texts)) == 1 else ("differs" if k >= 2 else "one card")
        slots.append({"slot": s, "kind": x["kind"], "path": x["path"], "found": k, "of": n, "text": text,
                      "block": x["block"], "role": "label" if text == "same" else ("value" if text == "differs" else "?"),
                      "label": None})
    for s, sl in enumerate(slots):                  # a value's label: nearest same-text slot before it, same block
        if sl["role"] != "value":
            continue
        for t in range(s - 1, -1, -1):
            if slots[t]["block"] != sl["block"]:
                break
            if slots[t]["role"] == "label":
                sl["label"] = t
                break
    blocks = []
    for sl in slots:
        if not blocks or blocks[-1]["block"] != sl["block"]:
            blocks.append({"block": sl["block"], "slots": []})
        blocks[-1]["slots"].append(sl["slot"])
    ext = [{"kind": k, "path": p, "cards": sorted(set(cs)), "count": len(cs)} for (k, p), cs in sorted(extras.items())]
    return {"ref": ref, "slots": slots, "blocks": [b["slots"] for b in blocks], "extras": ext,
            "labels": sum(sl["role"] == "label" for sl in slots), "values": sum(sl["role"] == "value" for sl in slots),
            "always": sum(sl["found"] == n for sl in slots)}


def match_set(members: List[int], nodes: List[Dict[str, Any]], flat: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pairwise card percentages, each card's mean against the others, the set's mean, and its structure."""
    kids: Dict[int, List[int]] = {}
    for x in nodes:
        if x["p"] >= 0:
            kids.setdefault(x["p"], []).append(x["i"])
    leaves = [card_leaves(m, nodes, flat, kids) for m in members]
    n = len(members)
    pairs: Dict[str, Dict[str, Any]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            pairs[f"{i}-{j}"] = compare(leaves[i], leaves[j])

    def get(i, j):
        return pairs[f"{min(i, j)}-{max(i, j)}"]

    names = [name for name, _ in LAYERS]
    cards = []
    for i in range(n):
        others = [get(i, j) for j in range(n) if j != i]
        mean = {nm: round(sum(o["layers"][nm] for o in others) / len(others), 1) for nm in names} if others else {}
        cards.append({"k": members[i], "nodes": len(leaves[i]),
                      "kinds": _counts(leaves[i]), "layers": mean,
                      "overall": round(sum(o["overall"] for o in others) / len(others), 1) if others else 100.0})
    allp = list(pairs.values())
    set_layers = {nm: round(sum(p["layers"][nm] for p in allp) / len(allp), 1) for nm in names} if allp else {}
    set_overall = round(sum(p["overall"] for p in allp) / len(allp), 1) if allp else 100.0
    ref = max(range(n), key=lambda i: (cards[i]["overall"], -i)) if n else 0
    return {"layer_names": names, "pairs": pairs, "cards": cards, "layers": set_layers, "overall": set_overall,
            "structure": structure(leaves, ref) if n else None}


def _counts(leaves: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in leaves:
        out[x["kind"]] = out.get(x["kind"], 0) + 1
    return dict(sorted(out.items()))
