"""
Stage C: JSON Parser & Quarantine
Extracts and parses JSON bodies, routing malformed chunks to quarantine records.
"""

import json
from typing import Dict, Any, Tuple, Optional


def parse_json_body(chunk: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Extracts and parses JSON payload following the 'RAW JSON PAYLOAD:' marker.
    Returns: (parsed_json_dict, error_message_if_failed)
    """
    marker = "RAW JSON PAYLOAD:"
    _, has_marker, json_part = chunk.partition(marker)
    
    if not has_marker:
        return None, "Missing 'RAW JSON PAYLOAD:' marker in entry chunk"

    json_str = json_part.strip()
    # Strip any trailing separator rules if present
    if json_str.endswith("="):
        lines = json_str.splitlines()
        while lines and lines[-1].startswith("="):
            lines.pop()
        json_str = "\n".join(lines).strip()

    if not json_str:
        return None, "Empty JSON payload string"

    # Attempt direct parse
    try:
        data = json.loads(json_str)
        if isinstance(data, (dict, list)):
            return data, None
        return {"data": data}, None
    except json.JSONDecodeError as e:
        # Fallback: find outermost braces in case of trailing noise
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                candidate = json_str[start : end + 1]
                data = json.loads(candidate)
                return data, None
            except json.JSONDecodeError:
                pass
                
        # Also try array braces
        start_arr = json_str.find("[")
        end_arr = json_str.rfind("]")
        if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
            try:
                candidate = json_str[start_arr : end_arr + 1]
                data = json.loads(candidate)
                return {"items": data}, None
            except json.JSONDecodeError:
                pass

        return None, f"JSON parse error: {str(e)}"
