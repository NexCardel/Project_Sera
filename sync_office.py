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
                **pairing_kwargs):
        self.app_dir = Path(app_dir)
        self._open_db = open_db
        self.admin_device_id = admin_device_id
        self._own_cert_pem = own_cert_pem
        self._on_joined_cb = on_joined
        self._on_closed_cb = on_closed
        self._sync_host = sync_host
        self._sync_port = sync_port
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
        """The bound snapshot-server port (useful when ``sync_port=0`` was requested)."""
        return self._server.address[1] if self._server is not None else None

    def _members(self) -> MemberSet:
        office = sera_keys.load_office(self.app_dir)
        conn = self._open_db()
        try:
            return MemberSet.from_db(conn, office.admin_pubkey, self._own_cert_pem)
        finally:
            conn.close()

    def start(self) -> None:
        """Starts the snapshot server, then the pairing window. Raises whatever ``PairingWindow.start``
        raises (e.g. ``sync_admin.NotAdmin``) before anything is opened."""
        cert_chain = sync_identity.load_cert_chain_args(self.app_dir)
        self._transport = SyncTransport(cert_chain, self._members())
        self._server = self._transport.serve(
            lambda session: sync_snapshot.handle_snapshot_session(session, self.app_dir),
            host=self._sync_host, port=self._sync_port,
        )
        try:
            self.window.start()
        except Exception:
            self._server.stop()
            self._server = None
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
