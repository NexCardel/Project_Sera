"""
timeline_decoder.py — Human-Readable Session Timeline Decoder for Project Sera
-------------------------------------------------------------------------------
Converts technical API intercept logs into crystal-clear, plain-English narrative
stories of human and portal interactions during a tax session.
"""

import datetime
import json
import re
from typing import Any, Dict, List, Optional


def _parse_iso_datetime(ts_str: Optional[str]) -> Optional[datetime.datetime]:
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(clean)
    except Exception:
        return None


def _format_elapsed(seconds: float) -> str:
    if seconds < 0:
        return "T+00s"
    total_sec = int(seconds)
    if total_sec < 60:
        return f"T+{total_sec:02d}s"
    mins = total_sec // 60
    secs = total_sec % 60
    if mins < 60:
        return f"T+{mins}m {secs:02d}s"
    hrs = mins // 60
    rem_mins = mins % 60
    return f"T+{hrs}h {rem_mins:02d}m"


def _format_ay(val: Any) -> str:
    s = str(val or "").strip()
    if not s or s == "N/A":
        return "N/A"
    if s.startswith("AY"):
        return s
    m = re.match(r"^(\d{4})$", s)
    if m:
        yr = int(m.group(1))
        return f"AY {yr}-{str(yr + 1)[-2:]}"
    m_split = re.match(r"^(\d{4})[-/](\d{2,4})$", s)
    if m_split:
        return f"AY {s}"
    return f"AY {s}"


def _translate_form_code(code: Any) -> str:
    c = str(code or "").strip()
    mapping = {
        "1": "ITR-1 (Sahaj)",
        "2": "ITR-2",
        "3": "ITR-3",
        "4": "ITR-4 (Sugam)",
        "5": "ITR-5",
        "6": "ITR-6",
        "7": "ITR-7",
        "ITR1": "ITR-1 (Sahaj)",
        "ITR4": "ITR-4 (Sugam)",
        "ITR2": "ITR-2",
        "ITR3": "ITR-3",
    }
    return mapping.get(c, c if c else "ITR")


def _translate_section_code(code: Any) -> str:
    c = str(code or "").strip()
    mapping = {
        "11": "Section 139(1) - On or Before Due Date",
        "12": "Section 139(1) - On or Before Due Date",
        "13": "Section 139(4) - Belated Return",
        "14": "Section 139(5) - Revised Return",
        "17": "Section 119(2)(b) - Condonation of Delay",
        "O": "Original Return",
        "R": "Revised Return",
        "B": "Belated Return"
    }
    return mapping.get(c, f"Section {c}" if c else "")


def _translate_login_mode(mode: Any) -> str:
    m = str(mode or "").strip().upper()
    if m in ("N", "WEB", "PASS", "PASSWORD"):
        return "Password Login"
    if m in ("O", "OTP"):
        return "OTP Authentication (Aadhaar / Mobile)"
    if m in ("D", "DSC"):
        return "Digital Signature (DSC)"
    return m if m else "Standard Web Login"


def _translate_efile_status(code: Any, desc: Optional[str] = None) -> str:
    if desc and len(desc.strip()) > 3 and not desc.strip().isdigit():
        return desc.strip()
    c = str(code or "").strip()
    mapping = {
        "600": "Refund Reissued",
        "62": "Refund Determined & Sent to Refund Banker",
        "624": "Refund Failed at CPC (No Validated Bank Account)",
        "997": "Successfully E-Verified",
        "998": "Filed — Pending for E-Verification",
        "999": "Return Filed",
        "46": "Processed with Zero Demand / No Refund",
        "63": "Processed with Zero Demand / No Refund",
        "10": "Return Uploaded",
        "1": "Successfully Submitted"
    }
    return mapping.get(c, desc if desc else (f"Status Code {c}" if c else "Processed"))


def decode_single_capture(capture: Dict[str, Any], index: int = 1, elapsed_sec: float = 0.0) -> Dict[str, Any]:
    """
    Decodes a single raw capture dictionary into a clear, plain-English human step.
    """
    url = str(capture.get("url") or "")
    portal = str(capture.get("portal") or "Government Portal")
    arn = str(capture.get("arn_number") or capture.get("arn") or capture.get("latest_arn") or "N/A")
    raw_payload_json = capture.get("raw_payload_json") or capture.get("raw_payload") or {}
    session_id = capture.get("session_id") or ""
    timestamp = capture.get("created_at") or capture.get("timestamp") or ""

    if isinstance(raw_payload_json, str):
        try:
            payload_data = json.loads(raw_payload_json)
        except Exception:
            payload_data = {}
    elif isinstance(raw_payload_json, dict):
        payload_data = raw_payload_json
    else:
        payload_data = {}

    inner = payload_data.get("raw_payload", payload_data) if isinstance(payload_data, dict) else {}
    if not isinstance(inner, dict):
        inner = {}

    if not session_id and isinstance(payload_data, dict):
        session_id = payload_data.get("session_id") or ""
    if not url and isinstance(payload_data, dict):
        url = payload_data.get("url") or ""

    step_title = "Portal Activity Detected"
    category = "General"
    icon = "mdi.web"
    color = "#58A6FF"
    narrative = "Activity recorded on portal."
    chips: List[Dict[str, str]] = []

    # =========================================================================
    # 1. Income Tax Portal Endpoints
    # =========================================================================
    if "incometax" in url or "Income Tax" in portal or "iec" in url:
        
        # 1.1 Login & Authentication
        if "loginapi/login" in url or "loginapi/auth/saveEntity" in url or "ActivityLogDetlFlag" in inner or "LastLogin" in inner:
            mode_raw = inner.get("ModeOfLoginFlag") or inner.get("modeOfLogin") or "WEB"
            mode_clean = _translate_login_mode(mode_raw)
            user_id = inner.get("submitUserId") or inner.get("entity") or inner.get("userId") or capture.get("pan") or ""
            
            step_title = "User Logged In to Portal"
            category = "Authentication"
            icon = "mdi.login-variant"
            color = "#4CF9B7"
            narrative = f"Successfully authenticated into Income Tax Portal via {mode_clean}."
            if user_id:
                narrative += f" Logged in User ID: {user_id}."
                chips.append({"label": "User / PAN", "val": str(user_id)})
            chips.append({"label": "Auth Mode", "val": mode_clean})
            if inner.get("clientIp"):
                chips.append({"label": "IP Address", "val": str(inner.get("clientIp"))})

        # 1.2 Bank Account Validation Check
        elif ("servicesapi/auth/getEntity" in url or "Bank" in portal) and ("bankAcctNum" in inner or "bankName" in inner or "accValidity" in inner or "cpcAccValidity" in inner):
            bank_name = inner.get("bankName") or "Bank Account"
            ifsc = inner.get("ifscCd") or "N/A"
            is_valid = (inner.get("accValidity") == "V")
            status_desc = "Validated by Bank" if is_valid else (inner.get("accountStatus") or "Pending Validation")
            is_refund_nom = (inner.get("refundFlag") == "Y" or inner.get("nominateForRefund") == "Y")
            
            step_title = f"Inspected Bank Account: {bank_name}"
            category = "Bank Validation"
            icon = "mdi.bank-check" if is_valid else "mdi.bank"
            color = "#4CF9B7" if is_valid else "#D29922"
            
            narrative = f"Viewed linked bank account for {bank_name} (IFSC: {ifsc}). Status: {status_desc}."
            if is_refund_nom:
                narrative += " Account is active and nominated for direct tax refunds."

            chips.append({"label": "Bank", "val": str(bank_name)})
            chips.append({"label": "IFSC", "val": str(ifsc)})
            chips.append({"label": "Status", "val": status_desc})
            chips.append({"label": "Refund Eligible", "val": "Yes (Nominated)" if is_refund_nom else "No"})

        # 1.3 View Filed Returns & Multi-Year Assessment History
        elif "ackNum" in inner and ("assmentYear" in inner or "computedRefndAmt" in inner or "itrPanDetlList" in inner or "efileStatus" in inner):
            raw_ay = inner.get("assmentYear") or capture.get("period_label") or "N/A"
            ay_str = _format_ay(raw_ay)
            ack_no = inner.get("ackNum") or arn
            form_type = _translate_form_code(inner.get("formTypeCd"))
            sec_desc = _translate_section_code(inner.get("incmTaxSecCd") or inner.get("filingTypeCd"))
            
            # Processing status from history timeline
            status_desc = inner.get("statusDesc")
            act_list = inner.get("itrPanDetlList") or []
            if act_list and isinstance(act_list, list) and isinstance(act_list[0], dict):
                status_desc = act_list[0].get("statusDesc") or status_desc

            clean_status = _translate_efile_status(inner.get("efileStatus"), status_desc)
            refnd = inner.get("refundAmt") or inner.get("computedRefndAmt")
            has_refund = refnd and str(refnd) not in ("0", "null", "None")

            step_title = f"Viewed Filed Return: {ay_str} ({form_type})"
            category = "Filing History"
            icon = "mdi.file-document-check-outline"
            color = "#58A6FF"
            
            narrative = f"Reviewed filed return record for {ay_str} (Ack: {ack_no})."
            if sec_desc:
                narrative += f" Filed as {sec_desc}."
            narrative += f" CPC Status: '{clean_status}'."
            if has_refund:
                narrative += f" Refund determined: Rs. {refnd}."

            chips.append({"label": "Assessment Year", "val": ay_str})
            chips.append({"label": "Ack Number", "val": str(ack_no)})
            chips.append({"label": "Filing Status", "val": clean_status})
            if has_refund:
                chips.append({"label": "Refund", "val": f"Rs. {refnd}"})

        # 1.4 Downloaded Return JSON / Form
        elif "downloadfile" in url or "ITR" in inner or "ITR1" in inner or "ITR4" in inner or "PersonalInfo" in inner:
            itr_root = inner.get("ITR", inner)
            form_name = "ITR-4" if "ITR4" in itr_root else ("ITR-1" if "ITR1" in itr_root else "ITR Return")
            step_title = f"Downloaded Filed Return Form ({form_name})"
            category = "Download"
            icon = "mdi.download-box-outline"
            color = "#4CF9B7"
            narrative = f"Downloaded official filing submission form and computational JSON for {form_name}."
            chips.append({"label": "Form", "val": form_name})
            pan_found = capture.get("pan") or inner.get("PAN")
            if pan_found:
                chips.append({"label": "PAN", "val": str(pan_found)})

        # 1.5 e-File Return Wizard & Draft Status
        elif "return/details" in url or "returns/details" in url or ("isDraftPresent" in inner and "isReturnFiled" in inner):
            draft_form = _translate_form_code(inner.get("draftFormCode") or "ITR")
            has_draft = (inner.get("isDraftPresent") == "Y")
            is_filed = (inner.get("isReturnFiled") == "Y")
            
            step_title = f"Checked e-File Wizard ({draft_form})"
            category = "e-File Wizard"
            icon = "mdi.wizard-hat"
            color = "#D29922" if has_draft else "#58A6FF"
            
            narrative = f"Opened the e-File Return Wizard for {draft_form}."
            if is_filed and has_draft:
                narrative += " Return is already filed, and an in-progress draft is present on the portal."
            elif is_filed:
                narrative += " The return has already been filed for this assessment year."
            elif has_draft:
                narrative += " An in-progress draft is saved on the portal ready for continuation."
            else:
                narrative += " Ready to initiate new return filing (No prior draft)."

            chips.append({"label": "Form", "val": str(draft_form)})
            chips.append({"label": "Draft on Portal", "val": "Yes (Draft Present)" if has_draft else "None"})
            chips.append({"label": "Filing State", "val": "Already Filed" if is_filed else "Pending Filing"})

        # 1.6 Return Wizard Step Save / Insert SLA
        elif "save/wzrd" in url or "insertSla/wzrd" in url:
            step_title = "Saved In-Progress Return Draft"
            category = "e-File Wizard"
            icon = "mdi.content-save-outline"
            color = "#D29922"
            narrative = "Saved intermediate draft schedules in the return filing wizard."
            if inner.get("arnNumber"):
                chips.append({"label": "Draft Ref", "val": str(inner.get("arnNumber"))})

        # 1.7 Return Pre-Validation
        elif "validate/wzrd" in url:
            step_title = "Ran Return Pre-Filing Validation"
            category = "e-File Wizard"
            icon = "mdi.check-decagram-outline"
            color = "#58A6FF"
            narrative = "Executed validation checks across all return schedules prior to final submission."
            if inner.get("httpStatus"):
                chips.append({"label": "Validation Result", "val": str(inner.get("httpStatus"))})

        # 1.8 Final Return Submission
        elif "submit/wzrd" in url:
            ack_no = inner.get("arnNumber") or arn
            step_title = "Submitted Final Income Tax Return"
            category = "Filing Submission"
            icon = "mdi.send-check"
            color = "#4CF9B7"
            narrative = f"Submitted final return filing to Income Tax Portal (Filing Ack: {ack_no})."
            chips.append({"label": "Filing ARN", "val": str(ack_no)})

        # 1.9 E-Verification / OTP Validation (Only actual validateOTP calls)
        elif "validateOTP" in url or "modeEVrf" in str(inner):
            ack_no = inner.get("ackNum") or arn
            raw_ay = inner.get("assessmntYr") or inner.get("taxYear") or "N/A"
            ay_str = _format_ay(raw_ay)
            module_cd = inner.get("moduleCode", "ITR")
            
            if module_cd == "NON-ITR" or "FO-091" in str(inner.get("header", {}).get("formName")):
                step_title = "E-Verified Bank Account / Form (OTP)"
                category = "Bank Validation"
                icon = "mdi.bank-check"
                color = "#58A6FF"
                narrative = f"Completed statutory electronic verification for Bank/Form (Ack: {ack_no})."
                chips.append({"label": "Ack Number", "val": str(ack_no)})
            else:
                step_title = "Completed Return E-Verification"
                category = "E-Verification"
                icon = "mdi.shield-check"
                color = "#4CF9B7"
                narrative = f"Completed electronic verification for return (Ack: {ack_no}, {ay_str})."
                chips.append({"label": "Ack Number", "val": str(ack_no)})
                if ay_str != "N/A":
                    chips.append({"label": "Assessment Year", "val": ay_str})

        # 1.10 Taxpayer Profile & Contact Details
        elif "saveEntity" in url or "getEntity" in url or "verificationservices" in url or "profile" in url or "addrLine1Txt" in inner or "priMobileNum" in inner:
            pan_val = inner.get("entityNum") or inner.get("pan") or capture.get("pan") or ""
            is_aadhaar_linked = (inner.get("aadhaarLinkFlag") == "Y")
            mobile = inner.get("priMobileNum") or inner.get("mobileNo")
            email = inner.get("priEmailId") or inner.get("emailId")
            
            step_title = "Viewed Taxpayer Profile & Contact Info"
            category = "Profile"
            icon = "mdi.account-details"
            color = "#58A6FF"
            
            narrative = "Accessed taxpayer demographic and contact record on the portal."
            if is_aadhaar_linked:
                narrative += " Aadhaar is linked."
            if mobile or email:
                narrative += f" Verified contact details on file."

            if pan_val:
                chips.append({"label": "PAN", "val": str(pan_val)})
            chips.append({"label": "Aadhaar Linked", "val": "Yes" if is_aadhaar_linked else "No"})
            if mobile:
                chips.append({"label": "Mobile", "val": str(mobile)})
            if email:
                chips.append({"label": "Email", "val": str(email)})

    # =========================================================================
    # 2. GST Portal Endpoints
    # =========================================================================
    elif "gst" in url or "GST" in portal:
        if "gstr1/summary" in url or "ttl_tax" in inner or "act_tax" in inner:
            sec_name = inner.get("sec_nm") or inner.get("sec_name") or "GSTR-1"
            tax_val = inner.get("ttl_tax") or inner.get("act_tax") or "0"
            rec_cnt = inner.get("ttl_rec") or "0"
            
            step_title = f"Viewed GSTR-1 Section Summary ({sec_name})"
            category = "GST Return"
            icon = "mdi.calculator-variant-outline"
            color = "#58A6FF"
            narrative = f"Loaded tax calculations for GSTR-1 section '{sec_name}'. Total Tax: Rs. {tax_val} across {rec_cnt} invoice record(s)."
            chips.append({"label": "Section", "val": str(sec_name)})
            chips.append({"label": "Total Tax", "val": f"Rs. {tax_val}"})
            chips.append({"label": "Invoices", "val": str(rec_cnt)})

        elif "filingsnapshot" in url or ("formName" in inner and "retPrds" in inner):
            form_name = inner.get("formName") or "GST Returns"
            step_title = f"Checked GST Filing Due Dates ({form_name})"
            category = "GST Compliance"
            icon = "mdi.calendar-month-outline"
            color = "#58A6FF"
            narrative = f"Queried multi-period GST filing obligations and track record for {form_name}."
            chips.append({"label": "Form", "val": str(form_name)})

        elif "signatory" in url or "aadhar" in inner or "dg" in inner:
            auth_name = f"{inner.get('firstName', '')} {inner.get('lastName', '')}".strip() or inner.get("name") or "Authorized Signatory"
            desig = inner.get("dg") or "Signatory"
            step_title = "Viewed GST Authorized Signatory"
            category = "GST Profile"
            icon = "mdi.badge-account-outline"
            color = "#4CF9B7"
            narrative = f"Retrieved authorized signatory details: {auth_name} ({desig})."
            chips.append({"label": "Signatory", "val": str(auth_name)})
            chips.append({"label": "Designation", "val": str(desig)})

        elif "getRcmAvl" in url or "clsBal" in inner:
            step_title = "Checked GST RCM & ITC Ledger Balances"
            category = "GST Ledger"
            icon = "mdi.cash-multiple"
            color = "#58A6FF"
            narrative = "Retrieved Reverse Charge (RCM) closing balances and input tax credit (ITC) ledger."

        elif "formdetails" in url or ("data" in inner and "bn" in inner.get("data", {})):
            data_node = inner.get("data", {}) if isinstance(inner.get("data"), dict) else {}
            b_name = data_node.get("bn") or "Business Entity"
            arn_no = data_node.get("arn") or arn
            fy = data_node.get("fy") or "N/A"
            raw_fp = str(data_node.get("fp") or "")
            
            # Format period: 072026 -> July 2026
            months_map = {"01": "July", "02": "August", "03": "September", "04": "April", "05": "May", "06": "June", "07": "July", "08": "August", "09": "September", "10": "October", "11": "November", "12": "December"}
            period_str = raw_fp
            if len(raw_fp) == 6 and raw_fp[:2] in months_map:
                period_str = f"{months_map[raw_fp[:2]]} {raw_fp[2:]}"
            
            form_code = data_node.get("form", "GSTR-1") if "form" in data_node else ("GSTR-1" if "07" in raw_fp or "08" in raw_fp else "GST Return")

            has_valid_arn = bool(arn_no and arn_no != "N/A" and re.match(r"^[A-Za-z0-9]{12,18}$", str(arn_no).strip()))
            
            if has_valid_arn:
                step_title = f"Submitted GST Return: {form_code} (ARN Issued)"
                category = "Filing Submission"
                icon = "mdi.send-check"
                color = "#4CF9B7"
                narrative = f"Successfully submitted official GST return for {b_name} (Period: {period_str}, FY: {fy}). Official Government ARN Generated: {arn_no}."
                chips.append({"label": "Filing State", "val": "Submitted & Filed"})
                chips.append({"label": "Filing ARN", "val": str(arn_no)})
                if period_str:
                    chips.append({"label": "Tax Period", "val": period_str})
                if fy != "N/A":
                    chips.append({"label": "Financial Year", "val": str(fy)})
            else:
                step_title = f"Viewed GST Form Receipt ({form_code})"
                category = "GST Filing"
                icon = "mdi.receipt-text-check-outline"
                color = "#4CF9B7"
                narrative = f"Loaded official filing acknowledgement receipt for {b_name} (Period: {period_str}, FY: {fy})."
                chips.append({"label": "Business", "val": str(b_name)})
                if arn_no and arn_no != "N/A":
                    chips.append({"label": "ARN", "val": str(arn_no)})

    # Fallback
    if step_title == "Portal Activity Detected":
        if arn and arn != "N/A":
            step_title = f"Captured Portal Event ({arn})"
            narrative = f"Recorded event from {portal} (Identifier: {arn})."
            chips.append({"label": "Identifier", "val": str(arn)})
        else:
            step_title = f"Interaction on {portal}"
            narrative = f"Portal API exchange recorded on {portal}."

    return {
        "step_number": index,
        "elapsed_str": _format_elapsed(elapsed_sec),
        "timestamp_str": str(timestamp)[:19].replace("T", " "),
        "title": step_title,
        "category": category,
        "icon": icon,
        "color": color,
        "narrative": narrative,
        "chips": chips,
        "url": url,
        "session_id": session_id,
        "raw_capture": capture
    }


def collapse_consecutive_repeat_steps(steps: List[Dict[str, Any]], session_prefix: str = "s1") -> List[Dict[str, Any]]:
    """
    Collapses consecutive identical action steps into a single step with a repeat indicator,
    storing all individual sub-steps for interactive unfolding.
    """
    if not steps:
        return []

    collapsed = []
    curr_group = [steps[0]]

    def _get_sig(s):
        return (s.get("title"), s.get("category"), s.get("narrative"), json.dumps(s.get("chips", []), sort_keys=True))

    for next_step in steps[1:]:
        if _get_sig(next_step) == _get_sig(curr_group[0]):
            curr_group.append(next_step)
        else:
            rep_count = len(curr_group)
            base_step = dict(curr_group[0])
            s_num = len(collapsed) + 1
            base_step["step_number"] = s_num
            base_step["step_uid"] = f"{session_prefix}_step{s_num}"
            base_step["repeat_sub_steps"] = list(curr_group)
            if rep_count > 1:
                first_ts = curr_group[0]["timestamp_str"][11:19]
                last_ts = curr_group[-1]["timestamp_str"][11:19]
                base_step["repeat_count"] = rep_count
                base_step["repeat_span_str"] = f"{first_ts} ➔ {last_ts}"
            else:
                base_step["repeat_count"] = 1
            collapsed.append(base_step)
            curr_group = [next_step]

    if curr_group:
        rep_count = len(curr_group)
        base_step = dict(curr_group[0])
        s_num = len(collapsed) + 1
        base_step["step_number"] = s_num
        base_step["step_uid"] = f"{session_prefix}_step{s_num}"
        base_step["repeat_sub_steps"] = list(curr_group)
        if rep_count > 1:
            first_ts = curr_group[0]["timestamp_str"][11:19]
            last_ts = curr_group[-1]["timestamp_str"][11:19]
            base_step["repeat_count"] = rep_count
            base_step["repeat_span_str"] = f"{first_ts} ➔ {last_ts}"
        else:
            base_step["repeat_count"] = 1
        collapsed.append(base_step)

    return collapsed


def group_captures_into_sessions(captures: List[Dict[str, Any]], gap_threshold_sec: int = 1800) -> List[Dict[str, Any]]:
    """
    Groups raw captures belonging to a client into distinct sequential portal sessions
    based on unique session_id tokens or significant elapsed time gaps (>30 mins).
    Each session gets its own independent T+00s offset baseline and step counter.
    """
    if not captures:
        return []

    def _get_sort_key(c):
        ts = c.get("created_at") or c.get("timestamp") or ""
        dt = _parse_iso_datetime(ts)
        return dt.timestamp() if dt else 0.0

    sorted_caps = sorted(captures, key=_get_sort_key)
    
    sessions_raw = []
    curr_caps = []
    curr_ses_id = None
    last_dt = None

    for cap in sorted_caps:
        ts = cap.get("created_at") or cap.get("timestamp") or ""
        dt = _parse_iso_datetime(ts)
        
        # Extract session_id from capture or raw_payload_json
        raw_p = cap.get("raw_payload_json") or cap.get("raw_payload") or {}
        if isinstance(raw_p, str):
            try:
                raw_p = json.loads(raw_p)
            except Exception:
                raw_p = {}
        ses_id = cap.get("session_id") or (raw_p.get("session_id") if isinstance(raw_p, dict) else None)
        
        # Determine if this capture starts a new session
        is_new_session = False
        if not curr_caps:
            is_new_session = True
        elif ses_id and curr_ses_id and ses_id != curr_ses_id:
            is_new_session = True
        elif dt and last_dt and (dt.timestamp() - last_dt.timestamp()) > gap_threshold_sec:
            is_new_session = True

        if is_new_session and curr_caps:
            sessions_raw.append(curr_caps)
            curr_caps = []
            curr_ses_id = None

        curr_caps.append(cap)
        if ses_id:
            curr_ses_id = ses_id
        if dt:
            last_dt = dt

    if curr_caps:
        sessions_raw.append(curr_caps)

    decoded_sessions = []
    for s_idx, s_caps in enumerate(sessions_raw, start=1):
        t0 = _parse_iso_datetime(s_caps[0].get("created_at") or s_caps[0].get("timestamp"))
        t_end = _parse_iso_datetime(s_caps[-1].get("created_at") or s_caps[-1].get("timestamp"))
        duration_sec = (t_end.timestamp() - t0.timestamp()) if (t0 and t_end) else 0.0
        
        s_id = None
        for c in s_caps:
            rp = c.get("raw_payload_json") or c.get("raw_payload") or {}
            if isinstance(rp, str):
                try: rp = json.loads(rp)
                except Exception: rp = {}
            if isinstance(rp, dict) and rp.get("session_id"):
                s_id = rp.get("session_id")
                break
            if c.get("session_id"):
                s_id = c.get("session_id")
                break

        steps = []
        for step_idx, cap in enumerate(s_caps, start=1):
            ts = cap.get("created_at") or cap.get("timestamp") or ""
            dt = _parse_iso_datetime(ts)
            elapsed_sec = (dt.timestamp() - t0.timestamp()) if (dt and t0) else 0.0
            step = decode_single_capture(cap, index=step_idx, elapsed_sec=max(elapsed_sec, 0.0))
            steps.append(step)

        # Collapse consecutive identical actions in this session
        collapsed_steps = collapse_consecutive_repeat_steps(steps, session_prefix=f"s{s_idx}")

        dur_clean = _format_elapsed(duration_sec).replace("T+", "")
        decoded_sessions.append({
            "session_num": s_idx,
            "session_id": s_id or f"Session-{s_idx}",
            "start_time_str": str(t0)[:19].replace("T", " ") if t0 else "N/A",
            "date_str": str(t0)[:10] if t0 else "N/A",
            "duration_str": dur_clean if dur_clean != "00s" else "< 1s",
            "raw_total_captures": len(s_caps),
            "steps": collapsed_steps
        })

    return decoded_sessions


def decode_session_timeline(captures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ingests raw captures, groups them by session, and returns all decoded steps.
    """
    sessions = group_captures_into_sessions(captures)
    all_steps = []
    for s in sessions:
        all_steps.extend(s.get("steps", []))
    return all_steps


def format_timeline_flow_html(
    decoded_sessions_or_steps: Any,
    title_tag: str = "Client",
    expanded_step_uids: Any = None
) -> str:
    """
    Renders a lightweight, high-contrast, monospace terminal-style flow diagram with arrows
    organized into clean Session Containers with interactive expandable repeat indicators.
    """
    if not decoded_sessions_or_steps:
        return (
            '<div style="font-family: \'Consolas\', \'Courier New\', monospace; font-size: 12px; color: #8B949E; background-color: #0D1117; padding: 20px;">'
            'No interaction steps recorded for this container.'
            '</div>'
        )

    expanded_set = set(expanded_step_uids) if expanded_step_uids else set()

    # Normalize input
    if isinstance(decoded_sessions_or_steps, list) and decoded_sessions_or_steps and "steps" in decoded_sessions_or_steps[0]:
        sessions = decoded_sessions_or_steps
    else:
        # Single flat list of steps or captures
        sessions = [{"session_num": 1, "session_id": "Session-1", "date_str": "Current", "duration_str": "", "steps": decoded_sessions_or_steps}]

    total_steps = sum(len(s.get("steps", [])) for s in sessions)
    total_raw_captures = sum(s.get("raw_total_captures", len(s.get("steps", []))) for s in sessions)

    summary_note = f"{len(sessions)} Session(s) Recorded ({total_steps} Action Steps"
    if total_raw_captures > total_steps:
        summary_note += f" across {total_raw_captures} Captures"
    summary_note += ")"

    html = [
        '<div style="font-family: \'Consolas\', \'Courier New\', monospace; font-size: 12px; line-height: 1.45; color: #C9D1D9; background-color: #0D1117; padding: 14px;">',
        f'<div style="border: 1px solid #30363D; border-radius: 6px; padding: 8px 12px; margin-bottom: 16px; background-color: #161B22;">'
        f'<span style="color: #4CF9B7; font-weight: bold;">⚡ SESSION INTERACTION TIMELINE FLOW</span> &nbsp;|&nbsp; '
        f'<span style="color: #58A6FF;">Target: {title_tag}</span> &nbsp;|&nbsp; '
        f'<span style="color: #D29922;">{summary_note}</span>'
        f'</div>'
    ]

    for s_idx, session in enumerate(sessions):
        s_num = session.get("session_num", s_idx + 1)
        s_date = session.get("date_str", "")
        s_dur = session.get("duration_str", "")
        s_id = session.get("session_id", "")
        steps = session.get("steps", [])

        # Session Header Box
        dur_label = f" | Duration: {s_dur}" if s_dur else ""
        html.append(f'<div style="margin-top: {20 if s_idx > 0 else 4}px; margin-bottom: 12px; border: 1px solid #238636; border-left: 4px solid #2EA043; background-color: #161B22; border-radius: 4px; padding: 6px 10px;">')
        html.append(f'<span style="color: #4CF9B7; font-weight: bold;">🔷 SESSION {s_num}</span> &nbsp;──&nbsp; <span style="color: #E6EDF3; font-weight: 600;">{s_date}</span> <span style="color: #8B949E; font-size: 11px;">({len(steps)} Steps{dur_label})</span>')
        if s_id and s_id != f"Session-{s_num}":
            html.append(f'<br><span style="color: #8B949E; font-size: 10.5px;">Session Token: <code style="color: #79C0FF;">{s_id}</code></span>')
        html.append('</div>')

        for i, s in enumerate(steps):
            is_last = (i == len(steps) - 1)
            step_color = s.get("color", "#58A6FF")
            badge = f"[{s['step_number']:02d}]"
            cat = s["category"].upper()
            time_str = f"⏱ {s['elapsed_str']} &nbsp;•&nbsp; {s['timestamp_str'][11:19]}"
            step_uid = s.get("step_uid", f"s{s_num}_step{s['step_number']}")

            rep_badge = ""
            sub_items_html = ""
            if s.get("repeat_count", 1) > 1:
                is_exp = (step_uid in expanded_set)
                if is_exp:
                    rep_badge = f'<a href="#toggle_repeat_{step_uid}" style="color: #F85149; text-decoration: none; font-weight: bold; background-color: #21262D; border: 1px solid #F85149; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-left: 6px;">▼ Collapse {s["repeat_count"]} occurrences</a>'
                    sub_list = []
                    sub_list.append(f'<div style="margin-left: 20px; margin-top: 6px; margin-bottom: 6px; padding: 6px 10px; background-color: #161B22; border-left: 2px dashed #58A6FF; border-radius: 4px;">')
                    sub_list.append(f'<div style="color: #8B949E; font-size: 11px; margin-bottom: 4px; font-weight: bold;">Unfolded Sub-Occurrences ({s["repeat_count"]} total calls):</div>')
                    for sub_i, sub in enumerate(s.get("repeat_sub_steps", [])):
                        sub_list.append(
                            f'<div style="color: #C9D1D9; font-size: 11px; line-height: 1.45; margin-bottom: 3px;">'
                            f'<span style="color: #79C0FF; font-weight: bold;">• #{sub_i + 1:02d}</span> &nbsp;'
                            f'<span style="color: #D29922;">⏱ {sub["elapsed_str"]} ({sub["timestamp_str"][11:19]})</span> '
                            f'<span style="color: #8B949E;">── {sub["narrative"]}</span>'
                            f'</div>'
                        )
                    sub_list.append('</div>')
                    sub_items_html = "\n".join(sub_list)
                else:
                    rep_badge = f'<a href="#toggle_repeat_{step_uid}" style="color: #58A6FF; text-decoration: none; font-weight: bold; background-color: #21262D; border: 1px solid #388BFD; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-left: 6px;">▶ Expand {s["repeat_count"]} occurrences ({s["repeat_span_str"]})</a>'


            chips_html = ""
            if s["chips"]:
                chips_parts = []
                for c in s["chips"]:
                    chips_parts.append(
                        f'<span style="background-color: #21262D; color: #E6EDF3; padding: 1px 6px; border-radius: 3px; border: 1px solid #30363D; margin-right: 4px;">'
                        f'<b>{c["label"]}:</b> {c["val"]}</span>'
                    )
                chips_html = " ".join(chips_parts)

            html.append('<div style="margin-bottom: 0px;">')
            html.append(
                f'<span style="color: {step_color}; font-weight: bold;">{badge}</span> '
                f'<span style="color: #30363D;">──</span> '
                f'<span style="color: #D29922; font-weight: 600;">{time_str}</span> '
                f'<span style="color: #30363D;">────</span> '
                f'<span style="background-color: #161B22; color: {step_color}; font-weight: 600; padding: 1px 6px; border-radius: 3px; border: 1px solid #30363D;">[ {cat} ]</span>'
                f'{rep_badge}'
            )
            html.append('</div>')
            html.append('<div style="border-left: 2px solid #30363D; margin-left: 12px; padding-left: 14px; padding-top: 3px; padding-bottom: 4px;">')
            html.append(f'<div><span style="color: #58A6FF; font-weight: bold;">▶ Action   :</span> <span style="color: #FFFFFF; font-weight: 600;">{s["title"]}</span></div>')
            html.append(f'<div style="margin-top: 2px;"><span style="color: #8B949E; font-weight: bold;">▶ Story    :</span> <span style="color: #C9D1D9;">{s["narrative"]}</span></div>')
            if chips_html:
                html.append(f'<div style="margin-top: 3px;"><span style="color: #8B949E; font-weight: bold;">▶ Details  :</span> {chips_html}</div>')
            if sub_items_html:
                html.append(sub_items_html)
            html.append('</div>')

            if not is_last:
                html.append('<div style="margin-left: 9px; color: #30363D; font-size: 13px; line-height: 1;">▼</div>')
            else:
                html.append(f'<div style="margin-left: 9px; color: #2EA043; font-weight: bold; padding-top: 2px;">└──▶ <span style="color: #8B949E; font-weight: normal;">[SESSION {s_num} COMPLETED]</span></div>')

    html.append('</div>')
    return "\n".join(html)



def format_timeline_flow_plain(decoded_sessions_or_steps: Any, title_tag: str = "Client") -> str:
    """
    Renders clean ASCII diagram format for clipboard copy, partitioned by session with repeat indicators.
    """
    if not decoded_sessions_or_steps:
        return f"=== SESSION AUDIT TIMELINE — {title_tag} ===\nNo steps recorded."

    if isinstance(decoded_sessions_or_steps, list) and decoded_sessions_or_steps and "steps" in decoded_sessions_or_steps[0]:
        sessions = decoded_sessions_or_steps
    else:
        sessions = [{"session_num": 1, "session_id": "Session-1", "date_str": "Current", "duration_str": "", "steps": decoded_sessions_or_steps}]

    total_steps = sum(len(s.get("steps", [])) for s in sessions)
    total_raw_captures = sum(s.get("raw_total_captures", len(s.get("steps", []))) for s in sessions)

    summary_note = f"{len(sessions)} Session(s) | {total_steps} Steps"
    if total_raw_captures > total_steps:
        summary_note += f" ({total_raw_captures} Total Captures)"

    lines = [
        "╔══════════════════════════════════════════════════════════════════════════════════════════════╗",
        f"║  SESSION INTERACTION TIMELINE FLOW — {title_tag} ({summary_note})" + " " * max(0, 30 - len(title_tag) - len(summary_note) + 20) + "║",
        "╚══════════════════════════════════════════════════════════════════════════════════════════════╝",
        ""
    ]

    for s_idx, session in enumerate(sessions):
        s_num = session.get("session_num", s_idx + 1)
        s_date = session.get("date_str", "")
        s_dur = session.get("duration_str", "")
        s_id = session.get("session_id", "")
        steps = session.get("steps", [])

        dur_label = f" | Duration: {s_dur}" if s_dur else ""
        lines.append(f"┌── 🔷 SESSION {s_num} ── {s_date} ({len(steps)} Steps{dur_label}) " + "─" * max(5, 45 - len(s_date) - len(dur_label)) + "┐")
        if s_id and s_id != f"Session-{s_num}":
            lines.append(f"│   Token: {s_id}")
        lines.append("└" + "─" * 80 + "┘")

        for i, s in enumerate(steps):
            is_last = (i == len(steps) - 1)
            badge = f"[{s['step_number']:02d}]"
            cat = s["category"].upper()
            time_str = f"{s['elapsed_str']} ({s['timestamp_str'][11:19]})"
            
            rep_tag = f" [🔄 Repeated {s['repeat_count']}x ({s['repeat_span_str']})]" if s.get("repeat_count", 1) > 1 else ""

            pad_len = max(5, 55 - len(time_str) - len(cat) - len(rep_tag))
            lines.append(f"  {badge} ── {time_str} ──── [ {cat} ]{rep_tag} " + ("─" * pad_len))
            lines.append(f"   │  ▶ Action   : {s['title']}")
            lines.append(f"   │  ▶ Story    : {s['narrative']}")
            if s["chips"]:
                chips_str = "   ".join(f"[{c['label']}: {c['val']}]" for c in s["chips"])
                lines.append(f"   │  ▶ Details  : {chips_str}")
            if not is_last:
                lines.append("   │")
                lines.append("   ▼")
            else:
                lines.append("   │")
                lines.append(f"   └──▶ [SESSION {s_num} COMPLETED]")
        lines.append("")

    return "\n".join(lines)



