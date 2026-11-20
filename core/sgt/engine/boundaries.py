"""
core/sgt/engine/boundaries.py - when a session starts, is set aside, comes back or ends
=======================================================================================
Per browser window and tab: the session in view, the ones set aside (another tab, another portal,
another client), login / logout links, idle windows, closed windows. Nothing here reads a page.
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from core.sgt import evidence as sgt_evidence
from .policy import (CLOSED_KEEP_SEC, CLOSED_RING, LOGIN_LINK, LOGOUT_LINK, RESUME_WINDOW_SEC,
                     SESSION_QUIET_SEC, TIMELINE_CAP)
from .session import RESTORED, Session, browser_of, session_from_json


class Boundaries:
    """Session bookkeeping for the windows SGT watches (part of SgtEngine)."""

    def end_session(self, hwnd: int, reason: str, keep_parked: bool = False) -> None:
        """Ends EVERY session of this window - the one in view and the ones set aside. For a
        closed window, shutdown and switching SGT off; a login / logout page or the idle limit
        end only the session they belong to (``_end_one``). Sessions parked from this window are
        forgotten too, unless keep_parked (shutdown: they resume after a restart)."""
        if not keep_parked:
            self._parked.pop(hwnd, None)
        s = self._sessions.pop(hwnd, None)
        aside = self._shelf.pop(hwnd, [])
        for x in ([s] if s is not None else []) + aside:
            self._finish(x, reason)
        if s is not None or aside:
            self._save_state(force=True)
        if self._stats is not None:
            self._stats.save()

    def window_gone(self, hwnd: int) -> None:
        """The window is really closed (IsWindow false): every session of it ends, none is parked."""
        self.end_session(hwnd, "window closed")

    def window_idle(self, hwnd: int) -> None:
        """The window still exists but its sessions sat unseen too long (the user worked elsewhere): a
        session with a client is parked - the same client, named again by any window, resumes its
        draft - and the others end. Never raises."""
        try:
            now = self._clock()
            sessions = ([self._sessions.pop(hwnd)] if hwnd in self._sessions else []) + self._shelf.pop(hwnd, [])
            for s in sessions:
                self._park(hwnd, s, now)
            if sessions and self._stats is not None:
                self._stats.save()
        except Exception as e:
            self._echo(f"[SGT] {self._tag} error, idle window skipped: {e}")

    def end_all(self, reason: str) -> None:
        for hwnd in list(self._sessions) + [h for h in self._shelf if h not in self._sessions]:
            self.end_session(hwnd, reason, keep_parked=True)
        self._save_state(force=True)            # parked clients stay on disk for the next start

    def _end_one(self, s: Session, reason: str) -> None:
        """Ends one session; the window's other sessions carry on."""
        self._finish(s, reason)
        self._save_state(force=True)
        if self._stats is not None:
            self._stats.save()

    def _all_sessions(self) -> List[Session]:
        return list(self._sessions.values()) + [x for lst in self._shelf.values() for x in lst]

    def sessions_open(self) -> bool:
        """True while some session with content was touched in the last SESSION_QUIET_SEC: an update
        restart now could cut a client's work in two."""
        try:
            now = self._clock()
            return any(s.has_content and now - s.last_seen < SESSION_QUIET_SEC for s in self._all_sessions())
        except Exception:
            return True

    def _shelve(self, hwnd: int, s: Session) -> None:
        """Sets the window's session in view aside. An empty one has nothing to keep."""
        if s.has_content:
            self._shelf.setdefault(hwnd, []).append(s)
            self._trail(s, "shelved")

    def _bring_back(self, hwnd: int, back: Session, url: str, h: Any, now: float) -> Session:
        """`back` (a session this window had set aside) is the one in view again."""
        self._sessions[hwnd] = back
        back.last_url, back.last_hash, back.last_read, back.last_seen = url, h, now, now
        if url and (not back.timeline or back.timeline[-1] != url) and len(back.timeline) < TIMELINE_CAP:
            back.timeline.append(url)
        self._echo(f"[SGT] {self._tag} back on this window's session for the client on this page")
        return back

    def _unshelve(self, hwnd: int, portal: str, key: Optional[str] = None,
                  value: Optional[str] = None, tab: Optional[str] = None) -> Optional[Session]:
        """The most recently seen set-aside session of this window for `portal` (and, with
        `key`/`value`, for that client PAN / GSTIN), taken off the shelf - or None. With `tab`,
        only a session that is that tab's own, or one that never learnt its tabs; a client match
        (`key`) needs no tab - the same client's page belongs to its session in any tab."""
        aside = self._shelf.get(hwnd) or []
        fit = [x for x in aside
               if (not portal or not x.portal or x.portal == portal)
               and (key is None or (x.profile.get(key) or {}).get("value") == value)
               and (key is not None or not tab or not x.tabs or tab in x.tabs)]
        if not fit:
            return None
        pick = max(fit, key=lambda x: x.last_seen)
        aside.remove(pick)
        if not aside:
            self._shelf.pop(hwnd, None)
        self._trail(pick, "back", tab, key or "")
        return pick

    def _claim_tab(self, hwnd: int, s: Session, tab: Optional[str]) -> None:
        """`tab` is s's from now on - and no other session's of this window."""
        if not tab:
            return
        s.tabs.add(tab)
        self._retired_tabs.pop(tab, None)
        for x in self._shelf.get(hwnd) or []:
            if x is not s:
                x.tabs.discard(tab)

    def _retire_tabs(self, s: Session) -> None:
        """s has ended: a tab of its, seen again, is not joined to whatever session is in view -
        its page is from a login that is over (or from the one that replaced it), so it starts a
        session of its own that names its client only from what it shows."""
        for t in s.tabs:
            self._retired_tabs[t] = None
        while len(self._retired_tabs) > 500:
            self._retired_tabs.pop(next(iter(self._retired_tabs)))

    def _cut(self, session_id: str, why: str) -> None:
        """`session_id` crossed a boundary: a read it asked for and has not had back is never absorbed."""
        for o in self._owners.values():
            if o[0] == session_id and not o[2]:
                o[2] = why

    def _prune_closed(self, now: float) -> None:
        while self._closed:
            sid, (ended, _, _) = next(iter(self._closed.items()))
            if len(self._closed) <= CLOSED_RING and now - ended <= CLOSED_KEEP_SEC:
                break
            self._closed.pop(sid)

    def _observe_window(self, hwnd: int, portal: str, url: str, tab: Optional[str], now: float) -> Optional[Session]:
        if self._state_dirty:
            self._save_state()              # the last change before a still page must not wait for the next one
        self._apply_demotion_answers()
        self._end_idle(now)
        self._daily_health_check()
        self._prune_closed(now)
        s = self._sessions.get(hwnd)
        if tab and s is not None and s.tabs and tab not in s.tabs:
            # The window shows another of its tabs. Each tab has its own session (2026-10-13): a
            # page that names no client must never feed the client of the tab the user came from.
            owner = self._unshelve(hwnd, "", tab=tab) if any(
                tab in x.tabs for x in self._shelf.get(hwnd) or []) else None
            if owner is not None:
                self._shelve(hwnd, s)
                s = self._sessions[hwnd] = owner
                self._echo(f"[SGT] {self._tag} another tab: back on its own session")
            elif tab in self._retired_tabs:
                self._shelve(hwnd, s)
                self._sessions.pop(hwnd, None)
                s = None
                self._echo(f"[SGT] {self._tag} a tab whose session ended: a new session for it")
            else:
                # A tab never seen before: opened from the session in view, under the same login -
                # it joins that session (claimed below). Until its first page is read: one that
                # names another client makes it that client's tab (a tab open before Sera started).
                s.unread_tabs.add(tab)
                self._trail(s, "tab+", tab)
        if s is not None and portal and s.portal and portal != s.portal:
            # The window is on another portal (another tab). That is NOT a boundary: this
            # session is set aside, and the one the window had on that portal comes back. An
            # empty session (say, opened on the GST login page before going to the ITR portal)
            # just takes the portal it is on.
            back = self._unshelve(hwnd, portal, tab=tab)
            if s.has_content:
                self._shelve(hwnd, s)
                s = back
            elif back is not None:
                s = back
            else:
                s.portal = portal
            if s is None:
                self._sessions.pop(hwnd, None)
            else:
                self._sessions[hwnd] = s
        if s is not None and url and url != s.last_url:
            if LOGOUT_LINK.search(url):
                if len(s.timeline) < TIMELINE_CAP:
                    s.timeline.append(url)      # how the session ended belongs on its timeline
                self._sessions.pop(hwnd, None)
                self._end_one(s, "logout")
                s = None
            elif LOGIN_LINK.search(url) and s.has_content:
                self._sessions.pop(hwnd, None)
                self._end_one(s, "login page - next client")
                s = None
        if s is None and not (url and (LOGIN_LINK.search(url) or LOGOUT_LINK.search(url))):
            # A session of this window that was set aside for this portal comes back.
            s = self._unshelve(hwnd, portal, tab=tab)
            if s is not None:
                self._sessions[hwnd] = s
        if s is None:
            resumed = False
            picked = None
            cands = self._parked.get(hwnd) or []
            if cands:
                # A snapshot read back from disk is never matched by window number: the number may now
                # belong to another window. Only the client, named by a page, brings it back.
                fit = [c for c in cands
                       if not c[1].get(RESTORED)
                       and (not portal or not c[1].get("portal") or portal == c[1].get("portal"))
                       and (not tab or not c[1].get("tabs") or tab in c[1]["tabs"])]
                rest = [c for c in cands if c not in fit]
                if rest:
                    self._parked[hwnd] = rest
                else:
                    self._parked.pop(hwnd, None)
                if fit:
                    picked = max(fit, key=lambda c: float(c[1].get("last_seen") or c[0]))
            if picked is not None:
                parked_at, snap = picked
                age = now - parked_at
                gap = now - float(snap.get("last_seen") or parked_at)
                url_ok = not (LOGIN_LINK.search(url) or LOGOUT_LINK.search(url)) if url else True
                if 0 <= age <= RESUME_WINDOW_SEC and gap <= RESUME_WINDOW_SEC and url_ok:
                    resumed = True
                    s = session_from_json(snap)
                    old_id = snap.get("session_id", s.session_id)
                    s.session_id = uuid.uuid4().hex[:12]
                    s.started = now
                    s.last_seen = now
                    s.slots = []
                    s.draft.slot = None
                    if portal:
                        s.portal = portal
                    if s.sdis is not None:
                        if s.sdis.instances:
                            s.sdis.instances = [inst for inst in s.sdis.instances if not inst.sent_key]
                            s.sdis.current = {k: v for k, v in s.sdis.current.items() if v in s.sdis.instances}
                        if s.sdis.carrier and s.sdis.carrier.sent_key:
                            s.sdis.carrier = None
                    self._sessions[hwnd] = s
                    idle_sec = max(0.0, round(gap, 1))
                    s.ev.born, s.ev.parent = sgt_evidence.BORN_PARKED, str(old_id)
                    self._trail(s, "resumed", tab, str(old_id))
                    self._event(s, "session_resumed", from_session=old_id, idle_sec=idle_sec)
                    self._echo(f"[SGT] {self._tag} session resumed (from session {old_id}, idled {int(idle_sec)}s)")
            if not resumed:
                s = self._sessions[hwnd] = Session(portal=portal)
        if not s.portal:
            s.portal = portal
        if not s.browser:
            s.browser = browser_of(hwnd)
        s.last_seen = now
        if not s.started:
            s.started = now
        self._claim_tab(hwnd, s, tab)
        if not s.ev.trail:
            self._trail(s, s.ev.born, tab)      # a session just made here

        url_changed = url != s.last_url
        self._url_changed = url_changed         # for observe()'s change gate
        if url_changed:
            s.last_url = url
            s.visit += 1
            if url and (not s.timeline or s.timeline[-1] != url) and len(s.timeline) < TIMELINE_CAP:
                s.timeline.append(url)
        if LOGOUT_LINK.search(url):
            return None                         # nothing about a client lives on a logout page
        # Login pages ARE read: the ITR password page shows the PAN being logged into. The
        # login keyword above already closed the previous client's session, so what is read
        # here belongs to the new one.
        return s
