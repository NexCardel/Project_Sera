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

from typing import Any, Optional

GATE_SIZE = 32                   # cells per side
GATE_LEVELS = 16                 # grey levels per cell
GATE_MIN_CELLS = 3               # cells that must change for a UIA read (D4)
FLASH_MIN_CELLS = 8              # cells that must change for an OCR flash read (D4; WP7 wires it)
GATE_LEVEL_DELTA = 2             # a cell "changed" when its level moved by at least this


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
