"""
sca_protocol.py - Sera Clipboard Assist, protocol v2 (2026-09-22)
================================================================
v1 pushed every one of a client's portal passwords to every browser the moment a UID was copied
(and the native host logged them). v2 is pull-based:

  1. Copy a client UID  -> desktop sends an ARM: who the client is, which portals, which UIDs
     trigger it, when it expires. NO passwords.
  2. The UID is pasted/typed on a login page -> the extension checks the UID and the page's
     host against the arm and asks the desktop for that ONE portal's password
     (SCA_PASSWORD_REQUEST).
  3. The desktop checks again (arm live, uses left, host belongs to that portal), reads the
     password from the vault at that moment and answers only the host that asked
     (SCA_PASSWORD_GRANT / SCA_PASSWORD_DENIED).
  4. The extension fills the field in the frame where the UID was entered and reports the
     outcome (SCA_FILL_RESULT).
"""

import re
import secrets
import time
import unicodedata
from typing import Any, Dict, Optional
from urllib.parse import urlparse

SCA_PROTOCOL_VERSION = 2

# Desktop -> extension
MSG_SCA_ARM_REQUEST = "SCA_ARM_REQUEST"
MSG_SCA_DISARM_REQUEST = "SCA_DISARM_REQUEST"
MSG_SCA_STATE_REQUEST = "SCA_STATE_REQUEST"
MSG_SCA_PING = "SCA_PING"
MSG_SCA_PASSWORD_GRANT = "SCA_PASSWORD_GRANT"
MSG_SCA_PASSWORD_DENIED = "SCA_PASSWORD_DENIED"

# Extension -> desktop
MSG_SCA_ACK = "SCA_ACK"
MSG_SCA_STATE = "SCA_STATE"
MSG_SCA_PASSWORD_REQUEST = "SCA_PASSWORD_REQUEST"
MSG_SCA_FILL_RESULT = "SCA_FILL_RESULT"
MSG_SCA_ERROR = "SCA_ERROR"

# Canonical states
STATE_IDLE = "IDLE"
STATE_ARMED = "ARMED"
STATE_MATCHED = "MATCHED"
STATE_FILLED = "FILLED"
STATE_CONSUMED = "CONSUMED"
STATE_EXPIRED = "EXPIRED"
STATE_REJECTED = "REJECTED"
STATE_FAILED = "FAILED"

ARM_TTL_MS = 5 * 60 * 1000   # the arm holds no secret, so it can outlive a slow portal login

RE_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
RE_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
RE_ID_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9._@:/-]{2,79}$")


def generate_id(prefix: str = "cmd") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def normalize_uid(raw_uid: Any) -> str:
    """NFKC, trim, collapse whitespace, drop control characters, upper-case. The extension's
    window.normalizeUid (sca_adapters.js) must give the same result."""
    if raw_uid is None:
        return ""
    uid = unicodedata.normalize("NFKC", str(raw_uid)).strip()
    uid = re.sub(r"\s+", " ", uid)
    uid = "".join(ch for ch in uid if ord(ch) >= 32)
    return uid.upper()


def looks_like_uid(value: str) -> bool:
    """A PAN, a GSTIN or a compact login id (no spaces). Anything else never triggers SCA."""
    v = normalize_uid(value)
    return bool(RE_PAN.fullmatch(v) or RE_GSTIN.fullmatch(v) or RE_ID_TOKEN.fullmatch(v))


def service_host(url: str) -> str:
    try:
        raw = url if "://" in url else f"https://{url}"
        return (urlparse(raw).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def host_matches(page_host: str, login_url: str) -> bool:
    """The page belongs to the portal whose login page is login_url: the same host, or one is
    a sub-domain of the other (eportal.incometax.gov.in / incometax.gov.in). Label-by-label,
    so services.gst.gov.in.evil.com does NOT match services.gst.gov.in. Both need at least
    three labels, so a bare "gov.in" matches nothing."""
    page = (page_host or "").lower().rstrip(".")
    svc = service_host(login_url)
    if not page or not svc or page.count(".") < 2 or svc.count(".") < 2:
        return False
    return page == svc or page.endswith("." + svc) or svc.endswith("." + page)


def build_arm_request(
    client_id: int,
    client_token: str,
    matched_uid: str,
    candidate_uids: list,
    services: list,
    business_name: str = "",
    owner_name: str = "",
    ttl_ms: int = ARM_TTL_MS,
    sca_mode: str = "autofill",
    max_uses: int = 1,
) -> Dict[str, Any]:
    """An SCA_ARM_REQUEST. `services` must not contain passwords - build_arm_service() makes
    the entries; this refuses anything with a password field as a last line of defence."""
    for s in services:
        if "password" in s:
            raise ValueError("SCA arms must not carry passwords (protocol v2)")
    now = int(time.time() * 1000)
    arm = {
        "schema": 2,
        "arm_id": generate_id("arm"),
        "client_id": client_id,
        "client_id_token": client_token,
        "matched_uid": matched_uid,
        "candidate_uids": candidate_uids,
        "services": services,
        "business_name": business_name,
        "owner_name": owner_name,
        "sca_mode": sca_mode,
        "max_uses": max_uses,
        "created_at": now,
        "expires_at": now + ttl_ms,
        "state": STATE_ARMED,
    }
    return {
        "type": MSG_SCA_ARM_REQUEST,
        "protocol_version": SCA_PROTOCOL_VERSION,
        "command_id": generate_id("cmd"),
        "sent_at": now,
        "arm": arm,
    }


def build_arm_service(svc: dict, has_password: bool, blocked: str = "",
                      needs_confirm: bool = False) -> Optional[Dict[str, Any]]:
    """What the extension needs to know about one portal - never its password. `blocked` says
    why there is no usable password ("scc_unverified", "no_password"), so the extension can
    report it instead of failing silently. `needs_confirm`: the password is only given for a
    request the user confirmed on the page (an Income Tax password SCC has not verified)."""
    link = (svc.get("login_page_link") or "").strip()
    if not link:
        return None
    return {
        "service_id": svc.get("id"),
        "name": svc.get("name", "Portal"),
        "url": link,
        "host": service_host(link),
        "password_selector": (svc.get("password_selector") or "").strip(),
        "has_password": bool(has_password),
        "blocked": "" if has_password else (blocked or "no_password"),
        "needs_confirm": bool(has_password and needs_confirm),
    }
