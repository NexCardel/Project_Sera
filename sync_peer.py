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
import hmac
import threading
import time
import shutil
import datetime
import glob
import tempfile
from pathlib import Path
from typing import Any, Optional, Callable

from sync_network_probe import NetworkCategoryMonitor


BEACON_PORT = 49156
SYNC_PORT = 49157
BEACON_INTERVAL_SEC = 5
MANUAL_PEER_INTERVAL_SEC = 10.0
PEER_TIMEOUT_SEC = 30
SOCK_TIMEOUT_SEC = 3

# ---------------- P0-7: HMAC authentication for legacy sync messages ----------------
# auth_key = HMAC-SHA256(hex_key_bytes, SERA_SYNC_AUTH_INFO); mac = HMAC-SHA256(auth_key, canonical_json(header w/o mac)).
# hex_key is the same SQLCipher key every PC in the office derives from the shared master
# password (+ its local sera.salt), so a valid mac proves the sender knows the office password.
SERA_SYNC_AUTH_INFO = b"sera-sync-auth-v1"
AUTH_TS_TOLERANCE_SEC = 120
# fetch_snapshot (P0-6 join) has no shared key yet: it is protected by the on-screen
# approval + one-time join code instead, so it is exempt from mac authentication.
AUTH_EXEMPT_ACTIONS = {"fetch_snapshot"}


def _get_directed_broadcast_addresses() -> set[str]:
    """
    Computes directed broadcast addresses for all active IPv4 adapters (ip | ~netmask)
    using ifaddr. Skips loopback (127.*) and link-local (169.254.*) addresses.
    Lazy-imports ifaddr and ipaddress (§0 rule 6).
    """
    bcast_addrs: set[str] = set()
    try:
        import ifaddr
        import ipaddress
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if not getattr(ip_info, "is_IPv4", False):
                    continue
                ip_str = ip_info.ip
                if not isinstance(ip_str, str):
                    continue
                if ip_str.startswith("127.") or ip_str.startswith("169.254."):
                    continue
                prefix = getattr(ip_info, "network_prefix", None)
                if prefix is None or not (0 <= prefix < 31):
                    continue
                try:
                    net = ipaddress.IPv4Network(f"{ip_str}/{prefix}", strict=False)
                    bcast = str(net.broadcast_address)
                    if bcast and not bcast.startswith("127.") and not bcast.startswith("169.254."):
                        bcast_addrs.add(bcast)
                except Exception:
                    pass
    except Exception:
        pass
    return bcast_addrs


_OWN_IPS_CACHE: set[str] = set()
_OWN_IPS_CACHE_TIME: float = 0.0


def _get_own_ips(force_refresh: bool = False) -> set[str]:
    """
    Returns the set of local IPv4 addresses on all active adapters and hostnames.
    Cached for 15 seconds to avoid expensive socket.gethostbyname_ex lookups on every cycle.
    Lazy-imports ifaddr (§0 rule 6).
    """
    global _OWN_IPS_CACHE, _OWN_IPS_CACHE_TIME
    now = time.monotonic()
    if not force_refresh and _OWN_IPS_CACHE and (now - _OWN_IPS_CACHE_TIME < 15.0):
        return set(_OWN_IPS_CACHE)

    ips: set[str] = set()
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if getattr(ip_info, "is_IPv4", False) and isinstance(ip_info.ip, str):
                    ips.add(ip_info.ip)
    except Exception:
        pass
    try:
        for host in (socket.gethostname(), "localhost"):
            try:
                for addr in socket.gethostbyname_ex(host)[2]:
                    ips.add(addr)
            except Exception:
                pass
    except Exception:
        pass
    ips.add("127.0.0.1")
    _OWN_IPS_CACHE = set(ips)
    _OWN_IPS_CACHE_TIME = now
    return ips


def _parse_peer_address(addr: str, default_port: int = BEACON_PORT) -> tuple[str, int]:
    """Parses 'ip' or 'ip:port' into (ip, port). Ensures port is 1-65535, falling back to default_port."""
    addr = str(addr).strip()
    def_port = default_port if (1 <= default_port <= 65535) else BEACON_PORT
    if ":" in addr:
        parts = addr.rsplit(":", 1)
        try:
            port = int(parts[1].strip())
            if 1 <= port <= 65535:
                return parts[0].strip(), port
        except (ValueError, TypeError):
            pass
        return parts[0].strip(), def_port
    return addr, def_port



def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def prune_pre_sync_backups(directory: str | Path, max_keep: int = 5):
    """
    Retains the most recent `max_keep` pre-sync backups and safely purges older ones (FIFO).
    Guarantees that disk space never grows indefinitely from repeated sync operations.
    """
    try:
        patterns = [
            "master.db.pre-sync-*.db",
            "sera.salt.pre-sync-*",
            "master.db-wal.pre-sync-*",
            "master.db-shm.pre-sync-*",
        ]
        for pat in patterns:
            matching_files = glob.glob(os.path.join(directory, pat))
            matching_files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0)
            if len(matching_files) > max_keep:
                to_delete = matching_files[:-max_keep]
                for old_f in to_delete:
                    try:
                        os.remove(old_f)
                    except OSError:
                        pass
    except Exception as e:
        print(f"[Sera Sync] Backup rotation notice: {e}")


def _retry_file_op(op, max_wait_sec: float = 5.0, interval: float = 0.25):
    """Retries a filesystem operation on PermissionError / OSError for up to max_wait_sec.
    Essential on Windows where a recently closed process may briefly hold file locks."""
    start = time.monotonic()
    while True:
        try:
            return op()
        except (PermissionError, OSError):
            if time.monotonic() - start >= max_wait_sec:
                raise
            time.sleep(interval)


def prune_outgoing_snapshots(directory: str | Path, max_age_seconds: float = 300.0):
    """
    Cleans up leftover snap_* temporary directories in incoming/out/ caused by prior crashes
    or aborted transfers. If max_age_seconds is 0.0, purges all snap_* directories regardless of age.
    """
    try:
        out_dir = Path(directory)
        if not out_dir.exists():
            return
        now = time.time()
        for p in out_dir.glob("snap_*"):
            if p.is_dir():
                try:
                    if max_age_seconds <= 0.0:
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        mtime = p.stat().st_mtime
                        if now - mtime >= max_age_seconds:
                            shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass
    except Exception as e:
        print(f"[Sera Sync] Stale snapshot cleanup notice: {e}")


def apply_pending_swap(app_dir: str | Path) -> bool:
    """
    Applies any staged database swap in app_dir/incoming/ pending an application restart.
    Must be called at startup BEFORE any database connection is opened.

    1. Reads incoming/pending_swap.json if present (returns False if none).
    2. Validates staged files strictly exist inside app_dir/incoming/ (no traversal, no fallback).
    3. Backs up current live master.db, sera.salt, and wal/shm sidecars if present.
    4. Replaces live files with staged incoming files using os.replace (with retry on Windows lock).
    5. Removes SQLite sidecars (master.db-wal, master.db-shm, master.db-journal).
    6. Deletes pending_swap.json (or renames to .done on failure).
    7. On any error, safely restores pre-sync copies without ever deleting pre-existing live DB/salt,
       and renames pending_swap.json to .failed.
    """
    app_path = Path(app_dir).resolve()
    incoming_dir = app_path / "incoming"
    pending_json = incoming_dir / "pending_swap.json"
    failed_path = incoming_dir / "pending_swap.json.failed"

    if not pending_json.exists():
        return False

    try:
        with open(pending_json, "r", encoding="utf-8") as f:
            swap_info = json.load(f)
    except Exception as e:
        print(f"[apply_pending_swap] Invalid pending_swap.json: {e}")
        try:
            if failed_path.exists():
                failed_path.unlink()
            os.replace(pending_json, failed_path)
        except OSError:
            pass
        return False

    db_rel = str(swap_info.get("db", "master.db")).strip()
    salt_rel = str(swap_info.get("salt", "sera.salt")).strip()

    # Strict staging validation: file names cannot contain directory separators or path traversal
    db_name = Path(db_rel).name
    salt_name = Path(salt_rel).name
    if not db_name or not salt_name or "/" in db_rel or "\\" in db_rel or ".." in db_rel:
        print(f"[apply_pending_swap] Security error: invalid path in pending_swap.json (db: {db_rel}, salt: {salt_rel})")
        try:
            if failed_path.exists():
                failed_path.unlink()
            os.replace(pending_json, failed_path)
        except OSError:
            pass
        return False

    # Staged files MUST exist directly inside incoming_dir (NEVER fall back to app_path)
    staged_db = incoming_dir / db_name
    staged_salt = incoming_dir / salt_name

    if not staged_db.is_file() or not staged_salt.is_file():
        print(f"[apply_pending_swap] Staged files missing (db: {staged_db.is_file()}, salt: {staged_salt.is_file()})")
        try:
            if failed_path.exists():
                failed_path.unlink()
            os.replace(pending_json, failed_path)
        except OSError:
            pass
        return False

    # Live target files
    live_db = app_path / db_name
    live_salt = app_path / salt_name
    live_wal = app_path / f"{db_name}-wal"
    live_shm = app_path / f"{db_name}-shm"

    had_live_db = live_db.exists()
    had_live_salt = live_salt.exists()
    had_live_wal = live_wal.exists()
    had_live_shm = live_shm.exists()

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_db = app_path / f"{db_name}.pre-sync-{ts}.db"
    backup_salt = app_path / f"{salt_name}.pre-sync-{ts}"
    backup_wal = app_path / f"{db_name}-wal.pre-sync-{ts}"
    backup_shm = app_path / f"{db_name}-shm.pre-sync-{ts}"

    copied_db_backup = False
    copied_salt_backup = False
    copied_wal_backup = False
    copied_shm_backup = False
    swapped_db = False
    swapped_salt = False

    try:
        # Create safety copies of current live files with retry for Windows file locks
        if had_live_db:
            _retry_file_op(lambda: shutil.copy2(live_db, backup_db))
            copied_db_backup = True
        if had_live_salt:
            _retry_file_op(lambda: shutil.copy2(live_salt, backup_salt))
            copied_salt_backup = True
        if had_live_wal:
            _retry_file_op(lambda: shutil.copy2(live_wal, backup_wal))
            copied_wal_backup = True
        if had_live_shm:
            _retry_file_op(lambda: shutil.copy2(live_shm, backup_shm))
            copied_shm_backup = True

        # Prune older pre-sync backups (keep 5)
        prune_pre_sync_backups(str(app_path), max_keep=5)

        # Ensure existing live WAL and sidecars are safely removed before replacing live DB,
        # so an old WAL cannot attach to the new incoming DB file.
        for ext in ["-wal", "-shm", "-journal"]:
            sidecar = app_path / f"{db_name}{ext}"
            if sidecar.exists():
                _retry_file_op(lambda: sidecar.unlink(missing_ok=True), max_wait_sec=3.0)

        # Atomic replacement with retry for Windows process termination race
        _retry_file_op(lambda: os.replace(staged_db, live_db))
        swapped_db = True

        _retry_file_op(lambda: os.replace(staged_salt, live_salt))
        swapped_salt = True

        # Delete pending_swap.json
        try:
            _retry_file_op(lambda: pending_json.unlink(missing_ok=True), max_wait_sec=2.0)
        except OSError:
            done_path = incoming_dir / "pending_swap.json.done"
            try:
                if done_path.exists():
                    done_path.unlink()
                os.replace(pending_json, done_path)
            except OSError:
                pass

        # Clean up any leftover outgoing snapshots (from P0-3 note #4)
        out_dir = app_path / "incoming" / "out"
        if out_dir.exists():
            prune_outgoing_snapshots(out_dir, max_age_seconds=0.0)

        print(f"[apply_pending_swap] Successfully swapped staged database from {swap_info.get('from', 'peer')} (at {swap_info.get('at', '')})")
        return True

    except Exception as exc:
        print(f"[apply_pending_swap] Error during swap: {exc}. Rolling back to pre-sync backup...")
        # Roll back safely: NEVER delete pre-existing live DB or salt!
        try:
            if swapped_db:
                if had_live_db and copied_db_backup and backup_db.exists():
                    _retry_file_op(lambda: shutil.copy2(backup_db, live_db))
                elif not had_live_db:
                    live_db.unlink(missing_ok=True)

            if swapped_salt:
                if had_live_salt and copied_salt_backup and backup_salt.exists():
                    _retry_file_op(lambda: shutil.copy2(backup_salt, live_salt))
                elif not had_live_salt:
                    live_salt.unlink(missing_ok=True)

            # If live DB is rolled back or replacement failed, restore WAL/SHM sidecars
            # whenever they existed and were backed up, regardless of swapped_db.
            if had_live_wal and copied_wal_backup and backup_wal.exists() and not live_wal.exists():
                _retry_file_op(lambda: shutil.copy2(backup_wal, live_wal))
            if had_live_shm and copied_shm_backup and backup_shm.exists() and not live_shm.exists():
                _retry_file_op(lambda: shutil.copy2(backup_shm, live_shm))
        except Exception as rollback_err:
            print(f"[apply_pending_swap] Fatal rollback error: {rollback_err}")

        # Rename pending_swap.json to .failed
        try:
            if failed_path.exists():
                failed_path.unlink()
            if pending_json.exists():
                os.replace(pending_json, failed_path)
        except OSError:
            pass

        raise exc


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
        return self.host

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
        hex_key: Optional[str] = None,
        key_path: Optional[str] = None,
        master_password: Optional[str] = None,
        on_peer_table_changed: Optional[Callable] = None,
        on_sync_received: Optional[Callable] = None,
        on_live_sync_received: Optional[Callable] = None,
        on_peer_logs_received: Optional[Callable] = None,
        on_tracker_dump_received: Optional[Callable] = None,
        on_activity: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        on_join_approval_requested: Optional[Callable[[str, str, str], bool]] = None,
        host_name: Optional[str] = None,
        enable_broadcast: bool = True,
        beacon_port: int = BEACON_PORT,
        bind_host: str = "",
        manual_peers: Optional[list[str]] = None,
    ):
        self.db_path = db_path
        self.salt_path = salt_path
        self.username = username
        self.sync_port = sync_port
        self.db = db
        self.inv_frames = bool(inv_frames)
        self.hex_key = hex_key
        self.key_path = key_path
        self.master_password = master_password
        self.host_name = host_name or socket.gethostname()
        self.enable_broadcast = bool(enable_broadcast)
        self.beacon_port = int(beacon_port)
        self.bind_host = str(bind_host) if bind_host else ""
        self._manual_peers: list[str] = [str(p).strip() for p in (manual_peers or []) if str(p).strip()]
        self._unicast_reply_cooldown: dict[str, float] = {}

        self.on_peer_table_changed = on_peer_table_changed
        self.on_sync_received = on_sync_received
        self.on_live_sync_received = on_live_sync_received
        self.on_peer_logs_received = on_peer_logs_received
        self.on_tracker_dump_received = on_tracker_dump_received
        self.on_activity = on_activity
        self.on_error = on_error
        self.on_join_approval_requested = on_join_approval_requested

        self._peers: dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()

        self._activity_history: list[dict] = []
        self._activity_lock = threading.Lock()
        self._staging_lock = threading.Lock()
        self._join_lock = threading.Lock()
        self._join_in_progress = False

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._udp_sock: Optional[socket.socket] = None
        self._tcp_server: Optional[socket.socket] = None

        # P0-9b: Public-network warning. Polled at start() and every 10 minutes;
        # the Sera Sync panel reads get_network_category() on its own refresh timer.
        self._network_monitor = NetworkCategoryMonitor(
            on_change=lambda result: self._on_network_category_changed(result)
        )
        self._last_known_public: Optional[bool] = None

        # Bootstrap quarantine: if local DB is empty on startup, block all outbound pushes
        # until we have received a pull from a higher-revision peer.
        local_metrics = self._get_local_metrics()
        self._is_bootstrapping: bool = (local_metrics.get("client_count", 0) == 0)
        self._bootstrap_pull_done: bool = False
        self._bootstrap_pull_attempted_peers: set[str] = set()

        # Clean up any leftover snap_* temporary folders from prior crashed runs
        try:
            out_base = Path(self.db_path).parent / "incoming" / "out"
            prune_outgoing_snapshots(out_base, max_age_seconds=0.0)
        except Exception:
            pass

    def _get_local_password(self) -> Optional[str]:
        """Resolves local master password from instance variable, explicit key_path, or default sera.key."""
        if getattr(self, "master_password", None):
            return self.master_password
        kp = getattr(self, "key_path", None)
        if kp and os.path.exists(kp):
            try:
                pwd = Path(kp).read_text(encoding="utf-8").strip()
                if pwd:
                    return pwd
            except Exception:
                pass
        default_key = Path(self.db_path).parent / "sera.key"
        if default_key.exists():
            try:
                pwd = default_key.read_text(encoding="utf-8").strip()
                if pwd:
                    return pwd
            except Exception:
                pass
        return "admin123"

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
        self._start_manual_peer_sender()
        self._start_tcp_server()
        self._start_peer_reaper()
        if self._network_monitor:
            self._network_monitor.start()

    def stop(self):
        self._stop_event.set()
        if self._network_monitor:
            self._network_monitor.stop()
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

    def get_network_category(self) -> dict:
        """Latest cached result from the network-category probe (P0-9b)."""
        if self._network_monitor:
            return self._network_monitor.last_result
        return {"is_public": False, "categories": [], "method": "disabled", "error": None}

    def _on_network_category_changed(self, result: dict):
        was_public = self._last_known_public
        is_public = bool(result.get("is_public"))
        self._last_known_public = is_public
        if is_public and was_public is not True:
            self.log_activity(
                "GUARD",
                "Public network detected",
                "Sera Sync is reachable by other devices on this network. "
                "For security: Windows Settings > Network > set this network to Private.",
            )

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)
        return t

    # ---------------- Manual peers & Unicast beacons (P0-10) ----------------

    def get_manual_peers(self) -> list[str]:
        """
        Returns the combined list of manual peer addresses:
        read from the office-wide setting 'sync_manual_peers' (JSON list) in DB,
        plus any in-memory manual peers.
        """
        combined = []
        seen = set()
        if self.db and hasattr(self.db, "get_setting"):
            try:
                raw = self.db.get_setting("sync_manual_peers", "[]")
                if raw:
                    peers = json.loads(raw)
                    if isinstance(peers, list):
                        for p in peers:
                            s = str(p).strip()
                            if s and s not in seen:
                                seen.add(s)
                                combined.append(s)
            except Exception:
                pass
        with self._peers_lock:
            for p in self._manual_peers:
                s = str(p).strip()
                if s and s not in seen:
                    seen.add(s)
                    combined.append(s)
        return combined

    def add_manual_peer(self, peer: str):
        """Adds a manual peer address in memory."""
        p = str(peer).strip()
        if not p:
            return
        with self._peers_lock:
            if p not in self._manual_peers:
                self._manual_peers.append(p)

    def set_manual_peers(self, peers: list[str]):
        """Sets the in-memory manual peer list."""
        with self._peers_lock:
            self._manual_peers = [str(p).strip() for p in peers if str(p).strip()]

    def remove_manual_peer(self, peer: str):
        """
        Removes a manual peer address in memory, from the office-wide setting
        'sync_manual_peers' (JSON list) in DB, and from active peers.
        """
        p = str(peer).strip()
        if not p:
            return
        # Only the exact entry is removed: removing "ip:port" must not also drop a
        # separate plain "ip" entry (or the other way round).
        ip_only, _ = _parse_peer_address(p, default_port=self.beacon_port)
        with self._peers_lock:
            self._manual_peers = [x for x in self._manual_peers if str(x).strip() != p]
            to_remove = [
                k for k, v in self._peers.items()
                if getattr(v, "ip", getattr(v, "ip_address", None)) == ip_only
            ]
            for k in to_remove:
                del self._peers[k]

        if self.db and hasattr(self.db, "get_setting") and hasattr(self.db, "set_setting"):
            try:
                raw = self.db.get_setting("sync_manual_peers", "[]")
                if raw:
                    peers = json.loads(raw)
                    if isinstance(peers, list):
                        new_peers = [str(x).strip() for x in peers if str(x).strip() != p]
                        self.db.set_setting("sync_manual_peers", json.dumps(new_peers))
            except Exception as e:
                print(f"[SyncPeerService] Failed to remove manual peer from settings: {e}")

        if to_remove:
            self._safe_call(self.on_peer_table_changed, self._peer_list())

    def is_own_address(self, target_ip: str, target_port: int, own_ips: Optional[set[str]] = None) -> bool:
        """Checks if (target_ip, target_port) corresponds to this instance's own address."""
        if self.bind_host and self.bind_host not in ("0.0.0.0", ""):
            return (target_ip == self.bind_host or (target_ip == "127.0.0.1" and self.bind_host == "127.0.0.1")) and target_port == self.beacon_port
        if target_port != self.beacon_port:
            return False
        if own_ips is None:
            own_ips = _get_own_ips()
        return target_ip in own_ips

    def send_manual_beacons(self, sock: Optional[socket.socket] = None):
        """
        Sends unicast discovery beacons to each peer in the manual peer list,
        skipping local addresses. Receivers respond with a unicast beacon back.
        """
        peers = self.get_manual_peers()
        if not peers:
            return

        payload = self._beacon_payload(request_reply=True)
        close_sock = False
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            close_sock = True

        try:
            own_ips = _get_own_ips()
            for peer_str in peers:
                try:
                    target_ip, target_port = _parse_peer_address(peer_str, default_port=self.beacon_port)
                    if self.is_own_address(target_ip, target_port, own_ips=own_ips):
                        continue
                    sock.sendto(payload, (target_ip, target_port))
                except Exception as e:
                    # Catch OverflowError, OSError, ValueError, etc. per peer so one malformed
                    # peer entry cannot abort sending to the remaining peers.
                    winerr = getattr(e, "winerror", None)
                    if winerr != 10065 and getattr(e, "errno", None) != 10065 and "10065" not in str(e):
                        self._safe_call(self.on_error, f"Manual beacon send to {peer_str} failed: {e}")
        finally:
            if close_sock:
                sock.close()

    def _start_manual_peer_sender(self):
        def loop():
            # Wait briefly after startup so listeners are ready
            if self._stop_event.wait(0.2):
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                while not self._stop_event.is_set():
                    try:
                        self.send_manual_beacons(sock)
                    except Exception:
                        pass
                    self._stop_event.wait(MANUAL_PEER_INTERVAL_SEC)
            finally:
                sock.close()
        self._spawn(loop, "sync-manual-peer-sender")

    def _send_unicast_beacon_reply(self, ip: str, port: int):
        # Validate port and enforce rate limit (max 1 reply per 5s per IP)
        if not (1 <= port <= 65535):
            return
        now = time.monotonic()
        with self._peers_lock:
            last_reply = self._unicast_reply_cooldown.get(ip, 0.0)
            if now - last_reply < 5.0:
                return
            self._unicast_reply_cooldown[ip] = now
            if len(self._unicast_reply_cooldown) > 200:
                cutoff = now - 30.0
                self._unicast_reply_cooldown = {k: v for k, v in self._unicast_reply_cooldown.items() if v > cutoff}

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            payload = self._beacon_payload(request_reply=False)
            sock.sendto(payload, (ip, port))
            sock.close()
        except Exception:
            pass

    # ---------------- UDP beacon (send + listen) ----------------

    def _beacon_payload(self, request_reply: bool = False) -> bytes:
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
            "beacon_port": self.beacon_port,
            "request_reply": bool(request_reply),
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
                if self.enable_broadcast:
                    targets = {"255.255.255.255"}
                    try:
                        targets.update(_get_directed_broadcast_addresses())
                    except Exception:
                        pass
                    payload = self._beacon_payload(request_reply=False)
                    for target in targets:
                        try:
                            sock.sendto(payload, (target, self.beacon_port))
                        except OSError as e:
                            # Suppress transient network unreachable error (WinError 10065) when adapter is temporarily offline
                            winerr = getattr(e, "winerror", None)
                            if winerr != 10065 and getattr(e, "errno", None) != 10065 and "10065" not in str(e):
                                self._safe_call(self.on_error, f"Beacon send to {target} failed: {e}")
                self._stop_event.wait(BEACON_INTERVAL_SEC)
            sock.close()
        self._spawn(loop, "sync-beacon-sender")

    def _start_udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_ip = self.bind_host if self.bind_host else ""
        sock.bind((bind_ip, self.beacon_port))
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
                try:
                    self._handle_beacon(data, addr[0])
                except Exception:
                    pass
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

        # P0-10: Safely parse and validate numeric fields
        try:
            inv_frames = bool(body.get("inv_frames", False))
            sync_rev = int(body.get("sync_revision", 0))
            client_cnt = int(body.get("client_count", 0))
            tracker_cnt = int(body.get("tracker_count", 0))
            timeline_cnt = int(body.get("timeline_count", 0))
            sync_port = int(body.get("sync_port", SYNC_PORT))
            if not (1 <= sync_port <= 65535):
                sync_port = SYNC_PORT
        except (ValueError, TypeError):
            return

        # P0-10: Answer unicast beacons with a unicast beacon reply (rate-limited and port-validated)
        if body.get("request_reply"):
            try:
                sender_beacon_port = int(body.get("beacon_port", self.beacon_port))
                if 1 <= sender_beacon_port <= 65535:
                    self._send_unicast_beacon_reply(ip, sender_beacon_port)
            except (ValueError, TypeError):
                pass

        peer = PeerInfo(
            username=body.get("username", "Unknown"),
            host=body["host"],
            ip=ip,
            sync_port=sync_port,
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
        else:
            if prev_peer.ip != ip:
                self.log_activity("NETWORK", f"Peer {peer.host} changed IP", f"{prev_peer.ip} → {ip}")
            if prev_peer.sync_revision != sync_rev or prev_peer.inv_frames != inv_frames:
                inv_tag = " [🛡️ INV-FRAMES]" if inv_frames else ""
                tracker_info = f" | Tracker: {tracker_cnt}" if tracker_cnt > 0 else ""
                self.log_activity("REVISION", f"Node {peer.host} updated{inv_tag}", f"Rev Score: {sync_rev} (was {prev_peer.sync_revision}) | Clients: {client_cnt}{tracker_info}")

        # ---- BOOTSTRAP AUTO-PULL ----
        # If this node is bootstrapping (empty DB) and we discover a peer with data,
        # immediately request a pull from them instead of waiting for user action.
        if self._is_bootstrapping and not self._bootstrap_pull_done:
            if client_cnt > 0 and ip not in self._bootstrap_pull_attempted_peers:
                self._bootstrap_pull_attempted_peers.add(ip)
                peer_port = sync_port
                self.log_activity(
                    "PULL",
                    f"Bootstrap: Requesting data from {peer.host} (we are empty, they have {client_cnt} clients)",
                    f"Peer Rev: {sync_rev}"
                )
                def _do_bootstrap_pull(_ip=ip, _port=peer_port):
                    time.sleep(0.5)  # brief settle so both TCP servers are ready
                    ok = self.request_pull_from(_ip, _port)
                    if ok:
                        self._bootstrap_pull_done = True
                threading.Thread(target=_do_bootstrap_pull, daemon=True).start()

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

            # P0-7: authenticate every message except fetch_snapshot (protected by the
            # on-screen join approval instead). This closes F9: request_database_pull /
            # push_database no longer act on an unauthenticated sender's say-so.
            auth_reject_reason = self._verify_header(header)
            if auth_reject_reason:
                # A completely absent mac (as opposed to one present but wrong) is what a
                # pre-P0-7 build sends, since it has never heard of signing headers - it is
                # the ordinary rollout case, not necessarily an attack. Surface that as a
                # hint (not the protocol `reason`, which callers/tests key off) so whoever
                # is watching the Sera Sync panel during a staggered upgrade knows to check
                # versions rather than suspect foul play. See docs/sera-sync-v3-blueprint.md
                # §6/§7: this release must reach every PC before push/pull is relied on again.
                looks_outdated = auth_reject_reason == "UNAUTHENTICATED" and not header.get("mac")
                hint = (
                    f"{sender_host} may still be on a build from before this release "
                    "and cannot authenticate - upgrade it to the current version."
                    if looks_outdated else None
                )
                detail = f"{auth_reject_reason} ({hint})" if hint else auth_reject_reason
                print(f"[Sync Guard] Rejected {action!r} from {sender_host}: {detail}")
                self.log_activity("GUARD", f"Rejected unauthenticated {action} from {sender_host}", detail)
                reject_payload = {"status": "rejected", "reason": auth_reject_reason}
                if hint:
                    reject_payload["hint"] = hint
                _send_framed(conn, json.dumps(reject_payload).encode("utf-8"))
                return

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

            # ---------------- TELEMETRY ACTIONS (Audit Logs & Tracker Dumps) ----------------
            # Telemetry is append-only and idempotent; it is always accepted and bypasses sovereign locks.
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
                
            if action == "push_tracker_dump":
                dumps = header.get("dumps", [])
                try:
                    if self.db and hasattr(self.db, "store_peer_tracker_dumps"):
                        self.db.store_peer_tracker_dumps(dumps)
                    else:
                        from database import SeraDatabase
                        db = SeraDatabase(self.db_path)
                        db.store_peer_tracker_dumps(dumps)
                    self.log_activity("DUMP", f"Received {len(dumps)} tracker dump(s) from {sender_host}", f"Local Rev: {local_sync_rev}")
                except Exception as ex:
                    print(f"[SYNC] Error storing peer tracker dumps from {sender_host}: {ex}")

                _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))
                self._safe_call(self.on_tracker_dump_received, sender_host, len(dumps))
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

            if action == "fetch_snapshot":
                sender_code = str(header.get("code", "")).strip()
                if not os.path.exists(self.db_path) or not os.path.exists(self.salt_path):
                    _send_framed(conn, json.dumps({"status": "rejected", "reason": "NO_DATABASE"}).encode("utf-8"))
                    return

                with self._join_lock:
                    if self._join_in_progress:
                        print(f"[Join Guard] Rejected join request from {sender_host}: join request already in progress")
                        _send_framed(conn, json.dumps({"status": "rejected", "reason": "BUSY"}).encode("utf-8"))
                        return
                    self._join_in_progress = True

                try:
                    if not self.on_join_approval_requested:
                        print(f"[Join Guard] No approval callback registered for join request from {sender_host}")
                        _send_framed(conn, json.dumps({"status": "rejected", "reason": "APPROVAL_UNAVAILABLE"}).encode("utf-8"))
                        return

                    # Wait for user approval with 120s timeout
                    done_event = threading.Event()
                    result_holder = [False]

                    def _ask_approval():
                        try:
                            result_holder[0] = bool(self.on_join_approval_requested(sender_host, sender_username, sender_code))
                        except Exception as ex:
                            print(f"[Join Approval Error] {ex}")
                            result_holder[0] = False
                        finally:
                            done_event.set()

                    threading.Thread(target=_ask_approval, daemon=True).start()
                    if not done_event.wait(timeout=120.0):
                        print(f"[Join Guard] Join request from {sender_host} timed out (120s)")
                        self.log_activity("JOIN", f"Join request from {sender_host} timed out (120s)", f"Code: {sender_code}")
                        _send_framed(conn, json.dumps({"status": "rejected", "reason": "TIMEOUT"}).encode("utf-8"))
                        return

                    if not result_holder[0]:
                        print(f"[Join Guard] Join request from {sender_host} rejected by user")
                        self.log_activity("JOIN", f"Join request from {sender_host} denied by user", f"Code: {sender_code}")
                        _send_framed(conn, json.dumps({"status": "rejected", "reason": "DENIED"}).encode("utf-8"))
                        return

                    # Approved: Create snapshot and stream snapshot + salt
                    temp_dir = None
                    snap_path = None
                    try:
                        if self.db is not None:
                            from database import make_snapshot
                            app_dir = Path(self.db_path).parent
                            out_base = app_dir / "incoming" / "out"
                            out_base.mkdir(parents=True, exist_ok=True)
                            prune_outgoing_snapshots(out_base, max_age_seconds=300.0)
                            temp_dir = tempfile.mkdtemp(dir=str(out_base), prefix="snap_")
                            snap_path = os.path.join(temp_dir, "master.db")
                            make_snapshot(self.db, snap_path)
                            file_to_send = snap_path
                        else:
                            file_to_send = self.db_path

                        with open(self.salt_path, "rb") as sf:
                            salt_bytes = sf.read()

                        db_size = os.path.getsize(file_to_send)
                        salt_size = len(salt_bytes)

                        # Send ready frame with payload sizes
                        ready_frame = {
                            "status": "ready",
                            "db_size": db_size,
                            "salt_size": salt_size,
                        }
                        _send_framed(conn, json.dumps(ready_frame).encode("utf-8"))

                        # Stream snapshot in 1 MB chunks
                        chunk_size = 1 << 20  # 1 MB
                        with open(file_to_send, "rb") as f:
                            while True:
                                chunk = f.read(chunk_size)
                                if not chunk:
                                    break
                                conn.sendall(chunk)

                        # Stream salt bytes
                        conn.sendall(salt_bytes)

                        # Receive joiner confirmation frame
                        try:
                            conn.settimeout(15.0)
                            _recv_framed(conn)
                        except Exception:
                            pass

                        self.log_activity("JOIN", f"Approved and sent database snapshot to {sender_host} ({sender_username})", f"Code: {sender_code}")
                    finally:
                        if temp_dir and os.path.exists(temp_dir):
                            try:
                                shutil.rmtree(temp_dir, ignore_errors=True)
                            except OSError:
                                pass
                    return
                finally:
                    with self._join_lock:
                        self._join_in_progress = False

            if action != "push_database":
                conn.close()
                return

            # ---------------- PROTOCOL RULE 1: Local inv_frames Mode ----------------
            # Inv-Frames mode strictly protects master.db from incoming database pushes
            if self.inv_frames:
                reject_reason = f"INV_FRAMES_ACTIVE: Local node ({self.host_name}) is in Inv-Frames mode and rejects incoming database push."
                print(f"[Inv-Frames] Rejected database push from {sender_host}: {reject_reason}")
                self.log_activity("INV_FRAMES", f"Rejected push from {sender_host}", f"Local Inv-Frames is ON | Sender Rev: {incoming_sync_rev}, Local Rev: {local_sync_rev}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_reason}).encode("utf-8"))
                return

            # ---------------- PROTOCOL RULE 2: Multiple inv_frames Nodes (LAN Freeze) ----------------
            if sync_state["status"] == "LAN_SYNC_FROZEN_MULTI_INV":
                inv_nodes_str = ", ".join(sync_state["active_inv_frames_nodes"])
                reject_reason = f"LAN_SYNC_FROZEN: Multiple nodes ({inv_nodes_str}) have Inv-Frames active. All database sync is paused to prevent corruption."
                print(f"[LAN Sync Frozen] Blocked database sync from {sender_host}: {reject_reason}")
                self.log_activity("GUARD", f"Blocked sync from {sender_host}", f"LAN Sync Frozen (Multiple Inv-Frames nodes: {inv_nodes_str}) | Sender Rev: {incoming_sync_rev}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_reason}).encode("utf-8"))
                return

            # ---------------- PROTOCOL RULE 3: Single inv_frames Authority Node ----------------
            if sync_state["status"] == "INV_FRAMES_FOLLOWER":
                authority = sync_state["authority_host"]
                if sender_host != authority and not sender_inv_frames:
                    reject_reason = f"INV_FRAMES_AUTHORITY_ACTIVE: Node {authority} is the active Inv-Frames authority. Database sync between normal nodes is locked."
                    print(f"[Inv-Frames Lock] Blocked non-authority push from {sender_host}: {reject_reason}")
                    self.log_activity("INV_FRAMES", f"Blocked push from {sender_host}", f"Waiting for Authority {authority} | Sender Rev: {incoming_sync_rev}")
                    _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_reason}).encode("utf-8"))
                    return

            db_size = int(header.get("db_size", 0))
            salt_size = int(header.get("salt_size", 0))
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
                    # Tell the sender what happened, and include PULL_REQUEST_FROM_YOU so they
                    # know to trigger a request_pull_from back to us — this is the safer flow
                    # for bootstrapping nodes because push_to from here can still race.
                    _send_framed(conn, json.dumps({
                        "status": "rejected",
                        "reason": reject_reason,
                        "pull_request_from_you": True,   # signal to sender: call request_pull_from(us)
                        "requester_ip": sender_ip,
                        "requester_sync_port": self.sync_port,
                    }).encode("utf-8"))
                    # Also trigger a direct reverse-push as a fallback (keeps existing behavior)
                    def reverse_sync():
                        time.sleep(1.0)
                        self.push_to(sender_ip, int(header.get("sync_port", SYNC_PORT)), live_update=True)
                    threading.Thread(target=reverse_sync, daemon=True).start()
                    return

            # Check if a staged database is already awaiting application restart
            app_dir = Path(self.db_path).parent
            incoming_dir = app_dir / "incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            pending_swap_file = incoming_dir / "pending_swap.json"
            if pending_swap_file.exists():
                reject_reason = "RESTART_PENDING: A previous database sync is already staged and awaiting restart."
                print(f"[Sync Guard] Rejected database push from {sender_host}: {reject_reason}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": "RESTART_PENDING"}).encode("utf-8"))
                return

            # Check payload integrity before locking or receiving
            if db_size <= 0 or salt_size not in (16, 32):
                reject_reason = f"INVALID_PAYLOAD: db_size ({db_size}) must be > 0 and salt_size ({salt_size}) must be 16 or 32"
                print(f"[Sync Guard] Rejected database push from {sender_host}: {reject_reason}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": "INVALID_PAYLOAD"}).encode("utf-8"))
                return

            # Acquire non-blocking staging lock to prevent concurrent incoming transfers racing
            if not self._staging_lock.acquire(blocking=False):
                reject_reason = "BUSY: Staging area is currently busy with another transfer"
                print(f"[Sync Guard] Rejected database push from {sender_host}: {reject_reason}")
                _send_framed(conn, json.dumps({"status": "rejected", "reason": "BUSY"}).encode("utf-8"))
                return

            try:
                # Re-check pending_swap.json inside lock to close the concurrency race window
                if pending_swap_file.exists():
                    reject_reason = "RESTART_PENDING: A previous database sync is already staged and awaiting restart."
                    print(f"[Sync Guard] Rejected database push from {sender_host}: {reject_reason}")
                    _send_framed(conn, json.dumps({"status": "rejected", "reason": "RESTART_PENDING"}).encode("utf-8"))
                    return

                # Send ACK to proceed
                _send_framed(conn, json.dumps({"status": "ready"}).encode("utf-8"))

                db_name = Path(self.db_path).name or "master.db"
                salt_name = Path(self.salt_path).name or "sera.salt"
                stream_id = time.time_ns()
                db_part = incoming_dir / f"{db_name}.{stream_id}.part"
                salt_part = incoming_dir / f"{salt_name}.{stream_id}.part"
                staged_db = incoming_dir / db_name
                staged_salt = incoming_dir / salt_name

                def _cleanup_staging():
                    for p in (db_part, salt_part, staged_db, staged_salt):
                        try:
                            if p.exists():
                                p.unlink()
                        except OSError:
                            pass

                try:
                    # 1. Stream incoming DB bytes directly to incoming/master.db.part (1 MB chunks)
                    chunk_size = 1 << 20  # 1 MB
                    bytes_left = db_size
                    with open(db_part, "wb") as f:
                        while bytes_left > 0:
                            to_read = min(chunk_size, bytes_left)
                            chunk = _recv_exact(conn, to_read)
                            if not chunk:
                                raise OSError(f"Connection closed while receiving database payload ({db_size - bytes_left}/{db_size} bytes)")
                            f.write(chunk)
                            bytes_left -= len(chunk)

                    # Stream incoming salt bytes to incoming/sera.salt.part
                    bytes_left = salt_size
                    with open(salt_part, "wb") as f:
                        while bytes_left > 0:
                            to_read = min(chunk_size, bytes_left)
                            chunk = _recv_exact(conn, to_read)
                            if not chunk:
                                raise OSError(f"Connection closed while receiving salt payload ({salt_size - bytes_left}/{salt_size} bytes)")
                            f.write(chunk)
                            bytes_left -= len(chunk)

                    # Drain raw payload db bytes if present from legacy sender to preserve TCP framing
                    if raw_db_size > 0:
                        _recv_exact(conn, raw_db_size)

                    # Atomic rename to final staging names
                    os.replace(db_part, staged_db)
                    os.replace(salt_part, staged_salt)

                    # 2. Verify before accepting: derive the key with the local sera.key password and the incoming salt,
                    # then open the staged DB and run SELECT count(*) FROM sqlite_master and PRAGMA cipher_integrity_check.
                    incoming_salt_bytes = staged_salt.read_bytes()
                    local_pwd = self._get_local_password()
                    verified = False
                    if local_pwd and staged_db.stat().st_size > 0 and len(incoming_salt_bytes) in (16, 32):
                        try:
                            import security
                            hex_key = security.derive_key_hex(local_pwd, incoming_salt_bytes)
                            import sqlcipher3.dbapi2 as sqlite3
                            verify_conn = sqlite3.connect(str(staged_db))
                            try:
                                verify_conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
                                table_count_row = verify_conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
                                table_count = table_count_row[0] if table_count_row else 0
                                integrity_rows = verify_conn.execute("PRAGMA cipher_integrity_check;").fetchall()
                                if table_count > 0 and not integrity_rows:
                                    verified = True
                                else:
                                    print(f"[Sync Guard] Staged DB verification failed: table_count={table_count}, cipher_integrity_check={integrity_rows}")
                            finally:
                                verify_conn.close()
                        except Exception as ex:
                            print(f"[Sync Guard] Verification failed with local password: {ex}")

                    if not verified:
                        _cleanup_staging()
                        reject_msg = "PASSWORD_MISMATCH"
                        self.log_activity("GUARD", f"Rejected DB push from {sender_host}", "Password mismatch or verification failed: local saved password cannot decrypt incoming database or database is empty/invalid")
                        self._safe_call(self.on_error, f"Incoming sync from {sender_host} rejected: verification failed (password mismatch or invalid database)")
                        _send_framed(conn, json.dumps({"status": "rejected", "reason": reject_msg}).encode("utf-8"))
                        return

                    # 3. On success, write incoming/pending_swap.json
                    swap_data = {
                        "db": db_name,
                        "salt": salt_name,
                        "from": sender_host,
                        "at": _utc_now_iso(),
                    }
                    pending_tmp = incoming_dir / "pending_swap.json.tmp"
                    with open(pending_tmp, "w", encoding="utf-8") as f:
                        json.dump(swap_data, f, indent=2)
                    os.replace(pending_tmp, pending_swap_file)

                    # Send success confirmation
                    _send_framed(conn, json.dumps({"status": "ok"}).encode("utf-8"))
                    sync_type_label = "Live Sync" if is_live_update else "Initial Full Sync"
                    auth_tag = " (Inv-Frames Master)" if (sync_state["status"] == "INV_FRAMES_FOLLOWER" and sender_host == sync_state["authority_host"]) else ""
                    self.log_activity(
                        "SYNC IN",
                        f"Accepted {sync_type_label} from {sender_username} ({sender_host}){auth_tag} (staged)",
                        f"New Rev: {incoming_sync_rev} | Previous Rev: {local_sync_rev} | Clients: {incoming_client_count} | Pending restart swap",
                    )

                    # Both live_update and regular pushes are staged and trigger restart
                    self._safe_call(self.on_sync_received)

                except Exception:
                    _cleanup_staging()
                    raise
            finally:
                self._staging_lock.release()

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
        # ---- BOOTSTRAP QUARANTINE ----
        # Never push from a machine that has zero clients (new/empty install).
        # This is an absolute hard block — it is unconditional and cannot be
        # overridden by force_override. An empty database must never propagate.
        if self._is_bootstrapping and not force_override:
            local_metrics = self._get_local_metrics()
            if local_metrics.get("client_count", 0) == 0:
                msg = "Bootstrap quarantine: This node has 0 clients and cannot push its database. Waiting for pull from LAN."
                self.log_activity("GUARD", "Outbound push blocked (bootstrap quarantine)", msg)
                return msg
            else:
                # DB has been filled since startup (e.g. by a successful pull) — lift quarantine
                self._is_bootstrapping = False

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

        with open(self.salt_path, "rb") as f:
            salt_bytes = f.read()

        local_mtime = os.path.getmtime(self.db_path) if os.path.exists(self.db_path) else 0.0
        metrics = self._get_local_metrics()
        local_sync_rev = metrics.get("sync_revision", 0)
        local_client_cnt = metrics.get("client_count", 0)

        temp_dir = None
        snap_path = None
        try:
            if self.db is not None:
                from database import make_snapshot
                app_dir = Path(self.db_path).parent
                out_base = app_dir / "incoming" / "out"
                out_base.mkdir(parents=True, exist_ok=True)
                prune_outgoing_snapshots(out_base, max_age_seconds=300.0)
                temp_dir = tempfile.mkdtemp(dir=str(out_base), prefix="snap_")
                snap_path = os.path.join(temp_dir, "master.db")
                make_snapshot(self.db, snap_path)
                file_to_send = snap_path
            else:
                file_to_send = self.db_path

            db_size = os.path.getsize(file_to_send)
            salt_size = len(salt_bytes)

            # Connect to peer
            with socket.create_connection((peer_ip, peer_port), timeout=SOCK_TIMEOUT_SEC) as conn:
                # Send header (raw_db_size is always 0 because tracker dumps sync incrementally)
                header = {
                    "action": "push_database",
                    "username": self.username,
                    "host": self.host_name,
                    "sync_port": self.sync_port,
                    "db_size": db_size,
                    "salt_size": salt_size,
                    "raw_db_size": 0,
                    "live_update": live_update,
                    "force_override": force_override,
                    "client_count": local_client_cnt,
                    "sync_revision": local_sync_rev,
                    "latest_timestamp": metrics.get("latest_timestamp", ""),
                    "db_mtime": local_mtime,
                    "inv_frames": self.inv_frames,
                }
                _send_framed(conn, json.dumps(self._sign_header(header)).encode("utf-8"))

                # Wait for ACK
                ack_raw = _recv_framed(conn)
                ack = json.loads(ack_raw.decode("utf-8"))
                if ack.get("status") != "ready":
                    reason = ack.get("reason", "Peer rejected sync request")
                    detail = f"{reason} ({ack['hint']})" if ack.get("hint") else reason
                    self.log_activity("PUSH", f"Sync rejected by {peer_ip}:{peer_port}", detail)
                    # If peer told us to pull from them (e.g. we are a bootstrapping empty node),
                    # honor that immediately — they have more data than us.
                    if ack.get("pull_request_from_you"):
                        pull_port = int(ack.get("requester_sync_port", peer_port))
                        self.log_activity(
                            "PULL",
                            f"Peer instructed us to pull from them (higher-revision DB)",
                            f"Connecting to {peer_ip}:{pull_port}"
                        )
                        def _do_instructed_pull(_ip=peer_ip, _port=pull_port):
                            time.sleep(0.3)
                            ok = self.request_pull_from(_ip, _port)
                            if ok:
                                self._bootstrap_pull_done = True
                                self._is_bootstrapping = False
                        threading.Thread(target=_do_instructed_pull, daemon=True).start()
                    return f"Sync skipped: {detail}"

                # Send database + salt (stream database in 1 MB chunks)
                chunk_size = 1 << 20  # 1 MB
                with open(file_to_send, "rb") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        conn.sendall(chunk)

                conn.sendall(salt_bytes)

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
        finally:
            if snap_path and os.path.exists(snap_path):
                try:
                    os.remove(snap_path)
                except OSError:
                    pass
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except OSError:
                    pass

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
                _send_framed(conn, json.dumps(self._sign_header(header)).encode("utf-8"))
                result_raw = _recv_framed(conn)
                if result_raw:
                    result = json.loads(result_raw.decode("utf-8"))
                    return result.get("status") == "ok"
        except Exception:
            pass
        return False

    def push_tracker_dumps_to_host(self, host_ip: str, dumps: list[dict], host_port: int = SYNC_PORT) -> bool:
        """
        Pushes local tracker dump records to the Host PC.
        """
        if not dumps:
            return True
        try:
            with socket.create_connection((host_ip, host_port), timeout=SOCK_TIMEOUT_SEC) as conn:
                header = {
                    "action": "push_tracker_dump",
                    "username": self.username,
                    "host": self.host_name,
                    "dumps": dumps,
                }
                _send_framed(conn, json.dumps(self._sign_header(header)).encode("utf-8"))
                result_raw = _recv_framed(conn)
                if result_raw:
                    result = json.loads(result_raw.decode("utf-8"))
                    return result.get("status") == "ok"
        except Exception:
            pass
        return False

    def broadcast_tracker_dumps(self, dumps: list[dict], peers: Optional[list[dict]] = None) -> int:
        """
        Broadcasts filing tracker dumps concurrently to all active LAN peers.
        Returns the count of peers that successfully accepted and acknowledged the dumps.
        """
        if not dumps:
            return 0
        if peers is None:
            peers = self.get_peers()
        if not peers:
            return 0

        success_count = [0]
        threads = []

        def _send(peer):
            ip = peer.get("ip")
            port = int(peer.get("sync_port", SYNC_PORT))
            host = peer.get("host", ip)
            if not ip:
                return
            try:
                with socket.create_connection((ip, port), timeout=SOCK_TIMEOUT_SEC) as conn:
                    header = {
                        "action": "push_tracker_dump",
                        "username": self.username,
                        "host": self.host_name,
                        "dumps": dumps,
                    }
                    _send_framed(conn, json.dumps(self._sign_header(header)).encode("utf-8"))
                    result_raw = _recv_framed(conn)
                    if result_raw:
                        result = json.loads(result_raw.decode("utf-8"))
                        if result.get("status") == "ok":
                            success_count[0] += 1
            except Exception:
                pass

        for p in peers:
            t = threading.Thread(target=_send, args=(p,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=2.0)

        if success_count[0] > 0:
            self.log_activity("DUMP", f"Broadcasted {len(dumps)} tracker dump(s)", f"Synced to {success_count[0]}/{len(peers)} peer(s)")
        return success_count[0]

    def broadcast_audit_logs(self, logs: list[dict], peers: Optional[list[dict]] = None) -> int:
        """
        Broadcasts audit logs to the Sovereign Master node (if present) or to all active peers.
        Returns the count of peers that successfully stored the logs.
        """
        if not logs:
            return 0
        if peers is None:
            peers = self.get_peers()
        if not peers:
            return 0

        # Prioritize sovereign authority nodes if one exists; otherwise broadcast to all peers
        sovereign_peers = [p for p in peers if p.get("inv_frames")]
        targets = sovereign_peers if sovereign_peers else peers

        success_count = [0]
        threads = []

        def _send(peer):
            ip = peer.get("ip")
            port = int(peer.get("sync_port", SYNC_PORT))
            host = peer.get("host", ip)
            if not ip:
                return
            try:
                with socket.create_connection((ip, port), timeout=SOCK_TIMEOUT_SEC) as conn:
                    header = {
                        "action": "push_audit_log",
                        "username": self.username,
                        "host": self.host_name,
                        "logs": logs,
                    }
                    _send_framed(conn, json.dumps(self._sign_header(header)).encode("utf-8"))
                    result_raw = _recv_framed(conn)
                    if result_raw:
                        result = json.loads(result_raw.decode("utf-8"))
                        if result.get("status") == "ok":
                            success_count[0] += 1
            except Exception:
                pass

        for p in targets:
            t = threading.Thread(target=_send, args=(p,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=2.0)

        if success_count[0] > 0:
            self.log_activity("SSAL", f"Pushed {len(logs)} audit log(s)", f"Synced to {success_count[0]}/{len(targets)} workstation(s)")
        return success_count[0]

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
                _send_framed(conn, json.dumps(self._sign_header(header)).encode("utf-8"))
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

    # ---------------- P0-7: header authentication ----------------

    def _auth_key(self) -> Optional[bytes]:
        """HMAC key derived from this PC's hex_key. None means this instance has no
        office/office-password key configured, so authentication is not enforced
        (legacy/test construction; real app instances always pass hex_key)."""
        if not self.hex_key:
            return None
        try:
            key_bytes = bytes.fromhex(self.hex_key)
        except (ValueError, TypeError):
            return None
        return hmac.new(key_bytes, SERA_SYNC_AUTH_INFO, hashlib.sha256).digest()

    def _sign_header(self, header: dict) -> dict:
        """Returns a copy of header with `ts` (and `mac`, if we have a key) added."""
        signed = dict(header)
        signed["ts"] = int(time.time())
        auth_key = self._auth_key()
        if auth_key is not None:
            signed["mac"] = hmac.new(auth_key, canonical_json(signed), hashlib.sha256).hexdigest()
        return signed

    def _verify_header(self, header: dict) -> Optional[str]:
        """Returns None if `header` is authenticated (or auth isn't enforced here),
        else a short rejection reason."""
        if header.get("action") in AUTH_EXEMPT_ACTIONS:
            return None
        auth_key = self._auth_key()
        if auth_key is None:
            return None
        mac = header.get("mac")
        ts = header.get("ts")
        if not mac or ts is None:
            return "UNAUTHENTICATED"
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            return "UNAUTHENTICATED"
        if abs(time.time() - ts) > AUTH_TS_TOLERANCE_SEC:
            return "STALE_TIMESTAMP"
        without_mac = {k: v for k, v in header.items() if k != "mac"}
        expected = hmac.new(auth_key, canonical_json(without_mac), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(mac)):
            return "UNAUTHENTICATED"
        return None


# ---------------- length-prefixed framing over TCP ----------------

def _send_framed(conn: socket.socket, data: bytes):
    conn.sendall(struct.pack("!I", len(data)) + data)


def _recv_framed(conn: socket.socket) -> bytes:
    header = _recv_exact(conn, 4)
    (length,) = struct.unpack("!I", header)
    return _recv_exact(conn, length)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(min(n - len(buf), 1 << 20))  # 1 MB chunks
        if not chunk:
            raise OSError("connection closed while reading frame")
        buf.extend(chunk)
    return bytes(buf)


# ---------------- First-Run (P0-6): Join Office / New Office Helpers ----------------

def discover_lan_peers(
    timeout_seconds: float = 10.0,
    stop_event: Optional[threading.Event] = None,
    on_peer_found: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
    """
    Listens for UDP beacons on BEACON_PORT for up to timeout_seconds without starting full sync service.
    Returns list of discovered peer dicts: [{"host": ..., "username": ..., "ip": ..., "sync_port": ..., ...}].
    """
    peers: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", BEACON_PORT))
    except Exception as e:
        print(f"[discover_lan_peers] Could not bind to beacon port {BEACON_PORT}: {e}")
        sock.close()
        return []

    sock.settimeout(0.5)
    start_time = time.monotonic()
    try:
        while time.monotonic() - start_time < timeout_seconds:
            if stop_event and stop_event.is_set():
                break
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                body = json.loads(data.decode("utf-8"))
                if body.get("magic") != SERA_SYNC_MAGIC:
                    continue
                host = body.get("host")
                if not host:
                    continue
                peer_info = {
                    "host": host,
                    "username": body.get("username", "Unknown"),
                    "ip": addr[0],
                    "sync_port": int(body.get("sync_port", SYNC_PORT)),
                    "app_version": body.get("app_version", "Unknown"),
                    "client_count": int(body.get("client_count", 0)),
                }
                if host not in peers:
                    peers[host] = peer_info
                    if on_peer_found:
                        try:
                            on_peer_found(peer_info)
                        except Exception:
                            pass
            except Exception:
                continue
    finally:
        sock.close()

    return list(peers.values())


def join_office_fetch_snapshot(
    peer_ip: str,
    peer_port: int,
    app_dir: str | Path,
    host_name: str,
    username: str,
    code: str,
    on_progress: Optional[Callable[[str], None]] = None,
    timeout: float = 135.0,
) -> tuple[bool, str, Optional[Path], Optional[Path]]:
    """
    Connects to an existing office workstation to request and fetch a database snapshot + salt.
    Streams to incoming/ and leaves files staged until master password verification.
    Returns (success, reason_or_message, staged_db_path, staged_salt_path).
    """
    app_path = Path(app_dir)
    incoming_dir = app_path / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)

    stream_id = time.time_ns()
    db_part = incoming_dir / f"master.db.join.{stream_id}.part"
    salt_part = incoming_dir / f"sera.salt.join.{stream_id}.part"
    staged_db = incoming_dir / "master.db"
    staged_salt = incoming_dir / "sera.salt"

    def _clean_parts():
        for p in (db_part, salt_part):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    try:
        if on_progress:
            on_progress(f"Connecting to {peer_ip}:{peer_port}...")

        with socket.create_connection((peer_ip, peer_port), timeout=10.0) as sock:
            sock.settimeout(timeout)

            # Send fetch_snapshot request
            header = {
                "action": "fetch_snapshot",
                "host": host_name,
                "username": username,
                "code": str(code).strip(),
            }
            _send_framed(sock, json.dumps(header).encode("utf-8"))

            if on_progress:
                on_progress("Waiting for approval on remote workstation (up to 120s)...")

            # Receive ready / reject frame
            resp_raw = _recv_framed(sock)
            if not resp_raw:
                return False, "Connection closed by remote peer without response", None, None
            resp = json.loads(resp_raw.decode("utf-8"))

            if resp.get("status") != "ready":
                reason = resp.get("reason", "Request rejected by remote workstation")
                return False, reason, None, None

            db_size = int(resp.get("db_size", 0))
            salt_size = int(resp.get("salt_size", 0))

            if db_size <= 0 or salt_size not in (16, 32):
                return False, f"Invalid payload sizes from server: db_size={db_size}, salt_size={salt_size}", None, None

            if on_progress:
                on_progress("Downloading office database...")

            # 1. Stream incoming DB snapshot (1 MB chunks)
            chunk_size = 1 << 20
            bytes_left = db_size
            with open(db_part, "wb") as f:
                while bytes_left > 0:
                    to_read = min(chunk_size, bytes_left)
                    chunk = _recv_exact(sock, to_read)
                    if not chunk:
                        raise OSError(f"Connection lost during database transfer ({db_size - bytes_left}/{db_size} bytes)")
                    f.write(chunk)
                    bytes_left -= len(chunk)

            # 2. Stream incoming salt
            bytes_left = salt_size
            with open(salt_part, "wb") as f:
                while bytes_left > 0:
                    to_read = min(chunk_size, bytes_left)
                    chunk = _recv_exact(sock, to_read)
                    if not chunk:
                        raise OSError(f"Connection lost during salt transfer ({salt_size - bytes_left}/{salt_size} bytes)")
                    f.write(chunk)
                    bytes_left -= len(chunk)

            # Atomic rename to staged files
            os.replace(db_part, staged_db)
            os.replace(salt_part, staged_salt)

            # Send acknowledgement back
            try:
                _send_framed(sock, json.dumps({"status": "ok"}).encode("utf-8"))
            except Exception:
                pass

            if on_progress:
                on_progress("Database downloaded successfully. Ready for password verification.")

            return True, "Snapshot fetched successfully", staged_db, staged_salt

    except Exception as e:
        _clean_parts()
        return False, f"Network transfer failed: {e}", None, None


def complete_join_office(
    app_dir: str | Path,
    staged_db: Path,
    staged_salt: Path,
    password: str,
) -> tuple[bool, str]:
    """
    Verifies master password against staged DB and salt (PRAGMA cipher_integrity_check + table count).
    If valid, installs files directly to app_dir, writes sera.key, and cleans up staging.
    If invalid, deletes staging files and leaves app_dir untouched.
    """
    app_path = Path(app_dir)
    staged_db = Path(staged_db)
    staged_salt = Path(staged_salt)

    def _cleanup_staged():
        for p in (staged_db, staged_salt):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    if not staged_db.exists() or not staged_salt.exists():
        _cleanup_staged()
        return False, "Staged database or salt file not found"

    salt_bytes = staged_salt.read_bytes()
    if len(salt_bytes) not in (16, 32):
        _cleanup_staged()
        return False, f"Invalid salt size ({len(salt_bytes)}) in staged files"

    # Verify password against staged DB
    try:
        import security
        hex_key = security.derive_key_hex(password, salt_bytes)
        import sqlcipher3.dbapi2 as sqlite3
        conn = sqlite3.connect(str(staged_db))
        try:
            conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
            table_count_row = conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
            table_count = table_count_row[0] if table_count_row else 0
            integrity_rows = conn.execute("PRAGMA cipher_integrity_check;").fetchall()
            if table_count <= 0 or integrity_rows:
                _cleanup_staged()
                return False, "PASSWORD_MISMATCH: Master password cannot decrypt the office database"
        finally:
            conn.close()
    except Exception as ex:
        _cleanup_staged()
        return False, f"PASSWORD_MISMATCH: Verification failed: {ex}"

    # Verified! Install files into app_dir
    # ── Blocking #1: backup any existing files before overwriting ──────────────
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y%m%d%H%M%S")

    final_db = app_path / "master.db"
    final_salt = app_path / security.SALT_FILE
    final_key = app_path / "sera.key"

    def _backup(path: Path) -> Optional[Path]:
        """Rename *path* to path.bak-<ts> and return the backup path, or None."""
        if not path.exists():
            return None
        bak = path.with_name(path.name + f".bak-{ts}")
        path.rename(bak)
        return bak

    db_bak = salt_bak = key_bak = None
    installed_db = installed_salt = installed_key = False
    try:
        db_bak   = _backup(final_db)
        salt_bak = _backup(final_salt)
        key_bak  = _backup(final_key)

        os.replace(staged_db, final_db)
        installed_db = True

        os.replace(staged_salt, final_salt)
        installed_salt = True

        final_key.write_text(password, encoding="utf-8")
        installed_key = True

        return True, "Office database installed successfully"

    except Exception as ex:
        # ── Blocking #2: rollback any partially installed files ────────────────
        def _restore(installed_flag: bool, current_path: Path, bak_path: Optional[Path]):
            if installed_flag:
                try:
                    current_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if bak_path and bak_path.exists():
                try:
                    bak_path.rename(current_path)
                except OSError:
                    pass

        _restore(installed_key,  final_key,  key_bak)
        _restore(installed_salt, final_salt, salt_bak)
        _restore(installed_db,   final_db,   db_bak)
        _cleanup_staged()
        return False, f"Failed to install verified database files: {ex}"


def create_new_office(
    app_dir: str | Path,
    password: str,
    password_confirm: str,
) -> tuple[bool, str]:
    """
    Creates a brand-new office database and salt in app_dir.
    Rules:
      - Master password min 8 characters
      - Passwords must match
      - Default password 'admin123' refused
      - Refuses if master.db already exists (§0 rule 3: no silent overwrite)
      - Backs up existing sera.salt / sera.key to .bak-<ts> before overwriting
      - Creates salt + DB, writes sera.key
    """
    pwd = password.strip()
    pwd_conf = password_confirm.strip()

    if len(pwd) < 8:
        return False, "Master password must be at least 8 characters long."
    if pwd != pwd_conf:
        return False, "Passwords do not match. Please retype."
    if pwd.lower() == "admin123":
        return False, "Default password 'admin123' is not permitted for a new office. Choose a strong master password."

    app_path = Path(app_dir)
    app_path.mkdir(parents=True, exist_ok=True)
    salt_path = app_path / "sera.salt"
    db_path = app_path / "master.db"
    key_path = app_path / "sera.key"

    # §0 rule 3: refuse if master.db already exists – do not silently overwrite a live office
    if db_path.exists():
        return False, (
            "An office database (master.db) already exists in this directory. "
            "Remove or back it up manually before creating a new office."
        )

    # §0 rule 3: backup existing salt / key before overwriting
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y%m%d%H%M%S")

    def _safe_backup(path: Path) -> None:
        if path.exists():
            bak = path.with_name(path.name + f".bak-{ts}")
            path.rename(bak)

    _safe_backup(salt_path)
    _safe_backup(key_path)

    try:
        import security
        security.generate_and_save_salt(str(salt_path))
        salt = security.load_salt(str(salt_path))
        hex_key = security.derive_key_hex(pwd, salt)

        from database import SeraDatabase
        db = SeraDatabase(str(db_path), hex_key, defer_startup_maintenance=True)
        del db

        key_path.write_text(pwd, encoding="utf-8")
        return True, "Office created successfully"
    except Exception as ex:
        return False, f"Failed to create new office database: {ex}"

