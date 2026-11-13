"""
tools/pre_dev/class_diff/render.py - PRE-DEV alias of core/uia_render/render.py (moved 2026-11-07)
==================================================================================================
Same CLI, same module: importing this file gives the core module itself (sys.modules swap), so
`import render` from this folder and monkeypatching it keep working.

usage: python tools/pre_dev/class_diff/render.py <file> [--snapshot N] [--doc N|all] [--record N|list]
                                                  [--comp-view present|all] [--out PATH] [--open]
                                                  [--groups] [--arrange] [--outline]
"""

import sys
from pathlib import Path

_REPO = str(Path(__file__).resolve().parents[3])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from core.uia_render import render as _engine  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_engine.main())
sys.modules[__name__] = _engine
