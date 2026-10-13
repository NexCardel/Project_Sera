"""
tools/pre_dev/class_diff/memory.py - alias of core/sdis/memory.py (the engine moved to core/sdis, SDIS W0-2).
Run it as before: python tools/pre_dev/class_diff/memory.py
"""
import os, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
os.environ.setdefault("SDIS_DATA_DIR", str(_HERE / "output"))
if str(_HERE.parents[2]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[2]))
import core.sdis.memory as _m

if __name__ == "__main__":
    raise SystemExit(_m.main())
else:
    sys.modules[__name__] = _m
