"""
tools/pre_dev/class_diff/compare.py - PRE-DEV TEST: same page link, two clients, compared by key
=================================================================================================
For EVERY page link the latest capture visited, takes that session's MAP of the link (link_map.py: every node one capture - or one
single read - saw at that link, form, popup, success message, merged by key) and the map of the
most recent OTHER session of the same link, and sorts every text element into:

    fixed          same key, same text for both clients      -> template (labels, headings, menus)
    variable       same key, different text                  -> data (the values we want)
    only latest    the key is only in the latest client's map -> e.g. one more list row / card
    only previous  the key is only in the other client's map

Portal-neutral: an element is matched by its KEY (keys.py) - its address in the page built from
element types, stable ids and class names, counted per parent (card 1, card 2...), with generated
(digit) and state (ng-valid, focused...) class names left out. Nothing here knows what any class
name means, and nothing is portal-specific.

View: by default the RAW view - every element the probe read, nesting kept (decided 2026-10-03:
production reads the raw view, measured 1.2-1.5x the control view's time); --view sgt for only what
SGT's control view sees today.

Pairing: by default elements are paired by ALIGNMENT (align.py: same shape + same text anchor in
page order, the rest paired by shape between anchors), so one extra notice or list row no longer
shifts every partner after it; --align off pairs by exact key, the old way. key_matched says
"yes" (same key), "aligned" (paired by alignment, keys differ - a shift was absorbed) or "no".

Label: for a variable element, the first fixed text inside the smallest box around it that has
one ("Acknowledgement No :" inside the same .valueBox as the number). In a table (Table -> row ->
cell) it is the row's first cell and the column's header cell instead: "Cash Ledger / IGST" - the
column found by the browser's column number or the screen box, never by counting cells.
A label is a text both clients show alike: a fixed text, or a shared text of words with digits
("9B - Credit / Debit Notes"); never a bare number, date, code or amount. Never a label: a link, a button, an image (its name is the alt text, or the browser's own words
when the page gave none), or a COMPOSITE - an element whose text is just its children's texts
joined ("79,99,235.00 View/Update"). A composite row is kept but flagged in check: its parts are
listed on their own.

    python tools/pre_dev/class_diff/compare.py                         # every page link's MEMORY (all clients)
    python tools/pre_dev/class_diff/compare.py --two                   # latest client vs previous client
    python tools/pre_dev/class_diff/compare.py --comp                  # experiment 1: comp page vs comp page
    python tools/pre_dev/class_diff/compare.py --latest A.json --previous B.json   # two single reads

The default is a view of core/sdis/memory.py (Part I): one row per node with a text, its status
(memory.status), label (memory.label), type, number of clients, the latest client's value and key,
in output/compare_memory__<page>.csv. Everything below describes the two-client mode (--two).

Writes output/compare_<latest read's timestamp>.csv (opens in Excel). example_value is from the
latest read; an "only previous" row has none there, so it shows the previous read's value. It holds real values from
the page - client data - and stays in the git-ignored output folder on this PC.

page: the page link the row belongs to (a capture can visit several links; each is compared with
the previous session that visited the same link). key_matched: "yes" when the element's key was
found in both sessions (fixed / variable), "no" when only one side has it.

value_type: SGT-I's generic type of the value (pairs.classify_type) plus "period", "label", "sentence",
"alphanumeric";
previous_type the same for the previous read. check flags a type that contradicts the status:
variable with different types (likely misaligned), fixed but data-shaped (may be a coincidence),
variable but furniture-shaped (wording changed?).

Known limits of this test (on purpose, it is a test): only TWO reads are compared, so a value two
clients happen to share ("Filed By: self") shows as fixed - the real version needs 3+ clients;
lists are matched card n to card n.
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
os.environ.setdefault("SDIS_DATA_DIR", str(OUT_DIR))

from core.sdis import keys                                       # noqa: E402
from core.sdis import link_map                                   # noqa: E402
from core.sdis.align import align, shape                         # noqa: E402
from core.sdis.identity import CLIENT_FIELDS, client_ids, masked   # noqa: E402,F401
from core.sdis.keys import BUTTON_CTYPES, CHOICE_CTYPES, flatten, page_slug   # noqa: E402
from core.sdis.labels import (CT_HYPERLINK, CT_IMAGE, CT_TABLE, FIXABLE_TYPES, LABEL_LOOKBACK,   # noqa: E402,F401
                              NEVER_LABEL_CTYPES, SENTENCE_WORDS, _MONTH, _PERIOD_RES, _YEAR_PREFIX,
                              _children, _container, _end, _first_label, _label, _same_column,
                              _table_label, composites, element_type, is_period, value_type)
from core.sdis.paths import write_csv                            # noqa: E402
import cards                                                     # noqa: E402
import arrange as arr                                            # noqa: E402

FIXED, SEMI_VARIABLE, VARIABLE, ONLY_LATEST, ONLY_PREVIOUS, VARIABLE_ALIGNMENT = (
    "fixed", "semi-variable", "variable", "only latest", "only previous", "variable_alignment")
STATUSES = (FIXED, SEMI_VARIABLE, VARIABLE, ONLY_LATEST, ONLY_PREVIOUS, VARIABLE_ALIGNMENT)


def page_key(rec: Dict[str, Any]) -> str:
    return rec.get("page") or ("title: " + (rec.get("title") or ""))


def _compared(e: Dict[str, Any]) -> bool:
    """Elements that take part in the comparison: a text, and not a button."""
    return bool(e["text"]) and e["node"].get("ctype") not in BUTTON_CTYPES


def check(status: str, latest_type: str, previous_type: str, label: str = "") -> str:
    """Where a value's type contradicts its status. A variable long text WITH a label beside it is
    data (an address), not changed wording - only a label-less one is flagged."""
    if status == VARIABLE and latest_type != previous_type:
        return "types differ - likely misaligned (list shifted?)"
    if status == VARIABLE and latest_type in ("label", "sentence") and not label:
        return "furniture-shaped - wording may have changed"
    return "ok"


def flat_of(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A read's flat list, or a comp page's nodes (snapshot_diff.py) as they are."""
    return rec["nodes"] if rec.get("comp_page") else flatten(rec)


def compare(latest: Dict[str, Any], previous: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two single reads (or two comp pages)."""
    return compare_flat(flat_of(latest), flat_of(previous))


ALIGN = True      # pair elements by align.py (content + shape); False = by exact key, the old way


def _partners(fl: List[Dict[str, Any]], fp: List[Dict[str, Any]],
              ambiguous: Optional[set] = None) -> Dict[int, int]:
    """{index in fl: index in fp} - by alignment, or by exact key when ALIGN is off."""
    if ALIGN:
        return align(fl, fp, _compared, ambiguous=ambiguous)
    by_key = {e["key"]: j for j, e in enumerate(fp) if _compared(e)}
    return {i: by_key[e["key"]] for i, e in enumerate(fl) if _compared(e) and e["key"] in by_key}


def compare_flat(fl: List[Dict[str, Any]], fp: List[Dict[str, Any]],
                 rejected: Optional[set] = None) -> List[Dict[str, Any]]:
    """One row per text element of either side (a read's or a map's flat list), latest first."""
    ambiguous: set = set()
    partner = _partners(fl, fp, ambiguous=ambiguous)
    paired_prev = set(partner.values())
    status: Dict[int, str] = {}
    for i, e in enumerate(fl):
        if not _compared(e):
            continue
        other = fp[partner[i]] if i in partner else None
        if other is None:
            status[i] = ONLY_LATEST
        elif other["text"] != e["text"]:
            status[i] = VARIABLE
        else:
            status[i] = FIXED if element_type(e) in FIXABLE_TYPES else SEMI_VARIABLE
    comp = {id(fl): composites(fl), id(fp): composites(fp)}
    var_shapes = {shape(fl[j]) for j, s in status.items() if s == VARIABLE}
    for i, s in list(status.items()):
        if s == FIXED:
            e = fl[i]
            txt = e["text"]
            node = e.get("node") or {}
            ctype = node.get("ctype")
            if (value_type(txt) != "label"
                    and not txt.rstrip().endswith(":")
                    and i not in comp[id(fl)]
                    and ctype not in CHOICE_CTYPES
                    and shape(e) in var_shapes):
                if rejected and any((link, shape(e), txt) in rejected for link in ("", fl[i].get("page", ""))):
                    continue
                status[i] = VARIABLE_ALIGNMENT
    fixed = {i for i, s in status.items() if s == FIXED}
    # A label is a text both clients show alike: the fixed texts, plus words-with-digits both share
    # ("9B - Credit / Debit Notes") - never a bare number, date, code or amount.
    shared = fixed | {i for i, s in status.items() if s == VARIABLE_ALIGNMENT} | {
        i for i, s in status.items() if s == SEMI_VARIABLE and element_type(fl[i]) == "alphanumeric"}
    # An unpaired text (one more list row, card) that repeats a template text of the same shape is
    # template too: a repeated card repeats its labels ("Period", "ARN" in rows 4 and 5).
    template = {(shape(fl[i]), fl[i]["text"]) for i in shared}
    shared |= {i for i, s in status.items() if s == ONLY_LATEST and (shape(fl[i]), fl[i]["text"]) in template}
    # ...and holding a real letter: an icon-font glyph beside a count ("<bell> 0") is no label.
    labels = {i for i in shared if i not in comp[id(fl)] and fl[i]["node"].get("ctype") not in NEVER_LABEL_CTYPES
              and any(c.isalpha() for c in fl[i]["text"])}

    def row(flat, i, st, example):
        e, n = flat[i], flat[i]["node"]
        other = fp[partner[i]] if flat is fl and i in partner else None
        vt = element_type(e)
        pt = element_type(other) if other else ""
        label = _label(flat, i, labels) if st != FIXED and flat is fl else ""
        if flat is fl and i in ambiguous:
            flag = "ambiguous pairing - look-alikes, one side has fewer"
        elif i in comp[id(flat)]:
            flag = "composite - its parts are listed on their own"
        else:
            flag = check(st, vt, pt, label)
        matched = "no" if other is None else ("yes" if other["key"] == e["key"] else "aligned")
        return {"status": st, "key_matched": matched,
                "value_type": vt, "previous_type": pt, "check": flag,
                "label": label,
                "element": n.get("type_name") or n.get("ctype"), "classes": e["cls"],
                "container": _container(flat, i), "id": n.get("id", ""),
                "sgt_sees": "yes" if n.get("sgt") else "no", "example_value": example,
                "previous_example": other["text"] if other else (example if flat is fp else ""),
                "values_seen": " || ".join(e.get("values") or []),
                "origin": e.get("origin", ""), "present": {True: "yes", False: "no"}.get(e.get("present"), ""),
                "key": e["key"]}

    rows = [row(fl, i, st, fl[i]["text"]) for i, st in status.items()]
    rows += [row(fp, j, ONLY_PREVIOUS, e["text"]) for j, e in enumerate(fp)
             if _compared(e) and j not in paired_prev]
    return rows


def _reads() -> List[Path]:
    return sorted(OUT_DIR.glob("key_probe_*.json"))


def pick(latest: Optional[str], previous: Optional[str]) -> Tuple[Path, Path]:
    reads = _reads()
    lp = Path(latest) if latest else (reads[-1] if reads else None)
    if lp is None:
        raise SystemExit(f"No reads in {OUT_DIR} yet - run key_probe.py on a page first.")
    if previous:
        return lp, Path(previous)
    key = page_key(json.loads(lp.read_text(encoding="utf-8")))
    for p in reversed(reads):
        if p.name < lp.name and page_key(json.loads(p.read_text(encoding="utf-8"))) == key:
            return lp, p
    raise SystemExit(f"No earlier read of the same page ({key}) - run key_probe.py on it for another client first.")


FIELDS = ["page", "status", "key_matched", "check", "label", "example_value", "previous_example", "values_seen", "value_type", "previous_type", "element", "classes",
          "container", "id", "sgt_sees", "origin", "present", "key"]


MEMORY_FIELDS = ["status", "label", "value_type", "clients", "example_value", "key"]

# --comp --cards (plan 7): the card columns come after the ordinary ones.
CARD_FIELDS = ["group", "group_kind", "member", "card_name", "slot", "slot_role", "pairing"]
CARDS_CSV_FIELDS = FIELDS + CARD_FIELDS


def _texts_of(member: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in member["items"]:
        out.setdefault(it["slot"], it["text"])
    return out


def _group_rows(gi: int, g: Dict[str, Any], h: Dict[str, Any], out: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Plan 6.2-6.3: the rows of one paired group. Cards: one row per slot of each pair (fixed / variable /
    only latest / only previous). list, table or unknown: the template, one row per slot with its values
    from both pages (first few and the count) and a row-count row."""
    rows: List[Dict[str, Any]] = []
    roles_l, roles_p = cards.roles(g), cards.roles(h)
    base = {"group": gi, "group_kind": g["kind"]}
    if out["mode"] == "cards":
        name = g.get("name_slot")
        for pr in out["pairs"]:
            ma = g["members"][pr["a"]] if pr["a"] is not None else None
            mb = h["members"][pr["b"]] if pr["b"] is not None else None
            ta, tb = (_texts_of(ma) if ma else {}), (_texts_of(mb) if mb else {})
            card = (ta.get(name) if ma else None) or (tb.get(h.get("name_slot")) if mb else None) or ""
            for slot in out["slots"]:
                if slot not in ta and slot not in tb:
                    continue
                if slot not in tb:
                    st = ONLY_LATEST
                elif slot not in ta:
                    st = ONLY_PREVIOUS
                else:
                    st = FIXED if ta[slot] == tb[slot] else VARIABLE
                role = (roles_l.get(slot) or roles_p.get(slot) or {}).get("role", "")
                rows.append(dict(base, member=(pr["a"] if pr["a"] is not None else f"p{pr['b']}"), card_name=card,
                                 slot=slot, slot_role=role, pairing=pr["pairing"], status=st,
                                 example_value=ta.get(slot, ""), previous_example=tb.get(slot, "")))
        return rows
    na, nb = out["rows"]
    for slot in out["slots"]:
        ta = [it["text"] for m in g["members"] for it in m["items"] if it["slot"] == slot]
        tb = [it["text"] for m in h["members"] for it in m["items"] if it["slot"] == slot]
        rows.append(dict(base, member="", card_name="", slot=slot,
                         slot_role=(roles_l.get(slot) or roles_p.get(slot) or {}).get("role", ""),
                         pairing="template", status="template",
                         example_value=" | ".join(ta[:3]) + (f" (+{len(ta) - 3})" if len(ta) > 3 else ""),
                         previous_example=" | ".join(tb[:3]) + (f" (+{len(tb) - 3})" if len(tb) > 3 else "")))
    rows.append(dict(base, member="", card_name="", slot="", slot_role="", pairing="rows",
                     status="row counts", example_value=f"latest {na} rows", previous_example=f"previous {nb} rows"))
    return rows


def cards_rows(lrec: Dict[str, Any], prec: Dict[str, Any], page: str, lclient: Any = "latest",
               pclient: Any = "previous") -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """--comp --cards for one page link: the groups of both comp pages (classified across the two clients),
    paired by identity, then the rest of each page compared with the group members taken out (plan 6.4).
    Returns (rows, counts). Counts only, for the console."""
    lfl, pfl = flat_of(lrec), flat_of(prec)
    lg = cards.find_groups(lfl, cards.docs_for(lrec, lfl))
    pg = cards.find_groups(pfl, cards.docs_for(prec, pfl))
    counts: Counter = Counter()
    if cards.RHO is None:
        # plan 8: until RHO is measured no group is a card; every group is compared as its template
        counts["rho not measured: templates"] = 1
    else:
        cards.classify([(page, lfl, lg, lclient), (page, pfl, pg, pclient)])
    rows: List[Dict[str, Any]] = []
    used: Set[int] = set()
    for gi, g in enumerate(lg):
        j = cards.match_group(g, lfl, pg, pfl, used)
        if j is None:
            counts["group only latest"] += 1
            rows.append(dict(group=gi, group_kind=g["kind"], pairing="page", status=ONLY_LATEST,
                             example_value=f"{len(g['members'])} members", slot="", member="", card_name=""))
            continue
        used.add(j)
        h = pg[j]
        counts["group paired"] += 1
        rows += _group_rows(gi, g, h, cards.compare_groups(g, h))
    for j, h in enumerate(pg):
        if j not in used:
            counts["group only previous"] += 1
            rows.append(dict(group=j, group_kind=h["kind"], pairing="page", status=ONLY_PREVIOUS,
                             example_value="", previous_example=f"{len(h['members'])} members", slot="", member="", card_name=""))
    outside = compare_flat(cards.outside_flat(lfl, lg), cards.outside_flat(pfl, pg))
    counts["rows outside groups"] = len(outside)
    rows += outside
    for r in rows:
        r.setdefault("page", page)
        for f in CARD_FIELDS:
            r.setdefault(f, "")
        for f in FIELDS:
            r.setdefault(f, "")
    return rows, dict(counts)



# --comp --arrange (WP4, design 3.6): the arrangement of each page (arrange.py) pairs its groups. Top-level
# groups pair by period and seed likeness; members pair by the name slot when one is known (corpus R at or
# above the cut-off, arrange.NAME_CUT), else by place; nested groups pair only inside a pair of members (bug
# 10). Every leaf is reported once: the rest of the page goes through compare_flat() with the group leaves
# taken out, so group text never anchors anything outside its group.
ARRANGE_FIELDS = ["group", "group_kind", "member", "card_name", "slot", "slot_role", "label_dir", "ticking", "pairing"]
ARRANGE_CSV_FIELDS = FIELDS + ARRANGE_FIELDS


def _without(fl: List[Dict[str, Any]], drop: Set[int]) -> List[Dict[str, Any]]:
    """The page without the given node indices (leaves only), parents re-indexed through removed ancestors."""
    keep = [i for i in range(len(fl)) if i not in drop]
    new_index = {old: new for new, old in enumerate(keep)}
    out = []
    for old in keep:
        e = dict(fl[old])
        p = e["parent"]
        while p >= 0 and p in drop:
            p = fl[p]["parent"]
        e["parent"] = new_index[p] if p >= 0 and p in new_index else -1
        out.append(e)
    return out


def _pair_by_seed(gl_idx: List[int], gp_idx: List[int], la: Any, pa: Any) -> Dict[int, int]:
    """Greedy pairing of groups by the likeness of their seed periods: cheapest first, each group once,
    never above EDITS_PER_TOKEN per token. Ties go to page order."""
    cands = []
    for i in gl_idx:
        g = la.groups[i]
        tl = la.tokens[g.seed:g.seed + g.period]
        for j in gp_idx:
            h = pa.groups[j]
            if h.period != g.period:
                continue
            cost = arr.edits(tl, pa.tokens[h.seed:h.seed + h.period]) / g.period
            if cost <= arr.EDITS_PER_TOKEN:
                cands.append((cost, i, j))
    out: Dict[int, int] = {}
    used: Set[int] = set()
    for _, i, j in sorted(cands):
        if i not in out and j not in used:
            out[i] = j
            used.add(j)
    return out


def _family_r(gl: Any, la: Any, gp: Any, pa: Any, lclient: str, pclient: str) -> Dict[Any, Optional[float]]:
    """Corpus R per slot over the two captures of this group (the two clients of one link)."""
    def texts(g: Any, a: Any) -> Dict[Any, List[str]]:
        return {k: [a.leaves[i].text for i in v if i is not None] for k, v in g.slots.items()}
    fam = [(lclient, len(gl.members), texts(gl, la)), (pclient, len(gp.members), texts(gp, pa))]
    return {k: arr.recurrence(fam, k) for k in gl.slots}


def _member_pairs(gl: Any, la: Any, gp: Any, pa: Any,
                  R: Dict[Any, Optional[float]]) -> Tuple[List[Tuple[Optional[int], Optional[int]]], str]:
    """(latest member, previous member) pairs and how they paired: by the name text when both pages show a
    name at the same slot, else by place (member 1 with member 1). Unpaired members have None on one side."""
    name = arr.name_slot(gl, R, arr.NAME_CUT)
    if name is not None and name == arr.name_slot(gp, R, arr.NAME_CUT):
        keyed: Dict[str, int] = {}
        for b, idx in enumerate(gp.slots[name]):
            if idx is not None:
                keyed.setdefault(pa.leaves[idx].text, b)
        pairs: List[Tuple[Optional[int], Optional[int]]] = []
        used: Set[int] = set()
        for a, idx in enumerate(gl.slots[name]):
            b = keyed.get(la.leaves[idx].text) if idx is not None else None
            if b is not None and b not in used:
                used.add(b)
                pairs.append((a, b))
            else:
                pairs.append((a, None))
        pairs += [(None, b) for b in range(len(gp.members)) if b not in used]
        return pairs, "name"
    n = min(len(gl.members), len(gp.members))
    pairs = [(a, a) for a in range(n)] + [(a, None) for a in range(n, len(gl.members))]
    pairs += [(None, b) for b in range(n, len(gp.members))]
    return pairs, "place"


def _label_dir(lv: Any, g: Any, member: int, slot: Any) -> str:
    """The step from the nearest label slot before this value (same member) to the value: R or D."""
    if not isinstance(slot, int):
        return ""
    val = g.slots[slot][member]
    if val is None:
        return ""
    for q in range(slot - 1, -1, -1):
        if g.roles.get(q) == "label":
            lab = g.slots[q][member] if q in g.slots else None
            return arr.step(lv.leaves[lab].box, lv.leaves[val].box) if lab is not None else ""
    return ""


def _template_rows(gl: Any, gp: Any, la: Any, pa: Any, group_no: int,
                   covered_l: Set[int], covered_p: Set[int]) -> List[Dict[str, Any]]:
    """A list compared by template: one row per slot with its first values and count from each page, and
    one row with the member counts. Every leaf of the group is covered by these rows."""
    base = {"group": group_no, "group_kind": gl.kind}
    rows: List[Dict[str, Any]] = []
    for slot in sorted(set(gl.slots) | set(gp.slots), key=arr._slot_order):
        ia = [i for i in gl.slots.get(slot, []) if i is not None]
        ib = [i for i in gp.slots.get(slot, []) if i is not None]
        covered_l.update(la.leaves[i].node for i in ia)
        covered_p.update(pa.leaves[i].node for i in ib)
        ta = [la.leaves[i].text for i in ia]
        tb = [pa.leaves[i].text for i in ib]
        rows.append(dict(base, member="", card_name="", slot=str(slot), slot_role=gl.roles.get(slot) or gp.roles.get(slot, ""),
                         label_dir="", ticking="", pairing="template", status="template",
                         example_value=" | ".join(ta[:3]) + (f" (+{len(ta) - 3})" if len(ta) > 3 else ""),
                         previous_example=" | ".join(tb[:3]) + (f" (+{len(tb) - 3})" if len(tb) > 3 else "")))
    rows.append(dict(base, member="", card_name="", slot="", slot_role="", label_dir="", ticking="",
                     pairing="rows", status="row counts", example_value=f"latest {len(gl.members)} members",
                     previous_example=f"previous {len(gp.members)} members"))
    return rows


def _pair_rows(li: int, pi: int, la: Any, pa: Any, lc: str, pc: str, group_no: int,
               covered_l: Set[int], covered_p: Set[int], counts: Counter) -> List[Dict[str, Any]]:
    """Rows of one paired group: its member pairs slot by slot, then its nested groups inside each pair."""
    gl, gp = la.groups[li], pa.groups[pi]
    rows: List[Dict[str, Any]] = []
    R = _family_r(gl, la, gp, pa, lc, pc)
    name_l = arr.name_slot(gl, R, arr.NAME_CUT)
    if name_l is None or name_l != arr.name_slot(gp, R, arr.NAME_CUT):
        distinct = "distinct" in gl.roles.values() or "distinct" in gp.roles.values()
        if len(gl.members) != len(gp.members) or distinct:
            # design 3.5: a count that differs across the captures makes a list; lists are never paired row
            # by row, they are compared by template (slot, example values, counts). A distinct slot may be
            # a name whose R is not measured yet (design 3.5.1: then the group is a list too): pairing its
            # members by place would join IFF with GSTR-1 when both pages show the same number of tiles.
            counts["group compared by template"] += 1
            return _template_rows(gl, gp, la, pa, group_no, covered_l, covered_p)
    pairs, how = _member_pairs(gl, la, gp, pa, R)
    counts[f"group paired by {how}"] += 1
    name = name_l if how == "name" else None
    base = {"group": group_no, "group_kind": gl.kind}
    for a, b in pairs:
        if a is None or b is None:
            # one side only: every leaf of that member is reported as only latest / only previous
            latest = a is not None
            lv, g, m, cov = (la, gl, a, covered_l) if latest else (pa, gp, b, covered_p)
            name_idx = g.slots[name][m] if name is not None else None
            card = lv.leaves[name_idx].text if name_idx is not None else ""
            for slot, idx in g.slots.items():
                if idx[m] is None:
                    continue
                leaf = lv.leaves[idx[m]]
                cov.add(leaf.node)
                rows.append(dict(base, member=m if latest else f"p{m}", card_name=card, slot=str(slot),
                                 slot_role=g.roles.get(slot, ""), label_dir="",
                                 ticking="yes" if leaf.ticking else "no", pairing=how,
                                 status=ONLY_LATEST if latest else ONLY_PREVIOUS,
                                 example_value=leaf.text if latest else "",
                                 previous_example="" if latest else leaf.text))
            continue
        card = la.leaves[gl.slots[name][a]].text if name is not None and gl.slots[name][a] is not None else ""
        for slot in sorted(set(gl.slots) | set(gp.slots), key=arr._slot_order):
            ia = gl.slots[slot][a] if slot in gl.slots else None
            ib = gp.slots[slot][b] if slot in gp.slots else None
            if ia is None and ib is None:
                continue
            if ia is not None:
                covered_l.add(la.leaves[ia].node)
            if ib is not None:
                covered_p.add(pa.leaves[ib].node)
            ta = la.leaves[ia].text if ia is not None else ""
            tb = pa.leaves[ib].text if ib is not None else ""
            if ia is None:
                st = ONLY_PREVIOUS
            elif ib is None:
                st = ONLY_LATEST
            else:
                st = FIXED if ta == tb else VARIABLE
            ticking = (la.leaves[ia].ticking if ia is not None else pa.leaves[ib].ticking)
            rows.append(dict(base, member=a, card_name=card, slot=str(slot),
                             slot_role=gl.roles.get(slot) or gp.roles.get(slot, ""),
                             label_dir=_label_dir(la, gl, a, slot) if ia is not None else "",
                             ticking="yes" if ticking else "no", pairing=how, status=st,
                             example_value=ta, previous_example=tb))
    # Nested groups produce no rows of their own (design 3.6.7): their leaves are slots of this group's
    # members, so the card rows carry them, and they pair only inside a pair of members (bug 10), because
    # every slot is compared with the slot at the same place in the paired member.
    return rows


def arrange_rows(lrec: Dict[str, Any], prec: Dict[str, Any], page: str, lclient: str = "latest",
                 pclient: str = "previous") -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """--comp --arrange for one page link: each page's arrangement, its groups paired, then the rest of each
    page through compare_flat() with the group leaves removed. Returns (rows, counts), counts only."""
    lfl, pfl = flat_of(lrec), flat_of(prec)
    la, pa = arr.arrange(lfl), arr.arrange(pfl)
    counts: Counter = Counter()
    top_l = [i for i, g in enumerate(la.groups) if g.parent is None]
    top_p = [j for j, h in enumerate(pa.groups) if h.parent is None]
    pairs = _pair_by_seed(top_l, top_p, la, pa)
    rows: List[Dict[str, Any]] = []
    covered_l: Set[int] = set()
    covered_p: Set[int] = set()
    for i in top_l:
        if i in pairs:
            counts["group paired"] += 1
            rows += _pair_rows(i, pairs[i], la, pa, lclient, pclient, i, covered_l, covered_p, counts)
        else:
            counts["group only latest"] += 1
            g = la.groups[i]
            for m, (a, b) in enumerate(g.members):
                for x in range(a, b):
                    covered_l.add(la.leaves[x].node)
                    rows.append(dict(group=i, group_kind=g.kind, member=m, card_name="", slot="", slot_role="",
                                     label_dir="", ticking="", pairing="page", status=ONLY_LATEST,
                                     example_value=la.leaves[x].text, previous_example=""))
    paired_p = set(pairs.values())
    for j in top_p:
        if j not in paired_p:
            counts["group only previous"] += 1
            h = pa.groups[j]
            for m, (a, b) in enumerate(h.members):
                for x in range(a, b):
                    covered_p.add(pa.leaves[x].node)
                    rows.append(dict(group=j, group_kind=h.kind, member=f"p{m}", card_name="", slot="", slot_role="",
                                     label_dir="", ticking="", pairing="page", status=ONLY_PREVIOUS,
                                     example_value="", previous_example=pa.leaves[x].text))
    for g in la.groups:
        for a, b in g.members:
            covered_l.update(la.leaves[x].node for x in range(a, b))
    for g in pa.groups:
        for a, b in g.members:
            covered_p.update(pa.leaves[x].node for x in range(a, b))
    outside = compare_flat(_without(lfl, covered_l), _without(pfl, covered_p))
    counts["rows outside groups"] = len(outside)
    rows += outside
    for r in rows:
        r.setdefault("page", page)
        for f in ARRANGE_FIELDS:
            r.setdefault(f, "")
        for f in FIELDS:
            r.setdefault(f, "")
    return rows, dict(counts)


def memory_rows(m: Any) -> List[Dict[str, Any]]:
    """One row per memory node with a text: example_value is the latest client's, from its own view."""
    rows = []
    for nid, nd in enumerate(m.nodes):
        if not nd["text"]:
            continue
        client, i = m._view_of(nid)
        example = m.views[client][0][i]["text"]
        rows.append({"status": m.status(nid), "label": m.label(nid), "value_type": m._type_of(nid, example),
                     "clients": len(nd["clients"]), "example_value": example, "key": nd["key"]})
    return rows


def main_memory() -> int:
    """The default: every page link's memory over all its clients (not one pair)."""
    from core.sdis import memory
    pages = {p: cm for p, cm in memory.client_maps().items() if len(cm) >= 2}
    if not pages:
        raise SystemExit(f"No page link has two clients in {OUT_DIR} yet - capture it for another client first.")
    for page in sorted(pages):
        cm = pages[page]
        m = memory.build(page, cm, sorted(cm, key=lambda c: cm[c].reads[0]), 2)
        page_rows = memory_rows(m)
        print(f"Page     : {page}   clients {len(cm)}")
        counts = Counter(r["status"] for r in page_rows)
        for st in sorted(counts):
            print(f"  {st:18s} {counts[st]:5d}")
        data = [r for r in page_rows if r["status"] in ("semi-variable", "variable", VARIABLE_ALIGNMENT)]
        print(f"  data nodes {len(data)}, labelled {sum(1 for r in data if r['label'])}")
        out = write_csv(OUT_DIR / f"compare_memory__{page_slug(page)}.csv", MEMORY_FIELDS, page_rows)
        print(f"CSV      : {out.name}")
        print()
    print("(the CSVs hold real page values - keep them on this PC)")
    return 0


class _Comp:
    """A comp page as client_ids() wants it: .link and .to_flat()."""

    def __init__(self, rec: Dict[str, Any]) -> None:
        self.link, self.rec = rec.get("page") or "", rec

    def to_flat(self) -> List[Dict[str, Any]]:
        return self.rec["nodes"]


def main_comp(stamp_out: bool = True, cards_mode: bool = False, arrange_mode: bool = False) -> int:
    """Experiment 1: for every page link, the LATEST comp page vs the latest comp page of the same
    link from another client (another session whose PAN / GSTIN differ; a session with none is
    compared anyway, with a warning)."""
    comps = []
    for p in sorted(OUT_DIR.glob("comp_*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("comp_page"):
            comps.append((p, rec, client_ids(_Comp(rec))))
    if not comps:
        raise SystemExit(f"No comp_*.json in {OUT_DIR} - run key_probe.py, or snapshot_diff.py --comp on old captures.")
    by_page: Dict[str, list] = {}
    for c in comps:
        by_page.setdefault(page_key(c[1]), []).append(c)
    done = 0
    for page in sorted(by_page):
        cs = sorted(by_page[page], key=lambda c: c[1].get("session") or c[0].name)
        lp, lrec, lids = cs[-1]
        others = [c for c in cs[:-1] if c[1].get("session") != lrec.get("session") and not (c[2] & lids and lids)]
        print(f"Page     : {page}")
        if not others:
            print(f"  only one client has a comp page of it ({masked(lids)}) - capture it for another client")
            print()
            continue
        pp, prec, pids = others[-1]
        print(f"Latest   : {lp.name}  [{masked(lids)}]  {len(lrec['nodes'])} nodes")
        print(f"Previous : {pp.name}  [{masked(pids)}]  {len(prec['nodes'])} nodes")
        if not (lids and pids):
            print("  WARNING: no PAN / GSTIN on one side - it may be the same client")
        if arrange_mode:
            arr_rows, arr_counts = arrange_rows(lrec, prec, page, "|".join(sorted(lids)) or "latest",
                                                "|".join(sorted(pids)) or "previous")
            for msg, k in arr_counts.items():
                print(f"  {msg:22s} {k:5d}")
            out = write_csv(OUT_DIR / f"compare_arrange_{lrec.get('session', '')}__{page_slug(page)}.csv",
                            ARRANGE_CSV_FIELDS, arr_rows)
            print(f"CSV      : {out.name}")
            print()
            done += 1
            continue
        if cards_mode:
            try:
                card_rows, card_counts = cards_rows(lrec, prec, page,
                                                   "|".join(sorted(lids)) or "latest", "|".join(sorted(pids)) or "previous")
            except ValueError as e:
                raise SystemExit(str(e))
            for msg, k in card_counts.items():
                print(f"  {msg:22s} {k:5d}")
            out = write_csv(OUT_DIR / f"compare_cards_{lrec.get('session', '')}__{page_slug(page)}.csv",
                            CARDS_CSV_FIELDS, card_rows)
            print(f"CSV      : {out.name}")
            print()
            done += 1
            continue
        page_rows = compare(lrec, prec)
        counts = Counter(r["status"] for r in page_rows)
        for st in STATUSES:
            print(f"  {st:18s} {counts.get(st, 0):5d}")
        matched = sum(1 for r in page_rows if r["key_matched"] in ("yes", "aligned"))
        print(f"  paired             {matched:5d} of {len(page_rows)}")
        data = [r for r in page_rows if r["status"] in (VARIABLE, SEMI_VARIABLE, VARIABLE_ALIGNMENT)]
        print(f"  data nodes {len(data)}, labelled {sum(1 for r in data if r['label'])}")
        flags = Counter(r["check"] for r in page_rows if r["check"] != "ok")
        for msg, k in flags.most_common():
            print(f"  check: {k:4d} x {msg}")
        out = write_csv(OUT_DIR / f"compare_comp_{lrec.get('session', '')}__{page_slug(page)}.csv", FIELDS,
                        [dict(r, page=page) for r in page_rows])
        print(f"CSV      : {out.name}")
        print()
        done += 1
    print("(the CSVs hold real page values - keep them on this PC)")
    return 0 if done else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Page memory view (default), or --two: two clients' maps of the same "
                                             "page link (or two single reads).")
    ap.add_argument("--two", action="store_true", help="the old mode: the latest client vs the previous one")
    ap.add_argument("--comp", action="store_true",
                    help="experiment 1: each link's latest comp page (snapshot_diff.py) vs another client's")
    ap.add_argument("--rho", type=float, default=None,
                    help="with --cards: try a recurrence cut-off before it is measured (an experiment knob; "
                         "the measured value goes in cards.RHO, plan docs/cards_rows/plan.md section 9)")
    ap.add_argument("--cards", action="store_true",
                    help="with --comp: compare the cards and rows of each page (plan docs/cards_rows/plan.md section 6)")
    ap.add_argument("--arrange", action="store_true",
                    help="with --comp: compare the arrangement of each page (arrange.py, design cards-solution.md section 3.6)")
    ap.add_argument("--latest", help="a key_probe_*.json - compare two single reads instead of client maps")
    ap.add_argument("--previous", help="a key_probe_*.json (default with --latest: the newest earlier read of that page)")
    ap.add_argument("--view", choices=("raw", "sgt"), default="raw",
                    help="raw (default): every element the probe read - the view production will read; "
                         "sgt: only what SGT's control view sees today")
    ap.add_argument("--align", choices=("on", "off"), default="on",
                    help="on (default): pair elements by content and shape (align.py); off: by exact key")
    args = ap.parse_args(argv)
    keys.VIEW = args.view
    global ALIGN
    ALIGN = args.align == "on"
    print(f"View     : {args.view}    Align: {args.align}")
    if args.comp:
        if args.rho is not None:
            cards.RHO = args.rho
            print(f"Rho      : {args.rho:g} (given on the command line, not measured)")
        return main_comp(cards_mode=args.cards, arrange_mode=args.arrange)
    if not (args.two or args.latest or args.previous):
        return main_memory()

    # One comparison per page link: (page, latest side's name, other side's name, rows).
    results: List[Tuple[str, str, str, List[Dict[str, Any]]]] = []
    if args.latest or args.previous:
        lp, pp = pick(args.latest, args.previous)
        latest = json.loads(lp.read_text(encoding="utf-8"))
        previous = json.loads(pp.read_text(encoding="utf-8"))
        results.append((page_key(latest), lp.name, pp.name, compare(latest, previous)))
        stamp = link_map.read_stamp(lp)
    else:
        maps = link_map.build_maps()
        if not maps:
            raise SystemExit(f"No captures or reads in {OUT_DIR} yet - run key_probe.py on a page first.")
        # The latest session, and every page link it visited.
        last = max(maps.values(), key=lambda m: m.reads[-1])
        session = last.client
        stamp = session.split()[-1]
        # One client per session: the PAN / GSTIN seen on ANY of its pages. Compared only with a
        # session of ANOTHER client - the same client twice would make all its data look fixed.
        ids: Dict[str, set] = {}
        for m in maps.values():
            ids.setdefault(m.client, set()).update(client_ids(m))
        me = ids.get(session, set())
        print(f"Client   : {masked(me)}")
        if not me:
            print("  WARNING: no PAN / GSTIN found in this capture - it cannot be told apart from the same")
            print("  client's earlier captures; visit a page that shows it (dashboard, profile) next time.")
        print()
        for lm in sorted((m for m in maps.values() if m.client == session), key=lambda m: m.reads[0]):
            same = [m for m in maps.values() if m.page == lm.page and m.client != session and ids[m.client] & me]
            others = [m for m in maps.values() if m.page == lm.page and m.client != session
                      and not (ids[m.client] & me)]
            if not others:
                print(f"Page     : {lm.page}")
                if same:
                    print(f"  only the SAME client ({masked(me)}) has it in earlier captures - capture it for another client")
                else:
                    print("  only this session has it - capture it for another client to compare")
                print()
                continue
            pm = max(others, key=lambda m: m.reads[-1])
            if same:
                print(f"  (skipped {len(same)} earlier capture(s) of the same client on {lm.page})")
            results.append((lm.page, f"{lm.client}: {len(lm.reads)} snapshot(s) merged",
                            f"{pm.client} [{masked(ids[pm.client])}]: {len(pm.reads)} snapshot(s) merged",
                            compare_flat(lm.to_flat(), pm.to_flat())))
        if not results:
            raise SystemExit("Nothing to compare yet.")

    for page, l_name, p_name, page_rows in results:
        print(f"Page     : {page}")
        print(f"Latest   : {l_name}")
        print(f"Previous : {p_name}")
        counts = Counter(r["status"] for r in page_rows)
        matched = sum(1 for r in page_rows if r["key_matched"] in ("yes", "aligned"))
        shifted = sum(1 for r in page_rows if r["key_matched"] == "aligned")
        for st in STATUSES:
            print(f"  {st:14s} {counts.get(st, 0):5d}")
        print(f"  paired         {matched:5d} of {len(page_rows)}   ({shifted} by alignment where the keys had shifted)")
        flags = Counter(r["check"] for r in page_rows if r["check"] != "ok")
        for msg, k in flags.most_common():
            print(f"  check: {k:4d} x {msg}")
        # One CSV per page link, named after the link, so the same page's files sort together.
        out = write_csv(OUT_DIR / f"compare_{stamp}__{page_slug(page)}.csv", FIELDS,
                        [dict(r, page=page) for r in page_rows])
        print(f"CSV      : {out.name}")
        print()
    print("(the CSVs hold real page values - keep them on this PC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
