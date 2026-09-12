"""
tests/test_vsdc_regex.py — Unit Tests for VSDC Statutory Regex & Optical Self-Repair
"""

import unittest
from core.vsdc.vsdc_regex import (
    repair_numeric_ack,
    repair_gst_arn,
    extract_pan,
    extract_gstin,
    extract_assessment_year,
    extract_filing_type,
    classify_verification_status,
)


class TestVSDCRegex(unittest.TestCase):

    def test_ack_extraction_and_optical_repair(self):
        # Clean 15-digit Ack
        ack = repair_numeric_ack("Your acknowledgement number is 982348123456789.")
        self.assertEqual(ack, "982348123456789")

        # Optical confusion with 'l' and 'O'
        # '982348l2345678O' -> '982348123456780'
        ack_repaired = repair_numeric_ack("Ack: 982348l2345678O")
        self.assertEqual(ack_repaired, "982348123456780")

        # Optical confusion with 'B' -> '8' and 'S' -> '5'
        ack_repaired_2 = repair_numeric_ack("Ref: 9B23481234S6789")
        self.assertEqual(ack_repaired_2, "982348123456789")

    def test_gst_arn_extraction(self):
        arn = repair_gst_arn("ARN for filing: AA070826000001Z generated successfully")
        self.assertEqual(arn, "AA070826000001Z")

        # Optical repair on ARN digits
        arn_repaired = repair_gst_arn("ARN: AA0708260OO001Z")
        self.assertEqual(arn_repaired, "AA070826000001Z")

    def test_pan_extraction(self):
        # Individual PAN
        pan = extract_pan("Taxpayer PAN: AHJPR0846B in profile")
        self.assertEqual(pan, "AHJPR0846B")

        # Company PAN
        pan_corp = extract_pan("Company PAN: AABCA1234F registered")
        self.assertEqual(pan_corp, "AABCA1234F")

        # Rejects invalid 4th character
        pan_invalid = extract_pan("Dummy PAN: AABZA1234Z")
        self.assertIsNone(pan_invalid)

        # Rejects purely alphabetic words (e.g. Profession -> PROFE5510N false positive)
        self.assertIsNone(extract_pan("Income from Business or Profession"))
        self.assertIsNone(extract_pan("Submission of return confirmation"))

        # Repaired optical PAN with OCR letter 'O' instead of '0'
        pan_repaired = extract_pan("PAN: BEBPM912OB")
        self.assertEqual(pan_repaired, "BEBPM9120B")

    def test_gstin_extraction(self):
        gstin = extract_gstin("GSTIN of assessee is 07AHJPR0846B1Z5 on invoice")
        self.assertEqual(gstin, "07AHJPR0846B1Z5")

    def test_assessment_year_extraction(self):
        ay = extract_assessment_year("Return filed for AY 2026-27 successfully")
        self.assertEqual(ay, "AY 2026-27")

        # Ignores disclaimer boilerplate and extracts actual return card AY
        raw_portal_text = (
            "*Filed Returns are available for download /view starting Assessment Year 2013-14. "
            "14 Filings till date A.Y. 2025-26 Filing Type Original"
        )
        self.assertEqual(extract_assessment_year(raw_portal_text), "AY 2025-26")

        # Rejects mathematically broken year (e.g. 2026-29)
        ay_bad = extract_assessment_year("AY 2026-29")
        self.assertIsNone(ay_bad)

    def test_filing_type_extraction(self):
        self.assertEqual(extract_filing_type("Income Tax Return Form ITR-4"), "ITR-4")
        self.assertEqual(extract_filing_type("Selecting ITR-1 Sahaj"), "ITR-1")
        self.assertEqual(extract_filing_type("Return form GSTR-3B monthly"), "GSTR-3B")
        self.assertEqual(extract_filing_type("Challan CMP-08 Quarterly"), "CMP-08")
        self.assertEqual(extract_filing_type("Form 10-IEA New Regime"), "Form 10-IEA")

    def test_verification_status_classification(self):
        self.assertEqual(
            classify_verification_status("Return successfully e-verified with Aadhaar OTP"),
            "Submitted (e-Verified)"
        )
        self.assertEqual(
            classify_verification_status("Return submitted. Please e-verify within 30 days or verify later"),
            "Submitted (Not e-Verified)"
        )
        # Live portal unverified case with optical space in 'e- verification'
        self.assertEqual(
            classify_verification_status("Filing Type Original Pending for e- verification sep 15, 2025 ITR Filed sep 15, 2025"),
            "Submitted (Not e-Verified)"
        )
        # Live portal processed & verified case
        self.assertEqual(
            classify_verification_status("Processed with no demand/refund sep 1, 2025 Successfully e-verified Aug 31, 2025 Pending for e- verification"),
            "Submitted (e-Verified)"
        )
        # Empty screen or loading page with no positive submission indicators defaults to "Not Submitted"
        self.assertEqual(
            classify_verification_status("0 Filings till date Loading ... 0-0 of 0 items"),
            "Not Submitted"
        )

    def test_is_page_loading_detection(self):
        from core.vsdc.vsdc_regex import is_page_loading
        self.assertTrue(is_page_loading("e-Filing Portal ... 0 Filings till date Loading ... 0-0 of 0 items"))
        self.assertTrue(is_page_loading("Please wait while we fetch your details..."))
        self.assertFalse(is_page_loading("14 Filings till date A.Y. 2025-26 Filing Type Original ITR-1 Acknowledgement Number : 198273645019283"))

    def test_extract_view_filed_returns_card(self):
        from core.vsdc.vsdc_regex import extract_view_filed_returns_card
        # Intermediate loading state returns None
        loading_text = "e-Filing Income Tax ... 0 Filings till date Loading ... 0-0 of 0 items"
        self.assertIsNone(extract_view_filed_returns_card(loading_text))

        # Real rendered cards with multiple return history
        rendered_text = (
            "WASIL MANDAL v e-File Authorised Partners Services AIS\n"
            "14 Filings till date\n"
            "Assessment Year : 2025-26\n"
            "Filing Type : Original\n"
            "ITR-1\n"
            "Acknowledgement Number : 198273645019283\n"
            "Filing Date : 15-Jul-2025\n"
            "Successfully e-verified Aug 31, 2025\n"
            "Assessment Year : 2024-25\n"
            "Filing Type : Original\n"
            "ITR-4\n"
            "Acknowledgement Number : 982348123456789\n"
            "Successfully e-verified\n"
        )
        card = extract_view_filed_returns_card(rendered_text)
        self.assertIsNotNone(card)
        self.assertEqual(card["ay"], "AY 2025-26")
        self.assertEqual(card["ack"], "198273645019283")
        self.assertEqual(card["form"], "ITR-1")
        self.assertEqual(card["status"], "Submitted (e-Verified)")

        # Real Wasil Aman portal dump with disclaimer and AY 2026-27
        wasil_text = (
            "e-FiIing Income Tax Department, Govemment Of India 5 : oo e-File Authorised Partners Services AIS Call Us "
            "v Pending Actions English v Grievances Help WASIL MANDAL v Individual Session Time 1 "
            "Dashboard > e-flle > Income Tax Return > View Filed Returns View Filed Returns "
            "The e-Filed Returns are available for download /view starting Assessment Year 2013-14. "
            "Please note that the refund will be credited only to the PAN linked Bank Account. Please ensure the same. "
            "Export To Excel View Details Download Form Download Receipt Download JSON Filter "
            "6 Filings till date A.Y. 2026-27 Filing Type Original Processed with no demand/refund Aug 1, 2026 "
            "Successfully e-verified Jul 30, 2026 Pending for e- verification ITR : ITR-4 "
            "Acknowledgement No : 827916720300726 Filed By : self Filing Date : Jul 30, 2026 Filing Section : 139(1) "
            "Download Intimation Order Dated Aug 1, 2026"
        )
        wasil_card = extract_view_filed_returns_card(wasil_text)
        self.assertIsNotNone(wasil_card)
        self.assertEqual(wasil_card["ay"], "AY 2026-27")
        self.assertEqual(wasil_card["ack"], "827916720300726")
        self.assertEqual(wasil_card["form"], "ITR-4")
        self.assertEqual(wasil_card["status"], "Submitted (e-Verified)")


if __name__ == "__main__":
    unittest.main()
