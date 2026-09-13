"""
core/vsdc — Visual Sera DOM Crosshair (VSDC)
============================================
Zero-browser-footprint visual scraping and session assembly subsystem for Project Sera.
"""

from .vsdc_crosshairs import (
    ALL_CROSSHAIRS,
    ITR_CROSSHAIRS,
    GST_CROSSHAIRS,
    CrosshairDefinition,
    match_url_crosshair,
)
from .vsdc_regex import (
    repair_numeric_ack,
    repair_gst_arn,
    extract_pan,
    extract_gstin,
    extract_assessment_year,
    extract_filing_type,
    classify_verification_status,
    extract_gst_filing_preference,
)
from .vsdc_name_parser import (
    sanitize_visual_name,
    is_valid_name,
    is_better_taxpayer_name,
    parse_human_name,
    extract_name_from_ocr_lines,
    extract_composite_form_name,
    extract_gst_welcome_name,
)
from .vsdc_ocr import VSDCOcrEngine
from .vsdc_assembler import VisualSessionAssembler
from .vsdc_router import VSDCRouter
from .vsdc_worker import VSDCWorker

__all__ = [
    "ALL_CROSSHAIRS",
    "ITR_CROSSHAIRS",
    "GST_CROSSHAIRS",
    "CrosshairDefinition",
    "match_url_crosshair",
    "repair_numeric_ack",
    "repair_gst_arn",
    "extract_pan",
    "extract_gstin",
    "extract_assessment_year",
    "extract_filing_type",
    "classify_verification_status",
    "sanitize_visual_name",
    "is_valid_name",
    "is_better_taxpayer_name",
    "parse_human_name",
    "extract_name_from_ocr_lines",
    "extract_composite_form_name",
    "VSDCOcrEngine",
    "VisualSessionAssembler",
    "VSDCRouter",
    "VSDCWorker",
]
