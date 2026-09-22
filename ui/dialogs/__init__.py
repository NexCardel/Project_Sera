"""
The AI dialogs load the whole capture-engine package (core.vsdc: OCR, numpy, UI Automation).
Importing them here eagerly made every `ui.dialogs.<anything>` import pay ~0.4 s of the app's
start-up before the window appeared (measured 2026-09-22), so they load on first use instead.
"""

__all__ = ["AISettingsDialog", "GeminiSettingsDialog", "AIDialog"]


def __getattr__(name):
    if name in __all__:
        from . import ai_settings_dialog
        return getattr(ai_settings_dialog, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
