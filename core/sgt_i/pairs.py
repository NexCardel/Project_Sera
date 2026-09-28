"""
core/sgt_i/pairs.py - container -> value pairs with generic types and masking
===============================================================================
Blueprint 14.4 step 2. Turns the page map's layout pairs (core/sgt_i/page_map.py) into the only
form SGT-I may keep them in: the container path, a generic type and a masked shape - never the
value.

* container    - the nested label path down to a pair: the section headings it sits under, then
                 its own label ("Bank Details" -> "Account Number"). In a table the column
                 header is already the pair's label (page_map.py), so no special case is needed.
* generic type - text, number, amount, date, code (letters and digits mixed), email, phone,
                 choice, percentage, yes/no. What a value LOOKS like, guessed from its shape and
                 the page-map pair's own method - never what it means (14.9: meaning comes only
                 from a registered Core spec). A page-map "choice" pair already carries a
                 structural answer (a chosen radio/dropdown option), so it is typed "choice"
                 without inspecting the text at all.
* masked shape - the value with every letter turned into "A" and every digit into "9", every
                 other character kept as-is (14.9's own example: "ABCDE1234F" -> "AAAAA9999A";
                 14.4 step 2's own example: an Aadhaar-shaped value -> "9999 9999 9999").

Privacy rule 1 (14.5): a value is read only long enough to compute its type and shape, then
dropped - classify_type() and mask_shape() both take a value and return something that is never
the value. ValuePair has no field a value could hide in. Pure functions over plain data: no UIA,
no I/O, no state (matches page_map.py).
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from . import page_map as pm

GENERIC_TYPES: Tuple[str, ...] = (
    "text", "number", "amount", "date", "code", "email", "phone", "choice", "percentage", "yes/no",
)

_YES_NO = frozenset({"yes", "no", "y", "n", "true", "false"})
_PERCENT_RE = re.compile(r"^[+-]?\d+(\.\d+)?\s?%$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# Phone: explicit shapes only, so a dashed date or an unformatted account number never matches by
# accident. A country code needs its own leading "+" or its own separator - an unbroken run of
# digits (however long) is a number, not a phone, unless it is exactly a bare 10-digit mobile.
_PHONE_RES = (
    re.compile(r"^\+\d{1,3}[-\s]?\d{10}$"),                 # +91 9876543210, +919876543210
    re.compile(r"^\+?\d{1,3}[-\s]\d{5}[-\s]\d{5}$"),         # +91 98765 43210, 91-98765-43210
    re.compile(r"^\(\d{2,4}\)[-\s]?\d{6,8}$"),               # (022) 23456789
    re.compile(r"^\d{10}$"),                                 # bare 10-digit mobile, no separators
)
_DATE_RES = (
    re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$"),                # 12/03/2026, 12-03-26
    re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"),                        # 2026-03-12 (ISO)
    re.compile(r"^\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{2,4}$"),        # 12-Mar-2026, 12 March 2026
    re.compile(r"^[A-Za-z]{3,9}\s\d{1,2},?\s\d{4}$"),              # March 12, 2026
)
# Amount: a currency symbol, or a thousands-grouped number (Indian or international) - a plain
# decimal like "3.14" is not money on its own, so it stays "number".
_AMOUNT_RE = re.compile(r"^[+-]?[₹$€£]\s?\d[\d,]*(\.\d{1,2})?$|^[+-]?\d{1,3}(,\d{2,3})+(\.\d{1,2})?$")
_CODE_RE = re.compile(r"^[A-Za-z0-9]+$")
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:[ ]\d+)*(\.\d+)?$")


def mask_shape(value: str) -> str:
    """Every letter -> "A", every digit -> "9", everything else (spaces, punctuation) unchanged."""
    return "".join("A" if ch.isalpha() else "9" if ch.isdigit() else ch for ch in value)


def classify_type(value: str, method: str = "") -> str:
    """A generic type for a value, never its meaning. `method` is the page-map pair's own method
    (control / choice / column / right / below); a "choice" pair is a chosen option, so it is
    typed "choice" from the structure alone, without looking at the text."""
    if method == "choice":
        return "choice"
    v = value.strip()
    if not v:
        return "text"
    if v.casefold() in _YES_NO:
        return "yes/no"
    if _PERCENT_RE.match(v):
        return "percentage"
    if _EMAIL_RE.match(v):
        return "email"
    if any(r.match(v) for r in _PHONE_RES):
        return "phone"
    if any(r.match(v) for r in _DATE_RES):
        return "date"
    if _AMOUNT_RE.match(v):
        return "amount"
    if _CODE_RE.match(v) and any(c.isalpha() for c in v) and any(c.isdigit() for c in v):
        return "code"
    if _NUMBER_RE.match(v):
        return "number"
    return "text"


@dataclass(frozen=True)
class ValuePair:
    """What SGT-I may keep for one page-map pair. No field carries the value itself."""
    container: Tuple[str, ...]     # section headings, outermost first, then this pair's own label
    type: str                      # one of GENERIC_TYPES
    shape: str                     # mask_shape(value)
    method: str                    # the page-map pair's own method
    zone: str
    row: Optional[int] = None      # table pairs: the row


def container_path(page: pm.PageMap, pair: pm.Pair) -> Tuple[str, ...]:
    """The section headings a pair sits under, then its own label."""
    return page.section_path(pair.section) + (pair.label,)


def pairs_from_page(page: pm.PageMap) -> Tuple[ValuePair, ...]:
    """Every page-map pair, reduced to what SGT-I may keep (blueprint 14.4 step 2)."""
    return tuple(
        ValuePair(container_path(page, p), classify_type(p.value, p.method), mask_shape(p.value),
                  p.method, p.zone, p.row)
        for p in page.pairs
    )
