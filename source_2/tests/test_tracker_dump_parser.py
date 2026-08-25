"""
Unit Tests for the 8-Stage Tracker Dump Parser & Action Decoder Pipeline
Tests all stages (A through H) against mock payload dump fixtures and the full mock corpus.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tracker_dump_parser.entry_splitter import split_entries
from tracker_dump_parser.header_parser import parse_header, parse_client_id_field
from tracker_dump_parser.json_parser import parse_json_body
from tracker_dump_parser.identity_resolver import resolve_identity, extract_pan_from_payload
from tracker_dump_parser.action_decoder import decode_action
from tracker_dump_parser.session_stitcher import stitch_sessions
from tracker_dump_parser.timeline_assembler import assemble_client_timelines, detect_data_quality_flags
from tracker_dump_parser import parse_dump_to_timelines

MOCK_DUMP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "seraRawPayloadDump_mock.txt"))


class TestTrackerDumpParserPipeline(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(MOCK_DUMP_PATH), f"Mock dump file not found at: {MOCK_DUMP_PATH}")
        with open(MOCK_DUMP_PATH, "r", encoding="utf-8") as f:
            self.raw_dump_text = f.read()

    def test_stage_a_entry_splitter(self):
        chunks = split_entries(self.raw_dump_text)
        self.assertGreaterEqual(len(chunks), 150, "Should split into at least 150 entry blocks")
        for chunk in chunks:
            self.assertIn("CAPTURE DUMP ENTRY #", chunk)

    def test_stage_b_header_parser(self):
        sample_chunk = (
            "CAPTURE DUMP ENTRY #11\n"
            "Timestamp       : 2026-08-23T14:43:40.343841+00:00\n"
            "Portal          : Income Tax Portal\n"
            "Capture Method  : SAD_API_Interceptor\n"
            "Status          : submitted\n"
            "ARN / Ack No    : 662914450160925\n"
            "Period Label    : \n"
            "Client ID       : 494 (//MOKPA1000A)\n"
            "Captured By     : Outside_PC\n"
            "----------------------------------------------------------------------------------------\n"
            "RAW JSON PAYLOAD:\n"
            '{"client_id": 214, "url": "https://eportal.incometax.gov.in/iec/servicesapi/auth/getEntity"}'
        )
        header = parse_header(sample_chunk)
        self.assertEqual(header["entry_num"], 11)
        self.assertEqual(header["portal"], "Income Tax Portal")
        self.assertEqual(header["header_client_id"], 494)
        self.assertEqual(header["header_pan"], "MOKPA1000A")
        self.assertEqual(header["arn_ack_no"], "662914450160925")

        # Test dual shapes of Client ID
        cid, pan = parse_client_id_field("null (//MOKPA1000A)")
        self.assertIsNone(cid)
        self.assertEqual(pan, "MOKPA1000A")

        cid, pan = parse_client_id_field("214")
        self.assertEqual(cid, 214)
        self.assertIsNone(pan)

    def test_stage_c_json_parser_and_quarantine(self):
        valid_chunk = (
            "RAW JSON PAYLOAD:\n"
            '{"status": "SUCCESS", "client_id": 100, "pan": "MOKPA1000A"}'
        )
        data, err = parse_json_body(valid_chunk)
        self.assertIsNone(err)
        self.assertIsNotNone(data)
        self.assertEqual(data.get("status"), "SUCCESS")

        # Malformed chunk
        bad_chunk = "RAW JSON PAYLOAD:\n{status: broken json without quotes..."
        bad_data, bad_err = parse_json_body(bad_chunk)
        self.assertIsNone(bad_data)
        self.assertIsNotNone(bad_err)

    def test_stage_d_identity_resolution_and_mismatch(self):
        header_fields = {
            "header_client_id": 494,
            "header_pan": "MOKPA1000A"
        }
        json_body = {
            "client_id": 214,
            "pan": "MOKPA1000A"
        }
        res = resolve_identity(header_fields, json_body)
        # Body client_id has priority
        self.assertEqual(res["resolved_client_id"], 214)
        self.assertEqual(res["identity_confidence"], "exact_id")
        self.assertEqual(res["resolved_pan"], "MOKPA1000A")
        # Should flag mismatch between Header 494 and Body 214
        self.assertTrue(any("identity_mismatch" in f for f in res["identity_flags"]))

    def test_stage_e_action_decoder_and_outcomes(self):
        # 1. Login endpoint
        act = decode_action("https://eportal.incometax.gov.in/iec/loginapi/login", {"status": "SUCCESS"})
        self.assertEqual(act["portal_code"], "IT")
        self.assertEqual(act["category"], "Authentication")
        self.assertEqual(act["outcome"], "success")

        # 2. Bank validation outcome
        bank_payload = {
            "raw_payload": {
                "bankName": "AXIS BANK",
                "status": "A",
                "accValidity": "V"
            }
        }
        act_bank = decode_action("https://eportal.incometax.gov.in/iec/servicesapi/auth/getEntity", bank_payload)
        self.assertEqual(act_bank["outcome"], "success")
        self.assertIn("AXIS BANK", act_bank["action"])

        # 3. GST Summary
        gst_payload = {
            "raw_payload": {
                "sec_nm": "HSN",
                "cur_gt": 263188
            }
        }
        act_gst = decode_action("https://services.gst.gov.in/returns/auth/api/gstr1/summary", gst_payload)
        self.assertEqual(act_gst["portal_code"], "GST")
        self.assertIn("HSN", act_gst["action"])

    def test_full_pipeline_against_mock_corpus(self):
        results = parse_dump_to_timelines(MOCK_DUMP_PATH)
        
        self.assertGreaterEqual(results["total_entries"], 150)
        self.assertGreater(results["valid_events_count"], 140)
        self.assertEqual(results["quarantine_count"], 0, "All mock dump entries should parse cleanly without quarantine")
        self.assertGreater(len(results["clients"]), 5, "Should organize events across multiple clients")

        print(f"\n[+] Pipeline Test Results Summary:")
        print(f"    - Total Entries Parsed  : {results['total_entries']}")
        print(f"    - Valid Events Decoded  : {results['valid_events_count']}")
        print(f"    - Quarantined Records   : {results['quarantine_count']}")
        print(f"    - Unique Client Entities: {len(results['clients'])}")
        print(f"    - Data Quality Flags    : {len(results['global_flags'])}")
        for f in results["global_flags"][:5]:
            print(f"      * Flag: {f}")


if __name__ == "__main__":
    unittest.main()
