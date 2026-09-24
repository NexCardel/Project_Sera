"""
Change office master password dialog & recovery kit export flow (WP P1-6).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
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


def _safe_icon(name: str, color: str = "#FFFFFF"):
    if qta:
        try:
            return qta.icon(name, color=color)
        except Exception:
            pass
    return None


class ChangeMasterPasswordDialog(QDialog):
    """Admin dialog to change the office master password without touching database data."""
    password_changed = Signal()

    def __init__(self, app_dir: str | Path, parent=None):
        super().__init__(parent)
        self.app_dir = Path(app_dir)

        self.setWindowTitle("Change Office Master Password")
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header
        header = QHBoxLayout()
        header.setSpacing(12)
        icon_lbl = QLabel()
        icon = _safe_icon("mdi.lock-reset", color="#4CF9B7")
        if icon:
            icon_lbl.setPixmap(icon.pixmap(28, 28))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Change Master Password")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Update the password used to recover the office key and protect recovery kits.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #A0A0A0;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Form
        form = QFormLayout()
        form.setSpacing(10)

        self.current_pw = QLineEdit()
        self.current_pw.setEchoMode(QLineEdit.Password)
        self.current_pw.setPlaceholderText("Current master password")
        form.addRow("Current password:", self.current_pw)

        self.new_pw = QLineEdit()
        self.new_pw.setEchoMode(QLineEdit.Password)
        self.new_pw.setPlaceholderText("New master password (min 8 characters)")
        form.addRow("New password:", self.new_pw)

        self.confirm_pw = QLineEdit()
        self.confirm_pw.setEchoMode(QLineEdit.Password)
        self.confirm_pw.setPlaceholderText("Confirm new master password")
        form.addRow("Confirm new password:", self.confirm_pw)

        layout.addLayout(form)

        info_lbl = QLabel("Note: The database is not modified. Only the key recovery file is re-encrypted.")
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("font-size: 11px; color: #808080;")
        layout.addWidget(info_lbl)

        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setStyleSheet("color: #FF5252; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.lbl_error)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Change Password")
        self.btn_save.setDefault(True)
        self.btn_save.setStyleSheet("background-color: #2E9B5F; color: #FFFFFF; font-weight: 600;")
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)
        self.current_pw.setFocus(Qt.OtherFocusReason)

    def _on_save(self):
        curr = self.current_pw.text()
        new = self.new_pw.text().strip()
        confirm = self.confirm_pw.text().strip()

        if not curr:
            self.lbl_error.setText("Enter the current master password.")
            return

        if not new:
            self.lbl_error.setText("Enter a new master password.")
            return

        if new != confirm:
            self.lbl_error.setText("New passwords do not match.")
            return

        val_err = sera_keys.validate_master_password(new)
        if val_err:
            self.lbl_error.setText(val_err)
            return

        if curr == new:
            self.lbl_error.setText("New password must be different from current password.")
            return

        try:
            sera_keys.change_master_password(self.app_dir, curr, new)
            QMessageBox.information(
                self,
                "Password Changed",
                "The office master password has been successfully updated.\n\n"
                "Remember to store your updated password securely or export a new recovery kit.",
            )
            self.password_changed.emit()
            self.accept()
        except sera_keys.WrongPassword:
            self.lbl_error.setText("Current master password is incorrect.")
            self.current_pw.clear()
            self.current_pw.setFocus()
        except ValueError as ve:
            self.lbl_error.setText(str(ve))
        except Exception as e:
            self.lbl_error.setText(f"Failed to change master password: {e}")


def export_recovery_kit_flow(parent, app_dir: str | Path, password: str | None = None, office_name: str | None = None) -> bool:
    """Guided flow to export a .serakit file. Prompts for password if not provided."""
    app_path = Path(app_dir)
    try:
        office = sera_keys.load_office(app_path)
    except Exception as e:
        QMessageBox.critical(parent, "Export Error", f"Cannot load office configuration:\n\n{e}")
        return False

    if office is None:
        QMessageBox.warning(parent, "Export Not Available", "This PC is not in office mode. No office key exists to export.")
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
        QMessageBox.information(
            parent,
            "Recovery Kit Exported",
            f"Sera recovery kit successfully exported to:\n\n{exported}\n\n"
            "Store this file safely on a secure USB drive.",
        )
        return True
    except sera_keys.WrongPassword:
        QMessageBox.critical(parent, "Export Failed", "Incorrect master password. Recovery kit was not exported.")
        return False
    except Exception as e:
        QMessageBox.critical(parent, "Export Failed", f"Failed to export recovery kit:\n\n{e}")
        return False
