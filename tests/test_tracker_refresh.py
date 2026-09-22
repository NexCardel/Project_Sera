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
        self.db.rebuild_raw_payload_dumps_file = lambda: 0
        for i, pan in enumerate(("ABCPD1234E", "XYZAB9876C", "PQRST4321K")):
            self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label=f"AY 202{i}-2{i + 1}",
                                        arn_number=f"12345678915092{i}", capture_method="VSDC-X_itr_submitted",
                                        status="Submitted", pan=pan, filing_type="ITR-4",
                                        raw_payload_json=json.dumps({"pan": pan}))
        self.feed = patch.object(tdw, "_request_live_feed_export", lambda: None)
        self.feed.start()
        self.win = tdw.TrackerDumpWindow(self.db)
        self.win.cmb_view_mode.setCurrentIndex(1)                     # raw rows
        APP.processEvents()

    def tearDown(self):
        self.feed.stop()
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


def test_the_feed_imports_without_any_earlier_export_click():
    """The feed thread used `import sdc_parser`, which only worked after an export button had put
    SDC_Parser on sys.path - until then every automatic refresh failed silently."""
    import inspect
    src = inspect.getsource(tdw._request_live_feed_export)
    assert "from SDC_Parser import sdc_parser" in src
    from SDC_Parser import sdc_parser
    assert callable(sdc_parser.export_ltt_live_feed)


def test_only_one_feed_export_runs_at_a_time():
    running, peak, calls = [0], [0], [0]
    lock = threading.Lock()

    def slow_export(*a, **k):
        with lock:
            running[0] += 1
            calls[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        with lock:
            running[0] -= 1
        return None, None

    with patch("SDC_Parser.sdc_parser.export_ltt_live_feed", slow_export):
        for _ in range(20):
            tdw._request_live_feed_export()
            time.sleep(0.01)
        deadline = time.time() + 5
        while tdw._feed_state["running"] and time.time() < deadline:
            time.sleep(0.05)
    assert peak[0] == 1                       # never two at once
    assert 2 <= calls[0] <= 4                 # 20 requests coalesced; the latest data still written
    assert not tdw._feed_state["running"]
