"""
The evidence block on every SGT tracker row (core/sgt/sgt_evidence.py): which read named the client,
which reads set the dataset's fields, what happened to the session, and how the tracker matched the
row to a client record. Also the wrong-client mark and the export. All identifiers fictional.
"""
import json
import os
import tempfile
import unittest

from PIL import Image

import security
from database import SeraDatabase
from core.sgt import sgt_evidence as ev
from core.sgt.sgt_shadow import CAPTURE_METHOD, _session_from_json, _session_to_json
from test_sgt_tracker_rows import CONTACT_URL, FILED_URL, ITR, PROFILE_PAGE, PROFILE_URL, Rig, filed_page

T1, T2 = "42.1.4.0.0.783", "42.1.4.0.0.891"
ARN = "123456789150925"
KEY = "SGT:ITR:ABCPD1234E:ITR4:AY202526"


class TabRig(Rig):
    def at(self, tab, url, page):
        self.clock[0] += 0.4
        self.shade += 7
        self.page = page
        self.sgt.observe(1, ITR, url, frame=Image.new("RGB", (32, 32), (self.shade,) * 3), tab=tab)

    def block(self):
        rows = [x for x in self.sgt.drain() if x["arn"] == ARN]
        return rows[-1]["raw_payload"][ev.EV_KEY]


# ── The pieces ───────────────────────────────────────────────────────────────────
class TestPieces:
    def test_a_page_is_its_path_without_host_or_query(self):
        assert ev.page_of("https://eportal.incometax.gov.in/iec/foservices/#/dashboard?x=AAAAA1111A") == \
            "/iec/foservices/#/dashboard"
        assert len(ev.page_of("https://h.in/" + "a" * 200)) == ev.PAGE_CHARS
        assert ev.page_of(None) == "" and ev.tab_of(T1) == ".0.783" and ev.tab_of(None) == ""

    def test_a_stamp_notes_a_late_or_slow_read_only_when_it_was_one(self):
        assert ev.stamp(10.94, T1, "https://h.in/p", "uia") == {"t": 10.9, "tab": ".0.783", "pg": "/p", "src": "uia"}
        s = ev.stamp(10, None, "", "ocr", late=True, lag=3.26)
        assert s["late"] == 1 and s["lag"] == 3.3
        assert "lag" not in ev.stamp(10, None, "", "uia", lag=1.0)

    def test_the_trail_is_capped_and_does_not_repeat_itself(self):
        s = ev.SessionEv()
        s.note(1, "late", T1)
        s.note(2, "late", T1)
        assert s.trail == [[1, "late", ".0.783"]]
        for i in range(ev.TRAIL_CAP + 5):
            s.note(i, f"e{i}")
        assert len(s.trail) == ev.TRAIL_CAP and s.trail[-1][1] == f"e{ev.TRAIL_CAP + 4}"
        back = ev.SessionEv.from_json(json.loads(json.dumps(s.to_json())))
        assert back.trail == s.trail and back.born == "new"

    def test_flags(self):
        ident = {"pan": {"t": 50, "tab": "a", "spec": "x", "others": 2}}
        data = {"arn": {"t": 40, "tab": "b", "src": "ocr", "late": 1}}
        block = {"born": ev.BORN_PARKED, "id": ident, "d": data, "tr": []}
        assert set(ev.flags_of(block, client_known=True)) == {
            ev.FLAG_TAB_DIFF, ev.FLAG_DATA_FIRST, ev.FLAG_LATE, ev.FLAG_RESUMED, ev.FLAG_OTHER_PAN, ev.FLAG_OCR}
        quiet = {"born": "new", "id": {"pan": {"t": 1, "tab": "a", "spec": "x"}},
                 "d": {"arn": {"t": 2, "tab": "a", "src": "uia"}}, "tr": [[1, "new", "a"]]}
        assert ev.flags_of(quiet, client_known=True) == []
        assert ev.flags_of({"id": {}, "d": {}}, client_known=False) == [ev.FLAG_NO_ID]
        linked = {"id": {"gstin": {"t": 1, "tab": "", "spec": ev.LINK_SPEC}}, "d": {}}
        assert ev.flags_of(linked, client_known=True) == [ev.FLAG_LINK_ID]


# ── What SGT puts on a row ───────────────────────────────────────────────────────
class TestRows:
    def test_a_row_says_which_read_named_the_client_and_which_set_its_fields(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, PROFILE_URL, PROFILE_PAGE)
        r.at(T1, CONTACT_URL, PROFILE_PAGE)
        r.at(T1, FILED_URL, filed_page())
        b = r.block()
        assert b["sid"] == r.sgt._sessions[1].session_id and b["born"] == "new" and b["tabs"] == 1
        assert b["id"]["pan"]["pg"].endswith("/myProfile/profileDetail") and b["id"]["pan"]["src"] == "uia"
        assert b["id"]["pan"]["tab"] == ".0.783" and b["id"]["pan"]["spec"]
        assert set(b["d"]) == {"form", "period", "arn", "status"}
        assert all(d["pg"].endswith("/dashboard/itrStatus") for d in b["d"].values())
        assert b["tr"][0][1] == "new" and b["f"] == []
        assert "ABCPD1234E" not in json.dumps(b)            # the block never repeats the identity

    def test_data_read_before_the_client_was_named_is_flagged(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, FILED_URL, filed_page())
        r.at(T1, PROFILE_URL, PROFILE_PAGE)
        assert ev.FLAG_DATA_FIRST in r.block()["f"]

    def test_a_row_with_no_client_is_flagged(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, FILED_URL, filed_page())
        assert r.block()["f"] == [ev.FLAG_NO_ID]

    def test_data_from_another_tab_than_the_identity_is_flagged(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, PROFILE_URL, PROFILE_PAGE)
        r.at(T2, FILED_URL, filed_page())                   # a tab opened from the session: joins it
        b = r.block()
        assert ev.FLAG_TAB_DIFF in b["f"] and b["tabs"] == 2
        assert [e[1] for e in b["tr"]] == ["new", "tab+"]

    def test_the_end_of_the_session_is_on_the_trail(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, PROFILE_URL, PROFILE_PAGE)
        r.at(T1, FILED_URL, filed_page())
        s = r.sgt._sessions[1]
        r.sgt.end_all("shutdown")
        assert s.ev.trail[-1][1:] == ["end", "", "shutdown"]

    def test_evidence_survives_the_crash_snapshot(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, PROFILE_URL, PROFILE_PAGE)
        r.at(T1, FILED_URL, filed_page())
        s = r.sgt._sessions[1]
        back = _session_from_json(json.loads(json.dumps(_session_to_json(s))))
        assert back.ev.ident == s.ev.ident and back.ev.trail == s.ev.trail
        assert back.slots[0].ev == s.slots[0].ev and back.slots[0].ev


# ── The tracker's side ───────────────────────────────────────────────────────────
class TestTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"))
        pan_col = next(c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN")
        self.pan_col = pan_col["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, pan):
        return self.db.add_client({self.pan_col: pan}, notes="", service_ids=[])

    def insert(self, pan="ABCPD1234E", sid="s1", pc="PC-1", extra=None, status="Draft", arn="N/A"):
        block = {"v": "t", "pc": pc, "sid": sid, "born": "new", "id": {}, "d": {}, "tr": [], "f": []}
        raw = {"source": {"engine": "SGT"}, ev.EV_KEY: block}
        raw.update(extra or {})
        payload = {"pan": pan, "arn": arn, "capture_method": CAPTURE_METHOD, "dataset_key": KEY, "raw_payload": raw}
        return self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label="AY 2025-26", arn_number=arn,
                                           capture_method=CAPTURE_METHOD, status=status, pan=pan,
                                           filing_type="ITR-4", raw_payload_json=json.dumps(payload), dataset_key=KEY)

    def stored(self, dump_id):
        with self.db._connect_raw() as c:
            return json.loads(c.execute("SELECT raw_payload_json FROM tracker_dump WHERE id = ?", (dump_id,)).fetchone()[0])

    def block(self, dump_id):
        return self.stored(dump_id)["raw_payload"][ev.EV_KEY]

    def test_a_row_matched_on_its_own_pan(self):
        cid = self.client("ABCPD1234E")
        res = self.insert()
        assert res["client_id"] == cid
        assert self.block(res["id"])["m"] == {"how": ev.MATCH_ID, "n": 1} and self.block(res["id"])["f"] == []

    def test_a_row_whose_pan_no_client_holds(self):
        res = self.insert()
        assert not res["client_id"]
        b = self.block(res["id"])
        assert b["m"]["how"] == ev.MATCH_NONE and b["f"] == [ev.FLAG_MATCH_NONE]

    def test_a_row_matched_through_another_value_in_its_payload_is_flagged(self):
        """Today's matching (sera_db/srpf.py) takes any PAN-shaped value in the payload when the row's
        own PAN is not a saved client's. The evidence names the value; the export reports the row."""
        other = self.client("AAAAA1111A")
        res = self.insert(extra={"sgt_dataset": {"landlord": "AAAAA1111A"}})
        assert res["client_id"] == other
        b = self.block(res["id"])
        assert b["m"] == {"how": ev.MATCH_OTHER, "n": 2, "by": "AAAAA1111A"} and b["f"] == [ev.FLAG_MATCH_OTHER]
        entry = self.db.sgt_evidence_entries()[0]
        assert entry["reasons"] == [ev.REASON_CLIENT_LACKS_ID, ev.FLAG_MATCH_OTHER]
        assert entry["row_ids"] == ["ABCPD1234E"] and entry["client_ids"] == ["AAAAA1111A"]

    def test_the_evidence_block_is_never_identity_evidence(self):
        self.client("AAAAA1111A")
        payload = {"capture_method": CAPTURE_METHOD, "dataset_key": "SGT:ITR:S1:ITR4:AY202526",
                   "raw_payload": {ev.EV_KEY: {"sid": "s1", "prev": [{"m": {"by": "AAAAA1111A"}}], "f": []}}}
        res = self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label="AY 2025-26",
                                          capture_method=CAPTURE_METHOD, status="Draft", filing_type="ITR-4",
                                          raw_payload_json=json.dumps(payload))
        assert not res["client_id"]

    def test_a_replaced_row_keeps_the_older_block_of_another_session_and_the_mark(self):
        self.client("ABCPD1234E")
        first = self.insert(sid="s1", pc="PC-1")
        assert self.db.mark_tracker_dump_wrong_client(first["id"]) and self.db.is_tracker_dump_marked_wrong(first["id"])
        same = self.insert(sid="s1", pc="PC-1", status="Submitted (Not Verified)", arn=ARN)
        assert "prev" not in self.block(same["id"])                     # the same session: nothing older to keep
        assert self.db.is_tracker_dump_marked_wrong(same["id"])
        for i in range(2, 5):
            last = self.insert(sid=f"s{i}", pc="PC-2", status="Submitted (Not Verified)", arn=ARN)
        b = self.block(last["id"])
        assert [p["sid"] for p in b["prev"]] == ["s3", "s2"] and all("prev" not in p for p in b["prev"])
        assert self.db.is_tracker_dump_marked_wrong(last["id"])

    def test_the_mark_is_set_and_cleared_without_touching_the_row(self):
        res = self.insert()
        before = self.stored(res["id"])
        assert self.db.mark_tracker_dump_wrong_client(res["id"])
        marked = self.stored(res["id"])
        assert marked.pop(ev.WRONG_KEY)["pc"] and marked == before
        assert self.db.mark_tracker_dump_wrong_client(res["id"], False)
        assert self.stored(res["id"]) == before and not self.db.is_tracker_dump_marked_wrong(res["id"])
        assert not self.db.mark_tracker_dump_wrong_client(999_999)

    def test_the_export_holds_only_rows_worth_a_look(self):
        self.client("ABCPD1234E")
        res = self.insert()
        assert self.db.sgt_evidence_entries() == []                     # matched on its own PAN, no flag
        self.db.mark_tracker_dump_wrong_client(res["id"])
        entries = self.db.sgt_evidence_entries()
        assert [e["reasons"] for e in entries] == [[ev.REASON_MARKED]] and entries[0][ev.EV_KEY]["sid"] == "s1"
        path = os.path.join(self.tmp.name, "out.json")
        assert ev.write_export(entries, path, "PC-1", "t", "2026-11-15T10:00:00") == 1
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        assert out["pc"] == "PC-1" and out["rows"][0]["id"] == res["id"] and "raw_payload_json" not in out["rows"][0]

    def test_a_row_older_than_the_evidence_block_is_still_audited(self):
        other = self.client("AAAAA1111A")
        payload = {"pan": "ABCPD1234E", "capture_method": CAPTURE_METHOD, "dataset_key": KEY,
                   "raw_payload": {"sgt_dataset": {"landlord": "AAAAA1111A"}}}
        res = self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label="AY 2025-26",
                                          capture_method=CAPTURE_METHOD, status="Draft", pan="ABCPD1234E",
                                          filing_type="ITR-4", raw_payload_json=json.dumps(payload), dataset_key=KEY)
        assert res["client_id"] == other and ev.EV_KEY not in self.stored(res["id"])["raw_payload"]
        entry = self.db.sgt_evidence_entries()[0]
        assert entry["reasons"] == [ev.REASON_CLIENT_LACKS_ID] and entry[ev.EV_KEY] is None
