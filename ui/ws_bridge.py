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
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer, QWebSocket

WS_PORTS = (48765, 48766, 48767, 48768)  # tried in order; all below Windows' dynamic port range

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


class WSBridge(QObject):
    filing_result_received = Signal(dict)
    uncertain_result_received = Signal(dict)
    sca_state_received = Signal(dict)
    sca_error_received = Signal(dict)
    sca_fill_result_received = Signal(dict)
    sca_password_requested = Signal(dict)   # reply with WSBridge.reply(msg, payload)
    session_started_received = Signal(dict)
    scc_password_verified_received = Signal(dict)
    sdc_timeline_received = Signal(dict)
    sudr_capture_received = Signal(dict)  # SUDR canonical envelope
    extension_settings_updated_received = Signal(dict)

    def __init__(self, parent=None):
        if not isinstance(parent, QObject):
            parent = None
        super().__init__(parent)
        self.settings_provider = None
        self._server: Optional[QWebSocketServer] = None
        self._sockets: list[QWebSocket] = []
        self._chrome_id: Optional[str] = None
        self._bound_port: Optional[int] = None
        # A reply to SCA_PASSWORD_REQUEST must go back only to the browser that asked, never to
        # every connected browser. Keyed by the request's "_id" (every message the extension
        # sends carries one); the socket itself never goes into a message dict, so nothing here
        # risks being json.dumps()'d by a signal handler that logs/echoes the message it received.
        self._pending_replies: dict[str, QWebSocket] = {}

    # ------------------------------------------------------------------ lifecycle

    def start(self, chrome_manifest_path: Optional[Path] = None):
        if chrome_manifest_path is not None:
            self._chrome_id = self._chrome_id_override() or _load_chrome_extension_id(chrome_manifest_path)
            if self._chrome_id:
                print(f"[WSBridge] Expecting Chrome/Edge origin chrome-extension://{self._chrome_id} "
                      f"- compare against chrome://extensions if the extension can't connect.")
            else:
                print(f"[WSBridge] Could not read a Chrome extension id from {chrome_manifest_path}; "
                      f"Chrome/Edge connections will be refused until this is fixed.")

        self._server = QWebSocketServer("Sera Bridge", QWebSocketServer.SslMode.NonSecureMode, self)
        self._server.newConnection.connect(self._on_new_connection)

        for port in WS_PORTS:
            if self._server.listen(QHostAddress.LocalHost, port):
                self._bound_port = port
                break
        else:
            print(f"[WSBridge] Could not bind any of {WS_PORTS} - extension bridge is offline.")
            return
        print(f"[WSBridge] Listening on 127.0.0.1:{self._bound_port}")

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
        origin = (origin or "").strip()
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
            sock.textMessageReceived.connect(lambda text, s=sock: self._on_text_message(s, text))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_disconnected(self, sock: QWebSocket):
        try:
            self._sockets.remove(sock)
        except ValueError:
            pass
        for pending_id, pending_sock in list(self._pending_replies.items()):
            if pending_sock is sock:
                del self._pending_replies[pending_id]
        sock.deleteLater()

    def _on_text_message(self, sock: QWebSocket, text: str):
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
        if mtype in ("filing_result", "audit_event"):
            self.filing_result_received.emit(msg)
        elif mtype == "uncertain_result":
            self.uncertain_result_received.emit(msg)
        elif mtype == "SCA_ACK":
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
        elif mtype == "session_start":
            self.session_started_received.emit(msg)
        elif mtype == "scc_password_verified":
            self.scc_password_verified_received.emit(msg)
        elif mtype == "sdc_session_timeline":
            self.sdc_timeline_received.emit(msg)
        elif mtype == "sudr_capture":
            self.sudr_capture_received.emit(msg)
        elif mtype == "extension_settings_updated":
            self.extension_settings_updated_received.emit(msg)
        elif mtype in ("request_settings", "get_settings"):
            payload = self.settings_provider() if callable(self.settings_provider) else {"status": "ok"}
            return {**payload, "type": "settings_response"}
        return None

    # ------------------------------------------------------------------ sending

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

    def broadcast(self, payload: dict) -> int:
        """Sends to every connected browser (there can be more than one - Chrome and Firefox open
        at once). Returns how many sockets it reached."""
        sent = 0
        for sock in list(self._sockets):
            self._send(sock, payload)
            sent += 1
        return sent

    def send_first(self, payload: dict) -> bool:
        """Sends to one connected browser only - whichever connected first. Used for autofill,
        where pushing the same command to every open browser would fill the same portal twice."""
        if not self._sockets:
            return False
        self._send(self._sockets[0], payload)
        return True

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
