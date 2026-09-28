"""
Tests for core/sgt_i/sera_data.py (blueprint 14.4 step 8). Every PAN, GSTIN and name here is
fictional. Uses a temp SQLCipher-encrypted DB pair, built with the same table shapes database.py
creates, to prove the accessor is genuinely read-only and matches its label conventions.
"""

import os

import pytest

sqlcipher3 = pytest.importorskip("sqlcipher3.dbapi2")

from core.sgt_i import sera_data as sd

HEX_KEY = "00" * 32


def _connect(path):
    conn = sqlcipher3.connect(path)
    conn.execute("PRAGMA key = \"x'%s'\";" % HEX_KEY)
    return conn


def _make_master_db(path):
    conn = _connect(path)
    conn.execute("""CREATE TABLE mcl_columns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
        field_type TEXT NOT NULL DEFAULT 'text', is_identity INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT, notes TEXT, created_at TEXT, updated_at TEXT,
        is_archived INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE client_values (
        client_id INTEGER NOT NULL, column_id INTEGER NOT NULL, value TEXT,
        PRIMARY KEY (client_id, column_id))""")
    conn.execute("""CREATE TABLE services (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE client_services (
        client_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
        PRIMARY KEY (client_id, service_id))""")

    cols = [("NAME OF COMPANY", 2), ("NAME OF PROPRIETOR", 3), ("GSTIN", 4),
            ("PAN", 5), ("GST_Password", 9)]
    col_ids = {}
    for label, order in cols:
        cur = conn.execute("INSERT INTO mcl_columns (label, sort_order) VALUES (?, ?)",
                            (label, order))
        col_ids[label] = cur.lastrowid

    def add_client(cid, name, pan, gstin, archived=0):
        conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived) "
                      "VALUES (?, '2026-01-01', '2026-01-01', ?)", (cid, archived))
        conn.execute("INSERT INTO client_values VALUES (?, ?, ?)",
                      (cid, col_ids["NAME OF COMPANY"], name))
        conn.execute("INSERT INTO client_values VALUES (?, ?, ?)",
                      (cid, col_ids["PAN"], pan))
        conn.execute("INSERT INTO client_values VALUES (?, ?, ?)",
                      (cid, col_ids["GSTIN"], gstin))
        # A password-labelled column must never be mistaken for PAN/GSTIN/name.
        conn.execute("INSERT INTO client_values VALUES (?, ?, ?)",
                      (cid, col_ids["GST_Password"], "hunter2"))

    add_client(1, "Alpha Traders", "ABCDE1234F", "27ABCDE1234F1Z0")
    add_client(2, "Beta Textiles", "PQRSX9876K", "07PQRSX9876K1ZS")
    add_client(3, "Gone Client", "ZZYYX0001A", "", archived=1)

    conn.execute("INSERT INTO services (id, name) VALUES (1, 'GSTR-3B')")
    conn.execute("INSERT INTO client_services VALUES (1, 1)")
    conn.commit()
    conn.close()


def _make_raw_db(path):
    conn = _connect(path)
    conn.execute("""CREATE TABLE tracker_dump (
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER, portal TEXT,
        period_label TEXT, arn_number TEXT, status TEXT)""")
    conn.execute("INSERT INTO tracker_dump (client_id, portal, period_label, arn_number, status) "
                 "VALUES (1, 'GST', 'August', 'AA270826000001Z0', 'submitted')")
    conn.commit()
    conn.close()


@pytest.fixture()
def sera(tmp_path):
    master = str(tmp_path / "master.db")
    raw = str(tmp_path / "rawPayload.db")
    _make_master_db(master)
    _make_raw_db(raw)
    return sd.SeraData(master, HEX_KEY, raw, cache_ttl_sec=1000.0)


# ── the accessor: clients, tracker rows, due services ────────────────────────────────────────────

def test_clients_reads_pan_gstin_name_and_skips_archived(sera):
    clients = {c.client_id: c for c in sera.clients()}
    assert set(clients) == {1, 2}
    assert clients[1].pan == "ABCDE1234F"
    assert clients[1].gstin == "27ABCDE1234F1Z0"
    assert clients[1].name == "Alpha Traders"


def test_find_by_pan_and_gstin(sera):
    assert sera.find_by_pan("abcde1234f").client_id == 1
    assert sera.find_by_gstin("07PQRSX9876K1ZS").client_id == 2
    assert sera.find_by_pan("NOSUCH0000X") is None


def test_tracker_rows_and_due_services(sera):
    rows = sera.tracker_rows(client_id=1)
    assert len(rows) == 1
    assert rows[0].portal == "GST" and rows[0].status == "submitted"
    assert sera.tracker_rows(client_id=2) == []
    due = sera.due_services(client_id=1)
    assert due == [sd.DueService(client_id=1, service="GSTR-3B")]


def test_cache_is_not_refreshed_within_ttl(tmp_path):
    master = str(tmp_path / "master.db")
    raw = str(tmp_path / "rawPayload.db")
    _make_master_db(master)
    _make_raw_db(raw)
    clock = [0.0]
    data = sd.SeraData(master, HEX_KEY, raw, cache_ttl_sec=100.0, now=lambda: clock[0])
    first = data.clients()
    os.remove(master)          # a second read would now fail
    clock[0] += 10.0           # still inside the TTL
    assert data.clients() == first


def test_never_writes_to_either_database(sera):
    """query_only + mode=ro: an attempted write via the accessor's own connection must fail."""
    sera.clients()
    conn = sera._connect(sera._db_path)
    try:
        with pytest.raises(sqlcipher3.OperationalError):
            conn.execute("UPDATE clients SET notes = 'x' WHERE id = 1")
    finally:
        conn.close()


# ── OCR constraint correction ─────────────────────────────────────────────────────────────────────

def test_gstin_recovered_exactly_by_its_checksum():
    # 27ABCDE1234F1Z0 with the state code's '2' misread as 'Z' (Z/2 confusion).
    valid = "27ABCDE1234F1Z0"
    broken = "Z7ABCDE1234F1Z0"
    assert sd.gstin_checksum_ok(valid)
    assert sd.recover_gstin(broken) == valid


def test_gstin_already_valid_is_never_touched():
    assert sd.recover_gstin("27ABCDE1234F1Z0") is None


def test_gstin_known_client_still_confirms_the_checksum_recovery():
    valid = "27ABCDE1234F1Z0"
    broken = "Z7ABCDE1234F1Z0"
    assert sd.recover_gstin(broken, known_gstins=[valid]) == valid


def test_pan_has_no_checksum_so_recovery_needs_a_known_client():
    broken = "ABCDEI234F"     # '1' misread as 'I'
    assert sd.recover_pan(broken) is None                      # no client list: no correction
    assert sd.recover_pan(broken, known_pans=["ABCDE1234F"]) == "ABCDE1234F"


def test_pan_already_shape_valid_is_never_touched():
    assert sd.recover_pan("ABCDE1234F", known_pans=["ABCDE1234F"]) is None


def test_seradata_recover_pan_and_gstin_use_the_client_list(sera):
    assert sera.recover_pan("ABCDEI234F") == "ABCDE1234F"
    assert sera.recover_gstin("Z7ABCDE1234F1Z0") == "27ABCDE1234F1Z0"


# ── name <-> PAN cross-check and masked-value confirmation ────────────────────────────────────────

def test_name_pan_mismatch_flags_a_wrong_client_read(sera):
    assert sera.name_pan_mismatch("Someone Else Pvt Ltd", "ABCDE1234F") is True
    assert sera.name_pan_mismatch("Alpha Traders", "ABCDE1234F") is False
    assert sera.name_pan_mismatch("ALPHA TRADERS PVT LTD", "ABCDE1234F") is False


def test_name_pan_mismatch_is_false_with_nothing_to_compare(sera):
    assert sera.name_pan_mismatch("", "ABCDE1234F") is False
    assert sera.name_pan_mismatch("Anyone", "NOSUCH0000X") is False


def test_masked_confirms_matches_unmasked_characters_only():
    assert sd.masked_confirms("98XXXXXX12", "9812345612")
    assert not sd.masked_confirms("98XXXXXX12", "9712345612")
    assert not sd.masked_confirms("98XXXXXX1", "9812345612")


def test_masked_confirms_never_needs_to_store_either_value():
    # Purely functional: same inputs, same output, nothing retained between calls.
    a = sd.masked_confirms("XX3456", "123456")
    b = sd.masked_confirms("XX3456", "123456")
    assert a == b is True
