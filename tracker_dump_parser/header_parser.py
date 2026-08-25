"""
Stage B: Header Parser
Extracts fixed-width and key-value header metadata preceding the JSON payload block.
"""

import re
from typing import Dict, Any, Tuple, Optional

HEADER_FIELD_RE = re.compile(r"^([A-Za-z /]+?)\s*:\s*(.*)$")
ENTRY_NUM_RE = re.compile(r"(?:CAPTURE DUMP )?ENTRY #(\d+)", re.IGNORECASE)
CLIENT_ID_FIELD_RE = re.compile(r"^(?P<id>\d+|null)?\s*(?:\(//(?P<pan>[A-Z0-9]+)\))?$", re.IGNORECASE)


def parse_client_id_field(raw_val: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Parses Client ID header formats:
    - "494" -> (494, None)
    - "494 (//MOKPA1000A)" -> (494, "MOKPA1000A")
    - "null (//MOKPA1000A)" -> (None, "MOKPA1000A")
    - "(//MOKPA1000A)" -> (None, "MOKPA1000A")
    """
    if not raw_val:
        return None, None
        
    s = raw_val.strip()
    m = CLIENT_ID_FIELD_RE.match(s)
    if not m:
        # Fallback regex for embedded PAN
        pan_m = re.search(r"([A-Z]{5}[0-9]{4}[A-Z])", s, re.IGNORECASE)
        pan = pan_m.group(1).upper() if pan_m else None
        num_m = re.search(r"^\d+", s)
        cid = int(num_m.group(0)) if num_m else None
        return cid, pan

    id_part = m.group("id")
    pan_part = m.group("pan")

    cid = None
    if id_part and id_part.lower() != "null" and id_part.isdigit():
        cid = int(id_part)

    pan = pan_part.upper() if pan_part else None
    return cid, pan


def parse_header(chunk: str) -> Dict[str, Any]:
    """
    Parses the header section of an entry chunk up to the 'RAW JSON PAYLOAD:' marker.
    """
    header_part, _, _ = chunk.partition("RAW JSON PAYLOAD:")
    
    fields: Dict[str, Any] = {
        "entry_num": None,
        "timestamp": None,
        "portal": None,
        "capture_method": "SAD_API_Interceptor",
        "status": "submitted",
        "arn_ack_no": None,
        "period_label": None,
        "client_id_raw": None,
        "header_client_id": None,
        "header_pan": None,
        "captured_by": None,
        "raw_header_text": header_part.strip()
    }

    # Extract entry number
    num_m = ENTRY_NUM_RE.search(header_part)
    if num_m:
        fields["entry_num"] = int(num_m.group(1))

    for line in header_part.splitlines():
        line_clean = line.strip()
        m = HEADER_FIELD_RE.match(line_clean)
        if not m:
            continue
            
        k = m.group(1).strip().lower()
        v = m.group(2).strip()

        if k == "timestamp":
            fields["timestamp"] = v
        elif k == "portal":
            fields["portal"] = v
        elif k == "capture method":
            fields["capture_method"] = v
        elif k == "status":
            fields["status"] = v
        elif k in ("arn / ack no", "arn", "ack no"):
            fields["arn_ack_no"] = v if v != "null" else None
        elif k == "period label":
            fields["period_label"] = v if v else None
        elif k == "client id":
            fields["client_id_raw"] = v
            cid, pan = parse_client_id_field(v)
            fields["header_client_id"] = cid
            fields["header_pan"] = pan
        elif k == "captured by":
            fields["captured_by"] = v

    return fields
