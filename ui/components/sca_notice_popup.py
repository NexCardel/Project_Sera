"""
ui/components/sca_notice_popup.py - SCA warnings where staff are looking: always on top of the browser
=====================================================================================================
A UID-page warning ("no client has this UID", "the UID box changed", ...) happens while the BROWSER is in
front, so Sera's own bottom-left alert (inside its window, behind the browser) is never seen. This small
card sits above every window at the bottom-right of the screen the cursor is on, never takes focus (the
portal keeps the keyboard), and fades after DURATION_MS; a click closes it. A new notice replaces the
one showing. Text only: the caller never puts a client's name or a password in it.
"""

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

DURATION_MS = 9000
WIDTH = 380
MARGIN = 24


class ScaNoticePopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._card = QFrame(self)
        self._card.setObjectName("scaNoticeCard")
        inner = QVBoxLayout(self._card)
        inner.setContentsMargins(14, 10, 14, 12)
        inner.setSpacing(4)
        self._badge = QLabel("SERA SCA", self._card)
        self._badge.setObjectName("scaNoticeBadge")
        self._text = QLabel(self._card)
        self._text.setObjectName("scaNoticeText")
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.PlainText)
        inner.addWidget(self._badge)
        inner.addWidget(self._text)
        lay.addWidget(self._card)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(350)
        self._anim.finished.connect(self._after_fade)
        self.level = ""

    def _style(self, level: str) -> None:
        accent = "#D29922" if level in ("warning", "error") else "#2E9B5F"
        self._card.setStyleSheet(
            f"#scaNoticeCard {{ background:#111814; border:1.5px solid {accent}; border-radius:12px; }}"
            f"#scaNoticeBadge {{ color:{accent}; font-size:10px; font-weight:700; letter-spacing:1px; }}"
            "#scaNoticeText { color:#FFFFFF; font-size:12.5px; }")

    def show_notice(self, text: str, level: str = "warning") -> None:
        self.level = level
        self._style(level)
        self._text.setText(str(text or ""))
        self._anim.stop()
        self.setWindowOpacity(1.0)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(DURATION_MS)

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        self.move(g.right() - self.width() - MARGIN, g.bottom() - self.height() - MARGIN)

    def mousePressEvent(self, event):          # a click closes it (it never takes focus)
        self._timer.stop()
        self._fade()

    def _fade(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() <= 0.01:
            self.hide()
