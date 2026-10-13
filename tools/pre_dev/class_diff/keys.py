"""
tools/pre_dev/class_diff/keys.py - alias of core/sdis/keys.py (the engine moved to core/sdis, SDIS W0-2).
Setting keys.VIEW here sets it on the real module, so this file replaces itself in sys.modules.
"""
import os, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
os.environ.setdefault("SDIS_DATA_DIR", str(_HERE / "output"))
if str(_HERE.parents[2]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[2]))
import core.sdis.keys as _m

sys.modules[__name__] = _m
