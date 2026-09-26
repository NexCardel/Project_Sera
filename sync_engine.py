"""
sync_engine.py
--------------
Sera Sync v3 session protocol and scheduler (blueprint §5, WP P3-5).

A session runs over a mutual-TLS ``sync_transport.Session`` (P2-3). Frames (``"t"``):

  hello   {proto, schema_version, device_id, office_tag, vectors: {stream: max_seq},
           members_rev, addresses: [...], mode, accepts}
  bye     {reason, ...}              reason: done | schema | proto | office | identity | busy |
                                             bad_batch | apply_failed | protocol
  members {records: [...]}           signed member / office_admin records (P2-2)
  changes {stream: "master"|"raw", items: [...]}   at most 500 changes / about 1 MB
  ack     {vectors}                  after each applied batch
  done    {}                         end of one side's changes
  need_snapshot {streams: [...]}     peer is below our compaction floor (P4-4; floor is 0 today)
  digest_request {}                  shadow-mode replica convergence check (P3-7a)
  digest_reply   {vectors, digest} | {busy: true}

Order: both HELLO (initiator first) -> MEMBERS both ways if ``members_rev`` differs -> the
initiator's CHANGES batches (each waits for an ACK), DONE -> the responder's, DONE -> BYE both
ways with the final vectors. The peer's final vectors go to ``_sync_peer_vectors`` and the
address that worked to ``_local_addresses`` (P2-5).

What is sent: ``_sync_changes`` rows (own and forwarded) with ``origin_seq`` above the peer's
vector for that stream, ordered by ``(hlc, origin, origin_seq)``, ``data`` as stored. Each batch
is applied in one transaction (P3-4 ``apply_batch``) together with the vector update, so an
interrupted session resumes from the vectors at the next round. The sender walks a cursor
instead of re-reading the peer's vector after each ACK, so a change the receiver can't take
(e.g. a forged admin change it rejects) is sent once per session, not forever.

Remote changes are applied only in mode ``live``. In ``shadow`` they go to ``shadow_apply``
(P3-7 provides it); without one this PC doesn't accept changes (HELLO ``accepts: false``) and
peers don't send any. In mode ``off`` sessions still exchange HELLO and membership records.

Scheduler: every 20 s +- 5 s each reachable member, one at a time; a UDP poke after a local seal
(``{"magic":"sera-sync-v3","office":tag,"dev":id,"poke":true}``) makes the member sync with this
PC within 1 s (debounced 1 s). At most one session per peer and 3 in total, both directions.
When both PCs dial each other at once, the one with the smaller device id keeps its session.

A stream whose vector can't advance (this PC holds later changes of it, but not the next one,
for ``STALL_SECONDS``) is reported by ``stalled_streams()`` and an ``on_event("stalled", ...)``,
and nothing is skipped (owner decision 2026-09-25).

Peer digest exchange (P3-7a, blueprint §7 "must be done before P3-9"): both HELLOs carry
``digest: true``. When both this PC and the peer are in mode ``shadow`` (from HELLO's ``mode``)
and both advertised ``digest: true``, the session **initiator** may send ``digest_request`` right
before its final BYE, at most once per peer every ``DIGEST_MIN_INTERVAL_SECONDS``. The responder
answers any ``digest_request`` it sees while waiting for the initiator's final BYE (so only the
initiator decides whether to run it each session; since initiator alternates across rounds, both
sides eventually get to run their own check -- a deliberate simplification over a fully symmetric
double-request, avoiding a second layer of per-session negotiation). Each side computes its own
replica's ``{origin: max_seq}`` vectors and digest in one read transaction
(``sync_shadow.replica_snapshot``), off the session thread with a bounded wait so a slow
computation replies ``busy`` instead of blocking the session past its timeouts. The requester
compares the reply's vectors to its own (read the same way, after receiving the reply): only
when they match exactly does it trust the digest comparison (``sync_shadow.convergence_check``)
and log OK/MISMATCH; a mismatch in the vectors themselves (either a race between the two reads, or
this round simply not leaving both PCs fully caught up with each other) is logged as "skipped:
vectors differ", never as a digest mismatch. A malformed reply, or a replica read that didn't
finish in time, is also logged as "skipped" with its own reason -- never silently dropped. At
most one ``digest_request`` is answered (computed) per session; a peer sending more gets an
immediate ``busy`` for each extra one. All logged to ``logs/sync_shadow.log`` (peer name,
OK/MISMATCH/skipped, seconds since this PC's last own local seal -- not bumped by applying a
remote batch), never change contents (§0 rule 12).

No PySide6 (§0 rule 7). Never logs change contents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Optional

import sync_transport
from sync_transport import Session, TransportError

_log = logging.getLogger("sera.sync.engine")

PROTO = 3
SYNC_INTERVAL_SECONDS = 20.0
SYNC_JITTER_SECONDS = 5.0
POKE_DEBOUNCE_SECONDS = 1.0
POKE_MIN_GAP_SECONDS = 0.2
POKE_PORT = 49156                 # the beacon port (P2-5); DiscoveryService passes pokes on
POKE_MAGIC = "sera-sync-v3"
MAX_POKE_BYTES = 512
MAX_BATCH_CHANGES = 500
MAX_BATCH_BYTES = 1024 * 1024
MAX_RECEIVED_BATCH = 1000         # accepted from a peer (a sender sends at most 500)
MAX_TOTAL_SESSIONS = 3
MAX_VECTOR_ENTRIES = 10000
MAX_MEMBER_RECORDS = 5000
MAX_GOSSIP_ENTRIES = 500
ACK_WAIT_SECONDS = 120.0          # applying a batch can take a while
RESPONDER_WAIT_SECONDS = 5.0      # tie-break wait for our own outgoing session to the same peer
BAD_BATCH_BACKOFF_SECONDS = 300.0
STALL_SECONDS = 600.0
MAX_ADDRESSES_PER_ROUND = 3
ONLINE_SECONDS = 60.0
DIGEST_MIN_INTERVAL_SECONDS = 600.0     # at most once per peer every 10 minutes (P3-7a)
DIGEST_COMPUTE_TIMEOUT = 5.0            # a slower replica read replies "busy" instead of blocking

DBS = ("master", "raw")
_STREAM_SUFFIX = {"master": ":m", "raw": ":r"}

F_HELLO, F_BYE, F_MEMBERS, F_CHANGES, F_ACK, F_DONE, F_NEED_SNAPSHOT = (
    "hello", "bye", "members", "changes", "ack", "done", "need_snapshot")
F_DIGEST_REQUEST, F_DIGEST_REPLY = "digest_request", "digest_reply"

_CHANGE_COLS = ("origin", "origin_seq", "hlc", "tbl", "row_key", "op", "data", "sig")


class SessionAborted(Exception):
    """The session ended early: we or the peer sent ``bye`` with ``reason``."""

    def __init__(self, reason: str, detail: str = "", by_peer: bool = False):
        super().__init__("%s%s" % (reason, (": " + detail) if detail else ""))
        self.reason = reason
        self.by_peer = by_peer


@dataclass
class SessionResult:
    device_id: str
    initiator: bool
    sent: int = 0                 # changes sent
    received: int = 0             # changes received
    applied: int = 0              # of those, applied (not already here)
    emitted: int = 0              # merge decisions this PC added while applying
    members_exchanged: bool = False    # member lists differed, so both sides sent theirs
    members_changed: bool = False      # this PC stored a new member / office_admin record
    need_snapshot: list = field(default_factory=list)   # streams the peer said we're behind on
    tables: set = field(default_factory=set)
    peer_vectors: dict = field(default_factory=dict)
    error: Optional[str] = None   # None = completed; else the bye reason / transport error

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def moved(self) -> int:
        return self.sent + self.received


def stream_db(stream: str) -> Optional[str]:
    """"master" for ``<device>:m``, "raw" for ``<device>:r``, else None."""
    if isinstance(stream, str):
        for which, suffix in _STREAM_SUFFIX.items():
            if stream.endswith(suffix):
                return which
    return None


def make_poke(office_tag: str, device_id: str) -> bytes:
    return json.dumps({"magic": POKE_MAGIC, "office": office_tag, "dev": device_id, "poke": True},
                      separators=(",", ":")).encode("utf-8")


def parse_poke(data) -> Optional[dict]:
    """``{"office", "dev"}`` of a well-formed poke datagram, else None (not a poke / malformed)."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_POKE_BYTES:
        return None
    try:
        obj = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(obj, dict) or obj.get("magic") != POKE_MAGIC or obj.get("poke") is not True:
        return None
    office, dev = obj.get("office"), obj.get("dev")
    if not (isinstance(office, str) and len(office) == 16 and _is_hex(office)):
        return None
    if not (isinstance(dev, str) and len(dev) == 32 and _is_hex(dev)):
        return None
    return {"office": office, "dev": dev}


def _is_hex(text: str) -> bool:
    return all(c in "0123456789abcdef" for c in text)


def _valid_vectors(obj) -> Optional[dict]:
    if not isinstance(obj, dict) or len(obj) > MAX_VECTOR_ENTRIES:
        return None
    out = {}
    for k, v in obj.items():
        if not isinstance(k, str) or not k or len(k) > 128:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return None
        out[k] = v
    return out


def _small_int(value):
    """``value`` if it is a plain int of sane size (for versions a peer reports), else None."""
    if isinstance(value, int) and not isinstance(value, bool) and -10**9 < value < 10**9:
        return value
    return None


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SyncEngine:
    """Runs v3 sync sessions for one PC. Thread-safe.

    ``db``: the ``SeraDatabase`` (office mode). ``transport``: this PC's ``SyncTransport``; serve
    ``engine.handle_session`` on it. ``connect(ip, port, device_id) -> Session`` replaces
    ``transport.connect`` (tests). ``beacon_sighting(device_id) -> (ip, port) | None`` adds the
    beacon address to the connect order (``DiscoveryService.get_beacon_sighting``).
    ``poke_port_for(device_id, sync_port) -> udp port`` (default: ``POKE_PORT``).
    ``poke_listen=(host, port)`` opens a UDP socket for pokes here; in the app the beacon socket
    of ``DiscoveryService`` passes them to ``handle_poke`` instead (``on_poke``).
    ``on_event(kind, info)`` is told about: synced, sync_failed, needs_update, need_snapshot,
    members_changed, admin_named_here, clock_ahead, stalled, parked.
    ``shadow_apply(which, changes) -> ApplyResult`` receives remote changes in mode shadow (P3-7).
    """

    def __init__(self, db, transport, *, app_dir=None, office_tag: Optional[str] = None,
                 admin_pubkey: Optional[str] = None,
                 connect: Optional[Callable[[str, int, str], Session]] = None,
                 beacon_sighting: Optional[Callable[[str], Optional[tuple]]] = None,
                 poke_port_for: Optional[Callable[[str, int], int]] = None,
                 poke_listen: Optional[tuple] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 shadow_apply: Optional[Callable[[str, list], object]] = None,
                 own_cert_pem: Optional[str] = None,
                 interval: float = SYNC_INTERVAL_SECONDS, jitter: float = SYNC_JITTER_SECONDS,
                 poke_debounce: float = POKE_DEBOUNCE_SECONDS, rng: Optional[random.Random] = None):
        import sera_keys
        import sync_admin
        import sync_discovery
        self.db = db
        self.transport = transport
        self.app_dir = str(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
        self.device_id = transport.members.own_device_id
        if office_tag is None:
            office_tag = sync_discovery.compute_office_tag(bytes.fromhex(db.hex_key))
        self.office_tag = office_tag
        if admin_pubkey is None:
            office = sera_keys.load_office(self.app_dir)
            admin_pubkey = office.admin_pubkey if office else None
        if not admin_pubkey:
            raise ValueError("the office has no admin public key (office mode needed)")
        self.admin_pubkey = admin_pubkey
        self._connect_fn = connect or (lambda ip, port, dev: transport.connect(ip, port, dev))
        self._beacon_sighting = beacon_sighting
        self._poke_port_for = poke_port_for or (lambda dev, port: POKE_PORT)
        self._poke_listen = poke_listen
        self._on_event = on_event
        self.shadow_apply = shadow_apply
        self._own_pem = own_cert_pem
        self.interval = interval
        self.jitter = jitter
        self.poke_debounce = poke_debounce
        self._rng = rng or random.Random()

        self._lock = threading.Lock()
        self._peer_locks: dict[str, threading.Lock] = {}
        self._outgoing: set[str] = set()
        self._slots = threading.BoundedSemaphore(MAX_TOTAL_SESSIONS)
        self._apply_locks = {w: threading.Lock() for w in DBS}
        self._peers: dict[str, dict] = {}
        self._backoff: dict[str, float] = {}
        self._gaps: dict[str, tuple] = {}          # stream -> (vector, first_seen, have_up_to)
        self._stall_reported: set[tuple] = set()
        self._digest_last: dict[str, float] = {}   # device_id -> last digest exchange (monotonic)
        self._last_local_change_at: Optional[float] = None

        self._cond = threading.Condition()
        self._poke_due: dict[str, float] = {}
        self._stop = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._poke_thread: Optional[threading.Thread] = None
        self._poke_sock: Optional[socket.socket] = None
        self._poke_dests: list[tuple] = []
        self._poke_dests_at = 0.0
        self._poke_out = threading.Event()
        self._poke_sender: Optional[threading.Thread] = None

        with self._conn("master") as conn:
            sync_discovery.ensure_address_book_table(conn)
            sync_admin.ensure_members_table(conn)

    # ------------------------------------------------------------ database access

    @contextmanager
    def _conn(self, which: str):
        """Private autocommit connection (never runs the sealer)."""
        import sync_capture
        path = self.db.db_path if which == "master" else self.db.raw_db_path
        conn = sync_capture._open(path, self.db.hex_key, 5.0)
        try:
            yield conn
        finally:
            conn.close()

    def _mode(self) -> str:
        return self.db.get_sync_mode()

    def _accepts(self) -> bool:
        mode = self._mode()
        return mode == "live" or (mode == "shadow" and self.shadow_apply is not None)

    def _can_send(self) -> bool:
        return self._mode() in ("shadow", "live")

    def _schema_version(self) -> int:
        import sync_capture
        with self._conn("master") as conn:
            value = sync_capture._meta(conn, "schema_version")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def own_vectors(self) -> dict:
        """``{stream: max_seq}`` of both DB files."""
        out = {}
        for which in DBS:
            with self._conn(which) as conn:
                out.update({o: int(s) for o, s in conn.execute("SELECT origin, max_seq FROM _sync_vector")})
        return out

    def _member_records(self) -> list:
        import sync_admin
        with self._conn("master") as conn:
            records = sync_admin.list_members(conn, self.admin_pubkey, include_revoked=True)
            admin = sync_admin.get_office_admin(conn, self.admin_pubkey)
        if admin is not None:
            records.append(admin)
        return sorted(records, key=_canonical)

    @staticmethod
    def members_rev(records) -> str:
        h = hashlib.sha256()
        for rec in sorted(records, key=_canonical):
            h.update(_canonical(rec).encode("utf-8") + b"\n")
        return h.hexdigest()[:16]

    def _is_member(self, device_id: str) -> bool:
        return device_id in self.transport.members.device_ids

    def _member_name(self, device_id: str) -> str:
        import sync_admin
        try:
            with self._conn("master") as conn:
                rec = sync_admin.get_member(conn, device_id, self.admin_pubkey)
            return (rec or {}).get("name") or device_id[:8]
        except Exception:
            return device_id[:8]

    def compaction_floors(self) -> dict:
        """``{stream: floor}``: changes at or below it are no longer kept (P4-4). No compaction
        exists yet, so nothing is ever below the floor."""
        return {}

    # ------------------------------------------------------------ events / status

    def _event(self, kind: str, **info) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(kind, info)
        except Exception:
            _log.exception("sync engine event handler failed (%s)", kind)

    def _peer(self, device_id: str) -> dict:
        return self._peers.setdefault(device_id, {"last_ok": None, "last_attempt": None,
                                                  "last_error": None, "needs_update": None,
                                                  "clock_ahead": None})

    def peer_status(self) -> dict:
        """``{device_id: {last_ok, last_attempt, last_error, needs_update, online}}``
        (``time.time()`` values; ``online`` = a session completed in the last minute)."""
        now = time.time()
        with self._lock:
            out = {}
            for dev, st in self._peers.items():
                d = dict(st)
                ca = d.get("clock_ahead")
                if ca and now - ca.get("seen_at", 0) > 3600.0:
                    d["clock_ahead"] = None
                    st["clock_ahead"] = None
                d["online"] = bool(st["last_ok"] and now - st["last_ok"] <= ONLINE_SECONDS)
                out[dev] = d
            return out

    # ------------------------------------------------------------ locks

    def _peer_lock(self, device_id: str) -> threading.Lock:
        with self._lock:
            return self._peer_locks.setdefault(device_id, threading.Lock())

    # ------------------------------------------------------------ outgoing

    def connect_order(self, device_id: str) -> list:
        import sync_discovery
        beacon = None
        if self._beacon_sighting is not None:
            try:
                beacon = self._beacon_sighting(device_id)
            except Exception:
                beacon = None
        with self._conn("master") as conn:
            return sync_discovery.get_connect_order(conn, device_id, beacon_addr=beacon)

    def reachable_members(self) -> list:
        """Active members other than this PC that have at least one address to try."""
        out = []
        for dev in sorted(self.transport.members.device_ids):
            if dev != self.device_id and self.connect_order(dev):
                out.append(dev)
        return out

    def sync_with(self, device_id: str) -> SessionResult:
        """One session with ``device_id``, trying its addresses in connect order (P2-5)."""
        result = SessionResult(device_id, initiator=True)
        if device_id == self.device_id or not self._is_member(device_id):
            result.error = "not_a_member"
            return result
        until = self._backoff.get(device_id)
        if until is not None and time.monotonic() < until:
            result.error = "backoff"
            return result
        lock = self._peer_lock(device_id)
        if not lock.acquire(blocking=False):
            result.error = "busy"
            return result
        try:
            if not self._slots.acquire(blocking=False):
                result.error = "busy"
                return result
            try:
                with self._lock:
                    self._outgoing.add(device_id)
                    self._peer(device_id)["last_attempt"] = time.time()
                try:
                    return self._dial(device_id, result)
                finally:
                    with self._lock:
                        self._outgoing.discard(device_id)
            finally:
                self._slots.release()
        finally:
            lock.release()

    def _dial(self, device_id: str, result: SessionResult) -> SessionResult:
        addresses = self.connect_order(device_id)[:MAX_ADDRESSES_PER_ROUND]
        if not addresses:
            result.error = "no_address"
            return result
        last_error = "unreachable"
        for ip, port in addresses:
            try:
                session = self._connect_fn(ip, port, device_id)
            except TransportError as exc:
                last_error = "unreachable: %s" % type(exc).__name__
                continue
            try:
                self._run(session, result, initiator=True)
            finally:
                session.close()
            if result.ok:
                self._record_address(device_id, ip, port)
            return result
        result.error = last_error
        self._finish(result)
        return result

    def sync_all(self) -> list:
        """One session with each reachable member, one at a time."""
        out = []
        for dev in self.reachable_members():
            if self._stop.is_set():
                break
            out.append(self.sync_with(dev))
        return out

    def _record_address(self, device_id: str, ip: str, port: int) -> None:
        import sync_discovery
        try:
            with self._conn("master") as conn:
                sync_discovery.record_successful_session(conn, device_id, ip, port)
        except Exception:
            _log.exception("could not record the address of %s", device_id)
        with self._lock:
            self._poke_dests_at = 0.0

    # ------------------------------------------------------------ incoming

    def handle_session(self, session: Session) -> None:
        """Server handler (``transport.serve(engine.handle_session)``)."""
        dev = session.peer_device_id
        result = SessionResult(dev, initiator=False)
        lock = self._peer_lock(dev)
        # Both PCs dialled each other: the smaller device id keeps its own outgoing session.
        with self._lock:
            dialling = dev in self._outgoing
        wait = RESPONDER_WAIT_SECONDS if (dialling and self.device_id > dev) else 0
        got = lock.acquire(timeout=wait) if wait else lock.acquire(blocking=False)
        if not got:
            self._refuse_busy(session)
            return
        try:
            if not self._slots.acquire(blocking=False):
                self._refuse_busy(session)
                return
            try:
                with self._lock:
                    self._peer(dev)["last_attempt"] = time.time()
                self._run(session, result, initiator=False)
            finally:
                self._slots.release()
        finally:
            lock.release()

    def _refuse_busy(self, session: Session) -> None:
        """``bye busy``, after reading the peer's HELLO: closing with its HELLO still unread
        resets the connection on Windows, and the peer would never see the ``bye``."""
        try:
            session.recv(wait=RESPONDER_WAIT_SECONDS)
        except TransportError:
            return
        self._send_bye(session, "busy")
        try:
            session.recv(wait=RESPONDER_WAIT_SECONDS)   # until the peer hangs up
        except TransportError:
            pass

    # ------------------------------------------------------------ protocol

    def _send_bye(self, session: Session, reason: str, **extra) -> None:
        try:
            session.send(dict({"t": F_BYE, "reason": reason}, **extra))
        except (TransportError, ValueError):
            pass

    def _abort(self, session: Session, reason: str, detail: str = "", **extra):
        self._send_bye(session, reason, **extra)
        return SessionAborted(reason, detail)

    def _recv(self, session: Session, wait: Optional[float] = None, expect=None) -> dict:
        frame = session.recv(wait=wait)
        t = frame.get("t")
        if t == F_BYE:
            reason = frame.get("reason")
            reason = reason if isinstance(reason, str) and len(reason) <= 32 else "unknown"
            if reason == "schema":
                self._needs_update(session.peer_device_id, _small_int(frame.get("yours")),
                                   _small_int(frame.get("mine")))
            raise SessionAborted(reason, by_peer=True)
        if expect is not None and t not in expect:
            raise self._abort(session, "protocol", "expected %s, got %r" % ("/".join(expect), str(t)[:32]))
        return frame

    def _hello(self) -> dict:
        import sync_discovery
        with self._conn("master") as conn:
            addresses = sync_discovery.export_gossip_addresses(conn)[:MAX_GOSSIP_ENTRIES]
        return {"t": F_HELLO, "proto": PROTO, "schema_version": self._schema_version(),
                "device_id": self.device_id, "office_tag": self.office_tag,
                "vectors": self.own_vectors(), "members_rev": self.members_rev(self._member_records()),
                "addresses": addresses, "mode": self._mode(), "accepts": self._accepts(),
                "digest": True}

    def _needs_update(self, device_id, mine, yours) -> None:
        """Schema versions differ. ``who`` = "peer" (show "PC <name> needs updating") or
        "this_pc" (this PC runs the older version)."""
        who = "this_pc" if (isinstance(mine, int) and isinstance(yours, int) and mine < yours) else "peer"
        with self._lock:
            self._peer(device_id)["needs_update"] = {"mine": mine, "yours": yours, "who": who}
        name = self._member_name(device_id)
        _log.warning("sync schema %s here, %s on PC %s: %s needs updating", mine, yours, name,
                     "this PC" if who == "this_pc" else "PC " + name)
        self._event("needs_update", device_id=device_id, name=name, mine=mine, yours=yours, who=who)

    def _check_hello(self, session: Session, hello: dict, mine: dict) -> dict:
        if hello.get("proto") != PROTO:
            raise self._abort(session, "proto", mine=PROTO, yours=_small_int(hello.get("proto")))
        if hello.get("device_id") != session.peer_device_id:
            raise self._abort(session, "identity")
        if hello.get("office_tag") != self.office_tag:
            raise self._abort(session, "office")
        sv = _small_int(hello.get("schema_version"))
        if sv is None or sv != mine["schema_version"]:
            self._needs_update(session.peer_device_id, mine["schema_version"], sv)
            raise self._abort(session, "schema", mine=mine["schema_version"], yours=sv)
        vectors = _valid_vectors(hello.get("vectors"))
        rev = hello.get("members_rev")
        addresses = hello.get("addresses")
        if vectors is None or not isinstance(rev, str) or len(rev) > 64 or not isinstance(addresses, list):
            raise self._abort(session, "protocol", "malformed hello")
        with self._lock:
            self._peer(session.peer_device_id)["needs_update"] = None
        return {"vectors": vectors, "members_rev": rev, "addresses": addresses[:MAX_GOSSIP_ENTRIES],
                "accepts": hello.get("accepts") is True, "digest": hello.get("digest") is True,
                "mode": hello.get("mode") if isinstance(hello.get("mode"), str) else None}

    def _run(self, session: Session, result: SessionResult, initiator: bool) -> None:
        try:
            try:
                self._exchange(session, result, initiator)
            except SessionAborted as exc:
                result.error = exc.reason
                if exc.reason == "bad_batch" and not exc.by_peer:
                    self._backoff[session.peer_device_id] = time.monotonic() + BAD_BATCH_BACKOFF_SECONDS
            except TransportError as exc:
                result.error = "transport: %s" % type(exc).__name__
            except Exception:
                _log.exception("sync session with %s failed", session.peer_device_id)
                self._send_bye(session, "protocol")
                result.error = "internal"
        finally:
            self._finish(result)

    def _finish(self, result: SessionResult) -> None:
        with self._lock:
            st = self._peer(result.device_id)
            if result.ok:
                st["last_ok"] = time.time()
                st["last_error"] = None
            else:
                st["last_error"] = result.error
        if result.ok:
            if result.moved or result.members_changed:
                _log.info("synced with %s: %d sent, %d received", result.device_id, result.sent, result.received)
            self._event("synced", device_id=result.device_id, sent=result.sent, received=result.received,
                        applied=result.applied, tables=sorted(result.tables))
        elif result.error not in ("busy", "backoff", "not_a_member", "no_address"):
            _log.info("sync with %s did not complete: %s", result.device_id, result.error)
            self._event("sync_failed", device_id=result.device_id, reason=result.error)
        if result.received:
            self._check_stalls()
        if result.emitted:
            # A merge decision made while applying a REMOTE batch (natural-key merge, P3-4) needs
            # forwarding, so wake the poke sender -- but this is not this PC's own edit, so it
            # must not stamp _last_local_change_at (review finding #3, P3-7a).
            self._wake_poke_sender()

    def _exchange(self, session: Session, result: SessionResult, initiator: bool) -> None:
        dev = session.peer_device_id
        mine = self._hello()
        if initiator:
            session.send(mine)
            peer = self._check_hello(session, self._recv(session, expect=(F_HELLO,)), mine)
        else:
            peer = self._check_hello(session, self._recv(session, expect=(F_HELLO,)), mine)
            session.send(mine)
        self._merge_gossip(peer["addresses"])

        if peer["members_rev"] != mine["members_rev"]:
            result.members_exchanged = True
            if initiator:
                self._send_members(session)
                result.members_changed = self._receive_members(session)
            else:
                result.members_changed = self._receive_members(session)
                self._send_members(session)
            if not self._is_member(dev):
                raise SessionAborted("revoked")

        if initiator:
            result.sent += self._send_changes(session, peer)
            self._receive_changes(session, result)
        else:
            self._receive_changes(session, result)
            result.sent += self._send_changes(session, peer)

        if initiator:
            self._run_digest_exchange(session, peer, dev)
            session.send({"t": F_BYE, "reason": "done", "vectors": self.own_vectors()})
            final = self._recv_final_bye(session)
        else:
            final = self._recv_final_bye(session)
            session.send({"t": F_BYE, "reason": "done", "vectors": self.own_vectors()})
        result.peer_vectors = final if final is not None else peer["vectors"]
        self._store_peer_vectors(dev, result.peer_vectors)

    def _recv_final_bye(self, session: Session) -> Optional[dict]:
        """Waits for the peer's final ``bye done``, answering any ``digest_request`` (P3-7a) it
        sees along the way -- the initiator may run the digest exchange right before sending its
        own final bye, so the responder must not mistake that frame for a protocol error. At most
        one ``digest_request`` is actually answered (computed, spawning a worker thread) per
        session; a peer sending more than one gets an immediate ``busy`` for each extra one
        (review finding #4, P3-7a: nothing should let one session spawn unbounded workers)."""
        answered_digest = False
        while True:
            frame = session.recv()
            t = frame.get("t")
            if t == F_DIGEST_REQUEST:
                if answered_digest:
                    session.send({"t": F_DIGEST_REPLY, "busy": True})
                else:
                    answered_digest = True
                    self._answer_digest_request(session)
                continue
            if t != F_BYE:
                raise self._abort(session, "protocol", "expected bye")
            if frame.get("reason") != "done":
                raise SessionAborted(str(frame.get("reason"))[:32], by_peer=True)
            return _valid_vectors(frame.get("vectors"))

    def _merge_gossip(self, addresses) -> None:
        import sync_discovery
        try:
            with self._conn("master") as conn:
                sync_discovery.merge_gossip_addresses(conn, addresses, own_device_id=self.device_id,
                                                      is_member=self._is_member)
        except Exception:
            _log.exception("could not merge gossiped addresses")

    # -- members

    def _send_members(self, session: Session) -> None:
        session.send({"t": F_MEMBERS, "records": self._member_records()})

    def _receive_members(self, session: Session) -> bool:
        import sync_admin
        frame = self._recv(session, expect=(F_MEMBERS,))
        records = frame.get("records")
        if not isinstance(records, list) or len(records) > MAX_MEMBER_RECORDS:
            raise self._abort(session, "protocol", "malformed members")
        changed = False
        with self._conn("master") as conn:
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                try:
                    changed |= bool(sync_admin.store_record(conn, rec, self.admin_pubkey))
                except sync_admin.InvalidRecord:
                    _log.warning("membership record from %s rejected: bad signature or shape",
                                 session.peer_device_id)
        if changed:
            self._members_changed()
        return changed

    def _members_changed(self) -> None:
        import sync_admin
        from sync_transport import MemberSet
        own_pem = self._own_cert_pem()
        with self._conn("master") as conn:
            members = MemberSet.from_db(conn, self.admin_pubkey, own_pem)
            try:
                dropped = sync_admin.reconcile_admin_key(self.app_dir, conn, self.device_id)
            except Exception:
                _log.exception("could not reconcile the admin key")
                dropped = False
            admin = sync_admin.get_office_admin(conn, self.admin_pubkey)
        self.transport.update_members(members)
        with self._lock:
            self._poke_dests_at = 0.0
        self._event("members_changed", members=sorted(members.device_ids), admin_key_dropped=dropped)
        if admin is not None and admin.get("device_id") == self.device_id and not sync_admin.has_admin_key(self.app_dir):
            # "Hand over admin" named this PC (P2-7). Claiming still needs the master password (D3).
            self._event("admin_named_here", device_id=self.device_id)

    def _own_cert_pem(self) -> str:
        if self._own_pem is None:
            import sync_identity
            pem = sync_identity.load_device_identity(self.app_dir).cert_pem
            self._own_pem = pem.decode("ascii") if isinstance(pem, bytes) else pem
        return self._own_pem

    # -- changes

    def _send_changes(self, session: Session, peer: dict) -> int:
        """Our changes the peer lacks (by its HELLO vectors), in batches that each wait for an ACK."""
        sent = 0
        if not peer["accepts"] or not self._can_send():
            session.send({"t": F_DONE})
            return 0
        floors = self.compaction_floors()
        behind = sorted(s for s, floor in floors.items() if peer["vectors"].get(s, 0) < floor)
        if behind:
            session.send({"t": F_NEED_SNAPSHOT, "streams": behind})
            session.send({"t": F_DONE})
            return 0
        vec_json = json.dumps(peer["vectors"])
        for which in DBS:
            cursor = ("", "", 0)
            while True:
                items, cursor = self._next_batch(which, vec_json, cursor)
                if not items:
                    break
                session.send({"t": F_CHANGES, "stream": which, "items": items})
                sent += len(items)
                self._recv(session, wait=ACK_WAIT_SECONDS, expect=(F_ACK,))
        session.send({"t": F_DONE})
        return sent

    def _next_batch(self, which: str, vec_json: str, cursor: tuple):
        with self._conn(which) as conn:
            rows = conn.execute(
                "SELECT c.origin, c.origin_seq, c.hlc, c.tbl, c.row_key, c.op, c.data, c.sig "
                "FROM _sync_changes c LEFT JOIN json_each(?) v ON v.key = c.origin "
                "WHERE c.origin_seq > COALESCE(v.value, 0) AND (c.hlc, c.origin, c.origin_seq) > (?, ?, ?) "
                "ORDER BY c.hlc, c.origin, c.origin_seq LIMIT ?",
                (vec_json, cursor[0], cursor[1], cursor[2], MAX_BATCH_CHANGES)).fetchall()
        items, size = [], 0
        for row in rows:
            item = dict(zip(_CHANGE_COLS, row))
            n = len(json.dumps(item, ensure_ascii=False)) + 1
            if items and size + n > MAX_BATCH_BYTES:
                break
            items.append(item)
            size += n
        if items:
            last = items[-1]
            cursor = (last["hlc"], last["origin"], last["origin_seq"])
        return items, cursor

    def _receive_changes(self, session: Session, result: SessionResult) -> None:
        import sync_apply
        while True:
            frame = self._recv(session, wait=ACK_WAIT_SECONDS, expect=(F_CHANGES, F_DONE, F_NEED_SNAPSHOT))
            t = frame["t"]
            if t == F_DONE:
                return
            if t == F_NEED_SNAPSHOT:
                streams = frame.get("streams")
                streams = [s for s in streams if isinstance(s, str)][:100] if isinstance(streams, list) else []
                result.need_snapshot = streams
                _log.warning("PC %s: this PC is behind its compaction floor; a snapshot is needed (P4-4)",
                             session.peer_device_id)
                self._event("need_snapshot", device_id=session.peer_device_id, streams=streams)
                continue
            which, items = frame.get("stream"), frame.get("items")
            if which not in DBS or not isinstance(items, list) or not items or len(items) > MAX_RECEIVED_BATCH:
                raise self._abort(session, "bad_batch", "malformed changes frame")
            if any(not isinstance(i, dict) or stream_db(i.get("origin")) != which for i in items):
                raise self._abort(session, "bad_batch", "change for the other database")
            if not self._accepts():
                raise self._abort(session, "protocol", "changes sent to a PC that doesn't accept them")
            try:
                applied = self._apply(which, items)
            except sync_apply.ApplyError as exc:
                _log.warning("malformed batch from %s dropped: %s", session.peer_device_id, exc)
                raise self._abort(session, "bad_batch", str(exc))
            except Exception as exc:
                _log.warning("batch from %s not applied (will retry next round): %s",
                             session.peer_device_id, type(exc).__name__)
                raise self._abort(session, "apply_failed", type(exc).__name__)
            result.received += len(items)
            result.applied += len(items) - applied.skipped
            result.emitted += applied.emitted
            result.tables |= set(applied.tables)
            if applied.clock_ahead:
                result.clock_ahead_seen = True
                dev_ahead = {}
                for origin, ahead in applied.clock_ahead:
                    dev_id = origin.split(":")[0]
                    if dev_id not in dev_ahead or ahead > dev_ahead[dev_id][1]:
                        dev_ahead[dev_id] = (origin, ahead)
                for dev_id, (origin, ahead) in dev_ahead.items():
                    minutes = max(1, round(ahead / 60000))
                    with self._lock:
                        self._peer(dev_id)["clock_ahead"] = {"ahead_ms": ahead, "minutes": minutes, "seen_at": time.time()}
                    self._event("clock_ahead", origin=origin, device_id=dev_id,
                                name=self._member_name(dev_id), ahead_ms=ahead)
            if applied.parked:
                self._event("parked", count=applied.parked)
            session.send({"t": F_ACK, "vectors": self.own_vectors()})

    def _apply(self, which: str, items: list):
        with self._apply_locks[which]:
            if self._mode() == "live":
                return self.db.apply_changes(which, items, admin_pubkey=self.admin_pubkey)
            return self.shadow_apply(which, items)

    def _store_peer_vectors(self, device_id: str, vectors: dict) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for which in DBS:
            rows = [(device_id, s, v, now) for s, v in vectors.items() if stream_db(s) == which]
            if not rows:
                continue
            try:
                with self._conn(which) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        conn.executemany(
                            "INSERT INTO _sync_peer_vectors(device_id, origin, max_seq, seen_at) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(device_id, origin) DO UPDATE SET max_seq = excluded.max_seq, "
                            "seen_at = excluded.seen_at", rows)
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
            except Exception:
                _log.exception("could not store the vectors of %s", device_id)

    # ------------------------------------------------------------ peer digest exchange (P3-7a)

    def _should_request_digest(self, dev: str, peer: dict) -> bool:
        if self._mode() != "shadow" or self.shadow_apply is None:
            return False
        if peer.get("mode") != "shadow" or not peer.get("digest"):
            return False
        last = self._digest_last.get(dev)
        if last is not None and time.monotonic() - last < DIGEST_MIN_INTERVAL_SECONDS:
            return False
        return True

    def _replica_snapshot_with_timeout(self):
        """``(vectors, digest)`` of this PC's shadow replica (``sync_shadow.replica_snapshot``),
        computed off this thread with a bounded wait -- "computing a digest must not block the
        session past its timeouts" (P3-7a). Returns None on timeout or any read failure; the
        background thread (if still running) is abandoned, not joined further."""
        import sync_shadow
        outcome: dict = {}
        done = threading.Event()

        def work():
            try:
                outcome["value"] = sync_shadow.replica_snapshot(self.app_dir, self.db.hex_key)
            except Exception as exc:
                outcome["error"] = exc
            finally:
                done.set()

        threading.Thread(target=work, name="sera-sync-digest", daemon=True).start()
        if not done.wait(DIGEST_COMPUTE_TIMEOUT):
            return None
        if "error" in outcome:
            _log.warning("could not compute the shadow replica digest: %s", outcome["error"])
            return None
        return outcome.get("value")

    def _seconds_since_last_seal(self) -> Optional[int]:
        if self._last_local_change_at is None:
            return None
        return int(time.time() - self._last_local_change_at)

    def _answer_digest_request(self, session: Session) -> None:
        """Replies to a peer's ``digest_request`` with this PC's own replica vectors + digest,
        or ``busy`` if mode/replica aren't ready or the read didn't finish in time."""
        snap = self._replica_snapshot_with_timeout() if self._mode() == "shadow" and self.shadow_apply else None
        if snap is None:
            session.send({"t": F_DIGEST_REPLY, "busy": True})
            return
        vectors, digest = snap
        session.send({"t": F_DIGEST_REPLY, "vectors": vectors, "digest": digest})

    def _run_digest_exchange(self, session: Session, peer: dict, dev: str) -> None:
        """Initiator side: request the peer's replica digest and log the comparison. A no-op
        unless both PCs are in mode shadow and both advertised ``digest: true`` in HELLO, and not
        more than once per peer every ``DIGEST_MIN_INTERVAL_SECONDS``."""
        if not self._should_request_digest(dev, peer):
            return
        self._digest_last[dev] = time.monotonic()
        import sync_shadow
        name = self._member_name(dev)
        session.send({"t": F_DIGEST_REQUEST})
        frame = self._recv(session, wait=ACK_WAIT_SECONDS, expect=(F_DIGEST_REPLY,))
        if frame.get("busy"):
            sync_shadow.log_peer_digest_check(self.app_dir, name, "skipped", "peer busy")
            self._event("digest_checked", device_id=dev, name=name, ok=None, skipped=True)
            return
        peer_vectors = _valid_vectors(frame.get("vectors"))
        peer_digest = frame.get("digest")
        if peer_vectors is None or not isinstance(peer_digest, str) or len(peer_digest) != 64 or not _is_hex(peer_digest):
            # Malformed reply: best-effort check, never fatal to the session itself, but still
            # worth a line so a buggy/half-built peer build is visible (review finding #2, P3-7a).
            sync_shadow.log_peer_digest_check(self.app_dir, name, "skipped", "malformed reply")
            self._event("digest_checked", device_id=dev, name=name, ok=None, skipped=True)
            return
        snap = self._replica_snapshot_with_timeout()
        if snap is None:
            sync_shadow.log_peer_digest_check(self.app_dir, name, "skipped", "could not read own replica in time")
            self._event("digest_checked", device_id=dev, name=name, ok=None, skipped=True)
            return
        own_vectors, own_digest = snap
        since = self._seconds_since_last_seal()
        since_text = f"{since}s since last local seal" if since is not None else "no local seal yet"
        if own_vectors != peer_vectors:
            # Either a genuine race (a replica moved between the reply and this comparison) or
            # this session simply didn't leave both PCs fully caught up with each other this
            # round (a parked change, a stalled stream, a batch limit) -- "differ" doesn't claim
            # to know which, unlike the earlier wording "vectors moved" (review finding #1, P3-7a).
            sync_shadow.log_peer_digest_check(self.app_dir, name, "skipped", f"vectors differ ({since_text})")
            self._event("digest_checked", device_id=dev, name=name, ok=None, skipped=True)
            return
        convergence = sync_shadow.convergence_check(self.device_id, own_digest, {dev: peer_digest})
        status = "OK" if convergence.ok else "MISMATCH"
        sync_shadow.log_peer_digest_check(self.app_dir, name, status, since_text)
        self._event("digest_checked", device_id=dev, name=name, ok=convergence.ok, skipped=False)

    # ------------------------------------------------------------ stuck streams

    def _check_stalls(self, now: Optional[float] = None) -> list:
        """Streams where this PC holds changes beyond a missing one. Reported after STALL_SECONDS."""
        now = time.monotonic() if now is None else now
        seen = {}
        for which in DBS:
            with self._conn(which) as conn:
                vec = dict(conn.execute("SELECT origin, max_seq FROM _sync_vector").fetchall())
                for origin, top in conn.execute("SELECT origin, MAX(origin_seq) FROM _sync_changes GROUP BY origin"):
                    at = vec.get(origin, 0)
                    if top > at:
                        seen[origin] = (at, top)
        stalled = []
        with self._lock:
            for origin in list(self._gaps):
                if origin not in seen or self._gaps[origin][0] != seen[origin][0]:
                    del self._gaps[origin]
            for origin, (at, top) in seen.items():
                first = self._gaps.get(origin, (at, now, top))[1]
                self._gaps[origin] = (at, first, top)
                if now - first >= STALL_SECONDS:
                    stalled.append({"stream": origin, "device_id": origin.split(":")[0],
                                    "waiting_for": at + 1, "have_up_to": top,
                                    "stuck_seconds": int(now - first)})
        for s in stalled:
            key = (s["stream"], s["waiting_for"])
            with self._lock:
                new = key not in self._stall_reported
                self._stall_reported.add(key)
            if new:
                s["name"] = self._member_name(s["device_id"])
                _log.warning("changes from PC %s are stuck: waiting for #%d, holding up to #%d",
                             s["name"], s["waiting_for"], s["have_up_to"])
                self._event("stalled", **s)
        return stalled

    def stalled_streams(self) -> list:
        """Streams stuck at a gap for at least ``STALL_SECONDS`` (for the panel, P3-8)."""
        return self._check_stalls()

    # ------------------------------------------------------------ pokes

    def handle_poke(self, data, sender_ip: Optional[str] = None) -> bool:
        """A poke datagram arrived. Schedules a session with that member within ``poke_debounce``."""
        poke = parse_poke(data)
        if poke is None or poke["office"] != self.office_tag:
            return False
        dev = poke["dev"]
        if dev == self.device_id or not self._is_member(dev):
            return False
        with self._cond:
            if not self._running:
                return False
            if dev not in self._poke_due:
                self._poke_due[dev] = time.monotonic() + self.poke_debounce
                self._cond.notify_all()
        return True

    def notify_local_change(self, *_args) -> None:
        """Call after a local seal produced changes (it runs on the writer's thread, maybe the UI
        thread): wakes the poke sender, which pokes the members. Bursts are coalesced. Also
        records when the last local seal happened, for the P3-7a digest-check log line. Only for
        this PC's own edits -- a merge decision made while applying a remote batch wakes the poke
        sender too (``_finish``), but through ``_wake_poke_sender`` directly, since that isn't
        this PC's own edit (review finding #3, P3-7a)."""
        self._last_local_change_at = time.time()
        self._wake_poke_sender()

    def _wake_poke_sender(self) -> None:
        if self._running:
            self._poke_out.set()

    def _poke_send_loop(self) -> None:
        while not self._stop.is_set():
            if not self._poke_out.wait(0.5):
                continue
            self._poke_out.clear()
            if self._stop.is_set():
                return
            try:
                self._send_pokes()
            except Exception:
                _log.exception("could not send sync pokes")
            self._stop.wait(POKE_MIN_GAP_SECONDS)

    def _poke_destinations(self) -> list:
        with self._lock:
            if self._poke_dests and time.monotonic() - self._poke_dests_at < 30.0:
                return list(self._poke_dests)
        dests = []
        for dev in sorted(self.transport.members.device_ids):
            if dev == self.device_id:
                continue
            try:
                order = self.connect_order(dev)
            except Exception:
                continue
            if order:
                ip, port = order[0]
                dests.append((ip, int(self._poke_port_for(dev, port))))
        with self._lock:
            self._poke_dests, self._poke_dests_at = dests, time.monotonic()
        return dests

    def _send_pokes(self) -> None:
        payload = make_poke(self.office_tag, self.device_id)
        sock = self._poke_socket()
        if sock is None:
            return
        for addr in self._poke_destinations():
            try:
                sock.sendto(payload, addr)
            except OSError:
                pass

    def _poke_socket(self) -> Optional[socket.socket]:
        with self._lock:
            if self._poke_sock is None:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.bind(("0.0.0.0", 0))
                    self._poke_sock = s
                except OSError:
                    return None
            return self._poke_sock

    def _poke_listen_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(MAX_POKE_BYTES + 1)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                time.sleep(0.1)
                continue
            self.handle_poke(data, addr[0])

    # ------------------------------------------------------------ scheduler

    def start(self) -> None:
        """Starts the scheduler (and the poke listener if ``poke_listen`` was given). Idempotent."""
        with self._cond:
            if self._running:
                return
            self._stop.clear()
            self._running = True
        if self._poke_listen is not None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(tuple(self._poke_listen))
                s.settimeout(0.5)
            except OSError:
                s.close()
                with self._cond:
                    self._running = False
                raise
            with self._lock:
                if self._poke_sock is not None:
                    self._poke_sock.close()
                self._poke_sock = s
            self._poke_thread = threading.Thread(target=self._poke_listen_loop, args=(s,),
                                                 name="sera-sync-poke", daemon=True)
            self._poke_thread.start()
        self._poke_sender = threading.Thread(target=self._poke_send_loop, name="sera-sync-poke-out", daemon=True)
        self._poke_sender.start()
        self._thread = threading.Thread(target=self._schedule_loop, name="sera-sync-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._running = False
            self._stop.set()
            self._cond.notify_all()
        self._poke_out.set()
        with self._lock:
            sock, self._poke_sock = self._poke_sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        for t in (self._thread, self._poke_thread, self._poke_sender):
            if t is not None and t is not threading.current_thread():
                t.join(timeout)
        self._thread = self._poke_thread = self._poke_sender = None
        self._poke_out.clear()

    @property
    def running(self) -> bool:
        return self._running

    def _next_round_delay(self) -> float:
        return max(1.0, self.interval + self._rng.uniform(-self.jitter, self.jitter))

    def _schedule_loop(self) -> None:
        next_round = time.monotonic() + self._next_round_delay()
        while True:
            with self._cond:
                while not self._stop.is_set():
                    now = time.monotonic()
                    due = min([next_round] + list(self._poke_due.values()))
                    if due <= now:
                        break
                    self._cond.wait(due - now)
                if self._stop.is_set():
                    return
                now = time.monotonic()
                poked = sorted(d for d, t in self._poke_due.items() if t <= now)
                for d in poked:
                    del self._poke_due[d]
            for dev in poked:
                self._safe_sync(dev)
            if time.monotonic() >= next_round:
                next_round = time.monotonic() + self._next_round_delay()
                try:
                    members = self.reachable_members()
                except Exception:
                    _log.exception("could not list members to sync with")
                    members = []
                for dev in members:
                    if self._stop.is_set():
                        return
                    self._safe_sync(dev)
                try:
                    self._check_stalls()
                except Exception:
                    _log.exception("stuck-stream check failed")

    def _safe_sync(self, device_id: str) -> None:
        try:
            self.sync_with(device_id)
        except Exception:
            _log.exception("scheduled sync with %s failed", device_id)
