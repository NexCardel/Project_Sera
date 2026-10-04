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

LABEL_LOOKBACK = 6


def changes_within_client(node: Dict[str, Any]) -> bool:
    """True if any client has 2+ distinct texts for this node."""
    cl = node.get("clients", {})
    return any(len(c.get("texts", [])) > 1 for c in cl.values())


def changes_with_time(node: Dict[str, Any]) -> bool:
    """True when there are 2+ days, every day's set of texts from every client's
    history has exactly one text, and the texts differ between days.

    For each client and each day seen, takes the LAST text that client had on that day.
    """
    day_texts: Dict[str, Set[str]] = {}
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

    if len(day_texts) < 2:
        return False
    if any(len(s) != 1 for s in day_texts.values()):
        return False
    all_texts = {next(iter(s)) for s in day_texts.values()}
    return len(all_texts) > 1


def probably_furniture(mem: Any, nid: int) -> bool:
    """True if the node's verdict is 'differs between clients', labels.value_type(text)
    is 'sentence' or 'label', and it has NO label.

    Until W3-1 brings real labels, 'no label' = none of the 6 nodes before it in
    mem.order is confirmed 'same for all clients' with a letter in its text.
    """
    nd = mem.nodes[nid]
    cl = nd.get("clients", {})

    # The node's verdict is 'differs between clients'
    if nd.get("composite"):
        return False
    if changes_within_client(nd):
        return False
    if changes_with_time(nd):
        return False
    if not mem.confirmed(nid):
        return False
    last = {c["texts"][-1] if c.get("texts") else "" for c in cl.values()}
    if len(last) <= 1:
        return False

    # labels.value_type(text) is 'sentence' or 'label'
    text = nd.get("text") or ""
    if not text:
        text = next((c["texts"][-1] for c in cl.values() if c.get("texts")), "")
    vtype = labels.value_type(text)
    if vtype not in ("sentence", "label"):
        return False

    # Check for preceding label:
    # TODO(W3-1): replace heuristic lookback with real labels when W3-1 brings them.
    if nid not in mem.order:
        return True
    idx = mem.order.index(nid)
    lookback_start = max(0, idx - LABEL_LOOKBACK)
    lookback = mem.order[lookback_start:idx]
    for prev_nid in lookback:
        if mem.confirmed(prev_nid) and mem.verdict(prev_nid) == "same for all clients":
            prev_nd = mem.nodes[prev_nid]
            prev_text = prev_nd.get("text") or ""
            if not prev_text:
                prev_text = next((c["texts"][-1] for c in prev_nd.get("clients", {}).values() if c.get("texts")), "")
            if any(ch.isalpha() for ch in prev_text):
                return False

    return True
