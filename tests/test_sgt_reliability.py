"""
SGT reliability by design (2026-09-22): datasets are checked as a whole before they are written,
open sessions survive a crash, a portal that changes is noticed, and one hung UI Automation read
no longer blinds every later one. All identifiers are fictional.
"""
import json
import threading
import time
from datetime import date, timedelta

import pytest
from PIL import Image

from core.sgt.sgt_health import ACTIVE_READS, SpecStats
from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecError, SpecStore, build_dataset_rule, \
    load_registry, self_test_dataset_rule

ITR = "Income Tax"
B = "https://eportal.incometax.gov.in/iec/foservices/#/"
PROFILE = B + "dashboard/myProfile/profileDetail"
CONTACT = B + "dashboard/myProfile/contactDetails"
FILED = B + "dashboard/itrStatus"
PROFILE_PAGE = ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E"]


def card(ay="2026-27", ack="123456789150726"):
    return ["View Filed Returns", f"A.Y. {ay}", "Filing Type", "Original", "done", "Pending for e-verification",
            "ITR :", "ITR-4", "Acknowledgement No :", ack]


class Rig:
    def __init__(self, tmp_path, state=True, stats=None, notify=None):
        self.clock = [1_000_000.0]
        self.page = []
        self.tmp = tmp_path
        self.stats = stats
        self.notified = []
        self.sgt = self.build(state, notify)

    def build(self, state=True, notify=None):
        return SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                         read_uia=lambda h: {"lines": list(self.page)}, log_dir=self.tmp / "log",
                         clock=lambda: self.clock[0], today=lambda: date(2026, 9, 22), echo=lambda m: None,
                         notify=notify or (lambda *a: self.notified.append(a)), stats=self.stats,
                         state_path=(self.tmp / "sessions_state.json") if state else None)

    def see(self, url, page, hwnd=1):
        self.clock[0] += 3
        self.page = page
        self.sgt.observe(hwnd, ITR, url, frame=Image.new("RGB", (8, 8), (int(self.clock[0]) % 250,) * 3))

    def events(self, kind=None):
        out = []
        for f in sorted((self.tmp / "log").glob("*.jsonl")):
            out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        return [e for e in out if kind is None or e.get("event") == kind]


# ── #4 a dataset is checked as a whole before it is written ─────────────────────
class TestDatasetRules:
    def test_the_builtin_rules_load_and_pass_their_own_examples(self):
        reg = load_registry([BUILTIN_FIELDS_PATH])
        names = {r.name for r in reg.dataset_rules.rules}
        assert {"itr_period_is_an_assessment_year", "itr_ack_date_fits_its_year"} <= names
        assert not [e for e in reg.errors if "dataset rule" in e]

    def test_a_dataset_that_breaks_a_rule_is_held_then_written_once_it_passes(self, tmp_path):
        r = Rig(tmp_path, state=False)
        r.see(PROFILE, PROFILE_PAGE)
        r.see(CONTACT, PROFILE_PAGE)
        r.see(FILED, card(ack="123456789150725"))          # dated 15 Jul 2025: before AY 2026-27 began
        assert r.sgt.drain() == []
        held = [e for e in r.events("dataset") if str(e.get("change", "")).startswith("held")]
        assert held and "itr_ack_date_fits_its_year" in held[0]["problems"][0]
        assert not any(a[0] == "submit" for a in r.notified)        # the pill stays quiet about it

    def test_a_rule_without_a_failing_example_is_refused(self):
        rule = build_dataset_rule({"name": "x", "message": "m", "require": {"period": "^AY"},
                                   "examples": [{"dataset": {"period": "AY 2026-27"}, "ok": True}]})
        with pytest.raises(SpecError):
            self_test_dataset_rule(rule)

    def test_a_bad_rule_in_the_override_file_never_removes_a_working_one(self, tmp_path):
        good = load_registry([BUILTIN_FIELDS_PATH])
        override = tmp_path / "o.json"
        override.write_text(json.dumps({"dataset_rules": {"rules": [
            {"name": "itr_period_is_an_assessment_year", "message": "m", "require": {"period": "(unclosed"},
             "examples": [{"dataset": {"period": "x"}, "ok": True}]}]}}), encoding="utf-8")
        reg = load_registry([BUILTIN_FIELDS_PATH, override], previous=good)
        assert "itr_period_is_an_assessment_year" in {r.name for r in reg.dataset_rules.rules}
        assert any("refused" in e for e in reg.errors)


# ── #5 crash safety ──────────────────────────────────────────────────────────────
class TestCrashRecovery:
    def test_an_open_session_is_snapshotted_and_recovered_after_a_crash(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE, PROFILE_PAGE)
        r.see(CONTACT, PROFILE_PAGE)
        r.see(FILED, card())
        r.sgt._save_state(force=True)
        assert (tmp_path / "sessions_state.json").exists()
        # the app dies here: nothing drained, no session ended
        again = r.build()
        rows = again.drain()
        assert [(x["pan"], x["arn"]) for x in rows] == [("ABCPD1234E", "123456789150726")]
        assert not (tmp_path / "sessions_state.json").exists()
        ends = [e for e in r.events("session_end") if "recovered" in e["reason"]]
        assert len(ends) == 1

    def test_a_clean_end_leaves_no_snapshot(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE, PROFILE_PAGE)
        r.see(FILED, card())
        r.sgt.end_all("quit")
        assert not (tmp_path / "sessions_state.json").exists()
        assert r.build().drain() == []

    def test_a_corrupt_snapshot_is_ignored(self, tmp_path):
        (tmp_path / "sessions_state.json").write_text("{not json", encoding="utf-8")
        r = Rig(tmp_path)
        assert r.sgt.drain() == []


# ── #5 noticing that a portal changed ────────────────────────────────────────────
class TestPortalChangeWatch:
    def fill(self, stats, day, reads, ocr=0, hits=()):
        for i in range(reads):
            stats.record_read(ITR, "ocr" if i < ocr else "uia", day)
        for spec in hits:
            stats.record_hits(ITR, [spec], day)

    def test_a_spec_that_goes_quiet_on_busy_days_raises_one_alert_a_day(self, tmp_path):
        st = SpecStats(tmp_path)
        today = date(2026, 9, 22)
        for back in range(10, 4, -1):                          # usually hits
            self.fill(st, today - timedelta(days=back), ACTIVE_READS, hits=["itr_pan"])
        for back in (2, 1, 0):                                 # three busy days, silent
            self.fill(st, today - timedelta(days=back), ACTIVE_READS)
        alerts = st.alerts(today)
        assert [(a["kind"], a["spec"]) for a in alerts] == [("spec_quiet", "itr_pan")]
        assert st.alerts(today) == []                          # once a day

    def test_quiet_days_with_little_use_are_not_a_change(self, tmp_path):
        st = SpecStats(tmp_path)
        today = date(2026, 9, 22)
        for back in range(10, 4, -1):
            self.fill(st, today - timedelta(days=back), ACTIVE_READS, hits=["itr_pan"])
        for back in (2, 1, 0):
            self.fill(st, today - timedelta(days=back), 3)       # barely used
        assert st.alerts(today) == []

    def test_a_portal_turning_blind_to_ui_automation_is_noticed(self, tmp_path):
        st = SpecStats(tmp_path)
        today = date(2026, 9, 22)
        for back in range(5, 0, -1):
            self.fill(st, today - timedelta(days=back), ACTIVE_READS, ocr=1)
        self.fill(st, today, ACTIVE_READS, ocr=ACTIVE_READS)
        assert [a["kind"] for a in st.alerts(today)] == ["portal_blind"]

    def test_counts_persist_and_carry_no_client_data(self, tmp_path):
        st = SpecStats(tmp_path)
        st.record_read(ITR, "uia", date(2026, 9, 22))
        st.record_hits(ITR, ["itr_pan"], date(2026, 9, 22))
        st.save()
        text = (tmp_path / "spec_stats.json").read_text(encoding="utf-8")
        assert "itr_pan" in text and "ABCPD" not in text
        assert SpecStats(tmp_path).data["hits"][ITR]["itr_pan"]["2026-09-22"] == 1

    def test_sgt_counts_its_reads_and_raises_the_alert_on_the_pill(self, tmp_path):
        st = SpecStats(tmp_path / "stats")
        today = date(2026, 9, 22)
        for back in range(10, 4, -1):
            self.fill(st, today - timedelta(days=back), ACTIVE_READS, hits=["itr_pan"])
        for back in (3, 2, 1):
            self.fill(st, today - timedelta(days=back), ACTIVE_READS)
        r = Rig(tmp_path, state=False, stats=st)
        r.see(FILED, ["nothing", "to", "see"])
        assert st.data["reads"][ITR]["2026-09-22"][0] == 1
        assert any(a[0] == "prompt" and "portal may have changed" in a[1] for a in r.notified)


# ── #5 one hung UI Automation read must not blind every later one ────────────────
def test_a_hung_uia_read_does_not_block_the_next_one():
    import core.vsdc.vsdc_uia_text as u
    before = u.worker_health()["abandoned_workers"]
    gate = threading.Event()
    try:
        assert u._run_with_timeout(lambda: gate.wait(20), 0.2) is None
        t = time.perf_counter()
        assert u._run_with_timeout(lambda: "read", 0.5) == "read"
        assert time.perf_counter() - t < 0.5
        assert u.worker_health()["abandoned_workers"] == before + 1
    finally:
        gate.set()


def test_the_page_recording_switch_defaults_on():
    from core.vsdc.vsdc_engines import read_sgt_record_pages
    assert read_sgt_record_pages(lambda k, d=None: d) is True
    assert read_sgt_record_pages(lambda k, d=None: "0") is False
