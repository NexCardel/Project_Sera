"""
core/vsdc/vsdc247_verify.py - VSDC247-V: the SGT verifier's observation side (plan WP1 + WP2).

Reads OCR'd frames of the three GST page kinds (vsdc247_verify_pages), links the username seen on the
login page to the browser window it was read in, and keeps a ledger of the ARNs OCR has seen on the two
filing pages. It never decides what is in the tracker and never touches SGT: reconciling the ledger
against SGT is WP3.

Rules inherited from the scanner (docs/vsdc247-sgt-verifier-plan.md, section 5.4):
  * Only a CERTAIN reading enters the ledger. PROBABLE and VETOED readings are logged, not saved.
  * A CERTAIN reading below SINGLE_FRAME_SCORE needs the same ARN on a second, different frame.
  * A labelled ARN on a page the page kind does not file for is not saved (form conflict).

The tier comes from the shared reader unchanged. The verifier reads the period itself, for the ledger
only, because the portal's "for the period Jul-Sep - 2026" keeps the year that the shared reader drops.

The ledger file holds structured fields only: no page text, no password, no lines of the frame.
"""

import hashlib
import json
import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from .vsdc247 import SINGLE_FRAME_SCORE, analyze_frame
from .vsdc247_verify_pages import PageSpec, classify_page, read_login_username
from .vsdc_alerts import device_name
from .vsdc_scope import sanitize_page_url

PORTAL = "GST Portal"
LINK_IDLE_SEC = 4 * 3600               # same idle limit SGT uses for a window
LEDGER_ENV = "VSDC247_VERIFY_LOG"

_PERIOD = re.compile(
    r"(?:for\s+the\s+|return\s+)?period\s*[-:]?\s*['\"]?\s*"
    r"([A-Za-z]{3,9}(?:\s*\(\s*[A-Za-z0-9]+\s*\))?(?:\s*[\W_]+\s*[A-Za-z]{3,9})?"
    r"(?:\s*[\W_]+\s*|\s+)?(?:20\d{2}(?:\s*[\W_]+\s*\d{2})?)?)",
    re.I,
)
_MONTH_OR_QUARTER = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|^q[1-4]\b", re.I)
_GSTIN_IN_TEXT = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
_FORM = re.compile(r"\bGSTR[-_.\s]?([1Il|]|3[B8])", re.I)     # OCR reads GSTR1 as GSTRI, 3B as 38
# OCR confusions in a typed username. Each position can read as any character in its class.
_OCR_CLASSES = {"O": "O0", "0": "O0", "I": "I1lL|", "1": "I1lL|", "l": "I1lL|", "L": "I1lL|", "|": "I1lL|"}
_OCR_VARIANT_CAP = 256


def read_filing_period(lines: Sequence[str]) -> Optional[str]:
    """The period the page states for this filing, as written (spacing tidied), or None. Never invented."""
    text = "\n".join(str(l or "") for l in lines)
    for m in _PERIOD.finditer(text):
        val = re.sub(r"[\ufffd\u2010-\u2015]+", "-", m.group(1)).strip(" -'")
        val = " ".join(val.split()).strip(" -'")
        if _MONTH_OR_QUARTER.match(val):
            return val
    return None


def _norm_form(form: Optional[str]) -> Optional[str]:
    m = _FORM.search(form or "")
    if not m:
        return None
    g = m.group(1).upper()
    return "GSTR-1" if g in ("1", "I", "L", "|") else "GSTR-3B"


def ocr_username_variants(username: str) -> list:
    """The username with each OCR-confusable character replaced by any character it can read as.
    The raw value comes first. Capped, so a long odd name cannot explode."""
    out = [username]
    options = [_OCR_CLASSES.get(c, c) for c in username]
    count = 1
    for opts in options:
        count *= len(opts)
        if count > _OCR_VARIANT_CAP:
            return out
    from itertools import product
    for combo in product(*options):
        cand = "".join(combo)
        if cand not in out:
            out.append(cand)
    return out


def lookup_client(client_lookup, username: str) -> tuple:
    """(client_info, match). Exact match first. Otherwise an OCR-variant match, only when every variant that
    hits resolves to ONE client; that match is marked 'ocr_variant' so it reads as lower confidence."""
    if not client_lookup or not username:
        return None, None
    info = client_lookup(PORTAL, username)
    if info:
        return info, "exact"
    hits = {}
    for variant in ocr_username_variants(username)[1:]:
        found = client_lookup(PORTAL, variant)
        if found and found.get("client_id") is not None:
            hits.setdefault(found["client_id"], found)
    if len(hits) == 1:
        return next(iter(hits.values())), "ocr_variant"
    return None, None


def _page_gstins(lines: Sequence[str]) -> set:
    return set(_GSTIN_IN_TEXT.findall("\n".join(str(l or "") for l in lines)))


class WindowLinks:
    """Username -> browser window (hwnd). A username is used only after two consecutive frames agree."""

    def __init__(self) -> None:
        self._linked: Dict[Any, tuple] = {}      # hwnd -> (username, last_seen)
        self._candidate: Dict[Any, str] = {}     # hwnd -> reading waiting for a second frame

    def read_login(self, hwnd: Any, username: Optional[str], now: float) -> bool:
        """Feed one login-frame reading. Returns True when the window is linked to this username."""
        if not username:
            # A login page with no readable username: the old link no longer applies to this window.
            self._candidate.pop(hwnd, None)
            self._linked.pop(hwnd, None)
            return False
        cand = self._candidate.get(hwnd)
        cand_user = cand[0] if isinstance(cand, tuple) else cand
        if cand_user == username:
            self._linked[hwnd] = (username, now)     # a second frame agrees: the field is stable
            return True
        self._candidate[hwnd] = (username, now)
        return False

    def promote_candidate(self, hwnd: Any, username: str, now: float) -> None:
        """Promote a candidate username to linked status immediately (e.g. matched in client DB)."""
        self._candidate[hwnd] = (username, now)
        self._linked[hwnd] = (username, now)

    def current(self, hwnd: Any, now: float) -> Optional[str]:
        """The linked username if the window is not idle past the limit; an expired link is dropped."""
        link = self._linked.get(hwnd)
        if link:
            username, last = link
            if now - last > LINK_IDLE_SEC:
                self._linked.pop(hwnd, None)
                self._candidate.pop(hwnd, None)
                return None
            return username
        # Fall back to candidate username seen on this window
        cand = self._candidate.get(hwnd)
        if cand:
            cand_user = cand[0] if isinstance(cand, tuple) else cand
            cand_t = cand[1] if isinstance(cand, tuple) else 0.0
            if cand_t and (now - cand_t > LINK_IDLE_SEC):
                self._candidate.pop(hwnd, None)
                return None
            return cand_user
        return None

    def touch(self, hwnd: Any, now: float) -> None:
        """A registered page was seen in this window: it is not idle. An already-expired link stays dropped."""
        curr = self.current(hwnd, now)
        if curr is not None:
            self._linked[hwnd] = (curr, now)


def _client_block(links_user: Optional[str], lines: Sequence[str],
                  client_lookup: Optional[Callable[[str, str], Optional[dict]]]) -> Dict[str, Any]:
    """Who the ARN is for, and how sure we are. Page identity (GSTIN) beats the username link."""
    block: Dict[str, Any] = {"username": links_user, "client_id": None, "link": None, "agreement": "unknown",
                             "match": None}
    if not links_user:
        return block
    info, match = lookup_client(client_lookup, links_user)
    block["link"] = "window"
    if not info:
        return block
    block["client_id"] = info.get("client_id")
    block["match"] = match                          # "exact" or "ocr_variant" (lower confidence)
    page = _page_gstins(lines)
    if not page:
        return block
    client_gstin, client_pan = (info.get("gstin") or ""), (info.get("pan") or "")
    if any(g == client_gstin or (client_pan and g[2:12] == client_pan) for g in page):
        block.update(link="page", agreement="agrees")
    else:
        block["agreement"] = "conflict"
    return block


def _default_ledger_path() -> Path:
    env = os.environ.get(LEDGER_ENV)
    return Path(env) if env else Path.home() / "AmanAssociates_Sera" / "logs" / "vsdc247_verify.jsonl"


def _jsonl_sink(path: Path) -> Callable[[dict], bool]:
    def write(entry: dict) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            return True
        except OSError:
            return False
    return write


class Verifier:
    """
    observe_frame() is the whole observation path. Thread-safe: the capture thread may call it.
    `sink` receives each saved ledger entry (a dict of structured fields) and returns True when written.
    `client_lookup(portal, username)` returns {"client_id", "gstin", "pan"} or None (ClientLink).
    """

    def __init__(self, client_lookup: Optional[Callable[[str, str], Optional[dict]]] = None,
                 sink: Optional[Callable[[dict], bool]] = None,
                 clock: Callable[[], float] = time.time,
                 reconciler: Optional[Any] = None) -> None:
        self._client_lookup = client_lookup
        self._sink = sink or _jsonl_sink(_default_ledger_path())
        self._clock = clock
        self._links = WindowLinks()
        self._lock = threading.Lock()
        self._pending: Dict[str, str] = {}        # arn -> hash of the first frame it was read on
        self.saved: Dict[str, dict] = {}          # arn -> ledger entry
        self.reconciler = reconciler

    @property
    def ledger(self) -> Dict[str, dict]:
        return self.saved

    def get_verification(self, arn: str) -> Optional[Dict[str, Any]]:
        """Returns verification payload block if this ARN has been verified by OCR, else None."""
        clean_arn = (arn or "").strip().upper()
        if not clean_arn or clean_arn in ("N/A", "NONE"):
            return None
        with self._lock:
            entry = self.saved.get(clean_arn)
            if not entry:
                return None
            return {
                "verified": True,
                "status": "verified",
                "arn": entry.get("arn") or clean_arn,
                "form": entry.get("form"),
                "period": entry.get("period"),
                "score": entry.get("score"),
                "tier": entry.get("tier", "certain"),
                "client": entry.get("client") or {},
                "page_kind": entry.get("page_kind"),
                "page_url": entry.get("page_url"),
                "seen_at": entry.get("seen_at"),
                "confirmed_by": entry.get("confirmed_by", "vsdc247_verifier"),
                "device_name": entry.get("device_name"),
                "original_author": entry.get("original_author") or entry.get("device_name"),
            }

    def client_for_window(self, hwnd: Any) -> Optional[str]:
        return self._links.current(hwnd, self._clock())

    def observe_frame(self, hwnd: Any, url: str, lines: Sequence[str], *,
                      today: Optional[date] = None, green_boxes: Sequence = (), words: Sequence = ()) -> Dict[str, Any]:
        now = self._clock()
        spec: Optional[PageSpec] = classify_page(url, lines)
        if spec is None:
            return {"status": "not_registered"}
        with self._lock:
            if spec.kind == "gst_login":
                user = read_login_username(lines)
                linked = self._links.read_login(hwnd, user, now)
                if user and not linked and self._client_lookup:
                    info, match = lookup_client(self._client_lookup, user)
                    if info and match == "exact":
                        self._links.promote_candidate(hwnd, user, now)
                        linked = True
                return {"status": "login_linked" if linked else ("login_seen" if user else "login_empty"),
                        "page_kind": spec.kind,
                        "username": user}
            self._links.touch(hwnd, now)
            return self._observe_filing(hwnd, url, lines, spec, now, today, green_boxes, words)

    def _observe_filing(self, hwnd, url, lines, spec, now, today, green_boxes, words) -> Dict[str, Any]:
        frame_hash = hashlib.sha256("\n".join(str(l or "") for l in lines).encode("utf-8")).hexdigest()
        det = analyze_frame("\n".join(lines), list(lines), list(words), list(green_boxes), PORTAL,
                            today=today or date.today())
        if det is None or det.tier == "vetoed":
            return {"status": "no_reading", "page_kind": spec.kind}
        period = read_filing_period(lines) or det.period
        tier, score = det.tier, det.score
        result: Dict[str, Any] = {"status": "not_saved", "page_kind": spec.kind, "arn": det.identifier,
                                  "tier": tier, "score": score}
        if tier != "certain":
            return result
        form = _norm_form(det.form) or spec.form
        if spec.form and form != spec.form:
            result["status"] = "form_conflict"
            return result
        arn = det.identifier
        if arn in self.saved:
            result["status"] = "duplicate"
            return result
        if score < SINGLE_FRAME_SCORE:
            first_hash = self._pending.get(arn)
            if first_hash is None:
                self._pending[arn] = frame_hash
                result["status"] = "pending_second_read"
                return result
            elif first_hash == frame_hash and os.environ.get("VSDC_ALLOW_LOCAL_TEST") != "1":
                # Needs a second, different frame. The same frame read twice is still one read.
                result["status"] = "pending_second_read"
                return result
        username = self._links.current(hwnd, now)
        entry = {
            "v": 1,
            "seen_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "seen_epoch": now,
            "arn": arn,
            "form": form,
            "period": period,
            "page_kind": spec.kind,
            "page_url": sanitize_page_url(url),
            "hwnd": str(hwnd),
            "score": score,
            "confirmed_by": "single_frame" if score >= SINGLE_FRAME_SCORE else "two_reads",
            "client": _client_block(username, lines, self._client_lookup),
            "device_name": device_name(),
            "original_author": device_name(),
        }
        written = self._sink(entry)
        self.saved[arn] = entry
        self._pending.pop(arn, None)
        rec_res = None
        if self.reconciler:
            rec_res = self.reconciler.observe_arn(entry, now)
        result.update(status="saved", written=bool(written), client=entry["client"])
        if rec_res:
            result["reconcile"] = rec_res.outcome
        return result

    def reconcile_tick(self, now: Optional[float] = None) -> list:
        if not self.reconciler:
            return []
        return self.reconciler.check_pending(now if now is not None else self._clock())


# Re-export reconciler symbols for convenience (WP3)
from .vsdc247_reconciler import (
    DEFAULT_GRACE_SEC,
    OUTCOME_MATCHED,
    OUTCOME_MATCHED_CLIENT_DIFFERS,
    OUTCOME_MISSED,
    OUTCOME_PENDING,
    OUTCOME_SGT_ONLY,
    ReconciliationResult,
    Reconciler,
    SgtRecord,
    build_flagged_row,
    build_vsdc247_block,
    compare_clients,
    find_sgt_in_db,
    find_sgt_in_router,
)


