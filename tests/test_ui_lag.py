"""core/ui_lag.py (the UI-lag meter) and tools/ui_lag_report.py. A real Qt event loop (offscreen): a slot
that keeps the main thread busy must be caught as a hitch and blamed on that slot's own code."""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core import ui_lag
from tools import ui_lag_report

app = QApplication.instance() or QApplication([])


def run_loop(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _slow_grid_fill(ms):
    """Stands for a slot that fills a big grid on the UI thread."""
    end = time.perf_counter() + ms / 1000.0
    n = 0
    while time.perf_counter() < end:
        n += 1
    return n


def test_sera_frames_and_keys():
    here = os.path.abspath(__file__)
    assert ui_lag.sera_rel(here) == "tests/test_ui_lag.py"
    assert ui_lag.sera_rel(threading.__file__) is None
    assert ui_lag.sera_rel("core\\sgt\\capture\\loop.py") == "core/sgt/capture/loop.py"     # frozen paths
    assert ui_lag.sera_rel("threading.py") is None
    assert ui_lag.sera_rel("PySide6\\QtCore.py") is None
    assert ui_lag.main_key(["main.py:3199 run", "main.py:3214 <module>"]) == ui_lag.QT_KEY
    assert ui_lag.main_key([]) == ui_lag.QT_KEY
    assert ui_lag.main_key(["ui/a.py:5 fill", "ui/a.py:9 fill", "ui/b.py:2 refresh", "main.py:3199 run"]) == \
        "ui/a.py:5 fill (via ui/b.py:2 refresh)"
    assert ui_lag.main_key(["ui/a.py:5 on_click", "main.py:3199 run"]) == "ui/a.py:5 on_click"


def test_a_slow_slot_is_caught_and_blamed_on_its_code():
    lines = []
    meter = ui_lag.UiLagMeter(write=lines.append, in_front=lambda: True)
    meter.start(app)
    try:
        run_loop(300)                                     # settle
        QTimer.singleShot(50, lambda: _slow_grid_fill(400))
        run_loop(900)
        meter._quiet_minutes = ui_lag.QUIET_EVERY_MIN     # write this minute even if nothing lagged
        meter._flush_minute(time.monotonic())
    finally:
        meter.stop()
    minute = next(l for l in lines if " MIN " in l)
    assert "hitch250=1" in minute or "hitch250=2" in minute, minute
    assert "in_use=yes" in minute
    assert "_slow_grid_fill" in minute.split(" top= ", 1)[1], minute
    hitch = next(l for l in lines if " HITCH " in l)
    ms = int(hitch.split("ms=", 1)[1].split()[0])
    assert 300 <= ms <= 1500, hitch
    assert "tests/test_ui_lag.py:" in hitch and "_slow_grid_fill" in hitch


def test_a_quiet_loop_has_no_hitch():
    lines = []
    meter = ui_lag.UiLagMeter(write=lines.append, in_front=lambda: False)
    meter.start(app)
    try:
        run_loop(600)
        with meter._lock:
            hitches = list(meter._hitches)
            ticks = len(meter._lates)
    finally:
        meter.stop()
    assert ticks >= 5
    assert not [h for h in hitches if h.ms >= 250]


def test_the_pc_asleep_is_not_lag():
    t = [100.0]
    meter = ui_lag.UiLagMeter(clock=lambda: t[0], write=lambda s: None, in_front=lambda: True)
    t[0] += 45.0                                          # 45 s without a tick: sleep / hibernate
    meter.tick()
    t[0] += 0.05
    meter.tick()
    assert meter._hitches == []
    assert len(meter._lates) == 1 and meter._lates[0] == pytest.approx(0.0, abs=1e-6)


def test_a_hitch_without_samples_says_so():
    t = [100.0]
    meter = ui_lag.UiLagMeter(clock=lambda: t[0], write=lambda s: None, in_front=lambda: True)
    t[0] += 0.35
    meter.tick()
    (h,) = meter._hitches
    assert round(h.ms) == 300 and ui_lag.NO_SAMPLE_KEY in h.causes


def test_report_ranks_causes_from_the_log():
    h = ui_lag._Hitch(640.0, 0.0, True)
    h.causes = {"ui/components/kit.py:3410 _fill (via ui/windows/search_window.py:220 refresh)": 500.0, ui_lag.QT_KEY: 140.0}
    h.busy = {"sgt-capture @ core/sgt/capture/loop.py:300 tick": 300.0}
    h.stack = ["ui/components/kit.py:3410 _fill", "ui/windows/search_window.py:220 refresh"]
    lines = ["2026-11-22 10:00:00 START pid=1 cpus=8 frozen=yes",
             "2026-11-22 10:01:00 " + ui_lag.format_minute([1, 2, 640], [h], True, 12.5, 100)[20:],
             "2026-11-22 10:01:00 " + ui_lag.format_hitch(h)[20:]]
    minutes, hitches, runs = ui_lag_report.parse(lines)
    assert runs == 1 and len(minutes) == 1 and len(hitches) == 1
    text = ui_lag_report.report(minutes, hitches, runs)
    assert "2026-11-22" in text
    ranked = text.split("ranked", 1)[1]
    assert ranked.index("kit.py:3410 _fill") < ranked.index("Qt / event loop")
    assert "sgt-capture @ core/sgt/capture/loop.py:300 tick" in text
    assert "ui/components/kit.py:3410 _fill < ui/windows/search_window.py:220 refresh" in text


def test_off_switch(monkeypatch):
    monkeypatch.setenv("SERA_UI_LAG", "off")
    monkeypatch.setattr(ui_lag, "_meter", None)
    assert ui_lag.start(app) is None
