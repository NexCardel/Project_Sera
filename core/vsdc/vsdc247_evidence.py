"""
core/vsdc/vsdc247_evidence.py - VSDC247's evidence screenshot in Notes & Media
===============================================================================
When VSDC247 recognises a submission it already has the frame it read. This module copies
that frame into the app's media folder and points the capture's row (raw view) and its
container (grouped view) at it, so the Notes & Media tab shows the confirmation screen as
proof alongside the user's own notes and attachments.

A screenshot the user attached by hand is never replaced: only an empty slot, or one that
was filled by an earlier VSDC247 evidence frame, is updated.
"""

import os
import re
import shutil
from datetime import datetime
from typing import Optional

AUTO_PREFIX = "vsdc247_"
LOGIN_PREFIX = "vsdc247_login_"


def _media_dir(db) -> str:
    """The media folder that sits beside rawPayload.db (same rule as the tracker window)."""
    db_path = getattr(db, "raw_db_path", None) or getattr(db, "db_path", None)
    base_dir = os.path.dirname(os.path.abspath(db_path)) if db_path else os.path.abspath("data")
    media_dir = os.path.join(base_dir, "media")
    os.makedirs(media_dir, exist_ok=True)
    return media_dir


def _is_auto(path: str) -> bool:
    return os.path.basename(str(path or "")).lower().startswith(AUTO_PREFIX)


def _is_login_auto(path: str) -> bool:
    return os.path.basename(str(path or "")).lower().startswith(LOGIN_PREFIX)


def _copy_to_media(db, source_path: str, prefix: str, stem: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "", str(stem or "capture"))[:40] or "capture"
    filename = f"{prefix}{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
    destination = os.path.join(_media_dir(db), filename)
    shutil.copy2(source_path, destination)
    return f"media/{filename}"


def _container_key(client_id: Optional[int], unassigned_identity: Optional[str]) -> str:
    if client_id is not None:
        try:
            return f"CLI-{int(client_id):05d}"
        except (TypeError, ValueError):
            return ""
    return str(unassigned_identity or "").strip()


def attach_capture_evidence(db, source_path: str, dump_id=None, client_id=None,
                            unassigned_identity=None, arn: str = "") -> str:
    """
    Copies `source_path` into the media folder and stores the relative path ("media/...")
    on the capture row and its SRPF container. Returns the relative path, or "" when the
    source is missing or both slots already hold a user's own image. This fills the first
    screenshot slot only; the second (login evidence) is left untouched.
    """
    if not source_path or not os.path.isfile(source_path):
        return ""

    row_media = db.get_tracker_dump_media(dump_id) if dump_id else {"notes": "", "screenshot_path": ""}
    row_path = row_media.get("screenshot_path", "")
    row_take = bool(dump_id) and (not row_path or _is_auto(row_path))

    key = _container_key(client_id, unassigned_identity)
    container_media = db.get_srpf_container_media(key) if key else {"notes": "", "screenshot_path": ""}
    container_path = container_media.get("screenshot_path", "")
    container_take = bool(key) and (not container_path or _is_auto(container_path))

    if not (row_take or container_take):
        return ""

    rel_path = _copy_to_media(db, source_path, AUTO_PREFIX, arn or "capture")

    if row_take:
        db.save_tracker_dump_media(dump_id, row_media.get("notes", ""), rel_path)
    if container_take:
        db.save_srpf_container_media(key, container_media.get("notes", ""), rel_path,
                                     client_id=client_id)
    return rel_path


def attach_login_evidence(db, source_path: str, client_id=None, username: str = "") -> str:
    """
    Copies the GST login frame (username filled, password not read) into the media folder and
    stores it in the second screenshot slot of the client's container. Never touches the first
    slot, and never replaces a screenshot the user attached by hand. Returns the relative path,
    or "" when the source is missing or the second slot already holds user media.
    """
    if not source_path or not os.path.isfile(source_path):
        return ""

    key = _container_key(client_id, None)
    if not key:
        return ""
    container_media = db.get_srpf_container_media(key)
    current = container_media.get("screenshot_path_2", "")
    if current and not _is_login_auto(current):
        return ""

    rel_path = _copy_to_media(db, source_path, LOGIN_PREFIX, username or "login")
    db.save_srpf_container_media(
        key, container_media.get("notes", ""),
        container_media.get("screenshot_path", ""),
        client_id=client_id,
        screenshot_path_2=rel_path,
    )
    return rel_path


def attach_login_evidence_to_capture(db, source_path: str, dump_id=None, client_id=None,
                                     unassigned_identity=None, username: str = "") -> str:
    """
    Copies the GST login frame captured before an ARN into the media folder and stores it in the
    second screenshot slot of BOTH the capture row and its client container, so the ARN row shows
    the UID/login evidence beside the filing evidence.

    An empty slot or an earlier automatic login frame is replaced; a screenshot the user attached
    by hand is never touched (neither slot 1 nor slot 2). Returns the relative path, or "" when
    the source is missing or both second slots already hold user media.
    """
    if not source_path or not os.path.isfile(source_path):
        return ""

    row_media = db.get_tracker_dump_media(dump_id) if dump_id else {"notes": "", "screenshot_path": ""}
    row_current = row_media.get("screenshot_path_2", "")
    row_take = bool(dump_id) and (not row_current or _is_login_auto(row_current))

    key = _container_key(client_id, unassigned_identity)
    container_media = db.get_srpf_container_media(key) if key else {"notes": "", "screenshot_path": ""}
    container_current = container_media.get("screenshot_path_2", "")
    container_take = bool(key) and (not container_current or _is_login_auto(container_current))

    if not (row_take or container_take):
        return ""

    # Reuse the automatic login frame the client already holds instead of copying it again for
    # every ARN filed in the same login session.
    if container_take and _is_login_auto(container_current):
        rel_path = container_current
    else:
        rel_path = _copy_to_media(db, source_path, LOGIN_PREFIX, username or "login")

    if row_take:
        db.save_tracker_dump_media(
            dump_id, row_media.get("notes", ""), row_media.get("screenshot_path", ""),
            screenshot_path_2=rel_path,
        )
    if container_take:
        db.save_srpf_container_media(
            key, container_media.get("notes", ""), container_media.get("screenshot_path", ""),
            client_id=client_id, screenshot_path_2=rel_path,
        )
    return rel_path
