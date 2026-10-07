"""core/ltt/feed.py - turns the tracker's captures into the rows of the LTT tracker sheet.

One row per (client, return form): PAN, Name, Return form, Current period, Submit status.
  * Which forms a client owes: every form with a capture for that PAN that matches a rule,
    plus the rule's `include` PANs, minus its `exclude` PANs.
  * Current period: the engine's current filing month for the rule, e.g. "September (FY 2026-27)".
  * Submit status: the best status among captures for that exact period, on the 4-level ladder.
    No capture for the period means "Not Submitted".
Pure functions over plain dicts; the app glue (export_feed) is the only part that touches the DB.
"""
from __future__ import annotations

import calendar
import csv
import os
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from .rules import FEED_FILE, FormRule, form_matches, load_rules

FIELDS = ["PAN", "Name", "Return form", "Current period", "Submit status", "Capture date & time"]

# Same vocabulary as core/sgt/sgt_toolbox.SUBMIT_LEVELS (a test keeps the two equal).
LEVELS = ("Not Submitted", "Draft", "Submitted (Not Verified)", "Submitted & Verified")
NOT_APPLICABLE = "Not applicable"          # a portal said so; outside the ladder

_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTH_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_RANGE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(?:-|–|\bto\b)\s*"
                       r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_QUARTERS = {1: (4, 5, 6), 2: (7, 8, 9), 3: (10, 11, 12), 4: (1, 2, 3)}


def ladder(status: str, arn: str = "") -> str:
    """Any portal/tracker wording -> one of LEVELS (or NOT_APPLICABLE). Same reading as the
    Tracker Dump window's status pill: an ARN on a not-yet-submitted row promotes it."""
    s = (status or "").strip().lower()
    for lvl in LEVELS:
        if s == lvl.lower():
            return lvl
    if "option expired" in s or re.search(r"\b(?:not applicable|na)\b", s):
        return NOT_APPLICABLE
    pending = any(k in s for k in ("not e-verified", "not verified", "pending", "verify later", "unverified"))
    if "not filed" in s or "unfiled" in s or "not submitted" in s or "to be filed" in s:
        lvl = LEVELS[0]
    elif any(k in s for k in ("draft", "visited", "in progress", "form selected", "personal info")):
        lvl = LEVELS[1]
    elif pending:
        lvl = LEVELS[2]
    elif any(k in s for k in ("verified", "filed", "portal confirmed", "processed", "accepted",
                              "submitted", "submit", "success", "evc")):
        lvl = LEVELS[3]
    else:
        lvl = LEVELS[0]
    if _rank(lvl) < 2 and (arn or "").strip() not in ("", "N/A"):
        lvl = LEVELS[2] if pending else LEVELS[3]
    return lvl


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def parse_period(label: str, itr: bool = False) -> tuple[set, int | None]:
    """A tracker period_label -> (months it names, FY start year or None).
    "June (FY 2026-27)" -> ({6}, 2026); "Jul-Sep (FY 2026-27)" -> ({7,8,9}, 2026);
    "AY 2026-27" -> (set(), 2025); "Q2 FY 2026-27" -> ({7,8,9}, 2026)."""
    text = str(label or "")
    fy = None
    m = re.search(r"\b(?:AY|A\.Y\.)\s*[:\-]?\s*(20\d{2})\s*[-_/]\s*\d{2}\b", text, re.I)
    if m:
        fy = int(m.group(1)) - 1
    else:
        m = re.search(r"\b(?:FY|F\.Y\.)?\s*[:\-]?\s*(20\d{2})\s*[-_/]\s*\d{2}\b", text, re.I)
        if m:
            fy = int(m.group(1)) - (1 if itr and not re.search(r"\bFY\b", text, re.I) else 0)
    months: set = set()
    r = _RANGE_RE.search(text)
    q = re.search(r"\bQ([1-4])\b", text, re.I)
    if r:
        a, b = _MONTHS[r.group(1).lower()], _MONTHS[r.group(2).lower()]
        months = {((a - 1 + i) % 12) + 1 for i in range(((b - a) % 12) + 1)}
    elif q:
        months = set(_QUARTERS[int(q.group(1))])
    else:
        mm = _MONTH_RE.search(text)
        if mm:
            months = {_MONTHS[mm.group(1).lower()]}
    return months, fy


def period_matches(label: str, month: date, fy_start_year: int, whole_year: bool, itr: bool = False) -> bool:
    months, fy = parse_period(label, itr)
    if fy is not None and fy != fy_start_year:
        return False
    if months:
        return month.month in months
    return whole_year and fy is not None


def local_time(iso: str) -> str:
    """A stored UTC/ISO capture time -> local "YYYY-MM-DD HH:MM" (same reading as the Tracker
    Dump window); "" when absent."""
    clean = str(iso or "").strip()
    if not clean:
        return ""
    try:
        dt = datetime.fromisoformat(clean[:-1] + "+00:00" if clean.endswith("Z") else clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return clean[:16].replace("T", " ")


def entry_form(entry: dict) -> str:
    """The return form of a filing_history entry: the text in brackets of "GST (GSTR-3B)"."""
    portal = str(entry.get("portal") or "")
    m = re.search(r"\(([^()]*)\)\s*$", portal)
    return (m.group(1) if m else portal).strip()


def _pan_of(c: dict) -> str:
    pan = str(c.get("pan") or "").strip().upper()
    if len(pan) == 15:
        pan = pan[2:12]
    if _PAN.match(pan):
        return pan
    gst = str(c.get("gstin") or "").strip().upper()
    return gst[2:12] if len(gst) >= 12 and _PAN.match(gst[2:12]) else ""


_LABEL = re.compile(r"^\s*(?:(?:trade|legal|business|company|party|client|firm)\s*name(?:\s+of\s+business)?|name)(?![A-Za-z0-9])"
                    r"\s*[-:–—.]*\s*", re.I)


def clean_name(text) -> str:
    """A captured name without the page's label: "Trade Name -" is a label with no value (""),
    "Trade Name - ABC Traders" is ABC Traders, and bare punctuation is nothing."""
    t = _LABEL.sub("", str(text or ""), count=1).strip(" -:–—.")
    return t if re.search(r"[A-Za-z0-9]", t) else ""


def name_of(c: dict) -> tuple[str, int]:
    """(name, rank) of a client container, best first (a lower rank is a better source):
    0 the name saved for the registered client, 1 the captured trade/company name, 2 the captured
    proprietor name, 3 whatever the container is displayed as. ("", 9) when it has none."""
    for rank, key in enumerate(("registered_name", "company_name", "proprietor_name")):
        name = clean_name(c.get(key))
        if name:
            return name, rank
    name = clean_name(re.sub(r"\s*\([^()]*\)\s*$", "", str(c.get("display_name") or "")))
    return (name, 3) if name and not name.lower().startswith("unregistered") else ("", 9)


def clients_from(containers: list) -> dict:
    """PAN -> {"name", "history"}; containers of the same PAN are merged."""
    out: dict = {}
    for c in containers:
        pan = _pan_of(c)
        if not pan:
            continue
        cur = out.setdefault(pan, {"name": "", "rank": 9, "history": []})
        name, rank = name_of(c)
        if name and rank < cur["rank"]:
            cur["name"], cur["rank"] = name, rank
        cur["history"].extend(c.get("filing_history") or [])
    return out


def forms_for(rule: FormRule, clients: dict) -> list:
    """PANs that owe this form: seen in captures, plus ticked by hand, minus unticked."""
    seen = {pan for pan, c in clients.items()
            if any(form_matches(rule.form, entry_form(h)) for h in c["history"])}
    return sorted((seen | {p for p in rule.include if p in clients}) - set(rule.exclude))


def build_rows(containers: list, rules: list, today: date | None = None) -> list:
    today = today or date.today()
    clients = clients_from(containers)
    rows = []
    for rule in rules:
        if rule.problem():
            continue
        cur = rule.engine().locate(today)["current"]
        itr = rule.form.upper().startswith("ITR")
        fy_year = int(cur.fy[:4])
        for pan in forms_for(rule, clients):
            best = LEVELS[0]
            na = False
            when = ""              # capture time of the entry that decided the status
            for h in clients[pan]["history"]:
                if not form_matches(rule.form, entry_form(h)):
                    continue
                if not period_matches(h.get("period_label"), cur.month, fy_year, rule.whole_year, itr):
                    continue
                lvl = ladder(h.get("status"), h.get("arn"))
                at = str(h.get("created_at") or "")
                if lvl == NOT_APPLICABLE:
                    na = True
                    when = max(when, at)
                elif _rank(lvl) > _rank(best) or (lvl == best and at > when):
                    best = lvl
                    when = at
            rows.append({"PAN": pan, "Name": clients[pan]["name"], "Return form": rule.form,
                         "Current period": cur.filing,
                         "Submit status": NOT_APPLICABLE if na and best == LEVELS[0] else best,
                         "Capture date & time": local_time(when)})
    rows.sort(key=lambda r: (r["Name"].lower(), r["PAN"], r["Return form"]))
    return rows


def write_csv(path, rows: list) -> None:
    """Atomic, UTF-8 with BOM so Excel and Power Query read it the same way."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def export_feed(db, app_dir=None, today: date | None = None) -> tuple[Path, int]:
    """Reads the tracker through the app's database object and rewrites ltt_feed.csv.
    Returns (path, number of rows)."""
    app_dir = app_dir or db.app_dir
    containers = db.get_srpf_containers(limit=1_000_000, slim=True)
    rows = build_rows(containers, load_rules(app_dir), today)
    path = Path(app_dir) / FEED_FILE
    write_csv(path, rows)
    return path, len(rows)


_timer = None
_timer_lock = threading.Lock()


def schedule_export(db, delay: float = 10.0) -> None:
    """Rewrites ltt_feed.csv `delay` seconds after the last capture (bursts collapse into one
    write). Does nothing until the office has switched the sheet on once - the CSV is created by
    Tools > Update LTT sheet - so no plain-text client list appears unasked. Never raises."""
    global _timer
    try:
        if not (Path(db.app_dir) / FEED_FILE).exists():
            return

        def run():
            try:
                export_feed(db)
            except Exception as e:                      # a background refresh must never disturb the app
                print(f"[ltt] feed refresh skipped: {e}")

        with _timer_lock:
            if _timer is not None:
                _timer.cancel()
            _timer = threading.Timer(delay, run)
            _timer.daemon = True
            _timer.start()
    except Exception:
        pass
