"""
core/vsdc/vsdc247_reconciler.py - VSDC247-V: SGT lookup, reconciler, and outcomes (plan WP3).

Reconciles the OCR ledger entries (vsdc247_verify) against SGT captures (plan section 6.2–6.3).
Lookup order (plan 6.2):
  1. SGT in-memory outbox / open session slots (cheap, immediate).
  2. tracker_dump rows with capture_method LIKE 'SGT%' and matching arn_number (read-only).

Outcomes (plan 6.3):
  - matched: SGT row exists with this ARN; client agrees (or both unknown).
  - matched_client_differs: same ARN, SGT names a different client than the username link.
  - missed: no SGT row with this ARN after the grace period (default 90 s).
  - sgt_only: SGT row with an ARN OCR never saw (informational block, no alert).

Late SGT arrival (plan 6.5):
  If SGT later delivers the same ARN after 'missed' was declared, outcome is updated to
  matched with late=True, sgt_delay_sec=N, and remove_flagged_row=True.
"""

from dataclasses import dataclass, field
import datetime
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .vsdc_alerts import device_name

DEFAULT_GRACE_SEC = 90.0
VERIFIER_VERSION = 1

ACCOUNTANT_LOG_ENV = "VSDC_ACCOUNTANT_LOG"


def _default_accountant_log_path() -> Path:
    env = os.environ.get(ACCOUNTANT_LOG_ENV) or os.environ.get("GST_RECONCILIATION_LOG")
    return Path(env) if env else Path.home() / "AmanAssociates_Sera" / "logs" / "gst_reconciliation.txt"

OUTCOME_MATCHED = "matched"
OUTCOME_MATCHED_CLIENT_DIFFERS = "matched_client_differs"
OUTCOME_MISSED = "missed"
OUTCOME_SGT_ONLY = "sgt_only"
OUTCOME_PENDING = "pending_grace"

_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


@dataclass
class SgtRecord:
    arn: str
    pan: Optional[str] = None
    gstin: Optional[str] = None
    client_name: Optional[str] = None
    client_id: Optional[Union[int, str]] = None
    form: Optional[str] = None
    period: Optional[str] = None
    captured_at: Optional[float] = None
    source: str = ""                         # "sgt_outbox", "sgt_session", "tracker_dump"
    row_id: Optional[int] = None
    dataset_key: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReconciliationResult:
    outcome: str                             # OUTCOME_* constant
    arn: str
    block: Optional[Dict[str, Any]] = None   # vsdc247 payload block
    sgt_record: Optional[SgtRecord] = None
    entry: Optional[Dict[str, Any]] = None   # OCR ledger entry
    client_agreement: str = "unknown"        # "agrees", "conflict", "unknown"
    sgt_delay_sec: int = 0
    late: bool = False
    alert: bool = False
    alert_kind: Optional[str] = None         # "client_differs", "sgt_missed"
    remove_flagged_row: bool = False
    flagged_row: Optional[Dict[str, Any]] = None


def append_accountant_reconciliation_log(
    outcome: str,
    arn: str,
    form: Optional[str] = None,
    period: Optional[str] = None,
    username: Optional[str] = None,
    client_name: Optional[str] = None,
    filing_pc: Optional[str] = None,
    sgt_delay_sec: int = 0,
    sgt_client: Optional[str] = None,
    sgt_pan: Optional[str] = None,
    late: bool = False,
    seen_at: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
) -> bool:
    """
    Appends a human-readable, non-technical plain text entry for accountants
    to reconcile GST returns and quickly spot SGT misses or mismatches.
    """
    try:
        is_anomaly = (
            outcome == OUTCOME_MISSED
            or late
            or outcome == OUTCOME_MATCHED_CLIENT_DIFFERS
        )
        if not is_anomaly and (outcome or arn):
            return False

        target_path = Path(path) if path else _default_accountant_log_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        form_str = _clean_str(form) or "GST Return"
        period_str = _clean_str(period) or "N/A"
        pc_str = _clean_str(filing_pc) or device_name()
        arn_str = (arn or "UNKNOWN").strip().upper()

        clean_cname = _clean_str(client_name)
        clean_user = _clean_str(username)
        if clean_cname and clean_user and clean_cname != clean_user:
            client_info = f"{clean_cname} ({clean_user})"
        else:
            client_info = clean_cname or clean_user or "Unknown Client"

        lines = []

        # If file is empty or brand new, write initial banner
        if not target_path.exists() or target_path.stat().st_size == 0:
            lines.append("=" * 80)
            lines.append("SERA GST FILING RECONCILIATION LOG")
            lines.append("Plain text audit trail for accountants to reconcile office GST filings.")
            lines.append(f"Initialized: {now_str}")
            lines.append("=" * 80)
            lines.append("")

        if outcome == OUTCOME_MISSED:
            lines.append(f"[{now_str}] [WARNING] SGT MISSED RETURN - CAPTURED BY OCR ONLY")
            lines.append("-" * 80)
            lines.append(f"  ARN:             {arn_str}")
            lines.append(f"  Form:            {form_str}")
            lines.append(f"  Return Period:   {period_str}")
            lines.append(f"  Portal Login:    {client_info}")
            lines.append(f"  Filing PC:       {pc_str}")
            if seen_at:
                lines.append(f"  Portal Seen At:  {seen_at}")
            lines.append("  Summary:         Return was submitted on GST Portal, but SGT did not capture it.")
            lines.append(f"  Action Required: Flagged in Tracker Dump as 'Pending_{arn_str}'.")
            lines.append("                   Please verify the client in Sera and assign the return.")
            lines.append("-" * 80)
            lines.append("")

        elif late:
            lines.append(f"[{now_str}] [RESOLVED] SGT LATE ARRIVAL")
            lines.append("-" * 80)
            lines.append(f"  ARN:             {arn_str}")
            lines.append(f"  Form:            {form_str}")
            lines.append(f"  Return Period:   {period_str}")
            lines.append(f"  Client:          {client_info}")
            lines.append(f"  Filing PC:       {pc_str}")
            lines.append(f"  SGT Delay:       {sgt_delay_sec} seconds")
            lines.append("  Summary:         SGT capture arrived late after the initial 90s grace period.")
            lines.append(f"  Action Required: None. Temporary row 'Pending_{arn_str}' has been cleared automatically.")
            lines.append("-" * 80)
            lines.append("")

        elif outcome == OUTCOME_MATCHED_CLIENT_DIFFERS:
            lines.append(f"[{now_str}] [ATTENTION] CLIENT MISMATCH")
            lines.append("-" * 80)
            lines.append(f"  ARN:             {arn_str}")
            lines.append(f"  Form:            {form_str}")
            lines.append(f"  Return Period:   {period_str}")
            sgt_str = _clean_str(sgt_client) or "Unknown Client"
            pan_part = f" (PAN: {sgt_pan})" if sgt_pan else ""
            lines.append(f"  SGT Client:      {sgt_str}{pan_part}")
            lines.append(f"  Portal User:     {client_info}")
            lines.append(f"  Filing PC:       {pc_str}")
            lines.append("  Summary:         SGT captured this under a different client than the GST portal login.")
            lines.append("  Action Required: Review Tracker Dump to confirm correct client assignment.")
            lines.append("-" * 80)
            lines.append("")

        if lines:
            with target_path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            return True
        return False
    except Exception:
        return False


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.upper() not in ("N/A", "NA", "NONE", "") else None


def _extract_pan(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    s = val.strip().upper()
    m = _PAN_RE.search(s)
    return m.group(0) if m else None


def _extract_gstin(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    s = val.strip().upper()
    m = _GSTIN_RE.search(s)
    return m.group(0) if m else None


def _parse_epoch(entry: Dict[str, Any], default: float) -> float:
    if "seen_epoch" in entry and isinstance(entry["seen_epoch"], (int, float)):
        return float(entry["seen_epoch"])
    seen_at = entry.get("seen_at")
    if seen_at and isinstance(seen_at, str):
        try:
            dt = datetime.datetime.fromisoformat(seen_at)
            return dt.timestamp()
        except Exception:
            pass
    return default


def find_sgt_in_router(sgt_router: Any, arn: str) -> Optional[SgtRecord]:
    """Look up an ARN in an active SgtShadow/SgtRouter's in-memory outbox and session slots."""
    if not sgt_router or not arn:
        return None
    clean_arn = arn.strip().upper()

    # 1. Check outbox
    outbox = getattr(sgt_router, "_outbox", None)
    if outbox:
        for s, slot in list(outbox.values()):
            slot_arn = _clean_str(getattr(slot, "arn", None) or slot.values.get("arn"))
            if slot_arn and slot_arn.upper() == clean_arn:
                return _build_sgt_record_from_session_slot(s, slot, source="sgt_outbox")

    # 2. Check active sessions and shelf
    sessions = list(getattr(sgt_router, "_sessions", {}).values())
    shelf = getattr(sgt_router, "_shelf", {})
    if isinstance(shelf, dict):
        for s_list in shelf.values():
            if isinstance(s_list, list):
                sessions.extend(s_list)

    for s in sessions:
        for slot in getattr(s, "slots", []):
            slot_arn = _clean_str(getattr(slot, "arn", None) or slot.values.get("arn"))
            if slot_arn and slot_arn.upper() == clean_arn:
                return _build_sgt_record_from_session_slot(s, slot, source="sgt_session")

    return None


def _build_sgt_record_from_session_slot(s: Any, slot: Any, source: str) -> SgtRecord:
    prof = getattr(s, "profile", {}) or {}
    name = prof.get("name", {}).get("value") if isinstance(prof.get("name"), dict) else prof.get("name")
    pan = prof.get("pan", {}).get("value") if isinstance(prof.get("pan"), dict) else prof.get("pan")
    gstin = prof.get("gstin", {}).get("value") if isinstance(prof.get("gstin"), dict) else prof.get("gstin")
    form = slot.values.get("form")
    period = slot.values.get("period")
    captured_at = getattr(s, "last_seen", None) or getattr(s, "started", None)

    return SgtRecord(
        arn=getattr(slot, "arn", None) or slot.values.get("arn", ""),
        pan=_extract_pan(pan),
        gstin=_extract_gstin(gstin),
        client_name=_clean_str(name),
        form=_clean_str(form),
        period=_clean_str(period),
        captured_at=captured_at,
        source=source,
    )


def find_sgt_in_db(db: Any, arn: str) -> Optional[SgtRecord]:
    """Look up an ARN in tracker_dump rows where capture_method LIKE 'SGT%' (read-only)."""
    if not db or not arn:
        return None
    clean_arn = arn.strip().upper()

    def _query(conn):

        cur = conn.execute(
            "SELECT id, dataset_key, unassigned_identity, arn_number, capture_method, "
            "raw_payload_json, created_at, client_id "
            "FROM tracker_dump WHERE capture_method LIKE 'SGT%' AND UPPER(TRIM(arn_number)) = ? "
            "ORDER BY id DESC LIMIT 1",
            (clean_arn,),
        )
        row = cur.fetchone()
        if not row:
            return None

        (row_id, dataset_key, unassigned_identity, arn_number,
         capture_method, raw_payload_json, created_at, client_id) = row

        raw_payload = {}
        if raw_payload_json:
            try:
                raw_payload = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
            except Exception:
                raw_payload = {}

        client_prof = {}
        sgt_d = {}
        if isinstance(raw_payload.get("raw_payload"), dict):
            client_prof = raw_payload["raw_payload"].get("client_profile", {}) or {}
            sgt_d = raw_payload["raw_payload"].get("sgt_dataset", {}) or {}

        pan = raw_payload.get("pan") or client_prof.get("pan") or unassigned_identity
        gstin = raw_payload.get("gstin") or client_prof.get("gstin") or unassigned_identity
        client_name = raw_payload.get("client_name") or client_prof.get("name")

        form = raw_payload.get("filing_type") or sgt_d.get("form")
        period = raw_payload.get("period_label") or sgt_d.get("period")

        cap_time = None
        if created_at:
            try:
                cap_time = datetime.datetime.fromisoformat(created_at).timestamp()
            except Exception:
                pass

        return SgtRecord(
            arn=arn_number or clean_arn,
            pan=_extract_pan(pan),
            gstin=_extract_gstin(gstin),
            client_name=_clean_str(client_name),
            client_id=client_id,
            form=_clean_str(form),
            period=_clean_str(period),
            captured_at=cap_time,
            source="tracker_dump",
            row_id=row_id,
            dataset_key=dataset_key,
            raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
        )

    if hasattr(db, "_connect_raw"):
        cm = db._connect_raw()
        if hasattr(cm, "__enter__") and hasattr(cm, "__exit__"):
            with cm as conn:
                return _query(conn)
        return _query(cm)
    elif hasattr(db, "cursor") or hasattr(db, "execute"):
        return _query(db)
    return None



def compare_clients(entry: Dict[str, Any], sgt: SgtRecord,
                    client_lookup: Optional[Callable[[str, str], Optional[dict]]] = None) -> str:
    """
    Compare OCR client evidence with SGT client identity.
    Returns: 'agrees', 'conflict', or 'unknown'. Never guesses.
    """
    client_block = entry.get("client") or {}
    ocr_user = client_block.get("username")
    ocr_cid = client_block.get("client_id")
    ocr_agreement = client_block.get("agreement")  # "agrees", "conflict", "unknown"

    # If OCR itself flagged a conflict on the page (e.g. page GSTIN contradicted login user)
    if ocr_agreement == "conflict":
        return "conflict"

    # Resolve OCR client details via lookup if available
    ocr_info = None
    if client_lookup and ocr_user:
        try:
            ocr_info = client_lookup("GST Portal", ocr_user)
        except Exception:
            pass

    ocr_gstin = _extract_gstin(ocr_info.get("gstin") if ocr_info else None)
    ocr_pan = _extract_pan(ocr_info.get("pan") if ocr_info else None)
    if not ocr_cid and ocr_info:
        ocr_cid = ocr_info.get("client_id")

    sgt_gstin = sgt.gstin
    sgt_pan = sgt.pan
    sgt_cid = sgt.client_id

    # If SGT has GSTIN but no PAN, derive PAN
    if sgt_gstin and not sgt_pan and len(sgt_gstin) == 15:
        sgt_pan = sgt_gstin[2:12]
    # If OCR has GSTIN but no PAN, derive PAN
    if ocr_gstin and not ocr_pan and len(ocr_gstin) == 15:
        ocr_pan = ocr_gstin[2:12]

    has_ocr_id = bool(ocr_cid is not None or ocr_gstin or ocr_pan)
    has_sgt_id = bool(sgt_cid is not None or sgt_gstin or sgt_pan)

    # Both unknown or one unknown -> "unknown" (not a failure of SGT per plan 5.3)
    if not has_ocr_id or not has_sgt_id:
        return "unknown"

    # Both have client_id
    if ocr_cid is not None and sgt_cid is not None:
        return "agrees" if str(ocr_cid) == str(sgt_cid) else "conflict"

    # Check GSTIN
    if ocr_gstin and sgt_gstin:
        return "agrees" if ocr_gstin == sgt_gstin else "conflict"

    # Check PAN
    if ocr_pan and sgt_pan:
        return "agrees" if ocr_pan == sgt_pan else "conflict"

    return "unknown"


def build_vsdc247_block(
    result: str,
    arn_seen: Optional[str] = None,
    seen_at: Optional[str] = None,
    form: Optional[str] = None,
    period: Optional[str] = None,
    page_kind: Optional[str] = None,
    username: Optional[str] = None,
    client: Any = "unknown",
    link: Optional[str] = None,
    sgt_delay_sec: Optional[int] = None,
    page_url: Optional[str] = None,
    late: bool = False,
    reason: Optional[str] = None,
    score: Optional[int] = None,
    tier: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the vsdc247-v verification section for the SGT payload or ledger."""
    is_verified = (result in (OUTCOME_MATCHED, OUTCOME_MATCHED_CLIENT_DIFFERS))
    if result == OUTCOME_SGT_ONLY:
        return {
            "verified": False,
            "status": "unverified",
            "checked": False,
            "reason": reason or "not seen by OCR",
            "result": OUTCOME_SGT_ONLY,
            "verifier_version": VERIFIER_VERSION,
        }

    block: Dict[str, Any] = {
        "verified": is_verified,
        "status": "verified" if is_verified else ("missed" if result == OUTCOME_MISSED else "pending"),
        "checked": True,
        "result": result,
        "arn": arn_seen,
        "arn_seen": arn_seen,
        "seen_at": seen_at,
        "form": form,
        "period": period,
        "score": score,
        "tier": tier or "certain",
        "page_kind": page_kind,
        "username": username,
        "client": client,
        "link": link,
        "sgt_delay_sec": int(sgt_delay_sec) if sgt_delay_sec is not None else 0,
        "page_url": page_url,
        "confirmed_by": "vsdc247_verifier",
        "verifier_version": VERIFIER_VERSION,
    }
    if late:
        block["late"] = True
    return block


def build_flagged_row(entry: Dict[str, Any], block: Dict[str, Any]) -> Dict[str, Any]:
    """Build the independent flagged row payload for a missed ARN (plan 6.5)."""
    arn = entry.get("arn", "")
    client_block = entry.get("client") or {}
    client_name = client_block.get("username") or f"Pending_{arn}"
    form = entry.get("form") or "GST Return"
    period = entry.get("period") or ""
    orig_author = entry.get("original_author") or entry.get("device_name") or device_name()

    return {
        "source": "vsdc247_verify",
        "portal": "GST Portal",
        "client_name": client_name,
        "filing_type": form,
        "period_label": period,
        "arn": arn,
        "status": "Submitted",
        "capture_method": "VSDC247_verify",
        "dataset_key": f"V247:GST:{arn}",
        "raw_text": "",
        "original_author": orig_author,
        "raw_payload": {
            "vsdc247": block,
            "original_author": orig_author,
        },
    }


class Reconciler:
    """
    Reconciles OCR-seen ARNs with SGT captures.
    Thread-safe and supports mock clocks for deterministic testing.
    """

    def __init__(
        self,
        sgt_lookup: Optional[Callable[[str], Optional[SgtRecord]]] = None,
        sgt_router: Any = None,
        sgt_router_getter: Optional[Callable[[], Any]] = None,
        db: Any = None,
        client_lookup: Optional[Callable[[str, str], Optional[dict]]] = None,
        grace_sec: float = DEFAULT_GRACE_SEC,
        clock: Callable[[], float] = time.time,
        on_alert: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        allow_flagged_rows: bool = False,
        accountant_log_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self._sgt_lookup = sgt_lookup
        self._sgt_router = sgt_router
        self._sgt_router_getter = sgt_router_getter
        self._db = db
        self._client_lookup = client_lookup
        self.grace_sec = float(grace_sec)
        self._clock = clock
        self._on_alert = on_alert
        self.allow_flagged_rows = bool(allow_flagged_rows)
        self.accountant_log_path = Path(accountant_log_path) if accountant_log_path else None

        self._lock = threading.Lock()
        self._pending: Dict[str, Tuple[Dict[str, Any], float]] = {}  # arn -> (entry, seen_epoch)
        self._missed: Dict[str, Tuple[Dict[str, Any], float]] = {}   # arn -> (entry, seen_epoch)
        self._reconciled: Dict[str, ReconciliationResult] = {}       # arn -> result

    def _current_sgt_router(self) -> Any:
        if self._sgt_router_getter is not None:
            try:
                return self._sgt_router_getter()
            except Exception:
                return None
        return self._sgt_router

    def set_sgt_router(self, sgt_router: Any) -> None:
        self._sgt_router = sgt_router
        self._sgt_router_getter = None

    def set_db(self, db: Any) -> None:
        self._db = db

    def lookup_sgt(self, arn: str) -> Optional[SgtRecord]:
        """Query SGT in-memory first, then tracker_dump."""
        if not arn:
            return None
        if self._sgt_lookup:
            res = self._sgt_lookup(arn)
            if res:
                return res if isinstance(res, SgtRecord) else SgtRecord(**res)
        router = self._current_sgt_router()
        if router:
            rec = find_sgt_in_router(router, arn)
            if rec:
                return rec
        if self._db:
            rec = find_sgt_in_db(self._db, arn)
            if rec:
                return rec
        return None

    def observe_arn(self, entry: Dict[str, Any], now: Optional[float] = None) -> ReconciliationResult:
        """
        Called when an ARN is saved in the OCR ledger.
        If SGT already captured it, reconciles immediately. Otherwise enters pending grace.
        """
        if now is None:
            now = self._clock()
        arn = entry.get("arn", "")
        seen_epoch = _parse_epoch(entry, now)

        with self._lock:
            if arn in self._reconciled:
                return self._reconciled[arn]

            sgt = self.lookup_sgt(arn)
            if sgt:
                result = self._resolve_match(entry, sgt, seen_epoch, now, late=False)
                self._reconciled[arn] = result
                self._pending.pop(arn, None)
                self._missed.pop(arn, None)
                self._maybe_alert(result)
                return result

            # Not seen by SGT yet: enter grace
            self._pending[arn] = (entry, seen_epoch)
            return ReconciliationResult(
                outcome=OUTCOME_PENDING,
                arn=arn,
                entry=entry,
            )

    def check_pending(self, now: Optional[float] = None) -> List[ReconciliationResult]:
        """
        Poll pending items and missed items (for late SGT arrivals).
        Called periodically on router / verifier tick.
        """
        if now is None:
            now = self._clock()
        resolved: List[ReconciliationResult] = []

        with self._lock:
            # 1. Check pending items awaiting SGT
            for arn, (entry, seen_epoch) in list(self._pending.items()):
                sgt = self.lookup_sgt(arn)
                if sgt:
                    res = self._resolve_match(entry, sgt, seen_epoch, now, late=False)
                    self._reconciled[arn] = res
                    self._pending.pop(arn, None)
                    resolved.append(res)
                    self._maybe_alert(res)
                elif now - seen_epoch >= self.grace_sec:
                    # Grace period expired: declare missed!
                    res = self._resolve_missed(entry, seen_epoch, now)
                    self._missed[arn] = (entry, seen_epoch)
                    self._pending.pop(arn, None)
                    resolved.append(res)
                    self._maybe_alert(res)

            # 2. Check missed items for late SGT arrival
            for arn, (entry, seen_epoch) in list(self._missed.items()):
                sgt = self.lookup_sgt(arn)
                if sgt:
                    # Late delivery!
                    res = self._resolve_match(entry, sgt, seen_epoch, now, late=True)
                    res.remove_flagged_row = True
                    self._reconciled[arn] = res
                    self._missed.pop(arn, None)
                    resolved.append(res)
                    self._maybe_alert(res)

        return resolved

    def check_sgt_row(self, sgt_data: Any, now: Optional[float] = None) -> ReconciliationResult:
        """
        Check an SGT row or record against the OCR ledger.
        If OCR never saw the ARN, returns sgt_only.
        """
        if now is None:
            now = self._clock()

        arn = None
        if isinstance(sgt_data, SgtRecord):
            arn = sgt_data.arn
            sgt = sgt_data
        elif isinstance(sgt_data, dict):
            arn = sgt_data.get("arn") or sgt_data.get("arn_number")
            sgt = SgtRecord(
                arn=arn or "",
                pan=_extract_pan(sgt_data.get("pan")),
                gstin=_extract_gstin(sgt_data.get("gstin")),
                client_name=_clean_str(sgt_data.get("client_name")),
                client_id=sgt_data.get("client_id"),
                form=_clean_str(sgt_data.get("filing_type") or sgt_data.get("form")),
                period=_clean_str(sgt_data.get("period_label") or sgt_data.get("period")),
                captured_at=sgt_data.get("captured_at"),
                source="tracker_dump",
                raw_payload=sgt_data.get("raw_payload", {}),
            )
        else:
            arn = getattr(sgt_data, "arn", None)
            sgt = SgtRecord(arn=arn or "")

        clean_arn = (arn or "").strip().upper()
        with self._lock:
            if clean_arn in self._reconciled:
                return self._reconciled[clean_arn]
            if clean_arn in self._pending:
                entry, seen_epoch = self._pending[clean_arn]
                res = self._resolve_match(entry, sgt, seen_epoch, now, late=False)
                self._reconciled[clean_arn] = res
                self._pending.pop(clean_arn, None)
                self._maybe_alert(res)
                return res
            if clean_arn in self._missed:
                entry, seen_epoch = self._missed[clean_arn]
                res = self._resolve_match(entry, sgt, seen_epoch, now, late=True)
                res.remove_flagged_row = True
                self._reconciled[clean_arn] = res
                self._missed.pop(clean_arn, None)
                self._maybe_alert(res)
                return res

        # ARN never seen by OCR: sgt_only
        block = build_vsdc247_block(result=OUTCOME_SGT_ONLY, reason="not seen by OCR")
        return ReconciliationResult(
            outcome=OUTCOME_SGT_ONLY,
            arn=clean_arn,
            block=block,
            sgt_record=sgt,
        )

    def is_pending(self, arn: str) -> bool:
        with self._lock:
            return (arn or "").strip().upper() in self._pending

    def is_missed(self, arn: str) -> bool:
        with self._lock:
            return (arn or "").strip().upper() in self._missed

    def get_result(self, arn: str) -> Optional[ReconciliationResult]:
        with self._lock:
            return self._reconciled.get((arn or "").strip().upper())

    def _resolve_match(self, entry: Dict[str, Any], sgt: SgtRecord, seen_epoch: float,
                       now: float, late: bool) -> ReconciliationResult:
        arn = entry.get("arn", "")
        agreement = compare_clients(entry, sgt, self._client_lookup)
        outcome = OUTCOME_MATCHED_CLIENT_DIFFERS if agreement == "conflict" else OUTCOME_MATCHED
        delay = max(0, int(now - seen_epoch))

        client_block = entry.get("client") or {}
        username = client_block.get("username")
        link = client_block.get("link")

        block = build_vsdc247_block(
            result=outcome,
            arn_seen=arn,
            seen_at=entry.get("seen_at"),
            form=entry.get("form"),
            period=entry.get("period"),
            page_kind=entry.get("page_kind"),
            username=username,
            client=agreement,
            link=link,
            sgt_delay_sec=delay,
            page_url=entry.get("page_url"),
            late=late,
            score=entry.get("score"),
            tier=entry.get("tier"),
        )

        alert_needed = (outcome == OUTCOME_MATCHED_CLIENT_DIFFERS)
        alert_kind = "client_differs" if alert_needed else None

        if self._db and hasattr(self._db, "annotate_vsdc247"):
            try:
                self._db.annotate_vsdc247(arn, block)
            except Exception:
                pass
        if late and self._db and hasattr(self._db, "remove_vsdc247_flagged_row"):
            try:
                self._db.remove_vsdc247_flagged_row(arn)
            except Exception:
                pass

        if late or outcome == OUTCOME_MATCHED_CLIENT_DIFFERS:
            append_accountant_reconciliation_log(
                outcome=outcome,
                arn=arn,
                form=entry.get("form") or sgt.form,
                period=entry.get("period") or sgt.period,
                username=username,
                client_name=sgt.client_name or username,
                filing_pc=entry.get("device_name") or device_name(),
                sgt_delay_sec=delay,
                sgt_client=sgt.client_name,
                sgt_pan=sgt.pan,
                late=late,
                seen_at=entry.get("seen_at"),
                path=getattr(self, "accountant_log_path", None),
            )

        return ReconciliationResult(
            outcome=outcome,
            arn=arn,
            block=block,
            sgt_record=sgt,
            entry=entry,
            client_agreement=agreement,
            sgt_delay_sec=delay,
            late=late,
            alert=alert_needed,
            alert_kind=alert_kind,
        )

    def _resolve_missed(self, entry: Dict[str, Any], seen_epoch: float, now: float) -> ReconciliationResult:
        arn = entry.get("arn", "")
        client_block = entry.get("client") or {}
        username = client_block.get("username")
        link = client_block.get("link")
        agreement = client_block.get("agreement") or "unknown"
        delay = max(0, int(now - seen_epoch))

        block = build_vsdc247_block(
            result=OUTCOME_MISSED,
            arn_seen=arn,
            seen_at=entry.get("seen_at"),
            form=entry.get("form"),
            period=entry.get("period"),
            page_kind=entry.get("page_kind"),
            username=username,
            client=agreement,
            link=link,
            sgt_delay_sec=delay,
            page_url=entry.get("page_url"),
        )
        flagged_row = build_flagged_row(entry, block)

        if self.allow_flagged_rows and self._db and hasattr(self._db, "insert_vsdc247_flagged_row"):
            try:
                self._db.insert_vsdc247_flagged_row(flagged_row)
            except Exception:
                pass

        append_accountant_reconciliation_log(
            outcome=OUTCOME_MISSED,
            arn=arn,
            form=entry.get("form"),
            period=entry.get("period"),
            username=username,
            client_name=username,
            filing_pc=entry.get("device_name") or device_name(),
            sgt_delay_sec=delay,
            seen_at=entry.get("seen_at"),
            path=getattr(self, "accountant_log_path", None),
        )

        return ReconciliationResult(
            outcome=OUTCOME_MISSED,
            arn=arn,
            block=block,
            entry=entry,
            client_agreement=agreement,
            sgt_delay_sec=delay,
            alert=True,
            alert_kind="sgt_missed",
            flagged_row=flagged_row,
        )

    def record_sgt_matched(self, arn: str, sgt_record_or_row: Any) -> Optional[ReconciliationResult]:
        """Directly record that SGT has captured/matched this ARN, removing it from pending."""
        clean_arn = (arn or "").strip().upper()
        if not clean_arn:
            return None
        with self._lock:
            if clean_arn in self._reconciled:
                return self._reconciled[clean_arn]
            entry_tuple = self._pending.pop(clean_arn, None) or self._missed.pop(clean_arn, None)
            entry = entry_tuple[0] if entry_tuple else None
            router = self._current_sgt_router()
            if not entry and router and hasattr(router, "_verifier") and router._verifier:
                entry = router._verifier.saved.get(clean_arn)
            if not entry:
                return None
            seen_epoch = _parse_epoch(entry, self._clock())
            if isinstance(sgt_record_or_row, SgtRecord):
                sgt = sgt_record_or_row
            else:
                sgt = SgtRecord(
                    arn=clean_arn,
                    pan=_extract_pan(sgt_record_or_row.get("pan")),
                    gstin=_extract_gstin(sgt_record_or_row.get("gstin")),
                    client_name=_clean_str(sgt_record_or_row.get("client_name")),
                    form=_clean_str(sgt_record_or_row.get("filing_type")),
                    period=_clean_str(sgt_record_or_row.get("period_label")),
                    source="sgt_direct",
                )
            res = self._resolve_match(entry, sgt, seen_epoch, self._clock(), late=False)
            self._reconciled[clean_arn] = res
            return res


    def _maybe_alert(self, res: ReconciliationResult) -> None:
        if res.alert and self._on_alert:
            try:
                self._on_alert(res.alert_kind or "alert", {
                    "arn": res.arn,
                    "outcome": res.outcome,
                    "block": res.block,
                    "delay_sec": res.sgt_delay_sec,
                })
            except Exception:
                pass
