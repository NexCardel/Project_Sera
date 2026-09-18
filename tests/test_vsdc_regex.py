"""
tests/test_vsdc_regex.py — Unit Tests for VSDC Statutory Regex & Optical Self-Repair
"""

import unittest
from core.vsdc.vsdc_regex import (
    repair_numeric_ack,
    repair_gst_arn,
    extract_pan,
    extract_gstin,
    extract_dob,
    extract_assessment_year,
    extract_filing_type,
    classify_verification_status,
    extract_gst_form_table,
    extract_gst_filing_preference,
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

    def test_dob_extraction_label_formats(self):
        self.assertEqual(extract_dob("Date of Birth : 06-Aug-1971"), "06-Aug-1971")
        self.assertEqual(extract_dob("DOB: 21/09/2003"), "21/09/2003")
        self.assertEqual(extract_dob("Birth Date - 1971-08-06"), "1971-08-06")
        # Dotted separators normalized to hyphens, matching SDC's own normalization
        self.assertEqual(extract_dob("DOB: 06.08.1971"), "06-08-1971")

    def test_dob_extraction_label_and_value_on_separate_lines(self):
        # UIA/OCR lines join label and value as adjacent lines, not same line
        self.assertEqual(extract_dob("Date of Birth\n06-Aug-1971"), "06-Aug-1971")

    def test_dob_extraction_requires_a_label(self):
        # A bare, unlabeled date must NOT be mistaken for DOB (e.g. a filing date)
        self.assertIsNone(extract_dob("Filed on 06-Aug-1971 successfully"))

    def test_dob_extraction_no_match_returns_none(self):
        self.assertIsNone(extract_dob("Part A General - Personal Information"))
        self.assertIsNone(extract_dob(""))

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

    def test_view_filed_returns_status_ignores_older_filings(self):
        from core.vsdc.vsdc_regex import extract_view_filed_returns_card
        # Latest card is still pending e-verification; older cards further down the
        # same page are e-verified/processed and must not leak into its status.
        text = (
            "View Filed Returns\n"
            "The e-Filed Returns are available for download /view starting Assessment Year 2013-14.\n"
            "14 Filings till date\n"
            "A.Y. 2025-26\n"
            "Filing Type\nOriginal\n"
            "Pending for e-verification\nSep 15, 2025\n"
            "ITR Filed\nSep 15, 2025\n"
            "ITR : ITR-4\n"
            "Acknowledgement No : 598290000150925\n"
            "Filing Section : 139(1)\n"
            "A.Y. 2024-25\n"
            "Filing Type\nOriginal\n"
            "Processed with refund\nOct 10, 2024\n"
            "Successfully e-verified Sep 01, 2024\n"
            "ITR : ITR-1\n"
            "Acknowledgement No : 123456780000012\n"
        )
        card = extract_view_filed_returns_card(text)
        self.assertEqual(card["ack"], "598290000150925")
        self.assertEqual(card["form"], "ITR-4")
        self.assertEqual(card["ay"], "AY 2025-26")
        self.assertEqual(card["status"], "Submitted (Not e-Verified)")

    # Lines exactly as the live My Profile page exposes them through UI Automation
    # (captured with tools/vsdc_uia_probe.py): plain Text label/value pairs.
    _PROFILE_LINES = [
        "RAHUL MONDAL Individual", "My Profile", "Personal Details", "Profile",
        "Name", "RAHUL MONDAL", "Date of Birth", "28-Feb-1983", "PAN", "BEBPM9120B",
        "Gender", "Male", "Contact", "Mobile", "Primary (Self)", "+91", "9732684972",
        "Secondary (--)", "--", "Residential/ Office (Mobile)", "--",
        "Residential/Office (Landline)", "--", "Email", "Primary (Friend)",
        "rusinafashion@gmail.com", "Secondary (--)", "--", "Address",
    ]

    def test_profile_contact_extraction(self):
        from core.vsdc.vsdc_regex import extract_mobile, extract_email
        self.assertEqual(extract_mobile(self._PROFILE_LINES), "9732684972")
        self.assertEqual(extract_email(self._PROFILE_LINES), "rusinafashion@gmail.com")

    def test_contact_never_takes_secondary_or_landline(self):
        from core.vsdc.vsdc_regex import extract_mobile, extract_email
        # No primary value: a secondary number/email must not be promoted to primary.
        self.assertIsNone(extract_mobile(["Mobile", "Primary (--)", "--", "Secondary (Self)", "9876543210"]))
        self.assertIsNone(extract_email(["Email", "Primary (--)", "--", "Secondary (Self)", "a@b.in"]))
        self.assertIsNone(extract_mobile(["Residential/Office (Landline)", "03472123456"]))
        self.assertIsNone(extract_mobile([]))

    def test_contact_alternate_layouts(self):
        from core.vsdc.vsdc_regex import extract_mobile, extract_email
        # Personal Info / form-field layout (label, then the field value) and inline form.
        self.assertEqual(extract_mobile(["Mobile No", "Mobile No", "9732684972"]), "9732684972")
        self.assertEqual(extract_mobile(["Mobile Number: 98765 43210"]), "9876543210")
        self.assertEqual(extract_email(["Email Address : Foo.Bar@Gmail.com"]), "foo.bar@gmail.com")

    def test_profile_name_field_beats_truncated_header_pill(self):
        from core.vsdc.vsdc_name_parser import extract_profile_name_field, extract_name_from_ocr_lines
        self.assertEqual(extract_profile_name_field(self._PROFILE_LINES), "RAHUL MONDAL")
        lines = ["MOHAMMAD ABDUL KARIM... Individual", "Name", "MOHAMMAD ABDUL KARIM MOLLA"]
        # The generic tiers would settle for the shortened header pill...
        self.assertEqual(extract_name_from_ocr_lines(lines), "MOHAMMAD ABDUL KARIM")
        # ...the exact labeled field is the full name.
        self.assertEqual(extract_profile_name_field(lines), "MOHAMMAD ABDUL KARIM MOLLA")
        self.assertIsNone(extract_profile_name_field(["ABDUL KARIM Individual", "Date of Birth", "28-Feb-1983"]))

    def test_everify_stepper_is_not_a_verification(self):
        from core.vsdc.vsdc_regex import strip_everify_stepper, has_everify_success_evidence
        stepper = "Select The Return To Be Verified\nSelect Method For Return Verification\nReturn Successfully Verified\n"
        # The wizard's own step labels appear on EVERY step (incl. the picker and OTP pages)
        # and must never read as "verified" - the classifier alone gets this wrong.
        self.assertEqual(classify_verification_status(stepper), "Submitted (e-Verified)")
        self.assertFalse(has_everify_success_evidence(stepper))
        self.assertFalse(has_everify_success_evidence(stepper + "Aadhaar OTP\nEVC through Net Banking\nEnter the 6-digit OTP"))
        # Labels wrapped across lines (as OCR renders them) are stripped as well.
        self.assertFalse(has_everify_success_evidence("Return Successfully\nVerified"))
        self.assertNotIn("Verified", strip_everify_stepper(stepper))

    def test_everify_real_confirmation_wording_is_recognised(self):
        from core.vsdc.vsdc_regex import has_everify_success_evidence
        stepper = "Return Successfully Verified\n"
        for msg in (
            "Your return has been successfully e-Verified.",
            "You have successfully verified your return",
            "Return verified successfully",
            "e-Verification completed",
        ):
            self.assertTrue(has_everify_success_evidence(stepper + msg), msg)

    def test_extract_gst_fy_smart_regex(self):
        from core.vsdc.vsdc_regex import extract_gst_fy

        # Clean label with dash
        self.assertEqual(extract_gst_fy("FY - 2026-27"), "2026-27")
        # Colon separator
        self.assertEqual(extract_gst_fy("FY: 2026-27"), "2026-27")
        # Multiline break between label and year
        self.assertEqual(extract_gst_fy("FY -\n2026-27"), "2026-27")
        self.assertEqual(extract_gst_fy("Financial Year\n2026-27"), "2026-27")
        # Spaces around hyphen and en-dash
        self.assertEqual(extract_gst_fy("FY - 2026 - 27"), "2026-27")
        self.assertEqual(extract_gst_fy("FY: 2026–27"), "2026-27")
        # Standalone financial year
        self.assertEqual(extract_gst_fy("Return for 2025-26 portal view"), "2025-26")

    def test_extract_gst_tax_period_smart_regex(self):
        from core.vsdc.vsdc_regex import extract_gst_tax_period

        # Clean month with (Q)
        self.assertEqual(extract_gst_tax_period("Tax Period - June(Q)"), "June(Q)")
        # Space between month and (Q) normalized
        self.assertEqual(extract_gst_tax_period("Tax Period : June (Q)"), "June(Q)")
        # Multiline break between label and value
        self.assertEqual(extract_gst_tax_period("Tax Period -\nJune(Q)"), "June(Q)")
        self.assertEqual(extract_gst_tax_period("Return Period\nJuly"), "July")
        # Quarter ranges
        self.assertEqual(extract_gst_tax_period("Return Period - Apr-Jun"), "Apr-Jun")
        self.assertEqual(extract_gst_tax_period("Tax Period - Q1"), "Q1")
        # Standalone fallback when label is missing
        self.assertEqual(extract_gst_tax_period("Outward supplies for June(Q) filed"), "June(Q)")

    def test_extract_gst_status_smart_regex(self):
        from core.vsdc.vsdc_regex import extract_gst_status

        # Standard filed
        self.assertEqual(extract_gst_status("Status - Filed"), "Filed")
        self.assertEqual(extract_gst_status("Status : Filed"), "Filed")
        # Multiline separation
        self.assertEqual(extract_gst_status("Status -\nFiled"), "Filed")
        self.assertEqual(extract_gst_status("Status\nFiled"), "Filed")
        # Not Filed (must not be confused with Filed)
        self.assertEqual(extract_gst_status("Status - Not Filed"), "Not Filed")
        self.assertEqual(extract_gst_status("Status -\nNot Filed"), "Not Filed")
        # Submitted, Initiated, Draft, Pending
        self.assertEqual(extract_gst_status("Status - Submitted"), "Submitted")
        self.assertEqual(extract_gst_status("Status - Initiated"), "Initiated")
        self.assertEqual(extract_gst_status("Status - Draft"), "Initiated")
        self.assertEqual(extract_gst_status("Status - Pending"), "Pending")
        self.assertEqual(extract_gst_status("Status - Ready to File"), "Ready to File")
        # Column interleaved OCR text
        self.assertEqual(
            extract_gst_status("Trade Name - SPY JUNIOR Status - Filed Due Date - 13/07/2026"),
            "Filed"
        )
        self.assertEqual(
            extract_gst_status("Trade Name - SPY JUNIOR Status: Not Filed Due Date - 13/07/2026"),
            "Not Filed"
        )

    def test_format_gst_period_label(self):
        from core.vsdc.vsdc_regex import format_gst_period_label

        self.assertEqual(format_gst_period_label("June(Q)", "2026-27"), "June(Q) (FY 2026-27)")
        self.assertEqual(format_gst_period_label("June", "2026-27"), "June (FY 2026-27)")
        self.assertEqual(format_gst_period_label("June(Q)", None), "June(Q)")
        self.assertEqual(format_gst_period_label(None, "2026-27"), "FY 2026-27")
        self.assertEqual(format_gst_period_label("June(Q) (FY 2026-27)", "2026-27"), "June(Q) (FY 2026-27)")

    def test_extract_gst_form_table_multiline_and_optical_noise(self):
        from core.vsdc.vsdc_regex import extract_gst_form_table

        # Simulated OCR with newlines between labels and values
        multiline_ocr = (
            "Goods and Services Tax\n"
            "Dashboard > Returns > GSTR-1/IFF\n"
            "GSTR-1 - Details of outward supplies\n"
            "GSTIN -\n19AAAAA0000A1Z5\n"
            "Legal Name -\nFATIMA BIBI\n"
            "Trade Name -\nSPY JUNIOR\n"
            "FY -\n2026-27\n"
            "Tax Period -\nJune (Q)\n"
            "Status -\nNot Filed\n"
            "Due Date -\n13/07/2026\n"
        )
        table = extract_gst_form_table(multiline_ocr)
        self.assertEqual(table["gstin"], "19AAAAA0000A1Z5")
        self.assertEqual(table["pan"], "AAAAA0000A")
        self.assertEqual(table["legal_name"], "FATIMA BIBI")
        self.assertEqual(table["trade_name"], "SPY JUNIOR")
        self.assertEqual(table["fy"], "2026-27")
        self.assertEqual(table["tax_period"], "June(Q)")
        self.assertEqual(table["period_label"], "June(Q) (FY 2026-27)")
        self.assertEqual(table["status"], "Not Filed")
        self.assertEqual(table["due_date"], "13/07/2026")
        self.assertEqual(table["form_type"], "GSTR-1/IFF")

    def test_extract_gst_form_table_gstr3b_qrmp(self):
        raw = (
            "New Tab c -o Goods & Service Tax (GSD I Use return.gst.gov.in/returns/auth/gstr3b "
            "Goods and Services Tax Government of India, States and Union Territories Ask Gemini "
            "Skip to Main Content O A- a ARIF MOHAMMAD MOLLA v 19CJLPM0265MIZO News and Updates English "
            "O Dashboard Services • GST Law Downloads • Search Taxpayer • Help and Taxpayer Facilities "
            "Status - Filed Due Date - 24/07/2026 e-lnvoice Dashboard Returns GSTR-3BQ GSTR-3BQ - Quarterly Return "
            "GSTIN - 19CJLPM0265MIZO FY - 2026-27 Facilitation in filing GSTR-3BQ "
            "Legal Name - ARIF MOHAMMAD MOLLA Return Period - Apr-Jun "
            "O System has not generated summary of Table 3.1(d) and Table 4 of FORM GSTR-3B on the basis of your GSTR-2B "
            "as same is not generated for the current tax period. O System has not generated summary of table 5.1 interest "
            "of Form GSTR-3B basis your previous period Form GSTR-3B as the same is not filed or interest is not applicable. "
            "Click here for system generated summary status for GSTR-3B. BACK SAVE GSTR3B DOWNLOAD FILED GSTR-3B "
            "Help Manual SYSTEM GENERATED GSTR-3B"
        )
        url = "https://return.gst.gov.in/returns/auth/gstr3b"
        table = extract_gst_form_table(raw, url=url)
        self.assertEqual(table["gstin"], "19CJLPM0265MIZO")
        self.assertEqual(table["pan"], "CJLPM0265M")
        self.assertEqual(table["legal_name"], "ARIF MOHAMMAD MOLLA")
        self.assertIsNone(table["trade_name"])
        self.assertEqual(table["fy"], "2026-27")
        self.assertEqual(table["tax_period"], "Apr-Jun")
        self.assertEqual(table["period_label"], "Apr-Jun (FY 2026-27)")
        self.assertEqual(table["status"], "Filed")
        self.assertEqual(table["due_date"], "24/07/2026")
        self.assertEqual(table["form_type"], "GSTR-3B")
        self.assertEqual(extract_gst_filing_preference(raw), "Quarterly")


if __name__ == "__main__":
    unittest.main()
