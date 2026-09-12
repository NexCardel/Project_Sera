"""
ui/components/vsdc_hud_pill.py — Floating Desktop Overlay Indicator for VSDC
=============================================================================
Displays real-time, non-intrusive HUD notifications at the bottom-left of the screen
when VSDC is actively monitoring routes, seeding identities, or capturing datasets.

Characteristics:
- Completely click-through (Qt.WA_TransparentForMouseEvents)
- Non-activating / focus-safe (Qt.WA_ShowWithoutActivating | Qt.WindowDoesNotAcceptFocus)
- Always on top (Qt.WindowStaysOnTopHint | Qt.ToolTip)
- Auto-dismisses within 3.0 seconds with smooth opacity fade
- Strictly uses Google Material Design icons (mdi.*) via QtAwesome
"""

import sys
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QGraphicsOpacityEffect, QApplication
from PySide6.QtGui import QFont, QColor, QScreen

try:
    import qtawesome as qta
except Exception:
    qta = None


class VSDCHudPill(QWidget):
    """
    Ultra-compact, glassmorphic desktop HUD pill at the bottom-left corner of the primary screen.
    """

    # Event configs: (icon_name, accent_color, badge_text)
    EVENT_THEMES = {
        "route": ("mdi.crosshairs-gps", "#58A6FF", "VSDC ROUTE"),
        "identity": ("mdi.account-check", "#3FB950", "VSDC ASSESSEE"),
        "capture": ("mdi.database-check", "#2EA043", "VSDC CAPTURED"),
        "flush": ("mdi.check-all", "#A371F7", "VSDC FLUSHED"),
        "default": ("mdi.radar", "#388BFD", "VSDC ACTIVE"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)

        # Window flags: ToolTip + Frameless + Always On Top + Non-Activating
        self.setWindowFlags(
            Qt.ToolTip
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )

        # Attributes: Transparent for mouse clicks so foreground window never loses focus
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._icon_cache = {}
        self._last_event_signature = ""

        # Opacity animation setup
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)

        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

        # Auto-dismiss timer: disappears within 3 seconds
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self._fade_out)
        self.anim.finished.connect(self._on_fade_finished)

        self._build_ui()
        self.hide()

    def _build_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Main Card Frame
        self.card = QFrame(self)
        self.card.setObjectName("VsdcHudCard")
        self.card.setStyleSheet("""
            QFrame#VsdcHudCard {
                background-color: rgba(22, 27, 34, 0.95);
                border: 1px solid #30363D;
                border-left: 4px solid #58A6FF;
                border-radius: 8px;
            }
        """)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(12, 9, 16, 9)
        card_layout.setSpacing(10)

        # Material Icon
        self.icon_label = QLabel(self.card)
        self.icon_label.setFixedSize(26, 26)
        self.icon_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.icon_label)

        # Content Column
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        # Header Row (Badge + Title)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.badge_label = QLabel("VSDC", self.card)
        self.badge_label.setStyleSheet("""
            color: #58A6FF;
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 0.5px;
        """)
        header_layout.addWidget(self.badge_label)

        self.title_label = QLabel("", self.card)
        self.title_label.setStyleSheet("""
            color: #F0F6FC;
            font-size: 12px;
            font-weight: 600;
        """)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        text_layout.addLayout(header_layout)

        # Subtitle / Details
        self.subtitle_label = QLabel("", self.card)
        self.subtitle_label.setStyleSheet("""
            color: #8B949E;
            font-size: 11px;
        """)
        text_layout.addWidget(self.subtitle_label)

        card_layout.addLayout(text_layout)
        root_layout.addWidget(self.card)

    def _get_icon(self, icon_name: str, color: str):
        cache_key = f"{icon_name}_{color}"
        if cache_key not in self._icon_cache:
            if qta is not None:
                try:
                    self._icon_cache[cache_key] = qta.icon(icon_name, color=color).pixmap(24, 24)
                except Exception:
                    self._icon_cache[cache_key] = None
            else:
                self._icon_cache[cache_key] = None
        return self._icon_cache[cache_key]

    def show_event(self, event_type: str, title: str, subtitle: str = "", duration_ms: int = 2400):
        """
        Displays the HUD pill at the bottom-left corner with the given event details.
        Ensures the indicator disappears completely within 3 seconds.
        """
        # Deduplicate identical consecutive event within short window
        sig = f"{event_type}:{title}:{subtitle}"
        if sig == self._last_event_signature and self.isVisible() and self.opacity_effect.opacity() > 0.5:
            # Refresh timer without jarring re-animation
            self.dismiss_timer.start(duration_ms)
            return
        self._last_event_signature = sig

        theme = self.EVENT_THEMES.get(event_type, self.EVENT_THEMES["default"])
        icon_name, accent_color, badge_text = theme

        # Update styling & accent line
        self.card.setStyleSheet(f"""
            QFrame#VsdcHudCard {{
                background-color: rgba(22, 27, 34, 0.96);
                border: 1px solid #30363D;
                border-left: 4px solid {accent_color};
                border-radius: 8px;
            }}
        """)
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet(f"color: {accent_color}; font-size: 10px; font-weight: bold; letter-spacing: 0.5px;")

        # Update text
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle if subtitle else "")
        self.subtitle_label.setVisible(bool(subtitle))

        # Update Material Icon
        pixmap = self._get_icon(icon_name, accent_color)
        if pixmap:
            self.icon_label.setPixmap(pixmap)
            self.icon_label.show()
        else:
            self.icon_label.hide()

        self.adjustSize()
        self._reposition_bottom_left()

        # Animate Fade-in
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(1.0)
        self.anim.start()

        # Auto-dismiss within 3 seconds total
        self.dismiss_timer.start(duration_ms)

    def _reposition_bottom_left(self):
        """Positions the HUD pill firmly at the bottom-left corner of the active primary screen."""
        screen = QApplication.primaryScreen()
        if not screen:
            return

        avail = screen.availableGeometry()
        margin_x = 24
        margin_y = 24

        x = avail.left() + margin_x
        y = avail.bottom() - self.height() - margin_y
        self.move(QPoint(x, y))

    def _on_fade_finished(self):
        if self.opacity_effect.opacity() == 0.0:
            self.hide()
            self._last_event_signature = ""

    def _fade_out(self):
        """Smoothly fades out and hides."""
        self.anim.stop()
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(0.0)
        self.anim.start()
