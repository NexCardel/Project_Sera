import os
import tempfile
import unittest
import security
from core.vsdc.vsdc_regex import extract_filing_type
from core.vsdc.vsdc_assembler import VisualSessionAssembler
from database import SeraDatabase

class TestVSDCDraftSupersession(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, 'test_master.db')
        self.raw_db_path = os.path.join(self.tmp_dir.name, 'rawPayload.db')
        self.salt_path = os.path.join(self.tmp_dir.name, 'test_sera.salt')
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.hex_key = security.derive_key_hex('testpass123', salt)
        self.db = SeraDatabase(self.db_path, self.hex_key, raw_db_path=self.raw_db_path)


    def tearDown(self):
        self.tmp_dir.cleanup()


    def test_open_dropdown_suppression(self):
        closed_menu_text = 'Select ITRForm\nITR-1\nContinue'
        self.assertEqual(extract_filing_type(closed_menu_text), 'ITR-1')
        open_menu_text = 'Select ITRForm\nITR-1\nITR-2\nITR-3\nITR-4\nCancel'
        self.assertIsNone(extract_filing_type(open_menu_text))
        statutory_text = 'Filing Form 10-IEA for AY 2026-27'
        self.assertEqual(extract_filing_type(statutory_text), 'Form 10-IEA')

    def test_assembler_form_switching_supersession(self):
        assembler = VisualSessionAssembler()
        assembler.update_identity(pan='GZEPM6367M', name='WASIL AMAN MANDAL', portal='Income Tax')
        assembler.update_selection(filing_type='ITR-2', period_label='AY 2026-27')
        # Without submit status from portal, dataset is incomplete (returns None)
        self.assertIsNone(assembler.get_completed_dataset_payload())

        # Taxpayer switches form to ITR-1
        assembler.update_selection(filing_type='ITR-1', period_label='AY 2026-27')
        self.assertIsNone(assembler.get_completed_dataset_payload())

        # Submission status captured from portal -> emits completed dataset
        assembler.record_submission(
            ack_number='982348123456789',
            status='Submitted (Not e-Verified)',
            crosshair_id='itr_submitted_pending'
        )
        p = assembler.get_completed_dataset_payload()
        self.assertIsNotNone(p)
        self.assertEqual(p['filing_type'], 'ITR-1')
        self.assertEqual(p['status'], 'Submitted (Not e-Verified)')
        self.assertEqual(p['arn'], '982348123456789')

    def test_database_unsubmitted_draft_supersession(self):
        pan = 'GZEPM6367M'
        period = 'AY 2026-27'
        self.db.insert_tracker_dump(portal='Income Tax', filing_type='ITR-2', period_label=period, pan=pan, status='Not Submitted', capture_method='VSDC_itr_form_select')
        with self.db._connect_raw() as conn:
            rows = conn.execute('SELECT id, dataset_key, status FROM tracker_dump WHERE period_label = ? AND unassigned_identity = ?', (period, pan)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0][1].endswith(':ITR2:AY_2026_27'))
        self.db.insert_tracker_dump(portal='Income Tax', filing_type='ITR-1', period_label=period, pan=pan, status='Not Submitted', capture_method='VSDC_itr_form_select')
        with self.db._connect_raw() as conn:
            rows = conn.execute('SELECT id, dataset_key, status FROM tracker_dump WHERE period_label = ? AND unassigned_identity = ?', (period, pan)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0][1].endswith(':ITR1:AY_2026_27'))
        self.db.insert_tracker_dump(portal='Income Tax', filing_type='ITR-1', period_label=period, pan=pan, arn_number='123456789012345', status='Submitted (Not e-Verified)', capture_method='VSDC_itr_submitted_pending')
        with self.db._connect_raw() as conn:
            rows = conn.execute('SELECT id, dataset_key, status FROM tracker_dump WHERE period_label = ? AND unassigned_identity = ?', (period, pan)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2], 'Submitted (Not e-Verified)')
        self.db.insert_tracker_dump(portal='Income Tax', filing_type='ITR-4', period_label=period, pan=pan, status='Not Submitted', capture_method='VSDC_itr_form_select')
        with self.db._connect_raw() as conn:
            rows = conn.execute('SELECT id, dataset_key, status FROM tracker_dump WHERE period_label = ? AND unassigned_identity = ?', (period, pan)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2], 'Submitted (Not e-Verified)')

    def test_database_deduplicate_tracker_dumps(self):
        pan = 'AAACB1234D'
        period = 'AY 2026-27'
        with self.db._connect_raw() as conn:
            conn.execute('INSERT INTO tracker_dump (portal, period_label, unassigned_identity, status, dataset_key, created_at) VALUES (?, ?, ?, ?, ?, ?)', ('Income Tax', period, pan, 'Not Submitted', f'ITD:{pan}:ITR-2:AY-2026-27', '2026-09-12 10:00:00'))
            conn.execute('INSERT INTO tracker_dump (portal, period_label, unassigned_identity, status, dataset_key, created_at) VALUES (?, ?, ?, ?, ?, ?)', ('Income Tax', period, pan, 'Not Submitted', f'ITD:{pan}:ITR-1:AY-2026-27', '2026-09-12 10:01:00'))
            conn.execute('INSERT INTO tracker_dump (portal, period_label, unassigned_identity, status, dataset_key, created_at) VALUES (?, ?, ?, ?, ?, ?)', ('Income Tax', period, pan, 'Not Submitted', f'ITD:{pan}:ITR-4:AY-2026-27', '2026-09-12 10:02:00'))
        purged = self.db.deduplicate_tracker_dumps()
        self.assertGreaterEqual(purged, 2)
        with self.db._connect_raw() as conn:
            rows = conn.execute('SELECT id, dataset_key, status FROM tracker_dump WHERE period_label = ? AND unassigned_identity = ?', (period, pan)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0][1].endswith(':ITR-4:AY-2026-27'))

if __name__ == '__main__':
    unittest.main()
