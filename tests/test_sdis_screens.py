"""
tests/test_sdis_screens.py - W1-2 Part G: screens (one link, several pages) by weighted matching
==============================================================================================
Tests:
(a) A then A plus one extra top-level block -> one screen
(b) a one-node shell with no text, then A -> one screen
(c) A, then a page that keeps only A's first and last top-level blocks and replaces the rest with new blocks of new text -> two screens
(d) covers() maths on a tiny hand-made example
"""

import copy
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "class_diff_align"
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import keys  # noqa: E402
from core.sdis import link_map, memory
from core.sdis.screens import SCREEN_MIN, covers, link_weights, same_screen


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


def _rebuild(rec, order, extra_first=None, extra_blocks=None):
    """The same page with its top-level blocks in `order`, and optionally extra blocks."""
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
    if extra_blocks:
        new_doc.extend(copy.deepcopy(extra_blocks))
    out = copy.deepcopy(rec)
    out["docs"] = [new_doc if d else d for d in rec["docs"]]
    return out


def _group(name):
    return {"parent": -1, "depth": 0, "ctype": 50026, "name": name, "rect": [0, 0, 100, 10], "id": "",
            "cls": "", "type_name": "Group", "sgt": True}


def test_covers_math_hand_made():
    """(d) covers() maths on a tiny hand-made example."""
    flat_old = [
        {"key": "D1 / Text[1]", "text": "Heading"},
        {"key": "D1 / Text[2]", "text": "Common"},
        {"key": "D1 / Text[3]", "text": ""},  # No text -> weight 0
    ]
    flat_new = [
        {"key": "D1 / Text[1]", "text": "Heading"},
        {"key": "D1 / Text[2]", "text": "Replaced"},
        {"key": "D1 / Text[3]", "text": ""},  # No text -> weight 0
    ]
    # Hand-crafted weights
    weights = {
        ("D1 / Text", "Heading"): 2.0,
        ("D1 / Text", "Common"): 3.0,
        ("D1 / Text", "Replaced"): 4.0,
    }
    # Index 0 in new ("Heading") matches index 0 in old ("Heading")
    pairs = {0: 0}

    # Old side:
    # node 0: weight 2.0 (matched)
    # node 1: weight 3.0 (unmatched)
    # node 2: weight 0.0 (no text)
    # total old = 5.0, matched old = 2.0 -> cover_old = 2.0 / 5.0 = 0.4

    # New side:
    # node 0: weight 2.0 (matched)
    # node 1: weight 4.0 (unmatched)
    # node 2: weight 0.0 (no text)
    # total new = 6.0, matched new = 2.0 -> cover_new = 2.0 / 6.0 = 1/3

    cover_new, cover_old = covers(flat_new, flat_old, pairs, weights)
    assert cover_new == pytest.approx(1.0 / 3.0)
    assert cover_old == pytest.approx(0.4)
    assert not same_screen(cover_new, cover_old)  # max(1/3, 0.4) = 0.4 < 0.5

    # If both nodes 0 and 1 matched:
    pairs_both = {0: 0, 1: 1}
    flat_new_same = [
        {"key": "D1 / Text[1]", "text": "Heading"},
        {"key": "D1 / Text[2]", "text": "Common"},
        {"key": "D1 / Text[3]", "text": ""},
    ]
    c_new, c_old = covers(flat_new_same, flat_old, pairs_both, weights)
    assert c_new == pytest.approx(1.0)
    assert c_old == pytest.approx(1.0)
    assert same_screen(c_new, c_old)

    # Total weight < 1e-9 gives 1.0 (empty or no text)
    empty_flat = [{"key": "D1 / Group[1]", "text": ""}]
    c_new_emp, c_old_emp = covers(empty_flat, flat_old, {}, weights)
    assert c_new_emp == 1.0
    assert c_old_emp == 0.0
    assert same_screen(c_new_emp, c_old_emp)


def test_link_weights_formula():
    """Verify link_weights formula: log((1 + links) / links_showing_it)."""
    # 2 distinct page links: "p1", "p2"
    rec1 = {"docs": [[{"parent": -1, "depth": 0, "ctype": 50026, "name": "Furniture", "rect": [0,0,1,1], "id": "", "cls": "", "type_name": "Group", "sgt": True},
                      {"parent": -1, "depth": 0, "ctype": 50026, "name": "OnlyP1", "rect": [0,0,1,1], "id": "", "cls": "", "type_name": "Group", "sgt": True}]]}
    rec2 = {"docs": [[{"parent": -1, "depth": 0, "ctype": 50026, "name": "Furniture", "rect": [0,0,1,1], "id": "", "cls": "", "type_name": "Group", "sgt": True},
                      {"parent": -1, "depth": 0, "ctype": 50026, "name": "OnlyP2", "rect": [0,0,1,1], "id": "", "cls": "", "type_name": "Group", "sgt": True}]]}
    sources = [
        ("1", "c1", "p1", rec1),
        ("2", "c2", "p2", rec2),
    ]
    w = link_weights(sources)
    assert w.links == 2
    # "Furniture" on both links (2 links showing it): log((1 + 2) / 2) = log(1.5)
    assert w[("D1 / Group", "Furniture")] == pytest.approx(math.log(1.5))
    # "OnlyP1" on 1 link: log((1 + 2) / 1) = log(3)
    assert w[("D1 / Group", "OnlyP1")] == pytest.approx(math.log(3.0))
    # New text not in dict gets log(1 + links) = log(3)
    assert w[("D1 / Group", "BrandNewText")] == pytest.approx(math.log(3.0))


def test_a_then_a_plus_extra_block_is_one_screen():
    """(a) A then A plus one extra top-level block -> one screen."""
    a = _load("A")
    n = len(_subtrees(_doc(a)))
    a_extra = _rebuild(a, range(n), extra_blocks=[_group("Extra Notification Banner")])

    sources = [
        ("20261001_100000", "client1", "portal.gov.in/page", a),
        ("20261001_100001", "client1", "portal.gov.in/page", a_extra),
    ]
    maps = link_map.build_maps(sources=sources)
    assert len(maps) == 1
    assert ("client1", "portal.gov.in/page") in maps
    lm = maps[("client1", "portal.gov.in/page")]
    assert lm.screen == 1
    assert lm.link == "portal.gov.in/page"
    assert len(lm.reads) == 2


def test_one_node_shell_then_a_is_one_screen():
    """(b) a one-node shell with no text, then A -> one screen."""
    a = _load("A")
    shell = {"docs": [[{"parent": -1, "depth": 0, "ctype": 50026, "name": "", "rect": [0, 0, 100, 10],
                        "id": "", "cls": "", "type_name": "Group", "sgt": True}]]}

    sources = [
        ("20261001_100000", "client1", "portal.gov.in/page", shell),
        ("20261001_100001", "client1", "portal.gov.in/page", a),
    ]
    maps = link_map.build_maps(sources=sources)
    assert len(maps) == 1
    assert ("client1", "portal.gov.in/page") in maps
    lm = maps[("client1", "portal.gov.in/page")]
    assert lm.screen == 1
    assert lm.link == "portal.gov.in/page"
    assert len(lm.reads) == 2


def test_content_replacement_creates_two_screens():
    """(c) A, then a page that keeps only A's first and last top-level blocks
    and replaces the rest with new blocks of new text -> two screens."""
    a = _load("A")
    doc = _doc(a)
    sub = _subtrees(doc)
    n = len(sub)
    assert n >= 3

    # Keep only first (0) and last (n - 1) blocks, replace the middle with 25 new blocks
    new_blocks = [_group(f"Completely different content block {i} with distinct text") for i in range(25)]
    replaced = _rebuild(a, [0], extra_blocks=new_blocks)
    # Also add the last block
    old_to_new = {}
    for i in sub[n - 1]:
        node = copy.deepcopy(doc[i])
        old_to_new[i] = len(replaced["docs"][0])
        node["parent"] = old_to_new[node["parent"]] if node["parent"] >= 0 else -1
        replaced["docs"][0].append(node)

    sources = [
        ("20261001_100000", "client1", "portal.gov.in/page", a),
        ("20261001_100001", "client1", "portal.gov.in/page", replaced),
    ]
    maps = link_map.build_maps(sources=sources)
    assert len(maps) == 2
    assert ("client1", "portal.gov.in/page") in maps
    assert ("client1", "portal.gov.in/page [screen 2]") in maps

    m1 = maps[("client1", "portal.gov.in/page")]
    m2 = maps[("client1", "portal.gov.in/page [screen 2]")]
    assert m1.screen == 1
    assert m1.link == "portal.gov.in/page"
    assert m2.screen == 2
    assert m2.link == "portal.gov.in/page"
    assert len(m1.reads) == 1
    assert len(m2.reads) == 1

    # In memory.client_maps(), these 2 screens are also separated
    cm = memory.client_maps(client_link_maps=maps, sources=sources)
    assert "portal.gov.in/page" in cm
    assert "portal.gov.in/page [screen 2]" in cm
    assert "client1" in cm["portal.gov.in/page"]
    assert "client1" in cm["portal.gov.in/page [screen 2]"]

    # A second client sees the two screens the other way round: its screens are numbered by its
    # own visit order, but memory pairs them by cover - each screen memory gets ONE map per client.
    sources2 = sources + [
        ("20261001_100002", "client2", "portal.gov.in/page", replaced),
        ("20261001_100003", "client2", "portal.gov.in/page", a),
    ]
    maps2 = link_map.build_maps(sources=sources2)
    assert maps2[("client2", "portal.gov.in/page")].reads == ["20261001_100002"]
    cm2 = memory.client_maps(client_link_maps=maps2, sources=sources2)
    assert sorted(cm2) == ["portal.gov.in/page", "portal.gov.in/page [screen 2]"]
    assert sorted(cm2["portal.gov.in/page"]) == ["client1", "client2"]
    assert sorted(cm2["portal.gov.in/page [screen 2]"]) == ["client1", "client2"]
    assert cm2["portal.gov.in/page"]["client2"].reads == ["20261001_100003"]
    assert cm2["portal.gov.in/page [screen 2]"]["client2"].reads == ["20261001_100002"]
