"""
Per-PC UI scale (ui/utils/ui_scale.py). The scale is chosen before QApplication, so these tests cover the
pure rules, the env var handling, the probe's fallbacks, and the rule that the scale never reaches the
synced app_settings table. QSettings is pointed at a temporary INI file, so the registry is never touched.
"""
import ctypes
import os
import sys
from pathlib import Path

import pytest

from ui.utils import ui_scale
from ui.utils.ui_scale import ScreenProbe, compute_auto_scale, resolve_scale

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ini_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    path = str(tmp_path / "scale.ini")
    monkeypatch.setattr(ui_scale, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    return path


@pytest.mark.parametrize("width, expected", [
    (1920, 1.00), (1366, 1.00), (1280, 1.00),   # at or above the target: no change
    (1152, 0.90), (1024, 0.80), (800, 0.80),    # 0.05 steps, clamped to 0.80 at the bottom
])
def test_auto_scale_table(width, expected):
    assert compute_auto_scale(width) == pytest.approx(expected)


def test_auto_scale_rounds_down_to_a_0_05_step():
    assert compute_auto_scale(1200) == pytest.approx(0.90)     # 0.9375 -> 0.90
    assert compute_auto_scale(1100) == pytest.approx(0.85)     # 0.859 -> 0.85


def test_auto_scale_never_goes_above_100_percent():
    assert compute_auto_scale(10_000) == 1.0


def test_manual_value_is_clamped_to_80_and_125_percent():
    assert resolve_scale("manual", 1.5, None) == pytest.approx(1.25)
    assert resolve_scale("manual", 0.5, None) == pytest.approx(0.80)
    assert resolve_scale("manual", 0.9, None) == pytest.approx(0.90)


def test_manual_mode_ignores_the_screen():
    probe = ScreenProbe(work_width_px=1024, dpi=96)
    assert resolve_scale("manual", 1.1, probe) == pytest.approx(1.10)


def test_auto_without_a_probe_is_100_percent():
    assert resolve_scale("auto", None, None) == 1.0


def test_auto_uses_the_probe_logical_width():
    probe = ScreenProbe(work_width_px=1280, dpi=120)     # 1280 physical at 125 % = 1024 logical
    assert probe.logical_work_width == pytest.approx(1024.0)
    assert resolve_scale("auto", None, probe) == pytest.approx(0.80)


def test_manual_with_no_saved_value_falls_back_to_auto():
    probe = ScreenProbe(work_width_px=1152, dpi=96)
    assert resolve_scale("manual", None, probe) == pytest.approx(0.90)


def test_apply_sets_the_env_var_only_when_scale_differs(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    assert ui_scale.apply_scale_env(1.0) is False
    assert "QT_SCALE_FACTOR" not in os.environ
    assert ui_scale.apply_scale_env(0.8) is True
    assert os.environ["QT_SCALE_FACTOR"] == "0.80"


def test_a_scale_the_user_set_is_respected(monkeypatch):
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.5")
    assert ui_scale.apply_scale_env(0.8) is False
    assert os.environ["QT_SCALE_FACTOR"] == "1.5"


def test_probe_is_none_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert ui_scale.probe_primary_screen() is None


def test_probe_is_none_when_the_win32_calls_fail(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", None, raising=False)     # any call now raises
    assert ui_scale.probe_primary_screen() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only probe")
def test_probe_reads_this_pc_sensibly():
    probe = ui_scale.probe_primary_screen()
    assert probe is not None
    assert probe.work_width_px >= 800 and probe.dpi >= 96


def test_startup_choice_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("registry unavailable")
    monkeypatch.setattr(ui_scale, "read_settings", boom)
    scale, note = ui_scale.choose_startup_scale()
    assert scale == 1.0 and "fallback" in note


def test_saved_choice_round_trips_per_pc(ini_settings):
    ui_scale.write_settings(ui_scale.MODE_MANUAL, 0.85)
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, 0.85)
    ui_scale.write_settings(ui_scale.MODE_AUTO)
    assert ui_scale.read_settings() == (ui_scale.MODE_AUTO, None)


def test_next_manual_value_steps_and_clamps():
    assert ui_scale.next_manual_value(0.80, 0.05) == pytest.approx(0.85)
    assert ui_scale.next_manual_value(0.80, -0.05) == pytest.approx(0.80)
    assert ui_scale.next_manual_value(1.25, 0.05) == pytest.approx(1.25)


def test_the_scale_is_never_written_to_the_synced_settings_table():
    """app_settings syncs to every PC (sync_schema.py), so a per-PC scale must never land there."""
    checked = [ROOT / "database.py", ROOT / "sync_schema.py"] + list((ROOT / "sera_db").glob("*.py"))
    for path in checked:
        if path.exists():
            assert "ui_scale" not in path.read_text(encoding="utf-8"), path.name


def test_the_display_control_saves_on_this_pc_only(ini_settings):
    from PySide6.QtWidgets import QApplication
    from ui.dialogs.display_scale_dialog import DisplayScaleControl
    QApplication.instance() or QApplication([])
    control = DisplayScaleControl()
    control.combo.setCurrentIndex(2)                # "90 %"
    control._apply()
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, 0.9)
    control.combo.setCurrentIndex(0)                # "Automatic"
    control._apply()
    assert ui_scale.read_settings() == (ui_scale.MODE_AUTO, None)
