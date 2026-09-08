"""
scc_tag_banner.py
-----------------
Floating desktop banner for SCC (Sera Credential Capture / Session Tagging).
Appears in the bottom-right corner of the desktop when an authenticated
portal session (e.g. Income Tax / ITR) starts for a client whose password
is not yet registered in master.db.

Features:
- Google Material Icons exclusively (mdi.*)
- 4 Quick-tag preset combination buttons
- Direct password input box with show/hide toggle
- 20-second auto-dismiss countdown with pause-on-hover
- One-click instant vault persistence
"""

import sys
from PySide6.QtCore import Qt, QTimer, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QFrame, QGraphicsOpacityEffect
)

try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_icon(icon_name: str, color: str = "#FFFFFF") -> QIcon:
    if qta:
        try:
            return qta.icon(icon_name, color=color)
        except Exception:
            pass
    return QIcon()


class SccQuickTagBanner(QWidget):
    """
    Floating, frameless desktop banner for quick password logging on login.
    """
    password_selected = Signal(str, str, int, str, str)
    # (pan, client_name, column_id, password, combo_label)
    dismissed = Signal(str)  # (pan)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._pan = ""
        self._client_name = ""
        self._column_id = 0
        self._portal = "Income Tax"
        self._combos = []
        self._total_duration_ms = 20000  # 20 seconds
        self._remaining_ms = 20000
        self._is_paused = False

        # Opacity effect for smooth fade-in / fade-out
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)

        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._anim.setDuration(220)

        # 100ms interval countdown timer
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(100)
        self._countdown_timer.timeout.connect(self._on_tick)

        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(420)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Root Card Frame
        self.card = QFrame(self)
        self.card.setObjectName("SccCard")
        self.card.setStyleSheet("""
            QFrame#SccCard {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 12px;
            }
        """)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 12)
        card_layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.icon_label = QLabel(self.card)
        self.icon_label.setPixmap(_safe_icon("mdi.shield-key-outline", "#F59E0B").pixmap(22, 22))
        header_layout.addWidget(self.icon_label)

        self.title_label = QLabel("ITR Password Required", self.card)
        self.title_label.setStyleSheet("color: #F8FAFC; font-size: 13px; font-weight: 700;")
        header_layout.addWidget(self.title_label, 1)

        self.btn_close = QPushButton(self.card)
        self.btn_close.setIcon(_safe_icon("mdi.close", "#94A3B8"))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip("Dismiss")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
            }
        """)
        self.btn_close.clicked.connect(self.dismiss)
        header_layout.addWidget(self.btn_close)
        card_layout.addLayout(header_layout)

        # ── Client Info Chip ─────────────────────────────────
        self.chip_frame = QFrame(self.card)
        self.chip_frame.setStyleSheet("""
            QFrame {
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-radius: 6px;
                padding: 4px 8px;
            }
        """)
        chip_layout = QHBoxLayout(self.chip_frame)
        chip_layout.setContentsMargins(6, 4, 6, 4)
        chip_layout.setSpacing(8)

        self.lbl_client_name = QLabel("Client Name", self.chip_frame)
        self.lbl_client_name.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: 600;")
        chip_layout.addWidget(self.lbl_client_name, 1)

        self.lbl_pan = QLabel("PAN: XXXXXXXXXX", self.chip_frame)
        self.lbl_pan.setStyleSheet("color: #94A3B8; font-size: 11px; font-family: monospace; font-weight: bold;")
        chip_layout.addWidget(self.lbl_pan)

        card_layout.addWidget(self.chip_frame)

        # ── Prompt Text ──────────────────────────────────────
        self.prompt_label = QLabel("Which password opened this account?", self.card)
        self.prompt_label.setStyleSheet("color: #CBD5E1; font-size: 11px;")
        card_layout.addWidget(self.prompt_label)

        # ── 4 Combo Buttons Grid ─────────────────────────────
        self.btn_grid_layout = QHBoxLayout()
        self.btn_grid_layout.setSpacing(6)
        self.combo_buttons = []

        for i in range(4):
            btn = QPushButton(f"Combo {i+1}", self.card)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #334155;
                    color: #F1F5F9;
                    border: 1px solid #475569;
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 6px;
                }
                QPushButton:hover {
                    background-color: #0284C7;
                    border-color: #38BDF8;
                }
                QPushButton:pressed {
                    background-color: #0369A1;
                }
            """)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_combo_clicked(idx))
            self.combo_buttons.append(btn)
            self.btn_grid_layout.addWidget(btn)

        card_layout.addLayout(self.btn_grid_layout)

        # ── Direct Input Box + Save Button ───────────────────
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.txt_password = QLineEdit(self.card)
        self.txt_password.setPlaceholderText("Or type / paste password here...")
        self.txt_password.setEchoMode(QLineEdit.Password)
        self.txt_password.setFixedHeight(30)
        self.txt_password.setStyleSheet("""
            QLineEdit {
                background-color: #0F172A;
                color: #FFFFFF;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #38BDF8;
            }
        """)
        self.txt_password.returnPressed.connect(self._on_save_input)
        input_layout.addWidget(self.txt_password, 1)

        # Toggle eye icon
        self.btn_toggle_eye = QPushButton(self.card)
        self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-outline", "#94A3B8"))
        self.btn_toggle_eye.setFixedSize(30, 30)
        self.btn_toggle_eye.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_eye.setToolTip("Show / Hide password")
        self.btn_toggle_eye.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                border: 1px solid #475569;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.btn_toggle_eye.clicked.connect(self._toggle_password_visibility)
        input_layout.addWidget(self.btn_toggle_eye)

        # Save button
        self.btn_save = QPushButton("Save", self.card)
        self.btn_save.setIcon(_safe_icon("mdi.content-save", "#FFFFFF"))
        self.btn_save.setFixedHeight(30)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #10B981;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #059669;
            }
            QPushButton:pressed {
                background-color: #047857;
            }
        """)
        self.btn_save.clicked.connect(self._on_save_input)
        input_layout.addWidget(self.btn_save)

        card_layout.addLayout(input_layout)

        # ── Countdown Progress & Status ──────────────────────
        status_layout = QHBoxLayout()
        status_layout.setSpacing(6)

        self.lbl_timer = QLabel("Auto-dismiss in 20s", self.card)
        self.lbl_timer.setStyleSheet("color: #64748B; font-size: 10px;")
        status_layout.addWidget(self.lbl_timer, 1)

        self.progress_bar = QProgressBar(self.card)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, self._total_duration_ms)
        self.progress_bar.setValue(self._total_duration_ms)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #334155;
                border: none;
                border-radius: 1px;
            }
            QProgressBar::chunk {
                background-color: #38BDF8;
                border-radius: 1px;
            }
        """)
        status_layout.addWidget(self.progress_bar, 1)

        card_layout.addLayout(status_layout)
        main_layout.addWidget(self.card)

    def enterEvent(self, event):
        """Pause timer when user hovers over the banner."""
        super().enterEvent(event)
        self._is_paused = True
        self.lbl_timer.setText("Paused")

    def leaveEvent(self, event):
        """Resume timer when mouse leaves."""
        super().leaveEvent(event)
        self._is_paused = False

    def show_prompt(self, pan: str, client_name: str, column_id: int, portal: str = "Income Tax", combos: list = None):
        """
        Activates the banner on screen for the specified taxpayer.
        """
        self._pan = (pan or "").strip().upper()
        self._client_name = (client_name or "Taxpayer").strip()
        self._column_id = column_id
        self._portal = portal
        self._combos = combos or []

        self.lbl_client_name.setText(self._client_name)
        self.lbl_pan.setText(f"PAN: {self._pan}")
        self.title_label.setText(f"{portal} Password Required")

        # Configure the 4 buttons
        for i in range(4):
            btn = self.combo_buttons[i]
            if i < len(self._combos):
                c = self._combos[i]
                lbl = (c.get("label") or f"Combo {i+1}").strip()
                val = (c.get("value") or "").strip()
                btn.setText(lbl)
                if val:
                    btn.setToolTip(f"Save '{lbl}' ({val})")
                else:
                    btn.setToolTip(f"Set '{lbl}'")
            else:
                btn.setText(f"Combo {i+1}")
                btn.setToolTip("")

        # Reset direct input & styling
        self.txt_password.clear()
        self.txt_password.setEchoMode(QLineEdit.Password)
        self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-outline", "#94A3B8"))

        self.card.setStyleSheet("""
            QFrame#SccCard {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 12px;
            }
        """)

        # Position at bottom-right of primary screen
        self._position_bottom_right()

        # Reset timer
        self._remaining_ms = self._total_duration_ms
        self.progress_bar.setValue(self._remaining_ms)
        self.lbl_timer.setText("Auto-dismiss in 20s")
        self._is_paused = False

        self.show()
        self.raise_()

        # Animate opacity in
        self._anim.stop()
        self._anim.setStartValue(self._opacity_effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

        self._countdown_timer.start()

    def _position_bottom_right(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        margin = 24
        x = geo.x() + geo.width() - self.width() - margin
        y = geo.y() + geo.height() - self.height() - margin
        self.move(x, y)

    def _on_tick(self):
        if self._is_paused:
            return
        self._remaining_ms -= 100
        self.progress_bar.setValue(max(0, self._remaining_ms))
        sec_left = max(0, int(self._remaining_ms / 1000) + 1)
        self.lbl_timer.setText(f"Auto-dismiss in {sec_left}s")

        if self._remaining_ms <= 0:
            self._countdown_timer.stop()
            self.dismiss()

    def _toggle_password_visibility(self):
        if self.txt_password.echoMode() == QLineEdit.Password:
            self.txt_password.setEchoMode(QLineEdit.Normal)
            self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-off-outline", "#38BDF8"))
        else:
            self.txt_password.setEchoMode(QLineEdit.Password)
            self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-outline", "#94A3B8"))

    def _on_combo_clicked(self, idx: int):
        label = f"Combo {idx+1}"
        val = ""
        if idx < len(self._combos):
            label = (self._combos[idx].get("label") or label).strip()
            val = (self._combos[idx].get("value") or "").strip()

        if val:
            # If combo has a configured value, commit immediately!
            self._commit_password(val, label)
        else:
            # If combo is not configured with a static password, put focus in input
            self.txt_password.setFocus()
            self.txt_password.setPlaceholderText(f"Type password for {label}...")

    def _on_save_input(self):
        val = self.txt_password.text().strip()
        if not val:
            return
        self._commit_password(val, "Direct Input")

    def _commit_password(self, password: str, combo_label: str):
        self._countdown_timer.stop()

        # Emit save signal to main application
        self.password_selected.emit(self._pan, self._client_name, self._column_id, password, combo_label)

        # Show success state
        self.icon_label.setPixmap(_safe_icon("mdi.check-circle", "#10B981").pixmap(22, 22))
        self.title_label.setText("✓ Saved to Vault!")
        self.prompt_label.setText(f"Password recorded under {combo_label}")
        self.card.setStyleSheet("""
            QFrame#SccCard {
                background-color: #064E3B;
                border: 1px solid #10B981;
                border-radius: 12px;
            }
        """)

        # Fade out smoothly after 1.2s
        QTimer.singleShot(1200, self.dismiss)

    def dismiss(self):
        self._countdown_timer.stop()
        self._anim.stop()
        self._anim.setStartValue(self._opacity_effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._on_fade_out_finished)
        self._anim.start()

    def _on_fade_out_finished(self):
        try:
            self._anim.finished.disconnect(self._on_fade_out_finished)
        except Exception:
            pass
        self.hide()
        self.dismissed.emit(self._pan)
