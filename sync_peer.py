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
SOCK_TIMEOUT_SEC = 3

# Fixed app-level magic bytes for beacon validation (not password-derived)
SERA_SYNC_MAGIC = "sera-sync-v2"


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class PeerInfo:
    __slots__ = ("username", "host", "ip", "sync_port", "app_version", "db_mtime", "last_seen")

    def __init__(self, username, host, ip, sync_port, app_version="Unknown", db_mtime="", last_seen=0.0):
        self.username = username
        self.host = host
        self.ip = ip
        self.sync_port = sync_port
        self.app_version = app_version
        self.db_mtime = db_mtime
        self.last_seen = last_seen

    def key(self) -> str:
        return f"{self.host}:{self.ip}"

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "host": self.host,
            "ip": self.ip,
            "sync_port": self.sync_port,
            "app_version": self.app_version,
            "db_mtime": self.db_mtime,
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
        sync_port: int = SYNC_PORT,
        on_peer_table_changed: Optional[Callable] = None,
        on_sync_received: Optional[Callable] = None,
        on_live_sync_received: Optional[Callable] = None,
        on_peer_logs_received: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        self.db_path = db_path
        self.salt_path = salt_path
        self.username = username
        self.sync_port = sync_port
        self.host_name = socket.gethostname()

        self.on_peer_table_changed = on_peer_table_changed
        self.on_sync_received = on_sync_received
        self.on_live_sync_received = on_live_sync_received
        self.on_peer_logs_received = on_peer_logs_received
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
        db_mtime_str = ""
        db_mtime_ts = 0.0
        try:
            if os.path.exists(self.db_path):
                mtime = os.path.getmtime(self.db_path)
                db_mtime_ts = mtime
                db_mtime_str = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

        app_ver = "Unknown"
        try:
            import version
            app_ver = getattr(version, "APP_VERSION", "Unknown")
        except Exception:
            pass

        body = {
            "magic": SERA_SYNC_MAGIC,
            "username": self.username,
            "host": self.host_name,
            "sync_port": self.sync_port,
            "app_version": app_ver,
            "db_mtime": db_mtime_str,
            "db_mtime_ts": db_mtime_ts,
        }
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    def _start_beacon_sender(self):
        def loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop_event.is_set():
                try:
                    payload = self._beacon_payload()
                    sock.sendto(payload, ("255.255.255.255", BEACON_PORT))
                except OSError as e:
                    # Suppress transient network unreachable error (WinError 10065) when adapter is temporarily offline
                    winerr = getattr(e, "winerror", None)
                    if winerr != 10065 and getattr(e, "errno", None) != 10065 and "10065" not in str(e):
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
            app_version=body.get("app_version", "Unknown"),
            db_mtime=body.get("db_mtime", ""),
            last_seen=time.time(),
        )

        pk = peer.key()
        is_new_peer = False
        with self._peers_lock:
            if pk not in self._peers:
                is_new_peer = True
            self._peers[pk] = peer

        self._safe_call(self.on_peer_table_changed, self._peer_list())

        # Auto-catchup: If a newly connected peer has an older database timestamp than our local database,
        # automatically push our newer local database to the new peer ONCE when it connects so it catches up!
        if is_new_peer:
            peer_mtime_ts = float(body.get("db_mtime_ts", 0))
            local_mtime_ts = 0.0
            if os.path.exists(self.db_path):
                local_mtime_ts = os.path.getmtime(self.db_path)
            if local_mtime_ts > 0 and peer_mtime_ts > 0 and (local_mtime_ts - peer_mtime_ts > 5.0):
                def bg_auto_sync():
                    time.sleep(1.0)
                    print(f"[LAN Catch-up] Pushing local newer DB to newly connected peer {peer.host} ({peer.ip})")
                    self.push_to(peer.ip, peer.sync_port, live_update=True)
                threading.Thread(target=bg_auto_sync, daemon=True).start()

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
        srv.bind(("0.0.0.0", self.sync_port))
        self.sync_port = srv.getsockname()[1]
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
        """Receives database pushes or SSAL audit logs from network peers."""
        try:
            conn.settimeout(SOCK_TIMEOUT_SEC)

            # Read header: JSON with action and payload details
            header_raw = _recv_framed(conn)
            header = json.loads(header_raw.decode("utf-8"))
            action = header.get("action")

            if action == "push_audit_log":
                sender_host = header.get("host", sender_ip)
                logs = header.get("logs", [])
                live_dir = os.path.dirname(self.db_path)
                try:
                    from database import PeerAuditLogManager
                    mgr = PeerAuditLogManager(live_dir)
                    mgr.store_peer_logs(sender_host, logs)
                except Exception as ex:
                    print(f"[SSAL] Error storing peer logs from {sender_host}: {ex}")

                _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))
                self._safe_call(self.on_peer_logs_received, sender_host)
                return

            if action != "push_database":
                conn.close()
                return

            sender_username = header.get("username", "Unknown")
            sender_host = header.get("host", sender_ip)
            is_live_update = bool(header.get("live_update", False))
            force_override = bool(header.get("force_override", False))
            incoming_mtime = float(header.get("db_mtime", 0))
            db_size = int(header["db_size"])
            salt_size = int(header["salt_size"])

            # TIMESTAMP CONFLICT GUARD:
            # If local database is newer than incoming database by > 3.0s, and force_override is False,
            # REJECT incoming push to protect local actions from being overwritten by an older database snapshot!
            local_mtime = os.path.getmtime(self.db_path) if os.path.exists(self.db_path) else 0
            if not force_override and local_mtime > 0 and incoming_mtime > 0 and (local_mtime - incoming_mtime > 3.0):
                print(f"[Sync Guard] Rejected older DB push from {sender_host}: Local mtime ({local_mtime}) > Incoming mtime ({incoming_mtime})")
                _send_framed(conn, json.dumps({
                    "status": "rejected",
                    "reason": "Local database is newer than incoming database. Overwrite prevented."
                }).encode("utf-8"))
                # Trigger a reverse auto-push so sender receives our newer database
                def reverse_sync():
                    time.sleep(1.0)
                    self.push_to(sender_ip, int(header.get("sync_port", SYNC_PORT)), live_update=True)
                threading.Thread(target=reverse_sync, daemon=True).start()
                return

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

            # Write files with fallback if Windows holds a temporary file lock
            def safe_write_file(target_path, content_bytes):
                tmp_path = target_path + ".incoming"
                with open(tmp_path, "wb") as f:
                    f.write(content_bytes)
                try:
                    os.replace(tmp_path, target_path)
                except OSError:
                    with open(target_path, "wb") as f:
                        f.write(content_bytes)
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

            safe_write_file(self.db_path, db_bytes)
            safe_write_file(self.salt_path, salt_bytes)

            # Delete lingering SQLite WAL / journal sidecar files (-wal, -shm, -journal)
            # so SQLite doesn't replay old transactions upon app restart!
            for ext in ["-wal", "-shm", "-journal"]:
                sidecar = self.db_path + ext
                if os.path.exists(sidecar):
                    try:
                        os.remove(sidecar)
                    except OSError:
                        pass

            # Send success confirmation
            _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))

            if is_live_update and self.on_live_sync_received:
                self._safe_call(self.on_live_sync_received, sender_username, sender_host)
            else:
                self._safe_call(self.on_sync_received)

        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            self._safe_call(self.on_error, f"Incoming sync from {sender_ip} failed: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ---------------- Push database to a peer ----------------

    def push_to(self, peer_ip: str, peer_port: int = SYNC_PORT, live_update: bool = False, force_override: bool = False) -> str:
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

        local_mtime = os.path.getmtime(self.db_path) if os.path.exists(self.db_path) else 0.0

        # Connect to peer
        try:
            with socket.create_connection((peer_ip, peer_port), timeout=SOCK_TIMEOUT_SEC) as conn:
                # Send header
                header = {
                    "action": "push_database",
                    "username": self.username,
                    "host": self.host_name,
                    "sync_port": self.sync_port,
                    "db_size": len(db_bytes),
                    "salt_size": len(salt_bytes),
                    "live_update": live_update,
                    "force_override": force_override,
                    "db_mtime": local_mtime,
                }
                _send_framed(conn, json.dumps(header).encode("utf-8"))

                # Wait for ACK
                ack_raw = _recv_framed(conn)
                ack = json.loads(ack_raw.decode("utf-8"))
                if ack.get("status") != "ready":
                    reason = ack.get("reason", "Peer rejected sync request")
                    return f"Sync skipped: {reason}"

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

    def push_audit_logs_to_host(self, host_ip: str, logs: list[dict], host_port: int = SYNC_PORT) -> bool:
        """
        Pushes local audit log entries to the Host PC for SSAL aggregation.
        """
        if not logs:
            return True
        try:
            with socket.create_connection((host_ip, host_port), timeout=SOCK_TIMEOUT_SEC) as conn:
                header = {
                    "action": "push_audit_log",
                    "username": self.username,
                    "host": self.host_name,
                    "logs": logs,
                }
                _send_framed(conn, json.dumps(header).encode("utf-8"))
                result_raw = _recv_framed(conn)
                result = json.loads(result_raw.decode("utf-8"))
                return result.get("status") == "ok"
        except Exception as e:
            return False

    def push_to_all(self, peers: Optional[list[dict]] = None) -> dict[str, str]:
        """
        Pushes local master.db + sera.salt to all specified peers (or all known peers if None).
        Returns a dictionary mapping peer_host -> result string.
        """
        if peers is None:
            peers = self.get_peers()

        results = {}
        for peer in peers:
            peer_ip = peer.get("ip")
            peer_port = int(peer.get("sync_port", SYNC_PORT))
            peer_host = peer.get("host", peer_ip)
            if peer_ip:
                res = self.push_to(peer_ip, peer_port)
                results[peer_host] = res
        return results

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
