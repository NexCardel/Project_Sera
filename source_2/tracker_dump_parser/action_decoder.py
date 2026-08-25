"""
Stage E: Action Decoder & Outcome Inference Engine
Maps URL patterns and payload shapes to semantic portal actions and execution outcomes.
"""

import re
from typing import Dict, Any, Tuple, Optional, List

# Outcome Strategies
def outcome_success_flag(payload: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Evaluates outcomes using successFlag, httpStatus, and errors list."""
    inner = payload.get("raw_payload") or payload
    if not isinstance(inner, dict):
        return "success", None

    errors = inner.get("errors") or []
    if errors:
        err_msg = str(errors[0].get("message") if isinstance(errors[0], dict) else errors[0])
        return "failure", err_msg

    if inner.get("successFlag") is False:
        return "failure", "Portal validation reported successFlag = false"
    elif inner.get("successFlag") is True:
        return "success", None

    http_status = inner.get("httpStatus") or inner.get("httpStatusCode")
    if http_status and str(http_status) not in ("200", "201"):
        return "failure", f"HTTP Status {http_status}"

    return "success", None


def outcome_code_desc(payload: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Evaluates outcomes using code and desc fields (e.g. DATA_INSERTION_SUCCESS_FLAG)."""
    inner = payload.get("raw_payload") or payload
    if not isinstance(inner, dict):
        return "success", None

    code = str(inner.get("code") or "").upper()
    desc = str(inner.get("desc") or "")

    if "SUCCESS" in code or "SUCCESS" in desc.upper():
        return "success", None
    elif "FAIL" in code or "ERROR" in code or "FAIL" in desc.upper():
        return "failure", desc or code or "Operation failed"

    errors = inner.get("errors") or []
    if errors:
        return "failure", str(errors[0])

    return "success", None


def outcome_bank_validation(payload: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Evaluates bank account validation status (status 'A', 'V', errorCd)."""
    inner = payload.get("raw_payload") or payload
    if not isinstance(inner, dict):
        return "success", None

    status = str(inner.get("status") or "").upper()
    acc_val = str(inner.get("accValidity") or "").upper()
    error_cd = inner.get("errorCd")

    # In ITD, status 'A' + accValidity 'V' indicates validated active account
    if status == "A" or acc_val == "V":
        return "success", None
    elif status == "E" or acc_val == "I":
        reason = error_cd or inner.get("userAction") or "Bank account validation rejected/invalid"
        return "failure", str(reason)

    return "success", None


def outcome_generic(payload: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Generic fallback outcome evaluator checking errors and messages."""
    inner = payload.get("raw_payload") or payload
    if not isinstance(inner, dict):
        return "success", None

    status_str = str(inner.get("status") or inner.get("status_cd") or "").upper()
    if status_str in ("FAIL", "FAILED", "ERROR", "0"):
        err = inner.get("error") or inner.get("errors") or inner.get("error_msg")
        return "failure", str(err) if err else "Portal returned error status"

    errors = inner.get("errors") or []
    if errors:
        msg = str(errors[0].get("message") if isinstance(errors[0], dict) else errors[0])
        return "failure", msg

    messages = inner.get("messages") or []
    for m in messages:
        if isinstance(m, dict) and m.get("type", "").upper() in ("ERROR", "FATAL"):
            return "failure", m.get("desc") or m.get("message")

    return "success", None


OUTCOME_HANDLERS = {
    "outcome_success_flag": outcome_success_flag,
    "outcome_code_desc": outcome_code_desc,
    "outcome_bank_validation": outcome_bank_validation,
    "outcome_generic": outcome_generic
}

# Declarative Action Rules
ACTION_RULES = [
    # (regex_pattern, portal_code, category, action_title, outcome_handler_key)
    (r"/loginapi/login$", "IT", "Authentication", "User Logged In to Portal", "outcome_generic"),
    (r"/verificationservices/auth/validateOTP$", "IT", "E-Verification", "Completed Return E-Verification", "outcome_generic"),
    (r"/servicesapi/auth/getEntity$", "IT", "Bank / Profile Lookup", "Bank Account / Entity Fetch", "outcome_bank_validation"),
    (r"/verificationservices/auth/getEntity$", "IT", "Profile", "Viewed Taxpayer Profile & Contact Info", "outcome_generic"),
    (r"/verificationservices/auth/saveEntity$", "IT", "Profile Save", "Saved Taxpayer Profile & Contact Info", "outcome_code_desc"),
    (r"/returns/view/wzrd$", "IT", "e-File Wizard", "Checked e-File Wizard Schedules", "outcome_generic"),
    (r"/returns/save/wzrd$", "IT", "e-File Wizard", "Saved In-Progress Return Draft", "outcome_generic"),
    (r"/returns/insertSla/wzrd$", "IT", "e-File Wizard", "Saved In-Progress Return Draft (SLA)", "outcome_generic"),
    (r"/returns/validate/wzrd$", "IT", "e-File Wizard", "Ran Return Pre-Filing Validation", "outcome_success_flag"),
    (r"/returns/submit/wzrd$", "IT", "Filing Submission", "Submitted Final Income Tax Return", "outcome_success_flag"),
    (r"/returns/downloadfile$", "IT", "Download", "Downloaded Filed Return Form", "outcome_generic"),
    (r"/return/details$", "IT", "e-File Wizard", "Checked e-File Wizard (Draft Status)", "outcome_generic"),
    (r"/masterservicesapi/auth/getEntity$", "IT", "Master Entity", "Master Entity Profile Lookup", "outcome_generic"),
    (r"/dashboard/fileIncomeTaxReturn$", "IT", "Filing History", "Viewed Filed Returns Dashboard", "outcome_generic"),
    
    # GST Portal Endpoints
    (r"/gstr1/summary", "GST", "GST Return", "Viewed GSTR-1 Section Summary", "outcome_generic"),
    (r"/gstr1/totalsummarycount", "GST", "GST Return", "Viewed GSTR-1 Summary Counts", "outcome_generic"),
    (r"/formdetails", "GST", "Filing Submission", "Submitted GST Return (ARN Issued)", "outcome_generic"),
    (r"/signatory$", "GST", "GST Profile", "Viewed GST Authorized Signatory", "outcome_generic"),
    (r"/filingsnapshot$", "GST", "GST Compliance", "Checked GST Filing Due Dates", "outcome_generic"),
    (r"/getRcmAvl", "GST", "GST Ledger", "Checked GST RCM & ITC Ledger Balances", "outcome_generic"),
]


def decode_action(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decodes an API endpoint and payload into a structured action event with outcome assessment.
    """
    url_clean = str(url or "").strip()
    matched_rule = None

    for pattern, portal_cd, cat, title, handler_name in ACTION_RULES:
        if re.search(pattern, url_clean, re.IGNORECASE):
            matched_rule = (portal_cd, cat, title, handler_name)
            break

    # Dynamic refinements based on payload shape
    inner = payload.get("raw_payload") or payload
    if not isinstance(inner, dict):
        inner = {}

    if not matched_rule:
        # Heuristic fallbacks
        if "accValidity" in inner or "bankName" in inner:
            matched_rule = ("IT", "Bank Validation", f"Inspected Bank Account: {inner.get('bankName', 'Bank')}", "outcome_bank_validation")
        elif "formTypeCd" in inner or "assmentYear" in inner or "ackNum" in inner:
            matched_rule = ("IT", "Filing History", "Viewed Filed Return Record", "outcome_generic")
        elif "sec_nm" in inner or "cur_gt" in inner:
            matched_rule = ("GST", "GST Return", f"Viewed GSTR-1 Section ({inner.get('sec_nm', 'Summary')})", "outcome_generic")
        elif "isDraftPresent" in inner or "isReturnFiled" in inner:
            matched_rule = ("IT", "e-File Wizard", "Checked e-File Wizard Draft Status", "outcome_generic")
        else:
            matched_rule = ("UNKNOWN", "General", f"Portal Activity: {url_clean or 'Event'}", "outcome_generic")

    portal_cd, cat, title, handler_key = matched_rule
    handler_fn = OUTCOME_HANDLERS.get(handler_key, outcome_generic)
    outcome, reason = handler_fn(payload)

    # Contextual enhancements
    if "bankName" in inner and "Bank Account" in title:
        title = f"Inspected Bank Account: {inner.get('bankName')}"
    elif "sec_nm" in inner:
        title = f"Viewed GSTR-1 Section Summary ({inner.get('sec_nm')})"

    return {
        "portal_code": portal_cd,
        "category": cat,
        "action": title,
        "outcome": outcome,
        "reason": reason,
        "handler_used": handler_key,
        "is_unknown_endpoint": (portal_cd == "UNKNOWN")
    }
