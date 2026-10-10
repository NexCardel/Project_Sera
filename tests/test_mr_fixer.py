"""
tools/mr_fixer.py - scan, report, apply, undo. Temp database and the injector's fictional pages only.
"""
import copy
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

from test_inject_unknown_gst_arn import (  # noqa: E402
    HOSTS, URL_GSTR1, all_records, build_world, host_pages, run_inject, write_lines, _page, GSTR1)
from tools import inject_unknown_gst_arn as inj  # noqa: E402
from tools import mr_fixer as mrf  # noqa: E402

DAY = date(2026, 10, 6)
NAMES = [h[0] for h in HOSTS]
GSTINS = [h[1] for h in HOSTS]


class Fx:
    pass


def make_fx(tmp_path, records=None, inject=True):
    fx = Fx()
    fx.w = build_world(tmp_path, records or all_records())
    fx.expected = run_inject(fx.w) if inject else None
    fx.roots = [inj.recover_dir(fx.w.app)]
    fx.arn_re = mrf.arn_regex()
    fx.builder = mrf.PayloadBuilder()
    return fx


@pytest.fixture
def fx(tmp_path):
    return make_fx(tmp_path)


def corpus(fx, roots=None):
    return mrf.Corpus(roots if roots is not None else fx.roots, fx.w.registry, fx.arn_re)


def report(fx, roots=None, **kw):
    targets = mrf.find_targets(fx.w.db, fx.arn_re, **kw)
    return mrf.make_report(fx.w.db, corpus(fx, roots), targets, fx.builder)


def by_case(fx, rows):
    ids = {r["case"]: str(r["row_id"]) for r in fx.expected["rows"]}
    return {c: next(x for x in rows if x["row_id"] == i) for c, i in ids.items()}


def table(db):
    with db._connect_raw() as c:
        return c.execute("SELECT * FROM tracker_dump ORDER BY id").fetchall()


def add_row(fx, arn, session, row_ts, method=None, portal=None, payload_extra=None):
    payload = inj.tracker_payload(session, arn, URL_GSTR1, row_ts)
    payload.update(payload_extra or {})
    rid = inj._insert_row(fx.w.db, arn, session, row_ts, payload, "T")
    if method or portal:
        with fx.w.db._connect_raw() as c:
            c.execute("UPDATE tracker_dump SET capture_method = COALESCE(?, capture_method), portal = COALESCE(?, portal) "
                      "WHERE id = ?", (method, portal, rid))
    return rid


# ── scan ─────────────────────────────────────────────────────────────────────────
def test_scan_finds_the_three_injected_rows(fx):
    t = mrf.find_targets(fx.w.db, fx.arn_re)
    assert sorted(x.id for x in t) == sorted(r["row_id"] for r in fx.expected["rows"])
    assert all(x.day == DAY for x in t)


def test_scan_leaves_out_rows_that_are_not_failed_gst_arn_rows(fx):
    before = {x.id for x in mrf.find_targets(fx.w.db, fx.arn_re)}
    add_row(fx, "AA191026999921T", "s1", "2026-10-06T12:00:00", payload_extra={"gstin": GSTINS[0]})          # has a client
    add_row(fx, "AA191026999922T", "s2", "2026-10-06T12:01:00", portal="ITR Portal (ITR)")                   # not GST
    add_row(fx, "AA191026999923T", "s3", "2026-10-06T12:02:00", method="DOM_Tracker")                        # not SGT
    with fx.w.db._connect_raw() as c:                                                                         # N/A ARN
        c.execute("UPDATE tracker_dump SET arn_number = 'N/A' WHERE arn_number = 'AA191026999923T'")
    add_row(fx, "BADARN", "s4", "2026-10-06T12:03:00")                                                       # not the ARN shape
    rid = add_row(fx, "AA191026999924T", "s5", "2026-10-06T12:04:00")                                       # has a period
    with fx.w.db._connect_raw() as c:
        c.execute("UPDATE tracker_dump SET period_label = 'May (FY 2026-27)', dataset_key = 'GST:X:GSTR1:AY_2026_27_MAY' WHERE id = ?", (rid,))
    assert {x.id for x in mrf.find_targets(fx.w.db, fx.arn_re)} == before


def test_scan_has_no_date_filter_by_default_and_from_to_narrow_it(fx):
    old = add_row(fx, "AA191026999925T", "s6", "2026-10-02T12:00:00")
    assert old in {x.id for x in mrf.find_targets(fx.w.db, fx.arn_re)}
    assert old not in {x.id for x in mrf.find_targets(fx.w.db, fx.arn_re, date_from=date(2026, 10, 5), date_to=date(2026, 10, 6))}
    assert {x.id for x in mrf.find_targets(fx.w.db, fx.arn_re, date_from=date(2026, 10, 2), date_to=date(2026, 10, 2))} == {old}


def test_scan_marks_days_without_a_corpus_file(fx):
    add_row(fx, "AA191026999926T", "s7", "2026-10-02T12:00:00")
    lines = []
    mrf.scan(fx.w.db, corpus(fx), fx.arn_re, None, None, log=lines.append)
    assert any("2026-10-02" in l and "no corpus file" in l for l in lines)
    assert not any("2026-10-06" in l and "no corpus file" in l for l in lines)


# ── report: every result ─────────────────────────────────────────────────────────
def test_injected_cases_come_out_sure_and_right(fx):
    rows = by_case(fx, report(fx))
    for case in "ABC":
        e = next(r for r in fx.expected["rows"] if r["case"] == case)
        r = rows[case]
        assert r["result"] == mrf.SURE, (case, r["reason"])
        assert (r["gstin"], r["client_name"]) == (e["true_gstin"], e["true_name"])
        assert (r["form"], r["period"]) == (e["true_form"], e["true_period"])
        assert r["master_client"] == "new" and r["merges_with_row"] == ""
        assert r["new_dataset_key"].startswith("GST:" + e["true_gstin"] + ":")
    assert "ARN page" in rows["A"]["evidence"] and "session" in rows["C"]["evidence"]


def test_report_scores_against_expected(fx, tmp_path):
    lines = []
    exp = tmp_path / "e.json"
    exp.write_text(json.dumps(fx.expected), encoding="utf-8")
    path, rc = mrf.run_report(fx.w.app, fx.w.db, corpus(fx), fx.arn_re, None, None, exp, tmp_path / "r.csv", log=lines.append)
    assert rc == 0 and path.exists()
    assert sum("sure - right" in l for l in lines) == 3 and not any("WRONG" in l for l in lines)


def test_a_wrong_sure_is_a_failure(fx):
    rows = report(fx)
    next(r for r in rows if r["row_id"] == str(fx.expected["rows"][0]["row_id"]))["gstin"] = "29AAAGC5678L1ZV"
    assert mrf.score(rows, fx.expected, log=lambda m: None) == 1


def test_a_row_on_a_day_with_no_corpus_file_is_not_found(fx):
    rid = add_row(fx, "AA191026999927T", "s8", "2026-10-02T12:00:00")
    r = next(x for x in report(fx) if x["row_id"] == str(rid))
    assert (r["result"], r["reason"]) == (mrf.NOT_FOUND, "no pages for that day")


def test_no_arn_page_and_no_session_is_not_found(fx):
    rid = add_row(fx, "AA191026999928T", "nosuchsession", "2026-10-06T12:00:00")
    r = next(x for x in report(fx) if x["row_id"] == str(rid))
    assert r["result"] == mrf.NOT_FOUND and "no pages of the row's session" in r["reason"]


def test_a_session_whose_last_page_is_long_before_the_row_is_not_found(fx):
    rid = add_row(fx, "AA191026999929T", "host-one", "2026-10-06T14:00:00")          # host-one ends 09:06
    r = next(x for x in report(fx) if x["row_id"] == str(rid))
    assert r["result"] == mrf.NOT_FOUND and "more than 30 min" in r["reason"]


def test_no_header_within_thirty_minutes_is_not_found(tmp_path):
    recs = [_page("lonely", "2026-10-06T09:00:00.000", "2026-10-06T09:00:00.000", URL_GSTR1, GSTR1)]
    f = make_fx(tmp_path, recs + all_records(), inject=False)
    d = tmp_path / "c" / "DEV1"
    write_lines(d / "sdis_2026-10-06.jsonl", recs)
    rid = add_row(f, "AA191026999930T", "lonely", "2026-10-06T09:01:00")
    r = next(x for x in report(f, [tmp_path / "c"]) if x["row_id"] == str(rid))
    assert r["result"] == mrf.NOT_FOUND and "no header" in r["reason"]


def test_client_found_but_no_form_and_period_is_client_only(tmp_path):
    recs = [_page("hdr", "2026-10-06T09:00:00.000", "2026-10-06T09:00:00.000", URL_GSTR1, [f" {NAMES[0]} {GSTINS[0]}", "Dashboard"])]
    f = make_fx(tmp_path, recs, inject=False)
    write_lines(tmp_path / "c" / "DEV1" / "sdis_2026-10-06.jsonl", recs)
    rid = add_row(f, "AA191026999931T", "hdr", "2026-10-06T09:02:00")
    r = next(x for x in report(f, [tmp_path / "c"]) if x["row_id"] == str(rid))
    assert (r["result"], r["gstin"], r["form"], r["period"]) == (mrf.CLIENT_ONLY, GSTINS[0], "", "")
    assert r["new_dataset_key"].endswith("ARN_" + "AA191026999931T") or "ARN" in r["new_dataset_key"]


def _interleaved(tmp_path):
    x = host_pages(NAMES[0], GSTINS[0], "sx", 9)                                    # chrome, 09:00 / 09:05 / 09:06
    y = [_page("sy", "2026-10-06T09:07:00.000", "2026-10-06T09:07:00.000", URL_GSTR1,
               [f" {NAMES[1]} {GSTINS[1]}"] + GSTR1, browser="")]                    # a page with no browser name
    f = make_fx(tmp_path, x + y, inject=False)
    arn = "AA191026999932T"
    arn_page = inj._page_for(x[2], "sarn", "2026-10-06T09:09:00.000", "2026-10-06T09:09:00.000", arn)
    write_lines(tmp_path / "c" / "DEV1" / "sdis_2026-10-06.jsonl", x + y + [arn_page])
    rid = add_row(f, arn, "sarn", "2026-10-06T09:09:30")
    return f, rid


def test_interleaved_clients_need_a_pick_and_are_never_sure(tmp_path):
    f, rid = _interleaved(tmp_path)
    r = next(x for x in report(f, [tmp_path / "c"]) if x["row_id"] == str(rid))
    assert r["result"] == mrf.NEEDS_PICK and r["gstin"] == "" and r["new_dataset_key"] == ""
    got = {c["gstin"] for c in mrf.parse_candidates(r["candidates"])}
    assert got == {GSTINS[0], GSTINS[1]}


def test_a_session_showing_two_clients_needs_a_pick(tmp_path):
    a, b = host_pages(NAMES[0], GSTINS[0], "both", 9), host_pages(NAMES[1], GSTINS[1], "both", 9, minute=10)
    f = make_fx(tmp_path, a + b, inject=False)
    write_lines(tmp_path / "c" / "DEV1" / "sdis_2026-10-06.jsonl", a + b)
    rid = add_row(f, "AA191026999933T", "both", "2026-10-06T09:17:00")
    r = next(x for x in report(f, [tmp_path / "c"]) if x["row_id"] == str(rid))
    assert r["result"] == mrf.NEEDS_PICK and "more than one client" in r["reason"]


def test_a_session_anchor_with_another_header_before_the_row_needs_a_pick(tmp_path):
    x = host_pages(NAMES[0], GSTINS[0], "sx", 9)
    y = [_page("sy", "2026-10-06T09:08:00.000", "2026-10-06T09:08:00.000", URL_GSTR1, [f" {NAMES[1]} {GSTINS[1]}"] + GSTR1)]
    f = make_fx(tmp_path, x + y, inject=False)
    write_lines(tmp_path / "c" / "DEV1" / "sdis_2026-10-06.jsonl", x + y)
    rid = add_row(f, "AA191026999934T", "sx", "2026-10-06T09:09:00")
    r = next(z for z in report(f, [tmp_path / "c"]) if z["row_id"] == str(rid))
    assert r["result"] == mrf.NEEDS_PICK


def test_the_default_run_reads_a_file_never_for_writing(fx):
    p = fx.roots[0] / "TESTDEV1" / "sdis_2026-10-06.jsonl"
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    report(fx)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before


# ── apply and undo ───────────────────────────────────────────────────────────────
def run_apply(fx, path, **kw):
    return mrf.apply_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, confirm=lambda: True,
                            log=kw.pop("log", lambda m: None), builder=fx.builder, **kw)


def write_rep(fx, tmp_path, rows):
    return mrf.write_report(rows, tmp_path / "report_20261009_100000.csv")


def test_apply_fixes_the_rows_like_sgt_and_keeps_the_filing_time(fx, tmp_path):
    rows = report(fx)
    with fx.w.db._connect_raw() as c:
        old = {r[0]: {"created_at": r[1], "raw": r[2]} for r in
               c.execute("SELECT id, created_at, raw_payload_json FROM tracker_dump").fetchall()}
    path = write_rep(fx, tmp_path, rows)
    counts = run_apply(fx, path)
    assert counts["fixed"] == 3
    assert mrf.find_targets(fx.w.db, fx.arn_re) == []
    for e in fx.expected["rows"]:
        with fx.w.db._connect_raw() as c:
            new = c.execute("SELECT id, period_label, capture_method, status, created_at, dataset_key, raw_payload_json, "
                            "captured_by FROM tracker_dump WHERE arn_number = ?", (e["arn"],)).fetchall()
        assert len(new) == 1
        nid, period, method, status, created, key, raw, by = new[0]
        p = json.loads(raw)
        assert (p["gstin"], p["client_name"], period) == (e["true_gstin"], e["true_name"], e["true_period"])
        assert p["pan"] == e["true_gstin"][2:12] and p["filing_type"] == e["true_form"]
        assert method == "SGT_live" and status == inj.STATUS and by == "TEST-PC"
        assert key.startswith("GST:" + e["true_gstin"] + ":") and "SGT" not in key.split(":")[1]
        assert created == old[e["row_id"]]["created_at"] and p["filing_date"] == json.loads(old[e["row_id"]]["raw"])["filing_date"]
        assert p["raw_payload"]["recovered_by"]["tool"] == "mr_fixer"


def test_apply_refuses_while_the_app_is_open_and_without_a_yes(fx, tmp_path):
    path = write_rep(fx, tmp_path, report(fx))
    before = table(fx.w.db)
    with pytest.raises(mrf.FixerError):
        mrf.apply_report(fx.w.app, lambda: fx.w.db, path, running=lambda: True, confirm=lambda: True, builder=fx.builder)
    with pytest.raises(mrf.FixerError):
        mrf.apply_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, confirm=lambda: False,
                         log=lambda m: None, builder=fx.builder)
    assert table(fx.w.db) == before


def test_apply_skips_a_row_that_changed_since_the_report(fx, tmp_path):
    rows = report(fx)
    victim = rows[0]
    with fx.w.db._connect_raw() as c:
        c.execute("UPDATE tracker_dump SET dataset_key = dataset_key || 'X' WHERE id = ?", (victim["row_id"],))
    counts = run_apply(fx, write_rep(fx, tmp_path, rows))
    assert counts["fixed"] == 2 and counts["changed"] == 1
    assert {t.id for t in mrf.find_targets(fx.w.db, fx.arn_re)} == {int(victim["row_id"])}


def test_apply_acts_only_on_the_rows_in_the_report(fx, tmp_path):
    rows = report(fx)
    kept = rows[:2]
    left = int(rows[2]["row_id"])
    counts = run_apply(fx, write_rep(fx, tmp_path, kept))
    assert counts["fixed"] == 2
    assert {t.id for t in mrf.find_targets(fx.w.db, fx.arn_re)} == {left}


def test_apply_skips_rows_that_are_not_sure_unless_asked(fx, tmp_path):
    rows = report(fx)
    rows[0]["result"] = mrf.CLIENT_ONLY
    rows[1]["result"] = mrf.NOT_FOUND
    rows[2]["result"] = mrf.NEEDS_PICK            # no pick given
    assert run_apply(fx, write_rep(fx, tmp_path, rows))["fixed"] == 0
    assert run_apply(fx, write_rep(fx, tmp_path, rows), include_client_only=True)["fixed"] == 1


def test_a_pick_settles_a_needs_pick_row_and_a_wrong_pick_is_refused(tmp_path):
    f, rid = _interleaved(tmp_path)
    rows = report(f, [tmp_path / "c"])
    r = rows[0]
    r["pick"] = "29AAAGC5678L1ZV"                                              # not among the candidates
    path = mrf.write_report(rows, tmp_path / "report_20261009_110000.csv")
    assert run_apply(f, path)["fixed"] == 0
    r["pick"] = GSTINS[0].lower()
    path = mrf.write_report(rows, tmp_path / "report_20261009_110001.csv")
    assert run_apply(f, path)["fixed"] == 1
    with f.w.db._connect_raw() as c:
        raw = c.execute("SELECT raw_payload_json FROM tracker_dump WHERE arn_number = 'AA191026999932T'").fetchone()[0]
    assert json.loads(raw)["gstin"] == GSTINS[0]


def test_a_fix_that_meets_an_existing_row_keeps_the_higher_status_and_undo_brings_both_back(fx, tmp_path):
    rows = report(fx)
    r = rows[0]
    with fx.w.db._connect_raw() as c:
        existing = fx.w.db.insert_tracker_dump(
            client_id=None, portal="GST Portal (GST Return)", period_label=r["period"], arn_number="N/A",
            capture_method="SGT_live", status="Submitted & Verified",
            raw_payload_json=json.dumps({"gstin": r["gstin"], "pan": r["gstin"][2:12], "dataset_key": r["new_dataset_key"]}),
            captured_by="X", pan=r["gstin"][2:12], filing_type=r["form"], dataset_key=r["new_dataset_key"])
    rows = report(fx)
    assert next(x for x in rows if x["row_id"] == r["row_id"])["merges_with_row"].startswith(f"{existing['id']} (")
    before = table(fx.w.db)
    path = write_rep(fx, tmp_path, rows)
    assert run_apply(fx, path)["fixed"] == 3
    with fx.w.db._connect_raw() as c:
        st = c.execute("SELECT status FROM tracker_dump WHERE dataset_key = ?", (r["new_dataset_key"],)).fetchall()
    assert st == [("Submitted & Verified",)]
    mrf.undo_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, log=lambda m: None)
    assert table(fx.w.db) == before


def test_undo_restores_the_original_ids_and_the_injectors_remove_still_works(fx, tmp_path):
    before = table(fx.w.db)
    path = write_rep(fx, tmp_path, report(fx))
    run_apply(fx, path)
    assert table(fx.w.db) != before
    mrf.undo_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, log=lambda m: None)
    assert table(fx.w.db) == before
    inj.remove(fx.w.app, lambda: fx.w.db, running=lambda: False, log=lambda m: None)
    assert table(fx.w.db) == []


def test_undo_refuses_while_the_app_is_open_or_without_an_undo_file(fx, tmp_path):
    path = write_rep(fx, tmp_path, report(fx))
    with pytest.raises(mrf.FixerError):
        mrf.undo_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, log=lambda m: None)
    run_apply(fx, path)
    with pytest.raises(mrf.FixerError):
        mrf.undo_report(fx.w.app, lambda: fx.w.db, path, running=lambda: True, log=lambda m: None)


def test_undo_leaves_a_row_alone_that_now_holds_another_arn(fx, tmp_path):
    path = write_rep(fx, tmp_path, report(fx))
    run_apply(fx, path)
    e = fx.expected["rows"][0]
    with fx.w.db._connect_raw() as c:
        nid = c.execute("SELECT id FROM tracker_dump WHERE arn_number = ?", (e["arn"],)).fetchone()[0]
        c.execute("UPDATE tracker_dump SET arn_number = 'AA191026999999T' WHERE id = ?", (nid,))
    mrf.undo_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, log=lambda m: None)
    with fx.w.db._connect_raw() as c:
        assert c.execute("SELECT arn_number FROM tracker_dump WHERE id = ?", (nid,)).fetchone()[0] == "AA191026999999T"


def test_no_page_text_gstins_or_names_reach_the_logs(fx, tmp_path):
    lines = []
    mrf.scan(fx.w.db, corpus(fx), fx.arn_re, None, None, log=lines.append)
    path, _rc = mrf.run_report(fx.w.app, fx.w.db, corpus(fx), fx.arn_re, None, None, out=tmp_path / "report_20261009_120000.csv",
                               log=lines.append)
    run_apply(fx, path, log=lines.append)
    mrf.undo_report(fx.w.app, lambda: fx.w.db, path, running=lambda: False, log=lines.append)
    text = "\n".join(lines)
    for secret in NAMES + GSTINS + [g[2:12] for g in GSTINS] + ["GSTR-1", "Return submitted"]:
        assert secret not in text
    assert "AA191026" in text                        # ARNs and ids are fine


def test_the_report_is_utf8_with_a_bom_and_round_trips(fx, tmp_path):
    rows = report(fx)
    path = mrf.write_report(rows, tmp_path / "r.csv")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert mrf.read_report(path) == rows


def _shown(db, **kw):
    return {r["arn_number"]: r["client_name"] for r in db.get_tracker_dumps(limit=-1, **kw)}


@pytest.mark.parametrize("slim", [True, False])
def test_corrected_rows_show_slash_dash_after_the_client_name(fx, tmp_path, slim):
    other = add_row(fx, "AA191026999950T", "unrelated", "2026-10-06T12:00:00")           # a row nobody corrected
    run_apply(fx, write_rep(fx, tmp_path, report(fx)))
    shown = _shown(fx.w.db, slim=slim)
    for e in fx.expected["rows"]:
        name = shown[e["arn"]]
        assert name.startswith(e["true_name"] + "/-"), name
        assert name.count("/-") == 1
    assert "/-" not in shown["AA191026999950T"]
    with fx.w.db._connect_raw() as c:                                                     # stored name stays plain
        raws = [json.loads(r[0]) for r in c.execute("SELECT raw_payload_json FROM tracker_dump WHERE arn_number IN (?,?,?)",
                                                    [e["arn"] for e in fx.expected["rows"]]).fetchall()]
    assert all("/-" not in p["client_name"] for p in raws)


def test_a_corrected_row_of_a_known_vault_client_shows_the_mark_too(fx, tmp_path):
    rows = report(fx)
    g = rows[0]["gstin"]
    cols = fx.w.db.get_mcl_columns()
    if not cols:
        pytest.skip("the test database has no client columns")
    # every internal-key column gets the GSTIN's PAN (the insert matches a client by GSTIN or PAN)
    fx.w.db.add_client({c["id"]: g[2:12] for c in cols if c.get("is_internal_pk")} or {cols[0]["id"]: g[2:12]}, "", [])
    run_apply(fx, write_rep(fx, tmp_path, rows))
    row = next(r for r in fx.w.db.get_tracker_dumps(limit=-1) if r["arn_number"] == rows[0]["arn"])
    assert row["client_id"] and row["client_name"].endswith("/-")
