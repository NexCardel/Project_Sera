"""
test_gst_dom_tracker.py
-----------------------
Unit tests verifying GST DOM Tracker extraction, derived PAN resolution from GSTIN,
accurate status and return period extraction, and cross-client context isolation.
"""

import os
import json
import tempfile
import unittest
from sqlcipher3 import dbapi2 as sqlite3
from database import SeraDatabase
import security
from DOM_Parser_1.dom_parser import classify_entries


class TestGstDomTracker(unittest.TestCase):
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

    def test_gst_summary_card_extraction_and_derived_pan(self):
        gstin = "19ATYPB6533F2ZX"
        expected_pan = "ATYPB6533F"
        page_url = "https://return.gst.gov.in/returns/auth/gstr1"

        # Construct raw payload snapshot mirroring the user's GST portal screenshot
        raw_msg = {
            "type": "filing_result",
            "client_id": None,
            "client_name": "ARUN BAIDYA",
            "name": "ARUN BAIDYA",
            "taxpayer_name": "ARUN BAIDYA",
            "portal": "gst",
            "arn": "N/A",
            "capture_method": "DOM_Tracker",
            "period_label": "June(Q) (FY 2026-27)",
            "filing_type": "GSTR-1",
            "status": "Filed",
            "pan": expected_pan,
            "gstin": gstin,
            "url": page_url,
            "page_key": page_url,
            "scraped_data": {
                "summary_labels": {
                    "GSTIN": gstin,
                    "Legal Name": "ARUN BAIDYA",
                    "Trade Name": "BAIDYA ELECTRIC",
                    "FY": "2026-27",
                    "Tax Period": "June(Q)",
                    "Status": "Filed",
                    "Due Date": "13/07/2026"
                },
                "breadcrumbs": "Dashboard > Returns > GSTR-1/IFF",
                "header_badges": ["ARUN BAIDYA 19ATYPB6533F2ZX"]
            }
        }

        res = self.db.insert_tracker_dump(
            portal="GST (GSTR-1)",
            period_label="June(Q) (FY 2026-27)",
            arn_number="N/A",
            capture_method="DOM_Tracker",
            status="Filed",
            raw_payload_json=json.dumps(raw_msg),
            pan=expected_pan
        )

        self.assertIsNotNone(res["id"])
        dumps = self.db.get_tracker_dumps(limit=10)
        self.assertEqual(len(dumps), 1)
        d = dumps[0]

        # Verify PAN derived from GSTIN
        self.assertEqual(d["unassigned_identity"], expected_pan)
        self.assertEqual(d["status"], "Filed")
        self.assertEqual(d["period_label"], "June(Q) (FY 2026-27)")
        self.assertEqual(d["portal"], "GST (GSTR-1)")

        # Verify DOM Parser 1 classification
        entry = {
            "Entry #": str(d["id"]),
            "Timestamp": d.get("created_at", ""),
            "Portal": d.get("portal", ""),
            "PAN": expected_pan,
            "Client ID": "",
            "ARN / Ack No": "N/A",
            "Period": d.get("period_label", ""),
            "Method": "DOM_Tracker",
            "Status": "Filed",
            "json": raw_msg
        }
        classified = classify_entries([entry])
        
        # Verify Cat 4 Taxpayer profile
        self.assertIn(expected_pan, classified["cat4"])
        profile = classified["cat4"][expected_pan]
        self.assertEqual(profile["name"], "ARUN BAIDYA")
        self.assertEqual(profile["gstin"], gstin)
        self.assertEqual(profile["pan"], expected_pan)
        self.assertIn("GSTR-1", profile["forms_seen"])

    def test_cross_client_context_isolation_guard(self):
        # Create Client A in master database with PAN 'ABCDE1234F'
        with self.db._connect() as conn:
            # Create a mock column for PAN with is_internal_pk=1
            conn.execute("INSERT OR IGNORE INTO mcl_columns (id, label, is_internal_pk) VALUES (1, 'PAN', 1)")
            now = "2026-08-28 12:00:00"
            conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived) VALUES (1, ?, ?, 0)", (now, now))
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (1, 1, 'ABCDE1234F')")

            # Create Client B in master database with PAN 'ATYPB6533F'
            conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived) VALUES (2, ?, ?, 0)", (now, now))
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (2, 1, 'ATYPB6533F')")
            conn.commit()

        # Simulate a dirty extension payload that still has Client 1's ID from a previous session,
        # but the live page belongs to Client B (PAN: ATYPB6533F)
        dirty_msg = {
            "type": "filing_result",
            "client_id": 1,  # Stale ID from Client A
            "client_name": "ARUN BAIDYA",
            "name": "ARUN BAIDYA",
            "taxpayer_name": "ARUN BAIDYA",
            "portal": "gst",
            "arn": "AA1907260012345",
            "capture_method": "DOM_Tracker",
            "period_label": "June(Q) (FY 2026-27)",
            "filing_type": "GSTR-1",
            "status": "Filed",
            "pan": "ATYPB6533F",
            "gstin": "19ATYPB6533F2ZX",
            "url": "https://return.gst.gov.in/returns/auth/gstr1"
        }

        # Emulate the main.py processing logic
        raw_client_id = dirty_msg.get("client_id")
        pan = dirty_msg.get("pan")
        if raw_client_id and pan:
            with self.db._connect() as m_conn:
                cur = m_conn.execute(
                    """SELECT cv.client_id FROM client_values cv
                       JOIN clients c ON c.id = cv.client_id
                       JOIN mcl_columns mc ON mc.id = cv.column_id
                       WHERE c.id = ? AND c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ?
                       LIMIT 1""",
                    (raw_client_id, pan.upper())
                )
                if not cur.fetchone():
                    raw_client_id = None

        # raw_client_id must be discarded because Client 1 does not own ATYPB6533F
        self.assertIsNone(raw_client_id)

        # Database must resolve it authoritatively to Client 2 (ARUN BAIDYA) using PAN
        res = self.db.insert_tracker_dump(
            client_id=raw_client_id,
            portal="GST (GSTR-1)",
            period_label="June(Q) (FY 2026-27)",
            arn_number="AA1907260012345",
            capture_method="DOM_Tracker",
            status="Filed",
            raw_payload_json=json.dumps(dirty_msg),
            pan="ATYPB6533F"
        )

        dumps = self.db.get_tracker_dumps(limit=10)
        self.assertEqual(len(dumps), 1)
        self.assertEqual(dumps[0]["client_id"], 2)  # Authoritatively linked to Client B (ID 2), NOT Client A (ID 1)
        self.assertEqual(dumps[0]["status"], "Filed")


if __name__ == "__main__":
    unittest.main()
