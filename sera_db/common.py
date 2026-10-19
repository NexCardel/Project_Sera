"""
sera_db/common.py - names shared by database.py and its mixins.
"""

import json
import re

DB_FILENAME = "master.db"

SKELETON_NAME_REGEX = re.compile(
    r'^(?:'
    r'taxpayer|client|user|individual|indicates\s*mandatory\s*fields|mandatory\s*fields|'
    r'goods\s+and\s+services\s+tax|gst\s+common\s+portal|gst\s+portal|status|due\s*date|'
    r'fy|financial\s*year|tax\s*period|return\s*period|filing\s*period|legal\s*name|'
    r'trade\s*name|gstin|pan|na|none|-+|sera(?:\s*assist)?|scc|'
    r'(?:client|taxpayer|user|unregistered|unassigned)?\s*[:\-\#]?\s*(?:\(?\s*[A-Z]{5}[0-9]{4}[A-Z]\s*\)?|\d+|cli-\d+)|'
    r'pan\s*:\s*[A-Z]{5}[0-9]{4}[A-Z].*'
    r')[\s\-:*]*$',
    re.I
)


def is_sgt_capture(capture_method) -> bool:
    """A row SGT wrote (SGT_live / SGT_shadow / SGT_sdis_info). SGT names a row's client only from what
    its own session read; a row it sends without a PAN/GSTIN must never borrow one from another row."""
    return str(capture_method or "").startswith("SGT")


def payload_device_name(raw_payload_json) -> str:
    """The PC a capture came from (core/vsdc/vsdc_alerts.stamp_device_name puts it at the payload's top
    level and inside raw_payload), or "" when the payload does not say."""
    try:
        p = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
    except (TypeError, ValueError):
        return ""
    if not isinstance(p, dict):
        return ""
    raw = p.get("raw_payload") if isinstance(p.get("raw_payload"), dict) else {}
    return str(p.get("device_name") or raw.get("device_name") or "").strip()


def payload_original_author(raw_payload_json) -> str:
    """The PC that filed the return and captured the ARN live, or "" when not known."""
    try:
        p = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
    except (TypeError, ValueError):
        return ""
    if not isinstance(p, dict):
        return ""
    raw = p.get("raw_payload") if isinstance(p.get("raw_payload"), dict) else {}
    return str(p.get("original_author") or raw.get("original_author") or "").strip()


class DatabaseError(Exception):
    pass
