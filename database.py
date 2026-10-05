"""
database.py
------------
All access to master.db goes through this module. The database is
encrypted at rest with SQLCipher (AES-256). The encryption key is
derived at runtime in security.py and passed in here.

Version 2.0: Removes hardcoded GST/ITR columns in favor of an EAV
schema driven by an admin-configurable Master Column List (MCL).
"""

import sqlcipher3.dbapi2 as sqlite3
import os
import time
import threading
from contextlib import contextmanager

# Re-exported: other modules import these from `database`.
from sera_db.common import DB_FILENAME, SKELETON_NAME_REGEX, DatabaseError  # noqa: F401
from sera_db.schema import SchemaMixin
from sera_db.settings import SettingsMixin
from sera_db.mcl_services import MclServicesMixin
from sera_db.clients import ClientsMixin
from sera_db.audit_backup import AuditBackupMixin
from sera_db.srpf import SrpfMixin
from sera_db.tracker_dump import TrackerDumpMixin
from sera_db.maintenance import MaintenanceMixin
from sera_db.sdis import SdisMixin


# Rows changed through master.db in this process, per database file (every SeraDatabase instance
# on the same file shares it - sync_peer opens its own). Screens compare it to skip redrawing
# data that has not changed; it only ever goes up.
_WRITE_GENERATION: dict = {}
_WRITE_GENERATION_LOCK = threading.Lock()


class SeraDatabase(SchemaMixin, SettingsMixin, MclServicesMixin, ClientsMixin, AuditBackupMixin, SrpfMixin, TrackerDumpMixin, MaintenanceMixin, SdisMixin):
    def __init__(self, db_path: str, hex_key: str, raw_db_path: str = None, defer_startup_maintenance: bool = False, key_mode: str = None):
        self.db_path = db_path
        self.hex_key = hex_key
        if key_mode:
            self.key_mode = key_mode
        else:
            db_dir = os.path.dirname(os.path.abspath(db_path))
            try:
                import sera_keys
                self.key_mode = "office" if sera_keys.load_office(db_dir) is not None else "legacy"
            except Exception:
                self.key_mode = "office" if os.path.exists(os.path.join(db_dir, "keys", "office.json")) else "legacy"

        if raw_db_path:
            self.raw_db_path = raw_db_path
        else:
            db_dir = os.path.dirname(os.path.abspath(db_path))
            self.raw_db_path = os.path.join(db_dir, "rawPayload.db")
        self.app_dir = os.path.dirname(os.path.abspath(db_path))

        self._local = threading.local()
        self._last_reresolve_ts = 0.0  # Debounce timestamp for re_resolve_all_tracker_dumps
        self._last_resequence_ts = 0.0  # Debounce timestamp for resequence_client_serial_numbers
        self._resequence_pending = False  # Track debounced pending resequence for clients
        self.raw_db_was_reset = None
        self._master_db_failed = False
        self._sync_mode = "off"  # read from _sync_meta in _init_schema
        self._seal_timer = None
        try:
            import sync_identity
            self._device_id = sync_identity.load_device_id_cheap(self.app_dir)
        except Exception:
            self._device_id = None
        try:
            self._init_schema()
        except Exception:
            self._master_db_failed = True
            raise
        self._init_raw_schema()
        self._migrate_tracker_dump_to_raw_payload_db()
        if not defer_startup_maintenance:
            self.run_startup_maintenance()

    def is_admin_pc(self, conn=None) -> bool:
        """True if this PC is the office admin PC, or True in legacy mode (blueprint §5 P3-6)."""
        import sync_admin
        return sync_admin.is_admin_pc(self.app_dir, conn=conn, device_id=self._device_id)

    def get_token_letter(self, conn=None) -> Optional[str]:
        """Returns this PC's token letter (e.g. 'A', 'B') from its member record, or None in legacy mode."""
        import sync_admin
        return sync_admin.get_token_letter(self.app_dir, conn=conn, device_id=self._device_id)

    def get_sync_device_id(self) -> Optional[str]:
        return self._device_id

    def set_sync_device_id(self, device_id: str):
        self._device_id = device_id
        import sync_tables
        with self._connect() as conn:
            sync_tables.set_sync_device_id(conn, "master", device_id)
        with self._connect_raw() as conn:
            sync_tables.set_sync_device_id(conn, "raw", device_id)

    def _bump_sync_revision_if_configured(self):
        """Invalidates the cached get_sync_metrics() result after a write. Named for the
        Rev Score / sync_revision mechanism this used to also drive; P4-1 removed that
        (and the write hook that broadcast to legacy LAN peers) along with the rest of the
        legacy v2 protocol, but call sites throughout this module still rely on the cache
        invalidation, so the method (and its many call sites) stayed."""
        if hasattr(self, "_sync_metrics_cache"):
            try:
                del self._sync_metrics_cache
            except AttributeError:
                pass
        if hasattr(self, "_sync_metrics_cache_ts"):
            try:
                del self._sync_metrics_cache_ts
            except AttributeError:
                pass

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        depth = self._enter_connection()
        try:
            conn.execute(f"PRAGMA key = \"x'{self.hex_key}'\";")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA cache_size = -64000;")       # 64MB RAM page cache
            conn.execute("PRAGMA temp_store = MEMORY;")        # In-memory temporary tables & sorts
            conn.execute("PRAGMA mmap_size = 268435456;")      # 256MB memory-mapped fast reads
            yield conn
            conn.commit()
            if conn.total_changes:
                self._note_write()
                self._mark_seal_needed("master")
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise e
        except sqlite3.OperationalError as e:
            conn.rollback()
            raise e
        except sqlite3.DatabaseError as e:
            conn.rollback()
            raise DatabaseError(
                "Could not open the database. This almost always means "
                "the master password was typed incorrectly."
            ) from e
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            self._leave_connection()
        if depth == 0:
            self._seal_after_commit()

    def _generation_key(self) -> str:
        return os.path.normcase(os.path.abspath(self.db_path))

    def _note_write(self) -> None:
        key = self._generation_key()
        with _WRITE_GENERATION_LOCK:
            _WRITE_GENERATION[key] = _WRITE_GENERATION.get(key, 0) + 1

    def data_generation(self) -> int:
        """Goes up after every committed change to master.db made in this process. Equal
        numbers = nothing written in between (another program writing the file is not seen -
        callers cap how long they trust it)."""
        return _WRITE_GENERATION.get(self._generation_key(), 0)

    def _raw_generation_key(self) -> str:
        return os.path.normcase(os.path.abspath(self.raw_db_path))

    def _note_raw_write(self) -> None:
        key = self._raw_generation_key()
        with _WRITE_GENERATION_LOCK:
            _WRITE_GENERATION[key] = _WRITE_GENERATION.get(key, 0) + 1

    def raw_generation(self) -> int:
        """Goes up after every committed change to rawPayload.db made in this process. Equal
        numbers = nothing written in between (another program writing the file is not seen -
        callers cap how long they trust it)."""
        return _WRITE_GENERATION.get(self._raw_generation_key(), 0)

    @contextmanager
    def _connect_raw(self):
        """Dedicated connection for rawPayload.db."""
        conn = sqlite3.connect(self.raw_db_path)
        depth = self._enter_connection()
        try:
            conn.execute(f"PRAGMA key = \"x'{self.hex_key}'\";")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA cache_size = -32000;")       # 32MB RAM page cache
            conn.execute("PRAGMA temp_store = MEMORY;")
            yield conn
            conn.commit()
            if conn.total_changes:
                self._note_raw_write()
                self._mark_seal_needed("raw")
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise e
        except sqlite3.OperationalError as e:
            conn.rollback()
            raise e
        except sqlite3.DatabaseError as e:
            conn.rollback()
            raise DatabaseError("Could not open rawPayload.db with provided key.") from e
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            self._leave_connection()
        if depth == 0:
            self._seal_after_commit()

    # ─── Sera Sync v3 change capture (P3-3) ───────────────────────────────────
    # The capture triggers fill _sync_pending; the sealer (sync_capture.seal) turns it into
    # _sync_changes. It runs when the outermost _connect/_connect_raw of a thread has committed
    # a change (an inner seal would wait on the outer connection's write lock), and every 5 s
    # from SealTimer for writes made by other processes. Only in sync mode shadow/live.

    def _enter_connection(self) -> int:
        depth = getattr(self._local, "conn_depth", 0)
        self._local.conn_depth = depth + 1
        return depth

    def _leave_connection(self) -> None:
        self._local.conn_depth = max(0, getattr(self._local, "conn_depth", 1) - 1)

    def _mark_seal_needed(self, which: str) -> None:
        if getattr(self, "_sync_mode", "off") in ("shadow", "live"):
            dirty = getattr(self._local, "seal_dirty", None)
            if dirty is None:
                dirty = self._local.seal_dirty = set()
            dirty.add(which)

    def _seal_after_commit(self) -> None:
        dirty = getattr(self._local, "seal_dirty", None)
        if not dirty:
            return
        which = [w for w in ("master", "raw") if w in dirty]
        dirty.clear()
        t0 = time.perf_counter()
        try:
            self.seal_pending(which, timeout=0.25)
        except Exception as e:
            # Never break the caller's committed write; the timer seal retries.
            print(f"[database] Sync seal after commit deferred: {e}")
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            cb = getattr(self, "_seal_timing_callback", None)
            if cb is not None:
                try:
                    cb(elapsed_ms)
                except Exception:
                    pass

    def set_seal_timing_callback(self, fn) -> None:
        """``fn(duration_ms)`` is called after an after-commit seal completes on the committing thread (P3-8a)."""
        self._seal_timing_callback = fn

    def get_sync_mode(self) -> str:
        return getattr(self, "_sync_mode", "off")

    def set_sync_mode(self, mode: str) -> None:
        """Writes _sync_meta.mode ('off' | 'shadow' | 'live') in both databases."""
        if mode not in ("off", "shadow", "live"):
            raise ValueError(f"unknown sync mode {mode!r}")
        if mode != "off":
            # Without a device id the sealer can't number changes, and pending rows would pile up.
            for opener in (self._connect, self._connect_raw):
                with opener() as conn:
                    row = conn.execute("SELECT value FROM _sync_meta WHERE key = 'device_id'").fetchone()
                if not (row and row[0]):
                    raise ValueError(f"sync mode {mode!r} needs this PC's device id (pair it first)")
        for opener in (self._connect, self._connect_raw):
            with opener() as conn:
                conn.execute(
                    "INSERT INTO _sync_meta(key, value) VALUES ('mode', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,))
        self._sync_mode = mode

    def _admin_signer(self):
        """The office admin private key if this PC holds it, else None (P3-3 admin_lww sealing)."""
        try:
            import sync_admin
            return sync_admin.load_admin_key(os.path.dirname(os.path.abspath(self.db_path)))
        except Exception:
            return None

    def seal_pending(self, which=("master", "raw"), timeout: float = 5.0) -> dict:
        """Seals pending captured changes of master.db and/or rawPayload.db (master first, so
        a tracker row's client is sealed before it). Returns {"master"|"raw": SealResult}."""
        if self.get_sync_mode() not in ("shadow", "live"):
            return {}
        import sync_capture
        results = {}
        seq_state = sync_capture.seq_state_path(self.db_path)
        if "master" in which:
            results["master"] = sync_capture.seal(self.db_path, self.hex_key, "master",
                                                  get_signer=self._admin_signer, timeout=timeout,
                                                  seq_state=seq_state)
        if "raw" in which:
            results["raw"] = sync_capture.seal(self.raw_db_path, self.hex_key, "raw",
                                               master_path=self.db_path,
                                               get_signer=self._admin_signer, timeout=timeout,
                                               seq_state=seq_state)
        listener = getattr(self, "_seal_listener", None)
        if listener is not None and any(r.changes for r in results.values()):
            try:
                listener(results)
            except Exception as e:
                print(f"[database] Sync seal listener failed: {e}")
        return results

    def set_seal_listener(self, fn) -> None:
        """``fn(results)`` is called after a seal that produced changes (P3-5: the sync engine
        pokes the other PCs). Runs on the sealing thread; must be quick. None removes it."""
        self._seal_listener = fn

    def _office_admin_pubkey(self):
        try:
            import sera_keys
            office = sera_keys.load_office(os.path.dirname(os.path.abspath(self.db_path)))
            return office.admin_pubkey if office else None
        except Exception:
            return None

    def apply_changes(self, which: str, changes, admin_pubkey: str = None, now_ms: int = None):
        """Applies remote changes to master.db ("master") or rawPayload.db ("raw") in one
        transaction (sync_apply.apply_batch, P3-4). After master.db it finishes any
        rawPayload.db re-pointing left by a client/service merge and retries rawPayload.db's
        parked changes (their client may just have arrived). Returns sync_apply.ApplyResult."""
        import sync_apply
        import sync_capture
        if which not in ("master", "raw"):
            raise ValueError(f"unknown database {which!r}")
        if admin_pubkey is None:
            admin_pubkey = self._office_admin_pubkey()
        kw = dict(admin_pubkey=admin_pubkey, get_signer=self._admin_signer,
                  seq_state=sync_capture.seq_state_path(self.db_path), now_ms=now_ms)
        if which == "master":
            result = sync_apply.apply_batch(self.db_path, self.hex_key, "master", changes, **kw)
            if result.applied or result.unparked or result.merges:
                self._note_write()
            sync_apply.run_raw_repoints(self.db_path, self.raw_db_path, self.hex_key)
            raw = sync_apply.retry_parked(self.raw_db_path, self.hex_key, "raw",
                                          master_path=self.db_path, **kw)
            result.tables |= raw.tables
            result.unparked += raw.unparked

            # Blueprint §5 P3-6: resequence on admin PC after an applied batch that inserted clients,
            # debounced to at most once per 10 minutes.
            inserted_clients = any(c.get("op") == "upsert" for c in changes if isinstance(c, dict) and c.get("tbl") == "clients") if isinstance(changes, list) else False
            if (inserted_clients or self._resequence_pending) and self._is_office_mode():
                import sync_admin
                if sync_admin.has_admin_key(self.app_dir) and self.is_admin_pc():
                    now_ts = time.time()
                    if now_ts - self._last_resequence_ts >= 600.0:
                        self._last_resequence_ts = now_ts
                        self._resequence_pending = False
                        try:
                            self.resequence_client_serial_numbers()
                        except Exception as e:
                            print(f"[-] Debounced serial resequence skipped: {e}")
                    elif inserted_clients:
                        self._resequence_pending = True

            return result
        sync_apply.run_raw_repoints(self.db_path, self.raw_db_path, self.hex_key)
        return sync_apply.apply_batch(self.raw_db_path, self.hex_key, "raw", changes,
                                      master_path=self.db_path, **kw)

    def start_seal_timer(self, interval: float = None) -> None:
        """Seals every 5 s (writes by DOM_Parser/SDC_Parser and other processes). Idempotent."""
        if self._seal_timer is not None:
            return
        import sync_capture
        self._seal_timer = sync_capture.SealTimer(
            self.seal_pending, interval or sync_capture.SEAL_TIMER_SECONDS)
        self._seal_timer.start()

    def stop_seal_timer(self) -> None:
        timer, self._seal_timer = self._seal_timer, None
        if timer is not None:
            timer.stop()

    def close(self):
        pass

    def _is_office_mode(self) -> bool:
        return getattr(self, "key_mode", "legacy") == "office"

    def get_sync_metrics(self) -> dict:
        """
        Returns database structural metrics used by the Sera Sync beacon and panel:
        - client_count: Total active non-deleted client records
        - archived_count: Total archived client records
        - log_count: Total SSAL audit log entries
        - tracker_count: Total tracker dump captures in rawPayload.db
        - timeline_count: Total SDC session timelines in rawPayload.db
        - latest_timestamp: ISO timestamp of most recent audit log / capture entry

        Uses a 15-second cache to prevent heavy SQLite lock contention on background thread.
        """
        import time
        now = time.time()
        if hasattr(self, "_sync_metrics_cache") and hasattr(self, "_sync_metrics_cache_ts"):
            if now - self._sync_metrics_cache_ts < 15:
                return self._sync_metrics_cache

        try:
            with self._connect() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM clients WHERE is_archived = 0")
                client_count = cur.fetchone()[0]

                cur = conn.execute("SELECT COUNT(*) FROM clients WHERE is_archived = 1")
                archived_count = cur.fetchone()[0]

                cur = conn.execute("SELECT COUNT(*), MAX(ts) FROM audit_log")
                row = cur.fetchone()
                log_count = row[0] if row and row[0] else 0
                latest_ts = row[1] if row and row[1] else ""

            tracker_count = 0
            timeline_count = 0
            latest_dump_ts = ""
            try:
                with self._connect_raw() as r_conn:
                    from core.dataset_key import NOT_CARRIER_SQL     # SDIS carrier rows are not captures
                    cur = r_conn.execute("SELECT COUNT(*), MAX(created_at) FROM tracker_dump WHERE " + NOT_CARRIER_SQL)
                    r_row = cur.fetchone()
                    tracker_count = r_row[0] if r_row and r_row[0] else 0
                    latest_dump_ts = r_row[1] if r_row and r_row[1] else ""

                    cur = r_conn.execute("SELECT COUNT(*), MAX(last_updated) FROM sdc_session_timelines")
                    t_row = cur.fetchone()
                    timeline_count = t_row[0] if t_row and t_row[0] else 0
            except Exception:
                pass

            if latest_dump_ts and latest_dump_ts > latest_ts:
                latest_ts = latest_dump_ts

            res = {
                "client_count": client_count,
                "archived_count": archived_count,
                "log_count": log_count,
                "tracker_count": tracker_count,
                "timeline_count": timeline_count,
                "latest_timestamp": latest_ts,
            }
            self._sync_metrics_cache = res
            self._sync_metrics_cache_ts = now
            return res
        except Exception as e:
            print(f"[database] get_sync_metrics failed: {e}")
            return {
                "client_count": 0,
                "archived_count": 0,
                "log_count": 0,
                "tracker_count": 0,
                "timeline_count": 0,
                "latest_timestamp": "",
            }

    def make_snapshot(self, dest_path: str) -> str:
        """Creates a consistent snapshot of this database at dest_path using sqlcipher_export."""
        return make_snapshot(self, dest_path)

    # ---------------- Sync Conflict Detection ----------------

    def get_sync_conflicts(self) -> list[str]:
        """Scans the database directory for unresolved sync conflict files:
        both legacy Syncthing markers (master.db.sync-conflict-*) for any
        machine mid-migration, and the built-in LAN sync's own markers
        (master.db.conflict-*) written by sync_peer.py when two machines
        genuinely diverge. Does NOT flag master.db.pre-sync-* or
        master.db.pre-restore-* files, since those are routine safety
        copies made on every clean sync/restore, not conflict evidence."""
        db_dir = os.path.dirname(self.db_path)
        if not os.path.exists(db_dir):
            return []
        conflicts = []
        for filename in os.listdir(db_dir):
            if "sync-conflict" in filename or ".conflict-" in filename:
                conflicts.append(os.path.join(db_dir, filename))
        return sorted(conflicts)





def make_snapshot(db, dest_path: str) -> str:
    """
    Creates a consistent snapshot of the database at dest_path using sqlcipher_export('snap').
    Copies PRAGMA user_version to the snapshot.
    Escapes dest_path (single quotes doubled).
    """
    if hasattr(db, "db_path") and hasattr(db, "hex_key"):
        db_path = db.db_path
        hex_key = db.hex_key
    elif isinstance(db, (list, tuple)) and len(db) >= 2:
        db_path, hex_key = db[0], db[1]
    else:
        raise ValueError("db must be a SeraDatabase instance or (db_path, hex_key) tuple")

    dest_path = os.path.abspath(str(dest_path))
    dest_dir = os.path.dirname(dest_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    if os.path.exists(dest_path):
        raise FileExistsError(f"Snapshot destination already exists: {dest_path}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        uv_res = conn.execute("PRAGMA user_version;").fetchone()
        user_version = int(uv_res[0]) if uv_res and uv_res[0] is not None else 0

        escaped_dest = dest_path.replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{escaped_dest}' AS snap KEY \"x'{hex_key}'\";")
        try:
            conn.execute("SELECT sqlcipher_export('snap');")
            conn.execute(f"PRAGMA snap.user_version = {user_version};")
        finally:
            try:
                conn.execute("DETACH DATABASE snap;")
            except Exception:
                pass
    finally:
        conn.close()

    return dest_path


