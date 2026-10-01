"""
core/sgt/sgt_corpus.py - the pages SGT read, kept for replay
============================================================
Every bug found in a field test so far (an option list read as the choice, "ITR - 2", a lowercase
dropdown name, an empty Last Name taking "PAN") was fixed once and could quietly return with the
next spec edit. Recording what SGT actually read lets every later change be replayed against
real pages (tools/sgt_replay.py) and shown as a diff before it goes live.

What is recorded: the TEXT LINES of each page SGT resolved (UIA, or OCR when UIA was blind), with
its sanitized link, window title, portal and time. No screenshots. A page whose lines are
unchanged since the last time they were recorded today is not written again.

Format version ("v" field, CORPUS_VERSION below): v1 records (no "v" field - every page recorded
before 2026-09-27) hold lines only. v2 adds an optional "nodes" field - the same page's node dump
(core/sgt_i/uia_nodes.read_page_nodes()["docs"]), written beside the lines only while SGT-I is on
(blueprint 14.2's "shared read" is not adopted yet, so this is an extra read, on SGT-I's own
account). It is the fuel tools/sgt_lines_equivalence.py's corpus mode checks. load_pages() and
sgt_replay.py read both versions unchanged - "nodes" is simply absent on v1 (and v2 pages read
with SGT-I off) and on any page whose extra read failed. v3 (2026-09-30): the same, and a node
carries "aid" (its HTML id) when the markup sets one - absent on v2 nodes even where the page had
ids, so tools/sgt_i_id_census.py only counts v3 pages.

Where: ~/AmanAssociates_Sera/sgt_corpus/pages_YYYY-MM-DD.jsonl - on this PC only, like the
database. It holds client data (names, PANs, whatever the portal shows), so it is NEVER copied
into the repository; tests use the fictional pages in tests/sgt_golden/. Files older than
RETENTION_DAYS are deleted, and recording stops for the day past MAX_DAY_BYTES.
Switch: Settings -> Tracker -> "Record pages for SGT testing" (on by default while SGT is on).
Every time that switch flips (on <-> off) it is echoed to the console: the corpus going dark for
days after 22 Sep 2026 traced back to this switch turning off with nothing saying so.
"""

import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME

CORPUS_DIR_ENV = "SGT_CORPUS_DIR"
RETENTION_DAYS = 30
MAX_DAY_BYTES = 50 * 1024 * 1024
FILE_PREFIX = "pages_"
CORPUS_VERSION = 3   # 1 = lines only (no "v" field); 2 = lines + an optional "nodes" field; 3 = nodes carry "aid"


def corpus_dir() -> Path:
    env = os.environ.get(CORPUS_DIR_ENV)
    return Path(env) if env else Path.home() / SERA_DATA_DIR_NAME / "sgt_corpus"


class PageRecorder:
    """Appends pages to the day's file. record() never raises."""

    def __init__(self, directory: Optional[Path] = None, enabled: bool = True, echo=print) -> None:
        self._dir = directory
        self._echo = echo
        self._day: Optional[str] = None
        self._seen: set = set()
        self._bytes = 0
        self._full = False
        self._enabled = bool(enabled)
        self._echo(f"[SGT] page recording is {'ON' if self._enabled else 'OFF'} "
                   f"(Settings -> Tracker -> \"Record pages for SGT testing\")")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        # A silent, permanent stop here is exactly what let the corpus go dark for days without
        # anyone noticing (2026-09-27): every transition is echoed, same as the size-cap pause.
        value = bool(value)
        if value != self._enabled:
            self._echo(f"[SGT] page recording turned {'ON' if value else 'OFF'} "
                       f"(Settings -> Tracker -> \"Record pages for SGT testing\")")
        self._enabled = value

    @property
    def directory(self) -> Path:
        return self._dir or corpus_dir()

    def _open_day(self, day: str) -> Path:
        path = self.directory / f"{FILE_PREFIX}{day}.jsonl"
        if self._day != day:
            self._day, self._seen, self._full = day, set(), False
            self._bytes = path.stat().st_size if path.exists() else 0
            if path.exists():                      # restarted mid-day: do not re-record today's pages
                for rec in _read_jsonl(path):
                    self._seen.add(rec.get("hash"))
            self.prune()
        return path

    def record(self, *, session: str, portal: str, url: str, title: str, source: str,
               lines: List[str], ts: float, today: date,
               nodes: Optional[List[List[Dict[str, Any]]]] = None) -> None:
        if not self.enabled or not lines:
            return
        try:
            day = today.isoformat()
            path = self._open_day(day)
            if self._full:
                return
            digest = hashlib.sha1(json.dumps([portal, url, title, lines], ensure_ascii=False)
                                  .encode("utf-8")).hexdigest()[:16]
            if digest in self._seen:
                return
            rec: Dict[str, Any] = {"v": CORPUS_VERSION, "hash": digest, "ts": ts, "today": day,
                                   "session": session, "portal": portal, "url": url, "title": title,
                                   "source": source, "lines": lines}
            if nodes:
                rec["nodes"] = nodes             # only while SGT-I is on and the extra read worked
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            if self._bytes + len(line) > MAX_DAY_BYTES:
                self._full = True
                self._echo(f"[SGT] page recording paused for today: {MAX_DAY_BYTES // (1024 * 1024)} MB reached")
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            self._seen.add(digest)
            self._bytes += len(line.encode("utf-8"))
        except Exception as e:
            self._echo(f"[SGT] could not record a page: {e}")

    def prune(self, keep_days: int = RETENTION_DAYS) -> int:
        """Deletes day files older than keep_days. Returns how many were removed."""
        removed = 0
        cutoff = date.today() - timedelta(days=keep_days)
        try:
            for p in self.directory.glob(f"{FILE_PREFIX}*.jsonl"):
                try:
                    day = date.fromisoformat(p.stem[len(FILE_PREFIX):])
                except ValueError:
                    continue
                if day < cutoff:
                    p.unlink()
                    removed += 1
        except OSError:
            pass
        return removed


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def load_pages(directory: Optional[Path] = None, days: Optional[int] = None) -> List[Dict[str, Any]]:
    """Every recorded page (optionally only the last `days` days), oldest first."""
    d = directory or corpus_dir()
    files = sorted(d.glob(f"{FILE_PREFIX}*.jsonl"))
    if days is not None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        files = [p for p in files if p.stem[len(FILE_PREFIX):] >= cutoff]
    pages: List[Dict[str, Any]] = []
    for p in files:
        pages.extend(_read_jsonl(p))
    pages.sort(key=lambda r: r.get("ts") or 0)
    return pages


def group_sessions(pages: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Pages grouped by the SGT session that read them, each in reading order."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for rec in pages:
        out.setdefault(str(rec.get("session") or "?"), []).append(rec)
    return out


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
