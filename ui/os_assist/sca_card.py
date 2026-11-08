"""
ui/os_assist/sca_card.py
------------------------
The SCA "Inject Password" card as a docked desktop card (blueprint §4.4, §4.6), the same look as the
extension's `showWidgetInPage`: SCA badge, ✎ + ✕, business name, "owner • portal", an optional amber
note (needs_confirm; the border turns amber with it) and an Inject button.

The card holds no password and fills nothing itself: Inject emits `inject_requested(arm_id,
service_id)` and the SCA engine continues (password request, then the fill, §4.6). The card goes
after Inject, ✕ or 60 s. ✎ opens the SCK widget through the shared D3 helper.
"""
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton

from ui.os_assist.dock_card import DockCard, css_px, wire_sck_edit

TIMEOUT_MS = 60000                    # the extension's auto-dismiss
BADGE_TEXT = "SCA"
INJECT_TEXT = "Inject Password"
AMBER = "#D29922"


def subtitle_text(owner_name: str, portal_name: str) -> str:
    owner, portal = (owner_name or "").strip(), str(portal_name or "")
    return f"{owner} • {portal}" if owner else portal


def _repolish(w) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


class ScaCard(DockCard):
    """`attach(browser_hwnd, service)` then `present()` (the engine's job)."""

    inject_requested = Signal(object, object)         # arm_id, service_id

    def __init__(self, arm_id, service_id, business_name: str, owner_name: str, portal_name: str,
                 note: str = "", client_id=None, timeout_ms: int = TIMEOUT_MS,
                 sck_handler: Optional[Callable[[dict], None]] = None, **kw):
        super().__init__(badge=BADGE_TEXT, timeout_ms=timeout_ms, **kw)
        self.arm_id = arm_id
        self.service_id = service_id
        self.client_id = client_id
        self.note_text = note or ""
        self._injected = False
        wire_sck_edit(self, client_id, service_id, portal_name or "", handler=sck_handler)
        self._build_body(business_name, owner_name, portal_name)
        self.card.setProperty("warn", bool(self.note_text))
        _repolish(self.card)

    @property
    def amber(self) -> bool:
        return bool(self.card.property("warn"))

    def _build_body(self, business_name: str, owner_name: str, portal_name: str) -> None:
        self.body.setSpacing(0)
        self.title = QLabel(business_name or "Client", self.card)
        self.title.setObjectName("title")
        self.subtitle = QLabel(subtitle_text(owner_name, portal_name), self.card)
        self.subtitle.setObjectName("subtitle")
        self.body.addWidget(self.title)
        self.body.addSpacing(css_px(2))
        self.body.addWidget(self.subtitle)
        self.body.addSpacing(css_px(10))
        self.note = None
        if self.note_text:
            self.note = QLabel(self.note_text, self.card)
            self.note.setObjectName("note")
            self.note.setWordWrap(True)
            self.body.addWidget(self.note)
            self.body.addSpacing(css_px(10))
        self.inject_btn = QPushButton(INJECT_TEXT, self.card)
        self.inject_btn.setObjectName("inject")
        self.inject_btn.setFocusPolicy(Qt.NoFocus)
        self.inject_btn.setCursor(Qt.PointingHandCursor)
        self.inject_btn.clicked.connect(self._inject)
        self.body.addWidget(self.inject_btn)

    def card_qss(self) -> str:
        """The extension's `.card.warn`, `.title`, `.subtitle`, `.note` and `.btn-inject` CSS."""
        px = css_px
        return super().card_qss() + f"""
            QFrame#card[warn="true"] {{ border-color: {AMBER}; }}
            QLabel#title {{
                font-size: {px(14)}px; font-weight: 700; color: #FFFFFF; background: transparent;
            }}
            QLabel#subtitle {{ font-size: {px(12)}px; color: #9FB3A8; background: transparent; }}
            QLabel#note {{ font-size: {px(12)}px; color: #E3B341; background: transparent; }}
            QPushButton#inject {{
                padding: {px(9)}px {px(12)}px; font-size: {px(13)}px; font-weight: 700; color: #FFFFFF;
                background: #2E9B5F; border: 1px solid #34B76D; border-radius: {px(8)}px;
                font-family: 'Segoe UI';
            }}
            QPushButton#inject:hover {{ background: #34B76D; }}
        """

    def _inject(self) -> None:
        if self.is_dismissed or self._injected:
            return
        self._injected = True
        self.inject_requested.emit(self.arm_id, self.service_id)
        self.dismiss("injected")
