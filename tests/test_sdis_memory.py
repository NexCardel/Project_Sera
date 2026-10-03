"""
SDIS pre-dev: R8 - count per page and per client, never per snapshot.

  * link_map.py merges one client's snapshots by alignment (shape + face), so a re-render that
    renumbers every key, or moves a block, adds NOTHING to the map (P16);
  * link_map.group_clients joins sessions that share a PAN / GSTIN into one client (P17);
  * memory.py gives one vote per client, and the client order changes no verdict.

Uses the two fictional clients in tests/class_diff_align/ (see test_class_diff_align.py).
"""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "class_diff_align"
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import keys  # noqa: E402
import link_map  # noqa: E402
import memory  # noqa: E402


@pytest.fixture(autouse=True)
def raw_view(monkeypatch):
    monkeypatch.setattr(keys, "VIEW", "raw")


def _load(c):
    return json.loads((FIX / f"client_{c}.json").read_text(encoding="utf-8"))


def _doc(rec):
    return next(d for d in rec["docs"] if d)


def _subtrees(doc):
    """The doc's top-level blocks, each a list of node indexes (a root and everything under it)."""
    blocks, cur = [], None
    for i, n in enumerate(doc):
        if n["parent"] < 0:
            cur = [i]
            blocks.append(cur)
        else:
            cur.append(i)
    return blocks


def _rebuild(rec, order, extra_first=None):
    """The same page with its top-level blocks in `order`, and optionally a new block first -
    a re-render: every key under a shifted block gets a new counter."""
    doc = _doc(rec)
    blocks = _subtrees(doc)
    new_doc = []
    if extra_first:
        new_doc.extend(copy.deepcopy(extra_first))
    for b in order:
        old_to_new = {}
        for i in blocks[b]:
            n = copy.deepcopy(doc[i])
            old_to_new[i] = len(new_doc)
            n["parent"] = old_to_new[n["parent"]] if n["parent"] >= 0 else -1
            new_doc.append(n)
    out = copy.deepcopy(rec)
    out["docs"] = [new_doc if d else d for d in rec["docs"]]      # same document slot
    return out


def _group(name):
    return {"parent": -1, "depth": 0, "ctype": 50026, "name": name, "rect": [0, 0, 100, 10], "id": "",
            "cls": "", "type_name": "Group", "sgt": True}


def _texts(m):
    return sorted(e["text"] for e in m.to_flat() if e["text"])


def test_same_snapshot_twice_adds_nothing():
    a = _load("A")
    m = link_map.LinkMap("c", "p")
    m.add(a, "1")
    before = _texts(m)
    assert m.add(a, "2")["new"] == set()
    assert _texts(m) == before


def test_rerender_with_renumbered_keys_adds_only_the_new_block():
    a = _load("A")
    n = len(_subtrees(_doc(a)))
    m = link_map.LinkMap("c", "p")
    m.add(a, "1")
    before = _texts(m)
    # A new top-level Group first: every later top-level Group's counter shifts by one.
    res = m.add(_rebuild(a, range(n), [_group("Server maintenance tonight")]), "2")
    assert len(res["new"]) == 1
    assert _texts(m) == sorted(before + ["Server maintenance tonight"])


def test_moved_block_is_not_stored_twice():
    a = _load("A")
    n = len(_subtrees(_doc(a)))
    m = link_map.LinkMap("c", "p")
    m.add(a, "1")
    before = _texts(m)
    order = [n - 1] + list(range(n - 1))          # the last block (the footer) moved to the top
    m.add(_rebuild(a, order), "2")
    assert _texts(m) == before


def test_group_clients_chains_shared_ids():
    owners = link_map.group_clients({
        "capture 1": {"gstin:X"}, "capture 2": {"gstin:X", "pan:P"}, "capture 3": {"pan:P"},
        "capture 4": set(), "capture 5": {"gstin:Y"}})
    assert owners["capture 1"] == owners["capture 2"] == owners["capture 3"] == "client 1"
    assert owners["capture 5"] == "client 2"
    assert owners["capture 4"] == "unidentified capture 4"


def _map(rec, client):
    m = link_map.LinkMap(client, "p")
    m.add(rec, "1")
    return m


def test_one_client_is_one_vote():
    a = _load("A")
    mem = memory.PageMemory("p", 2)
    mem.add(_map(a, "A").to_flat(), "A")
    mem.add(_map(a, "A").to_flat(), "A")           # the same client again
    assert not any(mem.confirmed(i) for i in range(len(mem.nodes)))


def test_client_order_changes_no_verdict():
    maps = {"A": _map(_load("A"), "A"), "B": _map(_load("B"), "B")}
    fwd = memory.build("p", maps, ["A", "B"], 2).summary()
    rev = memory.build("p", maps, ["B", "A"], 2).summary()
    assert fwd == rev
    # Every label of the fictional page is template for both clients.
    assert fwd[("confirmed", "same for all clients")] > 0
    assert fwd[("confirmed", "differs between clients")] > 0
