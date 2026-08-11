"""
test_updater.py
---------------
Unit tests for version comparison and update metadata.
"""

import unittest
import json
from pathlib import Path
import version


class TestUpdater(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(version.parse_version("2.3.0"), (2, 3, 0))
        self.assertEqual(version.parse_version("v2.3.1"), (2, 3, 1))
        self.assertEqual(version.parse_version("3.0.0.1"), (3, 0, 0, 1))

    def test_is_update_available(self):
        self.assertTrue(version.is_update_available("2.3.1", "2.3.0"))
        self.assertTrue(version.is_update_available("3.0.0", "2.3.0"))
        self.assertFalse(version.is_update_available("2.3.0", "2.3.0"))
        self.assertFalse(version.is_update_available("2.2.9", "2.3.0"))

    def test_version_json_format(self):
        v_file = Path(__file__).resolve().parent.parent / "version.json"
        self.assertTrue(v_file.exists(), "version.json must exist in root")
        
        with open(v_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        self.assertIn("version", data)
        self.assertIn("min_required_version", data)
        self.assertIn("mandatory", data)
        self.assertIn("download_url", data)


if __name__ == "__main__":
    unittest.main()
