"""
Start-up time (measured 2026-09-22): the window used to wait for a ~3 s tracker repair on every
database open, the whole capture-engine package (~0.4 s) imported through unrelated modules,
and a hidden page filling its table. These pin the fixes.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import security
from database import SeraDatabase

ROOT = Path(__file__).resolve().parents[1]


def _modules_after(code: str) -> set:
    out = subprocess.run([sys.executable, "-c", code + "\nimport sys\nprint(' '.join(sys.modules))"],
                         cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
    assert out.returncode == 0, out.stderr
    return set(out.stdout.split())


def test_importing_the_app_does_not_load_the_capture_engines():
    mods = _modules_after("import main")
    assert "core.vsdc" not in mods and "numpy" not in mods


def test_the_dialogs_package_does_not_load_the_capture_engines():
    assert "core.vsdc" not in _modules_after("import ui.dialogs.csv_import_dialog")
    assert "core.vsdc" in _modules_after("from ui.dialogs import AISettingsDialog")   # still works


class TestDatabaseOpen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt)
        self.key = security.derive_key_hex("testpass123", security.load_salt(salt))

    def tearDown(self):
        self.tmp.cleanup()

    def open(self, defer):
        return SeraDatabase(os.path.join(self.tmp.name, "m.db"), self.key,
                            raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"),
                            defer_startup_maintenance=defer)

    def test_the_tracker_repair_waits_for_the_background_maintenance(self):
        with patch.object(SeraDatabase, "re_resolve_all_tracker_dumps", return_value=0) as repair:
            db = self.open(defer=True)
            assert repair.call_count == 0
            db.run_startup_maintenance()
            assert repair.call_count == 1

    def test_without_deferring_the_repair_still_runs_before_the_constructor_returns(self):
        with patch.object(SeraDatabase, "re_resolve_all_tracker_dumps", return_value=0) as repair:
            self.open(defer=False)
            assert repair.call_count == 1


def test_the_hidden_tracker_page_fills_when_first_shown():
    from PySide6.QtWidgets import QApplication
    import ui.windows.tracker_dump_window as tdw
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        salt = os.path.join(tmp, "t.salt")
        security.generate_and_save_salt(salt)
        key = security.derive_key_hex("testpass123", security.load_salt(salt))
        db = SeraDatabase(os.path.join(tmp, "m.db"), key, raw_db_path=os.path.join(tmp, "rawPayload.db"))
        with patch.object(tdw, "_request_live_feed_export", lambda: None), \
                patch.object(tdw.TrackerDumpWindow, "load_data", autospec=True,
                             side_effect=tdw.TrackerDumpWindow.load_data) as load:
            win = tdw.TrackerDumpWindow(db, defer_first_load=True)
            assert load.call_count == 0
            win.show()
            app.processEvents()
            assert load.call_count == 1
            win.hide(); win.show()
            app.processEvents()
            assert load.call_count == 1                      # only the first show fills it
            win.deleteLater()
            app.processEvents()


def test_unchanged_extension_files_are_not_copied_again(tmp_path):
    import main
    src, dst = tmp_path / "a.js", tmp_path / "b.js"
    src.write_text("x = 1")
    with patch("shutil.copy2", wraps=__import__("shutil").copy2) as copy:
        main._copy_if_changed(src, dst)
        main._copy_if_changed(src, dst)
        assert copy.call_count == 1
        src.write_text("x = 22")
        main._copy_if_changed(src, dst)
        assert copy.call_count == 2 and dst.read_text() == "x = 22"


class TestSearchGridReuse(unittest.TestCase):
    """Switching back to the search tab rebuilt every cell (~0.2 s) even when nothing changed."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from ui.windows.search_window import SearchWindow
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        salt = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt)
        key = security.derive_key_hex("testpass123", security.load_salt(salt))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"))
        pan_col = next(c["id"] for c in self.db.get_mcl_columns() if c.get("is_internal_pk"))
        self.pan_col = pan_col
        self.db.add_client({pan_col: "ABCPD1234E"}, "", [], actor="test")
        self.win = SearchWindow(self.db)
        self.app.processEvents()

    def tearDown(self):
        self.win.deleteLater()
        self.app.processEvents()
        self.tmp.cleanup()

    def rebuilds(self):
        with patch.object(self.win, "_on_search_changed", wraps=self.win._on_search_changed) as spy:
            self.win.refresh()
            return spy.call_count

    def test_nothing_changed_keeps_the_grid(self):
        assert self.win.results_table.rowCount() == 1
        assert self.rebuilds() == 0

    def test_a_write_rebuilds_it(self):
        self.db.add_client({self.pan_col: "XYZAB9876C"}, "", [], actor="test")
        assert self.rebuilds() == 1
        assert self.win.results_table.rowCount() == 2

    def test_reads_do_not_count_as_writes(self):
        before = self.db.data_generation()
        self.db.search_clients("")
        self.db.get_mcl_columns()
        assert self.db.data_generation() == before

    def test_a_typed_search_is_cleared_and_redrawn(self):
        self.win.search_box.setText("ABC")
        self.win._on_search_changed()
        assert self.rebuilds() == 1 and self.win.search_box.text() == ""

    def test_it_is_trusted_for_a_minute_at_most(self):
        with patch.object(self.win, "REUSE_GRID_FOR_S", 0.0):
            assert self.rebuilds() == 1


def test_a_slow_screen_switch_is_logged(tmp_path, monkeypatch):
    import time as _time
    from core import memlog
    monkeypatch.setattr(memlog, "_log_path", lambda: tmp_path / "memory.log")
    with memlog.timed("fast"):
        pass
    with memlog.timed("opening the test grid"):
        _time.sleep(memlog.SLOW_MS / 1000 + 0.02)
    text = (tmp_path / "memory.log").read_text(encoding="utf-8")
    assert "fast" not in text and "slow: opening the test grid took" in text


def test_the_periodic_sample_does_not_trim_while_the_window_is_in_use(tmp_path, monkeypatch):
    from core import memlog
    monkeypatch.setattr(memlog, "_log_path", lambda: tmp_path / "memory.log")
    calls = []
    monkeypatch.setattr(memlog, "trim_working_set", lambda reason: calls.append(reason))
    monkeypatch.setattr(memlog, "snapshot", lambda: (memlog.TRIM_CEILING_MB + 50, 150.0, 200.0))
    memlog.sample_and_maybe_trim("sample", may_trim=lambda: False)
    assert calls == []
    memlog.sample_and_maybe_trim("sample", may_trim=lambda: True)
    assert len(calls) == 1
