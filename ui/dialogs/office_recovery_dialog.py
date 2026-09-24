"""
Office recovery dialog (WP P1-6).

Shown when Windows DPAPI cannot decrypt the office data key on start-up.
Allows unlocking via the office master password (up to 5 attempts) or
restoring from a .serakit recovery file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import sera_keys

try:
    import qtawesome as qta
except Exception:
    qta = None


MAX_ATTEMPTS = 5


def _safe_icon(name: str, color: str = "#FFFFFF"):
    if qta:
        try:
            return qta.icon(name, color=color)
        except Exception:
            pass
    return None


class OfficeRecoveryDialog(QDialog):
    """Shown when DPAPI can't decrypt. Prompts for master password or recovery kit restore."""

    def __init__(self, app_dir: str | Path, parent=None):
        super().__init__(parent)
        self.app_dir = Path(app_dir)
        self.recovered_dek: bytes | None = None
        self._attempts = 0

        self.setWindowTitle("Aman Associates — Unlock Office Key")
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header with Material icon
        header = QHBoxLayout()
        header.setSpacing(12)
        icon_lbl = QLabel()
        icon = _safe_icon("mdi.shield-key-outline", color="#2E9B5F")
        if icon:
            icon_lbl.setPixmap(icon.pixmap(32, 32))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Office Key Unlock Required")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("This Windows account can't unlock Sera. Enter the office master password.")
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("font-size: 12px; color: #A0A0A0;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Password Form
        form = QFormLayout()
        form.setSpacing(10)

        pw_row = QHBoxLayout()
        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.Password)
        self.pw_edit.setPlaceholderText("Enter office master password")
        self.pw_edit.returnPressed.connect(self._on_unlock)
        pw_row.addWidget(self.pw_edit)

        self.btn_toggle_pw = QPushButton()
        self.btn_toggle_pw.setFixedSize(32, 28)
        self.btn_toggle_pw.setCursor(Qt.PointingHandCursor)
        eye_icon = _safe_icon("mdi.eye-outline", color="#808080")
        if eye_icon:
            self.btn_toggle_pw.setIcon(eye_icon)
        self.btn_toggle_pw.setToolTip("Show / Hide password")
        self.btn_toggle_pw.clicked.connect(self._toggle_password_visibility)
        pw_row.addWidget(self.btn_toggle_pw)

        form.addRow("Master password:", pw_row)
        layout.addLayout(form)

        # Error / Status label
        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setStyleSheet("color: #FF5252; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.lbl_error)

        # Kit recovery option
        kit_row = QHBoxLayout()
        self.btn_restore_kit = QPushButton("Restore from recovery kit file...")
        kit_icon = _safe_icon("mdi.file-key-outline", color="#4CF9B7")
        if kit_icon:
            self.btn_restore_kit.setIcon(kit_icon)
        self.btn_restore_kit.setCursor(Qt.PointingHandCursor)
        self.btn_restore_kit.clicked.connect(self._on_restore_kit)
        kit_row.addWidget(self.btn_restore_kit)
        kit_row.addStretch()
        layout.addLayout(kit_row)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_exit = QPushButton("Exit")
        self.btn_exit.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_exit)

        self.btn_unlock = QPushButton("Unlock")
        self.btn_unlock.setDefault(True)
        self.btn_unlock.setStyleSheet("background-color: #2E9B5F; color: #FFFFFF; font-weight: 600;")
        self.btn_unlock.clicked.connect(self._on_unlock)
        btn_layout.addWidget(self.btn_unlock)

        layout.addLayout(btn_layout)
        self.pw_edit.setFocus(Qt.OtherFocusReason)

    def _toggle_password_visibility(self):
        if self.pw_edit.echoMode() == QLineEdit.Password:
            self.pw_edit.setEchoMode(QLineEdit.Normal)
            eye_off = _safe_icon("mdi.eye-off-outline", color="#808080")
            if eye_off:
                self.btn_toggle_pw.setIcon(eye_off)
        else:
            self.pw_edit.setEchoMode(QLineEdit.Password)
            eye_on = _safe_icon("mdi.eye-outline", color="#808080")
            if eye_on:
                self.btn_toggle_pw.setIcon(eye_on)

    def _on_unlock(self):
        pwd = self.pw_edit.text()
        if not pwd:
            self.lbl_error.setText("Enter the office master password.")
            return

        try:
            dek = sera_keys.recover_dek(self.app_dir, pwd)
            self.recovered_dek = dek
            try:
                sera_keys.load_dek(self.app_dir)
            except sera_keys.KeyUnavailable:
                QMessageBox.warning(
                    self,
                    "Automatic Unlock Unavailable",
                    "Note: Windows could not save the key for automatic unlocking on this account.\n\n"
                    "You will need to enter the master password again on the next start.",
                )
            self.accept()
        except sera_keys.WrongPassword:
            self._attempts += 1
            rem = MAX_ATTEMPTS - self._attempts
            if rem > 0:
                self.lbl_error.setText(
                    f"Wrong password. ({rem} attempts remaining)\n\n"
                    "This Windows account can't unlock Sera. Enter the office master password."
                )
                self.pw_edit.clear()
            else:
                QMessageBox.critical(
                    self, "Aman Associates — Key Recovery Failed",
                    "Could not recover office key.\nThe maximum number of attempts (5) was exceeded."
                )
                self.reject()
        except sera_keys.KeyUnavailable as e:
            self.lbl_error.setText(f"Recovery file not found or unavailable:\n{e}")
        except sera_keys.KeyFileInvalid as e:
            self.lbl_error.setText(f"The recovery data is invalid or damaged:\n{e}")
        except Exception as e:
            self.lbl_error.setText(f"Failed to recover office key: {e}")

    def _on_restore_kit(self):
        kit_file, _ = QFileDialog.getOpenFileName(
            self,
            "Select Recovery Kit File",
            "",
            "Sera Recovery Kit (*.serakit);;All Files (*.*)",
        )
        if not kit_file:
            return

        try:
            meta = sera_keys.inspect_recovery_kit(kit_file)
        except Exception as e:
            QMessageBox.critical(self, "Invalid Recovery Kit", f"Could not read recovery kit file:\n\n{e}")
            return

        # Prompt for password to unlock this kit file
        pwd = self.pw_edit.text()
        if not pwd:
            prompt_pwd, ok = QInputDialog.getText(
                self,
                "Recovery Kit Password",
                f"Enter master password for \"{meta.get('office_name', 'Office')}\":",
                QLineEdit.Password,
            )
            if not ok or not prompt_pwd:
                return
            pwd = prompt_pwd

        try:
            dek = sera_keys.restore_recovery_kit(self.app_dir, kit_file, pwd)
            self.recovered_dek = dek
            try:
                sera_keys.load_dek(self.app_dir)
                dpapi_saved = True
            except sera_keys.KeyUnavailable:
                dpapi_saved = False

            msg = (
                f"Office key successfully restored for \"{meta.get('office_name', 'Office')}\".\n\n"
                "Sera will now proceed with startup."
            )
            if not dpapi_saved:
                msg += (
                    "\n\nNote: Windows could not save the key for automatic unlocking on this account.\n"
                    "You will need to enter the master password again on the next start."
                )
            QMessageBox.information(
                self,
                "Office Key Restored",
                msg,
            )
            self.accept()
        except sera_keys.WrongPassword:
            self._attempts += 1
            rem = MAX_ATTEMPTS - self._attempts
            if rem > 0:
                self.lbl_error.setText(f"Wrong password for recovery kit file. ({rem} attempts remaining)")
            else:
                QMessageBox.critical(
                    self, "Aman Associates — Key Recovery Failed",
                    "Could not recover office key.\nThe maximum number of attempts (5) was exceeded."
                )
                self.reject()
        except sera_keys.KeyFileInvalid as e:
            QMessageBox.critical(self, "Invalid Recovery Kit", f"Recovery kit validation failed:\n\n{e}")
        except Exception as e:
            QMessageBox.critical(self, "Restore Failed", f"Failed to restore recovery kit:\n\n{e}")
