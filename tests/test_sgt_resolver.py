"""
SGT step 1: the toolbox, the spec loader's safety rules, and the resolver.

All identifiers are fictional. 19ABCPD1234E1ZB and 27AAACZ9876K1ZE are made-up GSTINs whose
check character is valid, so they exercise the checksum without belonging to anyone.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from core.sgt import sgt_specs
from core.sgt.sgt_resolver import (drop_future_steps, prepare_lines, resolve_page, resolve_profile_field,
                                   resolve_records, resolve_single)
from core.sgt.sgt_specs import (BUILTIN_FIELDS_PATH, SpecError, SpecStore, build_current_rules, build_field,
                                build_record, build_submit_rules, label_regex, load_registry, safe_compile)
from core.sgt.sgt_toolbox import (CHECKS, MERGES, SUBMIT_LEVELS, TRANSFORMS, ddmmyy_tail_date, period_sort_key,
                                  submit_level)

TODAY = date(2026, 7, 15)
ITR = "Income Tax"
GST = "GST Portal"


def field(**kw):
    raw = {"field": "pan", "labels": ["PAN"], "pattern": "([A-Z]{5}[0-9]{4}[A-Z])", "examples": ["ABCPD1234E"]}
    raw.update(kw)
    return build_field(raw, "profile")


def write_specs(path: Path, profile=(), records=()):
    path.write_text(json.dumps({"profile": list(profile), "records": list(records)}), encoding="utf-8")
    return path


# ── Toolbox ──────────────────────────────────────────────────────────────────────
class TestToolbox:
    @pytest.mark.parametrize("raw,iso", [
        ("12-Mar-1980", "1980-03-12"), ("Sep 15, 2025", "2025-09-15"), ("18/07/2026", "2026-07-18"),
        ("15/09/2026 14:07", "2026-09-15"), ("31-Feb-1980", None), ("tomorrow", None)])
    def test_parse_date(self, raw, iso):
        assert TRANSFORMS["parse_date"](raw) == iso

    @pytest.mark.parametrize("raw,out", [
        ("ITR 4", "ITR-4"), ("itr4", "ITR-4"), ("GSTR3B", "GSTR-3B"), ("CMP 08", "CMP-08"), ("IFF", None)])
    def test_dash_form(self, raw, out):
        assert TRANSFORMS["dash_form"](raw) == out

    def test_ay_label(self):
        assert TRANSFORMS["ay_label"]("A.Y. 2025-26") == "AY 2025-26"
        assert TRANSFORMS["ay_label"]("2025 - 2026") == "AY 2025-26"
        assert TRANSFORMS["ay_label"]("no year") is None

    @pytest.mark.parametrize("raw,out", [
        ("Jun - 2026", "June (FY 2026-27)"), ("Apr - 2026", "April (FY 2026-27)"),
        ("Mar - 2027", "March (FY 2026-27)"), ("Jan 2026", "January (FY 2025-26)"), ("Xyz - 2026", None)])
    def test_gst_month_period_follows_the_april_financial_year(self, raw, out):
        assert TRANSFORMS["gst_month_period"](raw) == out

    def test_ocr_digits(self):
        assert TRANSFORMS["ocr_digits"]("12O45l") == "120451"

    def test_gstin_checksum(self):
        assert CHECKS["gstin_checksum"]("19ABCPD1234E1ZB", TODAY)
        assert CHECKS["gstin_checksum"]("27AAACZ9876K1ZE", TODAY)
        assert not CHECKS["gstin_checksum"]("19ABCPD1234E1ZC", TODAY)
        assert not CHECKS["gstin_checksum"]("19ABCPD1234E1Z", TODAY)

    def test_ack_tail_date(self):
        assert ddmmyy_tail_date("123456789150925") == date(2025, 9, 15)
        assert ddmmyy_tail_date("123456789320925") is None
        assert CHECKS["ddmmyy_tail_today"]("123456789150726", TODAY)
        assert not CHECKS["ddmmyy_tail_today"]("123456789150925", TODAY)

    def test_years_consecutive(self):
        assert CHECKS["years_consecutive"]("AY 2025-26", TODAY)
        assert CHECKS["years_consecutive"]("2025-2026", TODAY)
        assert not CHECKS["years_consecutive"]("2025-27", TODAY)

    def test_names_and_ui_chrome(self):
        assert CHECKS["looks_like_name"]("ASHOK KUMAR SEN", TODAY)
        assert not CHECKS["looks_like_name"]("ABCPD1234E", TODAY)
        assert not CHECKS["looks_like_name"]("12 MAIN ROAD", TODAY)
        assert not CHECKS["not_ui_chrome"]("Search Box Input Field", TODAY)
        assert CHECKS["not_ui_chrome"]("ASHOK KUMAR SEN", TODAY)

    @pytest.mark.parametrize("held,seen,promote", [
        ("RAVI MEHTA", "RAVI KUMAR MEHTA", True),          # middle name filled in
        ("FARHAN ALI KHAN", "FARHAN ALI KHANNA", True),  # header cut the last word
        ("ASHOK SEN", "ASHOK SEN", False),                    # same, nothing to do
        ("ASHOK KUMAR SEN", "ASHOK SEN", False),              # shorter never demotes
        ("RAVI MEHTA", "SUNIL ROY DAS", False),          # longer but someone else
        ("KHAN SEN", "KHANNA SEN", False),                      # only the LAST word may be cut off
    ])
    def test_promote_longer(self, held, seen, promote):
        assert MERGES["promote_longer"](held, seen) is promote
        assert MERGES["latch"](held, seen) is False

    def test_not_future_and_today(self):
        assert CHECKS["not_future"]("2026-07-15", TODAY)
        assert not CHECKS["not_future"]("2026-07-16", TODAY)
        assert CHECKS["date_is_today"]("2026-07-14", TODAY)
        assert not CHECKS["date_is_today"]("2026-07-12", TODAY)


# ── Pattern safety ───────────────────────────────────────────────────────────────
class TestPatternSafety:
    @pytest.mark.parametrize("bad", ["(a+)+$", "(?:\\s+\\w+)*", "(x*)*y", "(a)\\1", "[unclosed", "", "a" * 401])
    def test_refused(self, bad):
        with pytest.raises(SpecError):
            safe_compile(bad)

    @pytest.mark.parametrize("good", ["[A-Z]{5}[0-9]{4}[A-Z]", "^(.+?) to GST Common Portal$", "(\\d{2}/\\d{2}/20\\d{2})",
                                      "([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,})"])
    def test_accepted(self, good):
        safe_compile(good)

    def test_longer_label_wins_over_its_prefix(self):
        rx = label_regex(["Date of Birth", "Date of Birth / Formation"], "start")
        assert rx.search("Date of Birth / Formation").group("rest") == ""

    def test_labels_are_plain_words_not_regex(self):
        rx = label_regex(["Primary Mobile No."], "start")
        assert rx.search("Primary Mobile No.")
        assert not rx.search("Primary Mobile NoX")


# ── Loading ──────────────────────────────────────────────────────────────────────
class TestLoading:
    def test_builtin_file_loads_clean(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        assert reg.errors == ()
        assert {s.field for s in reg.profile} >= {"pan", "name", "gstin", "dob", "email", "phone"}
        assert {r.name for r in reg.records} >= {"itr_dataset_cards", "itr_submit_success", "itr_verification_success", "gst_submit_success", "gst_returns_calendar"}

    def test_every_builtin_spec_has_examples(self):
        data = json.loads(BUILTIN_FIELDS_PATH.read_text(encoding="utf-8"))
        for spec in data["profile"]:
            assert spec.get("examples"), spec["name"]
        for rec in data["records"]:
            assert rec.get("examples"), rec["name"]
            for f in rec["fields"]:
                assert f.get("examples"), f"{rec['name']}.{f['field']}"

    @pytest.mark.parametrize("change,reason", [
        ({"transforms": ["no_such_tool"]}, "unknown transform"),
        ({"checks": ["no_such_check"]}, "unknown check"),
        ({"examples": []}, "has no 'examples'"),
        ({"examples": ["not a pan"]}, "was not captured"),
        ({"counter_examples": ["ABCPD1234E"]}, "counter-example"),
        ({"pattern": "([A-Z]+)+"}, "nests a repeat"),
        ({"take": "sideways"}, "'take' must be"),
        ({"labels": []}, "needs at least one label"),
        ({"colour": "red"}, "unknown key"),
        ({"merge": "longest_wins"}, "unknown merge"),
    ])
    def test_bad_spec_is_refused_with_its_reason(self, tmp_path, change, reason):
        raw = {"name": "tan", "field": "tan", "labels": ["PAN"], "pattern": "([A-Z]{5}[0-9]{4}[A-Z])",
               "examples": ["ABCPD1234E"]}
        raw.update(change)
        reg = load_registry([write_specs(tmp_path / "f.json", [raw])])
        assert reg.profile == ()
        assert len(reg.errors) == 1 and reason in reg.errors[0] and "'tan'" in reg.errors[0]

    def test_override_replaces_by_name_and_disabled_removes(self, tmp_path):
        base = write_specs(tmp_path / "base.json", [
            {"name": "a", "field": "pan", "labels": ["PAN"], "pattern": "([A-Z]{5}[0-9]{4}[A-Z])", "examples": ["ABCPD1234E"]},
            {"name": "b", "field": "email", "labels": ["Email"], "pattern": "(\\S+@\\S+\\.[a-z]{2,})", "examples": ["x@example.com"]},
        ])
        over = write_specs(tmp_path / "over.json", [
            {"name": "a", "field": "pan", "labels": ["Permanent Account Number"], "pattern": "([A-Z]{5}[0-9]{4}[A-Z])",
             "examples": ["ABCPD1234E"]},
            {"name": "b", "disabled": True},
        ])
        reg = load_registry([base, over])
        assert [s.name for s in reg.profile] == ["a"]
        assert reg.profile[0].labels == ("Permanent Account Number",)

    def test_adding_a_datapoint_is_pure_config(self, tmp_path):
        """TAN was deliberately left out of the built-in file: adding it must need no code."""
        tan = {"name": "tan", "field": "tan", "labels": ["TAN", "TAN of the Deductor"],
               "pattern": "(?<![A-Z0-9])([A-Z]{4}[0-9]{5}[A-Z])(?![A-Z0-9])", "confidence": 90,
               "examples": ["MUMA12345B", {"lines": ["TAN of the Deductor", "MUMA12345B"], "expect": "MUMA12345B"}],
               "counter_examples": ["ABCPD1234E"]}
        reg = load_registry([BUILTIN_FIELDS_PATH, write_specs(tmp_path / "o.json", [tan])])
        assert reg.errors == ()
        res = resolve_page(reg, ["TAN : MUMA12345B"], ITR, "https://eportal.incometax.gov.in/x", TODAY)
        assert res.profile["tan"].value == "MUMA12345B"

    def test_unreadable_file_is_ignored_whole(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        reg = load_registry([BUILTIN_FIELDS_PATH, bad])
        assert any("could not be read" in e for e in reg.errors)
        assert len(reg.profile) == len(load_registry([BUILTIN_FIELDS_PATH]).profile)

    def test_bad_edit_keeps_the_previous_good_version(self, tmp_path):
        good = {"name": "a", "field": "pan", "labels": ["PAN"], "pattern": "([A-Z]{5}[0-9]{4}[A-Z])", "examples": ["ABCPD1234E"]}
        path = write_specs(tmp_path / "f.json", [good])
        first = load_registry([path])
        write_specs(path, [dict(good, pattern="([A-Z]+)+")])
        second = load_registry([path], previous=first)
        assert [s.name for s in second.profile] == ["a"]
        assert second.profile[0].pattern.pattern == "([A-Z]{5}[0-9]{4}[A-Z])"
        assert "previous version keeps running" in second.errors[0]

    def test_record_require_must_name_its_own_fields(self):
        with pytest.raises(SpecError, match="require"):
            build_record({"name": "r", "require": ["arn"], "fields": [
                {"field": "form", "take": "anywhere", "pattern": "(ITR-[1-7])", "examples": ["ITR-4"]}]})

    def test_spec_store_reloads_on_edit(self, tmp_path):
        spec = {"name": "a", "field": "pan", "labels": ["PAN"], "pattern": "([A-Z]{5}[0-9]{4}[A-Z])", "examples": ["ABCPD1234E"]}
        path = write_specs(tmp_path / "f.json", [spec])
        logs = []
        store = SpecStore([path], log=logs.append)
        assert [s.name for s in store.get(now=0).profile] == ["a"]
        write_specs(path, [spec, dict(spec, name="b", field="pan2")])
        assert len(store.get(now=1).profile) == 1                 # not re-checked yet
        assert len(store.get(now=10).profile) == 2
        assert any("loaded 2 profile" in m for m in logs)


# ── Resolver ─────────────────────────────────────────────────────────────────────
class TestResolver:
    def test_label_then_value_on_the_next_line(self):
        assert resolve_profile_field(field(), ["PAN", "ABCPD1234E"], TODAY).value == "ABCPD1234E"

    def test_value_on_the_label_line(self):
        assert resolve_profile_field(field(), ["PAN : ABCPD1234E"], TODAY).value == "ABCPD1234E"

    def test_a_longer_label_is_a_different_label(self):
        assert resolve_profile_field(field(), ["PAN Status", "ABCPD1234E"], TODAY) is None
        assert resolve_profile_field(field(), ["PAN of the Employer", "XYZAB9876C"], TODAY) is None

    def test_separated_rest(self):
        spec = field(field="name", labels=["Name"], label_rest="separated", within=1,
                     pattern="^([A-Za-z][A-Za-z .]*[A-Za-z])$", examples=["ASHOK SEN"])
        assert resolve_profile_field(spec, ["Name: ASHOK SEN"], TODAY).value == "ASHOK SEN"
        assert resolve_profile_field(spec, ["Name of the Bank", "STATE BANK"], TODAY) is None

    def test_within_bounds_the_search(self):
        spec = field(within=1)
        assert resolve_profile_field(spec, ["PAN", "Gender", "ABCPD1234E"], TODAY) is None

    def test_repeated_label_line_is_skipped(self):
        assert resolve_profile_field(field(within=1), ["PAN", "PAN", "ABCPD1234E"], TODAY).value == "ABCPD1234E"

    def test_two_values_on_one_page_is_a_conflict(self):
        conflicts = []
        assert resolve_profile_field(field(), ["PAN", "ABCPD1234E", "PAN", "XYZAB9876C"], TODAY, conflicts) is None
        assert conflicts and "2 different values" in conflicts[0]

    def test_icon_glyphs_are_stripped(self):
        assert prepare_lines([" ASHOK SEN ", "   ", "x" * 900]) == ["ASHOK SEN", "x" * 400]

    def test_portal_and_url_scope(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        lines = ["PAN", "ABCPD1234E"]
        assert "pan" in resolve_page(reg, lines, ITR, "https://x/#/dashboard/myProfile/profileDetail", TODAY).profile
        assert "pan" not in resolve_page(reg, lines, ITR, "https://x/#/dashboard/grievances", TODAY).profile
        assert "pan" not in resolve_page(reg, lines, GST, "https://x/myProfile", TODAY).profile

    def test_latched_profile_fields_are_not_looked_for(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        url = "https://x/#/dashboard/myProfile"
        lines = ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E"]
        res = resolve_page(reg, lines, ITR, url, TODAY, skip_profile={"pan"})
        assert "pan" not in res.profile and res.profile["name"].value == "ASHOK KUMAR SEN"

    def test_higher_confidence_spec_wins_across_specs(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        lines = ["ASHOK SEN Individual", "Name", "ASHOK KUMAR SEN"]
        res = resolve_page(reg, lines, ITR, "https://x/#/dashboard/myProfile", TODAY)
        assert res.profile["name"].value == "ASHOK KUMAR SEN" and res.profile["name"].spec == "itr_profile_name"

    def test_output_carries_values_never_page_text(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        lines = ["Some private note on screen", "Welcome ASHOK KUMAR SEN to GST Common Portal", "19ABCPD1234E1ZB"]
        out = json.dumps(resolve_page(reg, lines, GST, "https://services.gst.gov.in/services/auth/fowelcome", TODAY).as_dict())
        assert "private note" not in out and "19ABCPD1234E1ZB" in out


class TestNewBuildingBlocks:
    def test_ay_from_year(self):
        assert TRANSFORMS["ay_from_year"]("2026") == "AY 2026-27"
        assert TRANSFORMS["ay_from_year"]("26") == "AY 2026-27"
        assert TRANSFORMS["ay_from_year"]("x") is None

    def test_period_order(self):
        assert period_sort_key("AY 2026-27") > period_sort_key("AY 2025-26")
        assert period_sort_key("March (FY 2026-27)") > period_sort_key("April (FY 2026-27)")
        assert period_sort_key("April (FY 2027-28)") > period_sort_key("March (FY 2026-27)")
        assert period_sort_key("Original") is None

    def test_latest_period_when_a_page_names_several(self):
        spec = build_field({"field": "period", "take": "anywhere", "multiple": "latest_period",
                            "pattern": "A\\.Y\\. (20[0-9]{2}-[0-9]{2})", "transforms": ["ay_label"],
                            "examples": ["A.Y. 2025-26"]}, "current")
        got = resolve_single(spec, ["A.Y. 2024-25", "A.Y. 2026-27", "A.Y. 2025-26"], TODAY)
        assert got.value == "AY 2026-27"

    def test_link_and_title_sources(self):
        spec = build_field({"field": "form", "take": "anywhere", "source": "link", "pattern": "fo-(itr[1-7])",
                            "case": "insensitive", "transforms": ["dash_form"], "examples": ["fo-itr4"]}, "current")
        assert resolve_single(spec, ["ITR-1"], TODAY, link="https://x/#/fo-itr4-ay2026").value == "ITR-4"
        assert resolve_single(spec, ["fo-itr4"], TODAY, link="https://x/#/dashboard") is None   # page text ignored

    def test_dataset_fields_cannot_use_link_or_multiple(self):
        with pytest.raises(SpecError, match="source"):
            build_field({"field": "form", "take": "anywhere", "source": "link", "pattern": "(x)",
                         "examples": ["x"]}, "dataset")

    def test_compose_with_optional_parts(self):
        from core.sgt.sgt_specs import build_compose, compose_values
        c = build_compose({"name": "{first_name} {middle_name?} {last_name}"})
        assert compose_values({"first_name": "A", "middle_name": "B", "last_name": "C"}, c) == {"name": "A B C"}
        assert compose_values({"first_name": "A", "last_name": "C"}, c) == {"name": "A C"}
        assert compose_values({"first_name": "A"}, c) == {}
        with pytest.raises(SpecError):
            build_compose({"name": "{a?} {b?}"})            # nothing required

    def test_a_label_line_is_never_a_value(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        res = resolve_page(reg, ["Last Name", "Last Name", "PAN", "PAN", "ABCPD1234E"], ITR,
                           "https://x/#/foreturns-ay26/fo-itr4-ay2026/personal_information", TODAY)
        assert "last_name" not in res.profile and res.profile["pan"].value == "ABCPD1234E"

    def test_current_rules_are_validated(self):
        assert build_current_rules({"compose": {"period": "{m} (FY {fy})"}}).compose == (("period", "{m} (FY {fy})"),)
        with pytest.raises(SpecError):
            build_current_rules({"compose": {"period": "{m} {bad-name}"}})
        with pytest.raises(SpecError):
            build_current_rules({"colour": "red"})

    def test_several_returns_on_one_page_keep_the_latest_period(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        lines = ["GSTR-1 / IFF", "May - 2026 Filed Filed on : 10/06/2026", "Jun - 2026 Filed Filed on : 10/07/2026",
                 "GSTR-3B", "Jun - 2026 Not Filed"]
        res = resolve_page(reg, lines, GST, "https://services.gst.gov.in/services/auth/fowelcome", TODAY)
        assert sorted((d.values()["form"], d.values()["period"]) for d in res.datasets) == \
            [("GSTR-1", "June (FY 2026-27)"), ("GSTR-3B", "June (FY 2026-27)")]
        assert res.is_list

    def test_a_disclaimer_year_is_not_the_return(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        res = resolve_page(reg, ["View Filed Returns",
                                 "The e-Filed Returns are available for download /view starting Assessment Year 2013-14.",
                                 "0 Filings till date"], ITR, "https://x/#/dashboard/itrStatus", TODAY)
        assert "period" not in res.current


class TestRecords:
    def reg(self):
        return load_registry([BUILTIN_FIELDS_PATH])

    def test_filed_returns_cards_each_become_a_dataset(self):
        lines = ["A.Y. 2025-26", "Filing Type", "Revised", "done", "e-Verified", "ITR Filed", "ITR :", "ITR-2",
                 "Acknowledgement No :", "123456789150925",
                 "A.Y. 2024-25", "ITR :", "ITR-1", "ITR Filed", "Acknowledgement No :", "987654321310724"]
        rec = next(r for r in self.reg().records if r.name == "itr_dataset_cards")
        got = [d.values() for d in resolve_records(rec, lines, TODAY)]
        assert got == [
            {"period": "AY 2025-26", "form": "ITR-2", "arn": "123456789150925", "filing_type": "Revised",
             "status": "Submitted & Verified"},
            {"period": "AY 2024-25", "form": "ITR-1", "arn": "987654321310724", "status": "Submitted (Not Verified)"},
        ]

    def test_a_record_missing_a_required_field_is_dropped(self):
        rec = next(r for r in self.reg().records if r.name == "itr_dataset_cards")
        assert resolve_records(rec, ["A.Y. 2025-26", "ITR :", "ITR-4"], TODAY) == []     # no ack

    def test_an_old_ack_on_a_confirmation_page_is_not_a_new_filing(self):
        rec = next(r for r in self.reg().records if r.name == "itr_submit_success")
        old = ["Return filed successfully", "Acknowledgement Number :", "123456789150925"]
        new = ["Return filed successfully", "Acknowledgement Number :", "123456789150726"]
        assert resolve_records(rec, old, TODAY) == []
        assert resolve_records(rec, new, TODAY)[0].values() == {"arn": "123456789150726",
                                                                "status": "Submitted (Not Verified)"}

    def test_gst_calendar_takes_the_form_from_the_heading_above(self):
        rec = next(r for r in self.reg().records if r.name == "gst_returns_calendar")
        lines = ["GSTR-3B", "Apr - 2026 NA", "May - 2026 Filed Filed on : 20/06/2026", "Jun - 2026 NA"]
        got = [d.values() for d in resolve_records(rec, lines, TODAY)]
        assert got == [{"form": "GSTR-3B", "period": "May (FY 2026-27)", "status": "Submitted & Verified",
                        "filing_date": "2026-06-20"}]


# The e-Verify picker exactly as UI Automation reads it (tools/vsdc_uia_probe_output/
# uia_probe_20260920_134430.txt), with made-up identifiers.
EVERIFY_PICKER = [
    "Dashboard", "e-File", "Session Time", "1", "1", ":", "4", "8",
    "stepper", "Current Step 1 of 3 in stepper", "Select The Return To Be Verified", "1",
    "Unvisited Step 2 of 3 in stepper", "Select Method For Return Verification", "2",
    "Unvisited Step 3 of 3 in stepper", "Return Successfully Verified", "3",
    "e-Verify / Discard Return", "Please select the return you would like to verify/discard",
    "Showing (2) returns", "Search Box Input Field",
    "Assessment Year", "2025-26", "ITR", "ITR 4", "Filing Type", "Original", "PAN :", "ABCPD1234E",
    "Acknowledgement Number :", "123456789180925", "Filed On :", "Sep 18, 2025",
    "Applicable Act :", "Income Tax Act 1961", "e verify", "Discard",
    "Assessment Year", "2024-25", "ITR", "ITR 1", "Filing Type", "Belated", "PAN :", "ABCPD1234E",
    "Acknowledgement Number :", "987654321100325", "Filed On :", "Mar 10, 2025",
    "Applicable Act :", "Income Tax Act 1961", "e verify", "Discard",
    "Last reviewed and updated on :", "20-Sep-2026",
]


class TestSubmitLadder:
    """One submit status vocabulary on every portal; portal wording is only evidence."""

    def reg(self):
        return load_registry([BUILTIN_FIELDS_PATH])

    def test_the_ladder_orders_the_four_levels(self):
        assert SUBMIT_LEVELS == ("Not Submitted", "Draft", "Submitted (Not Verified)", "Submitted & Verified")
        assert [submit_level(x) for x in SUBMIT_LEVELS] == [0, 1, 2, 3]
        assert submit_level("Filed") == 0 and submit_level(None) == 0      # portal jargon is not a level

    def test_a_status_map_outside_the_ladder_is_refused(self):
        raw = {"field": "status", "take": "anywhere", "pattern": "(Filed)", "map": [["filed", "Filed"]],
               "examples": ["Filed"]}
        with pytest.raises(SpecError, match="submit ladder"):
            build_field(raw, "current")
        with pytest.raises(SpecError, match="needs a 'map'"):
            build_field(dict(raw, map=None), "current")
        assert build_field(dict(raw, map=[["filed", "Submitted & Verified"]]), "current").field == "status"

    def test_every_status_spec_in_the_builtin_file_speaks_the_ladder(self):
        reg = self.reg()
        specs = [f for r in reg.records for f in r.fields] + list(reg.current)
        outs = {out for f in specs if f.field == "status" for _, out in f.value_map if out}
        assert outs and outs <= set(SUBMIT_LEVELS)

    def test_what_an_arn_proves_is_the_portals_own(self):
        rules = self.reg().submit_rules
        assert rules.proven_by_identifier(ITR) == "Submitted (Not Verified)"     # ack before e-Verification
        assert rules.proven_by_identifier(GST) == "Submitted & Verified"         # ARN only once filed
        with pytest.raises(SpecError):
            build_submit_rules({"identifier_proves": {"GST Portal": "Filed"}})

    def test_the_status_keeps_the_portals_wording_as_evidence(self):
        rec = next(r for r in self.reg().records if r.name == "itr_dataset_cards")
        ds = resolve_records(rec, ["A.Y. 2025-26", "Processed with refund", "ITR :", "ITR-4",
                                   "Acknowledgement No :", "123456789150925"], TODAY)[0]
        assert ds.values()["status"] == "Submitted & Verified"
        assert ds.evidence("status") == "Processed with refund"


class TestEverifyPicker:
    def reg(self):
        return load_registry([BUILTIN_FIELDS_PATH])

    def test_every_card_is_a_dataset_awaiting_verification(self):
        rec = next(r for r in self.reg().records if r.name == "itr_dataset_cards")
        got = [d.values() for d in resolve_records(rec, EVERIFY_PICKER, TODAY)]
        assert got == [
            {"period": "AY 2025-26", "form": "ITR-4", "arn": "123456789180925", "pan": "ABCPD1234E",
             "filing_type": "Original", "filing_date": "2025-09-18", "status": "Submitted (Not Verified)"},
            {"period": "AY 2024-25", "form": "ITR-1", "arn": "987654321100325", "pan": "ABCPD1234E",
             "filing_type": "Belated", "filing_date": "2025-03-10", "status": "Submitted (Not Verified)"},
        ]

    def test_the_page_is_a_list_and_keeps_the_latest_period(self):
        res = resolve_page(self.reg(), EVERIFY_PICKER, ITR,
                           "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/eVerifyReturn/eVerifyReturn-al", TODAY)
        assert res.is_list
        assert [(d.record, d.values()["period"]) for d in res.datasets] == [("itr_dataset_cards", "AY 2025-26")]

    def test_the_future_stepper_step_is_never_a_verification(self):
        """'Return Successfully Verified' is drawn on step 1 as the step still to come."""
        res = resolve_page(self.reg(), EVERIFY_PICKER, ITR, "https://x/#/dashboard/eVerifyReturn/eVerifyReturn-al", TODAY)
        assert all(d.values()["status"] != "Submitted & Verified" for d in res.datasets)
        assert all(d.record != "itr_verification_success" for d in res.datasets)

    def test_a_card_status_of_its_own_beats_the_page_fact(self):
        rec = next(r for r in self.reg().records if r.name == "itr_dataset_cards")
        lines = ["Please select the return you would like to verify/discard", "A.Y. 2025-26", "Processed",
                 "ITR :", "ITR-4", "Acknowledgement No :", "123456789150925"]
        assert resolve_records(rec, lines, TODAY)[0].values()["status"] == "Submitted & Verified"


class TestNeverOnAList:
    def test_a_confirmation_record_is_not_read_on_a_list_page(self):
        """An older card's 'Successfully e-Verified' must not confirm the page's latest dataset."""
        reg = load_registry([BUILTIN_FIELDS_PATH])
        lines = ["View Filed Returns",
                 "A.Y. 2025-26", "Pending for e-verification", "ITR :", "ITR-4", "Acknowledgement No :", "123456789150925",
                 "A.Y. 2024-25", "Successfully e-Verified", "ITR :", "ITR-1", "Acknowledgement No :", "987654321310724"]
        res = resolve_page(reg, lines, ITR, "https://x/#/dashboard/itrStatus", TODAY)
        assert {d.record for d in res.datasets} == {"itr_dataset_cards"}
        assert [d.values()["status"] for d in res.datasets] == ["Submitted (Not Verified)"]


class TestFutureSteps:
    def test_unvisited_steps_are_dropped_with_their_label_and_number(self):
        lines = ["Current Step 1 of 3 in stepper", "Select", "1", "Unvisited Step 2 of 3 in stepper", "Pay", "2",
                 "Unvisited Step 3 of 3 in stepper", "Return Successfully Verified", "3", "Body text"]
        assert drop_future_steps(lines) == ["Current Step 1 of 3 in stepper", "Select", "1", "Body text"]

    def test_a_step_without_a_number_keeps_the_next_line(self):
        assert drop_future_steps(["Unvisited Step 2 of 2", "Done", "Acknowledgement Number :"]) == ["Acknowledgement Number :"]

    def test_prepare_lines_applies_it(self):
        assert prepare_lines(["Unvisited Step 3 of 3 in stepper", "Return Successfully Verified", "3"]) == []


class TestGstSubmission:
    def reg(self):
        return load_registry([BUILTIN_FIELDS_PATH])

    def test_filing_with_an_arn_is_submitted_and_verified(self):
        rec = next(r for r in self.reg().records if r.name == "gst_submit_success")
        got = resolve_records(rec, ["Filing Successful", "GSTR-3B filed successfully. ARN: AA070826000001Z"], TODAY)
        assert got[0].values() == {"arn": "AA070826000001Z", "status": "Submitted & Verified"}

    def test_an_arn_in_a_status_table_is_not_a_new_filing(self):
        rec = next(r for r in self.reg().records if r.name == "gst_submit_success")
        assert resolve_records(rec, ["Track Return Status", "ARN", "AA070826000001Z", "Status", "Filed"], TODAY) == []


    def test_dataset_confidence_is_its_weakest_field(self):
        rec = next(r for r in self.reg().records if r.name == "itr_submit_success")
        ds = resolve_records(rec, ["successfully submitted", "Acknowledgement Number :", "123456789150726", "ITR-4"], TODAY)[0]
        assert ds.confidence == 80                                   # the form spec's 80
