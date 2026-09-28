"""
core/sgt_i/invariants.py - checksum discovery, values inside values, relations
================================================================================
Blueprint 14.4 step 3, the rest of it (shapes.py already does the shape-grammar part). Given many
values from the same container - or paired values from two containers on the same page - this
module proves, with numbers, the rules they obey. Every function is pure and stateless: the caller
supplies whatever values it currently holds (a "value summary" - a batch, not a store), and gets
back only counts, positions and a confidence bound - never a value. Nothing here keeps a value
after the call returns (privacy rule 1, 14.5); persisting the *result* across pages is the atlas's
job (step 4), not this module's.

* **Checksum discovery.** Luhn, Verhoeff, mod-11 and a generalised mod-36 (the same alternating
  1/2-weight, base-36 scheme as `sgt_toolbox.c_gstin_checksum`, but for any length - that function
  stays fixed at 15 characters because it also has to know it is a GSTIN; this one does not).
  Passing a real check digit by chance is rare (14.4 step 3's own example: 1 in 36^30 for a mod-36
  container of 30 values) - but trying 4 schemes at once multiplies that luck, so the chance is
  Bonferroni-corrected by the number of schemes tried before it is reported.
* **Values inside values.** Is one container's value always found, unchanged, inside another's, at
  a position that holds across every sample? Covers both directions 14.9 names: a fixed offset
  from the start (GSTIN characters 3-12 hold the PAN) and a fixed offset from the end (an ITR ack's
  own trailing DDMMYY date - pair it with `format_ddmmyy` of the filing-date container).
* **Relations between containers.** "Filed on <= Processed on", "Total = sum of the rows" -
  invariant mining, the idea behind Daikon: count, don't guess.

*Where maths stops* (14.4 step 3): a passing scheme, a found offset or a held relation is
*structure*, proven by counting - never *meaning*. It cannot know the checksummed value is a
GSTIN, only that it always carries one of four known kinds of check digit.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Dict, Optional, Sequence, Tuple

from .shapes import shape_grammar_confidence

__all__ = [
    "ChecksumMatch", "CHECKSUM_SCHEMES", "discover_checksum",
    "ContainmentMatch", "find_value_containment", "format_ddmmyy",
    "OrderRelation", "find_order_relation",
    "SumRelation", "find_sum_relation",
]


# ── checksum discovery ───────────────────────────────────────────────────────────

def _luhn_ok(value: str) -> Optional[bool]:
    """None when `value` is not a Luhn candidate (digits only, at least 2 of them)."""
    v = value.strip()
    if not v.isdigit() or len(v) < 2:
        return None
    total = 0
    for i, ch in enumerate(reversed(v)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# The standard Verhoeff dihedral-group (d) and permutation (p) tables.
_VERHOEFF_D: Tuple[Tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P: Tuple[Tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def _verhoeff_ok(value: str) -> Optional[bool]:
    v = value.strip()
    if not v.isdigit() or len(v) < 2:
        return None
    c = 0
    for i, ch in enumerate(reversed(v)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def _mod11_ok(value: str) -> Optional[bool]:
    """Weights count down from the length to 1 (the ISBN-10 scheme); no 'X' check digit."""
    v = value.strip()
    if not v.isdigit() or len(v) < 2:
        return None
    n = len(v)
    total = sum(int(ch) * (n - i) for i, ch in enumerate(v))
    return total % 11 == 0


_MOD36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _mod36_ok(value: str) -> Optional[bool]:
    """Generalises `sgt_toolbox.c_gstin_checksum` (fixed at 15 characters, because it also has to
    know it is a GSTIN) to any length: the same alternating 1/2 weight, base-36 Luhn-style check
    over the last character."""
    v = value.strip().upper()
    if len(v) < 2 or any(c not in _MOD36_ALPHABET for c in v):
        return None
    total = 0
    for i, c in enumerate(v[:-1]):
        product = _MOD36_ALPHABET.index(c) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _MOD36_ALPHABET[(36 - total % 36) % 36] == v[-1]


# name -> (check function, chance a random value passes it)
CHECKSUM_SCHEMES: Dict[str, Tuple[Callable[[str], Optional[bool]], float]] = {
    "luhn": (_luhn_ok, 1 / 10),
    "verhoeff": (_verhoeff_ok, 1 / 10),
    "mod11": (_mod11_ok, 1 / 11),
    "mod36": (_mod36_ok, 1 / 36),
}


@dataclass(frozen=True)
class ChecksumMatch:
    """The best-fitting checksum scheme found for one container's values.

    scheme      - one of CHECKSUM_SCHEMES's keys.
    n           - how many values were well-formed candidates for this scheme (right alphabet,
                  long enough); values that are not are simply excluded, not exceptions.
    exceptions  - how many of those n failed the check.
    p_value     - the chance a container with no real checksum would pass this scheme on all n
                  values by luck, Bonferroni-corrected by the number of schemes tried (so testing
                  4 schemes at once cannot manufacture a false discovery); None when exceptions
                  is not zero (a single failure disproves the scheme outright).
    """
    scheme: str
    n: int
    exceptions: int
    p_value: Optional[float]


def discover_checksum(values: Sequence[str]) -> Optional[ChecksumMatch]:
    """Tests every scheme in CHECKSUM_SCHEMES against `values` and returns the one with the
    strongest (lowest) Bonferroni-corrected p-value among those with zero exceptions - or None if
    no scheme had any well-formed, zero-exception candidates."""
    candidates = []
    for name, (check, chance) in CHECKSUM_SCHEMES.items():
        outcomes = [o for o in (check(v) for v in values if v) if o is not None]
        n = len(outcomes)
        if n == 0:
            continue
        exceptions = sum(1 for o in outcomes if not o)
        p_value = None
        if exceptions == 0:
            p_value = min(1.0, (chance ** n) * len(CHECKSUM_SCHEMES))
        candidates.append(ChecksumMatch(name, n, exceptions, p_value))
    fits = [c for c in candidates if c.p_value is not None]
    if not fits:
        return None
    return min(fits, key=lambda c: c.p_value)


# ── values inside values ─────────────────────────────────────────────────────────

def _find_all(haystack: str, needle: str) -> Tuple[int, ...]:
    if not needle:
        return ()
    out = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            break
        out.append(idx)
        start = idx + 1
    return tuple(out)


def _exceptions_from_start(pairs: Sequence[Tuple[str, str]], offset: int) -> int:
    return sum(1 for o, g in pairs if o[offset:offset + len(g)] != g)


def _exceptions_from_end(pairs: Sequence[Tuple[str, str]], offset: int) -> int:
    exceptions = 0
    for o, g in pairs:
        end = len(o) - offset
        start = end - len(g)
        if start < 0 or o[start:end] != g:
            exceptions += 1
    return exceptions


@dataclass(frozen=True)
class ContainmentMatch:
    """A fixed position at which one container's value always holds another's.

    anchor      - "start": `offset` characters always come before the match. "end": `offset`
                  characters always come after it (0 means the match is the value's own suffix).
    n           - how many paired samples were compared (blank values on either side excluded).
    exceptions  - how many of those n did not hold the match at this position.
    confidence  - the rule-of-three bound (shapes.shape_grammar_confidence), corrected for the
                  number of candidate positions tried; None when exceptions is not zero.
    """
    anchor: str
    offset: int
    n: int
    exceptions: int
    confidence: Optional[float]


def find_value_containment(outer_values: Sequence[str], inner_values: Sequence[str],
                            confidence: float = 0.95) -> Optional[ContainmentMatch]:
    """Blueprint 14.4 step 3, "values inside values": `outer_values[i]` and `inner_values[i]` must
    come from the same observation (the same page, the same client). Finds the position - from the
    start or from the end - where the inner value sits inside the outer one for the most samples,
    e.g. a GSTIN's characters 3-12 always equal the PAN, or an ack's last 6 characters always equal
    `format_ddmmyy` of its own filing date. Returns None when fewer than 2 samples have a value on
    both sides, or when the inner value is never found inside the outer one in any sample."""
    pairs = [(o, g) for o, g in zip(outer_values, inner_values) if o and g]
    if len(pairs) < 2:
        return None

    start_candidates = sorted({pos for o, g in pairs for pos in _find_all(o, g)})
    end_candidates = sorted({len(o) - pos - len(g) for o, g in pairs for pos in _find_all(o, g)})
    if not start_candidates and not end_candidates:
        return None

    best: Optional[Tuple[str, int, int]] = None  # (anchor, offset, exceptions)
    for offset in start_candidates:
        exceptions = _exceptions_from_start(pairs, offset)
        if best is None or exceptions < best[2]:
            best = ("start", offset, exceptions)
    for offset in end_candidates:
        exceptions = _exceptions_from_end(pairs, offset)
        if best is None or exceptions < best[2]:
            best = ("end", offset, exceptions)

    anchor, offset, exceptions = best
    n = len(pairs)
    conf = None
    if exceptions == 0:
        base = shape_grammar_confidence(n, 0, confidence)
        tried = len(start_candidates) + len(end_candidates)
        conf = min(1.0, base * tried) if base is not None else None
    return ContainmentMatch(anchor, offset, n, exceptions, conf)


_DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d",
    "%d-%b-%Y", "%d %B %Y", "%B %d, %Y", "%B %d %Y",
)


def _parse_comparable(value: str):
    """A `date` or a `float` for `value`, whichever it parses as; None for neither - relations
    (14.4 step 3) only ever compare two values of the same kind."""
    v = (value or "").strip()
    if not v:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    cleaned = re.sub(r"[₹$€£,\s]", "", v)
    try:
        return float(cleaned)
    except ValueError:
        return None


def format_ddmmyy(value: str) -> Optional[str]:
    """A date value rendered as DDMMYY (14.9's own example), so it can be tested with
    `find_value_containment` against a container that is expected to end with its own date (an ITR
    acknowledgement number). None when `value` does not parse as a date at all."""
    parsed = _parse_comparable(value)
    if not isinstance(parsed, date):
        return None
    return parsed.strftime("%d%m%y")


# ── relations between containers ─────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderRelation:
    """"Filed on <= Processed on": holds for every paired sample, or it does not.

    n, exceptions   - samples where both sides parsed as the same kind (date or number); how many
                      of those broke the order.
    confidence      - the rule-of-three bound, corrected by 2 (discovering the direction - <=
                      versus >= - is itself one bit of multiple testing); None when exceptions
                      is not zero.
    """
    op: str
    n: int
    exceptions: int
    confidence: Optional[float]


def find_order_relation(a_values: Sequence[str], b_values: Sequence[str],
                         confidence: float = 0.95) -> Optional[OrderRelation]:
    """Tests whether `a[i] <= b[i]` holds for every paired sample (dates or numbers; a pair where
    either side does not parse, or the two sides parse as different kinds, is excluded rather than
    counted as an exception). Returns None when fewer than 2 samples are comparable."""
    pairs = []
    for a, b in zip(a_values, b_values):
        pa, pb = _parse_comparable(a), _parse_comparable(b)
        if pa is None or pb is None or type(pa) is not type(pb):
            continue
        pairs.append((pa, pb))
    n = len(pairs)
    if n < 2:
        return None
    exceptions = sum(1 for pa, pb in pairs if not (pa <= pb))
    conf = None
    if exceptions == 0:
        base = shape_grammar_confidence(n, 0, confidence)
        conf = min(1.0, base * 2) if base is not None else None
    return OrderRelation("<=", n, exceptions, conf)


@dataclass(frozen=True)
class SumRelation:
    """"Total = sum of the rows": one container's value against the sum of another's, paired
    sample by sample (e.g. a table's total row against its own amount column, one page at a
    time)."""
    n: int
    exceptions: int
    confidence: Optional[float]


def find_sum_relation(total_values: Sequence[str], row_group_values: Sequence[Sequence[str]],
                       tolerance: float = 0.01, confidence: float = 0.95) -> Optional[SumRelation]:
    """`total_values[i]` paired with `row_group_values[i]`, the rows summed for that same sample.
    Tests `total == sum(rows)` within `tolerance` (rounding). A sample where the total or any row
    fails to parse as a number, or that has no rows, is excluded rather than counted as an
    exception. Returns None when fewer than 2 samples are comparable."""
    pairs = []
    for total, rows in zip(total_values, row_group_values):
        t = _parse_comparable(total)
        parsed_rows = [_parse_comparable(r) for r in rows]
        if not isinstance(t, float) or not parsed_rows or any(not isinstance(r, float) for r in parsed_rows):
            continue
        pairs.append((t, parsed_rows))
    n = len(pairs)
    if n < 2:
        return None
    exceptions = sum(1 for t, rows in pairs if abs(t - sum(rows)) > tolerance)
    conf = shape_grammar_confidence(n, 0, confidence) if exceptions == 0 else None
    return SumRelation(n, exceptions, conf)
