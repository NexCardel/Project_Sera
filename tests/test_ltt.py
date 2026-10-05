from datetime import date

from core.ltt import feed, rules
from core.ltt.engine import LTTEngine, parse_date, parse_gap
from core.sgt.sgt_toolbox import SUBMIT_LEVELS

TODAY = date(2026, 10, 4)


def _c(pan, name, *hist, company=""):
    return {"pan": pan, "company_name": company, "proprietor_name": name, "gstin": "",
            "filing_history": [dict(zip(("portal", "period_label", "status", "arn", "created_at"), h)) for h in hist]}


def test_levels_match_sgt_ladder():
    assert feed.LEVELS == tuple(SUBMIT_LEVELS)


def test_engine_label_is_trackers_wording():
    e = LTTEngine(parse_date("20-10-2026"), parse_gap("monthly"), 1, 4)
    assert e.locate(TODAY)["current"].filing == "September (FY 2026-27)"
    assert e.locate(TODAY)["previous"][0].filing == "August (FY 2026-27)"


def test_fy_end_lag():
    r = rules.FormRule("ITR", "Yearly", "2026-07-31", "fy_end")
    assert r.engine().locate(TODAY)["current"].filing == "March (FY 2026-27)"   # due Jul 2027
    assert r.engine().locate(date(2026, 6, 1))["current"].filing == "March (FY 2025-26)"
    assert rules.FormRule("GSTR-9", "Yearly", "2026-12-31", "fy_end").lag() == 9


def test_ladder():
    assert feed.ladder("Not e-verified") == "Submitted (Not Verified)"
    assert feed.ladder("Filed") == "Submitted & Verified"
    assert feed.ladder("Form Selected") == "Draft"
    assert feed.ladder("", "N/A") == "Not Submitted"
    assert feed.ladder("Visited", "123456789012345") == "Submitted & Verified"
    assert feed.ladder("Option Expired") == feed.NOT_APPLICABLE


def test_parse_period():
    assert feed.parse_period("June (FY 2026-27)") == ({6}, 2026)
    assert feed.parse_period("Jul-Sep (FY 2026-27)") == ({7, 8, 9}, 2026)
    assert feed.parse_period("AY 2026-27") == (set(), 2025)
    assert feed.parse_period("Q2 FY 2026-27") == ({7, 8, 9}, 2026)
    assert feed.parse_period("") == (set(), None)


def test_form_matching():
    assert rules.form_matches("GSTR-3B", "GSTR3B")
    assert rules.form_matches("ITR", "ITR-4")
    assert not rules.form_matches("GSTR-1", "GSTR-1A")
    assert not rules.form_matches("GSTR-1", "GSTR-3B")


def test_build_rows():
    rs = [rules.FormRule("GSTR-3B", "Monthly", "2026-10-20"),
          rules.FormRule("ITR", "Yearly", "2026-07-31", "fy_end")]
    cs = [
        _c("ABCDE1234F", "Asha", ("GST (GSTR-3B)", "September (FY 2026-27)", "Not e-verified", "A1", "2026-10-03T08:30:00+00:00"),
           ("GST (GSTR-3B)", "August (FY 2026-27)", "Filed", "A0"), company="Asha Traders"),
        _c("PQRST5678K", "Ravi", ("GST (GSTR-3B)", "August (FY 2026-27)", "Filed", "B0"),
           ("Income Tax (ITR-4)", "AY 2026-27", "Draft", "N/A")),
    ]
    rows = {(r["PAN"], r["Return form"]): r for r in feed.build_rows(cs, rs, TODAY)}
    a = rows[("ABCDE1234F", "GSTR-3B")]
    assert (a["Name"], a["Current period"], a["Submit status"]) == (
        "Asha Traders", "September (FY 2026-27)", "Submitted (Not Verified)")
    assert a["Capture date & time"].startswith("2026-10-0")                   # local time of that capture
    assert rows[("PQRST5678K", "GSTR-3B")]["Capture date & time"] == ""
    assert rows[("PQRST5678K", "GSTR-3B")]["Submit status"] == "Not Submitted"   # only August filed
    # ITR current period is March (FY 2026-27) -> AY 2027-28, so AY 2026-27 does not count
    assert rows[("PQRST5678K", "ITR")]["Submit status"] == "Not Submitted"
    assert ("ABCDE1234F", "ITR") not in rows


def test_include_exclude():
    r = rules.FormRule("GSTR-3B", "Monthly", "2026-10-20", include=["NEWCL1234A"], exclude=["ABCDE1234F"])
    cs = [_c("ABCDE1234F", "Asha", ("GST (GSTR-3B)", "August (FY 2026-27)", "Filed", "")),
          _c("NEWCL1234A", "New")]
    pans = [x["PAN"] for x in feed.build_rows(cs, [r], TODAY)]
    assert pans == ["NEWCL1234A"]


def test_rules_roundtrip(tmp_path):
    assert [r.form for r in rules.load_rules(tmp_path)][0] == "GSTR-1"        # defaults
    rs = [rules.FormRule("GSTR-3B", "Monthly", "2026-10-20", include=["abcde1234f"])]
    rules.save_rules(tmp_path, rs)
    assert rules.load_rules(tmp_path)[0].include == ["ABCDE1234F"]
    assert rules.validate([rules.FormRule("X", "Monthly", "bad")]) != ""
    feed.write_csv(tmp_path / "f.csv", [])
    assert (tmp_path / "f.csv").read_text(encoding="utf-8-sig").strip() == ",".join(feed.FIELDS)
