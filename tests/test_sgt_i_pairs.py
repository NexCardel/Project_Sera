"""
Tests for core/sgt_i/pairs.py (blueprint 14.4 step 2, 14.5 privacy rule 1). Every container,
name, PAN, Aadhaar-shaped, account and email value here is fictional.
"""

import dataclasses
import json
import os
import tempfile

import security
from core.sgt_i import page_map as pm
from core.sgt_i import pairs
from database import SeraDatabase

# A page-map with no real nodes: pairs_from_page only ever reads page.pairs and page.section_path,
# so a PageMap can be built directly, without going through a layout read at all.
SECTIONS = (
    pm.Section(0, "Personal details", 2, node=0, parent=-1),
    pm.Section(1, "Bank Details", 2, node=1, parent=-1),
)
FIXTURE_PAIRS = (
    pm.Pair("PAN", "ABCDE1234F", "right", 10, 11, "main", 0),
    pm.Pair("Email", "test.user@example.test", "right", 12, 13, "main", 0),
    pm.Pair("Mobile", "+91 98765 43210", "right", 14, 15, "main", 0),
    pm.Pair("Date of Birth", "12-Mar-1980", "right", 16, 17, "main", 0),
    pm.Pair("Married", "Yes", "right", 18, 19, "main", 0),
    pm.Pair("Filed u/s", "Original", "choice", 20, 21, "main", 0),
    pm.Pair("Tax Rate", "18%", "right", 22, 23, "main", 0),
    pm.Pair("Account Number", "000123456789", "right", 24, 25, "main", 1),
    pm.Pair("IFSC", "HDFC0001234", "right", 26, 27, "main", 1),
    pm.Pair("Aadhaar Number", "1234 5678 9012", "right", 28, 29, "main", 1),
    pm.Pair("Total Amount", "₹1,23,456.00", "right", 30, 31, "main", 1),
    pm.Pair("Remarks", "Some free text here", "right", 32, 33, "main", 1),
)
PAGE = pm.PageMap(nodes=(), sections=SECTIONS, pairs=FIXTURE_PAIRS)
SENSITIVE_VALUES = [p.value for p in FIXTURE_PAIRS]     # what must never reach disk unmasked


def by_label():
    return {p.label: vp for p, vp in zip(FIXTURE_PAIRS, pairs.pairs_from_page(PAGE))}


# ── generic types ────────────────────────────────────────────────────────────────
def test_generic_type_for_every_kind():
    got = {p.label: vp.type for p, vp in zip(FIXTURE_PAIRS, pairs.pairs_from_page(PAGE))}
    assert got == {
        "PAN": "code", "Email": "email", "Mobile": "phone", "Date of Birth": "date",
        "Married": "yes/no", "Filed u/s": "choice", "Tax Rate": "percentage",
        "Account Number": "number", "IFSC": "code", "Aadhaar Number": "number",
        "Total Amount": "amount", "Remarks": "text",
    }


def test_a_choice_pair_is_typed_from_its_method_not_its_text():
    # "Original" alone would classify as text; the page-map "choice" method settles it structurally.
    assert pairs.classify_type("Original", "choice") == "choice"
    assert pairs.classify_type("Original", "right") == "text"


def test_plain_decimal_is_a_number_not_an_amount():
    assert pairs.classify_type("3.14") == "number"


def test_empty_value_is_text():
    assert pairs.classify_type("") == "text"


# ── masked shape ─────────────────────────────────────────────────────────────────
def test_mask_shape_matches_the_blueprints_own_examples():
    assert pairs.mask_shape("ABCDE1234F") == "AAAAA9999A"          # 14.9
    assert pairs.mask_shape("1234 5678 9012") == "9999 9999 9999"  # 14.4 step 2


def test_mask_shape_replaces_every_letter_and_digit_and_nothing_else():
    for value in SENSITIVE_VALUES:
        shape = pairs.mask_shape(value)
        assert len(shape) == len(value)
        for orig, masked in zip(value, shape):
            if orig.isalpha():
                assert masked == "A"
            elif orig.isdigit():
                assert masked == "9"
            else:
                assert masked == orig


# ── container path ───────────────────────────────────────────────────────────────
def test_container_is_the_section_path_plus_the_label():
    b = by_label()
    assert b["PAN"].container == ("Personal details", "PAN")
    assert b["Aadhaar Number"].container == ("Bank Details", "Aadhaar Number")


# ── the structural guarantee: no field can hold a value ─────────────────────────
def test_value_pair_has_no_field_for_the_value():
    names = {f.name for f in dataclasses.fields(pairs.ValuePair)}
    assert "value" not in names and "text" not in names


def test_no_produced_pair_contains_any_fixture_value():
    vps = pairs.pairs_from_page(PAGE)
    blob = json.dumps([dataclasses.asdict(vp) for vp in vps], ensure_ascii=False)
    for value in SENSITIVE_VALUES:
        assert value not in blob


# ── the disk-level safety net ────────────────────────────────────────────────────
def test_nothing_sgt_i_writes_to_disk_ever_contains_a_fixture_value(tmp_path):
    """The one place SGT-I's enrichment reaches disk today: raw_payload['sgt_i'] ->
    main.py's json.dumps(dataset_msg) -> database.insert_tracker_dump's raw_payload_json TEXT
    column (see 14.10's W1-1/W1-R notes). Round-trips real pairs.py output through that exact
    path into a real sqlite file, then reads the file's own bytes off disk - not a query - so
    nothing (WAL, journal, page cache) escapes the check."""
    vps = pairs.pairs_from_page(PAGE)
    enrichment = {"pairs": [dataclasses.asdict(vp) for vp in vps]}
    raw_payload = {"pan": "ABCPD1234E", "arn": "N/A", "sgt_i": enrichment}

    salt_path = os.path.join(tmp_path, "t.salt")
    security.generate_and_save_salt(salt_path)
    key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
    db = SeraDatabase(os.path.join(tmp_path, "m.db"), key,
                      raw_db_path=os.path.join(tmp_path, "rawPayload.db"))
    db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label="AY 2025-26", arn_number="N/A",
                           capture_method="SGT_shadow", status="Draft", pan="ABCPD1234E",
                           filing_type="ITR-4", raw_payload_json=json.dumps(raw_payload),
                           dataset_key="SGT:ITR:ABCPD1234E:ITR4:AY202526")

    for fname in os.listdir(tmp_path):
        with open(os.path.join(tmp_path, fname), "rb") as f:
            blob = f.read()
        for value in SENSITIVE_VALUES:
            assert value.encode("utf-8") not in blob, f"{value!r} found in {fname}"
