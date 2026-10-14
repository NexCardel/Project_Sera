"""
core/vsdc/vsdc_gate.py - the frame signature and the change gate
=================================================================
SGT never-miss blueprint section 4.4. A window's picture is shrunk to a 32x32 grid of 16 grey
levels (1024 bytes). Two signatures are compared cell by cell: a blinking caret or one typed
character moves a cell's average by under one level, a toast or a banner moves many cells.

Numbers are the owner's answer D4; WP0's `gate` probe (tools/sgt_probe_pack.py, which keeps an
identical copy of these two functions) measures them on a real PC and WP7 tunes them.
Pixels only: nothing here reads text.
"""

from typing import Any, List, Optional, Sequence, Tuple

GATE_SIZE = 32                   # cells per side
GATE_LEVELS = 16                 # grey levels per cell
GATE_MIN_CELLS = 3               # cells that must change for a UIA read (D4)
FLASH_MIN_CELLS = 8              # cells that must change for an OCR flash read (D4)
GATE_LEVEL_DELTA = 2             # a cell "changed" when its level moved by at least this
FLASH_UIA_SLOW_MS = 700.0        # the last read of the page took longer than this: a flash is not left to UIA
GREEN_SAME_BOX = 0.5             # a green box overlapping an earlier one by this share of the smaller is the same box


def frame_signature(img: Any) -> Optional[bytes]:
    """A 32x32 grid of 16-level greys; None when img is None or cannot be converted."""
    if img is None:
        return None
    try:
        from PIL import Image
        small = img.convert("L").resize((GATE_SIZE, GATE_SIZE), Image.BOX)
        step = 256 // GATE_LEVELS
        return small.point([v // step for v in range(256)]).tobytes()
    except Exception:
        return None


def cells_changed(a: Optional[bytes], b: Optional[bytes]) -> int:
    """How many cells moved by GATE_LEVEL_DELTA or more; every cell when either side is missing."""
    if a is None or b is None or len(a) != len(b):
        return GATE_SIZE * GATE_SIZE
    return sum(1 for x, y in zip(a, b) if abs(x - y) >= GATE_LEVEL_DELTA)


def flash_wanted(cells: int, uia_inflight: bool, uia_ms: float, uia_paused: bool) -> bool:
    """Blueprint 5.1's flash rule: a big enough change in the picture while UIA cannot be trusted to
    see it - a read of this window is running, the page's last read was slow, or UIA is paused."""
    if cells < FLASH_MIN_CELLS:
        return False
    return uia_inflight or uia_paused or uia_ms > FLASH_UIA_SLOW_MS


def green_boxes(img: Any) -> List[Tuple[int, int, int, int]]:
    """Success-coloured banners in the picture as (x, y, w, h): vsdc247's own detector, pixels only
    (about 9 ms). numpy comes with the import, so it happens here and not at start-up."""
    if img is None:
        return []
    try:
        from .vsdc247 import find_green_boxes
        return [(b.x, b.y, b.w, b.h) for b in find_green_boxes(img)]
    except Exception:
        return []


def new_green_box(before: Sequence[Tuple[int, int, int, int]], after: Sequence[Tuple[int, int, int, int]]) -> bool:
    """A green box in `after` that is not in `before` (a box that stays put is not new)."""
    for ax, ay, aw, ah in after:
        for bx, by, bw, bh in before:
            w = min(ax + aw, bx + bw) - max(ax, bx)
            h = min(ay + ah, by + bh) - max(ay, by)
            if w > 0 and h > 0 and w * h >= GREEN_SAME_BOX * min(aw * ah, bw * bh):
                break
        else:
            return True
    return False
