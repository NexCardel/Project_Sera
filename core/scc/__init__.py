"""
core/scc - SCC-U, Income Tax login detection on SGT's UIA reads (autofill-tweaks blueprint Part G)
=================================================================================================
It gets a read-only copy of each page SGT reads, on its own thread, and never changes what SGT
captures: see host.py for the contract. Switched by Settings -> SCC -> Detect login automatically
(scc_detect_mode).
"""

from .attempts import Attempt, AttemptOpener, ClientInfo, db_lookup
from .card import SccCard
from .counts import SccCounter
from .host import SccHost
from . import manual
from .outcome import OutcomeReader, db_client_names
from .save import SaveResult, SccSaver, save_verified
from .scc_rules import RuleSet, get_rules, load_rules
from .which import WhichOne, pick_rows

__all__ = ["Attempt", "AttemptOpener", "ClientInfo", "OutcomeReader", "RuleSet", "SaveResult", "SccCard", "SccCounter", "SccHost",
           "SccSaver", "WhichOne", "db_client_names", "db_lookup", "get_rules", "load_rules", "pick_rows",
           "save_verified"]
