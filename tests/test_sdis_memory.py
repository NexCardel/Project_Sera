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


def test_link_map_browser_attribute_from_first_read():
    """LinkMap gets attribute browser from the first read."""
    a = _load("A")
    rec_chrome = dict(a, browser="chrome")
    m = link_map.LinkMap("client1", "portal.gov.in/page")
    assert m.browser == ""
    m.add(rec_chrome, "1")
    assert m.browser == "chrome"

    # Subsequent reads do not change the initial browser attribute
    rec_edge = dict(a, browser="msedge")
    m.add(rec_edge, "2")
    assert m.browser == "chrome"


def test_page_memory_browser_attribute():
    """PageMemory gets attribute browser and parses it correctly."""
    pm = memory.PageMemory("portal.gov.in/page", 2, browser="chrome")
    assert pm.browser == "chrome"
    assert pm.link == "portal.gov.in/page"

    pm2 = memory.PageMemory("portal.gov.in/page [firefox]", 2)
    assert pm2.browser == "firefox"
    assert pm2.link == "portal.gov.in/page"

    pm3 = memory.PageMemory("portal.gov.in/page [firefox] [screen 2]", 2)
    assert pm3.browser == "firefox"
    assert pm3.screen == 2
    assert pm3.link == "portal.gov.in/page"


def test_two_fixture_reads_different_browsers_produce_two_memories_both_empty_one():
    """Two fixture reads of one link marked chrome and firefox -> two memories; both '' -> one."""
    a = _load("A")
    b = _load("B")

    # 1. Both marked '' -> one memory
    rec_a_empty = dict(a, browser="")
    rec_b_empty = dict(b, browser="")
    sources_empty = [
        ("20261001_100000", "client A", "portal.gov.in/page", rec_a_empty),
        ("20261001_100001", "client B", "portal.gov.in/page", rec_b_empty),
    ]
    cm_empty = memory.client_maps(sources=sources_empty)
    assert len(cm_empty) == 1
    assert "portal.gov.in/page" in cm_empty
    assert len(cm_empty["portal.gov.in/page"]) == 2

    # Verify PageMemory built from cm_empty has browser ''
    order_empty = ["client A", "client B"]
    mem_empty = memory.build("portal.gov.in/page", cm_empty["portal.gov.in/page"], order_empty, 2)
    assert mem_empty.browser == ""

    # 2. Marked chrome and firefox -> two memories
    rec_a_chrome = dict(a, browser="chrome")
    rec_b_firefox = dict(b, browser="firefox")
    sources_diff = [
        ("20261001_100000", "client A", "portal.gov.in/page", rec_a_chrome),
        ("20261001_100001", "client B", "portal.gov.in/page", rec_b_firefox),
    ]
    cm_diff = memory.client_maps(sources=sources_diff)
    assert len(cm_diff) == 2
    assert "portal.gov.in/page [chrome]" in cm_diff
    assert "portal.gov.in/page [firefox]" in cm_diff
    assert len(cm_diff["portal.gov.in/page [chrome]"]) == 1
    assert len(cm_diff["portal.gov.in/page [firefox]"]) == 1

    # Verify PageMemory built from each has respective browser attribute
    mem_chrome = memory.build("portal.gov.in/page [chrome]", cm_diff["portal.gov.in/page [chrome]"], ["client A"], 2)
    assert mem_chrome.browser == "chrome"
    mem_firefox = memory.build("portal.gov.in/page [firefox]", cm_diff["portal.gov.in/page [firefox]"], ["client B"], 2)
    assert mem_firefox.browser == "firefox"


def test_key_probe_browser_name_of_hwnd():
    """Verify key_probe.browser_name_of_hwnd returns empty string on invalid/0 hwnd."""
    import key_probe
    assert key_probe.browser_name_of_hwnd(0) == ""

