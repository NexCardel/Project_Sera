"""
core/vsdc/vsdc_engines.py - which capture engines are switched on
==================================================================
Settings -> Tracker has three independent switches:

    vsdc_enabled     VSDC    - the crosshair pipeline, reading the screen (OCR)
    vsdc_x_enabled   VSDC-X  - UI Automation exact-text reading
    vsdc247_enabled  VSDC247 - the crosshair-independent submission safety net

They are stored as "1" / "0" in the settings table. This module only turns those stored
values into three booleans, using the same defaults the settings page shows for a value that
was never saved, so what the page shows is what runs. VSDCRouter.apply_engine_settings()
does the work with them.
"""

from typing import Callable, Optional, Tuple

# The defaults the Settings -> Tracker page shows for a switch that was never saved.
ENGINE_DEFAULTS = {"vsdc_enabled": "1", "vsdc_x_enabled": "1", "vsdc247_enabled": "0"}

# The HUD pill switch (Settings -> Tracker). It is not an engine - it only decides whether the
# pill is shown - so it lives beside ENGINE_DEFAULTS rather than in it.
HUD_SETTING = "vsdc_hud_enabled"
HUD_DEFAULT = "1"

# SGT - Sera Global Tracker - the fourth switch. Not on/off but a mode: "off" or "shadow"
# ("live" arrives once shadow mode has been compared against the live pipeline).
SGT_SETTING = "sgt_mode"
SGT_DEFAULT = "off"
SGT_MODES = ("off", "shadow")
# Record the text of the pages SGT reads, for replaying spec changes against real pages
# (core/sgt/sgt_corpus.py - local only, 30 days). Only matters while SGT is on.
SGT_RECORD_SETTING = "sgt_record_pages"
SGT_RECORD_DEFAULT = "1"

_TRUE = ("1", "true", "yes", "on")


def _on(value: Optional[object], default: str) -> bool:
    text = str(default if value is None else value).strip().lower()
    return text in _TRUE


def read_engine_flags(get_setting: Callable[..., object]) -> Tuple[bool, bool, bool]:
    """(vsdc, vsdc_x, vsdc247) from a get_setting(key, default) callable such as db.get_setting."""
    def flag(key: str) -> bool:
        try:
            return _on(get_setting(key, ENGINE_DEFAULTS[key]), ENGINE_DEFAULTS[key])
        except Exception:
            return _on(None, ENGINE_DEFAULTS[key])
    return flag("vsdc_enabled"), flag("vsdc_x_enabled"), flag("vsdc247_enabled")


def read_sgt_mode(get_setting: Callable[..., object]) -> str:
    """SGT's mode: "off" or "shadow". Anything unrecognised reads as off."""
    try:
        value = str(get_setting(SGT_SETTING, SGT_DEFAULT) or SGT_DEFAULT).strip().lower()
    except Exception:
        return SGT_DEFAULT
    return value if value in SGT_MODES else SGT_DEFAULT


def read_sgt_record_pages(get_setting: Callable[..., object]) -> bool:
    """Whether SGT records the pages it reads (default on)."""
    try:
        return str(get_setting(SGT_RECORD_SETTING, SGT_RECORD_DEFAULT) or SGT_RECORD_DEFAULT).strip() == "1"
    except Exception:
        return True


def read_hud_enabled(get_setting: Callable[..., object]) -> bool:
    """Whether the HUD pill is switched on (default on, matching the settings page)."""
    try:
        return _on(get_setting(HUD_SETTING, HUD_DEFAULT), HUD_DEFAULT)
    except Exception:
        return _on(None, HUD_DEFAULT)
