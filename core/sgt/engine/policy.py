"""
core/sgt/engine/policy.py - the engine's fixed numbers and rules
==================================================================
Timeouts, what a read of each source may change, the link keywords that end a session, and the
two small page tests (is_blind, frame_hash). No state; imported by every part of the engine.
"""

import os
import re
from pathlib import Path
from typing import Any, List, Optional

from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME

# SGT's folder (logs, crash snapshot, spec stats). The folder and the variable keep the names they
# had when SGT first ran as a shadow engine, so every office PC keeps finding its files.
DATA_DIR_ENV = "SGT_SHADOW_DIR"
DATA_DIR_NAME = "sgt_shadow"
LOG_PREFIX = "sgt_shadow_"          # sgt_shadow_YYYY-MM-DD.jsonl
MODE_LIVE = "live"                  # the only mode; still written on each row ("mode": "live")
# The HUD pill colours the tag (ui/components/vsdc_hud_pill.py); the tracker colours and can hide
# rows whose capture method starts "SGT".
HUD_TAG = "SGT (Live)"
CAPTURE_METHOD = "SGT_live"
SESSION_QUIET_SEC = 600.0       # a session with content touched this recently holds back a mandatory update
IDLE_END_SEC = 4 * 3600        # owner D13: one client survives a whole workday of pauses
IDLE_REASON = "idle 4 hr"
CLIENT_LINK_SPEC = "client_db:user_id"       # the "spec" of a PAN / GSTIN taken from the saved client that owns the portal user ID
IDENTITY_PROBLEM = "form and period are not known yet"      # why an ack-only row waits (see _queue)
RESUME_WINDOW_SEC = 12 * 3600   # a parked client (also on disk, sessions_state.json) can be resumed this long
# What a read may change, by where its text came from (SGT never-miss blueprint 5.2 with R6). OCR
# is allowed on every portal but only ever ADDS: it never sets the client or any profile field,
# fills a piece of the dataset in progress only where none exists (a canvas read loses label
# markers such as "Period •", which once cleared values UIA had just set: GST, 2026-10-06), and
# creates a row or raises a status only together with that dataset's own ARN/ack.
SOURCE_POLICY = {
    "uia": {"profile": "all", "pieces": "set_change_clear", "datasets": "merge_follow", "identity_check": True},
    "ocr": {"profile": "none", "pieces": "fill_empty_only", "datasets": "merge_add_only", "identity_check": False},
}
BLIND_RETRIES = 2               # a page that read fine before is read again this often before OCR
BLIND_RETRY = "blind-retry"     # absorb()'s answer: the capture loop asks for the page again
RESULT_MAX_AGE_SEC = 60.0       # as core/capture/reader: an older result is dropped, never absorbed
OWNERS_KEPT = 500               # requests remembered (request_made) at most
CLOSED_RING = 20                # ended sessions a late result may still land in...
CLOSED_KEEP_SEC = 300.0         # ...for this long
# Only these ends let a late result in (R1): the next page in that tab is still the same login.
# Never a logout, a login page or another client's PAN/GSTIN - the read may show the next client.
ABSORB_AFTER_END = ("idle", "window closed")
REREAD_AFTER_SEC = 15.0
MIN_UIA_LINES = 3               # fewer than this and the page is treated as blind
MISSES_TO_CLEAR = 2             # reads in a row a piece must be missing from its page to be cleared
MISSING_CLEAR_SEC = 5.0         # ...and for at least this long
TIMELINE_CAP = 200
CURRENT_RECORD = "current_dataset"  # the record name a dataset built across pages is written under
SDIS_CARRIER_FORM = "SDIS info"     # the form in a carrier row's key (blueprint S.4); never a dataset
# How the pill names the client datapoints it has just captured.
PROFILE_LABELS = {"name": "name", "first_name": "first name", "middle_name": "middle name",
                  "last_name": "last name", "pan": "PAN", "gstin": "GSTIN", "dob": "date of birth",
                  "email": "email", "phone": "phone", "tan": "TAN", "address": "address",
                  "aggregate_turnover": "aggregate turnover"}
# Fields a dataset may name that say WHOSE it is: checked against the session's client, never
# stored as dataset fields.
CLIENT_KEYS = ("pan", "gstin")

# Any PAN printed on a page (upper-case, whole word).
PAN_TOKEN = re.compile(r"(?<![A-Z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Z0-9])")

# Flutter Web's accessibility placeholder: the whole page as UIA sees it until clicked.
BLIND_PLACEHOLDER = re.compile(r"^\s*enable\s+accessibility\s*$", re.IGNORECASE)
# The keyword must be a whole segment of the link ("#/login", "/services/logout"), so a page
# such as ".../myProfile/aadhaarOtpLogin" does not end the session.
LOGOUT_LINK = re.compile(r"(?:^|[/#=])(?:log-?out|log-?off|sign-?out)(?=$|[/?#.&])", re.IGNORECASE)
LOGIN_LINK = re.compile(r"(?:^|[/#=])(?:log-?in|sign-?in)(?=$|[/?#.&])", re.IGNORECASE)
EV_IDLE = {"tab": None, "source": "", "late": False, "lag": 0.0}   # evidence context outside a page read


def sgt_data_dir() -> Path:
    env = os.environ.get(DATA_DIR_ENV)
    return Path(env) if env else Path.home() / SERA_DATA_DIR_NAME / DATA_DIR_NAME


def is_blind(lines: List[str]) -> bool:
    real = [ln for ln in lines if ln and ln.strip() and not BLIND_PLACEHOLDER.match(ln)]
    return len(real) < MIN_UIA_LINES


def frame_hash(img: Any) -> Optional[int]:
    if img is None:
        return None
    try:
        return hash(img.convert("RGB").resize((32, 32)).tobytes())
    except Exception:
        return None
