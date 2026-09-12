"""
core/vsdc/vsdc_assembler.py — Visual Session Assembler (1-to-1 SDC Parity)
==========================================================================
Maintains an in-memory session state machine that aggregates multi-screen visual
captures (Landing -> Form/Period -> Submission Receipt) and emits the canonical
multi-dataset master payload directly into database.py (tracker_dump).
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime


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
    """Returns numeric priority rank for filing statuses (higher = more complete).
    Rank 0: Non-submission / In-Progress / Draft
    Rank 2: Submitted / Filing Confirmed
    Rank 3: Submitted (Pending e-Verification / Not e-Verified)
    Rank 4: Submitted & e-Verified / Filed
    Rank 5: Processed
    """
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


class VisualSessionAssembler:
    """
    Assembles multi-screen visual crosshairs into unified filing envelopes.
    Mirror of sdc_assembler from sdc_core.js.
    """

    def __init__(self):
        self.session_id: str = f"vsdc_sess_{uuid.uuid4().hex[:12]}"
        self.portal: str = "Income Tax"
        self.client_pan: Optional[str] = None
        self.client_name: Optional[str] = None
        self.gstin: Optional[str] = None
        self.current_filing_type: Optional[str] = None
        self.current_period_label: Optional[str] = None
        self.captures: Dict[str, Dict[str, Any]] = {}
        self.steps: List[Dict[str, Any]] = []
        self.last_activity: float = time.time()
        self._flushed: bool = False
        self._emitted_datasets: Dict[str, Tuple[str, str, str]] = {}  # {dataset_key: (status, arn, client_name)}

    def reset(self, new_pan: Optional[str] = None):
        """
        Resets active session state for a fresh client session.
        """
        self.session_id = f"vsdc_sess_{uuid.uuid4().hex[:12]}"
        self.client_pan = new_pan
        self.client_name = None
        self.gstin = None
        self.current_filing_type = None
        self.current_period_label = None
        self.captures.clear()
        self.steps.clear()
        self.last_activity = time.time()
        self._flushed = False
        self._emitted_datasets.clear()

    def clear_workflow_selection(self):
        """
        Clears active filing form selection, period, and pending unsubmitted captures
        when a filing workflow concludes or when navigating to boundary/hub screens,
        strictly preventing data bleeding into subsequent workflows or views.
        """
        self.current_filing_type = None
        self.current_period_label = None
        self.captures.clear()
        self._flushed = False

    def record_step(self, route_url: str, crosshair_id: str, details: Optional[Dict[str, Any]] = None):
        """
        Records a navigation step in the visual session timeline.
        """
        self.last_activity = time.time()
        step = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": route_url,
            "crosshair_id": crosshair_id,
            "details": details or {},
        }
        self.steps.append(step)

    def update_identity(self, pan: Optional[str] = None, name: Optional[str] = None, gstin: Optional[str] = None, portal: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Updates taxpayer identity attributes. Enforces PAN context switch guard.
        Returns sealed prior session payload if a context switch occurred, else None.
        """
        self.last_activity = time.time()
        flushed_prior = None

        if portal:
            self.portal = portal

        if pan:
            pan_clean = pan.strip().upper()
            if self.client_pan and self.client_pan != pan_clean:
                # Context switch detected! Seal prior session before switching
                flushed_prior = self.seal_and_flush()
                self.reset(new_pan=pan_clean)
            else:
                self.client_pan = pan_clean

        if name:
            new_name = name.strip().upper()
            if not self.client_name:
                self.client_name = new_name
            else:
                existing_words = self.client_name.split()
                new_words = new_name.split()
                # Do not downgrade a full name to a partial fragment or truncated version
                # (e.g. preserve 'WASIL AMAN MANDAL' if incoming is 'WASIL MANDAL', 'WASIL AMAN', or 'WASIL AMAN MAND')
                is_downgrade = False
                if len(new_words) < len(existing_words):
                    if set(new_words).issubset(set(existing_words)) or new_name in self.client_name:
                        is_downgrade = True
                elif len(new_words) == len(existing_words):
                    if all(ew.startswith(nw) for nw, ew in zip(new_words, existing_words)):
                        if len(new_name.replace(' ', '')) < len(self.client_name.replace(' ', '')):
                            is_downgrade = True

                if not is_downgrade:
                    self.client_name = new_name
        if gstin:
            self.gstin = gstin.strip().upper()

        return flushed_prior

    def update_selection(self, filing_type: Optional[str] = None, period_label: Optional[str] = None):
        """
        Records the active form type and assessment year / return period.
        If a new filing form is selected, any prior unsubmitted draft for this assessee & period
        is superseded to ensure only one active draft return per period.
        """
        self.last_activity = time.time()
        if filing_type:
            new_ft = filing_type.strip().upper()
            if self.current_filing_type and self.current_filing_type != new_ft:
                # User switched filing forms!
                # If the previous form had an unsubmitted draft, purge it from active captures
                # so it does not linger or co-exist as multiple drafts for the same period.
                old_ft = self.current_filing_type
                period = self.current_period_label
                entity_id = self.client_pan or self.gstin or "UNKNOWN"
                if period:
                    old_key = f"{entity_id}|{old_ft}|{period}"
                    old_cap = self.captures.get(old_key)
                    if old_cap and get_status_rank(old_cap.get("status")) <= 0:
                        del self.captures[old_key]
                    if old_key in self._emitted_datasets:
                        del self._emitted_datasets[old_key]
            self.current_filing_type = new_ft
        if period_label:
            self.current_period_label = period_label.strip()

    def record_submission(
        self,
        ack_number: str,
        status: str,
        filing_type: Optional[str] = None,
        period_label: Optional[str] = None,
        raw_text: Optional[str] = None,
        crosshair_id: str = "vsh_submission",
    ) -> Dict[str, Any]:
        """
        Records a verified or submitted filing acknowledgement.
        Constructs a dataset item keyed by (PAN/GSTIN | FilingType | Period).
        """
        self.last_activity = time.time()
        ft = filing_type or self.current_filing_type or ("Return" if self.portal != "Income Tax" else "ITR")
        if not period_label and not self.current_period_label:
            if self.portal == "Income Tax":
                now = datetime.now()
                curr_year = now.year if now.month >= 4 else now.year - 1
                period = f"AY {curr_year + 1}-{str(curr_year + 2)[2:]}"
            else:
                period = datetime.now().strftime("%B %Y")
        else:
            period = period_label or self.current_period_label
        entity_id = self.client_pan or self.gstin or "UNKNOWN"

        dataset_key = f"{entity_id}|{ft}|{period}"

        # Monotonicity Guard: Status promotes, it does not demote!
        existing = self.captures.get(dataset_key)
        if existing:
            if get_status_rank(existing.get("status")) > get_status_rank(status):
                status = existing.get("status", status)
            if (not ack_number or ack_number == "N/A") and existing.get("ack_number") and existing.get("ack_number") != "N/A":
                ack_number = existing.get("ack_number")

        item = {
            "dataset_key": dataset_key,
            "pan": self.client_pan or "",
            "gstin": self.gstin or "",
            "client_name": self.client_name or "",
            "filing_type": ft,
            "period_label": period,
            "arn": ack_number,
            "ack_number": ack_number,
            "status": status,
            "portal": self.portal,
            "capture_method": f"VSDC_{crosshair_id}",
            "filing_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_text": raw_text or "",
            "session_id": self.session_id,
        }
        self.captures[dataset_key] = item
        return item

    def get_completed_dataset_payload(self, crosshair_id: str = "vsdc_dataset_completion") -> Optional[Dict[str, Any]]:
        """
        Dataset Completion Principle:
        The dataset completes ONLY when submit status is captured from the portal.
        Requires entity identifier (PAN/GSTIN), authoritative client name, tax period/AY,
        filing form, and an authoritative portal submission status (rank > 0).
        Deduplicates against already emitted (status, arn, client_name) triplets to prevent spam.
        """
        entity_id = self.client_pan or self.gstin
        name = self.client_name
        period = self.current_period_label
        form = self.current_filing_type

        # Must have entity identifier, client name, tax period/AY, and filing form
        if not entity_id or not name or not period or not form:
            return None

        dataset_key = f"{entity_id}|{form}|{period}"

        # Submit status MUST be captured from the portal
        sub_capture = self.captures.get(dataset_key)
        if not sub_capture:
            return None

        status = sub_capture.get("status")
        if not status or get_status_rank(status) <= 0:
            return None

        arn = sub_capture.get("ack_number") or sub_capture.get("arn")
        if not arn or arn == "N/A":
            return None
        capture_method = sub_capture.get("capture_method", f"VSDC_{crosshair_id}")
        raw_text = sub_capture.get("raw_text", f"Filing confirmed for {name} ({entity_id}). Form: {form}, Period: {period}, Status: {status}.")

        prev_emitted = self._emitted_datasets.get(dataset_key)
        if prev_emitted:
            prev_status, prev_arn, _ = prev_emitted
            if get_status_rank(prev_status) > get_status_rank(status):
                status = prev_status
                if arn == "N/A" and prev_arn != "N/A":
                    arn = prev_arn

        fingerprint = (status, arn, name)
        if self._emitted_datasets.get(dataset_key) == fingerprint:
            return None

        capture_item = {
            "dataset_key": dataset_key,
            "pan": self.client_pan or "",
            "gstin": self.gstin or "",
            "client_name": name,
            "filing_type": form,
            "period_label": period,
            "arn": arn,
            "ack_number": arn,
            "status": status,
            "portal": self.portal,
            "capture_method": capture_method,
            "filing_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_text": raw_text,
            "session_id": self.session_id,
        }

        if dataset_key not in self.captures:
            self.captures[dataset_key] = capture_item

        payload = {
            "source": "vsdc_optical",
            "session_id": self.session_id,
            "portal": self.portal,
            "pan": self.client_pan or "",
            "gstin": self.gstin or "",
            "client_name": name,
            "filing_type": form,
            "period_label": period,
            "arn": arn,
            "status": status,
            "capture_method": capture_method,
            "raw_payload": {
                "source": {
                    "protocol": self.portal,
                    "engine": "VSDC",
                    "version": "1.0.0",
                },
                "client_temp_name": name,
                "assembler_captures": [capture_item],
                "timeline": list(self.steps),
            },
        }

        self._emitted_datasets[dataset_key] = fingerprint
        return payload

    def seal_and_flush(self) -> Optional[Dict[str, Any]]:
        """
        Seals the active visual session into a canonical SDC master payload envelope.
        - Flushes confirmed filings if portal submission captures exist.
        - If no confirmed filing was captured, returns None (no dummy session records).
        """
        if self._flushed:
            return None

        if not self.captures:
            return None

        valid_captures = [
            c for c in self.captures.values()
            if get_status_rank(c.get("status")) >= 2
            and c.get("arn") not in (None, "", "N/A")
            and c.get("ack_number") not in (None, "", "N/A")
        ]
        if not valid_captures:
            return None

        self._flushed = True
        primary = valid_captures[0]

        master_payload = {
            "source": "vsdc_optical",
            "session_id": self.session_id,
            "portal": self.portal,
            "pan": self.client_pan or primary.get("pan", ""),
            "gstin": self.gstin or primary.get("gstin", ""),
            "client_name": self.client_name or primary.get("client_name", ""),
            "filing_type": primary.get("filing_type", ""),
            "period_label": primary.get("period_label", ""),
            "arn": primary.get("arn", ""),
            "status": primary.get("status", "Submitted"),
            "capture_method": primary.get("capture_method", "VSDC_optical"),
            "raw_payload": {
                "source": {
                    "protocol": self.portal,
                    "engine": "VSDC",
                    "version": "1.0.0",
                },
                "client_temp_name": self.client_name or "",
                "assembler_captures": valid_captures,
                "timeline": list(self.steps),
            },
        }
        # Clear active workflow variables to avoid bleeding into subsequent screens
        self.current_filing_type = None
        self.current_period_label = None
        self.captures.clear()
        return master_payload
