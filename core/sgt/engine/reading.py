"""
core/sgt/engine/reading.py - one page read into the session that asked for it
=============================================================================
The change gate (observe), a finished read from the capture loop (absorb), blind pages and OCR,
the source policy, and handing a frozen copy of the page to SDIS, SGT-I and SCC-U.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Dict, List, Optional

from core.sgt import evidence as sgt_evidence
from core.sgt.containers import split_hits
from core.sgt.specs.resolver import PageResult, resolve_page
from .policy import (BLIND_RETRIES, BLIND_RETRY, CLIENT_KEYS, EV_IDLE, LOGIN_LINK, LOGOUT_LINK, MIN_UIA_LINES,
                     PAN_TOKEN, REREAD_AFTER_SEC, RESULT_MAX_AGE_SEC, SOURCE_POLICY, frame_hash, is_blind)
from .session import Session, absorbs_after


class Reading:
    """Turns page reads into resolved pages for the session they belong to (part of SgtEngine)."""

    def _observe(self, hwnd: int, portal: str, url: str, frame: Any, ocr: Any, title: str = "",
                 tab: Optional[str] = None, read: Optional[Dict[str, Any]] = None) -> Optional[PageResult]:
        now = self._clock()
        s = self._observe_window(hwnd, portal, url, tab, now)
        if s is None:
            return None
        h = frame_hash(frame)
        changed = (read is not None or self._url_changed or h is None or h != s.last_hash
                   or (now - s.last_read) >= REREAD_AFTER_SEC)
        sgt_i = self._sgt_i    # one read: Settings may detach it from another thread mid-tick
        if not changed and sgt_i is not None:
            changed = sgt_i.wants_read(s.session_id)   # SGT-I may only ask for MORE reads
        scc = self._scc        # one read, as sgt_i
        if not changed and scc is not None:
            changed = scc.wants_read(s.session_id)     # SCC-U too: at most 30 s after a copy
        prev_hash, prev_read = s.last_hash, s.last_read
        s.last_hash = h
        if not changed:
            return None
        s.last_read = now

        t0 = time.perf_counter()
        res = read if read is not None else (self._read_uia(hwnd) or {})
        if res.get("busy"):
            # The previous UIA read is still running (a slow page, not a hung one): this page was
            # not read at all, so it must not count as read - the next tick asks again.
            s.last_hash, s.last_read = prev_hash, prev_read
            self._diag["busy"] += 1
            self._report(self._on_read, False, 0.0, "busy")
            return None
        out = self._absorb_lines(hwnd, s, portal, url, title, tab, list(res.get("lines") or []), "uia",
                                 docs=(read.get("docs") if read is not None else None), own_read=read is None,
                                 read_ms=float(res.get("ms") or 0.0) if read is not None else 0.0, t0=t0,
                                 frame=frame, ocr=ocr, now=now, h=h, blind_retries=BLIND_RETRIES, in_view=True)
        return out if isinstance(out, PageResult) else None

    def _absorb_result(self, r: Any, frame: Any, ocr: Any, now: Optional[float],
                       blind_retries: int) -> Any:
        t0 = time.perf_counter()
        when = self._clock()
        self._prune_closed(when)
        ctx = r.ctx
        owner = self._owners.pop(ctx.seq, None)
        why = ""
        if getattr(r, "error", ""):
            why = "error"
        elif r.torn:
            why = f"torn ({r.torn})"            # R1: both kinds; the loop asks again
        elif now is not None and now - float(ctx.asked_at or r.started_at or 0.0) > RESULT_MAX_AGE_SEC:
            why = "too old"
        elif owner is None:
            why = "owner unknown"
        elif owner[2]:
            why = f"its session crossed a boundary ({owner[2]})"
        hwnd = ctx.hwnd
        s, in_view, ended = None, False, ""
        if not why:
            s = self._sessions.get(hwnd)
            in_view = s is not None and s.session_id == owner[0]
            if not in_view:
                s = next((x for x in self._shelf.get(hwnd) or [] if x.session_id == owner[0]), None)
            if s is None and owner[0] in self._closed:
                _, s, ended = self._closed[owner[0]]
                if not absorbs_after(ended):
                    s, why = None, f"its session ended ({ended})"
            if s is None and not why:
                why = "its session is gone"
            elif s is not None and not why and r.tab_before and s.tabs and r.tab_before not in s.tabs:
                why = "the page read is another tab's"       # asked for one tab, the other was in front
        from core.capture import scope
        url = scope.sanitize_page_url(r.url) if r.url else ""
        if not why and LOGOUT_LINK.search(url):
            why = "logout page"
        if why:
            self._diag["dropped"] += 1
            if s is not None or owner is not None and owner[2]:
                self._echo(f"[SGT] {self._tag} a read was not absorbed: {why}")
            return None
        portal = scope.portal_for_url(url) or ctx.portal or s.portal
        if in_view:
            s.last_read = when                  # SGT's clock; the result's own times are the reader's
        asked = float(ctx.asked_at or r.started_at or 0.0)
        self._ev_lag = max(0.0, now - asked) if now is not None and asked else 0.0
        try:
            out = self._absorb_lines(hwnd, s, portal, url, ctx.title or "", r.tab_after, list(r.lines or []),
                                     r.source or "uia", docs=r.docs, own_read=False, read_ms=r.ms, t0=t0,
                                     frame=frame, ocr=ocr, now=when, h=None, blind_retries=blind_retries,
                                     in_view=in_view)
        finally:
            self._ev_lag = 0.0
        if ended and isinstance(out, PageResult):
            # Late, after an idle end or a closed window: the read was asked while this session was the
            # window's, and its tab and page did not change during it. Its rows go out as usual.
            self._close_draft(s, self.store.get().current_rules, f"late result after session end ({ended})", url)
            self._event(s, "late_absorbed_after_end", ended=ended, page=url, source=r.source or "uia")
        return out

    def _absorb_lines(self, hwnd: int, s: Session, portal: str, url: str, title: str, tab: Optional[str],
                      lines: List[str], source: str, docs: Optional[List[Any]], own_read: bool, read_ms: float,
                      t0: float, frame: Any, ocr: Any, now: float, h: Any, blind_retries: int,
                      in_view: bool) -> Any:
        """One page read of `s`, from its lines on. in_view: `s` is the window's session in view (not
        one set aside or ended since the read was asked: then it never switches the window's session)."""
        sgt_i = self._sgt_i    # one read: Settings may detach it from another thread mid-tick
        scc = self._scc        # likewise; also the host the page is handed to below
        self._diag["reads"] += 1
        self._diag["portal"] = portal or "?"
        if source == "uia" and is_blind(lines):
            if blind_retries < BLIND_RETRIES and s.good_lines.get(url, 0) > MIN_UIA_LINES:
                # This page read fine before: most likely a half-drawn page (Chromium after idle).
                self._report(self._on_read, False, read_ms, source)
                return BLIND_RETRY
            s.blind_pages += 1
            self._diag["blind"] += 1
            lines = []
            if ocr is not None and frame is not None:      # every portal, add-only (5.2, R6)
                lines = list((ocr.scan_image(frame, region_type="full") or {}).get("lines") or [])
                source = "ocr"
                s.ocr_reads += 1
                self._diag["ocr"] += 1
        elif source == "uia":
            s.good_lines.pop(url, None)
            s.good_lines[url] = len(lines)
            while len(s.good_lines) > 50:
                s.good_lines.pop(next(iter(s.good_lines)))
        elif source == "ocr":
            s.ocr_reads += 1
            self._diag["ocr"] += 1
        read_ms += (time.perf_counter() - t0) * 1000
        s.read_ms += read_ms
        s.reads += 1
        self._diag["last_lines"] = len(lines)
        self._diag["last_source"] = source
        self._report(self._on_read, bool(lines) and not is_blind(lines), read_ms, source)
        if not lines:
            return None
        self._policy = SOURCE_POLICY.get(source, SOURCE_POLICY["ocr"])
        self._ev_ctx = {"tab": tab, "source": source, "late": not in_view, "lag": self._ev_lag}
        try:
            return self._absorb_page(hwnd, s, portal, url, title, tab, lines, source, docs, own_read,
                                     now, h, in_view, sgt_i, scc)
        finally:
            self._policy = SOURCE_POLICY["uia"]
            self._ev_ctx = dict(EV_IDLE)

    def _absorb_page(self, hwnd: int, s: Session, portal: str, url: str, title: str, tab: Optional[str],
                     lines: List[str], source: str, docs: Optional[List[Any]], own_read: bool, now: float,
                     h: Any, in_view: bool, sgt_i: Any, scc: Any) -> Optional[PageResult]:
        policy = self._policy
        today = self._today()
        if not in_view:
            self._trail(s, "late", tab)
        # The capture loop's read carries the nodes of the same read: no second read for them.
        nodes = (list(docs or []) or None) if source == "uia" else None
        if self._recorder is not None:
            record_nodes = self._recorder.enabled and sgt_i is not None and sgt_i.active
            if own_read:
                nodes = self._nodes_for_recording(hwnd) if record_nodes else None
            self._recorder.record(session=s.session_id, portal=portal, url=url, title=title,
                                  source=source, lines=lines, ts=self._stamp(now), today=today,
                                  nodes=nodes if record_nodes else None)
        sdis = self._sdis
        try:
            if sdis is not None and sdis.enabled and in_view:
                sdis.offer(hwnd, s.session_id, portal, url, title)
        except Exception:
            pass                                # SDIS never disturbs SGT
        if self._stats is not None:
            self._stats.record_read(portal, source, today)

        registry = self.store.get()
        # Latched datapoints are not looked for again; "promote_longer" ones (the name) are.
        # The client's PAN / GSTIN are ALWAYS looked for: a second sighting confirms the
        # client, a different value means this window is now on another client.
        skip = (set(s.profile) & registry.latching_fields()) - set(CLIENT_KEYS)
        res = resolve_page(registry, lines, portal, url, today, skip_profile=skip, title=title)
        block = s.strict_block if s.strict else None
        if block and url == block.get("url"):
            # The page that started this session, read again: its clashing PAN/GSTIN still does
            # not name it (see Session.strict).
            for k, v in (block.get("values") or {}).items():
                if getattr(res.profile.get(k), "value", None) == v:
                    res.profile.pop(k, None)
        if self._stats is not None:
            self._stats.record_hits(portal, [h.spec for h in res.profile.values()]
                                    + [h.spec for h in res.current.values()]
                                    + [d.record for d in res.datasets], today)
        self._report(self._on_hits, s.session_id,
                     bool(s.profile or res.profile or res.current or res.datasets),
                     not LOGIN_LINK.search(url or ""))
        if policy["profile"] == "none":
            res.profile.clear()                 # OCR never names, splits or switches a client (5.2)
        names = any(getattr(res.profile.get(k), "value", None) for k in CLIENT_KEYS)
        if (in_view and names and not res.is_list and tab and tab in s.unread_tabs and len(s.tabs) > 1
                and not any((s.profile.get(k) or {}).get("value") for k in CLIENT_KEYS)):
            # A tab that joined this session before any of its tabs named a client now names one: it
            # may be another login (two tabs opened side by side), so it becomes that client's own
            # session rather than naming the tabs it joined. The session it leaves is kept, unnamed.
            s.tabs.discard(tab)
            s.unread_tabs.discard(tab)
            self._shelf.setdefault(hwnd, []).append(s)
            left = s
            self._trail(left, "split", tab)
            s = self._sessions[hwnd] = Session(portal=portal, last_url=url, last_hash=h, last_read=now,
                                                timeline=[url] if url else [])
            self._ev_born(s, sgt_evidence.BORN_SPLIT, left, tab)
            self._claim_tab(hwnd, s, tab)
        clash = None if res.is_list or not policy["identity_check"] else self._identity_conflict(s, res)
        if clash and not in_view:
            # A late read of a session set aside or ended names another client: nothing of it is taken.
            self._event(s, "page", change="late read names another client - not absorbed", field=clash, page=url)
            self._echo(f"[SGT] {self._tag} a late read shows another {clash.upper()} - not absorbed")
            return None
        if clash:
            # Another client's PAN/GSTIN: this window moved to someone else without passing a
            # login page (another tab). That is not a boundary either: the session is set aside
            # untouched, and the window's own session for that client comes back if it has one.
            # Otherwise a new one starts (strict): the clashing value is NOT taken from this page,
            # not even when it is read again - a stray GSTIN (a supplier in a table) must not name
            # it. Another page that shows it names it at once.
            seen = getattr(res.profile.get(clash), "value", None)
            first_read = bool(tab) and tab in s.unread_tabs
            s.unread_tabs.discard(tab)
            if not first_read:
                # The same tab now shows someone else: a read this session asked for and has not had
                # back may show them too (R1). A new tab's first page is only a tab switch.
                self._cut(s.session_id, "another client")
            left = s
            self._trail(left, "clash", tab, clash)
            self._shelve(hwnd, s)
            back = self._unshelve(hwnd, portal, key=clash, value=seen) if seen else None
            if back is not None:
                s = self._bring_back(hwnd, back, url, h, now)
            elif first_read:
                # The first page of a tab that had only joined the session in view: it is simply
                # that client's tab, named from this page like any session's first page.
                s = self._sessions[hwnd] = Session(portal=portal, last_url=url, last_hash=h, last_read=now,
                                                    timeline=[url] if url else [])
                self._ev_born(s, sgt_evidence.BORN_TAB, left, tab)
            else:
                # The re-reads of this page are blocked too, but only when the tab is known: then a
                # switch to another client's tab never comes this way (each tab has its session), so
                # this is a clash INSIDE one tab - most likely a stray row, the same evidence again
                # on every re-read. With no tab id, two clients' tabs are told apart only by this
                # path, and GST gives every client's welcome page the same link - blocking it would
                # leave the second client unnamed (test_two_gst_clients_in_two_tabs_never_end_or_mix).
                blocked = {k: getattr(res.profile.get(k), "value", None) for k in CLIENT_KEYS} if tab else {}
                s = self._sessions[hwnd] = Session(
                    portal=portal, strict=True, last_url=url, last_hash=h, last_read=now,
                    timeline=[url] if url else [],
                    strict_block={"url": url, "values": {k: v for k, v in blocked.items() if v}} if tab else {})
                self._ev_born(s, sgt_evidence.BORN_STRICT, left, tab)
                for k in CLIENT_KEYS:
                    res.profile.pop(k, None)
            self._claim_tab(hwnd, s, tab)
        elif not res.is_list and in_view:
            # A session that has no client yet (one born from a stray PAN/GSTIN, say) meets the
            # page of a client this window already has a session for: that session comes back.
            for k in CLIENT_KEYS:
                v = getattr(res.profile.get(k), "value", None)
                if v and not (s.profile.get(k) or {}).get("value"):
                    back = self._unshelve(hwnd, portal, key=k, value=v)
                    if back is not None:
                        self._shelve(hwnd, s)       # nothing to keep when it is still empty
                        s = self._bring_back(hwnd, back, url, h, now)
                        self._claim_tab(hwnd, s, tab)
                        break
        s.last_seen = now                   # a session made above was not stamped yet
        if not s.started:
            s.started = now
        if names or any((s.profile.get(k) or {}).get("value") for k in CLIENT_KEYS):
            s.unread_tabs.discard(tab)      # until a client is named, a joined tab may still be another login
        s.conflicts += len(res.conflicts)
        self._page_pans = {p for ln in lines for p in PAN_TOKEN.findall(ln or "")}
        self._foreign_pans = self._other_pans_on_page(s, res, lines)
        # SDIS field specs ("sdis.<field>") feed containers, never SGT's built-in dataset.
        res.current, sdis_hits = split_hits(res.current)
        if not res.is_list:
            # Before the datasets, so a confirmation page's ARN can be joined to the form and
            # period the earlier pages supplied.
            self._update_draft(s, res, url, source, registry.current_rules)
        self._absorb(s, res, url, source, registry)
        self._feed_containers(s, res, sdis_hits, url, source)
        if scc is not None:    # the one read above: Settings may detach it mid-tick
            self._hand_to_scc(scc, s, portal, url, title, source, lines, res, registry, now, today, hwnd)
        self._save_state()
        if self._sgt_i is not None:
            self._hand_to_sgt_i(s, portal, url, title, source, lines, res, registry, now, today, hwnd,
                                nodes if source == "uia" else None)
        return res

    def _nodes_for_recording(self, hwnd: int) -> Optional[List[List[Dict[str, Any]]]]:
        """The page's node dump for the corpus (sgt_corpus format v2) - an extra read, never
        allowed to disturb capture: any failure here is silent, same as a recorder failure."""
        try:
            return list((self._read_nodes(hwnd) or {}).get("docs") or []) or None
        except Exception:
            return None

    def _hand_to_sgt_i(self, s: Session, portal: str, url: str, title: str, source: str,
                       lines: List[str], res: PageResult, registry: Any, now: float, today: date,
                       hwnd: int = 0, nodes: Optional[List[Any]] = None) -> None:
        """SGT-I (core/sgt_i) gets a frozen copy of the page the Core has just finished with.
        It runs on its own thread; nothing it does can come back here (blueprint 14.2). `nodes`
        is the node tree the recorder already read for this page, if any - never an extra read."""
        try:
            if not self._sgt_i.active:
                return
            from core.sgt_i import make_observation
            obs = make_observation(
                session_id=s.session_id, portal=portal, url=url, title=title, source=source, lines=lines,
                result=res, profile={k: p.get("value", "") for k, p in s.profile.items()},
                draft=s.draft.values(registry.current_rules), ts=now, today=today.isoformat(),
                nodes=nodes)
            seen = getattr(self._sgt_i, "window_seen", None)
            if seen is not None:
                seen(hwnd, obs)     # step 9: what flashed in this (scope-gated) window since the last read
            self._sgt_i.submit(obs)
        except Exception as e:
            self._echo(f"[SGT-I] page not handed over: {e}")

    def _hand_to_scc(self, scc: Any, s: Session, portal: str, url: str, title: str, source: str,
                     lines: List[str], res: PageResult, registry: Any, now: float, today: date,
                     hwnd: int) -> None:
        """SCC-U (core/scc) gets a frozen copy of the page the Core has just absorbed, with the
        window and SGT session it belongs to (obs.session_id). Nothing comes back (G.1 rule 1)."""
        try:
            if not scc.active:
                return
            from core.sgt_i.observation import make_observation
            obs = make_observation(
                session_id=s.session_id, portal=portal, url=url, title=title, source=source, lines=lines,
                result=res, profile={k: p.get("value", "") for k, p in s.profile.items()},
                draft=s.draft.values(registry.current_rules), ts=now, today=today.isoformat())
            scc.submit(obs, hwnd)
        except Exception as e:
            self._echo(f"[SCC-U] page not handed over: {type(e).__name__}")
