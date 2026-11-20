"""
tests/sgt_engine_patch.py - pin a module-level name in every part of the SGT engine
====================================================================================
The engine (core/sgt/engine) is one class split over several modules, so a test that fixes the
clock, the date or the session ids for byte-identical output has to set the name in each module
that uses it. patch_engine(monkeypatch, "uuid", FakeUuid) does that.
"""

import importlib

ENGINE_PARTS = ("engine", "boundaries", "reading", "draft", "identity", "merge", "demotion", "rows",
                "sdis_rows", "ending", "persistence", "journal", "evidence_hooks", "session", "policy")


def patch_engine(monkeypatch, name, value):
    """Sets `name` to `value` in every engine module that has it; returns how many were patched."""
    n = 0
    for part in ENGINE_PARTS:
        mod = importlib.import_module(f"core.sgt.engine.{part}")
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, value)
            n += 1
    assert n, f"no part of the SGT engine uses {name!r}"
    return n
