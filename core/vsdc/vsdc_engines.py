"""
core/vsdc/vsdc_engines.py - which capture engines are switched on
==================================================================
Settings -> Tracker has three independent switches:

    vsdc_enabled     VSDC    - the crosshair pipeline, reading the screen (OCR)
    vsdc_x_enabled   VSDC-X  - UI Automation exact-text reading
    vsdc247_enabled  VSDC247 - the crosshair-independent submission safety net

and SGT's mode (sgt_mode: off / shadow / live). SGT live is the main capture engine
(2026-09-26): it runs alone, the other three off.

They are stored as "1" / "0" in the settings table. This module only turns those stored
values into three booleans, using the same defaults the settings page shows for a value that
was never saved, so what the page shows is what runs. VSDCRouter.apply_engine_settings()
does the work with them.
"""

from typing import Callable, Dict, Optional, Tuple

# The defaults the Settings -> Tracker page shows for a switch that was never saved. All off:
# SGT (below) is the capture engine.
ENGINE_DEFAULTS = {"vsdc_enabled": "0", "vsdc_x_enabled": "0", "vsdc247_enabled": "0"}

# The HUD pill switch (Settings -> Tracker). It is not an engine - it only decides whether the
# pill is shown - so it lives beside ENGINE_DEFAULTS rather than in it.
HUD_SETTING = "vsdc_hud_enabled"
HUD_DEFAULT = "1"

# SGT - Sera Global Tracker - the fourth switch. Not on/off but a mode: "off", "shadow" (its
# rows kept apart from the other engines', for comparison) or "live" (the capture engine).
SGT_SETTING = "sgt_mode"
SGT_DEFAULT = "live"
SGT_MODES = ("off", "shadow", "live")
# One-time switch-over of the whole office to SGT live (settings are office-wide and synced):
# SGT live, VSDC / VSDC-X / VSDC 24/7 off. The marker makes it happen once - a PC switched back
# afterwards by hand stays as it was set.
SGT_LIVE_ROLLOUT_SETTING = "sgt_live_rollout_done"
SGT_LIVE_ROLLOUT_VALUES = {SGT_SETTING: "live", "vsdc_enabled": "0", "vsdc_x_enabled": "0", "vsdc247_enabled": "0"}
# Record the text of the pages SGT reads, for replaying spec changes against real pages
# (core/sgt/sgt_corpus.py - local only, 30 days). Only matters while SGT is on.
SGT_RECORD_SETTING = "sgt_record_pages"
SGT_RECORD_DEFAULT = "1"
# SGT-I - SGT's Intelligence half (core/sgt_i, blueprint 14.2): "off" or "on". It watches the
# pages SGT reads on its own thread and never changes what SGT captures. Off = SGT exactly as before.
SGT_I_SETTING = "sgt_i_mode"
SGT_I_DEFAULT = "off"
SGT_I_MODES = ("off", "on")
# SCC-U - Income Tax login detection on SGT's reads (core/scc, autofill-tweaks blueprint Part G):
# "off" or "on" (Settings -> SCC -> Detect login automatically). Off = SGT exactly as before.
SCC_DETECT_SETTING = "scc_detect_mode"
SCC_DETECT_DEFAULT = "off"
SCC_DETECT_MODES = ("off", "on")

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
    """SGT's mode: "off", "shadow" or "live". Anything unrecognised reads as the default."""
    try:
        value = str(get_setting(SGT_SETTING, SGT_DEFAULT) or SGT_DEFAULT).strip().lower()
    except Exception:
        return SGT_DEFAULT
    return value if value in SGT_MODES else SGT_DEFAULT


def apply_sgt_live_rollout(get_setting: Callable[..., object],
                           set_settings: Callable[[Dict[str, str]], None]) -> bool:
    """Switches this office to SGT live once (see SGT_LIVE_ROLLOUT_VALUES). True when it did."""
    try:
        if str(get_setting(SGT_LIVE_ROLLOUT_SETTING, "") or "").strip() == "1":
            return False
        set_settings({**SGT_LIVE_ROLLOUT_VALUES, SGT_LIVE_ROLLOUT_SETTING: "1"})
    except Exception as e:
        print(f"[SGT] could not switch the engines over to SGT live: {e}")
        return False
    print("[SGT] switched to SGT live: SGT is the capture engine; VSDC, VSDC-X and VSDC 24/7 are off")
    return True


def read_sgt_record_pages(get_setting: Callable[..., object]) -> bool:
    """Whether SGT records the pages it reads (default on)."""
    try:
        return str(get_setting(SGT_RECORD_SETTING, SGT_RECORD_DEFAULT) or SGT_RECORD_DEFAULT).strip() == "1"
    except Exception:
        return True


def read_sgt_i_mode(get_setting: Callable[..., object]) -> str:
    """SGT-I's mode: "off" or "on". Anything unrecognised reads as off."""
    try:
        value = str(get_setting(SGT_I_SETTING, SGT_I_DEFAULT) or SGT_I_DEFAULT).strip().lower()
    except Exception:
        return SGT_I_DEFAULT
    return value if value in SGT_I_MODES else SGT_I_DEFAULT


def read_scc_detect_mode(get_setting: Callable[..., object]) -> str:
    """SCC-U's mode: "off" or "on". Anything unrecognised reads as off."""
    try:
        value = str(get_setting(SCC_DETECT_SETTING, SCC_DETECT_DEFAULT) or SCC_DETECT_DEFAULT).strip().lower()
    except Exception:
        return SCC_DETECT_DEFAULT
    return value if value in SCC_DETECT_MODES else SCC_DETECT_DEFAULT


def read_hud_enabled(get_setting: Callable[..., object]) -> bool:
    """Whether the HUD pill is switched on (default on, matching the settings page)."""
    try:
        return _on(get_setting(HUD_SETTING, HUD_DEFAULT), HUD_DEFAULT)
    except Exception:
        return _on(None, HUD_DEFAULT)
