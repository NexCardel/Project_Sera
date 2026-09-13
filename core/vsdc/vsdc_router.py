"""
core/vsdc/vsdc_router.py — Zero-CPU Route Sniffer & Crosshair Dispatcher
========================================================================
Monitors the foreground window, uses Windows UI Automation (UIAutomationCore.dll)
to inspect the browser address bar in < 0.5ms with 0.0% CPU, matches routes against
the SDC crosshair catalog, and delegates to the visual OCR and assembler pipeline.
"""

import ctypes
from ctypes import wintypes
import time
from typing import Dict, List, Optional, Tuple, Any, Callable

import comtypes.client
from .vsdc_crosshairs import match_url_crosshair, CrosshairDefinition, ALL_CROSSHAIRS
from .vsdc_ocr import VSDCOcrEngine
from .vsdc_assembler import VisualSessionAssembler, get_status_rank
from .vsdc_regex import (
    repair_numeric_ack,
    repair_gst_arn,
    extract_pan,
    extract_gstin,
    extract_assessment_year,
    extract_filing_type,
    classify_verification_status,
    is_page_loading,
    extract_view_filed_returns_card,
)
from .vsdc_name_parser import extract_name_from_ocr_lines, parse_human_name

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PORTAL_KEYWORDS = (
    "income tax",
    "incometax",
    "e-filing",
    "gst portal",
    "goods and services tax",
    "gstn",
    "traces",
    "mca",
    "dashboard",
    "returns",
)

BROWSER_EXE_NAMES = (
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
)


class VSDCRouter:
    """
    Coordinates browser detection, URL route sniffing via Windows UI Automation,
    regional OCR capture, and session assembly.
    """

    def __init__(
        self,
        ocr_engine: Optional[VSDCOcrEngine] = None,
        assembler: Optional[VisualSessionAssembler] = None,
        on_activity: Optional[Callable[[str, str, str], None]] = None,
    ):
        self.ocr = ocr_engine or VSDCOcrEngine()
        self.assembler = assembler or VisualSessionAssembler()
        self.on_activity = on_activity

        self._uia = None
        self._uia_client = None
        self._init_uia()

        self.last_url: str = ""
        self.last_crosshair_id: Optional[str] = None
        self.last_hwnd: int = 0
        self.active_portal: str = "Income Tax"
        self.has_flushed_current_route: bool = False
        self.route_poll_count: int = 0
        self.last_screen_hash: Optional[int] = None
        self.last_logged_name: Optional[str] = None
        self.last_logged_pan: Optional[str] = None

    def notify_activity(self, event_type: str, title: str, subtitle: str = ""):
        """Emits real-time visual indicator event (route, identity, capture, flush)."""
        if self.on_activity and callable(self.on_activity):
            try:
                self.on_activity(event_type, title, subtitle)
            except Exception as e:
                print(f"[VSDC Router] on_activity callback error: {e}")

    def _init_uia(self):
        try:
            self._uia_client = comtypes.client.GetModule("UIAutomationCore.dll")
            self._uia = comtypes.client.CreateObject(
                self._uia_client.CUIAutomation,
                interface=self._uia_client.IUIAutomation,
            )
        except Exception as e:
            print(f"⚠️ VSDC Router: UI Automation init warning: {e}")
            self._uia = None

    def get_foreground_info(self) -> Tuple[int, str, str]:
        """
        Returns (hwnd, window_title, process_name) of the current foreground window.
        """
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0, "", ""

        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value.strip()

        # Get process executable name
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        proc_name = ""
        if hproc:
            name_buff = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            # QueryFullProcessImageNameW
            if kernel32.QueryFullProcessImageNameW(hproc, 0, name_buff, ctypes.byref(size)):
                full_path = name_buff.value
                proc_name = full_path.split("\\")[-1].lower()
            kernel32.CloseHandle(hproc)

        return hwnd, title, proc_name

    def extract_browser_url(self, hwnd: int) -> Optional[str]:
        """
        Reads the active browser address bar URL in < 0.5ms via UIAutomationCore.
        """
        if not self._uia or not hwnd:
            return None

        try:
            element = self._uia.ElementFromHandle(hwnd)
            if not element:
                return None

            # Look for Edit control (Address bar)
            # UIA_EditControlTypeId = 50004
            condition = self._uia.CreatePropertyCondition(
                self._uia_client.UIA_ControlTypePropertyId,
                50004,
            )
            edits = element.FindAll(self._uia_client.TreeScope_Descendants, condition)
            if not edits:
                return None

            count = edits.Length
            for i in range(count):
                edit = edits.GetElement(i)
                try:
                    name = (edit.CurrentName or "").lower()
                    auto_id = (edit.CurrentAutomationId or "").lower()
                    # Address bar indicators across Chrome, Edge, Brave, Firefox
                    if any(k in name for k in ("address", "search", "url")) or any(k in auto_id for k in ("address", "url", "view_")):
                        val_pattern = edit.GetCurrentPattern(self._uia_client.UIA_ValuePatternId)
                        if val_pattern:
                            val_obj = val_pattern.QueryInterface(self._uia_client.IUIAutomationValuePattern)
                            url_val = val_obj.CurrentValue
                            if url_val and ("." in url_val or "/" in url_val):
                                return url_val
                except Exception:
                    continue

        except Exception as e:
            # Silently handle transient UIA errors (e.g. during alt-tab)
            pass

        return None

    def evaluate_tick(self) -> Optional[Dict[str, Any]]:
        """
        Executes a single monitoring tick (called every 1–2s by worker).
        Returns completed master payload dictionary if a filing is finalized, else None.
        """
        hwnd, title, proc_name = self.get_foreground_info()
        if not hwnd or not title:
            return None

        title_lower = title.lower()

        # Quick gate: Is foreground window a tax portal or browser?
        is_portal_title = any(k in title_lower for k in PORTAL_KEYWORDS)
        is_browser = any(b in proc_name for b in BROWSER_EXE_NAMES)

        if not (is_portal_title or is_browser):
            return None

        # Determine portal dialect
        if "gst" in title_lower or "goods and services" in title_lower:
            self.active_portal = "GST Portal"
        elif "income tax" in title_lower or "e-filing" in title_lower or "itr" in title_lower:
            self.active_portal = "Income Tax"

        # Read active URL from browser address bar
        extracted_url = self.extract_browser_url(hwnd)
        if extracted_url:
            url = extracted_url
        else:
            # Strip session countdown timers and browser suffixes so title doesn't change every second
            cleaned_title = re.sub(r"\b(?:session\s*time|time\s*remaining|timeout)\b[:\s0-9]+", "", title_lower, flags=re.IGNORECASE)
            cleaned_title = re.sub(r"\b\d{1,2}\s*:\s*\d{2}\b", "", cleaned_title)
            cleaned_title = re.sub(r"-\s*(?:google chrome|microsoft edge|mozilla firefox|brave|opera).*$", "", cleaned_title, flags=re.IGNORECASE).strip()
            url = cleaned_title or title_lower

        url_normalized = url.replace("\\", "/")

        is_new_url = (url != self.last_url)
        is_new_window = (hwnd != self.last_hwnd)

        if is_new_url or is_new_window:
            self.last_url = url
            self.last_hwnd = hwnd
            self.has_flushed_current_route = False
            self.route_poll_count = 0
            self.last_screen_hash = None

        # Match against SDC Crosshairs
        matched_crosshair = match_url_crosshair(url_normalized)
        if not matched_crosshair and url != url_normalized:
            matched_crosshair = match_url_crosshair(url)
        if not matched_crosshair:
            # Fallback: check window title against crosshair patterns
            matched_crosshair = match_url_crosshair(title)

        if not matched_crosshair:
            return None

        # If on the same URL and we ALREADY captured & flushed the filing for this route: sleep (0.0% CPU)
        if not is_new_url and self.has_flushed_current_route:
            return None

        # Cap polling attempts on the same route if nothing has changed
        if not is_new_url and self.route_poll_count >= 15:
            return None

        self.route_poll_count += 1

        if matched_crosshair.id != self.last_crosshair_id or is_new_url:
            self.last_crosshair_id = matched_crosshair.id
            self.assembler.record_step(url, matched_crosshair.id)
            label_text = getattr(matched_crosshair, "label", getattr(matched_crosshair, "description", matched_crosshair.id))
            # Route changes are console-only; toast only fires for identity/capture/flush
            print(f"[VSDC Router] Route: {label_text} | Portal: {self.active_portal}")

        # Handle session boundaries (Login / Logout / Timeout)
        if matched_crosshair.is_session_boundary:
            flushed = self.assembler.seal_and_flush()
            self.assembler.reset()
            self.has_flushed_current_route = True
            self.last_logged_name = None
            self.last_logged_pan = None
            self.last_screen_hash = None
            self.last_crosshair_id = None
            self.route_poll_count = 0
            if flushed and flushed.get("pan"):
                self.notify_activity("flush", "Session Concluded", f"Archived {flushed.get('pan')} • {flushed.get('filing_type', 'Activity')}")
            return flushed

        # Handle login screen boundary: navigating back to login page terminates any prior session
        if matched_crosshair.id == "itr_login_auth" or "/login" in url.lower():
            if self.assembler.client_pan or self.assembler.captures or self.assembler.current_filing_type:
                flushed = self.assembler.seal_and_flush()
                self.assembler.reset()
                self.last_logged_name = None
                self.last_logged_pan = None
                self.last_screen_hash = None
                self.last_crosshair_id = None
                self.route_poll_count = 0
                if flushed and flushed.get("pan"):
                    self.notify_activity("flush", "Prior Session Concluded", f"{flushed.get('pan')}")
                    return flushed

        # Hub / Dashboard boundary: clear active workflow selection when returning to landing/dashboard
        if matched_crosshair.id in ("itr_landing", "gst_welcome_calendar", "gst_returns_dashboard"):
            if self.assembler._flushed or not self.assembler.captures:
                self.assembler.clear_workflow_selection()

        # Capture target region and execute OCR
        img = self.ocr.capture_window_image(hwnd)
        if not img:
            return None

        # Fast screen-change check:
        # Resize content below header (ignoring live session countdown timer) to 32x32 thumbnail
        try:
            w, h = img.size
            content_crop = img.crop((0, int(h * 0.15), w, h)) if h > 100 else img
            thumb = content_crop.resize((32, 32)).tobytes()
            curr_hash = hash(thumb)
            if not is_new_url and self.last_screen_hash == curr_hash and self.route_poll_count > 3:
                return None
            self.last_screen_hash = curr_hash
        except Exception:
            pass

        ocr_res = self.ocr.scan_image(img, region_type=matched_crosshair.target_crop)
        full_text = ocr_res.get("text", "")
        lines = ocr_res.get("lines", [])

        # Process extracted fields based on crosshair type
        pan = extract_pan(full_text)
        gstin = extract_gstin(full_text)

        # On login authentication (password) page: strictly capture PAN only.
        # Never extract or register name candidates from the password / secure access message page
        # to prevent breadcrumb spamming and bogus name seeding.
        is_login_auth = (matched_crosshair.id == "itr_login_auth")
        if is_login_auth:
            client_name = None
        else:
            client_name = extract_name_from_ocr_lines(lines)

        # On login authentication page, if PAN is not in cropped card, fallback to full image
        if not pan and is_login_auth:
            full_res = self.ocr.scan_image(img, region_type="full")
            f_text = full_res.get("text", "")
            pan = extract_pan(f_text)
            if pan:
                print(f"[VSDC Router] Captured PAN {pan} from full login screen")

        # If identity (PAN / GSTIN / Name) is not present in target crop (e.g. landing directly
        # on receipt card), or if extracted name is incomplete (fewer than 2 words), scan the header region!
        name_incomplete = not client_name or len(client_name.split()) < 2
        pan_missing = not pan and not self.assembler.client_pan
        # For Personal Info page the center_card already contains the full name table (avoid header truncated pill).
        # For Login Auth page, we strictly do NOT scan header for name.
        _skip_header_for_name = matched_crosshair.id in ("itr_personal_info", "itr_login_auth")
        if pan_missing or (name_incomplete and not self.assembler.client_name and not _skip_header_for_name):
            if matched_crosshair.target_crop != "header":
                header_res = self.ocr.scan_image(img, region_type="header")
                h_text = header_res.get("text", "")
                h_lines = header_res.get("lines", [])
                h_pan = extract_pan(h_text)
                h_gstin = extract_gstin(h_text)
                h_name = extract_name_from_ocr_lines(h_lines)
                if h_name:
                    if not client_name or len(h_name.split()) > len(client_name.split()):
                        client_name = h_name
                if h_pan and not pan:
                    pan = h_pan
                if h_gstin and not gstin:
                    gstin = h_gstin
        # If we skipped header for name but still need PAN, scan header for PAN only
        elif pan_missing and _skip_header_for_name:
            if matched_crosshair.target_crop != "header":
                header_res = self.ocr.scan_image(img, region_type="header")
                h_text = header_res.get("text", "")
                h_pan = extract_pan(h_text)
                h_gstin = extract_gstin(h_text)
                if h_pan and not pan:
                    pan = h_pan
                if h_gstin and not gstin:
                    gstin = h_gstin

        if client_name and not is_login_auth:
            self.assembler.update_identity(name=client_name)
            authoritative_name = self.assembler.client_name or client_name
            is_better_name = not self.last_logged_name or len(authoritative_name.split()) > len(self.last_logged_name.split())
            if is_better_name and authoritative_name != self.last_logged_name:
                print(f"[VSDC Router] Extracted client name: {authoritative_name}")
                self.last_logged_name = authoritative_name

        if pan or gstin:
            flushed_prior = self.assembler.update_identity(pan=pan, gstin=gstin, portal=self.active_portal)
            c_name = self.assembler.client_name or ""
            if pan and pan != self.last_logged_pan:
                print(f"[VSDC Router] Identity updated: PAN={pan}")
                self.notify_activity("identity", f"Assessee: {pan}", f"{c_name}" if c_name else f"Portal: {self.active_portal}")
                self.last_logged_pan = pan
            elif gstin and gstin != self.last_logged_pan:
                print(f"[VSDC Router] Identity updated: GSTIN={gstin}")
                self.notify_activity("identity", f"GSTIN: {gstin}", f"{c_name}" if c_name else f"Portal: {self.active_portal}")
                self.last_logged_pan = gstin
            if flushed_prior:
                print(f"[VSDC Router] Flushed prior client session due to PAN context switch!")
                return flushed_prior

        filing_type = extract_filing_type(full_text)
        period = extract_assessment_year(full_text)
        if filing_type or period:
            self.assembler.update_selection(filing_type=filing_type, period_label=period)

        # Check for terminal filing confirmation (Ack / ARN)
        ack_number = None
        if self.active_portal == "Income Tax":
            ack_number = repair_numeric_ack(full_text)
        else:
            ack_number = repair_gst_arn(full_text) or repair_numeric_ack(full_text)

        # Fallback to full image scan if Ack was not found in cropped card on a return/submission card!
        if not ack_number and (matched_crosshair.is_terminal_submission or matched_crosshair.id == "itr_view_filed_returns"):
            if matched_crosshair.target_crop != "full":
                full_res = self.ocr.scan_image(img, region_type="full")
                f_text = full_res.get("text", "")
                f_ack = repair_numeric_ack(f_text) if self.active_portal == "Income Tax" else (repair_gst_arn(f_text) or repair_numeric_ack(f_text))
                if f_ack:
                    ack_number = f_ack
                    full_text = full_text + "\n" + f_text
                    if not filing_type:
                        filing_type = extract_filing_type(f_text)
                    if not period:
                        period = extract_assessment_year(f_text)

        # Check if page is currently in an asynchronous loading state
        if is_page_loading(full_text):
            return None

        # Dedicated parser for Historical Filed Returns (itr_view_filed_returns)
        if matched_crosshair.id == "itr_view_filed_returns":
            card = extract_view_filed_returns_card(full_text)
            if not card:
                full_res = self.ocr.scan_image(img, region_type="full")
                f_text = full_res.get("text", "")
                if is_page_loading(f_text):
                    return None
                card = extract_view_filed_returns_card(f_text)
                if card:
                    full_text = full_text + "\n" + f_text

            if not card or not card.get("ack") or not card.get("ay"):
                return None  # Screen still loading or 0 filings, do not emit dummy

            ack_number = card["ack"]
            period = card["ay"]
            filing_type = card["form"]
            status = card["status"]

        is_sub_crosshair = matched_crosshair.is_terminal_submission or matched_crosshair.id in (
            "itr_filed_verified",
            "itr_everify_return",
            "itr_submitted_pending",
            "gst_filing_success",
            "gst_filing_file_success",
        )

        # STRICT MANDATE: An optical submission MUST have a valid Acknowledgement Number / ARN
        if ack_number and ack_number != "N/A":
            if matched_crosshair.id != "itr_view_filed_returns":
                status = classify_verification_status(full_text)
            if get_status_rank(status) >= 2:
                self.assembler.record_submission(
                    ack_number=ack_number,
                    status=status,
                    filing_type=filing_type,
                    period_label=period,
                    raw_text=full_text[:2000],
                    crosshair_id=matched_crosshair.id,
                )
                form_lbl = filing_type or self.assembler.current_filing_type or "Return"
                period_lbl = f" • {period or self.assembler.current_period_label}" if (period or self.assembler.current_period_label) else ""
                assessee = self.assembler.client_name or ""
                name_part = f"{assessee} • " if assessee else ""
                # Toast title: form + period; subtitle: name + ack
                self.notify_activity("capture", f"Captured {form_lbl}{period_lbl}", f"{name_part}Ack: {ack_number}")

                if is_sub_crosshair:
                    # Seal and flush filing payload!
                    master_payload = self.assembler.seal_and_flush()
                    if master_payload:
                        self.has_flushed_current_route = True
                        self.assembler.clear_workflow_selection()
                        flush_name = master_payload.get("client_name") or master_payload.get("pan", "")
                        print(f"[VSDC Router] Flushed filing payload to tracker dump: Ack={ack_number} Form={form_lbl} Status={status}")
                        self.notify_activity("flush", "Filing Saved to Tracker Dump", f"{flush_name} • {form_lbl}")
                    return master_payload

        # Dataset Completion Principle:
        # A dataset completes ONLY when submit status is captured from the portal!
        completed_payload = self.assembler.get_completed_dataset_payload(crosshair_id=matched_crosshair.id)
        if completed_payload:
            c_form = completed_payload.get("filing_type", "Return")
            c_period = f" • {completed_payload.get('period_label')}" if completed_payload.get("period_label") else ""
            c_status = completed_payload.get("status", "Submitted")
            c_pan = completed_payload.get("pan", "")
            c_name = completed_payload.get("client_name") or c_pan
            print(f"[VSDC Router] Dataset completed & shot to app: PAN={c_pan} Form={c_form}{c_period} Status={c_status}")
            self.notify_activity("capture", f"Dataset: {c_form}{c_period}", f"{c_name} • {c_status}")
            return completed_payload

        return None
