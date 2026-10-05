"""
tests/test_sdis_history.py - tests for SDIS value history and day_of (Part B, W1-1)
====================================================================================
Tests:
- day_of on all three formats (capture snapshot, single read, ISO recorder time);
- two snapshots with different texts -> 2 history items;
- the same text twice -> 1;
- A, B, A -> 3;
- memory keeps each client's history apart and skips exact duplicates.
"""

import json
from pathlib import Path

import pytest

from core.sdis import keys
from core.sdis.history import day_of
from core.sdis.link_map import LinkMap
from core.sdis.memory import PageMemory

FIX = Path(__file__).resolve().parent / "class_diff_align"


@pytest.fixture(autouse=True)
def raw_view(monkeypatch):
    monkeypatch.setattr(keys, "VIEW", "raw")


def _make_rec(text: str, tag: str = "Heading", cls: str = "title") -> dict:
    """Helper to produce a single-element document for unit tests."""
    return {
        "docs": [[
            {
                "parent": -1,
                "type_name": tag,
                "name": text,
                "rect": [0, 0, 100, 20],
                "cls": cls,
                "id": "",
                "sgt": True,
            }
        ]]
    }


def test_day_of_all_three_formats():
    # 1. Capture snapshot: '20261001_235545+0005.0' -> '20261001'
    assert day_of("20261001_235545+0005.0") == "20261001"

    # 2. Single read: '20261001_235545' -> '20261001'
    assert day_of("20261001_235545") == "20261001"

    # 3. ISO time (future recorder): '2026-10-01T23:55:45' -> '20261001'
    assert day_of("2026-10-01T23:55:45") == "20261001"

    # Variations and edge cases
    assert day_of("2026-10-01") == "20261001"
    assert day_of("20261001") == "20261001"
    assert day_of("2026-10") == ""          # fewer than 8 digits
    assert day_of("abc") == ""
    assert day_of("") == ""
    assert day_of(None) == ""


def test_two_snapshots_with_different_texts_gives_two_history_items():
    m = LinkMap("client1", "page1")
    m.add(_make_rec("Alpha"), "20261001_100000+0001.0")
    m.add(_make_rec("Beta"), "20261001_100000+0002.0")

    flat = m.to_flat()
    assert len(flat) == 1
    assert flat[0]["text"] == "Beta"
    assert flat[0]["history"] == [
        ["20261001_100000+0001.0", "Alpha"],
        ["20261001_100000+0002.0", "Beta"],
    ]


def test_same_text_twice_gives_one_history_item():
    m = LinkMap("client1", "page1")
    m.add(_make_rec("SameText"), "20261001_100000+0001.0")
    m.add(_make_rec("SameText"), "20261001_100000+0002.0")

    flat = m.to_flat()
    assert len(flat) == 1
    assert flat[0]["text"] == "SameText"
    assert flat[0]["history"] == [
        ["20261001_100000+0001.0", "SameText"],
    ]


def test_text_sequence_a_b_a_gives_three_history_items():
    m = LinkMap("client1", "page1")
    m.add(_make_rec("A"), "20261001_100000+0001.0")
    m.add(_make_rec("B"), "20261001_100000+0002.0")
    m.add(_make_rec("A"), "20261001_100000+0003.0")

    flat = m.to_flat()
    assert len(flat) == 1
    assert flat[0]["text"] == "A"
    assert flat[0]["history"] == [
        ["20261001_100000+0001.0", "A"],
        ["20261001_100000+0002.0", "B"],
        ["20261001_100000+0003.0", "A"],
    ]


def test_memory_keeps_each_client_history_apart():
    # Client 1 has 2 history items
    m1 = LinkMap("client1", "page1")
    m1.add(_make_rec("Client1_Text1"), "20261001_100000")
    m1.add(_make_rec("Client1_Text2"), "20261001_110000")

    # Client 2 has 2 history items
    m2 = LinkMap("client2", "page1")
    m2.add(_make_rec("Client2_Text1"), "20261002_100000")
    m2.add(_make_rec("Client2_Text2"), "20261002_110000")

    mem = PageMemory("page1", n_promote=2)
    mem.add(m1.to_flat(), "client1")
    mem.add(m2.to_flat(), "client2")

    assert len(mem.nodes) == 1
    node = mem.nodes[0]

    assert "client1" in node["clients"]
    assert "client2" in node["clients"]

    c1 = node["clients"]["client1"]
    c2 = node["clients"]["client2"]

    assert c1["history"] == [
        ["20261001_100000", "Client1_Text1"],
        ["20261001_110000", "Client1_Text2"],
    ]
    assert c2["history"] == [
        ["20261002_100000", "Client2_Text1"],
        ["20261002_110000", "Client2_Text2"],
    ]

    # Re-adding client 1's map skips exact duplicate history entries
    mem.add(m1.to_flat(), "client1")
    assert c1["history"] == [
        ["20261001_100000", "Client1_Text1"],
        ["20261001_110000", "Client1_Text2"],
    ]


def test_fictional_client_fixture_history():
    a = json.loads((FIX / "client_A.json").read_text(encoding="utf-8"))
    m = LinkMap("client_a", "page_test")
    m.add(a, "20261001_131804")

    flat = m.to_flat()
    assert len(flat) > 0
    for e in flat:
        assert "history" in e
        if e["text"]:
            assert len(e["history"]) == 1
            assert e["history"][0] == ["20261001_131804", e["text"]]
        else:
            assert e["history"] == []
