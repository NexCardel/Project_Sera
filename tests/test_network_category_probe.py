import time
import unittest

from sync_network_probe import (
    NetworkCategoryMonitor,
    probe_network_category,
    _query_categories_powershell,
)


class TestProbeNetworkCategory(unittest.TestCase):
    def test_public_category_from_comtypes(self):
        import sync_network_probe as mod
        orig = mod._query_categories_comtypes
        mod._query_categories_comtypes = lambda: ["private", "public"]
        try:
            result = probe_network_category()
        finally:
            mod._query_categories_comtypes = orig
        self.assertTrue(result["is_public"])
        self.assertEqual(result["method"], "comtypes")
        self.assertIsNone(result["error"])
        self.assertEqual(result["categories"], ["private", "public"])

    def test_private_only_is_not_public(self):
        import sync_network_probe as mod
        orig = mod._query_categories_comtypes
        mod._query_categories_comtypes = lambda: ["private", "domain"]
        try:
            result = probe_network_category()
        finally:
            mod._query_categories_comtypes = orig
        self.assertFalse(result["is_public"])
        self.assertEqual(result["method"], "comtypes")

    def test_falls_back_to_powershell_when_comtypes_fails(self):
        import sync_network_probe as mod
        orig_com = mod._query_categories_comtypes
        orig_ps = mod._query_categories_powershell

        def _boom():
            raise OSError("comtypes unavailable")

        mod._query_categories_comtypes = _boom
        mod._query_categories_powershell = lambda: ["public"]
        try:
            result = probe_network_category()
        finally:
            mod._query_categories_comtypes = orig_com
            mod._query_categories_powershell = orig_ps
        self.assertTrue(result["is_public"])
        self.assertEqual(result["method"], "powershell")

    def test_both_probes_fail_is_not_public_but_reports_error(self):
        import sync_network_probe as mod
        orig_com = mod._query_categories_comtypes
        orig_ps = mod._query_categories_powershell

        def _boom_com():
            raise OSError("no COM")

        def _boom_ps():
            raise RuntimeError("no powershell")

        mod._query_categories_comtypes = _boom_com
        mod._query_categories_powershell = _boom_ps
        try:
            result = probe_network_category()
        finally:
            mod._query_categories_comtypes = orig_com
            mod._query_categories_powershell = orig_ps
        self.assertFalse(result["is_public"])
        self.assertEqual(result["method"], "unknown")
        self.assertIn("no COM", result["error"])
        self.assertIn("no powershell", result["error"])

    def test_powershell_json_parsing_single_object(self):
        import sync_network_probe as mod
        import subprocess

        class FakeProc:
            returncode = 0
            stdout = '{"NetworkCategory": "Public"}'
            stderr = ""

        orig_run = subprocess.run
        subprocess.run = lambda *a, **k: FakeProc()
        try:
            categories = _query_categories_powershell()
        finally:
            subprocess.run = orig_run
        self.assertEqual(categories, ["public"])

    def test_powershell_json_parsing_list(self):
        import subprocess

        class FakeProc:
            returncode = 0
            stdout = '[{"NetworkCategory": "Private"}, {"NetworkCategory": "Public"}]'
            stderr = ""

        orig_run = subprocess.run
        subprocess.run = lambda *a, **k: FakeProc()
        try:
            categories = _query_categories_powershell()
        finally:
            subprocess.run = orig_run
        self.assertEqual(categories, ["private", "public"])

    def test_powershell_json_parsing_numeric_category(self):
        # Windows PowerShell 5.1's ConvertTo-Json renders NetworkCategory as
        # the bare enum number (0/1/2), not the string name - confirmed on a
        # real machine during P0-9b review. Must not come out "unknown".
        import subprocess

        class FakeProc:
            returncode = 0
            stdout = '{"NetworkCategory": 0}'
            stderr = ""

        orig_run = subprocess.run
        subprocess.run = lambda *a, **k: FakeProc()
        try:
            categories = _query_categories_powershell()
        finally:
            subprocess.run = orig_run
        self.assertEqual(categories, ["public"])

    def test_powershell_json_parsing_numeric_category_list(self):
        import subprocess

        class FakeProc:
            returncode = 0
            stdout = '[{"NetworkCategory": 1}, {"NetworkCategory": 0}]'
            stderr = ""

        orig_run = subprocess.run
        subprocess.run = lambda *a, **k: FakeProc()
        try:
            categories = _query_categories_powershell()
        finally:
            subprocess.run = orig_run
        self.assertEqual(categories, ["private", "public"])


class TestNetworkCategoryMonitor(unittest.TestCase):
    def test_poll_once_updates_last_result_and_notifies(self):
        changes = []
        monitor = NetworkCategoryMonitor(
            on_change=lambda r: changes.append(r),
            probe=lambda: {"is_public": True, "categories": ["public"], "method": "fake", "error": None},
        )
        result = monitor.poll_once()
        self.assertTrue(result["is_public"])
        self.assertEqual(monitor.last_result, result)
        self.assertEqual(len(changes), 1)

    def test_probe_exception_is_swallowed_and_marks_not_public(self):
        monitor = NetworkCategoryMonitor(probe=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        result = monitor.poll_once()
        self.assertFalse(result["is_public"])
        self.assertEqual(result["method"], "unknown")
        self.assertIn("boom", result["error"])

    def test_start_polls_immediately_then_stop_joins(self):
        calls = []
        monitor = NetworkCategoryMonitor(
            interval_seconds=9999,
            probe=lambda: (calls.append(1), {"is_public": False, "categories": [], "method": "fake", "error": None})[1],
        )
        monitor.start()
        try:
            deadline = time.time() + 2
            while not calls and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(calls, "poll should have run at start()")
        finally:
            monitor.stop()


if __name__ == "__main__":
    unittest.main()
