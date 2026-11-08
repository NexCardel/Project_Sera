"""
core/sca/uid_node.py - the picked UID node: its fingerprint, and finding it again (docs/sca-uid-node-plan.md)
============================================================================================================
The picker saves one fingerprint per service (services.uid_node, JSON). The watcher finds that node on every
read of the service's UID page and reads ONLY it.

A UID page is the portal's login page that shows the password box with the UID already set: on a one-page
login the UID is the VALUE of the username box; on a two-page login page 2 shows it as text. So a page
without an on-screen password box is not (yet) the UID page: nothing is read from it and nothing warned.

Finding the node again ("fix the kind, not the case": the same stages for every portal). Each stage must
give EXACTLY one node, else the next stage runs; when none does the result is "missing" (no node) or
"ambiguous" (several) - never a guess:
  1. the strict key (core/sdis/keys.flatten: path of type#id.classes[n]) + the same label before it
  2. the HTML id + control type
  3. the loose key (types only) + control type + label
  4. control type + label
  5. the strict key alone
  6. control type + the same offset from the password box (+/- OFFSET_TOL px)
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from core.sca import uid_clean
from core.sdis import keys

FP_VERSION = 1
OFFSET_TOL = 40
ANCHOR_MAX = 60
CT_EDIT = 50004
BOX_TYPES = frozenset({50003, 50004})      # combo, edit: a box whose UID is what staff TYPED (its value)


def _norm_label(text: str) -> str:
    return " ".join(str(text or "").lower().split())[:ANCHOR_MAX]


def _flat(docs: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return keys.flatten({"docs": docs})


def _flat_index(docs, doc_k: int, node_i: int) -> int:
    return sum(len(d) for d in docs[:doc_k]) + node_i


def _anchor(flat: List[Dict[str, Any]], i: int) -> str:
    """The nearest text before node i in page order, in the same doc (its label, usually)."""
    doc = flat[i]["key"].split(" / ", 1)[0]
    for j in range(i - 1, -1, -1):                     # pre-order: nothing before i is inside i
        e = flat[j]
        if e["key"].split(" / ", 1)[0] != doc:
            break
        if e["node"].get("ctype") == CT_EDIT:          # a box's name is its own label, not page text
            continue
        t = _norm_label(e["node"].get("name"))
        if t:
            return t
    return ""


def _pw_rect(flat: List[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
    for e in flat:
        n = e["node"]
        if n.get("pw") and n.get("rect"):
            return tuple(n["rect"])
    return None


def has_password_box(docs) -> bool:
    return any(n.get("pw") for d in docs for n in d)


def node_text(node: Dict[str, Any], field: str) -> str:
    return str(node.get("value") if field == "value" else node.get("name") or "")


def is_box(node: Dict[str, Any]) -> bool:
    return node.get("ctype") in BOX_TYPES


def pick_field(node: Dict[str, Any]) -> str:
    """Where the UID is: a box's VALUE, always (its name is only its label, "Username": a box picked while
    still empty must not be saved as that label); any other node's name."""
    return "value" if is_box(node) else "name"


def fingerprint(docs, doc_k: int, node_i: int, rule: str = "") -> Dict[str, Any]:
    """The fingerprint of node node_i of doc doc_k (indexes as the render page gives them)."""
    flat = _flat(docs)
    i = _flat_index(docs, doc_k, node_i)
    e = flat[i]
    n = e["node"]
    field = pick_field(n)
    text = node_text(n, field)
    rule = rule if rule in uid_clean.RULES else uid_clean.auto_rule(text)
    fp = {"v": FP_VERSION, "key": e["key"], "loose": e["loose"], "ctype": n.get("ctype"),
          "type": e["type"], "id": keys.stable_id(n.get("id", "")), "cls": e["cls"], "field": field,
          "anchor": _anchor(flat, i), "rule": rule, "pw_dx": None, "pw_dy": None}
    pw = _pw_rect(flat)
    if pw and n.get("rect"):
        fp["pw_dx"], fp["pw_dy"] = n["rect"][0] - pw[0], n["rect"][1] - pw[1]
    return fp


def dumps(fp: Dict[str, Any]) -> str:
    return json.dumps(fp, sort_keys=True, separators=(",", ":"))


def loads(raw: Any) -> Optional[Dict[str, Any]]:
    try:
        fp = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except ValueError:
        return None
    return fp if isinstance(fp, dict) and fp.get("key") and fp.get("ctype") is not None else None


def find(docs, fp: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """(node, "found") | (None, "missing" | "ambiguous")."""
    flat = _flat(docs)
    ctype = fp.get("ctype")
    same_type = [i for i, e in enumerate(flat) if e["node"].get("ctype") == ctype and not e["node"].get("pw")]
    anchors: Dict[int, str] = {}

    def anchor(i):
        if i not in anchors:
            anchors[i] = _anchor(flat, i)
        return anchors[i]

    want_anchor = fp.get("anchor") or ""
    pw = _pw_rect(flat)

    def near_pw(i):
        r = flat[i]["node"].get("rect")
        if not (pw and r and fp.get("pw_dx") is not None):
            return False
        return (abs(r[0] - pw[0] - fp["pw_dx"]) <= OFFSET_TOL and abs(r[1] - pw[1] - fp["pw_dy"]) <= OFFSET_TOL)

    stages = [
        lambda i: flat[i]["key"] == fp["key"] and anchor(i) == want_anchor,
        lambda i: bool(fp.get("id")) and keys.stable_id(flat[i]["node"].get("id", "")) == fp["id"],
        lambda i: flat[i]["loose"] == fp.get("loose") and anchor(i) == want_anchor,
        lambda i: bool(want_anchor) and anchor(i) == want_anchor,
        lambda i: flat[i]["key"] == fp["key"],
        near_pw,
    ]
    seen_many = False
    for test in stages:
        hits = [i for i in same_type if test(i)]
        if len(hits) == 1:
            return flat[hits[0]]["node"], "found"
        seen_many = seen_many or len(hits) > 1
    return None, ("ambiguous" if seen_many else "missing")


def read_text(node: Dict[str, Any], fp: Dict[str, Any]) -> str:
    """The text the watcher reads from the found node: a box's value, whatever the fingerprint says (a pick
    saved before 2026-11-07 could point a box at its label), else the fingerprint's field."""
    return node_text(node, "value" if is_box(node) else fp.get("field", "name"))


def uid_of(node: Dict[str, Any], fp: Dict[str, Any]) -> str:
    return uid_clean.clean(read_text(node, fp), fp.get("rule", ""))


def fast_node(found: Optional[Dict[str, Any]], fp: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    """Reads UiaReader.find_fast's result: (the node when exactly one node with the id and the type is on
    screen, else None = do a full read; number of password boxes)."""
    if not found:
        return None, 0
    hits = [h for h in found.get("hits") or [] if h.get("ctype") == fp.get("ctype")]
    return (hits[0] if len(hits) == 1 else None), int(found.get("pw") or 0)


def fast_uid(found: Optional[Dict[str, Any]], fp: Dict[str, Any]) -> Tuple[Optional[str], int]:
    """fast_node, read: (uid or '' for the one node, None = do a full read; number of password boxes)."""
    n, pw = fast_node(found, fp)
    return (None if n is None else uid_of(n, fp)), pw
