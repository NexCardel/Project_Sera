"""
core/sdis/relevance.py - relevance ranking by occurrences + slots (SDIS Part J)
===============================================================================
Counting only, no AI. What counts, and how:
  - a node counts when its status is variable, semi-variable or variable_alignment (data, R2); fixed,
    furniture, ambiguous, waiting, retired, composite and rejected variable_alignment never do;
  - one client = one vote per page (R8): a node's page share = clients that have it (sure
    observations) / clients in that PageMemory, however many rows or snapshots hold it;
  - datapoints are joined across pages and browsers by key = (label lower-cased without a trailing
    ':' or spaces, value type); a node with no label is its own datapoint, key = (link, shape);
  - relevance = sum of the page shares; relevance_pct scales it to the best datapoint; sure_pct
    = distinct clients / FULL_SURE_CLIENTS (D3, capped at 100);
  - slots: slot_type_pct = share of the most common value type among all the datapoint's values;
    surprise = that share is >= 90% and some value has another type;
  - a node whose text is a slide position ("Site Map 3 of 4") is left out (noise.is_slide_position);
  - D8: a picked key counts x1.5, a rejected key is left out.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.sdis.keys import CHOICE_CTYPES
from core.sdis.labels import value_type
from core.sdis.noise import is_slide_position

N = 2                      # clients that confirm a node (D3)
FULL_SURE_CLIENTS = 5      # clients that give Sure 100% (D3)
SURPRISE_SHARE = 90
PICKED_FACTOR = 1.5
_DATA_STATUSES = ("variable", "semi-variable", "variable_alignment")


@dataclass
class Datapoint:
    key: Tuple[str, str]
    label: str
    value_type: str
    relevance_pct: int
    sure_pct: int
    pages: List[Tuple[str, int, str, float]]       # (link, screen, browser, share)
    nodes: List[Tuple[int, int]]                   # (memory index, node id)
    slot_type_pct: int
    surprise: bool
    suggested_class: Optional[str] = None          # Part S.1, set by classes.annotate
    class_reason: str = ""
    status: str = ""


def clean_label(label: str) -> str:
    return re.sub(r"[\s:]+$", "", (label or "").lower()).strip()


def _type(nd: Dict[str, Any], text: str) -> str:
    return "control" if nd.get("ctype") in CHOICE_CTYPES else value_type(text)


def _norm(key: Any) -> Any:
    if isinstance(key, tuple) and len(key) == 2 and isinstance(key[0], str):
        return (clean_label(key[0]), key[1])
    return key


def _sure_clients(nd: Dict[str, Any]) -> Dict[str, Any]:
    return {c: v for c, v in nd.get("clients", {}).items() if v.get("sure", True)}


def _node_type(nd: Dict[str, Any]) -> str:
    """The most common type of the clients' last values (ties: the type name) - the same on every run."""
    count = Counter(_type(nd, v["texts"][-1] if v.get("texts") else "") for v in _sure_clients(nd).values())
    return min(count, key=lambda t: (-count[t], t)) if count else "word"


def datapoints(memories: Iterable[Any], rejected: Iterable[Any] = (), picked: Iterable[Any] = ()) -> List[Datapoint]:
    """Every datapoint of the given PageMemory objects, joined by key, best relevance first.
    rejected / picked are datapoint keys (label, value type) or (link, shape)."""
    mems = list(memories)
    rejected_keys = {_norm(k) for k in rejected}
    picked_keys = {_norm(k) for k in picked}
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for mi, m in enumerate(mems):
        rej = getattr(m, "rejected", None) or set()
        for nid, nd in enumerate(m.nodes):
            if not nd.get("text") or m.status(nid) not in _DATA_STATUSES:
                continue
            votes = _sure_clients(nd)
            if not votes:
                continue
            if m.base_verdict(nid) == "same for all clients":      # a rejected variable_alignment is not data
                text = next(iter(v["texts"][-1] for v in votes.values() if v.get("texts")), "")
                if (m.link, nd["shape"], text) in rej or (m.page, nd["shape"], text) in rej:
                    continue
            if is_slide_position(nd["text"]):
                continue
            label = m.label(nid)
            key = (clean_label(label), _node_type(nd)) if clean_label(label) else (m.link, nd["shape"])
            if key in rejected_keys:
                continue
            g = groups.setdefault(key, {"labels": Counter(), "nodes": [], "clients": {}, "values": []})
            if clean_label(label):
                g["labels"][label.rstrip(": \t").strip()] += 1
            g["nodes"].append((mi, nid))
            g["clients"].setdefault(mi, set()).update(votes)
            g["values"].extend(_type(nd, t) for v in votes.values() for t in v.get("texts", []))

    raw = []
    for key, g in groups.items():
        pages = []
        for mi in sorted(g["clients"]):
            m = mems[mi]
            pages.append((m.link, m.screen, m.browser, len(g["clients"][mi]) / max(1, len(m.clients))))
        relevance = sum(p[3] for p in pages) * (PICKED_FACTOR if key in picked_keys else 1)
        who: Set[str] = set().union(*g["clients"].values())
        types = Counter(g["values"])
        top = min(types, key=lambda t: (-types[t], t)) if types else ""
        slot_pct = round(100 * types[top] / len(g["values"])) if types else 0
        label = min(g["labels"], key=lambda t: (-g["labels"][t], t)) if g["labels"] else ""
        vtype = key[1] if label else top
        st_counter = Counter()
        for mi, nid in g["nodes"]:
            try:
                if mi < len(mems):
                    s = mems[mi].status(nid)
                    if s:
                        st_counter[s] += 1
            except Exception:
                pass
        st_str = ", ".join(sorted(st_counter.keys())) if st_counter else ""
        raw.append((relevance, Datapoint(
            key=key, label=label, value_type=vtype, relevance_pct=0,
            sure_pct=round(100 * min(1.0, len(who) / FULL_SURE_CLIENTS)), pages=pages, nodes=g["nodes"],
            slot_type_pct=slot_pct, surprise=bool(types) and slot_pct >= SURPRISE_SHARE and len(types) > 1,
            status=st_str)))
    best = max((r for r, _ in raw), default=0)
    out = []
    for relevance, dp in sorted(raw, key=lambda x: (-x[0], -x[1].sure_pct, str(x[1].key))):
        dp.relevance_pct = round(100 * relevance / best) if best else 0
        out.append(dp)
    return out
