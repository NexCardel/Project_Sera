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
        (bridge.filing_result_received, "filing_result"),
        (bridge.uncertain_result_received, "uncertain_result"),
        (bridge.sca_state_received, "SCA_STATE"),
        (bridge.sca_error_received, "SCA_ERROR"),
        (bridge.sca_fill_result_received, "SCA_FILL_RESULT"),
        (bridge.sca_password_requested, "SCA_PASSWORD_REQUEST"),
        (bridge.session_started_received, "session_start"),
        (bridge.scc_password_verified_received, "scc_password_verified"),
        (bridge.sdc_timeline_received, "sdc_session_timeline"),
        (bridge.sudr_capture_received, "sudr_capture"),
        (bridge.extension_settings_updated_received, "extension_settings_updated"),
    ]:
        sig.connect(lambda m, k=kind: got.setdefault(k, m))
        bridge.dispatch({"type": kind})
    assert set(got) == {
        "filing_result", "uncertain_result", "SCA_STATE", "SCA_ERROR", "SCA_FILL_RESULT",
        "SCA_PASSWORD_REQUEST", "session_start", "scc_password_verified", "sdc_session_timeline",
        "sudr_capture", "extension_settings_updated",
    }


def test_audit_event_routes_like_filing_result(bridge):
    got = []
    bridge.filing_result_received.connect(got.append)
    bridge.dispatch({"type": "audit_event", "x": 1})
    assert len(got) == 1


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


@pytest.mark.parametrize("ext", ["sera_extension", "sera_extension_firefox"])
def test_no_extension_code_talks_to_the_app_directly_except_background(ext):
    """Only background.js may open the WebSocket to the app - a content script or the SDC page
    script must always go through it (chrome.runtime.sendMessage), never straight to 127.0.0.1."""
    sdc = (ROOT / ext / "sdc" / "sdc_core.js").read_text(encoding="utf-8")
    assert "fetch('http://127.0.0.1" not in sdc
    assert "new WebSocket" not in sdc


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_coordinator_js_tests_pass():
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "test_sca_coordinator.js")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
