"""
core/vsdc/vsdc_assembler.py — Boundary-Driven Visual Session Assembler
======================================================================
1-to-1 SDC Parity with decoupled Session and Multi-Return Filing Registry.

Architectural Pillars:
1. SessionContext (Client Lifecycle): IN -> OUT boundary tracking.
   Survives across internal return form and dashboard navigations.
2. Normalized Period & Month Enum: Statutory month mapping where quarterly
   periods (e.g. Apr-Jun) resolve deterministically to their terminal month (June).
3. Strict 10-Digit PAN Entity Identification: Entity key is ALWAYS the PAN
   (derived from GSTIN[2:12] if GSTIN is present).
4. Composite Tuple Keys (entity_pan, form_type, fy, normalized_month):
   Eliminates string collision and accurately merges multi-screen captures.
5. Monotonic Status Engine & Unified Dispatch: Status only promotes, ARNs are
   never lost, and a single canonical payload envelope is emitted to database.py.
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .vsdc_name_parser import is_better_taxpayer_name, is_valid_name
from .vsdc_session_logger import VSDCSessionLogger


# ─── 1. Predefined Statutory Months & Mapping ─────────────────────────────────

MONTHS: List[str] = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

MONTH_MAP: Dict[str, str] = {
    # Statutory Quarters (Maps to terminal / closing month of the tax period)
    "apr-jun": "June",
    "apr - jun": "June",
    "april-june": "June",
    "april - june": "June",
    "q1": "June",
    "quarter 1": "June",
    "quarter-1": "June",
    "quarter 01": "June",
    "jul-sep": "September",
    "jul - sep": "September",
    "july-september": "September",
    "july - september": "September",
    "q2": "September",
    "quarter 2": "September",
    "quarter-2": "September",
    "quarter 02": "September",
    "oct-dec": "December",
    "oct - dec": "December",
    "october-december": "December",
    "october - december": "December",
    "q3": "December",
    "quarter 3": "December",
    "quarter-3": "December",
    "quarter 03": "December",
    "jan-mar": "March",
    "jan - mar": "March",
    "january-march": "March",
    "january - march": "March",
    "q4": "March",
    "quarter 4": "March",
    "quarter-4": "March",
    "quarter 04": "March",
    # Individual Months & Standard Abbreviations
    "jan": "January",
    "january": "January",
    "feb": "February",
    "february": "February",
    "mar": "March",
    "march": "March",
    "apr": "April",
    "april": "April",
    "may": "May",
    "jun": "June",
    "june": "June",
    "jul": "July",
    "july": "July",
    "aug": "August",
    "august": "August",
    "sep": "September",
    "sept": "September",
    "september": "September",
    "oct": "October",
    "october": "October",
    "nov": "November",
    "november": "November",
    "dec": "December",
    "december": "December",
}


def resolve_entity_pan(pan: Optional[str] = None, gstin: Optional[str] = None) -> str:
    """
    Strictly resolves the 10-character PAN as the authoritative entity identifier.
    Extracts PAN from chars 2..12 of GSTIN if PAN is not directly supplied.
    Guarantees entity_id is ALWAYS the PAN and never the full GSTIN or arbitrary strings.
    """
    if pan:
        clean_pan = pan.strip().upper()
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", clean_pan):
            return clean_pan
    if gstin:
        clean_gstin = gstin.strip().upper()
        if len(clean_gstin) >= 12:
            derived = clean_gstin[2:12]
            if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", derived):
                return derived
    if pan:
        return pan.strip().upper()
    return "UNKNOWN"


def normalize_period(
    tax_period: Optional[str],
    fy: Optional[str] = None,
    portal: str = "GST Portal",
) -> Tuple[str, str, str]:
    """
    Normalizes statutory period and financial year into:
    (canonical_month, canonical_fy, formatted_label).

    Example Normalizations:
      - 'Apr-Jun', fy='2026-27' -> ('June', '2026-27', 'June (FY 2026-27)')
      - 'June(Q)', fy='2026-27'  -> ('June', '2026-27', 'June (FY 2026-27)')
      - 'September', fy='2026-27' -> ('September', '2026-27', 'September (FY 2026-27)')
      - 'AY 2026-27', portal='Income Tax' -> ('Annual', '2026-27', 'AY 2026-27')
    """
    raw_period = (tax_period or "").strip()
    raw_fy = (fy or "").strip()

    # 1. Extract Financial Year / Assessment Year
    clean_fy = ""
    fy_match = re.search(r"\b(20\d{2}[-_/]\d{2})\b", f"{raw_fy} {raw_period}")
    if fy_match:
        clean_fy = fy_match.group(1).replace("/", "-").replace("_", "-")
    elif re.search(r"\b20\d{2}\b", f"{raw_fy} {raw_period}"):
        y_match = re.search(r"\b(20\d{2})\b", f"{raw_fy} {raw_period}")
        if y_match:
            y = int(y_match.group(1))
            clean_fy = f"{y}-{str(y + 1)[2:]}"

    # 2. Income Tax Protocol Normalization
    if portal == "Income Tax":
        ay_match = re.search(r"(?:AY|A\.Y\.?)\s*(20\d{2}[-_/]\d{2})", f"{raw_period} {raw_fy}", re.IGNORECASE)
        ay_str = ay_match.group(1).replace("/", "-").replace("_", "-") if ay_match else clean_fy
        if not ay_str:
            now = datetime.now()
            curr_y = now.year if now.month >= 4 else now.year - 1
            ay_str = f"{curr_y + 1}-{str(curr_y + 2)[2:]}"
        label = f"AY {ay_str}"
        return ("Annual", ay_str, label)

    # 3. GST Protocol Normalization (Month Resolution from Predefined List)
    clean_text = raw_period.lower()
    # Strip (Q), (FY ...), FY ...
    clean_text = re.sub(r"\(q\)", "", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"\(fy\s*20\d{2}[-_/]\d{2}\)", "", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"fy\s*20\d{2}[-_/]\d{2}", "", clean_text, flags=re.IGNORECASE)
    clean_text = clean_text.strip()

    canonical_month = "Unknown"
    # Exact lookup in MONTH_MAP
    if clean_text in MONTH_MAP:
        canonical_month = MONTH_MAP[clean_text]
    else:
        # Check quarters first
        matched = False
        for alias, target_month in MONTH_MAP.items():
            if alias in clean_text:
                canonical_month = target_month
                matched = True
                break
        if not matched:
            for m in MONTHS:
                if m.lower() in clean_text:
                    canonical_month = m
                    break

    # Build canonical period label: Month (FY Year)
    if clean_fy:
        period_label = f"{canonical_month} (FY {clean_fy})" if canonical_month != "Unknown" else f"FY {clean_fy}"
    else:
        period_label = canonical_month if canonical_month != "Unknown" else raw_period

    return (canonical_month, clean_fy, period_label)


# ─── 2. Status Ranking Engine ──────────────────────────────────────────────────

STATUS_RANK: Dict[str, int] = {
    "not submitted": 0,
    "visited / in progress": 0,
    "draft / personal info": 0,
    "form selected": 0,
    "filing submitted": 2,
    "submitted": 2,
    "submitted (not e-verified)": 3,
    "submitted (pending e-verification)": 3,
    "submitted (e-verified)": 4,
    "filed & verified": 4,
    "processed": 5,
    "processed with no demand/refund": 5,
    "processed with refund": 5,
    "processed with demand": 5,
}


def get_status_rank(status_str: Optional[str]) -> int:
    """Returns numeric priority rank for filing statuses (higher = more complete)."""
    if not status_str:
        return 0
    clean = str(status_str).strip().lower()
    if clean in STATUS_RANK:
        return STATUS_RANK[clean]
    if any(k in clean for k in ("not filed", "unfiled", "not submitted", "visited", "progress", "draft", "selected", "landing", "profile")):
        return 0
    if any(k in clean for k in ("processed",)):
        return 5
    if any(k in clean for k in ("verified", "e-verified")):
        if "not e-verified" in clean or "pending" in clean:
            return 3
        return 4
    if any(k in clean for k in ("submitted", "filed", "success")):
        return 2
    return 0


# ─── 3. Data Structures: SessionContext & FilingRecord ─────────────────────────

@dataclass
class SessionContext:
    """Represents the post-login taxpayer session boundary (IN -> OUT)."""
    session_id: str
    portal: str = "Income Tax"
    client_pan: Optional[str] = None
    client_name: Optional[str] = None
    trade_name: Optional[str] = None
    gstin: Optional[str] = None
    dob: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    filing_preference: Optional[str] = None
    is_active: bool = False
    started_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    steps: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class FilingRecord:
    """
    Represents an individual return filing record in the multi-return registry.
    Keyed by canonical tuple: (entity_pan, form_type, fy, canonical_month).
    """
    key: Tuple[str, str, str, str]
    entity_pan: str
    gstin: str
    client_name: str
    trade_name: str
    filing_type: str
    tax_period: str        # Canonical Month e.g. "June"
    fy: str                # Financial Year e.g. "2026-27"
    period_label: str      # Formatted Label e.g. "June (FY 2026-27)"
    status: str
    status_rank: int
    due_date: str = ""
    dob: str = ""
    mobile: str = ""
    email: str = ""
    arn: str = ""
    ack_number: str = ""
    portal: str = "GST Portal"
    capture_method: str = "VSDC_optical"
    filing_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    filing_preference: str = ""
    raw_text: str = ""
    session_id: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def dataset_key(self) -> str:
        """Returns standard SDC dataset key: PAN|FormType|PeriodLabel."""
        return f"{self.entity_pan}|{self.filing_type}|{self.period_label}"

    def to_dict(self) -> Dict[str, Any]:
        """Converts to standard SDC capture item dictionary."""
        return {
            "dataset_key": self.dataset_key,
            "pan": self.entity_pan,
            "gstin": self.gstin,
            "client_name": self.client_name,
            "trade_name": self.trade_name,
            "filing_type": self.filing_type,
            "period_label": self.period_label,
            "tax_period": self.tax_period,
            "fy": self.fy,
            "due_date": self.due_date,
            "dob": self.dob,
            "mobile": self.mobile,
            "email": self.email,
            "filing_preference": self.filing_preference,
            "arn": self.arn,
            "ack_number": self.ack_number or self.arn,
            "status": self.status,
            "portal": self.portal,
            "capture_method": self.capture_method,
            "filing_date": self.filing_date,
            "raw_text": self.raw_text,
            "session_id": self.session_id,
        }


# ─── 4. Boundary-Driven Visual Session Assembler ──────────────────────────────

class VisualSessionAssembler:
    """
    Decoupled Session and Multi-Return Filing Assembler.
    Maintains 1-to-1 parity with Sera SDC protocols.
    """

    def __init__(self, session_logger: Optional[VSDCSessionLogger] = None):
        initial_sess_id = f"vsdc_sess_{uuid.uuid4().hex[:12]}"
        self.session = SessionContext(session_id=initial_sess_id)
        self.records: Dict[Tuple[str, str, str, str], FilingRecord] = {}
        self.captures: Dict[str, Dict[str, Any]] = {}  # Synchronized with self.records for backward compatibility
        self.current_filing_type: Optional[str] = None
        self.current_period_label: Optional[str] = None
        self.fy: Optional[str] = None
        self.due_date: Optional[str] = None
        self.last_activity: float = time.time()
        self._flushed: bool = False
        self._emitted_datasets: Dict[str, Tuple[str, str, str, str]] = {}
        self.logger: VSDCSessionLogger = session_logger or VSDCSessionLogger()
        self._session_started: bool = False

    # ── Backward Compatible Property Facades ──────────────────────────────────
    @property
    def session_id(self) -> str:
        return self.session.session_id

    @session_id.setter
    def session_id(self, val: str):
        self.session.session_id = val

    @property
    def portal(self) -> str:
        return self.session.portal

    @portal.setter
    def portal(self, val: str):
        self.session.portal = val

    @property
    def client_pan(self) -> Optional[str]:
        return self.session.client_pan

    @client_pan.setter
    def client_pan(self, val: Optional[str]):
        self.session.client_pan = val

    @property
    def client_name(self) -> Optional[str]:
        return self.session.client_name

    @client_name.setter
    def client_name(self, val: Optional[str]):
        self.session.client_name = val

    @property
    def trade_name(self) -> Optional[str]:
        return self.session.trade_name

    @trade_name.setter
    def trade_name(self, val: Optional[str]):
        self.session.trade_name = val

    @property
    def gstin(self) -> Optional[str]:
        return self.session.gstin

    @gstin.setter
    def gstin(self, val: Optional[str]):
        self.session.gstin = val

    @property
    def dob(self) -> Optional[str]:
        return self.session.dob

    @dob.setter
    def dob(self, val: Optional[str]):
        self.session.dob = val

    @property
    def mobile(self) -> Optional[str]:
        return self.session.mobile

    @mobile.setter
    def mobile(self, val: Optional[str]):
        self.session.mobile = val

    @property
    def email(self) -> Optional[str]:
        return self.session.email

    @email.setter
    def email(self, val: Optional[str]):
        self.session.email = val

    @property
    def filing_preference(self) -> Optional[str]:
        return self.session.filing_preference

    @filing_preference.setter
    def filing_preference(self, val: Optional[str]):
        self.session.filing_preference = val

    @property
    def steps(self) -> List[Dict[str, Any]]:
        return self.session.steps

    # ── Session Lifecycle & Boundaries ────────────────────────────────────────
    def reset(self, new_pan: Optional[str] = None):
        """
        Resets active session state for a fresh client session.
        Concludes session log if active and clears all in-memory return records.
        """
        if self._session_started:
            self.logger.end_session(reason="Session reset / Client switch", summary_items=list(self.captures.values()))
            self._session_started = False

        self.session = SessionContext(
            session_id=f"vsdc_sess_{uuid.uuid4().hex[:12]}",
            portal=self.portal,
            client_pan=new_pan,
        )
        self.current_filing_type = None
        self.current_period_label = None
        self.fy = None
        self.due_date = None
        self.records.clear()
        self.captures.clear()
        self.last_activity = time.time()
        self._flushed = False
        self._emitted_datasets.clear()

    def clear_workflow_selection(self):
        """
        Clears active filing form selection, period, and pending unsubmitted drafts (rank <= 0)
        when navigating back to hub/dashboard screens. Strictly preserves verified submitted filings
        (rank >= 2) and client identity, supporting multi-return filing sessions.
        """
        # Purge only unsubmitted draft captures
        unsubmitted_keys = [k for k, r in self.records.items() if r.status_rank <= 0]
        for k in unsubmitted_keys:
            r = self.records.pop(k, None)
            if r and r.dataset_key in self.captures:
                del self.captures[r.dataset_key]
            if r and r.dataset_key in self._emitted_datasets:
                del self._emitted_datasets[r.dataset_key]

        self.current_filing_type = None
        self.current_period_label = None
        self.fy = None
        self.due_date = None
        self._flushed = False

    def record_step(self, route_url: str, crosshair_id: str, details: Optional[Dict[str, Any]] = None):
        """Records a navigation step in the visual session timeline."""
        self.last_activity = time.time()
        step = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": route_url,
            "crosshair_id": crosshair_id,
            "details": details or {},
        }
        self.session.steps.append(step)

        if not self._session_started and (self.client_pan or self.gstin or self.client_name):
            self.logger.start_session(
                session_id=self.session_id,
                portal=self.portal,
                client_name=self.client_name,
                pan=self.client_pan,
                gstin=self.gstin,
                filing_preference=self.filing_preference,
                initial_url=route_url,
            )
            self._session_started = True
        elif self._session_started:
            desc = (details or {}).get("description")
            self.logger.log_route_transition(crosshair_id, route_url, description=desc)

    def update_identity(
        self,
        pan: Optional[str] = None,
        name: Optional[str] = None,
        gstin: Optional[str] = None,
        portal: Optional[str] = None,
        filing_preference: Optional[str] = None,
        trade_name: Optional[str] = None,
        dob: Optional[str] = None,
        mobile: Optional[str] = None,
        email: Optional[str] = None,
        is_authoritative: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Updates taxpayer identity attributes. Enforces PAN context switch guard.
        Returns sealed prior session payload if a context switch occurred, else None.
        """
        self.last_activity = time.time()
        flushed_prior = None

        if portal:
            self.portal = portal

        resolved_pan = resolve_entity_pan(pan=pan, gstin=gstin)
        if resolved_pan != "UNKNOWN":
            if self.client_pan and self.client_pan != resolved_pan:
                # Context switch detected! Seal prior session before switching
                flushed_prior = self.seal_and_flush()
                self.reset(new_pan=resolved_pan)
            else:
                self.client_pan = resolved_pan

        if name:
            new_name = name.strip().upper()
            if is_valid_name(new_name):
                if is_authoritative or not self.client_name or is_better_taxpayer_name(new_name, self.client_name):
                    self.client_name = new_name
                    # Propagate updated name to existing records
                    for rec in self.records.values():
                        rec.client_name = new_name
                        self.captures[rec.dataset_key] = rec.to_dict()

        if trade_name:
            self.trade_name = trade_name.strip().upper()
            for rec in self.records.values():
                rec.trade_name = self.trade_name
                self.captures[rec.dataset_key] = rec.to_dict()

        if gstin:
            self.gstin = gstin.strip().upper()

        if dob and not self.dob:
            # DOB is a fixed identity attribute (unlike name, which can be refined
            # across pages) — once captured, it never legitimately changes, so the
            # first valid read wins rather than overwriting on every later tick.
            self.dob = dob.strip()
            for rec in self.records.values():
                rec.dob = self.dob
                self.captures[rec.dataset_key] = rec.to_dict()

        # Contact details, unlike DOB, can legitimately change (the taxpayer can edit
        # them on the portal), so a fresh exact read replaces an earlier one. Kept out
        # of the session logger on purpose - it never records personal identifiers.
        if mobile and mobile.strip() != (self.mobile or ""):
            self.mobile = mobile.strip()
            for rec in self.records.values():
                rec.mobile = self.mobile
                self.captures[rec.dataset_key] = rec.to_dict()

        if email and email.strip().lower() != (self.email or ""):
            self.email = email.strip().lower()
            for rec in self.records.values():
                rec.email = self.email
                self.captures[rec.dataset_key] = rec.to_dict()

        if filing_preference:
            norm_pref = filing_preference.strip().title()
            if norm_pref in ("Quarterly", "Monthly"):
                self.filing_preference = norm_pref

        if flushed_prior and self._session_started:
            self.logger.end_session(reason="PAN Context Switch", summary_items=list(self.captures.values()))
            self._session_started = False

        if not self._session_started and (self.client_pan or self.gstin or self.client_name):
            self.logger.start_session(
                session_id=self.session_id,
                portal=self.portal,
                client_name=self.client_name,
                pan=self.client_pan,
                gstin=self.gstin,
                filing_preference=self.filing_preference,
            )
            self._session_started = True
        elif self._session_started:
            self.logger.update_identity(
                name=self.client_name,
                pan=self.client_pan,
                gstin=self.gstin,
                filing_preference=self.filing_preference,
                portal=self.portal,
            )

        return flushed_prior

    def update_selection(
        self,
        filing_type: Optional[str] = None,
        period_label: Optional[str] = None,
        fy: Optional[str] = None,
        due_date: Optional[str] = None,
    ):
        """
        Records the active form type and assessment year / return period.
        If a new filing form is selected, any prior unsubmitted draft for this assessee & period
        is superseded to ensure only one active draft return per period.
        """
        self.last_activity = time.time()
        if filing_type:
            new_ft = filing_type.strip().upper()
            if self.current_filing_type and self.current_filing_type != new_ft:
                # User switched forms: purge any unsubmitted draft of the old form
                old_ft = self.current_filing_type
                c_month, c_fy, p_label = normalize_period(self.current_period_label, fy=self.fy or fy, portal=self.portal)
                entity_pan = resolve_entity_pan(self.client_pan, self.gstin)
                old_key = (entity_pan, old_ft, c_fy, c_month)
                old_rec = self.records.get(old_key)
                if old_rec and old_rec.status_rank <= 0:
                    del self.records[old_key]
                    if old_rec.dataset_key in self.captures:
                        del self.captures[old_rec.dataset_key]
                    if old_rec.dataset_key in self._emitted_datasets:
                        del self._emitted_datasets[old_rec.dataset_key]
            self.current_filing_type = new_ft

        if period_label:
            c_month, c_fy, p_label = normalize_period(period_label, fy=fy, portal=self.portal)
            self.current_period_label = p_label
            if c_fy:
                self.fy = c_fy

        if fy:
            self.fy = fy.strip()
        if due_date:
            self.due_date = due_date.strip()

        if self._session_started:
            self.logger.log_milestone(
                "FORM SELECTION",
                f"Selected {self.current_filing_type or 'Form'} ({self.current_period_label or 'Period'})",
                {
                    "filing_type": self.current_filing_type,
                    "period": self.current_period_label,
                    "fy": self.fy,
                    "due_date": self.due_date,
                },
            )

    # ── Return Records & Monotonic Status Engine ──────────────────────────────
    def record_submission(
        self,
        ack_number: str,
        status: str,
        filing_type: Optional[str] = None,
        period_label: Optional[str] = None,
        raw_text: Optional[str] = None,
        crosshair_id: str = "vsh_submission",
        engine: str = "VSDC",
    ) -> Dict[str, Any]:
        """
        Records a verified or submitted filing acknowledgement.
        Constructs a typed FilingRecord keyed by (PAN, FormType, FY, Month).
        Enforces monotonic status promotion: status only upgrades, valid ARNs are never lost.
        """
        self.last_activity = time.time()
        ft = filing_type or self.current_filing_type or ("Return" if self.portal != "Income Tax" else "ITR")
        c_month, c_fy, p_label = normalize_period(
            tax_period=period_label or self.current_period_label,
            fy=self.fy,
            portal=self.portal,
        )

        entity_pan = resolve_entity_pan(self.client_pan, self.gstin)
        record_key = (entity_pan, ft, c_fy, c_month)

        # Monotonic Status Protection: promote, never demote
        new_rank = get_status_rank(status)
        effective_status = status
        effective_rank = new_rank
        effective_arn = ack_number

        existing = self.records.get(record_key)
        if existing:
            if existing.status_rank > new_rank:
                effective_status = existing.status
                effective_rank = existing.status_rank
            if (not ack_number or ack_number == "N/A") and existing.arn and existing.arn != "N/A":
                effective_arn = existing.arn

        record = FilingRecord(
            key=record_key,
            entity_pan=entity_pan,
            gstin=self.gstin or "",
            client_name=self.client_name or "",
            trade_name=self.trade_name or "",
            filing_type=ft,
            tax_period=c_month,
            fy=c_fy or self.fy or "",
            period_label=p_label,
            status=effective_status,
            status_rank=effective_rank,
            due_date=self.due_date or "",
            dob=self.dob or "",
            mobile=self.mobile or "",
            email=self.email or "",
            arn=effective_arn,
            ack_number=effective_arn,
            portal=self.portal,
            capture_method=f"{engine}_{crosshair_id}",
            filing_preference=self.filing_preference or "",
            raw_text=raw_text or "",
            session_id=self.session_id,
            steps=list(self.session.steps),
        )

        self.records[record_key] = record
        self.captures[record.dataset_key] = record.to_dict()

        if self._session_started:
            self.logger.log_milestone(
                "SUBMISSION / ARN",
                f"{ft} Submitted ({p_label})",
                {
                    "filing_type": ft,
                    "period": p_label,
                    "status": effective_status,
                    "arn": effective_arn,
                    "filing_date": record.filing_date,
                },
            )

        return record.to_dict()

    def record_gst_form_details(
        self,
        form_type: str,
        tax_period: str,
        status: str,
        due_date: Optional[str] = None,
        fy: Optional[str] = None,
        trade_name: Optional[str] = None,
        period_label: Optional[str] = None,
        raw_text: Optional[str] = None,
        crosshair_id: str = "gst_form_details",
        legal_name: Optional[str] = None,
        engine: str = "VSDC",
    ) -> Dict[str, Any]:
        """
        Records full metadata captured from a GST Return Form page table
        (GSTIN, PAN, Legal Name, Trade Name, Form Type, FY, Tax Period, Status, Due Date).
        Normalizes tax period into Month (FY Year) e.g. Apr-Jun -> June (FY 2026-27).
        """
        self.last_activity = time.time()
        ft = form_type or self.current_filing_type or "GSTR-1"

        if legal_name:
            self.client_name = legal_name.strip().upper()
        if trade_name:
            self.trade_name = trade_name.strip().upper()
        if fy:
            self.fy = fy.strip()
        if due_date:
            self.due_date = due_date.strip()

        c_month, c_fy, p_label = normalize_period(
            tax_period=period_label or tax_period or self.current_period_label,
            fy=fy or self.fy,
            portal="GST Portal",
        )

        self.current_filing_type = ft
        self.current_period_label = p_label

        entity_pan = resolve_entity_pan(self.client_pan, self.gstin)
        record_key = (entity_pan, ft, c_fy, c_month)

        new_rank = get_status_rank(status)
        effective_status = status
        effective_rank = new_rank
        effective_arn = ""

        existing = self.records.get(record_key)
        if existing:
            if existing.status_rank > new_rank:
                effective_status = existing.status
                effective_rank = existing.status_rank
            effective_arn = existing.arn or existing.ack_number or ""

        resolved_client_name = (legal_name.strip().upper() if legal_name else "") or self.client_name or ""
        resolved_trade_name = self.trade_name or (trade_name.strip().upper() if trade_name else "")

        record = FilingRecord(
            key=record_key,
            entity_pan=entity_pan,
            gstin=self.gstin or "",
            client_name=resolved_client_name,
            trade_name=resolved_trade_name,
            filing_type=ft,
            tax_period=c_month,
            fy=c_fy or self.fy or "",
            period_label=p_label,
            status=effective_status,
            status_rank=effective_rank,
            due_date=self.due_date or due_date or "",
            arn=effective_arn,
            ack_number=effective_arn,
            portal="GST Portal",
            capture_method=f"{engine}_{crosshair_id}",
            filing_preference=self.filing_preference or "",
            raw_text=raw_text or "",
            session_id=self.session_id,
            steps=list(self.session.steps),
        )

        self.records[record_key] = record
        self.captures[record.dataset_key] = record.to_dict()

        if self._session_started:
            self.logger.log_milestone(
                "FORM CAPTURED",
                f"Captured {ft} ({p_label})",
                {
                    "form_type": ft,
                    "period": p_label,
                    "status": effective_status,
                    "due_date": record.due_date,
                    "fy": record.fy,
                    "legal_name": resolved_client_name,
                    "trade_name": resolved_trade_name,
                },
            )

        return record.to_dict()

    # ── Unified Single-Point Payload Dispatch Engine ──────────────────────────
    def _build_payload(self, record: FilingRecord, crosshair_id: str) -> Dict[str, Any]:
        """Builds the canonical master payload envelope expected by database.py."""
        return {
            "source": "vsdc_optical",
            "session_id": self.session_id,
            "portal": record.portal,
            "pan": record.entity_pan,
            "gstin": record.gstin,
            "client_name": record.client_name,
            "trade_name": record.trade_name,
            "filing_type": record.filing_type,
            "period_label": record.period_label,
            "tax_period": record.tax_period,
            "fy": record.fy,
            "due_date": record.due_date,
            "dob": record.dob,
            "mobile": record.mobile,
            "email": record.email,
            "filing_preference": record.filing_preference,
            "arn": record.arn,
            "ack_number": record.ack_number or record.arn,
            "status": record.status,
            "raw_text": record.raw_text,
            "capture_method": record.capture_method or f"VSDC_{crosshair_id}",
            "raw_payload": {
                "source": {
                    "protocol": record.portal,
                    # Which engine actually supplied this capture, so the stored payload
                    # agrees with capture_method instead of always claiming plain VSDC.
                    "engine": "VSDC-X" if (record.capture_method or "").startswith("VSDC-X") else "VSDC",
                    "version": "1.0.0",
                },
                "raw_text": record.raw_text,
                "client_temp_name": record.client_name,
                "trade_name": record.trade_name,
                "assembler_captures": [record.to_dict()],
                "timeline": list(self.session.steps),
            },
        }

    def get_gst_form_dataset_payload(
        self,
        dataset_key: Optional[str] = None,
        crosshair_id: str = "gst_form_details",
    ) -> Optional[Dict[str, Any]]:
        """
        Dispatches an assembled GST Form dataset payload to the app database.
        Deduplicates against (status, arn, client_name, trade_name) to avoid duplicate ticks.
        """
        target_record = None
        if dataset_key:
            for rec in self.records.values():
                if rec.dataset_key == dataset_key:
                    target_record = rec
                    break
        else:
            entity_pan = resolve_entity_pan(self.client_pan, self.gstin)
            ft = self.current_filing_type or "GSTR-1"
            c_month, c_fy, _ = normalize_period(self.current_period_label, fy=self.fy, portal="GST Portal")
            target_record = self.records.get((entity_pan, ft, c_fy, c_month))

        if not target_record:
            return None

        fingerprint = (target_record.status, target_record.arn, target_record.client_name, target_record.trade_name)
        if self._emitted_datasets.get(target_record.dataset_key) == fingerprint:
            return None

        self._emitted_datasets[target_record.dataset_key] = fingerprint
        return self._build_payload(target_record, crosshair_id)

    @staticmethod
    def _is_dataset_complete(record: "FilingRecord") -> bool:
        """
        The one mandatory-field gate for ANY dataset leaving VSDC, uniform across
        both portals: PAN, name, form type, period, submit status (rank >= 2), and
        a valid ARN/Ack must all be present. Everything else (DOB, trade name, due
        date, filing preference, ...) is optional and rides along only if captured.

        Deliberately NOT applied to GST's per-form draft path
        (get_gst_form_dataset_payload) — that one is meant to ship even an
        unfiled/incomplete draft so the app can show "Not Filed" status live;
        this gate exists for the two paths that claim a filing is actually done.
        """
        return bool(
            record.entity_pan and record.entity_pan != "UNKNOWN"
            and record.client_name
            and record.filing_type
            and record.period_label
            and record.status_rank >= 2
            and record.arn not in (None, "", "N/A", "UNKNOWN")
        )

    def get_completed_dataset_payload(self, crosshair_id: str = "vsdc_dataset_completion") -> Optional[Dict[str, Any]]:
        """
        Dataset Completion Principle:
        Emits an updated dataset payload whenever the mandatory fields are all
        present and the record's fingerprint has changed since it was last shot
        (e.g. a status PROMOTION such as e-Verified arriving after an earlier
        flush already sealed the session — seal_and_flush() itself can only fire
        once per session, so this is the path that keeps later promotions from
        being silently dropped; see _route_itr_crosshair / _route_gst_crosshair).
        """
        entity_pan = resolve_entity_pan(self.client_pan, self.gstin)
        if entity_pan == "UNKNOWN":
            return None

        # Prefer the record matching the currently-tracked form/period (the
        # normal case). Fall back to any complete record for this entity — a
        # status-promotion confirmation page (e.g. e-Verified) often doesn't
        # restate the form type/period as text, so current_filing_type/
        # current_period_label can be stale/None at that point without this.
        target_record = None
        form = self.current_filing_type
        period = self.current_period_label
        if form and period:
            c_month, c_fy, _ = normalize_period(period, fy=self.fy, portal=self.portal)
            target_record = self.records.get((entity_pan, form, c_fy, c_month))
            if not target_record:
                for rec in self.records.values():
                    if rec.entity_pan == entity_pan and rec.filing_type == form:
                        target_record = rec
                        break

        if not target_record or not self._is_dataset_complete(target_record):
            for rec in self.records.values():
                if rec.entity_pan == entity_pan and self._is_dataset_complete(rec):
                    target_record = rec
                    break

        if not target_record or not self._is_dataset_complete(target_record):
            return None

        arn = target_record.arn or target_record.ack_number
        fingerprint = (target_record.status, arn, target_record.client_name, target_record.trade_name)
        if self._emitted_datasets.get(target_record.dataset_key) == fingerprint:
            return None

        self._emitted_datasets[target_record.dataset_key] = fingerprint
        return self._build_payload(target_record, crosshair_id)

    def seal_and_flush(self) -> Optional[Dict[str, Any]]:
        """
        Seals the active visual session into a canonical SDC master payload envelope.
        - Flushes confirmed filings if portal submission captures exist (rank >= 2 with ARN).
        - If no confirmed filing was captured, returns None (no dummy session records).
        """
        if self._flushed:
            return None

        valid_records = [r for r in self.records.values() if self._is_dataset_complete(r)]
        if not valid_records:
            return None

        self._flushed = True
        primary = valid_records[0]

        master_payload = {
            "source": "vsdc_optical",
            "session_id": self.session_id,
            "portal": primary.portal,
            "pan": primary.entity_pan,
            "gstin": primary.gstin,
            "client_name": primary.client_name,
            "trade_name": primary.trade_name,
            "filing_type": primary.filing_type,
            "period_label": primary.period_label,
            "fy": primary.fy,
            "due_date": primary.due_date,
            "dob": primary.dob,
            "mobile": primary.mobile,
            "email": primary.email,
            "filing_preference": primary.filing_preference,
            "arn": primary.arn,
            "status": primary.status,
            "raw_text": primary.raw_text,
            "capture_method": primary.capture_method,
            "raw_payload": {
                "source": {
                    "protocol": primary.portal,
                    # Which engine actually supplied this capture, so the stored payload
                    # agrees with capture_method instead of always claiming plain VSDC.
                    "engine": "VSDC-X" if (primary.capture_method or "").startswith("VSDC-X") else "VSDC",
                    "version": "1.0.0",
                },
                "raw_text": primary.raw_text,
                "client_temp_name": primary.client_name,
                "trade_name": primary.trade_name,
                "assembler_captures": [r.to_dict() for r in valid_records],
                "timeline": list(self.session.steps),
            },
        }

        if self._session_started and valid_records:
            self.logger.log_milestone(
                "SESSION SEALED",
                f"Master Payload Sealed with {len(valid_records)} Return(s)",
                {
                    "arn": primary.arn,
                    "status": primary.status,
                    "filing_type": primary.filing_type,
                    "period": primary.period_label,
                },
            )

        # Clear active workflow variables to prevent bleeding into subsequent screens
        self.current_filing_type = None
        self.current_period_label = None
        self.fy = None
        self.due_date = None
        self.records.clear()
        self.captures.clear()
        return master_payload
