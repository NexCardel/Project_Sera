"""
tests/test_sdis_identity.py - tests for SDIS Part D: identity by session id + data fingerprint.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from core.sdis import identity, keys, link_map, memory
from core.sdis.identity import (DIFF, MIN_VALUES, SAME, decide, fingerprint,
                                group_clients, resolve_owners)

FIX = Path(__file__).resolve().parent / "class_diff_align"


def _make_dummy_map(client: str, page: str, flat: list) -> link_map.LinkMap:
    m = link_map.LinkMap(client, page, screen=1, link=page)
    # populate entries from flat
    for e in flat:
        k = e["key"]
        m.entries[k] = {
            "key": k,
            "cls": e.get("cls", ""),
            "type": e.get("type", "Text"),
            "text": e.get("text", ""),
            "node": e.get("node", {}),
            "values": [e["text"]] if e.get("text") else [],
            "first": "20260101_000000",
            "last": "20260101_000000",
            "history": [[("20260101_000000"), e["text"]]] if e.get("text") else [],
        }
        p = e.get("parent", -1)
        parent_key = flat[p]["key"] if p >= 0 and p < len(flat) else None
        m.children.setdefault(parent_key, []).append(k)
    return m


def test_decide_thresholds():
    # n < MIN_VALUES always gives undecided
    assert decide(1.0, MIN_VALUES - 1) == "undecided"
    assert decide(0.0, MIN_VALUES - 1) == "undecided"
    assert decide(0.5, 0) == "undecided"

    # n >= MIN_VALUES
    assert decide(SAME, MIN_VALUES) == "same"
    assert decide(0.95, 5) == "same"
    assert decide(1.0, 10) == "same"

    assert decide(DIFF, MIN_VALUES) == "different"
    assert decide(0.49, 4) == "different"
    assert decide(0.0, 3) == "different"

    # Between DIFF and SAME -> undecided
    assert decide(0.51, 3) == "undecided"
    assert decide(0.70, 4) == "undecided"
    assert decide(0.89, 5) == "undecided"


def test_fingerprint_rarity_weights():
    # Two simple synthetic maps: 2 data elements
    # Map 1: node 1 = "100" (number), node 2 = "200" (number)
    # Map 2: node 1 = "100" (number), node 2 = "300" (number)
    def make_entry(key, text):
        return {
            "key": key, "text": text, "type": "Text", "cls": "", "parent": -1,
            "node": {"type_name": "Text", "ctype": 50020, "name": text, "sgt": True},
        }

    flat_1 = [make_entry("K1", "100"), make_entry("K2", "200")]
    flat_2 = [make_entry("K1", "100"), make_entry("K2", "300")]
    m1 = _make_dummy_map("s1", "page1", flat_1)
    m2 = _make_dummy_map("s2", "page1", flat_2)

    # With default rarity (all 1)
    agr, tot, n = fingerprint(m1, m2)
    assert n == 2
    # equal pair: wa = 1.0, adds 1.0 to agree and total
    # unequal pair: wa = 1.0, wb = 1.0, adds 1.0 to total
    assert agr == 1.0
    assert tot == 2.0

    # With custom rarity: "100" seen by the 2 compared sessions only (counted as one: w = 1.0),
    # "200" by 1 (w = 1.0), "300" by 1 (w = 1.0)
    from core.sdis.align import shape
    sh = shape(flat_1[0])
    rarity = {
        ("page1", sh, "100"): 2,
        ("page1", sh, "200"): 1,
        ("page1", sh, "300"): 1,
    }
    agr_r, tot_r, n_r = fingerprint(m1, m2, rarity)
    assert n_r == 2
    assert agr_r == 1.0
    assert tot_r == 1.0 + (1.0 + 1.0) / 2.0  # 2.0

    # "100" also shown by 2 third sessions (4 in all): w = 1 / 3
    rarity[("page1", sh, "100")] = 4
    agr_c, tot_c, _ = fingerprint(m1, m2, rarity)
    assert agr_c == pytest.approx(1 / 3)
    assert tot_c == pytest.approx(1 / 3 + 1.0)


def test_one_changed_value_among_ten_rare_ones_is_same_client():
    """Nine values only the two sessions share + one that changed: 9 / 10 = SAME. Counting the
    shared ones at 1/2 (both sessions show them) would give 4.5 / 5.5 -> undecided."""
    def make_entry(key, text):
        return {
            "key": key, "text": text, "type": "Text", "cls": "", "parent": -1,
            "node": {"type_name": "Text", "ctype": 50020, "name": text, "sgt": True},
        }

    flat_1 = [make_entry(f"K{i}", str(1000 + i)) for i in range(10)]
    flat_2 = [make_entry(f"K{i}", str(1000 + i)) for i in range(9)] + [make_entry("K9", "2999")]
    page = "test.local/rare.html"
    session_maps = {
        ("capture 20260101_000000", page): _make_dummy_map("capture 20260101_000000", page, flat_1),
        ("capture 20260101_000100", page): _make_dummy_map("capture 20260101_000100", page, flat_2),
    }
    owners = resolve_owners(session_maps, {"capture 20260101_000000": set(), "capture 20260101_000100": set()})
    assert owners["capture 20260101_000000"] == owners["capture 20260101_000100"] == "client 1"


def test_link_with_two_screens_gives_no_evidence():
    """Screens are numbered per session, so a link where one session has two screens is not
    compared at all: no evidence -> undecided, never 'different' by pairing the wrong screens."""
    rec_a = json.loads((FIX / "client_A.json").read_text(encoding="utf-8"))
    rec_b = json.loads((FIX / "client_B.json").read_text(encoding="utf-8"))
    page = "test.local/client_A.html"
    s1, s2 = "capture 20260101_000000", "capture 20260101_000100"
    m1 = _make_dummy_map(s1, page, keys.flatten(rec_a))
    m1b = _make_dummy_map(s1, page, keys.flatten(rec_b))
    m1b.screen = 2
    m2 = _make_dummy_map(s2, page, keys.flatten(rec_b))
    session_maps = {(s1, page): m1, (s1, page + " [screen 2]"): m1b, (s2, page): m2}
    owners = resolve_owners(session_maps, {s1: set(), s2: set()})
    assert owners[s1] == f"undecided {s1}"
    assert owners[s2] == f"undecided {s2}"


def test_fixtures_a_and_b_two_clients():
    """Fixtures A and B as two sessions with no ids -> 'different' -> two clients."""
    rec_a = json.loads((FIX / "client_A.json").read_text(encoding="utf-8"))
    rec_b = json.loads((FIX / "client_B.json").read_text(encoding="utf-8"))
    page = "test.local/client_A.html"

    ma = _make_dummy_map("capture 20260101_000000", page, keys.flatten(rec_a))
    mb = _make_dummy_map("capture 20260101_000100", page, keys.flatten(rec_b))

    session_maps = {
        ("capture 20260101_000000", page): ma,
        ("capture 20260101_000100", page): mb,
    }
    session_ids = {
        "capture 20260101_000000": set(),
        "capture 20260101_000100": set(),
    }

    owners = resolve_owners(session_maps, session_ids)
    assert owners["capture 20260101_000000"] == "client 1"
    assert owners["capture 20260101_000100"] == "client 2"


def test_fixture_a_twice_one_client():
    """A twice (two sessions) -> 'same' -> one client."""
    rec_a = json.loads((FIX / "client_A.json").read_text(encoding="utf-8"))
    page = "test.local/client_A.html"

    ma1 = _make_dummy_map("capture 20260101_000000", page, keys.flatten(rec_a))
    ma2 = _make_dummy_map("capture 20260101_000100", page, keys.flatten(rec_a))

    session_maps = {
        ("capture 20260101_000000", page): ma1,
        ("capture 20260101_000100", page): ma2,
    }
    session_ids = {
        "capture 20260101_000000": set(),
        "capture 20260101_000100": set(),
    }

    owners = resolve_owners(session_maps, session_ids)
    assert owners["capture 20260101_000000"] == "client 1"
    assert owners["capture 20260101_000100"] == "client 1"


def test_session_with_too_few_data_values_undecided():
    """A session with too few data values -> 'undecided'."""
    def make_entry(key, text):
        return {
            "key": key, "text": text, "type": "Text", "cls": "", "parent": -1,
            "node": {"type_name": "Text", "ctype": 50020, "name": text, "sgt": True},
        }

    # Only 1 data value each (MIN_VALUES is 3)
    flat_1 = [make_entry("K1", "100")]
    flat_2 = [make_entry("K1", "200")]
    page = "test.local/sparse.html"
    m1 = _make_dummy_map("capture 20260101_000000", page, flat_1)
    m2 = _make_dummy_map("capture 20260101_000100", page, flat_2)

    session_maps = {
        ("capture 20260101_000000", page): m1,
        ("capture 20260101_000100", page): m2,
    }
    session_ids = {
        "capture 20260101_000000": set(),
        "capture 20260101_000100": set(),
    }

    owners = resolve_owners(session_maps, session_ids)
    assert owners["capture 20260101_000000"] == "undecided capture 20260101_000000"
    assert owners["capture 20260101_000100"] == "undecided capture 20260101_000100"


def test_chained_ids_still_join():
    """Chained ids still join into one client."""
    session_ids = {
        "capture 20260101_000000": {"gstin:11"},
        "capture 20260101_000100": {"gstin:11", "pan:22"},
        "capture 20260101_000200": {"pan:22"},
    }
    owners = resolve_owners({}, session_ids)
    assert owners["capture 20260101_000000"] == "client 1"
    assert owners["capture 20260101_000100"] == "client 1"
    assert owners["capture 20260101_000200"] == "client 1"


def test_session_without_ids_joins_session_with_id_on_same():
    """A session with no IDs that matches a session with IDs takes its owner."""
    rec_a = json.loads((FIX / "client_A.json").read_text(encoding="utf-8"))
    page = "test.local/client_A.html"

    m1 = _make_dummy_map("capture 20260101_000000", page, keys.flatten(rec_a))
    m2 = _make_dummy_map("capture 20260101_000100", page, keys.flatten(rec_a))

    session_maps = {
        ("capture 20260101_000000", page): m1,
        ("capture 20260101_000100", page): m2,
    }
    session_ids = {
        "capture 20260101_000000": {"gstin:27AAAAA1111A1Z1"},
        "capture 20260101_000100": set(),
    }

    owners = resolve_owners(session_maps, session_ids)
    assert owners["capture 20260101_000000"] == "client 1"
    assert owners["capture 20260101_000100"] == "client 1"
