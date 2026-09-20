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
# the same thread. These are only ever touched from inside functions dispatched
# through _POOL below — never read or written directly from a caller's thread.
_uia = None
_uia_client = None
_uia_init_failed = False


def _get_uia_on_worker():
    """Must only be called from within the _POOL worker thread. Lazily creates
    and caches the UIA COM objects there (expensive to recreate per call)."""
    global _uia, _uia_client, _uia_init_failed
    if _uia is not None:
        return _uia, _uia_client
    if _uia_init_failed:
        return None, None
    try:
        uia_client = comtypes.client.GetModule("UIAutomationCore.dll")
        uia = comtypes.client.CreateObject(uia_client.CUIAutomation, interface=uia_client.IUIAutomation)
        _uia, _uia_client = uia, uia_client
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
    """
    future = _POOL.submit(fn)
    try:
        return future.result(timeout=timeout_sec)
    except FutureTimeoutError:
        return None
    except Exception:
        return None


def is_available(timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> bool:
    """Cheap availability check without doing a real page read."""
    result = _run_with_timeout(lambda: _get_uia_on_worker()[0] is not None, timeout_sec)
    return bool(result)


def _find_document_elements(uia, uia_client, root_element) -> List[Any]:
    condition = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, UIA_DOCUMENT_CONTROL_TYPE_ID)
    found = root_element.FindAll(uia_client.TreeScope_Descendants, condition)
    return [found.GetElement(i) for i in range(found.Length)] if found else []


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


def _collect_descendant_lines(uia, uia_client, root_element, max_lines: Optional[int] = None) -> List[str]:
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
    for i in range(count):
        if max_lines is not None and len(lines) >= max_lines:
            break
        try:
            el = elements.GetElement(i)
            name = (el.CurrentName or "").strip()
            if name:
                lines.append(name)

            # Only control types that can actually carry one — querying a
            # pattern on every descendant of a large page would cost a
            # cross-process call per element for nothing.
            value = ""
            try:
                if el.CurrentControlType in _VALUE_BEARING_CONTROL_TYPES:
                    value = _read_value_pattern(uia_client, el)
            except Exception:
                value = ""
            if value and value != name:
                lines.append(value)
        except Exception:
            continue
    return lines


def read_page_text(hwnd: int, timeout_sec: float = DEFAULT_TIMEOUT_SEC, max_lines: Optional[int] = None) -> Dict[str, Any]:
    """
    Reads the visible text of the web page content (not browser chrome —
    tabs, toolbar, address bar) hosted in the given window handle, via its
    UI Automation Document element(s).

    Returns {"text": str, "lines": list[str]}, empty on any failure —
    callers should treat an empty result as "UIA didn't get anything this
    tick" and fall back to / merge with OCR as usual, not as an error.

    max_lines stops the walk once that many lines are collected - used by the portal-logo
    tripwire, which only needs the head of a page it is not cleared to read.
    """
    empty: Dict[str, Any] = {"text": "", "lines": []}
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
            all_lines.extend(_collect_descendant_lines(uia, uia_client, doc_el, max_lines))
            if max_lines is not None and len(all_lines) >= max_lines:
                all_lines = all_lines[:max_lines]
                break
        return {"text": "\n".join(all_lines), "lines": all_lines}

    result = _run_with_timeout(_do_read, timeout_sec)
    return result if result is not None else empty
