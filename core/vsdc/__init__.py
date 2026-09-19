"""
core/vsdc — Visual Sera DOM Crosshair (VSDC)
============================================
Zero-browser-footprint visual scraping and session assembly subsystem for Project Sera.
"""

from .vsdc_crosshairs import (
    ALL_CROSSHAIRS,
    CROSSHAIRS_BY_ID,
    get_crosshair,
    ITR_CROSSHAIRS,
    GST_CROSSHAIRS,
    CrosshairDefinition,
    match_url_crosshair,
)
from .vsdc_regex import (
    repair_numeric_ack,
    extract_everify_transaction_id,
    find_ack_candidates,
    repair_gst_arn,
    extract_pan,
    extract_gstin,
    extract_assessment_year,
    extract_filing_type,
    classify_verification_status,
    extract_gst_filing_preference,
    extract_gst_form_table,
    extract_gst_fy,
    extract_gst_tax_period,
    extract_gst_status,
    format_gst_period_label,
    resolve_gst_form_type_from_url,
    extract_gst_filing_date,
)
from .vsdc_name_parser import (
    sanitize_visual_name,
    is_valid_name,
    is_better_taxpayer_name,
    parse_human_name,
    extract_name_from_ocr_lines,
    extract_composite_form_name,
    extract_proximity_labeled_names,
    extract_header_profile_caps_name,
    extract_gst_welcome_name,
)
from .vsdc_ocr import VSDCOcrEngine
from .vsdc_uia_text import is_available as uia_text_available, read_page_text as uia_read_page_text
from .vsdc_assembler import VisualSessionAssembler
from .vsdc_router import VSDCRouter
from .vsdc_worker import VSDCWorker
from .vsdc_session_logger import VSDCSessionLogger

__all__ = [
    "ALL_CROSSHAIRS",
    "CROSSHAIRS_BY_ID",
    "get_crosshair",
    "ITR_CROSSHAIRS",
    "GST_CROSSHAIRS",
    "CrosshairDefinition",
    "match_url_crosshair",
    "repair_numeric_ack",
    "extract_everify_transaction_id",
    "find_ack_candidates",
    "repair_gst_arn",
    "extract_pan",
    "extract_gstin",
    "extract_assessment_year",
    "extract_filing_type",
    "classify_verification_status",
    "extract_gst_filing_preference",
    "extract_gst_form_table",
    "extract_gst_fy",
    "extract_gst_tax_period",
    "extract_gst_status",
    "format_gst_period_label",
    "resolve_gst_form_type_from_url",
    "extract_gst_filing_date",
    "sanitize_visual_name",
    "is_valid_name",
    "is_better_taxpayer_name",
    "parse_human_name",
    "extract_name_from_ocr_lines",
    "extract_composite_form_name",
    "extract_proximity_labeled_names",
    "extract_header_profile_caps_name",
    "VSDCOcrEngine",
    "uia_text_available",
    "uia_read_page_text",
    "VisualSessionAssembler",
    "VSDCRouter",
    "VSDCWorker",
    "VSDCSessionLogger",
]
