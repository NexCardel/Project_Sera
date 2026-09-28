"""
core/sgt_i - SGT-I, the Intelligence half of SGT (blueprint section 14)
======================================================================
It watches the pages the Core (core/sgt) reads, on its own thread, and never changes what the
Core captures: see host.py for the contract. Switched by Settings -> Tracker -> SGT-I (sgt_i_mode).
"""

from .host import SgtIntelligence
from .observation import Observation, make_observation


def default_components() -> list:
    """The SGT-I components the app runs: the atlas, fed every page read (step 4), then the GPS
    reading that same atlas (step 5; costs nothing while it is empty, 14.6), the evidence ledger (step 7), which reads the GPS's route - so it runs
    after it - page diffing / what flashed (step 9) and what no spec claimed (step 10)."""
    from .atlas import AtlasComponent
    from .gps import GpsComponent
    from .ledger import LedgerComponent
    from .page_diff import FlashComponent
    from .residues import ResiduesComponent

    atlas = AtlasComponent()
    gps = GpsComponent(atlas=atlas.atlas)
    return [atlas, gps, LedgerComponent(gps=gps), FlashComponent(), ResiduesComponent()]


__all__ = ["SgtIntelligence", "Observation", "make_observation", "default_components"]
