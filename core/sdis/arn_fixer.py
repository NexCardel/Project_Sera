"""
core/sdis/arn_fixer.py - find, report and fix failed GST ARN rows (CLI: tools/mr_fixer.py; unattended: arn_autofix.py)
=============================================================
Plan: docs/gst_arn_recovery/mr-fixer-plan.md. SGT can write a GST filing as a row holding only an
ARN (no client, form or period). This tool finds those rows, looks their client, form and period up
in the SDIS corpus, and fixes them the way SGT does when it learns the client.

    python tools/mr_fixer.py scan   [--from D] [--to D] [--corpus DIR]...
    python tools/mr_fixer.py report [--from D] [--to D] [--corpus DIR]... [--expected expected.json]
    python tools/mr_fixer.py apply  <report.csv> [--include-client-only]
    python tools/mr_fixer.py undo   <report.csv>

scan and report change nothing. apply reads the reviewed report (including any `pick` you typed) and
works nothing out again. undo puts the rows back with their original ids. apply and undo refuse
while Sera is open. The report holds client names and GSTINs: it stays on this PC, and the console
and logs print counts, ids, ARNs and times only - never page text, names or GSTINs.
"""

import base64
import csv
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.sgt.sgt_resolver import resolve_page                                       # noqa: E402
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore, compose_values, load_registry   # noqa: E402
from core.sgt_i.uia_nodes import lines_from_nodes                                    # noqa: E402

LOOKBACK = timedelta(minutes=30)
SESSION_SLACK = timedelta(minutes=2)
PORTAL = "GST Portal"
CORPUS_RE = re.compile(r"^sdis_(\d{4}-\d{2}-\d{2})\.jsonl$")
SESSION_KEY_RE = re.compile(r"SGT[0-9A-Z]+:.*ARN", re.I)
FIX_COLUMNS = [
    "row_id", "arn", "row_time", "result", "reason", "evidence", "gstin", "client_name", "master_client",
    "form", "period", "filing_pref", "merges_with_row", "old_dataset_key", "new_dataset_key",
    "candidates", "pick", "capture_method",
]
CAND_SEP = " ;; "
SURE, CLIENT_ONLY, NEEDS_PICK, NOT_FOUND = "sure", "client only", "needs pick", "not found"


class FixerError(Exception):
    """A gate or a precondition failed. The message says why; nothing else was changed."""


# ── The ARN shape: the shipped gst_submit_success spec, not a new regex ─────────────
def _find_spec(node: Any, name: str) -> Optional[Dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("name") == name:
            return node
        for v in node.values():
            hit = _find_spec(v, name)
            if hit:
                return hit
    elif isinstance(node, list):
        for v in node:
            hit = _find_spec(v, name)
            if hit:
                return hit
    return None


def arn_regex() -> "re.Pattern[str]":
    spec = _find_spec(json.loads(Path(BUILTIN_FIELDS_PATH).read_text(encoding="utf-8")), "gst_submit_success")
    pattern = next(f["pattern"] for f in spec["fields"] if f["field"] == "arn")
    return re.compile(pattern)


# ── Times ────────────────────────────────────────────────────────────────────────
def to_local(stamp: str, naive_is_utc: bool = False) -> datetime:
    """A local, naive datetime. Corpus times are local already; tracker times are UTC."""
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is None and naive_is_utc:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


# ── 1. Which rows (scan) ─────────────────────────────────────────────────────────
@dataclass
class Target:
    id: int
    arn: str
    when: datetime                  # local time of the filing (created_at read as UTC)
    created_at: str
    session: str                    # the SGT session id, no prefix
    dataset_key: str
    capture_method: str
    portal: str
    status: str
    captured_by: str
    payload: Dict[str, Any]

    @property
    def day(self) -> date:
        return self.when.date()


_ROW_COLUMNS = ("id, client_id, portal, period_label, arn_number, capture_method, status, "
                "raw_payload_json, captured_by, created_at, dataset_key")


def find_targets(db: Any, arn_re: "re.Pattern[str]", date_from: Optional[date] = None,
                 date_to: Optional[date] = None, older_than: Optional[timedelta] = None) -> List[Target]:
    """older_than: leave out rows newer than this (a live SGT session may still be about to fix its own row)."""
    now = datetime.now()
    with db._connect_raw() as conn:
        rows = conn.execute(f"SELECT {_ROW_COLUMNS} FROM tracker_dump WHERE capture_method LIKE 'SGT%' "
                            "AND client_id IS NULL AND arn_number IS NOT NULL AND arn_number != 'N/A' "
                            "ORDER BY id").fetchall()
    out: List[Target] = []
    for rid, _cid, portal, period, arn, method, status, raw, by, created, key in rows:
        if "gst" not in str(portal or "").lower() or not arn_re.fullmatch(str(arn)):
            continue
        try:
            p = json.loads(raw) if raw else {}
        except ValueError:
            p = {}
        if not isinstance(p, dict) or p.get("gstin") or p.get("pan"):
            continue
        if str(period or "").strip() and not SESSION_KEY_RE.search(str(key or "")):
            continue
        try:
            when = to_local(str(created), naive_is_utc=True)
        except ValueError:
            continue
        if (date_from and when.date() < date_from) or (date_to and when.date() > date_to):
            continue
        if older_than is not None and now - when < older_than:
            continue
        sid = str(p.get("session_id") or (p.get("raw_payload") or {}).get("session_id") or "")
        out.append(Target(id=rid, arn=str(arn), when=when, created_at=str(created),
                          session=sid[4:] if sid.startswith("SGT-") else sid, dataset_key=str(key or ""),
                          capture_method=str(method), portal=str(portal), status=str(status or ""),
                          captured_by=str(by or ""), payload=p))
    return out


# ── 2. Where the pages are ───────────────────────────────────────────────────────
@dataclass
class Rec:
    ts: datetime
    browser: str
    session: str
    gst: bool
    device: str
    path: Path
    offset: int


@dataclass
class Page:
    gstin: Optional[str]
    name: str
    sets: List[Dict[str, str]]       # each dataset's values, then the page's own, composed as SGT composes them

    @property
    def arns(self) -> set:
        return {s["arn"] for s in self.sets if s.get("arn")}


class Corpus:
    """Corpus files read-only. A day's file is indexed once (one JSON parse per line, times and
    sessions only); a page is resolved only when something needs it."""

    def __init__(self, roots: Sequence[Path], registry: Any, arn_re: "re.Pattern[str]",
                 pause: Optional[Callable[[], None]] = None):
        self.registry = registry
        self.arn_re = arn_re
        # Called between lines / pages: the unattended pass uses it to give the rest of the app the
        # interpreter (the app's own thread stalled for seconds while it ran); the CLI passes none.
        self.breathe: Callable[[], None] = pause or (lambda: None)
        self.files: Dict[date, List[Tuple[str, Path]]] = {}
        seen = set()
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("sdis_*.jsonl")):
                m = CORPUS_RE.match(p.name)
                if not m or p.resolve() in seen:
                    continue
                seen.add(p.resolve())
                rel = p.relative_to(root).parts
                device = rel[0] if len(rel) > 1 else "local"
                self.files.setdefault(date.fromisoformat(m.group(1)), []).append((device, p))
        self._index: Dict[date, List[Rec]] = {}
        self._arn_map: Dict[str, List[Rec]] = {}
        self._pages: Dict[Tuple[Path, int], Page] = {}

    def has_day(self, day: date) -> bool:
        return day in self.files

    def records(self, day: date) -> List[Rec]:
        if day not in self._index:
            recs: List[Rec] = []
            for device, path in self.files.get(day, ()):
                with open(path, "rb") as f:                     # read-only
                    while True:
                        self.breathe()
                        off = f.tell()
                        raw = f.readline()
                        if not raw:
                            break
                        try:
                            rec = json.loads(raw)
                            ts = to_local(rec["ts"])
                        except (ValueError, KeyError, TypeError):
                            continue
                        if not isinstance(rec, dict) or not isinstance(rec.get("docs"), list):
                            continue
                        r = Rec(ts=ts, browser=rec.get("browser") or "", session=rec.get("session") or "",
                                gst="gst" in str(rec.get("portal") or "").lower(), device=device, path=path, offset=off)
                        recs.append(r)
                        for arn in set(self.arn_re.findall(raw.decode("utf-8", "replace"))):
                            self._arn_map.setdefault(arn, []).append(r)
            recs.sort(key=lambda r: (r.ts, r.device, r.offset))
            self._index[day] = recs
        return self._index[day]

    def arn_pages(self, arn: str, days: Sequence[date]) -> List[Rec]:
        for d in days:
            self.records(d)
        return sorted(self._arn_map.get(arn, ()), key=lambda r: (r.ts, r.device, r.offset))

    def between(self, device: str, lo: datetime, hi: datetime, days: Sequence[date]) -> List[Rec]:
        """GST pages of one device with lo <= time <= hi, by time."""
        return [r for d in sorted(set(days)) for r in self.records(d) if r.device == device and r.gst and lo <= r.ts <= hi]

    def session_pages(self, session: str, days: Sequence[date]) -> List[Rec]:
        return [r for d in days for r in self.records(d) if r.gst and r.session == session]

    def read(self, rec: Rec) -> Page:
        key = (rec.path, rec.offset)
        if key not in self._pages:
            self.breathe()
            with open(rec.path, "rb") as f:                     # read-only
                f.seek(rec.offset)
                doc = json.loads(f.readline())
            lines = lines_from_nodes(doc["docs"])
            res = resolve_page(self.registry, lines, doc.get("portal") or PORTAL, doc.get("url") or "",
                               rec.ts.date(), title=doc.get("title") or "")
            g, n = res.profile.get("gstin"), res.profile.get("name")
            raw = {k: h.value for k, h in res.current.items()}
            current = {**raw, **compose_values(raw, self.registry.current_rules.compose)}
            sets = [dict(d.values()) for d in res.datasets] + [current]
            self._pages[key] = Page(gstin=g.value if g else None, name=n.value if n else "", sets=sets)
        return self._pages[key]


def days_for(t: Target) -> List[date]:
    return [t.day - timedelta(days=1), t.day]


def corpus_fingerprint(corpus: Corpus, days: Sequence[date]) -> str:
    """Which corpus files hold `days`, and how big / how recent they are - without opening them. The same
    string later means the corpus has nothing new for a row that was judged from those days."""
    import hashlib
    parts = []
    for d in sorted(set(days)):
        for device, path in sorted(corpus.files.get(d, ()), key=lambda x: (x[0], str(x[1]))):
            try:
                st = path.stat()
                parts.append(f"{d}|{device}|{path.name}|{st.st_size}|{int(st.st_mtime)}")
            except OSError:
                parts.append(f"{d}|{device}|{path.name}|gone")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


# ── 3. Finding the answer for one row (report) ───────────────────────────────────
@dataclass
class Finding:
    result: str = NOT_FOUND
    reason: str = ""
    evidence: str = ""
    gstin: str = ""
    name: str = ""
    form: str = ""
    period: str = ""
    pref: str = ""
    candidates: List[Dict[str, str]] = field(default_factory=list)


def _lenient(anchor_browser: str, rec: Rec) -> bool:
    """A page with no browser name could be any browser, so it counts for the 'other client' check."""
    return not anchor_browser or not rec.browser or rec.browser == anchor_browser


def _form_period(corpus: Corpus, recs: Sequence[Rec], arn: str = "") -> Tuple[str, str, str]:
    """Form, period and filing preference, latest page first, the way the injector's survey composes them.
    A dataset that carries this very ARN wins."""
    for r in reversed(recs):
        sets = corpus.read(r).sets
        if arn:
            for s in sets:
                if s.get("arn") == arn and s.get("form") and s.get("period"):
                    return s["form"], s["period"], s.get("filing_type", "")
        for s in reversed(sets):
            if s.get("form") and s.get("period"):
                return s["form"], s["period"], s.get("filing_type", "")
    return "", "", ""


def _candidate(corpus: Corpus, gstin: str, recs: Sequence[Rec]) -> Dict[str, str]:
    """One client seen in these pages: name, last time seen, and the form + period of its header pages."""
    mine = [r for r in recs if corpus.read(r).gstin == gstin]
    name = next((corpus.read(r).name for r in reversed(mine) if corpus.read(r).name), "")
    form, period, pref = _form_period(corpus, mine)
    return {"gstin": gstin, "name": name, "seen": mine[-1].ts.strftime("%H:%M:%S"), "form": form,
            "period": period, "pref": pref}


def _all_candidates(corpus: Corpus, recs: Sequence[Rec]) -> List[Dict[str, str]]:
    last: Dict[str, datetime] = {}
    for r in recs:
        g = corpus.read(r).gstin
        if g:
            last[g] = r.ts
    return [_candidate(corpus, g, recs) for g, _ in sorted(last.items(), key=lambda kv: kv[1], reverse=True)]


def _client_from_window(corpus: Corpus, t: Target, anchor: Rec, end: datetime, kind: str,
                        looked: Callable[[Rec], str]) -> Finding:
    """Steps 2-4 for an anchor that gives a time and a browser: look back at most 30 minutes."""
    days = [anchor.ts.date() - timedelta(days=1), anchor.ts.date()]
    window = corpus.between(anchor.device, anchor.ts - LOOKBACK, max(end, anchor.ts), days)
    window = [r for r in window if _lenient(anchor.browser, r) and r.ts <= max(end, anchor.ts)]
    headers = [(r, corpus.read(r).gstin) for r in window if corpus.read(r).gstin]
    strict = [(r, g) for r, g in headers if r.browser == anchor.browser and r.ts <= anchor.ts]
    if not strict:
        return Finding(NOT_FOUND, "no header within 30 min before the anchor", evidence=kind)
    near_rec, client = strict[-1]
    ev = f"{kind}, {anchor.device}, {anchor.ts.isoformat(timespec='seconds')}, looked back {looked(near_rec)}"
    others = [(r, g) for r, g in headers if g != client and r.ts > near_rec.ts]
    candidates = _all_candidates(corpus, window)
    if others:
        return Finding(NEEDS_PICK, "another client's header sits in the window (two clients interleaved)",
                       evidence=ev, candidates=candidates)
    # the client's contiguous run of headers, going back until another client's header
    run_start = near_rec.ts
    for r, g in reversed(headers):
        if r.ts > near_rec.ts:
            continue
        if g != client:
            break
        run_start = r.ts
    pages = [r for r in window if run_start <= r.ts <= max(end, anchor.ts)]
    form, period, pref = _form_period(corpus, pages, t.arn if kind == "ARN page" else "")
    name = next((corpus.read(r).name for r, g in reversed(headers) if g == client and corpus.read(r).name), "")
    f = Finding(SURE, evidence=ev, gstin=client, name=name, form=form, period=period, pref=pref,
                candidates=candidates)
    if not (form and period):
        f.result, f.reason = CLIENT_ONLY, "client found, no form + period on its pages"
    return f


def _minutes(a: datetime, b: datetime) -> str:
    return f"{abs((a - b).total_seconds()) / 60:.1f} min"


def find_answer(corpus: Corpus, t: Target) -> Finding:
    days = days_for(t)
    if not any(corpus.has_day(d) for d in days):
        return Finding(NOT_FOUND, "no pages for that day")
    # Step 1a: the ARN page
    for rec in corpus.arn_pages(t.arn, days):
        if t.arn in corpus.read(rec).arns:
            return _client_from_window(corpus, t, rec, rec.ts, "ARN page", lambda nr, a=rec: _minutes(a.ts, nr.ts))
    # Step 1b: the row's session
    if not t.session:
        return Finding(NOT_FOUND, "no ARN page and the row has no session id")
    sess = [r for r in corpus.session_pages(t.session, days) if r.ts <= t.when + SESSION_SLACK]
    if not sess:
        return Finding(NOT_FOUND, "no ARN page and no pages of the row's session")
    anchor = max(sess, key=lambda r: (r.ts, r.offset))
    if t.when - anchor.ts > LOOKBACK:
        return Finding(NOT_FOUND, "the session's last page is more than 30 min before the row")
    ev = f"session, {anchor.device}, {anchor.ts.isoformat(timespec='seconds')}"
    seen = {}
    for r in sess:
        g = corpus.read(r).gstin
        if g:
            seen[g] = r
    if len(seen) > 1:
        return Finding(NEEDS_PICK, "the session shows more than one client", evidence=ev,
                       candidates=_all_candidates(corpus, sess))
    if not seen:
        f = _client_from_window(corpus, t, anchor, t.when, "session", lambda nr, a=anchor: _minutes(a.ts, nr.ts))
        if f.result in (SURE, CLIENT_ONLY):          # the client came by time alone: the user decides
            f.result, f.reason = NEEDS_PICK, "the session shows no header; client taken by time only"
            f.candidates = f.candidates or [{"gstin": f.gstin, "name": f.name, "seen": "", "form": f.form,
                                             "period": f.period, "pref": f.pref}]
            f.gstin = f.name = f.form = f.period = f.pref = ""
        return f
    client = next(iter(seen))
    last_hdr = max(r.ts for r in sess if corpus.read(r).gstin == client)
    between = [r for r in corpus.between(anchor.device, last_hdr, t.when, [last_hdr.date(), t.when.date()])
               if _lenient(anchor.browser, r) and r.ts > last_hdr]
    if any(corpus.read(r).gstin not in (None, client) for r in between):
        return Finding(NEEDS_PICK, "another client's header follows the session's last header before the row",
                       evidence=ev, candidates=_all_candidates(corpus, sess + between))
    form, period, pref = _form_period(corpus, sess)
    name = next((corpus.read(r).name for r in reversed(sess) if corpus.read(r).gstin == client and corpus.read(r).name), "")
    f = Finding(SURE, evidence=f"{ev}, header {_minutes(t.when, last_hdr)} before the row", gstin=client, name=name,
                form=form, period=period, pref=pref, candidates=[_candidate(corpus, client, sess)])
    if not (form and period):
        f.result, f.reason = CLIENT_ONLY, "client found, no form + period on its pages"
    return f


# ── The new row: built by SGT's own payload code ─────────────────────────────────
class PayloadBuilder:
    """SgtShadow._tracker_payload on a confirmed stub session: the payload SGT writes once it learns
    the client (main.py's superseded-key path)."""

    def __init__(self) -> None:
        from core.sgt.sgt_shadow import MODE_LIVE, SgtShadow
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                             read_uia=lambda h: {"lines": []}, log_dir=tmp / "log", state_path=tmp / "state.json",
                             mode=MODE_LIVE, today=date.today, echo=lambda m: None)

    def build(self, t: Target, gstin: str, name: str, form: str, period: str, pref: str,
              evidence: str = "", report: str = "") -> Dict[str, Any]:
        from core.sgt.sgt_shadow import _Session, _Slot
        old = t.payload
        s = _Session(session_id=t.session or "recovered", portal=old.get("portal") or PORTAL)
        s.profile = {"gstin": {"value": gstin}, "pan": {"value": gstin[2:12]}}
        if name:
            s.profile["name"] = {"value": name}
        s.confirmed, s.confirm_note = True, "recovered from the SDIS corpus by mr_fixer"
        values = {"arn": t.arn, "status": t.status}
        if form and period:
            values.update(form=form, period=period)
        if pref:
            values["filing_type"] = pref
        src = (old.get("raw_payload") or {}).get("source") or {}
        slot = _Slot(values=values, record=src.get("record") or "gst_submit_success",
                     confidence=int(src.get("confidence") or 90), first_page=old.get("page_url") or "",
                     sent_key=t.dataset_key)
        payload = self.sgt._tracker_payload(s, slot)
        if old.get("filing_date"):
            payload["filing_date"] = old["filing_date"]
        payload["raw_payload"]["recovered_by"] = {"tool": "mr_fixer", "report": report, "evidence": evidence}
        return payload


# ── Vault + merge facts for the report ───────────────────────────────────────────
def vault_name(db: Any, gstin: str) -> str:
    """The name in the vault when the GSTIN (or its PAN) is a known client, else 'new'."""
    try:
        with db._connect() as conn:
            for cand in (gstin.upper(), gstin[2:12].upper()):
                row = conn.execute("SELECT cv.client_id FROM client_values cv JOIN clients c ON c.id = cv.client_id "
                                   "WHERE c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ? LIMIT 1", (cand,)).fetchone()
                if row:
                    nm = conn.execute("SELECT cv.value FROM client_values cv JOIN mcl_columns mc ON mc.id = cv.column_id "
                                      "WHERE cv.client_id = ? AND (mc.is_identity = 1 OR LOWER(mc.label) LIKE '%name%') "
                                      "ORDER BY mc.is_identity DESC LIMIT 1", (row[0],)).fetchone()
                    return (nm[0] if nm and nm[0] else f"client #{row[0]}")
    except Exception:
        return "?"
    return "new"


def merge_info(db: Any, new_key: str, row_id: int) -> str:
    with db._connect_raw() as conn:
        row = conn.execute("SELECT id, status FROM tracker_dump WHERE dataset_key = ? AND id != ? ORDER BY id LIMIT 1",
                           (new_key, row_id)).fetchone()
    return f"{row[0]} ({row[1]})" if row else ""


def _cand_text(c: Dict[str, str]) -> str:
    return " | ".join([c["gstin"], c["name"], c["seen"], c["form"], c["period"], c["pref"]])


def parse_candidates(text: str) -> List[Dict[str, str]]:
    out = []
    for part in [p for p in (text or "").split(CAND_SEP.strip()) if p.strip()]:
        bits = [b.strip() for b in part.split("|")] + [""] * 6
        out.append({"gstin": bits[0], "name": bits[1], "seen": bits[2], "form": bits[3], "period": bits[4], "pref": bits[5]})
    return out


# ── The report ───────────────────────────────────────────────────────────────────
def make_report(db: Any, corpus: Corpus, targets: Sequence[Target],
                builder: Optional[PayloadBuilder] = None) -> List[Dict[str, str]]:
    builder = builder or PayloadBuilder()
    rows = []
    for t in targets:
        corpus.breathe()
        f = find_answer(corpus, t)
        row = {c: "" for c in FIX_COLUMNS}
        row.update(row_id=str(t.id), arn=t.arn, row_time=t.when.isoformat(timespec="seconds"), result=f.result,
                   reason=f.reason, evidence=f.evidence, old_dataset_key=t.dataset_key, capture_method=t.capture_method,
                   candidates=CAND_SEP.join(_cand_text(c) for c in f.candidates))
        if f.result in (SURE, CLIENT_ONLY):
            payload = builder.build(t, f.gstin, f.name, f.form, f.period, f.pref, evidence=f.evidence)
            row.update(gstin=f.gstin, client_name=f.name, master_client=vault_name(db, f.gstin), form=f.form,
                       period=f.period, filing_pref=f.pref, new_dataset_key=payload["dataset_key"],
                       merges_with_row=merge_info(db, payload["dataset_key"], t.id))
        rows.append(row)
    return rows


def write_report(rows: Sequence[Dict[str, str]], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIX_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return out


def read_report(path: Path) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def score(rows: Sequence[Dict[str, str]], expected: Dict[str, Any], log: Callable[[str], None] = print) -> int:
    """Scores the report against the injector's truth. A wrong 'sure' is a failure (returns 1)."""
    by_id = {r["row_id"]: r for r in rows}
    wrong = 0
    for e in expected.get("rows", []):
        r = by_id.get(str(e.get("row_id")))
        case = e.get("case", "?")
        if r is None:
            log(f"case {case}: not in the report")
            continue
        if r["result"] in (SURE, CLIENT_ONLY):
            ok = r["gstin"] == e.get("true_gstin") and (r["result"] == CLIENT_ONLY or
                                                       (r["form"] == e.get("true_form") and r["period"] == e.get("true_period")))
            if not ok:
                wrong += 1
            log(f"case {case}: {r['result']} - {'right' if ok else 'WRONG'}")
        elif r["result"] == NEEDS_PICK:
            has = any(c["gstin"] == e.get("true_gstin") for c in parse_candidates(r["candidates"]))
            log(f"case {case}: needs pick - not fixed ({'the right client is among the candidates' if has else 'right client NOT among the candidates'})")
        else:
            log(f"case {case}: not found - not fixed")
    return 1 if wrong else 0


# ── 5. Fixing (apply) and undoing ────────────────────────────────────────────────
def _jsonable(v: Any) -> Any:
    return {"__b64__": base64.b64encode(v).decode("ascii")} if isinstance(v, (bytes, bytearray)) else v


def _unjson(v: Any) -> Any:
    return base64.b64decode(v["__b64__"]) if isinstance(v, dict) and "__b64__" in v else v


def _rows_where(conn: Any, where: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
    cur = conn.execute(f"SELECT * FROM tracker_dump WHERE {where}", list(params))
    cols = [d[0] for d in cur.description]
    return [{c: _jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def fixer_dir(app_dir: Path) -> Path:
    return app_dir / "mr_fixer"


def report_stamp(report: Path) -> str:
    m = re.match(r"^report_(.+)\.csv$", report.name)
    return m.group(1) if m else report.stem


def undo_path(app_dir: Path, report: Path) -> Path:
    return fixer_dir(app_dir) / f"undo_{report_stamp(report)}.jsonl"


def _append(path: Path, event: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _restore(db: Any, rows: Sequence[Dict[str, Any]]) -> int:
    """Puts saved rows back with their original ids and columns; rows that still exist are left alone."""
    n = 0
    with db._connect_raw() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
        for row in rows:
            if conn.execute("SELECT 1 FROM tracker_dump WHERE id = ?", (row["id"],)).fetchone():
                continue
            keep = [c for c in row if c in cols]
            conn.execute(f"INSERT INTO tracker_dump ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                         [_unjson(row[c]) for c in keep])
            n += 1
    return n


def _pick_values(r: Dict[str, str], include_client_only: bool) -> Tuple[Optional[Dict[str, str]], str]:
    """What apply may do with one report row: the values to write, or None and why not."""
    result = r["result"]
    if result == SURE:
        return {"gstin": r["gstin"], "name": r["client_name"], "form": r["form"], "period": r["period"],
                "pref": r["filing_pref"]}, ""
    if result == CLIENT_ONLY:
        if not include_client_only:
            return None, "client only (use --include-client-only)"
        return {"gstin": r["gstin"], "name": r["client_name"], "form": "", "period": "", "pref": r["filing_pref"]}, ""
    if result == NEEDS_PICK:
        pick = (r.get("pick") or "").strip().upper()
        if not pick:
            return None, "needs pick, none given"
        cand = next((c for c in parse_candidates(r["candidates"]) if c["gstin"].upper() == pick), None)
        if cand is None:
            return None, "pick is not among the candidates"
        return {"gstin": cand["gstin"], "name": cand["name"], "form": cand["form"], "period": cand["period"],
                "pref": cand["pref"]}, ""
    return None, f"result is {result or 'empty'}"


def apply_report(app_dir: Path, db_factory: Callable[[], Any], report: Path, include_client_only: bool = False,
                 running: Optional[Callable[[], bool]] = None, confirm: Callable[[], bool] = lambda: False,
                 log: Callable[[str], None] = print, builder: Optional[PayloadBuilder] = None) -> Dict[str, int]:
    if running is not None and running():
        raise FixerError("Sera is open. Close the app first: it holds the database and its tracker.")
    rows = read_report(report)
    db = db_factory()
    plan = []
    for r in rows:
        vals, why = _pick_values(r, include_client_only)
        plan.append((r, vals, why))
    todo = [p for p in plan if p[1] is not None]
    counts = {"fixed": 0, "skipped": len(plan) - len(todo), "changed": 0, "failed": 0}
    if not todo:
        log(f"nothing to fix ({counts['skipped']} row(s) skipped)")
        return counts
    log(f"{len(todo)} row(s) will be fixed. Sync is NOT paused: on a PC with sync live these fixes go to every PC.")
    if not confirm():
        raise FixerError("not confirmed; nothing changed")
    builder = builder or PayloadBuilder()
    undo = undo_path(app_dir, report)
    for r, vals, _why in todo:
        rid = int(r["row_id"])
        with db._connect_raw() as conn:
            cur = conn.execute("SELECT arn_number, dataset_key, capture_method, created_at, raw_payload_json, captured_by, "
                               "status FROM tracker_dump WHERE id = ?", (rid,)).fetchone()
        if cur is None or (cur[0], cur[1], cur[2]) != (r["arn"], r["old_dataset_key"], r["capture_method"]):
            counts["changed"] += 1
            log(f"row {rid}: changed since the report; skipped")
            continue
        old_created, old_raw, old_by, old_status = cur[3], cur[4], cur[5], cur[6]
        t = Target(id=rid, arn=r["arn"], when=to_local(str(old_created), naive_is_utc=True), created_at=str(old_created),
                   session="", dataset_key=r["old_dataset_key"], capture_method=r["capture_method"], portal="",
                   status=str(old_status or ""), captured_by=str(old_by or ""), payload=json.loads(old_raw or "{}"))
        sid = str(t.payload.get("session_id") or "")
        t.session = sid[4:] if sid.startswith("SGT-") else sid
        payload = builder.build(t, vals["gstin"], vals["name"], vals["form"], vals["period"], vals["pref"],
                                evidence=r["evidence"], report=report.name)
        new_key = payload["dataset_key"]
        with db._connect_raw() as conn:
            saved = _rows_where(conn, "id = ? OR dataset_key IN (?, ?) OR arn_number = ?", (rid, r["old_dataset_key"], new_key, r["arn"]))
        _append(undo, {"event": "saved", "old_id": rid, "arn": r["arn"], "rows": saved})
        try:
            db.delete_sgt_rows_by_dataset_key(r["old_dataset_key"])
            period = payload.get("period_label", "")
            result = db.insert_tracker_dump(
                client_id=None, service_id=None, portal=f"{payload['portal']} ({payload['filing_type']})",
                period_label=period, arn_number=payload["arn"], capture_method=payload["capture_method"],
                status=payload["status"], raw_payload_json=json.dumps(payload), captured_by=t.captured_by,
                pan=payload["pan"], session_id=payload["session_id"], filing_type=payload["filing_type"],
                dataset_key=new_key)
            if not result or result.get("duplicate") or not result.get("id"):
                raise FixerError("the insert did not give a new row")
            with db._connect_raw() as conn:
                upd = conn.execute("UPDATE tracker_dump SET created_at = ? WHERE id = ? AND arn_number = ?",
                                   (t.created_at, result["id"], r["arn"]))
                if upd.rowcount != 1:
                    raise FixerError("could not set the row time")
        except Exception as e:
            _restore(db, saved)                       # put back whatever the failed step took away
            counts["failed"] += 1
            log(f"row {rid}: failed ({type(e).__name__}); restored")
            continue
        _append(undo, {"event": "applied", "old_id": rid, "new_id": int(result["id"]), "arn": r["arn"], "result": r["result"]})
        counts["fixed"] += 1
        log(f"row {rid} -> {result['id']}, ARN {r['arn']}, {r['result']}")
    return counts


def undo_report(app_dir: Path, db_factory: Callable[[], Any], report: Path,
                running: Optional[Callable[[], bool]] = None, log: Callable[[str], None] = print) -> int:
    if running is not None and running():
        raise FixerError("Sera is open. Close the app first: it holds the database and its tracker.")
    path = undo_path(app_dir, report)
    if not path.exists():
        raise FixerError("no undo file for this report: nothing was applied")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    applied = {e["old_id"]: e for e in events if e["event"] == "applied"}
    db = db_factory()
    restored = 0
    for e in events:
        if e["event"] != "saved":
            continue
        done = applied.get(e["old_id"])
        if done:
            with db._connect_raw() as conn:
                found = conn.execute("SELECT arn_number FROM tracker_dump WHERE id = ?", (done["new_id"],)).fetchone()
                if found and found[0] == e["arn"]:
                    conn.execute("DELETE FROM tracker_dump WHERE id = ?", (done["new_id"],))
                elif found:
                    log(f"row {done['new_id']}: holds another ARN now; left alone")
        n = _restore(db, e["rows"])
        restored += n
        log(f"row {e['old_id']}: ARN {e['arn']}, {n} row(s) restored")
    return restored


# ── Commands ─────────────────────────────────────────────────────────────────────
def default_roots(app_dir: Path) -> List[Path]:
    sdis = Path(os.environ["SDIS_DATA_DIR"]) if os.environ.get("SDIS_DATA_DIR") else app_dir / "sdis"
    roots = [sdis / "corpus", app_dir / "sdis_capture"]
    if (sdis / "recover_test" / "expected.json").exists():       # the injector's test copy, only while injected
        roots.append(sdis / "recover_test")
    return roots


def scan(db: Any, corpus: Corpus, arn_re: "re.Pattern[str]", date_from: Optional[date], date_to: Optional[date],
         log: Callable[[str], None] = print) -> List[Target]:
    targets = find_targets(db, arn_re, date_from, date_to)
    by_day: Dict[date, List[Target]] = {}
    for t in targets:
        by_day.setdefault(t.day, []).append(t)
    log(f"{len(targets)} row(s)")
    for day in sorted(by_day):
        ids = ", ".join(str(t.id) for t in by_day[day])
        have = any(corpus.has_day(d) for d in (day - timedelta(days=1), day))
        log(f"{day}: {len(by_day[day])} row(s) [{ids}]" + ("" if have else "  - no corpus file for this day"))
    return targets


def run_report(app_dir: Path, db: Any, corpus: Corpus, arn_re: "re.Pattern[str]", date_from: Optional[date],
               date_to: Optional[date], expected: Optional[Path] = None, out: Optional[Path] = None,
               log: Callable[[str], None] = print) -> Tuple[Path, int]:
    targets = find_targets(db, arn_re, date_from, date_to)
    rows = make_report(db, corpus, targets)
    out = out or fixer_dir(app_dir) / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = write_report(rows, out)
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    log(f"{len(rows)} row(s): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) if rows else "no rows")
    log(f"report: {path}")
    rc = 0
    if expected:
        rc = score(rows, json.loads(Path(expected).read_text(encoding="utf-8")), log)
    return path, rc


