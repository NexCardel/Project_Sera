"""Safe, retrospective enrichment of Master Column List values from captures."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ui.utils.profile_parser import map_profile_to_mcl_columns


def _name_quality(value: Any) -> tuple[int, int, int]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return len(text.split()), len(text), sum(ch.isalpha() for ch in text)


def _is_name_column(label: str) -> bool:
    label = label.lower()
    return any(token in label for token in ("name", "proprietor", "director", "owner", "partner"))


def build_mcl_updates(mcl_columns: List[Dict[str, Any]], existing_values: Dict[int, Any], profiles: Iterable[Dict[str, Any]]) -> Dict[int, str]:
    """Return only safe changes: fill blanks, or improve an incomplete name."""
    candidates: Dict[int, List[str]] = {}
    for profile in profiles:
        mapped = map_profile_to_mcl_columns(profile, mcl_columns)
        for column_id, value in mapped.items():
            value = str(value or "").strip()
            if value:
                candidates.setdefault(column_id, []).append(value)

    updates: Dict[int, str] = {}
    for column in mcl_columns:
        column_id = column["id"]
        values = candidates.get(column_id, [])
        if not values:
            continue
        current = str(existing_values.get(column_id) or "").strip()
        if not current:
            key = _name_quality if _is_name_column(column.get("label", "")) else lambda value: (0, len(value), len(value))
            updates[column_id] = max(values, key=key)
        elif _is_name_column(column.get("label", "")):
            strongest = max(values, key=_name_quality)
            if _name_quality(strongest) > _name_quality(current):
                updates[column_id] = strongest
    return updates
