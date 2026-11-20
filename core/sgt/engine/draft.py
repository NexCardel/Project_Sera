"""
core/sgt/engine/draft.py - the dataset being worked on
======================================================
Built from pieces read on different pages; each piece belongs to the page it came from. It becomes a
dataset row once complete, when another period or form is opened, or at session end.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.sgt.specs.resolver import PageResult
from core.sgt.specs.toolbox import submit_level
from .policy import CURRENT_RECORD, MISSES_TO_CLEAR, MISSING_CLEAR_SEC
from .session import Draft, Session, compose


class DraftBuilder:
    """The dataset in progress (part of SgtEngine)."""

    # ── The dataset being worked on ──────────────────────────────────────────────
    def _update_draft(self, s: Session, res: PageResult, url: str, source: str, rules: Any) -> None:
        d = s.draft
        seen = res.current
        fill_only = self._policy["pieces"] == "fill_empty_only"
        if fill_only:
            # OCR: only a field nothing has set yet, and never the status (wording alone never
            # promotes, R6). Nothing it reads or misses changes or clears a piece.
            seen = {f: h for f, h in seen.items() if f not in d.pieces and f != "status"}
        if self._foreign_pans:
            # Another client's PAN is readable on this page: its TEXT may be about them, so only
            # what the link and window title say (the filing wizard's form and year) is used.
            seen = {f: h for f, h in seen.items() if h.source != "page"}
        scoped = (s.portal or "") in getattr(rules, "scoped_portals", ())
        if scoped:
            # A GST return page is one self-contained return. A page that does not name its own
            # form (a comparison tool, a menu) says nothing about the dataset being built: its
            # year, period or status could belong to any form.
            named = (d.pieces.get("form") or {}).get("page") == url
            if "form" not in seen and not named:
                seen = {}
        if self._page_pans and not self._client_pan(s) and any(h.source == "page" for h in seen.values()):
            # The client is not known yet and this page's text prints PANs: remember them, so the
            # dataset is only ever written for a client whose PAN was among them.
            d.claims = sorted(set(d.claims) | self._page_pans)
        # This page owns the pieces it supplied: gone from it now = cleared (the CA went back
        # and emptied the field). Only after MISSES_TO_CLEAR reads in a row, so one read of a
        # half-drawn page cannot wipe a good value.
        now = self._clock()
        for fld, piece in list(d.pieces.items()):
            if piece["page"] != url or fill_only:
                continue
            if fld in seen:
                piece["misses"], piece["missing_since"] = 0, None
                continue
            piece["misses"] = piece.get("misses", 0) + 1
            if piece.get("missing_since") is None:
                piece["missing_since"] = now
            # Both: several reads AND some seconds. Clicking Continue leaves the old link in the
            # address bar for a moment while the page goes blank - that gap must not clear what
            # was just picked (seen in a real test: the AY was cleared 2 s after it was set).
            if piece["misses"] >= MISSES_TO_CLEAR and now - piece["missing_since"] >= MISSING_CLEAR_SEC:
                del d.pieces[fld]
                self._event(s, "current", change="cleared", field=fld, value=piece["value"], page=url, source=source)
                self._echo(f"[SGT] {self._tag} dataset in progress: {fld} cleared (gone from its page)")
        if not seen:
            return
        # A different period is a different dataset: close the one being built, start afresh.
        if scoped and "form" in seen:
            old_form = (d.pieces.get("form") or {}).get("value")
            if old_form and old_form != seen["form"].value:
                # Another return: nothing read for the old one (year, period, status) is carried over.
                old_period = d.values(rules).get("period") or d.last_period
                self._close_draft(s, rules, f"a different form was opened ({old_form} -> {seen['form'].value})", url)
                d = s.draft
                d.last_period = ""
                if old_period:
                    self._echo(f"[SGT] {self._tag} dataset in progress closed: {old_form} {old_period}")
        before = d.values(rules).get("period") or d.last_period
        trial = dict(d.values())
        trial.update({f: h.value for f, h in seen.items()})
        after = compose(trial, rules).get("period")
        if before and after and before != after:
            if fill_only:
                return                          # OCR never opens another dataset
            self._close_draft(s, rules, "a different period was opened", url)
            d = s.draft
        for fld, hit in seen.items():
            old = d.pieces.get(fld)
            if old and old["value"] == hit.value:
                old["page"], old["source"], old["misses"], old["missing_since"] = url, hit.source, 0, None
                continue
            d.pieces[fld] = {"value": hit.value, "page": url, "source": hit.source, "spec": hit.spec,
                             "evidence": hit.evidence, "misses": 0, "missing_since": None,
                             "ev": self._ev_stamp(url, source)}
            if hit.source == "link":
                d.pieces[fld]["ev"]["src"] = "link"     # read from the page link, not its text
            self._event(s, "current", change="changed" if old else "set", field=fld, value=hit.value,
                        previous=old["value"] if old else None, spec=hit.spec, via=hit.source, page=url, source=source,
                        evidence=hit.evidence if hit.evidence != hit.value else None)
            self._echo(f"[SGT] {self._tag} dataset in progress: {fld} = {hit.value}"
                       f"{' (was ' + old['value'] + ')' if old else ''}  [{hit.spec}, {hit.source}]")
        period_now = d.values(rules).get("period")
        if period_now:
            d.last_period = period_now
        if fill_only:
            return                              # a row from OCR needs its own ARN (R6): the next UIA read or the end writes it
        # Complete already? Then it is a dataset now - written at once rather than when it
        # closes, so quitting the app (or a crash) in the middle of a filing loses nothing.
        # It keeps building; later pieces update the same row.
        vals, missing = self._draft_as_dataset(d, rules)
        if not missing:
            prev = d.slot
            if (prev is not None and prev in s.slots and not prev.arn and not scoped
                    and submit_level(prev.values.get("status")) <= submit_level("Draft")
                    and prev.form_period != (vals.get("form"), vals.get("period"))):
                # The CA went back and changed the form (or year) of this same dataset, still only a
                # draft in a wizard: move its row rather than leave a stale one behind (the old key is
                # superseded on send). A return that was submitted, or a portal where each period is
                # its own return (GST), is never moved: a new period is a new dataset.
                old = dict(prev.values)
                prev.values.update(vals)
                self._ev_fields(prev, prev.values, [k for k in vals if old.get(k) != vals[k]], d.pieces, url, source)
                self._queue(s, prev)
                self._event(s, "dataset", change="moved", record=CURRENT_RECORD, previous=old,
                            values=prev.values, page=url, source=source)
                self._echo(f"[SGT] {self._tag} dataset in progress moved: {self._label(old)} -> {self._label(prev.values)}")
            else:
                st = d.pieces.get("status")
                d.slot = self._merge_values(s, vals, CURRENT_RECORD, 85, url, source, claimed=d.claims or None,
                                            follow=True, status_explicit=bool(st and st.get("page") == url),
                                            pieces=d.pieces)

    @staticmethod
    def _draft_as_dataset(d: Draft, rules: Any) -> Tuple[Dict[str, str], List[str]]:
        """
        The dataset being built as dataset values, plus what it still lacks (empty = complete).
        Its captured form + period make it at least a Draft, whatever a page status said
        ("Not Filed" on the form's own page is still a return being prepared).
        """
        vals = d.values(rules)
        if vals.get("status"):
            vals["status_evidence"] = d.pieces.get("status", {}).get("evidence") or vals["status"]
        missing = [f for f in getattr(rules, "complete_when", ("form", "period")) if not vals.get(f)]
        if missing:
            return vals, missing
        working = getattr(rules, "in_progress_status", None)
        needs_link = getattr(rules, "in_progress_needs_link", True)
        if working and (d.link_evidence or not needs_link) and submit_level(vals.get("status")) < submit_level(working):
            vals["status"] = working
            vals["status_evidence"] = ("form + period captured (in the page link)" if d.link_evidence
                                       else "form + period captured")
        if not vals.get("status"):
            missing.append("status (nothing shows it, and no link evidence that it was being filed)")
        return vals, missing

    def _close_draft(self, s: Session, rules: Any, reason: str, url: str) -> None:
        """Turns the dataset being built into a dispatched one if it is complete; logs it if not."""
        d = s.draft
        s.draft = Draft()
        if not d.pieces:
            return
        vals, missing = self._draft_as_dataset(d, rules)
        if missing:
            self._event(s, "current", change="incomplete - not dispatched", values=vals, missing=missing, reason=reason)
            self._echo(f"[SGT] {self._tag} dataset in progress dropped ({reason}): missing {', '.join(missing)}")
            return
        self._merge_values(s, vals, CURRENT_RECORD, 85, url, "pages", claimed=d.claims or None, pieces=d.pieces)

    def _fill_from_draft(self, s: Session, values: Dict[str, str], rules: Any) -> bool:
        """
        A dataset that lacks its form or period (a confirmation page shows the ARN, not the
        form) takes them from the dataset being built - unless they disagree, which means
        it is some other dataset. True if the draft was used.
        """
        draft = s.draft.values(rules)
        if not draft or (values.get("form") and values.get("period")):
            return False
        for k in ("form", "period"):
            if values.get(k) and draft.get(k) and values[k] != draft[k]:
                return False
        used = False
        for k in ("form", "period", "filing_type"):
            if not values.get(k) and draft.get(k):
                values[k] = draft[k]
                used = True
        return used
