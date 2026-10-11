"""
Shared test setup.

The VSDC router sends a phone alert (through ntfy) when it captures a submission with no
client attached, if the developer has configured a topic in vsdc_alert.json or the
environment. A test run must never do that: it would push real notifications for fake
filings. So every test starts with alerts pointed at a config that does not exist and the
alert environment variables removed. Tests that exercise alerts build their own
AlertSender with an explicit config and a fake transport.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _ui_lock_clipboard_cleared(request):
    """The tests/test_ui_lock_*.py files copy to the clipboard; a QMimeData still held by it
    crashes the interpreter at exit (offscreen), so it is emptied after each of those tests."""
    yield
    if request.module.__name__.startswith("test_ui_lock"):
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is not None:
            QApplication.clipboard().clear()


@pytest.fixture(autouse=True)
def _unanswered_prompt_fails(monkeypatch):
    """Every prompt is a kit dialog (kit.MessageBox); offscreen, one that a test forgot to patch would
    wait forever. After 10 s it is closed and the test fails with its title instead."""
    import sys
    kit = sys.modules.get("ui.components.kit")
    if kit is None:
        yield
        return
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    original, hung = kit._choose, []

    def guarded(parent, title, *args, **kwargs):
        def give_up():
            modal = QApplication.activeModalWidget()
            if modal is not None:
                hung.append(title)
                modal.reject()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(give_up)
        timer.start(10000)
        try:
            return original(parent, title, *args, **kwargs)
        finally:
            timer.stop()

    monkeypatch.setattr(kit, "_choose", guarded)
    yield
    if hung:
        pytest.fail(f"a kit prompt was never answered: {hung}")


@pytest.fixture(autouse=True)
def _no_real_phone_alerts(monkeypatch, tmp_path):
    monkeypatch.setenv("VSDC_ALERT_CONFIG", str(tmp_path / "no_such_vsdc_alert.json"))
    for var in ("VSDC_ALERT_TOPIC", "VSDC_ALERT_SERVER", "VSDC_ALERT_MACHINE"):
        monkeypatch.delenv(var, raising=False)
