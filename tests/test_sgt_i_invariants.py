"""
Tests for core/sgt_i/invariants.py (blueprint 14.4 step 3: checksums, values inside values,
relations). Every GSTIN, PAN, ack number, card-shaped and account-shaped value here is fictional,
generated for this test only.
"""

import math

from core.sgt_i import invariants as inv


# ── checksum discovery ───────────────────────────────────────────────────────────
LUHN = ["4477127825", "2619730696", "5239384992", "7989355727",
        "1518471568", "1777778687", "9818365539", "6753989224"]
VERHOEFF = ["226339209", "590819353", "882204820", "177844839",
            "781068719", "388163027", "150325826", "215356429"]
MOD11 = ["82753679", "80157645", "21719799", "50376551",
         "25219111", "81222505", "19917090", "30770521"]
MOD36 = ["E3P3E2Z8IQ9Y7J9", "ZB6CN6Z43DVYRKY", "TTNJFBF5JXVLSIV", "47WQAL9VQ24ZKL0",
         "MVT45HU43JSIOML", "1TMA7V3DI8FPPV7", "5ASPZH8RZHQMOE4", "95B9EE0VBGI09QC"]


def test_luhn_values_are_discovered():
    match = inv.discover_checksum(LUHN)
    assert match.scheme == "luhn"
    assert match.n == 8
    assert match.exceptions == 0
    assert math.isclose(match.p_value, (1 / 10) ** 8 * len(inv.CHECKSUM_SCHEMES))


def test_verhoeff_values_are_discovered():
    match = inv.discover_checksum(VERHOEFF)
    assert match.scheme == "verhoeff"
    assert match.exceptions == 0


def test_mod11_values_are_discovered():
    match = inv.discover_checksum(MOD11)
    assert match.scheme == "mod11"
    assert match.exceptions == 0


def test_mod36_values_are_discovered_matching_the_gstin_scheme():
    match = inv.discover_checksum(MOD36)
    assert match.scheme == "mod36"
    assert match.exceptions == 0
    # sgt_toolbox.c_gstin_checksum is the same algorithm, fixed at 15 characters
    from core.sgt.sgt_toolbox import c_gstin_checksum
    import datetime
    for v in MOD36:
        assert c_gstin_checksum(v, datetime.date(2026, 1, 1)) is True


def test_a_single_broken_value_leaves_no_scheme_discovered():
    broken_last = LUHN[-1][:-1] + str((int(LUHN[-1][-1]) + 1) % 10)
    assert inv.discover_checksum(LUHN[:-1] + [broken_last]) is None


def test_multiple_testing_bound_is_bonferroni_corrected_by_scheme_count():
    match = inv.discover_checksum(MOD36)
    single_scheme_p = (1 / 36) ** match.n
    assert math.isclose(match.p_value, single_scheme_p * len(inv.CHECKSUM_SCHEMES))


def test_empty_or_non_numeric_values_discover_nothing():
    assert inv.discover_checksum([]) is None
    assert inv.discover_checksum(["", "abc"]) is None


# ── values inside values ─────────────────────────────────────────────────────────
def test_gstin_contains_pan_at_characters_3_to_12():
    pans = ["ABCDE1234F", "PQRSX9876K", "ZZYYX0001A", "MNBVC5678L", "QWERT2345Y"]
    gstins = [f"19{p}1ZB" for p in pans]
    match = inv.find_value_containment(gstins, pans)
    assert match.anchor == "start"
    assert match.offset == 2                  # characters 3-12, 1-indexed
    assert match.n == 5
    assert match.exceptions == 0
    assert match.confidence is not None


def test_ack_ends_in_its_own_ddmmyy_date_despite_a_varying_prefix():
    filed = ["12/03/2026", "05/11/2025", "28/02/2026", "01/01/2026"]
    prefixes = ["ACK", "ACKNOWLEDGEMENT", "AK", "ACKN"]     # different lengths on purpose
    acks = [p + inv.format_ddmmyy(d) for p, d in zip(prefixes, filed)]
    match = inv.find_value_containment(acks, [inv.format_ddmmyy(d) for d in filed])
    assert match.anchor == "end"
    assert match.offset == 0                  # the date is the value's own suffix
    assert match.n == 4
    assert match.exceptions == 0


def test_format_ddmmyy_rejects_a_non_date():
    assert inv.format_ddmmyy("not a date") is None
    assert inv.format_ddmmyy("12/03/2026") == "120326"


def test_containment_needs_at_least_two_paired_samples():
    assert inv.find_value_containment(["abc"], ["x"]) is None


def test_containment_is_none_when_the_inner_value_never_appears():
    assert inv.find_value_containment(["abc", "def"], ["zzz", "yyy"]) is None


# ── relations between containers ─────────────────────────────────────────────────
def test_filed_on_before_or_equal_processed_on_holds():
    filed = ["01/03/2026", "02/03/2026", "03/03/2026"]
    processed = ["05/03/2026", "02/03/2026", "10/03/2026"]
    relation = inv.find_order_relation(filed, processed)
    assert relation.op == "<="
    assert relation.n == 3
    assert relation.exceptions == 0
    assert relation.confidence is not None


def test_order_relation_breaks_with_one_counter_example():
    filed = ["01/03/2026", "02/03/2026", "03/03/2026"]
    processed_bad = ["05/03/2026", "01/03/2026", "10/03/2026"]      # 2nd sample: 02 > 01
    relation = inv.find_order_relation(filed, processed_bad)
    assert relation.exceptions == 1
    assert relation.confidence is None


def test_order_relation_skips_pairs_of_different_kinds():
    filed = ["01/03/2026", "02/03/2026", "03/03/2026", "100"]
    processed = ["05/03/2026", "02/03/2026", "10/03/2026", "not a number"]
    relation = inv.find_order_relation(filed, processed)
    assert relation.n == 3                     # the mismatched-kind pair is excluded, not counted


def test_total_equals_sum_of_the_rows():
    totals = ["300", "150.50", "90"]
    rows = [["100", "100", "100"], ["50.25", "100.25"], ["30", "30", "30"]]
    relation = inv.find_sum_relation(totals, rows)
    assert relation.n == 3
    assert relation.exceptions == 0
    assert relation.confidence is not None


def test_sum_relation_breaks_with_one_counter_example():
    totals_bad = ["300", "999", "90"]
    rows = [["100", "100", "100"], ["50.25", "100.25"], ["30", "30", "30"]]
    relation = inv.find_sum_relation(totals_bad, rows)
    assert relation.exceptions == 1
    assert relation.confidence is None


def test_relations_need_at_least_two_comparable_samples():
    assert inv.find_order_relation(["x"], ["y"]) is None
    assert inv.find_sum_relation(["10"], [["5", "5"]]) is None


# ── privacy: nothing here ever returns a value it was given ─────────────────────
def test_no_result_object_ever_carries_a_raw_value():
    match = inv.discover_checksum(LUHN)
    for v in LUHN:
        assert v not in repr(match)
    containment = inv.find_value_containment(["19ABCDE1234F1ZB"] * 2, ["ABCDE1234F"] * 2)
    assert "ABCDE1234F" not in repr(containment)
