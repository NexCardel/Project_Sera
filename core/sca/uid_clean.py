"""
core/sca/uid_clean.py - the UID inside a picked node's text (docs/sca-uid-node-plan.md WP5)
==========================================================================================
A picked node can hold more than the UID: "GSTIN: 27ABCDE1234F1Z5", "Welcome abc@gmail.com". A rule FINDS
the UID in the text; the result is sca_protocol.normalize_uid'd, so it is the same key the client index uses.

Rules (the picker suggests one with auto_rule; staff may change it; the service keeps it):
  GSTIN     the first GSTIN in the text
  PAN       the first PAN in the text (a GSTIN's embedded PAN does not count)
  EMAIL     the part before "@" of the first email address (the database stores only that part)
  LOGIN_ID  the text when it is one compact token, else the token after the last ":" ("User ID: abc12")
  WHOLE     the whole text
'' means "no UID in this text": the watcher then does not arm.
"""

import re
from typing import Dict

import sca_protocol

RULES = ("GSTIN", "PAN", "EMAIL", "LOGIN_ID", "WHOLE")

_GSTIN = re.compile(r"(?<![0-9A-Z])[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z](?![0-9A-Z])")
_PAN = re.compile(r"(?<![0-9A-Z])[A-Z]{5}[0-9]{4}[A-Z](?![0-9A-Z])")
_EMAIL = re.compile(r"([A-Z0-9][A-Z0-9._%+-]*)@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+")


def _ok(uid: str) -> str:
    return uid if uid and 3 <= len(uid) <= 80 and sca_protocol.looks_like_uid(uid) else ""


def clean(text: str, rule: str) -> str:
    t = sca_protocol.normalize_uid(text)
    if not t:
        return ""
    if rule == "GSTIN":
        m = _GSTIN.search(t)
        return m.group(0) if m else ""
    if rule == "PAN":
        m = _PAN.search(t)
        return m.group(0) if m else ""
    if rule == "EMAIL":
        m = _EMAIL.search(t)
        return _ok(m.group(1)) if m else ""
    if rule == "LOGIN_ID":
        if " " not in t:
            return _ok(t)
        tail = t.rsplit(":", 1)[1].strip() if ":" in t else ""
        return _ok(tail) if tail and " " not in tail else ""
    if rule == "WHOLE":
        return _ok(t)
    return ""


def auto_rule(text: str) -> str:
    """The first rule, in RULES order, that finds a UID; '' when none does."""
    for rule in RULES:
        if clean(text, rule):
            return rule
    return ""


def previews(text: str) -> Dict[str, str]:
    """{rule: uid} for every rule that finds one (the picker shows them)."""
    return {r: u for r in RULES for u in [clean(text, r)] if u}
