"""
Autofill tweaks B4/#15: when Client Detail launches Manual Copy (MECP) or Manual Assist (SMTI)
for a client, copying that client's ids must not arm SCA for the next 5 minutes. Other clients
arm as before. Identifiers are fictional.
"""
import inspect
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import clipboard_watch
from clipboard_watch import ClipboardWatchService

APP = QApplication.instance() or QApplication([])


class _FakeDb:
    def search_clients(self, *_a, **_k):
        return []

    def get_mcl_columns(self):
        return []

    def get_services(self):
        return []


class _Mime:
    def formats(self):
        return ["Csv"]


class TestSuppressClient(unittest.TestCase):
    def setUp(self):
        clipboard_watch._suppressed_until.clear()
        with patch.object(clipboard_watch.QTimer, "singleShot"):
            self.watch = ClipboardWatchService(_FakeDb(), parent=None)
        self.watch._uid_index = {"ABCPD1234E": 7, "XYZAB9876C": 8}
        self.watch._ensure_index_fresh = lambda: None

    def tearDown(self):
        clipboard_watch._suppressed_until.clear()

    def copy(self, text):
        self.watch._last_copy = ("", 0.0)
        with patch.object(self.watch, "_arm_client_services") as arm:
            with patch.object(clipboard_watch.QApplication, "instance") as inst:
                cb = inst.return_value.clipboard.return_value
                cb.text.return_value = text
                cb.mimeData.return_value = _Mime()
                self.watch._on_clipboard_changed()
        return arm.call_count

    def test_unsuppressed_client_arms(self):
        self.assertEqual(self.copy("ABCPD1234E"), 1)

    def test_suppressed_client_does_not_arm(self):
        clipboard_watch.suppress_client(7)
        self.assertEqual(self.copy("ABCPD1234E"), 0)

    def test_suppression_is_per_client(self):
        clipboard_watch.suppress_client(7)
        self.assertEqual(self.copy("XYZAB9876C"), 1)

    def test_suppression_lasts_five_minutes_by_default(self):
        now = time.time()
        clipboard_watch.suppress_client("7")        # ids arrive as str or int
        self.assertAlmostEqual(clipboard_watch._suppressed_until[7], now + 300, delta=5)
        self.assertTrue(clipboard_watch.is_suppressed(7))

    def test_suppression_expires(self):
        clipboard_watch.suppress_client(7, seconds=300)
        with patch.object(clipboard_watch.time, "time", return_value=time.time() + 301):
            self.assertFalse(clipboard_watch.is_suppressed(7))
            self.assertEqual(self.copy("ABCPD1234E"), 1)

    def test_bad_client_id_is_ignored(self):
        clipboard_watch.suppress_client(None)
        self.assertEqual(clipboard_watch._suppressed_until, {})


class TestClientDetailSuppresses(unittest.TestCase):
    def tearDown(self):
        clipboard_watch._suppressed_until.clear()

    def test_launches_suppress_the_client(self):
        from ui.windows.client_detail_window import ClientDetailWindow
        ClientDetailWindow._suppress_sca_for_client(SimpleNamespace(client={"id": 42}))
        self.assertTrue(clipboard_watch.is_suppressed(42))
        for name in ("_launch_manual_copy", "_launch_manual_assist"):
            src = inspect.getsource(getattr(ClientDetailWindow, name))
            self.assertIn("self._suppress_sca_for_client()", src, name)
            # suppressed before the extension is told to open the card
            self.assertLess(src.index("_suppress_sca_for_client"), src.index("automation.trigger_"), name)


if __name__ == "__main__":
    unittest.main()
