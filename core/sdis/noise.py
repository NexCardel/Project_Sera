"""
core/sdis/noise.py - noise detection over time and furniture heuristics
========================================================================
Implements Blueprint Part C (Noise over time):
  1. changes_within_client: one client, several texts (rotating notice/banner).
  2. changes_with_time: differs between days, but on each day all clients agreed.
  3. probably_furniture: differs between clients, sentence or label shaped, and
     has no label beside it.
"""

from typing import Any, Dict, List, Optional, Set

from core.sdis import history, labels


def changes_within_client(node: Dict[str, Any]) -> bool:
    """True if any client has 2+ distinct texts for this node."""
    cl = node.get("clients", {})
    return any(len(c.get("texts", [])) > 1 for c in cl.values())


def changes_with_time(node: Dict[str, Any]) -> bool:
    """True when every day's set of texts from every client's history has exactly one text,
    and the texts differ between days WITNESSED by 2+ clients. A day only one client was seen
    on proves nothing: clients captured on different days with their own data are not noise.

    For each client and each day seen, takes the LAST text that client had on that day.
    """
    day_texts: Dict[str, Set[str]] = {}
    day_clients: Dict[str, int] = {}
    for c in node.get("clients", {}).values():
        hist = c.get("history") or []
        # Sort by timestamp to ensure chronological order for the client
        sorted_hist = sorted(
            hist,
            key=lambda item: str(item[0]) if isinstance(item, (list, tuple)) and len(item) > 0
            else (str(item.get("stamp", "")) if isinstance(item, dict) else "")
        )
        client_days: Dict[str, str] = {}
        for item in sorted_hist:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                stamp, text = item[0], item[1]
            elif isinstance(item, dict):
                stamp, text = item.get("stamp"), item.get("text")
            else:
                continue
            day = history.day_of(stamp)
            if day:
                client_days[day] = str(text) if text is not None else ""
        for day, text in client_days.items():
            day_texts.setdefault(day, set()).add(text)
            day_clients[day] = day_clients.get(day, 0) + 1

    if any(len(s) != 1 for s in day_texts.values()):
        return False
    witnessed = {next(iter(s)) for d, s in day_texts.items() if day_clients[d] >= 2}
    return len(witnessed) > 1


def probably_furniture(mem: Any, nid: int) -> bool:
    """True if the node's base verdict is 'differs between clients', EVERY voting client's
    text is labels.value_type 'sentence' or 'label' (so the client order cannot change it),
    and it has NO label (mem.label: table column, or a shared text in its box / just before it).
    """
    if mem.base_verdict(nid) != "differs between clients":
        return False
    cl = mem.nodes[nid].get("clients", {})
    texts = [c["texts"][-1] for c in cl.values() if c.get("sure", True) and c.get("texts")]
    if not texts or any(labels.value_type(t) not in ("sentence", "label") for t in texts):
        return False
    return mem.label(nid) == ""
