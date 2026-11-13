"""
OS Assist fill: the one writer (blueprint rule 2, 4.1, 4.3). Only this module calls SetValue, SetFocus
or SendInput. Runs on the OS Assist worker thread. Never logs or keeps a value: the log line is
`user|pass  outcome  reason`, and a password box is judged by its bullet count only (D2).
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from core.os_assist import locate, worker

log = logging.getLogger("os_assist.fill")

# The first check is asked at once and again every FIRST_POLL_S until the value is there (given up
# after VERIFY_AFTER_S[0]); a value that is there must still be there SETTLE_S later (a page that
# resets its box does it on its next tick). Then once more 1.0 s after the write.
VERIFY_AFTER_S = (0.15, 1.0)
FIRST_POLL_S = 0.01
SETTLE_S = 0.04
FRONT_WAIT_S, FOCUS_WAIT_S = 0.2, 0.15      # longest wait for the browser to be in front / the box to take focus
FOCUS_SETTLE_S = 0.02
KEYS_ONLY_S = 120.0                         # how long a box that ignored SetValue is typed into directly
CT_EDIT = 50004
_ADDRESS_WORDS = ("address", "search", "url")       # the browser's own address bar

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None


@dataclass
class FillResult:
    outcome: str            # filled | failed | refused
    method: str = ""        # setvalue | keys | ""
    reason: str = ""
    # fill(..., late_check="background") only: the 1.0 s re-check, still running when "filled" is
    # returned. Resolves to "" (the value stayed) or the reason it did not.
    late: Optional[Future] = None


# ----------------------------------------------------------------------------- keyboard (SendInput)
ULONG_PTR = ctypes.c_size_t
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VK_CONTROL, VK_A, VK_DELETE = 0x11, 0x41, 0x2E


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def _send(*events) -> int:
    arr = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _vk(vk: int, up: bool = False) -> INPUT:
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


def _uni(code: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, flags, 0, 0)))


class Keyboard:
    """The window and key calls, behind one small object so tests can fake them."""

    def foreground(self) -> int:
        return int(user32.GetForegroundWindow() or 0)

    def bring_to_front(self, hwnd: int) -> None:
        user32.SetForegroundWindow(hwnd)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def later(self, seconds: float, fn: Callable[[], str]) -> Future:
        """fn() as a job on the OS Assist worker after `seconds`, without holding the worker
        meanwhile. A cancel before it starts drops it."""
        out: Future = Future()

        def _go() -> None:
            if not out.set_running_or_notify_cancel():
                return
            try:
                inner = worker.submit(fn)
            except Exception as e:
                out.set_exception(e)
                return

            def _done_inner(f) -> None:
                try:
                    out.set_result(f.result())
                except BaseException as e:
                    out.set_exception(e)
            inner.add_done_callback(_done_inner)

        t = threading.Timer(seconds, _go)
        t.daemon = True
        t.start()
        return out

    CHAR_GAP_S = 0.005          # between characters
    SLOW_GAP_S = 0.012          # the second try, when the page did not keep up with the first

    def type_text(self, text: str, hwnd: int, gap: Optional[float] = None) -> bool:
        """Ctrl+A, Delete, then each character as a Unicode key press. False if the window changed."""
        gap = self.CHAR_GAP_S if gap is None else gap
        _send(_vk(VK_CONTROL), _vk(VK_A), _vk(VK_A, True), _vk(VK_CONTROL, True))
        time.sleep(0.03)
        _send(_vk(VK_DELETE), _vk(VK_DELETE, True))
        for ch in text:
            if self.foreground() != hwnd:
                return False
            units = ch.encode("utf-16-le")
            for i in range(0, len(units), 2):
                code = int.from_bytes(units[i:i + 2], "little")
                _send(_uni(code), _uni(code, True))
            time.sleep(gap)
        return True


_keyboard = Keyboard()


def _kb(keyboard) -> Any:
    return keyboard or _keyboard


# ----------------------------------------------------------------------------- UIA helpers
def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _value_pattern(el):
    uia = worker.current_uia()
    p = el.GetCurrentPattern(uia.c.UIA_ValuePatternId)
    return p.QueryInterface(uia.c.IUIAutomationValuePattern) if p else None


def _focused():
    return _safe(lambda: worker.current_uia().uia.GetFocusedElement(), None)


def _same(a, b) -> bool:
    if a is None or b is None:
        return False
    return bool(_safe(lambda: worker.current_uia().uia.CompareElements(a, b), False))


def _check(el, length: int, text: Optional[str], is_password: bool) -> str:
    """'ok', 'ok-unreadable' (password box exposing no value, D2) or a short failure reason.
    A user box is compared with `text`; a password box only by its bullet count. A box UIA marks
    as a password box is judged as one whatever the caller said (rule 4)."""
    if _safe(lambda: el.CurrentIsPassword, False):
        is_password, text = True, None
    vp = _safe(lambda: _value_pattern(el), None)
    if vp is None:
        return "no ValuePattern"
    got = _safe(lambda: vp.CurrentValue, None)
    if got is None:
        return "ok-unreadable" if is_password else "value unreadable"
    if not is_password:
        return "ok" if got == text else f"mismatch ({len(got)} chars)"
    if got == "":
        return "empty"
    if len(got) == length and len(set(got)) == 1:
        return "ok"
    return f"masked, length {len(got)} vs {length}"


def _wait_until(kb, cond: Callable[[], bool], limit: float, step: float = FIRST_POLL_S) -> bool:
    """cond() now and every `step` until it holds; False when it still does not after `limit`."""
    waited = 0.0
    while True:
        if cond():
            return True
        if waited + 1e-9 >= limit:
            return False
        kb.sleep(step)
        waited += step


def _first_check(el, length: int, want: Optional[str], is_password: bool, kb):
    """(verdict, seconds waited). Back as soon as the value is there and stayed for SETTLE_S,
    instead of after a fixed wait; a value that never comes is given up after VERIFY_AFTER_S[0]."""
    limit = VERIFY_AFTER_S[0]
    waited = 0.0
    while True:
        out = _check(el, length, want, is_password)
        if out.startswith("ok"):
            kb.sleep(SETTLE_S)
            waited += SETTLE_S
            out = _check(el, length, want, is_password)
            if out.startswith("ok"):
                return out, waited
        if waited + 1e-9 >= limit:
            return f"{out} (at {limit}s)", waited
        kb.sleep(FIRST_POLL_S)
        waited += FIRST_POLL_S


def _verify(el, text: str, is_password: bool, kb, background: bool = False):
    """(verdict, late). Blocking: the first check and the ones 1.0 s after the write, late None.
    Background: the first check only; the later ones run on the worker afterwards (late: a Future
    of "" or the reason). A password is never kept for the late check, only its length."""
    rest = VERIFY_AFTER_S[1:]
    length, want = len(text), None if is_password else text
    out, waited = _first_check(el, length, want, is_password, kb)
    if not out.startswith("ok") or not rest:
        return out, None
    if not background:
        for wait in rest:
            kb.sleep(max(0.0, wait - waited))
            waited = max(waited, wait)
            out = _check(el, length, want, is_password)
            if not out.startswith("ok"):
                return f"{out} (at {wait}s)", None
        return out, None

    def _late_check() -> str:
        verdict = ""
        for wait in rest:
            got = _check(el, length, want, is_password)
            if not got.startswith("ok"):
                verdict = f"{got} (at {wait}s)"
                break
        if verdict:
            log.info("%s  late-failed  %s", "pass" if is_password else "user", verdict)
        return verdict

    return out, _schedule(kb, max(0.0, rest[0] - waited), _late_check)


def _schedule(kb, seconds: float, fn: Callable[[], str]) -> Future:
    """kb.later when the keyboard has it; a fake without it runs fn now (after kb.sleep)."""
    later = getattr(kb, "later", None)
    if later is not None:
        return later(seconds, fn)
    kb.sleep(seconds)
    fut: Future = Future()
    try:
        fut.set_result(fn())
    except BaseException as e:
        fut.set_exception(e)
    return fut


def _done(is_password: bool, res: FillResult) -> FillResult:
    log.info("%s  %s  %s", "pass" if is_password else "user",
             "typed" if res.outcome == "filled" and res.method == "keys" else res.outcome,
             res.reason or res.method)
    return res


def _ok(method: str, verdict: str) -> FillResult:
    return FillResult("filled", method, "value not exposed; trusted the write" if verdict == "ok-unreadable" else "")


def _refused(service) -> FillResult:
    return FillResult("refused", "", "address bar is no longer the service's login page")


def _type_and_verify(hwnd: int, el, text: str, is_password: bool, service, kb,
                     background: bool = False) -> FillResult:
    if not locate.host_matches_service(hwnd, service):
        return _refused(service)
    if not kb.type_text(text, hwnd):
        return FillResult("failed", "keys", "window changed while typing")
    verdict, late = _verify(el, text, is_password, kb, background)
    slow = getattr(kb, "SLOW_GAP_S", None)
    if not verdict.startswith("ok") and slow and _same(_focused(), el):
        # The page did not keep up with the fast keys: once more, slower. Only while the focus is
        # still on this box, and the host is checked again (typing is a write).
        if not locate.host_matches_service(hwnd, service):
            return _refused(service)
        if not kb.type_text(text, hwnd, slow):
            return FillResult("failed", "keys", "window changed while typing")
        verdict, late = _verify(el, text, is_password, kb, background)
    if verdict.startswith("ok"):
        res = _ok("keys", verdict)
        res.late = late
        return res
    return FillResult("failed", "keys", verdict)


# ----------------------------------------------------------------------------- boxes that ignore SetValue
# (hwnd, is password) -> (the box, when). Worker thread only. A box whose SetValue did not take but
# whose keys did is typed into directly next time, so a second press does not wait for a SetValue
# that is known to fail. Only that same box (CompareElements), and only for KEYS_ONLY_S.
_keys_only: dict = {}


def _is_keys_only(hwnd: int, element, is_password: bool) -> bool:
    key = (int(hwnd or 0), bool(is_password))
    hit = _keys_only.get(key)
    if hit is None:
        return False
    if time.monotonic() - hit[1] > KEYS_ONLY_S or not _same(hit[0], element):
        _keys_only.pop(key, None)
        return False
    return True


def _set_keys_only(hwnd: int, element, is_password: bool, on: bool) -> None:
    key = (int(hwnd or 0), bool(is_password))
    if not on:
        _keys_only.pop(key, None)
        return
    if len(_keys_only) >= 16:
        _keys_only.clear()
    _keys_only[key] = (element, time.monotonic())


# ----------------------------------------------------------------------------- fill
def fill(hwnd: int, element, text: str, is_password: bool, service, method: str = "auto",
         keyboard=None, late_check: str = "wait") -> FillResult:
    """_fill, timed: one more log line with how long it took (never a value)."""
    t0 = time.perf_counter()
    res = _fill(hwnd, element, text, is_password, service, method, keyboard, late_check)
    log.info("%s  took  %d ms (%s)", "pass" if is_password else "user",
             round((time.perf_counter() - t0) * 1000), res.method or res.outcome)
    return res


def _fill(hwnd: int, element, text: str, is_password: bool, service, method: str = "auto",
          keyboard=None, late_check: str = "wait") -> FillResult:
    """Put `text` in `element`: SetValue first, SendInput keys if it did not take.
    method: auto | setvalue (never types) | keys.
    late_check: "wait" = the 1.0 s re-check too before returning (about 1 s);
    "background" = return after the first check (as soon as the value is there and stayed for
    SETTLE_S) with the re-check in result.late, so a card can show the result at once."""
    kb = _kb(keyboard)
    background = late_check == "background"
    notes: List[str] = []
    if method == "auto" and _is_keys_only(hwnd, element, is_password):
        notes.append("SetValue skipped (did not take last time)")
    elif method in ("auto", "setvalue"):
        if not locate.host_matches_service(hwnd, service):
            return _done(is_password, _refused(service))
        try:
            vp = _value_pattern(element)
            if vp is None:
                notes.append("no ValuePattern")
            elif vp.CurrentIsReadOnly:
                notes.append("read-only")
            else:
                vp.SetValue(text)
                verdict, late = _verify(element, text, is_password, kb, background)
                if verdict.startswith("ok"):
                    res = _ok("setvalue", verdict)
                    res.late = late
                    return _done(is_password, res)
                notes.append(verdict)
        except Exception as e:
            notes.append(f"SetValue error {type(e).__name__}")
        if method == "setvalue":
            return _done(is_password, FillResult("failed", "setvalue", "; ".join(notes)))

    if kb.foreground() != hwnd:
        kb.bring_to_front(hwnd)
        if not _wait_until(kb, lambda: kb.foreground() == hwnd, FRONT_WAIT_S):
            return _done(is_password, FillResult("failed", "keys", "browser not in front"))
    if not locate.host_matches_service(hwnd, service):          # SetFocus is a write too
        return _done(is_password, _refused(service))
    _safe(lambda: element.SetFocus(), None)
    if not _wait_until(kb, lambda: _same(_focused(), element), FOCUS_WAIT_S):
        return _done(is_password, FillResult("failed", "keys", "box did not take focus"))
    kb.sleep(FOCUS_SETTLE_S)
    res = _type_and_verify(hwnd, element, text, is_password, service, kb, background)
    if method == "auto":
        _set_keys_only(hwnd, element, is_password, res.outcome == "filled" and bool(notes))
    if res.outcome == "failed" and notes:
        res.reason = f"{'; '.join(notes)}; keys: {res.reason}"
    elif notes:                                     # typed: say why SetValue was not enough
        log.info("%s  setvalue-not-used  %s", "pass" if is_password else "user", "; ".join(notes))
    return _done(is_password, res)


def type_into_focused(hwnd: int, text: str, is_password: bool, service, keyboard=None) -> FillResult:
    """Flutter step mode: the box already has focus (staff clicked it); type into it directly."""
    kb = _kb(keyboard)
    if not locate.host_matches_service(hwnd, service):
        return _done(is_password, _refused(service))
    if kb.foreground() != hwnd:
        return _done(is_password, FillResult("failed", "keys", "browser not in front"))
    el = _focused()
    if el is None or _safe(lambda: el.CurrentControlType, 0) != CT_EDIT:
        return _done(is_password, FillResult("failed", "keys", "focus is not on a text box"))
    return _done(is_password, _type_and_verify(hwnd, el, text, is_password, service, kb))


class FocusWatcher:
    """Flutter step mode (4.3): poll the focused element (call poll() every ~200 ms). Returns each
    new Edit of the browser window once; the address bar and other windows are ignored."""

    POLL_S = 0.2

    def __init__(self, hwnd: int, keyboard=None) -> None:
        self.hwnd = hwnd
        self._kb = _kb(keyboard)
        self._reported: list = []

    def poll(self):
        if self._kb.foreground() != self.hwnd:
            return None
        el = _focused()
        if el is None or _safe(lambda: el.CurrentControlType, 0) != CT_EDIT:
            return None
        name_id = ((_safe(lambda: el.CurrentName, "") or "") + " "
                   + (_safe(lambda: el.CurrentAutomationId, "") or "")).lower()
        if any(w in name_id for w in _ADDRESS_WORDS):
            return None
        if any(_same(el, seen) for seen in self._reported):
            return None
        self._reported.append(el)
        return el
