"""
Runs the real sera_extension/background.js in an embedded V8 (py_mini_racer) with stubbed
browser APIs, to pin how many settings requests the extension sends the desktop app.

ensureConnected() used to call syncSettingsFromDesktop() even while the WebSocket was closed.
That awaits waitForConnection(), which calls ensureConnected() again - and each step runs
synchronously up to its first await, so it recursed until the stack overflowed, leaving one
queued on-open callback per level. When the app's socket opened, every one of them sent a
request_settings (about 4,000 in one burst), and the app built each settings payload on its GUI
thread: a frozen window and high CPU after every app start.
"""
from pathlib import Path

import pytest

MiniRacer = pytest.importorskip("py_mini_racer").MiniRacer

BACKGROUND_JS = Path(__file__).resolve().parents[1] / "sera_extension" / "background.js"

_STUBS = r"""
var self = globalThis;
function setTimeout(fn, ms) { return 0; }
function clearTimeout() {}
function setInterval() { return 0; }
function importScripts() {}
var console = { log(){}, warn(){}, error(){}, info(){}, debug(){} };
self.SeraSCA = { createCoordinator: function() { return { handleDesktopMessage(){} }; } };
function __deep() {
  return new Proxy(function(){}, {
    get(t, k) {
      if (k === 'get') return function(keys, cb) { if (typeof cb === 'function') cb({}); return Promise.resolve({}); };
      if (k === 'set' || k === 'remove') return function(o, cb) { if (typeof cb === 'function') cb(); return Promise.resolve(); };
      if (k === 'lastError') return undefined;
      return __deep();
    },
    apply() { return undefined; },
  });
}
var chrome = __deep();
var document = { getElementById(){ return null; } };
var __sockets = [], __sent = [];
function WebSocket(url) {
  this.readyState = 0; __sockets.push(this);
  this.send = function(s) { __sent.push(JSON.parse(s)); };
  this.close = function() {};
}
WebSocket.CONNECTING = 0; WebSocket.OPEN = 1; WebSocket.CLOSING = 2; WebSocket.CLOSED = 3;
"""


def _load_extension_with_socket_closed():
    ctx = MiniRacer()
    ctx.eval(_STUBS)
    ctx.eval(BACKGROUND_JS.read_text(encoding="utf-8"))
    return ctx


def test_a_closed_socket_does_not_queue_a_settings_request_per_recursion_level():
    ctx = _load_extension_with_socket_closed()
    assert ctx.eval("_wsOpenCallbacks.length") <= 3


def test_opening_the_socket_sends_only_a_handful_of_settings_requests():
    ctx = _load_extension_with_socket_closed()
    ctx.eval("var s = __sockets[0]; s.readyState = WebSocket.OPEN; s.onopen && s.onopen();")
    for _ in range(20):  # let the promise continuations that send the requests run
        ctx.eval("0")
    sent = ctx.eval("__sent.filter(function(m) { return m.type === 'request_settings'; }).length")
    assert 1 <= sent <= 3
