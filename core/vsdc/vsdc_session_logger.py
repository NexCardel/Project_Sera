"""
core/vsdc/vsdc_session_logger.py — Human-Readable Session Capture Logger
========================================================================
Records the visual capture lifecycle into dedicated text files under
'Vsdc_Captures/' for live inspection, auditability, and debugging.

File Naming Standard:
  Vsdc_Captures/<Client Name> (<PAN>).txt
  Example: Vsdc_Captures/ARIF MOHAMMAD MOLLA (CJLPM0265M).txt

Lifecycle Flow:
  SESSION START -> MILESTONES (Routes, Form Selections, Captures, ARNs) -> SESSION END

Privacy Protection:
  Strictly compliant with client data protection rules. Only logs statutory
  metadata (PAN, Name, GSTIN, Form Type, Period/AY, Status, ARN, timestamps).
  NEVER logs personal identifiers (email, mobile, bank, password, etc.).
"""

import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def sanitize_filename(name: str) -> str:
    """Removes or replaces characters forbidden in Windows filenames."""
    if not name:
        return "UNKNOWN"
    # Replace characters forbidden in Windows: < > : " / \ | ? *
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name.strip())
    # Strip trailing spaces or dots (forbidden on Windows)
    cleaned = cleaned.rstrip('. ')
    return cleaned or "UNKNOWN"


class VSDCSessionLogger:
    """
    Thread-safe session audit logger writing real-time capture steps to disk.
    """

    def __init__(self, output_dir: Optional[str] = None):
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            # Primary: User profile ~/AmanAssociates_Sera/Vsdc_Captures (guaranteed writable)
            self.output_dir = Path.home() / "AmanAssociates_Sera" / "Vsdc_Captures"

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Fallback to temp directory if user directory creation fails
            import tempfile
            self.output_dir = Path(tempfile.gettempdir()) / "Vsdc_Captures"
            self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self.current_session_id: Optional[str] = None
        self.portal: str = "Unknown Portal"
        self.client_name: Optional[str] = None
        self.pan: Optional[str] = None
        self.gstin: Optional[str] = None
        self.filing_preference: Optional[str] = None

        self.current_file_path: Optional[Path] = None
        self._file_handle = None
        self._session_active: bool = False

    def _generate_target_filename(self) -> str:
        """Generates the canonical 'Client Name (PAN).txt' filename."""
        name_str = sanitize_filename(self.client_name or "UNKNOWN")
        pan_str = self.pan or (self.gstin[2:12] if (self.gstin and len(self.gstin) >= 12) else None) or self.gstin or "UNKNOWN"
        pan_str = sanitize_filename(pan_str)
        return f"{name_str} ({pan_str}).txt"

    def _resolve_unique_filepath(self, target_filename: str) -> Path:
        """Resolves target path without overwriting previous sessions."""
        base_path = self.output_dir / target_filename
        if not base_path.exists():
            return base_path

        # If file exists, check if it belongs to current active session
        if self.current_file_path and self.current_file_path.name == target_filename:
            return base_path

        # File exists from an earlier completed session: create an indexed filename
        stem = base_path.stem
        idx = 2
        while True:
            candidate = self.output_dir / f"{stem} ({idx}).txt"
            if not candidate.exists():
                return candidate
            idx += 1

    def _write_line(self, line: str = ""):
        """Writes a line to the current log file and immediately flushes to disk."""
        if self._file_handle and not self._file_handle.closed:
            try:
                self._file_handle.write(line + "\n")
                self._file_handle.flush()
            except Exception:
                pass

    def start_session(
        self,
        session_id: str,
        portal: str,
        client_name: Optional[str] = None,
        pan: Optional[str] = None,
        gstin: Optional[str] = None,
        filing_preference: Optional[str] = None,
        initial_url: Optional[str] = None,
    ):
        """Starts a new session log file with header."""
        with self._lock:
            # Conclude any dangling active session
            if self._session_active:
                self._end_session_unlocked(reason="Preempted by new session")

            self.current_session_id = session_id
            self.portal = portal
            self.client_name = client_name
            self.pan = pan or (gstin[2:12] if (gstin and len(gstin) >= 12) else None)
            self.gstin = gstin
            self.filing_preference = filing_preference
            self._session_active = True

            filename = self._generate_target_filename()
            self.current_file_path = self._resolve_unique_filepath(filename)
            self._file_handle = open(self.current_file_path, "a", encoding="utf-8")

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._write_line("=" * 80)
            self._write_line("SERA VSDC AUDIT CAPTURE LOG")
            self._write_line("=" * 80)
            self._write_line(f"Session ID       : {session_id}")
            self._write_line(f"Portal           : {portal}")
            self._write_line(f"Client Name      : {self.client_name or 'PENDING IDENTIFICATION'}")
            self._write_line(f"PAN              : {self.pan or 'PENDING'}")
            if self.gstin:
                self._write_line(f"GSTIN            : {self.gstin}")
            if self.filing_preference:
                self._write_line(f"Filing Preference: {self.filing_preference}")
            self._write_line(f"Started At       : {now_str}")
            self._write_line(f"Status           : Active")
            self._write_line("=" * 80)
            self._write_line()
            self._write_line(f"[{now_str}] [SESSION START]")
            self._write_line(f"  - Portal       : {portal}")
            if initial_url:
                self._write_line(f"  - Initial URL  : {initial_url}")
            if self.client_name or self.pan or self.gstin:
                ident_str = f"{self.client_name or 'Client'} ({self.pan or self.gstin or 'UNKNOWN'})"
                self._write_line(f"  - Assessee     : {ident_str}")
            self._write_line()

    def update_identity(
        self,
        name: Optional[str] = None,
        pan: Optional[str] = None,
        gstin: Optional[str] = None,
        filing_preference: Optional[str] = None,
        portal: Optional[str] = None,
    ):
        """
        Updates taxpayer identity attributes and dynamically renames the log file
        if new authoritative name or PAN information is discovered.
        """
        with self._lock:
            if not self._session_active:
                return

            changed = False
            old_name = self.client_name
            old_pan = self.pan

            if name and name != self.client_name:
                self.client_name = name
                changed = True
            if gstin and gstin != self.gstin:
                self.gstin = gstin
                derived_pan = gstin[2:12] if len(gstin) >= 12 else None
                if derived_pan and not self.pan:
                    self.pan = derived_pan
                    changed = True
            if pan and pan != self.pan:
                self.pan = pan
                changed = True
            if filing_preference and filing_preference != self.filing_preference:
                self.filing_preference = filing_preference
                changed = True
            if portal and portal != self.portal:
                self.portal = portal

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if changed:
                self._write_line(f"[{now_str}] [IDENTITY UPDATED]")
                self._write_line(f"  - Client Name  : {self.client_name or 'N/A'}")
                self._write_line(f"  - PAN          : {self.pan or 'N/A'}")
                if self.gstin:
                    self._write_line(f"  - GSTIN        : {self.gstin}")
                if self.filing_preference:
                    self._write_line(f"  - Preference   : {self.filing_preference}")
                self._write_line()

                # Dynamic file rename if name or PAN resolved from UNKNOWN
                if (not old_name and self.client_name) or (not old_pan and self.pan):
                    new_filename = self._generate_target_filename()
                    if self.current_file_path and self.current_file_path.name != new_filename:
                        try:
                            self._file_handle.close()
                            new_path = self._resolve_unique_filepath(new_filename)
                            # Rename the existing file on disk
                            if self.current_file_path.exists():
                                self.current_file_path.rename(new_path)
                            self.current_file_path = new_path
                            self._file_handle = open(self.current_file_path, "a", encoding="utf-8")
                            self._write_line(f"[{now_str}] [FILE RENAMED] -> {new_filename}")
                            self._write_line()
                        except Exception:
                            # If rename fails (e.g. file lock), reopen current path
                            try:
                                self._file_handle = open(self.current_file_path, "a", encoding="utf-8")
                            except Exception:
                                pass

    def log_route_transition(self, crosshair_id: str, url: str, description: Optional[str] = None):
        """Logs browser navigation to a registered crosshair route."""
        with self._lock:
            if not self._session_active:
                return
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._write_line(f"[{now_str}] [ROUTE TRANSITION]")
            self._write_line(f"  - Crosshair ID : {crosshair_id}")
            self._write_line(f"  - URL          : {url}")
            if description:
                self._write_line(f"  - Description  : {description}")
            self._write_line()

    def log_milestone(self, category: str, title: str, details: Optional[Dict[str, Any]] = None):
        """
        Logs intermediate milestone captures (Form Selection, Form Table, Submission Receipt).
        category: e.g. 'FORM SELECTION', 'FORM CAPTURED', 'SUBMISSION / ARN', 'IN-PLACE REFINEMENT'
        """
        with self._lock:
            if not self._session_active:
                return
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._write_line(f"[{now_str}] [MILESTONE: {category.upper()}]")
            self._write_line(f"  - Event        : {title}")
            if details:
                for k, v in details.items():
                    if v not in (None, "", "N/A"):
                        # Format label cleanly
                        clean_key = k.replace("_", " ").title()
                        self._write_line(f"  - {clean_key:<13}: {v}")
            self._write_line()

    def end_session(self, reason: str = "User Logout", summary_items: Optional[List[Dict[str, Any]]] = None):
        """Concludes the session log and flushes summary block."""
        with self._lock:
            self._end_session_unlocked(reason=reason, summary_items=summary_items)

    def _end_session_unlocked(self, reason: str = "User Logout", summary_items: Optional[List[Dict[str, Any]]] = None):
        """Internal unlocked implementation of end_session."""
        if not self._session_active:
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write_line(f"[{now_str}] [SESSION END]")
        self._write_line(f"  - Concluded At : {now_str}")
        self._write_line(f"  - Reason       : {reason}")

        if summary_items:
            self._write_line(f"  - Captured Datasets ({len(summary_items)}):")
            for item in summary_items:
                form = item.get("filing_type", "Return")
                period = item.get("period_label", "")
                status = item.get("status", "N/A")
                arn = item.get("arn") or item.get("ack_number") or "N/A"
                self._write_line(f"    * {form} | Period: {period} | Status: {status} | ARN: {arn}")
        else:
            self._write_line("  - Captured Datasets : None submitted during this session.")

        self._write_line()
        self._write_line("=" * 80)
        self._write_line("SESSION CONCLUDED")
        self._write_line("=" * 80)
        self._write_line()

        try:
            if self._file_handle and not self._file_handle.closed:
                self._file_handle.flush()
                self._file_handle.close()
        except Exception:
            pass

        self._session_active = False
        self._file_handle = None
