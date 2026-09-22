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

Where: ~/AmanAssociates_Sera/sgt_corpus/pages_YYYY-MM-DD.jsonl - on this PC only, like the
database. It holds client data (names, PANs, whatever the portal shows), so it is NEVER copied
into the repository; tests use the fictional pages in tests/sgt_golden/. Files older than
RETENTION_DAYS are deleted, and recording stops for the day past MAX_DAY_BYTES.
Switch: Settings -> Tracker -> "Record pages for SGT testing" (on by default while SGT is on).
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


def corpus_dir() -> Path:
    env = os.environ.get(CORPUS_DIR_ENV)
    return Path(env) if env else Path.home() / SERA_DATA_DIR_NAME / "sgt_corpus"


class PageRecorder:
    """Appends pages to the day's file. record() never raises."""

    def __init__(self, directory: Optional[Path] = None, enabled: bool = True, echo=print) -> None:
        self.enabled = enabled
        self._dir = directory
        self._echo = echo
        self._day: Optional[str] = None
        self._seen: set = set()
        self._bytes = 0
        self._full = False

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
               lines: List[str], ts: float, today: date) -> None:
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
            line = json.dumps({"hash": digest, "ts": ts, "today": day, "session": session,
                               "portal": portal, "url": url, "title": title, "source": source,
                               "lines": lines}, ensure_ascii=False) + "\n"
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
