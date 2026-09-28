"""
Tests for core/sgt_i/expectations.py (blueprint 14.4 step 8, W9-2): due filings as prime
candidates, an already-filed period flagged revision/duplicate, and known-value anchoring
(self-healing) - all second opinion only, never a Core value. Every PAN/GSTIN/name here is
fictional; the temp DB pair uses the same table shapes database.py creates (test_sgt_i_sera_data.py).
"""

import json

import pytest

sqlcipher3 = pytest.importorskip("sqlcipher3.dbapi2")

from core.sgt_i import make_observation
from core.sgt_i import sera_data as sd
from core.sgt_i.expectations import (ExpectationsComponent, classify_dataset, find_synonyms,
                                     label_is_known, load_config)
from core.sgt_i import page_map as pm

HEX_KEY = "00" * 32
CFG = load_config()


def _connect(path):
    conn = sqlcipher3.connect(path)
    conn.execute("PRAGMA key = \"x'%s'\";" % HEX_KEY)
    return conn


def _make_master_db(path, dob="15-06-1985"):
    conn = _connect(path)
    conn.execute("""CREATE TABLE mcl_columns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
        field_type TEXT NOT NULL DEFAULT 'text', is_identity INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT, notes TEXT, created_at TEXT, updated_at TEXT,
        is_archived INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE client_values (
        client_id INTEGER NOT NULL, column_id INTEGER NOT NULL, value TEXT,
        PRIMARY KEY (client_id, column_id))""")
    conn.execute("""CREATE TABLE services (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE client_services (
        client_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
        PRIMARY KEY (client_id, service_id))""")

    cols = [("NAME OF PROPRIETOR", 2), ("PAN", 3), ("GSTIN", 4), ("Date of Birth", 5),
            ("GST_Password", 9)]
    col_ids = {}
    for label, order in cols:
        cur = conn.execute("INSERT INTO mcl_columns (label, sort_order) VALUES (?, ?)",
                            (label, order))
        col_ids[label] = cur.lastrowid

    conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived) "
                 "VALUES (1, '2026-01-01', '2026-01-01', 0)")
    conn.execute("INSERT INTO client_values VALUES (1, ?, 'Alpha Traders')", (col_ids["NAME OF PROPRIETOR"],))
    conn.execute("INSERT INTO client_values VALUES (1, ?, 'ABCDE1234F')", (col_ids["PAN"],))
    conn.execute("INSERT INTO client_values VALUES (1, ?, '27ABCDE1234F1Z0')", (col_ids["GSTIN"],))
    conn.execute("INSERT INTO client_values VALUES (1, ?, ?)", (col_ids["Date of Birth"], dob))
    conn.execute("INSERT INTO client_values VALUES (1, ?, 'hunter2')", (col_ids["GST_Password"],))

    conn.execute("INSERT INTO services (id, name) VALUES (1, 'GSTR-3B')")
    conn.execute("INSERT INTO client_services VALUES (1, 1)")
    conn.commit()
    conn.close()


def _make_raw_db(path, rows=()):
    conn = _connect(path)
    conn.execute("""CREATE TABLE tracker_dump (
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER, portal TEXT,
        period_label TEXT, arn_number TEXT, status TEXT)""")
    for r in rows:
        conn.execute("INSERT INTO tracker_dump (client_id, portal, period_label, arn_number, status) "
                     "VALUES (?, ?, ?, ?, ?)", r)
    conn.commit()
    conn.close()


@pytest.fixture()
def sera(tmp_path):
    master = str(tmp_path / "master.db")
    raw = str(tmp_path / "rawPayload.db")
    _make_master_db(master)
    _make_raw_db(raw, rows=[(1, "GST", "August", "AA270826000001Z0", "submitted")])
    return sd.SeraData(master, HEX_KEY, raw, cache_ttl_sec=1000.0)


class _Ctx:
    def __init__(self):
        self.data = None

    def enrich(self, data):
        json.dumps(data)     # must be plain JSON, like the real host requires
        self.data = data

    def ask_more_reads(self, n=1):
        pass

    def read_harder(self, s=30):
        pass


def _card(**values):
    return {"record": "gst_dataset_cards", "confidence": 95, "values": values}


def _obs(lines=(), datasets=(), profile=None, portal="GST Portal", session="s1"):
    return make_observation(session_id=session, portal=portal, url="https://example.test/p", title="t",
                            source="uia", lines=list(lines),
                            result={"profile": {}, "datasets": list(datasets),
                                    "current": {}, "is_list": bool(datasets), "conflicts": []},
                            profile=profile if profile is not None else {"pan": "ABCDE1234F"},
                            draft={}, ts=1.0, today="2026-09-28")


# ── classify_dataset: due, revision, duplicate ───────────────────────────────────────────────────

def test_due_service_with_nothing_filed_is_prime_candidate():
    exp = classify_dataset("GST Portal", "GSTR-3B", "September", "", ["GSTR-3B"], [], CFG)
    assert exp.verdict == "prime_candidate"


def test_already_filed_period_is_duplicate(sera):
    filed = sera.tracker_rows(client_id=1)
    exp = classify_dataset("GST Portal", "GSTR-3B", "August", "Submitted", ["GSTR-3B"], filed, CFG)
    assert exp.verdict == "duplicate"


def test_already_filed_period_with_revision_wording_is_revision(sera):
    filed = sera.tracker_rows(client_id=1)
    exp = classify_dataset("GST Portal", "GSTR-3B", "August", "Revised return filed",
                           ["GSTR-3B"], filed, CFG)
    assert exp.verdict == "revision"


def test_a_period_with_no_due_service_and_nothing_filed_is_no_expectation():
    assert classify_dataset("GST Portal", "GSTR-1", "September", "", ["GSTR-3B"], [], CFG) is None


def test_a_different_period_is_not_treated_as_already_filed(sera):
    filed = sera.tracker_rows(client_id=1)
    exp = classify_dataset("GST Portal", "GSTR-3B", "September", "", ["GSTR-3B"], filed, CFG)
    assert exp.verdict == "prime_candidate"


# ── label_is_known / find_synonyms: self-healing ─────────────────────────────────────────────────

def test_label_is_known_by_shared_word():
    assert label_is_known("PAN Number", "pan", CFG)
    assert not label_is_known("DOB", "Date of Birth", CFG)


def test_label_is_known_by_config_synonym():
    assert label_is_known("Permanent Account No", "pan", CFG)
    assert label_is_known("Acknowledgement No", "ack", CFG)


def _page_from_lines(lines):
    boxes = [{"text": t, "x": 20, "y": 24 * i, "width": 8 * max(1, len(t)), "height": 18}
             for i, t in enumerate(lines)]
    return pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0)


def test_find_synonyms_flags_a_new_wording_for_a_known_value():
    page = _page_from_lines(["DOB", "15-06-1985"])
    out = find_synonyms(page, [("Date of Birth", "15-06-1985")], CFG)
    assert out == [{"field": "Date of Birth", "container": ["DOB"]}]


def test_find_synonyms_does_not_flag_a_label_that_already_reads_as_the_field():
    page = _page_from_lines(["PAN", "ABCDE1234F"])
    out = find_synonyms(page, [("pan", "ABCDE1234F")], CFG)
    assert out == []


def test_find_synonyms_never_leaks_the_value_itself():
    page = _page_from_lines(["DOB", "15-06-1985"])
    out = find_synonyms(page, [("Date of Birth", "15-06-1985")], CFG)
    assert "15-06-1985" not in json.dumps(out)


# ── the component, end to end ────────────────────────────────────────────────────────────────────

def test_component_enriches_prime_candidate_for_a_due_unfiled_period(sera):
    comp = ExpectationsComponent(sera)
    ctx = _Ctx()
    obs = _obs(datasets=[_card(form="GSTR-3B", period="September")])
    comp.observe(obs, ctx)
    assert ctx.data["expectations"] == [{"dataset": "GSTR-3B September", "expectation": "prime_candidate"}]


def test_component_enriches_duplicate_for_an_already_filed_period(sera):
    comp = ExpectationsComponent(sera)
    ctx = _Ctx()
    obs = _obs(datasets=[_card(form="GSTR-3B", period="August", status="Submitted")])
    comp.observe(obs, ctx)
    assert ctx.data["expectations"] == [{"dataset": "GSTR-3B August", "expectation": "duplicate"}]


def test_component_finds_a_synonym_only_once_per_session(sera):
    comp = ExpectationsComponent(sera)
    ctx = _Ctx()
    obs = _obs(lines=["DOB", "15-06-1985"])
    comp.observe(obs, ctx)
    assert ctx.data["synonyms"] == [{"field": "Date of Birth", "container": ["DOB"]}]
    ctx2 = _Ctx()
    comp.observe(obs, ctx2)
    assert ctx2.data is None      # already reported this session - nothing new to say


def test_component_says_nothing_for_an_unidentified_client(sera):
    comp = ExpectationsComponent(sera)
    ctx = _Ctx()
    obs = _obs(datasets=[_card(form="GSTR-3B", period="September")], profile={})
    comp.observe(obs, ctx)
    assert ctx.data is None


def test_component_output_is_json_safe_and_never_holds_a_raw_pan_value(sera):
    comp = ExpectationsComponent(sera)
    ctx = _Ctx()
    obs = _obs(lines=["DOB", "15-06-1985"], datasets=[_card(form="GSTR-3B", period="September")])
    comp.observe(obs, ctx)
    dumped = json.dumps(ctx.data)
    assert "ABCDE1234F" not in dumped
    assert "15-06-1985" not in dumped
