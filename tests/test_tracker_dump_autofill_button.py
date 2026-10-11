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
from ui.utils.tokens import TOKENS as T
import ui.windows.tracker_dump_window as tdw
from ui.windows.tracker_dump_window import TrackerDumpWindow, _service_matches_portal
from ui.windows.client_detail_window import ClientDetailWindow
from ui.components.kit import MessageBox

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

    def test_row_actions_order_and_buttons(self):
        mock_db = MagicMock()
        win = TrackerDumpWindow(mock_db, defer_first_load=True)

        registered = win._row_actions({"client_id": 5, "portal": "Income Tax"}, is_grouped=True)
        self.assertEqual([a.key for a in registered], ["autofill", "view", "more"])

        unassigned = win._row_actions({"client_id": None, "is_unassigned": True, "portal": "Income Tax"}, is_grouped=True)
        self.assertEqual([a.key for a in unassigned], ["view", "create", "more"])

        raw_no_client = win._row_actions({"client_id": None, "portal": "Income Tax"}, is_grouped=False)
        self.assertEqual([a.key for a in raw_no_client], ["view", "create", "more"])

        win.deleteLater()
        APP.processEvents()

    def test_autofill_action_follows_the_service_mode(self):
        mock_db = MagicMock()
        services = [
            {"id": 1, "name": "Income Tax", "automation_mode": "extension"},
            {"id": 2, "name": "GST", "automation_mode": "smti"},
            {"id": 3, "name": "TRACES", "automation_mode": "manual"},
        ]
        mock_db.get_client_services.return_value = services

        win = TrackerDumpWindow(mock_db, defer_first_load=True)

        fast = win._autofill_action({"client_id": 1, "portal": "Income Tax"})
        self.assertIn("Fast Autofill", fast.tooltip)
        self.assertTrue(fast.primary)
        self.assertIsNone(fast.colour)          # the kit's emerald primary; red is for danger only

        smti = win._autofill_action({"client_id": 1, "portal": "GST"})
        self.assertIn("SMTI Manual Assist", smti.tooltip)
        self.assertFalse(smti.primary)

        mecp = win._autofill_action({"client_id": 1, "portal": "TRACES"})
        self.assertIn("MECP Manual Copy", mecp.tooltip)
        self.assertFalse(mecp.primary)

        none = win._autofill_action({"client_id": 1, "portal": "Unknown Portal"})
        self.assertIn("Fast Autofill", none.tooltip)
        self.assertTrue(none.primary)

        win.deleteLater()
        APP.processEvents()

    def test_on_action_autofill_click_emission(self):
        mock_db = MagicMock()
        service = {"id": 1, "name": "Income Tax", "automation_mode": "extension"}
        mock_db.get_client_services.return_value = [service]

        win = TrackerDumpWindow(mock_db, defer_first_load=True)
        win.table.setRowCount(1)

        matched_item = {"client_id": 7, "is_unassigned": False, "portal": "Income Tax"}
        win._current_page_items = [matched_item]
        win.cmb_view_mode.setCurrentIndex(0)

        emitted = []
        win.service_action_requested.connect(lambda cid, svc: emitted.append((cid, svc)))

        win._on_grid_action(0, TrackerDumpWindow.COL_ACTIONS, "autofill")
        APP.processEvents()

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], 7)
        self.assertEqual(emitted[0][1]["id"], 1)

        # Unmatched service scenario -> shows QMessageBox and emits nothing
        win._client_services_cache = {}
        mock_db.get_client_services.return_value = []
        with patch.object(MessageBox, "information") as mock_info:
            win._on_grid_action(0, TrackerDumpWindow.COL_ACTIONS, "autofill")
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
