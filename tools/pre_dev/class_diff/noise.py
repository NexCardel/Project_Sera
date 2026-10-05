"""
tools/pre_dev/class_diff/noise.py - alias of core/sdis/noise.py (SDIS W2-2).
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parents[2]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[2]))
import core.sdis.noise as _m

sys.modules[__name__] = _m
