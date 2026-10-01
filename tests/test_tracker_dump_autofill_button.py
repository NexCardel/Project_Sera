"""
Unit and integration tests for the Tracker Dump 'Automation 1' (autofill) button,
service matching, action cell reuse, and ClientDetailWindow.run_service_action dispatcher.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox, QWidget

import automation
import ui.windows.tracker_dump_window as tdw
from ui.windows.tracker_dump_window import TrackerDumpWindow, _service_matches_portal
from ui.windows.client_detail_window import ClientDetailWindow

APP = QApplication.instance() or QApplication([])


class TestTrackerDumpAutofillButton(unittest.TestCase):

    def test_service_matches_portal(self):
        # Substring and keyword matches
        self.assertTrue(_service_matches_portal("Income Tax", "Income Tax Portal"))
        self.assertTrue(_service_matches_portal("Income Tax", "e-Filing Income Tax (ITR-1)"))
        self.assertTrue(_service_matches_portal("GST", "GST"))
        self.assertTrue(_service_matches_portal("GST Portal", "GST"))
        self.assertTrue(_service_matches_portal("TRACES", "traces tds"))

        # Empty / whitespace guards
        self.assertFalse(_service_matches_portal("", "Income Tax"))
        self.assertFalse(_service_matches_portal("GST", ""))
        self.assertFalse(_service_matches_portal(None, "GST"))
        self.assertFalse(_service_matches_portal("GST", None))

        # Unrelated names
        self.assertFalse(_service_matches_portal("GST", "Income Tax Portal"))
        self.assertFalse(_service_matches_portal("TRACES", "Income Tax Portal"))

    def test_service_for_row(self):
        mock_db = MagicMock()
        services = [
            {"id": 1, "name": "Income Tax", "automation_mode": "extension"},
            {"id": 2, "name": "Income Tax Portal (Detailed)", "automation_mode": "smti"},
            {"id": 3, "name": "GST", "automation_mode": "manual"},
        ]
        mock_db.get_client_services.return_value = services

        win = TrackerDumpWindow(mock_db, defer_first_load=True)

        # Matched client, exact portal match
        item_exact = {"client_id": 10, "portal": "Income Tax"}
        res = win._service_for_row(item_exact)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], 1)

        # Matched client, substring / fuzzy match
        item_fuzzy = {"client_id": 10, "portal": "GST Returns"}
        res_fuzzy = win._service_for_row(item_fuzzy)
        self.assertIsNotNone(res_fuzzy)
        self.assertEqual(res_fuzzy["id"], 3)

        # Unmatched / falsy client_id
        self.assertIsNone(win._service_for_row({"client_id": None, "portal": "Income Tax"}))
        self.assertIsNone(win._service_for_row({"client_id": 0, "portal": "Income Tax"}))

        # Empty portal name
        self.assertIsNone(win._service_for_row({"client_id": 10, "portal": ""}))

        # No services returned for client
        mock_db.get_client_services.return_value = []
        self.assertIsNone(win._service_for_row({"client_id": 10, "portal": "Income Tax"}))

        win.deleteLater()
        APP.processEvents()

    def test_build_action_cell_layout_and_order(self):
        mock_db = MagicMock()
        win = TrackerDumpWindow(mock_db, defer_first_load=True)
        item = {"client_id": 5, "portal": "Income Tax"}

        cell = win._build_action_cell(item, is_grouped=True)
        btn_autofill = cell.findChild(QPushButton, "act_autofill")
        btn_view = cell.findChild(QPushButton, "act_view")
        btn_more = cell.findChild(QPushButton, "act_more")
        btn_create = cell.findChild(QPushButton, "act_create")

        self.assertIsNotNone(btn_autofill)
        self.assertIsNotNone(btn_view)
        self.assertIsNotNone(btn_more)
        self.assertIsNotNone(btn_create)

        # Verify layout order: autofill before view
        layout = cell.layout()
        idx_autofill = layout.indexOf(btn_autofill)
        idx_view = layout.indexOf(btn_view)
        self.assertLess(idx_autofill, idx_view)

        win.deleteLater()
        APP.processEvents()

    def test_set_action_cell_visibility_and_rebuild(self):
        mock_db = MagicMock()
        win = TrackerDumpWindow(mock_db, defer_first_load=True)
        win.table.setRowCount(2)
        win.table.setColumnCount(TrackerDumpWindow.COLUMN_COUNT)

        # Row 0: matched client
        matched_item = {"client_id": 42, "is_unassigned": False, "portal": "Income Tax"}
        win._set_action_cell(0, matched_item, is_grouped=True)
        cell_0 = win.table.cellWidget(0, TrackerDumpWindow.COL_ACTIONS)
        btn_autofill_0 = cell_0.findChild(QPushButton, "act_autofill")
        self.assertFalse(btn_autofill_0.isHidden())

        # Row 1: unassigned capture (no client)
        unassigned_item = {"client_id": None, "is_unassigned": True, "portal": "Income Tax"}
        win._set_action_cell(1, unassigned_item, is_grouped=True)
        cell_1 = win.table.cellWidget(1, TrackerDumpWindow.COL_ACTIONS)
        btn_autofill_1 = cell_1.findChild(QPushButton, "act_autofill")
        self.assertTrue(btn_autofill_1.isHidden())

        # Rebuilding a cell that lacks act_autofill
        dummy_cell = QWidget()
        dummy_layout = win.table.layout()
        btn_old_view = QPushButton()
        btn_old_view.setObjectName("act_view")
        # Notice dummy_cell has no act_autofill
        dummy_btn_layout = tdw.QHBoxLayout(dummy_cell)
        dummy_btn_layout.addWidget(btn_old_view)
        win.table.setCellWidget(0, TrackerDumpWindow.COL_ACTIONS, dummy_cell)

        # _set_action_cell should detect missing act_autofill and rebuild it
        win._set_action_cell(0, matched_item, is_grouped=True)
        new_cell = win.table.cellWidget(0, TrackerDumpWindow.COL_ACTIONS)
        self.assertIsNot(new_cell, dummy_cell)
        self.assertIsNotNone(new_cell.findChild(QPushButton, "act_autofill"))

        win.deleteLater()
        APP.processEvents()

    def test_set_action_cell_dynamic_service_mode_styling(self):
        mock_db = MagicMock()
        services = [
            {"id": 1, "name": "Income Tax", "automation_mode": "extension"},
            {"id": 2, "name": "GST", "automation_mode": "smti"},
            {"id": 3, "name": "TRACES", "automation_mode": "manual"},
        ]
        mock_db.get_client_services.return_value = services

        win = TrackerDumpWindow(mock_db, defer_first_load=True)
        win.table.setRowCount(3)
        win.table.setColumnCount(TrackerDumpWindow.COLUMN_COUNT)

        # Fast Autofill row
        row_ext = {"client_id": 1, "portal": "Income Tax"}
        win._set_action_cell(0, row_ext, is_grouped=True)
        btn_0 = win.table.cellWidget(0, TrackerDumpWindow.COL_ACTIONS).findChild(QPushButton, "act_autofill")
        self.assertIn("Fast Autofill", btn_0.toolTip())
        self.assertIn("#FF4D4D", btn_0.styleSheet())

        # SMTI Assist row
        row_smti = {"client_id": 1, "portal": "GST"}
        win._set_action_cell(1, row_smti, is_grouped=True)
        btn_1 = win.table.cellWidget(1, TrackerDumpWindow.COL_ACTIONS).findChild(QPushButton, "act_autofill")
        self.assertIn("SMTI Manual Assist", btn_1.toolTip())
        self.assertIn("1.5px solid #FF4D4D", btn_1.styleSheet())

        # MECP Manual Copy row
        row_mecp = {"client_id": 1, "portal": "TRACES"}
        win._set_action_cell(2, row_mecp, is_grouped=True)
        btn_2 = win.table.cellWidget(2, TrackerDumpWindow.COL_ACTIONS).findChild(QPushButton, "act_autofill")
        self.assertIn("MECP Manual Copy", btn_2.toolTip())
        self.assertIn("1.5px solid #FF4D4D", btn_2.styleSheet())

        win.deleteLater()
        APP.processEvents()

    def test_on_action_autofill_click_emission(self):
        mock_db = MagicMock()
        service = {"id": 1, "name": "Income Tax", "automation_mode": "extension"}
        mock_db.get_client_services.return_value = [service]

        win = TrackerDumpWindow(mock_db, defer_first_load=True)
        win.table.setRowCount(1)
        win.table.setColumnCount(TrackerDumpWindow.COLUMN_COUNT)

        matched_item = {"client_id": 7, "is_unassigned": False, "portal": "Income Tax"}
        win._current_page_items = [matched_item]
        win._set_action_cell(0, matched_item, is_grouped=True)

        emitted = []
        win.service_action_requested.connect(lambda cid, svc: emitted.append((cid, svc)))

        cell = win.table.cellWidget(0, TrackerDumpWindow.COL_ACTIONS)
        btn_autofill = cell.findChild(QPushButton, "act_autofill")
        btn_autofill.click()
        APP.processEvents()

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], 7)
        self.assertEqual(emitted[0][1]["id"], 1)

        # Unmatched service scenario -> shows QMessageBox and emits nothing
        mock_db.get_client_services.return_value = []
        with patch.object(QMessageBox, "information") as mock_info:
            btn_autofill.click()
            APP.processEvents()
            self.assertEqual(len(emitted), 1)  # No new emission
            mock_info.assert_called_once()

        win.deleteLater()
        APP.processEvents()

    def test_client_detail_run_service_action_dispatch_and_restore(self):
        mock_db = MagicMock()
        original_client = {"id": 100, "values": {1: "ORIG_USER", 2: "ORIG_PASS"}, "notes": "original"}
        target_client = {"id": 200, "values": {1: "TARGET_USER", 2: "TARGET_PASS"}, "notes": "target"}
        mock_db.get_client.return_value = target_client

        detail_win = ClientDetailWindow(mock_db, actor="test_actor")
        detail_win.client = original_client

        launch_calls = {"extension": 0, "smti": 0, "manual": 0}

        def mock_launch_extension(svc):
            self.assertEqual(detail_win.client["id"], 200)
            launch_calls["extension"] += 1

        def mock_launch_smti(svc):
            self.assertEqual(detail_win.client["id"], 200)
            launch_calls["smti"] += 1

        def mock_launch_manual(svc):
            self.assertEqual(detail_win.client["id"], 200)
            launch_calls["manual"] += 1

        detail_win._launch_extension_autofill = mock_launch_extension
        detail_win._launch_manual_assist = mock_launch_smti
        detail_win._launch_manual_copy = mock_launch_manual

        # Test Extension autofill dispatch
        svc_ext = {"id": 10, "name": "Income Tax", "automation_mode": "extension"}
        detail_win.run_service_action(200, svc_ext)
        self.assertEqual(launch_calls["extension"], 1)
        self.assertEqual(detail_win.client["id"], 100)  # Restored

        # Test SMTI dispatch
        svc_smti = {"id": 11, "name": "SMTI Service", "automation_mode": "smti"}
        detail_win.run_service_action(200, svc_smti)
        self.assertEqual(launch_calls["smti"], 1)
        self.assertEqual(detail_win.client["id"], 100)  # Restored

        # Test MECP / Manual Copy dispatch
        svc_mecp = {"id": 12, "name": "Manual Service", "automation_mode": "manual"}
        detail_win.run_service_action(200, svc_mecp)
        self.assertEqual(launch_calls["manual"], 1)
        self.assertEqual(detail_win.client["id"], 100)  # Restored

        # Test exception safety: client is restored even if launcher raises
        def mock_failing_launcher(svc):
            self.assertEqual(detail_win.client["id"], 200)
            raise RuntimeError("Launcher explosion")

        detail_win._launch_extension_autofill = mock_failing_launcher
        with self.assertRaises(RuntimeError):
            detail_win.run_service_action(200, svc_ext)
        self.assertEqual(detail_win.client["id"], 100)  # Must still be restored!

        detail_win.deleteLater()
        APP.processEvents()


if __name__ == "__main__":
    unittest.main()
