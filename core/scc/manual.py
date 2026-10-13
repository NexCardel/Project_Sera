"""
core/scc/manual.py - Client Detail's way into the SCC card (autofill-tweaks blueprint Part G, W5-8)
===================================================================================================
MECP on an unverified Income Tax client opens the MECP card in SCC mode from the desktop. The window
has no reference to main, so main registers one function here (set_opener) and the window calls
open_card. The card's rows, the attempt and the save all stay in the SCC-U objects main owns; this
only carries the request. It works with "Detect login automatically" Off: then the card and its
"This one worked" button are the only path.
"""

from typing import Callable, Optional

_opener: Optional[Callable[..., bool]] = None


def set_opener(fn: Optional[Callable[..., bool]]) -> None:
    global _opener
    _opener = fn


def open_card(pan: str, client_id: Optional[int], on_error: Optional[Callable] = None) -> bool:
    """Opens the SCC card for `pan`. False when nothing could be shown (no opener yet, no Income
    Tax service, no password rows): the caller then falls back to the plain MECP card."""
    if _opener is None:
        return False
    try:
        return bool(_opener(pan, client_id, on_error))
    except Exception as e:
        print(f"[SCC] Client Detail card failed: {type(e).__name__}")
        return False
