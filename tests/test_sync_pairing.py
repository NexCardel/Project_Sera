"""Tests for sync_pairing.py (Sera Sync v3, WP P2-4): pairing with a 6-digit code (SPAKE2).

Everything runs on 127.0.0.1 with PCs under pytest's tmp_path; the real data folder is
never touched (§0 rule 2). Membership is stored with a plain sqlite3 connection (the table
layout is the same inside the encrypted master.db).
"""

import ast
import base64
import json
import logging
import socket
import sqlite3
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

import sera_keys
import sync_admin
import sync_identity

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only

import sync_pairing  # noqa: E402
from sync_pairing import PairingBusy, PairingError, PairingWindow, WrongCode, join_office  # noqa: E402

MASTER_PW = "OfficeMaster#2026"
_HEADER = struct.Struct(">I")


# ------------------------------------------------------------------ helpers

def _make_office(app: Path) -> sera_keys.OfficeInfo:
    dek = sera_keys.new_dek()
    info = sera_keys.OfficeInfo(office_id=sera_keys.OfficeInfo.new_office_id(),
                                office_name="Test Office", key_id=sera_keys.key_id(dek))
    sera_keys.store_dek(app, dek, MASTER_PW, info.office_id)
    sera_keys.save_office(app, info)
    return info


class AdminPC:
    def __init__(self, app: Path):
        self.app = app
        app.mkdir(parents=True, exist_ok=True)
        self.office = _make_office(app)
        self.pubkey = sync_admin.create_admin_key(app, MASTER_PW)
        self.identity = sync_identity.ensure_device_identity(app)
        self.device_id = self.identity.device_id
        self.db_path = app / "master.db"
        conn = self.open_db()
        sync_admin.init_office_membership(app, conn, self.identity.cert_pem.decode("ascii"), "Front desk")
        conn.close()
        self.joined = []
        self.closed = []

    def open_db(self):
        return sqlite3.connect(str(self.db_path))

    def members(self, include_revoked=True):
        conn = self.open_db()
        try:
            return sync_admin.list_members(conn, self.pubkey, include_revoked=include_revoked)
        finally:
            conn.close()

    def window(self, **kw) -> PairingWindow:
        kw.setdefault("host", "127.0.0.1")
        kw.setdefault("port", 0)
        w = PairingWindow(self.app, self.open_db, self.device_id,
                          on_joined=self.joined.append, on_closed=self.closed.append, **kw)
        w.start()
        return w


@pytest.fixture
def admin(tmp_path):
    return AdminPC(tmp_path / "admin_pc")


@pytest.fixture
def windows():
    opened = []
    yield opened
    for w in opened:
        w.close()


def _open(admin_pc, windows, **kw) -> PairingWindow:
    w = admin_pc.window(**kw)
    windows.append(w)
    return w


def _wrong(code: str) -> str:
    return "%06d" % ((int(code) + 1) % 10 ** 6)


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _key_files(app: Path) -> dict:
    kdir = sera_keys.keys_dir(app)
    if not kdir.exists():
        return {}
    return {p.name: p.read_bytes() for p in sorted(kdir.iterdir()) if p.is_file()}


def _no_office_written(app: Path) -> bool:
    names = set(_key_files(app))
    return not (names & {sera_keys.OFFICE_FILE, sera_keys.DEK_DPAPI_FILE, sera_keys.DEK_RECOVERY_FILE,
                         sera_keys.ADMIN_KEY_RECOVERY_FILE})


# -- raw frame helpers (for tampering proxies / hand-driven clients)

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        got = sock.recv(n - len(buf))
        if not got:
            raise ConnectionError("closed")
        buf += got
    return buf


def _read_frame(sock) -> bytes:
    (n,) = _HEADER.unpack(_recv_exact(sock, 4))
    return _recv_exact(sock, n)


def _write_frame(sock, payload: bytes):
    sock.sendall(_HEADER.pack(len(payload)) + payload)


class TamperProxy:
    """Relays frames between a joiner and the admin; ``tamper(direction, index, frame_dict)`` may edit them.

    direction is "j2a" (joiner -> admin) or "a2j"; index counts frames per direction.
    """

    def __init__(self, target_port, tamper):
        self.target_port = target_port
        self.tamper = tamper
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _pipe(self, src, dst, direction):
        i = 0
        try:
            while True:
                frame = json.loads(_read_frame(src))
                frame = self.tamper(direction, i, frame)
                i += 1
                _write_frame(dst, json.dumps(frame).encode())
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _run(self):
        client, _ = self.listener.accept()
        upstream = socket.create_connection(("127.0.0.1", self.target_port))
        threading.Thread(target=self._pipe, args=(client, upstream, "j2a"), daemon=True).start()
        threading.Thread(target=self._pipe, args=(upstream, client, "a2j"), daemon=True).start()

    def close(self):
        self.listener.close()


def _b64flip(value: str) -> str:
    raw = bytearray(base64.b64decode(value))
    raw[-1] ^= 0x01
    return base64.b64encode(bytes(raw)).decode()


# ------------------------------------------------------------------ Accept: success path

def test_pairing_success(admin, windows, tmp_path):
    w = _open(admin, windows)
    joiner = tmp_path / "joiner_pc"
    result = join_office(joiner, "127.0.0.1", w.code, "Back office", port=w.port)

    # The joiner ends with an identical key_id (office.json and the stored DEK).
    j_office = sera_keys.load_office(joiner)
    assert j_office.key_id == admin.office.key_id == result.key_id
    assert sera_keys.key_id(sera_keys.load_dek(joiner)) == admin.office.key_id
    assert sera_keys.load_dek(joiner) == sera_keys.load_dek(admin.app)
    assert j_office.office_id == admin.office.office_id
    assert j_office.admin_pubkey == admin.pubkey

    # office.json names the joiner's own device, never the admin's.
    j_identity = sync_identity.load_device_identity(joiner)
    assert j_identity is not None
    assert j_office.device_id == j_identity.device_id == result.device_id != admin.device_id

    # Recovery blobs saved exactly as received; no admin_key.dpapi on the joiner.
    kdir_a, kdir_j = sera_keys.keys_dir(admin.app), sera_keys.keys_dir(joiner)
    for name in (sera_keys.DEK_RECOVERY_FILE, sera_keys.ADMIN_KEY_RECOVERY_FILE):
        assert json.loads((kdir_j / name).read_text()) == json.loads((kdir_a / name).read_text())
    assert not (kdir_j / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()

    # Admin signed a member record for the joiner with the next letter.
    members = admin.members()
    assert [m["token_letter"] for m in members] == ["A", "B"]
    rec = members[1]
    assert rec["device_id"] == j_identity.device_id
    assert rec["name"] == "Back office"
    assert rec["cert_pem"] == j_identity.cert_pem.decode("ascii")
    assert result.token_letter == "B"
    assert admin.joined and admin.joined[0]["device_id"] == j_identity.device_id

    # The joiner got every record, verified, incl. office_admin; saved for the snapshot step.
    assert result.admin_device_id == admin.device_id
    assert result.admin_address == "127.0.0.1"
    types = sorted(r["type"] for r in result.records)
    assert types == ["member", "member", "office_admin"]
    saved = json.loads((joiner / "incoming" / "join" / "pairing.json").read_text())
    assert saved["admin_device_id"] == admin.device_id
    assert len(saved["records"]) == 3
    for r in saved["records"]:
        sync_admin.verify_record(r, admin.pubkey)

    # One joiner per code: the window is closed afterwards.
    assert _wait(lambda: not w.is_open)
    assert w.close_reason == "joined"
    assert admin.closed == ["joined"]


def test_success_stores_records_in_given_connection(admin, windows, tmp_path):
    w = _open(admin, windows)
    joiner = tmp_path / "joiner_pc"
    joiner.mkdir()
    conn = sqlite3.connect(str(joiner / "staging.db"))
    try:
        join_office(joiner, "127.0.0.1", w.code, "Back office", port=w.port, conn=conn)
        assert len(sync_admin.list_members(conn, admin.pubkey)) == 2
        assert sync_admin.get_office_admin(conn, admin.pubkey)["device_id"] == admin.device_id
    finally:
        conn.close()


def test_code_format(admin, windows, tmp_path):
    w = _open(admin, windows)
    assert len(w.code) == 6 and w.code.isdigit()
    assert w.display_code == "%s %s" % (w.code[:3], w.code[3:])
    # "123 456" as shown on screen is accepted.
    join_office(tmp_path / "j", "127.0.0.1", w.display_code, "Back office", port=w.port)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "12a456", None, 123456])
def test_bad_code_rejected_before_connecting(tmp_path, bad):
    with pytest.raises(ValueError):
        join_office(tmp_path / "j", "127.0.0.1", bad, "Back office", port=1)
    assert _no_office_written(tmp_path / "j")


# ------------------------------------------------------------------ Accept: wrong code

def test_wrong_code_fails_and_counts_attempt(admin, windows, tmp_path):
    w = _open(admin, windows)
    joiner = tmp_path / "joiner_pc"
    with pytest.raises(WrongCode):
        join_office(joiner, "127.0.0.1", _wrong(w.code), "Back office", port=w.port)
    assert _wait(lambda: w.failed_attempts == 1)
    assert w.is_open
    assert _no_office_written(joiner)
    assert len(admin.members()) == 1
    # The right code still works afterwards.
    join_office(joiner, "127.0.0.1", w.code, "Back office", port=w.port)
    assert len(admin.members()) == 2


def test_three_failures_close_window(admin, windows, tmp_path):
    w = _open(admin, windows)
    for i in range(3):
        with pytest.raises(WrongCode):
            join_office(tmp_path / ("j%d" % i), "127.0.0.1", _wrong(w.code), "Back office", port=w.port)
    assert _wait(lambda: not w.is_open)
    assert w.failed_attempts == 3
    assert w.close_reason == "too_many_attempts"
    # Even the right code is refused now.
    with pytest.raises(PairingError):
        join_office(tmp_path / "late", "127.0.0.1", w.code, "Back office", port=w.port, timeout=3)
    assert _no_office_written(tmp_path / "late")
    assert len(admin.members()) == 1


def test_attempt_counted_when_joiner_drops_after_admin_confirm(admin, windows):
    """A guesser who reads the admin's confirmation and hangs up still used up an attempt."""
    from spake2 import SPAKE2_Symmetric
    w = _open(admin, windows)
    s = SPAKE2_Symmetric(_wrong(w.code).encode(), idSymmetric=b"sera-pair-v1")
    sock = socket.create_connection(("127.0.0.1", w.port))
    try:
        _write_frame(sock, json.dumps({"t": "spake", "m": base64.b64encode(s.start()).decode()}).encode())
        assert json.loads(_read_frame(sock))["t"] == "spake"
        assert json.loads(_read_frame(sock))["t"] == "confirm"
    finally:
        sock.close()
    assert _wait(lambda: w.failed_attempts == 1)


def test_connection_without_a_guess_is_not_counted(admin, windows, tmp_path):
    w = _open(admin, windows, first_message_timeout=0.3)
    sock = socket.create_connection(("127.0.0.1", w.port))
    sock.close()
    assert _wait(lambda: w.stalled_connections.get("127.0.0.1") == 1)
    sock = socket.create_connection(("127.0.0.1", w.port))   # silent until timeout
    time.sleep(0.6)
    sock.close()
    time.sleep(0.1)
    assert w.failed_attempts == 0 and w.is_open
    assert _wait(lambda: w.stalled_connections == {"127.0.0.1": 2})
    join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=w.port)


def test_repeated_stalls_from_one_address_are_warned_about(admin, windows, caplog):
    caplog.set_level(logging.INFO, logger="sera.sync.pairing")
    w = _open(admin, windows, first_message_timeout=0.2)
    for _ in range(sync_pairing.STALL_WARNING_THRESHOLD):
        sock = socket.create_connection(("127.0.0.1", w.port))
        assert _wait(lambda: w.busy)
        assert _wait(lambda: not w.busy)
        sock.close()
    assert w.failed_attempts == 0 and w.is_open
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "without trying a code" in r.getMessage()]
    assert len(warnings) == 1 and "127.0.0.1" in warnings[0].getMessage()


def test_garbage_spake_message_counts_as_attempt(admin, windows):
    w = _open(admin, windows)
    sock = socket.create_connection(("127.0.0.1", w.port))
    try:
        _write_frame(sock, json.dumps({"t": "spake", "m": base64.b64encode(b"S" + b"\x00" * 32).decode()}).encode())
        try:
            while True:
                _read_frame(sock)
        except (OSError, ConnectionError):
            pass
    finally:
        sock.close()
    assert _wait(lambda: w.failed_attempts == 1)
    assert len(w.code) == 6


# ------------------------------------------------------------------ Accept: tampering

def test_modified_spake_message_fails_key_confirmation(admin, windows, tmp_path):
    """A valid-looking but different SPAKE message (same code) makes the keys differ: confirmation fails."""
    from spake2 import SPAKE2_Symmetric
    w = _open(admin, windows)

    def tamper(direction, i, frame):
        if direction == "j2a" and frame.get("t") == "spake":
            other = SPAKE2_Symmetric(w.code.encode(), idSymmetric=b"sera-pair-v1")
            frame = dict(frame, m=base64.b64encode(other.start()).decode())
        return frame

    proxy = TamperProxy(w.port, tamper)
    try:
        with pytest.raises(WrongCode):
            join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=proxy.port)
    finally:
        proxy.close()
    assert _wait(lambda: w.failed_attempts == 1)
    assert len(admin.members()) == 1
    assert _no_office_written(tmp_path / "j")


@pytest.mark.parametrize("target", [("j2a", "spake"), ("a2j", "spake"), ("a2j", "confirm"), ("j2a", "confirm"),
                                    ("j2a", "join"), ("a2j", "welcome")])
def test_flipped_bit_in_transit_fails(admin, windows, tmp_path, target):
    w = _open(admin, windows)
    field = {"spake": "m", "confirm": "mac", "join": "c", "welcome": "c"}

    def tamper(direction, i, frame):
        if (direction, frame.get("t")) == target:
            key = field[frame["t"]]
            frame = dict(frame, **{key: _b64flip(frame[key])})
        return frame

    proxy = TamperProxy(w.port, tamper)
    try:
        with pytest.raises(PairingError):
            join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=proxy.port, timeout=5)
    finally:
        proxy.close()
    assert _no_office_written(tmp_path / "j")
    if target != ("a2j", "welcome"):
        # Nothing was signed before the joiner's encrypted request was authenticated.
        assert _wait(lambda: w.failed_attempts == 1)
        assert len(admin.members()) == 1


# ------------------------------------------------------------------ one joiner per code / window life

def test_second_joiner_refused_on_same_code(admin, windows, tmp_path):
    w = _open(admin, windows)
    join_office(tmp_path / "j1", "127.0.0.1", w.code, "Back office", port=w.port)
    with pytest.raises(PairingError):
        join_office(tmp_path / "j2", "127.0.0.1", w.code, "Upstairs", port=w.port, timeout=3)
    assert _no_office_written(tmp_path / "j2")
    assert len(admin.members()) == 2


@pytest.mark.parametrize("second_code", ["right", "wrong"])
def test_no_second_session_between_join_and_close(admin, windows, tmp_path, monkeypatch, second_code):
    """Review finding: the slot was freed before the window was marked closed, so a PC waiting
    in that gap could pair on the same code (or make an uncounted guess)."""
    w = _open(admin, windows)
    real_close = PairingWindow.close

    def slow_close(self, reason="cancelled"):
        if reason == "joined":
            time.sleep(1.0)       # widen the gap between the join finishing and the window closing
        real_close(self, reason)

    monkeypatch.setattr(PairingWindow, "close", slow_close)
    join_office(tmp_path / "j1", "127.0.0.1", w.code, "Back office", port=w.port)
    code = w.code if second_code == "right" else _wrong(w.code)
    with pytest.raises(PairingError):
        join_office(tmp_path / "j2", "127.0.0.1", code, "Upstairs", port=w.port, timeout=3)
    assert _wait(lambda: not w.is_open)
    assert w.close_reason == "joined"
    assert _no_office_written(tmp_path / "j2")
    assert [m["token_letter"] for m in admin.members()] == ["A", "B"]


def test_close_ends_a_running_session_whatever_the_reason(admin, windows):
    w = _open(admin, windows, first_message_timeout=60)
    holder = socket.create_connection(("127.0.0.1", w.port))
    try:
        assert _wait(lambda: w.busy)
        w.close("too_many_attempts")
        assert _wait(lambda: not w.busy, timeout=2)
    finally:
        holder.close()


def test_one_joiner_at_a_time(admin, windows, tmp_path):
    # Long first-message timeout: under a loaded full-suite run a 5 s one could free the
    # holder's slot before the joiner connects, and the joiner would then pair (seen once).
    w = _open(admin, windows, first_message_timeout=60)
    holder = socket.create_connection(("127.0.0.1", w.port))
    try:
        assert _wait(lambda: w.busy)
        with pytest.raises(PairingBusy):
            join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=w.port)
        assert w.failed_attempts == 0 and w.is_open
    finally:
        holder.close()
    assert _wait(lambda: not w.busy)
    join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=w.port)


def test_window_expires(admin, windows, tmp_path):
    w = _open(admin, windows, window_seconds=0.5)
    assert w.is_open
    assert _wait(lambda: not w.is_open, timeout=3)
    assert w.close_reason == "expired"
    with pytest.raises(PairingError):
        join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=w.port, timeout=3)
    assert _no_office_written(tmp_path / "j")


def test_close_cancels(admin, windows, tmp_path):
    w = _open(admin, windows)
    w.close()
    assert not w.is_open and w.close_reason == "cancelled"
    with pytest.raises(PairingError):
        join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=w.port, timeout=3)


def test_window_requires_named_admin_with_key(admin, tmp_path):
    (sera_keys.keys_dir(admin.app) / sera_keys.ADMIN_KEY_DPAPI_FILE).unlink()
    with pytest.raises(sync_admin.SyncAdminError):
        PairingWindow(admin.app, admin.open_db, admin.device_id, host="127.0.0.1", port=0).start()
    other = sync_identity.ensure_device_identity(tmp_path / "other").device_id
    with pytest.raises(sync_admin.NotAdmin):
        PairingWindow(admin.app, admin.open_db, other, host="127.0.0.1", port=0).start()


def test_admin_key_lost_while_open_is_not_a_wrong_code(admin, windows, tmp_path):
    w = _open(admin, windows)
    (sera_keys.keys_dir(admin.app) / sera_keys.ADMIN_KEY_DPAPI_FILE).unlink()
    with pytest.raises(sync_pairing.JoinRefused):
        join_office(tmp_path / "j", "127.0.0.1", w.code, "Back office", port=w.port)
    assert _wait(lambda: not w.is_open)
    assert w.close_reason == "failed" and w.failed_attempts == 0
    assert _no_office_written(tmp_path / "j")
    assert len(admin.members()) == 1


def test_removed_device_is_refused(admin, windows, tmp_path):
    w = _open(admin, windows)
    joiner = tmp_path / "j"
    join_office(joiner, "127.0.0.1", w.code, "Back office", port=w.port)
    j_id = sync_identity.load_device_identity(joiner).device_id
    conn = admin.open_db()
    try:
        sync_admin.revoke_member(admin.app, conn, admin.device_id, j_id)
    finally:
        conn.close()
    # Same device identity, fresh key folder (office files moved away): refused, nothing written.
    fresh = tmp_path / "j_again"
    sera_keys.keys_dir(fresh).mkdir(parents=True)
    for name in (sync_identity.DEVICE_CERT_FILE, sync_identity.DEVICE_KEY_FILE,
                 sync_identity.DEVICE_KEY_PASS_DPAPI_FILE):
        (sera_keys.keys_dir(fresh) / name).write_bytes((sera_keys.keys_dir(joiner) / name).read_bytes())
    w2 = _open(admin, windows)
    with pytest.raises(sync_pairing.JoinRefused):
        join_office(fresh, "127.0.0.1", w2.code, "Back office", port=w2.port)
    assert _no_office_written(fresh)
    assert _wait(lambda: not w2.is_open)


# ------------------------------------------------------------------ joiner-side checks

def test_join_refused_when_already_in_an_office(admin, windows, tmp_path):
    w = _open(admin, windows)
    joiner = tmp_path / "j"
    joiner.mkdir()
    _make_office(joiner)
    before = _key_files(joiner)
    with pytest.raises(sync_pairing.JoinRefused):
        join_office(joiner, "127.0.0.1", w.code, "Back office", port=w.port)
    assert _key_files(joiner) == before
    assert w.failed_attempts == 0 and w.is_open


def _welcome_for(admin_pc, joiner_identity, **over):
    """A welcome payload as the admin would send it, for checking the joiner's validation."""
    conn = admin_pc.open_db()
    try:
        rec = sync_admin.add_member(admin_pc.app, conn, admin_pc.device_id,
                                    joiner_identity.cert_pem.decode("ascii"), "Back office")
        records = sync_admin.list_members(conn, admin_pc.pubkey) + [sync_admin.get_office_admin(conn, admin_pc.pubkey)]
    finally:
        conn.close()
    kdir = sera_keys.keys_dir(admin_pc.app)
    office = sera_keys.load_office(admin_pc.app)
    payload = {
        "office": {"format": 1, "office_id": office.office_id, "office_name": office.office_name,
                   "key_id": office.key_id, "admin_pubkey": office.admin_pubkey, "device_id": None,
                   "created_at": office.created_at},
        "dek": base64.b64encode(sera_keys.load_dek(admin_pc.app)).decode(),
        "office_key_recovery": json.loads((kdir / sera_keys.DEK_RECOVERY_FILE).read_text()),
        "admin_key_recovery": json.loads((kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE).read_text()),
        "members": records,
        "admin_address": "127.0.0.1",
    }
    payload.update(over)
    return payload, rec


def test_welcome_validation(admin, tmp_path):
    ident = sync_identity.ensure_device_identity(tmp_path / "j")
    good, _ = _welcome_for(admin, ident)
    parsed = sync_pairing._check_welcome(good, ident, "127.0.0.1")
    assert parsed.office.key_id == admin.office.key_id
    assert parsed.office.device_id == ident.device_id

    def bad(**over):
        payload = json.loads(json.dumps(good))
        payload.update(over)
        with pytest.raises(sync_pairing.PairingProtocolError):
            sync_pairing._check_welcome(payload, ident, "127.0.0.1")

    bad(dek=base64.b64encode(sera_keys.new_dek()).decode())          # DEK doesn't match key_id
    bad(dek="not base64!")
    bad(office=dict(good["office"], admin_pubkey=None))
    bad(office=dict(good["office"], format=2))
    bad(office_key_recovery="x")
    bad(admin_key_recovery=None)
    bad(members=[r for r in good["members"] if r["type"] != "office_admin"])
    bad(members=[r for r in good["members"] if r.get("device_id") != ident.device_id])
    forged = [dict(r, name="Evil") if r.get("device_id") == ident.device_id else r for r in good["members"]]
    bad(members=forged)
    bad(members="nope")

    # A bogus admin_address falls back to the address we connected to.
    parsed = sync_pairing._check_welcome(dict(good, admin_address="0.0.0.0"), ident, "10.0.0.5")
    assert parsed.admin_address == "10.0.0.5"
    parsed = sync_pairing._check_welcome(dict(good, admin_address=None), ident, "10.0.0.5")
    assert parsed.admin_address == "10.0.0.5"


def test_install_writes_database_after_key_files_and_checks_it(admin, tmp_path):
    """Review finding 4: the records go into ``conn`` after the key files and before office.json,
    and a database that says this PC was removed stops the join before office mode is switched on."""
    joiner = tmp_path / "j"
    ident = sync_identity.ensure_device_identity(joiner)
    payload, _ = _welcome_for(admin, ident)
    parsed = sync_pairing._check_welcome(payload, ident, "127.0.0.1")
    conn_a = admin.open_db()
    try:
        revoked = sync_admin.revoke_member(admin.app, conn_a, admin.device_id, ident.device_id)
    finally:
        conn_a.close()
    conn = sqlite3.connect(str(joiner / "staging.db"))
    try:
        sync_admin.store_record(conn, revoked, admin.pubkey)
        conn.commit()
        with pytest.raises(sync_pairing.JoinRefused):
            sync_pairing._install(joiner, parsed, conn)
        assert not (sera_keys.keys_dir(joiner) / sera_keys.OFFICE_FILE).exists()
        assert (sera_keys.keys_dir(joiner) / sera_keys.DEK_DPAPI_FILE).exists()   # key files came first
        assert sync_admin.get_member(conn, ident.device_id, admin.pubkey)["revoked_at"] is not None
    finally:
        conn.close()


# ------------------------------------------------------------------ hygiene

def test_secrets_never_logged(admin, windows, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    w = _open(admin, windows)
    code = w.code
    with pytest.raises(WrongCode):
        join_office(tmp_path / "bad", "127.0.0.1", _wrong(code), "Back office", port=w.port)
    join_office(tmp_path / "j", "127.0.0.1", code, "Back office", port=w.port)
    _wait(lambda: not w.is_open)
    text = caplog.text + " ".join(str(r.args) for r in caplog.records)
    dek = sera_keys.load_dek(admin.app)
    for secret in (code, w.display_code, dek.hex(), base64.b64encode(dek).decode()):
        assert secret not in text
    assert "wrong" in caplog.text.lower() or "failed" in caplog.text.lower()


def test_no_pyside6_or_top_level_heavy_imports():
    path = Path(sync_pairing.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "PySide6" not in names
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert not top & {"spake2", "cryptography", "sync_identity", "sync_admin"}


def test_spake2_in_requirements():
    req = (Path(sync_pairing.__file__).parent / "requirements.txt").read_text(encoding="utf-8")
    assert any(line.strip().lower().startswith("spake2") for line in req.splitlines())
