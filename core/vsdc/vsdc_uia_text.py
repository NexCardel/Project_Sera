"""
core/vsdc/vsdc_uia_text.py — VSDC-X: UI Automation Exact-Text Capture
========================================================================
Reads the visible text of a portal page via its Windows UI Automation
accessibility tree (the same read-only OS channel screen readers use) instead
of OCR-ing screen pixels. Never touches the browser process, the page's DOM,
or the network — same passive, outside-the-browser-process posture as the
rest of VSDC.

Produces the same {"text": str, "lines": list[str]} shape
VSDCOcrEngine.scan_image() returns, so callers can feed it straight into the
existing vsdc_regex.py extractors. Every entry point degrades gracefully to
an empty result on any failure (never raises) so callers can treat "UIA
didn't get anything this tick" as a normal condition and fall back to OCR.

Validated via tools/vsdc_uia_probe.py against live GST and ITR portal pages
(2026-09-17): exact name/status/GSTIN reads at ~0.1s per page, versus OCR's
much heavier capture + BMP encode + WinRT async decode + recognize round trip.
"""

import ctypes
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional

import comtypes.client

user32 = ctypes.windll.user32

# UIA_ControlTypeIds.Document — see Windows SDK UIAutomationClient.h. Referenced
# as a literal (matching the existing convention in vsdc_router.py) since not
# every comtypes-generated wrapper exposes every named constant.
UIA_DOCUMENT_CONTROL_TYPE_ID = 50030

# Control types whose displayed text lives in ValuePattern rather than in the
# element's accessible Name. For a form field, Name is the *label* ("First
# Name") and the actual content ("WASIL") is only reachable through
# ValuePattern — so reading Name alone makes every field value invisible.
# The ITR Personal Info / Profile pages render their prefilled name and date
# of birth as readonly <input>s, which is exactly this case.
UIA_COMBOBOX_CONTROL_TYPE_ID = 50003
UIA_EDIT_CONTROL_TYPE_ID = 50004
UIA_SPINNER_CONTROL_TYPE_ID = 50016
_VALUE_BEARING_CONTROL_TYPES = frozenset({
    UIA_COMBOBOX_CONTROL_TYPE_ID,
    UIA_EDIT_CONTROL_TYPE_ID,
    UIA_SPINNER_CONTROL_TYPE_ID,
})

# Choice controls: UIA gives their label as Name ("Original", "Revised") but NOT which one is
# ticked - that is a separate pattern query. Measured in Edge: a radio group reads as both
# labels, indistinguishable, unless SelectionItem / Toggle is asked. Only done when a caller
# opts in (include_selection), so VSDC-X's lines are unchanged.
UIA_RADIOBUTTON_CONTROL_TYPE_ID = 50013
UIA_CHECKBOX_CONTROL_TYPE_ID = 50002
UIA_IS_OFFSCREEN_PROPERTY_ID = 30022
UIA_FRAMEWORK_ID_PROPERTY_ID = 30024

# Gecko (Firefox) builds a different tree for the same HTML: a closed <select> keeps its options as
# ListItems under the ComboBox, and a <label> wrapping a radio / checkbox is a named container
# right before the control. Chromium folds both, so these two rules apply to Gecko elements only.
FRAMEWORK_GECKO = "Gecko"
_LABEL_CONTAINER_TYPES = frozenset({50025, 50026, 50033})      # Custom, Group, Pane
SELECTED_PREFIX = "Selected: "

# Generous versus the ~0.1s observed in practice, while still bounded: a raw
# UIA call from a thread with no message loop can genuinely hang/deadlock on
# a complex page (observed while probing), so every call below is guarded.
DEFAULT_TIMEOUT_SEC = 3.0


def _init_com_on_worker_thread():
    """
    Every task submitted to _POOL runs on the same persistent background
    thread (max_workers=1). A plain Python thread has no COM apartment set up
    the way the main thread does — comtypes calls fail with "CoInitialize has
    not been called" unless this runs first on that thread.
    """
    import comtypes
    comtypes.CoInitialize()


_POOL = ThreadPoolExecutor(max_workers=1, initializer=_init_com_on_worker_thread)

# UIA COM objects are apartment-bound: they must be both created AND used from
# the same thread - so they are kept per thread. A worker that hangs is replaced
# (see _run_with_timeout) and the new one creates its own.
_tls = threading.local()

# A read that times out may still be running - wedged inside a UIA call that never returns, or
# just slow (a heavy page on a slow PC). The pool has ONE worker, so a wedged read would make
# every later read queue behind it and fail after the full timeout, forever.
#
# Before 2026-10-13 any read still running when the next one was asked for counted as wedged,
# and after MAX_ABANDONED_WORKERS of those in one run UIA was switched off until the app
# restarted. A merely slow read counted too, the count never went down, and the only notice was
# a print() the installed app has no console for: an office PC went blind on the GST portal
# (no OCR there) 3-3.5 hours into two evenings in a row. Now:
#   - a read still running for less than SLOW_GRACE_SEC is SLOW: this read is skipped (the
#     caller gets "busy" and asks again next tick), nothing is abandoned;
#   - past that it is WEDGED: its worker is abandoned and a fresh one takes over;
#   - only workers still stuck count; once one comes back its thread ends and leaves the count;
#   - MAX_WEDGED_WORKERS stuck at once pauses UIA for PAUSE_SEC, then it is tried again -
#     never switched off for the rest of the run.
# Every one of these goes to logs/vsdc.log (core/vsdc/vsdc_diag).
SLOW_GRACE_SEC = 20.0
MAX_WEDGED_WORKERS = 5
PAUSE_SEC = 300.0
_pool_lock = threading.Lock()
_inflight = None
_inflight_started = 0.0
_abandoned = 0                  # workers abandoned this run (all time)
_wedged: List[Any] = []         # futures of abandoned workers - pruned as they finish
_paused_until = 0.0             # time.monotonic(); 0 = not paused
_disabled_reason: Optional[str] = None      # set only while paused
_uia_init_failed = False
_caller = threading.local()     # .busy: the calling thread's last read was skipped
_stats_lock = threading.Lock()
_stats: Dict[str, Any] = {"reads": 0, "late": 0, "busy": 0, "max_ms": 0.0, "last_ms": 0.0}


def _diag(text: str, key: Optional[str] = None) -> None:
    try:
        from core.vsdc import vsdc_diag
        if key:
            vsdc_diag.note_limited(key, text)
        else:
            vsdc_diag.note(text)
    except Exception:
        pass


def _timed(fn, timeout_sec: float):
    """fn, timing itself on the worker - a read that comes back after its caller gave up is the
    evidence of a slow page, so it is logged with how long it took."""
    def run():
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            ms = (time.perf_counter() - t0) * 1000
            late = ms > timeout_sec * 1000
            with _stats_lock:
                _stats["reads"] += 1
                _stats["last_ms"] = ms
                if ms > _stats["max_ms"]:
                    _stats["max_ms"] = ms
                if late:
                    _stats["late"] += 1
            if late:
                _diag(f"UIA read came back after {ms / 1000:.1f} s (caller gave up at {timeout_sec:g} s)",
                      key="uia-late")
    return run


def _get_uia_on_worker():
    """Must only be called from within the _POOL worker thread. Lazily creates
    and caches the UIA COM objects there (expensive to recreate per call)."""
    global _uia_init_failed
    uia = getattr(_tls, "uia", None)
    if uia is not None:
        return uia, _tls.uia_client
    if _uia_init_failed:
        return None, None
    try:
        uia_client = comtypes.client.GetModule("UIAutomationCore.dll")
        uia = comtypes.client.CreateObject(uia_client.CUIAutomation, interface=uia_client.IUIAutomation)
        _tls.uia, _tls.uia_client = uia, uia_client
        return uia, uia_client
    except Exception as e:
        print(f"[VSDC-X] UI Automation init failed: {e}")
        _uia_init_failed = True
        return None, None


def _run_with_timeout(fn, timeout_sec: float):
    """
    Runs fn() on the persistent COM-initialized worker thread with a hard
    timeout. A stuck call times out instead of freezing the caller — this
    matters a lot here since callers run on VSDCWorker's single background
    QThread, and OCR capture shares that same per-tick loop: an unguarded
    hang would silently kill the entire VSDC pipeline, not just UIA reads.

    None on a timeout, an error, a pause, or when the previous read is still running (slow, not
    yet wedged) - was_busy() tells that last case apart, so a caller can ask again next tick.
    """
    global _POOL, _inflight, _inflight_started, _abandoned, _paused_until, _disabled_reason
    _caller.busy = False
    with _pool_lock:
        now = time.monotonic()
        if _paused_until:
            if now < _paused_until:
                return None
            _wedged[:] = [f for f in _wedged if not f.done()]
            _paused_until, _disabled_reason = 0.0, None
            _diag(f"UIA pause over - reading again ({len(_wedged)} worker(s) still stuck)")
        if _inflight is not None and not _inflight.done():
            running = now - _inflight_started
            if running < SLOW_GRACE_SEC:
                # Slow, not wedged: let it finish rather than abandon it.
                _caller.busy = True
                with _stats_lock:
                    _stats["busy"] += 1
                return None
            # Wedged: leave it, start afresh.
            _abandoned += 1
            _wedged.append(_inflight)
            _wedged[:] = [f for f in _wedged if not f.done()]
            old = _POOL
            _POOL = ThreadPoolExecutor(max_workers=1, initializer=_init_com_on_worker_thread)
            _inflight = None
            try:
                old.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            _diag(f"UIA read stuck {running:.0f} s - replaced its worker "
                  f"({_abandoned} this run, {len(_wedged)} still stuck)")
            if len(_wedged) >= MAX_WEDGED_WORKERS:
                _paused_until = now + PAUSE_SEC
                _disabled_reason = (f"{len(_wedged)} UI Automation workers stuck - UIA paused for "
                                    f"{PAUSE_SEC / 60:.0f} min (OCR still works)")
                _diag(_disabled_reason)
                return None
        future = _POOL.submit(_timed(fn, timeout_sec))
        _inflight = future
        _inflight_started = now
    try:
        return future.result(timeout=timeout_sec)
    except FutureTimeoutError:
        return None
    except Exception:
        return None


def was_busy() -> bool:
    """Whether this thread's last _run_with_timeout was skipped because the previous read was
    still running (slow, not wedged)."""
    return bool(getattr(_caller, "busy", False))


def worker_health() -> Dict[str, Any]:
    """Workers abandoned this run, how many are still stuck, and why UIA is paused (None if not)."""
    with _pool_lock:
        _wedged[:] = [f for f in _wedged if not f.done()]
        return {"abandoned_workers": _abandoned, "stuck_workers": len(_wedged),
                "disabled": _disabled_reason}


def take_read_stats() -> Dict[str, Any]:
    """Reads since the last call (the heartbeat's line): count, slowest / last ms, reads that
    came back after their caller gave up, reads skipped while one was still running, plus the
    worker health. Resets the per-interval counts."""
    with _stats_lock:
        out = {"reads": _stats["reads"], "max_ms": round(_stats["max_ms"]), "last_ms": round(_stats["last_ms"]),
               "late": _stats["late"], "busy": _stats["busy"]}
        _stats.update(reads=0, late=0, busy=0, max_ms=0.0)
    h = worker_health()
    out.update(abandoned=h["abandoned_workers"], stuck=h["stuck_workers"],
               paused="yes" if h["disabled"] else None)
    return out


UIA_TABITEM_CONTROL_TYPE_ID = 50019
NO_TABS_RECHECK_SEC = 10.0      # a window with no tab strip is searched again only this often (~40 ms a search)


def _selected_among(uia_client, items: List[Any]) -> Optional[str]:
    for el in items:
        try:
            p = el.GetCurrentPattern(uia_client.UIA_SelectionItemPatternId)
            if p and p.QueryInterface(uia_client.IUIAutomationSelectionItemPattern).CurrentIsSelected:
                return ".".join(str(x) for x in el.GetRuntimeId())
        except Exception:
            continue                # a closed tab's element: skip it
    return None


def selected_tab_id(hwnd: int, timeout_sec: float = 1.0) -> Optional[str]:
    """
    An id for the browser tab in front in this window: the UIA RuntimeId of its selected TabItem
    in the tab strip (measured 2026-10-13 in Edge/Chrome: the same id every read and across a
    navigation inside the tab, a different one per tab). None when it cannot be told (no tab
    strip, a browser that exposes none, a failed or skipped read - see was_busy()).

    The window's tab elements are kept per worker thread: a normal tick only asks each of them
    "are you selected?" (1-3 ms in all, measured); the tab strip is searched again (30-120 ms, once
    545 ms on a big window) only when none of them is - a new tab, a closed one - and a window with
    no tab strip at most every NO_TABS_RECHECK_SEC. A property read only: no tab is ever clicked.
    """
    _caller.busy = False        # an early return below must not leave an older read's "busy" behind
    if not hwnd or not user32.IsWindow(hwnd):
        return None

    def _do():
        uia, uia_client = _get_uia_on_worker()
        if not uia:
            return None
        cache = getattr(_tls, "tabs", None)
        if cache is None:
            cache = _tls.tabs = {}
        items, searched = cache.get(hwnd) or ([], 0.0)
        if items:
            found = _selected_among(uia_client, items)
            if found:
                return found
        elif searched and time.monotonic() - searched < NO_TABS_RECHECK_SEC:
            return None             # no tab strip here (an app window, full screen): not every tick
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return None
        cond = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, UIA_TABITEM_CONTROL_TYPE_ID)
        coll = root.FindAll(uia_client.TreeScope_Descendants, cond)
        items = [coll.GetElement(i) for i in range(coll.Length)] if coll else []
        if len(cache) > 20:
            cache.clear()
        cache[hwnd] = (items, time.monotonic())
        return _selected_among(uia_client, items)

    return _run_with_timeout(_do, timeout_sec)


def is_available(timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> bool:
    """Cheap availability check without doing a real page read."""
    result = _run_with_timeout(lambda: _get_uia_on_worker()[0] is not None, timeout_sec)
    return bool(result)


def _find_document_elements(uia, uia_client, root_element) -> List[Any]:
    condition = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, UIA_DOCUMENT_CONTROL_TYPE_ID)
    found = root_element.FindAll(uia_client.TreeScope_Descendants, condition)
    return onscreen_only([found.GetElement(i) for i in range(found.Length)] if found else [])


def onscreen_only(documents: List[Any]) -> List[Any]:
    """Only the Documents someone can see. Firefox exposes every tab's page (background tabs too)
    as a Document; a page nobody can see is never read, in any browser. An element whose
    IsOffscreen cannot be read is kept, as before."""
    kept = []
    for doc in documents:
        if _is_offscreen(doc):
            continue
        kept.append(doc)
    return kept


def _is_offscreen(element) -> bool:
    """The live IsOffscreen, else the cached one (a cache-only element, as read_page_nodes gets,
    raises on the live property). False when neither can be read."""
    try:
        return bool(element.CurrentIsOffscreen)
    except Exception:
        pass
    try:
        return bool(element.GetCachedPropertyValue(UIA_IS_OFFSCREEN_PROPERTY_ID))
    except Exception:
        return False


def _is_password(uia_client, element) -> bool:
    """True when UIA marks this element a password field (UIA_IsPasswordPropertyId). Checked
    before any ValuePattern read - a password's content must never reach a line, the corpus, or
    a log, even masked."""
    try:
        return bool(element.GetCurrentPropertyValue(uia_client.UIA_IsPasswordPropertyId))
    except Exception:
        return False


def _read_value_pattern(uia_client, element) -> str:
    """Reads an element's ValuePattern text, or "" if it exposes none. Strictly
    a property read — never SetValue — so this stays as passive as the rest of
    VSDC-X (see the module docstring)."""
    try:
        pattern = element.GetCurrentPattern(uia_client.UIA_ValuePatternId)
        if not pattern:
            return ""
        value_obj = pattern.QueryInterface(uia_client.IUIAutomationValuePattern)
        return (value_obj.CurrentValue or "").strip()
    except Exception:
        return ""


def _is_chosen(uia_client, element, control_type: int) -> bool:
    """Whether a radio button is selected / a checkbox is ticked. A property read only."""
    try:
        if control_type == UIA_RADIOBUTTON_CONTROL_TYPE_ID:
            p = element.GetCurrentPattern(uia_client.UIA_SelectionItemPatternId)
            return bool(p and p.QueryInterface(uia_client.IUIAutomationSelectionItemPattern).CurrentIsSelected)
        if control_type == UIA_CHECKBOX_CONTROL_TYPE_ID:
            p = element.GetCurrentPattern(uia_client.UIA_TogglePatternId)
            return bool(p and p.QueryInterface(uia_client.IUIAutomationTogglePattern).CurrentToggleState == 1)
    except Exception:
        pass
    return False


def _framework_is_gecko(element) -> bool:
    try:
        return element.CurrentFrameworkId == FRAMEWORK_GECKO
    except Exception:
        pass
    try:
        return element.GetCachedPropertyValue(UIA_FRAMEWORK_ID_PROPERTY_ID) == FRAMEWORK_GECKO
    except Exception:
        return False


def _collect_descendant_lines(uia, uia_client, root_element, max_lines: Optional[int] = None,
                              include_selection: bool = False) -> List[str]:
    """Returns the visible text of every descendant, in document order — label
    and value elements arrive as adjacent entries, which is exactly the 'label
    on line N, value on line N+1' shape vsdc_regex.py's extractors already
    handle for OCR line-wrapping.

    For form fields, the element's accessible Name is only the label; its
    displayed content comes from ValuePattern and is emitted as its own line
    directly after that label, preserving the same adjacency contract.
    """
    lines: List[str] = []
    true_cond = uia.CreateTrueCondition()
    elements = root_element.FindAll(uia_client.TreeScope_Descendants, true_cond)
    count = elements.Length if elements else 0
    gecko = _framework_is_gecko(root_element)
    skip = 0                    # Gecko: the option elements of the dropdown just read
    container_name = ""         # Gecko: the name of the container element read just before
    for i in range(count):
        if max_lines is not None and len(lines) >= max_lines:
            break
        if skip:
            skip -= 1
            continue
        try:
            el = elements.GetElement(i)
            name = (el.CurrentName or "").strip()
            ctype = None
            if gecko:
                try:
                    ctype = el.CurrentControlType
                except Exception:
                    pass
                echoed, container_name = container_name, ""
                if ctype in _LABEL_CONTAINER_TYPES:
                    container_name = name
                elif name and name == echoed and ctype in (UIA_RADIOBUTTON_CONTROL_TYPE_ID, UIA_CHECKBOX_CONTROL_TYPE_ID) \
                        and lines and lines[-1] == name:
                    lines.pop()         # the <label> around this control already said its name
            if name:
                lines.append(name)

            # Only control types that can actually carry one — querying a
            # pattern on every descendant of a large page would cost a
            # cross-process call per element for nothing.
            value = ""
            try:
                ctype = el.CurrentControlType
                if ctype in _VALUE_BEARING_CONTROL_TYPES and not _is_password(uia_client, el):
                    value = _read_value_pattern(uia_client, el)
            except Exception:
                value = ""
            if gecko and ctype == UIA_COMBOBOX_CONTROL_TYPE_ID:
                try:
                    skip = el.FindAll(uia_client.TreeScope_Descendants, true_cond).Length
                except Exception:
                    skip = 0
            if value and value != name:
                lines.append(value)
                # A dropdown's value IS the user's choice; mark it as one, so a rule can tell
                # it apart from the option labels a page lists under the same heading.
                if include_selection and ctype == UIA_COMBOBOX_CONTROL_TYPE_ID:
                    lines.append(SELECTED_PREFIX + (f"{name} = {value}" if name else value))
            if include_selection and name and ctype in (UIA_RADIOBUTTON_CONTROL_TYPE_ID, UIA_CHECKBOX_CONTROL_TYPE_ID) \
                    and _is_chosen(uia_client, el, ctype):
                lines.append(SELECTED_PREFIX + name)
        except Exception:
            continue
    return lines


def read_page_text(hwnd: int, timeout_sec: float = DEFAULT_TIMEOUT_SEC, max_lines: Optional[int] = None,
                   include_selection: bool = False) -> Dict[str, Any]:
    """
    Reads the visible text of the web page content (not browser chrome —
    tabs, toolbar, address bar) hosted in the given window handle, via its
    UI Automation Document element(s).

    Returns {"text": str, "lines": list[str]}, empty on any failure —
    callers should treat an empty result as "UIA didn't get anything this
    tick" and fall back to / merge with OCR as usual, not as an error.

    max_lines stops the walk once that many lines are collected - used by the portal-logo
    tripwire, which only needs the head of a page it is not cleared to read.

    include_selection adds a "Selected: <label>" line after each ticked radio button or
    checkbox, and "Selected: <label> = <value>" after each dropdown's value (SGT uses it;
    VSDC-X does not, so its lines are exactly as before).
    """
    empty: Dict[str, Any] = {"text": "", "lines": []}
    _caller.busy = False        # an early return below must not leave an older read's "busy" behind
    if not hwnd or not user32.IsWindow(hwnd):
        return empty

    def _do_read():
        uia, uia_client = _get_uia_on_worker()
        if not uia:
            return empty
        root_element = uia.ElementFromHandle(hwnd)
        if not root_element:
            return empty
        doc_elements = _find_document_elements(uia, uia_client, root_element)
        if not doc_elements:
            return empty
        all_lines: List[str] = []
        for doc_el in doc_elements:
            all_lines.extend(_collect_descendant_lines(uia, uia_client, doc_el, max_lines, include_selection))
            if max_lines is not None and len(all_lines) >= max_lines:
                all_lines = all_lines[:max_lines]
                break
        return {"text": "\n".join(all_lines), "lines": all_lines}

    result = _run_with_timeout(_do_read, timeout_sec)
    if result is None and was_busy():
        return {"text": "", "lines": [], "busy": True}
    return result if result is not None else empty
