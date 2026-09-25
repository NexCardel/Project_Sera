"""tests/sync_harness.py
---------------------
Multi-node in-process test harness for Sera Sync v3 (blueprint §5 WP P3-0).

Starts N in-process nodes on localhost, each with its own temp APP_DIR, ports,
identity, and office key (paired through the real P2-4 code).

Harness controls:
- node.write(fn)
- partition(a, b)
- heal()
- run_until_quiet(timeout)
- digest(node) (from P3-7)

No PySide6 imports here (§0 rule 7). Lazy imports where appropriate (§0 rule 6).
All databases use temporary directories and never touch real client data (§0 rule 2).

Deviation from spec (documented): joiners receive a direct filesystem copy of the
admin's checkpointed databases rather than going through the P2-6 snapshot service.
This is acceptable for a test harness (it avoids a live mTLS download loop between
localhost processes) but it also copies any _local_addresses rows that exist on the
admin at the time of the copy.  After all nodes pair, every node's DB is brought up
to date with the full member roster by copying _sync_members rows directly from the
admin without individual signature re-verification via store_record, so that mutual-TLS
handshakes succeed in all directions.
"""

from __future__ import annotations

import hashlib
import threading
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import sera_keys
import sync_admin
import sync_discovery
import sync_identity
import sync_schema
from sync_pairing import PairingWindow, join_office
from sync_transport import MemberSet, Session, SyncServer, SyncTransport, TransportError

_log = logging.getLogger("sera.sync.harness")
DEFAULT_MASTER_PASSWORD = "OfficeMaster#2026"


def create_connection(path: Path | str, hex_key: str):
    """Creates a new SQLCipher connection to the specified database."""
    import sqlcipher3.dbapi2 as sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


@contextmanager
def open_db_conn(path: Path | str, hex_key: str):
    """Contextmanager providing an auto-committing SQLCipher connection."""
    conn = create_connection(path, hex_key)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Digest encoding helpers
# ---------------------------------------------------------------------------

def _encode_value(val: Any) -> str:
    """Encode a single cell value to a string that uniquely represents it.

    Rules:
    - NULL  → ``<NULL>``
    - bytes → lowercase hex
    - float → Python repr (no precision loss, no ambiguity)
    - str   → backslash-escape ``\\``, ``|``, ``,``, ``=``, and ``:`` so they cannot
              collide with the delimiter characters used in row serialisation.
    - other → str(), then same escaping as str.
    """
    if val is None:
        return "<NULL>"
    if isinstance(val, (bytes, bytearray, memoryview)):
        return bytes(val).hex()
    if isinstance(val, float):
        return repr(val)
    text = str(val)
    # Escape characters used as delimiters in row serialisation.
    text = (
        text
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(":", "\\:")
    )
    return text


def _translate_fk(col_name: str, val: Any, fk_gid_maps: dict[str, dict[int, str]]) -> str | None:
    """Translates an FK value to 'gid:<gid>' if a mapping is available.

    Handles integer IDs and stringified integer IDs (such as cell_formatting.column_key
    referencing mcl_columns.id). Non-numeric string literals like 'services' pass through.
    """
    if col_name not in fk_gid_maps:
        return None
    mapping = fk_gid_maps[col_name]
    if isinstance(val, int):
        if val in mapping:
            return f"gid:{mapping[val]}"
        # A local id whose row no longer exists (e.g. audit_log.client_id after the client was
        # deleted) is still a local id (P3-7: local ids are excluded). The sealer sends it as
        # NULL (P3-3), so that's what it is on every other PC. (P3-4)
        return "<NULL>"
    elif isinstance(val, str) and val.isdigit():
        int_val = int(val)
        if int_val in mapping:
            return f"gid:{mapping[int_val]}"
        return "<NULL>"
    return None


def digest(node_or_db: Any) -> str:
    """Computes a deterministic SHA-256 digest over replicated tables (blueprint §5 P3-0, P3-7).

    Uses ``sync_schema.replicated_tables_for()`` to determine which tables to hash,
    so the digest always reflects the canonical P3-1 registry (no hardcoded lists).

    Raises ``RuntimeError`` on any database read error so a node whose database is
    unreadable never looks falsely converged.
    """
    if isinstance(node_or_db, HarnessNode):
        with node_or_db.open_db() as m_conn:
            with node_or_db.open_raw_db() as r_conn:
                return _compute_digest_from_conns(m_conn, r_conn)

    from database import SeraDatabase
    if isinstance(node_or_db, SeraDatabase):
        with node_or_db._connect() as m_conn:
            with node_or_db._connect_raw() as r_conn:
                return _compute_digest_from_conns(m_conn, r_conn)

    # Bare connection
    return _compute_digest_from_conns(node_or_db, None)


def _compute_digest_from_conns(master_conn, raw_conn=None) -> str:
    """Internal: SHA-256 digest over replicated tables from the P3-1 registry.

    Raises ``RuntimeError`` if any database read fails (so a broken DB is never
    mistaken for an empty-but-converged one).
    """
    hasher = hashlib.sha256()

    # Build a map from (db_label, table_name) → TableSpec from the registry.
    master_specs = {s.name: s for s in sync_schema.replicated_tables_for(sync_schema.MASTER_DB)}
    raw_specs = {s.name: s for s in sync_schema.replicated_tables_for(sync_schema.RAW_DB)}

    conn_map = [(sync_schema.MASTER_DB, master_conn, master_specs)]
    if raw_conn is not None:
        conn_map.append((sync_schema.RAW_DB, raw_conn, raw_specs))

    for db_label, conn, specs in conn_map:
        if conn is None:
            continue

        # Verify the connection is readable — raises on failure.
        try:
            live_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except Exception as exc:
            raise RuntimeError(
                f"digest: cannot read sqlite_master in {db_label}: {exc}"
            ) from exc

        # Hash only tables that are both in the registry and present live.
        for tname in sorted(specs):
            if tname not in live_tables:
                continue

            spec = specs[tname]

            # Read column info — raises on failure.
            try:
                col_info = conn.execute(f"PRAGMA table_info({tname})").fetchall()
            except Exception as exc:
                raise RuntimeError(
                    f"digest: cannot read PRAGMA table_info({tname}) in {db_label}: {exc}"
                ) from exc

            if not col_info:
                continue

            col_names = [c[1] for c in col_info]
            col_set = set(col_names)
            col_idx = {c[1]: i for i, c in enumerate(col_info)}
            has_gid = "gid" in col_set

            # Columns to include: exclude local integer id when gid exists.
            cols_to_hash = sorted(c for c in col_names if not (c == "id" and has_gid))

            # Gather FK→gid maps for all FK columns declared in the spec.
            fk_gid_maps: dict[str, dict[int, str]] = {}
            for fk_col, ref_table in spec.fk.items():
                # Cross-DB FKs (tracker_dump references master.db tables) use master_conn.
                ref_conn = master_conn if db_label == sync_schema.RAW_DB else conn
                if ref_conn is None:
                    continue
                try:
                    ref_live = {
                        row[0]
                        for row in ref_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                except Exception as exc:
                    raise RuntimeError(
                        f"digest: cannot read sqlite_master in ref DB for {ref_table}: {exc}"
                    ) from exc

                if ref_table in ref_live:
                    try:
                        ri = ref_conn.execute(f"PRAGMA table_info({ref_table})").fetchall()
                        rcs = {c[1] for c in ri}
                        if "gid" in rcs and "id" in rcs:
                            fk_gid_maps[fk_col] = {
                                row[0]: str(row[1])
                                for row in ref_conn.execute(
                                    f"SELECT id, gid FROM {ref_table} WHERE gid IS NOT NULL"
                                ).fetchall()
                            }
                    except Exception as exc:
                        raise RuntimeError(
                            f"digest: cannot read {ref_table} for FK column {fk_col}: {exc}"
                        ) from exc

            # Read all rows — raises on failure.
            try:
                rows = conn.execute(f"SELECT * FROM {tname}").fetchall()
            except Exception as exc:
                raise RuntimeError(
                    f"digest: cannot read rows from {db_label}.{tname}: {exc}"
                ) from exc

            # Determine effective row key columns.
            # If spec specifies ("gid",) but P3-2 schema migration has not yet added
            # the gid column to this table, fall back to ("id",) for pre-P3-2 compatibility.
            if spec.row_key == ("gid",) and "gid" not in col_set:
                row_key_cols = ("id",) if "id" in col_set else (col_names[0],)
            else:
                row_key_cols = spec.row_key

            row_strings: list[str] = []
            for r in rows:
                # Build row-key string (uses gid where available, FK-translated).
                key_parts: list[str] = []
                for k in row_key_cols:
                    if k not in col_idx:
                        raise RuntimeError(
                            f"digest: row-key column {k!r} not found in {db_label}.{tname}"
                        )
                    val = r[col_idx[k]]
                    if val is None:
                        raise RuntimeError(
                            f"digest: row-key column {k!r} is NULL in {db_label}.{tname}"
                        )
                    fk_gid = _translate_fk(k, val, fk_gid_maps)
                    if fk_gid is not None:
                        key_parts.append(fk_gid)
                    else:
                        key_parts.append(_encode_value(val))
                row_key_str = ":".join(key_parts)

                # Build sorted col=value pairs (FK values translated to gids).
                pairs: list[str] = []
                for col in cols_to_hash:
                    if col not in col_idx:
                        continue
                    val = r[col_idx[col]]
                    fk_gid = _translate_fk(col, val, fk_gid_maps)
                    if fk_gid is not None:
                        val_str = fk_gid
                    else:
                        val_str = _encode_value(val)
                    pairs.append(f"{col}={val_str}")

                row_strings.append(f"{row_key_str}|" + ",".join(pairs))

            row_strings.sort()
            hasher.update(f"--- {db_label}.{tname} ---\n".encode("utf-8"))
            for rs in row_strings:
                hasher.update(f"{rs}\n".encode("utf-8"))

    return hasher.hexdigest()


class HarnessNode:
    """Represents a single in-process peer workstation node on localhost."""

    def __init__(
        self,
        index: int,
        name: str,
        app_dir: Path,
        dek: bytes,
        office: sera_keys.OfficeInfo,
        identity: sync_identity.DeviceIdentity,
        harness: "SyncHarness",
    ):
        self.index = index
        self.name = name
        self.app_dir = app_dir
        self.dek = dek
        self.hex_key = sera_keys.dek_hex(dek)
        self.office = office
        self.identity = identity
        self.device_id = identity.device_id
        self.cert_pem = identity.cert_pem.decode("ascii")
        self.chain = sync_identity.load_cert_chain_args(app_dir)
        self.harness = harness

        self.db_path = app_dir / "master.db"
        self.raw_db_path = app_dir / "rawPayload.db"

        self._db = None
        self.transport: SyncTransport | None = None
        self.server: SyncServer | None = None
        self.port: int = 0
        self._active_sessions: set[Session] = set()
        self._lock = threading.Lock()

    @property
    def db(self):
        if self._db is None:
            from database import SeraDatabase
            self._db = SeraDatabase(
                str(self.db_path),
                self.hex_key,
                raw_db_path=str(self.raw_db_path),
                defer_startup_maintenance=True,
                key_mode="office",
            )
        return self._db

    @contextmanager
    def open_db(self):
        """Contextmanager for a connection to this node's master.db via SeraDatabase."""
        with self.db._connect() as conn:
            yield conn

    @contextmanager
    def open_raw_db(self):
        """Contextmanager for a connection to this node's rawPayload.db via SeraDatabase."""
        with self.db._connect_raw() as conn:
            yield conn

    def write(self, fn: Callable[..., Any]) -> Any:
        """Executes a write function on this node.

        ``fn`` must accept exactly one argument: either ``(conn)`` for a direct
        database connection or ``(node)`` for the HarnessNode itself.  The
        signature is determined by the name of the first parameter.

        Writes pass through ``self.open_db()`` (which uses ``SeraDatabase._connect``),
        ensuring that commit hooks (such as HLC sealing and change capture) are
        properly triggered upon commit.
        """
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())

        if params and params[0] in ("conn", "connection", "m_conn", "db_conn"):
            with self.open_db() as conn:
                res = fn(conn)
        else:
            res = fn(self)

        return res

    def digest(self) -> str:
        """Returns the database digest of this node."""
        return digest(self)

    def connect(self, peer: "HarnessNode | str", **kw) -> Session:
        """Opens a verified mutual-TLS connection to peer, checking harness partitions."""
        peer_node = self.harness.get_node(peer)
        if self.harness.is_partitioned(self.device_id, peer_node.device_id):
            raise TransportError(f"Network partition between {self.name} and {peer_node.name}")

        if self.transport is None:
            raise TransportError("Node transport not initialized")

        session = self.transport.connect("127.0.0.1", peer_node.port, peer_node.device_id, **kw)
        # Track session and remove it automatically when it closes.
        with self._lock:
            self._active_sessions.add(session)
        self._start_session_cleanup_thread(session)
        return session

    def _start_session_cleanup_thread(self, session: Session) -> None:
        """Spawns a daemon thread that removes *session* from _active_sessions when it closes."""
        def _wait_and_remove():
            try:
                # Poll until the session signals closure.
                while not session._closed:
                    time.sleep(0.02)
            finally:
                with self._lock:
                    self._active_sessions.discard(session)

        t = threading.Thread(target=_wait_and_remove, daemon=True)
        t.start()

    def close_sessions_with(self, peer_device_id: str) -> None:
        """Closes any active sessions with the specified peer."""
        with self._lock:
            to_close = [s for s in self._active_sessions if s.peer_device_id == peer_device_id]
        for s in to_close:
            try:
                s.close()
            except Exception:
                pass

    def _handle_session(self, session: Session) -> None:
        """Handles an incoming verified session from another node."""
        if self.harness.is_partitioned(self.device_id, session.peer_device_id):
            session.close()
            return

        with self._lock:
            self._active_sessions.add(session)
        try:
            # Placeholder loop for sessions until the P3-5 engine is wired in.
            while not session._closed:
                try:
                    frame = session.recv(wait=0.2)
                    if frame.get("t") == "bye":
                        break
                except Exception:
                    break
        finally:
            with self._lock:
                self._active_sessions.discard(session)

    def close(self) -> None:
        """Stops the transport server and closes active sessions."""
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:
                pass
            self.server = None

        with self._lock:
            sessions = list(self._active_sessions)
            self._active_sessions.clear()

        for s in sessions:
            try:
                s.close()
            except Exception:
                pass


class SyncHarness:
    """Manages a cluster of N in-process Sera workstations for synchronization testing."""

    def __init__(
        self,
        num_nodes: int = 3,
        base_dir: Path | str | None = None,
        master_password: str = DEFAULT_MASTER_PASSWORD,
    ):
        if num_nodes < 1:
            raise ValueError("num_nodes must be >= 1")

        self.num_nodes = num_nodes
        self._temp_dir = None
        if base_dir is None:
            self._temp_dir = tempfile.mkdtemp(prefix="sera_harness_")
            self.base_dir = Path(self._temp_dir)
        else:
            self.base_dir = Path(base_dir)
            self.base_dir.mkdir(parents=True, exist_ok=True)

        self.master_password = master_password
        self.nodes: list[HarnessNode] = []
        self._partitions: set[frozenset[str]] = set()

        self._init_cluster()

    def _init_cluster(self) -> None:
        """Bootstraps Node 0 (Admin) and pairs subsequent joiner nodes via real P2-4 code.

        After all pairings complete the full member roster from the admin is
        propagated into every joiner's database so that each node's MemberSet
        covers the entire cluster (not just the members that existed when its
        snapshot was copied).
        """
        # 1. Initialize Node 0 (Office Creator / Admin)
        admin_dir = self.base_dir / "node_0"
        admin_dir.mkdir(parents=True, exist_ok=True)
        dek = sera_keys.new_dek()
        office_id = sera_keys.OfficeInfo.new_office_id()
        office = sera_keys.OfficeInfo(
            office_id=office_id,
            office_name="Test Office",
            key_id=sera_keys.key_id(dek),
        )
        sera_keys.store_dek(admin_dir, dek, self.master_password, office_id)
        sera_keys.save_office(admin_dir, office)
        admin_pubkey = sync_admin.create_admin_key(admin_dir, self.master_password)
        office.admin_pubkey = admin_pubkey

        admin_identity = sync_identity.ensure_device_identity(admin_dir)
        admin_hex_key = sera_keys.dek_hex(dek)
        admin_db_path = admin_dir / "master.db"
        admin_raw_path = admin_dir / "rawPayload.db"

        # Initialize admin DB schema.
        from database import SeraDatabase
        SeraDatabase(
            str(admin_db_path),
            admin_hex_key,
            raw_db_path=str(admin_raw_path),
            defer_startup_maintenance=True,
            key_mode="office",
        )

        with open_db_conn(admin_db_path, admin_hex_key) as conn:
            sync_admin.init_office_membership(
                admin_dir,
                conn,
                admin_identity.cert_pem.decode("ascii"),
                "Node-0 (Admin)",
            )
            sync_discovery.ensure_address_book_table(conn)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        with open_db_conn(admin_raw_path, admin_hex_key) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        admin_node = HarnessNode(
            index=0,
            name="Node-0",
            app_dir=admin_dir,
            dek=dek,
            office=office,
            identity=admin_identity,
            harness=self,
        )
        self.nodes.append(admin_node)

        # 2. Initialize Joiner nodes through real P2-4 code.
        for i in range(1, self.num_nodes):
            joiner_dir = self.base_dir / f"node_{i}"
            joiner_dir.mkdir(parents=True, exist_ok=True)

            # Admin opens PairingWindow (P2-4).
            window = PairingWindow(
                admin_dir,
                lambda: create_connection(admin_db_path, admin_hex_key),
                admin_identity.device_id,
                host="127.0.0.1",
                port=0,
            )
            window.start()

            try:
                join_res = join_office(
                    joiner_dir,
                    "127.0.0.1",
                    window.code,
                    f"Node-{i}",
                    port=window.port,
                )
            finally:
                window.close()

            # Checkpoint admin databases before copying.
            with open_db_conn(admin_db_path, admin_hex_key) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            with open_db_conn(admin_raw_path, admin_hex_key) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

            # Copy checkpointed snapshot to joiner (harness deviation — see module docstring).
            shutil.copy2(admin_db_path, joiner_dir / "master.db")
            shutil.copy2(admin_raw_path, joiner_dir / "rawPayload.db")

            # Store the join-result member records in the joiner's DB.
            with open_db_conn(joiner_dir / "master.db", admin_hex_key) as conn:
                for rec in join_res.records:
                    sync_admin.store_record(conn, rec, office.admin_pubkey)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

            joiner_identity = sync_identity.load_device_identity(joiner_dir)
            joiner_office = sera_keys.load_office(joiner_dir)

            joiner_node = HarnessNode(
                index=i,
                name=f"Node-{i}",
                app_dir=joiner_dir,
                dek=dek,
                office=joiner_office,
                identity=joiner_identity,
                harness=self,
            )
            self.nodes.append(joiner_node)

        # 3. Initialize transports, servers, and address books on all nodes.
        #    _setup_network calls _propagate_all_members first, which pushes the
        #    full admin member roster into every joiner so all MemberSets cover
        #    the entire cluster.
        self._setup_network()


    def _propagate_all_members(self) -> None:
        """Push every member row from the admin DB into every joiner DB.

        Raises RuntimeError if reading from admin or writing to any joiner fails.
        Note (deviation): membership rows are copied directly across local databases
        in the harness rather than verified via sync_admin.store_record.
        """
        if len(self.nodes) <= 1:
            return

        admin_node = self.nodes[0]

        # Read column names once from the admin.
        with open_db_conn(admin_node.db_path, admin_node.hex_key) as src_conn:
            try:
                cols = [
                    c[1]
                    for c in src_conn.execute(
                        "PRAGMA table_info(_sync_members)"
                    ).fetchall()
                ]
                if not cols:
                    raise RuntimeError("PRAGMA table_info(_sync_members) returned no columns on admin")
                all_rows = src_conn.execute(
                    "SELECT * FROM _sync_members ORDER BY rowid"
                ).fetchall()
            except Exception as exc:
                raise RuntimeError(f"_propagate_all_members: could not read _sync_members from admin: {exc}") from exc

        col_names = ", ".join(cols)
        placeholders = ", ".join("?" * len(cols))
        sql = (
            f"INSERT OR REPLACE INTO _sync_members ({col_names}) "
            f"VALUES ({placeholders})"
        )
        for node in self.nodes[1:]:
            with open_db_conn(node.db_path, node.hex_key) as conn:
                for row in all_rows:
                    try:
                        conn.execute(sql, row)
                    except Exception as exc:
                        raise RuntimeError(
                            f"_propagate_all_members: could not insert into {node.name}: {exc}"
                        ) from exc

    def _setup_network(self) -> None:
        """Sets up mutual-TLS transports and servers on all nodes."""
        # Propagate the full roster before building MemberSets.
        self._propagate_all_members()

        admin_pubkey = self.nodes[0].office.admin_pubkey

        for node in self.nodes:
            with node.open_db() as conn:
                recs = sync_admin.list_members(conn, admin_pubkey, include_revoked=False)
                members = MemberSet(
                    [(m["device_id"], m["cert_pem"]) for m in recs],
                    own_cert_pem=node.cert_pem,
                )

            transport = SyncTransport(node.chain, members)
            server = transport.serve(node._handle_session, host="127.0.0.1", port=0)
            node.transport = transport
            node.server = server
            node.port = server.address[1]

        # Record all peer addresses in local address book.
        for node in self.nodes:
            with node.open_db() as conn:
                for other in self.nodes:
                    if other.device_id != node.device_id:
                        sync_discovery.upsert_address(
                            conn,
                            other.device_id,
                            "127.0.0.1",
                            other.port,
                            source="manual",
                        )

    def _to_device_id(self, node_or_id: "HarnessNode | int | str") -> str:
        """Resolves node, index, or string to a device_id."""
        if isinstance(node_or_id, HarnessNode):
            return node_or_id.device_id
        if isinstance(node_or_id, int):
            return self.nodes[node_or_id].device_id
        return str(node_or_id)

    def get_node(self, node_or_id: "HarnessNode | int | str") -> HarnessNode:
        """Retrieves a HarnessNode by index, name, or device_id."""
        if isinstance(node_or_id, HarnessNode):
            return node_or_id
        if isinstance(node_or_id, int):
            return self.nodes[node_or_id]
        dev_id = str(node_or_id)
        for n in self.nodes:
            if n.device_id == dev_id or n.name == dev_id:
                return n
        raise KeyError(f"No node with identity {node_or_id!r}")

    def partition(
        self,
        a: "HarnessNode | int | str",
        b: "HarnessNode | int | str",
    ) -> None:
        """Partitions node a and node b so neither can communicate with the other."""
        id_a = self._to_device_id(a)
        id_b = self._to_device_id(b)
        self._partitions.add(frozenset({id_a, id_b}))

        # Drop any existing active connections between them.
        node_a = self.get_node(id_a)
        node_b = self.get_node(id_b)
        node_a.close_sessions_with(id_b)
        node_b.close_sessions_with(id_a)

    def heal(
        self,
        a: "HarnessNode | int | str | None" = None,
        b: "HarnessNode | int | str | None" = None,
    ) -> None:
        """Removes the partition between a and b, or heals all partitions if neither is specified."""
        if a is None and b is None:
            self._partitions.clear()
        else:
            id_a = self._to_device_id(a)
            id_b = self._to_device_id(b)
            self._partitions.discard(frozenset({id_a, id_b}))

    def is_partitioned(
        self,
        a: "HarnessNode | int | str",
        b: "HarnessNode | int | str",
    ) -> bool:
        """Returns True if nodes a and b are currently partitioned."""
        id_a = self._to_device_id(a)
        id_b = self._to_device_id(b)
        return frozenset({id_a, id_b}) in self._partitions

    def digest(self, node: "HarnessNode | int | str") -> str:
        """Calculates the database digest of a node."""
        return digest(self.get_node(node))

    def run_until_quiet(self, timeout: float = 5.0, quiet_period: float = 0.05) -> None:
        """Waits until all in-flight sync sessions across the cluster settle.

        Raises ``TimeoutError`` if ``timeout`` seconds elapse before the cluster
        goes quiet.  This ensures tests do not silently pass because the timeout
        expired with sessions still open.
        """
        deadline = time.monotonic() + timeout
        last_busy = time.monotonic()

        while time.monotonic() < deadline:
            busy = any(node._active_sessions for node in self.nodes)

            if busy:
                last_busy = time.monotonic()
                time.sleep(0.02)
            else:
                if time.monotonic() - last_busy >= quiet_period:
                    return
                time.sleep(0.01)

        # Check one final time after deadline.
        busy = any(node._active_sessions for node in self.nodes)
        if busy:
            counts = {n.name: len(n._active_sessions) for n in self.nodes if n._active_sessions}
            raise TimeoutError(
                f"run_until_quiet: cluster still has active sessions after {timeout}s: {counts}"
            )

    def close(self) -> None:
        """Stops all servers and nodes, cleaning up resources."""
        for node in self.nodes:
            node.close()
        self.nodes.clear()

        if self._temp_dir is not None and os.path.exists(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    def __enter__(self) -> "SyncHarness":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
