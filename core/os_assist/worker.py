"""
OS Assist UIA worker: one daemon thread that owns its own UI Automation object. Every UIA call of
OS Assist runs on it (submit a function, get a Future), so a slow browser tree can never stall the
Qt thread or SGT's shared reader (core/vsdc/vsdc_uia_text).
"""

import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable, Dict, Optional

CT_EDIT, CT_DOCUMENT = 50004, 50030

_local = threading.local()


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


class Uia:
    """The worker thread's UIA object. Read-only helpers; the one writer is core/os_assist/fill.py."""

    def __init__(self) -> None:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        self.c = comtypes.client.GetModule("UIAutomationCore.dll")
        try:
            self.uia = comtypes.client.CreateObject(self.c.CUIAutomation8, interface=self.c.IUIAutomation)
        except Exception:
            self.uia = comtypes.client.CreateObject(self.c.CUIAutomation, interface=self.c.IUIAutomation)
        self._bar_cache: Dict[int, Any] = {}

    def url(self, hwnd: int) -> str:
        from core.vsdc.vsdc_uia_text import address_bar_here
        return address_bar_here(self.uia, self.c, hwnd, self._bar_cache) or ""

    def documents(self, hwnd: int) -> list:
        root = self.uia.ElementFromHandle(hwnd)
        cond = self.uia.CreatePropertyCondition(self.c.UIA_ControlTypePropertyId, CT_DOCUMENT)
        found = root.FindAll(self.c.TreeScope_Descendants, cond)
        return [found.GetElement(i) for i in range(found.Length)] if found else []

    def edits(self, doc) -> list:
        cond = self.uia.CreatePropertyCondition(self.c.UIA_ControlTypePropertyId, CT_EDIT)
        found = doc.FindAll(self.c.TreeScope_Descendants, cond)
        return [found.GetElement(i) for i in range(found.Length)] if found else []


class Worker:
    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def submit(self, fn: Callable, *args) -> Future:
        fut: Future = Future()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="os-assist-uia", daemon=True)
                self._thread.start()
        self._q.put((fut, fn, args))
        return fut

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            self._q.put(None)
            thread.join(timeout=2.0)

    def _run(self) -> None:
        _local.uia = None
        while True:
            job = self._q.get()
            if job is None:
                return
            fut, fn, args = job
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:
                fut.set_exception(e)


def current_uia():
    """The Uia of the calling thread, created on first use. Only meaningful on the worker."""
    uia = getattr(_local, "uia", None)
    if uia is None:
        uia = _local.uia = Uia()
    return uia


def bind(uia) -> None:
    """Install a UIA object for the calling thread (tests use a fake)."""
    _local.uia = uia


_default = Worker()


def submit(fn: Callable, *args) -> Future:
    return _default.submit(fn, *args)


def stop() -> None:
    _default.stop()
