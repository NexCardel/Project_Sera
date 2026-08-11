"""
test_drs.py
-----------
Unit tests for DRS Phase 1: Database schema, CRUD methods, period calculations,
due date math, variant resolutions, and FPS JSON importing.
"""

import os
import sys
import tempfile
import unittest
import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import SeraDatabase
from drs import DRSEngine, import_fps_json
import security


class TestDRSPhase1(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_master.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        # Initialize test salt and derive key
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.key = security.derive_key_hex("testpass123", salt)
        
        # Initialize database
        self.db = SeraDatabase(self.db_path, self.key)
        
        # Seed test services
        existing = {s["name"]: s["id"] for s in self.db.get_services()}
        self.gst_svc_id = existing.get("GST") or self.db.create_service("GST", "https://gst.gov.in", None, None, "", "", "manual")
        self.tds_svc_id = existing.get("TDS") or self.db.create_service("TDS", "https://tds.gov.in", None, None, "", "", "manual")
        self.itr_svc_id = existing.get("Income Tax") or self.db.create_service("Income Tax", "https://incometax.gov.in", None, None, "", "", "manual")


    def test_database_crud(self):
        # 1. Upsert filing type
        ft_id = self.db.upsert_filing_type(
            service_id=self.gst_svc_id,
            code="GSTR1",
            name="GSTR-1",
            frequency="monthly",
            start_period="2026-04-01",
            due_day=11,
            grace_days=0,
            notes="GSTR1 Outward",
            variants=[{"tag": "QRMP", "frequency": "quarterly", "due_day": 13}]
        )
        self.assertIsNotNone(ft_id)
        
        fts = self.db.get_filing_types(service_id=self.gst_svc_id)
        self.assertEqual(len(fts), 1)
        self.assertEqual(fts[0]["code"], "GSTR1")
        self.assertEqual(len(fts[0]["variants"]), 1)
        self.assertEqual(fts[0]["variants"][0]["tag"], "QRMP")

        # 2. Attach client filing type
        client_id = self.db.add_client(values={}, notes="Test Client", service_ids=[self.gst_svc_id])
        self.db.attach_client_filing_type(client_id, ft_id, variant_tag="QRMP")
        
        cfts = self.db.get_client_filing_types(client_id)
        self.assertEqual(len(cfts), 1)
        self.assertEqual(cfts[0]["variant_tag"], "QRMP")

        # 3. Set & get filing status
        status_rec = self.db.set_filing_status(
            client_id=client_id,
            filing_type_id=ft_id,
            period_label="2026-07",
            status="submitted",
            updated_by="TestStaff",
            arn_number="AA123456789"
        )
        self.assertEqual(status_rec["status"], "submitted")
        
        fetched_status = self.db.get_filing_status(client_id, ft_id, "2026-07")
        self.assertIsNotNone(fetched_status)
        self.assertEqual(fetched_status["arn_number"], "AA123456789")

    def test_drs_engine_monthly_period(self):
        ft = {
            "frequency": "monthly",
            "due_day": 11,
            "grace_days": 0
        }
        # Ref date: 2026-08-07
        ref = datetime.date(2026, 8, 7)
        
        # Current period (offset=0): July 2026, due Aug 11
        info_curr = DRSEngine.get_period_info(ft, offset_periods=0, ref_date=ref)
        self.assertEqual(info_curr["period_label"], "July 2026")
        self.assertEqual(info_curr["due_date"], "2026-08-11")
        
        # Previous period (offset=-1): June 2026, due July 11
        info_prev = DRSEngine.get_period_info(ft, offset_periods=-1, ref_date=ref)
        self.assertEqual(info_prev["period_label"], "June 2026")
        self.assertEqual(info_prev["due_date"], "2026-07-11")

    def test_drs_engine_quarterly_period_and_variant(self):
        ft = {
            "frequency": "monthly",
            "due_day": 20,
            "variants": [{"tag": "QRMP", "frequency": "quarterly", "due_day": 22}]
        }
        # Ref date: 2026-08-07 (Q3)
        ref = datetime.date(2026, 8, 7)
        
        # With QRMP variant -> frequency=quarterly, due_day=22
        # Current quarter period (offset=0) is Q2 (Apr-Jun): Q2 (Apr-Jun) 2026, due July 22
        info_qrmp = DRSEngine.get_period_info(ft, variant_tag="QRMP", offset_periods=0, ref_date=ref)
        self.assertEqual(info_qrmp["period_label"], "Q2 (Apr-Jun) 2026")
        self.assertEqual(info_qrmp["due_date"], "2026-07-22")

    def test_drs_engine_annual_period(self):
        ft = {
            "frequency": "annual",
            "due_day_absolute": "07-31"
        }
        # Ref date: 2026-08-07
        ref = datetime.date(2026, 8, 7)
        
        # Current FY period (offset=0) is FY 2025-26, due 2026-07-31
        info_annual = DRSEngine.get_period_info(ft, offset_periods=0, ref_date=ref)
        self.assertEqual(info_annual["period_label"], "FY 2025-26")
        self.assertEqual(info_annual["due_date"], "2026-07-31")

    def test_evaluate_status(self):
        ref = datetime.date(2026, 8, 15)
        
        # Due on Aug 11 -> overdue on Aug 15 if not submitted
        st1 = DRSEngine.evaluate_status(None, due_date_str="2026-08-11", grace_days=0, ref_date=ref)
        self.assertEqual(st1, "overdue")
        
        # Due on Aug 20 -> pending on Aug 15
        st2 = DRSEngine.evaluate_status(None, due_date_str="2026-08-20", grace_days=0, ref_date=ref)
        self.assertEqual(st2, "pending")
        
        # DB status record = submitted -> submitted
        st3 = DRSEngine.evaluate_status({"status": "submitted"}, due_date_str="2026-08-11", grace_days=0, ref_date=ref)
        self.assertEqual(st3, "submitted")

    def test_import_fps_json(self):
        json_sample = """
        {
          "version": "1.0",
          "filing_types": [
            {
              "service": "GST",
              "code": "GSTR1",
              "name": "GSTR-1",
              "frequency": "monthly",
              "start_period": "2026-04-01",
              "due_day": 11
            },
            {
              "service": "GST",
              "code": "GSTR3B",
              "name": "GSTR-3B",
              "frequency": "monthly",
              "start_period": "2026-04-01",
              "due_day": 20
            }
          ]
        }
        """
        res = import_fps_json(self.db, json_sample, actor="AdminTest")
        self.assertEqual(res["imported"], 2)
        self.assertEqual(len(res["warnings"]), 0)

        fts = self.db.get_filing_types(service_id=self.gst_svc_id)
        self.assertEqual(len(fts), 2)

if __name__ == "__main__":
    unittest.main()
