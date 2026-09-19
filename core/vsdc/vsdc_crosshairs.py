"""
core/vsdc/vsdc_crosshairs.py — Registered Crosshairs for Visual SDC (VSDC)
========================================================================
1-to-1 parity with Sera SDC (Sera DOM Crosshair) protocols:
- Income Tax Return (ITR) Protocol (12 crosshairs)
- GST Portal Protocol (8 crosshairs)
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
        id="itr_everify_success",
        protocol="Income Tax",
        # "Return e-Verified Successfully" - the last step of the e-Verify wizard, where a
        # return filed under "Verify Later" finally becomes e-Verified.
        #
        # The wizard is an Angular SPA: all three of its steps (pick the return, choose
        # the method / enter the OTP, and this confirmation) are served at the SAME route,
        # .../eVerifyReturn/eVerifyReturn-al, so the URL alone CANNOT tell them apart.
        # This pattern therefore only covers a distinctly-named success route, should the
        # portal ever add one; the confirmation as it ships today is reached by content
        # promotion in the router - itr_everify_return is promoted to this crosshair the
        # moment the page itself says the return was verified
        # (see _route_itr_crosshair / has_everify_success_evidence).
        pattern=re.compile(r"(?:e-?verify(?:return)?|everify)[-_/]?(?:success|confirmation|verified|complete[d]?)", re.IGNORECASE),
        target_crop="receipt_card",
        description="Return e-Verified successfully (Verify Later completion)",
        host_pattern=ITR_HOST_PATTERN,
        is_terminal_submission=True,
    ),
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
        description="e-Verify Return wizard (return picker, method/OTP, confirmation)",
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
        pattern=re.compile(r"(?:itr.?status|view.?filed.?returns|fo-view-filed-returns|viewreturns|view-returns|filed-returns|filedreturns|dashboard/returns?|return.?status)", re.IGNORECASE),
        target_crop="center_card",
        description="Historical filed returns view (15-digit Ack recovery)",
        host_pattern=ITR_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="itr_offline_json_upload",
        protocol="Income Tax",
        # Anchored to the end of the path on purpose: a deeper route beneath it
        # (.../offlineJsonSubmission/<verification step>) is the SUBMISSION page and
        # must fall through to itr_offline_json_submit below, not stop here.
        pattern=re.compile(r"offline[-_]?json[-_]?submission(?:[?#]|$)", re.IGNORECASE),
        target_crop="center_card",
        description="Updated/Revised return JSON upload (u/s 139(8A) offline utility)",
        host_pattern=ITR_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="itr_offline_json_submit",
        protocol="Income Tax",
        # The submit + mandatory e-verification step that follows the JSON upload. Its
        # exact route was not available when this was written, so the pattern covers
        # the verification/submission steps beneath the two paths this flow is known to
        # live under. Matching alone captures nothing: recording still requires a real
        # acknowledgement number and a submitted-or-better status, so a wrong match
        # here stays inert rather than inventing a filing.
        pattern=re.compile(
            r"(?:fileincometaxreturn|offline[-_]?json[-_]?submission)/"
            r"(?:[A-Za-z0-9_-]+/)*"
            r"(?:verif\w*|preview\w*|submit\w*|success|confirmation|acknowledg\w*)",
            re.IGNORECASE,
        ),
        target_crop="receipt_card",
        description="Updated/Revised return submission & mandatory e-verification",
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
        pattern=re.compile(r"(?:/#)?/?(?:returns|services|payment)/auth/(?:[a-zA-Z0-9_-]+/)?(?:file|filing|success)(?:[?#/]|$)", re.IGNORECASE),
        target_crop="receipt_card",
        description="GST return filing file route (IFF, GSTR-1, GSTR-3B, CMP-08 submission)",
        host_pattern=GST_HOST_PATTERN,
        is_terminal_submission=True,
    ),
    CrosshairDefinition(
        id="gst_audit_history",
        protocol="GST Portal",
        pattern=re.compile(r"(?:/#)?/?(?:returns|services)/auth/(?:efiledReturns|trackreturnstatus)(?:[?#/]|$)", re.IGNORECASE),
        target_crop="full",
        description="GST Return Filing History and Tracking Status",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_form_details",
        protocol="GST Portal",
        pattern=re.compile(r"(?:/#)?/?(?:returns|services|payment)/(?:auth/)?(?:gstr[-_ ]*[1-9A-Z]+|cmp[-_ ]*08|iff|itc[-_ ]*04|drc[-_ ]*03)(?!/(?:file|filing|success))", re.IGNORECASE),
        target_crop="full",
        description="GST return form table & period details (All Forms)",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_returns_dashboard",
        protocol="GST Portal",
        pattern=re.compile(r"(?:/#)?/?(?:services/auth/returns|services/quicklinks/returns|returns/(?:auth/)?dashboard)", re.IGNORECASE),
        target_crop="full",
        description="GST Returns Dashboard (Period & Financial Year selection)",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_welcome_calendar",
        protocol="GST Portal",
        pattern=re.compile(r"(?:/#)?/?(?:services/auth/fowelcome|services/auth/dashboard|fowelcome|services/dashboard$)", re.IGNORECASE),
        target_crop="full",
        description="GST Portal welcome page, returns calendar, and GSTIN badge",
        host_pattern=GST_HOST_PATTERN,
    ),
    CrosshairDefinition(
        id="gst_logout",
        protocol="GST Portal",
        pattern=re.compile(r"[/#](?:logout|sign-?out|session.?expire|session-?timeout|sessionexpired)(?:[?/#]|$)", re.IGNORECASE),
        target_crop="header",
        description="GST session termination boundary",
        host_pattern=GST_HOST_PATTERN,
        is_session_boundary=True,
    ),
    CrosshairDefinition(
        id="gst_login",
        protocol="GST Portal",
        pattern=re.compile(r"(?:/#)?/?services/(?:auth/)?login(?:[?#/]|$)", re.IGNORECASE),
        target_crop="header",
        description="GST Portal login page (pre-auth or post-logout boundary)",
        host_pattern=GST_HOST_PATTERN,
        is_session_boundary=False,
    ),
]

# Unified crosshair catalog
ALL_CROSSHAIRS: List[CrosshairDefinition] = ITR_CROSSHAIRS + GST_CROSSHAIRS

CROSSHAIRS_BY_ID: Dict[str, CrosshairDefinition] = {c.id: c for c in ALL_CROSSHAIRS}


def get_crosshair(crosshair_id: str) -> Optional[CrosshairDefinition]:
    """
    Looks a crosshair up by id, for the routes a URL alone cannot identify. The e-Verify
    wizard is the case this exists for: its three steps share one Angular route, so the
    confirmation step is resolved from the page content, not the address bar.
    """
    return CROSSHAIRS_BY_ID.get(crosshair_id)


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

    # 2. Fallback: match without host constraint (strictly respecting portal_hint if present)
    for crosshair in ALL_CROSSHAIRS:
        if portal_hint and crosshair.protocol != portal_hint:
            continue
        if crosshair.pattern.search(url):
            return crosshair

    return None
