"""
core/rotation/injector.py — Sera Credential Keeper (SCK) Win32 keystroke engine.

Sends a password string to a target browser window using the Win32
``SendInput`` API with ``KEYEVENTF_UNICODE`` (0x0004).  No third-party
packages are required beyond the Python standard library and ``ctypes``.

Injection sequence
------------------
1. ``IsWindow`` guard — raises ``RuntimeError`` on an invalid HWND.
2. ``SetForegroundWindow`` to bring the browser to the front, then a
   300 ms settle delay.
3. ``Ctrl+A`` followed by ``Delete`` to clear any pre-existing content in
   the focused field.
4. For each character in the password, send a *key-down* + *key-up* pair
   via ``SendInput`` with ``KEYEVENTF_UNICODE``, separated by a random
   natural typing delay of 12–20 ms.
5. The local password variable is overwritten with ``""`` immediately
   after the last keystroke, so it lives in process memory for the
   minimum possible duration.

Security note
-------------
The password string is accepted as a plain Python ``str``.  The caller
is responsible for obtaining it only from
``RotationSession.commit_candidate()`` and discarding their own
reference promptly.  This module never logs, prints, or stores the
value.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import random
import time
from typing import Optional

# ── Win32 constants ───────────────────────────────────────────────────────────

INPUT_KEYBOARD: int = 1
KEYEVENTF_UNICODE: int = 0x0004
KEYEVENTF_KEYUP: int = 0x0002

VK_CONTROL: int = 0x11
VK_A: int = 0x41
VK_DELETE: int = 0x2E


# ── ctypes structures ─────────────────────────────────────────────────────────

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.wintypes.WORD),
        ("wScan",       ctypes.wintypes.WORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type",  ctypes.wintypes.DWORD),
        ("_data", _INPUT_UNION),
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

_user32 = ctypes.windll.user32


def _send_inputs(*inputs: _INPUT) -> None:
    """Forward a sequence of _INPUT structs to SendInput."""
    count = len(inputs)
    arr = (_INPUT * count)(*inputs)
    _user32.SendInput(count, arr, ctypes.sizeof(_INPUT))


def _vk_input(vk: int, flags: int = 0) -> _INPUT:
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp._data.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0,
                                dwExtraInfo=None)
    return inp


def _unicode_input(char: str, keyup: bool = False) -> _INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp._data.ki = _KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0,
                                dwExtraInfo=None)
    return inp


# ── Public class ─────────────────────────────────────────────────────────────

class RotationInjector:
    """Win32 ``SendInput`` unicode keystroke engine for SCK rotation.

    Instantiate once and reuse; the object holds no state between calls.

    Example::

        injector = RotationInjector()
        try:
            injector.inject(hwnd, candidate_password)
        finally:
            candidate_password = ""
    """

    # Natural typing cadence: random pause between each character (seconds).
    _MIN_DELAY: float = 0.012    # 12 ms
    _MAX_DELAY: float = 0.020    # 20 ms
    _SETTLE_MS: float = 0.300    # 300 ms after SetForegroundWindow

    def inject(self, hwnd: int, password: str) -> None:  # noqa: C901
        """Type ``password`` into the foreground field of ``hwnd``.

        The password variable is zeroed from the local scope immediately
        after the last keystroke.

        Args:
            hwnd:      Win32 HWND of the target browser window.
            password:  The credential string to inject (plaintext, volatile).

        Raises:
            RuntimeError: If ``hwnd`` is not a valid window handle.
        """
        if not _user32.IsWindow(int(hwnd)):
            raise RuntimeError(f"Invalid HWND: {hwnd!r} — window does not exist")

        # 1. Bring the browser to the front and let it settle.
        _user32.SetForegroundWindow(int(hwnd))
        time.sleep(self._SETTLE_MS)

        # 2. Ctrl+A to select all, then Delete to clear existing content.
        _send_inputs(
            _vk_input(VK_CONTROL),
            _vk_input(VK_A),
            _vk_input(VK_A, KEYEVENTF_KEYUP),
            _vk_input(VK_CONTROL, KEYEVENTF_KEYUP),
        )
        time.sleep(0.050)
        _send_inputs(
            _vk_input(VK_DELETE),
            _vk_input(VK_DELETE, KEYEVENTF_KEYUP),
        )
        time.sleep(0.050)

        # 3. Type each character with a natural random inter-keystroke delay.
        #    We iterate over the string directly; the loop body is fast.
        try:
            for ch in password:
                _send_inputs(_unicode_input(ch), _unicode_input(ch, keyup=True))
                time.sleep(random.uniform(self._MIN_DELAY, self._MAX_DELAY))
        finally:
            # 4. Zero the local reference regardless of any mid-loop exception.
            password = ""  # noqa: F841 — intentional zeroing of the local
