"""
tools/pre_dev/class_diff/cards.py - PRE-DEV: the repeating parts of a page (cards, list rows, table rows)
=========================================================================================================
Plan: docs/cards_rows/plan.md. Pre-dev only; nothing in core/ changes (align.py, keys.py and tables.py
are shared with production and are only imported here).

What it does, in order (plan sections 3-6):
  1. find_groups(): sibling subtrees built alike (same root type, relative shapes of their parts
     similar: Jaccard >= THETA) form a group; every Table element's body rows form a `table` group.
  2. slots(): each member's text nodes get a slot - the table's column name, or for a generic group
     the template member's relative key (members aligned with align.align()); an extra text gets its
     own slot (an optional field).
  3. roles(): per slot on one page: label (same text in every member), distinct (a different text in
     every member), mixed.
  4. classify(): across the captures of ONE page link: a `cards` group has a name slot whose texts
     recur on other captures (recurrence >= RHO); otherwise `list`; one capture only: `unknown`.
  5. compare_groups(): cards paired by name (or by content when no name slot), rows never paired.

Fix the kind, not the case: shapes, boxes, texts and counts only. No portal, class name or wording.

THETA and RHO are MEASURED (plan section 8), not picked: until they are written in plan section 9
the functions that need them refuse to run. `python cards.py --histogram FILE...` prints the
sibling-similarity histogram (counts only) that THETA is read from.

    python tools/pre_dev/class_diff/cards.py [FILE]                 # groups of a page, shape-only report
    python tools/pre_dev/class_diff/cards.py --histogram FILE...    # jaccard histogram over sibling pairs

Captures hold client data: the console prints counts and shapes, never page text.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDIS_DATA_DIR", str(OUT_DIR))

from core.sdis import align as _align                 # noqa: E402
from core.sdis import tables as _tables               # noqa: E402
from core.sdis.keys import flatten                    # noqa: E402

# The node types whose look or text a member's shape is made of. Text is in the set on purpose:
# a tile with no button or link still says something (decision recorded in plan section 9).
SIG_TYPES = frozenset({"Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "Image", "RadioButton", "Text"})

THETA: Optional[float] = 0.4167   # plan 9.3: middle of the widest empty gap (0.3333 to 0.5000), 33 distinct values, 15 files
RHO: Optional[float] = None       # plan 9.4: NOT set - the two widest gaps in r tie (0.00-0.50, 0.50-1.00)

TOL = 3                           # pixels: boxes this close are the same edge (lattice check)


# ── loading ───────────────────────────────────────────────────────────────────

def load_flat(rec: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[List[List[Dict[str, Any]]]]]:
    """(flat list, raw docs or None). A comp page keeps its flat list as written; a read is flattened."""
    if rec.get("comp_page"):
        return rec["nodes"], None
    return flatten(rec), rec.get("docs") or []


def _bases(docs: List[List[Dict[str, Any]]]) -> List[int]:
    """Flat index of each doc's first node (flatten() appends the docs in order)."""
    out, total = [], 0
    for d in docs:
        out.append(total)
        total += len(d)
    return out


def _children(fl: List[Dict[str, Any]]) -> List[List[int]]:
    kids: List[List[int]] = [[] for _ in fl]
    for i, e in enumerate(fl):
        if e["parent"] >= 0:
            kids[e["parent"]].append(i)
    return kids


_DOC = re.compile(r"^D\d+ / ")
ROOT_SHAPE = "(document)"


def _sibling_sets(fl: List[Dict[str, Any]]) -> List[Tuple[int, List[int]]]:
    """Every set of siblings: (parent index, children) for each node with 2+ children, deepest parents
    first, then each document's TOP-LEVEL nodes (parent -1) as one set per document (parent -1). The top
    level matters: a page's tiles are often direct children of the document."""
    kids = _children(fl)
    in_table = [False] * len(fl)
    for i, e in enumerate(fl):            # parents come before children: one pass marks every Table subtree
        in_table[i] = e["type"] == "Table" or (e["parent"] >= 0 and in_table[e["parent"]])
    # plan 3.1: inside a Table only tables.extract() finds rows, never the generic rule
    out = [(p, kids[p]) for p in range(len(fl) - 1, -1, -1) if len(kids[p]) >= 2 and not in_table[p]]
    tops: Dict[str, List[int]] = defaultdict(list)
    for i, e in enumerate(fl):
        if e["parent"] < 0 and not in_table[i]:
            m = _DOC.match(e["key"])
            tops[m.group(0) if m else ""].append(i)
    out += [(-1, cs) for _, cs in sorted(tops.items()) if len(cs) >= 2]
    return out


# ── 1. find groups ────────────────────────────────────────────────────────────

def _root(fl, kids, i: int) -> int:
    """Plan 3.3: walk down while the node has exactly one child and no text of its own."""
    while len(kids[i]) == 1 and not fl[i]["text"]:
        i = kids[i][0]
    return i


def _text_under(fl, kids, i: int) -> bool:
    stack = [i]
    while stack:
        j = stack.pop()
        if fl[j]["text"]:
            return True
        stack.extend(kids[j])
    return False


def _sig(fl, kids, r: int) -> frozenset:
    """Plan 3.2, as decided in plan 9: the relative shapes of the SIG_TYPES nodes under r, as paths of
    ELEMENT TYPES only (Hyperlink / Group / Text). Class names are left out: one class on an outer
    element (a disabled tile's "disableClick") would change every path under it and make one card of a
    group look unlike the rest (measured on the GST dashboard, 2026-10-11: jaccard 0.00)."""
    out: Set[str] = set()
    stack = [(c, fl[c]["type"]) for c in kids[r]]
    while stack:
        j, path = stack.pop()
        if fl[j]["type"] in SIG_TYPES:
            out.add(path)
        stack.extend((c, path + " / " + fl[c]["type"]) for c in kids[j])
    return frozenset(out)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def sibling_pairs(fl: List[Dict[str, Any]]) -> List[Tuple[int, int, int, float]]:
    """Every pair of siblings (same parent) that are both member candidates: (parent, a, b, jaccard).
    The input of the plan-section-8 histogram. Candidates = roots with text and a non-empty signature."""
    kids = _children(fl)
    info: Dict[int, Tuple[str, frozenset]] = {}
    out = []
    for p, cs in _sibling_sets(fl):
        cand = []
        for c in cs:
            r = _root(fl, kids, c)
            if r not in info:
                info[r] = (fl[r]["type"], _sig(fl, kids, r))
            t, sig = info[r]
            if sig and _text_under(fl, kids, r):
                cand.append((r, t, sig))
        for x in range(len(cand)):
            for y in range(x + 1, len(cand)):
                (ra, ta, sa), (rb, tb, sb) = cand[x], cand[y]
                out.append((p, ra, rb, jaccard(sa, sb) if ta == tb else -1.0))
    return out


def _members_items(fl, kids, roots: List[int]) -> List[Dict[str, Any]]:
    """Plan 4: each member's text nodes with a relative key (its address under the member's root), made of
    element types and sibling counters only (the `loose` key): class names are left out, as in the
    signature (plan 9), so a disabled card's extra class does not give its fields slots of their own."""
    members = []
    for r in roots:
        field = "loose" if fl[r].get("loose") else "key"
        base = fl[r][field]
        items = []
        stack = [r]
        while stack:
            j = stack.pop()
            if fl[j]["text"]:
                rel = "" if j == r else fl[j][field][len(base) + 3:]
                items.append({"rel": rel, "text": fl[j]["text"], "node": j})
            stack.extend(reversed(kids[j]))
        items.sort(key=lambda it: it["node"])
        members.append({"root": r, "items": items})
    return members


def _slots(fl, kids, members: List[Dict[str, Any]]) -> None:
    """Plan 4: line every member's items up against the template (the member with most items).
    An item that pairs with a template item takes its slot; the rest get their own slot."""
    tpl = max(range(len(members)), key=lambda k: (len(members[k]["items"]), -k))
    tlist = [{"key": it["rel"], "text": it["text"], "node": fl[it["node"]]["node"]}
             for it in members[tpl]["items"]]
    for it in members[tpl]["items"]:
        it["slot"] = it["rel"]
    for k, m in enumerate(members):
        if k == tpl:
            continue
        mlist = [{"key": it["rel"], "text": it["text"], "node": fl[it["node"]]["node"]} for it in m["items"]]
        pairs = _align.align(tlist, mlist, compared=lambda e: True)
        taken = {j: i for i, j in pairs.items()}
        for j, it in enumerate(m["items"]):
            it["slot"] = members[tpl]["items"][taken[j]]["rel"] if j in taken else "own: " + it["rel"]


def find_groups(fl: List[Dict[str, Any]], docs: Optional[List[List[Dict[str, Any]]]] = None,
                theta: Optional[float] = None) -> List[Dict[str, Any]]:
    """Plan 3: the groups of one page, deepest first in the list order of their parents."""
    theta = THETA if theta is None else theta
    if theta is None:
        raise ValueError("THETA is not measured yet (docs/cards_rows/plan.md section 8)")
    kids = _children(fl)
    info: Dict[int, Tuple[str, frozenset, bool]] = {}
    groups: List[Dict[str, Any]] = []

    for p, cs in _sibling_sets(fl):                          # deepest parents first, the top level last
        cand = []
        for c in cs:
            r = _root(fl, kids, c)
            if r not in info:
                info[r] = (fl[r]["type"], _sig(fl, kids, r), _text_under(fl, kids, r))
            t, sig, txt = info[r]
            if sig and txt:
                cand.append((r, t, sig))
        # connected components of "like": same root type and jaccard >= theta
        parent_of = list(range(len(cand)))

        def find(x: int) -> int:
            while parent_of[x] != x:
                parent_of[x] = parent_of[parent_of[x]]
                x = parent_of[x]
            return x

        for x in range(len(cand)):
            for y in range(x + 1, len(cand)):
                if cand[x][1] == cand[y][1] and jaccard(cand[x][2], cand[y][2]) >= theta:
                    parent_of[find(x)] = find(y)
        comp: Dict[int, List[int]] = defaultdict(list)
        for x in range(len(cand)):
            comp[find(x)].append(cand[x][0])
        for roots in comp.values():
            if len(roots) < 2:
                continue
            groups.append({"kind": "unknown", "parent": p, "members": _members_items(fl, kids, roots),
                           "source": "siblings", "parent_group": None, "parent_member": None,
                           "sig": sorted(set().union(*(info[r][1] for r in roots)))})

    for g in groups:
        _slots(fl, kids, g["members"])
    for g in _table_groups(fl, docs):
        groups.append(g)
    _link_nesting(fl, groups)
    return groups


def _table_groups(fl, docs) -> List[Dict[str, Any]]:
    """Plan 3.1: the body rows of every Table element, slot = column name (no generic detection)."""
    if not docs:
        return []
    bases = _bases(docs)
    out = []
    for tb in _tables.extract(docs):
        base = bases[tb["doc"]]
        names = _tables.column_names(tb)
        rows: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for c in tb["cells"]:
            if c["row"] >= tb["header_rows"] and c["text"]:
                rows[c["row"]].append(c)
        members = []
        for r in sorted(rows):
            cells = sorted(rows[r], key=lambda c: c["col"])
            root = fl[base + cells[0]["node"]]["parent"]
            items = []
            for c in cells:
                slot = names[c["col"]] if c["col"] < len(names) and names[c["col"]] else f"col {c['col']}"
                items.append({"rel": slot, "slot": slot, "text": c["text"], "node": base + c["node"]})
            members.append({"root": root, "items": items})
        if len(members) >= 2:
            out.append({"kind": "table", "parent": fl[members[0]["root"]]["parent"], "members": members,
                        "source": "table", "parent_group": None, "parent_member": None})
    return out


def _link_nesting(fl, groups: List[Dict[str, Any]]) -> None:
    """Plan 3.6: a group whose parent sits inside a member of another group belongs to that member."""
    owners: List[Tuple[int, int, int]] = []        # (group index, member root, depth of the root)
    for gi, g in enumerate(groups):
        for m in g["members"]:
            owners.append((gi, m["root"], fl[m["root"]]["depth"]))
    for gi, g in enumerate(groups):
        best = None
        a = g["parent"]
        while a >= 0:
            for hi, root, depth in owners:
                if root == a and hi != gi and (best is None or depth > best[2]):
                    best = (hi, root, depth)
            if best is not None:
                break
            a = fl[a]["parent"]
        if best is not None:
            g["parent_group"], g["parent_member"] = best[0], best[1]


# ── 3. roles (one page) ───────────────────────────────────────────────────────

def roles(group: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Plan 5 (one page): slot -> {role, present, texts}. present = members holding the slot."""
    by: Dict[str, List[str]] = defaultdict(list)
    for m in group["members"]:
        seen = set()
        for it in m["items"]:
            if it["slot"] in seen:
                continue
            seen.add(it["slot"])
            by[it["slot"]].append(it["text"])
    n = len(group["members"])
    out = {}
    for slot, texts in by.items():
        if len(texts) < 2:
            role = "single"
        elif len(set(texts)) == 1:
            role = "label"
        elif len(set(texts)) == len(texts):
            role = "distinct"
        else:
            role = "mixed"
        out[slot] = {"role": role, "present": len(texts), "of": n, "texts": texts}
    return out


def lattice(group: Dict[str, Any], fl) -> str:
    """Plan 3.5: a geometry witness only. 'rows' = same width, stacked; 'cards' = same height, side by side."""
    boxes = []
    for m in group["members"]:
        rect = fl[m["root"]]["node"].get("rect")
        if not rect or len(rect) < 4:
            return "none"
        boxes.append(rect)
    if len(boxes) < 2:
        return "none"
    if all(abs(b[2] - boxes[0][2]) <= TOL for b in boxes) and all(abs(b[1] - boxes[0][1]) > TOL for b in boxes[1:]):
        return "rows"
    if all(abs(b[3] - boxes[0][3]) <= TOL for b in boxes) and all(abs(b[0] - boxes[0][0]) > TOL for b in boxes[1:]):
        return "cards"
    return "none"


# ── 4. kind across captures of one page link ──────────────────────────────────

def _parent_shape(g: Dict[str, Any], fl) -> str:
    p = g["parent"]
    return ROOT_SHAPE if p < 0 else _DOC.sub("", _align.shape({"key": fl[p]["key"]}))


def _group_sig(g: Dict[str, Any], fl) -> frozenset:
    if "sig" in g:
        return frozenset(g["sig"])
    return frozenset(_DOC.sub("", _align.shape({"key": fl[m["root"]]["key"]})) for m in g["members"])  # table rows


def match_group(g: Dict[str, Any], fl, candidates: List[Dict[str, Any]], fl2,
                taken: Optional[Set[int]] = None) -> Optional[int]:
    """Plan 6.1: the group of another page that is the same group as g - same kind of source (table or
    siblings), same parent shape, same member root type, and the best jaccard of the members' signatures
    (each candidate used once; ties go to the earlier group). None when nothing shares any signature."""
    taken = taken or set()
    rt = fl[g["members"][0]["root"]]["type"]
    ps, sg = _parent_shape(g, fl), _group_sig(g, fl)
    best = None
    for j, h in enumerate(candidates):
        if j in taken or h["source"] != g["source"] or _parent_shape(h, fl2) != ps:
            continue
        if fl2[h["members"][0]["root"]]["type"] != rt:
            continue
        score = jaccard(sg, _group_sig(h, fl2))
        if score > 0 and (best is None or score > best[0]):
            best = (score, j)
    return None if best is None else best[1]


def client_of(rec: Dict[str, Any], fl: List[Dict[str, Any]], fallback: str) -> str:
    """Who the client of one capture is: the PAN / GSTIN the page shows (core.sdis.identity.client_ids, the
    same reader compare.py uses), as one sorted label. A page that shows none is its own client (fallback)."""
    from core.sdis.identity import client_ids

    class _Page:
        link = rec.get("page") or ""

        @staticmethod
        def to_flat():
            return fl
    ids = client_ids(_Page())
    return "|".join(sorted(ids)) if ids else "unknown:" + fallback


def _capture(c) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Any]:
    """A capture entry: (link, flat, groups) or (link, flat, groups, client). Without a client each
    capture is its own client."""
    return (c[0], c[1], c[2], c[3]) if len(c) > 3 else (c[0], c[1], c[2], id(c[1]))


def recurrence(g: Dict[str, Any], fl, others: List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]
               ) -> Dict[str, float]:
    """Plan 5: for each slot of g that is `distinct` here AND on every other capture that has the same
    group, r = share of its texts that appear in that slot on one of those other captures.
    others = [(flat, groups)] of OTHER CLIENTS' captures of the same link (the caller filters clients:
    a same-client value recurs for a reason that is not a name). Slots without such a group are left out."""
    same = []
    for f2, gs2 in others:
        j = match_group(g, fl, gs2, f2)
        if j is not None:
            same.append(gs2[j])
    if not same:
        return {}
    out = {}
    for slot, info in roles(g).items():
        if info["role"] != "distinct":
            continue
        if any(roles(g2).get(slot, {}).get("role") != "distinct" for g2 in same):
            continue
        seen = {t for g2 in same for t in roles(g2).get(slot, {}).get("texts", [])}
        out[slot] = sum(1 for t in info["texts"] if t in seen) / len(info["texts"])
    return out


def classify(captures: List[tuple], rho: Optional[float] = None) -> None:
    """Plan 5 (across captures). captures = [(page link, flat list, groups[, client])], one per capture.
    Only captures of the same link AND another client count. Sets group['kind'] and, for cards,
    group['name_slot'] and group['recurrence'] in place. Table groups stay `table`."""
    rho = RHO if rho is None else rho
    caps = [_capture(c) for c in captures]
    for link, fl, groups, client in caps:
        others = [(f2, gs2) for l2, f2, gs2, c2 in caps if l2 == link and c2 != client]
        for g in groups:
            if g["kind"] == "table":
                continue
            if not any(match_group(g, fl, gs2, f2) is not None for f2, gs2 in others):
                g["kind"] = "unknown"
                continue
            if rho is None:
                raise ValueError("RHO is not measured yet (docs/cards_rows/plan.md section 8)")
            rs = recurrence(g, fl, others)
            best = max(sorted(rs.items()), key=lambda kv: kv[1], default=None)
            if best is not None and best[1] >= rho:
                g["kind"], g["name_slot"], g["recurrence"] = "cards", best[0], best[1]
            else:
                g["kind"] = "list"


# ── 5. compare two pages' groups ──────────────────────────────────────────────

def compare_groups(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Plan 6.2-6.3. Cards pair their members: by equal name text when both have the same name slot,
    otherwise by content (a member pairs when its slots agree on more than they differ: score > 0.5,
    greedy by best score, page order breaks ties). list / table / unknown groups pair NO rows: the
    result is the template (slots) and the row counts on each side.
    Returns {"mode": "cards" | "template", "pairs": [{"a", "b", "pairing"}], "rows": (na, nb),
    "slots": [slot names of either side]}. An index is None where that side has no member."""
    ma, mb = a["members"], b["members"]
    slots = sorted({s for m in ma + mb for s in {it["slot"] for it in m["items"]}})
    if a["kind"] != "cards" and b["kind"] != "cards":
        return {"mode": "template", "pairs": [], "rows": (len(ma), len(mb)), "slots": slots}

    def texts(m):
        out: Dict[str, str] = {}
        for it in m["items"]:
            out.setdefault(it["slot"], it["text"])
        return out

    pairs: List[Dict[str, Any]] = []
    name = a.get("name_slot") if a.get("name_slot") == b.get("name_slot") else None
    if name:
        used = set()
        for i, m in enumerate(ma):
            k = texts(m).get(name)
            j = next((j for j, mm in enumerate(mb) if j not in used and k is not None and texts(mm).get(name) == k), None)
            if j is not None:
                used.add(j)
            pairs.append({"a": i, "b": j, "pairing": "name"})
        pairs += [{"a": None, "b": j, "pairing": "page"} for j in range(len(mb)) if j not in used]
        return {"mode": "cards", "pairs": pairs, "rows": (len(ma), len(mb)), "slots": slots}

    scored = []
    for i, m1 in enumerate(ma):
        t1 = texts(m1)
        for j, m2 in enumerate(mb):
            t2 = texts(m2)
            union = set(t1) | set(t2)
            same = sum(1 for s in union if s in t1 and s in t2 and t1[s] == t2[s])
            if union and same / len(union) > 0.5:
                scored.append((-same / len(union), i, j))
    used_a, used_b = set(), set()
    for _, i, j in sorted(scored):
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append({"a": i, "b": j, "pairing": "content"})
    pairs += [{"a": i, "b": None, "pairing": "page"} for i in range(len(ma)) if i not in used_a]
    pairs += [{"a": None, "b": j, "pairing": "page"} for j in range(len(mb)) if j not in used_b]
    return {"mode": "cards", "pairs": pairs, "rows": (len(ma), len(mb)), "slots": slots}


def docs_for(rec: Dict[str, Any], fl: List[Dict[str, Any]]) -> Optional[List[List[Dict[str, Any]]]]:
    """Raw docs for tables.extract(): a read keeps its own; a comp page is rebuilt from its nodes (each raw
    node with the comp page's own parent index), so a Table is found there too."""
    if not rec.get("comp_page"):
        return rec.get("docs") or []
    return [[dict(e["node"], parent=e["parent"]) for e in fl]]


def outside_flat(fl: List[Dict[str, Any]], groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Plan 6.4: the page without the members of any group (their subtrees), parents re-indexed. Group
    text can then never be an anchor for text outside its group (the IFF / GSTR-1 trap). A page with no
    groups comes back unchanged in content and order."""
    kids = _children(fl)
    removed: Set[int] = set()
    for g in groups:
        for m in g["members"]:
            stack = [m["root"]]
            while stack:
                j = stack.pop()
                if j not in removed:
                    removed.add(j)
                    stack.extend(kids[j])
    keep = [i for i in range(len(fl)) if i not in removed]
    new_index = {old: new for new, old in enumerate(keep)}
    out = []
    for old in keep:
        e = dict(fl[old])
        p = e["parent"]
        while p >= 0 and p in removed:
            p = fl[p]["parent"]
        e["parent"] = new_index[p] if p >= 0 else -1
        out.append(e)
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def _read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _report(path: Path, theta: float) -> int:
    rec = _read(path)
    fl, docs = load_flat(rec)
    groups = find_groups(fl, docs, theta)
    if not groups:
        print(f"{path.name}: no group")
        return 0
    for gi, g in enumerate(groups):
        size = [len(m["items"]) for m in g["members"]]
        print(f"group {gi}: kind={g['kind']} source={g['source']} members={len(size)} "
              f"items per member={min(size)}-{max(size)} lattice={lattice(g, fl)} "
              f"parent_group={g['parent_group']}")
        for slot, info in sorted(roles(g).items(), key=lambda kv: kv[0]):
            print(f"    slot {slot[:60]!r}: {info['role']} ({info['present']}/{info['of']} members)")
    return 0


def recurrence_report(paths: List[Path], theta: float, client_labels: Optional[Dict[str, str]] = None) -> None:
    """Plan 8 (rho): for every page link with 2+ captures, each group's distinct slots and their r against
    OTHER CLIENTS' captures (recurrence(), the same rule classify() uses). The client of a capture is read
    from the page (client_of: PAN / GSTIN); client_labels {"comp_<stamp>_<n>": label} overrides it.
    Counts and slot names only. THETA must be set for the groups to exist."""
    by_link: Dict[str, list] = defaultdict(list)
    for path in paths:
        rec = _read(path)
        fl, docs = load_flat(rec)
        prefix = path.stem.split("__")[0]
        client = (client_labels or {}).get(prefix) or client_of(rec, fl, prefix)
        by_link[rec.get("page") or path.name].append((path, fl, find_groups(fl, docs_for(rec, fl), theta), client))
    for link, caps in by_link.items():
        clients = sorted({c for _, _, _, c in caps})
        print(f"link {link[:70]!r}: captures={len(caps)} clients={len(clients)}")
        for path, _, _, c in caps:
            print(f"  {path.name[:40]} client #{clients.index(c) + 1}")
        if len(clients) < 2:
            print("  one client only: no recurrence (plan 8: needs another client on the same link)")
            continue
        for path, fl, gs, c in caps:
            others = [(f2, g2) for _, f2, g2, c2 in caps if c2 != c]
            for gi, g in enumerate(gs):
                if g["kind"] == "table":
                    continue
                for slot, r in sorted(recurrence(g, fl, others).items()):
                    print(f"  {path.name[:40]} group {gi} members={len(g['members'])} "
                          f"slot {slot[:50]!r}: r={r:.2f}")


def _expand(args: List[str]) -> List[Path]:
    """Paths as given; a pattern (comp_*.json) expands here, so it works in PowerShell too."""
    import glob
    out: List[Path] = []
    for a in args:
        out += [Path(p) for p in sorted(glob.glob(a))] if any(c in a for c in "*?[") else [Path(a)]
    return out


def _histogram(paths: List[Path]) -> int:
    """Counts only: how many sibling pairs fall in each 0.1 band of jaccard, per page and in all."""
    total = Counter()
    for path in paths:
        fl, _ = load_flat(_read(path))
        pairs = sibling_pairs(fl)
        bands = Counter(min(9, int(v * 10)) if v >= 0 else "type" for _, _, _, v in pairs)
        print(f"{path.name}: sibling pairs={len(pairs)}")
        total.update(bands)
    print("all pages, jaccard band -> pairs:")
    for k in list(range(10)) + ["type"]:
        if total.get(k):
            print(f"  {k if k == 'type' else f'{k / 10:.1f}'}: {total[k]}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Repeating parts of a page (plan: docs/cards_rows/plan.md).")
    ap.add_argument("files", nargs="*", help="a key_probe read or comp page (.json, globs ok); default: the newest key_probe read in output/")
    ap.add_argument("--theta", type=float, default=None, help="similarity cut-off (plan section 8)")
    ap.add_argument("--histogram", action="store_true", help="print the sibling-similarity histogram instead")
    ap.add_argument("--recurrence", action="store_true", help="print r per distinct slot per page link (plan section 8, rho)")
    ap.add_argument("--clients", default=None, help='optional JSON {"comp_<stamp>_<n>": "client label"} overriding the PAN / GSTIN read from each page (--recurrence)')
    args = ap.parse_args(argv)
    paths = _expand(args.files) or sorted(OUT_DIR.glob("key_probe_*.json"))[-1:]
    if args.histogram:
        return _histogram(paths)
    theta = args.theta if args.theta is not None else THETA
    if args.recurrence:
        if theta is None:
            print("THETA is not measured yet: the groups do not exist, so no recurrence. Run --histogram first.")
            return 2
        clients = json.loads(Path(args.clients).read_text(encoding="utf-8")) if args.clients else None
        recurrence_report(paths, theta, clients)
        return 0
    theta = args.theta if args.theta is not None else THETA
    if theta is None:
        print("THETA is not measured yet: run --histogram, then write it in docs/cards_rows/plan.md section 9.")
        return 2
    return sum(_report(p, theta) for p in paths)


if __name__ == "__main__":
    raise SystemExit(main())
