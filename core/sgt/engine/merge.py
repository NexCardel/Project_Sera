"""
core/sgt/engine/merge.py - folding a dataset seen on a page into the session's rows
===================================================================================
Finding the row a dataset belongs to, the ARN's submit level, a bare submit message, the status
ladder (only up, except a correction by the same page) and which fields a page may correct.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.sgt.specs.resolver import Dataset
from core.sgt.specs.toolbox import SUBMIT_LEVELS, submit_level
from .policy import CLIENT_KEYS, IDENTITY_PROBLEM
from .session import Draft, Session, Slot


class Merging:
    """Merges datasets into the session's slots (part of SgtEngine)."""

    def _find_slot(self, s: Session, values: Dict[str, str], stamp: str = "", follow: bool = False) -> Optional[Slot]:
        arn = values.get("arn")
        if arn:
            for slot in s.slots:
                if slot.arn == arn:
                    return slot
        fp = (values.get("form"), values.get("period"))
        if all(fp):
            for slot in s.slots:
                if slot.form_period == fp and (not arn or not slot.arn):
                    return slot
        if follow and arn and stamp:
            # The same confirmation page, still in view, now shows another ack: a corrected read of the
            # first one - unless the page names a different return.
            for slot in s.slots:
                if slot.arn and slot.owner.get("arn") == stamp and (not all(fp) or slot.form_period in (None, fp)):
                    return slot
        return None

    def _other_client(self, s: Session, values: Dict[str, str]) -> Optional[str]:
        """The field naming a DIFFERENT client than the session's (a card of someone else's), or None."""
        for k in CLIENT_KEYS:
            held = (s.profile.get(k) or {}).get("value")
            if values.get(k) and held and values[k] != held:
                return k
        return None

    def _apply_identifier(self, s: Session, values: Dict[str, str]) -> None:
        """
        A captured ARN is a submission: at least "Submitted (Not Verified)" - or more on a
        portal that issues its ARN only later (submit_rules). The submit message, when one was
        read, may already say more; the higher level wins.
        """
        proven = self.store.get().submit_rules.proven_by_identifier(s.portal)
        if values.get("arn") and submit_level(values.get("status")) < submit_level(proven):
            values["status"] = proven
            values["status_evidence"] = ("ARN captured" if submit_level(proven) == 2
                                         else f"ARN captured ({s.portal} issues it only at this level)")

    @staticmethod
    def _is_whole_page(registry: Any, record: str) -> bool:
        """A whole-page record (a confirmation, a verification message) as against a card of a list."""
        for r in getattr(registry, "records", ()) or ():
            if r.name == record:
                return r.start is None
        return False

    def _tie_message(self, s: Session, values: Dict[str, str], url: str, source: str) -> bool:
        """
        A submit message with no ack on its page counts only for a return that already has one: the one
        its form + period name, or the only one in this session still waiting for verification. It never
        creates a dataset and never uses the return being built. True when it was tied (values then carry
        that return's ack).
        """
        fp = (values.get("form"), values.get("period"))
        acked = [sl for sl in s.slots if sl.arn]
        pool = [sl for sl in acked if sl.form_period == fp] if all(fp) else acked
        if len(pool) != 1:
            pool = [sl for sl in pool if submit_level(sl.values.get("status")) == 2]
        if len(pool) == 1:
            values["arn"] = pool[0].arn
            return True
        stamp = f"{url}#{s.visit}"
        if stamp not in s.untied:
            s.untied.add(stamp)
            why = "no return with an ack in this session" if not acked else "more than one return could be meant"
            self._event(s, "dataset", change="message not tied - ignored", status=values.get("status"),
                        reason=why, page=url, source=source)
            self._echo(f"[SGT] {self._tag} a submit message on this page was ignored: {why}")
        return False

    def _merge(self, s: Session, ds: Dataset, url: str, source: str, rules: Any = None, registry: Any = None) -> None:
        values = ds.values()
        other = self._other_client(s, values) or ("page" if self._foreign_pans else None)
        if other:
            self._event(s, "dataset", change="not attributed - another client's", record=ds.record,
                        field=other, page=url, source=source,
                        **({"reason": "another PAN is readable on the page"} if other == "page" else {}))
            self._echo(f"[SGT] {self._tag} {ds.record}: names a different {other.upper()} than this session's client - skipped")
            return
        card_pan = values.get("pan") or (values["gstin"][2:12] if len(values.get("gstin") or "") == 15 else None)
        claimed = [card_pan] if card_pan else None
        for k in CLIENT_KEYS:
            values.pop(k, None)
        if values.get("status"):
            values["status_evidence"] = ds.evidence("status")
        whole = self._is_whole_page(registry, ds.record)
        message_only = whole and bool(values.get("status")) and not values.get("arn")
        add_only = self._policy["datasets"] == "merge_add_only"
        if message_only and add_only:
            return                              # OCR wording with no ARN of its own never promotes (R6)
        pieces = None
        if message_only:
            if not self._tie_message(s, values, url, source):
                return
        elif rules is not None and self._fill_from_draft(s, values, rules):
            pieces = s.draft.pieces             # its form and period were read on the draft's pages
            self._echo(f"[SGT] {self._tag} {ds.record}: form/period taken from the dataset in progress")
            if values.get("arn"):
                # Submitted: the dataset being built is this one, and it is finished.
                s.draft = Draft()
                self._event(s, "current", change="completed by a submission", arn=values["arn"], page=url)
        self._merge_values(s, values, ds.record, ds.confidence, url, source, claimed=claimed,
                           follow=whole and not add_only, status_explicit=bool(values.get("status")),
                           recovered=not whole, pieces=pieces)

    # Fields a page may correct once it shows another value (form and period are the dataset's identity:
    # a different one is a different dataset, not a correction).
    _FOLLOW = ("arn", "filing_type", "filing_preference", "fy", "tax_period", "filing_date")

    def _merge_values(self, s: Session, values: Dict[str, str], record: str, confidence: int,
                      url: str, source: str, claimed: Optional[List[str]] = None, follow: bool = False,
                      status_explicit: bool = False, recovered: bool = False,
                      pieces: Any = None) -> Optional[Slot]:
        """
        follow: this is a page read of one dataset (a whole-page record, the return being built), not a card
        of a list: a value the SAME page, still in view, now shows differently replaces the one it set.
        status_explicit: the page itself shows a status right now (not a default the dataset was lifted to).
        recovered: the values are a card of a list page, so a row born from them is "recovered" (6.5).
        """
        if not values.get("arn") and not (values.get("form") and values.get("period")):
            return None                         # nothing to key it on - not a dataset yet
        add_only = self._policy["datasets"] == "merge_add_only"
        # OCR (R6): a row is created, or its status raised, only by a read that also gives the
        # dataset's own ARN/ack (the resolver's label, shape and date checks already passed).
        with_id = bool(values.get("arn")) or not add_only
        values = dict(values)
        self._apply_identifier(s, values)
        if not values.get("status"):
            values["status"] = SUBMIT_LEVELS[0]     # the ladder's default: nothing shows any work
        stamp = f"{url}#{s.visit}"
        slot = self._find_slot(s, values, stamp, follow)
        if slot is not None and claimed:
            slot.claimed = sorted(set(slot.claimed or []) | set(claimed))
        if slot is None and not with_id:
            return None
        if slot is None:
            slot = Slot(dict(values), record, confidence, url, claimed=claimed)
            slot.owner = {} if add_only else {k: stamp for k in values}
            slot.evidence_source = slot.first_seen_as = "ocr" if add_only else "recovered" if recovered else "uia"
            self._ev_fields(slot, values, values, pieces, url, source)
            s.slots.append(slot)
            if add_only:
                if self._stats is not None:
                    self._stats.record_ocr_only_row(self._diag.get("portal") or "?", self._today())
                self._report(self._on_ocr_row)
            self._queue(s, slot)
            self._event(s, "dataset", change="new", record=record, values=values,
                        confidence=confidence, page=url, source=source)
            self._echo(f"[SGT] {self._tag} dataset {self._label(values)}  [{record}]")
            if slot.held:
                if any(IDENTITY_PROBLEM in p for p in slot.held):
                    self._hud(s, "prompt", "Submission seen - return not identified",
                              "open the return's page so SGT can place it")
                return slot                     # not written - the pill stays quiet about it
            submitted = submit_level(values.get("status")) >= 2
            detail = f"ARN: {values['arn']} • {values['status']}" if values.get("arn") else values["status"]
            self._hud(s, "submit" if submitted else "capture",
                      "Submission captured" if submitted else "Dataset captured",
                      f"{self._who(s)} • {detail}".strip(" •"), slot.values)
            return slot
        ds_record = record
        changes = {}
        if not add_only and slot.evidence_source == "ocr" and slot.arn and values.get("arn") == slot.arn:
            slot.evidence_source = "uia"        # a UIA read shows the same ARN: the row no longer rests on OCR
            changes["evidence_source"] = ["ocr", "uia"]
        elif (not add_only and not recovered and slot.evidence_source == "recovered"
              and values.get("arn") and slot.arn in (None, values["arn"])):
            slot.evidence_source = "uia"        # a confirmation page shows the ack; first_seen_as keeps "recovered"
            changes["evidence_source"] = ["recovered", "uia"]
        for k, v in values.items():
            old = slot.values.get(k)
            if k == "status_evidence":
                continue                        # travels with the status, below
            mine = follow and slot.owner.get(k) == stamp
            if k == "status":
                up = (old is None or submit_level(v) > submit_level(old)) and with_id
                corrected = (not up) and mine and status_explicit and v != old and not add_only
                if corrected and slot.arn:
                    # A filing with an ARN: lowering its status is risky - ask, never assume.
                    self._request_demotion(s, slot, old, v, values.get("status_evidence") or "", stamp, url)
                    corrected = False
                    continue
                if up or corrected:
                    slot.values[k] = v
                    slot.owner[k] = stamp
                    changes[k] = [old, v]
                    if corrected:
                        slot.lowered = True
                    if values.get("status_evidence"):
                        slot.values["status_evidence"] = values["status_evidence"]
            elif old is None:
                slot.values[k] = v
                if not add_only:
                    slot.owner[k] = stamp
                changes[k] = [None, v]
            elif old != v:
                if mine and k in self._FOLLOW:
                    slot.values[k] = v
                    slot.owner[k] = stamp
                    changes[k] = [old, v]
                else:
                    changes[k] = [old, v, "disagrees - kept the first"]
        self._ev_fields(slot, slot.values, [k for k, c in changes.items() if len(c) == 2], pieces, url, source)
        if any(len(c) == 2 for c in changes.values()):     # a real change, not just a disagreement
            self._queue(s, slot)
        if changes:
            self._event(s, "dataset", change="updated", record=ds_record, key=self._label(slot.values),
                        changes=changes, page=url, source=source)
            self._echo(f"[SGT] {self._tag} dataset {self._label(slot.values)} updated: "
                       + ", ".join(f"{k} {c[0]!r}->{c[1]!r}" for k, c in changes.items()))
            if "status" in changes and changes["status"][0]:        # a status that moved
                self._hud(s, "update", "Dataset status updated",
                          f"{changes['status'][0]} \u2192 {changes['status'][1]}", slot.values)
        return slot

    @staticmethod
    def _label(values: Dict[str, str]) -> str:
        return " ".join(str(values[k]) for k in ("form", "period", "status", "arn") if values.get(k))
