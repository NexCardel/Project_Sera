"""
responsive.py
-------------
Width modes for pages that rearrange themselves in a narrow window (plan: docs/ui-scale-adaptive-layout-plan.md).

Pages measure their own width (not the screen) and call mode_for() from resizeEvent. The two
thresholds leave a gap, so a page sitting near the edge does not flip between modes on every resize.
"""
from enum import Enum

COMPACT_BELOW = 1080   # go COMPACT when the page gets narrower than this (logical px)
WIDE_ABOVE = 1120      # come back to WIDE only once the page is wider than this


class WidthMode(Enum):
    WIDE = "wide"
    COMPACT = "compact"


def mode_for(width: int, current: WidthMode) -> WidthMode:
    if current is WidthMode.WIDE and width < COMPACT_BELOW:
        return WidthMode.COMPACT
    if current is WidthMode.COMPACT and width > WIDE_ABOVE:
        return WidthMode.WIDE
    return current
