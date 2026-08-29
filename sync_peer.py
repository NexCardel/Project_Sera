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
    __slots__ = ("username", "host", "ip", "sync_port", "app_version", "db_mtime", "last_seen", "inv_frames", "sync_revision", "client_count", "tracker_count", "timeline_count")

    def __init__(
        self,
        username,
        host,
        ip,
        sync_port,
        app_version="Unknown",
        db_mtime="",
        last_seen=0.0,
        inv_frames=False,
        sync_revision=0,
        client_count=0,
        tracker_count=0,
        timeline_count=0,
    ):
        self.username = username
        self.host = host
        self.ip = ip
        self.sync_port = sync_port
        self.app_version = app_version
        self.db_mtime = db_mtime
        self.last_seen = last_seen
        self.inv_frames = bool(inv_frames)
        self.sync_revision = int(sync_revision)
        self.client_count = int(client_count)
        self.tracker_count = int(tracker_count)
        self.timeline_count = int(timeline_count)

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
            "inv_frames": self.inv_frames,
            "sync_revision": self.sync_revision,
            "client_count": self.client_count,
            "tracker_count": self.tracker_count,
            "timeline_count": self.timeline_count,
        }


class SyncPeerService:
    """
    Owns the beacon thread, listener thread, TCP sync server, and the
    peer table. Instantiate once per app run and call start()/stop().

    Supports two synchronization types:
      - Initial Sync: Full database + salt transfer with pre-sync backup and app restart.
      - Live Sync: Lightweight incremental update with real-time UI refresh without restart.

    Supports the inv_frames protocol:
      - When inv_frames is True on a node, it rejects ALL incoming data but can push its DB.
      - If only 1 node on LAN has inv_frames ON, it acts as the master authority, and normal nodes accept.
      - If >1 node has inv_frames ON, the entire LAN sync halts (complementary freeze) to prevent corruption.
      - If 0 nodes have inv_frames ON, normal P2P sync operates freely.
    """

    def __init__(
        self,
        db_path: str,
        salt_path: str,
        username: str,
        sync_port: int = SYNC_PORT,
        db: Optional[Any] = None,
        inv_frames: bool = False,
        on_peer_table_changed: Optional[Callable] = None,
        on_sync_received: Optional[Callable] = None,
        on_live_sync_received: Optional[Callable] = None,
        on_peer_logs_received: Optional[Callable] = None,
        on_activity: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        self.db_path = db_path
        self.salt_path = salt_path
        self.username = username
        self.sync_port = sync_port
        self.db = db
        self.inv_frames = bool(inv_frames)
        self.host_name = socket.gethostname()

        self.on_peer_table_changed = on_peer_table_changed
        self.on_sync_received = on_sync_received
        self.on_live_sync_received = on_live_sync_received
        self.on_peer_logs_received = on_peer_logs_received
        self.on_activity = on_activity
        self.on_error = on_error

        self._peers: dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()

        self._activity_history: list[dict] = []
        self._activity_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._udp_sock: Optional[socket.socket] = None
        self._tcp_server: Optional[socket.socket] = None

    def set_inv_frames(self, enabled: bool):
        self.inv_frames = bool(enabled)
        mode_str = "ENABLED" if self.inv_frames else "DISABLED"
        metrics = self._get_local_metrics()
        self.log_activity(
            "INV_FRAMES",
            f"Inv-Frames mode {mode_str}",
            f"Node is Sovereign Master (Rev: {metrics.get('sync_revision', 0)})" if self.inv_frames else f"Node returned to normal P2P mode (Rev: {metrics.get('sync_revision', 0)})",
        )
        self.send_immediate_beacon()
        self._safe_call(self.on_peer_table_changed, self._peer_list())

    def send_immediate_beacon(self):
        def _send():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                payload = self._beacon_payload()
                sock.sendto(payload, ("255.255.255.255", BEACON_PORT))
                sock.close()
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()

    def get_active_inv_frames_nodes(self) -> list[str]:
        """Returns hostnames of all active nodes currently running with inv_frames = True."""
        nodes = []
        if self.inv_frames:
            nodes.append(self.host_name)
        cutoff = time.time() - PEER_TIMEOUT_SEC
        with self._peers_lock:
            for p in self._peers.values():
                if p.inv_frames and p.last_seen >= cutoff and p.host not in nodes:
                    nodes.append(p.host)
        return nodes

    def get_sync_state(self) -> dict:
        """Evaluates LAN sync state according to inv_frames protocol rules."""
        active_inv = self.get_active_inv_frames_nodes()
        total_inv = len(active_inv)
        if total_inv > 1:
            status = "LAN_SYNC_FROZEN_MULTI_INV"
            authority = None
        elif total_inv == 1:
            authority = active_inv[0]
            status = "INV_FRAMES_MASTER" if self.inv_frames else "INV_FRAMES_FOLLOWER"
        else:
            authority = None
            status = "NORMAL"
        return {
            "status": status,
            "authority_host": authority,
            "active_inv_frames_nodes": active_inv,
            "total_inv_frames_count": total_inv,
            "local_inv_frames": self.inv_frames,
        }

    def log_activity(self, cat: str, title: str, detail: str = ""):
        entry = {
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "category": cat,
            "title": title,
            "detail": detail,
        }
        with self._activity_lock:
            self._activity_history.append(entry)
            if len(self._activity_history) > 300:
                self._activity_history = self._activity_history[-300:]
        msg = f"{title} - {detail}" if detail else title
        self._safe_call(self.on_activity, entry["timestamp"], cat, msg)

    def get_activity_history(self) -> list[dict]:
        with self._activity_lock:
            return list(self._activity_history)

    def _get_local_metrics(self) -> dict:
        if self.db and hasattr(self.db, "get_sync_metrics"):
            try:
                return self.db.get_sync_metrics()
            except Exception:
                pass
        return {
            "client_count": 0,
            "archived_count": 0,
            "log_count": 0,
            "latest_timestamp": "",
            "sync_revision": 0,
        }

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
            try:
                t.join(timeout=0.05)
            except Exception:
                pass

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

        metrics = self._get_local_metrics()

        body = {
            "magic": SERA_SYNC_MAGIC,
            "username": self.username,
            "host": self.host_name,
            "sync_port": self.sync_port,
            "app_version": app_ver,
            "db_mtime": db_mtime_str,
            "db_mtime_ts": db_mtime_ts,
            "client_count": metrics.get("client_count", 0),
            "tracker_count": metrics.get("tracker_count", 0),
            "timeline_count": metrics.get("timeline_count", 0),
            "sync_revision": metrics.get("sync_revision", 0),
            "latest_timestamp": metrics.get("latest_timestamp", ""),
            "inv_frames": self.inv_frames,
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

        inv_frames = bool(body.get("inv_frames", False))
        sync_rev = int(body.get("sync_revision", 0))
        client_cnt = int(body.get("client_count", 0))
        tracker_cnt = int(body.get("tracker_count", 0))
        timeline_cnt = int(body.get("timeline_count", 0))

        peer = PeerInfo(
            username=body.get("username", "Unknown"),
            host=body["host"],
            ip=ip,
            sync_port=int(body.get("sync_port", SYNC_PORT)),
            app_version=body.get("app_version", "Unknown"),
            db_mtime=body.get("db_mtime", ""),
            last_seen=time.time(),
            inv_frames=inv_frames,
            sync_revision=sync_rev,
            client_count=client_cnt,
            tracker_count=tracker_cnt,
            timeline_count=timeline_cnt,
        )

        pk = peer.key()
        prev_peer = None
        with self._peers_lock:
            prev_peer = self._peers.get(pk)
            self._peers[pk] = peer

        # Log node discovery and revision score updates in live activity stream
        if not prev_peer:
            inv_tag = " [🛡️ INV-FRAMES]" if inv_frames else ""
            tracker_info = f" | Tracker: {tracker_cnt}" if tracker_cnt > 0 else ""
            self.log_activity("BEACON", f"Discovered {peer.username} ({peer.host}){inv_tag}", f"Rev Score: {sync_rev} | Clients: {client_cnt}{tracker_info}")
        elif prev_peer.sync_revision != sync_rev or prev_peer.inv_frames != inv_frames:
            inv_tag = " [🛡️ INV-FRAMES]" if inv_frames else ""
            tracker_info = f" | Tracker: {tracker_cnt}" if tracker_cnt > 0 else ""
            self.log_activity("REVISION", f"Node {peer.host} updated{inv_tag}", f"Rev Score: {sync_rev} (was {prev_peer.sync_revision}) | Clients: {client_cnt}{tracker_info}")

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
        """Receives database pushes or SSAL audit logs from network peers obeying inv_frames protocol."""
        try:
            conn.settimeout(SOCK_TIMEOUT_SEC)

            # Read header: JSON with action and payload details
            header_raw = _recv_framed(conn)
            header = json.loads(header_raw.decode("utf-8"))
            action = header.get("action")
            sender_host = header.get("host", sender_ip)
            sender_username = header.get("username", "Unknown")
            is_live_update = bool(header.get("live_update", False))
            force_override = bool(header.get("force_override", False))
            incoming_client_count = int(header.get("client_count", 0))
            incoming_sync_rev = int(header.get("sync_revision", 0))
            incoming_latest_ts = str(header.get("latest_timestamp", ""))
            sender_inv_frames = bool(header.get("inv_frames", False))

            sync_state = self.get_sync_state()
            local_metrics = self._get_local_metrics()
            local_client_count = local_metrics.get("client_count", 0)
            local_sync_rev = local_metrics.get("sync_revision", 0)
            local_latest_ts = local_metrics.get("latest_timestamp", "")

            # ---------------- PROTOCOL RULE 1: Local inv_frames Mode ----------------
            if self.inv_frames:
                reject_reason = f"INV_FRAMES_ACTIVE: Local node ({self.host_name}) is in Inv-Frames mode and rejects all incoming data."
                print(f"[Inv-Frames] Rejected {action} from {sender_host}: {reject_reason}")
                self.log_activity("INV_FRAMES", f"Rejected {action} from {sender_host}", f"Local Inv-Frames is ON | Sender Rev: {incoming_sync_rev}, Local Rev: {local_sync_rev}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_reason}).encode("utf-8"))
                return

            # ---------------- PROTOCOL RULE 2: Multiple inv_frames Nodes (LAN Freeze) ----------------
            if sync_state["status"] == "LAN_SYNC_FROZEN_MULTI_INV":
                inv_nodes_str = ", ".join(sync_state["active_inv_frames_nodes"])
                reject_reason = f"LAN_SYNC_FROZEN: Multiple nodes ({inv_nodes_str}) have Inv-Frames active. All LAN sync is paused to prevent corruption."
                print(f"[LAN Sync Frozen] Blocked sync from {sender_host}: {reject_reason}")
                self.log_activity("GUARD", f"Blocked sync from {sender_host}", f"LAN Sync Frozen (Multiple Inv-Frames nodes: {inv_nodes_str}) | Sender Rev: {incoming_sync_rev}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_reason}).encode("utf-8"))
                return

            # ---------------- PROTOCOL RULE 3: Single inv_frames Authority Node ----------------
            if sync_state["status"] == "INV_FRAMES_FOLLOWER":
                authority = sync_state["authority_host"]
                if sender_host != authority and not sender_inv_frames:
                    reject_reason = f"INV_FRAMES_AUTHORITY_ACTIVE: Node {authority} is the active Inv-Frames authority. Sync between normal nodes is locked."
                    print(f"[Inv-Frames Lock] Blocked non-authority push from {sender_host}: {reject_reason}")
                    self.log_activity("INV_FRAMES", f"Blocked push from {sender_host}", f"Waiting for Authority {authority} | Sender Rev: {incoming_sync_rev}")
                    _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_reason}).encode("utf-8"))
                    return

            if action == "push_audit_log":
                logs = header.get("logs", [])
                live_dir = os.path.dirname(self.db_path)
                try:
                    from database import PeerAuditLogManager
                    mgr = PeerAuditLogManager(live_dir)
                    mgr.store_peer_logs(sender_host, logs)
                    self.log_activity("SSAL", f"Received {len(logs)} audit log(s) from {sender_host}", f"Local Rev: {local_sync_rev}")
                except Exception as ex:
                    print(f"[SSAL] Error storing peer logs from {sender_host}: {ex}")

                _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))
                self._safe_call(self.on_peer_logs_received, sender_host)
                return

            if action == "request_database_pull":
                peer_port = int(header.get("sync_port", SYNC_PORT))
                print(f"[LAN Pull Request] {sender_host} requested database pull. Pushing local DB...")
                self.log_activity("PULL", f"Pull request from {sender_host}", f"Local Rev Score: {local_sync_rev} | Pushing to {sender_ip}:{peer_port}")
                _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))
                def fulfill_pull():
                    time.sleep(0.2)
                    self.push_to(sender_ip, peer_port, live_update=True)
                threading.Thread(target=fulfill_pull, daemon=True).start()
                return

            if action != "push_database":
                conn.close()
                return

            db_size = int(header["db_size"])
            salt_size = int(header["salt_size"])
            raw_db_size = int(header.get("raw_db_size", 0))

            # ---------------- PROTOCOL RULE 4: Normal P2P Sync Guard ----------------
            # If in Normal P2P mode (not following a sovereign Inv-Frames node), enforce standard revision protection
            if sync_state["status"] == "NORMAL" and not force_override:
                reject_reason = None
                if incoming_client_count == 0 and local_client_count > 0:
                    reject_reason = f"Incoming empty database (0 clients) rejected to protect local records ({local_client_count} clients)"
                elif incoming_client_count < local_client_count:
                    reject_reason = f"Incoming database has fewer clients ({incoming_client_count}) than local database ({local_client_count})"
                elif incoming_client_count == local_client_count and incoming_sync_rev < local_sync_rev:
                    reject_reason = f"Incoming database revision ({incoming_sync_rev}) is lower than local database revision ({local_sync_rev})"
                elif incoming_client_count == local_client_count and incoming_sync_rev == local_sync_rev and local_latest_ts > incoming_latest_ts:
                    reject_reason = f"Incoming database timestamp ({incoming_latest_ts}) is older than local timestamp ({local_latest_ts})"

                if reject_reason:
                    print(f"[Sync Guard] Rejected incoming DB push from {sender_host}: {reject_reason}")
                    self.log_activity("GUARD", f"Rejected DB push from {sender_host}", f"{reject_reason} | Sender Rev: {incoming_sync_rev}, Local Rev: {local_sync_rev}")
                    _send_framed(conn, json.dumps({
                        "status": "rejected",
                        "reason": reject_reason
                    }).encode("utf-8"))
                    # Trigger reverse auto-push so sender receives our higher-revision database
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
            # Receive raw payload db bytes if present
            raw_db_bytes = b""
            if raw_db_size > 0:
                raw_db_bytes = _recv_exact(conn, raw_db_size)

            # Create safety backup of current live files
            live_dir = os.path.dirname(self.db_path)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

            if os.path.exists(self.db_path):
                backup_db = os.path.join(live_dir, f"master.db.pre-sync-{now_str}.db")
                shutil.copy2(self.db_path, backup_db)

            if os.path.exists(self.salt_path):
                backup_salt = os.path.join(live_dir, f"sera.salt.pre-sync-{now_str}")
                shutil.copy2(self.salt_path, backup_salt)

            raw_db_path = os.path.join(live_dir, "rawPayload.db")
            if os.path.exists(raw_db_path):
                backup_raw = os.path.join(live_dir, f"rawPayload.db.pre-sync-{now_str}.db")
                shutil.copy2(raw_db_path, backup_raw)

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
            if raw_db_size > 0:
                safe_write_file(raw_db_path, raw_db_bytes)

            # Delete lingering SQLite WAL / journal sidecar files (-wal, -shm, -journal)
            for ext in ["-wal", "-shm", "-journal"]:
                sidecar = self.db_path + ext
                if os.path.exists(sidecar):
                    try:
                        os.remove(sidecar)
                    except OSError:
                        pass
                raw_sidecar = raw_db_path + ext
                if os.path.exists(raw_sidecar):
                    try:
                        os.remove(raw_sidecar)
                    except OSError:
                        pass

            # Send success confirmation
            _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))
            sync_type_label = "Live Sync" if is_live_update else "Initial Full Sync"
            auth_tag = " (Inv-Frames Master)" if (sync_state["status"] == "INV_FRAMES_FOLLOWER" and sender_host == sync_state["authority_host"]) else ""
            self.log_activity(
                "SYNC IN",
                f"Accepted {sync_type_label} from {sender_username} ({sender_host}){auth_tag}",
                f"New Rev: {incoming_sync_rev} | Previous Rev: {local_sync_rev} | Clients: {incoming_client_count}",
            )

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
        sync_state = self.get_sync_state()
        if sync_state["status"] == "LAN_SYNC_FROZEN_MULTI_INV" and not force_override:
            inv_nodes_str = ", ".join(sync_state["active_inv_frames_nodes"])
            msg = f"Sync blocked: Multiple nodes ({inv_nodes_str}) have Inv-Frames active. LAN sync is frozen."
            self.log_activity("GUARD", "Outbound sync blocked", msg)
            return msg

        # Read local files
        if not os.path.exists(self.db_path):
            raise FileNotFoundError("Local master.db not found")
        if not os.path.exists(self.salt_path):
            raise FileNotFoundError("Local sera.salt not found")

        with open(self.db_path, "rb") as f:
            db_bytes = f.read()
        with open(self.salt_path, "rb") as f:
            salt_bytes = f.read()

        raw_db_path = os.path.join(os.path.dirname(self.db_path), "rawPayload.db")
        raw_db_bytes = b""
        if os.path.exists(raw_db_path):
            with open(raw_db_path, "rb") as f:
                raw_db_bytes = f.read()

        local_mtime = os.path.getmtime(self.db_path) if os.path.exists(self.db_path) else 0.0
        metrics = self._get_local_metrics()
        local_sync_rev = metrics.get("sync_revision", 0)
        local_client_cnt = metrics.get("client_count", 0)

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
                    "raw_db_size": len(raw_db_bytes),
                    "live_update": live_update,
                    "force_override": force_override,
                    "client_count": local_client_cnt,
                    "sync_revision": local_sync_rev,
                    "latest_timestamp": metrics.get("latest_timestamp", ""),
                    "db_mtime": local_mtime,
                    "inv_frames": self.inv_frames,
                }
                _send_framed(conn, json.dumps(header).encode("utf-8"))

                # Wait for ACK
                ack_raw = _recv_framed(conn)
                ack = json.loads(ack_raw.decode("utf-8"))
                if ack.get("status") != "ready":
                    reason = ack.get("reason", "Peer rejected sync request")
                    self.log_activity("PUSH", f"Sync rejected by {peer_ip}:{peer_port}", reason)
                    return f"Sync skipped: {reason}"

                # Send database + salt
                conn.sendall(db_bytes)
                conn.sendall(salt_bytes)
                if len(raw_db_bytes) > 0:
                    conn.sendall(raw_db_bytes)

                # Wait for confirmation
                result_raw = _recv_framed(conn)
                result = json.loads(result_raw.decode("utf-8"))
                if result.get("status") == "ok":
                    sync_kind = "Live Sync" if live_update else "Initial Full Sync"
                    self.log_activity(
                        "PUSH",
                        f"Pushed {sync_kind} to {peer_ip}:{peer_port}",
                        f"Local Rev Score: {local_sync_rev} | Clients: {local_client_cnt}",
                    )
                    return f"{sync_kind} synced successfully!"
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

    def request_pull_from(self, peer_ip: str, peer_port: int = SYNC_PORT) -> bool:
        """Requests specified peer to push their higher-revision database to us."""
        try:
            with socket.create_connection((peer_ip, peer_port), timeout=SOCK_TIMEOUT_SEC) as conn:
                header = {
                    "action": "request_database_pull",
                    "username": self.username,
                    "host": self.host_name,
                    "sync_port": self.sync_port,
                }
                _send_framed(conn, json.dumps(header).encode("utf-8"))
                resp_raw = _recv_framed(conn)
                resp = json.loads(resp_raw.decode("utf-8"))
                return resp.get("status") == "ok"
        except OSError as ex:
            print(f"[LAN Pull Request] Failed to request database from {peer_ip}: {ex}")
            return False

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
