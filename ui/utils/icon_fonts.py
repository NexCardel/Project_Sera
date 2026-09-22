"""
ui/utils/icon_fonts.py — load only the icon font the app uses
==============================================================
qtawesome loads every icon font it ships (12 of them, plus their character maps) the first
time any icon is drawn. The app only ever uses Material Design Icons ("mdi."), so the other
11 cost ~15 MB of memory for nothing (measured 2026-09-21).

USED_ICON_PREFIXES is the single place to widen this. tests/test_memory_tuning.py fails if
any code asks for an icon from a font that is not listed here, so a new icon can never
silently come out blank.
"""

USED_ICON_PREFIXES = ("mdi",)


def restrict_icon_fonts() -> bool:
    """Call before the first icon is drawn. Returns True if qtawesome was restricted."""
    try:
        import qtawesome
        fonts = getattr(qtawesome, "_BUNDLED_FONTS", None)
        if not fonts:
            return False
        kept = tuple(f for f in fonts if f and f[0] in USED_ICON_PREFIXES)
        if not kept:            # never leave the app with no icons at all
            return False
        qtawesome._BUNDLED_FONTS = kept
        return True
    except Exception:
        return False
