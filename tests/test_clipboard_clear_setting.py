"""The desktop tells the extension how many seconds a copied password may stay on the clipboard."""
import threading

import automation
from ui import ws_bridge


class _Bridge:
    def __init__(self):
        self.sent = []
        self.done = threading.Event()

    def broadcast(self, payload):
        self.sent.append(payload)
        self.done.set()
        return 1


def _send(monkeypatch, **kwargs):
    bridge = _Bridge()
    monkeypatch.setattr(ws_bridge, "get_active_bridge", lambda: bridge)
    automation.update_extension_settings(**kwargs)
    assert bridge.done.wait(5)
    return bridge.sent[0]


def test_update_settings_carries_clipboard_clear_seconds(monkeypatch):
    assert _send(monkeypatch, clipboard_clear_seconds=45)["clipboard_clear_seconds"] == 45


def test_clipboard_clear_seconds_is_clamped_to_the_settings_range(monkeypatch):
    assert _send(monkeypatch, clipboard_clear_seconds=1)["clipboard_clear_seconds"] == 5
    assert _send(monkeypatch, clipboard_clear_seconds=9999)["clipboard_clear_seconds"] == 300


def test_update_settings_without_it_leaves_the_extension_value_alone(monkeypatch):
    assert "clipboard_clear_seconds" not in _send(monkeypatch)
