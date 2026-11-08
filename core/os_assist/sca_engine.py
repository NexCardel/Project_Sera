"""
OS Assist SCA engine (blueprint §4.6): SCA in OS mode, without the extension.

clipboard_watch arms as today; with assist_engine == "os" the arm (no passwords, as the extension
keeps it) comes here instead of going to the browsers. While an arm is live a 0.6 s Qt timer sends
one read-only probe job to the OS Assist worker: the browser in front, its address-bar host (one of
the armed portals, on an approved domain), and whether one of its visible non-password boxes holds
one of the arm's ids. No arm -> no timer, no UIA at all.

A match runs sca_coordinator.js onCandidate, ported: a portal without a usable password is reported
once (handle_fill_result -> sca_notice); widget/assist mode or an unverified password shows the SCA
card (Inject = onWidgetFill, confirmed); otherwise handle_password_request is called in-process
with the dict the extension sends. A grant waits up to 90 s for a visible password box in the same
window (Income Tax: PAN page, Continue, password page), fills it with fill.fill (host checked right
before the write), reports with handle_fill_result and shows the chip. One fill per arm / window /
service (`busy`, hwnd in place of tab:frame). An OS SMTI/MECP card open on a window keeps SCA quiet
there (assist lock).

The extension acts on an `input` event; a probe sees a state, so a box is acted on when its match
appears (or the arm is new), not on every tick - a refused or failed fill is not retried until the
id is typed again. The password lives only in the pending fill (memory) and is dropped on fill,
timeout or disarm; it is never logged.
"""

import ctypes
import time
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

import sca_protocol
from core.os_assist import fill, locate, worker

TICK_MS = 600
FIELD_WAIT_MS = 90000          # Income Tax: PAN page, Continue, then the password page
MAX_PROBED_BOXES = 8
CONFIRM_NOTE = "This password has not been verified by SCC yet. Inject it only if you expect it to work."


# ----------------------------------------------------------------------------- rules (sca_coordinator.js)
def is_approved_domain(host: str, approved) -> bool:
    if not approved:
        return True                                   # desktop list not synced yet
    h = str(host or "").lower()
    for d in approved:
        dom = str(d or "").lower().strip(".")
        if dom and (h == dom or h.endswith("." + dom)):
            return True
    return False


def _service_host(s: dict) -> str:
    return s.get("host") or sca_protocol.service_host(s.get("url") or "")


def _host_ok(host: str, s: dict) -> bool:
    return sca_protocol.host_matches(host, _service_host(s))


def pick_service(arm: Optional[dict], host: str, approved) -> Optional[dict]:
    """Which armed portal may this page receive a password for? None = none."""
    if not arm or not isinstance(arm.get("services"), list):
        return None
    if not is_approved_domain(host, approved):
        return None
    return next((s for s in arm["services"] if s and s.get("has_password") and _host_ok(host, s)), None)


def blocked_service(arm: Optional[dict], host: str, approved) -> Optional[dict]:
    """One of the client's portals with no password SCA may give (said once, never silent)."""
    if not arm or not is_approved_domain(host, approved):
        return None
    return next((s for s in arm.get("services") or [] if s and not s.get("has_password") and _host_ok(host, s)),
                None)


def is_arm_live(arm: Optional[dict], now_ms: float) -> bool:
    return bool(arm and arm.get("arm_id") and isinstance(arm.get("expires_at"), (int, float))
                and arm["expires_at"] > now_ms)


def candidate_matches(arm: Optional[dict], candidate) -> bool:
    c = sca_protocol.normalize_uid(candidate)
    if not c or not arm or not isinstance(arm.get("candidate_uids"), list):
        return False
    return any(sca_protocol.normalize_uid(u) == c for u in arm["candidate_uids"])


def _bare(link: str) -> str:
    link = str(link or "").lower()
    return link[4:] if link.startswith("www.") else link


def _page_link(url: str) -> str:
    from core.sdis import keys
    url = str(url or "").strip()
    if url and "://" not in url:
        url = "https://" + url                       # the address bar may hide the scheme
    try:
        return keys.page_link(url) or ""
    except Exception:
        return ""


# ----------------------------------------------------------------------------- worker jobs (read-only, but fill_job)
def _foreground() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow() or 0) if hasattr(ctypes, "windll") else 0


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _box_value(el) -> str:
    """A non-password box's text (ValuePattern read). Never called for a password box."""
    uia = worker.current_uia()
    p = el.GetCurrentPattern(uia.c.UIA_ValuePatternId)
    vp = p.QueryInterface(uia.c.IUIAutomationValuePattern) if p else None
    return (vp.CurrentValue if vp is not None else "") or ""


def _id_boxes(hwnd: int) -> list:
    """Visible non-password Edits of the page, the focused one first (login-page probe, §4.6.3)."""
    uia = worker.current_uia()
    boxes = []
    for doc in locate.page_documents(hwnd):
        for el in _safe(lambda: uia.edits(doc), []):
            d = locate.describe(el)
            if locate.visible(d) and not d["password"]:
                boxes.append(el)
    focused = _safe(lambda: uia.uia.GetFocusedElement(), None)
    if focused is not None:
        for i, el in enumerate(boxes):
            if _safe(lambda: uia.uia.CompareElements(el, focused), False):
                boxes.insert(0, boxes.pop(i))
                break
    return boxes[:MAX_PROBED_BOXES]


def probe_job(services: List[dict], approved, candidate_uids: List[str], foreground=None) -> Optional[dict]:
    """§4.6 steps 1-3 on the worker. None, or {hwnd, host, page_link, matched}: `matched` is the arm id
    found in a box ('' when none). Box text never leaves this job; a password box is never read."""
    hwnd = int((foreground or _foreground)() or 0)
    if not hwnd or not locate._is_browser(hwnd):
        return None
    url = locate.read_url(hwnd)
    host = sca_protocol.service_host(url)
    if not host or not is_approved_domain(host, approved) or not any(_host_ok(host, s) for s in services):
        return None
    wanted = {sca_protocol.normalize_uid(u) for u in candidate_uids or []}
    wanted.discard("")
    matched = ""
    for el in _id_boxes(hwnd):
        if _safe(lambda: el.CurrentIsPassword, True):
            continue
        value = sca_protocol.normalize_uid(_safe(lambda: _box_value(el), ""))
        if value and value in wanted:
            matched = value
            break
    return {"hwnd": hwnd, "host": host, "page_link": _page_link(url), "matched": matched}


def host_job(hwnd: int) -> str:
    return sca_protocol.service_host(locate.read_url(hwnd))


def fill_job(hwnd: int, service: dict, password: str, selector: str, foreground=None):
    """The password box of that window, filled (fill.fill checks the host right before the write).
    None while there is nothing to fill yet: window not in front, another site, no visible box."""
    if int((foreground or _foreground)() or 0) != hwnd or not locate.host_matches_service(hwnd, service):
        return None
    _user, box = locate.find_fields(hwnd, None, selector or None)
    if box is None:
        return None
    return fill.fill(hwnd, box, password, True, service)


# ----------------------------------------------------------------------------- assist lock
def assist_hwnds() -> set:
    """Browser windows with an OS SMTI/MECP card open (launcher cards and SCC MECP cards)."""
    out = set()
    try:
        from core.os_assist import launcher
        for kind in launcher.KINDS:
            card = launcher.default().card(kind)
            if card is not None and not card.is_dismissed and card.browser_hwnd:
                out.add(int(card.browser_hwnd))
    except Exception:
        pass
    try:
        from ui.os_assist import mecp_card
        with mecp_card._cards_lock:
            cards = list(mecp_card._cards.values())
        out |= {int(c.browser_hwnd) for c in cards if not c.is_dismissed and c.browser_hwnd}
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------- cards
def _card_service(service: dict) -> dict:
    return {"login_page_link": service.get("url") or "", "name": service.get("name") or ""}


def _default_card(arm: dict, service: dict, hwnd: int, note: str):
    from ui.os_assist.sca_card import ScaCard
    card = ScaCard(arm.get("arm_id"), service.get("service_id"), arm.get("business_name") or "",
                   arm.get("owner_name") or "", service.get("name") or "Portal", note=note,
                   client_id=arm.get("client_id"))
    card.attach(hwnd, _card_service(service))
    card.present()
    return card


def _default_chip(arm: dict, service: dict, hwnd: int):
    from ui.os_assist.sca_chip import ScaChip
    chip = ScaChip(arm.get("business_name") or "", service.get("name") or "", arm.get("client_id_token") or "")
    chip.attach(hwnd, _card_service(service))
    chip.present()
    return chip


# ----------------------------------------------------------------------------- engine (Qt thread)
class ScaEngine(QObject):
    """Lives on the Qt thread (handle_password_request reads the vault there); UIA runs on the worker."""

    _deliver = Signal(object)

    def __init__(self, watcher, submit: Callable = None, probe: Callable = None, filler: Callable = None,
                 host_of: Callable = None, card_factory: Callable = None, chip_factory: Callable = None,
                 locked: Callable[[], set] = None, now: Callable[[], float] = None, parent=None):
        super().__init__(parent)
        self.watcher = watcher
        self._submit = submit or worker.submit
        self._probe = probe or probe_job
        self._fill = filler or fill_job
        self._host_of = host_of or host_job
        self._card_factory = card_factory or _default_card
        self._chip_factory = chip_factory or _default_chip
        self._locked = locked or assist_hwnds
        self._now = now or (lambda: time.time() * 1000.0)
        self._deliver.connect(lambda fn: fn())
        self.arm_data: Optional[dict] = None
        self.allowed: list = []
        self.busy: set = set()
        self._seen: Dict[int, str] = {}           # hwnd -> the arm id last seen in its boxes
        self._uid_page_done = False
        self._probe_job = None
        self._pending: Dict[str, dict] = {}       # busy key -> granted fill waiting for its box
        self.cards: Dict[int, object] = {}        # hwnd -> the SCA card shown there
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

    # -- arm ------------------------------------------------------------------------------------
    @property
    def armed(self) -> bool:
        return self.arm_data is not None

    def arm(self, arm: dict, allowed_domains=None) -> bool:
        """SCA_ARM_REQUEST's arm. Refused when expired or when it carries a password (v1)."""
        if not arm or not is_arm_live(arm, self._now()):
            return False
        if any(s and "password" in s for s in arm.get("services") or []):
            return False
        self._drop()
        self.arm_data = dict(arm, fills=0)
        self.allowed = list(allowed_domains or [])
        self._timer.start()
        return True

    def disarm(self, why: str = "IDLE") -> None:
        if self.arm_data is not None:
            print(f"[SCA] OS engine: arm cleared ({why})")
        self._drop()
        self.arm_data = None
        self._timer.stop()

    def _drop(self) -> None:
        self.busy.clear()
        self._seen.clear()
        self._uid_page_done = False
        self._probe_job = None
        for p in self._pending.values():
            p.pop("password", None)
            job = p.get("job")
            if job is not None:
                job.cancel()                           # still queued: nothing is typed after a disarm
        self._pending.clear()

    def _live(self) -> Optional[dict]:
        a = self.arm_data
        if a is None:
            return None
        if not getattr(self.watcher, "enabled", True):
            self.disarm("DISABLED")
            return None
        if not is_arm_live(a, self._now()):
            self.disarm("EXPIRED")
            return None
        return a

    def _later(self, fut, fn) -> None:
        """fn(result) on the Qt thread once the worker job is done; errors count as None."""
        def done(f):
            try:
                result = f.result()
            except Exception:
                result = None
            self._deliver.emit(lambda: fn(result))
        fut.add_done_callback(done)

    # -- tick -----------------------------------------------------------------------------------
    def tick(self) -> None:
        a = self._live()
        if a is None:
            return
        arm_id = a["arm_id"]
        for key, p in list(self._pending.items()):
            self._tick_fill(key, p)
        if self.arm_data is not a or self._probe_job is not None:
            return
        job = self._probe_job = self._submit(self._probe, list(a.get("services") or []), list(self.allowed),
                                             list(a.get("candidate_uids") or []))
        self._later(job, lambda r, j=job: self._on_probe(arm_id, j, r))

    def _on_probe(self, arm_id, job, result) -> None:
        if self._probe_job is job:
            self._probe_job = None
        a = self.arm_data
        if a is None or a["arm_id"] != arm_id or not result:
            return
        hwnd = int(result["hwnd"])
        matched = result.get("matched") or ""
        if hwnd in self._locked():
            self._seen[hwnd] = matched                 # quiet here, now and after the card goes
            return
        if not self._uid_page_done and a.get("trigger") == "source" and a.get("uid_page"):
            self._uid_page_done = True                 # actOnUidPage: once, on the window in front
            if _bare(result.get("page_link")) == _bare(a["uid_page"]):
                self._seen[hwnd] = matched
                self.on_candidate(a.get("matched_uid") or "", hwnd, result["host"])
                return
        if self._seen.get(hwnd, "") == matched:
            return
        self._seen[hwnd] = matched
        if matched:
            self.on_candidate(matched, hwnd, result["host"])

    # -- onCandidate ----------------------------------------------------------------------------
    def on_candidate(self, candidate: str, hwnd: int, host: str) -> str:
        a = self._live()
        if a is None:
            return "not-armed"
        if hwnd in self._locked():
            return "assist-open-in-tab"
        if not candidate_matches(a, candidate):
            return "not-this-client"
        service = pick_service(a, host, self.allowed)
        if service is None:
            blocked = blocked_service(a, host, self.allowed)
            if blocked is None:
                return "not-a-portal-of-this-client"
            bkey = f"{a['arm_id']}:{hwnd}:{blocked.get('service_id')}:blocked"
            if bkey not in self.busy:
                self.busy.add(bkey)
                self._report(a["arm_id"], blocked, "failed", blocked.get("blocked") or "no_password")
            return "blocked:" + (blocked.get("blocked") or "no_password")
        key = f"{a['arm_id']}:{hwnd}:{service.get('service_id')}"
        if key in self.busy:
            return "already-handled"
        self.busy.add(key)
        mode = a.get("sca_mode") or "autofill"
        if mode in ("widget", "assist") or service.get("needs_confirm"):
            try:
                self._show_card(a, service, hwnd, CONFIRM_NOTE if service.get("needs_confirm") else "")
            except Exception as e:
                print(f"[SCA] OS engine: card error {type(e).__name__}")
                self.busy.discard(key)
                return "widget-failed"
            return "widget-shown"
        return self._request(a, service, hwnd, host, sca_protocol.normalize_uid(candidate), key, False)

    def _show_card(self, a: dict, service: dict, hwnd: int, note: str) -> None:
        old = self.cards.pop(hwnd, None)
        if old is not None and not old.is_dismissed:
            old.dismiss("replaced")
        card = self._card_factory(a, service, hwnd, note)
        self.cards[hwnd] = card
        card.inject_requested.connect(
            lambda arm_id, service_id, h=hwnd, cid=a.get("client_id"): self.on_widget_fill(arm_id, service_id, h, cid))
        card.dismissed.connect(lambda _r, h=hwnd, c=card: self.cards.pop(h, None) if self.cards.get(h) is c else None)

    # -- onWidgetFill ---------------------------------------------------------------------------
    def on_widget_fill(self, arm_id, service_id, hwnd: int, client_id=None) -> str:
        """Inject on the card. A card from an earlier arm of the same client acts on the live arm;
        another client's card never gets this client's password."""
        a = self._live()
        if a is None:
            return "not-armed"
        service = next((s for s in a.get("services") or [] if s.get("service_id") == service_id), None)
        if service is None:
            return "no-service" if a["arm_id"] == arm_id else "not-armed"
        if a["arm_id"] != arm_id and not (client_id is not None and a.get("client_id") == client_id):
            return "not-armed"
        key = f"{a['arm_id']}:{hwnd}:{service_id}"
        live_id = a["arm_id"]

        def with_host(host):
            b = self.arm_data
            if b is None or b["arm_id"] != live_id:
                return
            if not host or not pick_service(b, host, self.allowed):
                print("[SCA] OS engine: Inject ignored (the window is not on that portal)")
                return
            self.busy.add(key)
            self._request(b, service, hwnd, host, b.get("matched_uid") or "", key, True)

        self._later(self._submit(self._host_of, hwnd), with_host)
        return "host-check"

    # -- password ---------------------------------------------------------------------------------
    def _request(self, a: dict, service: dict, hwnd: int, host: str, matched_uid: str, key: str,
                 confirmed: bool) -> str:
        msg = {"type": sca_protocol.MSG_SCA_PASSWORD_REQUEST, "request_id": sca_protocol.generate_id("req"),
               "arm_id": a["arm_id"], "service_id": service.get("service_id"), "page_host": host,
               "matched_uid": matched_uid, "confirmed": confirmed is True}
        try:
            reply = self.watcher.handle_password_request(msg) or {}
        except Exception as e:
            reply = {"type": sca_protocol.MSG_SCA_PASSWORD_DENIED, "reason": f"desktop error {type(e).__name__}"}
        if reply.get("type") != sca_protocol.MSG_SCA_PASSWORD_GRANT or not reply.get("password"):
            self.busy.discard(key)
            self._report(a["arm_id"], service, "failed", str(reply.get("reason") or "the desktop app refused"))
            return "denied"
        self._pending[key] = {"arm_id": a["arm_id"], "hwnd": hwnd, "service": service,
                              "password": reply["password"],
                              "selector": reply.get("password_selector") or service.get("password_selector") or "",
                              "deadline": self._now() + FIELD_WAIT_MS, "job": None}
        self._tick_fill(key, self._pending[key])
        return "password-requested"

    def _tick_fill(self, key: str, p: dict) -> None:
        if p.get("job") is not None:
            return
        if self._now() > p["deadline"]:
            self._end_fill(key, p, "failed", "no visible password field")
            return
        job = p["job"] = self._submit(self._fill, p["hwnd"], _card_service(p["service"]), p["password"], p["selector"])
        self._later(job, lambda r: self._on_fill(key, p, r))

    def _on_fill(self, key: str, p: dict, result) -> None:
        if self._pending.get(key) is not p:
            return                                     # disarmed or replaced meanwhile
        p["job"] = None
        if result is None:
            return                                     # no box yet: the next tick looks again
        if result.outcome == "filled":
            self._end_fill(key, p, "filled", "")
        else:
            self._end_fill(key, p, "failed", result.reason or result.outcome)

    def _end_fill(self, key: str, p: dict, outcome: str, reason: str) -> None:
        self._pending.pop(key, None)
        p.pop("password", None)
        a = self.arm_data
        if outcome != "filled":
            self.busy.discard(key)                     # staff may try again (type the id again)
        self._report(p["arm_id"], p["service"], outcome, reason)
        if outcome != "filled" or a is None or a["arm_id"] != p["arm_id"]:
            return
        try:
            self._chip_factory(a, p["service"], p["hwnd"])
        except Exception as e:
            print(f"[SCA] OS engine: chip error {type(e).__name__}")
        a["fills"] = a.get("fills", 0) + 1
        if a["fills"] >= (a.get("max_uses") or 1):
            self.disarm("CONSUMED")

    def _report(self, arm_id, service: dict, result: str, reason: str) -> None:
        try:
            self.watcher.handle_fill_result({"type": sca_protocol.MSG_SCA_FILL_RESULT, "arm_id": arm_id,
                                             "service_id": service.get("service_id"), "result": result,
                                             "reason": reason})
        except Exception as e:
            print(f"[SCA] OS engine: fill result error {type(e).__name__}")


# ----------------------------------------------------------------------------- one engine per watcher
_engines: Dict[int, ScaEngine] = {}


def for_watcher(watcher) -> ScaEngine:
    """The engine of that ClipboardWatchService (created on first use, on the Qt thread)."""
    eng = _engines.get(id(watcher))
    if eng is None or eng.watcher is not watcher:
        eng = _engines[id(watcher)] = ScaEngine(watcher)
    return eng


def disarm_all(why: str = "IDLE") -> None:
    for eng in list(_engines.values()):
        eng.disarm(why)
