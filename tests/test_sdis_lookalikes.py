"""
tests/test_sdis_lookalikes.py - Part E: look-alikes scored pairing, AMBIGUOUS
"""

import pytest
from core.sdis.align import (
    AMBIGUOUS_MARGIN,
    _MAX_CELLS,
    _best_injection,
    _column,
    _look_alikes,
    _pattern,
    _score,
    align,
)
from core.sdis.memory import PageMemory
from tools.pre_dev.class_diff.compare import compare_flat


def test_pattern_examples():
    assert _pattern("AB1234") == "A9"
    assert _pattern("Filed") == "A"
    assert _pattern("12345") == "9"
    assert _pattern("ABCDE") == "A"
    assert _pattern("") == ""
    assert _pattern("   ") == ""
    assert _pattern("  AB  1234  ") == "A 9"


def test_column_overlap():
    a = {"node": {"rect": [10, 0, 100, 20]}}
    b = {"node": {"rect": [10, 50, 100, 20]}}
    assert _column(a, b) == 1.0

    # Half overlap: a=[10..110], b=[60..160] -> overlap=50, min width=100 -> 0.5
    b_half = {"node": {"rect": [60, 50, 100, 20]}}
    assert _column(a, b_half) == 0.5

    # No overlap
    b_none = {"node": {"rect": [120, 50, 100, 20]}}
    assert _column(a, b_none) == 0.0

    # Missing rect
    assert _column({}, b) == 0.0
    assert _column(a, {"node": {}}) == 0.0
    assert _column(a, {"node": {"rect": [10, 0, 0, 20]}}) == 0.0


def test_score_calculation():
    a = {"text": "AB1234", "node": {"rect": [10, 0, 100, 20]}}
    b_same = {"text": "XY5678", "node": {"rect": [10, 50, 100, 20]}}  # pattern "A9" == "A9"
    b_diff = {"text": "Filed", "node": {"rect": [10, 50, 100, 20]}}   # pattern "A" != "A9"

    assert _score(a, b_same) == 1.0 + 1.0  # 2.0
    assert _score(a, b_diff) == 0.0 + 1.0  # 1.0


def test_best_injection_banned_and_backtracking():
    # 2 rows x 3 columns
    score = [
        [2.0, 1.0, 0.0],
        [0.0, 2.0, 2.0],
    ]
    total, chosen = _best_injection(score)
    assert total == 4.0
    assert chosen == [0, 1]  # ties broken towards earlier column

    # Banning (1, 1) forces row 1 to take column 2
    total_banned, chosen_banned = _best_injection(score, banned=(1, 1))
    assert total_banned == 4.0
    assert chosen_banned == [0, 2]

    # If cannot pair, return -inf and [] without backtracking
    k_gt_n = [[1.0], [2.0]]
    assert _best_injection(k_gt_n) == (-float("inf"), [])

    single = [[1.0]]
    assert _best_injection(single, banned=(0, 0)) == (-float("inf"), [])


def test_equal_counts_no_ambiguous():
    fl = [
        {"key": "Card / Text[1]", "text": "foo", "node": {"rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "bar", "node": {"rect": [10, 60, 100, 30]}},
    ]
    fp = [
        {"key": "Card / Text[1]", "text": "baz", "node": {"rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "qux", "node": {"rect": [10, 60, 100, 30]}},
    ]
    amb = set()
    pairs = align(fl, fp, lambda e: True, ambiguous=amb)
    assert pairs == {0: 0, 1: 1}
    assert len(amb) == 0


def test_two_vs_one_matching_second_by_pattern_not_ambiguous():
    # fl has 2 items, fp has 1 item matching fl's second item by pattern
    fl = [
        {"key": "Card / Text[1]", "text": "AB1234", "node": {"rect": [10, 20, 100, 30]}},  # pattern A9
        {"key": "Card / Text[2]", "text": "Filed", "node": {"rect": [10, 60, 100, 30]}},   # pattern A
    ]
    fp = [
        {"key": "Card / Text[1]", "text": "Approved", "node": {"rect": [10, 20, 100, 30]}},  # pattern A
    ]
    amb = set()
    pairs = align(fl, fp, lambda e: True, ambiguous=amb)
    # The single one (fp[0]) pairs with the second one (fl[1])
    assert pairs == {1: 0}
    assert len(amb) == 0

    # Reverse: fl has 1 item, fp has 2 items
    fl_rev = [
        {"key": "Card / Text[1]", "text": "Approved", "node": {"rect": [10, 20, 100, 30]}},  # pattern A
    ]
    fp_rev = [
        {"key": "Card / Text[1]", "text": "AB1234", "node": {"rect": [10, 20, 100, 30]}},  # pattern A9
        {"key": "Card / Text[2]", "text": "Filed", "node": {"rect": [10, 60, 100, 30]}},   # pattern A
    ]
    amb_rev = set()
    pairs_rev = align(fl_rev, fp_rev, lambda e: True, ambiguous=amb_rev)
    # fl[0] pairs with fp[1]
    assert pairs_rev == {0: 1}
    assert len(amb_rev) == 0


def test_two_vs_one_identical_patterns_and_columns_ambiguous():
    # fl has 2 items, fp has 1 item with identical patterns and columns
    fl = [
        {"key": "Card / Text[1]", "text": "Alpha", "node": {"rect": [10, 20, 100, 30]}},  # pattern A
        {"key": "Card / Text[2]", "text": "Beta", "node": {"rect": [10, 60, 100, 30]}},   # pattern A
    ]
    fp = [
        {"key": "Card / Text[1]", "text": "Gamma", "node": {"rect": [10, 20, 100, 30]}},  # pattern A
    ]
    amb = set()
    pairs = align(fl, fp, lambda e: True, ambiguous=amb)
    assert pairs == {0: 0}
    assert amb == {0}

    # Reverse: fl has 1 item, fp has 2 items
    fl_rev = [
        {"key": "Card / Text[1]", "text": "Gamma", "node": {"rect": [10, 20, 100, 30]}},  # pattern A
    ]
    fp_rev = [
        {"key": "Card / Text[1]", "text": "Alpha", "node": {"rect": [10, 20, 100, 30]}},  # pattern A
        {"key": "Card / Text[2]", "text": "Beta", "node": {"rect": [10, 60, 100, 30]}},   # pattern A
    ]
    amb_rev = set()
    pairs_rev = align(fl_rev, fp_rev, lambda e: True, ambiguous=amb_rev)
    assert pairs_rev == {0: 0}
    assert amb_rev == {0}


def test_max_cells_forces_all_unsure(monkeypatch):
    import core.sdis.align as align_mod
    monkeypatch.setattr(align_mod, "_MAX_CELLS", 2)  # k*n*k = 1*2*1 = 2 <= 2 not exceeded, but 2*3*2 = 12 > 2

    fl = [
        {"key": "Card / Text[1]", "text": "Alpha", "node": {"rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "Beta", "node": {"rect": [10, 60, 100, 30]}},
    ]
    fp = [
        {"key": "Card / Text[1]", "text": "A1", "node": {"rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "B2", "node": {"rect": [10, 60, 100, 30]}},
        {"key": "Card / Text[3]", "text": "C3", "node": {"rect": [10, 100, 100, 30]}},
    ]
    amb = set()
    align_mod.align(fl, fp, lambda e: True, ambiguous=amb)
    # k=2, n=3, k*n*k = 12 > _MAX_CELLS (2) -> all rows marked unsure
    assert amb == {0, 1}


def test_memory_wire_up():
    pm = PageMemory("test_page", n_promote=2)
    flat_c1 = [
        {"key": "Head / Text[1]", "text": "Title", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [0, 0, 200, 30]}},
        {"key": "Card / Text[1]", "text": "Alpha", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "Beta", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [10, 60, 100, 30]}},
    ]
    pm.add(flat_c1, "client_1")

    # Client 2 has only 1 card element with identical pattern & column
    flat_c2 = [
        {"key": "Head / Text[1]", "text": "Title", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [0, 0, 200, 30]}},
        {"key": "Card / Text[1]", "text": "Gamma", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [10, 20, 100, 30]}},
    ]
    pm.add(flat_c2, "client_2")

    # Node 1 was matched to Card / Text[1], but ambiguously
    node1 = pm.nodes[1]
    assert node1["clients"]["client_1"]["sure"] is True
    assert node1["clients"]["client_2"]["sure"] is False
    assert pm.confirmed(1) is False  # client_2 has sure=False, so only 1 sure client < n_promote=2
    assert pm.verdict(1) == "ambiguous"

    # A later sure observation of client_2 sets sure to True
    flat_c2_sure = [
        {"key": "Head / Text[1]", "text": "Title", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [0, 0, 200, 30]}},
        {"key": "Card / Text[1]", "text": "Alpha", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "Beta", "type": "text", "cls": "", "depth": 0, "parent": -1, "node": {"rect": [10, 60, 100, 30]}},
    ]
    pm.add(flat_c2_sure, "client_2")
    assert pm.nodes[1]["clients"]["client_2"]["sure"] is True
    assert pm.confirmed(1) is True


def test_compare_flat_wire_up():
    fl = [
        {"key": "Head / Text[1]", "text": "Title", "cls": "", "parent": -1, "depth": 0, "node": {"ctype": "Text", "rect": [0, 0, 200, 30]}},
        {"key": "Card / Text[1]", "text": "Alpha", "cls": "", "parent": -1, "depth": 0, "node": {"ctype": "Text", "rect": [10, 20, 100, 30]}},
        {"key": "Card / Text[2]", "text": "Beta", "cls": "", "parent": -1, "depth": 0, "node": {"ctype": "Text", "rect": [10, 60, 100, 30]}},
    ]
    fp = [
        {"key": "Head / Text[1]", "text": "Title", "cls": "", "parent": -1, "depth": 0, "node": {"ctype": "Text", "rect": [0, 0, 200, 30]}},
        {"key": "Card / Text[1]", "text": "Gamma", "cls": "", "parent": -1, "depth": 0, "node": {"ctype": "Text", "rect": [10, 20, 100, 30]}},
    ]
    rows = compare_flat(fl, fp)
    # The paired look-alike row should be flagged as ambiguous
    card_row = next(r for r in rows if r["example_value"] == "Alpha")
    assert card_row["check"] == "ambiguous pairing - look-alikes, one side has fewer"
