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


def read_hud_enabled(get_setting: Callable[..., object]) -> bool:
    """Whether the HUD pill is switched on (default on, matching the settings page)."""
    try:
        return _on(get_setting(HUD_SETTING, HUD_DEFAULT), HUD_DEFAULT)
    except Exception:
        return _on(None, HUD_DEFAULT)
