"""
tools/pre_dev/class_diff/align.py - alias of core/sdis/align.py (the engine moved to core/sdis, SDIS W0-2).
Tests set attributes on this module, so it replaces itself in sys.modules with the real one.
"""
import os, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
os.environ.setdefault("SDIS_DATA_DIR", str(_HERE / "output"))
if str(_HERE.parents[2]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[2]))
import core.sdis.align as _m

sys.modules[__name__] = _m
