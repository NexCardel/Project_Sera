"""
core/rotation/watcher.py - SCK's failed-login watcher
======================================================
Sera Credential Keeper's detection half. A handler for ``SccHost`` (core/scc/host.py), so it runs
on SCC-U's own thread on a read-only ``Observation`` and never touches SGT.

Why this exists apart from ``core/scc/outcome.py``
--------------------------------------------------
``OutcomeReader`` follows an SCC *attempt*: Income Tax only, bound to one SGT session, skipped for
clients already marked verified. A password changed on the portal breaks every one of those
assumptions, so the failure that matters is often never read. This watcher is portal-agnostic and
keyed by ``(service, portal)`` instead:

  * any registered service, not just Income Tax;
  * verified clients included - a stale "verified" password is exactly what fails;
  * the login page, not a session id, decides where a failure can be seen, so a logout/login in
    the middle of a live password change does not hide it;
  * the service's dashboard (welcome) link decides when the card has done its job.

What it reports
---------------
``wrong_password`` for a registered service -> ``on_failure(SckFailure)`` once per service until
the dashboard page is reached, the portal itself says the login worked, the password is saved
(``mark_resolved``) or ``PENDING_TTL_SEC`` passes. The card is persistent: no countdown, no
repeated firing.

While a failure is pending, ``on_outcome(service_id, kind)`` reports a retry rejection
(``wrong_password`` / ``locked``) and the success transition (``worked``) to the desktop
card. A ``worked`` on a dashboard page is reported even with nothing pending, because the card
can stay open after its pending failure was cleared or expired.

Reads around the login page
---------------------------
While an observation is the service's login page, ``ctx.read_harder()`` keeps SGT reading that
page on every tick (SccHost's read window). Without it SGT re-reads a page only when its frame
hash changes or every ``REREAD_AFTER_SEC`` (15 s), so a refusal that flashes and disappears is
invisible. This is the whole of SCK's "read around the submit" fix.

Captcha wording is stripped before classification: a bad CAPTCHA is not a bad password.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from core.scc.scc_rules import RuleSet, get_rules

PENDING_TTL_SEC = 900.0     # 15 min: a card nobody resolved stops blocking new ones

# Lines that look like a password failure but are a CAPTCHA error.
_CAPTCHA_FRAGMENTS = (
    "invalid captcha",
    "enter valid captcha",
    "captcha is invalid",
    "please enter captcha",
    "captcha code is invalid",
)


def _is_captcha_line(line: str) -> bool:
    lower = str(line or "").strip().lower()
    return any(frag in lower for frag in _CAPTCHA_FRAGMENTS)


def _parts(url: str) -> Optional[Tuple[str, str]]:
    """(host, route): host without a leading ``www.``, route = path + '#fragment' without a
    trailing slash. The fragment is part of the route because the Income Tax portal is a
    hash-router (#/login/password), where the path alone is the same on every page."""
    try:
        u = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    route = (u.path or "").rstrip("/")
    frag = (u.fragment or "").strip("/")
    if frag:
        route += "#" + frag
    return host, route


def _under(route: str, base: str) -> bool:
    """True when `route` is `base` or below it (a page of that flow). An empty base is every page."""
    if not base:
        return True
    return route == base or route.startswith(base + "/")


def on_site(url: str, link: str) -> bool:
    """True when `url` is on the page (or below it) `link` points at."""
    cur, ref = _parts(url), _parts(link)
    if not cur or not ref:
        return False
    host_match = cur[0] == ref[0] or cur[0].endswith("." + ref[0]) or ref[0].endswith("." + cur[0])
    return host_match and _under(cur[1], ref[1])


def on_dashboard(url: str, link: str) -> bool:
    """True when `url` is the service's dashboard/welcome page (or a page below it)."""
    return bool(link) and on_site(url, link)


@dataclass(frozen=True)
class SckFailure:
    """One failed login worth asking staff for the new password. Never a password itself."""
    service: Dict[str, Any]           # the service row (id, name, links, password column)
    service_id: Optional[int]
    client_id: Optional[int]         # None = the page did not name a client Sera knows
    client_name: str
    portal: str
    hwnd: int
    url: str
    rule: str
    line: str
    pan: str = ""                    # the PAN/GSTIN read on the page, when there is one


Lookup = Callable[[Dict[str, Any], Any], Tuple[Optional[int], str]]


class SckWatcher:
    """SccHost handler: route + rules in, one SckFailure out. Thread-safe; never raises."""

    name = "sck-watcher"

    def __init__(self, services: Callable[[], List[dict]],
                 resolve_client: Optional[Lookup] = None,
                 on_failure: Optional[Callable[[SckFailure], Any]] = None,
                 on_clear: Optional[Callable[[SckFailure], Any]] = None,
                 on_outcome: Optional[Callable[[int, str], Any]] = None,
                 rules: Optional[Callable[[], RuleSet]] = None,
                 ttl_sec: float = PENDING_TTL_SEC,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._services = services
        self._resolve_client = resolve_client
        self._on_failure = on_failure
        self._on_clear = on_clear
        self._on_outcome = on_outcome
        self._rules = rules or get_rules
        self._ttl = float(ttl_sec)
        self._now = monotonic
        self._lock = threading.Lock()
        # service id -> (fired at, the SckFailure it fired with). One card per service: the page
        # card is per service too (one host element), so a second client on the same service
        # would only replace the first one's card.
        self._pending: Dict[int, Tuple[float, SckFailure]] = {}
        # (service id, outcome kind) -> monotonic time of the last on_outcome call. The card
        # needs to hear about a retry rejection and the dashboard transition while staff are
        # still working; the page keeps showing the same line, so it must be throttled.
        self._last_outcome: Dict[Tuple[int, str], float] = {}
        # service id -> (kind, line) of the page last read. A rejection line that is still on
        # screen is not a new rejection: the card would otherwise call the password it just filled
        # "rejected" while staff are still solving the CAPTCHA. The line is forgotten as soon as
        # the page shows something else (a submit reloads the page).
        self._last_page_outcome: Dict[int, Tuple[str, str]] = {}

    # ── Read side (any thread) ───────────────────────────────────────────────────
    def pending(self) -> List[SckFailure]:
        """The failures whose card is still outstanding (for tests and the debug pane)."""
        with self._lock:
            return [f for _, f in self._pending.values()]

    def mark_resolved(self, service_id: Any, client_id: Any = None) -> None:
        """The password was saved (or staff dealt with it): the next failure may fire again."""
        sid = _as_int(service_id)
        if sid is None:
            return
        with self._lock:
            self._pending.pop(sid, None)

    def reset_outcome_throttle(self, service_id: Any) -> None:
        """A new attempt started for this service (staff pressed Fill & Test). Clear the time
        throttle of the previous attempt so a genuine new rejection is not delayed - an unchanged
        page line is still suppressed by the page-line check in _observe()."""
        sid = _as_int(service_id)
        if sid is None:
            return
        with self._lock:
            for key in [k for k in self._last_outcome if k[0] == sid]:
                self._last_outcome.pop(key, None)

    # ── Handler (SCC-U's thread) ─────────────────────────────────────────────────
    def observe(self, obs: Any, hwnd: int, ctx: Any = None) -> Optional[str]:
        """Returns what it did, for tests: wrong_password / pending / cleared / no_service /
        no_client / (None = nothing to do)."""
        try:
            return self._observe(obs, hwnd, ctx)
        except Exception:
            # SccHost trips on an exception; detection must not switch the whole thing off.
            return None

    def _observe(self, obs: Any, hwnd: int, ctx: Any = None) -> Optional[str]:
        services = list(self._services() or ())
        if not services:
            return None
        svc = service_for(services, obs)
        if svc is None:
            return "no_service"
        sid = _as_int(svc.get("id"))
        if sid is None:
            return "no_service"

        url = str(getattr(obs, "url", "") or "")
        if on_dashboard(url, svc.get("dashboard_page_link")):
            cleared = self._clear(sid)
            if cleared is not None:
                self._send(self._on_clear, cleared)
            # The card may still be open after its pending failure was cleared or expired,
            # so the dashboard is also reported as an outcome rather than only through on_clear.
            self._notify(sid, "worked", throttle_sec=10.0)
            return "cleared" if cleared is not None else None

        login_link = (svc.get("login_page_link") or "").strip()
        if login_link and not on_site(url, login_link):
            return None

        # On the login page: keep reading it on every tick, so a refusal that appears and is
        # replaced quickly is still seen (SGT otherwise re-reads only every 15 s).
        if ctx is not None and hasattr(ctx, "read_harder"):
            try:
                ctx.read_harder()
            except Exception:
                pass

        pending = self._is_pending(sid)
        clean = [line for line in (getattr(obs, "lines", ()) or ()) if not _is_captcha_line(line)]
        out = self._rules().classify(clean, url, str(getattr(obs, "portal", "") or ""))
        if out is None:
            with self._lock:
                self._last_page_outcome.pop(sid, None)
            return "pending" if pending else None

        page_key = (out.kind, out.line)
        with self._lock:
            same_page = self._last_page_outcome.get(sid) == page_key
            self._last_page_outcome[sid] = page_key

        if out.kind == "worked":
            # The portal's own wording says the login succeeded (some portals keep the login
            # route and swap the form for a welcome page). The failure is resolved and the
            # desktop saves the candidate the card filled.
            cleared = self._clear(sid)
            if cleared is not None:
                self._send(self._on_clear, cleared)
            self._notify(sid, "worked", throttle_sec=10.0)
            return "cleared" if cleared is not None else None

        if pending:
            # The card already exists; it still needs to know a retry was rejected - but
            # only when the page actually changed, not while the old error line is still shown.
            if out.kind in ("wrong_password", "locked") and not same_page:
                self._notify(sid, out.kind, throttle_sec=5.0)
            return "pending"

        if out.kind != "wrong_password":
            return None

        client_id, client_name = self._identify(svc, obs)
        portal = str(getattr(obs, "portal", "") or svc.get("name") or "").strip()
        pan = profile_value(obs, "pan")
        if not pan:
            gstin = profile_value(obs, "gstin")
            pan = gstin[2:12] if len(gstin) == 15 else ""
        failure = SckFailure(service=dict(svc), service_id=sid, client_id=client_id,
                            client_name=client_name, portal=portal, hwnd=int(hwnd or 0),
                            url=url, rule=out.rule, line=out.line, pan=pan)
        with self._lock:
            self._pending[sid] = (self._now(), failure)
        try:
            if self._on_failure is not None:
                self._on_failure(failure)
        except Exception:
            with self._lock:
                self._pending.pop(sid, None)
            return None
        return "wrong_password" if client_id is not None else "no_client"

    # ── Internals ────────────────────────────────────────────────────────────────
    def _is_pending(self, sid: int) -> bool:
        with self._lock:
            hit = self._pending.get(sid)
            if hit is None:
                return False
            if self._now() - hit[0] <= self._ttl:
                return True
            self._pending.pop(sid, None)
            return False

    def _clear(self, sid: int) -> Optional[SckFailure]:
        with self._lock:
            hit = self._pending.pop(sid, None)
        return hit[1] if hit else None

    def _notify(self, sid: int, kind: str, throttle_sec: float) -> None:
        """Send one page outcome to the desktop (for the card), at most once per `throttle_sec`
        per kind."""
        if self._on_outcome is None:
            return
        now = self._now()
        key = (sid, kind)
        with self._lock:
            last = self._last_outcome.get(key)
            if last is not None and now - last < throttle_sec:
                return
            self._last_outcome[key] = now
        try:
            self._on_outcome(sid, kind)
        except Exception:
            pass

    def _identify(self, svc: Dict[str, Any], obs: Any) -> Tuple[Optional[int], str]:
        if self._resolve_client is None:
            return None, ""
        try:
            got = self._resolve_client(svc, obs)
        except Exception:
            return None, ""
        if not isinstance(got, (tuple, list)) or not got:
            return None, ""
        cid = _as_int(got[0])
        name = str(got[1] or "").strip() if len(got) > 1 else ""
        return cid, name

    def _send(self, cb: Optional[Callable[[SckFailure], Any]], failure: SckFailure) -> None:
        if cb is None:
            return
        try:
            cb(failure)
        except Exception:
            pass


# ── Route helpers ─────────────────────────────────────────────────────────────────
def service_for(services: List[dict], obs: Any) -> Optional[dict]:
    """The registered service this observation belongs to: by page first, then by portal name."""
    url = str(getattr(obs, "url", "") or "")
    for svc in services:
        if on_site(url, svc.get("login_page_link")) or on_site(url, svc.get("dashboard_page_link")):
            return svc
    portal = str(getattr(obs, "portal", "") or "").strip().lower()
    if not portal:
        return None
    for svc in services:
        name = str(svc.get("name") or "").strip().lower()
        if name and (name == portal or name.startswith(portal) or portal in name):
            return svc
    # "Income Tax" / "ITR" / "eportal" name the same portal
    aliases = {"itr": "income tax", "income tax": "itr", "eportal": "income tax"}
    want = aliases.get(portal, "")
    if want:
        for svc in services:
            name = str(svc.get("name") or "").strip().lower()
            if want in name:
                return svc
    return None


def profile_value(obs: Any, key: str) -> str:
    """A client key SGT read on the page (PAN / GSTIN), from the page result or the session."""
    result = getattr(obs, "result", None) or {}
    try:
        hit = (result.get("profile") or {}).get(key) or {}
    except AttributeError:
        hit = {}
    if isinstance(hit, dict):
        val = str(hit.get("value") or "").strip().upper()
        if val:
            return val
    profile = getattr(obs, "profile", None) or {}
    try:
        return str(profile.get(key) or "").strip().upper()
    except AttributeError:
        return ""


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
