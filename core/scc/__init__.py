"""
core/scc - SCC-U, Income Tax login detection on SGT's UIA reads (autofill-tweaks blueprint Part G)
=================================================================================================
It gets a read-only copy of each page SGT reads, on its own thread, and never changes what SGT
captures: see host.py for the contract. Switched by Settings -> SCC -> Detect login automatically
(scc_detect_mode).
"""

from .attempts import Attempt, AttemptOpener, ClientInfo, db_lookup
from .card import SccCard
from .host import SccHost
from .scc_rules import RuleSet, get_rules, load_rules

__all__ = ["Attempt", "AttemptOpener", "ClientInfo", "RuleSet", "SccCard", "SccHost", "db_lookup", "get_rules",
           "load_rules"]
