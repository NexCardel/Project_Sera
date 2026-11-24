"""tests/test_drs_strip.py - the All Clients "Filing status" strip (core/drs/strip.py)."""
from core.drs.strip import MISSING, build_filing_strips, period_text, strip_text
from core.ltt.feed import LEVELS
from datetime import date

from test_drs import DummyDB


def _h(form, period, status, arn="", at="2026-10-01T00:00:00Z", portal="GST"):
    return {"portal": f"{portal} ({form})", "period_label": period, "status": status, "arn": arn, "created_at": at}


def _strips(db):
    return {cid: [(c.text, c.level, c.slot) for c in chips]
            for cid, chips in build_filing_strips(db, db.search_clients()).items()}


def _world():
    db = DummyDB()
    db._containers[0]["filing_history"] = [
        _h("GSTR-3B", "September (FY 2026-27)", "Draft", at="2026-10-21T00:00:00Z"),
        _h("GSTR-3B", "September (FY 2026-27)", "Filed", "AA1", at="2026-10-20T00:00:00Z"),
        _h("GSTR-3B", "July (FY 2026-27)", "Submitted & Verified", "AA2"),
        _h("GSTR-1", "Jul-Sep (FY 2026-27)", "Submitted & Verified", "AA3"),
    ]
    return db


def test_period_text():
    assert period_text(date(2026, 6, 1), 1, "GSTR3B") == "Jun 26"
    assert period_text(date(2026, 9, 1), 3, "GSTR1") == "Jul–Sep 26"
    assert period_text(date(2026, 3, 1), 12, "ITR4") == "AY 26-27"
    assert period_text(date(2026, 3, 1), 12, "GSTR9") == "FY 25-26"


def test_current_period_first_gaps_shown_and_best_status_per_period():
    s = _strips(_world())[101]
    assert s == [
        ("GSTR-1 · Jul–Sep 26", LEVELS[3], 0),          # quarterly filer: its own cadence
        ("GSTR-3B · Sep 26", LEVELS[3], 0),             # Filed + ARN beats the later Draft
        ("GSTR-3B · Aug 26", MISSING, 1),               # a gap shows instead of vanishing
        ("GSTR-3B · Jul 26", LEVELS[3], 2),             # stops at the first capture: no Jun
    ]


def test_current_period_is_the_latest_any_client_captured():
    db = _world()
    db._containers.append({"pan": "CDEFG3456H", "client_id": 103, "filing_history": [
        _h("GSTR-3B", "October (FY 2026-27)", "Not Submitted")]})
    s = _strips(db)
    assert s[101][1] == ("GSTR-3B · Oct 26", MISSING, 0)
    assert ("GSTR-3B · Oct 26", LEVELS[0], 0) in s[103]


def test_service_implies_the_core_forms_for_a_client_with_no_capture():
    s = _strips(_world())
    # Gamma (GST + Income Tax) has never been captured: its GST forms show as not captured
    assert ("GSTR-1 · Sep 26", MISSING, 0) in s[103] and ("GSTR-3B · Sep 26", MISSING, 0) in s[103]
    assert all(slot == 0 for _t, _l, slot in s[103])
    assert s[102] == []                                  # Income Tax, but no ITR captured by anyone yet


def test_several_gstins_show_the_worst_with_a_count():
    db = _world()
    db._clients[0]["values"][7] = "27ABCDE1234F2Z4"
    db._mcl.append({"id": 7, "label": "GSTIN 2", "field_type": "text", "is_identity": 0})
    for h in db._containers[0]["filing_history"]:
        h["gstin"] = "27ABCDE1234F1Z5"
    chips = build_filing_strips(db, db.search_clients())[101]
    sep = next(c for c in chips if c.text == "GSTR-3B · Sep 26")
    assert sep.registrations == 2 and sep.level == MISSING
    assert "27ABCDE1234F2Z4: Not captured" in sep.detail and "ARN AA1" in sep.detail


def test_strip_text_and_empty_tracker():
    assert strip_text(build_filing_strips(_world(), _world().search_clients())[101]).startswith(
        "GSTR-1 · Jul–Sep 26: Submitted & Verified; ")
    db = DummyDB()
    db._containers = []
    assert build_filing_strips(db, db.search_clients()) == {}
