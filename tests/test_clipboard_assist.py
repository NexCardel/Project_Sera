import unittest
import tempfile
import os
import shutil
import sqlite3
from unittest.mock import MagicMock

from database import SeraDatabase
from clipboard_watch import ClipboardWatchService, is_excel_source


class MockMimeData:
    def __init__(self, formats_list):
        self._formats = formats_list

    def formats(self):
        return self._formats


class TestClipboardAssist(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        self.db = SeraDatabase(self.db_path, self.hex_key)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_excel_format_gating(self):
        excel_mime = MockMimeData(["Csv", "text/plain", "Biff12"])
        non_excel_mime = MockMimeData(["text/plain", "text/html", "application/x-qt-windows-mime;value=\"UniformResourceLocator\""])

        self.assertTrue(is_excel_source(excel_mime))
        self.assertFalse(is_excel_source(non_excel_mime))

    def test_in_memory_index_building(self):
        pan_col = next((c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
        pan_id = pan_col["id"] if pan_col else 5
        client_id = self.db.add_client(values={pan_id: "ABCDE1234F"}, notes="Test Client", service_ids=[])
        
        service = ClipboardWatchService(self.db, parent=None)
        self.assertIn("ABCDE1234F", service._uid_index)
        self.assertEqual(service._uid_index["ABCDE1234F"], client_id)

    def test_debouncing(self):
        service = ClipboardWatchService(self.db, parent=None)
        service._uid_index = {"TESTPAN123": 1}
        service._last_armed_token = "1"
        import time
        service._last_armed_time = time.time()
        
        # Inside debounce window, same token should not re-arm
        now = time.time()
        is_debounced = (service._last_armed_token == "1" and (now - service._last_armed_time) < service._debounce_window)
        self.assertTrue(is_debounced)

    def test_sca_password_gated_by_scc_verification(self):
        """Verify SCA strictly zeroes out password for ITR services unless SCC-verified."""
        # 1. Setup MCL and Services
        pan_col = next((c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
        pan_id = pan_col["id"] if pan_col else 5
        pwd_col = next((c for c in self.db.get_mcl_columns() if "pass" in c["label"].lower()), None)
        pwd_id = pwd_col["id"] if pwd_col else 6

        itr_svc_id = self.db.create_service(
            name="Income Tax Portal",
            login_page_link="https://eportal.incometax.gov.in/iec/foservices/#/login",
            userid_column_id=pan_id,
            password_column_id=pwd_id,
            username_selector="#panAdhaarUserId",
            password_selector="input[type='password']",
            automation_mode="manual"
        )

        # Client 1: Unverified client
        c1_id = self.db.add_client(
            values={pan_id: "ABCDE1234F", pwd_id: "UnverifiedPass#1"},
            notes="Regular notes without scc tag",
            service_ids=[itr_svc_id]
        )

        service = ClipboardWatchService(self.db, parent=None)
        armed_payloads = []
        from unittest.mock import patch
        with patch("automation.arm_sca", side_effect=lambda **kwargs: armed_payloads.append(kwargs.get("services"))):
            service._arm_client_services(c1_id, "ABCDE1234F", "ABCDE1234F")

        self.assertEqual(len(armed_payloads), 1)
        itr_entry = armed_payloads[0][0]
        self.assertEqual(itr_entry["user_id"], "ABCDE1234F")
        self.assertEqual(itr_entry["password"], "", "Password MUST be empty for unverified client on ITR portal")

        # Now verify client via SCC tag
        self.db.tag_client_scc_verified(c1_id, combo_label="Combo 1")
        self.assertTrue(self.db.is_client_scc_verified(client_id=c1_id))

        armed_payloads.clear()
        with patch("automation.arm_sca", side_effect=lambda **kwargs: armed_payloads.append(kwargs.get("services"))):
            service._arm_client_services(c1_id, "ABCDE1234F", "ABCDE1234F")

        self.assertEqual(len(armed_payloads), 1)
        itr_entry = armed_payloads[0][0]
        self.assertEqual(itr_entry["user_id"], "ABCDE1234F")
        self.assertEqual(itr_entry["password"], "UnverifiedPass#1", "Password MUST be delivered once verified via SCC")


if __name__ == "__main__":
    unittest.main()
