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
    QLineEdit, QProgressBar, QFrame, QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect
)

try:
    import qtawesome as qta
except Exception:
    qta = None


# ── Sera Design System Palette ────────────────────────────────────────────────
_BG_CARD        = "#171717"
_BG_CHIP        = "#141414"
_BG_INPUT       = "#141414"
_BG_BUTTON      = "#202020"
_BG_HOVER       = "#282828"
_BORDER_CARD    = "#2C2C2C"
_BORDER_INNER   = "#242424"
_BORDER_CTRL    = "#333333"

_TEXT_PRI       = "#F8FAFC"
_TEXT_MUTED     = "#8E8D88"
_TEXT_SEC       = "#CBD5E1"

_ACCENT         = "#2E9B5F"  # Canonical Sera Emerald
_ACCENT_HOVER   = "#34B76D"
_ACCENT_LIGHT   = "#4CF9B7"  # Mint / Token Cyan
_ACCENT_PRESSED = "#237A4B"

_SUCCESS_BG     = "#15261D"
_SUCCESS_BORDER = "#2E7D32"


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
    Fully integrated into the Sera design system.
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

        # Opacity effect & animation for window
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)

        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        # 100ms interval countdown timer
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(100)
        self._countdown_timer.timeout.connect(self._on_tick)

        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(420)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── Root Card Frame ───────────────────────────────────
        self.card = QFrame(self)
        self.card.setObjectName("SccCard")
        self.card.setStyleSheet(f"""
            QFrame#SccCard {{
                background-color: {_BG_CARD};
                border: 1px solid {_BORDER_CARD};
                border-radius: 10px;
            }}
        """)

        # Soft floating elevation drop shadow
        self._shadow = QGraphicsDropShadowEffect(self.card)
        self._shadow.setBlurRadius(24)
        self._shadow.setColor(QColor(0, 0, 0, 160))
        self._shadow.setOffset(0, 6)
        self.card.setGraphicsEffect(self._shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)

        # Emerald Icon Container
        self.icon_frame = QFrame(self.card)
        self.icon_frame.setFixedSize(32, 32)
        self.icon_frame.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(46, 155, 95, 0.15);
                border: 1px solid rgba(76, 249, 183, 0.28);
                border-radius: 6px;
            }}
        """)
        icon_layout = QVBoxLayout(self.icon_frame)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setAlignment(Qt.AlignCenter)

        self.icon_label = QLabel(self.icon_frame)
        self.icon_label.setPixmap(_safe_icon("mdi.shield-key-outline", _ACCENT_LIGHT).pixmap(18, 18))
        icon_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.icon_frame)

        # Title + Section Label
        title_vbox = QVBoxLayout()
        title_vbox.setContentsMargins(0, 0, 0, 0)
        title_vbox.setSpacing(1)

        self.subtitle_label = QLabel("SERA CREDENTIAL CAPTURE", self.card)
        self.subtitle_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;")
        title_vbox.addWidget(self.subtitle_label)

        self.title_label = QLabel("ITR Password Required", self.card)
        self.title_label.setStyleSheet(f"color: {_TEXT_PRI}; font-size: 13px; font-weight: 600;")
        title_vbox.addWidget(self.title_label)
        header_layout.addLayout(title_vbox, 1)

        # Ghost Close Button
        self.btn_close = QPushButton(self.card)
        self.btn_close.setIcon(_safe_icon("mdi.close", _TEXT_MUTED))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip("Dismiss (Auto-dismisses in 20s)")
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #262626;
                border-color: #333333;
            }}
            QPushButton:pressed {{
                background-color: #1A1A1A;
            }}
        """)
        self.btn_close.clicked.connect(self.dismiss)
        header_layout.addWidget(self.btn_close)
        card_layout.addLayout(header_layout)

        # ── Client Info Chip ─────────────────────────────────
        self.chip_frame = QFrame(self.card)
        self.chip_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {_BG_CHIP};
                border: 1px solid {_BORDER_INNER};
                border-radius: 6px;
            }}
        """)
        chip_layout = QHBoxLayout(self.chip_frame)
        chip_layout.setContentsMargins(8, 6, 8, 6)
        chip_layout.setSpacing(8)

        self.chip_icon = QLabel(self.chip_frame)
        self.chip_icon.setPixmap(_safe_icon("mdi.card-account-details-outline", _TEXT_MUTED).pixmap(16, 16))
        chip_layout.addWidget(self.chip_icon)

        self.lbl_client_name = QLabel("Client Name", self.chip_frame)
        self.lbl_client_name.setStyleSheet(f"color: {_TEXT_PRI}; font-size: 12px; font-weight: 600;")
        chip_layout.addWidget(self.lbl_client_name, 1)

        self.lbl_pan = QLabel("PAN: XXXXXXXXXX", self.chip_frame)
        self.lbl_pan.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(46, 155, 95, 0.15);
                border: 1px solid rgba(76, 249, 183, 0.3);
                color: {_ACCENT_LIGHT};
                font-size: 11px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-weight: 700;
                padding: 2px 7px;
                border-radius: 4px;
            }}
        """)
        chip_layout.addWidget(self.lbl_pan)
        card_layout.addWidget(self.chip_frame)

        # ── Prompt Text ──────────────────────────────────────
        self.prompt_label = QLabel("Select working preset or enter custom password:", self.card)
        self.prompt_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: 500;")
        card_layout.addWidget(self.prompt_label)

        # ── 4 Combo Buttons Grid ─────────────────────────────
        self.btn_grid_layout = QHBoxLayout()
        self.btn_grid_layout.setSpacing(6)
        self.combo_buttons = []

        for i in range(4):
            btn = QPushButton(f"Combo {i+1}", self.card)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_BG_BUTTON};
                    color: {_TEXT_PRI};
                    border: 1px solid {_BORDER_CTRL};
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 8px;
                }}
                QPushButton:hover {{
                    background-color: {_BG_HOVER};
                    border: 1px solid {_ACCENT};
                    color: {_ACCENT_LIGHT};
                }}
                QPushButton:pressed {{
                    background-color: {_BG_CHIP};
                    border: 1px solid {_ACCENT_PRESSED};
                }}
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
        self.txt_password.setFixedHeight(32)
        self.txt_password.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_BG_INPUT};
                color: {_TEXT_PRI};
                border: 1px solid {_BORDER_CTRL};
                border-radius: 6px;
                padding: 0 10px;
                font-size: 11px;
                selection-background-color: {_ACCENT};
                selection-color: #FFFFFF;
            }}
            QLineEdit:focus {{
                border: 1.5px solid {_ACCENT};
            }}
        """)
        self.txt_password.returnPressed.connect(self._on_save_input)
        input_layout.addWidget(self.txt_password, 1)

        # Toggle eye icon
        self.btn_toggle_eye = QPushButton(self.card)
        self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-outline", _TEXT_MUTED))
        self.btn_toggle_eye.setFixedSize(32, 32)
        self.btn_toggle_eye.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_eye.setToolTip("Show / Hide password")
        self.btn_toggle_eye.setStyleSheet(f"""
            QPushButton {{
                background-color: {_BG_BUTTON};
                border: 1px solid {_BORDER_CTRL};
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {_BG_HOVER};
                border: 1px solid {_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {_BG_CHIP};
            }}
        """)
        self.btn_toggle_eye.clicked.connect(self._toggle_password_visibility)
        input_layout.addWidget(self.btn_toggle_eye)

        # Save button
        self.btn_save = QPushButton("Save", self.card)
        self.btn_save.setIcon(_safe_icon("mdi.check", "#FFFFFF"))
        self.btn_save.setFixedHeight(32)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {_ACCENT};
                border: 1px solid {_ACCENT_HOVER};
                border-radius: 6px;
                color: #FFFFFF;
                padding: 0 14px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: {_ACCENT_HOVER};
                border-color: {_ACCENT_LIGHT};
            }}
            QPushButton:pressed {{
                background-color: {_ACCENT_PRESSED};
            }}
        """)
        self.btn_save.clicked.connect(self._on_save_input)
        input_layout.addWidget(self.btn_save)

        card_layout.addLayout(input_layout)

        # ── Countdown Progress & Status ──────────────────────
        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        self.lbl_timer = QLabel("Auto-dismiss in 20s", self.card)
        self.lbl_timer.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        status_layout.addWidget(self.lbl_timer, 1)

        self.progress_bar = QProgressBar(self.card)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, self._total_duration_ms)
        self.progress_bar.setValue(self._total_duration_ms)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #242424;
                border: none;
                border-radius: 1.5px;
            }}
            QProgressBar::chunk {{
                background-color: {_ACCENT};
                border-radius: 1.5px;
            }}
        """)
        status_layout.addWidget(self.progress_bar, 1)

        card_layout.addLayout(status_layout)
        main_layout.addWidget(self.card)

    def enterEvent(self, event):
        """Pause timer when user hovers over the banner."""
        super().enterEvent(event)
        self._is_paused = True
        self.lbl_timer.setText("Paused (mouse over)")
        self.lbl_timer.setStyleSheet(f"color: {_ACCENT_LIGHT}; font-size: 11px; font-weight: 600;")

    def leaveEvent(self, event):
        """Resume timer when mouse leaves."""
        super().leaveEvent(event)
        self._is_paused = False
        sec_left = max(0, int(self._remaining_ms / 1000) + 1)
        self.lbl_timer.setText(f"Auto-dismiss in {sec_left}s")
        self.lbl_timer.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: normal;")

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
        self.subtitle_label.setText("SERA CREDENTIAL CAPTURE")
        self.title_label.setText(f"{portal} Password Required")
        self.title_label.setStyleSheet(f"color: {_TEXT_PRI}; font-size: 13px; font-weight: 600;")
        self.prompt_label.setText("Select working preset or enter custom password:")
        self.prompt_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: 500;")
        self.icon_label.setPixmap(_safe_icon("mdi.shield-key-outline", _ACCENT_LIGHT).pixmap(18, 18))

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
        self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-outline", _TEXT_MUTED))

        self.card.setStyleSheet(f"""
            QFrame#SccCard {{
                background-color: {_BG_CARD};
                border: 1px solid {_BORDER_CARD};
                border-radius: 10px;
            }}
        """)

        # Position at bottom-right of primary screen
        self._position_bottom_right()

        # Reset timer
        self._remaining_ms = self._total_duration_ms
        self.progress_bar.setValue(self._remaining_ms)
        self.lbl_timer.setText("Auto-dismiss in 20s")
        self.lbl_timer.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
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
            self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-off-outline", _ACCENT_LIGHT))
        else:
            self.txt_password.setEchoMode(QLineEdit.Password)
            self.btn_toggle_eye.setIcon(_safe_icon("mdi.eye-outline", _TEXT_MUTED))

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

        # Show Sera emerald success confirmation
        self.icon_label.setPixmap(_safe_icon("mdi.check-circle", _ACCENT_LIGHT).pixmap(18, 18))
        self.title_label.setText("✓ Password Verified & Saved!")
        self.title_label.setStyleSheet(f"color: {_ACCENT_LIGHT}; font-size: 13px; font-weight: 700;")
        self.subtitle_label.setText("SERA VAULT UPDATED")
        self.prompt_label.setText(f"Recorded under {combo_label} • Notes updated")
        self.prompt_label.setStyleSheet(f"color: #A5D6A7; font-size: 11px; font-weight: 500;")
        self.card.setStyleSheet(f"""
            QFrame#SccCard {{
                background-color: {_SUCCESS_BG};
                border: 1px solid {_SUCCESS_BORDER};
                border-radius: 10px;
            }}
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
