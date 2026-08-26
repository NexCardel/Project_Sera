"""Evidence-based taxpayer/client name selection for tracker dump events."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_NON_NAME_PARTS = ("bank", "account", "branch", "ifsc", "institution", "holdertype", "status", "message", "error")
_NAME_KEYS = {"fullname": 100, "full_name": 100, "assesseename": 98, "assessee_name": 98, "legalname": 94, "legal_name": 94, "bn": 98, "ln": 98, "tn": 82, "tradename": 92, "trade_name": 92, "authsignatory": 90, "auth_signatory": 90, "proprietorname": 90, "proprietor_name": 90, "taxpayername": 88, "taxpayer_name": 88, "nameasperbank": 86, "name": 70, "firstname": 45, "first_name": 45, "lastname": 45, "last_name": 45}


def _walk(value: Any, path: str = "") -> Iterable[tuple[str, Any, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            yield key_text, child, child_path
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _valid_name(value: Any) -> str | None:
    text = _clean(value)
    upper = text.upper()
    if len(text) < 3 or len(text) > 120 or _PAN_RE.fullmatch(upper) or _GSTIN_RE.fullmatch(upper):
        return None
    if "@" in text or "HTTP://" in upper or "HTTPS://" in upper or not re.search(r"[A-Za-z]", text):
        return None
    if sum(ch.isdigit() for ch in text) > 2:
        return None
    return text


def extract_name_evidence(header: Dict[str, Any], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract candidate names without allowing bank/account metadata to win."""
    candidates: Dict[str, Dict[str, Any]] = {}
    first = last = ""
    for key, value, path in _walk(payload if isinstance(payload, dict) else {}):
        key_lower = key.lower()
        if key_lower in ("firstname", "first_name", "fname"):
            first = _clean(value)
        elif key_lower in ("lastname", "last_name", "lname", "surname", "sur_name"):
            last = _clean(value)
        base_score = _NAME_KEYS.get(key_lower)
        if base_score is None or any(part in key_lower for part in _NON_NAME_PARTS):
            continue
        name = _valid_name(value)
        if not name:
            continue
        score = base_score + (12 if len(name.split()) >= 2 else 0) + (4 if len(name) >= 10 else 0)
        normalized = name.casefold()
        record = candidates.setdefault(normalized, {"value": name, "sources": [], "score": score})
        record["score"] = max(record["score"], score)
        record["sources"].append(path)

    if first or last:
        combined = _valid_name(f"{first} {last}".strip())
        if combined:
            normalized = combined.casefold()
            record = candidates.setdefault(normalized, {"value": combined, "sources": [], "score": 0})
            record["score"] = max(record["score"], 103)
            record["sources"].append("payload.firstName+lastName")

    header_name = _valid_name(header.get("client_name") or header.get("header_name")) if isinstance(header, dict) else None
    if header_name:
        normalized = header_name.casefold()
        record = candidates.setdefault(normalized, {"value": header_name, "sources": [], "score": 92})
        record["score"] = max(record["score"], 92)
        record["sources"].append("header.client_name")
    return sorted(candidates.values(), key=lambda item: (-item["score"], -len(item["value"]), item["value"].casefold()))


def choose_client_name(candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = list(candidates)
    if not ordered:
        return {"client_name": "", "client_name_confidence": "unresolved", "client_name_candidates": []}
    winner = max(ordered, key=lambda item: (item.get("score", 0), len(item.get("value", "")), len(item.get("value", "").split())))
    score = winner.get("score", 0)
    return {"client_name": winner["value"], "client_name_confidence": "high" if score >= 100 else "medium" if score >= 80 else "low", "client_name_candidates": ordered}
