"""
core/sgt/engine/demotion.py - asking before a filing's status is lowered
========================================================================
A page that shows a lower status for a filing that has an ARN is never applied on its own: the user
is asked, and the answer is applied on SGT's own thread at its next tick.
"""

from __future__ import annotations

import uuid

from .session import Session, Slot


class Demotion:
    """The lower-this-status question and its answer (part of SgtEngine)."""

    def _request_demotion(self, s: Session, slot: Slot, old: str, new: str, evidence: str,
                          stamp: str, url: str) -> None:
        mark = f"{new}|{stamp}"
        if mark in slot.demotion_asked:
            return                              # already put to the user for this stay on this page
        slot.demotion_asked.add(mark)
        token = uuid.uuid4().hex[:12]
        self._demotions[token] = (s, slot, new, evidence)
        info = {"token": token, "client": self._who(s), "form": slot.values.get("form") or "",
                "period": slot.values.get("period") or "", "arn": slot.arn, "from": old, "to": new,
                "evidence": evidence, "portal": s.portal}
        self._event(s, "dataset", change="status demotion needs confirmation", key=self._label(slot.values),
                    **{"from": old, "to": new, "evidence": evidence}, page=url)
        self._echo(f"[SGT] {self._tag} a page shows {new!r} for {self._label(slot.values)}, which was {old!r} "
                   f"with an ARN - asking before lowering it")
        if self._ask_demotion is not None:
            try:
                self._ask_demotion(info)
            except Exception as e:              # a dialog problem must never stop capture; the status is kept
                self._echo(f"[SGT] {self._tag} could not ask about the demotion: {e}")

    def answer_demotion(self, token: str, lower: bool) -> None:
        """The user's answer, from the UI thread: it is applied on SGT's own thread at its next tick."""
        self._demotion_answers.append((token, bool(lower)))

    def _apply_demotion_answers(self) -> None:
        while self._demotion_answers:
            token, lower = self._demotion_answers.popleft()
            (self.confirm_demotion if lower else self.decline_demotion)(token)

    def confirm_demotion(self, token: str) -> bool:
        """The user agreed to lower the status: apply it and send the row (the tracker accepts it)."""
        got = self._demotions.pop(token, None)
        if got is None:
            return False
        s, slot, new, evidence = got
        old = slot.values.get("status")
        slot.values["status"] = new
        if evidence:
            slot.values["status_evidence"] = evidence
        slot.lowered = True
        slot.confirmed = True
        self._event(s, "dataset", change="status lowered - confirmed by the user", key=self._label(slot.values),
                    **{"from": old, "to": new})
        self._queue(s, slot)
        return True

    def decline_demotion(self, token: str) -> bool:
        got = self._demotions.pop(token, None)
        if got is not None:
            s, slot, new, _ = got
            self._event(s, "dataset", change="status kept - lowering declined", key=self._label(slot.values),
                        to=new)
        return got is not None
