"""
SGT rows in the tracker dump: shown beside the other engines' rows, tagged, and never able to
replace, drop or purge another engine's row (or be removed by one). All identifiers fictional.
"""
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import security
from database import SeraDatabase
from core.sgt.sgt_shadow import CAPTURE_METHOD, SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore

ITR = "Income Tax"
PROFILE_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"
FILED_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus"
WIZ_PI = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr4-ay2026/personal_information"
PROFILE_PAGE = ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E", "Date of Birth", "12-Mar-1980"]


def filed_page(status="Pending for e-verification", ack="123456789150925"):
    return ["A.Y. 2025-26", "Filing Type", "Original", "done", status, "ITR :", "ITR-4", "Acknowledgement No :", ack]


class Rig:
    def __init__(self, tmp_path):
        self.clock = [1_000_000.0]
        self.page = []
        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                             read_uia=lambda h: {"lines": list(self.page)}, log_dir=tmp_path / "log",
                             clock=lambda: self.clock[0], today=lambda: date(2026, 7, 15), echo=lambda m: None)
        self.shade = 90

    def see(self, url, page):
        self.clock[0] += 0.4
        self.shade += 7
        self.page = page
        self.sgt.observe(1, ITR, url, frame=Image.new("RGB", (32, 32), (self.shade,) * 3))


# ── What SGT hands to the pipeline ───────────────────────────────────────────────
class TestRows:
    def test_a_return_becomes_one_tagged_row(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page())
        rows = r.sgt.drain()
        assert len(rows) == 1
        row = rows[0]
        assert row["capture_method"] == CAPTURE_METHOD
        assert (row["pan"], row["client_name"], row["filing_type"], row["period_label"], row["arn"], row["status"]) == \
            ("ABCPD1234E", "ASHOK KUMAR SEN", "ITR-4", "AY 2025-26", "123456789150925", "Submitted (Not e-Verified)")
        assert row["filing_preference"] == "Original"
        assert row["dataset_key"] == "SGT:ITR:ABCPD1234E:ITR4:AY202526"
        assert row["raw_text"] == "" and row["supersedes_dataset_key"] is None
        assert row["raw_payload"]["source"]["engine"] == "SGT"

    def test_nothing_changes_nothing_is_sent_again(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page())
        r.sgt.drain()
        r.see(FILED_URL, filed_page())
        assert r.sgt.drain() == []

    def test_a_status_promotion_resends_the_same_row(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page("ITR Filed"))
        first = r.sgt.drain()
        r.see(FILED_URL, filed_page("Pending for e-verification"))
        again = r.sgt.drain()
        assert len(first) == len(again) == 1
        assert again[0]["dataset_key"] == first[0]["dataset_key"] and again[0]["status"] == "Submitted (Not e-Verified)"

    def test_a_row_sent_before_the_client_was_known_is_superseded(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, filed_page())                                     # no PAN yet
        early = r.sgt.drain()[0]
        assert early["dataset_key"].startswith("SGT:ITR:S") and early["pan"] == ""
        r.see(PROFILE_URL, PROFILE_PAGE)                                   # now the client is known
        later = [x for x in r.sgt.drain() if x["arn"] == "123456789150925"][-1]
        assert later["pan"] == "ABCPD1234E"
        assert later["dataset_key"] == "SGT:ITR:ABCPD1234E:ITR4:AY202526"
        assert later["supersedes_dataset_key"] == early["dataset_key"]

    def test_a_return_in_progress_is_written_as_soon_as_it_is_complete(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.sgt.drain()
        r.see(WIZ_PI, ["Personal Information", "x", "y"])                  # form + AY from the link
        rows = r.sgt.drain()
        assert [(x["filing_type"], x["period_label"], x["status"]) for x in rows] == [("ITR-4", "AY 2026-27", "In Progress")]

    def test_changing_the_form_moves_the_row_instead_of_leaving_a_stale_one(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(WIZ_PI.replace("itr4", "itr3"), ["x", "y", "z"])
        first = [x for x in r.sgt.drain() if x["filing_type"] == "ITR-3"][0]
        r.see(WIZ_PI, ["x", "y", "z"])                                     # same AY, now ITR-4
        moved = r.sgt.drain()
        assert len(moved) == 1 and moved[0]["filing_type"] == "ITR-4"
        assert moved[0]["supersedes_dataset_key"] == first["dataset_key"]

    def test_several_changes_before_a_send_go_out_once(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page("ITR Filed"))
        r.see(FILED_URL, filed_page("Pending for e-verification"))
        assert len(r.sgt.drain()) == 1


# ── The router hands them over and keeps them out of its own bookkeeping ─────────
class TestRouter:
    def test_pending_rows_go_first_and_never_count_as_captured(self):
        from tests.test_vsdc247_integration import Harness
        h = Harness()
        r = h.router
        fake = MagicMock()
        fake.pending.side_effect = [1, 0]
        fake.pop_dispatch.return_value = {"capture_method": CAPTURE_METHOD, "arn": "123456789150726",
                                          "raw_payload": {}}
        r._sgt = fake
        out = h.tick()
        assert out["capture_method"] == CAPTURE_METHOD and "device_name" in out
        assert "123456789150726" not in r._dispatched_ids          # VSDC247 may still save its own

    def test_rows_are_drained_even_with_every_engine_off(self):
        from tests.test_vsdc247_integration import Harness
        h = Harness()
        r = h.router
        r.apply_engine_settings(False, False, False, sgt="off")
        fake = MagicMock()
        fake.pending.return_value = 1
        fake.pop_dispatch.return_value = {"capture_method": CAPTURE_METHOD, "arn": "N/A", "raw_payload": {}}
        r._sgt = fake
        assert h.tick()["capture_method"] == CAPTURE_METHOD

    def test_drain_for_shutdown(self):
        from tests.test_vsdc247_integration import Harness
        r = Harness().router
        assert r.drain_sgt_dispatches() == []
        r._sgt = MagicMock()
        r._sgt.drain.return_value = [{"capture_method": CAPTURE_METHOD, "raw_payload": {}}]
        rows = r.drain_sgt_dispatches()
        assert len(rows) == 1 and "device_name" in rows[0]


# ── The database keeps the engines apart ─────────────────────────────────────────
class TestDatabaseIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"))
        # A replacement rebuilds report files in project folders - not wanted from a test.
        self.db.rebuild_raw_payload_dumps_file = lambda: 0

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with self.db._connect_raw() as c:
            return c.execute("SELECT capture_method, arn_number, status, unassigned_identity, dataset_key "
                             "FROM tracker_dump ORDER BY id").fetchall()

    def insert(self, method, arn="N/A", status="Submitted", pan="ABCPD1234E", key=None, period="AY 2025-26"):
        payload = {"pan": pan, "arn": arn, "capture_method": method, "dataset_key": key}
        return self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label=period, arn_number=arn,
                                           capture_method=method, status=status, pan=pan, filing_type="ITR-4",
                                           raw_payload_json=json.dumps(payload), dataset_key=key)

    def test_same_arn_seconds_apart_keeps_both_engines_rows(self):
        self.insert("VSDC247_itr_ack", arn="123456789150925")
        self.insert(CAPTURE_METHOD, arn="123456789150925", key="SGT:ITR:ABCPD1234E:ITR4:AY202526")
        self.insert("VSDC-X_itr_submitted", arn="123456789150925")        # still VSDC's own dedupe
        methods = [r[0] for r in self.rows()]
        assert methods == ["VSDC247_itr_ack", CAPTURE_METHOD]

    def test_an_sgt_row_never_removes_vsdc247s_pending_placeholder(self):
        self.insert("VSDC247_itr_ack", arn="123456789150925", pan="")        # client unknown
        assert self.rows()[0][3] == "Pending_123456789150925"
        self.insert(CAPTURE_METHOD, arn="123456789150925", key="SGT:ITR:ABCPD1234E:ITR4:AY202526")
        assert "Pending_123456789150925" in [r[3] for r in self.rows()]

    def test_an_sgt_row_never_purges_a_vsdc_draft(self):
        self.insert("VSDC-X_itr_personal_info", status="Visited / In Progress")
        self.insert(CAPTURE_METHOD, status="In Progress", key="SGT:ITR:ABCPD1234E:ITR4:AY202526")
        assert [r[0] for r in self.rows()] == ["VSDC-X_itr_personal_info", CAPTURE_METHOD]

    def test_a_vsdc_row_never_purges_an_sgt_row(self):
        self.insert(CAPTURE_METHOD, status="Visited / In Progress", key="SGT:ITR:ABCPD1234E:ITR4:AY202526")
        self.insert("VSDC-X_itr_personal_info", status="Visited / In Progress")
        assert CAPTURE_METHOD in [r[0] for r in self.rows()]

    def test_sgt_rows_replace_only_themselves(self):
        key = "SGT:ITR:ABCPD1234E:ITR4:AY202526"
        self.insert(CAPTURE_METHOD, status="In Progress", key=key)
        self.insert(CAPTURE_METHOD, status="Submitted", arn="123456789150925", key=key)
        sgt = [r for r in self.rows() if r[0] == CAPTURE_METHOD]
        assert len(sgt) == 1 and sgt[0][2] == "Submitted"

    def test_superseded_delete_is_sgt_only(self):
        self.insert("VSDC-X_itr_submitted", arn="123456789150925")
        vsdc_key = self.rows()[0][4]
        assert self.db.delete_sgt_rows_by_dataset_key(vsdc_key) == 0          # not an SGT key: refused
        self.insert(CAPTURE_METHOD, status="In Progress", key="SGT:ITR:SABC:ITR4:AY202526")
        assert self.db.delete_sgt_rows_by_dataset_key("SGT:ITR:SABC:ITR4:AY202526") == 1
        assert [r[0] for r in self.rows()] == ["VSDC-X_itr_submitted"]


def test_tracker_source_filter():
    from ui.windows.tracker_dump_window import _capture_method_color, _passes_source_filter
    sgt_row, vsdc_row = {"capture_method": CAPTURE_METHOD}, {"capture_method": "VSDC-X_itr"}
    assert not _passes_source_filter(sgt_row, "Hide SGT", False)
    assert _passes_source_filter(vsdc_row, "Hide SGT", False)
    assert _passes_source_filter(sgt_row, "SGT Only", False) and not _passes_source_filter(vsdc_row, "SGT Only", False)
    mixed = {"filing_history": [{"capture_method": CAPTURE_METHOD}, {"capture_method": "VSDC247_itr_ack"}]}
    only_sgt = {"filing_history": [{"capture_method": CAPTURE_METHOD}]}
    assert _passes_source_filter(mixed, "Hide SGT", True) and not _passes_source_filter(only_sgt, "Hide SGT", True)
    assert _passes_source_filter(mixed, "SGT Only", True)
    assert _capture_method_color(CAPTURE_METHOD) == "#FFA657" and _capture_method_color("VSDC247_itr_ack") == "#D2A8FF"


def test_main_skips_toasts_for_sgt_rows():
    """The pill already shows SGT captures; main must not add a toast / tray balloon per row."""
    import inspect
    import main
    src = inspect.getsource(main.SeraApp._on_capture_processed_ui) if hasattr(main, "SeraApp") else ""
    if not src:
        pytest.skip("main's app class name differs")
    assert 'startswith("SGT")' in src
