"""
sync_peer.py
------------
Built-in LAN discovery + join for Sera Sync.

Discovery:
  - UDP broadcast beacon on BEACON_PORT for peer discovery (every ~5s).
  - Each instance also listens on BEACON_PORT and keeps a live peer table.
  - Sera Sync v3 (sync_engine.py etc.) shares this beacon socket for its own
    discovery via `on_v3_beacon` instead of binding a second listener.

TCP server on `sync_port`:
  - Serves `fetch_snapshot` only: P0-6 first-run "Join office" and P2-6's
    snapshot service stream a consistent database snapshot + salt to a
    joining PC, protected by on-screen approval / a one-time join code.
  - The legacy v2 whole-database push/pull protocol (a TCP push of
    master.db + sera.salt, the `inv_frames` sovereign-master/LAN-freeze
    rules, Rev Score / `sync_revision`, bootstrap quarantine, tracker-dump
    and audit-log telemetry broadcasts) was removed in P4-1 once Sera Sync
    v3 went live on every PC (P3-9). See docs/sera-sync-v3-blueprint.md §5.
  - `apply_pending_swap()` (staged-swap installer) is kept only to finish
    applying a legacy swap staged by a pre-P4-1 build.

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
    salt_val = swap_info.get("salt")
    salt_rel = str(salt_val).strip() if salt_val is not None else None

    # Strict staging validation: file names cannot contain directory separators or path traversal
    db_name = Path(db_rel).name
    if not db_name or "/" in db_rel or "\\" in db_rel or ".." in db_rel:
        print(f"[apply_pending_swap] Security error: invalid db path in pending_swap.json ({db_rel})")
        try:
            if failed_path.exists():
                failed_path.unlink()
            os.replace(pending_json, failed_path)
        except OSError:
            pass
        return False

    has_salt = bool(salt_rel)
    salt_name = None
    if has_salt:
        salt_name = Path(salt_rel).name
        if not salt_name or "/" in salt_rel or "\\" in salt_rel or ".." in salt_rel:
            print(f"[apply_pending_swap] Security error: invalid salt path in pending_swap.json ({salt_rel})")
            try:
                if failed_path.exists():
                    failed_path.unlink()
                os.replace(pending_json, failed_path)
            except OSError:
                pass
            return False

    # Staged files MUST exist directly inside incoming_dir (NEVER fall back to app_path)
    staged_db = incoming_dir / db_name
    staged_salt = incoming_dir / salt_name if has_salt else None

    if not staged_db.is_file() or (has_salt and not staged_salt.is_file()):
        print(f"[apply_pending_swap] Staged files missing (db: {staged_db.is_file()}, salt: {staged_salt.is_file() if has_salt else 'skipped'})")
        try:
            if failed_path.exists():
                failed_path.unlink()
            os.replace(pending_json, failed_path)
        except OSError:
            pass
        return False

    # Live target files
    live_db = app_path / db_name
    live_salt = app_path / salt_name if has_salt else None
    live_wal = app_path / f"{db_name}-wal"
    live_shm = app_path / f"{db_name}-shm"

    had_live_db = live_db.exists()
    had_live_salt = live_salt.exists() if has_salt else False
    had_live_wal = live_wal.exists()
    had_live_shm = live_shm.exists()

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_db = app_path / f"{db_name}.pre-sync-{ts}.db"
    backup_salt = app_path / f"{salt_name}.pre-sync-{ts}" if has_salt else None
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
        if has_salt and had_live_salt:
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

        if has_salt and staged_salt:
            _retry_file_op(lambda: os.replace(staged_salt, live_salt))
            swapped_salt = True
        else:
            # §0 rule 3: never delete a key or salt file. If any staged salt remains in incoming/,
            # rename it to a timestamped stale backup rather than unlinking.
            for stray in incoming_dir.glob("sera.salt*"):
                try:
                    if stray.is_file() and ".stale-" not in stray.name:
                        stale_name = incoming_dir / f"{stray.name}.stale-{ts}"
                        _retry_file_op(lambda: os.replace(stray, stale_name))
                except OSError:
                    pass

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

            if swapped_salt and has_salt:
                if had_live_salt and copied_salt_backup and backup_salt.exists():
                    _retry_file_op(lambda: shutil.copy2(backup_salt, live_salt))
                elif not had_live_salt and live_salt:
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
    __slots__ = ("username", "host", "ip", "sync_port", "app_version", "db_mtime", "last_seen", "client_count", "tracker_count", "timeline_count", "key_id")

    def __init__(
        self,
        username,
        host,
        ip,
        sync_port,
        app_version="Unknown",
        db_mtime="",
        last_seen=0.0,
        client_count=0,
        tracker_count=0,
        timeline_count=0,
        key_id=None,
    ):
        self.username = username
        self.host = host
        self.ip = ip
        self.sync_port = sync_port
        self.app_version = app_version
        self.db_mtime = db_mtime
        self.last_seen = last_seen
        self.client_count = int(client_count)
        self.tracker_count = int(tracker_count)
        self.timeline_count = int(timeline_count)
        self.key_id = str(key_id).strip() if key_id else None

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
            "client_count": self.client_count,
            "tracker_count": self.tracker_count,
            "timeline_count": self.timeline_count,
            "key_id": self.key_id,
        }


class SyncPeerService:
    """
    Owns the beacon thread, listener thread, TCP sync server, and the
    peer table. Instantiate once per app run and call start()/stop().

    The TCP server on `sync_port` now serves only `fetch_snapshot` (P0-6 join /
    P2-6 snapshot service). The legacy v2 whole-database push/pull protocol
    (push_database, request_database_pull, push_tracker_dump, push_audit_log,
    inv_frames, Rev Score / sync_revision) was removed in P4-1 once Sera Sync
    v3 went live on every PC (P3-9); see docs/sera-sync-v3-blueprint.md §5.
    """

    def __init__(
        self,
        db_path: str,
        salt_path: str,
        username: str,
        sync_port: int = SYNC_PORT,
        db: Optional[Any] = None,
        hex_key: Optional[str] = None,
        key_id: Optional[str] = None,
        key_path: Optional[str] = None,
        master_password: Optional[str] = None,
        on_peer_table_changed: Optional[Callable] = None,
        on_sync_received: Optional[Callable] = None,
        on_live_sync_received: Optional[Callable] = None,
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
        self.hex_key = hex_key
        self.key_id = key_id
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
        self.on_activity = on_activity
        self.on_error = on_error
        self.on_join_approval_requested = on_join_approval_requested
        self.on_v3_beacon: Optional[Callable[[bytes, str], None]] = None

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
            "latest_timestamp": metrics.get("latest_timestamp", ""),
        }
        if self.key_id:
            body["key_id"] = self.key_id
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
            if body.get("magic") == "sera-sync-v3":
                if getattr(self, "on_v3_beacon", None):
                    try:
                        self.on_v3_beacon(data, ip)
                    except Exception:
                        pass
                return
            if body.get("magic") != SERA_SYNC_MAGIC:
                return
            if body.get("host") == self.host_name:
                return  # ignore our own broadcast
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            return

        # P0-10: Safely parse and validate numeric fields
        try:
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

        raw_key_id = body.get("key_id")
        peer_key_id = str(raw_key_id).strip() if raw_key_id else None

        peer = PeerInfo(
            username=body.get("username", "Unknown"),
            host=body["host"],
            ip=ip,
            sync_port=sync_port,
            app_version=body.get("app_version", "Unknown"),
            db_mtime=body.get("db_mtime", ""),
            last_seen=time.time(),
            client_count=client_cnt,
            tracker_count=tracker_cnt,
            timeline_count=timeline_cnt,
            key_id=peer_key_id,
        )

        pk = peer.key()
        prev_peer = None
        with self._peers_lock:
            prev_peer = self._peers.get(pk)
            self._peers[pk] = peer

        # Log node discovery in the live activity stream
        if not prev_peer:
            tracker_info = f" | Tracker: {tracker_cnt}" if tracker_cnt > 0 else ""
            self.log_activity("BEACON", f"Discovered {peer.username} ({peer.host})", f"Clients: {client_cnt}{tracker_info}")
        elif prev_peer.ip != ip:
            self.log_activity("NETWORK", f"Peer {peer.host} changed IP", f"{prev_peer.ip} → {ip}")

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
        """Receives requests from network peers on the sync TCP server. Since P4-1 the only
        action served here is `fetch_snapshot` (P0-6 join / P2-6 snapshot service) -- the legacy
        v2 whole-database push/pull protocol was removed once Sera Sync v3 went live everywhere."""
        try:
            conn.settimeout(SOCK_TIMEOUT_SEC)

            # Read header: JSON with action and payload details
            header_raw = _recv_framed(conn)
            header = json.loads(header_raw.decode("utf-8"))
            action = header.get("action")
            sender_host = header.get("host", sender_ip)
            sender_username = header.get("username", "Unknown")

            # P1-5: Key-fingerprint gate: an office-mode PC only serves a joiner that has no
            # office key yet.
            incoming_key_id = header.get("key_id")
            if action == "fetch_snapshot":
                if self.key_id != incoming_key_id or self.key_id is not None:
                    detail = "different office key — rejoin needed"
                    print(f"[Sync Guard] Rejected {action!r} from {sender_host}: KEY_ID_MISMATCH ({detail})")
                    self.log_activity("GUARD", f"Rejected {action} from {sender_host}", f"KEY_ID_MISMATCH: {detail}")
                    reject_payload = {"status": "rejected", "reason": "KEY_ID_MISMATCH", "hint": detail}
                    _send_framed(conn, json.dumps(reject_payload).encode("utf-8"))
                    return

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

            # Any other action is unrecognized: the legacy v2 protocol (push_database,
            # request_database_pull, push_tracker_dump, push_audit_log) was removed in P4-1.
            conn.close()
            return

        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            self._safe_call(self.on_error, f"Incoming sync from {sender_ip} failed: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # push_to, push_audit_logs_to_host, push_tracker_dumps_to_host, broadcast_tracker_dumps,
    # broadcast_audit_logs, push_to_all and request_pull_from (the legacy v2 whole-database
    # push/pull protocol and its telemetry broadcasts) were removed in P4-1 once Sera Sync v3
    # went live on every PC (P3-9). See docs/sera-sync-v3-blueprint.md §5.


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
        if self.key_id is not None:
            signed["key_id"] = self.key_id
        elif "key_id" in signed and signed["key_id"] is None:
            del signed["key_id"]
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

