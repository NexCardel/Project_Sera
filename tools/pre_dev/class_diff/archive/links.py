"""
tools/pre_dev/class_diff/archive/links.py - ARCHIVED alias of core/sdis/links.py (SDIS W1-4).
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parents[3]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[3]))
import core.sdis.links as _m

sys.modules[__name__] = _m
