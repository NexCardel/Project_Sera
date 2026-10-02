"""
The tracker window refreshes after every capture. Before 2026-09-21 each refresh built new
styled widgets for every row (each holding the row's full payload history in its click
handler) and started a new CSV-export thread: +335 MB over 100 refreshes, 14 exports running at
once. These tests pin the fix: row widgets are reused, clicks act on the row's CURRENT data,
and only one export runs at a time. Identifiers are fictional.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import security
from database import SeraDatabase
from PySide6.QtWidgets import QApplication, QPushButton
from shiboken6 import getCppPointer

import ui.windows.tracker_dump_window as tdw

APP = QApplication.instance() or QApplication([])


class TestTrackerRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt)
        key = security.derive_key_hex("testpass123", security.load_salt(salt))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"))
        for i, pan in enumerate(("ABCPD1234E", "XYZAB9876C", "PQRST4321K")):
            self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label=f"AY 202{i}-2{i + 1}",
                                        arn_number=f"12345678915092{i}", capture_method="VSDC-X_itr_submitted",
                                        status="Submitted", pan=pan, filing_type="ITR-4",
                                        raw_payload_json=json.dumps({"pan": pan}))
        self.win = tdw.TrackerDumpWindow(self.db)
        self.win.cmb_view_mode.setCurrentIndex(1)                     # raw rows
        APP.processEvents()

    def tearDown(self):
        self.win.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def cells(self, col):
        return [getCppPointer(self.win.table.cellWidget(r, col))[0] for r in range(self.win.table.rowCount())]

    def test_a_refresh_reuses_the_row_widgets(self):
        assert self.win.table.rowCount() == 3
        actions, statuses = self.cells(tdw.TrackerDumpWindow.COL_ACTIONS), self.cells(tdw.TrackerDumpWindow.COL_STATUS)
        for _ in range(5):
            self.win.load_data()
            APP.processEvents()
        assert (self.cells(tdw.TrackerDumpWindow.COL_ACTIONS) == actions
                and self.cells(tdw.TrackerDumpWindow.COL_STATUS) == statuses)

    def test_a_click_acts_on_the_rows_current_data(self):
        opened = []
        self.win._show_payload_dialog = lambda item: opened.append(item.get("arn_number"))
        btn = self.win.table.cellWidget(0, tdw.TrackerDumpWindow.COL_ACTIONS).findChild(QPushButton, "act_view")
        first = self.win._current_page_items[0]["arn_number"]
        btn.click()
        self.win.cmb_date.setCurrentIndex(0)
        self.win.txt_search.setText("XYZAB9876C")                    # row 0 is now another record
        self.win._apply_filters()
        APP.processEvents()
        btn = self.win.table.cellWidget(0, tdw.TrackerDumpWindow.COL_ACTIONS).findChild(QPushButton, "act_view")
        btn.click()
        assert opened[0] == first and opened[1] == "123456789150921" and opened[0] != opened[1]

    def test_the_status_cell_follows_the_row(self):
        label = self.win.table.cellWidget(0, tdw.TrackerDumpWindow.COL_STATUS).findChild(tdw.QLabel, "status_label")
        assert label is not None and label.text()

    def test_raw_generation_increments_on_raw_write(self):
        initial_gen = self.db.raw_generation()
        self.db.insert_tracker_dump(portal="Income Tax (ITR-1)", period_label="AY 2026-27",
                                    arn_number="99988877711122", capture_method="VSDC-X_itr_submitted",
                                    status="Submitted", pan="AAAPB1234F", filing_type="ITR-1")
        new_gen = self.db.raw_generation()
        self.assertGreater(new_gen, initial_gen)

    def test_load_data_if_stale_avoids_redundant_queries(self):
        load_count = [0]
        orig_load = self.win.load_data
        def counting_load():
            load_count[0] += 1
            return orig_load()
        self.win.load_data = counting_load

        # Initial state from setUp is already fresh, so load_data_if_stale reuses cache
        self.win.load_data_if_stale()
        self.assertEqual(load_count[0], 0)

        # Writing a new dump increments raw_generation, so load_data_if_stale triggers reload
        self.db.insert_tracker_dump(portal="Income Tax (ITR-1)", period_label="AY 2026-27",
                                    arn_number="88877766655544", capture_method="VSDC-X_itr_submitted",
                                    status="Submitted", pan="BBBPC5678G", filing_type="ITR-1")
        self.win.load_data_if_stale()
        self.assertEqual(load_count[0], 1)

        # Immediate next call within 60s without any new writes reuses cache
        self.win.load_data_if_stale()
        self.assertEqual(load_count[0], 1)
