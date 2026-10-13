"""
core/sdis/paths.py - where SDIS keeps its data (admin PC only)
===============================================================
data_dir(): env SDIS_DATA_DIR if set (the pre-dev aliases and the regression runner set it), else
~/<SERA_DATA_DIR_NAME>/sdis. Also write_csv(), the one CSV writer of the engine.
"""

import csv
import os
from pathlib import Path
from typing import Any, Dict, List


def data_dir() -> Path:
    env = os.environ.get("SDIS_DATA_DIR")
    if env:
        return Path(env)
    from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME   # importing core.vsdc loads Qt: only when needed
    return Path.home() / SERA_DATA_DIR_NAME / "sdis"


def write_csv(out: Path, fields: List[str], rows: List[Dict[str, Any]]) -> Path:
    """Write rows (UTF-8 with BOM, so Excel reads it right). When `out` is open in Excel (locked),
    write beside it as _2, _3... Returns the path written."""
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.stem
    for n in range(2, 100):
        try:
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            return out
        except PermissionError:
            out = out.with_name(f"{stem}_{n}.csv")
    raise SystemExit(f"Could not write {out} - close it in Excel and run again.")
