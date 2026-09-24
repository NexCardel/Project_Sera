"""
sync_pairing.py
---------------
Pairing a new PC with the office using a 6-digit code (blueprint §4.3, WP P2-4).

Admin side (``PairingWindow``): "Add workstation" makes a random 6-digit code, shown as
``123 456``, and opens a plain TCP listener on 49158 for 5 minutes. One joiner at a time;
3 failed attempts in total close the window, and so does a successful join (one joiner
per code).

Protocol (each message is a JSON frame: 4-byte big-endian length + UTF-8 JSON; bytes are
base64):

1. Both sides: ``s = SPAKE2_Symmetric(code, idSymmetric=b"sera-pair-v1")``, send
   ``{"t": "spake", "m": s.start()}`` and compute ``K = s.finish(peer_m)``.
2. ``k_conf`` / ``k_enc`` = HKDF-SHA256(K) with info ``sera-pair-confirm`` / ``sera-pair-enc``.
3. Admin sends ``HMAC(k_conf, b"A" + transcript)``; the joiner checks it and only then
   sends ``HMAC(k_conf, b"J" + transcript)``. ``transcript = sha256(msgA || msgB)``, the
   admin's SPAKE message first.
4. Joiner -> admin: AES-GCM(k_enc) ``{"cert_pem", "device_name"}``, AAD ``sera-pair-v1|J``.
5. Admin signs a ``member`` record for the joiner (sync_admin) and sends AES-GCM(k_enc)
   ``{"office", "dek", "office_key_recovery", "admin_key_recovery", "members",
   "admin_address"}``, AAD ``sera-pair-v1|A``.
6. Joiner: stores the DEK (DPAPI only; the recovery blobs are saved as received), the
   members (``incoming/join/pairing.json``, and ``conn`` if given) and office.json (last).
   The snapshot download (P2-6) follows.

A connection counts as a failed attempt once the admin has received the joiner's SPAKE
message (that is when a code guess has been made) and the pairing doesn't complete.

Never log the code, K, derived keys or the DEK. No PySide6 here. ``spake2`` and
``cryptography`` are imported lazily.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import sera_keys

PAIRING_PORT = 49158
PAIRING_WINDOW_SECONDS = 300.0
MAX_FAILED_ATTEMPTS = 3
FIRST_MESSAGE_TIMEOUT_SECONDS = 10.0
FRAME_TIMEOUT_SECONDS = 30.0
SESSION_DEADLINE_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10.0
STALL_WARNING_THRESHOLD = 3

PAIR_ID = b"sera-pair-v1"
AAD_JOINER = b"sera-pair-v1|J"
AAD_ADMIN = b"sera-pair-v1|A"
INFO_CONFIRM = b"sera-pair-confirm"
INFO_ENC = b"sera-pair-enc"

# Frames before the peer is authenticated are tiny; the welcome carries all member records.
_MAX_PREAUTH_FRAME = 4 * 1024
_MAX_FRAME = 1024 * 1024
_NONCE_LEN = 12
_MAX_NAME_LEN = 200
_HEADER = struct.Struct(">I")
_ACCEPT_POLL_SECONDS = 0.25
_MAX_BUSY_REFUSERS = 4
_BUSY_LINGER_SECONDS = 2.0

JOIN_DIRNAME = "join"
JOIN_STATE_FILE = "pairing.json"

_log = logging.getLogger("sera.sync.pairing")


class PairingError(Exception):
    """Pairing failed. Messages never contain the code or key material."""


class WrongCode(PairingError):
    """Key confirmation failed: wrong code, or a message was changed in transit."""


class PairingBusy(PairingError):
    """The admin PC is pairing another PC right now."""


class PairingProtocolError(PairingError):
    """A malformed or unexpected message."""


class JoinRefused(PairingError):
    """The join can't go ahead (this PC already belongs to an office, or the admin refused it)."""


# ---------------------------------------------------------------- framing

class _Channel:
    """Length-prefixed JSON frames over a plain socket, with per-frame and session deadlines."""

    def __init__(self, sock: socket.socket, deadline: float):
        self.sock = sock
        self.deadline = deadline

    def _timeout(self, limit: float) -> None:
        left = min(limit, self.deadline) - time.monotonic()
        if left <= 0:
            raise PairingError("timed out")
        self.sock.settimeout(left)

    def _recv_exact(self, n: int, limit: float) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            self._timeout(limit)
            try:
                got = self.sock.recv(n - len(buf))
            except TimeoutError:
                raise PairingError("timed out") from None
            except OSError as exc:
                raise PairingError("connection lost (%s)" % type(exc).__name__) from None
            if not got:
                raise PairingError("the other PC closed the connection")
            buf += got
        return bytes(buf)

    def send(self, frame: dict) -> None:
        payload = json.dumps(frame, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_FRAME:
            raise PairingProtocolError("message too large")
        self._timeout(time.monotonic() + FRAME_TIMEOUT_SECONDS)
        try:
            self.sock.sendall(_HEADER.pack(len(payload)) + payload)
        except TimeoutError:
            raise PairingError("timed out") from None
        except OSError as exc:
            raise PairingError("connection lost (%s)" % type(exc).__name__) from None

    def recv(self, expect: str, max_bytes: int = _MAX_FRAME, wait: float = FRAME_TIMEOUT_SECONDS) -> dict:
        """One frame of type ``expect``. ``busy`` / ``refused`` frames raise the matching error."""
        first = time.monotonic() + wait
        header = self._recv_exact(1, first)
        header += self._recv_exact(_HEADER.size - 1, time.monotonic() + FRAME_TIMEOUT_SECONDS)
        (length,) = _HEADER.unpack(header)
        if not 0 < length <= max_bytes:
            raise PairingProtocolError("bad message length")
        payload = self._recv_exact(length, time.monotonic() + FRAME_TIMEOUT_SECONDS)
        try:
            frame = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise PairingProtocolError("message is not JSON") from None
        if not isinstance(frame, dict) or not isinstance(frame.get("t"), str):
            raise PairingProtocolError("message has no type")
        if frame["t"] == "busy":
            raise PairingBusy("the admin PC is pairing another PC; try again in a minute")
        if frame["t"] == "refused":
            reason = frame.get("reason")
            raise JoinRefused(reason[:200] if isinstance(reason, str) else "the admin PC refused this PC")
        if frame["t"] != expect:
            raise PairingProtocolError("unexpected message")
        return frame

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value, length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise PairingProtocolError("bad field")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise PairingProtocolError("bad field") from None
    if length is not None and len(raw) != length:
        raise PairingProtocolError("bad field length")
    return raw


# ---------------------------------------------------------------- key exchange

class _Keys:
    def __init__(self, k_conf: bytes, k_enc: bytes, transcript: bytes):
        self.k_conf = k_conf
        self.k_enc = k_enc
        self.transcript = transcript

    def mac(self, side: bytes) -> bytes:
        return hmac.new(self.k_conf, side + self.transcript, hashlib.sha256).digest()

    def check_mac(self, side: bytes, value) -> bool:
        try:
            got = _unb64(value)
        except PairingProtocolError:
            return False
        return hmac.compare_digest(self.mac(side), got)

    def seal(self, obj: dict, aad: bytes) -> dict:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(self.k_enc).encrypt(nonce, json.dumps(obj, separators=(",", ":")).encode("utf-8"), aad)
        return {"n": _b64(nonce), "c": _b64(ct)}

    def open(self, frame: dict, aad: bytes) -> dict:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = _unb64(frame.get("n"), _NONCE_LEN)
        ct = _unb64(frame.get("c"))
        try:
            plain = AESGCM(self.k_enc).decrypt(nonce, ct, aad)
        except InvalidTag:
            raise PairingProtocolError("encrypted message was changed in transit") from None
        try:
            obj = json.loads(plain.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise PairingProtocolError("encrypted message is not JSON") from None
        if not isinstance(obj, dict):
            raise PairingProtocolError("encrypted message is not an object")
        return obj


def _spake(code: str):
    from spake2 import SPAKE2_Symmetric
    return SPAKE2_Symmetric(code.encode("ascii"), idSymmetric=PAIR_ID)


def _derive_keys(s, peer_msg: bytes, msg_admin: bytes, msg_joiner: bytes) -> _Keys:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    try:
        K = s.finish(peer_msg)
    except Exception:
        # Not a valid SPAKE2 message (bad point, reflected message, wrong side byte, ...).
        raise PairingProtocolError("invalid key-exchange message") from None
    k_conf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO_CONFIRM).derive(K)
    k_enc = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO_ENC).derive(K)
    return _Keys(k_conf, k_enc, hashlib.sha256(msg_admin + msg_joiner).digest())


def normalize_code(code) -> str:
    """``"123 456"`` / ``"123456"`` -> ``"123456"``. Raises ValueError for anything else."""
    if not isinstance(code, str):
        raise ValueError("the pairing code is 6 digits")
    digits = "".join(code.split())
    if len(digits) != 6 or not all("0" <= ch <= "9" for ch in digits):
        raise ValueError("the pairing code is 6 digits")
    return digits


def _check_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("device name is required")
    name = name.strip()
    if len(name) > _MAX_NAME_LEN:
        raise ValueError("device name is too long")
    return name


# ---------------------------------------------------------------- admin side

class PairingWindow:
    """The admin PC's "Add workstation" window: a code, a listener on 49158, 5 minutes.

    ``open_db()`` returns a new DB-API connection to master.db; it is used (and closed) in
    the worker thread when a joiner is added. ``on_joined(member_record)`` is called after
    the joiner's record is stored; the caller must then rebuild the sync transport's member
    set (``SyncTransport.update_members``). ``on_closed(reason)`` is called once, with
    ``"joined"``, ``"too_many_attempts"``, ``"expired"``, ``"refused"``, ``"failed"`` or
    ``"cancelled"``. For the beacon (P2-5): ``is_open``, ``office_name``, ``port``.
    """

    def __init__(self, app_dir, open_db, admin_device_id: str, *, host: str = "0.0.0.0",
                 port: int = PAIRING_PORT, window_seconds: float = PAIRING_WINDOW_SECONDS,
                 max_failures: int = MAX_FAILED_ATTEMPTS,
                 first_message_timeout: float = FIRST_MESSAGE_TIMEOUT_SECONDS,
                 on_joined=None, on_closed=None):
        self.app_dir = Path(app_dir)
        self._open_db = open_db
        self.admin_device_id = admin_device_id
        self._host = host
        self._port = port
        self.window_seconds = window_seconds
        self.max_failures = max_failures
        self.first_message_timeout = first_message_timeout
        self._on_joined = on_joined
        self._on_closed = on_closed
        self.code = "%06d" % secrets.randbelow(10 ** 6)
        self._lock = threading.Lock()
        self._failed = 0
        self._open = False
        self._closed = False
        self._close_reason = None
        self._active = None          # _Channel of the joiner being handled
        self._refusers = 0           # connections being told "busy"
        self._stalls: dict[str, int] = {}   # ip -> connections that ended before a code was tried
        self._listener = None
        self._threads: list[threading.Thread] = []
        self.office_name = ""
        self.port = None
        self.expires_at = None

    def __repr__(self):
        return "<PairingWindow open=%s port=%s>" % (self._open, self.port)   # never the code

    @property
    def display_code(self) -> str:
        return "%s %s" % (self.code[:3], self.code[3:])

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    @property
    def failed_attempts(self) -> int:
        with self._lock:
            return self._failed

    @property
    def stalled_connections(self) -> dict[str, int]:
        """Per source address: connections that ended without trying a code. For the UI (P2-7):
        an address with ``STALL_WARNING_THRESHOLD`` or more may be blocking pairing."""
        with self._lock:
            return dict(self._stalls)

    @property
    def close_reason(self) -> str | None:
        with self._lock:
            return self._close_reason

    def _check_admin(self) -> None:
        import sync_admin
        office = sera_keys.load_office(self.app_dir)
        if office is None:
            raise sera_keys.KeyUnavailable("no office key on this PC")
        if not office.admin_pubkey:
            raise sync_admin.AdminKeyUnavailable("this office has no admin key")
        conn = self._open_db()
        try:
            current = sync_admin.get_office_admin(conn, office.admin_pubkey)
        finally:
            conn.close()
        if current is None or current["device_id"] != self.admin_device_id:
            raise sync_admin.NotAdmin("this PC is not the office admin PC")
        sync_admin.load_admin_key(self.app_dir)   # raises AdminKeyUnavailable
        self.office_name = office.office_name

    def start(self) -> None:
        """Check this PC may add members, then open the listener. Raises before opening if not."""
        if self._open or self._closed:
            raise RuntimeError("a pairing window can be started only once")
        self._check_admin()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((self._host, self._port))
            listener.listen(4)
            listener.settimeout(_ACCEPT_POLL_SECONDS)
        except OSError:
            listener.close()
            raise
        self._listener = listener
        self.port = listener.getsockname()[1]
        self.expires_at = time.monotonic() + self.window_seconds
        self._open = True
        t = threading.Thread(target=self._accept_loop, name="sera-pairing-accept", daemon=True)
        self._threads.append(t)
        t.start()
        _log.info("pairing window open on port %s for %.0f s", self.port, self.window_seconds)

    def close(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._open = False
            self._close_reason = reason
            active = self._active
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        if active is not None:
            # Whatever the reason, no session may carry on once the code is dead.
            active.close()
        _log.info("pairing window closed: %s", reason)
        if self._on_closed is not None:
            try:
                self._on_closed(reason)
            except Exception:
                _log.exception("on_closed callback failed")

    def _accept_loop(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    return
            if time.monotonic() >= self.expires_at:
                self.close("expired")
                return
            try:
                sock, address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                with self._lock:
                    if self._closed:
                        return
                time.sleep(0.05)
                continue
            deadline = min(time.monotonic() + SESSION_DEADLINE_SECONDS, self.expires_at)
            chan = _Channel(sock, deadline)
            with self._lock:
                closed = self._closed
                admit = self._active is None and not closed
                if admit:
                    self._active = chan
            if closed:
                chan.close()
                return
            if not admit:
                # One joiner at a time. No code guess is made, so nothing is counted.
                with self._lock:
                    refuse = self._refusers < _MAX_BUSY_REFUSERS
                    if refuse:
                        self._refusers += 1
                if refuse:
                    threading.Thread(target=self._refuse_busy, args=(chan,), name="sera-pairing-busy",
                                     daemon=True).start()
                else:
                    chan.close()
                continue
            t = threading.Thread(target=self._handle, args=(chan, address), name="sera-pairing-session",
                                 daemon=True)
            self._threads.append(t)
            t.start()

    def _refuse_busy(self, chan: _Channel) -> None:
        """Send ``busy`` and close gracefully: on Windows, closing with the joiner's first
        message still unread resets the connection before the joiner can read ``busy``."""
        chan.deadline = time.monotonic() + _BUSY_LINGER_SECONDS
        try:
            chan.send({"t": "busy"})
            chan.sock.shutdown(socket.SHUT_WR)
            while True:
                chan._timeout(chan.deadline)
                if not chan.sock.recv(_MAX_PREAUTH_FRAME):
                    break
        except (PairingError, OSError):
            pass
        finally:
            chan.close()
            with self._lock:
                self._refusers -= 1

    def _handle(self, chan: _Channel, address) -> None:
        ip = address[0] if address else "?"
        guessed = False
        outcome = None
        try:
            s = _spake(self.code)
            msg_admin = s.start()
            chan.send({"t": "spake", "m": _b64(msg_admin)})
            frame = chan.recv("spake", _MAX_PREAUTH_FRAME, wait=self.first_message_timeout)
            msg_joiner = _unb64(frame.get("m"))
            guessed = True
            keys = _derive_keys(s, msg_joiner, msg_admin, msg_joiner)
            chan.send({"t": "confirm", "mac": _b64(keys.mac(b"A"))})
            frame = chan.recv("confirm", _MAX_PREAUTH_FRAME)
            if not keys.check_mac(b"J", frame.get("mac")):
                raise WrongCode("key confirmation failed")
            request = keys.open(chan.recv("join", _MAX_PREAUTH_FRAME * 4), AAD_JOINER)
            cert_pem, name = request.get("cert_pem"), request.get("device_name")
            if not isinstance(cert_pem, str) or not isinstance(name, str):
                raise PairingProtocolError("bad join request")
            outcome = self._admit(chan, keys, cert_pem, name, ip)
        except PairingError as exc:
            if guessed:
                self._count_failure(ip, exc)
            else:
                self._note_stall(ip, exc)
        except Exception:
            _log.exception("pairing session from %s failed", ip)
            if guessed:
                self._count_failure(ip, None)
        finally:
            chan.close()
            # Close the window *before* freeing the slot: the accept loop admits a connection
            # only while the slot is free and the window isn't closed, so nothing can get in
            # on this code in between.
            if outcome is not None:
                self.close(outcome)
            with self._lock:
                self._active = None

    def _note_stall(self, ip: str, exc) -> None:
        """A connection that ended before trying a code. Not an attempt (no guess was made),
        but a device that keeps doing it holds the single joiner slot, so say so."""
        with self._lock:
            if self._closed:
                return
            self._stalls[ip] = self._stalls.get(ip, 0) + 1
            count = self._stalls[ip]
        if count >= STALL_WARNING_THRESHOLD:
            _log.warning("%s has connected %d times without trying a code; it may be blocking pairing "
                         "(last: %s)", ip, count, exc)
        else:
            _log.info("pairing connection from %s ended before a code was tried: %s", ip, exc)

    def _count_failure(self, ip: str, exc) -> None:
        with self._lock:
            if self._closed:
                return
            self._failed += 1
            failed = self._failed
        _log.warning("pairing attempt from %s failed (%d of %d): %s", ip, failed, self.max_failures,
                     exc if exc is not None else "internal error")
        if failed >= self.max_failures:
            self.close("too_many_attempts")

    def _admit(self, chan: _Channel, keys: _Keys, cert_pem: str, name: str, ip: str) -> str:
        """Sign the joiner's member record and send the office keys. Returns the close reason."""
        import sync_admin
        try:
            name = _check_name(name)
            sync_admin.device_id_from_cert_pem(cert_pem)
        except (ValueError, sync_admin.InvalidRecord):
            raise PairingProtocolError("bad join request") from None
        office = sera_keys.load_office(self.app_dir)
        conn = self._open_db()
        try:
            try:
                record = sync_admin.add_member(self.app_dir, conn, self.admin_device_id, cert_pem, name)
            except sync_admin.MembershipError as exc:
                _log.warning("pairing from %s refused: %s", ip, exc)
                self._send_refused(chan, str(exc))
                return "refused"
            except (sync_admin.SyncAdminError, sera_keys.SeraKeysError) as exc:
                # E.g. admin handed over, or the admin key can't be read any more. Not a code guess.
                _log.warning("pairing from %s stopped: this PC can't add members (%s)", ip, exc)
                self._send_refused(chan, "the admin PC can't add workstations right now")
                return "failed"
            records = sync_admin.list_members(conn, office.admin_pubkey, include_revoked=True)
            office_admin = sync_admin.get_office_admin(conn, office.admin_pubkey)
        finally:
            conn.close()
        _log.info("added %s (%s, letter %s) from %s", record["device_id"], name, record["token_letter"], ip)
        if self._on_joined is not None:
            try:
                self._on_joined(record)
            except Exception:
                _log.exception("on_joined callback failed")
        try:
            welcome = self._welcome(office, records + ([office_admin] if office_admin else []), chan)
            chan.send(dict(keys.seal(welcome, AAD_ADMIN), t="welcome"))
        except (PairingError, OSError, sera_keys.SeraKeysError) as exc:
            # The record is signed; pairing the same PC again returns it unchanged.
            _log.warning("could not send the office keys to %s: %s", ip, exc)
            return "failed"
        return "joined"

    @staticmethod
    def _send_refused(chan: _Channel, reason: str) -> None:
        try:
            chan.send({"t": "refused", "reason": reason})
        except PairingError:
            pass

    def _welcome(self, office, records, chan: _Channel) -> dict:
        kdir = sera_keys.keys_dir(self.app_dir)
        dek = sera_keys.load_dek(self.app_dir)
        if not hmac.compare_digest(sera_keys.key_id(dek), office.key_id):
            raise sera_keys.KeyFileInvalid("the stored office key does not match office.json")
        blobs = {}
        for key, name in (("office_key_recovery", sera_keys.DEK_RECOVERY_FILE),
                          ("admin_key_recovery", sera_keys.ADMIN_KEY_RECOVERY_FILE)):
            try:
                blobs[key] = json.loads((kdir / name).read_text(encoding="utf-8"))
            except ValueError:
                raise sera_keys.KeyFileInvalid("%s is not valid JSON" % name) from None
        try:
            admin_address = chan.sock.getsockname()[0]
        except OSError:
            admin_address = None
        return {
            "office": {
                "format": sera_keys.OFFICE_FORMAT,
                "office_id": office.office_id,
                "office_name": office.office_name,
                "key_id": office.key_id,
                "admin_pubkey": office.admin_pubkey,
                "device_id": None,          # the joiner fills in its own
                "created_at": office.created_at,
            },
            "dek": _b64(dek),
            "office_key_recovery": blobs["office_key_recovery"],
            "admin_key_recovery": blobs["admin_key_recovery"],
            "members": records,
            "admin_address": admin_address,
        }


# ---------------------------------------------------------------- joiner side

@dataclass
class JoinResult:
    office_id: str
    office_name: str
    key_id: str
    device_id: str
    token_letter: str
    admin_device_id: str
    admin_address: str
    records: list = field(default_factory=list)   # verified member + office_admin records


@dataclass
class _Welcome:
    office: sera_keys.OfficeInfo
    dek: bytes
    office_key_recovery: dict
    admin_key_recovery: dict
    records: list
    own_record: dict
    admin_device_id: str
    admin_address: str


def _check_wrap_blob(blob) -> dict:
    if not isinstance(blob, dict) or blob.get("format") != sera_keys.WRAP_FORMAT or blob.get("kdf") != "argon2id":
        raise PairingProtocolError("bad recovery data")
    return blob


def _check_welcome(payload: dict, identity, host: str) -> _Welcome:
    """Validate everything the admin sent before anything is written."""
    import sync_admin
    if not isinstance(payload, dict):
        raise PairingProtocolError("bad welcome")
    o = payload.get("office")
    if not isinstance(o, dict) or o.get("format") != sera_keys.OFFICE_FORMAT:
        raise PairingProtocolError("bad office information")
    for name in ("office_id", "office_name", "key_id", "admin_pubkey"):
        if not isinstance(o.get(name), str) or not o[name]:
            raise PairingProtocolError("office information is missing %s" % name)
    dek = _unb64(payload.get("dek"), sera_keys.DEK_LEN)
    if not hmac.compare_digest(sera_keys.key_id(dek), o["key_id"]):
        raise PairingProtocolError("the office key does not match its key id")
    office_rec = _check_wrap_blob(payload.get("office_key_recovery"))
    admin_rec = _check_wrap_blob(payload.get("admin_key_recovery"))

    raw_records = payload.get("members")
    if not isinstance(raw_records, list) or not raw_records:
        raise PairingProtocolError("bad member list")
    records, own, office_admin = [], None, None
    own_cert = identity.cert_pem.decode("ascii") if isinstance(identity.cert_pem, bytes) else identity.cert_pem
    for raw in raw_records:
        try:
            rec = sync_admin.verify_record(raw, o["admin_pubkey"])
        except sync_admin.InvalidRecord as exc:
            raise PairingProtocolError("a member record does not verify: %s" % exc) from None
        if rec["type"] == sync_admin.RECORD_OFFICE_ADMIN:
            if office_admin is not None:
                raise PairingProtocolError("two office_admin records")
            office_admin = rec
        elif rec["type"] == sync_admin.RECORD_MEMBER:
            if rec["device_id"] == identity.device_id:
                own = rec
        else:
            raise PairingProtocolError("unexpected record type")
        records.append(rec)
    if own is None or own["revoked_at"] is not None or own["cert_pem"] != own_cert:
        raise PairingProtocolError("the admin PC did not add this PC")
    if office_admin is None:
        raise PairingProtocolError("no office_admin record")
    admin_member = next((r for r in records if r["type"] == sync_admin.RECORD_MEMBER
                         and r["device_id"] == office_admin["device_id"] and r["revoked_at"] is None), None)
    if admin_member is None:
        raise PairingProtocolError("the office admin PC is not an active member")

    admin_address = host
    value = payload.get("admin_address")
    if isinstance(value, str):
        try:
            ip = ipaddress.ip_address(value)
            if not (ip.is_unspecified or ip.is_multicast):
                admin_address = str(ip)
        except ValueError:
            pass

    office = sera_keys.OfficeInfo(office_id=o["office_id"], office_name=o["office_name"], key_id=o["key_id"],
                                  admin_pubkey=o["admin_pubkey"], device_id=identity.device_id)
    if isinstance(o.get("created_at"), str) and o["created_at"]:
        office.created_at = o["created_at"]
    return _Welcome(office, dek, office_rec, admin_rec, records, own, office_admin["device_id"], admin_address)


def _install(app_dir: Path, w: _Welcome, conn) -> None:
    """Write what pairing delivered. office.json is written last: it switches the PC to office mode."""
    import sync_admin
    kdir = sera_keys.keys_dir(app_dir)
    kdir.mkdir(parents=True, exist_ok=True)
    if (kdir / sera_keys.OFFICE_FILE).exists():
        raise JoinRefused("this PC already belongs to an office")
    # Computed before any write, so a DPAPI failure writes nothing.
    dpapi_blob = sera_keys.dpapi_protect(w.dek, sera_keys.ENTROPY_OFFICE_KEY)
    sera_keys._replace_key_file(kdir / sera_keys.DEK_RECOVERY_FILE,
                                (json.dumps(w.office_key_recovery, indent=2) + "\n").encode("utf-8"))
    sera_keys._replace_key_file(kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE,
                                (json.dumps(w.admin_key_recovery, indent=2) + "\n").encode("utf-8"))
    sera_keys._replace_key_file(kdir / sera_keys.DEK_DPAPI_FILE, dpapi_blob)
    join_dir = Path(app_dir) / "incoming" / JOIN_DIRNAME
    join_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "office_id": w.office.office_id,
        "key_id": w.office.key_id,
        "admin_device_id": w.admin_device_id,
        "admin_address": w.admin_address,
        "records": w.records,
    }
    sera_keys._replace_key_file(join_dir / JOIN_STATE_FILE, (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    if conn is not None:
        # After the key files (a failure leaves key files without office.json, which the next
        # join backs up and replaces) and before office.json (which switches on office mode).
        # store_record returning False only means an equal or newer record is already there;
        # what matters is that this PC ends up an active member in that database.
        with sync_admin._transaction(conn):
            for rec in w.records:
                sync_admin.store_record(conn, rec, w.office.admin_pubkey)
            mine = sync_admin.get_member(conn, w.own_record["device_id"], w.office.admin_pubkey)
            if mine is None or mine["revoked_at"] is not None:
                raise JoinRefused("the database given for the member records says this PC was removed")
    sera_keys.save_office(app_dir, w.office)


def join_office(app_dir, host: str, code, device_name: str, *, port: int = PAIRING_PORT,
                timeout: float = CONNECT_TIMEOUT_SECONDS, conn=None) -> JoinResult:
    """Pair this PC with the admin PC at ``host`` using the code shown there.

    Creates this PC's device identity if needed, runs the protocol, then stores the DEK
    (DPAPI), the received recovery blobs, the member records (``incoming/join/pairing.json``,
    and ``conn`` if given) and office.json. The snapshot download (P2-6) comes next.
    Raises ``WrongCode``, ``PairingBusy``, ``JoinRefused``, ``PairingProtocolError`` or
    ``PairingError``; on any error nothing of the office is written.
    """
    import sync_identity
    code = normalize_code(code)
    device_name = _check_name(device_name)
    app_dir = Path(app_dir)
    if sera_keys.load_office(app_dir) is not None:
        raise JoinRefused("this PC already belongs to an office")
    identity = sync_identity.ensure_device_identity(app_dir)

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise PairingError("can't reach the admin PC at %s:%s (%s)" % (host, port, type(exc).__name__)) from None
    chan = _Channel(sock, time.monotonic() + SESSION_DEADLINE_SECONDS)
    try:
        s = _spake(code)
        msg_joiner = s.start()
        chan.send({"t": "spake", "m": _b64(msg_joiner)})
        frame = chan.recv("spake", _MAX_PREAUTH_FRAME, wait=timeout)
        msg_admin = _unb64(frame.get("m"))
        try:
            keys = _derive_keys(s, msg_admin, msg_admin, msg_joiner)
        except PairingProtocolError:
            raise WrongCode("the code doesn't match (or the connection was tampered with)") from None
        frame = chan.recv("confirm", _MAX_PREAUTH_FRAME)
        if not keys.check_mac(b"A", frame.get("mac")):
            raise WrongCode("the code doesn't match (or the connection was tampered with)")
        chan.send({"t": "confirm", "mac": _b64(keys.mac(b"J"))})
        chan.send(dict(keys.seal({"cert_pem": identity.cert_pem.decode("ascii"), "device_name": device_name},
                                 AAD_JOINER), t="join"))
        welcome = _check_welcome(keys.open(chan.recv("welcome"), AAD_ADMIN), identity, host)
    finally:
        chan.close()

    _install(app_dir, welcome, conn)
    _log.info("joined office %s as %s (letter %s)", welcome.office.office_id, identity.device_id,
              welcome.own_record["token_letter"])
    return JoinResult(
        office_id=welcome.office.office_id,
        office_name=welcome.office.office_name,
        key_id=welcome.office.key_id,
        device_id=identity.device_id,
        token_letter=welcome.own_record["token_letter"],
        admin_device_id=welcome.admin_device_id,
        admin_address=welcome.admin_address,
        records=welcome.records,
    )
