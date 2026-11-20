"""
core/sgt/engine/evidence_hooks.py - the evidence block on every row
===================================================================
Stamps of the reads that named the client and set each field, the session's trail, and the block
(core/sgt/evidence.py) attached to each tracker row. Never allowed to disturb capture.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from core.sgt import evidence as sgt_evidence
from core.vsdc.vsdc_alerts import device_name
from .policy import CLIENT_LINK_SPEC
from .session import Session, Slot


class EvidenceHooks:
    """Writes the sgt_ev evidence (part of SgtEngine)."""

    # ── Evidence (core/sgt/evidence.py): never allowed to disturb capture ─────
    def _trail(self, s: Session, code: str, tab: Optional[str] = None, detail: str = "") -> None:
        try:
            s.ev.note(self._stamp(self._clock()), code, tab, detail)
        except Exception:
            pass

    def _ev_born(self, s: Session, born: str, parent: Optional[Session], tab: Optional[str]) -> None:
        """`s` was just made out of `parent` (a clash, a tab of its own)."""
        s.ev.born = born
        s.ev.parent = parent.session_id if parent is not None else ""
        self._trail(s, born, tab)

    def _ev_stamp(self, url: str, source: str) -> Dict[str, Any]:
        try:
            c = self._ev_ctx
            return sgt_evidence.stamp(self._stamp(self._clock()), c["tab"], url, c["source"] or source,
                                      c["late"], c["lag"])
        except Exception:
            return {}

    def _ev_identity(self, s: Session, fld: str, value: str, spec: str, url: str, source: str) -> None:
        """The read that named the client: where, how, and how many other PANs that page printed."""
        try:
            st = self._ev_stamp(url, source)
            st["spec"] = spec
            if spec != CLIENT_LINK_SPEC:
                others = len(self._page_pans - {value if fld == "pan" else value[2:12]})
                if others:
                    st["others"] = others
            s.ev.ident[fld] = st
        except Exception:
            pass

    def _ev_fields(self, slot: Slot, values: Dict[str, str], keys: Iterable[str], pieces: Any,
                   url: str, source: str) -> None:
        """Stamps the dataset fields just set. A field the dataset in progress supplied keeps the
        stamp of the page that gave it (`pieces`: Draft.pieces), not this page's."""
        try:
            for k in keys:
                if k not in sgt_evidence.DATA_FIELDS or not values.get(k):
                    continue
                piece = (pieces or {}).get(k)
                if isinstance(piece, dict) and isinstance(piece.get("ev"), dict) and piece.get("value") == values[k]:
                    slot.ev[k] = dict(piece["ev"])
                else:
                    slot.ev[k] = self._ev_stamp(url, source)
        except Exception:
            pass

    def _attach_evidence(self, s: Session, slot: Any, payload: Dict[str, Any]) -> None:
        try:
            raw = payload.get("raw_payload")
            if not isinstance(raw, dict):
                return
            from version import APP_VERSION
            raw[sgt_evidence.EV_KEY] = sgt_evidence.build(
                s.ev, getattr(slot, "ev", None), sid=s.session_id, tabs=len(s.tabs),
                client_known=bool(self._client_ids(s)), version=APP_VERSION, pc=device_name(),
                at=datetime.now().isoformat(timespec="seconds"))
        except Exception as e:
            self._echo(f"[SGT] {self._tag} evidence not attached: {type(e).__name__}")
