"""
core/sgt/engine/rows.py - tracker rows
======================================
The outbox, the dataset rules a row must pass before it is written, the tracker key, and the payload
a row is written as (with SGT-I's enrichment and the VSDC247-V verification section).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.dataset_key import compute_dataset_key
from core.sgt.containers import Carrier, Instance
from core.sgt.specs.toolbox import SUBMIT_LEVELS
from core.vsdc.vsdc_alerts import device_name
from .policy import CAPTURE_METHOD, CLIENT_KEYS, IDENTITY_PROBLEM, MODE_LIVE
from .session import Session, Slot


class Rows:
    """Builds and queues the tracker rows (part of SgtEngine)."""

    def _attach_verifier(self, s: Session, slot: Any, payload: Dict[str, Any]) -> None:
        """Attaches the vsdc247-v verification section directly to the payload."""
        arn = payload.get("arn") or (getattr(slot, "arn", None) if slot is not None else None)
        if not arn and hasattr(slot, "values") and isinstance(slot.values, dict):
            arn = slot.values.get("arn")
        arn_clean = str(arn).strip().upper() if arn and str(arn).strip().upper() not in ("N/A", "NONE", "") else None
        verif = None
        if self._verifier is not None and arn_clean:
            try:
                if hasattr(self._verifier, "get_verification"):
                    verif = self._verifier.get_verification(arn_clean)
                elif hasattr(self._verifier, "ledger") and isinstance(self._verifier.ledger, dict):
                    entry = self._verifier.ledger.get(arn_clean)
                    if entry:
                        verif = {
                            "verified": True,
                            "status": "verified",
                            "arn": entry.get("arn") or arn_clean,
                            "form": entry.get("form") or payload.get("filing_type"),
                            "period": entry.get("period") or payload.get("period_label"),
                            "score": entry.get("score"),
                            "tier": entry.get("tier", "certain"),
                            "client": entry.get("client") or {},
                            "page_kind": entry.get("page_kind"),
                            "page_url": entry.get("page_url") or payload.get("page_url"),
                            "seen_at": entry.get("seen_at"),
                            "confirmed_by": entry.get("confirmed_by", "vsdc247_verifier"),
                            "device_name": entry.get("device_name"),

                        }
            except Exception:
                verif = None

        if verif is None:
            verif = {
                "verified": False,
                "status": "unverified",
                "arn": arn_clean or (arn or "N/A"),
            }

        payload["vsdc247-v"] = dict(verif)
        if "raw_payload" in payload and isinstance(payload["raw_payload"], dict):
            payload["raw_payload"]["vsdc247-v"] = dict(verif)
            payload["raw_payload"]["vsdc247"] = dict(verif)

    # ── Folding a page into the session ──────────────────────────────────────────
    # ── Tracker rows ─────────────────────────────────────────────────────────────
    def _queue(self, s: Session, slot: Slot) -> None:
        """Hands a dataset to the tracker - unless it breaks a dataset rule: then it is held,
        with the reason, and written the next time it is queued and passes."""
        if isinstance(slot, (Instance, Carrier)):
            # SDIS rows: the built-in dataset_rules are about SGT's own GST/ITR datasets.
            self._outbox[id(slot)] = (s, slot)
            self._outbox.move_to_end(id(slot))
            return
        problems = self.store.get().dataset_rules.problems(s.portal, slot.values, self._today())
        if not slot.waived and not (slot.values.get("form") and slot.values.get("period")):
            # Identity first: an ack or a message with no form and period of its own waits for them.
            problems.insert(0, IDENTITY_PROBLEM)
        if slot.claimed:
            client = self._client_pan(s)
            named = ", ".join(slot.claimed)
            if not client:
                problems.append(f"it was read where PAN {named} is printed; waiting until this session's client is known")
            elif client not in slot.claimed:
                problems.append(f"it was read where only PAN {named} is printed - not this session's client")
        if problems:
            self._outbox.pop(id(slot), None)
            if slot.held != problems:
                slot.held = problems
                self._event(s, "dataset", change="held - not written", values=dict(slot.values), problems=problems)
                self._echo(f"[SGT] {self._tag} dataset {self._label(slot.values)} HELD: {'; '.join(problems)}")
            return
        if slot.held:
            slot.held = None
            self._event(s, "dataset", change="released - now passes its checks", values=dict(slot.values))
        self._outbox[id(slot)] = (s, slot)
        self._outbox.move_to_end(id(slot))

    def _queue_all(self, s: Session, every: bool = False) -> None:
        """The client became known (or their name completed): every row of the session is
        rewritten under the client's key, replacing the rows written before it was known.
        every=True also queues rows never sent (crash recovery)."""
        for slot in s.slots:
            if every or slot.sent_key is not None:
                self._queue(s, slot)
        for inst in s.sdis.instances:
            if inst.keyed and (every or inst.sent_key is not None):
                self._queue(s, inst)
        carrier = s.sdis.carrier
        if carrier is not None and (every or carrier.sent_key is not None):
            self._queue(s, carrier)

    def pending(self) -> int:
        return len(self._outbox)

    def pop_dispatch(self) -> Optional[Dict[str, Any]]:
        """The next tracker row to save, in the pipeline's payload shape, or None."""
        while self._outbox:
            _, (s, slot) = self._outbox.popitem(last=False)
            try:
                payload = self._tracker_payload(s, slot)
                self._attach_evidence(s, slot, payload)
                return payload
            except Exception as e:                  # never let one bad row block the rest
                self._echo(f"[SGT] could not build a tracker row: {e}")
        return None

    def drain(self) -> List[Dict[str, Any]]:
        out = []
        while True:
            p = self.pop_dispatch()
            if p is None:
                return out
            out.append(p)

    def row_key(self, s: Session, values: Dict[str, str]) -> str:
        """The tracker key for a dataset (live_key)."""
        return self.live_key(s, values)

    @staticmethod
    def live_key(s: Session, values: Dict[str, str]) -> str:
        """
        The tracker's canonical key (core/dataset_key.py), identified the way the database
        identifies any row (GSTIN, else PAN), so a dataset another engine saved is the same row.
        Until the client is known the identifier is "SGT<session>" - it cannot collide with a
        real client's row, and is superseded once the client is known. A dataset known only by
        its ARN is keyed by it ("ARN_<n>" in the period place).
        """
        prof = Rows._client_ids(s)
        ident = prof.get("gstin") or prof.get("pan") or f"SGT{s.session_id}"
        form, period = values.get("form") or "", values.get("period") or ""
        if not (form and period):
            form, period = "", f"ARN {values.get('arn') or ''}"
        return compute_dataset_key(s.portal, ident, form, period, values.get("filing_type") or "")

    @staticmethod
    def _client_pan(s: Session) -> Optional[str]:
        """The client's PAN as seen so far (from the PAN, or inside the GSTIN) - confirmed or not."""
        pan = (s.profile.get("pan") or {}).get("value")
        gstin = (s.profile.get("gstin") or {}).get("value")
        return pan or (gstin[2:12] if gstin and len(gstin) == 15 else None)

    @staticmethod
    def _client_ids(s: Session) -> Dict[str, str]:
        """The client's PAN / GSTIN - only once confirmed; until then the rows stay unattributed."""
        if not s.confirmed:
            return {}
        return {k: s.profile[k]["value"] for k in CLIENT_KEYS if k in s.profile}

    def _tracker_payload(self, s: Session, slot: Slot) -> Dict[str, Any]:
        if isinstance(slot, (Instance, Carrier)):
            return self._sdis_payload(s, slot)
        v = dict(slot.values)
        # Name, PAN and GSTIN say WHO: none of them go on a row until the client is confirmed.
        prof = {k: p["value"] for k, p in s.profile.items() if k not in CLIENT_KEYS + ("name",)}
        prof.update(self._client_ids(s))
        if s.confirmed and "name" in s.profile:
            prof["name"] = s.profile["name"]["value"]
        key = self.row_key(s, v)
        supersedes = slot.sent_key if slot.sent_key and slot.sent_key != key else None
        slot.sent_key = key
        lowered, slot.lowered = slot.lowered, False        # the tracker is told once; later rows are ordinary
        confirmed, slot.confirmed = slot.confirmed, False
        gstin = prof.get("gstin") or ""
        pan = prof.get("pan") or (gstin[2:12] if len(gstin) == 15 else "")
        status = v.get("status") or SUBMIT_LEVELS[0]
        filing_date = (f"{v['filing_date']} 00:00:00" if v.get("filing_date")
                       else datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        default_form = "GST Return" if "gst" in (s.portal or "").lower() else "ITR"
        arn_value = str(v.get("arn") or "").strip().upper()
        has_arn = bool(arn_value and arn_value not in ("N/A", "NA", "UNKNOWN"))
        # original_author marks the PC that captured/filed the return when an ARN appears:
        # an in-progress row (draft / form selected) without an ARN remains None.
        orig_author = device_name() if has_arn else None
        payload = {
            "source": "sgt",
            "session_id": f"SGT-{s.session_id}",
            "portal": s.portal,
            "pan": pan,
            "gstin": gstin,
            "client_name": prof.get("name", ""),
            "dob": prof.get("dob", ""),
            "mobile": prof.get("phone", ""),
            "email": prof.get("email", ""),
            "filing_type": v.get("form") or default_form,
            "filing_preference": v.get("filing_type", ""),
            "period_label": v.get("period", ""),
            "arn": v.get("arn") or "N/A",
            "status": status,
            "filing_date": filing_date,
            "raw_text": "",                         # SGT never stores page text
            "capture_method": CAPTURE_METHOD,
            "capture_browser": s.browser,
            "identity_resolved": bool(pan),
            "page_url": slot.first_page,
            "dataset_key": key,
            "supersedes_dataset_key": supersedes,
            "status_correction": lowered,
            "status_confirmed": bool(lowered and confirmed),     # a complete (ARN + verified) row yields only to this
            "original_author": orig_author,
            "raw_payload": {
                "source": {"engine": "SGT", "mode": MODE_LIVE, "record": slot.record, "confidence": slot.confidence,
                           "identity": s.confirm_note or "not confirmed yet",
                           "evidence_source": slot.evidence_source, "first_seen_as": slot.first_seen_as},
                "sgt_dataset": v,
                "client_profile": prof,
                "timeline": list(s.timeline),
                "session_id": f"SGT-{s.session_id}",
                "dataset_key": key,
                "original_author": orig_author,
            },
        }
        sgt_i = self._sgt_i    # one read (see observe): a failure here would drop this row
        if sgt_i is not None:
            # SGT-I's enrichment sits beside the Core's values, in its own key only (14.2 rule 4).
            extra = sgt_i.enrichment(s.session_id)
            if extra:
                payload["raw_payload"]["sgt_i"] = extra
        self._attach_sdis(s, payload)
        self._attach_verifier(s, slot, payload)
        return payload
