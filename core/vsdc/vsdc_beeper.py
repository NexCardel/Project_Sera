"""
core/vsdc/vsdc_beeper.py — PAN Beeper (Data Anonymizer & Token Minimizer)
=======================================================================
1. Anonymizes all sensitive taxpayer credentials (GSTIN, PAN, Phone, Email, Bank/IFSC)
   by replacing them with deterministic anonymous tokens: [GSTIN_TOKEN], [PAN_TOKEN].
2. Slims payloads down to minimal token context (< 80-100 tokens) by discarding
   portal boilerplate (navigation bars, footers, helpline text, repeated tabs).
3. Pre-Flight Leak Guard: assert_zero_sensitive_data() halts execution locally if
   any sensitive pattern is detected in the outgoing prompt before network dispatch.
"""

import re
from typing import List, Dict, Tuple, Optional, Any

# Strict patterns for sensitive Indian compliance credentials
RE_GSTIN = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
RE_MASKED_GSTIN = re.compile(r"\b[0-9]{2}[A-Z0-9*]{8,12}[1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
RE_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
RE_MASKED_PAN = re.compile(r"\b[A-Z]{2}[*]{3,6}[0-9]{2,4}[A-Z]\b", re.IGNORECASE)
RE_PHONE = re.compile(r"\b(?:\+91[- ]?)?[6-9]\d{9}\b")
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RE_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
RE_BANK_ACC = re.compile(r"\b\d{11,18}\b")

# Portal boilerplate tokens to discard to aggressively slim prompt tokens
BOILERPLATE_SUBSTRINGS = (
    "skip to main content",
    "screen reader",
    "help desk",
    "toll free",
    "copyright",
    "all rights reserved",
    "designed and developed",
    "portal last updated",
    "disclaimer",
    "privacy policy",
    "hyperlink policy",
    "terms and conditions",
    "quick links",
    "services",
    "search taxpayer",
    "feedback",
    "site map",
    "national informatics centre",
    "gstn",
    "cbic",
    "e-way bill system",
    "logout",
    "dashboard",
    "return dashboard",
    "help on this page",
    "instructions for filing",
)

# Anchor phrases that denote valuable compliance fields
VALUABLE_ANCHORS = (
    "legal name",
    "trade name",
    "financial year",
    "tax period",
    "fy ",
    "period",
    "gstr",
    "iff",
    "itr",
    "filed",
    "status",
    "due date",
    "quarter",
    "welcome",
    "details of the taxpayer",
    "view filed returns",
    "acknowledgement",
)


class SensitiveDataLeakageError(Exception):
    """Raised when an outgoing payload contains unmasked client credentials."""
    pass


class PANBeeper:
    """
    Scans raw optical or UIA text lines, anonymizes all PAN/GSTIN/PII,
    slims the payload to minimize LLM token consumption, and validates
    zero-leakage compliance before any external API dispatch.
    """

    @classmethod
    def anonymize_and_slim(
        cls,
        ocr_lines: List[str],
        known_pan: Optional[str] = None,
        known_gstin: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processes OCR/text lines:
        1. Identifies and extracts unmasked GSTIN & PAN for local retention.
        2. Replaces all occurrences with [GSTIN_TOKEN] and [PAN_TOKEN].
        3. Removes portal navigation and footer boilerplate.
        4. Retains only high-information lines around relevant anchors.
        
        Returns:
            {
                "slimmed_masked_text": str,
                "extracted_pan": str or None,
                "extracted_gstin": str or None,
                "line_count": int,
                "estimated_tokens": int
            }
        """
        extracted_pan = known_pan.strip() if known_pan else None
        extracted_gstin = known_gstin.strip() if known_gstin else None
        
        # Step 1: Detect unmasked PAN/GSTIN if not already known
        for line in ocr_lines:
            if not extracted_gstin:
                m_gst = RE_GSTIN.search(line)
                if m_gst:
                    extracted_gstin = m_gst.group(0).upper()
            if not extracted_pan:
                m_pan = RE_PAN.search(line)
                if m_pan:
                    extracted_pan = m_pan.group(0).upper()
                    
        # If GSTIN was found, its characters 3..12 is the PAN
        if extracted_gstin and len(extracted_gstin) == 15 and not extracted_pan:
            extracted_pan = extracted_gstin[2:12]

        # Step 2: Line-by-line filtering & masking
        clean_lines = []
        for raw_line in ocr_lines:
            line_str = raw_line.strip()
            if not line_str or len(line_str) < 2:
                continue
            
            line_lower = line_str.lower()
            
            # Discard boilerplate lines
            if any(bp in line_lower for bp in BOILERPLATE_SUBSTRINGS):
                continue
                
            # Keep line if it has valuable anchors or looks like name / form / period
            is_valuable = any(anc in line_lower for anc in VALUABLE_ANCHORS)
            if not is_valuable and len(clean_lines) >= 15:
                # If we already have enough lines, discard low-relevance noise
                continue

            # Mask credentials in this line
            masked_line = cls.mask_text(
                line_str,
                target_pan=extracted_pan,
                target_gstin=extracted_gstin
            )
            
            clean_lines.append(masked_line)

        # De-duplicate consecutive identical lines
        deduped = []
        for l in clean_lines:
            if not deduped or deduped[-1] != l:
                deduped.append(l)

        # Limit to top 20 relevant lines to strictly minimize token footprint (< 80 tokens)
        slimmed_text = "\n".join(deduped[:20])

        # Step 3: Run Pre-Flight Leak Guard
        cls.assert_zero_sensitive_data(slimmed_text)

        estimated_tokens = max(1, len(slimmed_text) // 4)

        return {
            "slimmed_masked_text": slimmed_text,
            "extracted_pan": extracted_pan,
            "extracted_gstin": extracted_gstin,
            "line_count": len(deduped),
            "estimated_tokens": estimated_tokens
        }

    @classmethod
    def mask_text(
        cls,
        text: str,
        target_pan: Optional[str] = None,
        target_gstin: Optional[str] = None
    ) -> str:
        """
        Replaces all sensitive compliance identifiers with anonymous tokens.
        """
        masked = text

        # Replace specific known GSTIN and PAN first
        if target_gstin:
            masked = re.sub(re.escape(target_gstin), "[GSTIN_TOKEN]", masked, flags=re.IGNORECASE)
        if target_pan:
            masked = re.sub(re.escape(target_pan), "[PAN_TOKEN]", masked, flags=re.IGNORECASE)

        # Replace standard GSTIN patterns
        masked = RE_GSTIN.sub("[GSTIN_TOKEN]", masked)
        masked = RE_MASKED_GSTIN.sub("[GSTIN_TOKEN]", masked)

        # Replace standard PAN patterns
        masked = RE_PAN.sub("[PAN_TOKEN]", masked)
        masked = RE_MASKED_PAN.sub("[PAN_TOKEN]", masked)

        # Replace Phone, Email, Bank/IFSC
        masked = RE_PHONE.sub("[PHONE_REDACTED]", masked)
        masked = RE_EMAIL.sub("[EMAIL_REDACTED]", masked)
        masked = RE_IFSC.sub("[IFSC_REDACTED]", masked)
        masked = RE_BANK_ACC.sub("[BANK_ACC_REDACTED]", masked)

        return masked

    @classmethod
    def assert_zero_sensitive_data(cls, text: str):
        """
        PRE-FLIGHT LEAK GUARD:
        Strictly scans text before any network transmission.
        Raises SensitiveDataLeakageError if ANY unmasked PAN, GSTIN,
        phone, or email pattern is found.
        """
        # 1. Check for unmasked 15-char GSTIN
        gst_match = RE_GSTIN.search(text)
        if gst_match:
            raise SensitiveDataLeakageError(
                f"PRE-FLIGHT SECURITY VIOLATION: Unmasked GSTIN detected: {gst_match.group(0)[:4]}***"
            )

        # 2. Check for unmasked 10-char PAN
        # Ignore false positives like pure dictionary words if any, but standard PAN has 4 digits
        pan_matches = RE_PAN.findall(text)
        for p in pan_matches:
            # Only trigger if it's not a known false anchor word
            if p.upper() not in ("GSTR1", "GSTR3", "GSTR4", "GSTR9", "INVOI"):
                raise SensitiveDataLeakageError(
                    f"PRE-FLIGHT SECURITY VIOLATION: Unmasked PAN detected: {p[:3]}***"
                )

        # 3. Check for unmasked Indian mobile numbers
        phone_match = RE_PHONE.search(text)
        if phone_match:
            raise SensitiveDataLeakageError("PRE-FLIGHT SECURITY VIOLATION: Unmasked phone number detected.")

        # 4. Check for unmasked Email addresses
        email_match = RE_EMAIL.search(text)
        if email_match:
            raise SensitiveDataLeakageError("PRE-FLIGHT SECURITY VIOLATION: Unmasked email address detected.")

        return True
