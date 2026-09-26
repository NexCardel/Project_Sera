"""
SGT live (2026-09-26): the main capture engine. Its rows take the tracker's canonical keys and
merge with the other engines' rows for the same filing; shadow rows already in the tracker are
converted; the office switches over once; a submission whose client never became known raises
the phone alert. All identifiers fictional.
"""
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import security
from database import SeraDatabase
from core.dataset_key import compute_dataset_key
from core.sgt.sgt_shadow import CAPTURE_METHODS, MODE_LIVE, SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.vsdc.vsdc_engines import (ENGINE_DEFAULTS, SGT_LIVE_ROLLOUT_SETTING, apply_sgt_live_rollout,
                                    read_engine_flags)

ITR = "Income Tax"
PROFILE_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"
FILED_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus"
PROFILE_PAGE = ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E", "Date of Birth", "12-Mar-1980"]
LIVE = CAPTURE_METHODS[MODE_LIVE]
KEY = "ITR:ABCPD1234E:ITR4:AY_2025_26"


def filed_page(status="Pending for e-verification", ack="123456789150925"):
    return ["A.Y. 2025-26", "Filing Type", "Original", "done", status, "ITR :", "ITR-4", "Acknowledgement No :", ack]


class Rig:
    def __init__(self, tmp_path, mode=MODE_LIVE, alert=None):
        self.clock = [1_000_000.0]
        self.page = []
        self.hud = []
        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                             read_uia=lambda h: {"lines": list(self.page)}, log_dir=tmp_path / "log",
                             clock=lambda: self.clock[0], today=lambda: date(2026, 7, 15), echo=lambda m: None,
                             notify=lambda *a: self.hud.append(a), mode=mode, alert_unattributed=alert)
        self.shade = 90

    def see(self, url, page):
        self.clock[0] += 0.4
        self.shade += 7
        self.page = page
        self.sgt.observe(1, ITR, url, frame=Image.new("RGB", (32, 32), (self.shade,) * 3))


# ── The rows live SGT hands to the pipeline ──────────────────────────────────────
class TestLiveRows:
    def test_a_live_row_takes_the_trackers_canonical_key(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page())
        row = r.sgt.drain()[0]
        assert row["capture_method"] == "SGT_live"
        assert row["dataset_key"] == KEY
        # the key the database would compute for any engine's row of this filing
        assert row["dataset_key"] == compute_dataset_key("Income Tax (ITR-4)", "ABCPD1234E", "ITR-4", "AY 2025-26")
        assert row["raw_payload"]["source"]["mode"] == "live"
        assert any(ev[2].endswith("SGT (Live)") for ev in r.hud)

    def test_before_the_client_is_known_the_key_is_the_sessions_then_superseded(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, filed_page())                                  # no PAN yet
        early = r.sgt.drain()[0]
        assert early["dataset_key"].startswith("ITR:SGT") and early["dataset_key"].endswith(":ITR4:AY_2025_26")
        r.see(PROFILE_URL, PROFILE_PAGE)
        later = [x for x in r.sgt.drain() if x["arn"] == "123456789150925"][-1]
        assert later["dataset_key"] == KEY and later["supersedes_dataset_key"] == early["dataset_key"]

    def test_a_dataset_known_only_by_its_arn_is_keyed_by_it(self, tmp_path):
        r = Rig(tmp_path)
        s = MagicMock(portal=ITR, session_id="abc", confirmed=True,
                      profile={"pan": {"value": "ABCPD1234E"}})
        assert r.sgt.live_key(s, {"arn": "123456789150925"}) == "ITR:ABCPD1234E:FORM:ARN_123456789150925"

    def test_shadow_mode_is_unchanged(self, tmp_path):
        r = Rig(tmp_path, mode="shadow")
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page())
        row = r.sgt.drain()[0]
        assert row["capture_method"] == "SGT_shadow" and row["dataset_key"] == "SGT:ITR:ABCPD1234E:ITR4:AY202526"


# ── Client never identified: the phone alert (VSDC247's client-unknown path) ─────
class TestUnknownClientAlert:
    CARD = ["View Filed Returns", "A.Y. 2025-26", "PAN :", "ABCPD1234E", "Filing Type", "Original",
            "done", "Pending for e-verification", "ITR :", "ITR-4", "Acknowledgement No :", "123456789150925"]

    def test_live_alerts_once_for_a_submission_written_with_no_client(self, tmp_path):
        sent = []
        r = Rig(tmp_path, alert=lambda form, page: sent.append((form, page)))
        r.see(FILED_URL, self.CARD)
        r.sgt.end_all("test")
        rows = r.sgt.drain()
        assert [(x["pan"], x["arn"]) for x in rows] == [("", "123456789150925")]
        assert sent == [("ITR-4", FILED_URL)]

    def test_no_alert_when_the_client_is_known(self, tmp_path):
        sent = []
        r = Rig(tmp_path, alert=lambda *a: sent.append(a))
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(FILED_URL, filed_page())
        r.sgt.end_all("test")
        assert sent == []

    def test_shadow_never_alerts(self, tmp_path):
        sent = []
        r = Rig(tmp_path, mode="shadow", alert=lambda *a: sent.append(a))
        r.see(FILED_URL, self.CARD)
        r.sgt.end_all("test")
        assert sent == []


# ── The database merges live rows with everyone else's ───────────────────────────
class TestDatabaseMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"))
        self.db.rebuild_raw_payload_dumps_file = lambda: 0

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with self.db._connect_raw() as c:
            return c.execute("SELECT capture_method, arn_number, status, dataset_key, raw_payload_json "
                             "FROM tracker_dump ORDER BY id").fetchall()

    def insert(self, method, arn="N/A", status="Submitted", pan="ABCPD1234E", key=None, period="AY 2025-26",
               form="ITR-4"):
        payload = {"pan": pan, "arn": arn, "capture_method": method, "dataset_key": key, "filing_type": form}
        return self.db.insert_tracker_dump(portal=f"Income Tax ({form})", period_label=period, arn_number=arn,
                                           capture_method=method, status=status, pan=pan, filing_type=form,
                                           raw_payload_json=json.dumps(payload), dataset_key=key)

    def test_a_live_row_is_the_same_row_as_vsdcs(self):
        self.insert("VSDC-X_itr_personal_info", status="Draft")
        assert self.rows()[0][3] == KEY
        self.insert(LIVE, status="Submitted (Not Verified)", arn="123456789150925", key=KEY)
        rows = self.rows()
        assert [(r[0], r[2]) for r in rows] == [(LIVE, "Submitted (Not Verified)")]

    def test_a_live_row_never_moves_a_status_down(self):
        self.insert("VSDC-X_itr_everified", status="Submitted & Verified", arn="123456789150925")
        self.insert(LIVE, status="Draft", key=KEY)
        rows = self.rows()
        assert len(rows) == 1 and rows[0][2] == "Submitted & Verified" and rows[0][1] == "123456789150925"

    def test_a_status_climbing_seconds_after_the_arn_is_kept(self):
        self.insert(LIVE, status="Submitted (Not Verified)", arn="123456789150925", key=KEY)
        self.insert(LIVE, status="Submitted & Verified", arn="123456789150925", key=KEY)
        rows = self.rows()
        assert len(rows) == 1 and rows[0][2] == "Submitted & Verified"

    def test_a_superseded_live_key_removes_only_sgts_row(self):
        self.insert("VSDC-X_itr_submitted", arn="123456789150925")
        assert self.db.delete_sgt_rows_by_dataset_key(KEY) == 0
        self.insert(LIVE, status="Draft", key="ITR:SGTABC:ITR4:AY_2025_26", pan="")
        assert self.db.delete_sgt_rows_by_dataset_key("ITR:SGTABC:ITR4:AY_2025_26") == 1
        assert [r[0] for r in self.rows()] == ["VSDC-X_itr_submitted"]

    def test_startup_keeps_sgts_own_keys(self):
        """The admin PC's start-up key rewrite used to fold SGT rows into other engines' rows."""
        self.insert("VSDC-X_itr_personal_info", status="Draft")
        self.insert(LIVE, status="Draft", key="ITR:SGTABC:ITR4:AY_2025_26", pan="")      # client unknown
        self.insert("SGT_shadow", status="Draft", key="SGT:ITR:SXYZ:ITR3:AY202526", pan="", period="AY 2025-26")
        self.db._init_raw_schema()                   # what every start-up runs (admin PC)
        keys = sorted(r[3] for r in self.rows())
        assert "ITR:SGTABC:ITR4:AY_2025_26" in keys and KEY in keys

    def test_shadow_rows_are_converted_and_merged_at_startup(self):
        # VSDC saw the filing verified; SGT's shadow row for it says less: VSDC's row wins, one row.
        self.insert("VSDC-X_itr_everified", status="Submitted & Verified", arn="123456789150925")
        self.insert("SGT_shadow", status="Submitted (Not Verified)", arn="123456789150925",
                    key="SGT:ITR:ABCPD1234E:ITR4:AY202526")
        # A shadow row nobody else has: becomes a live row under the canonical key.
        self.insert("SGT_shadow", status="Draft", key="SGT:ITR:ABCPD1234E:ITR3:AY202627", period="AY 2026-27",
                    form="ITR-3")
        self.db._init_raw_schema()
        rows = {r[3]: r for r in self.rows()}
        assert "SGT_shadow" not in [r[0] for r in rows.values()]
        assert rows[KEY][0] == "VSDC-X_itr_everified" and rows[KEY][2] == "Submitted & Verified"
        new = rows["ITR:ABCPD1234E:ITR3:AY_2026_27"]
        assert new[0] == LIVE and json.loads(new[4])["capture_method"] == LIVE
        self.db._init_raw_schema()                   # idempotent
        assert len(self.rows()) == 2

    def test_a_converted_shadow_row_that_says_more_wins(self):
        self.insert("VSDC-X_itr_personal_info", status="Draft")
        self.insert("SGT_shadow", status="Submitted & Verified", arn="123456789150925",
                    key="SGT:ITR:ABCPD1234E:ITR4:AY202526")
        self.db._init_raw_schema()
        rows = self.rows()
        assert [(r[0], r[2], r[3]) for r in rows] == [(LIVE, "Submitted & Verified", KEY)]


# ── The switch-over ──────────────────────────────────────────────────────────────
class TestRollout:
    def test_the_office_switches_to_sgt_live_once(self):
        store = {"sgt_mode": "shadow", "vsdc_enabled": "1", "vsdc_x_enabled": "1", "vsdc247_enabled": "1"}
        get = lambda k, d=None: store.get(k, d)
        assert apply_sgt_live_rollout(get, store.update)
        assert store["sgt_mode"] == "live" and read_engine_flags(get) == (False, False, False)
        assert store[SGT_LIVE_ROLLOUT_SETTING] == "1"
        store["vsdc247_enabled"] = "1"               # someone turns VSDC 24/7 back on by hand
        assert not apply_sgt_live_rollout(get, store.update)
        assert store["vsdc247_enabled"] == "1"

    def test_a_fresh_install_runs_sgt_alone(self):
        assert set(ENGINE_DEFAULTS.values()) == {"0"}
        assert read_engine_flags(lambda k, d=None: d) == (False, False, False)


class TestRouter:
    @pytest.fixture
    def harness(self):
        from tests.test_vsdc247_integration import Harness
        with patch.dict(os.environ):
            for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY", "SGT_MODE"):
                os.environ.pop(var, None)
            yield Harness()

    def test_live_runs_alone(self, harness):
        from core.vsdc.vsdc_router import SGT_LIVE
        r = harness.router
        r.apply_engine_settings(False, False, False, sgt="live")
        assert r._sgt_mode == SGT_LIVE and r._sgt_alone and not r._engines_off

    def test_changing_mode_ends_open_sessions_first(self, harness):
        r = harness.router
        r.apply_engine_settings(False, False, False, sgt="shadow")
        fake = MagicMock()
        r._sgt = fake
        r.apply_engine_settings(False, False, False, sgt="live")
        fake.end_all.assert_called_once()
        fake.set_mode.assert_called_once_with("live")

    def test_live_observes_in_scope_ticks(self, harness):
        r = harness.router
        r.apply_engine_settings(False, False, False, sgt="live")
        fake = MagicMock()
        fake.pending.return_value = 0
        r._sgt = fake
        harness.screen(["Dashboard"])
        assert harness.tick() is None
        fake.observe.assert_called_once()

    def test_env_live(self, harness):
        from core.vsdc.vsdc_router import SGT_LIVE
        with patch.dict(os.environ, {"SGT_MODE": "live"}):
            harness.router.apply_engine_settings(True, True, False, sgt="off")
            assert harness.router._sgt_mode == SGT_LIVE
