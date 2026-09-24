"""Rejoin office wizard and salvage review (WP P2-8).

Runs at start-up, before any database is opened (``main.py`` ``_run_pending_rejoin``). The
plumbing is in ``sync_rejoin``; this module only asks the user and shows results.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import sync_rejoin

TITLE = "Aman Associates — Rejoin Office"
MAX_PASSWORD_ATTEMPTS = 5


@dataclass
class RejoinDetails:
    host: str
    code: str
    device_name: str
    legacy_password: str | None


class RejoinOfficeDialog(QDialog):
    """Old password (only if the saved one doesn't work), admin PC address, pairing code, PC name."""

    def __init__(self, verify_legacy_password=None, default_name: str = "", parent=None):
        super().__init__(parent)
        self._verify = verify_legacy_password
        self._attempts = 0
        self.details: RejoinDetails | None = None
        self.setWindowTitle("Rejoin Office")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "This PC gets the office database from the admin PC. Its current files are moved to a "
            "\"legacy\" folder first (nothing is deleted). Afterwards Sera shows what only this PC has "
            "(clients, audit entries, tracker filings) and imports it after you confirm.\n\n"
            "On the admin PC, open Sera Sync → \"Add workstation\" to get a 6-digit code."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.legacy_pw = QLineEdit()
        self.legacy_pw.setEchoMode(QLineEdit.Password)
        if self._verify is not None:
            form.addRow("This PC's current master password:", self.legacy_pw)
        self.host = QLineEdit()
        self.host.setPlaceholderText("e.g. 192.168.1.10")
        form.addRow("Admin PC address (IP):", self.host)
        self.code = QLineEdit()
        self.code.setPlaceholderText("123 456")
        self.code.setMaxLength(7)
        form.addRow("Pairing code:", self.code)
        self.device_name = QLineEdit(default_name)
        self.device_name.setMaxLength(64)
        form.addRow("This PC's name:", self.device_name)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #E5534B;")
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Rejoin")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def show_error(self, text: str) -> None:
        self.error.setText(text)

    def _on_ok(self) -> None:
        import sync_pairing
        host = self.host.text().strip()
        if not host:
            self.show_error("Enter the admin PC's address.")
            return
        try:
            code = sync_pairing.normalize_code(self.code.text())
        except ValueError:
            self.show_error("The pairing code is 6 digits.")
            return
        name = self.device_name.text().strip()
        if not name:
            self.show_error("Enter a name for this PC.")
            return
        password = None
        if self._verify is not None:
            password = self.legacy_pw.text()
            try:
                ok = self._verify(password)
            except Exception as e:
                self.show_error(f"Can't check the password right now: {e}")
                return
            if not ok:
                self._attempts += 1
                if self._attempts >= MAX_PASSWORD_ATTEMPTS:
                    self.show_error("Too many wrong passwords.")
                    self.reject()
                    return
                self.show_error("That password doesn't open this PC's database.")
                return
        self.details = RejoinDetails(host, code, name, password)
        self.accept()


class SalvageDialog(QDialog):
    """Shows the dry run. ``choice``: "import", "later" or "skip"; ``take_legacy``: chosen PK values."""

    def __init__(self, report: sync_rejoin.SalvageReport, parent=None):
        super().__init__(parent)
        self.report = report
        self.choice = "later"
        self.take_legacy: set[str] = set()
        self._boxes: dict[str, QCheckBox] = {}
        self.setWindowTitle("Import this PC's data")
        self.setMinimumWidth(620)

        r = report
        pk = r.pk_label or "PAN"
        layout = QVBoxLayout(self)
        summary = [
            f"Found on this PC but not in the office database:",
            f"  • {len(r.to_insert)} client(s) to add",
            f"  • {r.audit_new} audit log entries",
            f"  • {r.tracker_new} tracker filing(s), {r.timelines_new} session timeline(s)",
            f"{r.matched_same} client(s) are already in the office with the same values.",
        ]
        if r.no_pk:
            summary.append(f"{len(r.no_pk)} client(s) have no {pk}, so they can't be matched safely and "
                           "won't be imported (they are listed in the report).")
        if r.ambiguous:
            summary.append(f"{len(r.ambiguous)} client(s) share a {pk} with other clients and won't be "
                           "imported (listed in the report).")
        if r.unknown_labels:
            summary.append("Columns the office doesn't have (not imported): "
                           + ", ".join(sorted(r.unknown_labels)))
        summary += r.notes
        text = QLabel("\n".join(summary))
        text.setWordWrap(True)
        layout.addWidget(text)

        if r.conflicts:
            hdr = QLabel(f"{len(r.conflicts)} client(s) have different values here. The office values are kept "
                         "unless you tick \"take this PC's values\" (empty values here never erase office values):")
            hdr.setWordWrap(True)
            layout.addWidget(hdr)
            inner = QWidget()
            inner_layout = QVBoxLayout(inner)
            for c in r.conflicts:
                box = QCheckBox(f"{c.key} — take this PC's values ({', '.join(c.labels)})")
                self._boxes[c.key] = box
                inner_layout.addWidget(box)
            inner_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(inner)
            scroll.setMinimumHeight(min(260, 40 + 28 * len(r.conflicts)))
            layout.addWidget(scroll)

        buttons = QDialogButtonBox()
        self.btn_import = QPushButton("Import")
        self.btn_later = QPushButton("Not now")
        self.btn_skip = QPushButton("Don't import")
        buttons.addButton(self.btn_import, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.btn_later, QDialogButtonBox.RejectRole)
        buttons.addButton(self.btn_skip, QDialogButtonBox.DestructiveRole)
        self.btn_import.clicked.connect(self._on_import)
        self.btn_later.clicked.connect(self.reject)
        self.btn_skip.clicked.connect(self._on_skip)
        layout.addWidget(buttons)

    def _on_import(self) -> None:
        self.take_legacy = {k for k, box in self._boxes.items() if box.isChecked()}
        self.choice = "import"
        self.accept()

    def _on_skip(self) -> None:
        reply = QMessageBox.question(
            self, "Don't import",
            "Don't import anything from this PC's old data?\n\nThe old files stay in the legacy folder, "
            "but Sera won't offer the import again.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.choice = "skip"
            self.accept()


# ---------------------------------------------------------------- start-up flow

def _busy(on: bool) -> None:
    if QApplication.instance() is None:
        return
    if on:
        QApplication.setOverrideCursor(Qt.WaitCursor)
    else:
        QApplication.restoreOverrideCursor()


def _progress(fname: str, received: int, size: int) -> None:
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def _run_wizard(app: Path) -> tuple[str | None, str | None]:
    """Steps 1–2. Returns (outcome, typed legacy password)."""
    import sera_keys
    import sync_migrate
    import sync_pairing

    saved_ok = sync_rejoin.saved_legacy_hex_key(app) is not None
    verify = None if saved_ok else (lambda pw: sync_migrate.check_legacy_password(app, pw))
    dlg = RejoinOfficeDialog(verify, default_name=socket.gethostname())
    while True:
        if dlg.exec() != QDialog.Accepted or dlg.details is None:
            return None, None
        d = dlg.details
        _busy(True)
        try:
            sync_rejoin.rejoin_office(app, d.host, d.code, d.device_name, on_progress=_progress)
            return "salvage_pending", d.legacy_password
        except sync_pairing.WrongCode:
            dlg.show_error("The code doesn't match. Ask for a new code on the admin PC and try again.")
        except Exception as e:
            if sera_keys.load_office(app) is None:
                dlg.show_error(f"Could not join the office: {e}\n\nThis PC's files were put back; nothing changed.")
            else:
                _busy(False)
                return _download_failed(app, e), None
        finally:
            _busy(False)


def _undo(app: Path) -> str | None:
    """Back to this PC's own files. Returns None (continue start-up, legacy mode) or "exit"."""
    try:
        sync_rejoin.undo_rejoin(app)
    except Exception as e:
        QMessageBox.critical(None, TITLE, f"The rejoin could not be undone: {e}\n\n"
                             "Sera will close so nothing is damaged. Contact the office administrator.")
        return "exit"
    QMessageBox.information(None, TITLE, "The rejoin was undone. This PC uses its own files again.")
    return None


def _download_failed(app: Path, error) -> str | None:
    reply = QMessageBox.question(
        None, TITLE,
        f"This PC joined the office, but the office database could not be downloaded:\n\n{error}\n\n"
        "Yes: undo the rejoin and keep using this PC's own files for now.\n"
        "No: close Sera; start it again (with the admin PC on and Sera open there) to finish.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if reply == QMessageBox.Yes:
        return _undo(app)
    return "exit"


def _ask_password(legacy_dir: str) -> str | None:
    text = ("Enter this PC's old master password to import its data.\n"
            f"(Old files: {legacy_dir})")
    pw, ok = QInputDialog.getText(None, TITLE, text, QLineEdit.Password)
    return pw if ok and pw else None


def _run_salvage(app: Path, password: str | None) -> None:
    state = sync_rejoin.read_state(app) or {}
    report = None
    for _ in range(MAX_PASSWORD_ATTEMPTS + 1):
        _busy(True)
        try:
            report = sync_rejoin.salvage_after_rejoin(app, dry_run=True, password=password)
            break
        except sync_rejoin.LegacyPasswordNeeded:
            _busy(False)
            password = _ask_password(state.get("legacy_dir", ""))
            if password is None:
                return                          # asked again at the next start
        except sync_rejoin.SalvageError as e:
            _busy(False)
            QMessageBox.critical(None, TITLE, f"{e}\n\nThis PC's old data is in:\n{state.get('legacy_dir', '')}\n\n"
                                 "Please report this to the owner. Sera will offer the import again next time.")
            return
        except Exception as e:
            _busy(False)
            QMessageBox.warning(None, TITLE, f"Could not read this PC's old data: {e}\n\n"
                                "Sera will offer the import again next time.")
            return
        finally:
            _busy(False)
    if report is None:
        return

    dlg = SalvageDialog(report)
    dlg.exec()
    if dlg.choice == "skip":
        path = sync_rejoin.skip_salvage(app)
        QMessageBox.information(None, TITLE, f"Nothing was imported. The old files stay in:\n{report.legacy_dir}"
                                + (f"\n\nReport: {path}" if path else ""))
        return
    if dlg.choice != "import":
        return
    _busy(True)
    try:
        done = sync_rejoin.salvage_after_rejoin(app, dry_run=False, password=password, take_legacy=dlg.take_legacy)
    except Exception as e:
        _busy(False)
        QMessageBox.warning(None, TITLE, f"The import failed and was undone: {e}\n\n"
                            "Sera will offer it again next time.")
        return
    finally:
        _busy(False)
    QMessageBox.information(
        None, TITLE,
        f"Imported {len(done.to_insert)} client(s), {done.audit_new} audit entries, {done.tracker_new} tracker "
        f"filing(s) and {done.timelines_new} session timeline(s).\n\nReport: {done.report_path}\n"
        f"Old files kept in: {done.legacy_dir}")


def _ask_pending_join() -> str:
    """"finish", "undo" or "quit"."""
    box = QMessageBox(QMessageBox.Question, TITLE,
                      "This PC joined the office but the office database hasn't been downloaded yet.\n\n"
                      "Connect to the admin PC now and finish? If the admin PC can't provide it, you can "
                      "undo the rejoin and keep using this PC's own files.")
    btn_finish = box.addButton("Finish now", QMessageBox.AcceptRole)
    btn_undo = box.addButton("Undo rejoin", QMessageBox.DestructiveRole)
    box.addButton("Close Sera", QMessageBox.RejectRole)
    box.setDefaultButton(btn_finish)
    box.exec()
    if box.clickedButton() is btn_finish:
        return "finish"
    if box.clickedButton() is btn_undo:
        return "undo"
    return "quit"


def run_pending_rejoin(app_dir, requested: bool) -> str | None:
    """Everything P2-8 does at start-up. Returns "exit" (stop with an error), "quit" (the user
    chose to stop) or None (continue start-up)."""
    import sera_keys
    app = Path(app_dir)
    try:
        outcome = sync_rejoin.resume_interrupted_rejoin(app)
    except Exception as e:
        QMessageBox.critical(None, TITLE, f"An earlier rejoin can't be finished automatically:\n\n{e}\n\n"
                             "Sera will close so nothing is damaged. Contact the office administrator.")
        return "exit"
    if outcome == "rolled_back":
        QMessageBox.warning(None, TITLE, "An interrupted rejoin was undone. This PC still uses its own files.")

    password = None
    if requested and outcome is None:
        if sera_keys.load_office(app) is not None:
            QMessageBox.information(None, TITLE, "This PC already uses the office key.")
            return None
        if not (app / sync_rejoin.MASTER_DB).exists():
            QMessageBox.information(None, TITLE, "This PC has no database to rejoin with. Use \"Join office\" instead.")
            return None
        outcome, password = _run_wizard(app)
        if outcome == "exit":
            return "exit"

    if outcome == "pending_join":
        choice = _ask_pending_join()
        if choice == "undo":
            return _undo(app)
        if choice != "finish":
            return "quit"
        _busy(True)
        try:
            sync_rejoin.resume_download(app, on_progress=_progress)
            outcome = "salvage_pending"
        except Exception as e:
            _busy(False)
            return _download_failed(app, e)
        finally:
            _busy(False)

    if outcome == "salvage_pending":
        _run_salvage(app, password)
    return None
