"""
test_dom_page_replace.py
------------------------
Unit tests verifying that when Sera DOM captures data from a page,
the user navigates away, and then returns to the same page link,
the new capture replaces the older one in tracker_dump, rawPayload.db,
and DOM Parser classification.
"""

import os
import json
import tempfile
import unittest
from sqlcipher3 import dbapi2 as sqlite3
from database import SeraDatabase
import security
from DOM_Parser_1.dom_parser import parse_entries_from_sqlite, classify_entries


class TestDomPageRevisitReplace(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_master.db")
        self.raw_db_path = os.path.join(self.tmp_dir.name, "rawPayload.db")
        self.salt_path = os.path.join(self.tmp_dir.name, "test_sera.salt")
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.hex_key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, self.hex_key, raw_db_path=self.raw_db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_dom_page_revisit_replaces_old_capture(self):
        pan = "ABCDE1234F"
        page_1 = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-itr2-ay24/parta"
        page_2 = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-itr2-ay24/schedule_salary"

        # 1. First visit to Page 1
        payload_1 = {
            "portal": "income tax",
            "pan": pan,
            "url": page_1,
            "capture_method": "DOM_Tracker",
            "filing_type": "ITR-2",
            "period_label": "AY 2026-27",
            "scraped_data": {
                "form_fields": {"FirstName": "John", "SurName": "Doe"}
            }
        }
        res_1 = self.db.insert_tracker_dump(
            portal="Income Tax (ITR-2)",
            period_label="AY 2026-27",
            arn_number="N/A",
            capture_method="DOM_Tracker",
            status="Draft",
            raw_payload_json=json.dumps(payload_1),
            pan=pan
        )
        self.assertFalse(res_1.get("replaced", False))
        dump_id_1 = res_1["id"]

        # 2. Navigate away to Page 2
        payload_2 = {
            "portal": "income tax",
            "pan": pan,
            "url": page_2,
            "capture_method": "DOM_Tracker",
            "filing_type": "ITR-2",
            "period_label": "AY 2026-27",
            "scraped_data": {
                "form_fields": {"GrossSalary": "1200000"}
            }
        }
        res_2 = self.db.insert_tracker_dump(
            portal="Income Tax (ITR-2)",
            period_label="AY 2026-27",
            arn_number="N/A",
            capture_method="DOM_Tracker",
            status="Draft",
            raw_payload_json=json.dumps(payload_2),
            pan=pan
        )
        self.assertFalse(res_2.get("replaced", False))
        self.assertEqual(len(self.db.get_tracker_dumps(limit=100)), 2)

        # 3. User navigates back to Page 1 with updated data
        payload_1_updated = {
            "portal": "income tax",
            "pan": pan,
            "url": page_1,
            "capture_method": "DOM_Tracker",
            "filing_type": "ITR-2",
            "period_label": "AY 2026-27",
            "scraped_data": {
                "form_fields": {"FirstName": "Johnny", "SurName": "Doe"}
            }
        }
        res_3 = self.db.insert_tracker_dump(
            portal="Income Tax (ITR-2)",
            period_label="AY 2026-27",
            arn_number="N/A",
            capture_method="DOM_Tracker",
            status="Draft",
            raw_payload_json=json.dumps(payload_1_updated),
            pan=pan
        )
        
        # Verify that the new capture replaced the old one
        self.assertTrue(res_3.get("replaced", False))
        self.assertEqual(res_3["id"], dump_id_1)

        # Total rows should STILL be 2, not 3!
        all_dumps = self.db.get_tracker_dumps(limit=100)
        self.assertEqual(len(all_dumps), 2)

        # The Page 1 entry should now contain "Johnny"
        updated_dump = next(d for d in all_dumps if d["id"] == dump_id_1)
        raw_obj = json.loads(updated_dump["raw_payload_json"])
        self.assertEqual(raw_obj["scraped_data"]["form_fields"]["FirstName"], "Johnny")

        # 4. Verify DOM Parser 1 classification deduplicates drafts by page
        entries = []
        for d in all_dumps:
            entries.append({
                "Entry #": str(d["id"]),
                "Timestamp": d.get("created_at", ""),
                "Portal": d.get("portal", ""),
                "PAN": d.get("unassigned_identity") or pan,
                "Client ID": str(d.get("client_id") or ""),
                "ARN / Ack No": d.get("arn_number", "N/A"),
                "Period": d.get("period_label", ""),
                "Method": d.get("capture_method", "DOM_Tracker"),
                "Status": d.get("status", "Draft"),
                "json": json.loads(d["raw_payload_json"]) if isinstance(d["raw_payload_json"], str) else d["raw_payload_json"]
            })
        classified = classify_entries(entries)
        self.assertEqual(len(classified["cat4"]), 2)
        cat4_names = [e.get("name") for e in classified["cat4"]]
        self.assertTrue(any("Johnny Doe" in n for n in cat4_names))


if __name__ == "__main__":
    unittest.main()
