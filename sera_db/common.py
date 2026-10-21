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


def is_filed_dataset(status, arn) -> bool:
    """A return has actually been filed: it carries a real ARN and is at least submitted.

    Only such a dataset may establish original_author. An in-progress capture (draft, form
    selected, not submitted) has no ARN to preserve, so the PC that later files it is the author."""
    if not arn or str(arn).strip().upper() in ("N/A", "NA", "", "UNKNOWN"):
        return False
    from core.vsdc.vsdc_assembler import get_status_rank
    return get_status_rank(status) >= 2


def stamp_payload_original_author(raw_payload_json, author: str):
    """Returns raw_payload_json with original_author set at the top level and inside raw_payload.

    Used by the write and merge paths to keep the first live filing's author: once a row for a
    dataset carries an author, that author is re-applied to any later capture of the same dataset
    (another PC's live re-capture, a peer-sync row, or a duplicate being collapsed). Returns the
    input unchanged when no author is given or where it cannot be parsed. Keeps the input's type
    (JSON string in, JSON string out; dict in, dict out)."""
    author = str(author or "").strip()
    if not author:
        return raw_payload_json
    try:
        p = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
    except (TypeError, ValueError):
        return raw_payload_json
    if not isinstance(p, dict):
        return raw_payload_json
    p["original_author"] = author
    raw = p.get("raw_payload")
    if isinstance(raw, dict):
        raw["original_author"] = author
    return json.dumps(p, ensure_ascii=False) if isinstance(raw_payload_json, str) else p


class DatabaseError(Exception):
    pass
