"""
sync_rejoin.py
--------------
WP P2-8: rejoin an existing (legacy-mode) PC to the office and salvage what only it has.

Because of F1 the office PCs have diverged. Only the admin PC migrates (P1-4); every other
PC rejoins (blueprint §5 P2-8):

  1. ``move_legacy_files``: master.db, rawPayload.db (with their -wal/-shm/-journal),
     sera.salt and sera.key go to ``legacy/<ts>/``. Nothing is deleted (§0 rule 3).
  2. ``rejoin_office``: pair (P2-4) and download the office snapshot (P2-6).
  3. Salvage: open the legacy DB with the legacy key, read-only (a temp copy is opened, the
     files in ``legacy/<ts>/`` are never written). ``plan_salvage`` is the dry run;
     ``apply_salvage`` writes to the office DB after the user confirmed.
       - clients matched by the internal PK column (mcl_columns.is_internal_pk, usually PAN),
         values normalised with upper-case + trim. Unmatched legacy clients are inserted with
         their client_values (columns mapped by label; unknown labels are reported, not
         imported). Matched clients with different values are listed; the office value is
         kept unless the user picks "take this PC's values" for that client.
       - audit_log rows whose (ts, actor, action, detail) the office doesn't have.
       - tracker_dump rows whose dataset_key (recomputed with the office's client id) the office
         doesn't have; sdc_session_timelines matched by session_id.
       - a legacy client with no internal-PK value can't be matched safely: it is listed and
         never inserted (stop and ask).
  4. ``write_salvage_report``: ``logs/salvage-<ts>.txt``.

A state file (``incoming/rejoin_state.json``) makes every step resumable at start-up
(``resume_interrupted_rejoin``). The salvage itself is idempotent: running it again imports
nothing twice, so a crash between the master.db and rawPayload.db transactions is repaired by
running it again.

Reports and logs show only PANs (the PK value), client tokens, column labels and counts, never
client values (§0 rule 12). Never logs passwords or keys. No PySide6 here (§0 rule 7).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sera_keys

MASTER_DB = "master.db"
RAW_DB = "rawPayload.db"
SALT_FILE = "sera.salt"
LEGACY_KEY_FILE = "sera.key"
SIDECARS = ("-wal", "-shm", "-journal")
LEGACY_DIRNAME = "legacy"
STATE_FILE = "rejoin_state.json"
REQUEST_FILE = "rejoin_request.json"
REPORT_PREFIX = "salvage-"

# Serial-number columns: rewritten per PC by resequence_client_serial_numbers (F12), so they
# are neither compared nor imported. Same list as database.py's resequence fallback.
SERIAL_LABELS = frozenset({"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "id", "#", "numer", "number"})

_log = logging.getLogger("sera.sync.rejoin")


class RejoinError(Exception):
    """The rejoin can't go on. The message says what to do; nothing was lost."""


class SalvageError(RejoinError):
    """The salvage can't be done safely (e.g. the PK columns don't agree). Stop and ask."""


class LegacyPasswordNeeded(RejoinError):
    """The saved sera.key doesn't open the legacy DB; ask the user for the old password."""


# ---------------------------------------------------------------- request / state files

def _incoming(app: Path) -> Path:
    return app / "incoming"


def request_path(app_dir) -> Path:
    return _incoming(Path(app_dir)) / REQUEST_FILE


def state_path(app_dir) -> Path:
    return _incoming(Path(app_dir)) / STATE_FILE


def write_rejoin_request(app_dir, requested_by: str | None = None) -> Path:
    path = request_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"requested_at": datetime.now().isoformat(timespec="seconds"), "requested_by": requested_by}
    sera_keys.atomic_write(path, (json.dumps(data) + "\n").encode("utf-8"))
    return path


def read_rejoin_request(app_dir) -> dict | None:
    path = request_path(app_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else {}


def clear_rejoin_request(app_dir) -> None:
    try:
        request_path(app_dir).unlink()
    except FileNotFoundError:
        pass


def read_state(app_dir) -> dict | None:
    path = state_path(app_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RejoinError(f"The rejoin state file {path} is damaged ({e}). The old files are in "
                          f"{Path(app_dir) / LEGACY_DIRNAME}.") from None
    if not isinstance(data, dict) or not data.get("legacy_dir") or not isinstance(data.get("moved"), list):
        raise RejoinError(f"The rejoin state file {path} is damaged. The old files are in "
                          f"{Path(app_dir) / LEGACY_DIRNAME}.")
    return data


def _write_state(app: Path, state: dict) -> None:
    path = state_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    sera_keys.atomic_write(path, (json.dumps(state, indent=2) + "\n").encode("utf-8"))


def _clear_state(app: Path) -> None:
    try:
        state_path(app).unlink()
    except FileNotFoundError:
        pass


def finish_rejoin(app_dir) -> None:
    """Forget the rejoin (after the salvage, or when the user chose not to import).
    The legacy files stay in ``legacy/<ts>/``."""
    _clear_state(Path(app_dir))


# ---------------------------------------------------------------- legacy key

def legacy_hex_key(legacy_dir, password: str) -> str:
    """Key of the legacy DB in ``legacy_dir`` (master.db + sera.salt). Raises WrongPassword."""
    import sync_migrate
    try:
        return sync_migrate._legacy_hex_key(Path(legacy_dir), password)
    except sync_migrate.MigrationError as e:
        raise RejoinError(str(e)) from None


def saved_legacy_hex_key(legacy_dir) -> str | None:
    """The legacy key from the saved sera.key, or None if there is none or it doesn't open the DB."""
    key_file = Path(legacy_dir) / LEGACY_KEY_FILE
    try:
        password = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not password:
        return None
    try:
        return legacy_hex_key(legacy_dir, password)
    except (sera_keys.WrongPassword, RejoinError):
        return None


# ---------------------------------------------------------------- step 1: move the legacy files

def _legacy_names(app: Path) -> list[str]:
    names = []
    for db in (MASTER_DB, RAW_DB):
        if (app / db).exists():
            names.append(db)
        names.extend(db + s for s in SIDECARS if (app / (db + s)).exists())
    names.extend(n for n in (SALT_FILE, LEGACY_KEY_FILE) if (app / n).exists())
    return names


def _new_legacy_dir(app: Path) -> Path:
    base = app / LEGACY_DIRNAME
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = base / ts
    n = 1
    while path.exists():
        path = base / f"{ts}_{n}"
        n += 1
    path.mkdir(parents=True)
    return path


def _remove_empty_dirs(legacy_dir: Path) -> None:
    for d in (legacy_dir, legacy_dir.parent):
        try:
            d.rmdir()
        except OSError:
            pass


def move_legacy_files(app_dir) -> Path:
    """Step 1: move this PC's legacy files to ``legacy/<ts>/``. Returns that folder.

    All or nothing: if one file can't be moved (in use), the ones already moved go back.
    """
    app = Path(app_dir)
    if sera_keys.load_office(app) is not None:
        raise RejoinError("This PC already uses an office key; there is nothing to rejoin.")
    if read_state(app) is not None:
        raise RejoinError("A rejoin is already in progress on this PC.")
    if not (app / MASTER_DB).is_file():
        raise RejoinError(f"{MASTER_DB} not found in {app}.")

    names = _legacy_names(app)
    legacy_dir = _new_legacy_dir(app)
    state = {"phase": "moving", "legacy_dir": str(legacy_dir), "moved": names,
             "started_at": datetime.now().isoformat(timespec="seconds")}
    _write_state(app, state)
    moved = []
    try:
        for n in names:
            os.replace(app / n, legacy_dir / n)
            moved.append(n)
    except OSError as e:
        for n in reversed(moved):
            try:
                os.replace(legacy_dir / n, app / n)
            except OSError:
                _log.error("could not move %s back from %s", n, legacy_dir)
                raise RejoinError(f"{n} could not be moved back from {legacy_dir}. Move it back by hand "
                                  f"before starting Sera again.") from None
        _clear_state(app)
        _remove_empty_dirs(legacy_dir)
        raise RejoinError(f"{e.filename and Path(e.filename).name or 'A file'} is in use, so this PC's files "
                          f"could not be moved. Close other programs using Sera's data and try again.") from None
    state["phase"] = "moved"
    _write_state(app, state)
    _log.info("moved legacy files to %s: %s", legacy_dir, ", ".join(names))
    return legacy_dir


def _restore_legacy_files(app: Path, state: dict) -> None:
    """Undo step 1 (only when no office.json was written)."""
    legacy_dir = Path(state["legacy_dir"])
    names = [n for n in state["moved"] if isinstance(n, str) and Path(n).name == n]
    clash = [n for n in names if (legacy_dir / n).exists() and (app / n).exists()]
    if clash:
        raise RejoinError(f"Can't put the old files back from {legacy_dir}: {', '.join(clash)} already "
                          f"exist in {app}. Nothing was changed; ask the office administrator.")
    for n in names:
        if (legacy_dir / n).exists():
            os.replace(legacy_dir / n, app / n)
    _clear_state(app)
    _remove_empty_dirs(legacy_dir)
    _log.info("rejoin undone: legacy files restored from %s", legacy_dir)


def _rename_to_bak(path: Path, ts: str) -> Path:
    bak = path.with_name(f"{path.name}.bak-{ts}")
    n = 1
    while bak.exists():
        bak = path.with_name(f"{path.name}.bak-{ts}_{n}")
        n += 1
    os.replace(path, bak)
    return bak


def undo_rejoin(app_dir) -> None:
    """Go back to the legacy files when the PC paired but never got the office DB (e.g. the
    admin PC can't serve the snapshot any more). Only allowed while master.db is missing.

    First, office files a partial snapshot install left in the app folder (the snapshot installs
    rawPayload.db before master.db) are renamed to ``*.bak-<ts>`` with their sidecars, so the
    legacy files can go back. Then office.json and pairing.json are renamed to ``*.bak-<ts>``
    (§0 rule 3); office.json goes first, so a crash after it is finished by
    ``resume_interrupted_rejoin`` ("rolled_back"). The other key files stay; a later join backs
    them up before replacing them.
    """
    app = Path(app_dir)
    state = read_state(app)
    if state is None:
        raise RejoinError("No rejoin is in progress on this PC.")
    if (app / MASTER_DB).exists():
        raise RejoinError("The office database is already installed; the rejoin can't be undone automatically.")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    import sync_pairing
    partial = [RAW_DB + s for s in ("",) + SIDECARS] + [MASTER_DB + s for s in SIDECARS]
    partial += [n for n in state["moved"] if isinstance(n, str) and Path(n).name == n and n not in partial]
    for name in partial:
        if (app / name).exists():
            _rename_to_bak(app / name, ts)
    for path in (sera_keys.keys_dir(app) / sera_keys.OFFICE_FILE,
                 _incoming(app) / sync_pairing.JOIN_DIRNAME / sync_pairing.JOIN_STATE_FILE):
        if path.exists():
            _rename_to_bak(path, ts)
    _restore_legacy_files(app, state)
    _log.info("rejoin undone by the user before the office DB was installed")


def resume_interrupted_rejoin(app_dir) -> str | None:
    """At start-up, before any DB is opened. Returns what the caller should do next:

    None               no rejoin in progress
    "rolled_back"      pairing never finished; the legacy files are back, the PC is legacy again
    "pending_join"     paired, snapshot not installed yet: call ``resume_download``
    "salvage_pending"  office DB installed: offer the salvage (``salvage_after_rejoin``)
    """
    app = Path(app_dir)
    state = read_state(app)
    if state is None:
        return None
    if sera_keys.load_office(app) is None:
        _restore_legacy_files(app, state)
        return "rolled_back"
    if not (app / MASTER_DB).exists():
        return "pending_join"
    return "salvage_pending"


# ---------------------------------------------------------------- step 2: pair + snapshot

def rejoin_office(app_dir, host: str, code, device_name: str, *, pairing_port: int | None = None,
                  sync_port: int | None = None, on_progress=None):
    """Steps 1–2: move the legacy files, pair with the admin PC, download the snapshot.

    If pairing fails, the legacy files are put back and the error is raised again (the PC stays
    legacy). If the download fails after pairing, the PC is paired but has no DB yet: the error
    is raised and ``resume_interrupted_rejoin`` says "pending_join" on the next start.
    Returns the P2-4 ``JoinResult``.
    """
    import sync_pairing
    import sync_snapshot
    app = Path(app_dir)
    sync_pairing.normalize_code(code)          # a typo shouldn't move any file
    sync_pairing._check_name(device_name)

    move_legacy_files(app)
    state = read_state(app)
    try:
        kw = {} if pairing_port is None else {"port": pairing_port}
        result = sync_pairing.join_office(app, host, code, device_name, **kw)
    except BaseException:
        if sera_keys.load_office(app) is None:
            _restore_legacy_files(app, state)
        raise
    state.update(phase="joined", office_id=result.office_id, device_id=result.device_id,
                 token_letter=result.token_letter)
    _write_state(app, state)

    kw = {} if sync_port is None else {"port": sync_port}
    sync_snapshot.download_snapshot(app, result.admin_address, result.admin_device_id, result.records,
                                    on_progress=on_progress, **kw)
    state["phase"] = "salvage"
    _write_state(app, state)
    return result


def resume_download(app_dir, *, sync_port: int | None = None, on_progress=None) -> None:
    """Finish an interrupted step 2 (P2-6 ``resume_join_snapshot``)."""
    import sync_snapshot
    app = Path(app_dir)
    kw = {} if sync_port is None else {"port": sync_port}
    sync_snapshot.resume_join_snapshot(app, on_progress=on_progress, **kw)
    state = read_state(app)
    if state is not None:
        state["phase"] = "salvage"
        _write_state(app, state)


# ---------------------------------------------------------------- salvage report

@dataclass
class NewClient:
    legacy_id: int
    key: str
    token: str | None


@dataclass
class ClientConflict:
    legacy_id: int
    key: str
    office_id: int
    labels: list


@dataclass
class NoPkClient:
    legacy_id: int
    token: str | None


@dataclass
class AmbiguousClient:
    legacy_id: int
    key: str
    reason: str


@dataclass
class SalvageReport:
    dry_run: bool
    pk_label: str = ""
    matched_same: int = 0
    to_insert: list = field(default_factory=list)       # NewClient
    conflicts: list = field(default_factory=list)       # ClientConflict
    taken: list = field(default_factory=list)           # keys whose legacy values are (to be) taken
    no_pk: list = field(default_factory=list)           # NoPkClient: listed, never inserted
    ambiguous: list = field(default_factory=list)       # AmbiguousClient: listed, never inserted
    unknown_labels: dict = field(default_factory=dict)  # legacy label -> [keys]; values not imported
    unmapped_services: dict = field(default_factory=dict)
    audit_new: int = 0
    audit_existing: int = 0
    audit_unlinked: int = 0
    tracker_new: int = 0
    tracker_existing: int = 0
    tracker_skipped_client: int = 0
    timelines_new: int = 0
    timelines_existing: int = 0
    timelines_skipped_client: int = 0
    container_notes_not_imported: int = 0
    notes: list = field(default_factory=list)
    legacy_dir: str = ""
    report_path: str | None = None

    @property
    def changes(self) -> int:
        return (len(self.to_insert) + len(self.taken) + self.audit_new + self.tracker_new
                + self.timelines_new)


def write_salvage_report(app_dir, report: SalvageReport) -> Path:
    """``logs/salvage-<ts>.txt``. PANs, tokens, labels and counts only, never client values."""
    logs = Path(app_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs / f"{REPORT_PREFIX}{ts}.txt"
    n = 1
    while path.exists():
        path = logs / f"{REPORT_PREFIX}{ts}_{n}.txt"
        n += 1
    r = report
    pk = r.pk_label or "internal PK"
    lines = [
        "Sera Sync — salvage report (rejoin, WP P2-8)",
        f"Written: {datetime.now().isoformat(timespec='seconds')}",
        f"Mode: {'dry run (nothing written)' if r.dry_run else 'applied'}",
        f"Old files kept in: {r.legacy_dir or '(n/a)'}",
        f"Clients matched by: {pk}",
        "",
        f"Clients already in the office with the same values: {r.matched_same}",
        f"Clients added from this PC: {len(r.to_insert)}",
    ]
    lines += [f"  {c.key}  (token {c.token or 'new'})" for c in r.to_insert]
    lines.append(f"Clients with different values on this PC: {len(r.conflicts)}")
    for c in r.conflicts:
        kept = "this PC's values taken" if c.key in r.taken else "office values kept"
        lines.append(f"  {c.key}: {', '.join(c.labels)}  ({kept})")
    lines.append(f"Clients without a {pk} value (not imported, check by hand): {len(r.no_pk)}")
    lines += [f"  old client #{c.legacy_id}  (token {c.token or '-'})" for c in r.no_pk]
    lines.append(f"Clients that can't be matched safely (not imported, check by hand): {len(r.ambiguous)}")
    lines += [f"  {c.key}  old client #{c.legacy_id}: {c.reason}" for c in r.ambiguous]
    lines.append(f"Columns the office doesn't have (values not imported): {len(r.unknown_labels)}")
    lines += [f"  {label}: {', '.join(keys)}" for label, keys in sorted(r.unknown_labels.items())]
    if r.unmapped_services:
        lines.append("Services the office doesn't have (not attached): "
                     + ", ".join(f"{k} x{v}" for k, v in sorted(r.unmapped_services.items())))
    lines += [
        "",
        f"Audit log: {r.audit_new} added, {r.audit_existing} already there"
        + (f", {r.audit_unlinked} added without a client link" if r.audit_unlinked else ""),
        f"Tracker filings: {r.tracker_new} added, {r.tracker_existing} already there, "
        f"{r.tracker_skipped_client} skipped (their client wasn't imported)",
        f"SDC session timelines: {r.timelines_new} added, {r.timelines_existing} already there, "
        f"{r.timelines_skipped_client} skipped (their client wasn't imported)",
    ]
    if r.container_notes_not_imported:
        lines.append(f"Tracker container notes/screenshots not imported: {r.container_notes_not_imported} "
                     "(still in the old rawPayload.db)")
    lines += [f"Note: {n_}" for n_ in r.notes]
    sera_keys.atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
    return path


# ---------------------------------------------------------------- salvage core

def _norm(value) -> str:
    return str(value if value is not None else "").strip().upper()


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _fold(label) -> str:
    return str(label or "").strip().casefold()


def _connect(path: Path, hex_key: str):
    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None          # explicit BEGIN / COMMIT
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    return conn


def _open_checked(path: Path, hex_key: str, what: str, *, wrong_password: bool):
    import sqlcipher3.dbapi2 as sqlite3
    conn = _connect(path, hex_key)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    except sqlite3.DatabaseError:
        conn.close()
        if wrong_password:
            raise sera_keys.WrongPassword(f"the old password doesn't open {what}") from None
        raise RejoinError(f"{what} can't be opened with its key") from None
    return conn


def _tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _table_cols(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _mcl(conn) -> list:
    return [{"id": r[0], "label": r[1], "field_type": r[2], "pk": bool(r[3])} for r in conn.execute(
        "SELECT id, label, field_type, is_internal_pk FROM mcl_columns ORDER BY id")]


def _pk_column(cols: list, which: str) -> dict:
    pks = [c for c in cols if c["pk"]]
    if len(pks) != 1:
        raise SalvageError(f"The {which} database has {len(pks)} internal PK columns; the salvage needs "
                           "exactly one to match clients safely. Nothing was imported.")
    return pks[0]


def _is_serial(col: dict) -> bool:
    return col["field_type"] == "id" or _fold(col["label"]) in SERIAL_LABELS


def _services(conn) -> dict:
    """id -> folded name."""
    return {sid: _fold(name) for sid, name in conn.execute("SELECT id, name FROM services")}


_LETTER_TOKEN = re.compile(r"^[A-Z]+-\d+$")


def _pick(ids: list, rows: dict):
    """The one client among ``ids`` sharing a PK value: the only one, or the only active
    (not archived) one. The app enforces a unique PK only among active clients. None if
    that doesn't single one out."""
    if len(ids) == 1:
        return ids[0]
    active = [i for i in ids if not rows.get(i, {}).get("is_archived")]
    return active[0] if len(active) == 1 else None


def _dataset_key(row: dict, client_ref, client_gid: str = None) -> str:
    """Same identifier rules as database.py's start-up recompute of tracker_dump.dataset_key,
    with the client reference already translated to the office's id or gid."""
    from database import SeraDatabase
    cid_key = f"CLI_{client_gid}" if client_gid else (f"CLI_{client_ref}" if client_ref else "UNKNOWN")
    cand_id = row.get("unassigned_identity") or cid_key
    cand_form = ""
    raw_json = row.get("raw_payload_json")
    if raw_json:
        try:
            cj = json.loads(raw_json)
            c_raw = cj.get("raw_payload") if isinstance(cj.get("raw_payload"), dict) else {}
            cand_id = cj.get("gstin") or cj.get("pan") or c_raw.get("gstin") or c_raw.get("pan") or cand_id
            cand_form = cj.get("filing_type") or c_raw.get("filing_type") or ""
        except Exception:
            pass
    dkey = row.get("dataset_key")
    if not cand_form and dkey and dkey.count(":") >= 3:
        parts = dkey.split(":")
        if parts[2] and parts[2] != "FORM":
            cand_form = parts[2]
    return SeraDatabase.compute_dataset_key(row.get("portal"), cand_id, cand_form, row.get("period_label"))


class _Salvage:
    """One salvage run. ``apply=False`` is the dry run: it only reads."""

    def __init__(self, lconn, lraw, oconn, oraw, *, apply: bool, take_legacy, token_letter, report):
        self.l, self.lr, self.o, self.orw = lconn, lraw, oconn, oraw
        self.apply = apply
        self.take = {_norm(k) for k in (take_legacy or ())}
        self.letter = token_letter
        self.r = report
        self.id_map: dict = {}            # legacy client id -> office client id (or a dry-run placeholder)

    # ------------------------------------------------------------ clients
    def clients(self) -> None:
        lcols, ocols = _mcl(self.l), _mcl(self.o)
        lpk, opk = _pk_column(lcols, "old"), _pk_column(ocols, "office")
        if _fold(lpk["label"]) != _fold(opk["label"]):
            raise SalvageError(f"The internal PK column is '{lpk['label']}' on this PC but '{opk['label']}' in "
                               "the office database, so clients can't be matched safely. Nothing was imported.")
        self.r.pk_label = opk["label"]

        by_label: dict = {}
        for c in ocols:
            by_label.setdefault(_fold(c["label"]), []).append(c)
        l_label_count: dict = {}
        for c in lcols:
            l_label_count[_fold(c["label"])] = l_label_count.get(_fold(c["label"]), 0) + 1
        # legacy column id -> office column dict (mapped by label; ambiguous labels stay unmapped)
        self.col_map = {}
        self.unmapped_labels = {}
        for c in lcols:
            if c["id"] == lpk["id"] or _is_serial(c):
                continue
            targets = by_label.get(_fold(c["label"]), [])
            if len(targets) == 1 and l_label_count[_fold(c["label"])] == 1 and not _is_serial(targets[0]) \
                    and targets[0]["id"] != opk["id"]:
                self.col_map[c["id"]] = targets[0]
            else:
                self.unmapped_labels[c["id"]] = c["label"]

        l_values = self._values(self.l)
        o_values = self._values(self.o)
        l_clients = self._client_rows(self.l)
        o_rows = self._client_rows(self.o)
        o_notes = {cid: r.get("notes") for cid, r in o_rows.items()}

        l_keys = {cid: _norm(l_values.get(cid, {}).get(lpk["id"])) for cid in l_clients}
        l_index: dict = {}
        for cid, k in l_keys.items():
            if k:
                l_index.setdefault(k, []).append(cid)
        o_index: dict = {}
        for cid in o_notes:
            k = _norm(o_values.get(cid, {}).get(opk["id"]))
            if k:
                o_index.setdefault(k, []).append(cid)

        self.used_tokens = {_norm(t) for (t,) in self.o.execute("SELECT client_id_token FROM clients") if _text(t)}
        # add_client makes each new client's token from its row id (str(id)). A numeric old
        # token above the office's highest id ever handed out would be produced again later,
        # so only numbers at or below it are kept (review B1).
        seq = None
        if "sqlite_sequence" in _tables(self.o):
            seq = self.o.execute("SELECT seq FROM sqlite_sequence WHERE name = 'clients'").fetchone()
        top_id = self.o.execute("SELECT max(id) FROM clients").fetchone()[0]
        self.max_numeric_token = max(int(seq[0]) if seq and seq[0] is not None else 0, int(top_id or 0))
        self.l_services = _services(self.l)
        self.o_service_by_name = {}
        for sid, name in _services(self.o).items():
            self.o_service_by_name.setdefault(name, sid)
        self.l_client_services = {}
        for cid, sid in self.l.execute("SELECT client_id, service_id FROM client_services ORDER BY client_id, service_id"):
            self.l_client_services.setdefault(cid, []).append(sid)

        client_cols = [c for c in _table_cols(self.l, "clients") if c not in ("id", "gid") and c in set(_table_cols(self.o, "clients"))]
        now = datetime.utcnow().isoformat()

        for cid, row in l_clients.items():
            key = l_keys[cid]
            lv = l_values.get(cid, {})
            if not key:
                self.r.no_pk.append(NoPkClient(cid, _text(row.get("client_id_token")) or None))
                continue
            same = l_index[key]
            if len(same) > 1 and _pick(same, l_clients) != cid:
                reason = (f"archived duplicate of another client on this PC with this {opk['label']}"
                          if _pick(same, l_clients) is not None
                          else f"{len(same)} clients on this PC have this {opk['label']}")
                self.r.ambiguous.append(AmbiguousClient(cid, key, reason))
                continue
            matches = o_index.get(key, [])
            if len(matches) > 1:
                chosen = _pick(matches, o_rows)
                if chosen is None:
                    self.r.ambiguous.append(AmbiguousClient(cid, key, f"{len(matches)} office clients have this {opk['label']}"))
                    continue
                matches = [chosen]
            self._note_unknown(key, lv)
            if matches:
                oid = matches[0]
                self.id_map[cid] = oid
                self._compare(cid, key, oid, row, lv, o_values.get(oid, {}), o_notes.get(oid), now)
            else:
                token = self._token(row.get("client_id_token"))
                self.r.to_insert.append(NewClient(cid, key, token))
                self.id_map[cid] = f"NEW{cid}"
                if self.apply:
                    self.id_map[cid] = self._insert_client(row, lv, lpk, opk, client_cols, token)

    def _values(self, conn) -> dict:
        out: dict = {}
        for cid, col, value in conn.execute("SELECT client_id, column_id, value FROM client_values"):
            out.setdefault(cid, {})[col] = value
        return out

    def _client_rows(self, conn) -> dict:
        cols = _table_cols(conn, "clients")
        return {r[0]: dict(zip(cols, r)) for r in conn.execute(
            "SELECT %s FROM clients ORDER BY id" % ", ".join(f'"{c}"' for c in cols))}

    def _note_unknown(self, key: str, lv: dict) -> None:
        for col_id, label in self.unmapped_labels.items():
            if _text(lv.get(col_id)):
                keys = self.r.unknown_labels.setdefault(label, [])
                if key not in keys:
                    keys.append(key)

    def _compare(self, cid, key, oid, row, lv, ov, o_note, now) -> None:
        labels, updates = [], []
        for lcol, ocol in self.col_map.items():
            a, b = _text(lv.get(lcol)), _text(ov.get(ocol["id"]))
            if a != b:
                labels.append(ocol["label"])
                if a:                               # "take this PC's values" never blanks an office value
                    updates.append((ocol["id"], lv.get(lcol)))
        note_diff = _text(row.get("notes")) != _text(o_note)
        if note_diff:
            labels.append("Notes")
        if not labels:
            self.r.matched_same += 1
            return
        self.r.conflicts.append(ClientConflict(cid, key, oid, sorted(labels, key=str.casefold)))
        if key not in self.take:
            return
        self.r.taken.append(key)
        if not self.apply:
            return
        for col_id, value in updates:
            self.o.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?) "
                           "ON CONFLICT(client_id, column_id) DO UPDATE SET value = excluded.value",
                           (oid, col_id, value))
        if note_diff and _text(row.get("notes")):
            self.o.execute("UPDATE clients SET notes = ? WHERE id = ?", (row.get("notes"), oid))
        self.o.execute("UPDATE clients SET updated_at = ? WHERE id = ?", (now, oid))

    def _token(self, legacy_token) -> str | None:
        t = _text(legacy_token)
        if t.isascii() and t.isdigit():
            keep = int(t) <= self.max_numeric_token     # add_client can never produce it again
        else:
            keep = bool(t) and not _LETTER_TOKEN.match(_norm(t))   # D8 tokens belong to their PC
        if keep and _norm(t) not in self.used_tokens:
            self.used_tokens.add(_norm(t))
            return t
        if not self.letter:
            if self.apply:
                raise ValueError("token_letter is required to give a salvaged client a new token")
            return None
        prefix = _norm(self.letter) + "-"
        top = 0
        for u in self.used_tokens:
            if u.startswith(prefix) and u[len(prefix):].isascii() and u[len(prefix):].isdigit():
                top = max(top, int(u[len(prefix):]))
        token = f"{_norm(self.letter)}-{top + 1}"
        self.used_tokens.add(_norm(token))
        return token

    def _insert_client(self, row, lv, lpk, opk, client_cols, token) -> int:
        vals = dict(row)
        vals["client_id_token"] = token
        cols = [c for c in client_cols if c in vals]
        cur = self.o.execute("INSERT INTO clients (%s) VALUES (%s)" % (
            ", ".join(f'"{c}"' for c in cols), ", ".join("?" * len(cols))), [vals[c] for c in cols])
        oid = cur.lastrowid
        self.o.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                       (oid, opk["id"], _text(lv.get(lpk["id"]))))
        for lcol, ocol in self.col_map.items():
            if _text(lv.get(lcol)):
                self.o.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                               (oid, ocol["id"], lv.get(lcol)))
        for sid in self.l_client_services.get(row["id"], []):
            osid = self._service(sid)
            if osid is not None:
                self.o.execute("INSERT OR IGNORE INTO client_services (client_id, service_id) VALUES (?, ?)", (oid, osid))
        return oid

    def _service(self, legacy_sid, count_unmapped: bool = True):
        if legacy_sid is None:
            return None
        name = self.l_services.get(legacy_sid)
        osid = self.o_service_by_name.get(name) if name is not None else None
        if osid is None and count_unmapped and name:
            self.r.unmapped_services[name] = self.r.unmapped_services.get(name, 0) + 1
        return osid

    def _client_ref(self, legacy_cid):
        """(ok, office id) for a legacy client reference; ok=False if its client wasn't imported."""
        if legacy_cid is None:
            return True, None
        if legacy_cid in self.id_map:
            return True, self.id_map[legacy_cid]
        return False, None

    # ------------------------------------------------------------ audit_log
    def audit(self) -> None:
        seen = {tuple(r) for r in self.o.execute("SELECT ts, actor, action, detail FROM audit_log")}
        ocols = set(_table_cols(self.o, "audit_log"))
        lcols = _table_cols(self.l, "audit_log")
        cols = [c for c in lcols if c not in ("id", "gid") and c in ocols]
        for r in self.l.execute("SELECT %s FROM audit_log ORDER BY id" % ", ".join(f'"{c}"' for c in lcols)):
            row = dict(zip(lcols, r))
            t = (row.get("ts"), row.get("actor"), row.get("action"), row.get("detail"))
            if t in seen:
                self.r.audit_existing += 1
                continue
            seen.add(t)
            self.r.audit_new += 1
            ok, oid = self._client_ref(row.get("client_id"))
            if not ok:
                self.r.audit_unlinked += 1
            row["client_id"] = oid
            row["service_id"] = self._service(row.get("service_id"), count_unmapped=False)
            if self.apply:
                self.o.execute("INSERT INTO audit_log (%s) VALUES (%s)" % (
                    ", ".join(f'"{c}"' for c in cols), ", ".join("?" * len(cols))), [row.get(c) for c in cols])

    # ------------------------------------------------------------ rawPayload.db
    def raw(self) -> None:
        if self.lr is None:
            return
        ltables = _tables(self.lr)
        if "client_raw_containers" in ltables:
            ccols = set(_table_cols(self.lr, "client_raw_containers"))
            conds = [f"trim(coalesce({c}, '')) <> ''" for c in ("notes", "screenshot_path") if c in ccols]
            if conds:
                self.r.container_notes_not_imported = self.lr.execute(
                    "SELECT count(*) FROM client_raw_containers WHERE " + " OR ".join(conds)).fetchone()[0]
        if self.orw is None:
            if {"tracker_dump", "sdc_session_timelines"} & ltables:
                self.r.notes.append("The office has no rawPayload.db yet, so tracker filings and session timelines "
                                    "were not imported (they are still in the old rawPayload.db).")
            return
        otables = _tables(self.orw)
        if "tracker_dump" in ltables and "tracker_dump" in otables:
            self._tracker()
        if "sdc_session_timelines" in ltables and "sdc_session_timelines" in otables:
            self._timelines()

    def _tracker(self) -> None:
        ocols = _table_cols(self.orw, "tracker_dump")
        known = set()
        ogid_map = {}
        if "gid" in set(_table_cols(self.o, "clients")):
            ogid_map = {r[0]: r[1] for r in self.o.execute("SELECT id, gid FROM clients WHERE gid IS NOT NULL")}
        for r in self.orw.execute("SELECT %s FROM tracker_dump" % ", ".join(f'"{c}"' for c in ocols)):
            row = dict(zip(ocols, r))
            if row.get("dataset_key"):
                known.add(row["dataset_key"])
            cid = row.get("client_id")
            known.add(_dataset_key(row, cid, ogid_map.get(cid)))
        lcols = _table_cols(self.lr, "tracker_dump")
        cols = [c for c in lcols if c not in ("id", "gid") and c in set(ocols)]
        for r in self.lr.execute("SELECT %s FROM tracker_dump ORDER BY id" % ", ".join(f'"{c}"' for c in lcols)):
            row = dict(zip(lcols, r))
            ok, oid = self._client_ref(row.get("client_id"))
            if not ok:
                self.r.tracker_skipped_client += 1
                continue
            key = _dataset_key(row, oid, ogid_map.get(oid))
            if key in known:
                self.r.tracker_existing += 1
                continue
            known.add(key)
            self.r.tracker_new += 1
            row.update(client_id=oid, dataset_key=key,
                       service_id=self._service(row.get("service_id"), count_unmapped=False))
            if self.apply:
                self.orw.execute("INSERT INTO tracker_dump (%s) VALUES (%s)" % (
                    ", ".join(f'"{c}"' for c in cols), ", ".join("?" * len(cols))), [row.get(c) for c in cols])

    def _timelines(self) -> None:
        known = {r[0] for r in self.orw.execute("SELECT session_id FROM sdc_session_timelines")}
        ocols = set(_table_cols(self.orw, "sdc_session_timelines"))
        lcols = _table_cols(self.lr, "sdc_session_timelines")
        cols = [c for c in lcols if c in ocols]
        for r in self.lr.execute("SELECT %s FROM sdc_session_timelines ORDER BY start_time, session_id"
                                 % ", ".join(f'"{c}"' for c in lcols)):
            row = dict(zip(lcols, r))
            if row.get("session_id") in known:
                self.r.timelines_existing += 1
                continue
            ok, oid = self._client_ref(row.get("client_id"))
            if not ok:
                self.r.timelines_skipped_client += 1
                continue
            known.add(row.get("session_id"))
            self.r.timelines_new += 1
            row["client_id"] = oid
            if self.apply:
                self.orw.execute("INSERT INTO sdc_session_timelines (%s) VALUES (%s)" % (
                    ", ".join(f'"{c}"' for c in cols), ", ".join("?" * len(cols))), [row.get(c) for c in cols])


def _copy_legacy(legacy_dir: Path, name: str, dest: Path) -> Path | None:
    src = legacy_dir / name
    if not src.is_file():
        return None
    shutil.copy2(src, dest / name)
    for s in SIDECARS:
        if (legacy_dir / (name + s)).is_file():
            shutil.copy2(legacy_dir / (name + s), dest / (name + s))
    return dest / name


def _salvage(legacy_dir, legacy_hex, office_dir, office_hex, *, apply: bool, take_legacy=(),
             token_letter=None) -> SalvageReport:
    legacy_dir, office_dir = Path(legacy_dir), Path(office_dir)
    report = SalvageReport(dry_run=not apply, legacy_dir=str(legacy_dir))
    if not (office_dir / MASTER_DB).is_file():
        raise RejoinError(f"The office database {office_dir / MASTER_DB} is missing.")
    conns = []

    def _track(c):
        conns.append(c)
        return c

    # The legacy files are copied and the copies opened, so they are never written, not even a
    # WAL checkpoint (read-only, §5 P2-8 step 3).
    with tempfile.TemporaryDirectory(prefix="sera_salvage_") as td:
        try:
            lpath = _copy_legacy(legacy_dir, MASTER_DB, Path(td))
            if lpath is None:
                raise RejoinError(f"The old {MASTER_DB} is missing from {legacy_dir}.")
            lconn = _track(_open_checked(lpath, legacy_hex, f"the old {MASTER_DB}", wrong_password=True))
            lconn.execute("PRAGMA query_only = ON;")
            lraw = None
            lrpath = _copy_legacy(legacy_dir, RAW_DB, Path(td))
            if lrpath is not None:
                try:
                    lraw = _track(_open_checked(lrpath, legacy_hex, f"the old {RAW_DB}", wrong_password=False))
                    lraw.execute("PRAGMA query_only = ON;")
                except RejoinError:
                    report.notes.append(f"The old {RAW_DB} can't be opened with the old password, so its "
                                        "tracker filings and timelines were not imported.")

            oconn = _track(_open_checked(office_dir / MASTER_DB, office_hex, f"the office {MASTER_DB}",
                                         wrong_password=False))
            oraw = None
            if (office_dir / RAW_DB).is_file():
                oraw = _track(_open_checked(office_dir / RAW_DB, office_hex, f"the office {RAW_DB}",
                                            wrong_password=False))
            if not apply:
                oconn.execute("PRAGMA query_only = ON;")
                if oraw is not None:
                    oraw.execute("PRAGMA query_only = ON;")

            job = _Salvage(lconn, lraw, oconn, oraw, apply=apply, take_legacy=take_legacy,
                           token_letter=token_letter, report=report)
            if apply:
                # master.db, then rawPayload.db. A crash in between is repaired by running the
                # salvage again: what the first transaction wrote now matches and isn't repeated.
                oconn.execute("BEGIN IMMEDIATE;")
                try:
                    job.clients()
                    job.audit()
                    oconn.execute("COMMIT;")
                except BaseException:
                    oconn.execute("ROLLBACK;")
                    raise
                if oraw is not None:
                    oraw.execute("BEGIN IMMEDIATE;")
                    try:
                        job.raw()
                        oraw.execute("COMMIT;")
                    except BaseException:
                        oraw.execute("ROLLBACK;")
                        raise
                else:
                    job.raw()
            else:
                job.clients()
                job.audit()
                job.raw()
        finally:
            for c in conns:
                try:
                    c.close()
                except Exception:
                    pass
    _log.info("salvage %s: %d clients added, %d conflicts (%d taken), %d without PK, %d ambiguous, "
              "%d audit, %d tracker, %d timelines", "applied" if apply else "dry run", len(report.to_insert),
              len(report.conflicts), len(report.taken), len(report.no_pk), len(report.ambiguous),
              report.audit_new, report.tracker_new, report.timelines_new)
    return report


def plan_salvage(legacy_dir, legacy_hex: str, office_dir, office_hex: str, *, take_legacy=(),
                 token_letter: str | None = None) -> SalvageReport:
    """Dry run: what ``apply_salvage`` would do. Writes nothing anywhere."""
    return _salvage(legacy_dir, legacy_hex, office_dir, office_hex, apply=False,
                    take_legacy=take_legacy, token_letter=token_letter)


def apply_salvage(legacy_dir, legacy_hex: str, office_dir, office_hex: str, *, take_legacy=(),
                  token_letter: str | None) -> SalvageReport:
    """Import what only the legacy DB has into the office DB. ``take_legacy``: PK values of
    conflicting clients whose legacy values should replace the office's (blank legacy values
    never erase an office value). ``token_letter``: this PC's D8 letter, for salvaged clients
    whose old token is already used in the office."""
    return _salvage(legacy_dir, legacy_hex, office_dir, office_hex, apply=True,
                    take_legacy=take_legacy, token_letter=token_letter)


# ---------------------------------------------------------------- step 3 after a rejoin

def _office_hex(app: Path) -> str:
    import hmac
    office = sera_keys.load_office(app)
    if office is None:
        raise RejoinError("This PC has no office key yet.")
    dek = sera_keys.load_dek(app)
    if not hmac.compare_digest(sera_keys.key_id(dek), office.key_id):
        raise RejoinError("The stored office key doesn't match office.json.")
    return dek.hex()


def _token_letter(app: Path, state: dict, office_hex: str) -> str | None:
    if state.get("token_letter"):
        return state["token_letter"]
    # A crash between pairing and writing the state: read this PC's member record.
    import sync_admin
    import sync_identity
    office = sera_keys.load_office(app)
    ident = sync_identity.load_device_identity(app)
    if office is None or ident is None or not office.admin_pubkey:
        return None
    conn = _connect(app / MASTER_DB, office_hex)
    try:
        rec = sync_admin.get_member(conn, ident.device_id, office.admin_pubkey)
    except Exception:
        rec = None
    finally:
        conn.close()
    return rec.get("token_letter") if rec else None


def salvage_after_rejoin(app_dir, *, dry_run: bool, password: str | None = None, take_legacy=()) -> SalvageReport:
    """Step 3–4 on a rejoined PC, using ``incoming/rejoin_state.json``.

    The old password comes from the saved ``legacy/<ts>/sera.key``, or ``password``. Raises
    ``LegacyPasswordNeeded`` if neither opens the old DB. When applied, the report is written to
    ``logs/salvage-<ts>.txt`` and the rejoin is finished (the legacy files stay where they are).
    """
    app = Path(app_dir)
    state = read_state(app)
    if state is None:
        raise RejoinError("No rejoin is in progress on this PC.")
    legacy_dir = Path(state["legacy_dir"])
    if password:
        try:
            legacy_hex = legacy_hex_key(legacy_dir, password)
        except sera_keys.WrongPassword:
            raise LegacyPasswordNeeded("That password doesn't open this PC's old database.") from None
    else:
        legacy_hex = saved_legacy_hex_key(legacy_dir)
        if legacy_hex is None:
            raise LegacyPasswordNeeded("Enter this PC's old master password to import its data.")
    office_hex = _office_hex(app)
    if dry_run:
        return plan_salvage(legacy_dir, legacy_hex, app, office_hex, take_legacy=take_legacy,
                            token_letter=_token_letter(app, state, office_hex))
    letter = _token_letter(app, state, office_hex)
    report = apply_salvage(legacy_dir, legacy_hex, app, office_hex, take_legacy=take_legacy, token_letter=letter)
    report.report_path = str(write_salvage_report(app, report))
    finish_rejoin(app)
    return report


def skip_salvage(app_dir) -> Path | None:
    """The user chose not to import. Writes a short report and finishes the rejoin."""
    app = Path(app_dir)
    state = read_state(app)
    finish_rejoin(app)
    if state is None:
        return None
    report = SalvageReport(dry_run=True, legacy_dir=state["legacy_dir"])
    report.notes.append("The user chose not to import this PC's old data. It is still in the folder above.")
    return write_salvage_report(app, report)
