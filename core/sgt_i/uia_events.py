"""
core/sgt_i/uia_events.py - catch what flashes: UIA events for short-lived messages
==================================================================================
Blueprint 14.4 step 9. The Core polls a page every tick (0.35 s) and only reads it again when it
seems to change, so a toast shown for under a second can be gone before the read. The browser
announces such messages to screen readers anyway, as UI Automation events - this module listens
to three of them, the same read-only OS channel screen readers use:

  * live-region-changed (a status / alert region's text changed - the usual toast),
  * notification (IUIAutomation5: a message the page asks to be read out),
  * window-opened (a dialog / window appeared inside the page).

Strictly passive: registering for an event and reading the sender's CACHED properties is all this
does - never a pattern method, never focus, never a keystroke or click.

Scope: handlers are registered on the Document elements (page content) of a window the Core has
already scope-gated and handed to SGT-I - never on the desktop or the browser chrome - and
re-registered when that window's URL changes (a new page is a new Document). Text from other
tabs or apps is never delivered.

Threading (decision W10-1): the listener has its OWN multithreaded-apartment COM thread, not
vsdc_uia_text's read worker. That worker is a single-threaded apartment with no message loop -
UIA would only deliver events to it while it pumps messages - and it is replaced when a read
hangs, which would silently drop every registration. In an MTA, UIA calls the handlers on its own
threads; they only read the cached subtree that came with the event (a CacheRequest given at
registration, so no cross-process call) and append to a bounded buffer. The same guard as the
read worker applies: a registration still running after HANG_SEC switches the listener off for
the rest of the run (the thread is abandoned, never waited for), and the Core never notices.

What a handler caught is text in memory only; the host turns it into an Observation
(source "uia_event") for the SGT-I components - nothing here writes it anywhere.
"""

import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, NamedTuple, Optional, Tuple

# UIA event ids (UIAutomationClient.h).
EVENT_WINDOW_OPENED = 20016
EVENT_LIVE_REGION_CHANGED = 20024
EVENT_NOTIFICATION = 20035
KIND_BY_EVENT = {EVENT_LIVE_REGION_CHANGED: "live_region", EVENT_NOTIFICATION: "notification",
                 EVENT_WINDOW_OPENED: "window_opened"}

HANG_SEC = 10.0            # a registration still running this long = hung
MAX_WINDOWS = 4            # windows listened to at once; the least recently seen is dropped
MAX_PENDING = 32           # flashes waiting per window; the oldest is dropped when full
FLASH_TTL_SEC = 30.0       # a flash nobody took by then is dropped
DEDUPE_SEC = 2.0           # the same text from the same event again this soon is one flash
MAX_FLASH_LINES = 40
MAX_LINE_CHARS = 300

_SCOPE_DESCENDANTS = 4
_SCOPE_SUBTREE = 7
_CT_DOCUMENT = 50030


class Flash(NamedTuple):
    kind: str                  # "live_region" / "notification" / "window_opened"
    lines: Tuple[str, ...]
    ts: float                  # monotonic time it was caught


def element_lines(sender: Any, extra: str = "") -> Tuple[str, ...]:
    """The text an event's sender carries, from its cached subtree only (no live call): the
    sender's own name, then its descendants in document order, as today's reader would list them."""
    from .uia_nodes import _node, _walk, lines_from_nodes

    nodes: List[Dict[str, Any]] = []
    try:
        nodes.append(_node(sender, -1, 0))
        _walk(sender, 0, 1, nodes)
    except Exception:
        pass
    lines = ([extra.strip()] if extra and extra.strip() else []) + lines_from_nodes([nodes])
    out: List[str] = []
    for ln in lines:
        ln = ln[:MAX_LINE_CHARS]
        if ln and (not out or out[-1] != ln):
            out.append(ln)
        if len(out) >= MAX_FLASH_LINES:
            break
    return tuple(out)


class _UiaBackend:
    """The real registrations. Every method runs on FlashWatcher's own MTA thread."""

    def __init__(self) -> None:
        import comtypes
        import comtypes.client
        from .uia_nodes import build_cache_request

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        self._client = comtypes.client.GetModule("UIAutomationCore.dll")
        try:        # CUIAutomation8 is the class that also answers IUIAutomation2..6 (Windows 8+)
            self._uia = comtypes.client.CreateObject(self._client.CUIAutomation8,
                                                     interface=self._client.IUIAutomation)
        except Exception:
            self._uia = comtypes.client.CreateObject(self._client.CUIAutomation,
                                                     interface=self._client.IUIAutomation)
        try:
            self._uia5 = self._uia.QueryInterface(self._client.IUIAutomation5)
        except Exception:
            self._uia5 = None          # before Windows 10 1709: no notification events
        self._cache = build_cache_request(self._uia)
        self._classes = _handler_classes(self._client)

    def register(self, hwnd: int, sink: Callable[[str, Tuple[str, ...]], None]) -> List[Tuple]:
        auto_cls, note_cls = self._classes
        root = self._uia.ElementFromHandle(hwnd)
        cond = self._uia.CreatePropertyCondition(self._client.UIA_ControlTypePropertyId, _CT_DOCUMENT)
        found = root.FindAll(_SCOPE_DESCENDANTS, cond) if root else None
        tokens: List[Tuple] = []
        for i in range(found.Length if found else 0):
            doc = found.GetElement(i)
            handler = auto_cls(sink)
            for event in (EVENT_LIVE_REGION_CHANGED, EVENT_WINDOW_OPENED):
                self._uia.AddAutomationEventHandler(event, doc, _SCOPE_SUBTREE, self._cache, handler)
                tokens.append(("auto", event, doc, handler))
            if self._uia5 is not None:
                note = note_cls(sink)
                self._uia5.AddNotificationEventHandler(doc, _SCOPE_SUBTREE, self._cache, note)
                tokens.append(("note", EVENT_NOTIFICATION, doc, note))
        return tokens

    def unregister(self, tokens: List[Tuple]) -> None:
        for how, event, doc, handler in tokens:
            try:
                if how == "auto":
                    self._uia.RemoveAutomationEventHandler(event, doc, handler)
                else:
                    self._uia5.RemoveNotificationEventHandler(doc, handler)
            except Exception:
                pass           # the page is gone already: nothing is delivered for it anyway


def _handler_classes(client: Any) -> Tuple[type, type]:
    """The two COM event sinks. Built on first use: the interfaces come from comtypes' generated
    UIAutomationCore wrapper. Each handler only reads the sender's cache and hands text on."""
    from comtypes import COMObject

    class _AutomationHandler(COMObject):
        _com_interfaces_ = [client.IUIAutomationEventHandler]

        def __init__(self, sink):
            super().__init__()
            self._sink = sink

        def HandleAutomationEvent(self, sender, event_id):
            try:
                kind = KIND_BY_EVENT.get(int(event_id))
                if kind:
                    self._sink(kind, element_lines(sender))
            except Exception:
                pass
            return 0

    class _NotificationHandler(COMObject):
        _com_interfaces_ = [client.IUIAutomationNotificationEventHandler]

        def __init__(self, sink):
            super().__init__()
            self._sink = sink

        def HandleNotificationEvent(self, sender, *args):
            try:
                said = next((a for a in args if isinstance(a, str)), "")   # the displayString
                self._sink("notification", element_lines(sender, said))
            except Exception:
                pass
            return 0

    return _AutomationHandler, _NotificationHandler


class _Window:
    __slots__ = ("key", "tokens", "pending", "seen")

    def __init__(self) -> None:
        self.key: Optional[str] = None      # the URL the registrations were made for
        self.tokens: List[Tuple] = []
        self.pending: Deque[Flash] = deque(maxlen=MAX_PENDING)
        self.seen = 0.0


class FlashWatcher:
    """Listens for short-lived messages in the windows SGT-I is told about. The Core-side calls
    (watch, take, pending, stop) never block and never raise; all COM work is on its own thread."""

    def __init__(self, backend_factory: Optional[Callable[[], Any]] = None, hang_sec: float = HANG_SEC,
                 echo: Callable[[str], None] = print,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._factory = backend_factory or _UiaBackend
        self._hang = float(hang_sec)
        self._echo = echo
        self._now = monotonic
        self._lock = threading.Lock()
        self._windows: Dict[int, _Window] = {}
        self._commands: Optional["queue.Queue"] = None
        self._busy_since: Optional[float] = None
        self.disabled: Optional[str] = None
        self.caught = 0

    # ── Core side ──────────────────────────────────────────────────────────────────────────────
    def watch(self, hwnd: int, key: str = "") -> None:
        """Listen to this window's page; a changed key (the URL) re-registers on the new page."""
        try:
            if not hwnd or self.disabled:
                return
            busy = self._busy_since
            if busy is not None and self._now() - busy > self._hang:
                self._disable(f"a UIA event registration has been running for over {self._hang:.0f} s (hung)")
                return
            with self._lock:
                w = self._windows.pop(hwnd, None) or _Window()
                self._windows[hwnd] = w                  # most recently seen last
                w.seen = self._now()
                stale = []
                while len(self._windows) > MAX_WINDOWS:
                    old = next(iter(self._windows))
                    stale.append(self._windows.pop(old))
                if w.key == key and not stale:
                    return
                changed = w.key != key
                w.key = key
                if self._commands is None:
                    if self._factory is _UiaBackend:
                        # comtypes' FIRST import CoInitializes the importing thread as a single-
                        # threaded apartment - that must not be the listener, which needs an MTA.
                        import comtypes  # noqa: F401
                    self._commands = queue.Queue()
                    threading.Thread(target=self._run, args=(self._commands,), name="sgt-i-uia-events",
                                     daemon=True).start()
                for old in stale:
                    self._commands.put(("drop", old))
                if changed:
                    self._commands.put(("watch", hwnd, w))
        except Exception:
            pass

    def take(self, hwnd: int) -> List[Flash]:
        """What flashed in this window since the last take (oldest first), then forgotten."""
        try:
            with self._lock:
                w = self._windows.get(hwnd)
                if w is None or not w.pending:
                    return []
                cutoff = self._now() - FLASH_TTL_SEC
                got = [f for f in w.pending if f.ts >= cutoff]
                w.pending.clear()
                return got
        except Exception:
            return []

    def pending(self, hwnd: int) -> bool:
        try:
            w = self._windows.get(hwnd)
            return bool(w is not None and w.pending)
        except Exception:
            return False

    def stop(self) -> None:
        """Unregister everything (SGT-I switched off). A later watch() starts afresh."""
        with self._lock:
            commands, self._commands = self._commands, None
            self._windows.clear()
        if commands is not None:
            commands.put(("stop",))

    def _disable(self, why: str) -> None:
        with self._lock:
            if self.disabled:
                return
            self.disabled = why
            self._windows.clear()
            self._commands = None           # the wedged thread is abandoned, never waited for
        self._echo(f"[SGT-I] UIA event listening off for the rest of this run: {why}")

    # ── What a handler calls (a UIA thread) ────────────────────────────────────────────────────
    def _sink_for(self, w: _Window) -> Callable[[str, Tuple[str, ...]], None]:
        def sink(kind: str, lines: Tuple[str, ...]) -> None:
            if not lines:
                return
            now = self._now()
            with self._lock:
                for f in reversed(w.pending):
                    if now - f.ts > DEDUPE_SEC:
                        break
                    if f.kind == kind and f.lines == lines:
                        return
                w.pending.append(Flash(kind, lines, now))
                self.caught += 1
        return sink

    # ── The listener's own thread ──────────────────────────────────────────────────────────────
    def _run(self, commands: "queue.Queue") -> None:
        backend = None
        try:
            self._busy_since = self._now()
            backend = self._factory()
        except Exception as e:
            self._busy_since = None
            self._disable(f"UI Automation events unavailable: {type(e).__name__}: {e}")
            return
        self._busy_since = None
        live: Dict[int, _Window] = {}          # windows this thread registered for
        while True:
            cmd = commands.get()
            if cmd[0] == "stop":
                break
            w: _Window = cmd[-1]
            self._busy_since = self._now()
            try:
                backend.unregister(w.tokens)
                w.tokens = []
                live.pop(id(w), None)
                if cmd[0] == "watch":
                    w.tokens = backend.register(cmd[1], self._sink_for(w))
                    live[id(w)] = w
            except Exception:
                pass
            finally:
                if cmd[0] == "watch" and not w.tokens:
                    with self._lock:
                        w.key = None   # window or page gone, or a cold page with no Document yet:
                                       # the next watch() of it tries again
                self._busy_since = None
        # Only this thread's own registrations: a listener started after a stop() keeps its own.
        for w in live.values():
            backend.unregister(w.tokens)
            w.tokens = []
