"""
manual_credentials_dialog.py
-----------------------------
Small popup used for "manual" portals.

Keyboard-first workflow (no mouse needed once the dialog has focus, which it
already gets via .raise_()/.activateWindow() in client_detail_window.py):

    Enter  -- "smart" step: copies User ID first, then (on the next Enter)
              copies Password, then (on the next Enter) closes the dialog.
              This is the only key most people need: Enter, Alt+Tab, Ctrl+V,
              Alt+Tab, Enter, Alt+Tab, Ctrl+V, Alt+Tab, Enter -- done.
    Ctrl+1 -- copy User ID directly, any time, without advancing the step.
    Ctrl+2 -- copy Password directly, any time, without advancing the step.
    Esc    -- close.

Mouse clicks on the Copy/Show/Close buttons still work exactly as before --
this only adds keyboard paths on top, it doesn't remove anything.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class _CredentialRow(QHBoxLayout):
    def __init__(self, label_text: str, value: str, secret: bool, clear_timeout_sec: int = 30):
        super().__init__()
        self._value = value
        self._clear_timeout_sec = clear_timeout_sec

        self.label = QLabel(label_text)
        self.label.setMinimumWidth(90)
        self.addWidget(self.label)

        self.field = QLineEdit(value)
        self.field.setReadOnly(True)
        self.field.setFocusPolicy(Qt.NoFocus)  # keep Tab/Enter free for dialog-level shortcuts
        self.secret = secret
        if secret:
            self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.addWidget(self.field, stretch=1)

        if secret:
            self.reveal_btn = QPushButton("Show")
            self.reveal_btn.setCheckable(True)
            self.reveal_btn.setFixedWidth(56)
            self.reveal_btn.setAutoDefault(False)
            self.reveal_btn.setDefault(False)
            self.reveal_btn.toggled.connect(self._on_reveal_toggled)
            self.addWidget(self.reveal_btn)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setFixedWidth(56)
        self.copy_btn.setAutoDefault(False)
        self.copy_btn.setDefault(False)
        self.copy_btn.clicked.connect(self.copy)
        self.addWidget(self.copy_btn)

    def _on_reveal_toggled(self, checked: bool):
        self.field.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.reveal_btn.setText("Hide" if checked else "Show")

    def copy(self):
        val = self._value
        QApplication.clipboard().setText(val)
        if self.secret:
            timeout_ms = self._clear_timeout_sec * 1000
            QTimer.singleShot(timeout_ms, lambda: self._clear_clipboard_if_matches(val))

    def _clear_clipboard_if_matches(self, val: str):
        try:
            clipboard = QApplication.clipboard()
            if clipboard.text() == val:
                clipboard.clear()
        except Exception:
            pass


class ManualCredentialsDialog(QDialog):
    # Flash the "copied" confirmation for this long before it fades back to the hint text.
    STATUS_FLASH_MS = 1400

    def __init__(self, portal: str, user_id: str, password: str, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(f"{portal} — Manual Login")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumWidth(420)

        timeout_sec = 30
        if db:
            try:
                timeout_sec = int(db.get_setting("clipboard_clear_seconds", "30"))
            except Exception:
                pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        info = QLabel(
            f"{portal}'s portal blocks automated browsers, so it opened in "
            f"your regular browser instead."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._user_row = _CredentialRow("User ID:", user_id, secret=False, clear_timeout_sec=timeout_sec)
        layout.addLayout(self._user_row)

        self._pass_row = _CredentialRow("Password:", password, secret=True, clear_timeout_sec=timeout_sec)
        layout.addLayout(self._pass_row)

        self._status_label = QLabel()
        self._status_label.setProperty("class", "SuccessText")
        layout.addWidget(self._status_label)

        hint = QLabel(
            "Keyboard: press <b>Enter</b> to copy User ID, Alt+Tab to the browser and "
            "paste, Alt+Tab back and press <b>Enter</b> again for Password, then "
            "<b>Enter</b> once more to close. Or use <b>Ctrl+1</b> / <b>Ctrl+2</b> to "
            "copy either field directly, and <b>Esc</b> to close."
        )
        hint.setWordWrap(True)
        hint.setProperty("class", "HintText")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)

        # "Smart Enter" progress: 0 = copy User ID next, 1 = copy Password next, 2 = close next.
        self._step = 0
        self._set_status("Ready — press Enter to copy the User ID.", flash=False)

    def _set_status(self, text: str, flash: bool = True):
        self._status_label.setText(text)
        if flash:
            self._status_timer.start(self.STATUS_FLASH_MS)

    def _clear_status(self):
        hints = [
            "Press Enter to copy the User ID.",
            "Press Enter to copy the Password.",
            "Press Enter to close.",
        ]
        self._status_label.setText(hints[min(self._step, 2)])

    def _copy_user(self):
        self._user_row.copy()
        self._set_status("✓ User ID copied — switch and paste (Alt+Tab, Ctrl+V).")

    def _copy_password(self):
        self._pass_row.copy()
        self._set_status("✓ Password copied — switch and paste (Alt+Tab, Ctrl+V).")

    def _advance(self):
        """Enter with no modifier: copy User ID, then Password, then close."""
        if self._step == 0:
            self._copy_user()
            self._step = 1
        elif self._step == 1:
            self._copy_password()
            self._step = 2
        else:
            self.close()

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.NoModifier:
            self._advance()
            event.accept()
            return

        if mods & Qt.ControlModifier and key == Qt.Key_1:
            self._copy_user()
            event.accept()
            return

        if mods & Qt.ControlModifier and key == Qt.Key_2:
            self._copy_password()
            event.accept()
            return

        if key == Qt.Key_Escape:
            self.close()
            event.accept()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._pass_row.field.setEchoMode(QLineEdit.EchoMode.Password)
        super().closeEvent(event)
