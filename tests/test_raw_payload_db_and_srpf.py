"""
test_raw_payload_db_and_srpf.py
--------------------------------
Unit tests verifying:
1. Dedicated rawPayload.db database creation, encryption, and automatic migration from master.db.
2. SRPF Stage 1: Grouping multiple captures for the same identity into unified client containers.
3. SRPF Stage 2: Profile parser extraction & mapping against Master Column List (MCL) definitions.
"""

import os
import json
import tempfile
import unittest
from sqlcipher3 import dbapi2 as sqlite3
from database import SeraDatabase
import security
from ui.utils.profile_parser import extract_profile_from_payload, map_profile_to_mcl_columns


class TestRawPayloadDbAndSRPF(unittest.TestCase):
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

    def test_raw_payload_db_isolation(self):
        """Verifies that tracker_dump table is housed in rawPayload.db and absent in master.db."""
        self.assertTrue(os.path.exists(self.raw_db_path))

        # Check master.db does NOT have tracker_dump
        with self.db._connect() as m_conn:
            cur = m_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracker_dump'")
            self.assertIsNone(cur.fetchone())

        # Check rawPayload.db DOES have tracker_dump and client_raw_containers
        with self.db._connect_raw() as r_conn:
            cur = r_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracker_dump'")
            self.assertIsNotNone(cur.fetchone())
            cur = r_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='client_raw_containers'")
            self.assertIsNotNone(cur.fetchone())

    def test_legacy_data_migration_to_raw_db(self):
        """Verifies that historical tracker_dump rows in master.db migrate to rawPayload.db."""
        # Create a fresh database where master.db initially has tracker_dump table and rows
        sub_tmp = tempfile.TemporaryDirectory()
        legacy_m_path = os.path.join(sub_tmp.name, "legacy_master.db")
        legacy_r_path = os.path.join(sub_tmp.name, "rawPayload.db")

        # Manually create legacy master.db with tracker_dump table
        conn = sqlite3.connect(legacy_m_path)
        conn.execute(f"PRAGMA key = \"x'{self.hex_key}'\";")
        conn.execute("""
            CREATE TABLE tracker_dump (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                unassigned_identity TEXT,
                service_id INTEGER,
                portal TEXT,
                period_label TEXT,
                arn_number TEXT,
                capture_method TEXT DEFAULT 'DOM_Tracker',
                status TEXT DEFAULT 'submitted',
                raw_payload_json TEXT,
                captured_by TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO tracker_dump (id, client_id, unassigned_identity, portal, arn_number, raw_payload_json, created_at)
            VALUES (1, NULL, 'AMTPL4994M', 'Income Tax', 'PROFILE-AMTPL4994M', '{"pan":"AMTPL4994M"}', '2026-08-24T12:00:00')
        """)
        conn.commit()
        conn.close()

        # Initialize SeraDatabase on this legacy DB -> auto-migration should trigger
        migrated_db = SeraDatabase(legacy_m_path, self.hex_key, raw_db_path=legacy_r_path)

        # 1. Row should now exist in rawPayload.db
        dumps = migrated_db.get_tracker_dumps()
        self.assertEqual(len(dumps), 1)
        self.assertEqual(dumps[0]["unassigned_identity"], "AMTPL4994M")
        self.assertEqual(dumps[0]["arn_number"], "PROFILE-AMTPL4994M")

        # 2. tracker_dump table should be removed from master.db
        with migrated_db._connect() as m_conn:
            cur = m_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracker_dump'")
            self.assertIsNone(cur.fetchone())

        sub_tmp.cleanup()

    def test_srpf_stage1_container_aggregation(self):
        """
        Verifies SRPF Stage 1: Multiple raw captures for the same PAN (ITR Ack + Entity Profile)
        are grouped into a single unified client container in rawPayload.db.
        """
        pan_target = "AHJPR0846B"

        # Capture 1: ITR-4 filing ack
        self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax Portal",
            arn_number="677475180230826",
            period_label="AY 2026-27",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({
                "ackNum": "677475180230826",
                "entityNum": pan_target,
                "formTypeCd": "4",
                "assmentYear": 2026
            })
        )

        # Capture 2: Entity Profile capture with names, phone, email
        self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax Portal",
            arn_number=f"PROFILE-{pan_target}",
            period_label="Profile Info",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({
                "pan": pan_target,
                "firstName": "Ramesh",
                "lastName": "Kumar",
                "legalName": "Ramesh Enterprises",
                "priMobileNum": "9876543210",
                "priEmailId": "ramesh@example.com",
                "dob": "1985-04-15"
            })
        )

        # Retrieve unified SRPF container
        container = self.db.get_client_raw_container(identity_key=pan_target)
        self.assertIsNotNone(container)
        self.assertEqual(container["identity_key"], pan_target)
        self.assertEqual(container["pan"], pan_target)
        self.assertEqual(container["company_name"], "Ramesh Enterprises")
        self.assertEqual(container["proprietor_name"], "Ramesh Kumar")
        self.assertEqual(container["phone"], "9876543210")
        self.assertEqual(container["email"], "ramesh@example.com")
        self.assertEqual(container["dob"], "1985-04-15")
        self.assertEqual(container["total_captures"], 2)
        self.assertEqual(len(container["filing_history"]), 2)

    def test_srpf_stage2_profile_parser_and_mcl_mapping(self):
        """Verifies SRPF Stage 2: Profile parser extracts fields and maps accurately to MCL columns."""
        mcl_cols = self.db.get_mcl_columns()

        sample_itd_payload = {
            "entityNum": "CIQPM7599L",
            "firstName": "Simran",
            "lastName": "Kaur",
            "tradeName": "Simran Dresses",
            "priMobileNum": "9123456780",
            "priEmailId": "simrandresses16@gmail.com",
            "dob": "1990-11-20"
        }

        # 1. Extract Profile
        profile = extract_profile_from_payload(sample_itd_payload)
        self.assertEqual(profile["pan"], "CIQPM7599L")
        self.assertEqual(profile["company_name"], "Simran Dresses")
        self.assertEqual(profile["proprietor_name"], "Simran Kaur")
        self.assertEqual(profile["phone"], "9123456780")
        self.assertEqual(profile["email"], "simrandresses16@gmail.com")
        self.assertEqual(profile["dob"], "1990-11-20")

        # 2. Map against MCL columns
        mapped = map_profile_to_mcl_columns(profile, mcl_cols)

        # Verify mapping against known seeded columns in settings.ini
        pan_col = next(c for c in mcl_cols if c["label"].strip().upper() == "PAN")
        self.assertEqual(mapped.get(pan_col["id"]), "CIQPM7599L")

        comp_col = next((c for c in mcl_cols if "COMPANY" in c["label"].upper()), None)
        if comp_col:
            self.assertEqual(mapped.get(comp_col["id"]), "Simran Dresses")

        prop_col = next((c for c in mcl_cols if "PROPRIETOR" in c["label"].upper()), None)
        if prop_col:
            self.assertEqual(mapped.get(prop_col["id"]), "Simran Kaur")

        ph_col = next((c for c in mcl_cols if "PH" in c["label"].upper()), None)
        if ph_col:
            self.assertEqual(mapped.get(ph_col["id"]), "9123456780")

        email_col = next((c for c in mcl_cols if "EMAIL" in c["label"].upper() and "PASS" not in c["label"].upper()), None)
        if email_col:
            self.assertEqual(mapped.get(email_col["id"]), "simrandresses16@gmail.com")

        dob_col = next((c for c in mcl_cols if "DOB" in c["label"].upper()), None)
        if dob_col:
            self.assertEqual(mapped.get(dob_col["id"]), "1990-11-20")

    def test_srpf_does_not_map_bank_name_to_taxpayer_names(self):
        profile = extract_profile_from_payload({
            "entityNum": "ASDPM3313P",
            "bankName": "AXIS BANK",
            "accountHolderType": " ",
            "accountStatus": "Account Valid and Open",
            "ifscCd": "UTIB0000439",
            "status": "E",
        })

        self.assertEqual(profile["pan"], "ASDPM3313P")
        self.assertEqual(profile["company_name"], "")
        self.assertEqual(profile["proprietor_name"], "")

        profile_with_full_name = extract_profile_from_payload({
            "entityNum": "ASDPM3313P",
            "bankName": "AXIS BANK",
            "fullName": "MOHAMMAD MOLLA",
        })
        self.assertEqual(profile_with_full_name["proprietor_name"], "MOHAMMAD MOLLA")
        self.assertEqual(profile_with_full_name["company_name"], "")

    def test_get_srpf_containers_grouping_eliminates_duplicate_rows(self):
        """
        Verifies that when 10 different assessment year filings are captured for the same client (e.g. DHANAJ TIWARI),
        get_srpf_containers() returns EXACTLY 1 single aggregated row instead of 10 individual rows.
        """
        pan = "AXTPT8590N"
        # Simulate SAD intercepting 10 assessment year filings in 1 session
        years = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]
        for idx, yr in enumerate(years):
            self.db.insert_tracker_dump(
                client_id=None,
                portal="Income Tax Portal (ITR-4)",
                arn_number=f"10000000000{idx}",
                period_label=f"AY {yr}",
                capture_method="SAD_API_Interceptor",
                raw_payload_json=json.dumps({
                    "entityNum": pan,
                    "legalName": "DHANAJ TIWARI",
                    "formTypeCd": "4",
                    "period": f"AY {yr}"
                })
            )

        # In raw tracker dump, there are 9 rows
        raw_dumps = self.db.get_tracker_dumps()
        self.assertEqual(len(raw_dumps), 9)

        # In SRPF Containers view, there is EXACTLY 1 unified client row!
        containers = self.db.get_srpf_containers()
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["pan"], pan)
        self.assertEqual(containers[0]["company_name"], "DHANAJ TIWARI")
        self.assertEqual(containers[0]["total_captures"], 9)
        self.assertIn("9 Filings", containers[0]["period_summary"])

    def test_srpf_container_deletion(self):
        """Verifies deleting a container cleans up both the container and underlying captures in rawPayload.db."""
        pan = "TESTP1234F"
        self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax Portal",
            arn_number="1234567890",
            period_label="AY 2026-27",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"entityNum": pan, "legalName": "Test Corp"})
        )
        self.assertEqual(len(self.db.get_srpf_containers()), 1)

        self.db.delete_srpf_container(pan)
        self.assertEqual(len(self.db.get_srpf_containers()), 0)

    def test_proximity_resolution_prevents_false_client_attribution(self):
        """
        Verifies that when a wizard submission has empty PAN in its root payload,
        it uses session proximity on the same portal and does NOT falsely attribute
        to a stale client_id from a different portal.
        """
        mcl_cols = self.db.get_mcl_columns()
        pan_col = next(c for c in mcl_cols if "PAN" in c["label"].upper())
        gst_vals = {c["id"]: "TestVal" for c in mcl_cols if c.get("is_internal_pk")}
        gst_vals[pan_col["id"]] = "GACPS8194B"
        itr_vals = {c["id"]: "TestVal2" for c in mcl_cols if c.get("is_internal_pk")}
        itr_vals[pan_col["id"]] = "AXTPT8591P"

        # Client 1: GST client (e.g. Bishalakshi Enterprise)
        cid_gst = self.db.add_client(gst_vals, notes="Bishalakshi Enterprise", service_ids=[])
        # Client 2: Income Tax client (e.g. Anita Tewari)
        cid_itr = self.db.add_client(itr_vals, notes="Anita Tewari", service_ids=[])

        # Step 1: User visits ITR portal, SAD captures profile for AXTPT8591P
        self.db.insert_tracker_dump(
            client_id=None,
            portal="Income Tax Portal",
            arn_number="PROFILE-AXTPT8591P",
            period_label="Profile Info",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"entityNum": "AXTPT8591P", "firstName": "Anita", "lastName": "Tewari"})
        )

        # Step 2: User files ITR return (wizard submit has no PAN, extension sends stale client_id: cid_gst)
        self.db.insert_tracker_dump(
            client_id=cid_gst, # Stale extension client_id from earlier GST browsing
            portal="Income Tax Portal",
            arn_number="ITR000883707378",
            period_label="",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"arnNumber": "680429730230826", "httpStatus": "ACCEPTED", "transactionNo": "ITR000883707378"})
        )

        # Step 3: Run re-resolution
        self.db.re_resolve_all_tracker_dumps()

        # Step 4: Verify Bishalakshi Enterprise (cid_gst) has ZERO ITR filings!
        gst_container = self.db.get_client_raw_container(client_id=cid_gst)
        if gst_container:
            self.assertEqual(len(gst_container.get("filing_history", [])), 0)

        # Step 5: Verify Anita Tewari (cid_itr) correctly inherited the ITR submission!
        itr_container = self.db.get_client_raw_container(client_id=cid_itr)
        self.assertIsNotNone(itr_container)
        self.assertEqual(len(itr_container["filing_history"]), 2)
        arns = [f["arn"] for f in itr_container["filing_history"]]
        self.assertIn("ITR000883707378", arns)

    def test_srpf_submission_status_and_chronological_ordering(self):
        """Verifies that:
        1. Latest filing status (e.g. pending e-verification) is accurately captured in containers.
        2. UI status resolver accurately reflects 'Submitted (e-verification pending)'.
        3. Containers and raw tracker dumps are returned with latest entries at the top."""
        from ui.windows.tracker_dump_window import _resolve_ltt_submission_status

        # Register client in master.db
        mcl_cols = self.db.get_mcl_columns()
        pan_col = next(c for c in mcl_cols if "PAN" in c["label"].upper())
        c_vals = {c["id"]: "TestVal" for c in mcl_cols if c.get("is_internal_pk")}
        c_vals[pan_col["id"]] = "AEYPH5467G"
        cid = self.db.add_client(c_vals, notes="Azad Hossain", service_ids=[])

        # Client: First filed return (AY 2025-26)
        self.db.insert_tracker_dump(
            client_id=cid,
            portal="Income Tax Portal",
            arn_number="827916720300726",
            period_label="AY 2025-26",
            status="Filed & Verified (Processed)",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"pan": "AEYPH5467G", "status": "Filed & Verified (Processed)"})
        )

        # Client: Later submission with pending e-verification (AY 2026-27)
        self.db.insert_tracker_dump(
            client_id=cid,
            portal="Income Tax Portal",
            arn_number="132255440300826",
            period_label="AY 2026-27",
            status="Submitted (Pending e-Verification)",
            capture_method="SAD_API_Interceptor",
            raw_payload_json=json.dumps({"pan": "AEYPH5467G", "status": "Submitted (Pending e-Verification)"})
        )

        # Run re-resolution to rebuild containers
        self.db.re_resolve_all_tracker_dumps()

        containers = self.db.get_srpf_containers()
        matched = [c for c in containers if c.get("client_id") == cid or "AEYPH5467G" in c.get("identity_key", "")]
        self.assertTrue(len(matched) > 0)
        c = matched[0]

        # Latest status must be pending e-verification
        self.assertEqual(c.get("status"), "Submitted (Pending e-Verification)")
        self.assertEqual(c.get("latest_arn"), "132255440300826")

        # Resolved LTT status must match sdc_parser: 'Submitted (e-verification pending)'
        status_text, status_color = _resolve_ltt_submission_status(c)
        self.assertEqual(status_text, "Submitted (e-verification pending)")
        self.assertEqual(status_color, "#F1E05A")

        # Test chronological ordering in raw dumps: latest capture (ARN 132255440300826) must be above earlier
        raw_dumps = self.db.get_tracker_dumps(client_id=cid)
        self.assertEqual(len(raw_dumps), 2)
        self.assertEqual(raw_dumps[0]["arn_number"], "132255440300826")
        self.assertEqual(raw_dumps[1]["arn_number"], "827916720300726")


if __name__ == "__main__":
    unittest.main()

