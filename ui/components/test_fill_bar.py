"""
ui/components/test_fill_bar.py — Sera Credential Keeper (SCK) floating bar.

Model B: Desktop Floating Test & Fill Bar (blueprint section 2–5).

TestFillBar is a native PySide6 frameless, always-on-top tool window that
lets a staff member enter a candidate password, fill it into the active
browser window, and observe the portal's response — automatically saving
the verified credential to the Sera vault when the login succeeds.

Fill mechanism
--------------
The password is **not** typed with Win32 ``SendInput``.  The bar asks the
Sera browser extension (over the local WebSocket bridge) to set the portal's
password field in its own DOM.  The extension uses the native value setter
plus ``input``/``change`` events, so Angular/React controllers see a normal
edit and the browser keeps its focus.  Nothing is submitted: staff still
solve the CAPTCHA and press Login.

Window characteristics
----------------------
* ``Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool``
* Dark ``#171717`` background, ``#FF4D4D`` accent border, white ``#FFFFFF`` text.
* Fixed width 520 px; height is determined by layout.
* Docked to the top-right corner of the target browser window.
* 3-minute idle TTL enforced by a ``QTimer``; auto-closes when it fires.

Visual layout
-------------
  ┌─────────────────────────────────────────────────┐
  │ 🛡 GST • ABCDE1234F (Acme Ltd)          [✕]     │  ← header
  │ New Password: [••••••••••••••] 👁  [Fill & Test] │  ← body
  │ ℹ Ready — Enter the working password to test …  │  ← status
  └─────────────────────────────────────────────────┘

Outcome states
--------------
ready       → "Ready — Enter the working password to test and save"
filling     → "Filling the password on the portal…"  (Fill button disabled)
filled      → "Password filled. Complete the CAPTCHA and submit the login."
wrong       → shake animation + "Portal rejected this password. …"
locked      → "Account locked. Ceasing attempts."  (auto-close after 3 s)
worked      → bold green banner "✓ Login Succeeded! Password saved to Sera Vault."
              (auto-close after 2.5 s)
fill_failed → "Could not find the password field on the portal page."

Integration
-----------
The bar polls a ``queue.Queue`` (``outcome_queue``) every 500 ms.  The
owning machinery (``main.py``) puts outcome strings or dicts into it:

* ``"fill_ok"`` / ``{"kind": "fill_failed", "message": …}`` from the
  extension's ``sck_fill_result`` reply.
* ``"wrong_password"`` / ``"locked"`` / ``"worked"`` from ``SckWatcher``'s
  page outcomes (SGT reading the portal).

On a ``"worked"`` outcome the bar:

1. Calls ``session.commit_candidate(attempt_id)`` to retrieve and zero
   the volatile password.
2. Calls ``save_verified_for_rotation(…)`` to persist the credential.
3. Emits ``rotation_succeeded``.
4. Shows the green banner for 2.5 s then closes.

Security note
-------------
The password field text is never logged or written to any persistent
store directly.  Only ``session.set_candidate()`` touches it (storing it
in volatile RAM), and it reaches the DB only through
``save_verified_for_rotation``.  The one outbound copy is the WebSocket
message that asks the extension to fill the field.
"""

from __future__ import annotations

import queue
from typing import Any, Callable, Optional

import qtawesome as qta
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.rotation.detector import save_verified_for_rotation
from core.rotation.session import RotationSession

# ── Colours / sizing ─────────────────────────────────────────────────────────

_BG = "#171717"
_ACCENT = "#FF4D4D"
_FG = "#FFFFFF"
_FG_DIM = "#AAAAAA"
_GREEN = "#4CAF50"
_WARN = "#FF9800"
_BAR_W = 520
_TTL_MS = 180_000       # 3 min idle TTL
_POLL_MS = 500          # outcome queue poll interval
_FILL_ACK_MS = 12000    # wait this long for the extension's sck_fill_result (it polls the page)
_ICON_SZ = 16           # icon point size for qtawesome

# ── Status messages ──────────────────────────────────────────────────────────

_MSG_READY = "Ready — Enter the working password to test and save"
_MSG_FILLING = "Filling the password on the portal…"
_MSG_FILLED = "Password filled. Complete the CAPTCHA and submit the login."
_MSG_WRONG = "Portal rejected this password. Please check and try another."
_MSG_LOCKED = "Account locked. Ceasing attempts."
_MSG_WORKED = "✓ Login Succeeded! Password saved to Sera Vault."
_MSG_NO_CLIENT = "Sera could not match this login to a client. Copy the client's ID first."
_MSG_NO_FIELD = "Could not find the password field on the portal page."
_MSG_NO_EXTENSION = ("Sera could not reach the browser extension. Make sure it is installed "
                     "and the portal tab is open.")
_MSG_SAVE_FAILED = "The login worked, but the password could not be saved. Try again."


def _icon(name: str, color: str = _FG) -> Any:
    """Return a QIcon from qtawesome using a Google Material Design icon name."""
    return qta.icon(name, color=color)


def _dock_xy(right_px: int, top_px: int, scale: float, geom: Any = None) -> Any:
    """The bar's top-right corner for a browser window, in Qt's logical pixels.

    `right_px` / `top_px` come from Win32 (physical pixels) and `scale` is the window's DPI
    factor (1.0 = 96 dpi), so a 125%/150% display does not push the bar off the right edge.
    With `geom` (the screen's available QRect) the result is clamped onto the screen, so a stale
    or oversized window rect can never leave the bar hanging off an edge.
    """
    scale = float(scale) if scale and scale > 0 else 1.0
    x = round(right_px / scale) - _BAR_W - 8
    y = round(top_px / scale) + 8
    if geom is not None:
        # QRect.right()/bottom() are inclusive, so +1 gives the screen's exclusive edge.
        x = min(max(geom.left() + 8, x), geom.right() + 1 - _BAR_W - 8)
        y = min(max(geom.top() + 8, y), geom.bottom() + 1 - 120)
    return max(0, x), max(0, y)


class _BorderWidget(QWidget):
    """Paints the ``#FF4D4D`` accent border around the bar."""

    def paintEvent(self, event: Any) -> None:  # noqa: N802 — Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        pen = QPen(QColor(_ACCENT))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))


class TestFillBar(QWidget):
    """SCK floating 'Test & Fill' bar — Sera Credential Keeper.

    Frameless, always-on-top, docked to the browser window corner.

    Signals:
        rotation_succeeded(client_id, pan, portal):
            Emitted when the portal confirms the login and the credential
            has been saved to the Sera vault.
        rotation_failed(client_id, pan, portal, reason):
            Emitted when the rotation ends without success.  ``reason`` is
            one of ``"wrong"``, ``"locked"``, or ``"timeout"``.
    """

    # ``object`` for client_id: an unregistered login has no id, and Signal(int) cannot emit None.
    rotation_succeeded = Signal(object, str, str)        # (client_id, pan, portal)
    rotation_failed = Signal(object, str, str, str)      # (client_id, pan, portal, reason)

    def __init__(
        self,
        db: Any,
        client_id: Optional[int],
        pan: str,
        portal: str,
        hwnd: int,
        client_name: str = "",
        actor_fn: Optional[Callable[[], str]] = None,
        service: Optional[dict] = None,
        session: Optional[RotationSession] = None,
        filler: Optional[Callable[[str], bool]] = None,
        outcome_queue: Optional[queue.Queue] = None,
        on_fill_started: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Args:
            db:            Sera database handle.
            client_id:     Sera client id, or ``None`` when the login is unregistered.
            pan:           PAN string (shown in the header; may be empty).
            portal:        Human-readable portal name.
            hwnd:          Win32 HWND of the target browser (0 = screen corner).
            client_name:   Display name shown in the header.
            actor_fn:      Zero-arg callable returning the current actor/username.
            service:       The service row: ``id``, ``login_page_link``,
                           ``password_selector`` — what the extension needs to find the field.
            session:       Shared :class:`RotationSession` instance.
            filler:        ``callable(password) -> bool`` that hands the password to the
                           browser.  Defaults to :func:`automation.sck_fill`.
            outcome_queue: ``queue.Queue`` into which ``main.py`` puts outcome strings or
                           dicts (``"worked"``, ``"wrong_password"``, ``"locked"``,
                           ``"fill_ok"``, ``{"kind": "fill_failed", "message": …}``).
                           If ``None`` a new private queue is used.
            on_fill_started: Zero-arg callable run when a fill starts, so the owning watcher can
                           deliver the new attempt's page outcome without the previous
                           attempt's throttle.
            parent:        Optional parent widget.
        """
        super().__init__(parent)

        self._db = db
        self._client_id = int(client_id) if client_id else None
        self._has_client = self._client_id is not None
        self._pan = str(pan or "").strip().upper()
        self._portal = str(portal or "").strip()
        self._hwnd = int(hwnd)
        self._client_name = str(client_name or "").strip()
        self._service: dict = dict(service or {})
        self._service_id = self._service.get("id")
        self._actor_fn: Callable[[], str] = actor_fn or (lambda: "Staff")
        self._session: RotationSession = session or RotationSession()
        self._filler: Callable[[str], bool] = filler or self._send_fill
        self._outcome_queue: queue.Queue = outcome_queue or queue.Queue()
        self._on_fill_started: Optional[Callable[[], None]] = on_fill_started
        self._testing = False
        self._fill_acked = False
        self._last_save: Any = None
        self._drag_offset: Any = None
        self._user_moved = False

        # Open an attempt session immediately.
        self._attempt = self._session.open(
            client_id=self._client_id,
            pan=self._pan,
            portal=self._portal,
            hwnd=self._hwnd,
        )

        # Build the window.
        self._build_window()
        self._build_ui()
        self._apply_stylesheet()

        # No client in Sera: there is nowhere to save a verified password, so do not offer to fill.
        if not self._has_client:
            self._pwd_field.setEnabled(False)
            self._fill_btn.setEnabled(False)
            self._set_status(_MSG_NO_CLIENT, _WARN)

        # TTL timer — auto-close on 3-minute idle.
        self._ttl_timer = QTimer(self)
        self._ttl_timer.setSingleShot(True)
        self._ttl_timer.timeout.connect(self._on_ttl)
        self._ttl_timer.start(_TTL_MS)

        # Outcome poll timer — check queue every 500 ms.
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_outcome)
        self._poll_timer.start(_POLL_MS)

        # If the extension never answers (not installed / Firefox build without SCK), do not
        # leave the bar stuck on "Filling…".
        self._fill_timer = QTimer(self)
        self._fill_timer.setSingleShot(True)
        self._fill_timer.timeout.connect(self._on_fill_ack_timeout)

        # Drag the frameless bar by its labels too, not just its own background.
        for child in self.findChildren(QLabel):
            child.installEventFilter(self)

        # Dock to the browser, then follow it when the window is moved or resized.
        self._dock_to_browser(self._hwnd)
        self._dock_timer = QTimer(self)
        self._dock_timer.setInterval(1000)
        self._dock_timer.timeout.connect(self._follow_browser)
        if self._hwnd:
            self._dock_timer.start()

    # ── Window setup ─────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint  # type: ignore[attr-defined]
            | Qt.FramelessWindowHint  # type: ignore[attr-defined]
            | Qt.Tool  # type: ignore[attr-defined]
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)  # type: ignore[attr-defined]
        # Close means destroy: main.py's registry drops the bar on `destroyed`.
        self.setAttribute(Qt.WA_DeleteOnClose, True)  # type: ignore[attr-defined]
        self.setFixedWidth(_BAR_W)

    def _build_ui(self) -> None:
        """Construct and wire up the three-row layout."""
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # ── Header row ────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_icon("mdi.shield-key", _ACCENT).pixmap(_ICON_SZ * 2, _ICON_SZ * 2))
        header.addWidget(icon_lbl)

        title_parts = [self._portal]
        if self._pan:
            title_parts.append(self._pan)
        if self._client_name:
            title_parts.append(f"({self._client_name})")
        self._title_lbl = QLabel(" • ".join(title_parts))
        self._title_lbl.setStyleSheet(f"color: {_FG}; font-weight: bold; font-size: 12px;")
        header.addWidget(self._title_lbl, stretch=1)

        self._close_btn = QPushButton()
        self._close_btn.setIcon(_icon("mdi.close", _FG_DIM))
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setFlat(True)
        self._close_btn.setToolTip("Close")
        self._close_btn.clicked.connect(self._on_user_close)
        header.addWidget(self._close_btn)

        root.addLayout(header)

        # ── Body row ──────────────────────────────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(6)

        pwd_lbl = QLabel("New Password:")
        pwd_lbl.setStyleSheet(f"color: {_FG_DIM}; font-size: 11px;")
        body.addWidget(pwd_lbl)

        self._pwd_field = QLineEdit()
        self._pwd_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd_field.setPlaceholderText("Enter candidate password…")
        self._pwd_field.setFont(QFont("Consolas, Courier New, monospace", 11))
        self._pwd_field.setStyleSheet(
            f"QLineEdit {{ background: #2A2A2A; color: {_FG}; border: 1px solid #444;"
            f" border-radius: 4px; padding: 3px 6px; }}"
        )
        self._pwd_field.textChanged.connect(self._reset_ttl)
        body.addWidget(self._pwd_field, stretch=1)

        self._eye_btn = QPushButton()
        self._eye_btn.setIcon(_icon("mdi.eye", _FG_DIM))
        self._eye_btn.setFixedSize(28, 28)
        self._eye_btn.setFlat(True)
        self._eye_btn.setToolTip("Show / hide password")
        self._eye_btn.setCheckable(True)
        self._eye_btn.toggled.connect(self._toggle_echo)
        body.addWidget(self._eye_btn)

        self._fill_btn = QPushButton("Fill & Test")
        self._fill_btn.setIcon(_icon("mdi.keyboard-outline", _FG))
        self._fill_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: {_FG}; border: none;"
            f" border-radius: 4px; padding: 4px 12px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #cc3d3d; }}"
            f"QPushButton:disabled {{ background: #555; color: #888; }}"
        )
        self._fill_btn.clicked.connect(self._on_fill_clicked)
        body.addWidget(self._fill_btn)

        root.addLayout(body)

        # ── Status row ────────────────────────────────────────────────────
        self._status_lbl = QLabel(f"ℹ {_MSG_READY}")
        self._status_lbl.setStyleSheet(f"color: {_FG_DIM}; font-size: 10px;")
        self._status_lbl.setWordWrap(True)
        root.addWidget(self._status_lbl)

        # Container border is drawn by the parent widget's paintEvent.
        self.setLayout(root)

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            f"TestFillBar {{ background: {_BG}; border: 2px solid {_ACCENT}; border-radius: 6px; }}"
            f"QLabel {{ color: {_FG}; }}"
        )

    # ── Positioning ───────────────────────────────────────────────────────────

    def _dock_to_browser(self, hwnd: int) -> None:
        """Place the bar at the top-right corner of the browser window (screen corner without
        a valid HWND)."""
        self.setFixedWidth(_BAR_W)
        pos = self._dock_position(hwnd)
        if pos is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geom = screen.availableGeometry()
            pos = (geom.right() - _BAR_W - 16, geom.top() + 16)
        self.move(*pos)

    def _dock_position(self, hwnd: int):
        """(x, y) for the browser's top-right corner, in Qt's logical pixels; None if the
        window is not there. Always clamped so the bar cannot hang off the screen edge."""
        try:
            import ctypes
            import ctypes.wintypes
            if not hwnd:
                return None
            rect = ctypes.wintypes.RECT()
            # GetWindowRect returns 0 for a stale HWND without raising; treat that as no window.
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            right, top = int(rect.right), int(rect.top)
        except Exception:
            return None
        # Win32 reports physical pixels while Qt moves in device-independent pixels: the
        # screen's devicePixelRatio is the same factor Qt uses, so a 125%/150% display does
        # not push the bar off the right edge.
        primary = QApplication.primaryScreen()
        scale = float(primary.devicePixelRatio()) if primary is not None else 1.0
        rough = _dock_xy(right, top, scale, None)
        screen = QApplication.screenAt(QPoint(rough[0] + _BAR_W // 2, rough[1] + 20))
        screen = screen or primary
        if screen is None:
            return rough
        return _dock_xy(right, top, float(screen.devicePixelRatio()),
                        screen.availableGeometry())

    def _follow_browser(self) -> None:
        """Keep the bar on the browser's corner when the window is moved or resized - unless
        staff dragged the bar somewhere themselves."""
        if self._user_moved or not self._hwnd:
            return
        try:
            import ctypes
            if ctypes.windll.user32.IsIconic(self._hwnd):
                return                      # minimised: the rect is meaningless
        except Exception:
            pass
        pos = self._dock_position(self._hwnd)
        if pos is not None and (self.x(), self.y()) != pos:
            self.move(*pos)

    # ── Dragging (frameless window) ───────────────────────────────────────────

    def _drag_start(self, event: Any) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return True
        return False

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 — Qt override
        if not self._drag_start(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 — Qt override
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._user_moved = True
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 — Qt override
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 — Qt override
        """Let a drag that starts on one of the labels move the window too."""
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if self._drag_start(event):
                return True
        elif kind == QEvent.Type.MouseMove and self._drag_offset is not None:
            self.mouseMoveEvent(event)
            return True
        elif kind == QEvent.Type.MouseButtonRelease:
            self._drag_offset = None
        return super().eventFilter(obj, event)

    # ── UI interaction ────────────────────────────────────────────────────────

    def _toggle_echo(self, checked: bool) -> None:
        if checked:
            self._pwd_field.setEchoMode(QLineEdit.EchoMode.Normal)
            self._eye_btn.setIcon(_icon("mdi.eye-off", _FG_DIM))
        else:
            self._pwd_field.setEchoMode(QLineEdit.EchoMode.Password)
            self._eye_btn.setIcon(_icon("mdi.eye", _FG_DIM))

    def _reset_ttl(self) -> None:
        """Restart the idle TTL timer whenever the user interacts."""
        self._ttl_timer.start(_TTL_MS)

    def _set_status(self, text: str, color: str = _FG_DIM) -> None:
        self._status_lbl.setText(f"ℹ {text}")
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 10px;")

    # ── Fill & Test ───────────────────────────────────────────────────────────

    def _on_fill_clicked(self) -> None:
        if not self._has_client:
            self._set_status(_MSG_NO_CLIENT, _WARN)
            return
        candidate = self._pwd_field.text()
        if not candidate:
            self._set_status("Please enter a candidate password first.", _WARN)
            return

        # Store the candidate in volatile session RAM. It reaches the page only through
        # `_filler` (the extension's DOM fill) and the DB only after a confirmed login.
        try:
            self._session.set_candidate(self._attempt.attempt_id, candidate)
        except KeyError:
            # Another bar's open() swept this attempt (3-minute sweep). Start a fresh one, so the
            # candidate still has somewhere volatile to live and the confirmed login can save it.
            self._attempt = self._session.open(
                client_id=self._client_id, pan=self._pan, portal=self._portal, hwnd=self._hwnd)
            self._session.set_candidate(self._attempt.attempt_id, candidate)

        # Clear the field in the UI immediately (UI copy is no longer needed).
        self._pwd_field.clear()

        # A new attempt starts: let the watcher deliver this attempt's page outcome at once
        # (the previous attempt's rejection may still be inside its throttle window).
        if self._on_fill_started is not None:
            try:
                self._on_fill_started()
            except Exception:
                pass

        self._testing = True
        self._fill_acked = False
        self._fill_btn.setEnabled(False)
        self._set_status(_MSG_FILLING, _FG_DIM)
        self._reset_ttl()
        self._fill_timer.start(_FILL_ACK_MS)

        try:
            reached = bool(self._filler(candidate))
        except Exception:
            reached = False
        finally:
            candidate = ""  # noqa: F841 — drop our local copy

        if not reached:
            self._fill_timer.stop()
            self._on_fill_failed(_MSG_NO_EXTENSION)

    def _send_fill(self, candidate: str) -> bool:
        """Ask the extension to set the password field in the open portal tab."""
        try:
            import automation
            return automation.sck_fill(self._service, candidate, self._client_id, self._portal)
        except Exception:
            return False

    def _on_fill_ack_timeout(self) -> None:
        if not self._testing or self._fill_acked:
            return
        self._on_fill_failed(_MSG_NO_EXTENSION)

    def _on_fill_failed(self, message: str) -> None:
        """The extension could not fill the field (or never answered)."""
        self._testing = False
        self._fill_acked = True
        self._fill_timer.stop()
        self._fill_btn.setEnabled(True)
        self._set_status(message or _MSG_NO_FIELD, _WARN)
        self._session.discard_candidate(self._attempt.attempt_id)

    # ── Outcome polling ───────────────────────────────────────────────────────

    def deliver_outcome(self, outcome: Any) -> None:
        """Thread-safe: ``main.py`` pushes an outcome for this bar (a string or a dict)."""
        self._outcome_queue.put(outcome)

    @property
    def last_save(self) -> Any:
        """The ``RotationSave`` from the confirmed login, or ``None``."""
        return self._last_save

    def _poll_outcome(self) -> None:
        """Called every 500 ms to drain the outcome queue."""
        while True:
            try:
                outcome = self._outcome_queue.get_nowait()
            except queue.Empty:
                return
            self._handle_outcome(outcome)

    def _handle_outcome(self, outcome: Any) -> None:
        if isinstance(outcome, dict):
            kind = str(outcome.get("kind") or "")
            message = str(outcome.get("message") or "")
        else:
            kind, message = str(outcome), ""
        if kind == "worked":
            self._on_worked()
        elif kind == "wrong_password":
            self._on_wrong()
        elif kind == "locked":
            self._on_locked()
        elif kind == "fill_ok":
            self._fill_acked = True
            self._fill_timer.stop()
            if self._testing:
                # The staff member now needs time for the CAPTCHA and Login: restart the idle TTL.
                self._reset_ttl()
                self._set_status(_MSG_FILLED, _GREEN)
        elif kind == "fill_failed":
            self._on_fill_failed(message or _MSG_NO_FIELD)
        # Anything else (neutral, no_conclusion) is silently ignored.

    def _on_worked(self) -> None:
        """The portal confirmed login.  Save the credential and announce success."""
        if not self._testing:
            return
        self._testing = False
        self._fill_acked = True
        self._fill_timer.stop()

        # Commit the volatile candidate (zeros it in session RAM).
        try:
            pwd = self._session.commit_candidate(self._attempt.attempt_id)
        except KeyError:
            # The attempt was already closed (idle TTL): there is no candidate to save.
            self._set_status(_MSG_SAVE_FAILED, _WARN)
            return
        actor = self._actor_fn()
        result = None
        try:
            result = save_verified_for_rotation(
                db=self._db,
                password=pwd,
                pan=self._pan,
                client_id=self._client_id,
                portal=self._portal,
                actor=actor,
                service_id=self._service_id,
                client_name=self._client_name,
                method="SCK-Bar",
            )
        except Exception:
            result = None
        finally:
            pwd = ""  # noqa: F841 — zero our local copy immediately

        self._last_save = result
        if result is None:
            self._fill_btn.setEnabled(True)
            self._set_status(_MSG_SAVE_FAILED, _WARN)
            return

        # Show success banner.
        self._fill_btn.setEnabled(False)
        self._status_lbl.setText(_MSG_WORKED)
        self._status_lbl.setStyleSheet(
            f"color: {_GREEN}; font-size: 11px; font-weight: bold;"
        )

        self.rotation_succeeded.emit(self._client_id, self._pan, self._portal)
        QTimer.singleShot(2500, self._close_clean)

    def _on_wrong(self) -> None:
        """The portal rejected the password — shake and re-enable for retry."""
        if not self._testing:
            return
        self._testing = False
        self._fill_acked = True
        self._fill_timer.stop()
        self._fill_btn.setEnabled(True)
        self._set_status(_MSG_WRONG, _WARN)
        self._session.discard_candidate(self._attempt.attempt_id)
        self._shake()

    def _on_locked(self) -> None:
        """The portal says the account is locked — warn and close."""
        if not self._testing:
            return
        self._testing = False
        self._fill_acked = True
        self._fill_timer.stop()
        self._fill_btn.setEnabled(False)
        self._set_status(_MSG_LOCKED, _ACCENT)
        self._session.discard_candidate(self._attempt.attempt_id)
        self.rotation_failed.emit(self._client_id, self._pan, self._portal, "locked")
        QTimer.singleShot(3000, self._close_clean)

    def _on_ttl(self) -> None:
        """Idle TTL expired — close quietly."""
        self.rotation_failed.emit(self._client_id, self._pan, self._portal, "timeout")
        self._close_clean()

    def _on_user_close(self) -> None:
        self.rotation_failed.emit(self._client_id, self._pan, self._portal, "wrong")
        self._close_clean()

    # ── Shake animation ───────────────────────────────────────────────────────

    def _shake(self) -> None:
        """Horizontal shake animation to signal a wrong-password rejection."""
        orig = self.pos()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(300)
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.setKeyValueAt(0.0,  orig)
        anim.setKeyValueAt(0.15, orig.__class__(orig.x() - 8, orig.y()))
        anim.setKeyValueAt(0.30, orig.__class__(orig.x() + 8, orig.y()))
        anim.setKeyValueAt(0.45, orig.__class__(orig.x() - 6, orig.y()))
        anim.setKeyValueAt(0.60, orig.__class__(orig.x() + 6, orig.y()))
        anim.setKeyValueAt(0.75, orig.__class__(orig.x() - 4, orig.y()))
        anim.setKeyValueAt(0.90, orig.__class__(orig.x() + 4, orig.y()))
        anim.setKeyValueAt(1.0,  orig)
        anim.start()

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _close_clean(self) -> None:
        """Stop timers, close the session attempt, and hide the bar."""
        self._poll_timer.stop()
        self._ttl_timer.stop()
        self._fill_timer.stop()
        self._dock_timer.stop()
        self._session.close(self._attempt.attempt_id)
        self.close()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 — Qt override
        """Ensure the session is always closed even if the window is force-closed."""
        self._poll_timer.stop()
        self._ttl_timer.stop()
        self._fill_timer.stop()
        self._dock_timer.stop()
        self._session.close(self._attempt.attempt_id)
        super().closeEvent(event)
