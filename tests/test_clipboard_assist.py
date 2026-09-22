"""
Desktop half of SCA v2 (clipboard_watch.py): copy -> arm without passwords; the password is
handed out only on a checked request. Identifiers and passwords are fictional.
"""
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import clipboard_watch
import sca_protocol
from clipboard_watch import ClipboardWatchService, is_excel_source
from database import SeraDatabase

APP = QApplication.instance() or QApplication([])
ITR_URL = "https://eportal.incometax.gov.in/iec/foservices/#/login"
GST_URL = "https://services.gst.gov.in/services/login"


class MockMimeData:
    def __init__(self, formats_list):
        self._formats = formats_list

    def formats(self):
        return self._formats


class TestClipboardAssist(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = SeraDatabase(os.path.join(self.temp_dir, "test.db"),
                               "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                               raw_db_path=os.path.join(self.temp_dir, "raw.db"))
        cols = self.db.get_mcl_columns()
        self.pan_id = next(c["id"] for c in cols if c.get("is_internal_pk"))
        self.pwd_id = next(c["id"] for c in cols if "pass" in c["label"].lower())
        self.other_id = next(c["id"] for c in cols if c["id"] not in (self.pan_id, self.pwd_id)
                             and not any(k in c["label"].lower() for k in clipboard_watch.IDENTITY_LABEL_TERMS))
        self.itr = self.db.create_service(name="Income Tax Portal", login_page_link=ITR_URL,
                                          userid_column_id=self.pan_id, password_column_id=self.pwd_id,
                                          username_selector="#panAdhaarUserId", password_selector="",
                                          automation_mode="manual")
        self.gst = self.db.create_service(name="GST Portal", login_page_link=GST_URL,
                                          userid_column_id=self.pan_id, password_column_id=self.pwd_id,
                                          username_selector="#username", password_selector="#user_pass",
                                          automation_mode="manual")
        self.client = self.db.add_client(values={self.pan_id: "ABCPD1234E", self.pwd_id: "Pw#Fictional1",
                                                 self.other_id: "NEWDELHI"},
                                         notes="", service_ids=[self.itr, self.gst])
        self.sent = []
        self.arm_patch = patch("automation.arm_sca", side_effect=lambda req, **k: self.sent.append(req))
        self.arm_patch.start()
        self.watch = ClipboardWatchService(self.db, parent=None)

    def tearDown(self):
        self.arm_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def arm(self, uid="ABCPD1234E"):
        self.watch._last_copy = ("", 0.0)
        self.watch._arm_client_services(self.client, str(self.client), uid)
        return self.sent[-1]["arm"] if self.sent else None

    def request(self, arm, service_id, host, **over):
        msg = {"request_id": "req_1", "arm_id": arm["arm_id"], "service_id": service_id, "page_host": host}
        msg.update(over)
        return self.watch.handle_password_request(msg)

    # ------------------------------------------------------------------ arming
    def test_excel_format_gating(self):
        self.assertTrue(is_excel_source(MockMimeData(["Csv", "text/plain", "Biff12"])))
        self.assertFalse(is_excel_source(MockMimeData(["text/plain", "text/html"])))

    def test_index_has_the_pan(self):
        self.assertEqual(self.watch._uid_index["ABCPD1234E"], self.client)

    def test_the_arm_carries_no_password(self):
        arm = self.arm()
        self.assertNotIn("Pw#Fictional1", repr(self.sent))
        self.assertTrue(all("password" not in s for s in arm["services"]))
        gst = next(s for s in arm["services"] if s["service_id"] == self.gst)
        self.assertTrue(gst["has_password"])
        self.assertEqual(gst["host"], "services.gst.gov.in")

    def test_only_identity_values_trigger(self):
        arm = self.arm()
        self.assertIn("ABCPD1234E", arm["candidate_uids"])
        self.assertNotIn("NEWDELHI", arm["candidate_uids"])

    def test_a_client_added_later_is_armable_without_a_restart(self):
        new = self.db.add_client(values={self.pan_id: "XYZAB9876C", self.pwd_id: "Pw#Fictional2"},
                                 notes="", service_ids=[self.gst])
        self.watch._ensure_index_fresh()
        self.assertEqual(self.watch._uid_index.get("XYZAB9876C"), new)

    def test_copying_the_same_id_again_rearms_after_two_seconds(self):
        self.watch._last_copy = ("ABCPD1234E", time.time() - 3)
        with patch.object(self.watch, "_arm_client_services") as arm:
            with patch.object(clipboard_watch.QApplication, "instance") as inst:
                cb = inst.return_value.clipboard.return_value
                cb.text.return_value = "ABCPD1234E"
                cb.mimeData.return_value = MockMimeData(["Csv"])
                self.watch._on_clipboard_changed()
                self.watch._on_clipboard_changed()          # same event burst: ignored
        self.assertEqual(arm.call_count, 1)

    def test_a_client_armed_five_times_without_a_fill_is_paused(self):
        for _ in range(clipboard_watch.UNUSED_ARMS_BEFORE_PAUSE + 1):
            self.arm()
        self.assertIn(self.client, self.watch._paused_clients)

    # ------------------------------------------------------------------ password requests
    def test_the_right_portal_gets_its_password(self):
        arm = self.arm()
        reply = self.request(arm, self.gst, "services.gst.gov.in")
        self.assertEqual(reply["type"], sca_protocol.MSG_SCA_PASSWORD_GRANT)
        self.assertEqual((reply["password"], reply["password_selector"], reply["request_id"]),
                         ("Pw#Fictional1", "#user_pass", "req_1"))

    def test_other_sites_and_lookalikes_get_nothing(self):
        arm = self.arm()
        for host in ("web.whatsapp.com", "services.gst.gov.in.evil.com", "eportal.incometax.gov.in"):
            reply = self.request(arm, self.gst, host)
            self.assertEqual(reply["type"], sca_protocol.MSG_SCA_PASSWORD_DENIED, host)
            self.assertNotIn("password", reply)

    def test_uses_run_out(self):
        arm = self.arm()
        self.assertEqual(self.request(arm, self.gst, "services.gst.gov.in")["type"], "SCA_PASSWORD_GRANT")
        self.assertEqual(self.request(arm, self.gst, "services.gst.gov.in")["reason"], "no uses left for this copy")

    def test_an_old_or_expired_arm_gets_nothing(self):
        first = self.arm()
        self.arm()
        self.assertIn("no such arm", self.request(first, self.gst, "services.gst.gov.in")["reason"])
        current = self.sent[-1]["arm"]
        self.watch._arm.expires_at = time.time() - 1
        self.assertEqual(self.request(current, self.gst, "services.gst.gov.in")["reason"], "the arm expired")

    def test_an_unverified_itr_password_needs_a_confirmed_request(self):
        """User choice 2026-09-22: ask before filling an Income Tax password SCC has not verified."""
        arm = self.arm()
        itr = next(s for s in arm["services"] if s["service_id"] == self.itr)
        self.assertEqual((itr["has_password"], itr["needs_confirm"], itr["blocked"]), (True, True, ""))
        refused = self.request(arm, self.itr, "eportal.incometax.gov.in")
        self.assertEqual(refused["type"], "SCA_PASSWORD_DENIED")
        self.assertIn("click on the page card", refused["reason"])
        granted = self.request(arm, self.itr, "eportal.incometax.gov.in", confirmed=True)
        self.assertEqual((granted["type"], granted["password"]), ("SCA_PASSWORD_GRANT", "Pw#Fictional1"))

    def test_a_verified_itr_password_fills_without_asking(self):
        self.db.tag_client_scc_verified(self.client, combo_label="Combo 1")
        arm = self.arm()
        itr = next(s for s in arm["services"] if s["service_id"] == self.itr)
        self.assertFalse(itr["needs_confirm"])
        reply = self.request(arm, self.itr, "eportal.incometax.gov.in")
        self.assertEqual((reply["type"], reply["password"]), ("SCA_PASSWORD_GRANT", "Pw#Fictional1"))

    def test_a_portal_uses_only_its_own_password_column(self):
        """USER ID / EMAIL / TAN are typed 'password' in real vaults: the old fallback would have
        typed the GST user id into the Income Tax password box when IT_Password was empty."""
        cols = {1: {"id": 1, "label": "USER ID", "field_type": "password"},
                2: {"id": 2, "label": "IT_Password", "field_type": "password"}}
        svc = {"name": "GST Portal", "login_page_link": GST_URL, "password_column_id": 2}
        self.assertEqual(self.watch._password_status(svc, self.client, {1: "GSTUSER01", 2: ""}, cols),
                         ("", "no_password"))
        self.assertEqual(self.watch._password_status(svc, self.client, {1: "GSTUSER01", 2: "Pw#X"}, cols),
                         ("Pw#X", ""))
        no_col = {"name": "GST Portal", "login_page_link": GST_URL}
        self.assertEqual(self.watch._password_status(no_col, self.client, {1: "GSTUSER01", 2: "Pw#Y"}, cols),
                         ("Pw#Y", ""))                       # labelled "pass" columns only

    def test_a_failed_fill_tells_staff_why(self):
        notices = []
        self.watch.sca_notice.connect(lambda text, level: notices.append((text, level)))
        arm = self.arm()
        self.watch.handle_fill_result({"arm_id": arm["arm_id"], "service_id": self.itr,
                                       "result": "failed", "reason": "no_password"})
        self.assertEqual(len(notices), 1)
        self.assertIn("Income Tax Portal", notices[0][0])
        self.assertIn("no password is saved", notices[0][0])

    def test_turning_sca_off_refuses(self):
        arm = self.arm()
        self.watch.set_enabled(False)
        self.assertEqual(self.request(arm, self.gst, "services.gst.gov.in")["reason"], "SCA is turned off")


if __name__ == "__main__":
    unittest.main()
