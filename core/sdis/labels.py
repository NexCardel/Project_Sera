"""
core/sdis/labels.py - what kind of value a text is, and which elements are composites
=======================================================================================
Value types (SGT-I's generic ones plus period, label, sentence, alphanumeric, control), the period
rule, and composites (an element whose text is only its descendants' texts joined). Moved unchanged
from the pre-dev compare.py, which imports it all back.
"""

import re
from typing import Any, Dict, List

from core.sdis.keys import BUTTON_CTYPES, CHOICE_CTYPES
from core.sgt_i.pairs import classify_type

# Only these value types can be FIXED (template). Any other type (number, date, period, code,
# alphanumeric, amount, email, phone, percentage, yes/no, control) is data-shaped: a bare number is
# a count or balance two clients can share (0 = 0), never template. When both sessions show the
# same value it is SEMI-VARIABLE - a value the clients compared so far happened to share - until a
# session with a different value makes it variable. "control" = a choice the user makes (dropdown,
# radio, checkbox - keys.CHOICE_CTYPES). Buttons are actions, not data: left out of the comparison.
FIXABLE_TYPES = frozenset({"text", "label", "sentence"})
SENTENCE_WORDS = 8

# Period: a span of time rather than one day. Month words must be real month names, so a hyphenated
# word ("Non-filer", "e-Verify") never matches.
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_YEAR_PREFIX = r"(?:(?:a\.?\s?y\.?|f\.?\s?y\.?|assessment\s+year|financial\s+year|tax\s+year|year)\s*:?\s*)?"
_PERIOD_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"^{_YEAR_PREFIX}(?:19|20)\d\d\s?[-–/]\s?(?:(?:19|20)\d\d|\d\d)$",   # A.Y. 2026-27, FY 2025-2026
    r"^(?:a\.?\s?y\.?|f\.?\s?y\.?)\s*:?\s*\d\d\s?[-–/]\s?\d\d$",          # AY 26-27, F.Y. 25-26 (prefix needed)
    rf"^{_MONTH}\s?[-–/',]?\s?(?:(?:19|20)\d\d|\d\d)$",                   # April 2026, Apr-2026, Apr'26
    rf"^{_MONTH}\s?[-–]\s?(?:19|20)\d\d$",                                 # January - 2021
    r"^(?:0?[1-9]|1[0-2])\s?[-/]\s?(?:19|20)\d\d$",                        # 04/2026, 4-2026
    rf"^q[1-4]\b.*$",                                                      # Q1 2026, Q1 (Apr-Jun)
    rf"^{_MONTH}\s?[-–]\s?{_MONTH}(?:\s?,?\s?(?:(?:19|20)\d\d|\d\d))?$",   # Apr-Jun 2026, April - June
))


def is_period(text: str) -> bool:
    v = " ".join(text.split())
    return any(r.match(v) for r in _PERIOD_RES)


def value_type(text: str) -> str:
    """SGT-I's generic type (core/sgt_i/pairs.classify_type: text, number, amount, date, code,
    email, phone, percentage, yes/no), plus four for this comparison: "period" (a year, month or
    quarter span - A.Y. 2026-27, AY 26-27, April 2026, Q1), "label" (text ending in ':'),
    "sentence" (SENTENCE_WORDS+ words) - those two almost always furniture - and "alphanumeric"
    (digits mixed with letters or punctuation that no other type claimed: 139(1), Flat 4B, Tower 2;
    "text" is then only words). The more
    specific type wins: a single letters+digits token stays "code" (PAN, ARN), a date stays
    "date", a period "period"; a label or sentence holding a digit stays label / sentence."""
    t = classify_type(text)
    if t != "text":
        return t
    if is_period(text):
        return "period"
    if text.rstrip().endswith(":"):
        return "label"
    if len(text.split()) >= SENTENCE_WORDS:
        return "sentence"
    if any(c.isdigit() for c in text):
        return "alphanumeric"      # digits mixed with letters or punctuation: 139(1), Flat 4B, 26-27
    return "text"


def element_type(e: Dict[str, Any]) -> str:
    """The value type of a flat entry: "control" for a choice element, else value_type(text)."""
    return "control" if e["node"].get("ctype") in CHOICE_CTYPES else value_type(e["text"])


CT_HYPERLINK, CT_IMAGE, CT_TABLE = 50005, 50006, 50036
NEVER_LABEL_CTYPES = frozenset({CT_HYPERLINK, CT_IMAGE}) | BUTTON_CTYPES


def _end(flat: List[Dict[str, Any]], i: int) -> int:
    """The index just past element i's subtree."""
    j = i + 1
    while j < len(flat) and flat[j]["depth"] > flat[i]["depth"]:
        j += 1
    return j


def composites(flat: List[Dict[str, Any]]) -> set:
    """Elements whose text is only their descendants' texts joined - at least two of them -
    ("<name> <GSTIN>", "79,99,235.00 View/Update"). Worked bottom-up, so a composite inside a
    composite is not counted twice: its parts are."""
    out: set = set()
    for i in range(len(flat) - 1, -1, -1):
        text = " ".join(flat[i]["text"].split())
        if not text:
            continue
        parts = [flat[j]["text"] for j in range(i + 1, _end(flat, i)) if flat[j]["text"] and j not in out]
        if len(parts) >= 2 and " ".join(" ".join(parts).split()) == text:
            out.add(i)
    return out
