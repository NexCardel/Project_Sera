"""
ui/os_assist/sca_chip.py
------------------------
The two-second "Filled <portal> for <client>[ (<token>)]" confirmation after every SCA fill (the
extension's `showFillChipInPage`). Docked like the cards (same base window, never takes focus), fades
in, fades out at 2.4 s and is gone with it. Holds no password, only names.
"""
from PySide6.QtCore import Qt, QEasingCurve, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui.os_assist.dock_card import DockCard, css_px

LIFE_MS = 2400
FADE_IN_MS, FADE_OUT_MS = 250, 400
MAX_WIDTH_PX = 340


def chip_text(portal_name: str, business_name: str, token: str = "") -> str:
    return f"Filled {portal_name or 'password'} for {business_name or 'client'}" + (f" ({token})" if token else "")


class ScaChip(DockCard):
    """`attach(browser_hwnd, service)` then `present()`."""

    DOCK_TOP = 20                             # .chip `top: 20px; right: 24px`

    def __init__(self, business_name: str, portal_name: str, token: str = "",
                 life_ms: int = LIFE_MS, **kw):
        super().__init__(badge="SCA", timeout_ms=None, **kw)
        self.text = chip_text(portal_name, business_name, token)
        self.label.setText(self.text)
        self._life = QTimer(self)
        self._life.setSingleShot(True)
        self._life.setInterval(life_ms)
        self._life.timeout.connect(lambda: self.dismiss("timeout"))
        self._fade.setEasingCurve(QEasingCurve.OutQuad)

    def _build(self, badge: str, timeout_ms) -> None:
        self.card = QFrame(self)
        self.card.setObjectName("card")
        px = css_px
        self.card.setStyleSheet(f"""
            QWidget {{ font-family: 'Segoe UI'; }}
            QFrame#card {{ background: #111814; border: {max(1, px(1.5))}px solid {self.ACCENT};
                border-radius: {px(10)}px; font-family: 'Segoe UI'; }}
            QLabel {{ background: transparent; color: #FFFFFF; font-size: {px(12.5)}px; }}
            QLabel#tag {{ color: #4CF9B7; font-weight: 700; }}
        """)
        self.card.setMaximumWidth(px(MAX_WIDTH_PX))
        lay = QHBoxLayout(self.card)
        lay.setContentsMargins(px(12), px(8), px(12), px(8))
        lay.setSpacing(px(6))
        tag = QLabel(badge, self.card)
        tag.setObjectName("tag")
        self.label = QLabel("", self.card)
        self.label.setWordWrap(True)
        lay.addWidget(tag, 0, Qt.AlignTop)
        lay.addWidget(self.label, 1)
        self.body = QVBoxLayout()            # unused; the base class expects it
        self.timer = None

    def refit(self) -> None:
        """One line as wide as the text needs; it wraps only past max-width 340 px (as the CSS)."""
        self.label.setWordWrap(False)
        one_line = self.card.sizeHint().width()
        self.label.setWordWrap(True)
        w = min(one_line, css_px(MAX_WIDTH_PX))
        self.card.setFixedWidth(w)
        lay = self.card.layout()
        h = lay.heightForWidth(w) if lay.hasHeightForWidth() else self.card.sizeHint().height()
        self.card.setFixedHeight(max(h, self.card.minimumSizeHint().height()))
        self.resize(self.card.size())
        self.card.move(0, 0)

    def present(self) -> None:
        if self.is_dismissed or self._presented:
            return
        super().present()
        self._life.start()

    def _slide_in(self) -> None:
        self._slid_in = True
        self._fade.stop()
        self._fade.setDuration(FADE_IN_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def dismiss(self, reason: str = "close") -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.clear_secrets()                  # holds none; kept so every dismiss path is the same
        self._life.stop()
        self._tick.stop()
        self._probe_job = None
        self.dismissed.emit(reason)
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setDuration(FADE_OUT_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.hide)
        self._fade.start()
