"""
core/sgt_i - SGT-I, the Intelligence half of SGT (blueprint section 14)
======================================================================
It watches the pages the Core (core/sgt) reads, on its own thread, and never changes what the
Core captures: see host.py for the contract. Switched by Settings -> Tracker -> SGT-I (sgt_i_mode).
"""

from .host import SgtIntelligence
from .observation import Observation, make_observation


def default_components() -> list:
    """The SGT-I components the app runs. Only the GPS (step 5) so far; it costs nothing while
    its atlas is empty (cold start, 14.6) - later steps add to this list."""
    from .gps import GpsComponent

    return [GpsComponent()]


__all__ = ["SgtIntelligence", "Observation", "make_observation", "default_components"]
