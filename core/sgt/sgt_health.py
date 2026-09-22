"""
core/sgt/sgt_health.py - noticing that a portal changed
=======================================================
Portals change their wording and their pages without notice. SGT then does not fail loudly: a
spec simply stops matching and SGT goes quiet. This keeps, per portal and per day, how many pages
were read (and how many had to be OCR'd because UI Automation was blind) and how many times each
spec matched, and raises an alert when:

  a spec went quiet   it matched on at least USUAL_DAYS of the last LOOKBACK_DAYS days, and has
                      not matched once on the last QUIET_ACTIVE_DAYS days the portal was really
                      used (ACTIVE_READS pages or more) - the portal probably reworded that page;
  a portal went blind today at least BLIND_SHARE_ALERT of the pages had to be OCR'd, where it
                      used to be under BLIND_SHARE_USUAL - the portal probably moved to canvas
                      rendering (as the new TRACES did).

Kept in ~/AmanAssociates_Sera/sgt_shadow/spec_stats.json - counts only, no client data.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LOOKBACK_DAYS = 30
USUAL_DAYS = 3
QUIET_ACTIVE_DAYS = 3
ACTIVE_READS = 30
BLIND_SHARE_ALERT = 0.5
BLIND_SHARE_USUAL = 0.1
SAVE_EVERY_SEC = 60.0
STATS_FILE = "spec_stats.json"


class SpecStats:
    def __init__(self, directory: Optional[Path] = None, clock=None) -> None:
        import time as _time
        self._path = (directory / STATS_FILE) if directory else None
        self._clock = clock or _time.time
        self._last_save = 0.0
        self._dirty = False
        # {"reads": {portal: {day: [reads, ocr]}}, "hits": {portal: {spec: {day: n}}}, "alerted": {key: day}}
        self.data: Dict[str, Any] = {"reads": {}, "hits": {}, "alerted": {}}
        self._load()

    # ── recording ──────────────────────────────────────────────────────────────
    def record_read(self, portal: str, source: str, today: date) -> None:
        day = today.isoformat()
        cell = self.data["reads"].setdefault(portal or "?", {}).setdefault(day, [0, 0])
        cell[0] += 1
        if source == "ocr":
            cell[1] += 1
        self._touch()

    def record_hits(self, portal: str, spec_names: Iterable[str], today: date) -> None:
        day = today.isoformat()
        per = self.data["hits"].setdefault(portal or "?", {})
        for name in set(spec_names):
            if name:
                days = per.setdefault(name, {})
                days[day] = days.get(day, 0) + 1
        self._touch()

    # ── checking ───────────────────────────────────────────────────────────────
    def alerts(self, today: date) -> List[Dict[str, str]]:
        """The alerts not yet raised today. Each: {kind, portal, spec?, detail}."""
        out: List[Dict[str, str]] = []
        cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
        tday = today.isoformat()
        for portal, per_day in self.data["reads"].items():
            active = sorted((d for d, (reads, _) in per_day.items() if reads >= ACTIVE_READS and d >= cutoff),
                            reverse=True)
            recent = active[:QUIET_ACTIVE_DAYS]
            if len(recent) == QUIET_ACTIVE_DAYS:
                for spec, days in self.data["hits"].get(portal, {}).items():
                    hit_days = [d for d in days if d >= cutoff]
                    earlier = [d for d in hit_days if d < recent[-1]]
                    if len(earlier) >= USUAL_DAYS and not any(days.get(d) for d in recent):
                        out.append({"kind": "spec_quiet", "portal": portal, "spec": spec,
                                    "detail": f"'{spec}' matched on {len(earlier)} earlier days but not once on "
                                              f"the last {QUIET_ACTIVE_DAYS} busy days on {portal} - "
                                              f"the portal may have changed that page"})
            reads, ocr = per_day.get(tday, [0, 0])
            usual = [o / r for d, (r, o) in per_day.items() if d < tday and d >= cutoff and r >= ACTIVE_READS]
            if reads >= ACTIVE_READS and ocr / reads >= BLIND_SHARE_ALERT and usual \
                    and max(usual) < BLIND_SHARE_USUAL:
                out.append({"kind": "portal_blind", "portal": portal, "spec": "",
                            "detail": f"{round(100 * ocr / reads)}% of {portal} pages had to be read by OCR today "
                                      f"(usually under {round(100 * BLIND_SHARE_USUAL)}%) - the portal may have "
                                      f"changed how it draws its pages"})
        fresh = []
        for a in out:
            key = f"{a['kind']}|{a['portal']}|{a['spec']}"
            if self.data["alerted"].get(key) != tday:
                self.data["alerted"][key] = tday
                fresh.append(a)
        if fresh:
            self._touch(force=True)
        return fresh

    def summary(self, today: date, days: int = 7) -> Dict[str, Any]:
        """Per portal over the last `days`: pages read, OCR share, and each spec's hit count."""
        cutoff = (today - timedelta(days=days)).isoformat()
        out: Dict[str, Any] = {}
        for portal, per_day in self.data["reads"].items():
            reads = sum(r for d, (r, _) in per_day.items() if d >= cutoff)
            ocr = sum(o for d, (_, o) in per_day.items() if d >= cutoff)
            hits = {spec: sum(n for d, n in days_.items() if d >= cutoff)
                    for spec, days_ in self.data["hits"].get(portal, {}).items()}
            out[portal] = {"pages": reads, "ocr_share": round(ocr / reads, 3) if reads else 0.0,
                           "spec_hits": dict(sorted(hits.items(), key=lambda kv: -kv[1]))}
        return out

    # ── persistence ────────────────────────────────────────────────────────────
    def _touch(self, force: bool = False) -> None:
        self._dirty = True
        now = self._clock()
        if force or now - self._last_save >= SAVE_EVERY_SEC:
            self.save()

    def save(self) -> None:
        if not self._path or not self._dirty:
            return
        try:
            self._prune()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(self._path)
            self._dirty = False
            self._last_save = self._clock()
        except OSError:
            pass

    def _load(self) -> None:
        if not self._path:
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k in ("reads", "hits", "alerted"):
                    if isinstance(loaded.get(k), dict):
                        self.data[k] = loaded[k]
        except (OSError, ValueError):
            pass

    def _prune(self) -> None:
        cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS * 2)).isoformat()
        for per_day in self.data["reads"].values():
            for d in [d for d in per_day if d < cutoff]:
                del per_day[d]
        for per in self.data["hits"].values():
            for days in per.values():
                for d in [d for d in days if d < cutoff]:
                    del days[d]
