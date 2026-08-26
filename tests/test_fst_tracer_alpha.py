import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import FST_Tracer_Alpha.tracer as tracer
from FST_Tracer_Alpha.tracer import parse_dump, process_dump


MOCK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "seraRawPayloadDump_mock.txt"))


class TestFSTTracerAlpha(unittest.TestCase):
    def test_mock_corpus_is_loss_aware_and_grouped_by_pan(self):
        result = process_dump(MOCK)
        self.assertEqual(result["stats"]["input_entries"], 157)
        self.assertGreaterEqual(result["stats"]["client_containers"], 20)
        self.assertGreater(result["stats"]["sessions"], 20)
        self.assertTrue(any(k == "PAN:MOKPA1003D" for k in result["clients"]))
        self.assertTrue(any(e["action"] == "GST return filing" for e in result["entries"]))
        self.assertTrue(any(e["identity_method"] == "ack-link" for e in result["entries"]))

    def test_client_id_is_not_used_as_container_key(self):
        result = process_dump(MOCK)
        self.assertTrue(all(not key.startswith("CLIENT:") for key in result["clients"]))
        self.assertTrue(all("client_id" not in key.lower() for key in result["clients"]))

    def test_malformed_entry_is_quarantinable(self):
        raw = """CAPTURE DUMP ENTRY #1\nTimestamp       : 2026-01-01T00:00:00+00:00\nPortal          : Income Tax Portal\nRAW JSON PAYLOAD:\n{broken\n"""
        result = process_dump(raw)
        self.assertEqual(result["stats"]["input_entries"], 1)
        self.assertEqual(result["stats"]["quarantine_entries"], 1)

    def test_obsidian_vault_contains_dashboard_clients_and_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "Sera FST Tracer Alpha")
            result = process_dump(MOCK, output_vault=vault)
            self.assertTrue(os.path.exists(os.path.join(vault, "Sera FST Tracer Alpha.md")))
            self.assertTrue(os.path.exists(os.path.join(vault, "Session Index.md")))
            self.assertTrue(os.path.exists(os.path.join(vault, "Clients", "PAN_MOKPA1000A.md")))
            self.assertGreater(len(os.listdir(os.path.join(vault, "Sessions"))), 20)
            with open(os.path.join(vault, "Sera FST Tracer Alpha.md"), encoding="utf-8") as handle:
                self.assertIn("MOKPA1000A", handle.read())

    def test_process_result_exposes_generated_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "report.xlsx")
            vault = os.path.join(tmp, "vault")
            result = process_dump(MOCK, output_excel=report, output_vault=vault)
            self.assertEqual(result["outputs"]["excel_path"], report)
            self.assertTrue(os.path.exists(report))
            self.assertEqual(result["outputs"]["obsidian_vault"], vault)

    def test_locked_canonical_report_uses_latest_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "report.xlsx")
            original_replace = tracer.os.replace
            calls = {"count": 0}

            def replace_with_first_lock(source, destination):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise PermissionError("simulated Excel lock")
                return original_replace(source, destination)

            with patch.object(tracer.os, "replace", side_effect=replace_with_first_lock):
                result = process_dump(MOCK, output_excel=report, output_vault=os.path.join(tmp, "vault"))

            self.assertTrue(result["outputs"]["excel_path"].endswith("report_latest.xlsx"))
            self.assertTrue(os.path.exists(result["outputs"]["excel_path"]))


if __name__ == "__main__":
    unittest.main()
