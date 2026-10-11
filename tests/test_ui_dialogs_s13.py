"""S-13: the small dialogs on the kit (CSV import, master password, office recovery, rejoin,
first run, join request, force update, startup loading).

Structure (DialogFrame, one primary, no literal styles / hex), attribute names the older tests
rely on, and the few behaviours the layout touches. Fictional data only; throwaway DB.
"""

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(__file__))
import ui_lock_support as S  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "csv_import_dialog.py", "change_master_password_dialog.py", "office_recovery_dialog.py",
    "rejoin_office_dialog.py", "first_run_dialog.py", "update_dialog.py", "loading_dialog.py",
]


@pytest.fixture
def made():
    S.app
    dialogs = []
    yield dialogs
    for dlg in dialogs:
        dlg.close()
        dlg.deleteLater()


def _keep(made, dlg):
    made.append(dlg)
    return dlg


def _primaries(widget):
    from ui.components.kit import LabelledAction
    return [b for b in widget.findChildren(LabelledAction) if b.variant == "primary"]


def _frames(widget):
    from ui.components.kit import DialogFrame
    return widget.findChildren(DialogFrame)


@pytest.mark.parametrize("name", FILES)
def test_no_literal_styles_or_hex(name):
    src = (ROOT / "ui" / "dialogs" / name).read_text(encoding="utf-8")
    assert "setStyleSheet(" not in src
    assert not re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", src)


def test_theme_has_one_progress_bar_rule():
    from ui.utils.theme import get_theme_stylesheet
    css = get_theme_stylesheet("dark")
    assert "QProgressBar {" in css and "QProgressBar::chunk" in css
    assert "$" not in css.split("QProgressBar {")[1].split("}")[0]


def test_csv_import_flow(made, tmp_path):
    from ui.dialogs.csv_import_dialog import CSVImportDialog, ImportResultDialog
    db = S.make_db(tmp_path)
    dlg = _keep(made, CSVImportDialog(db))
    assert len(_frames(dlg)) == 1 and len(_primaries(dlg)) == 1
    assert not dlg.import_btn.isEnabled()
    cols = [c["label"] for c in db.get_mcl_columns()][:2]
    csv_path = tmp_path / "fake.csv"
    csv_path.write_text(",".join(cols + ["Mystery"]) + "\nx,y,z\n", encoding="utf-8")
    dlg._load_preview(str(csv_path))
    assert dlg.import_btn.isEnabled()
    assert dlg.table.rowCount() == 3
    res = _keep(made, ImportResultDialog({"imported": 2, "updated": 1, "skipped_columns": ["Mystery"],
                                           "warnings": ["Row 2: fictional warning."]}))
    assert len(_primaries(res)) == 1


def test_change_master_password_structure(made, tmp_path):
    from ui.dialogs.change_master_password_dialog import ChangeMasterPasswordDialog
    from PySide6.QtWidgets import QLineEdit
    dlg = _keep(made, ChangeMasterPasswordDialog(tmp_path))
    assert len(_frames(dlg)) == 1 and len(_primaries(dlg)) == 1
    for field in (dlg.current_pw, dlg.new_pw, dlg.confirm_pw):
        assert field.echoMode() == QLineEdit.Password
    assert dlg.lbl_error.isHidden()
    dlg._show_error("New passwords do not match.")
    assert not dlg.lbl_error.isHidden()
    assert "do not match" in dlg.lbl_error.text()


def test_office_recovery_structure(made, tmp_path):
    from ui.dialogs.office_recovery_dialog import OfficeRecoveryDialog
    from PySide6.QtWidgets import QLineEdit
    dlg = _keep(made, OfficeRecoveryDialog(tmp_path))
    assert len(_frames(dlg)) == 1 and len(_primaries(dlg)) == 1
    assert dlg.pw_edit.echoMode() == QLineEdit.Password
    dlg.btn_toggle_pw.click()
    assert dlg.pw_edit.echoMode() == QLineEdit.Normal
    dlg.btn_toggle_pw.click()
    assert dlg.pw_edit.echoMode() == QLineEdit.Password
    for name in ("btn_restore_kit", "btn_exit", "btn_unlock", "lbl_error"):
        assert hasattr(dlg, name)


def test_rejoin_and_salvage(made):
    from PySide6.QtWidgets import QLineEdit
    from ui.dialogs.rejoin_office_dialog import RejoinOfficeDialog, SalvageDialog
    d1 = _keep(made, RejoinOfficeDialog(lambda pw: True, default_name="Branch PC"))
    assert len(_primaries(d1)) == 1 and d1.legacy_pw.isEnabled()
    assert d1.legacy_pw.echoMode() == QLineEdit.Password
    d2 = _keep(made, RejoinOfficeDialog(None, default_name="Branch PC"))
    assert not d2.legacy_pw.isEnabled()
    conf = [SimpleNamespace(key=f"ABCDE{1000 + i}F", labels=["NAME OF COMPANY"]) for i in range(3)]
    report = SimpleNamespace(pk_label="PAN", to_insert=[1], audit_new=2, tracker_new=1, timelines_new=0,
                             matched_same=5, no_pk=[], ambiguous=[], unknown_labels=[], notes=[],
                             conflicts=conf, legacy_dir="legacy")
    sal = _keep(made, SalvageDialog(report))
    assert len(_primaries(sal)) == 1
    assert set(sal._boxes) == {c.key for c in conf}
    assert all(not b.isChecked() for b in sal._boxes.values())


def test_first_run_pages(made, tmp_path):
    from ui.dialogs.first_run_dialog import FirstRunDialog
    from PySide6.QtWidgets import QLineEdit, QProgressBar
    dlg = _keep(made, FirstRunDialog(tmp_path, "Admin"))
    assert dlg.stack.count() == 3 and len(_frames(dlg)) == 3
    for page in (dlg.stack.widget(i) for i in range(3)):
        assert len(_primaries(page)) <= 1
    assert len(_primaries(dlg.page_office_new)) == 1 and len(_primaries(dlg.page_office_join)) == 1
    for field in (dlg.office_new_pwd_input, dlg.office_new_pwd_confirm):
        assert field.echoMode() == QLineEdit.Password
    dlg._show_new_error("The passwords do not match.")
    assert "do not match" in dlg.office_new_error_label.text()
    dlg.new_office_btn.click()
    assert dlg.stack.currentIndex() == 1
    assert isinstance(dlg.office_join_progress, QProgressBar)
    assert not dlg.office_join_progress.isVisibleTo(dlg)
    dlg._add_office_peer_row({"office_name": "Aman Associates", "name": "ADMIN-PC-1", "ip": "192.168.1.10"})
    assert dlg.office_peers_table.rowCount() == 1


def test_join_request_dialog(made):
    from ui.dialogs.first_run_dialog import JoinApprovalDialog
    dlg = _keep(made, JoinApprovalDialog("OFFICE-PC-2", "Rao", "123456", 120))
    dlg._timer.stop()
    assert "123  456" in dlg.code_label.text()
    assert len(_primaries(dlg)) == 1 and dlg.allow_btn.isDefault()
    dlg._on_tick()
    assert dlg.timeout_remaining == 119
    assert "119" in dlg.timer_label.text()


def test_force_update_dialog(made, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QMessageBox
    from ui.dialogs import update_dialog
    asked = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: asked.append(a[1]) or QMessageBox.No))
    info = {"current_version": "2.12.0", "latest_version": "2.13.0", "release_notes": "Notes",
            "download_url": ""}
    dlg = _keep(made, update_dialog.ForceUpdateDialog(info, auto_start=False))
    assert len(_primaries(dlg)) == 1
    assert dlg.progress_bar.isHidden()
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    ev.accept()
    dlg.keyPressEvent(ev)
    assert not ev.isAccepted()
    dlg.exit_btn.click()
    assert asked == ["Update Required"]
    dlg._on_progress(50, 100)
    assert "50%" in dlg.status_label.text() and dlg.progress_bar.value() == 50
    dlg.mandatory = False


def test_startup_loading_dialog(made):
    from ui.dialogs.loading_dialog import StartupLoadingDialog
    dlg = _keep(made, StartupLoadingDialog())
    assert dlg.progress.minimum() == 0 and dlg.progress.maximum() == 0
    dlg.lbl_status.setText("Opening...")
    assert dlg.lbl_status.text() == "Opening..."
