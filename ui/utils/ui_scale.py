"""
ui_scale.py
-----------
Per-PC UI scale for small screens (plan: docs/ui-scale-adaptive-layout-plan.md).

The scale is decided before QApplication exists, because Qt reads QT_SCALE_FACTOR when it starts.

Probe approach (chosen 2026-10-08, verified on a 1920 px, 125 % PC):
- Read the primary monitor's work area and effective DPI through Win32 (ctypes, no heavy imports).
- The process must be per-monitor DPI aware before that read, otherwise Windows hands back
  virtualised numbers. Qt sets the same awareness itself when the QApplication is created, so the
  probe sets it first. Checked: Qt reports the same logical width (1536 px) with or without the
  pre-set, and QT_SCALE_FACTOR=0.80 widens it to 1920 px as expected. No Qt warnings either way.
- logical width = physical work-area width / (dpi / 96), the width Qt lays out in.

Scale rules:
- auto: logical width / 1280, rounded down to 0.05, clamped to 0.80-1.00.
- manual (per PC): 0.80-1.25, chosen in Settings -> Display or with Ctrl+= / Ctrl+- / Ctrl+0.
- Stored in QSettings (HKCU), never in app_settings: app_settings syncs to every PC.
- A QT_SCALE_FACTOR the user set themselves is respected and left alone.
"""
import math
import os
import sys
from dataclasses import dataclass

TARGET_LOGICAL_WIDTH = 1280.0
AUTO_MIN, AUTO_MAX = 0.80, 1.00
MANUAL_MIN, MANUAL_MAX = 0.80, 1.25
STEP = 0.05

MODE_AUTO = "auto"
MODE_MANUAL = "manual"

SETTINGS_ORG = "AmanAssociates"
SETTINGS_APP = "ProjectSera"
KEY_MODE = "ui_scale_mode"
KEY_VALUE = "ui_scale_value"

ENV_VAR = "QT_SCALE_FACTOR"


@dataclass(frozen=True)
class ScreenProbe:
    work_width_px: int   # physical pixels
    dpi: int             # effective DPI of the primary monitor

    @property
    def logical_work_width(self) -> float:
        return self.work_width_px / (self.dpi / 96.0)


def compute_auto_scale(logical_work_width: float, target: float = TARGET_LOGICAL_WIDTH) -> float:
    """Rounds down to a 0.05 step (the small epsilon stops 0.9 / 0.05 landing on 17.999...)."""
    stepped = math.floor((logical_work_width / target) / STEP + 1e-9) * STEP
    return round(min(AUTO_MAX, max(AUTO_MIN, stepped)), 2)


def resolve_scale(stored_mode: str, stored_value, probe: ScreenProbe | None) -> float:
    if stored_mode == MODE_MANUAL and stored_value is not None:
        return round(min(MANUAL_MAX, max(MANUAL_MIN, float(stored_value))), 2)
    if probe is None:
        return 1.0
    return compute_auto_scale(probe.logical_work_width)


def next_manual_value(current_scale: float, delta: float) -> float:
    return round(min(MANUAL_MAX, max(MANUAL_MIN, current_scale + delta)), 2)


def probe_primary_screen() -> ScreenProbe | None:
    """Windows only. Returns None elsewhere, and on any error."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore
        # Same awareness Qt asks for (per-monitor v2); without it the numbers below are virtualised.
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

        monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)     # MONITOR_DEFAULTTOPRIMARY
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:  # MDT_EFFECTIVE_DPI
            return None
        work_width = info.rcWork.right - info.rcWork.left
        if work_width <= 0 or dpi_x.value <= 0:
            return None
        return ScreenProbe(work_width_px=int(work_width), dpi=int(dpi_x.value))
    except Exception:
        return None


def apply_scale_env(scale: float) -> bool:
    """Sets QT_SCALE_FACTOR for this process. Returns True only when it was set here."""
    if abs(scale - 1.0) < 1e-9 or ENV_VAR in os.environ:
        return False
    os.environ[ENV_VAR] = f"{scale:.2f}"
    return True


def _settings():
    from PySide6.QtCore import QSettings
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def read_settings() -> tuple[str, float | None]:
    try:
        s = _settings()
        mode = str(s.value(KEY_MODE, MODE_AUTO))
        raw = s.value(KEY_VALUE, None)
        value = float(raw) if raw not in (None, "") else None
    except Exception:
        return MODE_AUTO, None
    if mode not in (MODE_AUTO, MODE_MANUAL):
        mode = MODE_AUTO
    return mode, value


def write_settings(mode: str, value: float | None = None) -> None:
    s = _settings()
    s.setValue(KEY_MODE, mode)
    if value is None:
        s.remove(KEY_VALUE)
    else:
        s.setValue(KEY_VALUE, round(float(value), 2))
    s.sync()


_running_scale = 1.0


def running_scale() -> float:
    """The scale this process started with (1.0 until choose_startup_scale has run)."""
    return _running_scale


def choose_startup_scale() -> tuple[float, str]:
    """Reads the saved choice, probes the screen and sets QT_SCALE_FACTOR. Never raises.

    Returns (scale in use, short description for the start-up log).
    """
    global _running_scale
    try:
        mode, value = read_settings()
        probe = probe_primary_screen()
        scale = resolve_scale(mode, value, probe)
        applied = apply_scale_env(scale)
        if ENV_VAR in os.environ and not applied:
            try:
                scale = float(os.environ[ENV_VAR])
            except ValueError:
                scale = 1.0
            note = "QT_SCALE_FACTOR set by the user"
        elif mode == MODE_MANUAL:
            note = "manual"
        elif probe is None:
            note = "auto, no screen probe"
        else:
            note = f"auto, logical width {round(probe.logical_work_width)}"
    except Exception as exc:
        scale, note = 1.0, f"fallback after {type(exc).__name__}"
    _running_scale = scale
    return scale, note
