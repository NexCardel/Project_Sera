"""
tools/inject_unknown_gst_arn.py - test rows for the GST ARN recovery tool
==========================================================================
Plan: docs/gst_arn_recovery/injector-plan.md. On a PC with real GST pages but no real ARN pages,
this makes three failed GST rows dated 6 Oct 2026 (the way SGT wrote them live) and a copy of that
day's SDIS capture with synthetic ARN pages added, so the recovery tool can be built and scored.

    python tools/inject_unknown_gst_arn.py inject   # make the rows and the test corpus copy
    python tools/inject_unknown_gst_arn.py inject --pause-sync   # same on a live PC: sync off until remove
    python tools/inject_unknown_gst_arn.py remove   # delete exactly what inject made (and restore sync)
    python tools/inject_unknown_gst_arn.py status   # what is injected right now

Refuses to run while the app is open (it holds the database and its in-memory tracker) and while
sync is shadow or live (the capture triggers would send these fake rows to the office). Never opens
the live capture folder for writing (sdis_capture/) and never touches the admin corpus (sdis/corpus/).
Logs print counts, ids, ARNs and times only - never page text, names or GSTINs.
"""

import copy
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.sera_tool_common import actor_name as _actor, app_dir_default, app_is_running, open_database as _open_database  # noqa: E402,F401
from core.sgt.sgt_resolver import resolve_page                    # noqa: E402
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore, compose_values, load_registry   # noqa: E402
from core.sgt_i.uia_nodes import lines_from_nodes                 # noqa: E402

DAY = "2026-10-06"
TODAY = date(2026, 10, 6)
CORPUS_NAME = f"sdis_{DAY}.jsonl"
EXPECTED_NAME = "expected.json"
RECORD_VERSION = 1                       # core/sdis/recorder.py RECORD_VERSION
PORTAL = "GST Portal"
FILING_TYPE = "GST Return"
STATUS = "Submitted (Not Verified)"      # what SGT wrote live for an unconfirmed session (2.12.2-2.12.5)
CAPTURE_METHOD = "SGT_live"
RECORD_SPEC = "gst_submit_success"
SPEC_CONFIDENCE = 90
CT_TEXT = 50020                          # UIA Text control type
# The success banner from tests/sgt_golden/gst_return_filed.json (a recorded GST success page).
BANNER = "Return submitted successfully. ARN: {arn}"
CASES = ("A", "B", "C")
# A and C: minutes after the host session's last page. B: seconds after it (right after the host, so
# the look-back from the ARN page reaches the host's header and nothing else).
OFFSET_MIN = {"A": 3, "C": 6}
B_GAP_S = 45
# B's header may sit at most this far before its ARN page (the plan's 10 minutes). The real portal shows
# the header on every page, so the gap can be zero.
B_HEADER_GAP = (timedelta(0), timedelta(minutes=10))
DESCRIPTION = {
    "A": "clean: the ARN page is in the host session",
    "B": "boundary split: the ARN page is in a new session with no header",
    "C": "ARN page dropped: no page, the row points at the host session",
}
HOST_RECORD_PORTAL = "gst"


class InjectError(Exception):
    """A gate or a precondition failed. The message says why; nothing else was changed."""


# ── Where things live ────────────────────────────────────────────────────────────
def live_corpus_path(app_dir: Path) -> Path:
    return app_dir / "sdis_capture" / CORPUS_NAME


def recover_dir(app_dir: Path) -> Path:
    return app_dir / "sdis" / "recover_test"


def expected_path(app_dir: Path) -> Path:
    return recover_dir(app_dir) / EXPECTED_NAME


def corpus_copy_path(app_dir: Path, device_id: str) -> Path:
    return recover_dir(app_dir) / device_id / CORPUS_NAME


def _guard_not_live(path: Path, app_dir: Path) -> None:
    """The copy must never land in a folder the recorder or the excavator owns."""
    p = path.resolve()
    for forbidden in (app_dir / "sdis_capture", app_dir / "sdis" / "corpus"):
        if forbidden.resolve() in p.parents:
            raise InjectError("refusing to write inside a folder the recorder or excavator owns")


# ── Gates ────────────────────────────────────────────────────────────────────────
def sync_mode(db: Any) -> str:
    with db._connect() as conn:
        row = conn.execute("SELECT value FROM _sync_meta WHERE key = 'mode'").fetchone()
    return (row[0] if row and row[0] else "off")


def _require_closed_and_sync_off(db_factory: Callable[[], Any], running: Callable[[], bool],
                                 pause: bool = False) -> Tuple[Any, str]:
    """The open database and its sync mode. A mode other than off is refused unless pause is set
    (inject --pause-sync: the caller turns sync off and remove turns it back on)."""
    if running():
        raise InjectError("Sera is open. Close the app first: it holds the database and its tracker.")
    db = db_factory()
    mode = sync_mode(db)
    if mode != "off" and not pause:
        raise InjectError(f"sync mode is '{mode}'. Turn sync off first, or run inject --pause-sync "
                          "(remove turns it back on): its triggers would send these rows.")
    return db, mode


# ── Reading the real capture (read-only) ─────────────────────────────────────────
@dataclass
class Host:
    session: str
    browser: str
    gstin: str
    name: str
    form: str
    period: str
    started: str
    last_ts: str
    last_url: str
    records: List[Dict[str, Any]] = field(default_factory=list)   # GST records of the session, by time
    header: List[bool] = field(default_factory=list)              # per record: the GSTIN header was read


def _reads(registry: Any, rec: Dict[str, Any]) -> Any:
    lines = lines_from_nodes(rec["docs"])
    return resolve_page(registry, lines, rec.get("portal") or PORTAL, rec.get("url") or "", TODAY,
                        title=rec.get("title") or "")


def survey_hosts(records: Sequence[Dict[str, Any]], registry: Any) -> Tuple[List[Host], List[Tuple[str, str, Optional[str]]]]:
    """GST sessions whose own header rule (the shipped gst_gstin spec) finds exactly one client
    GSTIN. Those with a form and a tax period come first, then by time. Also returns every GST
    page's header as (time, browser, GSTIN or None), all sessions, for the same-browser check."""
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        if HOST_RECORD_PORTAL in (rec.get("portal") or "").lower() and rec.get("session"):
            by_session.setdefault(rec["session"], []).append(rec)
    hosts: List[Host] = []
    headers: List[Tuple[str, str, Optional[str]]] = []
    for sid, recs in by_session.items():
        recs = sorted(recs, key=lambda r: r["ts"])
        gstins, name, form, period = set(), "", "", ""
        header: List[bool] = []
        for rec in recs:
            res = _reads(registry, rec)
            g = res.profile.get("gstin")
            header.append(bool(g))
            headers.append((rec["ts"], rec.get("browser") or "", g.value if g else None))
            if g:
                gstins.add(g.value)
            if res.profile.get("name"):
                name = res.profile["name"].value
            # the period is composed ("August (FY 2026-27)") from the page's parts, as SGT composes it
            raw = {k: h.value for k, h in res.current.items()}
            current = {**raw, **compose_values(raw, registry.current_rules.compose)}
            pairs = [d.values() for d in res.datasets] + [current]
            for vals in pairs:
                if vals.get("form") and vals.get("period"):
                    form, period = vals["form"], vals["period"]
        if len(gstins) != 1:
            continue
        last = recs[-1]
        hosts.append(Host(session=sid, browser=last.get("browser") or "", gstin=next(iter(gstins)), name=name,
                          form=form, period=period, started=recs[0].get("started") or recs[0]["ts"],
                          last_ts=last["ts"], last_url=last.get("url") or "", records=recs, header=header))
    hosts.sort(key=lambda h: (not (h.form and h.period), h.started))
    return hosts, headers


def make_arn(gstin: str, seq: int) -> str:
    """AA + state code + 1026 + 9999 + two digits + a check letter: valid by shape, 9999 marks it fake."""
    return f"AA{gstin[:2]}10269999{seq:02d}T"


def _banner_docs(docs: List[List[Dict[str, Any]]], arn: str) -> List[List[Dict[str, Any]]]:
    """A copy of the page with one text node added: the success banner carrying the ARN."""
    out = copy.deepcopy(docs)
    target = next((d for d in out if d), None)
    if target is None:
        target = []
        out.append(target)
    rect = next((d[0].get("rect") for d in out if d), None) or [0, 0, 0, 0]
    target.append({"parent": -1, "depth": 0, "ctype": CT_TEXT, "name": BANNER.format(arn=arn),
                   "rect": list(rect), "role": "description"})
    return out


def _reads_arn(registry: Any, rec: Dict[str, Any], arn: str) -> bool:
    res = _reads(registry, rec)
    if any(d.values().get("arn") == arn for d in res.datasets):
        return True
    hit = res.current.get("arn")
    return bool(hit and hit.value == arn)


def _page_for(src: Dict[str, Any], session: str, started: str, ts: str, arn: str) -> Dict[str, Any]:
    return {"v": RECORD_VERSION, "ts": ts, "started": started, "session": session,
            "portal": src.get("portal") or PORTAL, "url": src.get("url") or "", "link": src.get("link") or "",
            "title": src.get("title") or "", "browser": src.get("browser") or "",
            "docs": _banner_docs(src["docs"], arn)}


def _local(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _clear(headers: Sequence[Tuple[str, str, Optional[str]]], host: Host, start_ts: str, end_ts: str) -> bool:
    """The same-browser check. Looking back from end_ts, the nearest GSTIN header in the host's
    browser must be the host's own: no other client's header may sit between start_ts (the host's
    header) and end_ts (the ARN page, or the row time for case C). A page with no browser name
    could be any browser, so it counts too."""
    for ts, browser, gstin in headers:
        if not (start_ts < ts <= end_ts) or gstin is None or gstin == host.gstin:
            continue
        if not host.browser or not browser or browser == host.browser:
            return False
    return True


def _build_case(case: str, host: Host, seq: int, registry: Any,
                headers: Sequence[Tuple[str, str, Optional[str]]]) -> Dict[str, Any]:
    """One case on one host, or InjectError when this host cannot carry it."""
    arn = make_arn(host.gstin, seq)
    header_idx = max((i for i, h in enumerate(host.header) if h), default=None)
    if header_idx is None:
        raise InjectError("no header page")
    header_ts = host.records[header_idx]["ts"]
    if case == "C":
        ts = _stamp(_local(host.last_ts) + timedelta(minutes=OFFSET_MIN["C"]))
        page = None
        row_session = host.session
    elif case == "A":
        ts = _stamp(_local(host.last_ts) + timedelta(minutes=OFFSET_MIN["A"]))
        src = next((r for r in reversed(host.records) if _reads_arn(registry, _page_for(r, host.session, host.started, ts, arn), arn)), None)
        if src is None:
            raise InjectError("no host page carries the synthetic ARN")
        page = _page_for(src, host.session, host.started, ts, arn)
        row_session = host.session
    else:  # B: right after the host's last page, with its header at most 10 minutes before
        ts = _stamp(_local(host.last_ts) + timedelta(seconds=B_GAP_S))
        gap = _local(ts) - _local(header_ts)
        if not B_HEADER_GAP[0] <= gap <= B_HEADER_GAP[1]:
            raise InjectError("the host's header is too long before the ARN page")
        new_session = uuid.uuid4().hex[:12]
        # Prefer a page with no header (the new session never saw one). The real portal shows the
        # header on every page, so when none reads the ARN the host's own header comes along: the
        # page then names the same client, and the look-back still lands on it.
        order = [i for i in reversed(range(len(host.records))) if not host.header[i]]
        order += [i for i in reversed(range(len(host.records))) if host.header[i]]
        src = None
        for i in order:
            if _reads_arn(registry, _page_for(host.records[i], new_session, ts, ts, arn), arn):
                src = host.records[i]
                break
        if src is None:
            raise InjectError("no host page carries the synthetic ARN")
        page = _page_for(src, new_session, ts, ts, arn)
        row_session = new_session
    if _local(ts).date().isoformat() != DAY:
        raise InjectError("the case would fall outside the day")
    if not _clear(headers, host, header_ts, ts):
        raise InjectError("another client's header is in the host's browser before the ARN time")
    return {"case": case, "arn": arn, "row_ts": ts, "row_session": row_session, "page": page,
            "host": host, "header_ts": header_ts, "page_url": (page or {}).get("url") or host.last_url}


def _page_headers(spec: Dict[str, Any], registry: Any) -> List[Tuple[str, str, Optional[str]]]:
    """The header an ARN page this script adds puts in the day, read the way the same-browser check reads it."""
    page = spec["page"]
    if page is None:
        return []
    g = _reads(registry, page).profile.get("gstin")
    return [(page["ts"], page.get("browser") or "", g.value if g else None)]


def plan_cases(records: Sequence[Dict[str, Any]], registry: Any) -> List[Dict[str, Any]]:
    hosts, headers = survey_hosts(records, registry)
    if len(hosts) < len(CASES):
        raise InjectError(f"only {len(hosts)} GST host sessions qualify; need {len(CASES)}. Stopping.")
    chosen: List[Dict[str, Any]] = []
    added: List[Tuple[str, str, Optional[str]]] = []     # the ARN pages chosen so far: headers too
    used_sessions, used_gstins = set(), set()
    for seq, case in enumerate(CASES, start=1):
        spec = None
        for distinct in (True, False):           # prefer three different clients, else any
            for host in hosts:
                if host.session in used_sessions or (distinct and host.gstin in used_gstins):
                    continue
                try:
                    spec = _build_case(case, host, seq, registry, headers + added)
                except InjectError:
                    continue
                break
            if spec:
                break
        if spec is None:
            raise InjectError(f"no host session can carry case {case}")
        chosen.append(spec)
        added.extend(_page_headers(spec, registry))
        used_sessions.add(spec["host"].session)
        used_gstins.add(spec["host"].gstin)
    # A page chosen later can land in the window of a case chosen earlier, so every case is checked
    # against every header, the day's and the added ARN pages alike.
    everything = headers + added
    for spec in chosen:
        if not _clear(everything, spec["host"], spec["header_ts"], spec["row_ts"]):
            raise InjectError(f"case {spec['case']}: another client's header, an added ARN page included, "
                              "is in the host's browser before the ARN time")
    return chosen


# ── The test corpus copy (new file; the live file is only read) ──────────────────
def _line_ts(raw: bytes) -> Optional[str]:
    try:
        return json.loads(raw).get("ts")
    except (ValueError, AttributeError):
        return None


def write_test_corpus(src: Path, dst: Path, pages: Sequence[Dict[str, Any]]) -> int:
    """Copies src line by line (bytes kept as they are) and inserts each page where its time falls."""
    pending = sorted(({"ts": p["ts"], "raw": (json.dumps(p, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")}
                      for p in pages), key=lambda x: x["ts"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    written = 0
    with open(src, "rb") as fin, open(tmp, "wb") as fout:          # read-only on the live file
        for raw in fin:
            ts = _line_ts(raw)
            while pending and ts is not None and pending[0]["ts"] < ts:
                fout.write(pending.pop(0)["raw"])
                written += 1
            fout.write(raw if raw.endswith(b"\n") else raw + b"\n")
        for p in pending:
            fout.write(p["raw"])
            written += 1
    os.replace(tmp, dst)
    return written


# ── Tracker rows: built by the app's own payload code, written by its own insert ─
def tracker_payload(session_id: str, arn: str, page_url: str, row_ts: str) -> Dict[str, Any]:
    from core.sgt.sgt_shadow import MODE_LIVE, SgtShadow, _Session, _Slot
    with tempfile.TemporaryDirectory() as tmp:
        sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                        read_uia=lambda h: {"lines": []}, log_dir=Path(tmp) / "log",
                        state_path=Path(tmp) / "state.json", mode=MODE_LIVE,
                        today=lambda: TODAY, echo=lambda m: None)
        s = _Session(session_id=session_id, portal=PORTAL)              # not confirmed: no PAN / GSTIN
        slot = _Slot(values={"arn": arn, "status": STATUS}, record=RECORD_SPEC,
                     confidence=SPEC_CONFIDENCE, first_page=page_url)
        payload = sgt._tracker_payload(s, slot)
    payload["filing_date"] = _local(row_ts).strftime("%Y-%m-%d %H:%M:%S")
    return payload


def _insert_row(db: Any, arn: str, session_id: str, row_ts: str, payload: Dict[str, Any], actor: str) -> int:
    result = db.insert_tracker_dump(
        client_id=None, service_id=None, portal=f"{PORTAL} ({FILING_TYPE})", period_label="",
        arn_number=arn, capture_method=CAPTURE_METHOD, status=payload["status"],
        raw_payload_json=json.dumps(payload), captured_by=actor, pan="", session_id=session_id,
        filing_type=FILING_TYPE, dataset_key=payload["dataset_key"])
    if not result or result.get("duplicate") or not result.get("id"):
        raise InjectError("the insert did not give a new row")
    created = _local(row_ts).astimezone(timezone.utc).isoformat()
    with db._connect_raw() as conn:
        cur = conn.execute("UPDATE tracker_dump SET created_at = ? WHERE id = ? AND arn_number = ?",
                           (created, result["id"], arn))
        if cur.rowcount != 1:
            raise InjectError("could not set the row time")
    return int(result["id"])


def _row_exists(db: Any, arns: Sequence[str]) -> int:
    with db._connect_raw() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM tracker_dump WHERE arn_number IN ({','.join('?' * len(arns))})",
                            list(arns)).fetchone()[0]


# ── expected.json (written atomically, after every step) ─────────────────────────
def _write_expected(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_expected(app_dir: Path) -> Optional[Dict[str, Any]]:
    p = expected_path(app_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ── Commands ─────────────────────────────────────────────────────────────────────
def inject(app_dir: Path, db_factory: Callable[[], Any], device_id: str, actor: str,
           running: Callable[[], bool] = app_is_running,
           records: Optional[Sequence[Dict[str, Any]]] = None,
           registry: Any = None, log: Callable[[str], None] = print,
           pause_sync: bool = False) -> Dict[str, Any]:
    if expected_path(app_dir).exists():
        raise InjectError("expected.json already exists: run remove first, so rows are never added twice")
    live = live_corpus_path(app_dir)
    dst = corpus_copy_path(app_dir, device_id)
    _guard_not_live(dst, app_dir)
    db, mode = _require_closed_and_sync_off(db_factory, running, pause=pause_sync)
    if records is None:
        records = _read_records(live)
    registry = registry or load_registry([BUILTIN_FIELDS_PATH])
    cases = plan_cases(records, registry)
    arns = [c["arn"] for c in cases]
    if _row_exists(db, arns):
        raise InjectError("a tracker row already holds one of these ARNs; not adding anything")

    expected: Dict[str, Any] = {
        "note": "Synthetic test rows made by tools/inject_unknown_gst_arn.py. This PC's tracker dump is test data.",
        "day": DAY, "device": device_id, "state": "inserting",
        "corpus_copy": str(dst), "sync_paused_from": mode if mode != "off" else None, "rows": [],
    }
    for c in cases:
        h = c["host"]
        expected["rows"].append({
            "row_id": None, "case": c["case"], "arn": c["arn"], "description": DESCRIPTION[c["case"]],
            "host_session": h.session, "row_session": c["row_session"],
            "arn_page_session": (c["page"] or {}).get("session"), "row_time": c["row_ts"],
            "host_last_page": h.last_ts,
            "true_gstin": h.gstin, "true_name": h.name, "true_form": h.form, "true_period": h.period,
            "arn_page_written": c["page"] is not None,
        })
    _write_expected(expected_path(app_dir), expected)    # first, so remove always knows the mode to restore
    if mode != "off":
        db.set_sync_mode("off")
        log(f"sync paused: '{mode}' -> 'off' (remove turns it back to '{mode}')")

    pages = [c["page"] for c in cases if c["page"] is not None]
    n = write_test_corpus(live, dst, pages)
    log(f"test corpus written: {n} records (the day's file plus {len(pages)} synthetic ARN pages)")

    for c, row in zip(cases, expected["rows"]):
        payload = tracker_payload(c["row_session"], c["arn"], c["page_url"], c["row_ts"])
        row["row_id"] = _insert_row(db, c["arn"], c["row_session"], c["row_ts"], payload, actor)
        _write_expected(expected_path(app_dir), expected)
        log(f"case {c['case']}: row {row['row_id']}, ARN {c['arn']}, time {c['row_ts']}")
    expected["state"] = "done"
    _write_expected(expected_path(app_dir), expected)
    return expected


def remove(app_dir: Path, db_factory: Callable[[], Any], running: Callable[[], bool] = app_is_running,
           log: Callable[[str], None] = print) -> int:
    expected = read_expected(app_dir)
    if expected is None:
        log("nothing injected (no expected.json)")
        return 0
    db, _mode = _require_closed_and_sync_off(db_factory, running)
    left = []
    for row in expected["rows"]:
        rid = row.get("row_id")
        if rid is None:
            continue
        with db._connect_raw() as conn:
            found = conn.execute("SELECT arn_number, capture_method FROM tracker_dump WHERE id = ?", (rid,)).fetchone()
        if found is None:
            log(f"row {rid}: already gone")
            continue
        if found != (row["arn"], CAPTURE_METHOD):
            left.append(rid)
            log(f"row {rid}: not the row inject made (ARN or method differs); left alone")
            continue
        db.delete_tracker_dump(rid)
        log(f"row {rid}: removed")
    if left:
        raise InjectError(f"{len(left)} row(s) left in place; expected.json kept so you can look at them")
    paused_from = expected.get("sync_paused_from")
    if paused_from:
        db.set_sync_mode(paused_from)
        log(f"sync restored: 'off' -> '{paused_from}'")
    copy_path = Path(expected["corpus_copy"])
    _guard_not_live(copy_path, app_dir)
    if copy_path.exists():
        copy_path.unlink()
    expected_path(app_dir).unlink()
    for folder in (copy_path.parent, recover_dir(app_dir)):
        try:
            folder.rmdir()                   # only if empty: anything else in there is not ours
        except OSError:
            pass
    log("recover_test cleared" if not recover_dir(app_dir).exists() else "recover_test kept: other files are in it")
    return 0


def status(app_dir: Path, log: Callable[[str], None] = print) -> int:
    expected = read_expected(app_dir)
    if expected is None:
        log("nothing injected")
        return 0
    log(f"state: {expected.get('state')}; device folder: {'yes' if Path(expected['corpus_copy']).exists() else 'no'}")
    if expected.get("sync_paused_from"):
        log(f"sync paused: off until remove (it was '{expected['sync_paused_from']}')")
    for row in expected["rows"]:
        log(f"case {row['case']}: row {row.get('row_id')}, ARN {row['arn']}, time {row['row_time']}, "
            f"ARN page {'written' if row['arn_page_written'] else 'dropped'}")
    return 0


def _read_records(path: Path) -> List[Dict[str, Any]]:
    out = []
    with open(path, "rb") as f:                  # read-only
        for raw in f:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("ts") and isinstance(rec.get("docs"), list):
                out.append(rec)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("inject", "remove", "status"):
        print(__doc__.strip().splitlines()[0])
        print("usage: python tools/inject_unknown_gst_arn.py inject [--pause-sync] | remove | status")
        return 2
    pause_sync = "--pause-sync" in argv[1:]
    app_dir = app_dir_default()
    try:
        if argv[0] == "status":
            return status(app_dir)
        from sync_identity import load_device_id_cheap
        device_id = load_device_id_cheap(app_dir)
        if not device_id:
            raise InjectError("this PC has no device id yet (pair it first)")
        db_factory = lambda: _open_database(app_dir)     # noqa: E731
        if argv[0] == "inject":
            inject(app_dir, db_factory, device_id, _actor(app_dir), pause_sync=pause_sync)
            print("inject done. Open Sera to see the rows; run remove when finished.")
            return 0
        return remove(app_dir, db_factory)
    except InjectError as e:
        print(f"refused: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
