"""
tests/test_vsdc_name.py — Unit Tests for VSDC Visual Name Parsing
"""

import unittest
from core.vsdc.vsdc_name_parser import (
    sanitize_visual_name,
    is_valid_name,
    parse_human_name,
    extract_name_from_ocr_lines,
    extract_composite_form_name,
    caps_run_name_candidates,
    extract_spacy_person_names,
    get_spacy_nlp,
)


class TestVSDCNameParser(unittest.TestCase):

    def test_material_icon_ligature_stripping(self):
        # Header user badge often has 'account_circle' and 'expand_more' directly attached
        raw = "account_circle AMAN ASSOCIATES expand_more"
        cleaned = sanitize_visual_name(raw)
        self.assertEqual(cleaned, "AMAN ASSOCIATES")

        # Portal role tags stripped
        raw_role = "JOHN DOE Individual"
        self.assertEqual(sanitize_visual_name(raw_role), "JOHN DOE")

    def test_name_validation(self):
        self.assertTrue(is_valid_name("AMAN ASSOCIATES"))
        self.assertTrue(is_valid_name("INDRAJIT CHATTERJEE"))
        self.assertTrue(is_valid_name("K. VENKATESHWARLU"))

        # Rejects noise words
        self.assertFalse(is_valid_name("DASHBOARD"))
        self.assertFalse(is_valid_name("LOGOUT"))
        self.assertFalse(is_valid_name("WELCOME"))
        self.assertFalse(is_valid_name("SERA ASSIST"))
        self.assertFalse(is_valid_name("SERA"))
        self.assertFalse(is_valid_name("SCC"))
        self.assertFalse(is_valid_name("12345"))
        self.assertFalse(is_valid_name("XZP")) # no vowel

    def test_human_name_parsing(self):
        parsed = parse_human_name("MR AMIT KUMAR SHARMA")
        self.assertEqual(parsed["first_name"], "AMIT")
        self.assertEqual(parsed["middle_name"], "KUMAR")
        self.assertEqual(parsed["last_name"], "SHARMA")
        self.assertEqual(parsed["title"], "MR")

    def test_extract_name_from_ocr_lines(self):
        lines = [
            "e-Filing Home Page",
            "Taxpayer Name: AMAN ASSOCIATES",
            "PAN: AHJPR0846B",
            "Status: Active"
        ]
        name = extract_name_from_ocr_lines(lines)
        self.assertEqual(name, "AMAN ASSOCIATES")

        # Welcome banner test
        lines_welcome = [
            "Welcome Back, DHANANJAY JOSHI",
            "Last Login: 12-Sep-2026"
        ]
        name_w = extract_name_from_ocr_lines(lines_welcome)
        self.assertEqual(name_w, "DHANANJAY JOSHI")

        # Standalone header badge test from live portal navbar
        lines_header = [
            "e-FiIing Income Tax Departnmt, Government Of India",
            "Authorised Partners",
            "Services",
            "Pending Actions",
            "Grievances",
            "Help",
            "WASIL AMAN MANDAL",
            "Individual"
        ]
        name_h = extract_name_from_ocr_lines(lines_header)
        self.assertEqual(name_h, "WASIL AMAN MANDAL")

        # Live portal navbar test with profile dropdown button (e.g. AMEJUDDIN SEKH v Individual)
        lines_live = [
            "e-FiIing",
            "Income Tax Department, Govemment Of India",
            "Services @ English v Call Us v Pending Actions AMEJUDDIN SEKH v Individual",
            "e-File Authorised Partners AIS Grievances Help",
            "Dashboard > e-flle > Income Tax Return > View Filed Returns",
        ]
        name_live = extract_name_from_ocr_lines(lines_live)
        self.assertEqual(name_live, "AMEJUDDIN SEKH")

        # Noise word rejection test: E FIIING must never be extracted as a person name
        lines_noise = [
            "e-FiIing",
            "Call Us",
            "Pending Actions",
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines_noise))

        # Rejects statutory portal disclaimers and instructions
        lines_disclaimer = [
            "ITR-1 is NOT FOR AN INDIVIDUAL WHO IS EITHER a Director in a company",
            "or has invested in unlisted equity shares"
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines_disclaimer))

    def test_caps_run_name_candidates(self):
        # Raw text dump containing taxpayer name surrounded by portal headers and buttons
        raw_dump = (
            "INCOME TAX DEPARTMENT SERVICES PENDING ACTIONS AMEJUDDIN SEKH "
            "INDIVIDUAL VIEW FILED RETURNS DOWNLOAD RECEIPT AY 2025-26"
        )
        cands = caps_run_name_candidates(raw_dump)
        self.assertIn("AMEJUDDIN SEKH", cands)
        # Verify portal noise runs are not included as names
        self.assertNotIn("INCOME TAX", cands)
        self.assertNotIn("VIEW FILED RETURNS", cands)
        self.assertNotIn("DOWNLOAD RECEIPT", cands)

    def test_spacy_person_name_extraction(self):
        if not get_spacy_nlp():
            self.skipTest("spaCy / en_core_web_sm not loaded")
        # Mixed-case document / notice text
        doc_text = "Income Tax intimation for Indrajit Chatterjee issued under section 143(1)."
        names = extract_spacy_person_names(doc_text)
        self.assertIn("INDRAJIT CHATTERJEE", names)

    def test_composite_name_extraction(self):
        # Case 1: Separate fields with colons
        lines_colon = [
            "Part A General - Personal Information",
            "First Name : MD SAYID",
            "Middle Name :",
            "Last Name : MOLLA",
            "PAN : GZEPM6417A"
        ]
        self.assertEqual(extract_composite_form_name(lines_colon), "MD SAYID MOLLA")
        self.assertEqual(extract_name_from_ocr_lines(lines_colon), "MD SAYID MOLLA")

        # Case 2: Alternating label and value lines
        lines_alt = [
            "Personal Information",
            "First Name",
            "WASIL",
            "Middle Name",
            "AMAN",
            "Last Name",
            "MANDAL",
            "PAN",
            "GZEPM6367M"
        ]
        self.assertEqual(extract_composite_form_name(lines_alt), "WASIL AMAN MANDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_alt), "WASIL AMAN MANDAL")

        # Case 3: Single-line multi-column layout
        lines_single = [
            "Profile Details",
            "First Name: MD SAYID   Last Name: MOLLA",
            "Status: Active"
        ]
        self.assertEqual(extract_composite_form_name(lines_single), "MD SAYID MOLLA")
        self.assertEqual(extract_name_from_ocr_lines(lines_single), "MD SAYID MOLLA")

        # Case 4: Surname with colon
        lines_surname = [
            "First Name : MOHAMMED",
            "Surname : GAZI"
        ]
        self.assertEqual(extract_composite_form_name(lines_surname), "MOHAMMED GAZI")
        self.assertEqual(extract_name_from_ocr_lines(lines_surname), "MOHAMMED GAZI")

        # Case 5: Horizontal table headers followed by data row
        lines_table = [
            "First Name    Middle Name    Last Name",
            "WASIL         AMAN           MANDAL"
        ]
        self.assertEqual(extract_composite_form_name(lines_table), "WASIL AMAN MANDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_table), "WASIL AMAN MANDAL")

        # Case 6: Dropdown adjacent line
        lines_dropdown_adj = [
            "WASIL AMAN MANDAL",
            "Individual"
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines_dropdown_adj), "WASIL AMAN MANDAL")

        # Case 7: Mandatory fields instruction line must NEVER be extracted as name
        lines_mandatory = [
            "* Indicates mandatory fields",
            "Please fill all details"
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines_mandatory))
        self.assertFalse(is_valid_name("INDICATES MANDATORY FIELDS"))

    def test_assembler_name_subset_downgrade_protection(self):
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        assembler = VisualSessionAssembler()
        assembler.update_identity(name="WASIL AMAN MANDAL")
        self.assertEqual(assembler.client_name, "WASIL AMAN MANDAL")

        # Partial fragment should NOT overwrite authoritative 3-word name
        assembler.update_identity(name="WASIL MANDAL")
        self.assertEqual(assembler.client_name, "WASIL AMAN MANDAL")

        # Truncated prefix words (e.g. WASIL AMAN MAND.) should NOT overwrite authoritative name
        assembler.update_identity(name="WASIL AMAN MAND")
        self.assertEqual(assembler.client_name, "WASIL AMAN MANDAL")

    def test_personal_info_page_table_extraction(self):
        # Real OCR lines from Part A General - Personal Information screen
        lines = [
            "e-Filing Anywhere Anytime",
            "WASIL AMAN MAND...",
            "Individual",
            "Dashboard > e-file > Income Tax Return > Return Summary > Part A General - Personal Information",
            "Part A General - Personal Information",
            "Profile",
            "First Name Middle Name Last Name",
            "WASIL AMAN MANDAL",
            "PAN Date of Birth / Formation Aadhaar Number",
            "GZEPM6367M"
        ]
        extracted = extract_name_from_ocr_lines(lines)
        self.assertEqual(extracted, "WASIL AMAN MANDAL")

    def test_profile_page_full_name_as_per_pan(self):
        # Real OCR lines from My Profile screen
        lines = [
            "e-Filing Anywhere Anytime",
            "WASIL AMAN MAND...",
            "Individual",
            "My Profile",
            "Personal Details",
            "Full Name as per PAN",
            "WASIL AMAN MANDAL",
            "PAN",
            "GZEPM6367M"
        ]
        extracted = extract_name_from_ocr_lines(lines)
        self.assertEqual(extracted, "WASIL AMAN MANDAL")

    def test_stacked_composite_headers_and_values(self):
        lines = [
            "First Name",
            "Middle Name",
            "Last Name",
            "WASIL",
            "AMAN",
            "MANDAL"
        ]
        extracted = extract_composite_form_name(lines)
        self.assertEqual(extracted, "WASIL AMAN MANDAL")


if __name__ == "__main__":
    unittest.main()
