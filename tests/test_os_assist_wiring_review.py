"""O3-R review fixes: settings.ini is edited one line at a time, the extension gets no SCA password
in OS mode, a disarm cancels a queued SCA fill. All test data is fictional."""

import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import automation
import clipboard_watch
import sca_protocol
from core.os_assist import sca_engine

_app = QApplication.instance() or QApplication([])

INI = ("; Sera settings - keep this comment\n"
       "[MCL_Columns]\n"
       "PAN = text\n"
       "GSTIN No = text\n"
       "\n"
       "[Automation]\n"
       "browser = chrome\n"
       "\n"
       "[Other]\n"
       "Key = 50%\n")


class TestSettingsIniEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ini = Path(self.tmp.name) / "settings.ini"
        patcher = mock.patch.object(automation, "_settings_ini_path", lambda: self.ini)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_the_engine_line_changes(self):
        self.ini.write_text(INI, encoding="utf-8")
        automation.set_assist_engine("os")
        text = self.ini.read_text(encoding="utf-8")
        self.assertEqual(text, INI.replace("browser = chrome\n", "browser = chrome\nassist_engine = os\n"))
        self.assertEqual(automation.assist_engine(), "os")
        automation.set_assist_engine("extension")
        text = self.ini.read_text(encoding="utf-8")
        self.assertEqual(text.count("assist_engine"), 1)
        self.assertIn("assist_engine = extension", text)
        self.assertEqual(automation.assist_engine(), "extension")

    def test_engine_line_sits_in_the_automation_section(self):
        self.ini.write_text(INI, encoding="utf-8")
        automation.set_assist_engine("os")
        text = self.ini.read_text(encoding="utf-8")
        self.assertLess(text.index("[Automation]"), text.index("assist_engine"))
        self.assertLess(text.index("assist_engine"), text.index("[Other]"))

    def test_bom_file_is_read_and_kept(self):
        self.ini.write_bytes(b"\xef\xbb\xbf" + INI.replace("browser = chrome", "assist_engine = os").encode())
        self.assertEqual(automation.assist_engine(), "os")
        automation.set_assist_engine("extension")
        self.assertTrue(self.ini.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(automation.assist_engine(), "extension")

    def test_no_automation_section_yet(self):
        self.ini.write_text("[MCL_Columns]\nPAN = text\n", encoding="utf-8")
        automation.set_assist_engine("os")
        self.assertEqual(self.ini.read_text(encoding="utf-8"),
                         "[MCL_Columns]\nPAN = text\n\n[Automation]\nassist_engine = os\n")

    def test_non_utf8_bytes_survive(self):
        raw = "[Automation]\nnote = caf\xe9\n".encode("latin-1")
        self.ini.write_bytes(raw)
        automation.set_assist_engine("os")
        self.assertEqual(self.ini.read_text(encoding="latin-1"), "[Automation]\nnote = caf\xe9\nassist_engine = os\n")
        self.assertEqual(automation.assist_engine(), "os")


class _Watcher:
    def __init__(self):
        self.asked = []

    def handle_password_request(self, msg):
        self.asked.append(msg)
        return {"type": sca_protocol.MSG_SCA_PASSWORD_GRANT, "password": "Fict-Pass-01"}


class TestExtensionGetsNoPasswordInOsMode(unittest.TestCase):
    def _ask(self, engine):
        w = _Watcher()
        msg = {"request_id": "req_1", "arm_id": "arm_1", "service_id": 1, "page_host": "services.gst.gov.in"}
        with mock.patch.object(automation, "assist_engine", lambda: engine):
            reply = clipboard_watch.ClipboardWatchService.handle_extension_password_request(w, msg)
        return w, reply

    def test_os_mode_denies_without_asking(self):
        w, reply = self._ask("os")
        self.assertEqual(w.asked, [])
        self.assertEqual(reply["type"], sca_protocol.MSG_SCA_PASSWORD_DENIED)
        self.assertNotIn("password", reply)
        self.assertEqual((reply["request_id"], reply["arm_id"]), ("req_1", "arm_1"))

    def test_extension_mode_unchanged(self):
        w, reply = self._ask("extension")
        self.assertEqual(len(w.asked), 1)
        self.assertEqual(reply["type"], sca_protocol.MSG_SCA_PASSWORD_GRANT)


class _GrantWatcher:
    enabled = True

    def handle_password_request(self, msg):
        return {"type": sca_protocol.MSG_SCA_PASSWORD_GRANT, "request_id": msg["request_id"],
                "arm_id": msg["arm_id"], "service_id": msg["service_id"], "password": "Fict-Pass-01",
                "password_selector": ""}

    def handle_fill_result(self, msg):
        pass


class TestDisarmCancelsQueuedFill(unittest.TestCase):
    def test_queued_fill_is_cancelled(self):
        now = 1_000_000.0
        queued = []

        def submit(fn, *args):
            if fn is filler:
                f = Future()                     # stays queued on the worker
                queued.append(f)
                return f
            f = Future()
            f.set_result(fn(*args))
            return f

        def filler(*_a):
            raise AssertionError("must not run")

        url = "https://services.gst.gov.in/services/login"
        service = {"service_id": 1, "name": "GST", "url": url, "host": sca_protocol.service_host(url),
                   "password_selector": "", "has_password": True, "blocked": "", "needs_confirm": False}
        arm = {"schema": 2, "arm_id": "arm_x", "client_id": 7, "client_id_token": "7", "matched_uid": "ABCDE1234F",
               "candidate_uids": ["ABCDE1234F"], "services": [service], "business_name": "Fictional Traders",
               "owner_name": "A. Person", "sca_mode": "autofill", "max_uses": 1, "trigger": "clipboard",
               "uid_page": "", "created_at": now, "expires_at": now + 300000, "state": "ARMED"}
        eng = sca_engine.ScaEngine(
            _GrantWatcher(), submit=submit, filler=filler,
            probe=lambda *_a: {"hwnd": 5, "host": "services.gst.gov.in", "matched": "ABCDE1234F", "page_link": ""},
            locked=lambda: set(), now=lambda: now, card_factory=lambda *a: None, chip_factory=lambda *a: None)
        self.assertTrue(eng.arm(arm, []))
        eng.tick()
        self.assertEqual(len(queued), 1)
        eng.disarm("DISABLED")
        self.assertTrue(queued[0].cancelled())
        self.assertFalse(eng._pending)


if __name__ == "__main__":
    unittest.main()
