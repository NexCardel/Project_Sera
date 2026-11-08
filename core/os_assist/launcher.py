"""
OS Assist launcher: SMTI and MECP through the OS engine (blueprint §3, §4.4, §4.7).

`launch(kind, service, ...)` opens the service's login page in the default browser (a new tab every
time, owner answer 3), waits up to 20 s for a browser window whose address bar is on the service's
host (locate.window_for_host, each look a job on the UIA worker; the wait itself never holds the
worker), then shows the card on the Qt main thread, attached to that window. No window in time ->
on_error with a clear message.

One live card per kind: a new launch dismisses the old card at once (its secrets go with it) and a
launch still waiting is superseded. User ID and password live only in the waiting closure and then
in the card's secrets; never logged. clipboard_watch.suppress_client stays the caller's job (Client
Detail does it before every MECP/SMTI launch, in both engines).
"""

import threading
import time
from typing import Callable, Dict, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal

from core.os_assist import locate, worker

KINDS = ("smti", "mecp")
WAIT_S = 20.0                     # how long a launch waits for the portal's browser window
FIRST_LOOK_S = 1.0                # let the new tab take the front before the first look (D4)
POLL_S = 0.5
LOOK_TIMEOUT_S = 5.0              # one window_for_host job that hangs counts as "not yet"
SMTI_TIMEOUT_MS = 30000           # the extension's SMTI duration (injectManualAssist)
MECP_TIMEOUT_MS = 90000           # injectMECP's expiresMs


def _get(service, key, default=None):
    if isinstance(service, dict):
        return service.get(key, default)
    return getattr(service, key, default)


def timeout_message(service) -> str:
    name = str(_get(service, "name", "") or "the portal")
    return (f"Sera could not find {name} in a browser window within {int(WAIT_S)} seconds.\n"
            "Open the portal's login page in your browser and press the button again.")


class _QtCall(QObject):
    """Runs a callable on the Qt main thread (queued from any thread)."""
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    def _run(self, fn) -> None:
        fn()


def _default_opener(url: str, browser: Optional[str]) -> None:
    import automation
    automation.open_in_default_browser(url, browser)


def _default_finder(url: str) -> int:
    try:
        return int(worker.submit(locate.window_for_host, url).result(timeout=LOOK_TIMEOUT_S) or 0)
    except Exception:
        return 0


_clear_seconds_provider: Optional[Callable[[], object]] = None


def set_clear_seconds_provider(provider: Optional[Callable[[], object]]) -> None:
    """main.py points this at the *clipboard clear* seconds setting."""
    global _clear_seconds_provider
    _clear_seconds_provider = provider


def clear_seconds() -> int:
    try:
        value = _clear_seconds_provider() if _clear_seconds_provider else 30
        return max(5, min(int(value), 300))
    except Exception:
        return 30


def _default_card_factory(kind: str, service, user_id: str, password: str, client_id,
                          client_name: str, clear_s: int):
    if kind == "smti":
        from ui.os_assist.smti_card import SmtiCard
        return SmtiCard(service, user_id, password, client_id=client_id, client_name=client_name,
                        clear_seconds=clear_s, timeout_ms=SMTI_TIMEOUT_MS)
    from ui.os_assist.mecp_card import MecpCard
    return MecpCard(service, user_id, password, client_id=client_id, client_name=client_name,
                    clear_seconds=clear_s, timeout_ms=MECP_TIMEOUT_MS)


class Launcher:
    def __init__(self, opener: Callable[[str, Optional[str]], None] = None,
                 finder: Callable[[str], int] = None, card_factory=None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 spawn: Callable[[Callable[[], None]], None] = None) -> None:
        self._opener = opener or _default_opener
        self._finder = finder or _default_finder
        self._factory = card_factory or _default_card_factory
        self._sleep = sleep
        self._clock = clock
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, name="os-assist-launch",
                                                            daemon=True).start())
        self._lock = threading.Lock()
        self._gen: Dict[str, int] = {k: 0 for k in KINDS}
        self._cards: Dict[str, object] = {}       # Qt thread only
        self._qt: Optional[_QtCall] = None

    # -- threads -------------------------------------------------------------------------------
    def _on_qt(self, fn) -> None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None or QThread.currentThread() is app.thread():
            fn()
            return
        if self._qt is None:
            self._qt = _QtCall()
            self._qt.moveToThread(app.thread())
        self._qt.call.emit(fn)

    def _current(self, kind: str, gen: int) -> bool:
        with self._lock:
            return self._gen[kind] == gen

    # -- API -----------------------------------------------------------------------------------
    def launch(self, kind: str, service, user_id: str, password: str, client_id=None,
               client_name: str = "", on_error: Optional[Callable[[str], None]] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown OS Assist card kind: {kind!r}")
        with self._lock:
            self._gen[kind] += 1
            gen = self._gen[kind]
        self._on_qt(lambda: self.dismiss(kind, "replaced"))

        url = str(_get(service, "login_page_link", "") or "").strip()
        if not url:
            if on_error:
                on_error(f"No login page is set for {_get(service, 'name', 'this service')}.")
            return
        browser = str(_get(service, "browser", "") or "").strip().lower() or None
        creds = {"user": user_id or "", "pass": password or ""}

        def _wait() -> None:
            try:
                try:
                    self._opener(url, browser)
                except Exception:
                    pass
                deadline = self._clock() + WAIT_S
                self._sleep(FIRST_LOOK_S)
                while self._current(kind, gen):
                    hwnd = self._finder(url)
                    if hwnd:
                        user, pwd = creds["user"], creds["pass"]
                        self._on_qt(lambda: self._show(kind, gen, hwnd, service, user, pwd,
                                                       client_id, client_name, on_error))
                        return
                    if self._clock() >= deadline:
                        break
                    self._sleep(POLL_S)
                if self._current(kind, gen) and on_error:
                    on_error(timeout_message(service))
            finally:
                creds.clear()

        self._spawn(_wait)

    def dismiss(self, kind: str, reason: str = "close") -> None:
        """Qt thread: dismiss the live card of that kind (its secrets are cleared by the card)."""
        card = self._cards.pop(kind, None)
        if card is not None and not card.is_dismissed:
            card.dismiss(reason)

    def card(self, kind: str):
        return self._cards.get(kind)

    # -- Qt thread -----------------------------------------------------------------------------
    def _show(self, kind, gen, hwnd, service, user_id, password, client_id, client_name, on_error):
        if not self._current(kind, gen):
            return                                    # a newer launch of this kind took over
        self.dismiss(kind, "replaced")
        card = None
        try:
            card = self._factory(kind, service, user_id, password, client_id,
                                 client_name or _get(service, "_client_name", "") or "", clear_seconds())
            card.attach(hwnd, service)
        except Exception:
            if card is not None:
                card.dismiss("error")                 # drops the secrets it took
            if on_error:
                on_error("Sera could not show the card over the browser window.")
            return
        self._cards[kind] = card
        card.dismissed.connect(lambda _reason, c=card, k=kind: self._forget(k, c))
        card.present()

    def _forget(self, kind: str, card) -> None:
        if self._cards.get(kind) is card:
            del self._cards[kind]


_default: Optional[Launcher] = None


def default() -> Launcher:
    global _default
    if _default is None:
        _default = Launcher()
    return _default


def launch(kind: str, service, user_id: str, password: str, client_id=None, client_name: str = "",
           on_error: Optional[Callable[[str], None]] = None) -> None:
    default().launch(kind, service, user_id, password, client_id, client_name, on_error)
