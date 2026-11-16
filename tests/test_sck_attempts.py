"""
SCK attempt model (core/rotation/attempts.py) and the safety fixes built on it: candidates bound to
a window, expiring, an untested save not marked verified, SCA paused while a card is open or the
account is locked. Identifiers and passwords are fictional.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import clipboard_watch
from clipboard_watch import ClipboardWatchService
from core.rotation import attempts as attempts_mod
from core.rotation.attempts import SckAttempts
from core.rotation.detector import save_verified_for_rotation
from database import SeraDatabase

APP = QApplication.instance() or QApplication([])
GST_URL = "https://services.gst.gov.in/services/login"


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestAttempts(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.reg = SckAttempts(monotonic=self.clock)

    def test_one_attempt_per_service_and_window(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        b = self.reg.open(7, hwnd=22, client_id=2)
        self.assertIsNot(a, b)
        self.assertIs(self.reg.get(7, 11), a)
        self.assertIs(self.reg.get(7, 22), b)
        self.assertNotEqual(a.attempt_id, b.attempt_id)

    def test_reopening_a_window_blanks_the_old_candidate(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        self.reg.set_candidate(a, "Pw#1", tested=True)
        self.reg.open(7, hwnd=11, client_id=1)
        self.assertEqual(self.reg.take_candidate(a), "")

    def test_take_candidate_blanks_the_slot(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        self.reg.set_candidate(a, "Pw#1", tested=True)
        self.assertEqual(self.reg.take_candidate(a), "Pw#1")
        self.assertEqual(self.reg.take_candidate(a), "")

    def test_candidate_expires(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        self.reg.set_candidate(a, "Pw#1", tested=True)
        self.clock.now += attempts_mod.CANDIDATE_TTL_SEC + 1
        self.assertFalse(self.reg.has_candidate(a))
        self.assertEqual(self.reg.take_candidate(a), "")

    def test_closing_blanks_and_forgets(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        self.reg.set_candidate(a, "Pw#1", tested=False)
        self.reg.close(a)
        self.assertEqual(a.candidate, "")
        self.assertIsNone(self.reg.get(7, 11))
        self.reg.set_candidate(a, "Pw#2", tested=False)      # a closed attempt takes nothing
        self.assertEqual(a.candidate, "")

    def test_repr_never_shows_the_password(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        self.reg.set_candidate(a, "Pw#Fictional9", tested=True)
        self.assertNotIn("Pw#Fictional9", repr(a))

    def test_worked_in_window_b_never_reaches_window_a(self):
        a = self.reg.open(7, hwnd=11, client_id=1)
        self.reg.set_candidate(a, "Pw#A", tested=True)
        self.assertIsNone(self.reg.find_for_outcome(7, 22, "ABCDE1234F"))
        self.assertIs(self.reg.find_for_outcome(7, 11, ""), a)
        self.assertEqual(a.candidate, "Pw#A")

    def test_extension_attempt_matches_by_identity_or_empty_identity(self):
        a = self.reg.open(7, hwnd=0, client_id=1, identity="abcde1234f")
        self.assertIs(self.reg.find_for_outcome(7, 555, "ABCDE1234F"), a)
        self.assertIs(self.reg.find_for_outcome(7, 555, ""), a)
        self.assertIsNone(self.reg.find_for_outcome(7, 555, "ZZZZZ9999Z"))

    def test_suppressed_lists_open_attempts_with_a_client(self):
        self.reg.open(7, hwnd=11, client_id=1)
        self.reg.open(8, hwnd=11, client_id=None, kind="new_client")
        self.assertEqual(self.reg.suppressed(), {(1, 7)})

    def test_lock_and_unlock(self):
        self.reg.lock(1, 7)
        self.assertTrue(self.reg.is_locked(1, 7))
        self.assertFalse(self.reg.is_locked(2, 7))
        self.reg.unlock(1, 7)
        self.assertFalse(self.reg.is_locked(1, 7))


class TestSafetyWithADatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = SeraDatabase(os.path.join(self.tmp, "t.db"),
                               "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                               raw_db_path=os.path.join(self.tmp, "raw.db"))
        cols = self.db.get_mcl_columns()
        self.pan_id = next(c["id"] for c in cols if c.get("is_internal_pk"))
        self.pwd_id = next(c["id"] for c in cols if "pass" in c["label"].lower())
        self.gst = self.db.create_service(name="GST Portal", login_page_link=GST_URL,
                                          userid_column_id=self.pan_id, password_column_id=self.pwd_id,
                                          username_selector="#username", password_selector="#user_pass",
                                          automation_mode="manual")
        self.client = self.db.add_client(values={self.pan_id: "ABCPD1234E", self.pwd_id: "Old#Pw1"},
                                         notes="", service_ids=[self.gst])
        self.reg = attempts_mod.default()
        self.reg.close_service(self.gst)
        self.reg.unlock(self.client, self.gst)

    def tearDown(self):
        self.reg.close_service(self.gst)
        self.reg.unlock(self.client, self.gst)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_untested_save_is_not_tagged_verified(self):
        with patch.object(self.db, "tag_client_scc_verified") as tag:
            r = save_verified_for_rotation(self.db, "New#Pw2", "", self.client, "GST",
                                           "Staff", service_id=self.gst, verified=False)
        self.assertEqual(r.outcome, "replaced")
        tag.assert_not_called()
        self.assertEqual(self.db.get_client(self.client)["values"][self.pwd_id], "New#Pw2")

    def test_tested_save_is_tagged_verified(self):
        with patch.object(self.db, "tag_client_scc_verified") as tag:
            save_verified_for_rotation(self.db, "New#Pw2", "", self.client, "GST",
                                       "Staff", service_id=self.gst, verified=True)
        tag.assert_called_once()

    def test_audit_line_says_untested_and_never_holds_the_password(self):
        with patch.object(self.db, "log_action") as log:
            save_verified_for_rotation(self.db, "New#Pw2", "", self.client, "GST",
                                       "Staff", service_id=self.gst, verified=False)
        detail = log.call_args.kwargs["detail"]
        self.assertIn("untested", detail)
        self.assertNotIn("New#Pw2", detail)

    def _arm_and_request(self):
        sent = []
        with patch("automation.arm_sca", side_effect=lambda req, **k: sent.append(req)):
            watch = ClipboardWatchService(self.db, parent=None)
            watch._last_copy = ("", 0.0)
            watch._arm_client_services(self.client, str(self.client), "ABCPD1234E")
        arm = sent[-1]["arm"]
        msg = {"request_id": "r1", "arm_id": arm["arm_id"], "service_id": self.gst,
               "page_host": "services.gst.gov.in"}
        return watch.handle_password_request(msg)

    def test_sca_is_granted_with_no_card_open(self):
        self.assertEqual(self._arm_and_request()["type"], "SCA_PASSWORD_GRANT")

    def test_sca_is_denied_while_a_card_is_open(self):
        att = self.reg.open(self.gst, hwnd=11, client_id=self.client)
        reply = self._arm_and_request()
        self.assertEqual(reply["type"], "SCA_PASSWORD_DENIED")
        self.assertIn("SCK", reply["reason"])
        self.reg.close(att)
        self.assertEqual(self._arm_and_request()["type"], "SCA_PASSWORD_GRANT")

    def test_sca_is_denied_while_the_account_is_locked(self):
        self.reg.lock(self.client, self.gst)
        reply = self._arm_and_request()
        self.assertEqual(reply["type"], "SCA_PASSWORD_DENIED")
        self.assertIn("locked", reply["reason"])
