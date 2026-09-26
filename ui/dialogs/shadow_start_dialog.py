"""Turning shadow mode on: start-up half (WP P3-7b).

Runs at start-up, before any database is opened (``main.py`` ``_run_pending_shadow_start``):
installs a staged shadow-mode start (the admin PC's replica becomes this PC's live DB, replica
and baseline) and then offers the P2-8 salvage import of what only this PC had. The plumbing is
in ``sync_shadow``; this module only asks the user and shows results.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

import sync_shadow

TITLE = "Aman Associates — Shadow Mode"


def _busy(on: bool) -> None:
    if QApplication.instance() is None:
        return
    if on:
        QApplication.setOverrideCursor(Qt.WaitCursor)
    else:
        QApplication.restoreOverrideCursor()


def _apply_pending(app: Path) -> None:
    _busy(True)
    try:
        outcome = sync_shadow.apply_pending_shadow_start(app)
    except sync_shadow.ShadowStartError as e:
        _busy(False)
        QMessageBox.warning(None, TITLE, f"Shadow mode could not be started: {e}\n\n"
                            "This PC's database was not changed. Try \"Start shadow mode\" again "
                            "from the Sera Sync panel.")
        return
    except Exception as e:
        _busy(False)
        QMessageBox.warning(None, TITLE, f"Shadow mode could not be started yet: {e}\n\n"
                            "Nothing was changed. Sera will try again at the next start.")
        return
    finally:
        _busy(False)
    if outcome:
        QMessageBox.information(
            None, TITLE,
            "Shadow mode is on. This PC now works on the office data from the admin PC.\n\n"
            f"This PC's previous database is kept in:\n{outcome['pre_start_dir']}")


def _run_salvage(app: Path) -> None:
    from ui.dialogs.rejoin_office_dialog import SalvageDialog
    _busy(True)
    try:
        report = sync_shadow.plan_shadow_salvage(app)
    except Exception as e:
        _busy(False)
        QMessageBox.warning(None, TITLE, f"Could not read this PC's previous data: {e}\n\n"
                            "Sera will offer the import again next time.")
        return
    finally:
        _busy(False)

    if not (report.changes or report.conflicts or report.no_pk or report.ambiguous):
        sync_shadow.skip_shadow_salvage(app)          # nothing only this PC had
        return
    dlg = SalvageDialog(report)
    dlg.exec()
    if dlg.choice == "skip":
        path = sync_shadow.skip_shadow_salvage(app)
        QMessageBox.information(None, TITLE, f"Nothing was imported. The previous database stays in:\n"
                                f"{report.legacy_dir}" + (f"\n\nReport: {path}" if path else ""))
        return
    if dlg.choice != "import":
        return
    _busy(True)
    try:
        done = sync_shadow.apply_shadow_salvage(app, take_legacy=dlg.take_legacy)
    except Exception as e:
        _busy(False)
        QMessageBox.warning(None, TITLE, f"The import failed and was undone: {e}\n\n"
                            "Sera will offer it again next time.")
        return
    finally:
        _busy(False)
    QMessageBox.information(
        None, TITLE,
        f"Imported {len(done.to_insert)} client(s), {done.audit_new} audit entries, {done.tracker_new} "
        f"tracker filing(s) and {done.timelines_new} session timeline(s). They reach the other PCs "
        f"through Sera Sync.\n\nReport: {done.report_path}")


def run_pending_shadow_start(app_dir) -> None:
    app = Path(app_dir)
    if sync_shadow.has_pending_shadow_start(app):
        _apply_pending(app)
    if sync_shadow.pending_shadow_salvage(app) is not None:
        _run_salvage(app)
