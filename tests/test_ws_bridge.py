"""
WSBridge checks (2026-09-22). Replaces native messaging (native_host/) and the port-49152 HTTP
listener (ui/extension_listener.py, ipc_auth.py) with one local WebSocket - see
docs/app-extension-communication-report.md and the WebSocket-bridge decision in project memory
for why. Identifiers and passwords below are fictional.
"""
import base64
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ui.ws_bridge import WSBridge, _chrome_extension_id, _load_chrome_extension_id

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ Chrome extension id
def test_chrome_id_is_32_lowercase_a_to_p_and_deterministic():
    key = base64.b64encode(b"a fake DER-encoded public key, 32+ bytes long for the test").decode()
    a = _chrome_extension_id(key)
    b = _chrome_extension_id(key)
    assert a == b
    assert len(a) == 32
    assert all(c in "abcdefghijklmnop" for c in a)


def test_chrome_id_matches_hand_computed_sha256():
    key = base64.b64encode(b"another fake key material used only to check the mapping").decode()
    der = base64.b64decode(key)
    expected_hex = hashlib.sha256(der).digest()[:16].hex()
    expected = "".join(chr(ord("a") + int(ch, 16)) for ch in expected_hex)
    assert _chrome_extension_id(key) == expected


def test_chrome_id_from_bad_key_is_none():
    assert _chrome_extension_id("not valid base64!!") is None


def test_loads_the_real_shipped_manifest_key():
    manifest = ROOT / "sera_extension" / "manifest.json"
    assert _load_chrome_extension_id(manifest) is not None


# ------------------------------------------------------------------ origin trust
@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from ui import ws_bridge as mod
    monkeypatch.setattr(mod, "_firefox_pairing_path", lambda: tmp_path / "firefox_origin.txt")
    b = WSBridge()
    b._chrome_id = "abcdefghijklmnopabcdefghijklmnop"
    b.settings_provider = lambda: {"status": "ok", "registered_pans": ["ABCPD1234E"]}
    return b


def test_a_website_cannot_pass_as_the_extension(bridge):
    assert not bridge._origin_allowed("https://evil.example")
    assert not bridge._origin_allowed("")
    assert not bridge._origin_allowed(None)


def test_the_real_chrome_extension_origin_is_allowed(bridge):
    assert bridge._origin_allowed("chrome-extension://abcdefghijklmnopabcdefghijklmnop")
    assert not bridge._origin_allowed("chrome-extension://someoneelsesextensionid00000000")


def test_firefox_is_trusted_on_first_connect_then_pinned(bridge):
    first = "moz-extension://11111111-1111-1111-1111-111111111111"
    other = "moz-extension://22222222-2222-2222-2222-222222222222"
    assert bridge._origin_allowed(first)          # nothing paired yet - trusted once
    assert bridge._origin_allowed(first)           # same origin again - fine
    assert not bridge._origin_allowed(other)       # a different origin - refused


def test_chrome_id_override_file_wins(tmp_path, monkeypatch):
    from ui import ws_bridge as mod
    override = tmp_path / "chrome_extension_id_override.txt"
    override.write_text("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz\n", encoding="ascii")
    monkeypatch.setattr(WSBridge, "_chrome_id_override", staticmethod(lambda: override.read_text().strip()))
    b = WSBridge()
    assert b._chrome_id_override() == "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"


# ------------------------------------------------------------------ dispatch routing
def test_dispatch_routes_each_message_type(bridge):
    got = {}
    for sig, kind in [
        (bridge.sca_state_received, "SCA_STATE"),
        (bridge.sca_error_received, "SCA_ERROR"),
        (bridge.sca_fill_result_received, "SCA_FILL_RESULT"),
        (bridge.sca_password_requested, "SCA_PASSWORD_REQUEST"),
        (bridge.extension_settings_updated_received, "extension_settings_updated"),
    ]:
        sig.connect(lambda m, k=kind: got.setdefault(k, m))
        bridge.dispatch({"type": kind})
    assert set(got) == {
        "SCA_STATE", "SCA_ERROR", "SCA_FILL_RESULT",
        "SCA_PASSWORD_REQUEST", "extension_settings_updated",
    }


def test_scc_password_verified_from_a_browser_is_not_a_message_any_more(bridge):
    assert not hasattr(bridge, "scc_password_verified_received")
    assert bridge.dispatch({"type": "scc_password_verified", "password": "x"}) is None


def test_scc_row_worked_reaches_the_desktop_with_only_the_attempt_and_the_label(bridge):
    got = []
    bridge.scc_row_worked_received.connect(got.append)
    bridge.dispatch({"type": "scc_row_worked", "attempt_id": "att-1", "row_label": "Combo 2", "extra": "x"})
    assert got == [{"type": "scc_row_worked", "attempt_id": "att-1", "row_label": "Combo 2"}]


@pytest.mark.parametrize("msg", [
    {"type": "scc_row_worked"},
    {"type": "scc_row_worked", "attempt_id": "", "row_label": "Combo 2"},
    {"type": "scc_row_worked", "attempt_id": "att-1", "row_label": ""},
    {"type": "scc_row_worked", "attempt_id": "att-1", "row_label": "x" * 101},
    {"type": "scc_row_worked", "attempt_id": "a" * 65, "row_label": "Combo 2"},
    {"type": "scc_row_worked", "attempt_id": 5, "row_label": "Combo 2"},
    {"type": "scc_row_worked", "attempt_id": "att-1", "row_label": ["Combo 2"]},
])
def test_a_malformed_scc_row_worked_is_dropped(bridge, msg):
    got = []
    bridge.scc_row_worked_received.connect(got.append)
    assert bridge.dispatch(msg) is None
    assert got == []


def test_scc_row_none_reaches_the_desktop_with_only_the_attempt(bridge):
    got = []
    bridge.scc_row_none_received.connect(got.append)
    bridge.dispatch({"type": "scc_row_none", "attempt_id": "att-1", "row_label": "Combo 2"})
    bridge.dispatch({"type": "scc_row_none"})
    bridge.dispatch({"type": "scc_row_none", "attempt_id": "a" * 65})
    assert got == [{"type": "scc_row_none", "attempt_id": "att-1"}]


def test_scc_card_closed_reaches_the_desktop(bridge):
    got = []
    bridge.scc_card_closed_received.connect(got.append)
    bridge.dispatch({"type": "scc_card_closed", "attempt_id": "att-1"})
    bridge.dispatch({"type": "scc_card_closed"})
    assert got == [{"type": "scc_card_closed", "attempt_id": "att-1"}]


def test_extension_tracking_message_types_are_no_longer_routed(bridge):
    """Part A removed all tracking from the extension (autofill-tweaks W1-1); these message
    types can no longer arrive from it, so the bridge no longer acts on them (W1-2)."""
    got = []
    bridge.filing_result_received.connect(got.append)
    for kind in ("filing_result", "audit_event", "uncertain_result", "session_start",
                 "sdc_session_timeline", "sudr_capture"):
        assert bridge.dispatch({"type": kind}) is None
    assert got == []


def test_request_settings_replies_with_the_provided_payload(bridge):
    reply = bridge.dispatch({"type": "request_settings"})
    assert reply["status"] == "ok"
    assert reply["registered_pans"] == ["ABCPD1234E"]
    assert reply["type"] == "settings_response"


def test_sca_ack_clears_the_pending_automation_ack(bridge, monkeypatch):
    import automation
    calls = []
    monkeypatch.setattr(automation, "register_ack", lambda cmd_id: calls.append(cmd_id))
    bridge.dispatch({"type": "SCA_ACK", "command_id": "cmd-1"})
    assert calls == ["cmd-1"]


# ------------------------------------------------------------------ reply routing (SCA_PASSWORD_REQUEST)
def test_a_password_reply_goes_only_to_the_socket_that_asked(bridge):
    asking_sock = MagicMock()
    other_sock = MagicMock()
    bridge._sockets = [asking_sock, other_sock]
    bridge._pending_replies["req-7"] = asking_sock

    ok = bridge.reply({"_id": "req-7"}, {"type": "SCA_PASSWORD_GRANT", "password": "Pw#1"})

    assert ok is True
    asking_sock.sendTextMessage.assert_called_once()
    other_sock.sendTextMessage.assert_not_called()
    assert "req-7" not in bridge._pending_replies      # answered once, then forgotten


def test_a_reply_with_no_matching_request_is_dropped(bridge):
    assert bridge.reply({"_id": "no-such-id"}, {"type": "SCA_PASSWORD_GRANT"}) is False


def test_disconnecting_forgets_its_pending_replies(bridge):
    sock = MagicMock()
    bridge._sockets = [sock]
    bridge._pending_replies["req-9"] = sock
    bridge._on_disconnected(sock)
    assert "req-9" not in bridge._pending_replies
    assert bridge.reply({"_id": "req-9"}, {"type": "x"}) is False


# ------------------------------------------------------------------ broadcast / send_first
def test_broadcast_reaches_every_connected_browser(bridge):
    a, b = MagicMock(), MagicMock()
    bridge._sockets = [a, b]
    assert bridge.broadcast({"type": "update_settings"}) == 2
    a.sendTextMessage.assert_called_once()
    b.sendTextMessage.assert_called_once()


def test_send_first_reaches_only_one_socket(bridge):
    a, b = MagicMock(), MagicMock()
    bridge._sockets = [a, b]
    assert bridge.send_first({"type": "autofill"}) is True
    a.sendTextMessage.assert_called_once()
    b.sendTextMessage.assert_not_called()


def test_send_first_with_nobody_connected(bridge):
    assert bridge.send_first({"type": "autofill"}) is False


# ------------------------------------------------------------------ late SCA arm replay
def _live_arm(cmd="cmd-late", ttl_ms=60_000):
    return {"type": "SCA_ARM_REQUEST", "command_id": cmd, "sent_at": int(time.time() * 1000),
            "arm": {"expires_at": int(time.time() * 1000) + ttl_ms}}


def test_a_browser_connecting_late_still_receives_the_live_sca_arm(bridge):
    assert bridge.broadcast(_live_arm()) == 0
    late = MagicMock()
    bridge._replay_sca_arm(late)
    late.sendTextMessage.assert_called_once()
    assert json.loads(late.sendTextMessage.call_args[0][0])["command_id"] == "cmd-late"


def test_a_new_socket_is_armed_as_soon_as_it_connects(bridge):
    bridge.broadcast(_live_arm("cmd-connect"))
    sock = MagicMock()
    sock.origin.return_value = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    server = MagicMock()
    server.hasPendingConnections.side_effect = [True, False]
    server.nextPendingConnection.return_value = sock
    bridge._server = server

    bridge._on_new_connection()

    sock.sendTextMessage.assert_called_once()
    assert json.loads(sock.sendTextMessage.call_args[0][0])["command_id"] == "cmd-connect"


def test_an_expired_sca_arm_is_not_replayed(bridge):
    bridge.broadcast(_live_arm(ttl_ms=-1))
    late = MagicMock()
    bridge._replay_sca_arm(late)
    late.sendTextMessage.assert_not_called()
    assert bridge._last_sca_arm is None


def test_a_disarm_forgets_the_cached_sca_arm(bridge):
    bridge.broadcast(_live_arm())
    bridge.broadcast({"type": "SCA_DISARM_REQUEST"})
    late = MagicMock()
    bridge._replay_sca_arm(late)
    late.sendTextMessage.assert_not_called()


def test_other_broadcasts_keep_the_cached_sca_arm(bridge):
    bridge.broadcast(_live_arm())
    bridge.broadcast({"type": "update_settings", "sca_enabled": True})
    late = MagicMock()
    bridge._replay_sca_arm(late)
    late.sendTextMessage.assert_called_once()


# ------------------------------------------------------------------ cross-thread safety
# automation.py calls broadcast()/send_first() from plain threading.Thread workers (autofill
# push, SCA arming, extension settings pushes - see _attempt_send/arm_sca/
# update_extension_settings there), never from the Qt thread WSBridge and its QWebSockets live
# on. A MagicMock socket doesn't have Qt thread affinity, so it can't reproduce the original bug
# by itself (calling sendTextMessage() on a real QWebSocket from a foreign thread silently drops
# the frame instead of raising - see ui/ws_bridge.py's _call_on_qt_thread); what these pin
# instead is the contract that fixes it: the actual send always runs on the bridge's own Qt
# thread, and the synchronous int/bool return value survives the trip, however many threads
# call in concurrently.
def test_broadcast_runs_the_actual_send_on_the_qt_thread_not_the_caller(bridge):
    import threading
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    qt_thread = bridge.thread()

    seen_threads = []
    sock = MagicMock()
    sock.sendTextMessage.side_effect = lambda *_a: seen_threads.append(QThread.currentThread())
    bridge._sockets = [sock]

    result = {}

    def call_from_background_thread():
        result["sent"] = bridge.broadcast({"type": "update_settings"})

    caller = threading.Thread(target=call_from_background_thread)
    caller.start()
    app = QApplication.instance()
    deadline = time.monotonic() + 5
    while caller.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    caller.join(timeout=0.5)

    assert not caller.is_alive(), "broadcast() from a background thread never returned"
    assert result.get("sent") == 1
    assert seen_threads == [qt_thread], "the actual send did not run on the Qt/GUI thread"


def test_send_first_runs_the_actual_send_on_the_qt_thread_not_the_caller(bridge):
    import threading
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    qt_thread = bridge.thread()

    seen_threads = []
    sock = MagicMock()
    sock.sendTextMessage.side_effect = lambda *_a: seen_threads.append(QThread.currentThread())
    bridge._sockets = [sock]

    result = {}

    def call_from_background_thread():
        result["ok"] = bridge.send_first({"type": "autofill"})

    caller = threading.Thread(target=call_from_background_thread)
    caller.start()
    app = QApplication.instance()
    deadline = time.monotonic() + 5
    while caller.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    caller.join(timeout=0.5)

    assert not caller.is_alive(), "send_first() from a background thread never returned"
    assert result.get("ok") is True
    assert seen_threads == [qt_thread], "the actual send did not run on the Qt/GUI thread"


def test_concurrent_background_broadcasts_each_get_their_own_result(bridge):
    """Two automation.py workers can legitimately call broadcast() around the same time (e.g.
    an SCA arm retry loop and an autofill push). Guards against a regression that shares one
    result slot across calls (a race one caller's result could overwrite another's, or a stale
    hand-off), which a single-threaded test can't exercise."""
    import threading
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    a, b = MagicMock(), MagicMock()
    bridge._sockets = [a, b]

    results = {}

    def call(name, payload):
        results[name] = bridge.broadcast(payload)

    callers = [
        threading.Thread(target=call, args=(f"caller-{i}", {"type": "update_settings", "i": i}))
        for i in range(5)
    ]
    for c in callers:
        c.start()
    app = QApplication.instance()
    deadline = time.monotonic() + 5
    while any(c.is_alive() for c in callers) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    for c in callers:
        c.join(timeout=0.5)

    assert all(not c.is_alive() for c in callers)
    assert results == {f"caller-{i}": 2 for i in range(5)}


# ------------------------------------------------------------------ extension parity (carried over from
# the old SCA v2 transport tests - unrelated to the transport itself)
@pytest.mark.parametrize("rel", ["sca/sca_coordinator.js", "content_scripts/login.js", "content_scripts/sca_adapters.js"])
def test_chrome_and_firefox_share_the_sca_files(rel):
    chrome = (ROOT / "sera_extension" / rel).read_bytes().replace(b"\r\n", b"\n")
    firefox = (ROOT / "sera_extension_firefox" / rel).read_bytes().replace(b"\r\n", b"\n")
    assert chrome == firefox, f"{rel} differs - copy sera_extension/{rel} to the Firefox build"


@pytest.mark.parametrize("ext", ["sera_extension", "sera_extension_firefox"])
def test_no_extension_code_stores_or_broadcasts_passwords_for_sca(ext):
    bg = (ROOT / ext / "background.js").read_text(encoding="utf-8")
    assert "armedSCAPayload =" not in bg and "services[0]" not in bg
    login = (ROOT / ext / "content_scripts" / "login.js").read_text(encoding="utf-8")
    assert "sera_sca_filled" not in login.replace("// Removed 2026-09-22: the per-tab \"sera_sca_filled\"", "")
    assert "armedSCAPayload" not in login


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_coordinator_js_tests_pass():
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "test_sca_coordinator.js")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr


def test_sck_edit_request_reaches_the_desktop(bridge):
    received = []
    bridge.sck_edit_requested.connect(received.append)
    bridge.dispatch({
        "type": "sck_edit_request",
        "service_id": 3,
        "client_id": 42,
        "portal": "GST Portal",
    })
    assert len(received) == 1
    assert received[0] == {
        "type": "sck_edit_request",
        "service_id": 3,
        "client_id": 42,
        "portal": "GST Portal",
    }


def test_sck_edit_request_with_no_client_id(bridge):
    received = []
    bridge.sck_edit_requested.connect(received.append)
    bridge.dispatch({
        "type": "sck_edit_request",
        "service_id": 5,
        "portal": "Income Tax",
    })
    assert len(received) == 1
    assert received[0]["service_id"] == 5
    assert received[0]["client_id"] is None
    assert received[0]["portal"] == "Income Tax"


def test_sck_candidate_reaches_the_desktop(bridge):
    """The card's Fill & Test hands the candidate to the desktop, which saves it on success."""
    received = []
    bridge.sck_candidate_received.connect(received.append)
    bridge.dispatch({
        "type": "sck_candidate",
        "service_id": 3,
        "client_id": 42,
        "portal": "GST Portal",
        "password": "s3cret-new",
    })
    assert received == [{
        "type": "sck_candidate",
        "service_id": 3,
        "client_id": 42,
        "portal": "GST Portal",
        "password": "s3cret-new",
    }]


def test_sck_candidate_without_a_client_id_still_arrives(bridge):
    """A failure that could not be matched to a client still reaches the card's save path."""
    received = []
    bridge.sck_candidate_received.connect(received.append)
    bridge.dispatch({"type": "sck_candidate", "service_id": 5, "password": "p",
                     "portal": "  Income Tax  "})
    assert len(received) == 1
    assert received[0]["client_id"] is None
    assert received[0]["portal"] == "Income Tax"


def test_sck_candidate_accepts_a_password_at_the_bound(bridge):
    received = []
    bridge.sck_candidate_received.connect(received.append)
    bridge.dispatch({"type": "sck_candidate", "service_id": 3, "password": "x" * 256})
    assert len(received) == 1
    assert received[0]["password"] == "x" * 256


@pytest.mark.parametrize("msg", [
    {"type": "sck_candidate", "password": "p"},                        # no service id
    {"type": "sck_candidate", "service_id": 0, "password": "p"},       # not a positive id
    {"type": "sck_candidate", "service_id": 3},                        # no password
    {"type": "sck_candidate", "service_id": 3, "password": ""},        # empty password
    {"type": "sck_candidate", "service_id": 3, "password": "x" * 257},  # too long
    {"type": "sck_candidate", "service_id": 3, "password": 5},         # not a string
    {"type": "sck_candidate", "service_id": 3, "password": "p", "client_id": 0},
    {"type": "sck_candidate", "service_id": 3, "password": "p", "portal": "x" * 101},
    {"type": "sck_candidate", "service_id": 3, "password": "p", "portal": 7},
])
def test_a_malformed_sck_candidate_is_dropped(bridge, msg):
    received = []
    bridge.sck_candidate_received.connect(received.append)
    bridge.dispatch(msg)
    assert received == []


def test_sck_widget_done_reports_a_dashboard_close(bridge):
    received = []
    bridge.sck_widget_closed_received.connect(received.append)
    bridge.dispatch({"type": "sck_widget_done", "service_id": 9})
    assert received == [{"type": "sck_widget_done", "service_id": 9, "reason": "dashboard"}]
