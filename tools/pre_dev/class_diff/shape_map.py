"""
tools/pre_dev/class_diff/shape_map.py - PRE-DEV: a page's container shapes as a map, and the patterns they imply
==============================================================================================================
Builds on render.py --outline: every container (a node with children and a box in the view) has a
shape (its key without the [n] counters: types, ids, classes) and a loose shape (types only). This tool
groups the containers of one page by shape and reads the groups the way a person reads the outline view:

  record set   found from the boxes, not from types, ids or classes: 2+ containers of 2+ texts that line up
               (one column: same left edge and width; one row: same top and height; a grid links both
               ways; the size along the line is free, so a card with one more line still belongs) and
               whose contents match (card_match.structural_score >= MATCH_MIN). Two page bands of one
               width do not match inside and are no set. A set inside another set's members is a part of
               those records (top level only); a set of cards that leaves one card out loses to the part
               found in every card. The members may be of several strict shapes (a class that differs).
  layout       column (same left edge), row (same top), grid (both repeat), scattered; the gaps between
               neighbours and the spread of the sizes.
  lift         when the instances' ancestors one level or more up are still one per instance, disjoint and
               of one loose shape, the record is probably that ancestor (the tile around the part found).
  parts        repeated shapes inside the members: 1 per member = a part of the record (a block, a
               stepper), k per member = a nested list (3 steps per card), varying = optional or a list.
  loose merge  instances of 2+ strict shapes that share one loose shape, lie disjoint and line up: the
               same record with a class that differs (an active tile, a highlighted row).
  card match   card_match.py compares the members of each record set node by node, in layers (node kinds,
               then nodes against their neighbours), gives each pair and each card a match percentage,
               and reads the structure the cards share (slots, labels, values, extras).
  group        the HTML's "Group structure" tab draws one generalized card per match group
  structure    (card_match.group_structure): each slot at the median box of its copies, coloured by role,
               dashed when not in every card, extras in orange, with each card of the group beside it.
               Hovering a slot lights it in every card. Kinds and boxes only, no page text.

Nothing here knows a portal, a class name or a wording. Deterministic: the same input gives the same
bytes. The console gets counts, sizes and the last two steps of each shape (template class names) - no
page text. The HTML map draws text boxes as grey bars, never their text.

Usage:
    python tools/pre_dev/class_diff/shape_map.py FILE [--snapshot N] [--doc N] [--no-match]
                                                 [--match-min PCT] [--group-min PCT] [--open]
--match-min: how much two lined-up boxes must match inside to be one record set (default MATCH_MIN).
--group-min: how much two cards of a set must match to be linked into one match group (default
             card_match.GROUP_MIN). Links chain: 0-2 and 2-5 linked puts 0, 2 and 5 in one group.
FILE: a comp page, a key_probe capture or read (.json), or a record file (.jsonl, with --record N).
Writes output/shape_map_<file>.html.
"""

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import card_match  # noqa: E402
import render  # noqa: E402

TOL = 4                 # pixels: two edges this close are the same line (render boxes are whole pixels)
MIN_TEXTS = 2           # a record holds at least this many drawn texts
MATCH_MIN = 50.0        # PROVISIONAL: two lined-up boxes are the same record when their leaves match this much
                        # (card_match.structural_score, percent). Keeps two page bands of one width (a header
                        # strip and a footer strip) from being a record set.


def page_containers(raw: List[Dict[str, Any]], comp_view: str = "present") -> Dict[str, Any]:
    """The outline view's data for one doc: containers (render._outline_els), drawn texts, and for each
    container its nearest container ancestor."""
    nodes = render._norm(raw)
    render._decide(nodes, comp_view)
    view = [n for n in nodes if n["in_view"] and render._pos(n["rect"])]
    ox = min((n["rect"][0] for n in view), default=0)
    oy = min((n["rect"][1] for n in view), default=0)
    W = max((n["rect"][0] + n["rect"][2] for n in view), default=0) - ox
    H = max((n["rect"][1] + n["rect"][3] for n in view), default=0) - oy
    ols, shapes, loose = render._outline_els(raw, nodes, ox, oy)
    by_k = {e["k"]: e for e in ols}
    for e in ols:
        p = nodes[e["k"]]["p"]
        while p >= 0 and p not in by_k:
            p = nodes[p]["p"]
        e["up"] = p                                  # nearest outlined ancestor, -1 = none
    texts = []
    for n in nodes:
        if n["text_drawn"]:
            x, y, w, h = n["rect"]
            p = n["p"]
            while p >= 0 and p not in by_k:
                p = nodes[p]["p"]
            texts.append({"k": n["i"], "x": x - ox, "y": y - oy, "w": w, "h": h, "up": p})
    import cards
    flat, _ = cards.load_flat({"docs": [raw]})
    return {"W": W, "H": H, "ols": ols, "by_k": by_k, "shapes": shapes, "loose": loose, "texts": texts,
            "nodes": nodes, "flat": flat}


def _ancestors(by_k: Dict[int, Dict[str, Any]], k: int) -> List[int]:
    out = []
    p = by_k[k]["up"]
    while p >= 0:
        out.append(p)
        p = by_k[p]["up"]
    return out


def _tail(shape: str, steps: int = 2) -> str:
    return " / ".join(shape.split(" / ")[-steps:])


def _spread(vals: List[int]) -> int:
    return max(vals) - min(vals) if vals else 0


def layout(insts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """column / row / grid / scattered, with the gaps between neighbours along the line."""
    xs = sorted({e["x"] for e in insts})
    ys = sorted({e["y"] for e in insts})

    def lines(vals):
        out = []
        for v in vals:
            if not out or v - out[-1] > TOL:
                out.append(v)
        return out
    cols, rows = lines(xs), lines(ys)
    n = len(insts)
    if len(cols) == 1:
        kind, order = "column", sorted(insts, key=lambda e: e["y"])
        gaps = [b["y"] - (a["y"] + a["h"]) for a, b in zip(order, order[1:])]
    elif len(rows) == 1:
        kind, order = "row", sorted(insts, key=lambda e: e["x"])
        gaps = [b["x"] - (a["x"] + a["w"]) for a, b in zip(order, order[1:])]
    elif len(cols) * len(rows) >= n and len(cols) * len(rows) <= 2 * n and len(cols) > 1 and len(rows) > 1:
        kind, gaps = f"grid {len(rows)}x{len(cols)}", []
    else:
        kind, gaps = "scattered", []
    return {"kind": kind, "gap_min": min(gaps) if gaps else None, "gap_max": max(gaps) if gaps else None,
            "w_spread": _spread([e["w"] for e in insts]), "h_spread": _spread([e["h"] for e in insts])}


def infer(pc: Dict[str, Any], match: bool = True, match_min: Optional[float] = None,
          group_min: Optional[float] = None) -> Dict[str, Any]:
    """Record sets, their parts and nested lists, lifts and loose merges (module doc). match_min: the
    percentage two lined-up boxes must match inside (None = MATCH_MIN). group_min: the pair percentage
    that links two cards of a set into one match group (None = card_match.GROUP_MIN)."""
    match_min = MATCH_MIN if match_min is None else float(match_min)
    ols, by_k = pc["ols"], pc["by_k"]
    anc = {e["k"]: set(_ancestors(by_k, e["k"])) for e in ols}
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for e in ols:
        groups.setdefault(e["sh"], []).append(e)

    def disjoint(insts):
        ks = {e["k"] for e in insts}
        return all(not (anc[e["k"]] & ks) for e in insts)

    repeated = {sh: sorted(v, key=lambda e: (e["y"], e["x"], e["k"])) for sh, v in groups.items()
                if len(v) >= 2 and disjoint(v) and min(e["nt"] for e in v) >= MIN_TEXTS}

    sets = []
    found, weakest, boxes = _box_sets(pc, anc, match_min)
    for insts in _top_sets(found, anc):
        n_sh: Dict[int, int] = {}
        for e in insts:
            n_sh[e["sh"]] = n_sh.get(e["sh"], 0) + 1
        sh = min(n_sh, key=lambda s: (-n_sh[s], s))          # the members' most common strict shape
        members = {e["k"] for e in insts}
        # parts and nested lists: repeated shapes inside the members, counted per member
        inner = []
        for sh2, v2 in sorted(repeated.items()):
            if sh2 in n_sh:
                continue
            per = {m: 0 for m in members}
            outside = 0
            for e in v2:
                hit = members & anc[e["k"]]
                if hit:
                    per[next(iter(hit))] += 1
                else:
                    outside += 1
            if outside or not any(per.values()):
                continue
            counts = sorted(per.values())
            role = ("part" if counts[0] == counts[-1] == 1 else
                    f"list of {counts[0]}" if counts[0] == counts[-1] else "varies")
            inner.append({"shape": sh2, "tail": _tail(pc["shapes"][sh2]), "role": role,
                          "per_member": [per[e["k"]] for e in insts], "texts": v2[0]["nt"],
                          "members": [e["k"] for e in v2]})
        # nest the inner shapes: each under the inner shape whose instances hold it (the nearest one)
        inner_ks = {i["shape"]: {e["k"] for e in repeated[i["shape"]]} for i in inner}
        depth_of = {i["shape"]: repeated[i["shape"]][0]["dep"] for i in inner}
        for i in inner:
            holders = [o["shape"] for o in inner if o["shape"] != i["shape"]
                       and inner_ks[o["shape"]] & anc[repeated[i["shape"]][0]["k"]]]
            i["level"] = len(holders)
        inner.sort(key=lambda i: (min(e["y"] for e in repeated[i["shape"]]), depth_of[i["shape"]], i["shape"]))
        inner = _tree_order(inner, inner_ks, repeated, anc)
        lift = _lift(pc, insts, anc)
        sets.append({"shape": sh, "tail": _tail(pc["shapes"][sh]), "type": insts[0]["t"], "count": len(insts),
                     "strict_shapes": len(n_sh), "link_min": weakest[min(e["k"] for e in insts)],
                     "texts": [e["nt"] for e in insts], "members": [e["k"] for e in insts],
                     "layout": layout(insts), "lift": lift, "inner": inner,
                     "match": card_match.match_set([e["k"] for e in insts], pc["nodes"], pc["flat"], group_min)
                     if match and "nodes" in pc else None})
    return {"sets": sets, "merges": _loose_merges(pc, groups, anc), "repeated": len(repeated), "boxes": boxes}


def _lined_up(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two boxes of one column (same left edge and width, one below the other) or of one row (same top
    and height, side by side). The size along the line is free: a card with one more line is taller."""
    col = (abs(a["x"] - b["x"]) <= TOL and abs(a["w"] - b["w"]) <= TOL
           and (a["y"] + a["h"] <= b["y"] + TOL or b["y"] + b["h"] <= a["y"] + TOL))
    row = (abs(a["y"] - b["y"]) <= TOL and abs(a["h"] - b["h"]) <= TOL
           and (a["x"] + a["w"] <= b["x"] + TOL or b["x"] + b["w"] <= a["x"] + TOL))
    return col or row


def _box_sets(pc: Dict[str, Any], anc: Dict[int, set], match_min: float = MATCH_MIN):
    """Candidate record sets from the boxes alone, confirmed by what is inside them. Two containers of 2+
    texts are linked when they line up (_lined_up), neither holds the other, and their leaves match
    (card_match.structural_score >= match_min). A set is a group of linked containers, so a grid is one
    set: its rows link across, its columns link down. No type, id or class is read here.

    Returns (sets, weakest, boxes): weakest maps a set's lowest member index to the lowest score among the
    links that joined it; boxes counts the pairs (lined up, scored, linked) and holds the highest score
    that was turned down, which shows how near match_min is to a wrong call."""
    cand = sorted((e for e in pc["ols"] if e["nt"] >= MIN_TEXTS), key=lambda e: (e["y"], e["x"], e["k"]))
    kids: Dict[int, List[int]] = {}
    for x in pc["nodes"]:
        if x["p"] >= 0:
            kids.setdefault(x["p"], []).append(x["i"])
    leaves: Dict[int, List[Dict[str, Any]]] = {}

    def lv(k):
        if k not in leaves:
            leaves[k] = card_match.structural(card_match.card_leaves(k, pc["nodes"], pc["flat"], kids))
        return leaves[k]

    root = {e["k"]: e["k"] for e in cand}

    def find(k):
        while root[k] != k:
            root[k] = root[root[k]]
            k = root[k]
        return k

    low: Dict[int, float] = {}                                   # set root -> weakest link that joined it
    boxes = {"candidates": len(cand), "lined_up": 0, "scored": 0, "linked": 0, "best_rejected": None,
             "match_min": match_min}
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if not _lined_up(a, b) or a["k"] in anc[b["k"]] or b["k"] in anc[a["k"]]:
                continue
            boxes["lined_up"] += 1
            ra, rb = find(a["k"]), find(b["k"])
            if ra == rb:                                         # already one set through other boxes
                continue
            boxes["scored"] += 1
            score = card_match.structural_score(lv(a["k"]), lv(b["k"]), match_min)
            if score >= match_min:
                boxes["linked"] += 1
                root[max(ra, rb)] = min(ra, rb)
                low[min(ra, rb)] = min(score, low.get(ra, 100.0), low.get(rb, 100.0))
            elif boxes["best_rejected"] is None or score > boxes["best_rejected"]:
                boxes["best_rejected"] = score
    comps: Dict[int, List[Dict[str, Any]]] = {}
    for e in cand:
        comps.setdefault(find(e["k"]), []).append(e)
    out = []
    weakest: Dict[int, float] = {}
    for r, v in comps.items():
        ks = {e["k"] for e in v}
        v = [e for e in v if not (anc[e["k"]] & ks)]             # a wrapper and the box in it: the outer one
        if len(v) >= 2:
            out.append(v)
            weakest[min(e["k"] for e in v)] = low[r]
    return out, weakest, boxes


def _top_sets(found: List[List[Dict[str, Any]]], anc: Dict[int, set]) -> List[List[Dict[str, Any]]]:
    """The sets that are not ruled out by another one. A set wholly inside another set's members is a part
    of those records. Two sets that overlap otherwise (some members of one inside members of the other):
    the one with more members stays, the outer one on a tie (cards of which one is unlike the rest lose
    to the part that is alike in all of them; the lift then walks back up to the cards)."""
    keys = [{e["k"] for e in v} for v in found]
    out = []
    for i, v in enumerate(found):
        ruled_out = False
        for j, w in enumerate(found):
            if i == j:
                continue
            inside = sum(1 for e in v if anc[e["k"]] & keys[j])          # members of v inside members of w
            holds = sum(1 for e in w if anc[e["k"]] & keys[i])           # members of w inside members of v
            if inside == len(v) or (inside and len(w) >= len(v)) or (holds and holds < len(w) and len(w) > len(v)):
                ruled_out = True
                break
        if not ruled_out:
            out.append(v)
    return sorted(out, key=lambda v: (-sum(e["nt"] for e in v), min(e["k"] for e in v)))


def _tree_order(inner, inner_ks, repeated, anc):
    """Depth-first: each inner shape right after the inner shape that holds it."""
    def holder(i):
        cands = [o for o in inner if o["shape"] != i["shape"]
                 and inner_ks[o["shape"]] & anc[repeated[i["shape"]][0]["k"]]]
        return max(cands, key=lambda o: o["level"])["shape"] if cands else None
    kids: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for i in inner:
        kids.setdefault(holder(i), []).append(i)
    out: List[Dict[str, Any]] = []

    def walk(sh):
        for i in kids.get(sh, []):
            out.append(i)
            walk(i["shape"])
    walk(None)
    return out


def _lift(pc, insts, anc) -> Optional[Dict[str, Any]]:
    """Walk the instances' ancestors up together while they stay one per instance, disjoint, of one loose
    shape and hold no other instance's subtree. The last such level is the probable record."""
    by_k = pc["by_k"]
    cur = [e["k"] for e in insts]
    levels = 0
    best = None
    while True:
        up = [by_k[k]["up"] for k in cur]
        if any(u < 0 for u in up) or len(set(up)) != len(up):
            break
        if len({by_k[u]["lo"] for u in up}) != 1:
            break
        if any(set(up) & anc[u] for u in up):
            break
        cur, levels = up, levels + 1
        best = cur
    if not best:
        return None
    strict = len({by_k[k]["sh"] for k in best})
    e0 = by_k[best[0]]
    return {"levels": levels, "type": e0["t"], "members": best, "strict_shapes": strict,
            "loose_tail": _tail(pc["loose"][e0["lo"]]), "texts": [by_k[k]["nt"] for k in best]}


def _loose_merges(pc, groups, anc) -> List[Dict[str, Any]]:
    """One loose shape over 2+ strict shapes whose instances are disjoint, hold 2+ texts and line up."""
    by_lo: Dict[int, List[Dict[str, Any]]] = {}
    for e in pc["ols"]:
        if e["nt"] >= MIN_TEXTS:
            by_lo.setdefault(e["lo"], []).append(e)
    out = []
    for lo, v in sorted(by_lo.items()):
        strict = sorted({e["sh"] for e in v})
        if len(strict) < 2 or len(v) < 2:
            continue
        ks = {e["k"] for e in v}
        if any(anc[e["k"]] & ks for e in v):
            continue
        lay = layout(v)
        if lay["kind"] == "scattered":
            continue
        out.append({"loose": lo, "tail": _tail(pc["loose"][lo]), "count": len(v), "strict_shapes": len(strict),
                    "members": sorted(e["k"] for e in v), "layout": lay})
    return out


# ── output ────────────────────────────────────────────────────────────────────

def report(inf: Dict[str, Any]) -> List[str]:
    """Counts, sizes and shape tails only."""
    lines = [f"repeated shapes {inf['repeated']}, top-level record sets {len(inf['sets'])}, loose merges {len(inf['merges'])}"]
    bx = inf.get("boxes")
    if bx:
        rej = "none" if bx["best_rejected"] is None else f"{bx['best_rejected']}%"
        lines.append(f"boxes: {bx['candidates']} containers of {MIN_TEXTS}+ texts, {bx['lined_up']} pairs line up, "
                     f"{bx['linked']} of {bx['scored']} scored pairs match inside (>= {bx['match_min']:g}%), "
                     f"highest turned down {rej}")
    for i, s in enumerate(inf["sets"]):
        lay = s["layout"]
        gaps = "" if lay["gap_min"] is None else f", gaps {lay['gap_min']}..{lay['gap_max']} px"
        mixed = f" ({s['strict_shapes']} strict shapes)" if s.get("strict_shapes", 1) > 1 else ""
        lines.append(f"set {i}: {s['count']} x {s['type']}{mixed}  texts {sorted(set(s['texts']))}  {lay['kind']}{gaps}, "
                     f"size spread {lay['w_spread']}x{lay['h_spread']} px   ...{s['tail'][-70:]}")
        if s.get("link_min") is not None:
            lines.append(f"    found by boxes: {lay['kind']}, weakest link {s['link_min']}%")
        if s["lift"]:
            L = s["lift"]
            lines.append(f"    lift: {L['levels']} level(s) up to {L['type']} (texts {sorted(set(L['texts']))}, "
                         f"{L['strict_shapes']} strict shape(s))  ...{L['loose_tail'][-60:]}")
        for p in s["inner"]:
            pad = "  " * p["level"]
            lines.append(f"    {pad}{p['role']:12s} {p['texts']:3d} texts  per member {p['per_member']}  ...{p['tail'][-60:]}")
        lines.extend(match_report(s.get("match")))
    for m in inf["merges"]:
        lines.append(f"loose merge: {m['count']} containers of {m['strict_shapes']} strict shapes, {m['layout']['kind']}  ...{m['tail'][-60:]}")
    return lines


def match_report(m: Optional[Dict[str, Any]]) -> List[str]:
    """The card match of one set: percentages, per card, and the shared structure (kinds and counts only)."""
    if not m:
        return []
    lay = "  ".join(f"{k} {v}%" for k, v in m["layers"].items())
    out = [f"    card match: overall {m['overall']}%  ({lay})"]
    for i, c in enumerate(m["cards"]):
        cl = "  ".join(f"{k} {v}%" for k, v in c["layers"].items())
        out.append(f"      card {i}: {c['nodes']:3d} nodes  overall {c['overall']:5.1f}%  ({cl})  group {c['group'] + 1}")
    out.append(f"    match groups (pairs >= {m['group_min']:g}% linked, links chain): {len(m['groups'])}")
    for g, grp in enumerate(m["groups"]):
        if grp["link_min"] is None:
            how = "alone"
        else:
            how = f"weakest link {grp['link_min']}%, lowest pair inside {grp['pair_min']}%"
            if grp["pair_min"] < m["group_min"]:
                how += " (a chain: not every pair matches directly)"
        out.append(f"      group {g + 1}: cards {', '.join(map(str, grp['cards']))}  ({how})")
    st = m["structure"]
    if st:
        out.append(f"    structure (reference card {st['ref']}): {len(st['slots'])} slots, in every card {st['always']}, "
                   f"labels {st['labels']}, values {st['values']}, blocks {len(st['blocks'])}, extra kinds {len(st['extras'])}")
        for b, ss in enumerate(st["blocks"]):
            parts = []
            for s in ss:
                sl = st["slots"][s]
                lab = f"<-{sl['label']}" if sl["label"] is not None else ""
                parts.append(f"{s}:{sl['kind']}/{sl['role']}{lab}({sl['found']}/{sl['of']})")
            out.append(f"      block {b}: " + " ".join(parts))
        for e in st["extras"]:
            out.append(f"      extra: {e['kind']} x{e['count']} in cards {e['cards']}  ...{e['path'][-50:]}")
    return out


def card_sets(inf: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The record sets as render.py --cards draws them: each set's cards (member node indexes), its slots
    (kind, role, path, label slot, found in, one node per card or None) and its extras (kind, path, nodes).
    Sets without a card match are left out. Node indexes and paths only, no text."""
    out = []
    for i, s in enumerate(inf["sets"]):
        st = (s.get("match") or {}).get("structure")
        if not st:
            continue
        out.append({"set": i, "tail": s["tail"], "layout": s["layout"]["kind"], "cards": s["members"],
                    "ref": st["ref"],
                    "slots": [{"slot": sl["slot"], "kind": sl["kind"], "role": sl["role"], "path": sl["path"],
                               "label": sl["label"], "found": sl["found"], "nodes": sl["nodes"]}
                              for sl in st["slots"]],
                    "extras": [{"kind": e["kind"], "path": e["path"], "nodes": e["nodes"], "node_cards": e["node_cards"]}
                               for e in st["extras"]]})
    return out


def build_html(pc: Dict[str, Any], inf: Dict[str, Any], meta: Dict[str, Any]) -> str:
    data = {"W": pc["W"], "H": pc["H"], "page": meta.get("page", ""), "source": meta.get("source", ""),
            "texts": [[t["x"], t["y"], t["w"], t["h"]] for t in pc["texts"]],
            "boxes": {str(e["k"]): [e["x"], e["y"], e["w"], e["h"]] for e in pc["ols"]},
            "sets": inf["sets"], "merges": inf["merges"], "how": inf.get("boxes"), "report": report(inf)}
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("/*DATA*/null", blob)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A page's container shapes as a map, and the record sets they imply.")
    ap.add_argument("src")
    ap.add_argument("--snapshot", type=int)
    ap.add_argument("--record", type=int)
    ap.add_argument("--doc", default="auto")
    ap.add_argument("--comp-view", default="present", choices=["present", "all"])
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--no-match", action="store_true", help="leave out the card match (card_match.py)")
    ap.add_argument("--match-min", type=float, default=None, metavar="PCT",
                    help=f"lined-up boxes are one record set when they match inside this much (default {MATCH_MIN:g})")
    ap.add_argument("--group-min", type=float, default=None, metavar="PCT",
                    help=f"two cards of a set are in one match group when their pair matches this much "
                         f"(default {card_match.GROUP_MIN:g})")
    a = ap.parse_args(argv)
    src = Path(a.src)
    # A comp page with pages (snapshot_diff.py, PAGES) stays one doc here: the map draws one doc, and every node
    # of every page must be in it.
    docs, meta = render.load(src, snapshot=a.snapshot, record=a.record, split=False)
    k = render._pick_docs(docs, a.doc if a.doc in ("auto", "all") else int(a.doc))[0]
    pc = page_containers(docs[k], a.comp_view)
    inf = infer(pc, match=not a.no_match, match_min=a.match_min, group_min=a.group_min)
    for line in report(inf):
        print(line)
    out = render.PRE_DEV_OUT / f"shape_map_{src.stem}.html"
    render.check_out(out)
    render.write_html(out, build_html(pc, inf, meta))
    print(f"-> {out.relative_to(render.REPO)}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shape map</title>
<style>
  :root { --bg:#f4f4f6; --ink:#1d1d1f; --muted:#6e6e73; --line:#c9c9ce; --card:#fff; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#1c1c1e; --ink:#e5e5ea; --muted:#98989d; --line:#48484a; --card:#2c2c2e; } }
  :root[data-theme="dark"] { --bg:#1c1c1e; --ink:#e5e5ea; --muted:#98989d; --line:#48484a; --card:#2c2c2e; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:13px/1.45 system-ui, "Segoe UI", sans-serif; }
  header { padding:10px 16px; border-bottom:1px solid var(--line); }
  header .meta { color:var(--muted); overflow-wrap:anywhere; }
  .wrap { display:flex; gap:16px; padding:12px 16px; align-items:flex-start; flex-wrap:wrap; }
  .map { flex:0 0 auto; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.15); }
  .side { flex:1 1 360px; min-width:280px; }
  .set { background:var(--card); border:1px solid var(--line); border-left:6px solid; border-radius:6px; padding:8px 10px; margin-bottom:8px; cursor:pointer; }
  .set.on { box-shadow:0 0 0 2px #e8590c; }
  .set .h { font-weight:600; }
  .set .s, .how .s { color:var(--muted); font:12px/1.4 ui-monospace, Consolas, monospace; overflow-wrap:anywhere; }
  .part { margin:4px 0 0 10px; font:12px/1.4 ui-monospace, Consolas, monospace; }
  pre { background:var(--card); border:1px solid var(--line); padding:8px; overflow:auto; font-size:12px; }
  label { display:inline-flex; gap:6px; align-items:center; margin-right:12px; cursor:pointer; }
  .cm { margin-top:8px; border-top:1px dashed var(--line); padding-top:6px; cursor:default; }
  .cm .big { font-weight:600; margin-top:4px; }
  .cm table { border-collapse:collapse; margin:6px 0; font:11px/1.3 ui-monospace, Consolas, monospace; }
  .cm td, .cm th { border:1px solid var(--line); padding:2px 5px; text-align:right; }
  .cm th { color:var(--muted); font-weight:500; }
  .cm td.self { color:var(--muted); text-align:center; }
  .cm td.link { font-weight:700; }
  .cm .grp { cursor:default; }
  .cm .slots { font:11px/1.5 ui-monospace, Consolas, monospace; }
  .cm .blk { margin:3px 0; }
  .chip { display:inline-block; border:1px solid var(--line); border-radius:4px; padding:0 4px; margin:1px 2px 1px 0; }
  .chip.label { color:var(--muted); }
  .chip.value { border-color:#2f9e44; color:#2f9e44; font-weight:600; }
  .chip.part { border-style:dashed; }
  .how { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:8px 10px; margin-bottom:8px; }
  .how b { font-weight:600; }
  .found { color:var(--muted); font:12px/1.4 ui-monospace, Consolas, monospace; margin-top:2px; }
  .bar { display:inline-block; height:8px; background:#2f9e44; vertical-align:middle; border-radius:2px; margin-left:4px; }
  .tabs { display:flex; gap:4px; margin-top:8px; }
  .tabs button { font:inherit; padding:4px 12px; border:1px solid var(--line); border-radius:6px 6px 0 0; background:var(--bg); color:var(--ink); cursor:pointer; }
  .tabs button.on { background:var(--card); font-weight:600; border-bottom-color:var(--card); }
  .tab[hidden] { display:none !important; }
  .gs { padding:12px 16px; }
  .gs .gset { background:var(--card); border:1px solid var(--line); border-left:6px solid; border-radius:6px; padding:10px 12px; margin-bottom:12px; }
  .gs .ggrp { border-top:1px dashed var(--line); margin-top:10px; padding-top:8px; }
  .gs .row { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-start; margin-top:6px; }
  .gs .gen { background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.15); max-width:100%; height:auto; }
  .gs .thumbs { display:flex; gap:8px; flex-wrap:wrap; align-items:flex-start; flex:1 1 240px; }
  .gs .thumb { text-align:center; font:11px/1.3 ui-monospace, Consolas, monospace; color:var(--muted); }
  .gs .thumb svg { background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.15); display:block; }
  .gs .info { font:12px/1.4 ui-monospace, Consolas, monospace; color:var(--muted); min-height:1.4em; margin-top:4px; overflow-wrap:anywhere; }
  .gs .legend { font-size:12px; color:var(--muted); margin-bottom:10px; }
  .sw { display:inline-block; width:12px; height:10px; border:1.5px solid; vertical-align:middle; margin:0 3px 0 8px; }
</style>
</head>
<body>
<header><b>Shape map</b> <span class="meta" id="meta"></span><br>
  <label><input type="checkbox" id="showparts" checked> parts and nested lists</label>
  <label><input type="checkbox" id="showlift" checked> lifted records</label>
  <label><input type="checkbox" id="showpct" checked> card match %</label>
  <span class="meta">click a set to show only it · click again to show all</span>
  <div class="tabs"><button id="tb-map" class="on">Map</button><button id="tb-groups">Group structure</button></div>
</header>
<div class="tab" id="tab-map">
<div class="wrap">
  <svg class="map" id="map"></svg>
  <div class="side"><div class="how" id="how"></div><div id="sets"></div><pre id="rep"></pre></div>
</div>
</div>
<div class="tab gs" id="tab-groups" hidden>
  <div class="legend">One generalized card per match group: each slot sits at the median box of its copies in the group's cards.
    <span class="sw" style="border-color:#2f9e44;background:rgba(47,158,68,.25)"></span>value (text differs)
    <span class="sw" style="border-color:#868e96;background:rgba(134,142,150,.25)"></span>label (same text)
    <span class="sw" style="border-color:#1971c2;background:rgba(25,113,194,.15)"></span>one card only
    <span class="sw" style="border-color:#868e96;border-style:dashed"></span>not in every card
    <span class="sw" style="border-color:#e8590c;border-style:dashed"></span>extra (no slot)
    · hover a slot to light it in every card</div>
  <div id="groups"></div>
</div>
<script>
const D = /*DATA*/null;
const NS = 'http://www.w3.org/2000/svg';
const PAL = ['#1971c2','#e8590c','#2f9e44','#ae3ec9','#f08c00','#0c8599','#c2255c','#5c940d','#6741d9','#d9480f'];
const scale = Math.min(1, 640 / Math.max(1, D.W));
const svg = document.getElementById('map');
svg.setAttribute('width', Math.round(D.W * scale)); svg.setAttribute('height', Math.round(D.H * scale));
svg.setAttribute('viewBox', '0 0 ' + D.W + ' ' + D.H);
function rect(b, attrs) {
  const r = document.createElementNS(NS, 'rect');
  r.setAttribute('x', b[0]); r.setAttribute('y', b[1]); r.setAttribute('width', Math.max(1, b[2])); r.setAttribute('height', Math.max(1, b[3]));
  for (const [k, v] of Object.entries(attrs)) r.setAttribute(k, v);
  svg.appendChild(r); return r;
}
for (const t of D.texts) rect(t, {fill:'#c9c9ce'});
let sel = null;
const layers = [];
D.sets.forEach((s, i) => {
  const col = PAL[i % PAL.length], g = [];
  for (const k of s.members) g.push({r: rect(D.boxes[k], {fill:col, 'fill-opacity':.10, stroke:col, 'stroke-width':4 / scale}), kind:'set'});
  if (s.lift) for (const k of s.lift.members) g.push({r: rect(D.boxes[k], {fill:'none', stroke:col, 'stroke-width':2 / scale, 'stroke-dasharray':(10 / scale) + ' ' + (6 / scale)}), kind:'lift'});
  for (const q of s.inner) for (const k of q.members)
    g.push({r: rect(D.boxes[k], {fill:'none', stroke:col, 'stroke-opacity':.7, 'stroke-width':1 / scale}), kind:'part'});
  if (s.match) s.members.forEach((k, ci) => {
    const b = D.boxes[k], c = s.match.cards[ci];
    const many = s.match.groups.length > 1;
    const tip = document.createElementNS(NS, 'title');           // the full label: hover the card
    tip.textContent = 'card ' + ci + (many ? ' · group ' + (c.group + 1) : '') + ' · ' + c.overall + '%';
    g[ci].r.appendChild(tip);
    // the label is fitted to the card (screen px: 18 down to 8), cut to "c0" when it does not fit, left
    // out when that does not fit either, and clipped to the card's box so it never runs over a neighbour
    const bw = b[2] * scale, bh = b[3] * scale;
    const fit = s_ => Math.min(18, (bw - 6) / (s_.length * 0.62), bh - 2);
    let txt = 'c' + ci + (many ? ' · g' + (c.group + 1) : '') + ' · ' + Math.round(c.overall) + '%', f = fit(txt);
    if (f < 8) { txt = 'c' + ci; f = fit(txt); }
    if (f < 8) return;
    const v = document.createElementNS(NS, 'svg');               // a nested svg clips its content to its box
    v.setAttribute('x', b[0]); v.setAttribute('y', b[1]); v.setAttribute('width', Math.max(1, b[2])); v.setAttribute('height', Math.max(1, b[3]));
    v.style.pointerEvents = 'none';
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', 3 / scale); t.setAttribute('y', (f * 0.85 + 1) / scale);
    t.setAttribute('font-size', f / scale); t.setAttribute('font-weight', 700); t.setAttribute('fill', col);
    t.textContent = txt;
    v.appendChild(t); svg.appendChild(v); g.push({r: v, kind: 'pct'});
  });
  layers.push({i, g, col});
});
const hi = [];
function highlight(ks, col) {
  for (const r of hi) r.remove();
  hi.length = 0;
  for (const k of ks) hi.push(rect(D.boxes[k], {fill:col, 'fill-opacity':.25, stroke:col, 'stroke-width':6 / scale}));
}
function apply() {
  const showLift = document.getElementById('showlift').checked;
  const showParts = document.getElementById('showparts').checked;
  for (const L of layers) for (const x of L.g) {
    const showPct = document.getElementById('showpct').checked;
    const vis = (sel === null || sel === L.i) && (x.kind !== 'lift' || showLift) && (x.kind !== 'part' || showParts)
      && (x.kind !== 'pct' || showPct);
    x.r.style.display = vis ? '' : 'none';
  }
  document.querySelectorAll('.set').forEach((el, i) => el.classList.toggle('on', sel === i));
  document.querySelectorAll('.part').forEach(el => el.style.display = document.getElementById('showparts').checked ? '' : 'none');
}
function el_(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function pctCell(td, v) {
  td.textContent = v.toFixed(1) + '%';
  td.style.background = 'rgba(47,158,68,' + (Math.max(0, v - 50) / 50 * .45).toFixed(2) + ')';
}
function matchPanel(s, col) {
  // the card match of one set: layers, a card x card matrix (hover = highlight on the map), the structure
  const m = s.match, n = m.cards.length;
  const el = el_('div', 'cm');
  el.appendChild(el_('div', 'big', 'Card match · overall ' + m.overall + '%'));
  m.layer_names.forEach((nm, li) => {
    const d = el_('div', 's', 'layer ' + (li + 1) + ' · ' + nm + ': ' + m.layers[nm] + '%');
    const b = el_('span', 'bar'); b.style.width = Math.round(m.layers[nm]) + 'px';
    d.appendChild(b); el.appendChild(d);
  });
  const tb = el_('table');
  let tr = el_('tr'); tr.appendChild(el_('th'));
  for (let j = 0; j < n; j++) tr.appendChild(el_('th', '', 'c' + j));
  tr.appendChild(el_('th', '', 'mean')); tr.appendChild(el_('th', '', 'nodes')); tr.appendChild(el_('th', '', 'group'));
  tb.appendChild(tr);
  for (let i = 0; i < n; i++) {
    tr = el_('tr');
    const h = el_('th', '', 'c' + i);
    h.addEventListener('mouseenter', () => highlight([s.members[i]], col));
    tr.appendChild(h);
    for (let j = 0; j < n; j++) {
      const td = el_('td');
      if (i === j) { td.className = 'self'; td.textContent = '·'; }
      else {
        const p = m.pairs[Math.min(i, j) + '-' + Math.max(i, j)];
        pctCell(td, p.overall);
        if (p.overall >= m.group_min) td.className = 'link';
        td.title = 'card ' + i + ' vs card ' + j + '\n' + m.layer_names.map(nm => nm + ' ' + p.layers[nm] + '%').join('\n');
        td.addEventListener('mouseenter', () => highlight([s.members[i], s.members[j]], col));
      }
      tr.appendChild(td);
    }
    const c = m.cards[i];
    const mt = el_('td'); pctCell(mt, c.overall);
    mt.title = m.layer_names.map(nm => nm + ' ' + c.layers[nm] + '%').join('\n');
    tr.appendChild(mt);
    const nt = el_('td', '', String(c.nodes));
    nt.title = Object.entries(c.kinds).map(([k, v]) => k + ' ' + v).join('\n');
    tr.appendChild(nt);
    const gt = el_('td', '', 'g' + (c.group + 1));
    gt.addEventListener('mouseenter', () => highlight(m.groups[c.group].cards.map(x => s.members[x]), col));
    tr.appendChild(gt);
    tb.appendChild(tr);
  }
  tb.addEventListener('mouseleave', () => highlight([], col));
  el.appendChild(tb);
  // match groups: cards linked by a pair >= group_min, links chain (hover = highlight the group on the map)
  el.appendChild(el_('div', 'big', 'Match groups (pairs ≥ ' + m.group_min + '% linked): ' + m.groups.length));
  const gl = el_('div', 'slots');
  m.groups.forEach((g, gi) => {
    const how = g.link_min === null ? 'alone'
      : 'weakest link ' + g.link_min + '% · lowest pair inside ' + g.pair_min + '%' + (g.pair_min < m.group_min ? ' (a chain)' : '');
    const d = el_('div', 'blk grp', 'g' + (gi + 1) + ': cards ' + g.cards.join(', ') + ' · ' + how);
    d.addEventListener('mouseenter', () => highlight(g.cards.map(x => s.members[x]), col));
    d.addEventListener('mouseleave', () => highlight([], col));
    gl.appendChild(d);
  });
  el.appendChild(gl);
  el.appendChild(el_('div', 's', 'bold cell = a link · a chain = cards joined through another card, not every pair matches directly'));
  const st = m.structure;
  if (st) {
    el.appendChild(el_('div', 'big', 'Structure (reference card ' + st.ref + '): ' + st.slots.length + ' slots · in every card '
      + st.always + ' · labels ' + st.labels + ' · values ' + st.values));
    const sl = el_('div', 'slots');
    st.blocks.forEach((ss, b) => {
      const d = el_('div', 'blk', 'block ' + b + ' ');
      for (const k of ss) {
        const x = st.slots[k];
        const c = el_('span', 'chip ' + x.role + (x.found < x.of ? ' part' : ''),
          k + ' ' + x.kind + (x.label !== null ? ' ←' + x.label : '') + (x.found < x.of ? ' ' + x.found + '/' + x.of : ''));
        c.title = 'slot ' + k + ': ' + x.kind + ', ' + x.role + ' (text ' + x.text + '), in ' + x.found + ' of ' + x.of
          + ' cards' + (x.label !== null ? ', label = slot ' + x.label : '') + '\n…' + x.path;
        d.appendChild(c);
      }
      sl.appendChild(d);
    });
    for (const e of st.extras)
      sl.appendChild(el_('div', 'blk', 'extra: ' + e.kind + ' ×' + e.count + ' in cards ' + e.cards.join(', ') + ' · …' + e.path.slice(-60)));
    el.appendChild(sl);
    el.appendChild(el_('div', 's', 'label = same text in every card it is in · value = text differs · ←n = its label slot · dashed = not in every card'));
  }
  return el;
}
const box = document.getElementById('sets');
D.sets.forEach((s, i) => {
  const el = document.createElement('div');
  el.className = 'set'; el.style.borderLeftColor = PAL[i % PAL.length];
  const lay = s.layout;
  const gaps = lay.gap_min === null ? '' : ', gaps ' + lay.gap_min + '..' + lay.gap_max + ' px';
  el.innerHTML = '<div class="h"></div><div class="s"></div>';
  el.querySelector('.h').textContent = 'set ' + i + ': ' + s.count + ' × ' + s.type + (s.strict_shapes > 1 ? ' (' + s.strict_shapes + ' strict shapes)' : '') + ' · ' + lay.kind + gaps;
  el.querySelector('.s').textContent = 'texts ' + [...new Set(s.texts)].sort((a, b) => a - b).join(', ') + ' · size spread ' + lay.w_spread + '×' + lay.h_spread + ' px\n…' + s.tail;
  if (s.link_min !== null && s.link_min !== undefined)
    el.appendChild(el_('div', 'found', 'found by boxes: ' + lay.kind + ' · weakest link ' + s.link_min + '%'));
  if (s.lift) {
    const p = document.createElement('div'); p.className = 'part';
    p.textContent = 'lift ' + s.lift.levels + ' up → ' + s.lift.type + ' (texts ' + [...new Set(s.lift.texts)].join(', ') + ', ' + s.lift.strict_shapes + ' strict shape(s))';
    el.appendChild(p);
  }
  for (const q of s.inner) {
    const p = document.createElement('div'); p.className = 'part';
    p.style.marginLeft = (10 + 14 * q.level) + 'px';
    p.textContent = q.role + ' · ' + q.texts + ' texts · per member [' + q.per_member.join(', ') + '] · …' + q.tail;
    el.appendChild(p);
  }
  if (s.match) el.appendChild(matchPanel(s, PAL[i % PAL.length]));
  el.addEventListener('click', ev => { if (ev.target.closest('.cm')) return; sel = sel === i ? null : i; apply(); });
  box.appendChild(el);
});
const how = document.getElementById('how');
if (D.how) {
  const b = D.how;
  how.appendChild(el_('b', '', 'Record sets from boxes'));
  how.appendChild(el_('div', 's', 'A set = boxes that line up (a column: same left edge and width; a row: same top and height; a grid: both) and match inside at least ' + b.match_min + '%. Types, ids and classes are not read.'));
  how.appendChild(el_('div', 'found', b.candidates + ' containers of 2+ texts · ' + b.lined_up + ' pairs line up · ' + b.linked + ' of ' + b.scored
    + ' scored pairs match inside · highest turned down ' + (b.best_rejected === null ? 'none' : b.best_rejected + '%')));
} else how.style.display = 'none';
document.getElementById('showparts').addEventListener('change', apply);
document.getElementById('showlift').addEventListener('change', apply);
document.getElementById('showpct').addEventListener('change', apply);
document.getElementById('rep').textContent = D.report.join('\n');
document.getElementById('meta').textContent = [D.page, D.source].filter(Boolean).join(' · ');
apply();

// ── Group structure tab: one generalized card per match group ──
const ROLE = {value: ['#2f9e44', .25], label: ['#868e96', .25], '?': ['#1971c2', .15]};
function sv(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function canvas(w, h, maxW) {
  // an svg in card pixels, shown at most maxW screen px wide; returns [svg, px per card px]
  const sc = Math.min(1, maxW / Math.max(1, w));
  const s = sv('svg', {viewBox: '0 0 ' + Math.max(1, w) + ' ' + Math.max(1, h),
    width: Math.round(Math.max(1, w) * sc), height: Math.round(Math.max(1, h) * sc)});
  return [s, sc];
}
function bounds(gs) {
  // the card's size, grown to hold any slot that sits outside it
  let w = gs.W, h = gs.H;
  for (const x of gs.slots.concat(gs.extras)) if (x.box) { w = Math.max(w, x.box[0] + x.box[2]); h = Math.max(h, x.box[1] + x.box[3]); }
  return [w, h];
}
function groupPanel(s, si, gs, gi, col) {
  const el = el_('div', 'ggrp');
  const n = gs.cards.length;
  el.appendChild(el_('div', 'big', 'group ' + (gi + 1) + ' · ' + n + ' card' + (n > 1 ? 's' : '') + ' (' + gs.cards.map(c => 'c' + c).join(', ') + ')'
    + ' · reference c' + gs.ref + ' · ' + gs.slots.length + ' slots, in every card ' + gs.always + ' · labels ' + gs.labels
    + ' · values ' + gs.values + ' · extras ' + gs.extras.reduce((a, e) => a + e.count, 0)));
  const row = el_('div', 'row');
  const [bw, bh] = bounds(gs);
  const [gen, sc] = canvas(bw, bh, 460);
  gen.classList.add('gen');
  sv('rect', {x: 0, y: 0, width: gs.W, height: gs.H, fill: 'none', stroke: col, 'stroke-width': 3 / sc}, gen);
  const info = el_('div', 'info', n > 1 ? 'hover a slot' : 'one card: nothing to generalize, its own layout is shown');
  const thumbs = el_('div', 'thumbs');
  const lit = [];                                   // per card: slot -> its rects
  const general = {};                               // slot -> generalized rect
  function light(slot) {
    for (const m of lit) for (const [k, rs] of Object.entries(m)) for (const r of rs) {
      r.setAttribute('stroke-width', (+k === slot ? 4 : 1.5) / r._sc);
      r.setAttribute('stroke', +k === slot ? '#e8590c' : r._col);
    }
    for (const [k, r] of Object.entries(general)) r.setAttribute('stroke', +k === slot ? '#e8590c' : r._col);
  }
  function draw(svg, b, colr, op, dashed, scl, label) {
    const r = sv('rect', {x: b[0], y: b[1], width: Math.max(1, b[2]), height: Math.max(1, b[3]), fill: colr, 'fill-opacity': op,
      stroke: colr, 'stroke-width': 1.5 / scl}, svg);
    if (dashed) r.setAttribute('stroke-dasharray', (5 / scl) + ' ' + (3 / scl));
    r._col = colr; r._sc = scl;
    if (label) {
      const f = Math.min(12, (b[2] * scl - 4) / (label.length * 0.62), b[3] * scl - 2);
      if (f >= 7) {
        const t = sv('text', {x: b[0] + 2 / scl, y: b[1] + (f * 0.85 + 1) / scl, 'font-size': f / scl, fill: colr, 'pointer-events': 'none',
          'font-family': 'ui-monospace, Consolas, monospace'}, svg);
        t.textContent = label;
      }
    }
    return r;
  }
  for (const x of gs.slots) {
    if (!x.box) continue;
    const [c, op] = ROLE[x.role] || ROLE['?'];
    const part = x.found < x.of;
    const r = draw(gen, x.box, c, part ? op * .5 : op, part, sc, x.slot + ' ' + x.kind);
    general[x.slot] = r;
    const tip = 'slot ' + x.slot + ': ' + x.kind + ', ' + x.role + ', in ' + x.found + ' of ' + x.of + ' cards'
      + (x.label !== null ? ', label = slot ' + x.label : '') + ' · box ' + x.box.join(',') + ' · jitter ' + x.jitter + ' px';
    sv('title', {}, r).textContent = tip + '\n…' + x.path;
    r.addEventListener('mouseenter', () => { light(x.slot); info.textContent = tip; });
    r.addEventListener('mouseleave', () => { light(null); });
  }
  for (const e of gs.extras) {
    if (!e.box) continue;
    const r = draw(gen, e.box, '#e8590c', .12, true, sc, '+' + e.kind);
    const tip = 'extra: ' + e.kind + ' ×' + e.count + ' in cards ' + e.cards.map(c => 'c' + c).join(', ') + ' · jitter ' + e.jitter + ' px';
    sv('title', {}, r).textContent = tip + '\n…' + e.path;
    r.addEventListener('mouseenter', () => { info.textContent = tip; });
  }
  row.appendChild(gen);
  if (n > 1) for (const o of gs.own) {
    const t = el_('div', 'thumb');
    let w = o.w, h = o.h;
    for (const b of o.leaves) { w = Math.max(w, b[0] + b[2]); h = Math.max(h, b[1] + b[3]); }
    const [ts, tsc] = canvas(w, h, 150);
    sv('rect', {x: 0, y: 0, width: o.w, height: o.h, fill: 'none', stroke: col, 'stroke-width': 2 / tsc}, ts);
    const m = {};
    for (const b of o.leaves) {
      const slot = b[4], x = slot >= 0 ? gs.slots[slot] : null;
      const [c, op] = x ? (ROLE[x.role] || ROLE['?']) : ['#e8590c', .12];
      const r = draw(ts, b, c, op, !x, tsc, '');
      if (x) (m[slot] = m[slot] || []).push(r);
    }
    lit.push(m);
    t.appendChild(ts);
    t.appendChild(el_('div', '', 'c' + o.card + (o.card === gs.ref ? ' (ref)' : '')));
    thumbs.appendChild(t);
  }
  row.appendChild(thumbs);
  el.appendChild(row);
  el.appendChild(info);
  return el;
}
const gbox = document.getElementById('groups');
let anyGroup = false;
D.sets.forEach((s, i) => {
  const m = s.match;
  if (!m || !m.group_structures) return;
  anyGroup = true;
  const col = PAL[i % PAL.length];
  const el = el_('div', 'gset');
  el.style.borderLeftColor = col;
  el.appendChild(el_('div', 'h', 'set ' + i + ': ' + s.count + ' × ' + s.type + ' · ' + s.layout.kind + ' · '
    + m.groups.length + ' match group' + (m.groups.length > 1 ? 's' : '') + ' (pairs ≥ ' + m.group_min + '% linked)'));
  m.group_structures.forEach((gs, gi) => el.appendChild(groupPanel(s, i, gs, gi, col)));
  gbox.appendChild(el);
});
if (!anyGroup) gbox.appendChild(el_('div', 'info', 'No record set with a card match on this page.'));
function showTab(name) {
  for (const t of ['map', 'groups']) {
    document.getElementById('tab-' + t).hidden = t !== name;
    document.getElementById('tb-' + t).classList.toggle('on', t === name);
  }
  try { localStorage.setItem('shape_map_tab', name); } catch (e) {}
}
document.getElementById('tb-map').addEventListener('click', () => showTab('map'));
document.getElementById('tb-groups').addEventListener('click', () => showTab('groups'));
try { if (localStorage.getItem('shape_map_tab') === 'groups') showTab('groups'); } catch (e) {}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
