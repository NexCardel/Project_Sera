"""
sync_office.py
---------------
First-run office-mode wiring for Sera Sync v3 (WP P2-7).

Keeps the UI (``ui/dialogs/first_run_dialog.py``, ``ui/dialogs/sera_sync_dialog.py``) out of
the key/pairing/snapshot plumbing, and keeps PySide6 out of this module (§0 rule 7).

- ``create_new_office``: a brand-new PC with no database creates the office data key, its
  own admin key, an empty ``master.db`` and its own (admin) membership record.
- ``join_office``: a brand-new PC pairs with the admin PC (P2-4) and downloads the office
  snapshot (P2-6) in one call. If pairing succeeds but the download doesn't, ``office.json``
  is already on disk and ``sync_snapshot.has_pending_join()`` is true; the existing start-up
  resume in ``main.py`` picks it up on the next launch.
- ``AddWorkstationSession``: the admin PC's "Add workstation" -- runs a ``PairingWindow``
  (P2-4) and, for as long as it is open, a mutual-TLS server (P2-3) that serves the snapshot
  (P2-6) to whoever just paired. Nothing served snapshots before this WP (see P2-6's notes).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import sera_keys
import sync_admin
import sync_identity
import sync_pairing
import sync_snapshot
import sync_transport
from sync_transport import MemberSet, SyncTransport

_log = logging.getLogger("sera.sync_office")

MASTER_DB_NAME = "master.db"

# How long the snapshot server keeps serving after pairing succeeds: pairing itself closes
# the window the instant the joiner is admitted, but the joiner's snapshot download (a
# separate step, started only after pairing returns) still needs somewhere to connect to.
JOIN_DOWNLOAD_GRACE_SECONDS = 120.0


class SyncOfficeError(Exception):
    """Raised by the first-run office-mode helpers in this module."""


def validate_new_office(office_name: str, password: str, password_confirm: str) -> str | None:
    """Returns an error message, or ``None`` if the "New office" inputs are acceptable."""
    if not (office_name or "").strip():
        return "Enter a name for the office."
    err = sera_keys.validate_master_password(password)
    if err:
        return err
    if (password or "").strip() != (password_confirm or "").strip():
        return "Passwords do not match. Please retype."
    return None


def create_new_office(app_dir, office_name: str, password: str, device_name: str) -> sera_keys.OfficeInfo:
    """Brand-new PC, no existing office or database: create the office.

    Creates the office data key (DEK), this PC's admin key, this PC's device identity, writes
    ``office.json``, then creates an empty ``master.db`` with this PC's own (admin, letter A)
    membership record.

    Unlike ``sync_pairing._install`` / ``sync_migrate.migrate_to_office_key`` (which write
    ``office.json`` last because everything it names is already verified by then), this PC's
    own ``init_office_membership`` needs ``office.json``'s ``admin_pubkey`` to sign its first
    records with (``sync_admin.load_admin_key`` reads it back), so it has to exist first. If
    database creation then fails, this PC is left in office mode with no database -- the same
    shape as an interrupted pairing download, except ``sync_snapshot.has_pending_join`` won't
    recognise it (no ``pairing.json``); the owner would need to remove ``keys/office.json`` (or
    restore a backup) and try again. Database creation failing right after key files that just
    succeeded is expected to be rare in practice (both write to the same local disk).

    Raises ``SyncOfficeError`` if this PC already has an office or a database.
    """
    app_dir = Path(app_dir)
    if (sera_keys.keys_dir(app_dir) / sera_keys.OFFICE_FILE).exists():
        raise SyncOfficeError("This PC already belongs to an office.")
    db_path = app_dir / MASTER_DB_NAME
    if db_path.exists():
        raise SyncOfficeError("A database already exists in this folder.")
    err = validate_new_office(office_name, password, password)
    if err:
        raise SyncOfficeError(err)

    office_name = office_name.strip()
    password = password.strip()

    dek = sera_keys.new_dek()
    office_id = sera_keys.OfficeInfo.new_office_id()
    key_id = sera_keys.key_id(dek)
    hex_key = sera_keys.dek_hex(dek)
    identity = sync_identity.ensure_device_identity(app_dir)
    cert_pem = identity.cert_pem.decode("ascii") if isinstance(identity.cert_pem, bytes) else identity.cert_pem

    # DEK and admin key are stored before office.json: a failure here leaves no office.json
    # (still legacy mode) and the next attempt simply overwrites these key files (§0 rule 3:
    # old ones are kept as .bak).
    sera_keys.store_dek(app_dir, dek, password, office_id)
    admin_pubkey = sync_admin.write_admin_key_files(app_dir, sync_admin.generate_admin_key(), password, office_id)
    office = sera_keys.OfficeInfo(office_id=office_id, office_name=office_name, key_id=key_id,
                                  admin_pubkey=admin_pubkey)
    sera_keys.save_office(app_dir, office)

    from database import SeraDatabase
    db = SeraDatabase(str(db_path), hex_key, defer_startup_maintenance=True)
    try:
        with db._connect() as conn:
            sync_admin.init_office_membership(app_dir, conn, cert_pem, device_name)
    finally:
        del db

    _log.info("created new office %r (%s) as %s", office_name, office_id, identity.device_id)
    return office


def ensure_office_identity(app_dir, conn, device_name: str) -> sync_identity.DeviceIdentity:
    """Office-mode start-up: make sure this PC has a device identity and, on the PC that holds
    the office admin key, that the office has its first membership records.

    "New office" and "Join office" already set both up, but "Convert to office key"
    (``sync_migrate``, P1-4) creates only the office and admin keys -- a converted admin PC
    had no device identity and no member record, so discovery, the sync engine, "Add
    workstation" and "Become admin" all stayed off on it. Idempotent: the identity is loaded
    if it exists, and the membership records are only written while the office has none
    (``init_office_membership`` refuses otherwise). A PC without the admin key never writes
    records here -- it gets its own from the admin PC when it joins.
    """
    identity = sync_identity.ensure_device_identity(app_dir)
    sync_admin.ensure_members_table(conn)
    has_records = conn.execute("SELECT 1 FROM %s LIMIT 1" % sync_admin.MEMBERS_TABLE).fetchone()
    if not has_records and sync_admin.has_admin_key(app_dir):
        cert_pem = identity.cert_pem.decode("ascii") if isinstance(identity.cert_pem, bytes) else identity.cert_pem
        sync_admin.init_office_membership(app_dir, conn, cert_pem, device_name)
        _log.info("wrote this admin PC's first membership records as %s", identity.device_id)
    return identity


def dispatch_session(session, app_dir, engine) -> None:
    """Routes one incoming session on the permanent sync port: a snapshot request
    (``{"t": "snapshot"}``, P2-6) goes to ``sync_snapshot.handle_snapshot_session``; everything
    else (``hello``, the P3-5 session protocol) goes to ``engine.handle_session`` (blueprint §5
    P3-7 "Port clash": the sync engine's server and "Add workstation"'s used to both want
    port 49159). Uses ``Session.peek_type`` so neither handler needs to change -- each still
    reads its own first frame with ``recv()``. ``{"t": "shadow_snapshot"}`` (P3-7b: another
    PC downloading the admin PC's shadow replica to turn shadow mode on) goes to
    ``sync_shadow.handle_shadow_snapshot_session``."""
    import sync_snapshot
    t = session.peek_type()
    if t == sync_snapshot.FRAME_SNAPSHOT:
        sync_snapshot.handle_snapshot_session(session, app_dir)
    elif t == "shadow_snapshot":
        import sync_shadow
        sync_shadow.handle_shadow_snapshot_session(session, engine.db, app_dir)
    else:
        engine.handle_session(session)


def join_office(app_dir, host: str, code, device_name: str, *,
                port: int = sync_pairing.PAIRING_PORT,
                sync_port: int = sync_transport.SYNC_PORT,
                on_progress: Callable[[str, int, int], None] | None = None) -> sync_pairing.JoinResult:
    """Brand-new PC joining an office for the first time (P2-7 first-run wizard).

    Pairs with the admin PC at ``host`` (P2-4: writes the office key, admin recovery blobs and
    ``office.json``), then downloads and installs the office snapshot (P2-6). ``port`` is the
    pairing port (49158); ``sync_port`` is the mutual-TLS snapshot port (49159) -- overridable
    for tests, same as ``sync_snapshot.download_snapshot``'s own ``port`` argument.

    If pairing fails, nothing is written (see ``sync_pairing.join_office``). If pairing
    succeeds but the download doesn't, ``office.json`` now exists without ``master.db``:
    the caller should tell the user Sera can resume automatically the next time it starts
    (``main.py``'s existing ``sync_snapshot.has_pending_join`` check), or retry immediately
    by calling ``sync_snapshot.resume_join_snapshot`` again.
    """
    result = sync_pairing.join_office(app_dir, host, code, device_name, port=port)
    sync_snapshot.download_snapshot(
        app_dir, result.admin_address, result.admin_device_id, result.records,
        port=sync_port, on_progress=on_progress,
    )
    return result


class AddWorkstationSession:
    """The admin PC's "Add workstation" (P2-7 Sera Sync panel).

    Runs a ``PairingWindow`` (P2-4) and, while it is open, a mutual-TLS server (P2-3) that
    serves the office snapshot (P2-6) to whoever just paired -- P2-6 shipped with nothing
    listening on 49159, so a real join always stalled at the download step until this WP.

    ``open_db()`` returns a new connection to master.db (used from worker threads).
    """

    def __init__(self, app_dir, open_db, admin_device_id: str, own_cert_pem: str, *,
                on_joined: Callable[[dict], None] | None = None,
                on_closed: Callable[[str], None] | None = None,
                sync_host: str = "0.0.0.0", sync_port: int = sync_transport.SYNC_PORT,
                transport: SyncTransport | None = None,
                **pairing_kwargs):
        """``transport``: an already-serving ``SyncTransport`` to reuse instead of binding a new
        port (blueprint §5 P3-7 "Port clash": once the sync engine's permanent server holds
        ``sync_port``, a second bind on it fails). When given, its member set is rebuilt on join
        and it is left running (not stopped) when this session closes -- it belongs to the
        caller. The caller's server must route ``{"t":"snapshot"}`` frames to
        ``sync_snapshot.handle_snapshot_session`` (``dispatch_session`` does this)."""
        self.app_dir = Path(app_dir)
        self._open_db = open_db
        self.admin_device_id = admin_device_id
        self._own_cert_pem = own_cert_pem
        self._on_joined_cb = on_joined
        self._on_closed_cb = on_closed
        self._sync_host = sync_host
        self._sync_port = sync_port
        self._external_transport = transport
        self._transport: SyncTransport | None = None
        self._server = None
        self._grace_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self.window = sync_pairing.PairingWindow(
            self.app_dir, open_db, admin_device_id,
            on_joined=self._on_joined, on_closed=self._on_closed, **pairing_kwargs,
        )

    @property
    def sync_port(self) -> int | None:
        """The bound snapshot-server port (useful when ``sync_port=0`` was requested). With an
        external ``transport``, that server's port was fixed by the caller."""
        if self._server is not None:
            return self._server.address[1]
        return self._sync_port if self._external_transport is not None else None

    def _members(self) -> MemberSet:
        office = sera_keys.load_office(self.app_dir)
        conn = self._open_db()
        try:
            return MemberSet.from_db(conn, office.admin_pubkey, self._own_cert_pem)
        finally:
            conn.close()

    def start(self) -> None:
        """Starts the snapshot server (unless an external ``transport`` was given, in which case
        it is reused as is), then the pairing window. Raises whatever ``PairingWindow.start``
        raises (e.g. ``sync_admin.NotAdmin``) before anything is opened."""
        if self._external_transport is not None:
            self._transport = self._external_transport
            self._transport.update_members(self._members())
        else:
            cert_chain = sync_identity.load_cert_chain_args(self.app_dir)
            self._transport = SyncTransport(cert_chain, self._members())
            self._server = self._transport.serve(
                lambda session: sync_snapshot.handle_snapshot_session(session, self.app_dir),
                host=self._sync_host, port=self._sync_port,
            )
        try:
            self.window.start()
        except Exception:
            self._stop_server()
            raise

    def _on_joined(self, record: dict) -> None:
        try:
            self._transport.update_members(self._members())
        except Exception:
            _log.exception("could not rebuild the sync trust store after %s joined", record.get("device_id"))
        if self._on_joined_cb is not None:
            self._on_joined_cb(record)

    def _on_closed(self, reason: str) -> None:
        if reason == "joined":
            # Pairing closes the window the instant the joiner is admitted; the joiner's
            # snapshot download is a separate step that starts right after and still needs
            # somewhere to connect, so the server outlives the window briefly instead of
            # being cut off the moment pairing succeeds.
            timer = threading.Timer(JOIN_DOWNLOAD_GRACE_SECONDS, self._stop_server)
            timer.daemon = True
            with self._lock:
                self._grace_timer = timer
            timer.start()
        else:
            self._stop_server()
        if self._on_closed_cb is not None:
            self._on_closed_cb(reason)

    def _stop_server(self) -> None:
        with self._lock:
            timer, self._grace_timer = self._grace_timer, None
            server, self._server = self._server, None
        if timer is not None:
            timer.cancel()
        if server is not None:
            server.stop()

    def close(self, reason: str = "cancelled") -> None:
        """Closes the pairing window (if still open) and always stops the snapshot server,
        even after a successful join whose download grace period hasn't elapsed yet."""
        self.window.close(reason)
        self._stop_server()
