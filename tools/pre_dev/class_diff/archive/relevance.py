"""
tools/pre_dev/class_diff/archive/relevance.py - ARCHIVED alias of core/sdis/relevance.py (SDIS W3-2).
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parents[3]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[3]))
import core.sdis.relevance as _m

sys.modules[__name__] = _m
