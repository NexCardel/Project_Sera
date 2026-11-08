"""
ui/os_assist/mecp_card.py
-------------------------
MECP as a docked desktop card (blueprint §4.4, §4.5), the same look as the extension's `mecpWidget`.
MECP copies, it never fills: nothing here touches the page.

- Plain: rows "User ID (PAN)" (only when there is one) and "Password", each with 📋 Copy ->
  "✓ Copied" for 2 s and a "<label> copied!" toast. The password goes through secure_clipboard
  as a secret (cleared after the clipboard-clear seconds); the user ID as plain text. 90 s timer
  bar; once both are copied the card goes after 1.2 s ("copied"), as the extension's D6.
- SCC mode (`scc_rows`, fed by core/scc/card.py): "⚡ Sera Assist (SCC)", the PAN row, the combo
  rows with Copy and "This one worked", the note line and "None - I typed my own"; no timer, the
  card stays until ✕ or the desktop closes it. Combo copies are secret and also go straight to the
  SccCard ledger (`ledger`, = SccCard.note_clipboard). apply_update() applies the desktop's marks (✗
  failed rows, next, ask, stop) exactly as the extension's onUpdate.
- The card sends the messages the extension card sends (scc_row_worked, scc_row_none, and
  scc_card_closed on ✕ only) to `scc_send`, i.e. main.py's handlers in-process, no socket.

update_card / close_card at the bottom have automation.update_scc_card / close_scc_card's
signatures (O3-4 plugs them into SccCard) and may be called from any thread.
Values are shown on the card as today; they are held only in the base card's secrets, dropped
from the labels on dismiss, and never logged (rule 4).
"""
import threading
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                               QGraphicsOpacityEffect)

from ui.os_assist.dock_card import DockCard, css_px, wire_sck_edit

TIMEOUT_MS = 90000                    # injectMECP's expiresMs
COPIED_MS, TOAST_MS = 2000, 2500
BOTH_COPIED_DISMISS_MS = 1200
WIDTH_PX, SCC_WIDTH_PX = 320, 360

SCC_BADGE = "⚡ Sera Assist (SCC)"
PLAIN_TITLE, SCC_TITLE = "Client Credentials", "Verify Password"
USER_LABEL, PASS_LABEL = "User ID (PAN)", "Password"
SECTION_TITLE = "Password Combinations (Unverified)"
COPY_TEXT, COPIED_TEXT = "📋 Copy", "✓ Copied"
WORKED_TEXT, NONE_TEXT, SENT_TEXT = "This one worked", "None - I typed my own", "✓ Sent"

_hooks: Dict[str, Optional[Callable]] = {"send": None, "ledger": None}
_cards: Dict[str, "MecpCard"] = {}             # attempt id -> open SCC card
_cards_lock = threading.Lock()


def set_scc_hooks(send: Optional[Callable[[dict], None]] = None,
                  ledger: Optional[Callable[[str], object]] = None) -> None:
    """The app registers where the SCC card's messages go (`send(msg)`: dispatch on msg["type"] to
    main.py `_handle_scc_row_worked` / `_handle_scc_row_none` / `_handle_scc_card_closed`) and the
    ledger (`SccCard.note_clipboard`)."""
    _hooks["send"], _hooks["ledger"] = send, ledger


def _copy(text: str, secret: bool, clear_after_s: Optional[int]) -> None:
    from core.os_assist import secure_clipboard
    secure_clipboard.copy(text, secret, clear_after_s)


def _breakable(text: str) -> str:
    """The CSS `word-break: break-all` of .field-value: a zero-width space between characters."""
    return "​".join(text)


def _repolish(w: QWidget) -> None:
    for x in [w, *w.findChildren(QWidget)]:
        x.style().unpolish(x)
        x.style().polish(x)


class _Row(QFrame):
    """One `.field-row`: label + value on the left, the buttons on the right.
    state "" | failed | next | ask."""

    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.name = label
        lay = QHBoxLayout(self)
        lay.setContentsMargins(css_px(10), css_px(8), css_px(10), css_px(8))
        lay.setSpacing(css_px(10))
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(css_px(2))
        self.label = QLabel(label, self)
        self.label.setObjectName("flabel")
        self.value = QLabel(_breakable(value), self)
        self.value.setObjectName("fvalue")
        self.value.setWordWrap(True)
        left.addWidget(self.label)
        left.addWidget(self.value)
        lay.addLayout(left, 1)
        self.actions = QVBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(css_px(4))
        lay.addLayout(self.actions)
        self.copy = self.add_button("copy", COPY_TEXT)
        self.worked: Optional[QPushButton] = None
        self._dim = QGraphicsOpacityEffect(self)
        self._dim.setOpacity(0.55)
        self._dim.setEnabled(False)
        self.setGraphicsEffect(self._dim)
        self.setProperty("state", "")

    def add_button(self, name: str, text: str) -> QPushButton:
        b = QPushButton(text, self)
        b.setObjectName(name)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        self.actions.addWidget(b)
        return b

    @property
    def state(self) -> str:
        return self.property("state") or ""

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.setProperty("state", state)
            _repolish(self)
        self._dim.setEnabled(state == "failed")


class MecpCard(DockCard):
    """The OS MECP card. `attach(browser_hwnd, service)` then `present()` (the launcher's job;
    in SCC mode the attempt's `att.hwnd`)."""

    _deliver = Signal(object)         # a callable run on the Qt thread (update/close from any thread)

    def __init__(self, service, user_id: str, password: str = "", client_id=None, client_name: str = "",
                 clear_seconds: Optional[int] = 30, timeout_ms: int = TIMEOUT_MS,
                 scc_rows: Optional[List[dict]] = None, attempt_id: str = "",
                 copier: Optional[Callable[[str, bool, Optional[int]], None]] = None,
                 scc_send: Optional[Callable[[dict], None]] = None,
                 ledger: Optional[Callable[[str], object]] = None,
                 sck_handler: Optional[Callable[[dict], None]] = None, **kw):
        self.scc = bool(scc_rows)
        super().__init__(badge=SCC_BADGE, timeout_ms=None if self.scc else timeout_ms, **kw)
        self._deliver.connect(self._run_delivered)
        self.card.setStyleSheet(self.card_qss())
        self.service = service
        self.attempt_id = str(attempt_id or "")
        self._clear_s = clear_seconds
        self._copier = copier or _copy
        self._send = scc_send
        self._ledger = ledger
        self._has_user = bool(user_id and str(user_id).strip())
        self._copied = {"user": False, "pass": False}
        self._finishing = False
        self.hold_secret("user", user_id or "")
        if not self.scc:
            self.hold_secret("pass", password or "")
        get = service.get if isinstance(service, dict) else (lambda k, d=None: getattr(service, k, d))
        wire_sck_edit(self, client_id, get("id"), str(get("name", "portal") or "portal").lower(),
                      handler=sck_handler)

        self.rows: Dict[str, _Row] = {}
        self.user_row: Optional[_Row] = None
        self.pass_row: Optional[_Row] = None
        self._build_header(client_name or (SCC_TITLE if self.scc else PLAIN_TITLE))
        self._build_body(scc_rows or [])
        self._build_toast()
        self.dismissed.connect(self._on_dismissed)
        if self.attempt_id and self.scc:
            with _cards_lock:
                old = _cards.get(self.attempt_id)
                _cards[self.attempt_id] = self
            if old is not None and old is not self:
                old.dismiss("replaced")

    # -- look ----------------------------------------------------------------------------------
    def _build_header(self, title: str) -> None:
        """`.badge-wrap` (the SCC tag over the client title), ✎, ✕, then the header's bottom line."""
        lay = self.card.layout()
        lay.setContentsMargins(css_px(16), css_px(14), css_px(16), css_px(14))
        header = lay.itemAt(0).layout()
        header.removeWidget(self.badge)
        header.takeAt(0)                                   # the base's stretch
        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(css_px(2))
        wrap.addWidget(self.badge)
        self.badge.setVisible(self.scc)
        self.title = QLabel(title, self.card)
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        wrap.addWidget(self.title)
        header.insertLayout(0, wrap, 1)
        line = QFrame(self.card)
        line.setObjectName("rule")
        line.setFixedHeight(1)
        lay.insertWidget(2, line)
        lay.insertSpacing(3, css_px(12))

    def _build_body(self, scc_rows: List[dict]) -> None:
        self.body.setSpacing(0)
        if self._has_user:
            self.user_row = _Row(USER_LABEL, self.secret("user"), self.card)
            self.user_row.copy.clicked.connect(self._copy_user)
            self._add(self.user_row, css_px(8))
        if not self.scc:
            self.pass_row = _Row(PASS_LABEL, self.secret("pass"), self.card)
            self.pass_row.copy.clicked.connect(self._copy_pass)
            self._add(self.pass_row, css_px(8))
            self.note = self.none_btn = None
            return
        self.body.addSpacing(css_px(10) if not self._has_user else css_px(2))
        section = QLabel(SECTION_TITLE.upper(), self.card)
        section.setObjectName("section")
        self._add(section, css_px(6))
        self.note = QLabel("", self.card)
        self.note.setObjectName("note")
        self.note.setWordWrap(True)
        self.note.setProperty("stop", False)
        self._add(self.note, css_px(8))
        self.note.hide()
        self.none_btn = QPushButton(NONE_TEXT, self.card)
        self.none_btn.setObjectName("none")
        self.none_btn.setFocusPolicy(Qt.NoFocus)
        self.none_btn.setCursor(Qt.PointingHandCursor)
        self.none_btn.clicked.connect(self._none_typed)
        self._add(self.none_btn, css_px(8))
        self.none_btn.hide()
        for combo in scc_rows:
            label = str(combo.get("label") or f"Combo {combo.get('id')}")
            value = str(combo.get("value") or "")
            self.hold_secret(f"row:{label}", value)
            row = _Row(label, value, self.card)
            row.worked = row.add_button("worked", WORKED_TEXT)
            row.copy.clicked.connect(lambda _=False, lb=label: self._copy_row(lb))
            row.worked.clicked.connect(lambda _=False, lb=label: self._row_worked(lb))
            self.rows[label] = row
            self._add(row, css_px(8))

    def _add(self, w: QWidget, after: int) -> None:
        self.body.addWidget(w)
        self.body.addSpacing(after)

    def _build_toast(self) -> None:
        self.toast = QLabel("", self.card)
        self.toast.setObjectName("toast")
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.hide()
        self._toast_off = QTimer(self)
        self._toast_off.setSingleShot(True)
        self._toast_off.timeout.connect(self.toast.hide)

    def refit(self) -> None:
        self.card.setFixedWidth(css_px(SCC_WIDTH_PX if self.scc else WIDTH_PX))
        super().refit()

    def card_qss(self) -> str:
        """The extension's MECP CSS: `.box`, `.header`, `.field-row` (failed / next / ask),
        `.copy-btn`, `.worked-btn`, `.scc-note` (stop), `.none-btn`, `.toast`."""
        px = css_px
        scc = getattr(self, "scc", False)
        return f"""
            QFrame#card {{
                background: #161B22; border: {max(1, px(1.5))}px solid {"#2E9B5F" if scc else "#30363D"};
                border-radius: {px(10)}px; color: #F0F6FC; font-family: 'Segoe UI'; font-size: {px(13)}px;
            }}
            QLabel {{ background: transparent; }}
            QLabel#badge {{
                font-size: {px(11)}px; font-weight: 700; color: #4CF9B7; padding: 0; border: none;
            }}
            QLabel#title {{ font-size: {px(13)}px; font-weight: 700; color: #F0F6FC; }}
            QPushButton#head {{
                background: transparent; border: none; color: #8B949E;
                font-size: {px(18)}px; padding: 0 {px(4)}px; border-radius: {px(4)}px;
            }}
            QPushButton#head:hover {{ color: #F0F6FC; background: #21262D; }}
            QFrame#rule {{ background: #30363D; border: none; }}
            QLabel#section {{
                font-size: {px(10.5)}px; font-weight: 700; color: #8B949E; padding-left: {px(2)}px;
            }}
            QFrame#row {{
                background: #0D1117; border: 1px solid #21262D; border-radius: {px(6)}px;
            }}
            QFrame#row[state="failed"] {{ border-color: #6E2B2B; }}
            QFrame#row[state="next"] {{ border: 2px solid #D29922; }}
            QFrame#row[state="ask"] {{ border: 2px solid #4CF9B7; }}
            QFrame#row QLabel#flabel {{
                font-size: {px(10)}px; font-weight: 700; color: #8B949E; border: none;
            }}
            QFrame#row QLabel#fvalue {{
                font-family: Consolas; font-size: {px(13)}px; font-weight: 600; color: #E6EDF3; border: none;
            }}
            QPushButton#copy {{
                background: #238636; color: #FFFFFF; border: none; border-radius: {px(5)}px;
                padding: {px(6)}px {px(12)}px; font-family: 'Segoe UI'; font-size: {px(12)}px; font-weight: 600;
            }}
            QPushButton#copy:hover {{ background: #2EA043; }}
            QPushButton#copy[copied="true"] {{ background: #1F6FEB; }}
            QPushButton#copy:disabled {{ background: rgba(35, 134, 54, 0.5); color: rgba(255, 255, 255, 0.5); }}
            QPushButton#worked {{
                background: transparent; color: #4CF9B7; border: 1px solid #2E9B5F; border-radius: {px(5)}px;
                padding: {px(4)}px {px(10)}px; font-family: 'Segoe UI'; font-size: {px(11)}px; font-weight: 600;
            }}
            QPushButton#worked:hover {{ background: #12261D; }}
            QPushButton#worked:disabled {{ color: rgba(76, 249, 183, 0.6); border-color: rgba(46, 155, 95, 0.6); }}
            QLabel#note {{
                padding: {px(7)}px {px(10)}px; border-radius: {px(6)}px; background: #2B2111;
                border: 1px solid #D29922; color: #F0F6FC; font-size: {px(12)}px;
            }}
            QLabel#note[stop="true"] {{ background: #3A1515; border-color: #F85149; }}
            QPushButton#none {{
                background: transparent; color: #C9D1D9; border: 1px solid #484F58; border-radius: {px(5)}px;
                padding: {px(5)}px {px(10)}px; font-family: 'Segoe UI'; font-size: {px(11)}px; font-weight: 600;
            }}
            QPushButton#none:hover {{ background: #21262D; }}
            QLabel#toast {{
                background: #238636; color: #FFFFFF; padding: {px(5)}px {px(10)}px; border-radius: {px(4)}px;
                font-size: {px(11)}px; font-weight: 600;
            }}
        """

    # -- copies ----------------------------------------------------------------------------------
    def _copy(self, text: Optional[str], secret: bool, toast_label: str, btn: QPushButton) -> bool:
        if self.is_dismissed or not text:
            return False
        try:
            self._copier(text, secret, self._clear_s if secret else None)
        except Exception:
            return False
        self._show_toast(f"{toast_label} copied!")
        btn.setProperty("copied", True)
        btn.setText(COPIED_TEXT)
        _repolish(btn)

        def _back():
            btn.setProperty("copied", False)
            btn.setText(COPY_TEXT)
            _repolish(btn)
        QTimer.singleShot(COPIED_MS, btn, _back)
        return True

    def _copy_user(self) -> None:
        if self._copy(self.secret("user"), False, "User ID", self.user_row.copy):
            self._copied["user"] = True
            self._close_when_both_copied()

    def _copy_pass(self) -> None:
        if self._copy(self.secret("pass"), True, "Password", self.pass_row.copy):
            self._copied["pass"] = True
            self._close_when_both_copied()

    def _copy_row(self, label: str) -> None:
        text = self.secret(f"row:{label}")
        if not self._copy(text, True, label, self.rows[label].copy):
            return
        ledger = self._ledger or _hooks["ledger"]
        if ledger is not None:
            try:
                ledger(text)                           # the ledger works whatever the monitor sees (§4.5)
            except Exception:
                pass

    def _close_when_both_copied(self) -> None:
        """D6: a plain card goes once the User ID (when shown) and the password are copied."""
        if self._finishing or self.scc or not self._copied["pass"] or (self._has_user and not self._copied["user"]):
            return
        self._finishing = True
        if self.timer is not None:
            self.timer.stop()
        QTimer.singleShot(BOTH_COPIED_DISMISS_MS, self, lambda: self.dismiss("copied"))

    def _show_toast(self, text: str) -> None:
        self.toast.setText(text)
        self.toast.adjustSize()
        h = self.toast.sizeHint().height()
        self.toast.setGeometry(css_px(16), self.card.height() - css_px(6) - h, self.card.width() - css_px(32), h)
        self.toast.show()
        self.toast.raise_()
        self._toast_off.start(TOAST_MS)

    # -- SCC: staff answers and the desktop's marks -------------------------------------------------
    def _emit(self, msg: dict) -> None:
        send = self._send or _hooks["send"]
        if send is not None:
            try:
                send(msg)
            except Exception:
                pass

    def _row_worked(self, label: str) -> None:
        row = self.rows[label]
        if self.is_dismissed or not row.worked.isEnabled():
            return
        self._emit({"type": "scc_row_worked", "attempt_id": self.attempt_id, "row_label": label})
        row.worked.setText(SENT_TEXT)
        row.worked.setEnabled(False)

    def _none_typed(self) -> None:
        if self.is_dismissed or not self.none_btn.isEnabled():
            return
        self._emit({"type": "scc_row_none", "attempt_id": self.attempt_id})
        self.none_btn.setText(SENT_TEXT)
        self.none_btn.setEnabled(False)

    def apply_update(self, failed=None, next_label=None, message: str = "", stop: bool = False, ask=None) -> None:
        """The extension's onUpdate (Qt thread): ✗ on failed rows, highlight next / ask, disable
        copies on stop, a ✗ row is never credited, the None button while asking, the note line."""
        if not self.scc or self.is_dismissed:
            return
        failed = set(failed or [])
        asking = set(ask) if isinstance(ask, (list, tuple, set)) else None
        for label, row in self.rows.items():
            is_failed = label in failed
            if is_failed:
                row.set_state("failed")
            elif asking is not None and label in asking:
                row.set_state("ask")
            elif not stop and next_label == label:
                row.set_state("next")
            else:
                row.set_state("")
            row.label.setText(f"✗ {label}" if is_failed else label)
            row.copy.setEnabled(not stop)
            if stop or is_failed:
                row.worked.setEnabled(False)
        self.none_btn.setVisible(asking is not None)
        self.note.setText(message or "")
        self.note.setProperty("stop", bool(stop))
        _repolish(self.note)
        self.note.setVisible(bool(message))
        if self._presented:
            self.refit()

    def _run_delivered(self, fn) -> None:
        fn()

    # -- out -------------------------------------------------------------------------------------
    def dismiss(self, reason: str = "close") -> None:
        if not self.is_dismissed and reason == "close" and self.scc and self.attempt_id:
            self._emit({"type": "scc_card_closed", "attempt_id": self.attempt_id})   # only ✕ tells the desktop
        super().dismiss(reason)

    def _on_dismissed(self, _reason: str) -> None:
        """The secrets are gone; drop the shown values from the labels too."""
        for row in [self.user_row, self.pass_row, *self.rows.values()]:
            if row is not None:
                row.value.setText("")
        self._toast_off.stop()
        with _cards_lock:
            if _cards.get(self.attempt_id) is self:
                _cards.pop(self.attempt_id, None)


# ----------------------------------------------------------------------------- SccCard hooks
def card_for(attempt_id: str) -> Optional[MecpCard]:
    with _cards_lock:
        return _cards.get(str(attempt_id or ""))


def update_card(attempt_id: str, failed: list, next_label, message: str, stop: bool, ask=None) -> bool:
    """automation.update_scc_card for the OS card; any thread. Returns whether a card is open."""
    card = card_for(attempt_id)
    if card is None:
        return False
    failed = [str(x) for x in failed or []]
    ask = [str(x) for x in ask] if ask is not None and not stop else None
    card._deliver.emit(lambda: card.apply_update(failed, None if stop else next_label, str(message or ""), bool(stop), ask))
    return True


def close_card(attempt_id: str) -> bool:
    """automation.close_scc_card for the OS card (a save or the attempt ended); any thread.
    Not ✕, so the desktop is not told back."""
    card = card_for(attempt_id)
    if card is None:
        return False
    card._deliver.emit(lambda: card.dismiss("closed"))
    return True
