"""
core/sgt/sgt_replay.py - running recorded pages through SGT again
=================================================================
Feeds recorded pages (sgt_corpus.py, or the fictional golden pages under tests/sgt_golden/) to a
fresh SgtShadow, one recorded session at a time, with the recorded time and date. Nothing real is
touched: no tracker, no crash snapshot, no page recording, and the SGT log goes to a temporary
folder. The result is what SGT would have written - so a spec change can be compared with the
result before it (diff()) before it goes live.
"""

import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .sgt_corpus import group_sessions
from .sgt_shadow import SgtShadow
from .sgt_specs import SpecStore

ROW_FIELDS = ("pan", "gstin", "client_name", "filing_type", "period_label", "arn", "status", "filing_preference")


class _FakeFrame:
    """Stands in for the screenshot: SGT only hashes it (a failed hash = "re-read")."""

    def convert(self, *_a, **_k):
        raise ValueError("replay has no screenshot")


class _FakeOcr:
    def __init__(self) -> None:
        self.lines: List[str] = []

    def scan_image(self, _frame, region_type="full"):
        return {"lines": list(self.lines)}


def replay_session(pages: List[Dict[str, Any]], store: Optional[SpecStore] = None) -> Dict[str, Any]:
    """One recorded session -> {"rows": {dataset_key: {...}}, "sessions": [...], "held": [...]}."""
    store = store or SpecStore()
    state = {"uia": [], "ts": float(pages[0].get("ts") or 0) if pages else 0.0,
             "today": date.fromisoformat(pages[0]["today"]) if pages and pages[0].get("today") else date.today()}
    ocr = _FakeOcr()
    with tempfile.TemporaryDirectory() as tmp:
        sgt = SgtShadow(store=store, read_uia=lambda h: {"lines": list(state["uia"])}, log_dir=Path(tmp),
                        clock=lambda: state["ts"], today=lambda: state["today"], echo=lambda m: None)
        rows: Dict[str, Dict[str, Any]] = {}

        def collect():
            for row in sgt.drain():
                if row.get("supersedes_dataset_key"):
                    rows.pop(row["supersedes_dataset_key"], None)
                rows[row["dataset_key"]] = {k: row.get(k, "") for k in ROW_FIELDS}

        for page in pages:
            lines = list(page.get("lines") or [])
            if page.get("source") == "ocr":
                state["uia"], ocr.lines = [], lines
            else:
                state["uia"], ocr.lines = lines, []
            state["ts"] = max(state["ts"], float(page.get("ts") or state["ts"])) + 0.001
            if page.get("today"):
                state["today"] = date.fromisoformat(page["today"])
            sgt.observe(1, page.get("portal") or "", page.get("url") or "", frame=_FakeFrame(),
                        ocr=ocr, title=page.get("title") or "")
            collect()
        sgt.end_all("replay end")
        collect()
        sessions, held = [], []
        log = Path(tmp)
        for f in sorted(log.glob("*.jsonl")):
            for raw in f.read_text(encoding="utf-8").splitlines():
                ev = json.loads(raw)
                if ev.get("event") == "session_end":
                    p = ev.get("payload") or {}
                    sessions.append({"reason": ev.get("reason"), "profile": p.get("client_profile"),
                                     "identity": p.get("identity"), "datasets": len(p.get("datasets") or [])})
                elif ev.get("event") == "dataset" and str(ev.get("change", "")).startswith("held"):
                    held.append({"values": ev.get("values"), "problems": ev.get("problems")})
    return {"rows": rows, "sessions": sessions, "held": held}


def replay(pages: Iterable[Dict[str, Any]], store: Optional[SpecStore] = None) -> Dict[str, Dict[str, Any]]:
    """Every recorded session, replayed on its own: {session_id: replay_session(...)}."""
    store = store or SpecStore()
    return {sid: replay_session(group, store) for sid, group in group_sessions(pages).items()}


def diff(before: Dict[str, Dict[str, Any]], after: Dict[str, Dict[str, Any]]) -> List[str]:
    """Human-readable differences in what SGT would write, per session."""
    out: List[str] = []
    for sid in sorted(set(before) | set(after)):
        a = (before.get(sid) or {}).get("rows", {})
        b = (after.get(sid) or {}).get("rows", {})
        for key in sorted(set(a) - set(b)):
            out.append(f"[{sid}] no longer written: {key} {_short(a[key])}")
        for key in sorted(set(b) - set(a)):
            out.append(f"[{sid}] newly written:     {key} {_short(b[key])}")
        for key in sorted(set(a) & set(b)):
            changed = {f: (a[key].get(f), b[key].get(f)) for f in ROW_FIELDS if a[key].get(f) != b[key].get(f)}
            if changed:
                out.append(f"[{sid}] changed:           {key} "
                           + ", ".join(f"{f}: {x!r} -> {y!r}" for f, (x, y) in changed.items()))
        ha = len((before.get(sid) or {}).get("held", []))
        hb = len((after.get(sid) or {}).get("held", []))
        if ha != hb:
            out.append(f"[{sid}] held datasets: {ha} -> {hb}")
    return out


def _short(row: Dict[str, Any]) -> str:
    return "(" + ", ".join(f"{k}={v}" for k, v in row.items() if v) + ")"
