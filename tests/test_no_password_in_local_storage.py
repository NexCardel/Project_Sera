"""
Guards finding #2 (W2-1): manualAssistPayload / mecpPayload / sccActiveAttempt /
activeAutofillPayload carry a plaintext password and must never be written to
chrome.storage.local (on disk) in either extension build. They live in
chrome.storage.session (or the in-memory _passwordStore fallback) instead.

Scans both background.js source files for every chrome.storage.local.set(...) call
and flags one whose object literal carries a password-shaped key.
"""
from pathlib import Path

import pytest

BACKGROUND_JS_FILES = [
    Path(__file__).resolve().parents[1] / "sera_extension" / "background.js",
    Path(__file__).resolve().parents[1] / "sera_extension_firefox" / "background.js",
]

# Keys that are known to carry a plaintext password.
PASSWORD_KEYS = ["manualAssistPayload", "mecpPayload", "sccActiveAttempt", "activeAutofillPayload"]


def _extract_local_set_calls(source):
    """Returns the argument text of every chrome.storage.local.set(...) call."""
    calls = []
    needle = "storage.local.set("
    start = 0
    while True:
        idx = source.find(needle, start)
        if idx == -1:
            break
        args_start = idx + len(needle) - 1  # points at the opening '('
        depth = 0
        i = args_start
        while i < len(source):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        calls.append(source[args_start:i + 1])
        start = i + 1
    return calls


@pytest.mark.parametrize("path", BACKGROUND_JS_FILES, ids=lambda p: p.parent.name)
def test_no_storage_local_set_call_carries_a_password(path):
    source = path.read_text(encoding="utf-8")
    calls = _extract_local_set_calls(source)
    assert calls, f"expected to find at least one chrome.storage.local.set(...) call in {path}"
    for call in calls:
        for key in PASSWORD_KEYS:
            assert key not in call, (
                f"{path}: chrome.storage.local.set(...) carries {key!r}, "
                f"which holds a plaintext password and must stay off disk: {call!r}"
            )
        assert "password:" not in call.replace(" ", ""), (
            f"{path}: chrome.storage.local.set(...) carries a literal 'password' field: {call!r}"
        )


@pytest.mark.parametrize("path", BACKGROUND_JS_FILES, ids=lambda p: p.parent.name)
def test_startup_clears_stale_local_password_keys(path):
    source = path.read_text(encoding="utf-8")
    assert "storage.local.remove(" in source
    for key in PASSWORD_KEYS:
        assert key in source, f"{path}: startup cleanup should still delete stale {key!r} from storage.local"
