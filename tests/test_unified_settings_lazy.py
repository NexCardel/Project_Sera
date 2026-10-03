"""
The Unified Settings dialog builds a page the first time it is visited (building all eight up
front cost ~1 s before it could show). Dirty tracking and Save must behave as if every page had
always existed.
"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import security
from database import SeraDatabase
from PySide6.QtWidgets import QApplication

from ui.dialogs import unified_settings_dialog as usd

_app = QApplication.instance() or QApplication([])


class TestUnifiedSettingsLazyPages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp.name, "s.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("pw", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "r.db"))
        self.dlg = usd.UnifiedSettingsDialog(self.db, actor="test")

    def tearDown(self):
        self.dlg.deleteLater()
        self.tmp.cleanup()

    def test_only_the_requested_page_is_built_at_first(self):
        self.assertEqual(set(self.dlg._page_widgets), {usd._P_GENERAL})
        self.assertFalse(hasattr(self.dlg, "scc_check"))

    def test_visiting_pages_is_not_a_change(self):
        for page in (usd._P_SCC, usd._P_ACTIONS, usd._P_TRACKER, usd._P_MAIN_VIS, usd._P_QC, usd._P_ADMIN_VIS):
            self.dlg._switch_page(page)
            self.assertFalse(self.dlg._btn_save.isEnabled(), f"page {page} made the dialog look dirty")
        self.assertEqual(self.dlg._capture_state(), self.dlg._initial_state)

    def test_edit_on_one_page_survives_building_another_and_saves_only_built_groups(self):
        self.dlg.clipboard_spin.setValue(77)                       # general page, unsaved
        self.assertTrue(self.dlg._btn_save.isEnabled())
        self.dlg._switch_page(usd._P_SCC)                          # builds + loads the SCC page
        self.assertEqual(self.dlg.clipboard_spin.value(), 77)      # the edit was not reset
        self.assertTrue(self.dlg._btn_save.isEnabled())

        self.db.set_setting("sgt_mode", "shadow")                  # a group whose page is never built
        self.dlg._on_save_settings()
        self.assertEqual(self.db.get_setting("clipboard_clear_seconds"), "77")
        self.assertEqual(self.db.get_setting("sgt_mode"), "shadow")  # untouched: tracker page not built
        self.assertFalse(self.dlg._btn_save.isEnabled())

        self.dlg.scc_check.setChecked(not self.dlg.scc_check.isChecked())
        self.assertTrue(self.dlg._btn_save.isEnabled())

    def test_reopen_shows_saved_values_and_reloads_built_pages(self):
        self.dlg._switch_page(usd._P_SCC)
        self.dlg.clipboard_spin.setValue(55)
        self.dlg.set_page("general")                               # reopen: unsaved edit is dropped
        self.assertNotEqual(self.dlg.clipboard_spin.value(), 55)
        self.assertFalse(self.dlg._btn_save.isEnabled())

    def test_page_requested_by_name_opens_on_that_page(self):
        self.dlg.set_page("mcl")
        self.assertIn(usd._P_MCL, self.dlg._page_widgets)
        self.assertEqual(self.dlg._stack.currentWidget(), self.dlg._page_widgets[usd._P_MCL])


if __name__ == "__main__":
    unittest.main()
