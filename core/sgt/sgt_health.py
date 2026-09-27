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
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LOOKBACK_DAYS = 30
USUAL_DAYS = 3
QUIET_ACTIVE_DAYS = 3
ACTIVE_READS = 30
BLIND_SHARE_ALERT = 0.5
BLIND_SHARE_USUAL = 0.1
SAVE_EVERY_SEC = 60.0
STATS_FILE = "spec_stats.json"

# Domain patterns for step 10 residue detection (unclaimed containers/typed values)
_DOMAIN_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("PAN", re.compile(r"(?<![0-9A-Za-z])([A-Z]{5}[0-9]{4}[A-Z]{1})(?![0-9A-Za-z])", re.IGNORECASE)),
    ("ARN", re.compile(r"(?<![0-9A-Za-z])([0-9OoIlSB]{15})(?![0-9A-Za-z])")),
    ("GSTIN", re.compile(r"(?<![0-9A-Za-z])([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9]{1}[Z]{1}[0-9]{1})(?![0-9A-Za-z])", re.IGNORECASE)),
    ("Aadhaar", re.compile(r"(?<![0-9])(\d{4}\s?\d{4}\s?\d{4})(?![0-9])")),
)


def _mask_shape(value: str) -> str:
    """Mask shape: letters -> A, digits -> 9, else unchanged."""
    return "".join("A" if ch.isalpha() else "9" if ch.isdigit() else ch for ch in value)


def _find_shaped_values(lines: Iterable[str]) -> Dict[str, int]:
    """Find all domain-typed values in lines, return {shape: count}."""
    shaped: Dict[str, int] = {}
    text = " ".join(str(ln) for ln in lines if ln)
    for type_name, pattern in _DOMAIN_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1)
            shape = _mask_shape(value)
            key = f"{type_name}:{shape}"
            shaped[key] = shaped.get(key, 0) + 1
    return shaped


def _extract_claimed_shapes(result: Any) -> Dict[str, int]:
    """Extract shapes of values claimed by Core specs from PageResult."""
    claimed: Dict[str, int] = {}
    if not result:
        return claimed

    # Convert PageResult to dict if needed
    as_dict = result.as_dict() if hasattr(result, "as_dict") else (result if isinstance(result, dict) else {})

    # Check profile hits
    for field, hit in (as_dict.get("profile") or {}).items():
        if isinstance(hit, dict) and "value" in hit:
            value = str(hit.get("value", ""))
            for type_name, pattern in _DOMAIN_PATTERNS:
                if pattern.search(value):
                    shape = _mask_shape(value)
                    key = f"{type_name}:{shape}"
                    claimed[key] = claimed.get(key, 0) + 1
                    break

    # Check current hits
    for field, hit in (as_dict.get("current") or {}).items():
        if isinstance(hit, dict) and "value" in hit:
            value = str(hit.get("value", ""))
            for type_name, pattern in _DOMAIN_PATTERNS:
                if pattern.search(value):
                    shape = _mask_shape(value)
                    key = f"{type_name}:{shape}"
                    claimed[key] = claimed.get(key, 0) + 1
                    break

    # Check datasets
    for dataset in (as_dict.get("datasets") or []):
        if isinstance(dataset, dict):
            for field, hit in (dataset.get("values") or {}).items():
                if hit:
                    value = str(hit)
                    for type_name, pattern in _DOMAIN_PATTERNS:
                        if pattern.search(value):
                            shape = _mask_shape(value)
                            key = f"{type_name}:{shape}"
                            claimed[key] = claimed.get(key, 0) + 1
                            break

    return claimed


def _detect_page_kind(url: str, title: str, lines: Iterable[str]) -> Optional[str]:
    """Guess page kind from URL, title, or content."""
    text = f"{url} {title} {' '.join(str(ln) for ln in list(lines)[:10])}".lower()

    if "gst" in text and ("confirmation" in text or "ack" in text or "acknowledgement" in text):
        return "gst_confirmation"
    if "gst" in text and ("return" in text or "filing" in text):
        return "gst_return"
    if "gst" in text and "dashboard" in text:
        return "gst_dashboard"

    if "itr" in text and ("filing" in text or "ack" in text or "acknowledgement" in text):
        return "itr_ack"
    if "itr" in text and ("return" in text or "form" in text):
        return "itr_return"
    if "itr" in text and ("profile" in text or "dashboard" in text):
        return "itr_profile"

    if "form" in text or "input" in text:
        return "form_page"
    if "list" in text or "table" in text or "record" in text:
        return "list_page"
    if "confirmation" in text or "success" in text or "submitted" in text:
        return "confirmation_page"

    return "other"


def compute_residues(lines: Iterable[str], result: Any, url: str, title: str) -> Optional[Tuple[str, Dict[str, int]]]:
    """Compute unclaimed residues. Returns (page_kind, {shape: count}) or None if no residues."""
    found = _find_shaped_values(lines)
    if not found:
        return None

    claimed = _extract_claimed_shapes(result)
    residues: Dict[str, int] = {}
    for shape_key, count in found.items():
        claimed_count = claimed.get(shape_key, 0)
        if claimed_count < count:
            residues[shape_key] = count - claimed_count

    if not residues:
        return None

    page_kind = _detect_page_kind(url, title, lines)
    if not page_kind:
        return None

    return page_kind, residues


class SpecStats:
    def __init__(self, directory: Optional[Path] = None, clock=None) -> None:
        import time as _time
        self._path = (directory / STATS_FILE) if directory else None
        self._clock = clock or _time.time
        self._last_save = 0.0
        self._dirty = False
        # {"reads": {portal: {day: [reads, ocr]}}, "hits": {portal: {spec: {day: n}}},
        #  "residues": {portal: {page_kind: {shape: count}}}, "alerted": {key: day}}
        self.data: Dict[str, Any] = {"reads": {}, "hits": {}, "residues": {}, "alerted": {}}
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

    def record_residues(self, portal: str, page_kind: str, residues: Dict[str, int]) -> None:
        """Record unclaimed containers/typed values (residues) per page kind and shape.
        residues: {masked_shape: count} - e.g. {"9999 9999 9999": 1, "A{15}": 2}
        """
        if not residues:
            return
        per_portal = self.data["residues"].setdefault(portal or "?", {})
        per_kind = per_portal.setdefault(page_kind, {})
        for shape, count in residues.items():
            per_kind[shape] = per_kind.get(shape, 0) + count
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
        """Per portal over the last `days`: pages read, OCR share, each spec's hit count, and residues."""
        cutoff = (today - timedelta(days=days)).isoformat()
        out: Dict[str, Any] = {}
        for portal, per_day in self.data["reads"].items():
            reads = sum(r for d, (r, _) in per_day.items() if d >= cutoff)
            ocr = sum(o for d, (_, o) in per_day.items() if d >= cutoff)
            hits = {spec: sum(n for d, n in days_.items() if d >= cutoff)
                    for spec, days_ in self.data["hits"].get(portal, {}).items()}
            residues = self.data["residues"].get(portal, {})
            out[portal] = {"pages": reads, "ocr_share": round(ocr / reads, 3) if reads else 0.0,
                           "spec_hits": dict(sorted(hits.items(), key=lambda kv: -kv[1])),
                           "residues": residues}
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
                for k in ("reads", "hits", "residues", "alerted"):
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
