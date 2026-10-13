"""
sync_migrate.py
---------------
P4-2: only ``check_legacy_password`` survives here, for the P2-8 rejoin/salvage importer
(``ui/dialogs/rejoin_office_dialog.py``), which is the sole remaining reader of a PC's own
``sera.key``/``sera.salt`` (blueprint §5 P4-2). It opens a rejoining PC's *own* legacy
``master.db`` with the password the user types, purely to prove the password is right before
the PC's files are moved aside into ``legacy/<ts>/`` and salvaged.

The WP P1-4 migration engine that used to live here (convert this PC from the legacy
password+salt key to an office key) was removed in P4-2: every real PC already has an office
key since Sera Sync v3 went live (P3-9), so the "Convert to office key" feature and its
sera.key/sera.salt reads no longer have a purpose.
"""

from __future__ import annotations

from pathlib import Path

import sera_keys

MASTER_DB = "master.db"


class MigrationError(Exception):
    """Raised by ``_legacy_hex_key`` when the legacy database can't be checked at all
    (missing file, locked by another program) -- as opposed to a wrong password."""


def _connect(path: Path, hex_key: str):
    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    return conn


def _legacy_hex_key(app: Path, password: str) -> str:
    """Derive the legacy key and prove it opens master.db. Raises WrongPassword / MigrationError."""
    import sqlcipher3.dbapi2 as sqlite3
    import security
    master = app / MASTER_DB
    salt = app / security.SALT_FILE
    if not master.is_file():
        raise MigrationError("master.db not found in %s" % app)
    if not salt.is_file():
        raise MigrationError("%s not found in %s" % (security.SALT_FILE, app))
    if not password:
        raise sera_keys.WrongPassword("wrong master password")
    hex_key = security.derive_key_hex(password, security.load_salt(str(salt)))
    conn = _connect(master, hex_key)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise MigrationError("master.db is in use by another program") from None
        raise sera_keys.WrongPassword("wrong master password") from None
    except sqlite3.DatabaseError:
        raise sera_keys.WrongPassword("wrong master password") from None
    finally:
        conn.close()
    return hex_key


def check_legacy_password(app_dir, password: str) -> bool:
    """False only for a wrong password. Raises MigrationError if it can't be checked (locked, file missing)."""
    try:
        _legacy_hex_key(Path(app_dir), password)
        return True
    except sera_keys.WrongPassword:
        return False
