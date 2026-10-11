"""
Change office master password dialog & recovery kit export flow (WP P1-6).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

import sera_keys
from ui.components.kit import MessageBox, Banner, DialogFrame, FieldLabel, Input
from ui.utils.tokens import TOKENS


def _field(label: FieldLabel, widget: QWidget) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(TOKENS["s_1"])
    lay.addWidget(label)
    lay.addWidget(widget)
    return box


class ChangeMasterPasswordDialog(QDialog):
    """Admin dialog to change the office master password without touching database data."""
    password_changed = Signal()

    def __init__(self, app_dir: str | Path, parent=None):
        super().__init__(parent)
        self.app_dir = Path(app_dir)

        self.setWindowTitle("Change Office Master Password")
        self.setMinimumWidth(540)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Change master password",
                            "Update the password used to recover the office key and protect recovery kits.",
                            self, icon="mdi.lock-reset")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        self.current_pw = Input(placeholder="Current master password")
        self.current_pw.setEchoMode(QLineEdit.Password)
        self.current_pw.setAccessibleName("Current password")
        frame.add_widget(_field(FieldLabel("Current password", for_widget=self.current_pw), self.current_pw))

        self.new_pw = Input(placeholder="New master password (min 8 characters)")
        self.new_pw.setEchoMode(QLineEdit.Password)
        self.new_pw.setAccessibleName("New password")
        frame.add_widget(_field(FieldLabel("New password", for_widget=self.new_pw), self.new_pw))

        self.confirm_pw = Input(placeholder="Confirm new master password")
        self.confirm_pw.setEchoMode(QLineEdit.Password)
        self.confirm_pw.setAccessibleName("Confirm new password")
        frame.add_widget(_field(FieldLabel("Confirm new password", for_widget=self.confirm_pw), self.confirm_pw))

        frame.add_widget(Banner("The database is not modified. Only the key recovery file is re-encrypted.", "i"))

        self.lbl_error = Banner("", "r")
        self.lbl_error.hide()
        frame.add_widget(self.lbl_error)

        self.btn_cancel = frame.add_action("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = frame.add_action("Change password", variant="primary", icon="mdi.check")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)

        self.current_pw.setFocus(Qt.OtherFocusReason)

    def _show_error(self, message: str):
        self.lbl_error.set_text(message)
        self.lbl_error.show()

    def _on_save(self):
        curr = self.current_pw.text()
        new = self.new_pw.text().strip()
        confirm = self.confirm_pw.text().strip()

        if not curr:
            self._show_error("Enter the current master password.")
            return

        if not new:
            self._show_error("Enter a new master password.")
            return

        if new != confirm:
            self._show_error("New passwords do not match.")
            return

        val_err = sera_keys.validate_master_password(new)
        if val_err:
            self._show_error(val_err)
            return

        if curr == new:
            self._show_error("New password must be different from current password.")
            return

        try:
            sera_keys.change_master_password(self.app_dir, curr, new)
            MessageBox.information(
                self,
                "Password Changed",
                "The office master password has been successfully updated.\n\n"
                "Remember to store your updated password securely or export a new recovery kit.",
            )
            self.password_changed.emit()
            self.accept()
        except sera_keys.WrongPassword:
            self._show_error("Current master password is incorrect.")
            self.current_pw.clear()
            self.current_pw.setFocus()
        except ValueError as ve:
            self._show_error(str(ve))
        except Exception as e:
            self._show_error(f"Failed to change master password: {e}")


def export_recovery_kit_flow(parent, app_dir: str | Path, password: str | None = None, office_name: str | None = None) -> bool:
    """Guided flow to export a .serakit file. Prompts for password if not provided."""
    app_path = Path(app_dir)
    try:
        office = sera_keys.load_office(app_path)
    except Exception as e:
        MessageBox.critical(parent, "Export Error", f"Cannot load office configuration:\n\n{e}")
        return False

    if office is None:
        MessageBox.warning(parent, "Export Not Available", "This PC is not in office mode. No office key exists to export.")
        return False

    name = office_name or office.office_name or "office"

    # Prompt for password if not provided
    pwd = password
    if not pwd:
        entered_pwd, ok = QInputDialog.getText(
            parent,
            "Office Master Password",
            f"Enter office master password for \"{name}\" to export recovery kit:",
            QLineEdit.Password,
        )
        if not ok or not entered_pwd:
            return False
        pwd = entered_pwd

    # Choose save path
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name.strip())
    default_filename = f"{safe_name}_recovery_kit.serakit"

    save_path, _ = QFileDialog.getSaveFileName(
        parent,
        "Save Sera Recovery Kit to USB",
        default_filename,
        "Sera Recovery Kit (*.serakit);;All Files (*.*)",
    )
    if not save_path:
        return False

    try:
        exported = sera_keys.export_recovery_kit(app_path, pwd, save_path)
        MessageBox.information(
            parent,
            "Recovery Kit Exported",
            f"Sera recovery kit successfully exported to:\n\n{exported}\n\n"
            "Store this file safely on a secure USB drive.",
        )
        return True
    except sera_keys.WrongPassword:
        MessageBox.critical(parent, "Export Failed", "Incorrect master password. Recovery kit was not exported.")
        return False
    except Exception as e:
        MessageBox.critical(parent, "Export Failed", f"Failed to export recovery kit:\n\n{e}")
        return False
