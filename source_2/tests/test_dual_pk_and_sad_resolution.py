"""
test_dual_pk_and_sad_resolution.py
----------------------------------
Unit tests verifying Dual Primary Key architecture and authoritative SAD identity resolution.
"""

import os
import json
import tempfile
import unittest
from database import SeraDatabase
import security


class TestDualPKAndSadResolution(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_sera_dual_pk.db")
        self.salt_path = os.path.join(self.tmp_dir.name, "test_sera.salt")
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        hex_key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, hex_key)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_internal_pk_mandatory_and_unique(self):
        """Verifies that columns with is_internal_pk=1 are strictly mandatory and unique."""
        mcl_cols = self.db.get_mcl_columns()
        pan_col = next((c for c in mcl_cols if c["label"].strip().upper() == "PAN"), None)
        self.assertIsNotNone(pan_col)
        self.assertTrue(pan_col.get("is_internal_pk"))

        # 1. Attempting to add client without PAN should raise ValueError
        with self.assertRaises(ValueError):
            self.db.add_client(values={}, notes="Missing PAN test", service_ids=[])

        with self.assertRaises(ValueError):
            self.db.add_client(values={pan_col["id"]: "   "}, notes="Whitespace PAN test", service_ids=[])

        # 2. Adding client with valid PAN should succeed
        cid_1 = self.db.add_client(values={pan_col["id"]: "ABCDE1234F"}, notes="Client 1", service_ids=[])
        self.assertGreater(cid_1, 0)

        # 3. Attempting to add duplicate PAN should raise ValueError
        with self.assertRaises(ValueError):
            self.db.add_client(values={pan_col["id"]: "ABCDE1234F"}, notes="Client 2 with same PAN", service_ids=[])

        # 4. Updating client with empty PAN or duplicate PAN should raise ValueError
        cid_2 = self.db.add_client(values={pan_col["id"]: "XYZPW9999K"}, notes="Client 2", service_ids=[])
        with self.assertRaises(ValueError):
            self.db.update_client(cid_2, values={pan_col["id"]: ""}, notes="Updated", service_ids=[])

        with self.assertRaises(ValueError):
            self.db.update_client(cid_2, values={pan_col["id"]: "ABCDE1234F"}, notes="Updated", service_ids=[])

    def test_sad_capture_no_false_attribution(self):
        """
        Verifies that incoming SAD network capture with a new PAN does NOT get falsely
        attributed to previous active session client_id (e.g. 66).
        """
        mcl_cols = self.db.get_mcl_columns()
        pan_col = next(c for c in mcl_cols if c["label"].strip().upper() == "PAN")
        name_col = next(c for c in mcl_cols if "COMPANY" in c["label"].upper() or "NAME" in c["label"].upper())

        # Create Client #1 (Bishalakshi Enterprise, PAN: AAAAA1111A)
        cid_1 = self.db.add_client(values={name_col["id"]: "Bishalakshi Enterprise", pan_col["id"]: "AAAAA1111A"}, notes="", service_ids=[])

        # Capture incoming from network with ARN for un-onboarded client (PAN: AMTPL4994M)
        # Even if client_id=cid_1 is passed (stale session), DB must resolve authoritatively by identity!
        res = self.db.insert_tracker_dump(
            client_id=cid_1,
            portal="Income Tax Portal",
            arn_number="PROFILE-AMTPL4994M",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"pan": "AMTPL4994M", "client_id": cid_1})
        )

        self.assertIsNone(res["client_id"])
        self.assertEqual(res["unassigned_identity"], "AMTPL4994M")

        dumps = self.db.get_tracker_dumps()
        self.assertGreaterEqual(len(dumps), 1)
        latest = dumps[0]
        self.assertTrue(latest["is_unassigned"])
        self.assertIn("Unregistered (PAN: AMTPL4994M)", latest["client_name"])
        self.assertIsNone(latest["client_id"])

    def test_sad_capture_matches_existing_client(self):
        """Verifies that an incoming capture with a known PAN correctly binds to that client."""
        mcl_cols = self.db.get_mcl_columns()
        pan_col = next(c for c in mcl_cols if c["label"].strip().upper() == "PAN")
        name_col = next(c for c in mcl_cols if "COMPANY" in c["label"].upper() or "NAME" in c["label"].upper())

        cid_target = self.db.add_client(values={name_col["id"]: "Target Client Ltd", pan_col["id"]: "AXTPT8591P"}, notes="", service_ids=[])

        # Capture arrives with no client_id or wrong client_id, but has PAN in ARN
        res = self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax",
            arn_number="PROFILE-AXTPT8591P",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"pan": "AXTPT8591P"})
        )

        self.assertEqual(res["client_id"], cid_target)
        self.assertIsNone(res["unassigned_identity"])

        dumps = self.db.get_tracker_dumps()
        latest = dumps[0]
        self.assertFalse(latest["is_unassigned"])
        self.assertEqual(latest["client_id"], cid_target)
        self.assertIn("Target Client Ltd", latest["client_name"])

    def test_link_unassigned_tracker_dumps(self):
        """Verifies retroactive linking when an unassigned client is created from capture."""
        mcl_cols = self.db.get_mcl_columns()
        pan_col = next(c for c in mcl_cols if c["label"].strip().upper() == "PAN")
        name_col = next(c for c in mcl_cols if "COMPANY" in c["label"].upper() or "NAME" in c["label"].upper())

        # Log 2 unassigned captures for PAN: ASDPM3313P
        self.db.insert_tracker_dump(
            client_id=None,
            portal="GST",
            arn_number="AA1908260001234",
            raw_payload_json=json.dumps({"pan": "ASDPM3313P", "gstin": "19ASDPM3313P1Z5"})
        )
        self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax",
            arn_number="PROFILE-ASDPM3313P",
            raw_payload_json=json.dumps({"pan": "ASDPM3313P"})
        )

        # Both should be unassigned
        unassigned_dumps = [d for d in self.db.get_tracker_dumps() if d.get("is_unassigned")]
        self.assertEqual(len(unassigned_dumps), 2)

        # Now user creates client for ASDPM3313P
        new_cid = self.db.add_client(values={name_col["id"]: "New Onboarded Client", pan_col["id"]: "ASDPM3313P"}, notes="", service_ids=[])
        linked_count = self.db.link_unassigned_tracker_dumps(new_cid, "ASDPM3313P")
        self.assertEqual(linked_count, 2)

        # All captures should now belong to new_cid
        all_dumps = self.db.get_tracker_dumps()
        for d in all_dumps:
            self.assertEqual(d["client_id"], new_cid)
            self.assertFalse(d["is_unassigned"])
            self.assertIn("New Onboarded Client", d["client_name"])

    def test_legacy_not_null_tracker_dump_migration(self):
        """Verifies that an existing legacy database with NOT NULL on tracker_dump.client_id seamlessly migrates to nullable."""
        with self.db._connect() as conn:
            # Force recreate a legacy tracker_dump table with NOT NULL constraint
            conn.execute("DROP TABLE IF EXISTS tracker_dump")
            conn.execute("""
                CREATE TABLE tracker_dump (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    service_id      INTEGER,
                    portal          TEXT,
                    period_label    TEXT,
                    arn_number      TEXT,
                    capture_method  TEXT DEFAULT 'DOM_Tracker',
                    status          TEXT DEFAULT 'submitted',
                    raw_payload_json TEXT,
                    captured_by     TEXT,
                    created_at      TEXT NOT NULL
                )
            """)

        # Inserting an unassigned capture with client_id=None must trigger auto-migration and succeed
        res = self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax Portal",
            arn_number="315279500290321",
            pan="CIQPM7599L",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"pan": "CIQPM7599L"})
        )

        self.assertIsNone(res["client_id"])
        self.assertEqual(res["unassigned_identity"], "CIQPM7599L")
        self.assertEqual(res["arn_number"], "315279500290321")

    def test_income_tax_entity_num_deep_scan(self):
        """Verifies that ITD payloads with entityNum (and pan='', client_id=261) extract PAN and resolve accurately."""
        # Client 261 exists in database for a DIFFERENT PAN (e.g. ZZZZZ9999Z)
        pan_col = next(c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN")
        name_col = next(c for c in self.db.get_mcl_columns() if "COMPANY" in c["label"].upper() or "NAME" in c["label"].upper())
        cid_stale = self.db.add_client(values={name_col["id"]: "Stale Client", pan_col["id"]: "ZZZZZ9999Z"}, notes="", service_ids=[])

        raw_itd_payload = {
            "ackDt": 1787496648000,
            "ackNum": "677475180230826",
            "assmentYear": 2026,
            "condonationDueDt": "2027-05-31",
            "efileStatus": "998",
            "entityNum": "AHJPR0846B",
            "filingTypeCd": "O",
            "formTypeCd": "4",
            "incmTaxSecCd": 11,
            "itrDueDt": "2026-12-31",
            "noOfDelay": 0,
            "submitTmstmp": 1787496648147,
            "taxYear": None
        }

        # Extracted candidates must contain AHJPR0846B
        cands = self.db._extract_identity_candidates_from_payload(arn_number="677475180230826", pan="", raw_payload_json=json.dumps(raw_itd_payload))
        self.assertIn("AHJPR0846B", cands)

        # Incoming capture from extension with client_id=cid_stale and pan=""
        res = self.db.insert_tracker_dump(
            client_id=cid_stale,
            portal="Income Tax Portal",
            arn_number="677475180230826",
            pan="",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps(raw_itd_payload)
        )

        # Must not be falsely attributed to cid_stale! Must be unassigned with AHJPR0846B
        self.assertIsNone(res["client_id"])
        self.assertEqual(res["unassigned_identity"], "AHJPR0846B")

        dumps = self.db.get_tracker_dumps()
        latest = dumps[0]
        self.assertTrue(latest["is_unassigned"])
        self.assertIn("Unregistered (PAN: AHJPR0846B)", latest["client_name"])


if __name__ == "__main__":
    unittest.main()
