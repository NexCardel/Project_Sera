"""
clipboard_watch.py
------------------
Desktop side of Sera Clipboard Assist (SCA), protocol v2 - see sca_protocol.py.

Copying a client's PAN / GSTIN / login id (from Excel, or any exact match) ARMS SCA: the
extension learns which client and portals, but receives no password. Only when that id is
entered on the matching portal's login page does the extension ask for one password, and
handle_password_request() decides - arm live, uses left, page host belongs to that portal -
reading the password from the vault at that moment.
"""

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication

import automation
import sca_protocol

# Precompiled regexes for common UID formats
RE_PAN = sca_protocol.RE_PAN
RE_GSTIN = sca_protocol.RE_GSTIN
# Excel clipboard MIME format markers
EXCEL_MIME_MARKERS = {
    "csv",
    "biff12",
    "biff8",
    "biff5",
    "xml spreadsheet",
    "application/x-qt-windows-mime;value=\"csv\"",
    "application/x-qt-windows-mime;value=\"biff12\"",
    "application/x-qt-windows-mime;value=\"biff8\"",
    "application/x-qt-windows-mime;value=\"xml spreadsheet\"",
    "application/x-qt-windows-mime;value=\"link\"",
    "link",
}
SERA_CLIPBOARD_MARKER = "application/x-sera-uid"
IDENTITY_LABEL_TERMS = ("pan", "gstin", "user id", "userid", "username", "login id", "login user")

REPEAT_COPY_S = 2.0          # Excel fires dataChanged more than once per copy
UNUSED_ARMS_BEFORE_PAUSE = 5  # guardrail: a client armed this often without a fill is paused

_suppressed_until: Dict[int, float] = {}   # client_id -> time.time() deadline


def suppress_client(client_id, seconds: float = 300):
    """Client Detail launched MECP/SMTI for this client: copying its ids from that card must not
    also arm SCA (#15). Copies of this client's ids are ignored until the time runs out."""
    try:
        cid = int(client_id)
    except (TypeError, ValueError):
        return
    _suppressed_until[cid] = time.time() + max(0.0, float(seconds))


def release_client(client_id) -> None:
    """The SCC attempt that suppressed this client has ended: its ids arm SCA again."""
    try:
        _suppressed_until.pop(int(client_id), None)
    except (TypeError, ValueError):
        pass


def is_suppressed(client_id) -> bool:
    until = _suppressed_until.get(client_id)
    if until is None:
        return False
    if time.time() >= until:
        _suppressed_until.pop(client_id, None)
        return False
    return True


def is_allowed_clipboard_identifier(value: str) -> bool:
    """Shape gate for PAN/GSTIN and configured service user IDs."""
    return sca_protocol.looks_like_uid(value)


def is_excel_source(mime_data) -> bool:
    """Checks for Excel data or a UID explicitly copied from Sera."""
    if not mime_data:
        return False
    for fmt in mime_data.formats():
        fmt_lower = fmt.lower()
        if SERA_CLIPBOARD_MARKER in fmt_lower or any(marker in fmt_lower for marker in EXCEL_MIME_MARKERS):
            return True
    return False


@dataclass
class Arm:
    arm_id: str
    client_id: int
    client_token: str
    matched_uid: str
    services: Dict[Any, dict]          # service_id -> {"url", "name", "password_selector"}
    expires_at: float                  # time.time()
    uses_remaining: int
    grants: int = 0
    fills: List[str] = field(default_factory=list)
    max_uses: int = 1
    sca_mode: str = "autofill"
    trigger: str = "clipboard"


class ClipboardWatchService(QObject):
    """Watches the clipboard for client ids and runs the desktop half of SCA."""
    sca_armed = Signal(int, str, list)  # client_id, client_token, services (no passwords)
    sca_notice = Signal(str, str)       # message for staff, level ("info" | "warning")
    source_uid = Signal(object, str)    # service_id, UID read from its UID page's picked node (watcher thread)
    source_warn = Signal(object, str)   # service_id, "missing" | "ambiguous" (watcher thread)
    source_notice = Signal(str, str)    # a UID-page notice, level: shown ON TOP of the browser (main.py)

    def __init__(self, db, parent=None, actor: str = "Staff"):
        super().__init__(parent)
        self.db = db
        self.actor = actor or "Staff"
        self.enabled = True
        self._uid_index: Dict[str, int] = {}
        self._identity_column_ids: set = set()
        self._index_generation = None
        self._arm: Optional[Arm] = None
        # arm_id of a copy replaced by a newer arm of the SAME client -> (client_id, live arm_id, until).
        # The clipboard arm and the UID-page arm follow each other within a second; a click on the
        # first card in that gap must not be refused. See _live_arm_for.
        self._superseded: dict = {}
        self.scc_ledger = None     # SccCard.note_clipboard, set by main: SCC attempt copy ledger
        self._last_copy = ("", 0.0)
        self._unused_streak: Dict[int, int] = {}
        self._paused_clients: set = set()
        self._uid_sources: list = []       # [{id, name, uid_page_link, uid_node}] (Settings -> SCA)
        self._source_watcher = None

        self.refresh_index()
        self.refresh_sources()
        self._connect_clipboard()
        # Proactively check clipboard on startup in case a UID was already copied
        QTimer.singleShot(600, self._on_clipboard_changed)

    # ------------------------------------------------------------------ index
    def _connect_clipboard(self):
        app = QApplication.instance()
        if app:
            app.clipboard().dataChanged.connect(self._on_clipboard_changed)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self._arm = None
            engine = sys.modules.get("core.os_assist.sca_engine")     # loaded only once OS mode armed
            if engine is not None:
                engine.disarm_all("DISABLED")

    # ------------------------------------------------------------------ SCK
    def client_for_arm(self, arm_id: str) -> Optional[int]:
        """The client the live SCA arm belongs to, when `arm_id` is that arm. Read-only; used by
        SCK to name a failed login the portal page itself did not."""
        arm = self._live_arm_for(arm_id)
        return arm.client_id if arm is not None else None

    SUPERSEDED_ARM_GRACE_S = 30.0

    def _live_arm_for(self, arm_id) -> Optional["Arm"]:
        """The live arm when `arm_id` is it, or is a copy it replaced for the same client moments ago."""
        arm = self._arm
        if arm is None or not arm_id:
            return None
        if str(arm.arm_id) == str(arm_id):
            return arm
        alias = self._superseded.get(str(arm_id))
        if alias and alias[0] == arm.client_id and alias[1] == arm.arm_id and time.time() < alias[2]:
            return arm
        return None

    def client_for_portal(self, service: dict, portal: str = "") -> Optional[int]:
        """The client SCA is working for on this portal, when that is unambiguous: the live arm
        filled this portal, or the arm covers exactly one service. Read-only."""
        arm = self._arm
        if arm is None or time.time() > arm.expires_at:
            return None
        svc_id = service.get("id") if isinstance(service, dict) else service
        names = {str(portal or "").strip().lower()}
        if isinstance(service, dict):
            names.add(str(service.get("name") or "").strip().lower())
        names.discard("")
        if any(str(f).strip().lower() in names for f in arm.fills):
            return arm.client_id
        if svc_id is not None and svc_id in arm.services:
            if len(arm.services) == 1:
                return arm.client_id
        return None

    def _generation(self):
        gen = getattr(self.db, "data_generation", None)
        return gen() if callable(gen) else None

    def refresh_index(self):
        """{normalized uid: client_id} for non-archived clients: identity columns (PAN, GSTIN,
        user/login ids, each service's user-id column) and the client token."""
        try:
            self._index_generation = self._generation()
            clients = self.db.search_clients("", include_archived=False)
            allowed = set()
            for col in self.db.get_mcl_columns():
                label = str(col.get("label", "")).lower()
                if any(term in label for term in IDENTITY_LABEL_TERMS):
                    allowed.add(col["id"])
            for service in self.db.get_services():
                if service.get("userid_column_id"):
                    allowed.add(service["userid_column_id"])
            self._identity_column_ids = allowed

            new_index, collisions = {}, set()
            for c in clients:
                cid = c["id"]
                keys = [v for col_id, v in c.get("values", {}).items() if col_id in allowed and v is not None]
                if c.get("client_id_token"):
                    keys.append(c["client_id_token"])
                for raw in keys:
                    key = sca_protocol.normalize_uid(raw)
                    if not sca_protocol.looks_like_uid(key):
                        continue
                    if key in new_index and new_index[key] != cid:
                        collisions.add(key)
                    else:
                        new_index[key] = cid
            for duplicate in collisions:
                new_index.pop(duplicate, None)
            self._uid_index = new_index
            print(f"[SCA] Index: {len(new_index)} ids; {len(collisions)} shared by two clients left out.")
        except Exception as e:
            print(f"[SCA] Index build error: {e}")

    def _ensure_index_fresh(self):
        """Clients added or edited anywhere (editor, CSV import, another PC via sync) are armable
        at once - the index was only rebuilt on start-up and settings save before."""
        gen = self._generation()
        if gen is None or gen != self._index_generation:
            self.refresh_index()

    # ------------------------------------------------------------------ copy -> arm
    def _on_clipboard_changed(self):
        app = QApplication.instance()
        if not app:
            return
        clipboard = app.clipboard()
        if not self.enabled and self.scc_ledger is None:
            return
        text = clipboard.text()
        if not text:
            return
        if self.scc_ledger is not None:
            try:
                self.scc_ledger(text)      # in memory only; the text is never stored or printed
            except Exception:
                pass
        if not self.enabled:
            return
        candidate = sca_protocol.normalize_uid(text)
        if not (3 <= len(candidate) <= 80) or not sca_protocol.looks_like_uid(candidate):
            return
        self._ensure_index_fresh()
        # Excel/Sera markers are preferred, but browser copies and some Qt table paths expose
        # plain text only; an exact match in the index is enough - other text is ignored.
        if not is_excel_source(clipboard.mimeData()) and candidate not in self._uid_index:
            return
        client_id = self._uid_index.get(candidate)
        if not client_id:
            return
        if is_suppressed(client_id):
            print(f"[SCA] Client {client_id} has a Manual Copy/Assist open - not arming.")
            return
        last_uid, last_t = self._last_copy
        now = time.time()
        if last_uid == candidate and now - last_t < REPEAT_COPY_S:
            return
        self._last_copy = (candidate, now)
        if client_id in self._paused_clients:
            print(f"[SCA] Client {client_id} armed {UNUSED_ARMS_BEFORE_PAUSE} times without a fill - paused this session.")
            return
        print(f"[SCA] Copied id matches client {client_id}. Arming...")
        self._arm_client_services(client_id, str(client_id), candidate)

    def _client_context(self, client_id: int):
        client = self.db.get_client(client_id)
        if not client:
            return None
        mcl_cols = {c["id"]: c for c in self.db.get_mcl_columns()}
        return client, mcl_cols, client.get("values", {})

    def _password_status(self, svc: dict, client_id: int, values: dict, mcl_cols: dict):
        """(saved password for this portal, caveat). Caveats:
          "no_password"    - nothing saved; shown to staff (it used to fail silently)
          "scc_unverified" - an Income Tax password SCC has not verified: given out only on a
                             request confirmed by a click on the page card (user choice,
                             2026-09-22 - before, it was never given, so SCA did nothing on the
                             IT portal for 468 of 521 clients).

        Only the portal's own password column is used. Falling back to "any column with pass in
        its label" (or the first column typed as password - USER ID, EMAIL and TAN are typed that
        way here) could type another portal's password, or the GST user id, into the box."""
        pwd_col_id = svc.get("password_column_id")
        password = str(values.get(pwd_col_id) or "").strip() if pwd_col_id else ""
        if not password:
            return "", "no_password"
        if automation.is_itr_service(svc) and not self.db.is_client_scc_verified(client_id=client_id):
            return password, "scc_unverified"
        return password, ""

    def _retire_current_arm(self):
        old = self._arm
        if old is None:
            return
        if old.grants == 0 and old.trigger in ("clipboard", "source"):   # a page arm is followed by a login by definition
            streak = self._unused_streak.get(old.client_id, 0) + 1
            self._unused_streak[old.client_id] = streak
            if streak >= UNUSED_ARMS_BEFORE_PAUSE:
                self._paused_clients.add(old.client_id)
        self._arm = None

    def _arm_client_services(self, client_id: int, client_token: str, matched_uid: str, trigger: str = "clipboard",
                             only_service_id=None, uid_page: str = ""):
        """only_service_id: arm for that one service of the client (a UID page arm), never the others."""
        try:
            ctx = self._client_context(client_id)
            if not ctx:
                return
            client, mcl_cols, values = ctx
            client_services = self.db.get_client_services(client_id)
            target_services = client_services or self.db.get_services()
            if only_service_id is not None:
                target_services = [x for x in (client_services or []) if x.get("id") == only_service_id]
            if not target_services:
                print(f"[SCA] Client {client_id} has no attached or configured services; not arming.")
                return

            business_name = owner_name = ""
            for c_id, col in mcl_cols.items():
                lbl = col.get("label", "").lower()
                val = str(values.get(c_id, "") or "").strip()
                if not val:
                    continue
                if any(k in lbl for k in ["business", "company", "firm", "trade", "client name", "name"]) and not business_name:
                    business_name = val
                elif any(k in lbl for k in ["owner", "proprietor", "director", "partner", "contact", "person"]) and not owner_name:
                    owner_name = val
            business_name = business_name or f"Client #{client_token}"

            # Only identity values trigger a fill - not every short value in the row (city,
            # phone, "YES" used to).
            identity_cols = set(self._identity_column_ids)
            identity_cols |= {s.get("userid_column_id") for s in target_services if s.get("userid_column_id")}
            candidates = [matched_uid]
            for c_id, val in values.items():
                key = sca_protocol.normalize_uid(val)
                if c_id in identity_cols and sca_protocol.looks_like_uid(key) and key not in candidates:
                    candidates.append(key)
            token = sca_protocol.normalize_uid(client.get("client_id_token") or "")
            if token and sca_protocol.looks_like_uid(token) and token not in candidates:
                candidates.append(token)

            services, arm_services = [], {}
            for svc in target_services:
                password, caveat = self._password_status(svc, client_id, values, mcl_cols)
                entry = sca_protocol.build_arm_service(svc, bool(password), caveat if not password else "",
                                                       needs_confirm=(caveat == "scc_unverified"))
                if entry is None:
                    continue
                services.append(entry)
                arm_services[svc.get("id")] = {"url": entry["url"], "name": entry["name"],
                                               "password_selector": entry["password_selector"]}
            if not services:
                print(f"[SCA] Client {client_id}: no portal with a login page; not arming.")
                return
            # Armed even when no portal has a usable password: the extension then explains, on
            # the login page, why nothing was filled (it used to fail silently).

            sca_mode = self.db.get_setting("sca_action_mode", "autofill")
            try:
                max_uses = max(1, min(int(self.db.get_setting("sca_max_uses", "1")), 20))
            except (TypeError, ValueError):
                max_uses = 1
            request = sca_protocol.build_arm_request(
                client_id=client_id, client_token=client_token, matched_uid=matched_uid,
                candidate_uids=candidates, services=services, business_name=business_name,
                owner_name=owner_name, sca_mode=sca_mode, max_uses=max_uses, trigger=trigger, uid_page=uid_page)
            previous = self._arm
            self._retire_current_arm()
            arm = request["arm"]
            self._arm = Arm(arm_id=arm["arm_id"], client_id=client_id, client_token=client_token,
                            matched_uid=matched_uid, services=arm_services,
                            expires_at=arm["expires_at"] / 1000.0, uses_remaining=max_uses,
                            max_uses=max_uses, sca_mode=sca_mode, trigger=trigger)
            now = time.time()
            self._superseded = {k: v for k, v in self._superseded.items() if v[2] > now}
            if previous is not None and previous.client_id == client_id:
                for k, v in list(self._superseded.items()):
                    if v[0] == client_id:
                        self._superseded[k] = (client_id, arm["arm_id"], v[2])
                self._superseded[str(previous.arm_id)] = (client_id, arm["arm_id"], now + self.SUPERSEDED_ARM_GRACE_S)
            print(f"[SCA] Armed client {client_token}: {len(services)} portal(s) [mode: {sca_mode}, {trigger}]")
            from core.sca import sca_log
            sca_log.note(f"armed client #{client_id}: arm {arm['arm_id']} cmd {request['command_id']} "
                         f"({trigger}, {sca_mode}, services {[x.get('service_id') for x in services]}, "
                         f"uid_page {'set' if uid_page else 'none'})")
            if automation.assist_engine() == "os":
                # OS Assist: the desktop's own SCA engine takes the arm; the browsers get nothing.
                from core.os_assist import sca_engine
                sca_engine.for_watcher(self).arm(
                    arm, automation.allowed_portal_domains(self.db.get_services()))
            else:
                automation.arm_sca(request)
            self.sca_armed.emit(client_id, client_token, services)
        except Exception as e:
            print(f"[SCA] _arm_client_services error: {e}")

    # ------------------------------------------------------------------ UID page -> arm
    SOURCES_REFRESH_MS = 10000

    def refresh_sources(self):
        """The services' UID pages and picked nodes (Settings -> SCA). Read on the UI thread; the watcher
        thread only reads the list (swapped whole), never the database."""
        try:
            self._uid_sources = [
                {"id": s.get("id"), "name": str(s.get("name") or ""),
                 "uid_page_link": str(s.get("uid_page_link") or "").strip(), "uid_node": s.get("uid_node") or ""}
                for s in self.db.get_services()
                if str(s.get("uid_page_link") or "").strip() and s.get("uid_node")]
        except Exception:
            self._uid_sources = []

    def start_source_watch(self):
        """Starts the watcher thread once. It does no UIA work until a UID page is in front. A pick saved in
        Settings is picked up within SOURCES_REFRESH_MS (no restart)."""
        if self._source_watcher is not None:
            return
        from core.sca.uid_source_watch import UidSourceWatcher
        self.source_uid.connect(self.handle_source_uid)
        self.source_warn.connect(self.handle_source_warn)
        self._sources_timer = QTimer(self)
        self._sources_timer.timeout.connect(self.refresh_sources)
        self._sources_timer.start(self.SOURCES_REFRESH_MS)
        self._source_watcher = UidSourceWatcher(
            lambda: self._uid_sources, lambda: self.enabled, self.source_uid.emit, self.source_warn.emit)
        self._source_watcher.start()

    def _source_name(self, service_id) -> str:
        return next((x["name"] for x in self._uid_sources if x["id"] == service_id), "this service")

    def handle_source_uid(self, service_id, uid: str) -> None:
        """A service's UID page shows this (cleaned) UID in its picked node. Arms that client for THAT
        service only, and only when the client has it."""
        from core.sca import sca_log
        step = lambda t: sca_log.note(f"arm service {service_id}: {t}")     # never the UID (sca_log)
        try:
            if not self.enabled:
                step("SCA is off: not armed")
                return
            self._ensure_index_fresh()
            from core.sca import uid_clean
            cand = sca_protocol.normalize_uid(uid)
            client_id = None
            for key in uid_clean.lookup_keys(cand):     # an email: whole, then the part before "@"
                client_id = self._uid_index.get(key)
                if client_id:
                    if key != cand:
                        step(f"matched by the part before '@' ({len(key)} characters)")
                    cand = key                          # the arm carries the key the client has
                    break
            name = self._source_name(service_id)
            if not client_id:
                step(f"no client has this UID ({len(cand)} characters): not armed, notice shown")
                self._source_refused(service_id, f"Sera read UID {cand} on {name}'s login page, but no client has "
                                     f"it{' (also tried the part before @)' if '@' in cand else ''}. Check the client's User ID, and that it is in {name}'s User ID column "
                                     f"(Services) or a PAN / GSTIN / user id column.")
                return
            if is_suppressed(client_id):
                step(f"client #{client_id} is suppressed (Client Detail / SCC just used it): not armed, quiet")
                return                      # staff just acted on this client: deliberately quiet
            if client_id in self._paused_clients:
                step(f"client #{client_id} is paused (unused arms): not armed, notice shown")
                self._source_refused(service_id, f"SCA is paused for the client with UID {cand}: it was armed "
                                     f"{UNUSED_ARMS_BEFORE_PAUSE} times without a fill.")
                return
            if not any(x.get("id") == service_id for x in (self.db.get_client_services(client_id) or [])):
                step(f"client #{client_id} does not have this service attached: not armed, notice shown")
                self._source_refused(service_id, f"The client with UID {cand} does not have {name} attached. "
                                     f"Attach the service to the client to use SCA here.")
                return
            # No "already armed" skip: the watcher reports once per VISIT of the page, and each visit needs a
            # fresh arm - the extension drops an arm once it is used and never shows the same arm twice in a
            # tab, so keeping the old one meant no widget at all until it expired (5 min). The unused-arm
            # streak (_retire_current_arm) still pauses a client armed this way 5 times without a fill.
            print(f"[SCA] UID page of service {service_id} shows client {client_id}. Arming...")
            from core.sdis import keys
            link = next((x["uid_page_link"] for x in self._uid_sources if x["id"] == service_id), "")
            self._arm_client_services(client_id, str(client_id), cand, trigger="source", only_service_id=service_id,
                                      uid_page=keys.page_link(link))
            arm = self._arm
            if arm is None or arm.client_id != client_id or service_id not in arm.services:
                step(f"client #{client_id}: arm could not be built (no login page link?), notice shown")
                self._source_refused(service_id, f"SCA could not arm {name} for the client with UID {cand}: "
                                     f"the service has no login page link (Services).")
        except Exception as e:
            print(f"[SCA] UID page error: {e}")
            step(f"error {type(e).__name__}, notice shown")
            self._source_refused(service_id, f"SCA error on the UID page: {e}")

    def _source_refused(self, service_id, text: str) -> None:
        """Why a UID page did not arm, shown to staff. The watcher reports a UID once per page visit, so
        this is once per visit too - never silent, never repeated every tick."""
        print(f"[SCA] UID page of service {service_id}: {text}")
        self._source_notice("SCA: " + text)

    def _source_notice(self, msg: str, level: str = "warning") -> None:
        """A UID-page notice: Sera's own alert, and ON TOP of the browser (source_notice -> main.py popup),
        because the browser is in front when it happens."""
        self.sca_notice.emit(msg, level)
        self.source_notice.emit(msg, level)

    def handle_source_warn(self, service_id, why: str) -> None:
        """The picked UID box is not on the page, or shows no UID (once per visit): staff must pick it again."""
        name = self._source_name(service_id)
        if why == "no_uid":
            from core.sca import uid_node
            src = next((x for x in self._uid_sources if x["id"] == service_id), {})
            rule = (uid_node.loads(src.get("uid_node")) or {}).get("rule") or "?"
            self._source_notice(f"SCA: Sera reads the UID box on {name}'s login page, but finds no UID in "
                                f"what it shows (rule {rule}). Pick it again in Settings \u2192 SCA, with a "
                                f"client's UID typed in.")
            return
        detail = "found more than one box like it" if why == "ambiguous" else "can't find it"
        self._source_notice(f"SCA: the UID box on {name}'s login page changed ({detail}). "
                            f"Pick it again in Settings \u2192 SCA.")

    # ------------------------------------------------------------------ extension -> desktop
    def handle_password_request(self, msg: dict) -> dict:
        """Answer to SCA_PASSWORD_REQUEST {request_id, arm_id, service_id, page_host,
        matched_uid}: a GRANT with the one password, or a DENIED with the reason."""
        request_id = msg.get("request_id")

        def denied(reason: str) -> dict:
            print(f"[SCA] Password request refused: {reason}")
            return {"type": sca_protocol.MSG_SCA_PASSWORD_DENIED, "request_id": request_id,
                    "arm_id": msg.get("arm_id"), "reason": reason}

        arm = self._live_arm_for(msg.get("arm_id"))
        if not self.enabled:
            return denied("SCA is turned off")
        if arm is None:
            return denied("no such arm (a newer copy replaced it, or the app restarted)")
        if time.time() > arm.expires_at:
            self._retire_current_arm()
            return denied("the arm expired")
        if arm.uses_remaining <= 0:
            return denied("no uses left for this copy")
        if arm.grants >= arm.max_uses + 2:
            return denied("too many password requests for this copy")
        svc_id = msg.get("service_id")
        svc = arm.services.get(svc_id)
        if svc is None:
            return denied("that portal is not part of this arm")
        if not sca_protocol.host_matches(str(msg.get("page_host") or ""), svc["url"]):
            return denied("the page is not that portal's site")
        ctx = self._client_context(arm.client_id)
        if not ctx:
            return denied("the client no longer exists")
        _client, mcl_cols, values = ctx
        full_svc = next((s for s in self.db.get_services() if s.get("id") == svc_id), None)
        password, caveat = (self._password_status(full_svc, arm.client_id, values, mcl_cols)
                            if full_svc else ("", "no_password"))
        if not password:
            return denied("no password SCA may use for that portal")
        if caveat == "scc_unverified" and msg.get("confirmed") is not True:
            return denied("this Income Tax password is not SCC-verified - it needs a click on the page card")
        arm.grants += 1
        self._unused_streak.pop(arm.client_id, None)
        return {"type": sca_protocol.MSG_SCA_PASSWORD_GRANT, "request_id": request_id,
                "arm_id": arm.arm_id, "service_id": svc_id, "password": password,
                "password_selector": svc.get("password_selector", "")}

    def handle_fill_result(self, msg: dict) -> None:
        """SCA_FILL_RESULT {arm_id, service_id, result: filled|failed, reason}: a use is spent only
        when the password was really filled; failures (and desktop denials, which the extension
        reports as failures) reach staff through sca_notice."""
        arm = self._live_arm_for(msg.get("arm_id"))
        client_id = arm.client_id if arm else None
        portal = (arm.services.get(msg.get("service_id"), {}).get("name") if client_id else None) or "portal"
        if msg.get("result") == "filled":
            print(f"[SCA] Password filled on {portal}")
            if client_id:
                arm.fills.append(portal)
                arm.uses_remaining = max(0, arm.uses_remaining - 1)
                mode = getattr(arm, "sca_mode", "autofill")
                is_widget = mode in ("widget", "assist")
                action = "sca_widget" if is_widget else "sca_autofill"
                mode_desc = "Widget" if is_widget else "Ambient Autofill"
                detail = f"SCA {mode_desc} filled password for {portal}"
                service_id = msg.get("service_id")
                try:
                    self.db.log_action(
                        actor=self.actor,
                        action=action,
                        client_id=client_id,
                        service_id=service_id,
                        detail=detail
                    )
                except Exception as e:
                    print(f"[SCA] Audit log error: {e}")
                    try:
                        self.db.record_client_activity(client_id, "SCA", f"Password filled on {portal}")
                    except Exception:
                        pass
        else:
            reason = msg.get("reason") or "unknown"
            print(f"[SCA] Fill on {portal} failed: {reason}")
            self.sca_notice.emit(self.explain(portal, reason), "warning")

    @staticmethod
    def explain(portal: str, reason: str) -> str:
        if reason == "scc_unverified":
            return (f"SCA did not fill the {portal} password: it has not been verified yet. "
                    "Log in once with SCC (Smart Credential Combinations) to verify it.")
        if reason == "no_password":
            return f"SCA did not fill {portal}: no password is saved for this client."
        return f"SCA could not fill the {portal} password: {reason}."
