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


def _container(flat: List[Dict[str, Any]], i: int) -> str:
    """The nearest ancestor that has a class - the box the element sits in."""
    p = flat[i]["parent"]
    while p >= 0:
        if flat[p]["cls"]:
            return flat[p]["cls"]
        p = flat[p]["parent"]
    return ""


def _children(flat: List[Dict[str, Any]], p: int) -> List[int]:
    return [j for j in range(p + 1, _end(flat, p)) if flat[j]["parent"] == p]


def _first_label(flat: List[Dict[str, Any]], i: int, labels: set) -> str:
    """Element i's own text if it can be a label, else the first one inside it."""
    for j in range(i, _end(flat, i)):
        if j in labels and flat[j]["text"]:
            return flat[j]["text"]
    return ""


def _same_column(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """How surely two cells sit in the same column: the browser's own column number when both
    carry one (SGT's reader keeps it as "grid"), else how much their screen boxes overlap
    sideways (0..1). Never the cell's count in its row: a view can drop a cell (SGT's control
    view drops an empty corner cell), and counting would shift every column by one."""
    ga, gb = a.get("grid"), b.get("grid")
    if ga and gb:
        return 1.0 if ga[1] == gb[1] else 0.0
    ra, rb = a.get("rect"), b.get("rect")
    if not ra or not rb or ra[2] <= 0 or rb[2] <= 0:
        return 0.0
    overlap = min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0])
    return max(0.0, overlap) / min(ra[2], rb[2])


def _table_label(flat: List[Dict[str, Any]], i: int, labels: set) -> str:
    """A table cell's label: its row's first cell and its column's header (the first earlier row
    that holds a label in that column), "row / column". Structure only: Table -> row -> cell."""
    a = i
    while a >= 0:
        r = flat[a]["parent"]
        t = flat[r]["parent"] if r >= 0 else -1
        if t >= 0 and flat[t]["node"].get("ctype") == CT_TABLE:
            break
        a = r
    else:
        return ""
    cells = _children(flat, r)
    row_label = _first_label(flat, cells[0], labels) if cells[0] != a else ""
    col_label = ""
    for other in _children(flat, t):
        if other == r:
            break
        best, best_cell = 0.5, -1
        for oc in _children(flat, other):
            score = _same_column(flat[a]["node"], flat[oc]["node"])
            if score > best:
                best, best_cell = score, oc
        if best_cell >= 0:
            col_label = _first_label(flat, best_cell, labels)
            if col_label:
                break
    return " / ".join(x for x in (row_label.strip(), col_label.strip()) if x)


LABEL_LOOKBACK = 6     # fallback: how many elements back a label may sit when no box holds one


def _label(flat: List[Dict[str, Any]], i: int, fixed: set) -> str:
    """The label of element i (fixed = the indexes that may be labels): a table cell's row and
    column (_table_label), else inside the smallest box around it that holds a fixed text, the
    fixed text NEAREST BEFORE it (else the first after it). When no box around it holds one - the
    browser can flatten a card, leaving "GSTIN OF TAXPAYER" and the value's box as plain siblings -
    the nearest fixed text at most LABEL_LOOKBACK elements before it in page order."""
    in_table = _table_label(flat, i, fixed)
    if in_table:
        return in_table
    a = flat[i]["parent"]
    while a >= 0:
        before, after = "", ""
        j = a + 1
        while j < len(flat) and flat[j]["depth"] > flat[a]["depth"]:
            if j != i and j in fixed and flat[j]["text"]:
                if j < i:
                    before = flat[j]["text"]
                else:
                    after = after or flat[j]["text"]
                    break
            j += 1
        if before or after:
            return before or after
        a = flat[a]["parent"]
    for j in range(i - 1, max(-1, i - 1 - LABEL_LOOKBACK), -1):
        if j in fixed and flat[j]["text"]:
            return flat[j]["text"]
    return ""


def cell_labels(flat: List[Dict[str, Any]]) -> Dict[int, str]:
    """{index: label} for every element inside a real table cell (core.sdis.tables finds the
    tables): the cell's column name top-down ("Tax / IGST"), and in a matrix (header_cols 1) its
    row's first cell in front. Header cells and cells whose column has no name get nothing, so the
    screen-box rules (_label) decide for them. Page order, outer tables first: a nested table's
    cells overwrite the outer cell they sit in."""
    from core.sdis import tables

    doc = [dict(e.get("node") or {}, parent=e["parent"]) for e in flat]
    out: Dict[int, str] = {}
    for tb in tables.extract([doc]):
        names, g = tables.column_names(tb), tables.grid(tb)
        for c in tb["cells"]:
            label = ""
            if c["row"] >= tb["header_rows"]:
                col_label = names[c["col"]] if c["col"] < len(names) else ""
                row_label = g[c["row"]][0] if tb["header_cols"] and c["col"] > 0 else ""
                label = " / ".join(x for x in (row_label.strip(), col_label.strip()) if x)
            for j in range(c["node"], _end(flat, c["node"])):
                out[j] = label
    return {j: s for j, s in out.items() if s}
