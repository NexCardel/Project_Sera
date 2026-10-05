"""core/ltt/rules.py - which return forms the office tracks, and how each one repeats.

One rule per form (GSTR-3B, ITR, ...): how often it is filed, ONE example due date, and which
month it covers. The rules live in ltt_rules.json next to the vault and are edited in the Tracker
Dump window (Tools > LTT form rules), never by hand. Who files each form is not stored per
client: it is learned from the captures already in the tracker, then corrected with `include`
(add a client) and `exclude` (drop one) PAN lists on the rule.

`covers` says which month a due date files for:
    month_before - the month before the due date's month   (GSTR-3B due 20 Oct files for Sep)
    same_month   - the due date's own month
    fy_end       - March of the financial year just ended   (ITR due 31 Jul, GSTR-9 due 31 Dec)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .engine import LTTEngine, parse_date, parse_gap

RULES_FILE = "ltt_rules.json"
FEED_FILE = "ltt_feed.csv"
WORKBOOK_FILE = "ltt_tracker.xlsx"

EVERY_CHOICES = ("Monthly", "Quarterly", "Half-yearly", "Yearly")
COVERS_CHOICES = (
    ("month_before", "The month before the due date"),
    ("same_month", "The same month as the due date"),
    ("fy_end", "March of the financial year just ended"),
)
FY_START_MONTH = 4   # Indian financial year


@dataclass
class FormRule:
    form: str
    every: str = "Monthly"
    deadline: str = ""            # one real due date, ISO yyyy-mm-dd; every other one follows from it
    covers: str = "month_before"
    include: list = field(default_factory=list)   # PANs ticked by hand
    exclude: list = field(default_factory=list)   # PANs unticked by hand

    def problem(self) -> str:
        """Why this rule cannot be used, or "" when it is fine."""
        if not self.form.strip():
            return "The form needs a name."
        if self.every.lower() not in {c.lower() for c in EVERY_CHOICES}:
            return f"{self.form}: pick how often it is filed."
        try:
            parse_date(self.deadline)
        except ValueError:
            return f"{self.form}: enter a valid due date."
        if self.covers not in {k for k, _ in COVERS_CHOICES}:
            return f"{self.form}: pick which month it covers."
        return ""

    def lag(self) -> int:
        """Months from the due date's month back to the month it files for."""
        if self.covers == "same_month":
            return 0
        if self.covers == "fy_end":
            fy_end = (FY_START_MONTH - 2) % 12 + 1          # 3 = March
            return (parse_date(self.deadline).month - fy_end) % 12 or 12
        return 1

    def engine(self) -> LTTEngine:
        return LTTEngine(parse_date(self.deadline), parse_gap(self.every), self.lag(), FY_START_MONTH)

    @property
    def whole_year(self) -> bool:
        return parse_gap(self.every).n >= 12


def canon(form: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (form or "").upper())


def form_matches(rule_form: str, form: str) -> bool:
    """GSTR-3B == GSTR3B == gstr 3b. A rule named without a number ("ITR") also takes ITR1..ITR7."""
    a, b = canon(rule_form), canon(form)
    if not a or not b:
        return False
    return a == b or (not a[-1].isdigit() and re.fullmatch(re.escape(a) + r"\d{1,2}", b) is not None)


# Suggested starting points for the standard Indian due dates. They are examples to correct in
# the dialog, not rules the app enforces; the dates only need to fall on the right day/month.
DEFAULT_RULES = [
    FormRule("GSTR-1", "Monthly", "2026-10-11", "month_before"),
    FormRule("GSTR-3B", "Monthly", "2026-10-20", "month_before"),
    FormRule("CMP-08", "Quarterly", "2026-10-18", "month_before"),
    FormRule("GSTR-4", "Yearly", "2027-04-30", "month_before"),
    FormRule("GSTR-9", "Yearly", "2026-12-31", "fy_end"),
    FormRule("ITR", "Yearly", "2026-07-31", "fy_end"),
]


def rules_path(app_dir) -> Path:
    return Path(app_dir) / RULES_FILE


def load_rules(app_dir) -> list[FormRule]:
    """The saved rules; the suggested defaults when nothing has been saved yet. Never raises."""
    try:
        data = json.loads(rules_path(app_dir).read_text(encoding="utf-8"))
        out = []
        for r in data.get("forms", []):
            out.append(FormRule(
                form=str(r.get("form", "")).strip(), every=str(r.get("every", "Monthly")),
                deadline=str(r.get("deadline", "")), covers=str(r.get("covers", "month_before")),
                include=[str(p).upper() for p in r.get("include", [])],
                exclude=[str(p).upper() for p in r.get("exclude", [])]))
        return out
    except FileNotFoundError:
        return [FormRule(**asdict(r)) for r in DEFAULT_RULES]
    except (OSError, ValueError, AttributeError):
        return []


def save_rules(app_dir, rules: list[FormRule]) -> None:
    path = rules_path(app_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "forms": [asdict(r) for r in rules]},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def validate(rules: list[FormRule]) -> str:
    """First problem found across the rule list ("" when all is well)."""
    seen = set()
    for r in rules:
        p = r.problem()
        if p:
            return p
        if canon(r.form) in seen:
            return f"{r.form} is listed twice."
        seen.add(canon(r.form))
    return ""
