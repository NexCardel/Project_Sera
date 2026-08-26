import time
import secrets
from typing import Dict, Any, Optional

# Protocol Versions
SCA_PROTOCOL_VERSION = 1

# Message Types (Desktop -> Extension)
MSG_SCA_ARM_REQUEST = "SCA_ARM_REQUEST"
MSG_SCA_DISARM_REQUEST = "SCA_DISARM_REQUEST"
MSG_SCA_STATE_REQUEST = "SCA_STATE_REQUEST"
MSG_SCA_PING = "SCA_PING"

# Message Types (Extension -> Desktop)
MSG_SCA_ACK = "SCA_ACK"
MSG_SCA_STATE = "SCA_STATE"
MSG_SCA_MATCHED = "SCA_MATCHED"
MSG_SCA_FILL_STARTED = "SCA_FILL_STARTED"
MSG_SCA_FILL_RESULT = "SCA_FILL_RESULT"
MSG_SCA_ERROR = "SCA_ERROR"

# Canonical States
STATE_IDLE = "IDLE"
STATE_ARMING = "ARMING"
STATE_ARMED = "ARMED"
STATE_MATCHED = "MATCHED"
STATE_WAITING_FOR_FIELDS = "WAITING_FOR_FIELDS"
STATE_FILLING = "FILLING"
STATE_CONSUMED = "CONSUMED"
STATE_EXPIRED = "EXPIRED"
STATE_REJECTED = "REJECTED"
STATE_FAILED = "FAILED"

def generate_id(prefix: str = "cmd") -> str:
    """Generate a random 16-hex-char string prefixed with the given prefix."""
    return f"{prefix}_{secrets.token_hex(8)}"

def build_arm_request(
    client_id: int,
    client_token: str,
    matched_uid: str,
    candidate_uids: list[str],
    services: list[dict],
    business_name: str = "",
    owner_name: str = "",
    ttl_ms: int = 45000,
    sca_mode: str = "autofill",
    max_uses: int = 1,
) -> Dict[str, Any]:
    """Builds a canonical SCA_ARM_REQUEST envelope and arm state."""
    arm_id = generate_id("arm")
    cmd_id = generate_id("cmd")
    
    # Store legacy compat fields directly on the arm state for Phase 2 bridging
    arm_state = {
        "schema": 1,
        "arm_id": arm_id,
        "client_id": client_id,
        "client_id_token": client_token,
        "matched_uid": matched_uid,
        "candidate_uids": candidate_uids,
        "services": services,
        "business_name": business_name,
        "owner_name": owner_name,
        "sca_mode": sca_mode,
        "max_uses": max_uses,
        "uses_remaining": max_uses,
        "created_at": int(time.time() * 1000),
        "expires_at": int(time.time() * 1000) + ttl_ms,
        "expiresAt": int(time.time() * 1000) + ttl_ms, # Legacy compat for Phase 1
        "state": STATE_ARMED,
        "last_operation_id": None,
        "last_error": None
    }
    
    return {
        "type": MSG_SCA_ARM_REQUEST,
        "protocol_version": SCA_PROTOCOL_VERSION,
        "command_id": cmd_id,
        "sent_at": int(time.time() * 1000),
        "arm": arm_state
    }

import unicodedata
import re

def normalize_uid(raw_uid: str) -> str:
    """
    Normalizes a UID string for strict matching according to SCA Phase 2 rules:
    1. Convert to string
    2. Normalize Unicode compatibility characters (NFKC)
    3. Trim leading/trailing whitespace
    4. Replace repeated whitespace with one space
    5. Convert to uppercase
    6. Reject empty values, control characters.
    Returns an empty string if invalid.
    """
    if raw_uid is None:
        return ""
    
    uid = str(raw_uid)
    uid = unicodedata.normalize('NFKC', uid)
    uid = uid.strip()
    uid = re.sub(r'\s+', ' ', uid)
    
    # Remove control characters (characters < 32 except space)
    uid = "".join(ch for ch in uid if ord(ch) >= 32)
    
    uid = uid.upper()
    return uid
