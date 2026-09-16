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
import re
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
    extract_gst_filing_preference,
    extract_gst_form_table,
    extract_gst_fy,
    extract_gst_tax_period,
    extract_gst_status,
    is_valid_gst_tax_period,
    is_valid_gst_status,
    format_gst_period_label,
    resolve_gst_form_type_from_url,
    extract_gst_filing_date,
)
from .vsdc_name_parser import (
    extract_name_from_ocr_lines,
    parse_human_name,
    is_better_taxpayer_name,
    extract_gst_welcome_name,
)
from .vsdc_beeper import PANBeeper
from .vsdc_gemini_parser import parse_compliance_with_gemini

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
        self.route_captured: bool = False
        self.route_poll_count: int = 0
        self.last_screen_hash: Optional[int] = None
        self.last_logged_name: Optional[str] = None
        self.last_logged_pan: Optional[str] = None
        self.burst_ticks_remaining: int = 0
        self.was_page_loading: bool = False
        self._cached_address_elements: Dict[int, Any] = {}

    def notify_activity(self, event_type: str, title: str, subtitle: str = ""):
        """Emits real-time visual indicator event (route, identity, capture, flush)."""
        if self.on_activity and callable(self.on_activity):
            try:
                self.on_activity(event_type, title, subtitle)
            except Exception as e:
                print(f"[VSDC Router] on_activity callback error: {e}")

    def _init_uia(self):
        try:
            import sys
            import os
            if getattr(sys, "frozen", False):
                import tempfile
                gen_dir = os.path.join(tempfile.gettempdir(), "comtypes_gen")
                os.makedirs(gen_dir, exist_ok=True)
                comtypes.client.gen_dir = gen_dir

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
        Uses per-HWND UIA address bar element caching for sub-0.2ms retrieval.
        """
        if not self._uia or not hwnd:
            return None

        # Fast-path: Check cached address bar element for this hwnd (< 0.2ms)
        cached = self._cached_address_elements.get(hwnd)
        if cached:
            edit, val_obj = cached
            try:
                url_val = val_obj.CurrentValue
                if url_val and ("." in url_val or "/" in url_val):
                    return url_val
            except Exception:
                # Element is stale or invalidated (e.g. navigation or tab closed)
                self._cached_address_elements.pop(hwnd, None)

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
                                if len(self._cached_address_elements) > 20:
                                    self._cached_address_elements.clear()
                                self._cached_address_elements[hwnd] = (edit, val_obj)
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

        if is_new_url:
            self.last_url = url
            self.has_flushed_current_route = False
            self.route_captured = False
            self.route_poll_count = 0
            self.last_screen_hash = None
            self.was_page_loading = False
            self.burst_ticks_remaining = 10  # Instant micro-burst (15ms) before user can scroll!

        if is_new_window:
            self.last_hwnd = hwnd

        # Match against SDC Crosshairs
        matched_crosshair = match_url_crosshair(url_normalized)
        if not matched_crosshair and url != url_normalized:
            matched_crosshair = match_url_crosshair(url)
        if not matched_crosshair:
            # Fallback: check window title against crosshair patterns
            matched_crosshair = match_url_crosshair(title)

        if not matched_crosshair:
            return None

        # Data points do not change after the page loads.
        # Once data points render on screen and are captured, stop capturing further until URL changes!
        if not is_new_url and (self.route_captured or self.has_flushed_current_route):
            return None

        # Route evaluation tick

        self.route_poll_count += 1

        prior_crosshair = self.last_crosshair_id
        if matched_crosshair.id != self.last_crosshair_id or is_new_url:
            self.last_crosshair_id = matched_crosshair.id
            self.assembler.record_step(url, matched_crosshair.id)
            label_text = getattr(matched_crosshair, "label", getattr(matched_crosshair, "description", matched_crosshair.id))
            # Route changes are console-only; toast only fires for identity/capture/flush
            print(f"[VSDC Router] Route: {label_text} | Portal: {self.active_portal}")

        # Strict Protocol Separation:
        # Route directly to dedicated GST or ITR handlers so changes to one pipeline never alter or degrade the other.
        if matched_crosshair.protocol == "Income Tax" or matched_crosshair.id.startswith("itr_"):
            self.active_portal = "Income Tax"
            is_gst = False
        elif matched_crosshair.protocol == "GST Portal" or matched_crosshair.id.startswith("gst_"):
            self.active_portal = "GST Portal"
            is_gst = True
        else:
            is_gst = (self.active_portal == "GST Portal")

        if is_gst:
            return self._route_gst_crosshair(matched_crosshair, url, hwnd, is_new_url, prior_crosshair)
        else:
            return self._route_itr_crosshair(matched_crosshair, url, hwnd, is_new_url, prior_crosshair)

    def _route_itr_crosshair(
        self,
        matched_crosshair: CrosshairDefinition,
        url: str,
        hwnd: int,
        is_new_url: bool,
        prior_crosshair: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Dedicated handler for Income Tax e-Filing crosshairs.
        Processes login authentication, personal info profile, return form selection,
        view filed returns, e-verify workflows, and terminal submission pages.
        """
        # Handle session boundaries (Login / Logout / Timeout)
        if matched_crosshair.is_session_boundary or matched_crosshair.id == "itr_logout":
            has_active_session = bool(
                getattr(self.assembler, "_session_started", False)
                or self.assembler.client_pan
                or self.assembler.captures
                or self.assembler.current_filing_type
            )
            ident = self.assembler.client_name or self.assembler.client_pan or self.last_logged_name or self.last_logged_pan or "Client"
            flushed = self.assembler.seal_and_flush()
            if getattr(self.assembler, "_session_started", False):
                self.assembler.logger.end_session(reason="Income Tax Logout", summary_items=list(self.assembler.captures.values()))
                self.assembler._session_started = False
            self.assembler.reset()
            self.has_flushed_current_route = True
            self.last_logged_name = None
            self.last_logged_pan = None
            self.last_screen_hash = None
            self.last_crosshair_id = None
            self.route_poll_count = 0
            if has_active_session:
                self.notify_activity("logout", "Session Concluded", f"Archived: {ident}")
            return flushed

        # Handle login screen boundary: returning to login from an active session terminates the prior session
        # CRITICAL: Regex must NOT match 'preLogin' (used in public-facing /preLogin/viewFiledReturns URL).
        # Only the literal '#/login' or '/#/login/...' routes are actual authentication boundaries.
        _login_url_match = re.search(r"[/#]login(?:/password|/otp|/auth)?(?:[?/#]|$)", url, re.IGNORECASE)
        # Exclude false positives: '/preLogin' and '/prelogin' pages are NOT login boundaries
        _is_pre_login_url = bool(re.search(r"[/#]pre[_-]?login", url, re.IGNORECASE))
        is_login_route = (matched_crosshair.id == "itr_login_auth" or (bool(_login_url_match) and not _is_pre_login_url))
        # Strictly never treat view filed returns, personal info, landing, or form select as login route
        if matched_crosshair.id in ("itr_view_filed_returns", "itr_personal_info", "itr_landing", "itr_form_select"):
            is_login_route = False
        if is_login_route and prior_crosshair not in (None, "itr_login_auth"):
            if self.assembler.client_pan or self.assembler.captures or self.assembler.current_filing_type:
                ident = self.assembler.client_name or self.assembler.client_pan or self.last_logged_name or "Client"
                flushed = self.assembler.seal_and_flush()
                if getattr(self.assembler, "_session_started", False):
                    self.assembler.logger.end_session(reason="Income Tax Session Concluded (Redirected to Login)", summary_items=list(self.assembler.captures.values()))
                    self.assembler._session_started = False
                self.assembler.reset()
                self.last_logged_name = None
                self.last_logged_pan = None
                self.last_screen_hash = None
                self.route_poll_count = 0
                self.notify_activity("logout", "Session Concluded", f"Archived: {ident}")
                return flushed

        # Hub / Dashboard boundary: clear active workflow selection when returning to landing/dashboard
        if matched_crosshair.id == "itr_landing":
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
            if not is_new_url and self.last_screen_hash == curr_hash:
                if self.route_captured or self.has_flushed_current_route:
                    return None
                # If page was loading or route data not yet captured, allow continued polling
                if not self.was_page_loading and self.route_poll_count > 6:
                    return None
            if self.last_screen_hash is not None and self.last_screen_hash != curr_hash:
                # Viewport scrolled or content changed — reset poll count to evaluate new view
                self.route_poll_count = 0
            self.last_screen_hash = curr_hash
        except Exception:
            pass

        ocr_res = self.ocr.scan_image(img, region_type=matched_crosshair.target_crop)
        full_text = ocr_res.get("text", "")
        lines = ocr_res.get("lines", [])

        # Loading State Transition: If the page was previously in an async loading state
        # and has now completed loading, re-trigger a micro-burst so rendered data is captured instantly!
        if self.was_page_loading and not is_page_loading(full_text):
            self.was_page_loading = False
            self.burst_ticks_remaining = 8
            self.route_poll_count = 0
            print(f"[VSDC Router] ITR Page finished loading -> triggered burst capture!")

        # Process extracted fields based on crosshair type
        pan = extract_pan(full_text)
        gstin = extract_gstin(full_text)

        # On login authentication (password) page: strictly capture PAN only.
        # Never extract or register name candidates from the password / secure access message page
        # to prevent breadcrumb spamming and bogus name seeding.
        is_login_auth = (matched_crosshair.id == "itr_login_auth")
        is_personal_info = (matched_crosshair.id in ("itr_personal_info", "itr_profile"))
        if is_login_auth:
            client_name = None
        else:
            client_name = extract_name_from_ocr_lines(lines)

        # On personal info / profile page: if name wasn't detected in cropped center card, fallback to full image
        if not client_name and is_personal_info:
            full_res = self.ocr.scan_image(img, region_type="full")
            f_lines = full_res.get("lines", [])
            f_name = extract_name_from_ocr_lines(f_lines)
            if f_name:
                client_name = f_name
                full_text = full_text + "\n" + full_res.get("text", "")

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
        # Scan header if: PAN is missing, OR name is incomplete AND we are not on a page that skips header for name.
        # NOTE: We also scan header even when assembler already has a name, because the assembled name may be stale
        # or from a previous session (e.g. assembler.client_name is set from a prior session but current OCR has no name).
        # The is_better_taxpayer_name() guard ensures we only upgrade, never downgrade.
        _should_scan_header_for_name = name_incomplete and not _skip_header_for_name
        if pan_missing or _should_scan_header_for_name:
            if matched_crosshair.target_crop not in ("header", "top_right_profile"):
                # Use highly precise top right crop to prevent name spoofing from other screen areas
                header_res = self.ocr.scan_image(img, region_type="top_right_profile")
                h_text = header_res.get("text", "")
                h_lines = header_res.get("lines", [])
                h_pan = extract_pan(h_text)
                h_gstin = extract_gstin(h_text)
                h_name = extract_name_from_ocr_lines(h_lines)
                if h_name:
                    if not client_name or is_better_taxpayer_name(h_name, client_name):
                        client_name = h_name
                if h_pan and not pan:
                    pan = h_pan
                if h_gstin and not gstin:
                    gstin = h_gstin
        # If we skipped header for name but still need PAN, scan header for PAN only
        elif pan_missing and _skip_header_for_name:
            if matched_crosshair.target_crop not in ("header", "top_right_profile"):
                header_res = self.ocr.scan_image(img, region_type="top_right_profile")
                h_text = header_res.get("text", "")
                h_pan = extract_pan(h_text)
                h_gstin = extract_gstin(h_text)
                if h_pan and not pan:
                    pan = h_pan
                if h_gstin and not gstin:
                    gstin = h_gstin

        if client_name and not is_login_auth:
            self.assembler.update_identity(name=client_name, is_authoritative=is_personal_info)
            authoritative_name = self.assembler.client_name or client_name
            is_better_name = is_better_taxpayer_name(authoritative_name, self.last_logged_name)
            if (is_better_name or is_personal_info) and authoritative_name != self.last_logged_name:
                print(f"[VSDC Router] Extracted client name: {authoritative_name}")
                self.last_logged_name = authoritative_name
                # Trigger live HUD toast update with authoritative full name
                pan_label = self.assembler.client_pan or self.assembler.gstin or ""
                self.notify_activity(
                    "identity",
                    f"Assessee: {authoritative_name}",
                    f"PAN: {pan_label}" if pan_label else f"Portal: {self.active_portal}",
                )

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

        # Mark route captured when key data points for the matched route render on screen
        if is_login_auth and pan:
            self.route_captured = True
        elif matched_crosshair.id in ("itr_personal_info", "itr_profile") and (client_name or pan):
            self.route_captured = True

        filing_type = extract_filing_type(full_text)
        period = extract_assessment_year(full_text)
        if filing_type or period:
            self.assembler.update_selection(filing_type=filing_type, period_label=period)
            if matched_crosshair.id == "itr_form_selection":
                self.route_captured = True

        # Check for terminal filing confirmation (Ack / ARN)
        ack_number = repair_numeric_ack(full_text)

        # Fallback to full image scan if Ack was not found in cropped card on a return/submission card!
        if not ack_number and (matched_crosshair.is_terminal_submission or matched_crosshair.id == "itr_view_filed_returns"):
            if matched_crosshair.target_crop != "full":
                full_res = self.ocr.scan_image(img, region_type="full")
                f_text = full_res.get("text", "")
                f_ack = repair_numeric_ack(f_text)
                if f_ack:
                    ack_number = f_ack
                    full_text = full_text + "\n" + f_text
                    if not filing_type:
                        filing_type = extract_filing_type(f_text)
                    if not period:
                        period = extract_assessment_year(f_text)

        # Check if page is currently in an asynchronous loading state
        if is_page_loading(full_text):
            self.was_page_loading = True
            return None

        # Dedicated parser for Historical Filed Returns (itr_view_filed_returns)
        if matched_crosshair.id == "itr_view_filed_returns":
            card = extract_view_filed_returns_card(full_text)
            if not card:
                full_res = self.ocr.scan_image(img, region_type="full")
                f_text = full_res.get("text", "")
                if is_page_loading(f_text):
                    self.was_page_loading = True
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
            self.route_captured = True

        is_sub_crosshair = matched_crosshair.is_terminal_submission or matched_crosshair.id in (
            "itr_filed_verified",
            "itr_everify_return",
            "itr_submitted_pending",
        )

        # STRICT MANDATE: An optical submission MUST have a valid Acknowledgement Number
        if ack_number and ack_number != "N/A":
            if matched_crosshair.id != "itr_view_filed_returns":
                status = classify_verification_status(full_text)
            if get_status_rank(status) >= 2:
                self.route_captured = True
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
                        print(f"[VSDC Router] Flushed ITR filing payload to tracker dump: Ack={ack_number} Form={form_lbl} Status={status}")
                    return master_payload

        # Dataset Completion Principle:
        # A dataset completes ONLY when submit status is captured from the portal!
        completed_payload = self.assembler.get_completed_dataset_payload(crosshair_id=matched_crosshair.id)
        if completed_payload:
            self.route_captured = True
            c_form = completed_payload.get("filing_type", "Return")
            c_period = f" • {completed_payload.get('period_label')}" if completed_payload.get("period_label") else ""
            c_status = completed_payload.get("status", "Submitted")
            c_pan = completed_payload.get("pan", "")
            c_name = completed_payload.get("client_name") or c_pan
            print(f"[VSDC Router] Dataset completed & shot to app: PAN={c_pan} Form={c_form}{c_period} Status={c_status}")
            self.notify_activity("capture", f"Dataset: {c_form}{c_period}", f"{c_name} • {c_status}")
            return completed_payload

        return None

    def _route_gst_crosshair(
        self,
        matched_crosshair: CrosshairDefinition,
        url: str,
        hwnd: int,
        is_new_url: bool,
        prior_crosshair: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Dedicated handler for GST Common Portal crosshairs.
        Calibrated for gst_welcome_calendar (fowelcome), returns dashboard,
        form details (GSTR-1, GSTR-3B, CMP-08), and terminal submission screens.
        Completely decoupled from ITR crosshairs.
        """
        # Handle GST session boundaries (Logout / Timeout)
        if matched_crosshair.is_session_boundary or matched_crosshair.id == "gst_logout":
            ident = self.assembler.client_name or self.assembler.gstin or self.assembler.client_pan or self.last_logged_name or self.last_logged_pan or "Client"
            flushed = self.assembler.seal_and_flush()
            if getattr(self.assembler, "_session_started", False):
                self.assembler.logger.end_session(reason="GST Logout", summary_items=list(self.assembler.captures.values()))
                self.assembler._session_started = False
            self.assembler.reset()
            self.has_flushed_current_route = True
            self.last_logged_name = None
            self.last_logged_pan = None
            self.last_screen_hash = None
            self.last_crosshair_id = None
            self.route_poll_count = 0
            self.notify_activity("logout", "GST Session Concluded", f"Archived: {ident}")
            return flushed

        # Returning to GST login screen from an active session terminates prior session
        if ("login" in url.lower() or matched_crosshair.id == "gst_login") and prior_crosshair != "gst_login":
            if self.assembler.gstin or self.assembler.client_pan or self.assembler.captures or self.last_logged_pan:
                ident = self.assembler.client_name or self.assembler.gstin or self.assembler.client_pan or self.last_logged_name or self.last_logged_pan or "Client"
                flushed = self.assembler.seal_and_flush()
                if getattr(self.assembler, "_session_started", False):
                    self.assembler.logger.end_session(reason="GST Logout (Redirected to Login)", summary_items=list(self.assembler.captures.values()))
                    self.assembler._session_started = False
                self.assembler.reset()
                self.last_logged_name = None
                self.last_logged_pan = None
                self.last_screen_hash = None
                self.route_poll_count = 0
                self.notify_activity("logout", "GST Session Concluded", f"Archived: {ident}")
                return flushed

        # Clear active workflow selection when returning to welcome/dashboard
        if matched_crosshair.id in ("gst_welcome_calendar", "gst_returns_dashboard"):
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
            if not is_new_url and self.last_screen_hash == curr_hash:
                if self.route_captured or self.has_flushed_current_route:
                    return None
                # If page was loading or route data not yet captured, allow continued polling
                if not self.was_page_loading and self.route_poll_count > 6:
                    return None
            if self.last_screen_hash is not None and self.last_screen_hash != curr_hash:
                # Viewport scrolled or content changed — reset poll count to evaluate new view
                self.route_poll_count = 0
            self.last_screen_hash = curr_hash
        except Exception:
            pass

        target_crop = matched_crosshair.target_crop or "welcome_dashboard"
        ocr_res = self.ocr.scan_image(img, region_type=target_crop)
        full_text = ocr_res.get("text", "")
        lines = ocr_res.get("lines", [])

        # Loading State Transition: If the page was previously in an async loading state
        # and has now completed loading, re-trigger a micro-burst so rendered data is captured instantly!
        if self.was_page_loading and not is_page_loading(full_text):
            self.was_page_loading = False
            self.burst_ticks_remaining = 8
            self.route_poll_count = 0
            print(f"[VSDC Router] GST Page finished loading -> triggered burst capture!")

        # Check if page is currently in an asynchronous loading state.
        # Bypass for gst_form_details if statutory metadata table is already rendered behind loading overlay!
        if is_page_loading(full_text):
            self.was_page_loading = True
            if matched_crosshair.id == "gst_form_details":
                quick_meta = extract_gst_form_table(full_text, lines=lines, url=url)
                if not (quick_meta.get("gstin") or quick_meta.get("status") or quick_meta.get("tax_period") or quick_meta.get("fy") or self.assembler.gstin):
                    return None
                # Statutory table already rendered behind loading overlay! Proceed with immediate capture!
            else:
                return None

        # -------------------------------------------------------------
        # CROSSHAIR CALIBRATION: gst_welcome_calendar (fowelcome)
        # Captures 3 core items: Taxpayer Name, GSTIN (and derived PAN),
        # and Return Filing Preference (Quarterly / Monthly).
        # Immune to modal popups (e.g. Aadhaar/E-KYC reminders).
        # -------------------------------------------------------------
        if matched_crosshair.id == "gst_welcome_calendar":
            # Primary pass on the main crop
            client_name = extract_gst_welcome_name(full_text)
            gstin = extract_gstin(full_text)
            
            # If identity is incomplete, forcefully scan ONLY the top right profile pill
            if not client_name or not gstin:
                tr_res = self.ocr.scan_image(img, region_type="top_right_profile")
                tr_text = tr_res.get("text", "")
                tr_lines = tr_res.get("lines", [])
                
                if not client_name:
                    client_name = extract_gst_welcome_name(tr_text) or extract_name_from_ocr_lines(tr_lines)
                if not gstin:
                    gstin = extract_gstin(tr_text)

            pan = None
            if gstin and len(gstin) >= 12:
                candidate_pan = gstin[2:12]
                if extract_pan(candidate_pan):
                    pan = candidate_pan
            if not pan:
                pan = extract_pan(full_text)

            pref = extract_gst_filing_preference(full_text)

            flushed_prior = self.assembler.update_identity(
                name=client_name,
                gstin=gstin,
                pan=pan,
                portal="GST Portal",
                filing_preference=pref,
            )

            authoritative_name = self.assembler.client_name or client_name
            authoritative_gstin = self.assembler.gstin or gstin
            authoritative_pref = self.assembler.filing_preference or pref

            # Live HUD Toast feedback
            if authoritative_name and authoritative_name != self.last_logged_name:
                self.last_logged_name = authoritative_name
                pref_suffix = f" • {authoritative_pref}" if authoritative_pref else ""
                sub = f"GSTIN: {authoritative_gstin}{pref_suffix}" if authoritative_gstin else f"Portal: GST Portal{pref_suffix}"
                print(f"[VSDC Router] GST Assessee identified: {authoritative_name} (GSTIN: {authoritative_gstin}, Pref: {authoritative_pref})")
                self.notify_activity(
                    "start",
                    f"Client: {authoritative_name}",
                    sub,
                )
            elif authoritative_gstin and authoritative_gstin != self.last_logged_pan:
                self.last_logged_pan = authoritative_gstin
                pref_suffix = f" • {authoritative_pref}" if authoritative_pref else ""
                name_part = f"{authoritative_name} • " if authoritative_name else ""
                print(f"[VSDC Router] GSTIN identified: {authoritative_gstin}")
                self.notify_activity(
                    "start",
                    f"GSTIN: {authoritative_gstin}",
                    f"{name_part}Portal: GST Portal{pref_suffix}",
                )

            # Record step in assembler journey
            self.assembler.record_step(
                url,
                matched_crosshair.id,
                details={
                    "client_name": authoritative_name,
                    "gstin": authoritative_gstin,
                    "filing_preference": authoritative_pref,
                },
            )

            if flushed_prior:
                print(f"[VSDC Router] Flushed prior client session due to GSTIN/PAN switch!")
                return flushed_prior

            if authoritative_name or authoritative_gstin:
                self.route_captured = True

            return None

        # -------------------------------------------------------------
        # CROSSHAIR CALIBRATION: gst_form_details (/returns/auth/gstr1, gstr3b, cmp08, iff)
        # Captures complete 4-column metadata table:
        # GSTIN, PAN, Legal Name, Trade Name, Form Type, FY, Tax Period, Status, Due Date.
        # Assembles and shoots the complete dataset to the app!
        # -------------------------------------------------------------
        if matched_crosshair.id == "gst_form_details":
            meta = extract_gst_form_table(full_text, lines=lines, url=url)

            # Fallback to full window scan if GSTIN, tax_period, or Status was missed in cropped area
            if (not meta.get("gstin") or not meta.get("tax_period") or not meta.get("status")) and target_crop != "full":
                full_res = self.ocr.scan_image(img, region_type="full")
                f_text = full_res.get("text", "")
                f_lines = full_res.get("lines", [])
                full_meta = extract_gst_form_table(f_text, lines=f_lines, url=url)
                for k, v in full_meta.items():
                    if v and not meta.get(k):
                        meta[k] = v
                full_text = full_text + "\n" + f_text

            gstin = meta.get("gstin") or extract_gstin(full_text)
            pan = meta.get("pan")
            if not pan and gstin and len(gstin) >= 12:
                pan = gstin[2:12]

            # Invalidate junk values from local regex
            if not is_valid_gst_tax_period(meta.get("tax_period")):
                meta["tax_period"] = None
                meta["period_label"] = None
            if not is_valid_gst_status(meta.get("status")):
                meta["status"] = None

            # Gemini API call removed to prevent synchronous thread blocking.

            legal_name = meta.get("legal_name")
            if not legal_name:
                # Force precise crop for name instead of full-screen line scanning
                tr_res = self.ocr.scan_image(img, region_type="top_right_profile")
                legal_name = extract_gst_welcome_name(tr_res.get("text", "")) or extract_name_from_ocr_lines(tr_res.get("lines", []))

            trade_name = meta.get("trade_name")
            form_type = resolve_gst_form_type_from_url(url, full_text) or meta.get("form_type") or extract_filing_type(full_text) or "GSTR-1"
            
            # Smart period & FY extraction without ITR Assessment Year leaks
            raw_period = meta.get("tax_period") or extract_gst_tax_period(full_text) or ""
            tax_period = raw_period if is_valid_gst_tax_period(raw_period) else ""
            fy = meta.get("fy") or extract_gst_fy(full_text) or ""
            period_label = meta.get("period_label") or format_gst_period_label(tax_period, fy)
            due_date = meta.get("due_date")
            raw_status = meta.get("status") or extract_gst_status(full_text)
            status = raw_status if is_valid_gst_status(raw_status) else None

            pref = extract_gst_filing_preference(full_text)
            if legal_name or trade_name or gstin or pan:
                flushed_prior = self.assembler.update_identity(
                    name=legal_name,
                    trade_name=trade_name,
                    gstin=gstin,
                    pan=pan,
                    portal="GST Portal",
                    filing_preference=pref,
                    is_authoritative=bool(meta.get("legal_name")),
                )
                if flushed_prior:
                    print(f"[VSDC Router] Flushed prior client session due to GSTIN/PAN switch!")
                    return flushed_prior

            # Premature lock protection: Ensure tax_period and status have rendered before locking
            has_period = bool(tax_period) and is_valid_gst_tax_period(tax_period)
            has_status = bool(status) and is_valid_gst_status(status)

            if not has_period:
                # Modal dialog is open or table is still loading
                self.was_page_loading = True
                return None

            effective_status = status or ("Initiated" if self.route_poll_count >= 5 else None)
            if not effective_status:
                return None

            # Record full form details dataset in assembler
            self.assembler.record_gst_form_details(
                form_type=form_type,
                tax_period=tax_period,
                status=effective_status,
                due_date=due_date,
                fy=fy,
                trade_name=trade_name,
                period_label=period_label,
                raw_text=full_text[:2000],
                crosshair_id=matched_crosshair.id,
                legal_name=legal_name,
            )

            # Record step in timeline
            self.assembler.record_step(
                url,
                matched_crosshair.id,
                details={
                    "client_name": legal_name,
                    "trade_name": trade_name,
                    "gstin": gstin,
                    "form_type": form_type,
                    "tax_period": tax_period,
                    "period_label": period_label,
                    "status": effective_status,
                    "due_date": due_date,
                    "fy": fy,
                },
            )

            # Live HUD Toast Feedback
            authoritative_name = legal_name or self.assembler.client_name or ""
            authoritative_trade = trade_name or self.assembler.trade_name or ""
            name_label = f"{authoritative_name}" + (f" ({authoritative_trade})" if authoritative_trade else "")
            display_period = period_label or tax_period
            period_str = f" • {display_period}" if display_period else ""
            status_str = f" • Status: {effective_status}" if effective_status else ""

            self.notify_activity(
                "capture",
                f"Captured {form_type}{period_str}",
                f"{name_label}{status_str}".strip(),
            )
            print(f"[VSDC Router] Captured GST Form Dataset: {form_type} {display_period} | {name_label} | Status={effective_status}")

            # Shoot assembled dataset directly to desktop application!
            dataset_payload = self.assembler.get_gst_form_dataset_payload(crosshair_id=matched_crosshair.id)
            if dataset_payload:
                self.route_captured = True
                self.has_flushed_current_route = True
                self.burst_ticks_remaining = 0
                return dataset_payload

            return None

        # -------------------------------------------------------------
        # OTHER GST CROSSHAIRS (Returns Dashboard, Submission)
        # -------------------------------------------------------------
        # Extract GSTIN and Taxpayer Name if not yet identified
        gstin = extract_gstin(full_text)
        client_name = extract_gst_welcome_name(full_text) or extract_name_from_ocr_lines(lines)
        pref = extract_gst_filing_preference(full_text)

        pan = None
        if gstin and len(gstin) >= 12:
            candidate_pan = gstin[2:12]
            if extract_pan(candidate_pan):
                pan = candidate_pan

        if client_name or gstin or pan or pref:
            flushed_prior = self.assembler.update_identity(
                name=client_name,
                gstin=gstin,
                pan=pan,
                portal="GST Portal",
                filing_preference=pref,
            )
            if flushed_prior:
                print(f"[VSDC Router] Flushed prior client session due to GSTIN/PAN switch!")
                return flushed_prior

        # Resolve filing_type accurately: prioritize URL for GST
        url_form = resolve_gst_form_type_from_url(url, full_text) if self.active_portal == "GST Portal" else None
        filing_type = url_form or extract_filing_type(full_text)
        gst_tp = extract_gst_tax_period(full_text)
        gst_fy = extract_gst_fy(full_text)
        period = format_gst_period_label(gst_tp, gst_fy) if (gst_tp or gst_fy) else None

        # On GST filing routes (/file or /filing) or success screens, extract full assessee identity if present
        if matched_crosshair.id in ("gst_filing_file_success", "gst_filing_success"):
            meta = extract_gst_form_table(full_text, lines=lines, url=url)
            m_gstin = meta.get("gstin")
            m_pan = meta.get("pan")
            m_legal = meta.get("legal_name")
            m_trade = meta.get("trade_name")
            if m_legal or m_trade or m_gstin or m_pan:
                self.assembler.update_identity(
                    name=m_legal,
                    trade_name=m_trade,
                    gstin=m_gstin,
                    pan=m_pan,
                    portal="GST Portal",
                    is_authoritative=bool(m_legal),
                )
            if not period and meta.get("period_label"):
                period = meta["period_label"]
            if not filing_type and meta.get("form_type"):
                filing_type = meta["form_type"]

        if filing_type or period:
            self.assembler.update_selection(filing_type=filing_type, period_label=period)
            if matched_crosshair.id == "gst_returns_dashboard":
                self.route_captured = True

        # Check for terminal filing confirmation (ARN)
        arn = repair_gst_arn(full_text) or repair_numeric_ack(full_text)

        # Fallback to full image scan if ARN was not found in cropped card on a return/submission card!
        # Even if the route wasn't technically a "submission" crosshair, if it's GST and they just hit submit
        # in-place (modal), we want to fallback to full screen just in case.
        is_sub_crosshair = matched_crosshair.is_terminal_submission or matched_crosshair.id in (
            "gst_filing_success",
            "gst_filing_file_success",
        ) or ("success" in url.lower() or "/file" in url.lower() or "/filing" in url.lower())

        if not arn and (is_sub_crosshair or matched_crosshair.id == "gst_form_details"):
            if target_crop != "full":
                full_res = self.ocr.scan_image(img, region_type="full")
                f_text = full_res.get("text", "")
                f_arn = repair_gst_arn(f_text) or repair_numeric_ack(f_text)
                if f_arn:
                    arn = f_arn
                    full_text = full_text + "\n" + f_text
                    if not filing_type:
                        filing_type = resolve_gst_form_type_from_url(url, f_text) or extract_filing_type(f_text)
                    if not period:
                        f_gst_tp = extract_gst_tax_period(f_text)
                        f_gst_fy = extract_gst_fy(f_text)
                        if f_gst_tp or f_gst_fy:
                            period = format_gst_period_label(f_gst_tp, f_gst_fy)

        # STRICT MANDATE: An optical GST submission MUST have a valid ARN
        if arn and arn != "N/A":
            self.route_captured = True
            status = classify_verification_status(full_text)
            if get_status_rank(status) < 2:
                status = "Filed"

            if not filing_type:
                filing_type = resolve_gst_form_type_from_url(url, full_text) or self.assembler.current_filing_type or "GSTR-1"

            if not period:
                period = self.assembler.current_period_label

            self.assembler.record_submission(
                ack_number=arn,
                status=status,
                filing_type=filing_type,
                period_label=period,
                raw_text=full_text[:2000],
                crosshair_id=matched_crosshair.id,
            )
            form_lbl = filing_type or self.assembler.current_filing_type or "GST Return"
            period_lbl = f" • {period or self.assembler.current_period_label}" if (period or self.assembler.current_period_label) else ""
            assessee = self.assembler.client_name or self.assembler.gstin or ""
            trade = f" ({self.assembler.trade_name})" if self.assembler.trade_name else ""
            name_part = f"{assessee}{trade} • " if assessee else ""
            self.notify_activity("submit", f"{form_lbl} Filed Successfully", f"{name_part}ARN: {arn}")

            # Seal and flush filing payload! If we got an ARN, the submission is confirmed.
            master_payload = self.assembler.seal_and_flush()
            if master_payload:
                self.has_flushed_current_route = True
                self.assembler.clear_workflow_selection()
                flush_name = master_payload.get("client_name") or master_payload.get("gstin") or master_payload.get("pan", "")
                print(f"[VSDC Router] Flushed GST filing payload: ARN={arn} Form={form_lbl} Status={status}")
            return master_payload

        # Dataset Completion Principle:
        completed_payload = self.assembler.get_completed_dataset_payload(crosshair_id=matched_crosshair.id)
        if completed_payload:
            self.route_captured = True
            c_form = completed_payload.get("filing_type", "GST Return")
            c_period = f" • {completed_payload.get('period_label')}" if completed_payload.get("period_label") else ""
            c_status = completed_payload.get("status", "Submitted")
            c_ident = completed_payload.get("gstin") or completed_payload.get("pan", "")
            c_name = completed_payload.get("client_name") or c_ident
            print(f"[VSDC Router] GST Dataset completed & shot to app: {c_ident} Form={c_form}{c_period} Status={c_status}")
            self.notify_activity("capture", f"Dataset: {c_form}{c_period}", f"{c_name} • {c_status}")
            return completed_payload

        return None
