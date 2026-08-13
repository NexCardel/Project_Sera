"""
sync_peer.py
------------
Built-in LAN sync for master.db / sera.salt.

Design (v2 — Sera Sync):
  - UDP broadcast beacon on BEACON_PORT for peer discovery (every ~5s).
  - Each instance also listens on BEACON_PORT and keeps a live peer table.
  - A TCP server on SYNC_PORT accepts incoming database pushes.
  - Sync is a deliberate one-way push: admin selects a peer in the
    Sera Sync dialog and pushes their master.db + sera.salt to that peer.
  - The receiver auto-accepts and auto-restarts to load the new database.
  - No shared master password required: both master.db and sera.salt are
    transferred together, so the receiver gets a self-consistent pair.
    They will use the sender's password to log in after restart.

This module has no PySide6 dependency so it can be unit-tested headless;
main.py wires its Qt-facing callbacks (toasts, restart) in.
"""

import os
import sys
import json
import socket
import struct
import hashlib
import threading
import time
import shutil
import datetime
from pathlib import Path
from typing import Optional, Callable


BEACON_PORT = 49156
SYNC_PORT = 49157
BEACON_INTERVAL_SEC = 5
PEER_TIMEOUT_SEC = 30
SOCK_TIMEOUT_SEC = 10

# Fixed app-level magic bytes for beacon validation (not password-derived)
SERA_SYNC_MAGIC = "sera-sync-v2"


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class PeerInfo:
    __slots__ = ("username", "host", "ip", "sync_port", "last_seen")

    def __init__(self, username, host, ip, sync_port, last_seen):
        self.username = username
        self.host = host
        self.ip = ip
        self.sync_port = sync_port
        self.last_seen = last_seen

    def key(self) -> str:
        return f"{self.host}:{self.ip}"

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "host": self.host,
            "ip": self.ip,
            "sync_port": self.sync_port,
            "last_seen": self.last_seen,
        }


class SyncPeerService:
    """
    Owns the beacon thread, listener thread, TCP sync server, and the
    peer table. Instantiate once per app run and call start()/stop().

    Callback hooks (all optional, called from background threads -- callers
    that touch Qt widgets must marshal back to the main thread themselves):
        on_peer_table_changed(list[dict])
        on_sync_received()                          # we received a database push, app should restart
        on_error(str)
    """

    def __init__(
        self,
        db_path: str,
        salt_path: str,
        username: str,
        on_peer_table_changed: Optional[Callable] = None,
        on_sync_received: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        self.db_path = db_path
        self.salt_path = salt_path
        self.username = username
        self.host_name = socket.gethostname()

        self.on_peer_table_changed = on_peer_table_changed
        self.on_sync_received = on_sync_received
        self.on_error = on_error

        self._peers: dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._udp_sock: Optional[socket.socket] = None
        self._tcp_server: Optional[socket.socket] = None

    # ---------------- lifecycle ----------------

    def start(self):
        self._stop_event.clear()
        self._start_udp_listener()
        self._start_beacon_sender()
        self._start_tcp_server()
        self._start_peer_reaper()

    def stop(self):
        self._stop_event.set()
        for sock in (self._udp_sock, self._tcp_server):
            try:
                if sock:
                    sock.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2)

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)
        return t

    # ---------------- UDP beacon (send + listen) ----------------

    def _beacon_payload(self) -> bytes:
        body = {
            "magic": SERA_SYNC_MAGIC,
            "username": self.username,
            "host": self.host_name,
            "sync_port": SYNC_PORT,
        }
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    def _start_beacon_sender(self):
        def loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop_event.is_set():
                try:
                    sock.sendto(self._beacon_payload(), ("255.255.255.255", BEACON_PORT))
                except OSError as e:
                    self._safe_call(self.on_error, f"Beacon send failed: {e}")
                self._stop_event.wait(BEACON_INTERVAL_SEC)
            sock.close()
        self._spawn(loop, "sync-beacon-sender")

    def _start_udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", BEACON_PORT))
        sock.settimeout(1.0)
        self._udp_sock = sock

        def loop():
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._handle_beacon(data, addr[0])
        self._spawn(loop, "sync-beacon-listener")

    def _handle_beacon(self, data: bytes, ip: str):
        try:
            body = json.loads(data.decode("utf-8"))
            if body.get("magic") != SERA_SYNC_MAGIC:
                return
            if body.get("host") == self.host_name:
                return  # ignore our own broadcast
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            return

        peer = PeerInfo(
            username=body.get("username", "Unknown"),
            host=body["host"],
            ip=ip,
            sync_port=int(body.get("sync_port", SYNC_PORT)),
            last_seen=time.time(),
        )
        with self._peers_lock:
            self._peers[peer.key()] = peer
        self._safe_call(self.on_peer_table_changed, self._peer_list())

    def _start_peer_reaper(self):
        def loop():
            while not self._stop_event.is_set():
                self._stop_event.wait(5)
                changed = False
                cutoff = time.time() - PEER_TIMEOUT_SEC
                with self._peers_lock:
                    stale = [k for k, p in self._peers.items() if p.last_seen < cutoff]
                    for k in stale:
                        del self._peers[k]
                        changed = True
                if changed:
                    self._safe_call(self.on_peer_table_changed, self._peer_list())
        self._spawn(loop, "sync-peer-reaper")

    def _peer_list(self) -> list[dict]:
        with self._peers_lock:
            return [p.as_dict() for p in self._peers.values()]

    def get_peers(self) -> list[dict]:
        return self._peer_list()

    # ---------------- TCP server: accept incoming database pushes ----------------

    def _start_tcp_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", SYNC_PORT))
        srv.listen(5)
        srv.settimeout(1.0)
        self._tcp_server = srv

        def loop():
            while not self._stop_event.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_incoming_push, args=(conn, addr[0]), daemon=True
                ).start()
        self._spawn(loop, "sync-tcp-server")

    def _handle_incoming_push(self, conn: socket.socket, sender_ip: str):
        """Receives master.db + sera.salt from the sender and overwrites local files."""
        try:
            conn.settimeout(SOCK_TIMEOUT_SEC)

            # Read header: JSON with action and sizes
            header_raw = _recv_framed(conn)
            header = json.loads(header_raw.decode("utf-8"))

            if header.get("action") != "push_database":
                conn.close()
                return

            sender_username = header.get("username", "Unknown")
            db_size = int(header["db_size"])
            salt_size = int(header["salt_size"])

            # Send ACK to proceed
            _send_framed(conn, json.dumps({"status": "ready"}).encode("utf-8"))

            # Receive database bytes
            db_bytes = _recv_exact(conn, db_size)
            # Receive salt bytes
            salt_bytes = _recv_exact(conn, salt_size)

            # Create safety backup of current live files
            live_dir = os.path.dirname(self.db_path)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

            if os.path.exists(self.db_path):
                backup_db = os.path.join(live_dir, f"master.db.pre-sync-{now_str}.db")
                shutil.copy2(self.db_path, backup_db)

            if os.path.exists(self.salt_path):
                backup_salt = os.path.join(live_dir, f"sera.salt.pre-sync-{now_str}")
                shutil.copy2(self.salt_path, backup_salt)

            # Atomic-ish replace: write to temp file then rename
            tmp_db = self.db_path + ".incoming"
            with open(tmp_db, "wb") as f:
                f.write(db_bytes)
            os.replace(tmp_db, self.db_path)

            tmp_salt = self.salt_path + ".incoming"
            with open(tmp_salt, "wb") as f:
                f.write(salt_bytes)
            os.replace(tmp_salt, self.salt_path)

            # Send success confirmation
            _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))

            # Notify app to auto-restart
            self._safe_call(self.on_sync_received)

        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            self._safe_call(self.on_error, f"Incoming sync from {sender_ip} failed: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ---------------- Push database to a peer ----------------

    def push_to(self, peer_ip: str, peer_port: int = SYNC_PORT) -> str:
        """
        Pushes local master.db + sera.salt to the specified peer.
        Returns a success/failure message string.
        """
        # Read local files
        if not os.path.exists(self.db_path):
            raise FileNotFoundError("Local master.db not found")
        if not os.path.exists(self.salt_path):
            raise FileNotFoundError("Local sera.salt not found")

        with open(self.db_path, "rb") as f:
            db_bytes = f.read()
        with open(self.salt_path, "rb") as f:
            salt_bytes = f.read()

        # Connect to peer
        try:
            with socket.create_connection((peer_ip, peer_port), timeout=SOCK_TIMEOUT_SEC) as conn:
                # Send header
                header = {
                    "action": "push_database",
                    "username": self.username,
                    "host": self.host_name,
                    "db_size": len(db_bytes),
                    "salt_size": len(salt_bytes),
                }
                _send_framed(conn, json.dumps(header).encode("utf-8"))

                # Wait for ACK
                ack_raw = _recv_framed(conn)
                ack = json.loads(ack_raw.decode("utf-8"))
                if ack.get("status") != "ready":
                    return f"Peer rejected sync request"

                # Send database + salt
                conn.sendall(db_bytes)
                conn.sendall(salt_bytes)

                # Wait for confirmation
                result_raw = _recv_framed(conn)
                result = json.loads(result_raw.decode("utf-8"))
                if result.get("status") == "ok":
                    return "Database synced successfully!"
                else:
                    return f"Sync failed: {result}"

        except OSError as e:
            return f"Could not connect to peer: {e}"

    def _safe_call(self, cb, *args):
        if cb:
            try:
                cb(*args)
            except Exception:
                pass


# ---------------- length-prefixed framing over TCP ----------------

def _send_framed(conn: socket.socket, data: bytes):
    conn.sendall(struct.pack("!I", len(data)) + data)


def _recv_framed(conn: socket.socket) -> bytes:
    header = _recv_exact(conn, 4)
    (length,) = struct.unpack("!I", header)
    return _recv_exact(conn, length)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(min(n - len(buf), 1 << 20))  # 1 MB chunks
        if not chunk:
            raise OSError("connection closed while reading frame")
        buf += chunk
    return buf
