"""
core/vsdc/vsdc_regex.py — Statutory Formats, Regex Validation & Optical Self-Repair
==================================================================================
Deterministic extraction and optical character confusion self-repair for:
- 15-digit Income Tax Acknowledgement Numbers
- 15-character GST Application Reference Numbers (ARN)
- 10-character Permanent Account Numbers (PAN)
- 15-character Goods and Services Tax Identification Numbers (GSTIN)
- Assessment Years (AY 20XX-YY) & GST Return Periods
- E-Verification & Submission Statuses
"""

import re
from typing import Optional, Dict, Tuple


# Optical confusion replacement tables
DIGIT_FIX_MAP = {
    'l': '1', 'I': '1', '|': '1', '!': '1',
    'O': '0', 'o': '0', 'D': '0', 'Q': '0',
    'B': '8',
    'S': '5', 's': '5',
    'Z': '2', 'z': '2',
    'g': '9', 'q': '9',
}

LETTER_FIX_MAP = {
    '0': 'O',
    '1': 'I',
    '5': 'S',
    '8': 'B',
    '2': 'Z',
}


def repair_numeric_ack(raw_str: str) -> Optional[str]:
    """
    Validates and repairs a 15-digit ITR Acknowledgement Number from optical OCR text.
    Corrects common OCR digit confusions (l/I -> 1, O/o -> 0, B -> 8, S -> 5)
    and handles OCR space fragmentation (e.g. 5982 9000 0150 925).
    """
    if not raw_str:
        return None

    # 1. Check for exact 15-digit match directly in the raw text
    direct = re.search(r"\b\d{15}\b", raw_str)
    if direct:
        return direct.group(0)

    # 2. Check explicitly labeled Ack numbers that may have optical spaces or hyphens
    labeled_m = re.search(
        r"(?:Acknowledgement|Receipt|Ack|ARN)\s*(?:Number|No\.?)?\s*[:#\-]?\s*([0-9\s\-]{15,25})",
        raw_str,
        re.IGNORECASE,
    )
    if labeled_m:
        raw_cand = labeled_m.group(1)
        clean_cand = re.sub(r"[\s\-]+", "", raw_cand)
        if len(clean_cand) >= 15:
            prefix15 = clean_cand[:15]
            repaired = "".join(DIGIT_FIX_MAP.get(c, c) for c in prefix15)
            if re.match(r"^\d{15}$", repaired):
                return repaired

    # 3. Check tokens of length 15 for optical repair
    for token in re.findall(r"\b[A-Za-z0-9|!]{15}\b", raw_str):
        repaired = "".join(DIGIT_FIX_MAP.get(c, c) for c in token)
        if re.match(r"^\d{15}$", repaired):
            return repaired

    # 4. Fallback: search anywhere in non-space chunks
    for token in raw_str.split():
        clean_tok = re.sub(r"[^\w|!]", "", token)
        if len(clean_tok) == 15:
            repaired = "".join(DIGIT_FIX_MAP.get(c, c) for c in clean_tok)
            if re.match(r"^\d{15}$", repaired):
                return repaired

    # 5. Check consecutive numeric fragments that sum to 15 digits (e.g. '5982 9000 0150 925')
    for m in re.finditer(r"\b(\d{3,6}(?:\s+\d{3,6}){2,4})\b", raw_str):
        merged = re.sub(r"\s+", "", m.group(1))
        if len(merged) == 15 and merged.isdigit():
            return merged

    return None


def repair_gst_arn(raw_str: str) -> Optional[str]:
    """
    Validates and repairs a 15-character GST ARN (e.g. AA070826000001Z).
    Format: 2 letters (State Code) + 12 digits + 1 checksum letter/digit.
    """
    if not raw_str:
        return None

    # 1. Direct regex match on raw text: 2 letters + 12 digits + 1 alnum
    direct = re.search(r"\b[A-Z]{2}\d{12}[A-Z0-9]\b", raw_str.upper())
    if direct:
        return direct.group(0)

    # 2. Optical repair on 15-char tokens
    for token in re.findall(r"\b[A-Za-z0-9]{15}\b", raw_str):
        token_upper = token.upper()
        state_code = "".join(LETTER_FIX_MAP.get(c, c) for c in token_upper[:2])
        digits = "".join(DIGIT_FIX_MAP.get(c, c) for c in token_upper[2:14])
        checksum = token_upper[14]
        candidate = state_code + digits + checksum
        if re.match(r"^[A-Z]{2}\d{12}[A-Z0-9]$", candidate):
            return candidate

    return None


def extract_pan(text: str) -> Optional[str]:
    """
    Extracts and validates a 10-character Indian PAN.
    Pattern: 5 uppercase letters, 4 digits, 1 uppercase letter.
    Enforces 4th character entity validity: P, C, F, H, A, T, B, L, J, G.
    """
    if not text:
        return None

    text_up = text.upper()

    # 1. Search for standard exact PAN (10 characters: 5 letters, 4 digits, 1 letter)
    matches = re.finditer(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", text_up)
    for m in matches:
        pan = m.group(1)
        if pan[3] in "PCFHTABRLJG":
            return pan

    # 2. Labeled PAN candidate (explicitly preceded by 'PAN')
    m_labeled = re.search(r"\bPAN\s*[:\-]?\s*([A-Z0-9]{10})\b", text_up)
    if m_labeled:
        cand = m_labeled.group(1)
        prefix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[:5])
        digits = "".join(DIGIT_FIX_MAP.get(c, c) for c in cand[5:9])
        suffix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[9:10])
        candidate_pan = prefix + digits + suffix
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", candidate_pan):
            if candidate_pan[3] in "PCFHTABRLJG":
                return candidate_pan

    # 3. Unlabeled candidate: Must NOT be purely alphabetic (skips words like 'PROFESSION', 'COMMISSION')
    # and must already contain at least one genuine digit in the middle numeric slice.
    for m in re.finditer(r"\b([A-Z0-9]{10})\b", text_up):
        cand = m.group(1)
        if cand.isalpha() or not any(c.isdigit() for c in cand[5:9]):
            continue
        prefix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[:5])
        digits = "".join(DIGIT_FIX_MAP.get(c, c) for c in cand[5:9])
        suffix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[9:10])
        candidate_pan = prefix + digits + suffix
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", candidate_pan):
            if candidate_pan[3] in "PCFHTABRLJG":
                return candidate_pan

    return None


def extract_gstin(text: str) -> Optional[str]:
    """
    Extracts a 15-character Goods and Services Tax Identification Number (GSTIN).
    Format: 2 digits (State) + 10-char PAN + 1 entity code + 'Z' + 1 checksum.
    """
    if not text:
        return None

    m = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", text.upper())
    return m.group(1) if m else None


def extract_assessment_year(text: str) -> Optional[str]:
    """
    Extracts Assessment Year (e.g. 'AY 2025-26', 'A.Y. 2025-26', 'Assessment Year 2025-26').
    - Enforces mathematical year consistency (second year = first year + 1).
    - Ignores portal disclaimer boilerplate (e.g. 'starting Assessment Year 2013-14').
    - Prioritizes explicitly labeled matches and the most recent assessment year.
    """
    if not text:
        return None

    candidates = []

    # 1. Matches AY / A.Y. / Assessment Year: 2025-26, 2025 - 26, 2025-2026, or plain 2025-26
    ay_pattern = re.compile(
        r"(?:(A\.?Y\.?|Assessment\s*Year)\s*[:\-]?)?\s*\b20(\d{2})\s*[-–/]\s*(?:20)?(\d{2})\b",
        re.IGNORECASE,
    )

    for m in ay_pattern.finditer(text):
        label = m.group(1)
        y1 = int(m.group(2))
        y2 = int(m.group(3))
        # Mathematical consistency check: e.g. 25-26
        if y2 != (y1 + 1) % 100 and y2 != y1 + 1:
            continue

        # Check preceding text for portal disclaimer banners (e.g. 'starting Assessment Year 2013-14')
        start_idx = m.start()
        raw_prefix = text[max(0, start_idx - 60):start_idx]
        clause_prefix = re.split(r"[\n\r.!?*•|]", raw_prefix)[-1].lower()
        if any(w in clause_prefix for w in ("starting", "since", "from", "available")):
            continue

        is_labeled = bool(label)
        canon_label = f"AY 20{y1:02d}-{((y1 + 1) % 100):02d}"
        candidates.append((is_labeled, y1, canon_label))

    if not candidates:
        return None

    # Sort by: 1) is_labeled (True first), 2) latest year (y1 descending)
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2]


def extract_filing_type(text: str, allow_multiple: bool = False) -> Optional[str]:
    """
    Identifies statutory filing form name from optical text.
    Suppresses latching when an open dropdown menu is detected (multiple distinct forms present),
    unless allow_multiple=True (e.g. on historical filed returns views).
    """
    if not text:
        return None

    # 1. Income Tax ITR forms (ITR-1, ITR 1, ITR-4, ITR-4S, Form ITR-1)
    itr_matches = re.findall(r"\b(?:Form\s*)?(ITR\s*[-_]?\s*[1-7UV]|ITR\s*[-_]?\s*4S)\b", text, re.IGNORECASE)
    if itr_matches:
        canonical_forms = []
        for m in itr_matches:
            val = re.sub(r"[-_\s]+", "-", m.upper())
            if not val.startswith("ITR-"):
                val = "ITR-" + val[3:].lstrip("-")
            if val not in canonical_forms:
                canonical_forms.append(val)
        # Dropdown Open Guard: If multiple distinct forms appear, the dropdown list is currently open!
        if len(canonical_forms) > 1 and not allow_multiple:
            return None
        return canonical_forms[0]

    # 2. Statutory Forms (10-IEA, 10BA, 29B, etc.)
    form_m = re.search(r"\b(?:Form\s*)?(10[-_ ]?[A-Z]+|29B|3CA|3CB|15CA|15CB)\b", text, re.IGNORECASE)
    if form_m:
        return "Form " + form_m.group(1).upper().replace(" ", "-")

    # 3. GST Forms
    gst_matches = re.findall(r"\b(GSTR[-_\s]?[1-9A-Z]+|CMP[-_\s]?08|IFF)\b", text, re.IGNORECASE)
    if gst_matches:
        canonical_gst = []
        for m in gst_matches:
            val = re.sub(r"[-_\s]+", "-", m.upper())
            if val not in canonical_gst:
                canonical_gst.append(val)
        if len(canonical_gst) > 1 and not allow_multiple:
            return None
        return canonical_gst[0]

    return None


def is_page_loading(text: str) -> bool:
    """
    Checks if optical text indicates an active asynchronous loading/transition state.
    """
    if not text:
        return False
    t = text.lower()
    return any(indicator in t for indicator in (
        "loading ...",
        "loading...",
        "please wait",
        "fetching details",
        "fetching...",
        "0 filings till date loading",
        "0-0 of 0 items",
    ))


def extract_view_filed_returns_card(text: str) -> Optional[Dict[str, Any]]:
    """
    Extracts the latest filed return card record from historical view screens
    (e.g. itr_view_filed_returns). Extracts the latest Assessment Year, Ack Number,
    Form Type, and Status.
    Returns details for strictly the single latest return on screen, or None if still loading / empty.
    """
    if not text or is_page_loading(text):
        return None

    ay = extract_assessment_year(text)
    ack = repair_numeric_ack(text)
    if not ay or not ack:
        return None

    form = extract_filing_type(text, allow_multiple=True) or "ITR-1"
    status = classify_verification_status(text)
    if status in ("Not Submitted", "Filing Submitted"):
        status = "Submitted (e-Verified)"

    return {
        "ack": ack,
        "ay": ay,
        "form": form,
        "status": status,
    }


def classify_verification_status(text: str) -> str:
    """
    Classifies the filing submission and e-verification status.
    Distinguishes between:
    - Submitted (e-Verified): Successfully completed verification or processed
    - Submitted (Not e-Verified): Return filed, awaiting e-verification (e.g. pending for e-verification, verify later, 30 days)
    - Filing Submitted: Standard submission confirmation
    """
    if not text:
        return "Submitted"

    lower = text.lower()
    # Normalize optical variations of e-verification / e-verify (e.g. 'e- verification', 'e - verify')
    normalized = re.sub(r"e\s*[-_ ]\s*verif", "e-verif", lower)

    # 1. Check if already successfully e-verified or processed
    if any(k in normalized for k in (
        "successfully e-verified",
        "successfully verified",
        "return has been verified",
        "return is verified",
        "e-verified on",
        "verified on",
        "processed with no demand/refund",
        "processed with refund",
        "processed with demand",
        "under processing",
        "processed",
        "verified with aadhaar otp",
        "otp validated",
        "e-verification successful",
        "e-verification completed",
    )):
        return "Submitted (e-Verified)"

    # 2. Check if pending verification / unverified
    if any(k in normalized for k in (
        "pending for e-verification",
        "pending for verification",
        "pending e-verification",
        "pending verification",
        "verification pending",
        "verify later",
        "e-verify later",
        "verify within 30 days",
        "not e-verified",
        "pending for e-verify",
        "under e-verification",
        "need to e-verify",
    )):
        return "Submitted (Not e-Verified)"

    # 3. Fallback: general e-verified vs unverified
    if "e-verified" in normalized and "not e-verified" not in normalized:
        return "Submitted (e-Verified)"

    # 4. Check explicit submission success
    if any(k in normalized for k in (
        "submitted successfully",
        "successfully submitted",
        "return submitted",
        "filed successfully",
        "itr filed",
        "filing confirmed",
        "return has been submitted",
        "you have successfully submitted your return",
        "arn generated",
        "acknowledgement number",
    )):
        return "Filing Submitted"

    return "Not Submitted"
