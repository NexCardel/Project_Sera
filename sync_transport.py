"""
sync_transport.py
-----------------
Mutual-TLS transport and framing for Sera Sync v3 (blueprint §4.3, WP P2-3).

- Both sides present their device certificate (P2-1). The trust store is exactly the
  certificates of the active (non-revoked) members (P2-2), plus this PC's own.
- The member certs are self-signed CA certs, so a chain *issued by* a member would also
  verify. After every handshake the peer certificate's SHA-256 fingerprint must therefore
  equal a member's certificate, and a client also checks that it reached the device it
  meant to reach.
- Frames: 4-byte big-endian length + UTF-8 JSON object with a string ``"t"``, at most
  16 MB. Binary data: a ``{"t": "chunk", "n": <len>}`` frame followed by ``n`` raw bytes.
  ``chunk`` and ``busy`` are reserved frame types.
- A frame must arrive whole within 30 s; a whole session ends after 10 min.
- Server: port 49159, at most 4 sessions; a member connecting beyond that gets a ``busy``
  frame. The TLS handshake runs in the connection's worker thread, never in the accept
  loop. A connection only takes a session slot once its peer is verified as a member;
  unverified connections are limited separately (in total and per source address) and
  get a short handshake timeout, so a device without a member cert can't fill the slots.

Never log frame contents. No PySide6 here. ``cryptography`` / ``sync_admin`` are imported
lazily (only to build a MemberSet).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
from pathlib import Path

SYNC_PORT = 49159
MAX_FRAME_BYTES = 16 * 1024 * 1024
FRAME_TIMEOUT_SECONDS = 30.0
SESSION_DEADLINE_SECONDS = 600.0
MAX_SESSIONS = 4
CHUNK_SIZE = 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10.0
HANDSHAKE_TIMEOUT_SECONDS = 10.0
MAX_PENDING_HANDSHAKES = 16
MAX_PENDING_PER_ADDRESS = 4

FRAME_CHUNK = "chunk"
FRAME_BUSY = "busy"
_RESERVED = frozenset({FRAME_CHUNK, FRAME_BUSY})

_HEADER = struct.Struct(">I")
_IO_PIECE = 64 * 1024
_ACCEPT_POLL_SECONDS = 0.5

_log = logging.getLogger("sera.sync.transport")


class TransportError(Exception):
    """Base class. The session is closed when one of these is raised while receiving."""


class NotAMember(TransportError):
    """The peer's certificate is not an active member's certificate."""


class WrongPeer(TransportError):
    """The peer is a member, but not the device the client meant to reach."""


class FrameTooLarge(TransportError):
    pass


class FrameTimeout(TransportError):
    pass


class SessionDeadline(TransportError):
    pass


class ProtocolError(TransportError):
    pass


class ConnectionClosed(TransportError):
    pass


class Busy(TransportError):
    """The server already has its maximum number of sessions."""


def cert_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


# ---------------------------------------------------------------- members / trust store

class MemberSet:
    """The certificates this PC trusts. Immutable; build a new one when membership changes.

    ``members``: ``(device_id, cert_pem)`` pairs of the active members only (callers pass
    verified, non-revoked records; see ``from_db``). ``own_cert_pem`` is added to the TLS
    trust store but is not accepted as a peer unless it is also in ``members``.
    """

    def __init__(self, members, own_cert_pem):
        fingerprints = {}
        pems = []
        for device_id, cert_pem in members:
            der, pem, actual_id = _parse_cert(cert_pem)
            if actual_id != device_id:
                raise ValueError("member certificate does not belong to device %s" % device_id)
            fingerprints[cert_fingerprint(der)] = device_id
            pems.append(pem)
        _, own_pem, self.own_device_id = _parse_cert(own_cert_pem)
        pems.append(own_pem)
        self._fingerprints = fingerprints
        self.device_ids = frozenset(fingerprints.values())
        self.cadata = "".join(dict.fromkeys(pems))

    @classmethod
    def from_records(cls, records, own_cert_pem) -> "MemberSet":
        """From already-verified ``member`` records (sync_admin); revoked ones are skipped."""
        return cls([(r["device_id"], r["cert_pem"]) for r in records if r.get("revoked_at") is None],
                   own_cert_pem)

    @classmethod
    def from_db(cls, conn, admin_pubkey: str, own_cert_pem) -> "MemberSet":
        """Active members from ``_sync_members``; rows that don't verify with ``admin_pubkey`` are left out."""
        import sync_admin
        return cls.from_records(sync_admin.list_members(conn, admin_pubkey, include_revoked=False), own_cert_pem)

    def device_for_fingerprint(self, fingerprint: str) -> str | None:
        return self._fingerprints.get(fingerprint)


def _parse_cert(cert_pem):
    """``(der, normalised_pem, device_id)`` for one PEM certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    if isinstance(cert_pem, str):
        cert_pem = cert_pem.encode("ascii")
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except Exception:
        raise ValueError("not a PEM certificate") from None
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return cert.public_bytes(serialization.Encoding.DER), pem, hashlib.sha256(spki).hexdigest()[:32]


def _build_context(side: str, cert_chain, members: MemberSet) -> ssl.SSLContext:
    if side == "server":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # No TLS 1.3 session tickets: a resumed session would skip the certificate check.
        ctx.num_tickets = 0
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_mode = ssl.CERT_REQUIRED
    certfile, keyfile, password = cert_chain
    ctx.load_cert_chain(certfile, keyfile, password)
    ctx.load_verify_locations(cadata=members.cadata)
    return ctx


# ---------------------------------------------------------------- session / framing

class Session:
    """One verified connection. Use from one thread; ``close`` may be called from any thread."""

    def __init__(self, sock: ssl.SSLSocket, peer_device_id: str, peer_address, deadline: float,
                 frame_timeout: float, on_close=None):
        self._sock = sock
        self.peer_device_id = peer_device_id
        self.peer_address = peer_address
        self._deadline = deadline
        self._frame_timeout = frame_timeout
        self._on_close = on_close
        self._close_lock = threading.Lock()
        self._closed = False
        self._pending_chunk = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def tls_version(self) -> str | None:
        return self._sock.version()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        try:
            # Plain socket shutdown: wakes a reader blocked in another thread without
            # touching the SSL object it is using.
            socket.socket.shutdown(self._sock, socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        if self._on_close is not None:
            self._on_close(self)

    # -- low level

    def _frame_deadline(self) -> float:
        now = time.monotonic()
        if now >= self._deadline:
            raise SessionDeadline("session deadline reached")
        return min(now + self._frame_timeout, self._deadline)

    def _frame_deadline_or_raise(self) -> float:
        """The session deadline, or ``SessionDeadline`` if it has passed."""
        if time.monotonic() >= self._deadline:
            raise SessionDeadline("session deadline reached")
        return self._deadline

    def _set_timeout(self, frame_deadline: float) -> None:
        now = time.monotonic()
        if now >= self._deadline:
            raise SessionDeadline("session deadline reached")
        if now >= frame_deadline:
            raise FrameTimeout("frame did not arrive in time")
        self._sock.settimeout(frame_deadline - now)

    def _fail(self, exc: TransportError) -> TransportError:
        self.close()
        return exc

    def _recv_into(self, view: memoryview, frame_deadline: float) -> None:
        pos = 0
        while pos < len(view):
            if self._closed:
                raise ConnectionClosed("session closed")
            try:
                self._set_timeout(frame_deadline)
                got = self._sock.recv_into(view[pos:], min(len(view) - pos, _IO_PIECE))
            except TransportError as exc:
                raise self._fail(exc)
            except TimeoutError:
                continue  # _set_timeout raises the right error on the next pass
            except (ssl.SSLError, OSError, ValueError) as exc:
                raise self._fail(ConnectionClosed("connection lost: %s" % type(exc).__name__)) from None
            if got == 0:
                raise self._fail(ConnectionClosed("peer closed the connection"))
            pos += got

    def _recv_exact(self, n: int, frame_deadline: float) -> bytes:
        buf = bytearray(n)
        self._recv_into(memoryview(buf), frame_deadline)
        return bytes(buf)

    def _send_all(self, data) -> None:
        view = memoryview(data)
        frame_deadline = self._frame_deadline()
        while view:
            if self._closed:
                raise ConnectionClosed("session closed")
            try:
                self._set_timeout(frame_deadline)
                sent = self._sock.send(view[:_IO_PIECE])
            except TransportError as exc:
                raise self._fail(exc)
            except TimeoutError:
                continue
            except (ssl.SSLError, OSError, ValueError) as exc:
                raise self._fail(ConnectionClosed("connection lost: %s" % type(exc).__name__)) from None
            view = view[sent:]

    def _send_json(self, obj: dict) -> None:
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(payload) > MAX_FRAME_BYTES:
            raise FrameTooLarge("frame of %d bytes is over the %d byte limit" % (len(payload), MAX_FRAME_BYTES))
        self._send_all(_HEADER.pack(len(payload)) + payload)

    # -- frames

    def send(self, frame: dict) -> None:
        """Send one JSON frame (a dict with a string ``"t"``). Nothing is sent if it's too large."""
        if not isinstance(frame, dict) or not isinstance(frame.get("t"), str):
            raise ValueError('a frame is a dict with a string "t"')
        if frame["t"] in _RESERVED:
            raise ValueError('"%s" is a reserved frame type' % frame["t"])
        self._send_json(frame)

    def recv(self, wait: float | None = None) -> dict:
        """Receive one JSON frame. A ``chunk`` header is returned as is; read its bytes with ``read_chunk``.

        ``wait``: how long to wait for the frame to *start* (default: the frame timeout), e.g.
        while the peer prepares a snapshot. Once it starts, the whole frame must arrive within
        the frame timeout. Always capped by the session deadline.
        Raises ``Busy`` when the server refused the session.
        """
        if self._pending_chunk is not None:
            raise ProtocolError("the previous chunk has not been read")
        if wait is None:
            first_deadline = self._frame_deadline()
        else:
            if not wait > 0:
                raise ValueError("wait must be > 0")
            first_deadline = min(time.monotonic() + wait, self._frame_deadline_or_raise())
        header = bytearray(_HEADER.size)
        self._recv_into(memoryview(header)[:1], first_deadline)
        frame_deadline = self._frame_deadline()
        self._recv_into(memoryview(header)[1:], frame_deadline)
        (length,) = _HEADER.unpack(header)
        if length > MAX_FRAME_BYTES:
            raise self._fail(FrameTooLarge("peer announced a %d byte frame" % length))
        if length == 0:
            raise self._fail(ProtocolError("empty frame"))
        payload = self._recv_exact(length, frame_deadline)
        try:
            frame = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise self._fail(ProtocolError("frame is not UTF-8 JSON")) from None
        if not isinstance(frame, dict) or not isinstance(frame.get("t"), str):
            raise self._fail(ProtocolError('frame is not an object with a string "t"'))
        if frame["t"] == FRAME_BUSY:
            raise self._fail(Busy("peer is busy"))
        if frame["t"] == FRAME_CHUNK:
            n = frame.get("n")
            if type(n) is not int or n < 0:
                raise self._fail(ProtocolError("bad chunk length"))
            if n > MAX_FRAME_BYTES:
                raise self._fail(FrameTooLarge("peer announced a %d byte chunk" % n))
            self._pending_chunk = n
        return frame

    def send_chunk(self, data) -> None:
        if len(data) > MAX_FRAME_BYTES:
            raise FrameTooLarge("chunk of %d bytes is over the %d byte limit" % (len(data), MAX_FRAME_BYTES))
        self._send_json({"t": FRAME_CHUNK, "n": len(data)})
        if len(data):
            self._send_all(data)

    def read_chunk(self, sink) -> int:
        """Stream the bytes of the chunk header just returned by ``recv`` into ``sink.write``."""
        n = self._pending_chunk
        if n is None:
            raise ProtocolError("no chunk header was received")
        self._pending_chunk = None
        frame_deadline = self._frame_deadline()
        buf = bytearray(min(n, _IO_PIECE) or 1)
        left = n
        while left:
            piece = memoryview(buf)[:min(left, len(buf))]
            self._recv_into(piece, frame_deadline)
            try:
                sink.write(piece)
            except BaseException:
                # The rest of the chunk is still unread, so the stream is out of step.
                self.close()
                raise
            left -= len(piece)
        return n

    def send_file(self, path, chunk_size: int = CHUNK_SIZE) -> tuple[int, str]:
        """Stream a file as chunk frames. Returns ``(size, sha256 hex)``. The size is announced by the caller."""
        if not 0 < chunk_size <= MAX_FRAME_BYTES:
            raise ValueError("bad chunk size")
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                self.send_chunk(data)
                h.update(data)
                size += len(data)
        return size, h.hexdigest()

    def recv_file(self, path, size: int) -> str:
        """Receive exactly ``size`` bytes of chunk frames into a new file. Returns its sha256 hex.

        ``path`` must not exist yet. On any failure the partial file is removed.
        """
        if type(size) is not int or size < 0:
            raise ProtocolError("bad file size")
        path = Path(path)
        h = hashlib.sha256()
        received = 0
        ok = False
        with open(path, "xb") as f:
            try:
                class _Sink:
                    @staticmethod
                    def write(data):
                        f.write(data)
                        h.update(data)

                while received < size:
                    frame = self.recv()
                    if frame["t"] != FRAME_CHUNK:
                        raise self._fail(ProtocolError("expected a chunk, got %r" % frame["t"][:32]))
                    if frame["n"] > size - received:
                        raise self._fail(ProtocolError("more file data than announced"))
                    if frame["n"] == 0:
                        raise self._fail(ProtocolError("empty chunk in a file"))
                    received += self.read_chunk(_Sink)
                ok = True
            finally:
                if not ok:
                    f.close()
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        return h.hexdigest()


# ---------------------------------------------------------------- transport

class SyncTransport:
    """Holds this PC's TLS contexts and the current member set; opens and serves sessions."""

    def __init__(self, cert_chain, members: MemberSet, *, frame_timeout: float = FRAME_TIMEOUT_SECONDS,
                 session_deadline: float = SESSION_DEADLINE_SECONDS,
                 handshake_timeout: float = HANDSHAKE_TIMEOUT_SECONDS):
        """``cert_chain`` is ``sync_identity.load_cert_chain_args(app_dir)``."""
        self._cert_chain = tuple(cert_chain)
        self.frame_timeout = frame_timeout
        self.session_deadline = session_deadline
        self.handshake_timeout = handshake_timeout
        self._lock = threading.Lock()
        self._sessions: set[Session] = set()
        self._state = self._build(members)

    def _build(self, members: MemberSet):
        return (members,
                _build_context("server", self._cert_chain, members),
                _build_context("client", self._cert_chain, members))

    @property
    def members(self) -> MemberSet:
        return self._state[0]

    @property
    def contexts(self) -> tuple[ssl.SSLContext, ssl.SSLContext]:
        """``(server, client)``."""
        return self._state[1], self._state[2]

    def update_members(self, members: MemberSet) -> None:
        """Rebuild both contexts for a new member set and close sessions with devices no longer in it."""
        state = self._build(members)
        with self._lock:
            self._state = state
            stale = [s for s in self._sessions if s.peer_device_id not in members.device_ids]
        for session in stale:
            _log.info("closing session with %s: no longer a member", session.peer_device_id)
            session.close()

    # -- sessions

    def _verify_peer(self, ssock: ssl.SSLSocket, expected_device_id: str | None = None) -> str:
        der = ssock.getpeercert(binary_form=True)
        if not der:
            raise NotAMember("peer sent no certificate")
        device_id = self.members.device_for_fingerprint(cert_fingerprint(der))
        if device_id is None:
            raise NotAMember("peer certificate is not a member's certificate")
        if expected_device_id is not None and device_id != expected_device_id:
            raise WrongPeer("reached %s instead of %s" % (device_id, expected_device_id))
        return device_id

    def _register(self, ssock, device_id, address, deadline) -> Session:
        session = Session(ssock, device_id, address, deadline, self.frame_timeout, on_close=self._forget)
        with self._lock:
            # Membership may have changed since the handshake.
            if device_id not in self._state[0].device_ids:
                session._on_close = None
                session.close()
                raise NotAMember("%s is no longer a member" % device_id)
            self._sessions.add(session)
        return session

    def _forget(self, session: Session) -> None:
        with self._lock:
            self._sessions.discard(session)

    def _handshake(self, ctx: ssl.SSLContext, raw: socket.socket, server_side: bool, deadline: float):
        timeout = min(self.handshake_timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise SessionDeadline("session deadline reached")
        raw.settimeout(timeout)
        try:
            return ctx.wrap_socket(raw, server_side=server_side)
        except ssl.SSLCertVerificationError as exc:
            raise NotAMember("certificate not trusted: %s" % (exc.verify_message or exc.reason)) from None
        except TimeoutError:
            raise FrameTimeout("TLS handshake timed out") from None
        except ssl.SSLError as exc:
            raise ConnectionClosed("TLS handshake failed: %s" % (exc.reason or type(exc).__name__)) from None
        except OSError as exc:
            raise ConnectionClosed("TLS handshake failed: %s" % type(exc).__name__) from None

    def connect(self, host: str, port: int, expected_device_id: str,
                connect_timeout: float = CONNECT_TIMEOUT_SECONDS) -> Session:
        """Open a verified session to the member ``expected_device_id`` (required)."""
        if not isinstance(expected_device_id, str) or not expected_device_id:
            raise ValueError("expected_device_id is required")
        deadline = time.monotonic() + self.session_deadline
        _, _, client_ctx = self._state
        try:
            raw = socket.create_connection((host, port), timeout=min(connect_timeout, self.frame_timeout))
        except OSError as exc:
            raise ConnectionClosed("can't connect to %s:%s: %s" % (host, port, type(exc).__name__)) from None
        try:
            ssock = self._handshake(client_ctx, raw, False, deadline)
        except TransportError:
            raw.close()
            raise
        try:
            device_id = self._verify_peer(ssock, expected_device_id)
            return self._register(ssock, device_id, (host, port), deadline)
        except TransportError:
            ssock.close()
            raise

    def serve(self, handler, host: str = "0.0.0.0", port: int = SYNC_PORT, max_sessions: int = MAX_SESSIONS,
              on_reject=None) -> "SyncServer":
        """Start listening. ``handler(session)`` runs in a worker thread per verified session; the
        session is closed when it returns. ``on_reject(address, reason)`` is told about refused
        connections (for the activity log)."""
        server = SyncServer(self, handler, host, port, max_sessions, on_reject)
        server.start()
        return server


class SyncServer:
    def __init__(self, transport: SyncTransport, handler, host: str, port: int, max_sessions: int, on_reject):
        self._transport = transport
        self._handler = handler
        self._on_reject = on_reject
        self._max = max_sessions
        self._lock = threading.Lock()
        self._active = 0            # verified sessions (at most max_sessions)
        self._pending = 0           # connections still in their handshake / member check
        self._pending_by_ip: dict[str, int] = {}
        self._stopped = threading.Event()
        self._sessions: set[Session] = set()
        self._threads: set[threading.Thread] = set()

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                # Windows: no other process may bind the same port and steal connections.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((host, port))
            listener.listen(16)
            listener.settimeout(_ACCEPT_POLL_SECONDS)
        except OSError:
            listener.close()
            raise
        self._listener = listener
        self.address = listener.getsockname()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="sera-sync-accept", daemon=True)

    @property
    def active_sessions(self) -> int:
        """Verified sessions in progress (connections still in their handshake don't count)."""
        with self._lock:
            return self._active

    @property
    def pending_handshakes(self) -> int:
        with self._lock:
            return self._pending

    def start(self) -> None:
        self._accept_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopped.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            sessions = list(self._sessions)
            threads = list(self._threads)
        for session in sessions:
            session.close()
        if self._accept_thread.is_alive() and self._accept_thread is not threading.current_thread():
            self._accept_thread.join(timeout)
        end = time.monotonic() + timeout
        for t in threads:
            if t is not threading.current_thread():
                t.join(max(0.0, end - time.monotonic()))

    def _reject(self, address, reason: str) -> None:
        _log.info("refused sync connection from %s: %s", address[0] if address else "?", reason)
        if self._on_reject is not None:
            try:
                self._on_reject(address, reason)
            except Exception:
                _log.exception("on_reject callback failed")

    def _accept_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                conn, address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stopped.is_set():
                    break
                time.sleep(0.1)
                continue
            ip = address[0]
            with self._lock:
                admit = (self._pending < MAX_PENDING_HANDSHAKES
                         and self._pending_by_ip.get(ip, 0) < MAX_PENDING_PER_ADDRESS)
                if admit:
                    self._pending += 1
                    self._pending_by_ip[ip] = self._pending_by_ip.get(ip, 0) + 1
            if not admit:
                conn.close()
                self._reject(address, "too many pending connections")
                continue
            t = threading.Thread(target=self._run, args=(conn, address), name="sera-sync-session", daemon=True)
            with self._lock:
                self._threads.add(t)
            t.start()

    def _release_pending(self, ip: str) -> None:
        with self._lock:
            self._pending -= 1
            left = self._pending_by_ip.get(ip, 1) - 1
            if left > 0:
                self._pending_by_ip[ip] = left
            else:
                self._pending_by_ip.pop(ip, None)

    def _open(self, conn, address):
        """Handshake + membership check in this worker thread. Returns ``(ssock, device_id, deadline)`` or None."""
        deadline = time.monotonic() + self._transport.session_deadline
        server_ctx = self._transport.contexts[0]
        try:
            ssock = self._transport._handshake(server_ctx, conn, True, deadline)
        except TransportError as exc:
            conn.close()
            self._reject(address, str(exc))
            return None
        try:
            return ssock, self._transport._verify_peer(ssock), deadline
        except TransportError as exc:
            ssock.close()
            self._reject(address, str(exc))
            return None

    def _run(self, conn, address) -> None:
        try:
            try:
                opened = self._open(conn, address)
            finally:
                self._release_pending(address[0])
            if opened is None:
                return
            ssock, device_id, deadline = opened
            with self._lock:
                admit = self._active < self._max and not self._stopped.is_set()
                if admit:
                    self._active += 1
            if not admit:
                self._send_busy(ssock, device_id, address, deadline)
                return
            try:
                self._run_session(ssock, device_id, address, deadline)
            finally:
                with self._lock:
                    self._active -= 1
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())

    def _run_session(self, ssock, device_id, address, deadline) -> None:
        try:
            session = self._transport._register(ssock, device_id, address, deadline)
        except TransportError as exc:
            self._reject(address, str(exc))
            return
        with self._lock:
            self._sessions.add(session)
        try:
            self._handler(session)
        except TransportError as exc:
            _log.info("sync session with %s ended: %s", device_id, exc)
        except Exception:
            _log.exception("sync session handler failed (session with %s)", device_id)
        finally:
            session.close()
            with self._lock:
                self._sessions.discard(session)

    def _send_busy(self, ssock, device_id, address, deadline) -> None:
        """Only verified members get the ``busy`` frame."""
        session = Session(ssock, device_id, address, deadline, self._transport.frame_timeout)
        try:
            session._send_json({"t": FRAME_BUSY})
        except TransportError:
            pass
        finally:
            session.close()
        self._reject(address, "busy (%d sessions)" % self._max)
