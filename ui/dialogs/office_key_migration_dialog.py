"""Step 1 of the office-key conversion (WP P1-4): current password, office name, master password."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

import sync_migrate

MAX_ATTEMPTS = 5


class OfficeKeyMigrationDialog(QDialog):
    def __init__(self, verify_current_password, parent=None):
        super().__init__(parent)
        self._verify = verify_current_password
        self._attempts = 0
        self.details = None
        self.setWindowTitle("Convert to Office Key (admin PC only)")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "This converts this PC's database to an office key, which other PCs will receive when "
            "they join the office. Do this only on the PC with the most complete database.\n\n"
            "A backup of the current files is made first. If anything goes wrong, nothing is changed."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.current_pw = QLineEdit()
        self.current_pw.setEchoMode(QLineEdit.Password)
        form.addRow("Current master password:", self.current_pw)

        self.office_name = QLineEdit()
        self.office_name.setMaxLength(sync_migrate.MAX_OFFICE_NAME_LEN)
        form.addRow("Office name:", self.office_name)

        self.keep_pw = QCheckBox("Keep the current master password")
        self.keep_pw.setChecked(True)
        self.keep_pw.toggled.connect(self._on_keep_toggled)
        form.addRow("", self.keep_pw)

        self.new_pw = QLineEdit()
        self.new_pw.setEchoMode(QLineEdit.Password)
        form.addRow("New master password:", self.new_pw)
        self.new_pw_confirm = QLineEdit()
        self.new_pw_confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm new password:", self.new_pw_confirm)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #FF6B6B;")
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Convert")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_keep_toggled(True)
        self.current_pw.setFocus(Qt.OtherFocusReason)

    def _on_keep_toggled(self, keep: bool):
        self.new_pw.setEnabled(not keep)
        self.new_pw_confirm.setEnabled(not keep)

    def _on_accept(self):
        current = self.current_pw.text()
        name = self.office_name.text().strip()
        if not current:
            self.error.setText("Enter the current master password.")
            return
        if not name:
            self.error.setText("Enter an office name.")
            return
        try:
            ok = self._verify(current)
        except Exception as e:
            # Locked or missing file: not a wrong password, so it doesn't use up an attempt.
            self.error.setText("Can't check the password right now: %s" % e)
            return
        if not ok:
            self._attempts += 1
            if self._attempts >= MAX_ATTEMPTS:
                self.reject()
                return
            self.error.setText("Wrong password (%d attempts left)." % (MAX_ATTEMPTS - self._attempts))
            self.current_pw.clear()
            return

        if self.keep_pw.isChecked():
            if current.lower() == sync_migrate.DEFAULT_PASSWORD:
                self.error.setText("The current password is the default 'admin123'. Choose a new master password.")
                self.keep_pw.setChecked(False)
                return
            new_password = None
        else:
            new_password = self.new_pw.text().strip()
            err = sync_migrate.validate_new_password(new_password)
            if err:
                self.error.setText(err)
                return
            if new_password != self.new_pw_confirm.text().strip():
                self.error.setText("The new passwords don't match.")
                return

        self.details = {"legacy_password": current, "office_name": name, "new_password": new_password}
        self.accept()
