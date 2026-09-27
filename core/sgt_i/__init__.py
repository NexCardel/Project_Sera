"""
core/sgt_i - SGT-I, the Intelligence half of SGT (blueprint section 14)
======================================================================
It watches the pages the Core (core/sgt) reads, on its own thread, and never changes what the
Core captures: see host.py for the contract. Switched by Settings -> Tracker -> SGT-I (sgt_i_mode).
"""

from .host import SgtIntelligence
from .observation import Observation, make_observation


def default_components() -> list:
    """The SGT-I components the app runs: the GPS (step 5; costs nothing while its atlas is
    empty, 14.6) and the evidence ledger (step 7), which reads the GPS's route - so it runs
    after it. Later steps add to this list."""
    from .gps import GpsComponent
    from .ledger import LedgerComponent

    gps = GpsComponent()
    return [gps, LedgerComponent(gps=gps)]


__all__ = ["SgtIntelligence", "Observation", "make_observation", "default_components"]
