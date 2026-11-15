"""
ui/os_assist/sck_card.py
------------------------
SCK (Sera Credential Keeper) as a docked desktop card: the OS-engine twin of the extension's
in-page `sckWidget`. Same wording, red accent, persistent (no timer bar), ✎ hidden.

Unlike the other OS cards this one has a text box, so it is the one card that takes focus
(`DockCard.ACCEPTS_FOCUS`): clicking the password box activates it and typing goes to it. It is
still shown without activating, so the browser keeps focus when the card first appears. The
buttons never take focus.

The typed password is read from the box only by the engine's handler (`password()`), held with
`hold_secret` until the fill job is submitted, and dropped on dismiss. Nothing here touches the
page, the vault or the database; `fill_requested` / `save_requested` carry the attempt id only.

States the engine drives: set_filled, set_fill_failed, set_outcome("wrong_password" | "locked" |
"worked"), set_saved, set_save_failed, set_no_outcome. A registry by attempt id lets the app reach
a card from any thread (update_card / close_card), like mecp_card.
"""
import threading
from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ui.os_assist.dock_card import DockCard, css_px

BADGE = "Sera Credential Keeper"
ACCENT = "#E5484D"
WIDTH_PX = 300
FILL_TEXT, SAVE_TEXT = "Fill && Test", "Save Directly"      # "&&": a lone "&" is a Qt mnemonic marker
SUB_ROTATION = "The portal refused the saved password. Enter the new password."
SUB_EDIT = "Enter the new password for this client."
SUB_NO_CLIENT = "Sera could not match this login to a client."
SUB_NEW_CLIENT = "This login is not in Sera. Enter its password and Sera will fill it and remember it."
LABEL = "New password"
PLACEHOLDER = "Type the new password"
NO_RESULT_TEXT = "No result seen yet. Press Login on the portal, or Save Directly as untested."
NO_RESULT_MS = 60_000

_cards: Dict[str, "SckCard"] = {}
_cards_lock = threading.Lock()


def _repolish(w: QWidget) -> None:
    for x in [w, *w.findChildren(QWidget)]:
        x.style().unpolish(x)
        x.style().polish(x)


class SckCard(DockCard):
    """The persistent new-password card. `kind` is "rotation" or "new_client"."""

    ACCEPTS_FOCUS = True
    DOCK_CORNER = "bottom-right"          # SCK's corner, as the in-page card; top-right is SCA's
    BADGE = BADGE
    ACCENT = ACCENT
    SLIDE_IN_MS, FADE_IN_MS = 380, 320

    fill_requested = Signal(str)          # attempt id
    save_requested = Signal(str)

    def __init__(self, attempt_id: str, service, client_id=None, client_name: str = "",
                 portal: str = "", kind: str = "rotation", identity: str = "", edit: bool = False,
                 no_result_ms: int = NO_RESULT_MS, **kw):
        self.attempt_id = str(attempt_id or "")
        self.kind = kind
        self.client_id = client_id
        self.client_name = str(client_name or "")
        self.portal = str(portal or "")
        self.identity = str(identity or "")
        self._edit = bool(edit)
        self._busy = False
        self._done = False
        self._no_result_ms = int(no_result_ms)
        super().__init__(badge=BADGE, timeout_ms=None, **kw)
        self.edit_btn.hide()
        self._build_body()
        self._no_result = QTimer(self)
        self._no_result.setSingleShot(True)
        self._no_result.timeout.connect(self.set_no_outcome)
        self.dismissed.connect(lambda _r: self._no_result.stop())
        with _cards_lock:
            _cards[self.attempt_id] = self
        self.dismissed.connect(lambda _r: self._unregister())

    # -- look ----------------------------------------------------------------------------------
    def _title_text(self) -> str:
        if self.kind == "new_client":
            return self.identity or self.portal or "Portal"
        return f"{self.client_name} • {self.portal}" if self.client_name else (self.portal or "Portal")

    def _subtitle_text(self) -> str:
        if self.kind == "new_client":
            return SUB_NEW_CLIENT
        if not self.client_id:
            return SUB_NO_CLIENT
        return SUB_EDIT if self._edit else SUB_ROTATION

    def _build_body(self) -> None:
        self.body.setSpacing(css_px(8))
        self.title = QLabel(self._title_text(), self.card)
        self.title.setObjectName("title")
        self.subtitle = QLabel(self._subtitle_text(), self.card)
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        self.label = QLabel(LABEL, self.card)
        self.label.setObjectName("flabel")
        self.pwd = QLineEdit(self.card)
        self.pwd.setObjectName("pwd")
        self.pwd.setEchoMode(QLineEdit.Password)
        self.pwd.setPlaceholderText(PLACEHOLDER)
        self.pwd.setFocusPolicy(Qt.ClickFocus)
        self.pwd.returnPressed.connect(self._fill)
        self.eye = QPushButton("👁", self.card)
        self.eye.setObjectName("eye")
        self.eye.setFocusPolicy(Qt.NoFocus)
        self.eye.setCursor(Qt.PointingHandCursor)
        self.eye.setToolTip("Show / hide")
        self.eye.clicked.connect(self._toggle_eye)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(css_px(4))
        row.addWidget(self.pwd, 1)
        row.addWidget(self.eye)
        self.fill_btn = QPushButton(FILL_TEXT, self.card)
        self.fill_btn.setObjectName("fill")
        self.save_btn = QPushButton(SAVE_TEXT, self.card)
        self.save_btn.setObjectName("save")
        for b in (self.fill_btn, self.save_btn):
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
        self.fill_btn.clicked.connect(self._fill)
        self.save_btn.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(css_px(8))
        buttons.addWidget(self.fill_btn, 1)
        buttons.addWidget(self.save_btn, 1)
        self.status = QLabel("", self.card)
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.setProperty("cls", "")
        self.status.hide()
        self.body.addWidget(self.title)
        self.body.addWidget(self.subtitle)
        self.body.addWidget(self.label)
        self.body.addLayout(row)
        self.body.addLayout(buttons)
        self.body.addWidget(self.status)

    def card_qss(self) -> str:
        px = css_px
        return super().card_qss() + f"""
            QLabel#badge {{
                color: #FF8A8E; background: rgba(229, 72, 77, 0.20);
                border: 1px solid rgba(255, 138, 142, 0.35);
            }}
            QLabel#title {{ font-size: {px(14)}px; font-weight: 700; color: #FFFFFF; background: transparent; }}
            QLabel#subtitle {{ font-size: {px(12)}px; color: #B8A6A7; background: transparent; }}
            QLabel#flabel {{ font-size: {px(11)}px; font-weight: 600; color: #B8A6A7; background: transparent; }}
            QLineEdit#pwd {{
                font-size: {px(13)}px; color: #FFFFFF; padding: {px(8)}px {px(10)}px;
                background: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: {px(8)}px;
            }}
            QLineEdit#pwd:focus {{ border-color: {ACCENT}; }}
            QPushButton#eye {{
                font-family: 'Segoe UI Symbol'; background: transparent; border: none;
                color: #B8A6A7; font-size: {px(14)}px; padding: {px(4)}px {px(6)}px;
            }}
            QPushButton#eye:hover {{ color: #FFFFFF; }}
            QPushButton#fill {{
                padding: {px(9)}px {px(12)}px; font-size: {px(13)}px; font-weight: 700; color: #FFFFFF;
                background: {ACCENT}; border: 1px solid #F0686C; border-radius: {px(8)}px;
            }}
            QPushButton#fill:hover {{ background: #F0686C; }}
            QPushButton#fill:disabled {{ background: #6B3A3C; border-color: #6B3A3C; color: #CCB; }}
            QPushButton#save {{
                padding: {px(9)}px {px(12)}px; font-size: {px(13)}px; font-weight: 600; color: #E8DCDC;
                background: rgba(255, 255, 255, 0.07); border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: {px(8)}px;
            }}
            QPushButton#save:hover {{ background: rgba(255, 255, 255, 0.14); }}
            QPushButton#save:disabled {{ color: #8A7E7E; }}
            QLabel#status {{ font-size: {px(12)}px; color: #B8A6A7; background: transparent; }}
            QLabel#status[cls="ok"] {{ color: #4CF9B7; }}
            QLabel#status[cls="err"] {{ color: #FF8A8E; }}
        """

    def refit(self) -> None:
        self.card.setFixedWidth(css_px(WIDTH_PX + 2 * 18 + 3))
        super().refit()

    # -- actions -------------------------------------------------------------------------------
    def password(self) -> str:
        """What staff typed (the engine reads it once per click, inside its handler)."""
        return self.pwd.text()

    def has_password(self) -> bool:
        return bool(self.pwd.text())

    def _toggle_eye(self) -> None:
        shown = self.pwd.echoMode() == QLineEdit.Normal
        self.pwd.setEchoMode(QLineEdit.Password if shown else QLineEdit.Normal)

    def _fill(self) -> None:
        if self.is_dismissed or self._busy or self._done:
            return
        if not self.pwd.text():
            self._set_status("Type the new password first.", "err")
            return
        self._busy = True
        self.fill_btn.setText("Filling…")
        self._set_status("Locating password field on portal…", "")
        self._set_enabled(False)
        self.fill_requested.emit(self.attempt_id)

    def _save(self) -> None:
        if self.is_dismissed or self._busy or self._done:
            return
        if not self.pwd.text():
            self._set_status("Type the new password first.", "err")
            return
        self._busy = True
        self.save_btn.setText("Saving…")
        self._set_status("Saving to Sera Vault…", "")
        self._set_enabled(False)
        self.save_requested.emit(self.attempt_id)

    def _set_enabled(self, on: bool) -> None:
        self.fill_btn.setEnabled(on)
        self.save_btn.setEnabled(on)

    def _set_status(self, text: str, cls: str = "") -> None:
        self.status.setText(text)
        self.status.setProperty("cls", cls)
        self.status.setVisible(bool(text))
        _repolish(self.status)
        if self._presented:
            self.refit()

    def _reset_buttons(self) -> None:
        self._busy = False
        self.fill_btn.setText(FILL_TEXT)
        self.save_btn.setText(SAVE_TEXT)
        self._set_enabled(True)

    def _forget_password(self) -> None:
        self.pwd.clear()

    # -- states the engine calls ---------------------------------------------------------------
    def set_filled(self) -> None:
        self._reset_buttons()
        self._set_status("✓ Password filled. Complete CAPTCHA & submit to test.", "ok")
        self._no_result.start(self._no_result_ms)

    def set_fill_failed(self, reason: str) -> None:
        self._reset_buttons()
        self._set_status(str(reason or "Sera could not fill the password box."), "err")

    def set_outcome(self, kind: str) -> None:
        if kind == "wrong_password":
            self._no_result.stop()
            self._reset_buttons()
            self._set_status("✕ Portal rejected this password. Check and try another.", "err")
            self._shake()
        elif kind == "locked":
            self._no_result.stop()
            self._busy = True
            self._set_enabled(False)
            self._forget_password()
            self._set_status("✕ Account locked. Ceasing attempts.", "err")
        elif kind == "worked":
            self._no_result.stop()
            self.subtitle.setText("Login succeeded.")
            self._set_status("✓ Login succeeded! Saving new password…", "ok")

    def set_no_outcome(self) -> None:
        if self.is_dismissed or self._done:
            return
        self._set_status(NO_RESULT_TEXT, "")

    def set_saved(self, message: str = "") -> None:
        self._done = True
        self._no_result.stop()
        self._forget_password()
        self.save_btn.setText("Saved")
        self.fill_btn.setText("✓ Verified")
        self._set_enabled(False)
        self._set_status(message or "✓ New password saved to Sera Vault", "ok")

    def set_save_failed(self, message: str = "") -> None:
        self._reset_buttons()
        self._set_status(message or "The password could not be saved.", "err")

    def _shake(self) -> None:
        """A short horizontal wobble of the card (the extension's 'shake')."""
        try:
            from PySide6.QtCore import QPoint, QPropertyAnimation
            anim = QPropertyAnimation(self.card, b"pos", self)
            anim.setDuration(260)
            anim.setKeyValueAt(0.0, QPoint(0, 0))
            anim.setKeyValueAt(0.2, QPoint(-6, 0))
            anim.setKeyValueAt(0.4, QPoint(6, 0))
            anim.setKeyValueAt(0.6, QPoint(-4, 0))
            anim.setKeyValueAt(0.8, QPoint(3, 0))
            anim.setKeyValueAt(1.0, QPoint(0, 0))
            anim.start(QPropertyAnimation.DeleteWhenStopped)
        except Exception:
            pass

    # -- dismiss -------------------------------------------------------------------------------
    def dismiss(self, reason: str = "close") -> None:
        if not self.is_dismissed:
            self._forget_password()          # the typed password never outlives the card
        super().dismiss(reason)

    def _unregister(self) -> None:
        with _cards_lock:
            if _cards.get(self.attempt_id) is self:
                del _cards[self.attempt_id]


def card_for(attempt_id: str) -> Optional[SckCard]:
    with _cards_lock:
        return _cards.get(attempt_id)


def close_card(attempt_id: str, reason: str = "closed") -> bool:
    card = card_for(attempt_id)
    if card is None:
        return False
    card.dismiss(reason)
    return True


def open_cards() -> Dict[str, SckCard]:
    with _cards_lock:
        return dict(_cards)
