"""
tools/sgt_soak_report.py - pass/fail for a live capture soak (blueprint 8.2, layer 1)
=====================================================================================
Run this after a 5 h soak (an office PC doing real work, or the dev PC on the mock pages of
`tools/sgt_i_uia_node_bench.py --soak`). It only READS the app's own logs and prints a table of
numbers with PASS / FAIL per row:

  ~/AmanAssociates_Sera/logs/vsdc.log       beats (once a minute), health lines, stalls, stuck workers
  ~/AmanAssociates_Sera/logs/memory.log     the app's working set
  ~/AmanAssociates_Sera/sgt_shadow/*.jsonl  dataset events, late_absorbed_after_end, session_end stats

Rows (a soak passes when no row says FAIL):
  data               the window has about one beat a minute (an empty window never passes)
  missed submissions every ARN in --expected has a dataset at Submitted or higher (needs --expected)
  p95 read time      p95 of the last segment is under 2x the first segment's (floor 100 ms); a segment is
                     the first / last hour of the window, or a third of it for a window under 3 h
  working set growth median working set of the last segment minus the first's is under 100 MB
  stalls             no "capture worker STALLED" line, no health state "stalled"
  beat gaps          no gap over 3 minutes between beats (an app restart in the gap is counted apart)
  abandoned workers  no "read stuck ... replaced its worker" line

  --expected FILE    one ARN per line (blank lines and # comments ignored); the owner writes it, keep it
                     off the repo. ARNs are only counted: the report prints how many were missed (and
                     their line numbers in that file), never the ARNs, client names or any page text.

Usage:
    python tools/sgt_soak_report.py --start "2026-10-15 09:00" --end "2026-10-15 14:00" [--expected arns.txt]
    python tools/sgt_soak_report.py --hours 5                 (the 5 h that end now)
    python tools/sgt_soak_report.py --root DIR                (another Sera data folder; for tests)

Exit code 0 = PASS, 1 = at least one FAIL, 2 = bad arguments.
"""

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

READ_RATIO_MAX = 2.0           # p95 of the last segment under 2x the first (8.2)
READ_FLOOR_MS = 100.0          # ... unless it is under this: a 20 ms -> 45 ms drift is not a slowdown
WS_GROWTH_MAX_MB = 100.0
BEAT_GAP_SEC = 180.0
BEAT_COVERAGE_MIN = 0.8        # beats found / minutes in the window
SEGMENT_SEC = 3600.0           # "hour 1" and "hour 5"; a window under 3 h uses a third each
SUBMITTED_LEVEL = 2            # "Submitted (Not Verified)" and up
STAMP = "%Y-%m-%d %H:%M:%S"

PASS, FAIL, NA, INFO = "PASS", "FAIL", "n/a", "info"


@dataclass
class Row:
    name: str
    value: str
    limit: str
    verdict: str


@dataclass
class Report:
    start: datetime
    end: datetime
    rows: List[Row] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.verdict != FAIL for r in self.rows)


# ---- reading the logs ---------------------------------------------------------------------------

def _stamp(line: str) -> Optional[datetime]:
    try:
        return datetime.strptime(line[:19], STAMP)
    except ValueError:
        return None


def _log_lines(path: Path) -> Iterator[str]:
    """The rolled file first (path.1 = older), then the live one."""
    for p in (path.with_name(path.name + ".1"), path):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                yield from f
        except OSError:
            continue


def _stamped(path: Path, start: datetime, end: datetime) -> Iterator[Tuple[datetime, str]]:
    for line in _log_lines(path):
        at = _stamp(line)
        if at is not None and start <= at <= end:
            yield at, line.rstrip("\n")


def _beat_fields(line: str) -> Dict[str, str]:
    body = line.split(" beat ", 1)[1] if " beat " in line else ""
    out = {}
    for part in body.split():
        k, eq, v = part.partition("=")
        if eq:
            out[k] = v
    return out


def _num(fields: Dict[str, str], key: str) -> Optional[float]:
    try:
        return float(fields[key])
    except (KeyError, ValueError):
        return None


def _p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * 95 // 100) - 1)]


def _segments(start: datetime, end: datetime) -> Tuple[Tuple[datetime, datetime], Tuple[datetime, datetime]]:
    """The first and last segment of the window: an hour each, or a third each for a window under 3 h."""
    span = (end - start).total_seconds()
    seg = timedelta(seconds=min(SEGMENT_SEC, span / 3.0))
    return (start, start + seg), (end - seg, end)


def _beats(path: Path, start: datetime, end: datetime) -> List[Tuple[datetime, Dict[str, str]]]:
    return [(at, _beat_fields(line)) for at, line in _stamped(path, start, end) if " beat " in line]


def _memory_samples(path: Path, start: datetime, end: datetime) -> List[Tuple[datetime, float]]:
    out = []
    for at, line in _stamped(path, start, end):
        m = re.search(r"\bws\s+([\d.]+) MB", line)
        if m:
            out.append((at, float(m.group(1))))
    return out


# ---- the expected-ARN check --------------------------------------------------------------------

def read_expected(path: Path) -> List[str]:
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#"):
            out.append(raw.split()[0].upper())
    return out


def _shadow_files(folder: Path, start: datetime, end: datetime) -> List[Path]:
    days: List[date] = []
    d = start.date()
    while d <= end.date():
        days.append(d)
        d += timedelta(days=1)
    return [p for p in (folder / f"sgt_shadow_{d.isoformat()}.jsonl" for d in days) if p.exists()]


def shadow_events(folder: Path, start: datetime, end: datetime) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """(raw line, parsed event) for each shadow-log record inside the window."""
    for p in _shadow_files(folder, start, end):
        try:
            f = open(p, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    rec = json.loads(line)
                    at = datetime.fromisoformat(str(rec["ts"]))
                except (ValueError, KeyError, TypeError):
                    continue
                if start <= at <= end and isinstance(rec, dict):
                    yield line, rec


def _level_of(status: Any) -> int:
    from core.sgt.sgt_toolbox import submit_level
    return submit_level(status)


def best_levels(events: Iterable[Tuple[str, Dict[str, Any]]], arns: Set[str]) -> Dict[str, int]:
    """For each expected ARN seen in a dataset event or a session_end payload: the highest status level."""
    best: Dict[str, int] = {}
    for line, rec in events:
        kind = rec.get("event")
        if kind not in ("dataset", "session_end"):
            continue
        upper = line.upper()
        hit = [a for a in arns if a in upper]
        if not hit:
            continue
        if kind == "dataset":
            change = rec.get("change")
            if change == "new":
                lvl = _level_of((rec.get("values") or {}).get("status"))
            elif change == "updated":
                moved = (rec.get("changes") or {}).get("status") or [None, None]
                lvl = _level_of(moved[1] if len(moved) > 1 else None)
            else:
                lvl = 0
            for a in hit:
                best[a] = max(best.get(a, -1), lvl)
        else:
            for ds in ((rec.get("payload") or {}).get("datasets") or []):
                a = str(ds.get("arn") or "").upper()
                if a in arns:
                    best[a] = max(best.get(a, -1), _level_of(ds.get("status")))
    return best


# ---- the report ---------------------------------------------------------------------------------

def build_report(root: Path, start: datetime, end: datetime, expected: Optional[List[str]] = None) -> Report:
    rep = Report(start, end)
    rows = rep.rows
    logs = root / "logs"
    beats = _beats(logs / "vsdc.log", start, end)
    minutes = max(1.0, (end - start).total_seconds() / 60.0)

    coverage = len(beats) / minutes
    rows.append(Row("data: beats in the window", f"{len(beats)} of about {minutes:.0f}",
                    f"at least {BEAT_COVERAGE_MIN:.0%}", PASS if coverage >= BEAT_COVERAGE_MIN else FAIL))

    rows.append(_missed_row(root, start, end, expected))

    (a0, a1), (b0, b1) = _segments(start, end)
    head = [v for at, f in beats if a0 <= at <= a1 for v in [_num(f, "uia_last_ms")]
            if v is not None and (_num(f, "uia_reads") or 0) > 0]
    tail = [v for at, f in beats if b0 <= at <= b1 for v in [_num(f, "uia_last_ms")]
            if v is not None and (_num(f, "uia_reads") or 0) > 0]
    p_head, p_tail = _p95(head), _p95(tail)
    limit = f"last under {READ_RATIO_MAX:.0f}x first (or {READ_FLOOR_MS:.0f} ms)"
    if p_head is None or p_tail is None:
        rows.append(Row("p95 read time", "no reads in a segment", limit, NA))
    else:
        ok = p_tail <= max(READ_RATIO_MAX * p_head, READ_FLOOR_MS)
        rows.append(Row("p95 read time", f"first {p_head:.0f} ms ({len(head)}), last {p_tail:.0f} ms ({len(tail)})",
                        limit, PASS if ok else FAIL))

    mem = _memory_samples(logs / "memory.log", start, end)
    m_head = [v for at, v in mem if a0 <= at <= a1]
    m_tail = [v for at, v in mem if b0 <= at <= b1]
    if not m_head or not m_tail:
        rows.append(Row("working set growth", "no memory.log samples in a segment", f"under {WS_GROWTH_MAX_MB:.0f} MB", NA))
    else:
        growth = statistics.median(m_tail) - statistics.median(m_head)
        rows.append(Row("working set growth", f"{growth:+.1f} MB (first {statistics.median(m_head):.0f}, "
                        f"last {statistics.median(m_tail):.0f}, highest {max(v for _, v in mem):.0f})",
                        f"under {WS_GROWTH_MAX_MB:.0f} MB", PASS if growth < WS_GROWTH_MAX_MB else FAIL))

    lines = list(_stamped(logs / "vsdc.log", start, end))
    stalls = sum(1 for _, ln in lines if "capture worker STALLED" in ln)
    stalls += sum(1 for _, ln in lines if "health state=stalled" in ln)
    rows.append(Row("stalls", str(stalls), "0", PASS if stalls == 0 else FAIL))

    gaps, restarts = _gaps(beats, lines)
    rows.append(Row("beat gaps over 3 min", f"{gaps} (+{restarts} around an app restart)", "0",
                    PASS if gaps == 0 else FAIL))

    stuck = sum(1 for _, ln in lines if re.search(r"reader \w+ read stuck .* replaced its worker", ln))
    most = max((_num(f, "uia_abandoned") or 0 for _, f in beats), default=0)
    abandoned = int(max(stuck, most))
    rows.append(Row("abandoned workers", str(abandoned), "0", PASS if abandoned == 0 else FAIL))

    rows.extend(_info_rows(root, beats, lines, start, end))
    return rep


def _missed_row(root: Path, start: datetime, end: datetime, expected: Optional[List[str]]) -> Row:
    name = "missed scripted submissions"
    if expected is None:
        return Row(name, "no --expected file given", "0", NA)
    if not expected:
        return Row(name, "the --expected file holds no ARN", "0", NA)
    arns = set(expected)
    best = best_levels(shadow_events(root / "sgt_shadow", start, end), arns)
    missed = [i + 1 for i, a in enumerate(expected) if a not in best]
    low = [i + 1 for i, a in enumerate(expected) if a in best and best[a] < SUBMITTED_LEVEL]
    value = f"{len(missed)} missed, {len(low)} under-stated of {len(expected)}"
    if missed:
        value += f"; missed are lines {_numbers(missed)}"
    if low:
        value += f"; under-stated are lines {_numbers(low)}"
    return Row(name, value, "0", PASS if not missed and not low else FAIL)


def _numbers(items: List[int], limit: int = 12) -> str:
    shown = ", ".join(str(i) for i in items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _gaps(beats: List[Tuple[datetime, Dict[str, str]]], lines: List[Tuple[datetime, str]]) -> Tuple[int, int]:
    """Beat gaps over BEAT_GAP_SEC: (unexplained, with a worker start/stop line inside)."""
    restarts_at = [at for at, ln in lines if "capture worker started" in ln or "capture worker stopped" in ln]
    plain = explained = 0
    for (t0, _), (t1, _) in zip(beats, beats[1:]):
        if (t1 - t0).total_seconds() > BEAT_GAP_SEC:
            if any(t0 <= r <= t1 for r in restarts_at):
                explained += 1
            else:
                plain += 1
    return plain, explained


def _info_rows(root: Path, beats: List[Tuple[datetime, Dict[str, str]]], lines: List[Tuple[datetime, str]],
               start: datetime, end: datetime) -> List[Row]:
    out = []
    peaks = [v for _, f in beats for v in [_num(f, "uia_max_ms")] if v is not None]
    if peaks:
        out.append(Row("slowest read in any minute", f"{max(peaks):.0f} ms", "", INFO))
    blind = sum(1 for _, f in beats if f.get("health_state") == "blind")
    bad = sum(1 for _, f in beats if f.get("health_state") not in (None, "ok", "no-portal", "alerts-unconfigured"))
    out.append(Row("minutes not in a normal health state", f"{bad} (blind {blind})", "", INFO))
    out.append(Row("reads dropped by the absorb rules", f"{int(sum(_num(f, 'sgt_dropped') or 0 for _, f in beats))}", "", INFO))
    out.append(Row("late reads for a finished client", f"{int(sum(_num(f, 'uia_late') or 0 for _, f in beats))}", "", INFO))
    late = ocr_rows = new_rows = ended = reads = ocr_reads = 0
    for _, rec in shadow_events(root / "sgt_shadow", start, end):
        kind = rec.get("event")
        if kind == "late_absorbed_after_end":
            late += 1
        elif kind == "dataset" and rec.get("change") == "new":
            new_rows += 1
            if rec.get("source") == "ocr":
                ocr_rows += 1
        elif kind == "session_end":
            ended += 1
            st = rec.get("stats") or {}
            reads += int(st.get("reads") or 0)
            ocr_reads += int(st.get("ocr_reads") or 0)
    out.append(Row("rows created / of them OCR-only", f"{new_rows} / {ocr_rows}", "", INFO))
    out.append(Row("reads landing after the session ended", str(late), "", INFO))
    out.append(Row("sessions ended / their reads / OCR reads", f"{ended} / {reads} / {ocr_reads}", "", INFO))
    return out


def format_report(rep: Report) -> str:
    wide = max(len(r.name) for r in rep.rows)
    out = [f"SGT soak report: {rep.start:%Y-%m-%d %H:%M} to {rep.end:%Y-%m-%d %H:%M} "
           f"({(rep.end - rep.start).total_seconds() / 3600:.1f} h)", ""]
    for r in rep.rows:
        tail = f"   [limit: {r.limit}]" if r.limit else ""
        out.append(f"{r.verdict:5} {r.name.ljust(wide)}  {r.value}{tail}")
    unchecked = sum(1 for r in rep.rows if r.verdict == NA)
    out.append("")
    out.append(("PASS" if rep.ok else "FAIL") + (f" ({unchecked} row(s) could not be checked)" if unchecked else ""))
    text = "\n".join(out)
    try:
        from core.vsdc.vsdc_alerts import redact_identifiers
        return redact_identifiers(text)
    except Exception:
        return text


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.strip().replace("T", " "))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Pass/fail for a live SGT capture soak (reads the app's logs only).")
    ap.add_argument("--start", help='window start, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--end", help='window end, "YYYY-MM-DD HH:MM" (default: now)')
    ap.add_argument("--hours", type=float, default=5.0, help="window length when --start is not given (default 5)")
    ap.add_argument("--expected", help="file with the ARNs of the test filings, one per line (kept off the repo)")
    ap.add_argument("--root", help="the Sera data folder (default ~/AmanAssociates_Sera)")
    args = ap.parse_args(argv)
    try:
        end = _when(args.end) if args.end else datetime.now().replace(microsecond=0)
        start = _when(args.start) if args.start else end - timedelta(hours=args.hours)
    except ValueError:
        print("--start / --end must look like 2026-10-15 09:00")
        return 2
    if end <= start:
        print("--end must be after --start")
        return 2
    root = Path(args.root) if args.root else Path.home() / "AmanAssociates_Sera"
    expected = None
    if args.expected:
        try:
            expected = read_expected(Path(args.expected))
        except OSError:
            print("cannot read the --expected file")
            return 2
    rep = build_report(root, start, end, expected)
    print(format_report(rep))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
