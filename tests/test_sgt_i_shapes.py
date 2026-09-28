"""
Tests for core/sgt_i/shapes.py (blueprint 14.4 step 3, shape grammar part). Every PAN, Aadhaar,
IFSC and account value here is fictional.
"""

import math
import re

from core.sgt_i import shapes


# ── class strings (re-exported from pairs.py, not redefined) ────────────────────
def test_mask_shape_is_pairs_mask_shape():
    from core.sgt_i import pairs
    assert shapes.mask_shape is pairs.mask_shape


# ── generalisation into one pattern ──────────────────────────────────────────────
def test_ten_identical_pan_shapes_generalise_to_the_exact_pan_regex():
    pans = ["ABCDE1234F", "PQRSX9876K", "ZZYYX0001A", "MNBVC5678L", "QWERT2345Y",
            "ASDFG6789H", "LKJHG3456T", "POIUY8901R", "TREWQ4567E", "GHJKL0123N"]
    grammar = shapes.induce_shape_grammar([shapes.mask_shape(p) for p in pans])
    assert grammar.pattern == r"^[A-Za-z]{5}\d{4}[A-Za-z]$"    # a lone run of length 1 has no {1}
    assert grammar.n == 10
    assert grammar.exceptions == 0
    for p in pans:
        assert re.match(grammar.pattern, p)


def test_aadhaar_shaped_values_generalise_with_the_grouping_spaces_kept():
    values = ["1234 5678 9012", "9999 0000 1111", "4567 8912 3456"]
    grammar = shapes.induce_shape_grammar([shapes.mask_shape(v) for v in values])
    sp = re.escape(" ")
    assert grammar.pattern == f"^\\d{{4}}{sp}\\d{{4}}{sp}\\d{{4}}$"
    assert all(re.match(grammar.pattern, shapes.mask_shape(v)) for v in values)
    assert grammar.exceptions == 0


def test_varying_run_length_becomes_a_range_not_a_fixed_count():
    # An account number container where clients have 11 or 12 digit accounts.
    accounts = ["12345678901", "123456789012", "123456789012", "12345678901", "123456789012"]
    grammar = shapes.induce_shape_grammar([shapes.mask_shape(a) for a in accounts])
    assert grammar.pattern == r"^\d{11,12}$"
    assert grammar.exceptions == 0


def test_a_differently_formatted_value_is_an_exception_not_folded_in():
    # Nine values formatted "999-999-999", one formatted "999999999" (no dashes).
    consistent = ["123-456-789"] * 9
    odd_one_out = ["123456789"]
    grammar = shapes.induce_shape_grammar([shapes.mask_shape(v) for v in consistent + odd_one_out])
    assert grammar.n == 10
    assert grammar.exceptions == 1
    dash = re.escape("-")
    assert grammar.pattern == f"^\\d{{3}}{dash}\\d{{3}}{dash}\\d{{3}}$"
    assert grammar.confidence is None                 # rule of three needs zero exceptions


def test_empty_input_returns_none():
    assert shapes.induce_shape_grammar([]) is None


def test_a_single_shape_still_generalises_trivially():
    grammar = shapes.induce_shape_grammar([shapes.mask_shape("ABCDE1234F")])
    assert grammar.n == 1
    assert grammar.exceptions == 0
    assert re.match(grammar.pattern, "ABCDE1234F")
    assert not re.match(grammar.pattern, "AB1234F")    # a shorter code correctly does not match


# ── the confidence bound: rule of three ──────────────────────────────────────────
def test_zero_exceptions_bound_is_about_three_over_n_at_95_percent():
    bound = shapes.shape_grammar_confidence(100, 0)
    assert math.isclose(bound, -math.log(0.05) / 100)
    assert bound < 3.0 / 100          # blueprint's own "< 3/N" holds (-ln(0.05) < 3)
    assert bound > 2.9 / 100


def test_any_exception_makes_the_bound_undefined():
    assert shapes.shape_grammar_confidence(100, 1) is None


def test_induced_grammar_confidence_matches_the_bound_function():
    pans = [shapes.mask_shape(f"ABCDE{n:04d}F") for n in range(20)]
    grammar = shapes.induce_shape_grammar(pans)
    assert grammar.confidence == shapes.shape_grammar_confidence(grammar.n, grammar.exceptions)


def test_confidence_tightens_as_n_grows():
    low_n = shapes.shape_grammar_confidence(10, 0)
    high_n = shapes.shape_grammar_confidence(1000, 0)
    assert high_n < low_n


def test_higher_confidence_asks_for_a_looser_bound():
    b95 = shapes.shape_grammar_confidence(100, 0, confidence=0.95)
    b99 = shapes.shape_grammar_confidence(100, 0, confidence=0.99)
    assert b99 > b95


def test_confidence_bound_rejects_bad_input():
    import pytest
    with pytest.raises(ValueError):
        shapes.shape_grammar_confidence(0, 0)
    with pytest.raises(ValueError):
        shapes.shape_grammar_confidence(10, 0, confidence=1.0)
    with pytest.raises(ValueError):
        shapes.shape_grammar_confidence(10, 0, confidence=0.0)
