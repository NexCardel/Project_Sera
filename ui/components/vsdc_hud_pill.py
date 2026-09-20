"""
ui/components/vsdc_hud_pill.py — Floating Desktop Overlay Indicator for VSDC
=============================================================================
Displays real-time, non-intrusive HUD notifications at the bottom-left of the screen
when VSDC is actively monitoring routes, seeding identities, or capturing datasets.

Characteristics:
- Completely click-through (Qt.WA_TransparentForMouseEvents)
- Non-activating / focus-safe (Qt.WA_ShowWithoutActivating | Qt.WindowDoesNotAcceptFocus)
- Always on top (Qt.WindowStaysOnTopHint | Qt.ToolTip)
- Hidden while VSDC is merely polling; appears only when something is captured
- Auto-dismisses within 3.0 seconds with smooth opacity fade
- Pulses (brief 1.0 -> ~1.03 -> 1.0 scale) on every capture event, like the legacy
  SDC toast (sdc_toast.js): a new event while the pill is already showing updates it
  in place and pulses, rather than re-entering
- Strictly uses Google Material Design icons (mdi.*) via QtAwesome
"""

import html
import re
import sys
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QVariantAnimation, QEasingCurve, QPoint
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
        # Something the user should look at: a VSDC247 capture that has no client yet, or a
        # possible submission that was seen but NOT saved. Amber, so it never reads as a
        # confirmed capture.
        "prompt": (
            "mdi.alert-circle-outline",
            "#D29922",
            "ACTION NEEDED",
            "rgba(210, 153, 34, 0.15)",
            "rgba(210, 153, 34, 0.40)",
        ),
        "default": (
            "mdi.radar",
            "#388BFD",
            "VSDC ACTIVE",
            "rgba(56, 139, 253, 0.20)",
            "rgba(56, 139, 253, 0.40)",
        ),
    }

    _BASE_WIDTH = 255
    # Mirrors sdc_toast.js's `sera-pulse` keyframes (scale 1 -> 1.02 -> 1 over 0.25s),
    # slightly stronger since a desktop overlay this small needs a few whole pixels
    # of growth to be perceptible at all.
    _PULSE_SCALE = 0.03
    _PULSE_MS = 250

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
        self._pulse_pending = False
        self._pulse_base_height = 0

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

        # Capture pulse (see _PULSE_SCALE): 0 -> 1 -> 0 progress driving a brief grow/shrink.
        self.pulse_anim = QVariantAnimation(self)
        self.pulse_anim.setDuration(self._PULSE_MS)
        self.pulse_anim.setStartValue(0.0)
        self.pulse_anim.setKeyValueAt(0.5, 1.0)
        self.pulse_anim.setEndValue(0.0)
        self.pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
        self.pulse_anim.valueChanged.connect(self._apply_pulse)
        self.pulse_anim.finished.connect(self._reset_pulse_size)

        self._build_ui()
        self.hide()

    def _build_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Main Card Frame (matches .sera-toast-card in sdc_toast.js)
        self.card = QFrame(self)
        self.card.setObjectName("VsdcHudCard")
        self.card.setFixedWidth(self._BASE_WIDTH)
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
        # Wrap rather than clip: the card is a fixed width, and a clipped title or
        # subtitle hides the very thing the toast exists to show (a name, an Ack).
        self.title_label.setWordWrap(True)
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
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet("""
            color: #8B949E;
            font-size: 10px;
        """)
        text_layout.addWidget(self.subtitle_label)

        # Return-context row (matches .sera-details-grid chips in sdc_toast.js): the
        # form, filing type / preference and period of the return being worked on.
        # Word-wrapped inside the fixed-width card; each chip is kept whole with
        # non-breaking spaces so a line only ever breaks between chips.
        self.details_label = QLabel("", self.card)
        self.details_label.setTextFormat(Qt.RichText)
        self.details_label.setWordWrap(True)
        self.details_label.setStyleSheet("""
            color: #8B949E;
            font-size: 10px;
        """)
        self.details_label.setVisible(False)
        text_layout.addWidget(self.details_label)

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
        - If a capture-source tag is present ("VSDC-X (Exact)" from the UI
          Automation accessibility-tree read, or "VSDC (Visual)" from OCR),
          renders it in a distinct color so the user can see at a glance
          which engine actually supplied that capture.
        Both apply independently — a subtitle can carry either, both, or neither.
        """
        if not raw_text:
            return ""

        text = raw_text

        arn_pattern = r"(ARN|Ack|Acknowledgement)(?:\s*[:\-–—]?\s*)([A-Za-z0-9]{14,16})"
        def repl_arn(m):
            lbl = m.group(1)
            val = m.group(2)
            return f'{lbl}: <span style="font-family: Consolas, monospace; color: #39FF14; font-weight: bold;">{val}</span>'
        text = re.sub(arn_pattern, repl_arn, text, flags=re.IGNORECASE)

        source_pattern = r"(VSDC-X \(Exact\)|VSDC247 \(Visual\)|VSDC \(Visual\))"
        def repl_source(m):
            label = m.group(1)
            # VSDC-X blue, the VSDC247 safety net purple, plain VSDC OCR muted grey.
            color = "#58A6FF" if "VSDC-X" in label else ("#D2A8FF" if "VSDC247" in label else "#8B949E")
            return f'<span style="color: {color}; font-weight: 700;">{label}</span>'
        text = re.sub(source_pattern, repl_source, text)

        return text

    # GST's QRMP values are a filing *preference*; everything else in filing_pref is
    # an ITR filing *type* (Original/Revised/Belated/Updated).
    _GST_PREFERENCES = ("Monthly", "Quarterly")

    @classmethod
    def _format_context_html(cls, context) -> str:
        """
        Renders the return context as label/value chips: Form, Type (ITR) or Pref
        (GST), and Period. Only captured values are shown; nothing at all when the
        session has not captured any of them yet.
        """
        if not context:
            return ""
        pref = context.get("filing_pref")
        pref_label = "Pref" if pref in cls._GST_PREFERENCES else "Type"
        chips = []
        for label, value in (("Form", context.get("form")), (pref_label, pref), ("Period", context.get("period"))):
            if not value:
                continue
            safe = html.escape(str(value)).replace(" ", "&nbsp;")
            chips.append(
                f'<span style="color:#8B949E;">{label}</span>&nbsp;'
                f'<span style="color:#E6EDF3; font-weight:600;">{safe}</span>'
            )
        return ' <span style="color:#484F58;">&middot;</span> '.join(chips)

    def show_event(self, event_type: str, title: str, subtitle: str = "", duration_ms: int = 1800, context=None):
        """
        Shows the HUD pill at the bottom-left corner for a capture/identity event,
        then auto-dismisses. Nothing is shown while VSDC is merely polling a page.

        Like the legacy SDC toast, every new event pulses the pill; if it is already
        on screen it is updated in place (and pulses) instead of re-entering.

        context carries the return being worked on (form, filing type/preference,
        period) and is shown as a chip row under the subtitle.
        """
        key = (event_type or "default").lower()
        theme = self.EVENT_THEMES.get(key, self.EVENT_THEMES["default"])
        icon_name, accent_color, badge_text, badge_bg, badge_border = theme

        # Identical consecutive event while still showing: just keep it up a little
        # longer - no new pulse for something the user has already just seen.
        context_html = self._format_context_html(context)
        sig = f"{key}:{title}:{subtitle}:{context_html}"
        already_showing = self.isVisible() and self.opacity_effect.opacity() > 0.5
        if sig == self._last_event_signature and already_showing:
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
        self.details_label.setText(context_html)
        self.details_label.setVisible(bool(context_html))

        # 4. Update Google Material Design Icon
        pixmap = self._get_icon(icon_name, accent_color)
        if pixmap:
            self.icon_label.setPixmap(pixmap)
            self.icon_label.show()
        else:
            self.icon_label.hide()

        # New text can change the pill's natural size; measure it at rest, never mid-pulse.
        self.pulse_anim.stop()
        self._reset_pulse_size()
        self.adjustSize()
        self._reposition_bottom_left()

        # 5. Enter (fade-in) if hidden, or update in place if already showing; pulse either way.
        self.show()
        self.raise_()
        if already_showing:
            self._start_pulse()
        else:
            # Pulse once the fade-in has finished, so the pulse is actually visible.
            self._pulse_pending = True
            self.anim.stop()
            self.anim.setStartValue(self.opacity_effect.opacity())
            self.anim.setEndValue(1.0)
            self.anim.start()

        # Snappy auto-dismiss
        self.dismiss_timer.stop()
        self.dismiss_timer.start(duration_ms)

    def _start_pulse(self):
        self._pulse_pending = False
        self._pulse_base_height = self.card.sizeHint().height()
        self.pulse_anim.stop()
        self.pulse_anim.start()

    def _apply_pulse(self, progress):
        """Grows the pill by up to _PULSE_SCALE (anchored bottom-left) at progress 1.0."""
        p = float(progress)
        self.card.setFixedWidth(self._BASE_WIDTH + round(self._BASE_WIDTH * self._PULSE_SCALE * p))
        self.card.setMinimumHeight(self._pulse_base_height + round(self._pulse_base_height * self._PULSE_SCALE * p))
        self.adjustSize()
        self._reposition_bottom_left()

    def _reset_pulse_size(self):
        self.card.setFixedWidth(self._BASE_WIDTH)
        self.card.setMinimumHeight(0)

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
            self.pulse_anim.stop()
            self._reset_pulse_size()
            self.hide()
            self._last_event_signature = ""
            self._pulse_pending = False
        elif self._pulse_pending:
            self._start_pulse()

    def _fade_out(self):
        """Smoothly fades out and hides."""
        self.anim.stop()
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(0.0)
        self.anim.start()
