"""
core/vsdc/vsdc_crosshairs.py — Registered Crosshairs for Visual SDC (VSDC)
========================================================================
1-to-1 parity with Sera SDC (Sera DOM Crosshair) protocols:
- Income Tax Return (ITR) Protocol (8 crosshairs)
- GST Portal Protocol (6 crosshairs)
"""

import re
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class CrosshairDefinition:
    id: str
    protocol: str
    pattern: re.Pattern
    target_crop: str  # 'header', 'center_card', 'receipt_card', 'full'
    description: str
    host_pattern: Optional[re.Pattern] = None
    is_terminal_submission: bool = False
    is_session_boundary: bool = False

    @property
    def label(self) -> str:
        return self.description


# Host patterns matching SDC (includes file: and local drive paths for local simulations)
ITR_HOST_PATTERN = re.compile(r"(?:incometax|eportal\.incometax\.gov\.in|localhost|127\.0\.0\.1|file:|(?<![a-zA-Z0-9])[a-zA-Z]:[/\\])", re.IGNORECASE)
GST_HOST_PATTERN = re.compile(r"(?:gst\.gov\.in|services\.gst\.gov\.in|return\.gst\.gov\.in|localhost|127\.0\.0\.1|file:|(?<![a-zA-Z0-9])[a-zA-Z]:[/\\])", re.IGNORECASE)

# ─── 1. Income Tax (ITR) Protocol Crosshairs ──────────────────────────────────
ITR_CROSSHAIRS: List[CrosshairDefinition] = [
    CrosshairDefinition(
        id="itr_filed_verified",
        protocol="Income Tax",
        pattern=re.compile(r"(?:fo-e-verify-now-success|fo-return-success|e-verify.*success|filing-success|filing.*confirmation.*verified|return.?success)", re.IGNORECASE),
        target_crop="receipt_card",
        description="Filing confirmed and e-verified successfully",
        host_pattern=ITR_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="itr_everify_return",
        protocol="Income Tax",
        pattern=re.compile(r"(?:eVerifyReturn|e-verify-return|everifyreturn)", re.IGNORECASE),
        target_crop="receipt_card",
        description="Step 3 Return e-verification completion page",
        host_pattern=ITR_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="itr_submitted_pending",
        protocol="Income Tax",
        pattern=re.compile(r"(?:fo-e-verify-later|complete-verification|fo-verify-later|fo-return-submitted|submit.?success|submit_success|filing.?confirmation)", re.IGNORECASE),
        target_crop="receipt_card",
        description="Filing submitted, pending e-verification (Verify Later)",
        host_pattern=ITR_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="itr_view_filed_returns",
        protocol="Income Tax",
        pattern=re.compile(r"(?:itr.?status|view.?filed.?returns|fo-view-filed-returns|viewreturns|view-returns|filed-returns|filedreturns)", re.IGNORECASE),
        target_crop="center_card",
        description="Historical filed returns view (15-digit Ack recovery)",
        host_pattern=ITR_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="itr_personal_info",
        protocol="Income Tax",
        pattern=re.compile(r"(?:personal.?information|personal.?info|test_page_personal_info|myProfile|profileDetail|profile-detail|my-profile|profile|partA|part-a|foreturns-ay\d+/(?:fo-itr\d+|fo-schedule|fo-return|parta)|return-summary|user-profile|view-profile)", re.IGNORECASE),
        target_crop="center_card",
        description="Taxpayer personal information and profile detail",
        host_pattern=ITR_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="itr_form_select",
        protocol="Income Tax",
        pattern=re.compile(r"(?:fo-select-itr-form|select.?itr.?form|fo-lets-get-started)", re.IGNORECASE),
        target_crop="center_card",
        description="ITR Form Type & Assessment Year selection",
        host_pattern=ITR_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="itr_landing",
        protocol="Income Tax",
        pattern=re.compile(r"(?:fileincometaxreturn|file-income-tax-return|filereturn|landing|home|[/#]welcome\b|dashboard$|dashboard/file)", re.IGNORECASE),
        target_crop="center_card",
        description="Taxpayer landing dashboard & Assessment Year dropdown",
        host_pattern=ITR_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="itr_login_auth",
        protocol="Income Tax",
        pattern=re.compile(r"[/#]login(?:/password|/otp|/auth)?(?:[?/#]|$)", re.IGNORECASE),
        target_crop="center_card",
        description="Taxpayer login step 1 & 2 (PAN & Authentication identity seeding)",
        host_pattern=ITR_HOST_PATTERN,
        is_session_boundary=False,
    ),
    CrosshairDefinition(
        id="itr_logout",
        protocol="Income Tax",
        pattern=re.compile(r"[/#](?:logout|sign-?out|session.?expire|session-?expired|session-?timeout)(?:[?/#]|$)", re.IGNORECASE),
        target_crop="header",
        description="Login/logout session termination boundary",
        host_pattern=ITR_HOST_PATTERN,
        is_session_boundary=True,
    ),
]


# ─── 2. GST Portal Protocol Crosshairs ─────────────────────────────────────────
GST_CROSSHAIRS: List[CrosshairDefinition] = [
    CrosshairDefinition(
        id="gst_filing_success",
        protocol="GST Portal",
        pattern=re.compile(r"(?:services/auth/gstr.*success|gstr.*submit.*success|returns.*success|filing.*success|ack.*success)", re.IGNORECASE),
        target_crop="receipt_card",
        description="GST return filing success screen (ARN capture)",
        host_pattern=GST_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="gst_filing_file_success",
        protocol="GST Portal",
        pattern=re.compile(r"returns/auth/file(?:[?#]|$)", re.IGNORECASE),
        target_crop="receipt_card",
        description="GST return filing file route (IFF, GSTR-1, GSTR-3B submission)",
        host_pattern=GST_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="gst_form_details",
        protocol="GST Portal",
        pattern=re.compile(r"(?:returns|services)/(?:auth/)?(?:gstr[-_ ]*[1-9A-Z]+|cmp[-_ ]*08|iff)", re.IGNORECASE),
        target_crop="form_details",
        description="GST return form table & period details (GSTR-1, 3B, CMP-08)",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_returns_dashboard",
        protocol="GST Portal",
        pattern=re.compile(r"(?:services/auth/returns|services/quicklinks/returns|returns/dashboard)", re.IGNORECASE),
        target_crop="center_card",
        description="GST Returns Dashboard (Period & Financial Year selection)",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_welcome_calendar",
        protocol="GST Portal",
        pattern=re.compile(r"(?:services/auth/fowelcome|services/auth/dashboard|fowelcome|auth/dashboard$)", re.IGNORECASE),
        target_crop="welcome_dashboard",
        description="GST Portal welcome page, returns calendar, and GSTIN badge",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_logout",
        protocol="GST Portal",
        pattern=re.compile(r"[/#](?:logout|sign-?out|session.?expire|session-?timeout)(?:[?/#]|$)", re.IGNORECASE),
        target_crop="header",
        description="GST session termination boundary",
        host_pattern=GST_HOST_PATTERN,
        is_session_boundary=True,
    ),
]

# Unified crosshair catalog
ALL_CROSSHAIRS: List[CrosshairDefinition] = ITR_CROSSHAIRS + GST_CROSSHAIRS


def match_url_crosshair(url: str, portal_hint: Optional[str] = None) -> Optional[CrosshairDefinition]:
    """
    Evaluates a browser URL string against all registered crosshairs in priority order.
    Checks hostMatch pattern when available.
    """
    if not url:
        return None

    if not portal_hint:
        u_lower = url.lower()
        if "gst" in u_lower:
            portal_hint = "GST Portal"
        elif "incometax" in u_lower:
            portal_hint = "Income Tax"

    # 1. If URL has host or hint, prioritize matching protocol
    for crosshair in ALL_CROSSHAIRS:
        if portal_hint and crosshair.protocol != portal_hint:
            continue
        if crosshair.host_pattern and not crosshair.host_pattern.search(url):
            continue
        if crosshair.pattern.search(url):
            return crosshair

    # 2. Fallback: match without host constraint
    for crosshair in ALL_CROSSHAIRS:
        if crosshair.pattern.search(url):
            return crosshair

    return None
