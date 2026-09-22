"""
core/sgt/sgt_toolbox.py — the shared transform / check library
================================================================
Field specs in sgt_fields.json name the tools they want ("transforms": ["upper"],
"checks": ["gstin_checksum"]). These are GENERIC: none of them knows which datapoint it is
serving. A new datapoint with an ordinary shape needs no new tool; only a genuinely new kind
of arithmetic (as "an ITR ack ends in its own filing date" once was) adds one here, once,
after which every spec can use it.

Transforms: str -> Optional[str]. None means "this value cannot be made valid" and drops it.
Checks:     (str, date) -> bool. The date is "today", passed in so tests control the clock.
"""

import re
from datetime import date, datetime
from typing import Callable, Dict, Optional

from core.vsdc.vsdc_regex import DIGIT_FIX_MAP

Transform = Callable[[str], Optional[str]]
Check = Callable[[str, date], bool]

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_MON3 = {m[:3].lower(): i + 1 for i, m in enumerate(_MONTHS)}

_DATE_FORMATS = (
    "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    "%d/%m/%Y %H:%M", "%d-%b-%Y, %I:%M %p",
)


# ── Transforms ───────────────────────────────────────────────────────────────────
def _parse_date(value: str) -> Optional[date]:
    v = re.sub(r"\s+", " ", value.strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def t_parse_date(value: str) -> Optional[str]:
    """Any date the portals print -> ISO YYYY-MM-DD, the format every payload uses."""
    d = _parse_date(value)
    return d.isoformat() if d else None


def t_dash_form(value: str) -> Optional[str]:
    """'ITR 4' / 'itr4' / 'GSTR3B' / 'CMP 08' -> 'ITR-4' / 'GSTR-3B' / 'CMP-08'."""
    m = re.fullmatch(r"\s*([A-Za-z]+)\s*-?\s*([0-9][0-9A-Za-z]*)\s*", value)
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2).upper()}"


def t_ay_label(value: str) -> Optional[str]:
    """'2025-26' / '2025 - 2026' / 'A.Y. 2025-26' -> 'AY 2025-26' (the tracker's AY format)."""
    m = re.search(r"(20\d{2})\s*[-/–]\s*(?:20)?(\d{2})\b", value)
    if not m:
        return None
    return f"AY {m.group(1)}-{m.group(2)}"


def t_gst_month_period(value: str) -> Optional[str]:
    """
    'Jun - 2026' / 'June 2026' -> 'June (FY 2026-27)', the tracker's GST period format.
    The financial year runs April-March, so January-March belong to the year before.
    """
    m = re.search(r"([A-Za-z]{3,9})\s*[-,]?\s*(20\d{2})", value)
    if not m:
        return None
    month = _MON3.get(m.group(1)[:3].lower())
    if not month:
        return None
    year = int(m.group(2))
    start = year if month >= 4 else year - 1
    return f"{_MONTHS[month - 1]} (FY {start}-{(start + 1) % 100:02d})"


def t_ay_from_year(value: str) -> Optional[str]:
    """
    An assessment year from its starting year alone, as the ITR portal writes it in its
    links: 'ay2026' / '2026' / '26' -> 'AY 2026-27'.
    """
    m = re.fullmatch(r"\s*(?:20)?(\d{2})\s*", value or "")
    if not m:
        return None
    start = 2000 + int(m.group(1))
    return f"AY {start}-{(start + 1) % 100:02d}"


def period_sort_key(value: Optional[str]) -> Optional[tuple]:
    """
    Orders periods so "the latest" can be picked: 'AY 2026-27' after 'AY 2025-26', and
    'June (FY 2026-27)' after 'May (FY 2026-27)' (months run April -> March). None when the
    value is not a period at all.
    """
    m = re.search(r"(20\d{2})\s*[-/–]\s*(?:20)?\d{2}\b", value or "")
    if not m:
        return None
    month = 0
    mm = re.search(r"\b([A-Za-z]{3,9})\b", (value or "")[: m.start()])
    if mm and mm.group(1)[:3].lower() in _MON3:
        n = _MON3[mm.group(1)[:3].lower()]
        month = n - 3 if n >= 4 else n + 9          # April = 1 ... March = 12
    return (int(m.group(1)), month)


def period_start(value: Optional[str]) -> Optional[date]:
    """
    The first day a dataset for this period can exist: 'AY 2026-27' -> 1 Apr 2026 (returns for
    an assessment year are filed from its first day); 'June (FY 2026-27)' -> 1 Jun 2026;
    'FY 2026-27' -> 1 Apr 2026. None when the value is not a period.
    """
    key = period_sort_key(value)
    if key is None:
        return None
    start_year, fy_month = key
    if fy_month == 0:
        return date(start_year, 4, 1)
    month = fy_month + 3 if fy_month <= 9 else fy_month - 9     # 1 = April ... 12 = March
    return date(start_year if month >= 4 else start_year + 1, month, 1)


# ── The submit-status ladder ─────────────────────────────────────────────────────
# ONE status vocabulary for every portal. What a portal prints ("Pending for e-verification",
# "Filed", "Processed with refund"...) is only evidence; a spec's "map" turns it into one of
# these four levels, and the loader refuses a status map that names anything else. A dataset's
# status only ever moves up this ladder.
SUBMIT_LEVELS = (
    "Not Submitted",                # 0 - the default: nothing shows any work on it
    "Draft",                        # 1 - being prepared on the portal, not submitted
    "Submitted (Not Verified)",     # 2 - submitted, verification (e-Verify / DSC / EVC) outstanding
    "Submitted & Verified",         # 3 - submitted and verified - complete on the portal
)
_LEVEL_OF = {s.lower(): i for i, s in enumerate(SUBMIT_LEVELS)}


def submit_level(status: Optional[str]) -> int:
    """The ladder position of a status (0-3). Anything that is not a ladder label is 0."""
    return _LEVEL_OF.get(str(status or "").strip().lower(), 0)


def is_submit_level(status: Optional[str]) -> bool:
    return str(status or "").strip().lower() in _LEVEL_OF


TRANSFORMS: Dict[str, Transform] = {
    "upper": lambda v: v.upper(),
    "lower": lambda v: v.lower(),
    "strip": lambda v: v.strip(),
    "collapse_spaces": lambda v: re.sub(r"\s+", " ", v).strip(),
    "strip_spaces": lambda v: re.sub(r"\s+", "", v),
    "digits_only": lambda v: re.sub(r"[^0-9]", "", v) or None,
    # OCR digit confusions (O->0, l->1, S->5 ...). Only for values that must be all digits.
    "ocr_digits": lambda v: "".join(DIGIT_FIX_MAP.get(c, c) for c in v),
    "strip_edge_punct": lambda v: v.strip(" \t:;,.-–|()[]") or None,
    "parse_date": t_parse_date,
    "dash_form": t_dash_form,
    "ay_label": t_ay_label,
    "gst_month_period": t_gst_month_period,
    "ay_from_year": t_ay_from_year,
}


# ── Checks ───────────────────────────────────────────────────────────────────────
def _as_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return _parse_date(value)


def ddmmyy_tail_date(value: str) -> Optional[date]:
    """The date carried in the last six digits as DDMMYY (an ITR ack's own filing date)."""
    if not re.fullmatch(r"\d{6,}", value or ""):
        return None
    try:
        return date(2000 + int(value[-2:]), int(value[-4:-2]), int(value[-6:-4]))
    except ValueError:
        return None


def c_years_consecutive(value: str, today: date) -> bool:
    """'2025-26' / 'AY 2025-26': the second year is the first year + 1."""
    m = re.search(r"(20\d{2})\s*[-/–]\s*(\d{2,4})\b", value)
    if not m:
        return False
    first, second = int(m.group(1)), int(m.group(2))
    return second % 100 == (first + 1) % 100


def c_gstin_checksum(value: str, today: date) -> bool:
    """A GSTIN's 15th character is a mod-36 check character over the first 14."""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    v = (value or "").upper()
    if len(v) != 15 or any(c not in alphabet for c in v):
        return False
    total = 0
    for i, c in enumerate(v[:14]):
        product = alphabet.index(c) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return alphabet[(36 - total % 36) % 36] == v[14]


# Words that are the portal's own controls, never a person's or business's name.
_UI_CHROME = re.compile(
    r"\b(?:input\s+field|search\s+box|text\s*box|edit\s+box|combo\s*box|check\s*box|radio\s+button|"
    r"drop\s*-?\s*down|scroll\s*bar|placeholder|button|hyperlink|menu|dashboard|logout|log\s*out|login|"
    r"log\s*in|profile|session|select|click|download|upload|submit|e-?verify|continue|back|next|home|"
    r"help|services|skip\s+to|main\s+content|font\s+size|dark\s+theme|call\s+us|english)\b",
    re.IGNORECASE)


def c_not_ui_chrome(value: str, today: date) -> bool:
    return not _UI_CHROME.search(value or "")


def c_looks_like_name(value: str, today: date) -> bool:
    """Letters and the punctuation names use, at least one real word, no identifiers."""
    v = (value or "").strip()
    if not (2 <= len(v) <= 120) or not re.fullmatch(r"[A-Za-z][A-Za-z .'&()/,-]*", v):
        return False
    if re.search(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", v):
        return False
    return sum(1 for w in re.split(r"[\s.]+", v) if len(w) >= 2) >= 1


# ── Merge policies ───────────────────────────────────────────────────────────────
# What a session does when a profile datapoint it already holds is seen again with a new
# value. (current, candidate) -> True means the candidate replaces the current value.
Merge = Callable[[str, str], bool]


def _words(value: str) -> list:
    return [w for w in re.split(r"[^A-Za-z0-9]+", (value or "").upper()) if w]


def extends(current: str, candidate: str) -> bool:
    """
    True if `candidate` is `current` with more in it: every word of current appears in
    candidate, in order, and the LAST word of current may be a cut-off start of a longer word
    (a header that truncates "KHANNA" to "KHAN"). "RAVI MEHTA" -> "RAVI KUMAR MEHTA" yes;
    "RAVI MEHTA" -> "SUNIL ROY" no.
    """
    old, new = _words(current), _words(candidate)
    if not old or len(new) < len(old):
        return False
    j = 0
    for i, w in enumerate(old):
        last = i == len(old) - 1
        while j < len(new) and not (new[j] == w or (last and new[j].startswith(w))):
            j += 1
        if j == len(new):
            return False
        j += 1
    return True


MERGES: Dict[str, Merge] = {
    # Found once, never looked for again (the profile builder's default).
    "latch": lambda current, candidate: False,
    # Keeps looking; a longer value replaces the current one only if it extends it.
    "promote_longer": lambda current, candidate: len(candidate) > len(current) and extends(current, candidate),
}


CHECKS: Dict[str, Check] = {
    "is_real_date": lambda v, today: _as_date(v) is not None,
    "not_future": lambda v, today: (_as_date(v) or date.max) <= today,
    "date_is_today": lambda v, today: (d := _as_date(v)) is not None and abs((d - today).days) <= 1,
    "years_consecutive": c_years_consecutive,
    "all_digits": lambda v, today: bool(re.fullmatch(r"\d+", v or "")),
    "ddmmyy_tail_real": lambda v, today: ddmmyy_tail_date(v) is not None,
    "ddmmyy_tail_today": lambda v, today: (d := ddmmyy_tail_date(v)) is not None and abs((d - today).days) <= 1,
    "gstin_checksum": c_gstin_checksum,
    "not_ui_chrome": c_not_ui_chrome,
    "looks_like_name": c_looks_like_name,
}
