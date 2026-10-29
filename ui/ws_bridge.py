"""
ws_bridge.py - the app<->extension bridge over one local WebSocket
====================================================================
Replaces native messaging (native_host/) and the hand-rolled HTTP listener that used to sit on
port 49152. Both were a source of unreliable, hard-to-install connections:
  * Native messaging needed a registry key and a host manifest registered per browser, which
    routinely failed on a fresh PC (antivirus, missing registry permissions, a stale path).
  * 49152-49155 sit inside Windows' own dynamic/ephemeral port range, so another program could
    already be using one at random - a silent bind failure with no obvious fix but a restart.
  * The old listener read a request until its 2 s timeout on every call, one at a time, which
    made every message wait roughly 2 s even when the app was idle.
  * Page scripts on the portal called the app directly over HTTP; browsers increasingly block a
    live web page from reaching localhost at all (Private Network Access), so captures were lost
    silently whenever that happened.

WSBridge runs one QWebSocketServer on the Qt event loop (no polling thread needed - Qt's
WebSocket server is event-driven). It listens on the first free port from WS_PORTS, well below
Windows' dynamic port range, and the extension tries the same list in the same order until one
answers - so there is nothing to configure and nothing to register.

Only the Sera extension's own background/service-worker page may connect - never a website, and
never the SDC content script running inside the portal page (that page never talks to the app
directly; everything goes through the extension's background page, which forwards it here). This
is enforced by checking the WebSocket handshake's Origin, exactly as the old HTTP listener did
for its direct-fetch path (see origin_kind() there before this rewrite) - a browser will not let
ordinary page content lie about its Origin, so a malicious website cannot pass this check. It is
not a defence against another program already running as the same Windows user account; that is
a stronger threat than this bridge is trying to solve (see docs/app-extension-communication-report.md
and the WebSocket-bridge decision in project memory for the fuller comparison).

Chrome's extension id is derived from the manifest's public "key" (deterministic, see
_chrome_extension_id). Firefox randomises its moz-extension:// origin per install, so the first
Firefox origin seen is remembered ("trust on first connect") in
~/AmanAssociates_Sera/ws_bridge/firefox_origin.txt and required to match on every later
connection. That first pairing can only be produced by a real Firefox extension page (the
scheme itself cannot be forged by web content), so it is a strict improvement over the previous
"Access-Control-Allow-Origin: *" default - but note it in any future security review.
"""

import json
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Qt, QThread, QTimer
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer, QWebSocket

WS_PORTS = (48765, 48766, 48767, 48768)  # tried in order; all below Windows' dynamic port range
_BIND_RETRY_MS = 5000
_BIND_RETRY_TRIES = 24                   # 2 minutes in all

CHROME_ORIGIN_PREFIX = "chrome-extension://"
EDGE_ORIGIN_PREFIX = "chrome-extension://"  # Edge is Chromium; same scheme, same id
FIREFOX_ORIGIN_PREFIX = "moz-extension://"

# Unlike the old HTTP listener, nothing here answers a "portal" origin (a content script on the
# tax portal itself) any more - every SDC capture now goes through the extension's own
# background page first (see docs/app-extension-communication-report.md for why the direct-fetch
# path was unreliable). So a connection that reaches dispatch() at all has already passed
# _origin_allowed() as the genuine extension - there is no second, more-sensitive tier of
# messages to gate further, the way the old SENSITIVE_TYPES set had to.


def _firefox_pairing_path() -> Path:
    return Path.home() / "AmanAssociates_Sera" / "ws_bridge" / "firefox_origin.txt"


def _chrome_extension_id(manifest_key_b64: str) -> Optional[str]:
    """The id Chrome/Edge derive from a packed extension's public key: SHA-256 of the DER key,
    first 16 bytes, each nibble mapped 0-15 -> 'a'-'p'."""
    import base64
    import hashlib
    try:
        der = base64.b64decode(manifest_key_b64)
        digest = hashlib.sha256(der).digest()[:16]
        return "".join(chr(ord("a") + (b >> 4)) + chr(ord("a") + (b & 0xF)) for b in digest)
    except Exception:
        return None


def _load_chrome_extension_id(manifest_path: Path) -> Optional[str]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        key = data.get("key")
        if not key:
            return None
        return _chrome_extension_id(key)
    except Exception:
        return None


def _as_positive_int(value) -> Optional[int]:
    """A positive integer id, or None. Accepts an int or a numeric string only."""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _clean_sck_password(msg: dict) -> Optional[dict]:
    """Validate an SCK save message before any handler touches it.

    The password is the only sensitive value here, so it is bounded (1-256 chars) and kept in
    local memory only; callers must never log or store the returned dict. Everything else is an
    id or a short label, bounded so a malformed page message cannot arrive as a huge string.
    """
    service_id = _as_positive_int(msg.get("service_id"))
    if service_id is None:
        return None
    password = msg.get("password")
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        return None
    client_id = msg.get("client_id")
    if client_id in (None, ""):
        client_id = None
    else:
        client_id = _as_positive_int(client_id)
        if client_id is None:
            return None
    arm_id = msg.get("arm_id") or ""
    portal = msg.get("portal") or ""
    if not isinstance(arm_id, str) or len(arm_id) > 64:
        return None
    if not isinstance(portal, str) or len(portal) > 100:
        return None
    return {
        "type": "sck_save_password",
        "service_id": service_id,
        "client_id": client_id,
        "arm_id": arm_id.strip(),
        "portal": portal.strip(),
        "password": password,
    }


class WSBridge(QObject):
    # Fed only by core/vsdc's filing_captured signal (SGT/VSDC desktop capture), connected
    # directly to main.py's _handle_extension_result - not by anything over this websocket.
    # The extension used to also feed it via "filing_result"/"audit_event" messages; that
    # tracking was removed (autofill-tweaks Part A/W1-1), so dispatch() below no longer emits it.
    filing_result_received = Signal(dict)
    sca_state_received = Signal(dict)
    sca_error_received = Signal(dict)
    sca_fill_result_received = Signal(dict)
    sca_password_requested = Signal(dict)   # reply with WSBridge.reply(msg, payload)
    # Staff clicks on the MECP SCC card (SCC-U step 3): {attempt_id, row_label}. Labels only.
    scc_row_worked_received = Signal(dict)
    scc_card_closed_received = Signal(dict)     # {attempt_id}
    scc_row_none_received = Signal(dict)        # {attempt_id}: "None - I typed my own" (step 5)
    # SCK: the new password staff typed into the portal card. Carries the password - the handler
    # must never log, print or store the message.
    sck_save_password_received = Signal(dict)
    # SCK: the card closed itself (staff pressed x, or the service's dashboard page was reached).
    sck_widget_closed_received = Signal(dict)   # {service_id, reason}
    # SCK: staff pressed the ✎ edit button on SMTI / MECP / SCA widget to update a
    # password without a detected failure.  {service_id, client_id, portal}
    sck_edit_requested = Signal(dict)
    extension_settings_updated_received = Signal(dict)

    # automation.py calls broadcast()/send_first() from plain background threads (autofill
    # push, SCA arming, settings updates - see _attempt_send/arm_sca/update_extension_settings
    # there), but every QWebSocket in self._sockets has thread affinity to the Qt/GUI thread
    # this WSBridge itself lives on. Calling sendTextMessage() on a QWebSocket from a foreign
    # thread is a silent Qt thread-affinity violation: no exception is raised (so _send()'s
    # try/except never fires and the call reports success), but the frame is never actually
    # written to the wire - the extension never receives it.
    #
    # This signal marshals a zero-arg callable onto the Qt thread via a BlockingQueuedConnection,
    # so broadcast()/send_first() below keep the exact synchronous int/bool return value every
    # caller already depends on, whichever thread calls them from. It carries a callable rather
    # than the payload/result directly because PySide6 round-trips a `list`/`dict`-typed signal
    # argument through QVariant across a queued connection - the slot receives a COPY, so
    # mutating it to hand a result back is silently lost (confirmed: appending to it in the slot
    # never reaches the emitting thread's own list). A `object`-typed argument carrying a Python
    # callable is passed by reference instead, so a closure capturing its own private result dict
    # observes the mutation after emit() returns - and, being private per call, two concurrent
    # callers from different threads never share (and can't race on) that dict.
    _run_on_qt_thread = Signal(object)

    def __init__(self, parent=None):
        if not isinstance(parent, QObject):
            parent = None
        super().__init__(parent)
        self.settings_provider = None
        self._server: Optional[QWebSocketServer] = None
        self._sockets: list[QWebSocket] = []
        self._chrome_id: Optional[str] = None
        self._kinds: dict = {}   # socket -> browser kind, see _browser_kind()
        self._bound_port: Optional[int] = None
        # A reply to SCA_PASSWORD_REQUEST must go back only to the browser that asked, never to
        # every connected browser. Keyed by the request's "_id" (every message the extension
        # sends carries one); the socket itself never goes into a message dict, so nothing here
        # risks being json.dumps()'d by a signal handler that logs/echoes the message it received.
        self._pending_replies: dict[str, QWebSocket] = {}
        self._last_sca_arm: Optional[dict] = None
        # The server may be started before the app has connected its signal handlers; messages
        # received in that window are held here and replayed by mark_ready().
        self._ready = False
        self._held: list[tuple[QWebSocket, str]] = []
        # Explicit BlockingQueuedConnection (not the default AutoConnection) so this is a queued
        # hop even in the one case Qt's own thread-affinity check can't tell apart from "already
        # on the Qt thread": a connection made before this object's thread affinity is what it
        # will be at emit time. broadcast()/send_first() below never emit these when already on
        # the Qt thread (that would deadlock a BlockingQueuedConnection), so this only ever
        # blocks a background thread, never itself.
        self._run_on_qt_thread.connect(lambda fn: fn(), Qt.ConnectionType.BlockingQueuedConnection)

    # ------------------------------------------------------------------ lifecycle

    def start(self, chrome_manifest_path: Optional[Path] = None):
        # Try override file first (admin escape hatch), then the provided path, then well-known
        # fallback locations (APP_DIR and the source folder next to this module).
        override_id = self._chrome_id_override()
        if override_id:
            self._chrome_id = override_id
            print(f"[WSBridge] Chrome/Edge id from override file: {self._chrome_id}")
        else:
            # Build a list of candidate manifest paths to try in order
            candidates: list[Optional[Path]] = [chrome_manifest_path]
            try:
                candidates.append(Path.home() / "AmanAssociates_Sera" / "sera_extension" / "manifest.json")
            except Exception:
                pass
            try:
                candidates.append(Path(__file__).resolve().parent.parent / "sera_extension" / "manifest.json")
            except Exception:
                pass

            for candidate in candidates:
                if candidate and candidate.exists():
                    ext_id = _load_chrome_extension_id(candidate)
                    if ext_id:
                        self._chrome_id = ext_id
                        print(f"[WSBridge] Expecting Chrome/Edge origin chrome-extension://{self._chrome_id} "
                              f"- compare against chrome://extensions if the extension can't connect.")
                        break
            else:
                if not self._chrome_id:
                    print(f"[WSBridge] Could not read a Chrome extension id from any manifest path; "
                          f"Chrome/Edge connections will be refused until this is fixed.")

        self._server = QWebSocketServer("Sera Bridge", QWebSocketServer.SslMode.NonSecureMode, self)
        self._server.newConnection.connect(self._on_new_connection)

        if not self._try_listen():
            # At logon another program (or this app's previous run) can still hold the ports for
            # a few seconds; keep trying instead of leaving the extension offline until a restart.
            print(f"[WSBridge] Could not bind any of {WS_PORTS} - will retry every "
                  f"{_BIND_RETRY_MS // 1000} s for {_BIND_RETRY_TRIES * _BIND_RETRY_MS // 60000} min.")
            self._bind_attempts_left = _BIND_RETRY_TRIES
            QTimer.singleShot(_BIND_RETRY_MS, self._retry_listen)

    def _try_listen(self) -> bool:
        if self._server is None:
            return False
        for port in WS_PORTS:
            if self._server.listen(QHostAddress.LocalHost, port):
                self._bound_port = port
                print(f"[WSBridge] Listening on 127.0.0.1:{self._bound_port}")
                return True
        return False

    def _retry_listen(self):
        if self._server is None or self._bound_port is not None:
            return    # stopped, or already bound
        if self._try_listen():
            return
        self._bind_attempts_left -= 1
        if self._bind_attempts_left > 0:
            QTimer.singleShot(_BIND_RETRY_MS, self._retry_listen)
        else:
            print(f"[WSBridge] Gave up binding {WS_PORTS} - extension bridge is offline.")


    def stop(self):
        for sock in list(self._sockets):
            try:
                sock.close()
            except Exception:
                pass
        self._sockets.clear()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None

    # ------------------------------------------------------------------ origin trust

    @staticmethod
    def _chrome_id_override() -> Optional[str]:
        """Escape hatch: if Chrome's id-from-key derivation is ever wrong for some build (a
        padding edge case, a re-keyed manifest, ...), an admin can drop the real id - visible in
        chrome://extensions - into this file instead of waiting on a code fix."""
        path = Path.home() / "AmanAssociates_Sera" / "ws_bridge" / "chrome_extension_id_override.txt"
        try:
            value = path.read_text(encoding="ascii").strip().lower()
            return value or None
        except OSError:
            return None

    def _origin_allowed(self, origin: str) -> bool:
        origin = (origin or "").strip().rstrip("/")
        if not origin:
            return False
        if self._chrome_id and origin == f"{CHROME_ORIGIN_PREFIX}{self._chrome_id}":
            return True
        if origin.startswith(FIREFOX_ORIGIN_PREFIX):
            return self._firefox_origin_allowed(origin)
        return False

    def _firefox_origin_allowed(self, origin: str) -> bool:
        path = _firefox_pairing_path()
        try:
            remembered = path.read_text(encoding="utf-8").strip()
        except OSError:
            remembered = ""
        if remembered:
            return origin == remembered
        # Trust on first connect: only a genuine Firefox extension page can present this scheme,
        # web content cannot forge it. Remember it so every later connection must match exactly.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(origin, encoding="utf-8")
            import os
            os.replace(tmp, path)
        except OSError:
            pass
        return True

    # ------------------------------------------------------------------ connections

    def _on_new_connection(self):
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            origin = sock.origin()
            if not self._origin_allowed(origin):
                print(f"[WSBridge] Refused connection from unrecognised origin: {origin!r}")
                sock.close()
                continue
            self._sockets.append(sock)
            self._kinds[sock] = self._browser_kind(sock, origin)
            sock.textMessageReceived.connect(lambda text, s=sock: self._on_text_message(s, text))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            self._replay_sca_arm(sock)

    @staticmethod
    def _browser_kind(sock: QWebSocket, origin: str) -> str:
        """'firefox' | 'edge' | 'chrome' | '' (unknown). Chrome and Edge share the
        chrome-extension:// origin, so the handshake's User-Agent tells them apart."""
        if (origin or "").startswith(FIREFOX_ORIGIN_PREFIX):
            return "firefox"
        try:
            ua = bytes(sock.request().rawHeader("User-Agent")).decode("latin-1")
        except Exception:
            return ""
        if "Edg/" in ua or "EdgA/" in ua:
            return "edge"
        if "Firefox/" in ua:
            return "firefox"
        if "Chrome/" in ua:
            return "chrome"
        return ""

    def _on_disconnected(self, sock: QWebSocket):
        self._kinds.pop(sock, None)
        try:
            self._sockets.remove(sock)
        except ValueError:
            pass
        for pending_id, pending_sock in list(self._pending_replies.items()):
            if pending_sock is sock:
                del self._pending_replies[pending_id]
        try:
            sock.deleteLater()
        except RuntimeError:
            pass  # C++ object already deleted by Qt - harmless

    def mark_ready(self):
        """Call once every signal/settings_provider is connected; replays held messages."""
        self._ready = True
        held, self._held = self._held, []
        for sock, text in held:
            if sock in self._sockets:
                self._on_text_message(sock, text)

    def _on_text_message(self, sock: QWebSocket, text: str):
        if not self._ready:
            self._held.append((sock, text))
            return
        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        req_id = msg.get("_id")
        if msg.get("type") == "SCA_PASSWORD_REQUEST" and req_id:
            self._pending_replies[req_id] = sock
        reply = self.dispatch(msg)
        if reply is not None:
            reply = {**reply, "_id": req_id} if req_id else reply
            self._send(sock, reply)
        elif req_id:
            self._send(sock, {"type": "_ack", "_id": req_id})

    # ------------------------------------------------------------------ dispatch (same message
    # shapes/types the old ExtensionListener handled - nothing about the protocol changes here,
    # only how a message gets to this function)

    def dispatch(self, msg: dict):
        mtype = msg.get("type")
        if mtype == "SCA_ACK":
            cmd_id = msg.get("command_id")
            if cmd_id:
                import automation
                automation.register_ack(cmd_id)
        elif mtype == "SCA_STATE":
            self.sca_state_received.emit(msg)
        elif mtype == "SCA_ERROR":
            self.sca_error_received.emit(msg)
        elif mtype == "SCA_FILL_RESULT":
            self.sca_fill_result_received.emit(msg)
        elif mtype == "SCA_PASSWORD_REQUEST":
            self.sca_password_requested.emit(msg)
        elif mtype == "scc_row_worked":
            attempt_id, label = msg.get("attempt_id"), msg.get("row_label")
            if isinstance(attempt_id, str) and isinstance(label, str) and 0 < len(attempt_id) <= 64 and 0 < len(label) <= 100:
                self.scc_row_worked_received.emit({"type": mtype, "attempt_id": attempt_id, "row_label": label})
        elif mtype == "scc_card_closed":
            attempt_id = msg.get("attempt_id")
            if isinstance(attempt_id, str) and 0 < len(attempt_id) <= 64:
                self.scc_card_closed_received.emit({"type": mtype, "attempt_id": attempt_id})
        elif mtype == "scc_row_none":
            attempt_id = msg.get("attempt_id")
            if isinstance(attempt_id, str) and 0 < len(attempt_id) <= 64:
                self.scc_row_none_received.emit({"type": mtype, "attempt_id": attempt_id})
        elif mtype == "sck_save_password":
            clean = _clean_sck_password(msg)
            if clean is not None:
                self.sck_save_password_received.emit(clean)
        elif mtype in ("sck_dismissed", "sck_widget_done"):
            service_id = _as_positive_int(msg.get("service_id"))
            if service_id is not None:
                self.sck_widget_closed_received.emit({
                    "type": mtype, "service_id": service_id,
                    "reason": "dismissed" if mtype == "sck_dismissed" else "dashboard"})
        elif mtype == "sck_edit_request":
            service_id = _as_positive_int(msg.get("service_id"))
            client_id  = _as_positive_int(msg.get("client_id"))
            portal = msg.get("portal", "")
            if service_id is not None and isinstance(portal, str) and len(portal) <= 100:
                self.sck_edit_requested.emit({
                    "type": mtype, "service_id": service_id,
                    "client_id": client_id, "portal": portal.strip()})
        elif mtype == "extension_settings_updated":
            self.extension_settings_updated_received.emit(msg)
        elif mtype in ("request_settings", "get_settings"):
            payload = self.settings_provider() if callable(self.settings_provider) else {"status": "ok"}
            return {**payload, "type": "settings_response"}
        return None

    # ------------------------------------------------------------------ sending

    def _remember_sca_arm(self, payload: dict):
        """Cache (or clear) the live SCA arm for _replay_sca_arm(). Runs on the Qt thread."""
        mtype = payload.get("type")
        if mtype == "SCA_ARM_REQUEST":
            arm = payload.get("arm")
            expires_at = arm.get("expires_at") if isinstance(arm, dict) else None
            live = (isinstance(expires_at, (int, float))
                    and not isinstance(expires_at, bool)
                    and expires_at > time.time() * 1000)
            self._last_sca_arm = payload if live else None
        elif mtype == "SCA_DISARM_REQUEST":
            self._last_sca_arm = None

    def _replay_sca_arm(self, sock: QWebSocket):
        """Arms a browser that connects after arm_sca() broadcast (its service worker may have
        been asleep while another browser acknowledged the arm and stopped the retry loop)."""
        payload = self._last_sca_arm
        if not payload:
            return
        arm = payload.get("arm")
        try:
            expires_at = float((arm or {}).get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at <= time.time() * 1000:
            self._last_sca_arm = None
            return
        self._send(sock, payload)

    def _send(self, sock: QWebSocket, payload: dict):
        try:
            sock.sendTextMessage(json.dumps(payload))
        except Exception as e:
            print(f"[WSBridge] send failed: {e}")

    def reply(self, original_msg: dict, payload: dict):
        """Replies to whichever socket sent original_msg (matched by its "_id") - used for
        SCA_PASSWORD_REQUEST, where the answer must go only to the browser that asked, never to
        every connected browser. Answerable once; the id is forgotten either way."""
        req_id = original_msg.get("_id")
        sock = self._pending_replies.pop(req_id, None) if req_id else None
        if sock is not None and sock in self._sockets:
            self._send(sock, payload)
            return True
        return False

    def _call_on_qt_thread(self, fn):
        """Runs fn() on the Qt thread and returns its result, whatever thread this is called
        from. A no-op passthrough when already on the Qt thread (also avoids deadlocking the
        BlockingQueuedConnection below, which blocks the CALLING thread until fn() has run -
        emitting it from the Qt thread itself would mean that thread waiting on itself)."""
        if QThread.currentThread() is self.thread():
            return fn()
        result = {}
        self._run_on_qt_thread.emit(lambda: result.__setitem__("v", fn()))
        return result["v"]

    def broadcast(self, payload: dict) -> int:
        """Sends to every connected browser (there can be more than one - Chrome and Firefox open
        at once). Returns how many sockets it reached.

        Safe to call from any thread: automation.py's background workers (autofill push, SCA
        arming, extension settings pushes) call this from plain threading.Thread instances, but
        every QWebSocket in self._sockets belongs to the Qt thread - so the actual send is
        marshalled there via _call_on_qt_thread if this isn't already that thread."""
        def _do():
            self._remember_sca_arm(payload)
            sent = 0
            for sock in list(self._sockets):
                self._send(sock, payload)
                sent += 1
            return sent
        return self._call_on_qt_thread(_do)

    def send_first(self, payload: dict, browser: Optional[str] = None) -> bool:
        """Sends to one connected browser only - whichever connected first. Used for autofill,
        where pushing the same command to every open browser would fill the same portal twice.

        `browser` ('chrome' | 'edge' | 'firefox') restricts that to a browser of that kind; with
        none connected it returns False rather than falling back to another browser.

        Safe to call from any thread - see broadcast() above for why that matters."""
        def _do():
            for sock in self._sockets:
                if not browser or self._kinds.get(sock) == browser:
                    self._send(sock, payload)
                    return True
            return False
        return self._call_on_qt_thread(_do)

    @property
    def connected_count(self) -> int:
        return len(self._sockets)


# ---------------------------------------------------------------------- module-level singleton
# automation.py's functions are plain module functions (called from many places, with no object
# to hold a bridge reference), so they reach the one running WSBridge through here - the same
# role HOST_PORTS + a fresh TCP connection per call used to play.
_active_bridge: Optional["WSBridge"] = None


def set_active_bridge(bridge: Optional["WSBridge"]):
    global _active_bridge
    _active_bridge = bridge


def get_active_bridge() -> Optional["WSBridge"]:
    return _active_bridge
