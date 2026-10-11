"""
Office recovery dialog (WP P1-6).

Shown when Windows DPAPI cannot decrypt the office data key on start-up.
Allows unlocking via the office master password (up to 5 attempts) or
restoring from a .serakit recovery file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import sera_keys
from ui.components.kit import Banner, DialogFrame, FieldLabel, IconButton, Input, LabelledAction
from ui.utils.tokens import TOKENS


MAX_ATTEMPTS = 5


def _eye_icon(shown: bool):
    try:
        import qtawesome as qta
        return qta.icon("mdi.eye-off-outline" if shown else "mdi.eye-outline", color=TOKENS["muted"])
    except Exception:
        return QIcon()


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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Office key unlock required",
                            "This Windows account can't unlock Sera. Enter the office master password.",
                            self, icon="mdi.shield-key-outline")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        self.pw_edit = Input(placeholder="Enter office master password")
        self.pw_edit.setEchoMode(QLineEdit.Password)
        self.pw_edit.setAccessibleName("Master password")
        self.pw_edit.returnPressed.connect(self._on_unlock)
        self.btn_toggle_pw = IconButton("mdi.eye-outline", "Show / Hide password", sm=True)
        self.btn_toggle_pw.setIcon(_eye_icon(False))
        self.btn_toggle_pw.clicked.connect(self._toggle_password_visibility)
        self.pw_edit.add_trailing(self.btn_toggle_pw)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(TOKENS["s_1"])
        lay.addWidget(FieldLabel("Master password", for_widget=self.pw_edit))
        lay.addWidget(self.pw_edit)
        frame.add_widget(box)

        self.lbl_error = Banner("", "r")
        self.lbl_error.hide()
        frame.add_widget(self.lbl_error)

        self.btn_restore_kit = LabelledAction("Restore from recovery kit file…", icon="mdi.file-key-outline",
                                              tooltip="Unlock with a .serakit recovery kit instead")
        self.btn_restore_kit.clicked.connect(self._on_restore_kit)
        frame.add_widget(self.btn_restore_kit)

        self.btn_exit = frame.add_action("Exit")
        self.btn_exit.clicked.connect(self.reject)

        self.btn_unlock = frame.add_action("Unlock", variant="primary", icon="mdi.lock-open-outline")
        self.btn_unlock.setDefault(True)
        self.btn_unlock.clicked.connect(self._on_unlock)

        self.pw_edit.setFocus(Qt.OtherFocusReason)

    def _show_error(self, message: str):
        self.lbl_error.set_text(message)
        self.lbl_error.show()

    def _toggle_password_visibility(self):
        shown = self.pw_edit.echoMode() == QLineEdit.Password
        self.pw_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.btn_toggle_pw.setIcon(_eye_icon(shown))

    def _on_unlock(self):
        pwd = self.pw_edit.text()
        if not pwd:
            self._show_error("Enter the office master password.")
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
                self._show_error(
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
            self._show_error(f"Recovery file not found or unavailable:\n{e}")
        except sera_keys.KeyFileInvalid as e:
            self._show_error(f"The recovery data is invalid or damaged:\n{e}")
        except Exception as e:
            self._show_error(f"Failed to recover office key: {e}")

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
                self._show_error(f"Wrong password for recovery kit file. ({rem} attempts remaining)")
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
