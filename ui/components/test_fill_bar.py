"""
ui/components/test_fill_bar.py — Sera Credential Keeper (SCK) floating bar.

Model B: Desktop Floating Test & Fill Bar (blueprint section 2–5).

TestFillBar is a native PySide6 frameless, always-on-top tool window that
lets a staff member enter a candidate password, inject it into the active
browser window, and observe the portal's response — automatically saving
the verified credential to the Sera vault when the login succeeds.

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
ready    → "Ready — Enter the working password to test and save"
testing  → "Testing credentials on portal…"  (Fill button disabled)
wrong    → shake animation + "Portal rejected this password. …"
locked   → "Account locked. Ceasing attempts."  (auto-close after 3 s)
worked   → bold green banner "✓ Login Succeeded! Password saved to Sera Vault."
           (auto-close after 2.5 s)

Integration
-----------
The bar polls a ``queue.Queue`` (``outcome_queue``) every 500 ms.  The
owning FailureDetector / rotation machinery puts outcome strings
(``"worked"``, ``"wrong_password"``, ``"locked"``) into that queue when
the portal responds.  On a ``"worked"`` outcome the bar:

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
``save_verified_for_rotation``.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional

import qtawesome as qta
from PySide6.QtCore import (
    QEasingCurve,
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
from core.rotation.injector import RotationInjector
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
_ICON_SZ = 16           # icon point size for qtawesome

# ── Status messages ──────────────────────────────────────────────────────────

_MSG_READY = "Ready — Enter the working password to test and save"
_MSG_TESTING = "Testing credentials on portal…"
_MSG_WRONG = "Portal rejected this password. Please check and try another."
_MSG_LOCKED = "Account locked. Ceasing attempts."
_MSG_WORKED = "✓ Login Succeeded! Password saved to Sera Vault."


def _icon(name: str, color: str = _FG) -> Any:
    """Return a QIcon from qtawesome using a Google Material Design icon name."""
    return qta.icon(name, color=color)


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

    rotation_succeeded = Signal(int, str, str)           # (client_id, pan, portal)
    rotation_failed = Signal(int, str, str, str)         # (client_id, pan, portal, reason)

    def __init__(
        self,
        db: Any,
        client_id: int,
        pan: str,
        portal: str,
        hwnd: int,
        client_name: str = "",
        actor_fn: Optional[Callable[[], str]] = None,
        session: Optional[RotationSession] = None,
        injector: Optional[RotationInjector] = None,
        outcome_queue: Optional[queue.Queue] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Args:
            db:            Sera database handle.
            client_id:     Sera client id.
            pan:           PAN string.
            portal:        Human-readable portal name.
            hwnd:          Win32 HWND of the target browser.
            client_name:   Display name shown in the header.
            actor_fn:      Zero-arg callable returning the current actor/username.
            session:       Shared :class:`RotationSession` instance.
            injector:      Shared :class:`RotationInjector` instance.
            outcome_queue: ``queue.Queue`` into which the rotation machinery
                           puts outcome strings (``"worked"``, ``"wrong_password"``,
                           ``"locked"``).  If ``None`` a new private queue is used.
            parent:        Optional parent widget.
        """
        super().__init__(parent)

        self._db = db
        self._client_id = client_id
        self._pan = str(pan or "").strip().upper()
        self._portal = str(portal or "").strip()
        self._hwnd = int(hwnd)
        self._client_name = str(client_name or "").strip()
        self._actor_fn: Callable[[], str] = actor_fn or (lambda: "Staff")
        self._session: RotationSession = session or RotationSession()
        self._injector: RotationInjector = injector or RotationInjector()
        self._outcome_queue: queue.Queue = outcome_queue or queue.Queue()

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

        # TTL timer — auto-close on 3-minute idle.
        self._ttl_timer = QTimer(self)
        self._ttl_timer.setSingleShot(True)
        self._ttl_timer.timeout.connect(self._on_ttl)
        self._ttl_timer.start(_TTL_MS)

        # Outcome poll timer — check queue every 500 ms.
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_outcome)
        self._poll_timer.start(_POLL_MS)

        # Dock to the browser.
        self._dock_to_browser(self._hwnd)

    # ── Window setup ─────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint  # type: ignore[attr-defined]
            | Qt.FramelessWindowHint  # type: ignore[attr-defined]
            | Qt.Tool  # type: ignore[attr-defined]
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)  # type: ignore[attr-defined]
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
        """Place the bar at the top-right corner of the browser window."""
        try:
            import ctypes
            import ctypes.wintypes
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            bar_w = _BAR_W
            x = rect.right - bar_w - 8
            y = rect.top + 8
            self.setFixedWidth(bar_w)
            self.move(max(0, x), max(0, y))
        except Exception:
            # Fall back to top-right of the primary screen.
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                self.move(geom.right() - _BAR_W - 16, geom.top() + 16)

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
        candidate = self._pwd_field.text()
        if not candidate:
            self._set_status("Please enter a candidate password first.", _WARN)
            return

        # Store the candidate in volatile session RAM.
        self._session.set_candidate(self._attempt.attempt_id, candidate)

        # Clear the field in the UI immediately (UI copy is no longer needed).
        self._pwd_field.clear()

        # Update state to "Testing".
        self._fill_btn.setEnabled(False)
        self._set_status(_MSG_TESTING, _FG_DIM)
        self._reset_ttl()

        # Run the injection on a daemon thread to keep the UI responsive.
        threading.Thread(
            target=self._inject_candidate,
            args=(candidate,),
            daemon=True,
            name="sck-inject",
        ).start()
        # Zero our local reference — the injector will zero its own copy.
        candidate = ""  # noqa: F841

    def _inject_candidate(self, candidate: str) -> None:
        """Daemon thread: inject the candidate then zero it."""
        try:
            self._injector.inject(self._hwnd, candidate)
        except RuntimeError as exc:
            # Invalid HWND — surface error on the UI thread.
            QTimer.singleShot(0, lambda: self._on_inject_error(str(exc)))
        finally:
            candidate = ""  # noqa: F841 — zero local copy

    def _on_inject_error(self, msg: str) -> None:
        self._fill_btn.setEnabled(True)
        self._set_status(f"Injection failed: {msg}", _WARN)
        self._session.discard_candidate(self._attempt.attempt_id)

    # ── Outcome polling ───────────────────────────────────────────────────────

    def _poll_outcome(self) -> None:
        """Called every 500 ms to drain the outcome queue."""
        try:
            outcome = self._outcome_queue.get_nowait()
        except queue.Empty:
            return
        self._handle_outcome(str(outcome))

    def _handle_outcome(self, outcome: str) -> None:
        if outcome == "worked":
            self._on_worked()
        elif outcome == "wrong_password":
            self._on_wrong()
        elif outcome == "locked":
            self._on_locked()
        # Anything else (neutral, no_conclusion) is silently ignored.

    def _on_worked(self) -> None:
        """The portal confirmed login.  Save the credential and announce success."""
        # Commit the volatile candidate (zeros it in session RAM).
        pwd = self._session.commit_candidate(self._attempt.attempt_id)
        actor = self._actor_fn()
        try:
            save_verified_for_rotation(
                db=self._db,
                password=pwd,
                pan=self._pan,
                client_id=self._client_id,
                portal=self._portal,
                actor=actor,
            )
        finally:
            pwd = ""  # noqa: F841 — zero our local copy immediately

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
        self._fill_btn.setEnabled(True)
        self._set_status(_MSG_WRONG, _WARN)
        self._session.discard_candidate(self._attempt.attempt_id)
        self._shake()

    def _on_locked(self) -> None:
        """The portal says the account is locked — warn and close."""
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
        self._session.close(self._attempt.attempt_id)
        self.close()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 — Qt override
        """Ensure the session is always closed even if the window is force-closed."""
        self._poll_timer.stop()
        self._ttl_timer.stop()
        self._session.close(self._attempt.attempt_id)
        super().closeEvent(event)
