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
WEAK_WAIT_S = 5      # a page whose only UID-like texts are plain words is read only after this long
STRONG_RULES = ("GSTIN", "PAN", "EMAIL")
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
                strong = field == "value" or any(r in rules for r in STRONG_RULES)
                out[f"{k}:{i}"] = {"text": text[:120], "rules": rules, "auto": uid_clean.auto_rule(text),
                                   "field": field, "strong": strong}
    return out


def _strong(cands: Dict[str, Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    """The candidates that are surely a UID: a box's typed value, or a GSTIN / PAN / email in a text. Most
    one-word labels ("Login", "Username") pass the LOGIN_ID rule too, so they alone do not mean that
    staff have set the UID yet."""
    return tuple(sorted((k, c["text"]) for k, c in cands.items() if c.get("strong")))


def wait_for_page(link: str, reader: Any, foreground: Callable[[], int], stop: threading.Event,
                  status: Callable[[str], None] = lambda s: None, timeout: float = WAIT_S,
                  weak_wait: float = WEAK_WAIT_S,
                  clock: Callable[[], float] = time.monotonic) -> Optional[Tuple[List[List[Dict[str, Any]]], str]]:
    """(docs, url) of the UID page with a password box and a UID set; None on timeout/stop.

    The read is taken only once a box holds a typed UID, or a text shows a GSTIN / PAN / email, and it
    reads the same twice in a row (a UID still being typed is not taken). A page whose only UID-like texts
    are plain words (a two-page login showing a login id) is taken after weak_wait seconds on it."""
    want = keys.page_link(link)
    end = clock() + timeout
    last, prev, since = "", None, None
    while not stop.is_set() and clock() < end:
        hwnd = foreground()
        msg = "Open the page in your browser (in front), with a client's UID set."
        if hwnd:
            url = reader.read_url(hwnd)
            if want and keys.page_link(url) == want:
                docs = reader.read_docs(hwnd)
                cands = candidates(docs) if uid_node.has_password_box(docs) else {}
                strong = _strong(cands)
                since = clock() if since is None else since
                if not uid_node.has_password_box(docs):
                    msg = "The page is open, but it shows no password box yet."
                    prev = None
                elif strong:
                    if strong == prev:
                        return docs, url
                    prev = strong
                    msg = "UID found, checking that it is complete..."
                elif cands and clock() - since >= weak_wait:
                    return docs, url
                else:
                    prev = None
                    msg = "The page is open. Type a client's UID in the username box (or open the page that shows it)."
            else:
                since, prev = None, None
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
