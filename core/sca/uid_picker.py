"""
core/sca/uid_picker.py - Settings -> SCA -> "Pick UID": register the node that shows a client's UID
===================================================================================================
docs/sca-uid-node-plan.md WP2-3. No Qt here (the dialog drives it from a worker thread):

  1. wait_for_page: staff open the service's UID page (its own link, not the login link) with a UID set.
     When the browser in front shows that page AND an on-screen password box, it is read once by UIA
     (core/sca/page_nodes: a password box's value is never read).
  2. PickSession: core/uia_render draws the read as an HTML page in pick mode. Only the nodes whose text
     holds a UID (core/sca/uid_clean) are clickable.
  3. The page is served by a one-shot server on 127.0.0.1 (random port) under a one-time token: GET the
     page, POST {token, doc, i, rule} once. Wrong token / Host / a second POST: refused. It stops after the
     pick or after TTL_S. Nothing is written to disk. The POST carries indexes only: Sera holds the read.
  4. on_done(fingerprint) gets core/sca/uid_node's fingerprint of the picked node.
"""

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.sca import uid_clean, uid_node
from core.sdis import keys

TTL_S = 300
WAIT_S = 90
POLL_S = 0.5
MAX_BODY = 4096


def candidates(docs: List[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """{"<doc>:<node>": {text, rules, auto, field}} for every on-screen node whose text holds a UID."""
    out: Dict[str, Dict[str, Any]] = {}
    for k, doc in enumerate(docs):
        for i, n in enumerate(doc):
            r = n.get("rect")
            if n.get("pw") or not r or r[2] <= 0 or r[3] <= 0:
                continue
            field = uid_node.pick_field(n)
            text = uid_node.node_text(n, field)
            rules = uid_clean.previews(text)
            if rules:
                out[f"{k}:{i}"] = {"text": text[:120], "rules": rules, "auto": uid_clean.auto_rule(text),
                                   "field": field}
    return out


def wait_for_page(link: str, reader: Any, foreground: Callable[[], int], stop: threading.Event,
                  status: Callable[[str], None] = lambda s: None,
                  timeout: float = WAIT_S) -> Optional[Tuple[List[List[Dict[str, Any]]], str]]:
    """(docs, url) of the first read of the UID page that has a password box and a UID; None on timeout/stop."""
    want = keys.page_link(link)
    end = time.monotonic() + timeout
    last = ""
    while not stop.is_set() and time.monotonic() < end:
        hwnd = foreground()
        msg = "Open the page in your browser (in front), with a client's UID set."
        if hwnd:
            url = reader.read_url(hwnd)
            if want and keys.page_link(url) == want:
                docs = reader.read_docs(hwnd)
                if not uid_node.has_password_box(docs):
                    msg = "The page is open, but it shows no password box yet."
                elif not candidates(docs):
                    msg = "The page is open, but no text on it looks like a UID. Set a client's UID first."
                else:
                    return docs, url
        if msg != last:
            status(msg)
            last = msg
        stop.wait(POLL_S)
    return None


class PickSession:
    """One pick: the server, the page, the answer. on_done(fp) runs on the server's thread."""

    def __init__(self, docs: List[List[Dict[str, Any]]], url: str, service_name: str,
                 on_done: Callable[[Dict[str, Any]], None], ttl: float = TTL_S) -> None:
        from core.uia_render import render
        self.docs, self._on_done = docs, on_done
        self.token = secrets.token_urlsafe(24)
        self.cands = candidates(docs)
        self._used = False
        self._lock = threading.Lock()
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self._server.server_address[1]
        self.path = f"/pick/{self.token}"
        pick = {"service": service_name, "post": self.path, "token": self.token, "cands": self.cands}
        meta = {"title": f"SCA UID picker: {service_name}", "page": keys.page_link(url), "source": "Sera SCA"}
        self.html = render.build(docs, meta, doc="all", pick=pick)[0].encode("utf-8")
        self._ttl = ttl
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.path}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, name="sca-uid-picker", daemon=True)
        self._thread.start()
        timer = threading.Timer(self._ttl, self.stop)
        timer.daemon = True                     # never keeps Sera alive at exit
        timer.start()

    def stop(self) -> None:
        threading.Thread(target=self._server.shutdown, daemon=True).start()

    def accept(self, payload: Any) -> Optional[Dict[str, Any]]:
        """The fingerprint for a valid answer, else None. Only the first valid answer counts."""
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("token", "")), self.token):
            return None
        doc, i, rule = payload.get("doc"), payload.get("i"), payload.get("rule")
        if not (isinstance(doc, int) and isinstance(i, int)) or isinstance(doc, bool) or isinstance(i, bool):
            return None
        cand = self.cands.get(f"{doc}:{i}")
        if cand is None or rule not in cand["rules"]:
            return None
        with self._lock:
            if self._used:
                return None
            self._used = True
        return uid_node.fingerprint(self.docs, doc, i, rule)

    def _handler(self):
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # never log: the page holds client data
                pass

            def _host_ok(self) -> bool:
                return self.headers.get("Host", "") == f"127.0.0.1:{session.port}"

            def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                if not self._host_ok() or self.path != session.path:
                    return self._send(404)
                self._send(200, session.html, "text/html; charset=utf-8")

            def do_POST(self):
                if not self._host_ok() or self.path != session.path:
                    return self._send(404)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if not (0 < n <= MAX_BODY):
                    return self._send(400)
                try:
                    payload = json.loads(self.rfile.read(n).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self._send(400)
                fp = session.accept(payload)
                if fp is None:
                    return self._send(403)
                try:
                    session._on_done(fp)          # before the reply: "Saved" on the page means saved
                    self._send(200, b'{"ok":true}', "application/json")
                except Exception:
                    self._send(500)
                finally:
                    session.stop()

        return Handler
