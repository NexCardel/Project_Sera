"""
core/sdis/classes.py - class suggestion: "what does this value stay the same with?" (SDIS Part S.1)
=================================================================================================
Counting only, no AI. For each datapoint and each client (Part D owners; undecided ones never reach a
memory), the value history (Part B) gives observations (day, period, value):
  profile  the value is the same on every observation, seen on 2+ days or in 2+ periods
  dataset  it differs between the client's periods but is the same inside each period (2+ periods)
  info     otherwise (it changes within a period, or has no period to compare)
  none     fewer than 2 observations, or a constant value seen on one day in at most one period
An observation is one read of the page; the node's value at that read is its latest history item at
or before the read, from the read its history starts. The period of a read is the period-shaped text
shown ONCE on it, else the Part N masked link segment when it is period-shaped, else none (the read
is skipped by the dataset test). One client = one vote; a class is suggested only when
CLASS_AGREE of the clients with evidence give it. A user's move overrides.
"""

from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core.sdis.history import day_of
from core.sdis.labels import is_period

CLASS_AGREE = 0.9
CLASSES = ("profile", "dataset", "info")
MASK = "{v}"


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower().strip(" .,;:")


def read_period(raw_link: str, resolved_link: str, texts: Iterable[str]) -> str:
    """The period of one read (normalised), or ''."""
    shown = [t for t in texts if t and is_period(t)]
    if len(shown) == 1:
        return _norm(shown[0])
    from core.sdis.links import parse_link
    raw, res = parse_link(raw_link).segments, parse_link(resolved_link).segments
    if len(raw) != len(res):
        return ""
    found = [raw[i] for i in range(len(res)) if res[i] == MASK and raw[i] != MASK and is_period(raw[i])]
    return _norm(found[0]) if len(found) == 1 else ""


def _observations(mems: List[Any], dp: Any) -> Dict[str, List[Tuple[str, str, str]]]:
    """client -> [(day, period, value)] over every node of the datapoint."""
    out: Dict[str, List[Tuple[str, str, str]]] = {}
    for mi, nid in dp.nodes:
        mem = mems[mi]
        for client, c in mem.nodes[nid].get("clients", {}).items():
            hist = sorted((h[0], h[1]) for h in c.get("history") or [] if len(h) == 2 and h[1])
            if not hist or not c.get("sure", True):
                continue
            reads = (getattr(mem, "read_info", None) or {}).get(client) or [
                {"stamp": s, "period": ""} for s, _t in hist]
            obs = out.setdefault(client, [])
            for r in reads:
                if r["stamp"] < hist[0][0]:
                    continue
                text = [t for s, t in hist if s <= r["stamp"]][-1]
                obs.append((day_of(r["stamp"]), r.get("period", ""), _norm(text)))
    return out


def client_class(obs: List[Tuple[str, str, str]]) -> Optional[str]:
    """One client's class from its observations, or None for not enough evidence."""
    if len(obs) < 2:
        return None
    values = {v for _d, _p, v in obs}
    days = {d for d, _p, _v in obs if d}
    periods = {p for _d, p, _v in obs if p}
    if len(values) == 1:
        return "profile" if len(days) >= 2 or len(periods) >= 2 else None
    by_period: Dict[str, set] = {}
    for _d, p, v in obs:
        if p:
            by_period.setdefault(p, set()).add(v)
    if (len(by_period) >= 2 and all(len(s) == 1 for s in by_period.values())
            and len(set().union(*by_period.values())) >= 2):
        return "dataset"
    return "info"


def _reason(cls: str, k: int, n: int) -> str:
    what = {"profile": "same on every day and period",
            "dataset": "same within a period, different between periods",
            "info": "changes within a period or has no period"}[cls]
    return f"{what} for {k} of {n} clients"


def suggest(datapoints: Iterable[Any], memory_state: Iterable[Any],
            moves: Optional[Mapping[Any, str]] = None) -> Dict[Any, Tuple[Optional[str], str]]:
    """{datapoint key: (class or None, reason)}. memory_state: the PageMemory objects the datapoints
    came from. moves: {datapoint key: class} the user chose; they override the suggestion."""
    from core.sdis.relevance import _norm as norm_key
    mems = list(memory_state)
    moved = {norm_key(k): v for k, v in (moves or {}).items() if v in CLASSES}
    out: Dict[Any, Tuple[Optional[str], str]] = {}
    for dp in datapoints:
        if dp.key in moved:
            out[dp.key] = (moved[dp.key], "moved by the user")
            continue
        votes = Counter(c for c in (client_class(o) for o in _observations(mems, dp).values()) if c)
        n = sum(votes.values())
        if not n:
            out[dp.key] = (None, "not enough evidence")
            continue
        top = min(votes, key=lambda c: (-votes[c], c))
        if votes[top] / n >= CLASS_AGREE - 1e-9:
            out[dp.key] = (top, _reason(top, votes[top], n))
        else:
            out[dp.key] = (None, "clients disagree")
    return out


def annotate(datapoints: List[Any], memory_state: Iterable[Any],
             moves: Optional[Mapping[Any, str]] = None) -> Dict[Any, Tuple[Optional[str], str]]:
    """suggest(), and set suggested_class / class_reason on every datapoint."""
    result = suggest(datapoints, memory_state, moves)
    for dp in datapoints:
        dp.suggested_class, dp.class_reason = result[dp.key]
    return result
