"""
test_tracker_arn_kept.py
------------------------
A row saved under a dataset key that already holds an ARN must not drop the ARN when it arrives
without one at the SAME status: SGT reads the GST welcome page's tile ("Sep - 2026 Filed") after
the filing, and that tile shows no ARN (seen live: GSTR-1 Sep for 19ALRPM5009R1Z6 lost
AA190926453028S this way).
"""

import json
import os
import tempfile
import unittest

import security
from database import SeraDatabase

KEY = "GST:19ALRPM5009R1Z6:GSTR1:AY_2026_27_SEP"


class TestTrackerArnKept(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp_dir.name, "t.salt")
        security.generate_and_save_salt(salt_path)
        hex_key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp_dir.name, "t.db"), hex_key)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _save(self, arn, status="Submitted & Verified", **kw):
        return self.db.insert_tracker_dump(
            portal="GST Portal (GSTR-1)", period_label="September (FY 2026-27)", arn_number=arn,
            capture_method="SGT_live", status=status, dataset_key=KEY,
            raw_payload_json=json.dumps({"gstin": "19ALRPM5009R1Z6"}), **kw)

    def _rows(self):
        return [d for d in self.db.get_tracker_dumps() if d.get("dataset_key") == KEY]

    def test_equal_status_row_without_arn_keeps_the_arn(self):
        self._save("AA190926453028S")
        self._save("N/A")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arn_number"], "AA190926453028S")

    def test_a_new_arn_still_replaces_the_old_one(self):
        self._save("AA190926453028S")
        self._save("AA190926999999X")
        self.assertEqual([r["arn_number"] for r in self._rows()], ["AA190926999999X"])


if __name__ == "__main__":
    unittest.main()
