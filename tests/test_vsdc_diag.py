"""
2026-10-13: an office PC stopped capturing from the GST portal 3-3.5 hours into two evenings in a
row. Five UI Automation reads that were merely slow (still running when the next was asked for)
switched UIA off for the rest of the run, GST has no OCR fallback, and the only notice was a
print() the installed app has no console for. These tests pin the fix (slow is not hung, the
count recovers, a pause is never permanent) and the on-disk diagnostics (logs/vsdc.log).
"""
import threading
import time

import pytest
from PIL import Image

from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.vsdc import vsdc_diag


@pytest.fixture
def uia(monkeypatch):
    import core.vsdc.vsdc_uia_text as u
    monkeypatch.setattr(u, "_inflight", None)
    monkeypatch.setattr(u, "_paused_until", 0.0)
    monkeypatch.setattr(u, "_disabled_reason", None)
    monkeypatch.setattr(u, "_wedged", [])
    gates = []
    u.gates = gates
    yield u
    for g in gates:
        g.set()
    time.sleep(0.05)


def gate(u):
    g = threading.Event()
    u.gates.append(g)
    return g


def log_text():
    p = vsdc_diag.log_path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── the fix ───────────────────────────────────────────────────────────────────────
def test_a_slow_read_is_not_abandoned_the_next_read_is_skipped_as_busy(uia):
    before = uia.worker_health()["abandoned_workers"]
    g = gate(uia)
    assert uia._run_with_timeout(lambda: g.wait(5), 0.1) is None
    assert uia.was_busy() is False                       # that one timed out, it was not skipped
    assert uia._run_with_timeout(lambda: "read", 0.5) is None
    assert uia.was_busy() is True
    assert uia.worker_health()["abandoned_workers"] == before
    g.set()
    time.sleep(0.05)
    assert uia._run_with_timeout(lambda: "read", 0.5) == "read"     # same worker, still alive
    assert uia.was_busy() is False


def test_a_read_still_running_past_the_grace_is_wedged_and_replaced(uia, monkeypatch):
    monkeypatch.setattr(uia, "SLOW_GRACE_SEC", 0.1)
    before = uia.worker_health()["abandoned_workers"]
    g = gate(uia)
    assert uia._run_with_timeout(lambda: g.wait(5), 0.05) is None
    time.sleep(0.15)
    t = time.perf_counter()
    assert uia._run_with_timeout(lambda: "read", 0.5) == "read"
    assert time.perf_counter() - t < 0.5
    h = uia.worker_health()
    assert h["abandoned_workers"] == before + 1 and h["stuck_workers"] == 1
    assert "replaced its worker" in log_text()
    g.set()
    time.sleep(0.05)
    assert uia.worker_health()["stuck_workers"] == 0     # it came back: no longer counted


def test_too_many_stuck_workers_pause_uia_then_it_reads_again(uia, monkeypatch):
    monkeypatch.setattr(uia, "SLOW_GRACE_SEC", 0.05)
    monkeypatch.setattr(uia, "MAX_WEDGED_WORKERS", 2)
    monkeypatch.setattr(uia, "PAUSE_SEC", 0.3)
    for _ in range(3):              # the 3rd call finds the 2nd wedged -> 2 stuck -> paused
        g = gate(uia)
        uia._run_with_timeout(lambda g=g: g.wait(5), 0.02)
        time.sleep(0.08)
    assert uia.worker_health()["disabled"]
    assert uia._run_with_timeout(lambda: "read", 0.5) is None       # paused
    assert "UIA paused" in log_text()
    time.sleep(0.35)
    assert uia._run_with_timeout(lambda: "read", 0.5) == "read"     # never off for the whole run
    assert uia.worker_health()["disabled"] is None
    assert "pause over" in log_text()


def test_many_slow_reads_over_a_long_evening_never_blind_uia(uia):
    """The office PC: dozens of reads slower than the timeout. Before the fix the 6th one
    switched UIA off until a restart."""
    for _ in range(12):
        g = gate(uia)
        uia._run_with_timeout(lambda g=g: g.wait(5), 0.02)
        assert uia._run_with_timeout(lambda: "x", 0.2) is None and uia.was_busy()
        g.set()
        time.sleep(0.03)
    assert uia._run_with_timeout(lambda: "read", 0.5) == "read"
    assert uia.worker_health()["disabled"] is None


def test_a_late_read_is_logged_with_its_time_and_counted(uia):
    vsdc_diag._limited.clear()
    uia.take_read_stats()
    uia._run_with_timeout(lambda: time.sleep(0.15), 0.05)
    time.sleep(0.2)
    stats = uia.take_read_stats()
    assert stats["late"] == 1 and stats["reads"] == 1 and stats["max_ms"] >= 140
    assert "UIA read came back after" in log_text()
    assert uia.take_read_stats()["reads"] == 0          # per interval


def test_read_page_text_says_busy(uia):
    g = gate(uia)
    uia._run_with_timeout(lambda: g.wait(5), 0.02)
    hwnd = uia.user32.GetDesktopWindow()
    assert uia.read_page_text(hwnd) == {"text": "", "lines": [], "busy": True}


# ── SGT: a skipped read is asked again, not counted ─────────────────────────────
def test_sgt_reads_again_on_the_next_tick_after_a_busy_skip(tmp_path):
    answers = [{"text": "", "lines": [], "busy": True}, {"lines": ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E"]}]
    calls = []

    def read(h):
        calls.append(h)
        return answers.pop(0) if answers else {"lines": []}

    clock = [1000.0]
    sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None), read_uia=read,
                    log_dir=tmp_path, clock=lambda: clock[0], echo=lambda m: None, notify=lambda *a: None)
    frame = Image.new("RGB", (8, 8), (9, 9, 9))
    url = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"
    sgt.observe(1, "Income Tax", url, frame=frame)
    clock[0] += 1                   # same page, same picture, well inside the 15 s re-read
    sgt.observe(1, "Income Tax", url, frame=frame)
    assert len(calls) == 2
    d = sgt.take_diag()
    assert d["busy"] == 1 and d["reads"] == 1 and d["last_lines"] == 4 and d["sessions"] == 1
    assert sgt.take_diag()["reads"] == 0


def test_sgt_counts_a_blind_gst_page_as_skipped(tmp_path):
    sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                    read_uia=lambda h: {"lines": []}, log_dir=tmp_path, echo=lambda m: None,
                    notify=lambda *a: None)
    sgt.observe(1, "GST Portal", "https://services.gst.gov.in/services/auth/fowelcome",
                frame=Image.new("RGB", (8, 8), (1, 1, 1)))
    d = sgt.take_diag()
    assert d["reads"] == 1 and d["blind"] == 1 and d["skipped"] == 1 and d["portal"] == "GST Portal"


# ── logs/vsdc.log ───────────────────────────────────────────────────────────────
def test_note_limited_holds_back_repeats_and_counts_them():
    vsdc_diag._limited.clear()
    for _ in range(4):
        vsdc_diag.note_limited("k", "same thing", every_sec=0.1)
    time.sleep(0.12)
    vsdc_diag.note_limited("k", "same thing", every_sec=0.1)
    text = log_text()
    assert text.count("same thing") == 2 and "+3 like it" in text


def test_the_heartbeat_line_carries_ticks_scope_sgt_and_uia(tmp_path):
    class Router:
        diag_tick = {"front": "msedge.exe", "address_bar": "read", "in_scope": "yes",
                     "portal": "GST Portal", "page": "https://services.gst.gov.in/services/auth/fowelcome"}
        _sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                         read_uia=lambda h: {"lines": []}, log_dir=tmp_path, echo=lambda m: None)

    beat = vsdc_diag.Heartbeat(every_sec=0)
    beat.tick(12.0)
    beat.tick(800.0, error=True)
    assert beat.due()
    beat.write(Router())
    line = [ln for ln in log_text().splitlines() if " beat " in ln][-1]
    for part in ("ticks=2", "errors=1", "max_tick_ms=800", "front=msedge.exe", "in_scope=yes",
                 "portal=GST Portal", "fowelcome", "sgt_reads=0", "sgt_sessions=0", "uia_reads=", "uia_stuck="):
        assert part in line, part
    assert beat.ticks == 0 and beat.errors == 0


def test_a_tick_error_is_logged_with_where_it_happened():
    vsdc_diag._limited.clear()
    try:
        {}["missing"]
    except KeyError as e:
        vsdc_diag.tick_error(e)
    text = log_text()
    assert "tick error KeyError" in text and "test_vsdc_diag.py" in text


def test_a_stuck_capture_worker_dumps_the_stacks(monkeypatch):
    from core import hang_watchdog
    dumps = []
    monkeypatch.setattr(hang_watchdog, "dump_threads", lambda title, native=False: dumps.append(title))

    class Worker:
        _running = True
        _paused = False
        last_progress = time.monotonic() - 100

    w = Worker()
    t = vsdc_diag.watch_worker(w, stall_s=0.5)
    time.sleep(1.3)
    w.last_progress = time.monotonic()
    time.sleep(1.2)
    w._running = False
    t.join(3)
    assert dumps and "capture worker stalled" in dumps[0]
    assert "STALLED" in log_text() and "moving again" in log_text()


def test_hang_watchdog_dump_threads_writes_the_title(monkeypatch, tmp_path):
    from core import hang_watchdog
    monkeypatch.setattr(hang_watchdog, "_log_path", lambda: tmp_path / "hang.log")
    hang_watchdog.dump_threads("capture worker stalled 31 s")
    text = (tmp_path / "hang.log").read_text(encoding="utf-8")
    assert "capture worker stalled 31 s" in text and "MainThread" in text
