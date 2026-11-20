"""
core/sgt/engine/sdis_rows.py - SDIS containers and Others values
================================================================
Blueprint S.2 / S.4: the page's SDIS field values fill container instances and Others values; they
ride on the session's rows, or on its one carrier row when it has no dataset row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from core.dataset_key import SDIS_INFO_METHOD, compute_dataset_key
from core.sgt.containers import (KEYED_MINIMUM, Carrier, Instance, evidence_of, info_signature, load_doc,
                                 sdis_info)
from core.sgt.specs.resolver import PageResult
from core.sgt.specs.toolbox import SUBMIT_LEVELS
from .policy import CAPTURE_METHOD, CLIENT_KEYS, MODE_LIVE, SDIS_CARRIER_FORM
from .session import Session


class SdisRows:
    """SDIS containers and their rows (part of SgtEngine)."""

    # ── SDIS containers (blueprint S.2 / S.4) ────────────────────────────────────
    def _feed_containers(self, s: Session, res: PageResult, hits: Dict[str, Any], url: str, source: str) -> None:
        """The page's SDIS field values into the session's container instances and Others values.
        Built like the dataset in progress: list pages never feed it, and while another client's
        PAN is readable only the link and title count. A changed containers document re-evaluates
        every open instance first; nothing moves down."""
        if not hits and not any(x.sdis.instances for x in self._all_sessions()):
            return
        doc = load_doc()
        if doc is not self._sdis_doc:
            if self._sdis_doc is not None:
                for other in self._all_sessions():
                    for inst in other.sdis.recompute(doc):
                        self._event(other, "container", change="re-evaluated (containers file changed)",
                                    container=inst.container, level=inst.level, status=inst.status)
                        if inst.keyed:
                            self._queue(other, inst)
            self._sdis_doc = doc
        if self._foreign_pans:
            hits = {f: h for f, h in hits.items() if h.source != "page"}
        if not hits or res.is_list:
            return
        values = {f: h.value for f, h in hits.items()}
        for inst in s.sdis.feed(doc, s.portal, values, url):
            before = inst.sent_key
            self._event(s, "container", container=inst.container, form=inst.form, period=inst.period,
                        fields=sorted(inst.captured), level=inst.level, status=inst.status,
                        k_of_n=f"{inst.k} of {inst.n}", page=url, source=source)
            self._echo(f"[SGT] {self._tag} container {inst.container} {inst.period}: {inst.k} of {inst.n}"
                       f"{' - ' + inst.level if inst.level else ''} ({inst.status})")
            if inst.keyed:
                self._queue(s, inst)
                if before is None:
                    self._hud(s, "capture", "Dataset captured",
                              f"{self._who(s)} • {inst.container} • {inst.level or f'{inst.k} of {inst.n}'}".strip(" •"),
                              {"form": inst.form, "period": inst.period})
        changed = s.sdis.feed_others(doc, s.portal, values, datetime.now().isoformat(timespec="seconds"))
        if changed:
            self._event(s, "others", fields=changed, page=url, source=source)
            self._echo(f"[SGT] {self._tag} Others: {', '.join(changed)} captured")

    def _sdis_profile(self, s: Session) -> Dict[str, str]:
        """The session's profile values a row may carry: PAN, GSTIN and name only once confirmed."""
        prof = {k: p["value"] for k, p in s.profile.items() if k not in CLIENT_KEYS + ("name",)}
        if s.confirmed:
            prof.update({k: s.profile[k]["value"] for k in CLIENT_KEYS + ("name",) if k in s.profile})
        return prof

    def _sdis_info(self, s: Session) -> Optional[Dict[str, Any]]:
        if not (s.sdis.others or s.profile):
            return None
        return sdis_info(s.sdis, load_doc(), s.portal, self._sdis_profile(s),
                         datetime.now().isoformat(timespec="seconds"))

    def _attach_sdis(self, s: Session, payload: Dict[str, Any]) -> None:
        """S.4: the session's Others and Profile builder values ride on every row it writes."""
        info = self._sdis_info(s)
        if info:
            payload["raw_payload"]["sdis"] = info
            s.sdis.carried = info_signature(info)

    def _carry_sdis(self, s: Session) -> None:
        """Session end: values no dispatched row carried yet go out - on the session's last row, or,
        with no dataset row at all, on its one carrier row (capture method SGT_sdis_info)."""
        info = self._sdis_info(s)
        if not info or info_signature(info) == s.sdis.carried:
            return
        if any(e[0] is s for e in self._outbox.values()):
            return                              # a row on its way picks the values up when built
        sent = [x for x in list(s.slots) + list(s.sdis.instances) if x.sent_key]
        if sent:
            self._queue(s, sent[-1])
            return
        if s.sdis.carrier is None:
            s.sdis.carrier = Carrier()
        self._queue(s, s.sdis.carrier)

    def _sdis_payload(self, s: Session, obj: Any) -> Dict[str, Any]:
        """A container instance's tracker row, or the session's carrier row."""
        prof = self._sdis_profile(s)
        ids = self._client_ids(s)
        ident = ids.get("gstin") or ids.get("pan") or f"SGT{s.session_id}"
        if isinstance(obj, Instance):
            key = compute_dataset_key(s.portal, ident, obj.form, obj.period)
            form, period, status, method = obj.form, obj.period, obj.status or KEYED_MINIMUM, CAPTURE_METHOD
            dataset = {"form": form, "period": period, "status": status, "container": obj.container,
                       "fields": dict(obj.values), "evidence": evidence_of(obj),
                       "status_evidence": f"{obj.k} of {obj.n} fields captured"
                                          + (f" ({obj.level})" if obj.level else "")}
            record = obj.container
        else:
            key = compute_dataset_key(s.portal, ident, SDIS_CARRIER_FORM, "")
            form, period, status, method = "", "", SUBMIT_LEVELS[0], SDIS_INFO_METHOD
            dataset, record = {}, "sdis_info"
        supersedes = obj.sent_key if obj.sent_key and obj.sent_key != key else None
        obj.sent_key = key
        gstin = prof.get("gstin") or ""
        pan = prof.get("pan") or (gstin[2:12] if len(gstin) == 15 else "")
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
            "filing_type": form,
            "filing_preference": "",
            "period_label": period,
            "arn": "N/A",
            "status": status,
            "filing_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_text": "",                         # SGT never stores page text
            "capture_method": method,
            "capture_browser": s.browser,
            "identity_resolved": bool(pan),
            "page_url": next(iter(obj.evidence.values()), s.last_url) if isinstance(obj, Instance) else s.last_url,
            "dataset_key": key,
            "supersedes_dataset_key": supersedes,
            "raw_payload": {
                "source": {"engine": "SGT", "mode": MODE_LIVE, "record": record, "confidence": 0,
                           "identity": s.confirm_note or "not confirmed yet"},
                "sgt_dataset": dataset,
                "client_profile": prof,
                "timeline": list(s.timeline),
                "session_id": f"SGT-{s.session_id}",
                "dataset_key": key,
            },
        }
        self._attach_sdis(s, payload)
        self._attach_verifier(s, obj, payload)
        return payload
