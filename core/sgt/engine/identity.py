"""
core/sgt/engine/identity.py - who the session's client is
=========================================================
Profile values, the client's PAN / GSTIN and their sightings, a contradiction from another client,
the saved-client link by portal user ID, resuming a parked client, and the identity pill.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from core.sgt.specs.resolver import PageResult
from core.sgt.specs.specs import compose_values
from core.sgt.specs.toolbox import MERGES
from .policy import (CLIENT_KEYS, CLIENT_LINK_SPEC, LOGIN_LINK, LOGOUT_LINK, PAN_TOKEN, PROFILE_LABELS,
                     RESUME_WINDOW_SEC, TIMELINE_CAP)
from .session import Session, client_value, session_from_json


class Identity:
    """Names the session's client from what its pages show (part of SgtEngine)."""

    def _offer_profile(self, s: Session, fld: str, value: str, spec: str, confidence: int,
                       url: str, source: str, registry: Any) -> None:
        """A profile value seen on a page: taken if new, else handled by the field's merge policy."""
        held = s.profile.get(fld)
        if held is not None:
            if held["value"] == value and fld in CLIENT_KEYS:
                self._sighted(s, fld, url)
                return
            policy = registry.merge_policy(fld) if registry is not None else "latch"
            if held["value"] == value or policy == "latch":
                return
            if not MERGES[policy](held["value"], value):
                if (fld, value) not in s.not_promoted:
                    s.not_promoted.add((fld, value))
                    self._event(s, "profile", change="not promoted", field=fld, value=value,
                                kept=held["value"], spec=spec, page=url, source=source)
                return
            self._event(s, "profile", change="promoted", field=fld, value=value, previous=held["value"],
                        spec=spec, confidence=confidence, page=url, source=source)
            self._echo(f"[SGT] {self._tag} profile {fld} promoted: {held['value']} -> {value}  [{spec}]")
            s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence}
            if fld == "name":
                self._hud(s, "update", "Client name completed", value)
                self._queue_all(s)
            return
        s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence, "pages": [url]}
        self._event(s, "profile", field=fld, value=value, spec=spec, confidence=confidence, page=url, source=source)
        self._echo(f"[SGT] {self._tag} profile {fld} = {value}  [{spec}]")
        if fld in CLIENT_KEYS:
            self._ev_identity(s, fld, value, spec, url, source)
            self._sighted(s, fld, url)
        if fld in ("pan", "gstin", "name"):
            self._queue_all(s)                  # rows written before the client was known get their identity
        if fld != "username":                   # typed char by char on a login page: never a pill
            self._new_profile.append(fld)       # the pill announces the page's datapoints together

    def _sighted(self, s: Session, fld: str, url: str) -> None:
        """A page showing the client's PAN/GSTIN. The first one attributes the session's rows
        (portals often show it only once); later ones only strengthen the note in the log."""
        pages = s.profile[fld].setdefault("pages", [])
        if url not in pages and len(pages) < 10:
            pages.append(url)
        pan = (s.profile.get("pan") or {}).get("value")
        gstin = (s.profile.get("gstin") or {}).get("value")
        if any(len((s.profile.get(k) or {}).get("pages", [])) >= 2 for k in CLIENT_KEYS):
            note = f"{fld.upper()} seen on two pages"
        elif pan and gstin and len(gstin) == 15 and gstin[2:12] == pan:
            note = "PAN and GSTIN agree"
        else:
            note = f"{fld.upper()} seen once"
        if note.endswith("seen once") and (s.profile.get(fld) or {}).get("spec") == CLIENT_LINK_SPEC:
            note = "portal user ID matched a saved client"
        first = not s.confirmed
        if not first and (note == s.confirm_note or s.confirm_note.startswith(note + "; earlier session")):
            return
        s.confirmed, s.confirm_note = True, note
        self._event(s, "identity", change="client identified" if first else "identity strengthened", note=note)
        if not first:
            return
        self._echo(f"[SGT] {self._tag} client identified ({note})")
        if (s.profile.get(fld) or {}).get("spec") != CLIENT_LINK_SPEC:     # named by a page, not a lookup
            self._resume_by_client(s, fld, s.profile[fld]["value"], self._clock())
        self._queue_all(s)
        for slot in s.slots:
            if slot.held and slot.sent_key is None:
                self._queue(s, slot)            # a card that was waiting for the client

    def _resume_by_client(self, s: Session, key: str, value: str, now: float) -> bool:
        """`s` was just named by its first page (`key` = PAN or GSTIN `value`, read from the page). A
        parked session of the same client and portal - from any window, idle less than
        RESUME_WINDOW_SEC ago - hands over its dataset in progress, its timeline and its identity note.
        Identity is never borrowed: `s` already has its client from its own page, and nothing else of
        the parked profile or its rows is taken. The parked entry is used up."""
        best: Optional[Tuple[int, Tuple[float, Dict[str, Any]]]] = None
        for h, lst in self._parked.items():
            for c in lst:
                snap = c[1]
                if client_value(snap, key) != value:
                    continue
                if s.portal and snap.get("portal") and s.portal != snap.get("portal"):
                    continue
                if now - float(snap.get("last_seen") or c[0]) > RESUME_WINDOW_SEC:
                    continue
                if best is None or float(snap.get("last_seen") or c[0]) > float(best[1][1].get("last_seen") or best[1][0]):
                    best = (h, c)
        if best is None:
            return False
        h, c = best
        self._parked[h].remove(c)
        if not self._parked[h]:
            self._parked.pop(h, None)
        snap = c[1]
        old = session_from_json(snap)
        # The dataset in progress carries on only if this page does not already show another one
        # (a piece of the same name with another value): then it is a different filing and starts fresh.
        if all(s.draft.pieces[f]["value"] == p["value"] for f, p in old.draft.pieces.items() if f in s.draft.pieces):
            for f, piece in old.draft.pieces.items():
                s.draft.pieces.setdefault(f, piece)
            s.draft.claims = sorted(set(s.draft.claims) | set(old.draft.claims))
        s.timeline = (old.timeline + [u for u in s.timeline if u not in old.timeline])[-TIMELINE_CAP:]
        if old.confirm_note:
            s.confirm_note = f"{s.confirm_note}; earlier session: {old.confirm_note}"
        idle_sec = max(0.0, round(now - float(snap.get("last_seen") or c[0]), 1))
        s.ev.parent = s.ev.parent or str(snap.get("session_id") or "")
        self._trail(s, "resumed-client", None, str(snap.get("session_id") or ""))
        self._event(s, "session_resumed", from_session=snap.get("session_id"), idle_sec=idle_sec, by="client")
        self._echo(f"[SGT] {self._tag} same client as a parked session: its dataset in progress carries on "
                   f"(idled {int(idle_sec)}s)")
        self._save_state(force=True)
        return True

    @staticmethod
    def _other_pans_on_page(s: Session, res: PageResult, lines: List[str]) -> Set[str]:
        """PANs on this page that are not the client's. A card whose own "PAN :" line was
        misread would otherwise be taken as the client's (found by the noise tests,
        2026-09-22), so while another client's PAN is readable anywhere on the page, its
        dataset cards are not attributed. Only consulted for record cards: the filing wizard's
        own pages legitimately list other PANs (landlord, donee)."""
        mine = set()
        for k in CLIENT_KEYS:
            v = (s.profile.get(k) or {}).get("value") or getattr(res.profile.get(k), "value", None)
            if v:
                mine.add(v if k == "pan" else v[2:12])
        if not mine:
            return set()
        return {p for ln in lines for p in PAN_TOKEN.findall(ln or "")} - mine

    @staticmethod
    def _identity_conflict(s: Session, res: PageResult) -> Optional[str]:
        """The PAN/GSTIN on this page that contradicts the session's client, or None."""
        held = {k: (s.profile.get(k) or {}).get("value") for k in CLIENT_KEYS}
        seen = {k: getattr(res.profile.get(k), "value", None) for k in CLIENT_KEYS}
        for k in CLIENT_KEYS:
            if held[k] and seen[k] and held[k] != seen[k]:
                return k
        pan = held["pan"] or seen["pan"]
        gstin = seen["gstin"] if seen["gstin"] and not held["gstin"] else None
        if pan and gstin and len(gstin) == 15 and gstin[2:12] != pan:
            return "gstin"
        gstin = held["gstin"]
        if gstin and seen["pan"] and not held["pan"] and len(gstin) == 15 and gstin[2:12] != seen["pan"]:
            return "pan"
        return None

    def _announce_profile(self, s: Session, before_identity: bool) -> None:
        """One pill event for the client datapoints this page gave us - the pill used to appear
        only for the PAN/GSTIN, so capturing a name, date of birth, e-mail or phone looked like
        nothing had happened (reported 2026-09-23). Never one pop per field."""
        fields = [f for f in dict.fromkeys(self._new_profile) if f in s.profile]
        self._new_profile = []
        if not fields:
            return
        known = self._client_pan(s) or (s.profile.get("gstin") or {}).get("value")
        named = [PROFILE_LABELS.get(f, f.replace("_", " ")) for f in fields]
        shown = ", ".join(named[:3]) + (f" +{len(named) - 3} more" if len(named) > 3 else "")
        who = self._who(s)
        if before_identity and known:               # this page is the one that identified them
            ident = next((f for f in fields if f in CLIENT_KEYS), None)
            detail = f"{ident.upper()}: {s.profile[ident]['value']}" if ident else shown
            lead = "" if (ident and who == s.profile[ident]["value"]) else who   # no name yet: just the id
            self._hud(s, "identity", "Client identified", f"{lead} • {detail}".strip(" •"))
            rest = [n for f, n in zip(fields, named) if f not in CLIENT_KEYS]
            if not rest:
                return
            shown = ", ".join(rest[:3]) + (f" +{len(rest) - 3} more" if len(rest) > 3 else "")
        self._hud(s, "capture", "Client details captured", f"{who} • {shown}".strip(" •"))

    def _absorb(self, s: Session, res: PageResult, url: str, source: str, registry: Any = None) -> None:
        had_client = bool(self._client_pan(s) or (s.profile.get("gstin") or {}).get("value"))
        for fld, hit in res.profile.items():
            self._offer_profile(s, fld, hit.value, hit.spec, hit.confidence, url, source, registry)
        # Datapoints built from parts (first + middle + last name): offered like any other
        # value, so the joined name goes through the same promotion rule as a read one.
        prules = getattr(registry, "profile_rules", None)
        if self._policy["profile"] == "all":
            if prules is not None and prules.compose:
                held = {k: v["value"] for k, v in s.profile.items()}
                for fld, value in compose_values(held, prules.compose).items():
                    self._offer_profile(s, fld, value, f"compose:{fld}", 92, url, source, registry)
            self._link_client(s, url, source, registry)
        self._announce_profile(s, before_identity=not had_client)
        rules = registry.current_rules if registry is not None else None
        for ds in res.datasets:
            self._merge(s, ds, url, source, rules, registry)

    def _link_client(self, s: Session, url: str, source: str, registry: Any) -> None:
        """A page that shows the portal's user ID (an SDIS profile datapoint, "username") but not the
        client's GSTIN: the saved client who owns that user ID names the session. Once per user ID per
        session. Only fills a gap - a GSTIN / PAN the pages gave is never overridden - and a later header
        GSTIN that disagrees still ends the session like any other contradiction."""
        user_id = (s.profile.get("username") or {}).get("value")
        if not user_id or self._resolve_client is None or any(k in s.profile for k in CLIENT_KEYS):
            return
        if url and (LOGIN_LINK.search(url) or LOGOUT_LINK.search(url)):
            return                              # still being typed (or retyped): link once the portal moves on
        if user_id in s.linked:
            return
        s.linked.add(user_id)
        try:
            found = self._resolve_client(s.portal, user_id)
        except Exception:
            found = None
        if not found:
            self._event(s, "identity", change="user id not matched to a saved client")
            return
        for fld in ("gstin", "pan"):            # a GST session is keyed by the GSTIN; the PAN only when that is all there is
            if found.get(fld):
                self._offer_profile(s, fld, found[fld], CLIENT_LINK_SPEC, 85, url, source, registry)
                break
        if found.get("name"):                   # the company name the client is registered under in the master DB
            self._offer_profile(s, "name", found["name"], CLIENT_LINK_SPEC, 85, url, source, registry)
        if found.get("client_id") is not None:
            self._event(s, "identity", change="user id matched a saved client", user_id=user_id,
                        client_id=found["client_id"], company=found.get("name") or "")
