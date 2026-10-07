"""
core/sgt/sgt_client_link.py - a portal user ID -> the saved client it belongs to
================================================================================
SGT's own page rules name the client from the portal's top header (PAN / GSTIN). Some pages the
session starts on do not show it, but SDIS has captured the portal's USER ID there. A user ID is
saved on the client row (the column a service's `userid_column_id` points at), so it names the
client without any page rule being bent.

Strict on purpose: no match, or more than one client holding the same user ID, links nothing. The
link only ever supplies the GSTIN / PAN the client row already holds.
"""

import re
from typing import Any, Dict, List, Optional

# portal -> words that identify its service by name (services are user-named rows)
SERVICE_HINTS = {"GST Portal": ("gst",)}

_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class ClientLink:
    """`ClientLink(db)(portal, user_id)` -> {"gstin", "pan", "client_id"} (empty strings when the
    client row lacks one), or None. Safe to call from SGT's thread."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def __call__(self, portal: str, user_id: str) -> Optional[Dict[str, Any]]:
        hints = SERVICE_HINTS.get(portal)
        user_id = str(user_id or "").strip()
        if not hints or not user_id:
            return None
        try:
            columns = {s.get("userid_column_id") for s in self._db.get_services()
                       if s.get("userid_column_id") and any(h in str(s.get("name") or "").lower() for h in hints)}
            owners: List[int] = []
            for col in columns:
                for cid in self._db.find_client_ids_by_column_value(col, user_id):
                    if cid not in owners:
                        owners.append(cid)
            if len(owners) != 1:
                return None                         # nobody, or ambiguous: never guess a client
            client = self._db.get_client(owners[0]) or {}
        except Exception:
            return None
        values = [str(v).strip().upper() for v in (client.get("values") or {}).values() if v]
        gstins = sorted({v for v in values if _GSTIN.match(v)})
        pans = sorted({v for v in values if _PAN.match(v)} | {g[2:12] for g in gstins})
        gstin = gstins[0] if len(gstins) == 1 else ""      # several registrations: the PAN is the safe identity
        pan = pans[0] if len(pans) == 1 else ""
        if not (gstin or pan):
            return None
        return {"gstin": gstin, "pan": pan, "client_id": owners[0]}
