"""S-14: the remaining dialogs on the kit, and the kit message helpers they use."""
import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _click_when_shown(label):
    """Clicks the button called `label` on the modal dialog that opens next; rejects it if absent."""
    def go():
        dlg = QApplication.activeModalWidget()
        for btn in dlg.findChildren(QPushButton):
            if btn.text() == label:
                btn.click()
                return
        dlg.reject()
    QTimer.singleShot(0, go)


def test_show_message_shows_its_text_and_one_ok(app):
    from ui.components.kit import show_message
    seen = []

    def go():
        dlg = QApplication.activeModalWidget()
        seen.append(([lbl.text() for lbl in dlg.findChildren(QLabel, "KitDlgMessage")],
                     [b.text() for b in dlg.findChildren(QPushButton)]))
        dlg.accept()
    QTimer.singleShot(0, go)
    show_message(None, "Title", "Body text", "warning")
    assert seen == [(["Body text"], ["OK"])]


def test_ask_confirm_returns_true_for_yes_and_false_for_no(app):
    from ui.components.kit import ask_confirm
    _click_when_shown("Remove")
    assert ask_confirm(None, "Confirm", "Remove it?", yes="Remove", no="Cancel", danger=True) is True
    _click_when_shown("Cancel")
    assert ask_confirm(None, "Confirm", "Remove it?", yes="Remove", no="Cancel") is False


def test_sca_diagnostics_dialog_builds_on_the_kit(app):
    from ui.dialogs.sca_diagnostics_dialog import ScaDiagnosticsDialog
    dlg = ScaDiagnosticsDialog(MagicMock())
    assert dlg.log_table.columnCount() == 4
    assert dlg.status_label.text()
    dlg.close()


def test_ltt_rules_dialog_builds_on_the_kit(app):
    from ui.windows.ltt_rules_dialog import LttRulesDialog
    dlg = LttRulesDialog(MagicMock())
    assert dlg.lst_forms is not None and dlg.btn_save is not None
    dlg.close()


def test_add_client_from_capture_lists_the_mcl_fields(app):
    from ui.windows.tracker_dump_window import AddClientFromCaptureDialog
    db = MagicMock()
    db.get_mcl_columns.return_value = [
        {"id": "pan", "label": "PAN", "field_type": "text", "is_internal_pk": True},
        {"id": "name", "label": "Client name", "field_type": "text"},
    ]
    dlg = AddClientFromCaptureDialog(db, {"portal": "GST", "arn_number": "AA1"})
    assert set(dlg.field_inputs) == {"pan", "name"}
    assert dlg.field_inputs["name"][2] == "Client name"
    dlg.close()


@pytest.fixture
def mcl_dialog(app, monkeypatch):
    from ui.windows import tracker_dump_window as tdw
    monkeypatch.setattr(tdw, "_DatapointScanThread", MagicMock())
    db = MagicMock()
    db.get_mcl_columns.return_value = [{"id": "c1", "label": "Client name", "field_type": "text"}]
    db.get_capture_mcl_mappings.return_value = [{"source": "saved.key", "column_id": "c1"}]
    shown = []
    monkeypatch.setattr(tdw, "show_message", lambda *a, **k: shown.append(a))
    dlg = tdw.McLMappingDialog(db)
    dlg._populate([{"key": "auto.key", "count": 3, "sample": "x", "auto_column_id": "c1"}])
    yield dlg, db, shown
    dlg.close()


def test_mcl_mapping_lists_captured_and_saved_datapoints(mcl_dialog):
    dlg, _, _ = mcl_dialog
    assert dlg.table.rowCount() == 2
    assert dlg.lbl_status.text() == "2 of 2 datapoints shown  ·  2 mapped, 0 unmapped"
    assert dlg.btn_save.isEnabled() and dlg.btn_apply.isEnabled()


def test_mcl_mapping_save_keeps_only_mapped_rows_and_reports(mcl_dialog):
    dlg, db, shown = mcl_dialog
    dlg._save(apply_now=False)
    db.save_capture_mcl_mappings.assert_called_once()
    saved = db.save_capture_mcl_mappings.call_args.args[0]
    assert saved == [{"source": "saved.key", "column_id": "c1"}]
    assert shown[0][1] == "Mappings saved"
    assert dlg.result() == QDialog.Accepted


def test_mcl_mapping_apply_reports_updated_clients(mcl_dialog):
    dlg, db, shown = mcl_dialog
    db.enrich_registered_clients_from_containers.return_value = 3
    dlg._save(apply_now=True)
    assert "3 registered client record(s) updated." in shown[0][2]


def test_mcl_mapping_save_failure_is_critical(mcl_dialog):
    dlg, db, shown = mcl_dialog
    db.save_capture_mcl_mappings.side_effect = RuntimeError("disk full")
    dlg._save(apply_now=False)
    assert shown[0][1] == "Save failed"
    assert shown[0][2] == "Could not save the mappings: disk full"
    assert shown[0][3] == "critical"
    assert dlg.result() != QDialog.Accepted
