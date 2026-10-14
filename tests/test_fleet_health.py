"""
Fleet health (WP12): each PC shares its capture health through a settings row, the Settings panel
colours the rows, the copied report and the daily digest hold no client data, and SGT answers
"is a session open" for the mandatory update path. All names and identifiers are fictional.
"""
import json
import re
import time
from datetime import date, datetime, timedelta

from PIL import Image

from core import fleet_health as fh
from core.sgt import sgt_digest as dg
from core.sgt.sgt_shadow import SESSION_QUIET_SEC, SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.vsdc import vsdc_health as vh

NOW = datetime(2026, 10, 14, 10, 0, 0)
IDENTIFIER_SHAPES = (r"\b[A-Z]{5}\d{4}[A-Z]\b", r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b",
                     r"\b[A-Z]{2}\d{11,14}[A-Z0-9]?\b", r"https?://")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def wall(self):
        return NOW + timedelta(seconds=self.t - 1000.0)


def monitor(clock=None, **kw):
    clock = clock or Clock()
    shared = []
    m = vh.HealthMonitor(clock=clock, wall=clock.wall, write=shared.append, log=lambda s: None,
                         device="PC-One", version="9.9.9", **kw)
    return m, clock, shared


def record(state="ok", age_min=1.0, sync_min=15, device="PC-One", **kw):
    """A shared settings value, made `age_min` minutes before NOW."""
    base = dict(device=device, version="9.9.9", at=(NOW - timedelta(minutes=age_min)).isoformat(timespec="seconds"),
                state=state, since="2026-10-14T08:00:00", detail="", sync_min=sync_min)
    base.update(kw)
    return json.dumps(base)


def settings(**pcs):
    return {fh.key_for(k): v for k, v in pcs.items()}


# ── sharing: on change and every N minutes ──────────────────────────────────────────────────────
class TestSharing:
    def test_health_key_is_written_on_change_and_every_n_minutes(self):
        m, clock, shared = monitor()
        m.set_sync_minutes(5)
        m.write()
        assert len(shared) == 1                      # the first record is shared at once
        clock.t += 60
        m.write()
        assert len(shared) == 1                      # a minute on: only the local log line
        clock.t += 4 * 60
        m.write()
        assert len(shared) == 2                      # five minutes after the last shared record
        clock.t += 1
        m.uia_paused(True)
        m.tick()                                     # a state change ...
        assert m.due()
        m.write()
        assert len(shared) == 3                      # ... is shared at once
        assert shared[-1].state == "uia-paused"
        clock.t += 60
        m.write()
        assert len(shared) == 3

    def test_the_local_log_line_stays_once_a_minute(self):
        logs = []
        clock = Clock()
        m = vh.HealthMonitor(clock=clock, wall=clock.wall, write=lambda r: None, log=logs.append,
                             device="PC-One", version="9.9.9")
        m.set_sync_minutes(30)
        assert not m.due()
        clock.t += 61
        assert m.due()
        m.write()
        assert not m.due()
        assert len([x for x in logs if x.startswith("health ")]) == 1

    def test_the_interval_defaults_to_15_and_is_clamped(self):
        m, clock, shared = monitor()
        assert m.record().sync_min == 15
        for given, want in ((0, 1), (-4, 1), (999, 60), ("12", 12), ("x", 15), (None, 15)):
            m.set_sync_minutes(given)
            assert m.record().sync_min == want, given

    def test_the_setting_is_read_with_the_same_limits(self):
        assert fh.read_sync_min(lambda k, d=None: d) == 15
        assert fh.read_sync_min(lambda k, d=None: "30") == 30
        assert fh.read_sync_min(lambda k, d=None: "500") == 60
        assert fh.read_sync_min(lambda k, d=None: "junk") == 15

    def test_no_writer_means_nothing_is_shared_and_nothing_breaks(self):
        clock = Clock()
        m = vh.HealthMonitor(clock=clock, wall=clock.wall, log=lambda s: None, device="PC-One", version="9.9.9")
        m.write()
        shared = []
        m.set_writer(shared.append)
        m.write()
        assert len(shared) == 1

    def test_yesterdays_totals_survive_midnight(self):
        m, clock, shared = monitor()
        m._tick()
        m.blind_sec_today = 600.0
        clock.t += 15 * 3600                         # past midnight
        m.tick()
        rec = m.record()
        assert (rec.blind_sec_today, rec.blind_sec_yesterday) == (0, 600)


# ── the panel ───────────────────────────────────────────────────────────────────────────────────
class TestPanel:
    def test_panel_rows_colour_by_age_and_state(self):
        rows = {r.device_id: r for r in fh.read_fleet(settings(
            fresh=record("ok", 2), late=record("ok", 20), quiet_off=record("no-portal", 45),
            gone=record("blind", 45), hurt=record("blind", 2), slow=record("slow", 2),
            old=record("ok", 3 * 1440, device="Old PC"), slow_sync=record("ok", 50, sync_min=60)), NOW)}
        assert rows["fresh"].level == fh.OK
        assert rows["late"].level == fh.WARN and rows["late"].note == "late"     # 15 min share: fresh to 18 min
        assert rows["quiet_off"].level == fh.OFF and rows["quiet_off"].note == "off"
        assert rows["gone"].level == fh.ALERT and rows["gone"].note == "not reporting"
        assert rows["hurt"].level == fh.ALERT
        assert rows["slow"].level == fh.WARN
        assert rows["old"].level == fh.OFF                                       # a stale quiet PC: grey, not red
        assert rows["slow_sync"].level == fh.OK                                  # shares hourly: 50 min is fresh

    def test_a_very_old_problem_is_last_seen_not_red(self):
        row = fh.read_fleet(settings(x=record("blind", 9 * 1440)), NOW)[0]
        assert (row.level, row.note) == (fh.OFF, "last seen")

    def test_rows_are_worst_first_and_junk_is_skipped(self):
        data = settings(a=record("ok", 1, device="AAA"), b=record("blind", 1, device="BBB"))
        data["capture_health:junk"] = "not json"
        data["capture_health:odd"] = json.dumps({"state": "ok"})                  # no time
        data["theme"] = "dark"
        rows = fh.read_fleet(data, NOW)
        assert [r.device for r in rows] == ["BBB", "AAA"]

    def test_two_pcs_show_each_others_health_rows(self):
        table = {}                                                                # the replicated settings table
        pcs = []
        for name, device_id in (("Front desk", "dev-1"), ("Back room", "dev-2")):
            clock = Clock()
            shared = []
            m = vh.HealthMonitor(clock=clock, wall=clock.wall, write=shared.append, log=lambda s: None,
                                 device=name, version="9.9.9")
            m.write()
            pcs.append((device_id, shared[0]))
        for device_id, rec in pcs:
            item = fh.HealthWrite(fh.key_for(device_id), rec.to_json())
            table[item.key] = item.value
        seen = fh.read_fleet(table, NOW)
        assert {r.device for r in seen} == {"Front desk", "Back room"}
        assert {r.device_id for r in seen} == {"dev-1", "dev-2"}

    def test_panel_copy_report_holds_no_client_data(self):
        m, clock, shared = monitor()
        m.row("sent")
        m.write()
        data = settings(a=shared[0].to_json(), b=record("blind", 1, device="ABCPD1234E desk 12345678901"))
        text = fh.report_text(fh.read_fleet(data, NOW + timedelta(minutes=1)), NOW)
        assert "PC-One" in text
        for shape in IDENTIFIER_SHAPES:
            assert not re.search(shape, text, re.I), shape

    def test_the_shared_record_is_small_and_holds_no_client_data(self):
        m, clock, shared = monitor()
        m.write()
        text = shared[0].to_json()
        assert len(text) < 1024
        for shape in IDENTIFIER_SHAPES:
            assert not re.search(shape, text), shape


# ── the daily digest ────────────────────────────────────────────────────────────────────────────
def fleet_with_one_of_each():
    return fh.read_fleet(settings(
        a=record("blind", 1, device="PC-A", version="9.9.9", blind_sec_yesterday=600, stalled_sec_yesterday=120,
                 alerts_configured=True),
        b=record("ok", 1, device="PC-B", version="9.9.8", alerts_configured=False,
                 unattributed_rows=2, ocr_only_rows=3, rows_dead_letter=1)), NOW)


class TestDigest:
    def test_digest_lists_every_category(self):
        d = dg.build_digest(fleet_with_one_of_each(), 4, date(2026, 10, 14))
        assert len(d.lines) == 7
        assert d.counts == {"blind_or_stalled_pcs": 1, "unattributed_rows": 2, "ocr_only_rows": 3,
                            "rows_not_saved": 1, "unverified_itr_old": 4, "old_build_pcs": 1,
                            "alerts_unconfigured_pcs": 1}
        text = d.text()
        assert "(PC-A 12 min)" in text and "(PC-B)" in text
        assert "after 7 days: 4" in text
        assert "Nothing needs attention" in dg.build_digest([], 0, date(2026, 10, 14)).subtitle()
        assert len(d.subtitle()) <= 140

    def test_the_digest_holds_no_client_data(self):
        d = dg.build_digest(fleet_with_one_of_each(), 4, date(2026, 10, 14))
        for shape in IDENTIFIER_SHAPES:
            assert not re.search(shape, d.text() + d.subtitle(), re.I), shape

    def test_digest_runs_once_a_day(self):
        store = {}
        runs = []
        daily = dg.DailyDigest(lambda: dict(store), store.__setitem__, lambda: True,
                               lambda: runs.append(1) or 0, now=lambda: NOW)
        first = daily(date(2026, 10, 14))
        assert first["title"] == "Capture digest ready"
        assert daily(date(2026, 10, 14)) is None                                   # again the same day: nothing
        assert len(runs) == 1
        assert "Capture digest for 2026-10-14" in store[dg.TEXT_KEY]
        assert daily(date(2026, 10, 15)) is not None                               # next day: again

    def test_only_the_admin_pc_makes_the_digest(self):
        store = {}
        daily = dg.DailyDigest(lambda: dict(store), store.__setitem__, lambda: False, lambda: 0, now=lambda: NOW)
        assert daily(date(2026, 10, 14)) is None
        assert store == {}

    def test_a_failing_count_does_not_stop_the_digest(self):
        store = {}
        daily = dg.DailyDigest(lambda: dict(store), store.__setitem__, lambda: True, lambda: 1 / 0, now=lambda: NOW)
        assert daily(date(2026, 10, 14))["counts"]["unverified_itr_old"] == 0

    def test_old_unverified_itr_returns_are_counted_once_each(self):
        def entry(status, days_ago, form="ITR-4", period="AY 2026-27", portal=None, arn="123"):
            at = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
            return {"portal": portal or f"ITR ({form})", "status": status, "arn": arn, "period_label": period,
                    "created_at": at}
        notv = "Submitted (Not Verified)"
        containers = [
            {"identity_key": "c1", "filing_history": [entry(notv, 10), entry(notv, 9)]},          # one return, old
            {"identity_key": "c2", "filing_history": [entry(notv, 3)]},                            # too recent
            {"identity_key": "c3", "filing_history": [entry(notv, 12), entry("Submitted & Verified", 2)]},  # verified since
            {"identity_key": "c4", "filing_history": [entry(notv, 20, portal="GST (GSTR-3B)")]},   # not an ITR
            {"identity_key": "c5", "filing_history": [entry(notv, 30, period="AY 2025-26"),
                                                      entry(notv, 30, form="ITR-1")]},              # two returns
        ]
        assert dg.count_old_unverified(containers, NOW) == 3


# ── SGT: sessions open, and the digest hook ─────────────────────────────────────────────────────
ITR = "Income Tax"
PROFILE = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"
PROFILE_PAGE = ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E"]


class SgtRig:
    def __init__(self, tmp_path):
        self.clock = [1_000_000.0]
        self.notified = []
        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                             read_uia=lambda h: {"lines": list(PROFILE_PAGE)}, log_dir=tmp_path / "log",
                             clock=lambda: self.clock[0], today=lambda: date(2026, 10, 14), echo=lambda m: None,
                             notify=lambda *a: self.notified.append(a))

    def see(self):
        self.clock[0] += 3
        self.sgt.observe(1, ITR, PROFILE, frame=Image.new("RGB", (8, 8), (int(self.clock[0]) % 250,) * 3))


class TestSgtHooks:
    def test_sessions_open_while_a_session_with_content_was_touched_lately(self, tmp_path):
        r = SgtRig(tmp_path)
        assert r.sgt.sessions_open() is False                                    # nothing seen yet
        r.see()
        assert r.sgt.sessions_open() is True
        r.clock[0] += SESSION_QUIET_SEC + 1
        assert r.sgt.sessions_open() is False                                    # quiet for ten minutes

    def test_the_daily_digest_runs_on_the_first_check_of_the_day_and_reaches_the_log_and_the_pill(self, tmp_path):
        r = SgtRig(tmp_path)
        calls = []
        r.sgt.set_digest(lambda today: calls.append(today) or {
            "title": "Capture digest ready", "subtitle": "1 PC(s) blind or stalled", "counts": {"old_build_pcs": 2}})
        r.sgt._daily_health_check()
        r.sgt._digest_thread.join(5)              # made off the capture thread...
        assert r.notified == []
        r.sgt._daily_health_check()               # ...and reported on the next tick
        assert calls == [date(2026, 10, 14)]
        assert r.notified == [("prompt", "Capture digest ready", "1 PC(s) blind or stalled", {})]
        logged = [json.loads(line) for f in (tmp_path / "log").glob("*.jsonl")
                  for line in f.read_text(encoding="utf-8").splitlines()]
        assert [e for e in logged if e.get("event") == "digest"][0]["old_build_pcs"] == 2

    def test_a_failing_digest_never_stops_the_check(self, tmp_path):
        r = SgtRig(tmp_path)
        r.sgt.set_digest(lambda today: 1 / 0)
        r.sgt._daily_health_check()
        r.sgt._digest_thread.join(5)
        r.sgt._daily_health_check()
        assert r.notified == []

    def test_a_slow_digest_never_holds_up_the_capture_tick(self, tmp_path):
        import threading
        r = SgtRig(tmp_path)
        release = threading.Event()
        r.sgt.set_digest(lambda today: release.wait(5) and None)
        started = time.monotonic()
        r.sgt._daily_health_check()
        assert time.monotonic() - started < 1.0
        release.set()
        r.sgt._digest_thread.join(5)


# ── the Settings panel ──────────────────────────────────────────────────────────────────────────
class TestSettingsPanel:
    def test_tracker_page_shows_the_fleet_and_saves_the_interval(self, tmp_path):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import security
        from database import SeraDatabase
        from PySide6.QtWidgets import QApplication
        from ui.dialogs import unified_settings_dialog as usd
        QApplication.instance() or QApplication([])
        salt = str(tmp_path / "s.salt")
        security.generate_and_save_salt(salt)
        key = security.derive_key_hex("pw", security.load_salt(salt))
        db = SeraDatabase(str(tmp_path / "m.db"), key, raw_db_path=str(tmp_path / "r.db"))
        db.set_setting(fh.key_for("dev-1"), record("blind", 0.5, device="Front desk"))
        dlg = usd.UnifiedSettingsDialog(db, actor="test")
        try:
            dlg._switch_page(usd._P_TRACKER)
            assert dlg.health_sync_spin.value() == 15
            assert (dlg.health_sync_spin.minimum(), dlg.health_sync_spin.maximum()) == (1, 60)
            assert dlg.health_table.rowCount() == 1
            assert dlg.health_table.item(0, 0).text() == "Front desk"
            assert not dlg._btn_save.isEnabled()
            dlg.health_sync_spin.setValue(30)
            assert dlg._btn_save.isEnabled()
            dlg._on_save_settings()
            assert db.get_setting(fh.SYNC_MIN_KEY) == "30"
        finally:
            dlg.deleteLater()
