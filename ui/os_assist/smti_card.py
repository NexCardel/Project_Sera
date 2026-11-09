"""
ui/os_assist/smti_card.py
-------------------------
SMTI Manual Assist as a docked desktop card (blueprint §4.4), the same look as the extension's
`manualAssistWidget`: badge, ✎ + ✕, client title, 👤 Username / 🔑 Password, 30 s timer bar.

- Username: the worker finds the box (locate.find_fields) and fills it (fill.fill). Filled ->
  "✓  Username Injected" for 2 s; no box or not filled -> copied (not secret) and
  "📋  Copied Username (Ctrl+V)" for 2.5 s.
- Password: filled -> "✓  Password Injected", and the card goes once the late check passes;
  otherwise a secure copy (cleared after the clipboard-clear seconds) and
  "📋  Copied Password (Ctrl+V)", then the card goes after 1.2 s.
- A press shows its result after fill's first check (about 0.15 s), not after the full 1.15 s:
  the 1.0 s re-check runs on the worker afterwards (fill late_check="background"). If the page
  drops the value by then, the card falls back to the copy, as if the fill had failed.
- Flutter mode (TRACES host, or a page with no visible Edit until one is focused): the buttons are
  hidden and two step cards shown; fill.FocusWatcher is asked on the worker whenever the browser
  reports a focus move (WinEvent, at once) and polled as a safety net (400 ms with events, 200 ms
  without), and the user ID, then the password, is typed into each new box staff click (§4.3).
  Nothing is clicked, tabbed or submitted.
- Speed: the boxes are found once when the card shows (locate.warm_fields, which also wakes the
  browser's accessibility tree) and reused by a press while still on the page; a press jumps the
  worker's queue (worker.submit_urgent).

The user ID and password live only in the base card's secrets and go with dismiss() (rule 4).
All UIA work runs on the OS Assist worker; results come back to the Qt thread by a signal.
"""
from typing import Callable, Optional
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton

from ui.os_assist.dock_card import DockCard, css_px, wire_sck_edit

TIMEOUT_MS = 30000                    # the extension's SMTI duration
INJECTED_MS, COPIED_USER_MS = 2000, 2500
COPIED_PASS_DISMISS_MS = 1200
FLUTTER_DONE_DISMISS_MS = 400
FOCUS_POLL_MS = 200                   # fill.FocusWatcher.POLL_S (no window events)
FOCUS_POLL_EVENTS_MS = 400            # safety net when focus events come from the hub
FOCUS_RECHECK_MS = 80                 # after a focus event: once more, in case UIA lags the event
FLUTTER_PROBE_MS = 1500               # look for boxes this long after the card shows ...
FLUTTER_PROBE_TRIES = 3               # ... again while the page has no Document yet
FLUTTER_HOSTS = ("tdscpc.gov.in", "traces")

USER_TEXT, PASS_TEXT = "👤  Username", "🔑  Password"
USER_DONE, USER_COPIED = "✓  Username Injected", "📋  Copied Username (Ctrl+V)"
PASS_DONE, PASS_COPIED = "✓  Password Injected", "📋  Copied Password (Ctrl+V)"
STEP1_TITLE, STEP1_DESC = ("👉 Step 1: Click the Username box",
                           "Click or tap inside the User ID box on the page. Sera will type it automatically!")
STEP2_TITLE, STEP2_DESC = ("Step 2: Click the Password box",
                           "Next, click inside the Password box on the page. Sera will type it for you!")
STEP2_ACTIVE_TITLE, STEP2_ACTIVE_DESC = ("👉 Step 2: Click the Password box",
                                         "Now tap or click inside the Password box on the page!")
STEP1_DONE, STEP2_DONE = "✓  Username Typed", "✓  Password Typed"


def is_flutter_service(service) -> bool:
    """TRACES runs on Flutter (the extension's isFlutterPage host rule)."""
    link = service.get("login_page_link") if isinstance(service, dict) else getattr(service, "login_page_link", "")
    link = (link or "").strip()
    host = (urlparse(link if "//" in link else "//" + link).hostname or "").lower()
    return any(h in host for h in FLUTTER_HOSTS)


# ----------------------------------------------------------------------------- worker jobs
def _fill_job(hwnd: int, service, kind: str, text: str, user_sel, pass_sel):
    """The outcome, or ("filled", late) where late is the Future of fill's 1.0 s re-check."""
    from core.os_assist import fill, locate
    user_el, pass_el = locate.find_fields_cached(hwnd, user_sel, pass_sel, want=kind)
    el = user_el if kind == "user" else pass_el
    if el is None:
        return "nobox"
    res = fill.fill(hwnd, el, text, kind == "pass", service, late_check="background")
    if res.outcome == "filled" and res.late is not None:
        return ("filled", res.late)
    return res.outcome


def _warm_job(hwnd: int, user_sel, pass_sel) -> bool:
    from core.os_assist import locate
    return locate.warm_fields(hwnd, user_sel, pass_sel)


def _edit_count_job(hwnd: int) -> int:
    """Visible Edits on the page; -1 while the page has no on-screen Document."""
    from core.os_assist import locate, worker
    docs = locate.page_documents(hwnd)
    if not docs:
        return -1
    uia = worker.current_uia()
    return sum(1 for doc in docs for el in uia.edits(doc) if locate.visible(locate.describe(el)))


class WorkerFiller:
    """The card's UIA jobs on the OS Assist worker; each call returns a Future. One per card (it
    keeps the card's FocusWatcher, used on the worker thread only). Tests pass a fake."""

    def __init__(self) -> None:
        self._watcher = None

    def fill(self, hwnd: int, service, kind: str, text: str, user_sel, pass_sel):
        from core.os_assist import worker
        return worker.submit_urgent(_fill_job, hwnd, service, kind, text, user_sel, pass_sel)

    def warm(self, hwnd: int, user_sel, pass_sel):
        from core.os_assist import worker
        return worker.submit(_warm_job, hwnd, user_sel, pass_sel)

    def edit_count(self, hwnd: int):
        from core.os_assist import worker
        return worker.submit(_edit_count_job, hwnd)

    def focus_step(self, hwnd: int, service, text: str, is_password: bool):
        """None while no new box has focus; else the outcome of typing `text` into it."""
        from core.os_assist import worker
        return worker.submit(self._focus_job, hwnd, service, text, is_password)

    def _focus_job(self, hwnd: int, service, text: str, is_password: bool) -> Optional[str]:
        from core.os_assist import fill
        if self._watcher is None or self._watcher.hwnd != hwnd:
            self._watcher = fill.FocusWatcher(hwnd)
        if self._watcher.poll() is None:
            return None
        return fill.type_into_focused(hwnd, text, is_password, service).outcome


def _copy(text: str, secret: bool, clear_after_s: Optional[int]) -> None:
    from core.os_assist import secure_clipboard
    secure_clipboard.copy(text, secret, clear_after_s)


def _repolish(w: QWidget) -> None:
    for x in [w, *w.findChildren(QWidget)]:
        x.style().unpolish(x)
        x.style().polish(x)


class _StepCard(QFrame):
    """One `.guide-step`: number bubble, title, description; state "" | active | done."""

    def __init__(self, num: str, title: str, desc: str, parent=None):
        super().__init__(parent)
        self.setObjectName("step")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(css_px(13), css_px(11), css_px(13), css_px(11))
        lay.setSpacing(css_px(12))
        self.num = QLabel(num, self)
        self.num.setObjectName("num")
        self.num.setAlignment(Qt.AlignCenter)
        self.num.setFixedSize(css_px(28), css_px(28))
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(css_px(2.5))
        self.title = QLabel(title, self)
        self.title.setObjectName("stitle")
        self.desc = QLabel(desc, self)
        self.desc.setObjectName("sdesc")
        self.desc.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.desc)
        lay.addWidget(self.num, 0, Qt.AlignVCenter)
        lay.addLayout(text, 1)
        self.set_state("")

    @property
    def state(self) -> str:
        return self.property("state") or ""

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        _repolish(self)


class SmtiCard(DockCard):
    """The OS SMTI card. `attach(browser_hwnd, service)` then `present()` (the launcher's job)."""

    _deliver = Signal(object)         # a callable run on the Qt thread (worker results)
    _focus_event = Signal()           # from the WinEvent hub thread: a box in the browser took focus

    def __init__(self, service, user_id: str, password: str, client_id=None, client_name: str = "",
                 clear_seconds: Optional[int] = 30, timeout_ms: int = TIMEOUT_MS,
                 flutter: Optional[bool] = None, filler=None,
                 copier: Optional[Callable[[str, bool, Optional[int]], None]] = None,
                 sck_handler: Optional[Callable[[dict], None]] = None, **kw):
        super().__init__(timeout_ms=timeout_ms, **kw)
        self._deliver.connect(self._run_delivered)          # queued: emitted on the worker thread
        self._focus_event.connect(self._on_focus_event, Qt.QueuedConnection)
        self._focus_again = False                # a focus event came while a focus job ran
        self.service = service
        self.hold_secret("user", user_id or "")
        self.hold_secret("pass", password or "")
        get = service.get if isinstance(service, dict) else (lambda k, d=None: getattr(service, k, d))
        self._user_sel = get("username_selector", "") or ""
        self._pass_sel = get("password_selector", "") or ""
        self._clear_s = clear_seconds
        self._filler = filler if filler is not None else WorkerFiller()
        self._copier = copier or _copy
        self._busy = {"user": False, "pass": False}
        self._used = False                       # a button was pressed: no Flutter switch after that
        self._step = 0                           # Flutter: 0 user ID, 1 password, 2 done
        self._focus_job = None
        self._jobs: set = set()                  # worker jobs not done yet; the queued ones die with the card
        self._probe_tries = 0
        self.flutter = False

        wire_sck_edit(self, client_id, get("id"), str(get("name", "portal") or "portal").lower(),
                      handler=sck_handler)
        self._build_body(client_name or get("_client_name") or "Client Profile")

        self._user_revert = QTimer(self)
        self._user_revert.setSingleShot(True)
        self._user_revert.timeout.connect(lambda: self._set_btn(self.user_btn, "primary", USER_TEXT))
        self._focus_poll = QTimer(self)
        self._focus_poll.setInterval(FOCUS_POLL_MS)
        self._focus_poll.timeout.connect(self._poll_focus)

        self._flutter_auto = flutter is None
        if flutter or (flutter is None and is_flutter_service(service)):
            self._enter_flutter()
            self._flutter_auto = False

    # -- look ----------------------------------------------------------------------------------
    def _build_body(self, title: str) -> None:
        self.body.setSpacing(0)
        self.title = QLabel(title, self.card)
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.body.addWidget(self.title)
        self.body.addSpacing(css_px(14))

        self.actions = QWidget(self.card)
        a = QVBoxLayout(self.actions)
        a.setContentsMargins(0, 0, 0, 0)
        a.setSpacing(css_px(9))
        self.user_btn = self._button(USER_TEXT)
        self.pass_btn = self._button(PASS_TEXT)
        self.user_btn.clicked.connect(lambda: self._press("user"))
        self.pass_btn.clicked.connect(lambda: self._press("pass"))
        a.addWidget(self.user_btn)
        a.addWidget(self.pass_btn)
        self.body.addWidget(self.actions)

        self.guide = QWidget(self.card)
        g = QVBoxLayout(self.guide)
        g.setContentsMargins(0, css_px(2), 0, css_px(2))
        g.setSpacing(css_px(9))
        self.step1 = _StepCard("1", STEP1_TITLE, STEP1_DESC, self.guide)
        self.step2 = _StepCard("2", STEP2_TITLE, STEP2_DESC, self.guide)
        g.addWidget(self.step1)
        g.addWidget(self.step2)
        self.body.addWidget(self.guide)
        self.guide.hide()

    def _button(self, text: str) -> QPushButton:
        b = QPushButton(text, self.actions)
        b.setObjectName("btn")
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("state", "primary")
        return b

    def card_qss(self) -> str:
        """The extension's `.client-title`, `.btn` (primary / done / warn) and `.guide-step` CSS."""
        px = css_px
        return super().card_qss() + f"""
            QLabel#title {{
                font-size: {px(15.5)}px; font-weight: 800; color: #FFFFFF; background: transparent;
            }}
            QPushButton#btn {{
                padding: {px(10.5)}px {px(14)}px; border: 1px solid #1E4D34; border-radius: {px(9)}px;
                background: #14281E; color: #E2F8EE;
                font-family: 'Segoe UI'; font-size: {px(13)}px; font-weight: 700;
            }}
            QPushButton#btn:hover {{ background: #1B3D2B; border-color: #2E9B5F; color: #FFFFFF; }}
            QPushButton#btn[state="primary"] {{ background: #2E9B5F; border-color: #34B76D; color: #FFFFFF; }}
            QPushButton#btn[state="primary"]:hover {{ background: #34B76D; }}
            QPushButton#btn[state="done"] {{ background: #102B1E; border-color: #2E9B5F; color: #4CF9B7; }}
            QPushButton#btn[state="warn"] {{ border-color: #E8A040; color: #F5C97A; }}
            QFrame#step {{
                background: rgba(255, 255, 255, 0.03);
                border: {max(1, px(1.5))}px solid #222826;   /* rgba(255,255,255,.08) on the card: Qt overdraws a translucent border's corners */
                border-radius: {px(10)}px;
            }}
            QFrame#step[state="active"] {{ background: rgba(46, 155, 95, 0.16); border-color: #2E9B5F; }}
            QFrame#step[state="done"] {{ background: rgba(16, 43, 30, 0.55); border-color: #1E7E48; }}
            QFrame#step QLabel {{ background: transparent; border: none; }}
            QFrame#step QLabel#num {{
                background: #18241E; border: {max(1, px(1.5))}px solid #334D3E; border-radius: {px(14)}px;
                color: #8EB7A0; font-size: {px(13)}px; font-weight: 800;
            }}
            QFrame#step[state="active"] QLabel#num {{ background: #2E9B5F; border-color: #4CF9B7; color: #FFFFFF; }}
            QFrame#step[state="done"] QLabel#num {{
                background: #102B1E; border-color: #4CF9B7; color: #4CF9B7; font-weight: 900;
            }}
            QFrame#step QLabel#stitle {{ font-size: {px(13.5)}px; font-weight: 700; color: #FFFFFF; }}
            QFrame#step QLabel#sdesc {{ font-size: {px(11.5)}px; color: #8BA295; }}
            QFrame#step[state="active"] QLabel#sdesc {{ color: #BDEFD6; }}
            QFrame#step[state="done"] QLabel#sdesc {{ color: #5D806E; }}
        """

    def _set_btn(self, btn: QPushButton, state: str, text: str) -> None:
        """The extension's setBtn: "done" | "warn" | anything else = primary."""
        btn.setProperty("state", state if state in ("done", "warn") else "primary")
        btn.setText(text)
        _repolish(btn)

    # -- worker results --------------------------------------------------------------------------
    def _when_done(self, job, fn: Callable) -> None:
        """Run fn(result) on the Qt thread once the worker job is done (a plain value: now)."""
        if not hasattr(job, "add_done_callback"):
            fn(job)
            return
        self._jobs.add(job)

        def _cb(fut):
            self._jobs.discard(fut)
            try:
                value = fut.result()
            except Exception:
                value = "failed"
            self._deliver.emit(lambda: fn(value))

        job.add_done_callback(_cb)

    def _run_delivered(self, fn) -> None:
        fn()

    # -- buttons ---------------------------------------------------------------------------------
    def _press(self, kind: str) -> None:
        if self.is_dismissed or self._busy[kind]:
            return
        self._used = True
        if self.timer is not None:
            self.timer.reset()
        self._busy[kind] = True
        try:
            job = self._filler.fill(self.browser_hwnd, self.service, kind, self.secret(kind) or "",
                                    self._user_sel, self._pass_sel)
        except Exception:
            job = "failed"
        self._when_done(job, lambda outcome: self._pressed(kind, outcome))

    def _pressed(self, kind: str, outcome) -> None:
        late = None
        if isinstance(outcome, tuple):
            outcome, late = outcome
        if late is None or self.is_dismissed:
            self._busy[kind] = False                  # else busy until the late check is in
        if self.is_dismissed:
            if late is not None:
                late.cancel()
            return
        if kind == "user":
            if outcome == "filled":
                self._set_btn(self.user_btn, "done", USER_DONE)
                self._user_revert.start(INJECTED_MS)
            else:
                self._user_copied()
        elif outcome == "filled" and late is None:
            self.dismiss("filled")
            return
        elif outcome == "filled":
            self._set_btn(self.pass_btn, "done", PASS_DONE)
        else:
            self._pass_copied()
            return
        if late is not None:
            self._when_done(late, lambda verdict: self._late_checked(kind, verdict))

    def _late_checked(self, kind: str, verdict) -> None:
        """fill's 1.0 s re-check: "" = the value stayed. Anything else = treat it as not filled."""
        self._busy[kind] = False
        if self.is_dismissed:
            return
        ok = verdict == ""
        if kind == "user":
            if not ok:
                self._user_copied()
            return
        if ok:
            self.dismiss("filled")
        else:
            self._pass_copied()

    def _user_copied(self) -> None:
        self._copy("user")
        self._set_btn(self.user_btn, "done", USER_COPIED)
        self._user_revert.start(COPIED_USER_MS)

    def _pass_copied(self) -> None:
        self._copy("pass")
        self._set_btn(self.pass_btn, "done", PASS_COPIED)
        QTimer.singleShot(COPIED_PASS_DISMISS_MS, lambda: self.dismiss("copied"))

    def _copy(self, kind: str) -> None:
        text = self.secret(kind)
        if not text:
            return
        secret = kind == "pass"
        try:
            self._copier(text, secret, self._clear_s if secret else None)
        except Exception:
            pass

    # -- Flutter steps ---------------------------------------------------------------------------
    def present(self) -> None:
        super().present()
        if self.events_active:
            self._focus_poll.setInterval(FOCUS_POLL_EVENTS_MS)
        if self.flutter and not self.is_dismissed:
            self._focus_poll.start()
        if not self.flutter and not self.is_dismissed and self.browser_hwnd:
            warm = getattr(self._filler, "warm", None)
            if warm is not None:
                try:
                    self._when_done(warm(self.browser_hwnd, self._user_sel, self._pass_sel), lambda _r: None)
                except Exception:
                    pass
        if self._flutter_auto and not self.is_dismissed:
            QTimer.singleShot(FLUTTER_PROBE_MS, self._probe_flutter)

    def _probe_flutter(self) -> None:
        if self.is_dismissed or self.flutter or self._used:
            return
        self._probe_tries += 1
        try:
            job = self._filler.edit_count(self.browser_hwnd)
        except Exception:
            return
        self._when_done(job, self._probed)

    def _probed(self, count) -> None:
        if self.is_dismissed or self.flutter or self._used or not isinstance(count, int):
            return
        if count == 0:
            self._enter_flutter()
        elif count < 0 and self._probe_tries < FLUTTER_PROBE_TRIES:
            QTimer.singleShot(FLUTTER_PROBE_MS, self._probe_flutter)

    def _enter_flutter(self) -> None:
        self.flutter = True
        self._step = 0
        self.actions.hide()
        self.guide.show()
        self.step1.set_state("active")
        self.step2.set_state("")
        self.refit()
        if self._presented:                      # else present() starts it
            self._focus_poll.start()

    def _focus_handler(self):
        return self._focus_event.emit             # hub thread -> queued to the Qt thread

    def _on_focus_event(self) -> None:
        if not self.flutter or self.is_dismissed or self._step >= 2:
            return
        if self._focus_job is not None:
            self._focus_again = True              # looked at again as soon as that job ends
            return
        self._poll_focus()
        QTimer.singleShot(FOCUS_RECHECK_MS, self._poll_focus)

    def _poll_focus(self) -> None:
        if self.is_dismissed or self._step >= 2:
            self._focus_poll.stop()
            return
        if self._focus_job is not None:
            return
        if self.hidden_reason or not self.browser_hwnd:
            return
        kind = "user" if self._step == 0 else "pass"
        try:
            job = self._filler.focus_step(self.browser_hwnd, self.service, self.secret(kind) or "",
                                          kind == "pass")
        except Exception:
            return
        self._focus_job = job
        self._when_done(job, lambda outcome: self._stepped(kind, outcome))

    def _stepped(self, kind: str, outcome) -> None:
        self._focus_job = None
        again, self._focus_again = self._focus_again, False
        if self.is_dismissed or self._step != (0 if kind == "user" else 1):
            return
        if outcome is None:
            if again:
                self._poll_focus()
            return
        filled = outcome == "filled"
        if not filled:
            self._copy(kind)                      # as the extension: not typed -> copied
        if kind == "user":
            self._step = 1
            self.step1.set_state("done")
            self.step1.num.setText("✓")
            self.step1.title.setText(STEP1_DONE if filled else USER_COPIED)
            self.step2.set_state("active")
            self.step2.title.setText(STEP2_ACTIVE_TITLE)
            self.step2.desc.setText(STEP2_ACTIVE_DESC)
            return
        self._step = 2
        self._focus_poll.stop()
        self.step2.set_state("done")
        self.step2.num.setText("✓")
        self.step2.title.setText(STEP2_DONE if filled else PASS_COPIED)
        QTimer.singleShot(FLUTTER_DONE_DISMISS_MS if filled else COPIED_PASS_DISMISS_MS,
                          lambda: self.dismiss("filled" if filled else "copied"))

    # -- out -------------------------------------------------------------------------------------
    def dismiss(self, reason: str = "close") -> None:
        """A job still queued on the worker (a press or a focus poll just before ✕) is cancelled, so
        nothing is typed after the card is gone; one already running finishes."""
        self._focus_poll.stop()
        self._user_revert.stop()
        super().dismiss(reason)
        for job in list(self._jobs):            # after: a cancelled job's result finds the card dismissed
            job.cancel()
