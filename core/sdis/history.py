"""
core/sdis/history.py - value history and capture timestamps
============================================================
Timestamp utilities for SDIS value history and noise-over-time analysis.
Stamps come from capture snapshots ('20261001_235545+0005.0'), single reads
('20261001_235545'), or ISO timestamps ('2026-10-01T23:55:45').
"""

from typing import Any


def day_of(stamp: Any) -> str:
    """Return 'YYYYMMDD' from a stamp, or '' if fewer than 8 digits.

    Stamps can be a capture snapshot ('20261001_235545+0005.0'), a single read
    ('20261001_235545'), or an ISO time ('2026-10-01T23:55:45').
    Takes the first 8 digits after removing '-'; returns '' if there are fewer.
    """
    if not stamp or not isinstance(stamp, str):
        return ""
    digits = [ch for ch in stamp.replace("-", "") if ch.isdigit()]
    if len(digits) < 8:
        return ""
    return "".join(digits[:8])
