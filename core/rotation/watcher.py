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

While a failure is pending, ``on_outcome(service_id, kind, hwnd, identity, profile)`` reports a retry rejection
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

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from core.scc.scc_rules import RuleSet, get_rules

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def pk_identity(value: str) -> str:
    """The PK-shaped identity a typed/read value stands for, upper-case: a PAN or TAN as is, the PAN
    inside a GSTIN; "" when the value is none of those."""
    v = str(value or "").strip().upper()
    if PAN_RE.match(v) or TAN_RE.match(v):
        return v
    if GSTIN_RE.match(v):
        return v[2:12]
    return ""


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


def same_site(url: str, link: str) -> bool:
    """True when `url` is on the same host as `link` (any page of it)."""
    cur, ref = _parts(url), _parts(link)
    if not cur or not ref:
        return False
    return cur[0] == ref[0] or cur[0].endswith("." + ref[0]) or ref[0].endswith("." + cur[0])


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
    identity: str = ""               # the PK-shaped ID read on the page ("" when none)
    user_id: str = ""                # the login ID read on the page ("" when none)
    unregistered: bool = False       # an ID that belongs to no client (new-client offer)


Lookup = Callable[[Dict[str, Any], Any], Tuple[Optional[int], str]]


class SckWatcher:
    """SccHost handler: route + rules in, one SckFailure out. Thread-safe; never raises."""

    name = "sck-watcher"

    def __init__(self, services: Callable[[], List[dict]],
                 resolve_client: Optional[Lookup] = None,
                 on_failure: Optional[Callable[[SckFailure], Any]] = None,
                 on_clear: Optional[Callable[[SckFailure], Any]] = None,
                 on_outcome: Optional[Callable[..., Any]] = None,
                 on_unregistered: Optional[Callable[[SckFailure], Any]] = None,
                 rules: Optional[Callable[[], RuleSet]] = None,
                 ttl_sec: float = PENDING_TTL_SEC,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._services = services
        self._resolve_client = resolve_client
        self._on_failure = on_failure
        self._on_clear = on_clear
        self._on_outcome = on_outcome
        self._on_unregistered = on_unregistered
        self._rules = rules or get_rules
        self._ttl = float(ttl_sec)
        self._now = monotonic
        self._lock = threading.Lock()
        # service id -> (fired at, the SckFailure it fired with). One card per service: the page
        # card is per service too (one host element), so a second client on the same service
        # would only replace the first one's card.
        self._pending: Dict[Tuple[int, int], Tuple[float, SckFailure]] = {}
        # (service id, outcome kind) -> monotonic time of the last on_outcome call. The card
        # needs to hear about a retry rejection and the dashboard transition while staff are
        # still working; the page keeps showing the same line, so it must be throttled.
        self._last_outcome: Dict[Tuple[int, int, str], float] = {}
        # service id -> (kind, line) of the page last read. A rejection line that is still on
        # screen is not a new rejection: the card would otherwise call the password it just filled
        # "rejected" while staff are still solving the CAPTCHA. The line is forgotten as soon as
        # the page shows something else (a submit reloads the page).
        self._last_page_outcome: Dict[Tuple[int, int], Tuple[str, str]] = {}
        # (service id, hwnd, identity) already offered as a new client; (service id, identity)
        # staff dismissed - not offered again for the run.
        self._offered: set = set()
        self._declined: set = set()

    # ── Read side (any thread) ───────────────────────────────────────────────────
    def pending(self) -> List[SckFailure]:
        """The failures whose card is still outstanding (for tests and the debug pane)."""
        with self._lock:
            return [f for _, f in self._pending.values()]

    def mark_resolved(self, service_id: Any, client_id: Any = None, hwnd: Any = None) -> None:
        """The password was saved (or staff dealt with it): the next failure may fire again.
        `hwnd` limits it to that window; None = every window of the service."""
        sid = _as_int(service_id)
        if sid is None:
            return
        with self._lock:
            for key in [k for k in self._pending if k[0] == sid and (hwnd is None or k[1] == int(hwnd or 0))]:
                self._pending.pop(key, None)

    def decline_offer(self, service_id: Any, identity: str) -> None:
        """Staff dismissed the new-client card: do not offer this ID again for the run."""
        sid = _as_int(service_id)
        ident = pk_identity(identity)
        if sid is not None and ident:
            with self._lock:
                self._declined.add((sid, ident))

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
        hwnd = int(hwnd or 0)
        key = (sid, hwnd)

        url = str(getattr(obs, "url", "") or "")
        if on_dashboard(url, svc.get("dashboard_page_link")):
            cleared = self._clear(key)
            if cleared is not None:
                self._send(self._on_clear, cleared)
            # The card may still be open after its pending failure was cleared or expired,
            # so the dashboard is also reported as an outcome rather than only through on_clear.
            self._notify(key, "worked", 10.0, obs)
            return "cleared" if cleared is not None else None

        login_link = (svc.get("login_page_link") or "").strip()
        if login_link and not on_site(url, login_link):
            # Off the login route. Most services have no dashboard link, so a successful login is
            # recognised here by the portal's own logged-in wording on any page of the same site.
            # Only "worked" counts off the login page: a failure is read on the login page alone.
            if not same_site(url, login_link):
                return None
            clean = [line for line in (getattr(obs, "lines", ()) or ()) if not _is_captcha_line(line)]
            out = self._rules().classify(clean, url, str(getattr(obs, "portal", "") or ""))
            if out is None or out.kind != "worked":
                return None
            cleared = self._clear(key)
            if cleared is not None:
                self._send(self._on_clear, cleared)
            self._notify(key, "worked", 10.0, obs)
            return "cleared" if cleared is not None else None

        # On the login page: keep reading it on every tick, so a refusal that appears and is
        # replaced quickly is still seen (SGT otherwise re-reads only every 15 s).
        if ctx is not None and hasattr(ctx, "read_harder"):
            try:
                ctx.read_harder()
            except Exception:
                pass

        pending = self._is_pending(key)
        clean = [line for line in (getattr(obs, "lines", ()) or ()) if not _is_captcha_line(line)]
        out = self._rules().classify(clean, url, str(getattr(obs, "portal", "") or ""))
        if out is None:
            with self._lock:
                self._last_page_outcome.pop(key, None)
            if pending:
                return "pending"
            return self._offer_unregistered(svc, sid, hwnd, obs, url)

        page_key = (out.kind, out.line)
        with self._lock:
            same_page = self._last_page_outcome.get(key) == page_key
            self._last_page_outcome[key] = page_key

        if out.kind == "worked":
            # The portal's own wording says the login succeeded (some portals keep the login
            # route and swap the form for a welcome page). The failure is resolved and the
            # desktop saves the candidate the card filled.
            cleared = self._clear(key)
            if cleared is not None:
                self._send(self._on_clear, cleared)
            self._notify(key, "worked", 10.0, obs)
            return "cleared" if cleared is not None else None

        if pending:
            # The card already exists; it still needs to know a retry was rejected - but
            # only when the page actually changed, not while the old error line is still shown.
            if out.kind in ("wrong_password", "locked") and not same_page:
                self._notify(key, out.kind, 5.0, obs)
            return "pending"

        if out.kind == "neutral":
            # A step that says nothing about the password (the Income Tax password page always
            # shows its "Secure Access Message"): the same as no verdict for the new-client offer.
            return self._offer_unregistered(svc, sid, hwnd, obs, url)

        if out.kind != "wrong_password":
            return None

        client_id, client_name = self._identify(svc, obs)
        failure = self._failure(svc, sid, client_id, client_name, hwnd, obs, url, out.rule, out.line,
                                unregistered=client_id is None)
        if client_id is None and failure.identity:
            if (sid, failure.identity) in self._declined:
                return "no_client"
            with self._lock:
                self._offered.add((sid, hwnd, failure.identity))
        with self._lock:
            self._pending[key] = (self._now(), failure)
        try:
            if self._on_failure is not None:
                self._on_failure(failure)
        except Exception:
            with self._lock:
                self._pending.pop(key, None)
            return None
        return "wrong_password" if client_id is not None else "no_client"

    def _failure(self, svc, sid, client_id, client_name, hwnd, obs, url, rule, line,
                 unregistered=False) -> SckFailure:
        portal = str(getattr(obs, "portal", "") or svc.get("name") or "").strip()
        pan = profile_value(obs, "pan")
        if not pan:
            gstin = profile_value(obs, "gstin")
            pan = gstin[2:12] if len(gstin) == 15 else ""
        return SckFailure(service=dict(svc), service_id=sid, client_id=client_id,
                          client_name=client_name, portal=portal, hwnd=int(hwnd or 0),
                          url=url, rule=rule, line=line, pan=pan, identity=page_identity(obs),
                          user_id=page_user_id(obs), unregistered=unregistered)

    def _offer_unregistered(self, svc, sid: int, hwnd: int, obs: Any, url: str) -> Optional[str]:
        """On a login page with no verdict: an ID that is PK-shaped and belongs to no client is
        offered once per (service, window, ID) - and not again once staff dismissed it."""
        if self._on_unregistered is None:
            return None
        ident = page_identity(obs)
        if not ident:
            return None
        if (sid, ident) in self._declined:
            return None
        with self._lock:
            if (sid, hwnd, ident) in self._offered:
                return None
        client_id, _name = self._identify(svc, obs)
        if client_id is not None:
            return None
        with self._lock:
            if (sid, hwnd, ident) in self._offered:
                return None
            self._offered.add((sid, hwnd, ident))
        failure = self._failure(svc, sid, None, "", hwnd, obs, url, "unregistered", "", unregistered=True)
        with self._lock:
            self._pending[(sid, hwnd)] = (self._now(), failure)
        try:
            self._on_unregistered(failure)
        except Exception:
            with self._lock:
                self._pending.pop((sid, hwnd), None)
            return None
        return "unregistered"

    # ── Internals ────────────────────────────────────────────────────────────────
    def _is_pending(self, key: Tuple[int, int]) -> bool:
        with self._lock:
            hit = self._pending.get(key)
            if hit is None:
                return False
            if self._now() - hit[0] <= self._ttl:
                return True
            self._pending.pop(key, None)
            return False

    def _clear(self, key: Tuple[int, int]) -> Optional[SckFailure]:
        with self._lock:
            hit = self._pending.pop(key, None)
        return hit[1] if hit else None

    def _notify(self, key: Tuple[int, int], kind: str, throttle_sec: float, obs: Any = None) -> None:
        """Send one page outcome to the desktop (for the card), at most once per `throttle_sec`
        per kind and window. Carries the window, the identity read on the page and the page's
        profile (a name, for a new client): metadata only, never a password."""
        if self._on_outcome is None:
            return
        sid, hwnd = key
        now = self._now()
        tkey = (sid, hwnd, kind)
        with self._lock:
            last = self._last_outcome.get(tkey)
            if last is not None and now - last < throttle_sec:
                return
            self._last_outcome[tkey] = now
        try:
            self._on_outcome(sid, kind, hwnd, page_identity(obs) if obs is not None else "",
                             profile_dict(obs) if obs is not None else {})
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
    if isinstance(hit, Mapping):         # SGT's observations are frozen (MappingProxyType)
        val = str(hit.get("value") or "").strip().upper()
        if val:
            return val
    profile = getattr(obs, "profile", None) or {}
    try:
        return str(profile.get(key) or "").strip().upper()
    except AttributeError:
        return ""


def page_identity(obs: Any) -> str:
    """The PK-shaped identity the page shows: the PAN, then the PAN inside a GSTIN, then a PAN /
    GSTIN / TAN typed as the login ID. "" when there is none."""
    for key in ("pan", "gstin", "tan", "username", "userid"):
        ident = pk_identity(profile_value(obs, key))
        if ident:
            return ident
    return ""


def page_user_id(obs: Any) -> str:
    return profile_value(obs, "username") or profile_value(obs, "userid")


def profile_dict(obs: Any) -> Dict[str, str]:
    """Everything SGT read as {key: value} (strings), the page result first."""
    out: Dict[str, str] = {}
    profile = getattr(obs, "profile", None) or {}
    try:
        for k, v in profile.items():
            if v not in (None, ""):
                out[str(k)] = str(v)
    except AttributeError:
        pass
    result = getattr(obs, "result", None) or {}
    try:
        for k, hit in (result.get("profile") or {}).items():
            val = hit.get("value") if isinstance(hit, Mapping) else hit
            if val not in (None, ""):
                out[str(k)] = str(val)
    except AttributeError:
        pass
    return out


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
