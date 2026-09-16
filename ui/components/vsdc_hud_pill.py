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

import re
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
    Compact, glassmorphic desktop HUD pill anchored at the bottom-left corner
    of the primary display screen. Matches the exact aesthetic of sdc_toast.js.
    """

    # Event configs: (icon_name, accent_color, badge_text, badge_bg, badge_border)
    EVENT_THEMES = {
        # IN Boundary: Post-login / assessee identified
        "start": (
            "mdi.login-variant",
            "#4CF9B7",
            "SESSION START",
            "rgba(76, 249, 183, 0.15)",
            "rgba(76, 249, 183, 0.35)",
        ),
        # Form Metadata table captured (GST table / ITR details)
        "capture": (
            "mdi.file-document-outline",
            "#39FF14",
            "FORM CAPTURED",
            "rgba(57, 255, 20, 0.15)",
            "rgba(57, 255, 20, 0.35)",
        ),
        # Terminal Filing submission / ARN milestone
        "submit": (
            "mdi.check-decagram",
            "#39FF14",
            "SUBMITTED",
            "rgba(57, 255, 20, 0.20)",
            "rgba(57, 255, 20, 0.45)",
        ),
        # In-place amendment or legal name refinement
        "update": (
            "mdi.update",
            "#388BFD",
            "UPDATED",
            "rgba(56, 139, 253, 0.20)",
            "rgba(56, 139, 253, 0.40)",
        ),
        # OUT Boundary: Logout / Timeout / Context switch
        "logout": (
            "mdi.logout-variant",
            "#8B949E",
            "SESSION END",
            "rgba(139, 148, 158, 0.15)",
            "rgba(139, 148, 158, 0.30)",
        ),
        # Backward compatibility aliases
        "identity": (
            "mdi.account-check",
            "#4CF9B7",
            "SESSION START",
            "rgba(76, 249, 183, 0.15)",
            "rgba(76, 249, 183, 0.35)",
        ),
        "flush": (
            "mdi.cloud-check",
            "#39FF14",
            "FILED",
            "rgba(57, 255, 20, 0.20)",
            "rgba(57, 255, 20, 0.45)",
        ),
        "default": (
            "mdi.radar",
            "#388BFD",
            "VSDC ACTIVE",
            "rgba(56, 139, 253, 0.20)",
            "rgba(56, 139, 253, 0.40)",
        ),
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

        # Opacity animation setup (smooth cubic ease)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)

        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.anim.setDuration(160)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

        # Auto-dismiss timer: 1.8s default (snappy like SDC toast)
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

        # Main Card Frame (matches .sera-toast-card in sdc_toast.js)
        self.card = QFrame(self)
        self.card.setObjectName("VsdcHudCard")
        self.card.setFixedWidth(255)
        self.card.setStyleSheet("""
            QFrame#VsdcHudCard {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-left: 3.5px solid #4CF9B7;
                border-radius: 6px;
            }
        """)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(8, 7, 10, 7)
        card_layout.setSpacing(8)

        # Icon Label (Google Material Design Icon via QtAwesome)
        self.icon_label = QLabel(self.card)
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        # Content Column
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        # Header Row (Badge + Title)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        # Translucent Badge (matches .sera-badge in sdc_toast.js)
        self.badge_label = QLabel("SESSION START", self.card)
        self.badge_label.setStyleSheet("""
            background: rgba(76, 249, 183, 0.15);
            color: #4CF9B7;
            border: 1px solid rgba(76, 249, 183, 0.35);
            border-radius: 3px;
            font-size: 8.5px;
            font-weight: 700;
            padding: 1px 4px;
            letter-spacing: 0.3px;
        """)
        header_layout.addWidget(self.badge_label, 0, Qt.AlignVCenter)

        # Primary Title (matches .sera-title in sdc_toast.js)
        self.title_label = QLabel("", self.card)
        self.title_label.setStyleSheet("""
            color: #FFFFFF;
            font-size: 11px;
            font-weight: 600;
        """)
        header_layout.addWidget(self.title_label, 1, Qt.AlignVCenter)

        text_layout.addLayout(header_layout)

        # Subtitle / Details Row (matches .sera-body in sdc_toast.js, with RichText support)
        self.subtitle_label = QLabel("", self.card)
        self.subtitle_label.setTextFormat(Qt.RichText)
        self.subtitle_label.setStyleSheet("""
            color: #8B949E;
            font-size: 10px;
        """)
        text_layout.addWidget(self.subtitle_label)

        card_layout.addLayout(text_layout)
        root_layout.addWidget(self.card)

    def _get_icon(self, icon_name: str, color: str):
        cache_key = f"{icon_name}_{color}"
        if cache_key not in self._icon_cache:
            if qta is not None:
                try:
                    self._icon_cache[cache_key] = qta.icon(icon_name, color=color).pixmap(18, 18)
                except Exception:
                    self._icon_cache[cache_key] = None
            else:
                self._icon_cache[cache_key] = None
        return self._icon_cache[cache_key]

    @staticmethod
    def _format_subtitle_html(raw_text: str) -> str:
        """
        Formats subtitle details with SDC styling:
        - If an ARN or Acknowledgement number is detected, renders it as a highlighted
          monospace Consolas chip (#39FF14).
        """
        if not raw_text:
            return ""

        arn_pattern = r"(ARN|Ack|Acknowledgement)(?:\s*[:\-–—]?\s*)([A-Za-z0-9]{14,16})"
        if re.search(arn_pattern, raw_text, re.IGNORECASE):
            def repl(m):
                lbl = m.group(1)
                val = m.group(2)
                return f'{lbl}: <span style="font-family: Consolas, monospace; color: #39FF14; font-weight: bold;">{val}</span>'
            return re.sub(arn_pattern, repl, raw_text, flags=re.IGNORECASE)

        return raw_text

    def show_event(self, event_type: str, title: str, subtitle: str = "", duration_ms: int = 1800):
        """
        Displays the HUD pill at the bottom-left corner with the given event details.
        Snappy auto-dismiss within 1.8 seconds.
        """
        key = (event_type or "default").lower()
        theme = self.EVENT_THEMES.get(key, self.EVENT_THEMES["default"])
        icon_name, accent_color, badge_text, badge_bg, badge_border = theme

        # Deduplicate identical consecutive event within short window
        sig = f"{key}:{title}:{subtitle}"
        if sig == self._last_event_signature and self.isVisible() and self.opacity_effect.opacity() > 0.5:
            self.dismiss_timer.start(duration_ms)
            return
        self._last_event_signature = sig

        # 1. Update Card Styling (Obsidian background with 3.5px accent line)
        self.card.setStyleSheet(f"""
            QFrame#VsdcHudCard {{
                background-color: #161B22;
                border: 1px solid #30363D;
                border-left: 3.5px solid {accent_color};
                border-radius: 6px;
            }}
        """)

        # 2. Update Translucent Badge Pill (matches SDC .sera-badge)
        self.badge_label.setText(badge_text)
        self.badge_label.setStyleSheet(f"""
            background: {badge_bg};
            color: {accent_color};
            border: 1px solid {badge_border};
            border-radius: 3px;
            font-size: 8.5px;
            font-weight: 700;
            padding: 1px 4px;
            letter-spacing: 0.3px;
        """)

        # 3. Update Title & Rich Subtitle
        self.title_label.setText(title)
        formatted_sub = self._format_subtitle_html(subtitle)
        self.subtitle_label.setText(formatted_sub)
        self.subtitle_label.setVisible(bool(subtitle))

        # 4. Update Google Material Design Icon
        pixmap = self._get_icon(icon_name, accent_color)
        if pixmap:
            self.icon_label.setPixmap(pixmap)
            self.icon_label.show()
        else:
            self.icon_label.hide()

        self.adjustSize()
        self._reposition_bottom_left()

        # 5. Smooth Fade-in
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(1.0)
        self.anim.start()

        # Snappy Auto-dismiss
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
