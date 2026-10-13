"""core/ltt/engine.py - the LTT engine: previous / current / upcoming period of a service.

A service repeats every `gap` (monthly, quarterly, 45d, ...). One known deadline anchors the
schedule; every other deadline is the anchor stepped forward or back by whole gaps. Period k
ends on deadline k and starts the day after deadline k-1.

  current  = the period whose window is still open on the reference date: the first deadline on/after
             it, or - with `grace_days` - a deadline that has passed but is still being filed late
  previous = the period before it
  upcoming = the period after it

Real deadlines bend, so the engine can be told how:
  grace_days   a period stays "current" this many days after its deadline (late-filing window)
  overrides    {pattern deadline: extended deadline} for notified extensions
  holidays     dates a deadline cannot fall on; `roll_sundays` adds Sundays; it moves to the next day
  month_end    every deadline is the last day of its month (anchor 30 Apr -> 31 May, not 30 May)
  covers       month_before | same_month | fy_end, worked out per deadline (overrides `lag`)
The filing month always comes from the PATTERN deadline, so extending a due date into the next
month never changes which month is being filed.

Each period has one FILING MONTH whatever the gap, worded like the tracker's period_label,
e.g. "September (FY 2026-27)": the deadline's month minus `lag` months (default 1: a deadline in
October files for September). The financial year starts in April (`fy_start`); 1 = calendar year.
Command line: tools/ltt_engine.py. Pure standard library.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping

# name -> (unit, n); unit is "d" (days) or "m" (months)
_NAMED = {
    "daily": ("d", 1), "weekly": ("d", 7), "fortnightly": ("d", 14),
    "monthly": ("m", 1), "bimonthly": ("m", 2), "quarterly": ("m", 3),
    "half-yearly": ("m", 6), "halfyearly": ("m", 6), "biannual": ("m", 6),
    "yearly": ("m", 12), "annual": ("m", 12), "annually": ("m", 12),
}
_CUSTOM = re.compile(r"^(\d+)\s*([dwmy])$", re.I)


@dataclass(frozen=True)
class Gap:
    unit: str   # "d" or "m"
    n: int
    label: str


@dataclass(frozen=True)
class Period:
    start: date
    end: date   # the deadline
    filing: str = ""   # the filing month as the tracker words it, e.g. "September (FY 2026-27)"
    month: date | None = None   # first day of the filing month
    fy: str = ""                # financial year of the filing month, e.g. "2026-27"
    pattern_end: date | None = None   # the deadline before any extension / holiday move

    def __str__(self) -> str:
        ext = f" (extended from {self.pattern_end:%d %b %Y})" if self.pattern_end not in (None, self.end) else ""
        return f"{self.filing:<30} {self.start:%d %b %Y} -> {self.end:%d %b %Y}{ext}"


def parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"cannot read date {text!r} (use YYYY-MM-DD or DD-MM-YYYY)")


def parse_gap(text: str) -> Gap:
    key = text.strip().lower()
    if key in _NAMED:
        unit, n = _NAMED[key]
        return Gap(unit, n, key)
    m = _CUSTOM.match(key)
    if m:
        n, u = int(m.group(1)), m.group(2).lower()
        if n < 1:
            raise ValueError("gap must be at least 1")
        unit, n = {"d": ("d", n), "w": ("d", n * 7), "m": ("m", n), "y": ("m", n * 12)}[u]
        return Gap(unit, n, key)
    raise ValueError(f"unknown gap {text!r}; try monthly, quarterly, 45d, 2m ...")


COVERS = ("month_before", "same_month", "fy_end")


def _add_months(d: date, months: int, day: int) -> date:
    """Shift by whole months, using the anchor's day (clamped to month end)."""
    idx = d.year * 12 + (d.month - 1) + months
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _fy_name(year: int, month: int, fy_start: int) -> str:
    sy = year if month >= fy_start else year - 1
    return f"{sy}" if fy_start == 1 else f"{sy}-{(sy + 1) % 100:02d}"


class LTTEngine:
    def __init__(self, deadline: date, gap: Gap, lag: int = 1, fy_start: int = 4, *,
                 grace_days: int = 0, overrides: Mapping[date, date] | None = None,
                 holidays: Iterable[date] = (), roll_sundays: bool = False,
                 month_end: bool = False, covers: str | None = None):
        if grace_days < 0:
            raise ValueError("grace_days cannot be negative")
        if covers is not None and covers not in COVERS:
            raise ValueError(f"covers must be one of {', '.join(COVERS)}")
        self.anchor = deadline
        self.gap = gap
        self.lag = lag
        self.fy_start = fy_start
        self.grace_days = grace_days
        self.overrides = dict(overrides or {})
        self.holidays = frozenset(holidays)
        self.roll_sundays = roll_sundays
        self.month_end = month_end
        self.covers = covers

    def lag_for(self, deadline: date) -> int:
        """Months from the deadline's month back to the month it files for."""
        if self.covers is None:
            return self.lag
        if self.covers == "month_before":
            return 1
        if self.covers == "same_month":
            return 0
        fy_end = (self.fy_start - 2) % 12 + 1               # last month of the financial year
        return (deadline.month - fy_end) % 12 or 12

    def filing_month(self, deadline: date) -> tuple[date, str, str]:
        """The single filing month for a (pattern) deadline, gap-independent:
        (first day of the month, FY name, label like "September (FY 2026-27)").
        The label is worded exactly like the tracker's own period_label."""
        y, m = divmod(deadline.year * 12 + deadline.month - 1 - self.lag_for(deadline), 12)
        m += 1
        fy = _fy_name(y, m, self.fy_start)
        return date(y, m, 1), fy, f"{calendar.month_name[m]} (FY {fy})"

    def pattern(self, k: int) -> date:
        """The k-th deadline of the plain schedule (k=0 is the anchor): no extension, no holiday."""
        if self.gap.unit == "d":
            return self.anchor + timedelta(days=k * self.gap.n)
        return _add_months(self.anchor, k * self.gap.n, 31 if self.month_end else self.anchor.day)

    def _roll(self, d: date) -> date:
        while d in self.holidays or (self.roll_sundays and d.weekday() == 6):
            d += timedelta(days=1)
        return d

    def deadline(self, k: int) -> date:
        """Real deadline of the k-th period: a notified extension if there is one, else the
        pattern date moved off holidays (and Sundays when asked)."""
        p = self.pattern(k)
        return self.overrides[p] if p in self.overrides else self._roll(p)

    def _open_until(self, k: int) -> date:
        return self.deadline(k) + timedelta(days=self.grace_days)

    def _index_open_on(self, ref: date) -> int:
        """Smallest k whose window (deadline + grace) has not closed before `ref`."""
        if self.gap.unit == "d":
            k = -((self.anchor - ref).days // self.gap.n)  # ceil((ref-anchor)/n)
        else:
            k = ((ref.year - self.anchor.year) * 12 + (ref.month - self.anchor.month)) // self.gap.n
        while self._open_until(k) < ref:
            k += 1
        while self._open_until(k - 1) >= ref:
            k -= 1
        return k

    def period(self, k: int) -> Period:
        end, pat = self.deadline(k), self.pattern(k)
        month, fy, label = self.filing_month(pat)
        return Period(self.deadline(k - 1) + timedelta(days=1), end, label, month, fy, pat)

    def locate(self, ref: date, count: int = 1) -> dict:
        """previous / current / upcoming around `ref`; `count` periods each side."""
        k = self._index_open_on(ref)
        return {
            "previous": [self.period(k - i) for i in range(count, 0, -1)],
            "current": self.period(k),
            "upcoming": [self.period(k + i) for i in range(1, count + 1)],
        }


def _when(p: Period, ref: date, grace: int = 0) -> str:
    days = (p.end - ref).days
    if days < 0 and grace and -days <= grace:
        return f"was due {-days} day{'s' if days != -1 else ''} ago, late-filing window open {grace + days} more"
    if days > 0:
        return f"due in {days} day{'s' if days != 1 else ''}"
    if days == 0:
        return "due TODAY"
    return f"was due {-days} day{'s' if days != -1 else ''} ago"


def render(engine: LTTEngine, ref: date, count: int = 1) -> str:
    r = engine.locate(ref, count)
    lines = [
        "LTT ENGINE",
        f"  anchor deadline : {engine.anchor:%d %b %Y}",
        f"  gap             : {engine.gap.label}   (lag {engine.lag}, FY starts month {engine.fy_start})",
        f"  as of           : {ref:%d %b %Y}",
        "",
    ]
    for tag in ("previous",):
        for p in r[tag]:
            lines.append(f"  PREVIOUS  {p}   ({_when(p, ref, engine.grace_days)})")
    lines.append(f"  CURRENT   {r['current']}   ({_when(r['current'], ref, engine.grace_days)})")
    for p in r["upcoming"]:
        lines.append(f"  UPCOMING  {p}   ({_when(p, ref, engine.grace_days)})")
    return "\n".join(lines)
