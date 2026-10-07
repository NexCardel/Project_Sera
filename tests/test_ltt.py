from datetime import date, datetime

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


# ---- engine: grace, extensions, holidays, month-end, covers ----

def _eng(deadline, gap, **kw):
    return LTTEngine(parse_date(deadline), parse_gap(gap), **kw)


def test_grace_keeps_overdue_period_current():
    e = _eng("2026-08-31", "yearly", covers="fy_end", grace_days=122)
    assert e.locate(TODAY)["current"].filing == "March (FY 2025-26)"
    assert e.locate(date(2026, 12, 31))["current"].filing == "March (FY 2025-26)"
    assert e.locate(date(2027, 1, 1))["current"].filing == "March (FY 2026-27)"
    assert _eng("2026-08-31", "yearly", covers="fy_end").locate(TODAY)["current"].filing == "March (FY 2026-27)"


def test_extension_moves_deadline_not_filing_month():
    e = _eng("2026-10-20", "monthly", overrides={date(2026, 10, 20): date(2026, 11, 3)})
    cur = e.locate(date(2026, 10, 25))["current"]
    assert cur.end == date(2026, 11, 3) and cur.filing == "September (FY 2026-27)"
    assert e.locate(date(2026, 11, 4))["current"].filing == "October (FY 2026-27)"
    assert e.locate(date(2026, 11, 4))["current"].start == date(2026, 11, 4)


def test_itr_extended_into_next_month_still_files_march():
    e = _eng("2026-07-31", "yearly", covers="fy_end", overrides={date(2026, 7, 31): date(2026, 9, 15)})
    assert e.locate(date(2026, 8, 20))["current"].filing == "March (FY 2025-26)"


def test_holidays_and_sundays_roll_forward():
    e = _eng("2026-10-20", "monthly", holidays=[date(2026, 11, 20)], roll_sundays=True)
    assert e.deadline(1) == date(2026, 11, 21)       # Fri holiday -> Sat
    assert e.deadline(3) == date(2027, 1, 20)        # Wed untouched
    assert e.deadline(2) == date(2026, 12, 21)       # Sun 20 Dec -> Mon
    assert _eng("2026-10-20", "monthly").deadline(2) == date(2026, 12, 20)   # off by default


def test_month_end_follows_month_length():
    e = _eng("2026-04-30", "monthly", month_end=True)
    assert [e.deadline(k) for k in (1, 2, 10)] == [date(2026, 5, 31), date(2026, 6, 30), date(2027, 2, 28)]
    assert _eng("2026-04-30", "monthly").deadline(1) == date(2026, 5, 30)    # default unchanged


def test_covers_calendar_year_and_validation():
    e = _eng("2026-03-31", "yearly", covers="fy_end", fy_start=1)
    assert e.locate(date(2026, 3, 1))["current"].filing == "December (FY 2025)"
    assert _eng("2026-10-20", "monthly", covers="same_month").locate(TODAY)["current"].filing == "October (FY 2026-27)"
    for bad in ({"covers": "nope"}, {"grace_days": -1}):
        try:
            _eng("2026-10-20", "monthly", **bad)
            assert False
        except ValueError:
            pass


# ---- monthly workbook: latest period per client per form ----
from core.ltt import monthly   # noqa: E402


def test_latest_period_only_and_split_by_form():
    cs = [_c("ABCDE1234F", "Asha",
             ("GST (GSTR-3B)", "June (FY 2026-27)", "Filed", "A1", "2026-07-03T08:30:00+00:00"),
             ("GST (GSTR-3B)", "September (FY 2026-27)", "Not e-verified", "A4", "2026-10-03T08:30:00+00:00"),
             ("GST (GSTR3B)", "March (FY 2025-26)", "Filed", "A9", "2026-10-05T08:30:00+00:00"),      # late capture, old period
             ("GST (GSTR-1)", "August (FY 2026-27)", "Draft", "N/A", "2026-09-10T08:30:00+00:00"),
             ("Income Tax (ITR-4)", "AY 2025-26", "Verified", "X", "2025-08-01T00:00:00+00:00"),
             ("Income Tax (ITR-4)", "AY 2026-27", "Draft", "N/A", "2026-08-01T00:00:00+00:00"),
             company="Asha Traders")]
    d = monthly.latest_rows(cs)
    assert list(d) == ["GSTR-1", "GSTR-3B", "ITR-4"]
    assert len(d["GSTR-3B"]) == 1
    r = d["GSTR-3B"][0]
    assert (r["Latest period"], r["Status"], r["ARN"]) == ("September (FY 2026-27)", "Submitted (Not Verified)", "A4")
    assert d["GSTR-1"][0]["Status"] == "Draft" and d["GSTR-1"][0]["ARN"] == ""
    assert (d["ITR-4"][0]["Latest period"], d["ITR-4"][0]["Status"]) == ("AY 2026-27", "Draft")


def test_month_cutoff_freezes_view():
    cs = [_c("ABCDE1234F", "Asha",
             ("GST (GSTR-3B)", "August (FY 2026-27)", "Filed", "A1", "2026-09-20T08:30:00+00:00"),
             ("GST (GSTR-3B)", "September (FY 2026-27)", "Filed", "A2", "2026-10-03T08:30:00+00:00"))]
    assert monthly.latest_rows(cs, date(2026, 9, 1))["GSTR-3B"][0]["Latest period"] == "August (FY 2026-27)"
    assert monthly.latest_rows(cs, date(2026, 10, 1))["GSTR-3B"][0]["Latest period"] == "September (FY 2026-27)"
    assert monthly.latest_rows(cs, date(2026, 8, 1)) == {}


def test_period_end_ordering():
    assert monthly.period_end("Jul-Sep (FY 2026-27)") == date(2026, 9, 1)
    assert monthly.period_end("January (FY 2026-27)") == date(2027, 1, 1)
    assert monthly.period_end("AY 2026-27", True) == date(2026, 3, 1)
    assert monthly.period_end("September") is None


def _sample():
    cs = [_c(f"ABCDE123{i}F", f"Client {i}", ("GST (GSTR-3B)", "September (FY 2026-27)", "Filed", "A")) for i in range(3)]
    cs.append(_c("PQRST5678K", "Ravi", ("Income Tax (ITR-4)", "AY 2026-27", "Draft", "N/A")))
    return monthly.latest_rows(cs, date(2026, 10, 1))


def test_csvs_hold_every_form_and_a_summary_row_per_form(tmp_path):
    import csv
    x = tmp_path / "LTT" / "LTT_2026-10.xlsx"
    monthly.write_csvs(x, _sample())
    data_csv, over_csv = monthly.csv_paths(x)
    rows = list(csv.DictReader(open(data_csv, encoding="utf-8-sig")))
    assert [r["Form"] for r in rows] == ["GSTR-3B"] * 3 + ["ITR-4"]
    over = {r["Form"]: r for r in csv.DictReader(open(over_csv, encoding="utf-8-sig"))}
    assert over["GSTR-3B"]["Clients"] == "3" and over["GSTR-3B"]["Submitted & Verified"] == "3"
    assert over["ITR-4"]["Latest period"] == "AY 2026-27" and over["ITR-4"]["Draft"] == "1"


def test_shell_has_a_sheet_per_form_and_is_rebuilt_when_a_form_appears(tmp_path):
    x = tmp_path / "LTT_2026-10.xlsx"
    forms = list(_sample())
    monthly.build_shell(x, date(2026, 10, 1), forms)
    assert monthly.workbook_matches(x, forms)
    assert not monthly.workbook_matches(x, forms + ["GSTR-1"])
    from openpyxl import load_workbook
    wb = load_workbook(x)
    assert wb.sheetnames == ["Overview", "GSTR-3B", "ITR-4"]
    assert wb["Overview"]["A1"].value == "LTT — October 2026"
    assert wb["GSTR-3B"].max_row == 2          # nothing pre-filled: the tables arrive with the queries


def test_old_layout_is_rebuilt_and_a_stale_lock_file_is_ignored(tmp_path):
    from openpyxl import Workbook, load_workbook
    x = tmp_path / "LTT_2026-10.xlsx"
    old = Workbook()
    old.active.title = "Overview"
    old.save(x)                                                   # no layout stamp = an older build
    (tmp_path / "~$LTT_2026-10.xlsx").write_text("")              # leftover owner file, nobody has it open
    assert not monthly.workbook_matches(x, [])
    monthly.ensure_workbook(x, date(2026, 10, 1), ["GSTR-3B"], excel=False)
    assert monthly.workbook_matches(x, ["GSTR-3B"])
    assert load_workbook(x).properties.title == monthly.FORMAT


# ---- client names: registered name > captured trade name > proprietor name ----

def test_clean_name_strips_labels_only():
    assert feed.clean_name("Trade Name -") == ""
    assert feed.clean_name("Legal Name :") == ""
    assert feed.clean_name("-") == ""
    assert feed.clean_name("Trade Name - ABC Traders") == "ABC Traders"
    assert feed.clean_name("Namrata Traders") == "Namrata Traders"


def test_name_priority_registered_then_trade_then_proprietor():
    both = {"registered_name": "Saved Co", "company_name": "Captured Trade", "proprietor_name": "Owner"}
    assert feed.name_of(both) == ("Saved Co", 0)
    assert feed.name_of({"company_name": "Captured Trade", "proprietor_name": "Owner"}) == ("Captured Trade", 1)
    assert feed.name_of({"company_name": "Trade Name -", "proprietor_name": "Owner"}) == ("Owner", 2)
    assert feed.name_of({"registered_name": "", "company_name": "", "proprietor_name": ""}) == ("", 9)


def test_registered_name_wins_when_containers_merge():
    a = {"pan": "ABCDE1234F", "company_name": "Trade Name -", "proprietor_name": "Asha", "filing_history": []}
    b = {"pan": "ABCDE1234F", "company_name": "", "proprietor_name": "", "registered_name": "Asha Traders Pvt Ltd",
         "filing_history": []}
    assert feed.clients_from([a, b])["ABCDE1234F"]["name"] == "Asha Traders Pvt Ltd"
    assert feed.clients_from([b, a])["ABCDE1234F"]["name"] == "Asha Traders Pvt Ltd"
    assert feed.clients_from([a])["ABCDE1234F"]["name"] == "Asha"


def test_csv_write_survives_a_locked_file_and_skips_unchanged_data(tmp_path, monkeypatch):
    x = tmp_path / "LTT_2026-10.xlsx"
    data = _sample()
    assert monthly.write_csvs(x, data)
    data_csv, over_csv = monthly.csv_paths(x)
    stamp = over_csv.read_text(encoding="utf-8-sig")
    assert monthly.write_csvs(x, data, updated=datetime(2030, 1, 1))
    assert over_csv.read_text(encoding="utf-8-sig") == stamp              # unchanged data: nothing rewritten
    real = monthly.os.replace

    def locked(src, dst):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(monthly.os, "replace", locked)
    changed = {k: v[:1] for k, v in data.items()}
    assert monthly.write_csvs(x, changed) is False                        # Excel holds it: no exception
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(monthly.os, "replace", real)
    assert monthly.write_csvs(x, changed) is True


def test_latest_capture_is_on_top():
    cs = [_c("AAAAA1111A", "Alpha", ("GST (GSTR-3B)", "September (FY 2026-27)", "Filed", "1", "2026-10-02T08:00:00+00:00")),
          _c("BBBBB2222B", "Bravo", ("GST (GSTR-3B)", "September (FY 2026-27)", "Filed", "2", "2026-10-06T08:00:00+00:00")),
          _c("CCCCC3333C", "Charlie", ("GST (GSTR-3B)", "September (FY 2026-27)", "Filed", "3", "2026-10-04T08:00:00+00:00")),
          _c("DDDDD4444D", "Delta", ("GST (GSTR-3B)", "September (FY 2026-27)", "Filed", "4", "2026-10-04T08:00:00+00:00"))]
    names = [r["Name"] for r in monthly.latest_rows(cs)["GSTR-3B"]]
    assert names == ["Bravo", "Charlie", "Delta", "Alpha"]
