"""
tools/inject_unknown_gst_arn.py - the test rows for the GST ARN recovery tool. Temp database and
temp corpus only; every identifier is fictional (the GSTINs pass the checksum the shipped spec uses).
"""
import builtins
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import security  # noqa: E402
from core.sgt.sgt_resolver import resolve_page  # noqa: E402
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, load_registry  # noqa: E402
from core.sgt_i.uia_nodes import lines_from_nodes  # noqa: E402
from database import SeraDatabase  # noqa: E402
from tools import inject_unknown_gst_arn as inj  # noqa: E402

# name, GSTIN, the session id, the hour (6 Oct 2026, local) its pages are read
HOSTS = [
    ("ASHOK KUMAR SEN", "19ABCPD1234E1ZB", "host-one", 9),
    ("RAVI MEHTA", "27AAACZ9876K1ZE", "host-two", 10),
    ("ANITA DESAI", "07AAAFC1234K1ZT", "host-three", 11),
]
URL_WELCOME = "https://services.gst.gov.in/services/auth/fowelcome"
URL_GSTR1 = "https://return.gst.gov.in/returns/auth/gstr1"
GSTR1 = ["GSTR-1 - Details of outward supplies", "FY -", "2026-27", "Tax Period -", "August"]
ARN_RE = re.compile(r"^AA[0-9]{2}10269999[0-9]{2}T$")


def _node(line):
    return {"name": line, "ctype": 50020, "parent": -1, "depth": 0, "rect": [0, 0, 1, 1], "role": "description"}


def _page(session, started, ts, url, lines, browser="chrome"):
    return {"v": 1, "ts": ts, "started": started, "session": session, "portal": "GST Portal", "url": url,
            "link": url, "title": "", "browser": browser, "docs": [[_node(x) for x in lines]]}


def host_pages(name, gstin, session, hour, minute=0, second=0, browser="chrome", dense=False):
    """Three pages. Unless dense, the last one shows no header (the made-up pages' default). A dense
    session shows the header on every page, as the real portal does."""
    head = [f" {name} {gstin}"]
    base = datetime(2026, 10, 6, hour, minute, second)
    t0, t1, t2 = ((base + timedelta(minutes=m)).isoformat(timespec="milliseconds") for m in (0, 5, 6))
    return [
        _page(session, t0, t0, URL_WELCOME, head + [f"Welcome {name} to GST Common Portal", gstin, "View Profile"], browser),
        _page(session, t0, t1, URL_GSTR1, head + GSTR1, browser),
        _page(session, t0, t2, URL_GSTR1, (head if dense else []) + GSTR1, browser),
    ]


def all_records():
    return [r for name, gstin, sid, hour in HOSTS for r in host_pages(name, gstin, sid, hour)]


def dense_records():
    return [r for name, gstin, sid, hour in HOSTS for r in host_pages(name, gstin, sid, hour, dense=True)]


def pages_at(name, gstin, session, stamps, headers, browser="chrome"):
    """One page per time; each shows the GSTIN header only where headers says so."""
    out = []
    for ts, has_header in zip(stamps, headers):
        lines = ([f" {name} {gstin}"] if has_header else []) + GSTR1
        out.append(_page(session, stamps[0], ts, URL_GSTR1, lines, browser))
    return out


# Four clients in ONE browser. The second starts 30 s after the first one's last page, so the first
# client's ARN pages would look back onto the second client's header: the first client is unusable.
BACK_TO_BACK = [
    ("ASHOK KUMAR SEN", "19ABCPD1234E1ZB", "b-one", 9, 0, 0),
    ("RAVI MEHTA", "27AAACZ9876K1ZE", "b-two", 9, 6, 30),
    ("ANITA DESAI", "07AAAFC1234K1ZT", "b-three", 11, 0, 0),
    ("KAVITA RAO", "29AAAGC5678L1ZV", "b-four", 13, 0, 0),
]


def back_to_back_records():
    return [r for name, gstin, sid, hour, minute, second in BACK_TO_BACK
            for r in host_pages(name, gstin, sid, hour, minute, second)]


def write_lines(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_world(tmp_path, records):
    app = tmp_path / "AmanAssociates_Sera"
    live = inj.live_corpus_path(app)
    write_lines(live, records)
    salt = str(tmp_path / "t.salt")
    security.generate_and_save_salt(salt)
    key = security.derive_key_hex("testpass123", security.load_salt(salt))
    db = SeraDatabase(str(app / "master.db"), key, raw_db_path=str(app / "rawPayload.db"))
    return SimpleNamespace(app=app, live=live, db=db, registry=load_registry([BUILTIN_FIELDS_PATH]))


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path, all_records())


def run_inject(w, running=lambda: False, records=None):
    return inj.inject(w.app, lambda: w.db, "TESTDEV1", "TEST-PC", running=running,
                      records=records, registry=w.registry, log=lambda m: None)


def tracker_rows(db):
    with db._connect_raw() as c:
        return c.execute("SELECT id, arn_number, capture_method, created_at, status, portal, "
                         "client_id, raw_payload_json FROM tracker_dump ORDER BY id").fetchall()


def copy_records(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")]


# ── The three cases ──────────────────────────────────────────────────────────────
def test_three_cases_are_written_as_their_plan_says(world):
    expected = run_inject(world)
    rows = {r["case"]: r for r in expected["rows"]}
    assert sorted(rows) == ["A", "B", "C"] and expected["state"] == "done"
    assert [rows[c]["arn_page_written"] for c in "ABC"] == [True, True, False]

    db_rows = {r[0]: r for r in tracker_rows(world.db)}
    for case in "ABC":
        exp = rows[case]
        rid, arn, method, created, status, portal, client, raw = db_rows[exp["row_id"]]
        assert arn == exp["arn"] and ARN_RE.match(arn)
        assert method == "SGT_live" and status == "Submitted (Not Verified)"
        assert portal == "GST Portal (GST Return)" and client is None
        assert json.loads(raw)["session_id"] == f"SGT-{exp['row_session']}"
        assert created == datetime.fromisoformat(exp["row_time"]).astimezone(timezone.utc).isoformat()
        payload = json.loads(raw)
        assert payload["filing_date"] == datetime.fromisoformat(exp["row_time"]).strftime("%Y-%m-%d %H:%M:%S")
        assert payload["gstin"] == "" and payload["pan"] == "" and payload["filing_type"] == "GST Return"
        assert payload["dataset_key"].startswith("GST:SGT") and payload["dataset_key"].endswith(f"ARN_{arn}")
    # A: the ARN page is in the host session. B: a new session. C: the host session, no page.
    assert rows["A"]["row_session"] == rows["A"]["host_session"]
    assert rows["B"]["row_session"] != rows["B"]["host_session"]
    assert rows["B"]["arn_page_session"] == rows["B"]["row_session"]
    assert rows["C"]["row_session"] == rows["C"]["host_session"]


def test_truth_names_the_host_client_and_the_form_and_period(world):
    expected = run_inject(world)
    by_host = {h[2]: h for h in HOSTS}
    for row in expected["rows"]:
        _name, gstin, _sid, _hour = by_host[row["host_session"]]
        assert row["true_gstin"] == gstin and row["true_name"] == _name
        assert row["true_form"] == "GSTR-1" and row["true_period"] == "August (FY 2026-27)"


def test_arn_pages_pass_the_sgt_resolver(world):
    expected = run_inject(world)
    pages = copy_records(corpus_copy(world))
    arn_pages = {p["session"]: p for p in pages if any("Return submitted successfully" in n["name"]
                                                       for doc in p["docs"] for n in doc)}
    for row in expected["rows"]:
        if not row["arn_page_written"]:
            continue
        page = arn_pages[row["arn_page_session"]]
        res = resolve_page(world.registry, lines_from_nodes(page["docs"]), page["portal"], page["url"],
                           inj.TODAY, title=page["title"])
        assert any(d.values().get("arn") == row["arn"] for d in res.datasets)
        if row["case"] == "B":
            assert "gstin" not in res.profile            # the new session never saw a header


def corpus_copy(w):
    return inj.corpus_copy_path(w.app, "TESTDEV1")


def test_copy_keeps_the_day_in_time_order_and_leaves_the_live_file_alone(world):
    before = sha(world.live)
    run_inject(world)
    assert sha(world.live) == before                          # the live capture file is untouched
    copied = copy_records(corpus_copy(world))
    original = all_records()
    assert len(copied) == len(original) + 2                   # A and B pages; C has none
    stamps = [r["ts"] for r in copied]
    assert stamps == sorted(stamps)
    synthetic = [r for r in copied if any("Return submitted successfully" in n["name"]
                                          for doc in r["docs"] for n in doc)]
    assert len(synthetic) == 2
    assert [r for r in copied if r not in synthetic] == original   # every real record, unchanged, in order


# ── Gates ────────────────────────────────────────────────────────────────────────
def test_refuses_while_the_app_is_open(world):
    with pytest.raises(inj.InjectError, match="Sera is open"):
        run_inject(world, running=lambda: True)
    assert not inj.expected_path(world.app).exists() and not corpus_copy(world).exists()
    assert tracker_rows(world.db) == []


@pytest.mark.parametrize("mode", ["shadow", "live"])
def test_refuses_while_sync_is_on(world, mode):
    with world.db._connect() as c:
        c.execute("INSERT INTO _sync_meta(key, value) VALUES ('mode', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,))
    with pytest.raises(inj.InjectError, match="sync mode"):
        run_inject(world)
    assert not inj.expected_path(world.app).exists() and tracker_rows(world.db) == []


def test_pause_sync_turns_it_off_and_remove_turns_it_back(world):
    for opener in (world.db._connect, world.db._connect_raw):
        with opener() as c:
            for key, value in (("device_id", "TESTDEV1"), ("mode", "live")):
                c.execute("INSERT INTO _sync_meta(key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    expected = inj.inject(world.app, lambda: world.db, "TESTDEV1", "TEST-PC", running=lambda: False,
                          registry=world.registry, log=lambda m: None, pause_sync=True)
    assert expected["sync_paused_from"] == "live" and len(tracker_rows(world.db)) == 3
    for opener in (world.db._connect, world.db._connect_raw):
        with opener() as c:
            assert c.execute("SELECT value FROM _sync_meta WHERE key = 'mode'").fetchone()[0] == "off"
            assert c.execute("SELECT COUNT(*) FROM _sync_pending").fetchone()[0] == 0   # nothing captured
    assert inj.remove(world.app, lambda: world.db, running=lambda: False, log=lambda m: None) == 0
    for opener in (world.db._connect, world.db._connect_raw):
        with opener() as c:
            assert c.execute("SELECT value FROM _sync_meta WHERE key = 'mode'").fetchone()[0] == "live"
            assert c.execute("SELECT COUNT(*) FROM _sync_pending").fetchone()[0] == 0
    assert tracker_rows(world.db) == []


def test_a_second_inject_is_refused(world):
    run_inject(world)
    count = len(tracker_rows(world.db))
    with pytest.raises(inj.InjectError, match="already exists"):
        run_inject(world)
    assert len(tracker_rows(world.db)) == count


def test_stops_when_fewer_than_three_hosts_qualify(world):
    two = [r for r in all_records() if r["session"] != "host-three"]
    with pytest.raises(inj.InjectError, match="need 3"):
        run_inject(world, records=two)
    assert not inj.expected_path(world.app).exists()


# ── remove ───────────────────────────────────────────────────────────────────────
def test_remove_deletes_only_its_rows_and_its_files(world):
    unrelated = world.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label="AY 2025-26",
                                             arn_number="123456789150925", capture_method="DOM_Tracker",
                                             status="Submitted", captured_by="TEST-PC", pan="ABCPD1234E",
                                             filing_type="ITR-4")
    before = tracker_rows(world.db)
    run_inject(world)
    assert len(tracker_rows(world.db)) == len(before) + 3
    assert inj.remove(world.app, lambda: world.db, running=lambda: False, log=lambda m: None) == 0
    after = tracker_rows(world.db)
    assert [r[0] for r in after] == [r[0] for r in before]    # same rows, same ids
    assert after[-1][0] == unrelated["id"] and max(r[0] for r in after) == max(r[0] for r in before)
    assert not inj.expected_path(world.app).exists()
    assert not inj.recover_dir(world.app).exists()


def test_remove_refuses_when_a_row_was_changed(world):
    expected = run_inject(world)
    rid = expected["rows"][0]["row_id"]
    with world.db._connect_raw() as c:
        c.execute("UPDATE tracker_dump SET arn_number = 'AA00000000000X' WHERE id = ?", (rid,))
    with pytest.raises(inj.InjectError, match="left in place"):
        inj.remove(world.app, lambda: world.db, running=lambda: False, log=lambda m: None)
    assert inj.expected_path(world.app).exists()
    assert any(r[0] == rid for r in tracker_rows(world.db))


def test_status_before_and_after(world, capsys):
    assert inj.status(world.app) == 0 and "nothing injected" in capsys.readouterr().out
    run_inject(world)
    inj.status(world.app)
    out = capsys.readouterr().out
    assert "case A" in out and "case B" in out and "case C" in out


# ── The live capture file is never opened for writing ─────────────────────────────
def test_live_capture_is_never_opened_for_writing(world, monkeypatch):
    live = world.live.resolve()
    real_open = builtins.open

    def guarded(file, mode="r", *args, **kwargs):
        try:
            hit = Path(os.fspath(file)).resolve() == live
        except (TypeError, OSError):
            hit = False
        if hit and any(ch in mode for ch in "wax+"):
            raise AssertionError("the live capture file was opened for writing")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    run_inject(world)
    inj.remove(world.app, lambda: world.db, running=lambda: False, log=lambda m: None)


# ── Shape of the ARN ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state", ["19", "27", "07"])
def test_arn_is_valid_by_shape_and_marked_fake(state):
    arn = inj.make_arn(state + "ABCPD1234E1ZB", 1)
    assert ARN_RE.match(arn) and arn[2:4] == state


# ── Same-browser look-back: each row must lead back to its own client ─────────────
def nearest_header(w, browser, at_ts):
    """The GSTIN a look-back from at_ts reaches first: the latest GST header in that browser."""
    best = None
    for rec in copy_records(corpus_copy(w)):
        if (rec.get("browser") or "") != browser or rec["ts"] > at_ts or "gst" not in rec["portal"].lower():
            continue
        g = inj._reads(w.registry, rec).profile.get("gstin")
        if g and (best is None or rec["ts"] >= best[0]):
            best = (rec["ts"], g.value)
    return best[1] if best else None


def test_case_b_lands_right_after_its_host(world):
    expected = run_inject(world)
    b = next(r for r in expected["rows"] if r["case"] == "B")
    gap = datetime.fromisoformat(b["row_time"]) - datetime.fromisoformat(b["host_last_page"])
    assert timedelta(0) < gap <= timedelta(seconds=60)


def test_each_row_looks_back_onto_its_own_client(world):
    expected = run_inject(world)
    for row in expected["rows"]:
        assert nearest_header(world, "chrome", row["row_time"]) == row["true_gstin"]


def test_clients_back_to_back_in_one_browser_are_not_mixed_up(tmp_path):
    w = build_world(tmp_path, back_to_back_records())
    expected = run_inject(w)
    used = {r["host_session"] for r in expected["rows"]}
    assert "b-one" not in used                   # its ARN pages would look back onto the next client
    assert len(used) == 3
    for row in expected["rows"]:
        assert nearest_header(w, "chrome", row["row_time"]) == row["true_gstin"]


# ── Dense headers: case B must still find a host ─────────────────────────────────
def test_dense_headers_still_find_a_host_for_case_b(tmp_path):
    w = build_world(tmp_path, dense_records())
    expected = run_inject(w)
    assert sorted(r["case"] for r in expected["rows"]) == ["A", "B", "C"]
    b = next(r for r in expected["rows"] if r["case"] == "B")
    assert b["arn_page_written"]
    # With the header on every page, B's ARN page is a copy of its host's page: it names the same client.
    page = next(p for p in copy_records(corpus_copy(w)) if p["session"] == b["arn_page_session"])
    g = inj._reads(w.registry, page).profile.get("gstin")
    assert g is not None and g.value == b["true_gstin"]
    for row in expected["rows"]:
        assert nearest_header(w, "chrome", row["row_time"]) == row["true_gstin"]


# ── The check counts the ARN pages the script adds for other cases ────────────────
def tie_scenario_records():
    # Client Q's ARN page (case A, 09:03:30) falls inside client P's window. The day's headers alone
    # do not reject P: Q's last header and P's header share one time, which the window excludes.
    q = pages_at("ASHOK KUMAR SEN", "19ABCPD1234E1ZB", "tie-q",
                 ["2026-10-06T08:58:30.000", "2026-10-06T08:59:30.000", "2026-10-06T09:00:30.000"], [True, True, True])
    p = pages_at("RAVI MEHTA", "27AAACZ9876K1ZE", "tie-p",
                 ["2026-10-06T09:00:30.000", "2026-10-06T09:01:30.000", "2026-10-06T09:02:50.000"], [True, False, False])
    r = host_pages("ANITA DESAI", "07AAAFC1234K1ZT", "tie-r", 11)
    s = host_pages("KAVITA RAO", "29AAAGC5678L1ZV", "tie-s", 13)
    return q + p + r + s


def test_an_arn_page_added_for_one_case_blocks_another_case(tmp_path):
    w = build_world(tmp_path, tie_scenario_records())
    expected = run_inject(w)
    used = {r["host_session"] for r in expected["rows"]}
    assert "tie-p" not in used                  # Q's added ARN page would be the nearest header before P's ARN
    assert len(used) == 3
    for row in expected["rows"]:
        assert nearest_header(w, "chrome", row["row_time"]) == row["true_gstin"]
