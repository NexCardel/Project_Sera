"""
core/scc - SCC-U, Income Tax login detection on SGT's UIA reads (autofill-tweaks blueprint Part G)
=================================================================================================
It gets a read-only copy of each page SGT reads, on its own thread, and never changes what SGT
captures: see host.py for the contract. Switched by Settings -> SCC -> Detect login automatically
(scc_detect_mode).
"""

from .host import SccHost

__all__ = ["SccHost"]
