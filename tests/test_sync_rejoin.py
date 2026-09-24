"""Tests for sync_rejoin.py (Sera Sync v3, WP P2-8): rejoin an existing legacy PC and salvage
what only it has.

Every PC lives under pytest's tmp_path; the real data folder is never touched (§0 rule 2).
All client data here is invented (§0 rule 12).
"""

from __future__ import annotations

import ast
import gc
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
import sqlcipher3.dbapi2 as sqlite3

import security
import sera_keys
import sync_rejoin
from database import SeraDatabase
from sync_rejoin import RejoinError, SalvageError

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

LEGACY_PW = "OldBranch#2026"
MASTER_PW = "OfficeMaster#2026"
OFFICE_HEX = "5a" * 32

# Invented clients. PANs follow the format only.
PAN_SHARED_SAME = "AAAPA1111A"      # on both PCs, same values
PAN_SHARED_DIFF = "BBBPB2222B"      # on both PCs, different values
PAN_LEGACY_ONLY = "CCCPC3333C"      # only on the legacy PC
PAN_OFFICE_ONLY = "DDDPD4444D"      # only on the office PC
FAKE_EMAIL = "someone@example.invalid"


# ------------------------------------------------------------------ helpers

def _open(path, hex_key):
    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    return conn


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cols(db: SeraDatabase) -> dict:
    return {c["label"]: c["id"] for c in db.get_mcl_columns()}


def _service_ids(db: SeraDatabase) -> dict:
    with db._connect() as conn:
        return {name: sid for sid, name in conn.execute("SELECT id, name FROM services")}


def _close(db):
    del db
    gc.collect()


def _checkpoint(folder: Path, hex_key: str) -> None:
    """Fold WAL content into the main files, so file hashes are a fair 'nothing changed' check."""
    for name in ("master.db", "rawPayload.db"):
        p = folder / name
        if p.exists():
            conn = _open(p, hex_key)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            finally:
                conn.close()


def _dump(folder: Path, hex_key: str) -> dict:
    """Every row of every table in both DBs, for a logical before/after comparison."""
    out = {}
    for name in ("master.db", "rawPayload.db"):
        p = folder / name
        if not p.exists():
            continue
        conn = _open(p, hex_key)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for t in tables:
                out[(name, t)] = sorted(map(repr, conn.execute(f'SELECT * FROM "{t}"').fetchall()))
        finally:
            conn.close()
    return out


def _add_tracker(db: SeraDatabase, client_id, portal, period, *, pan=None, form="GSTR3B", arn="AA0000000000001",
                 unassigned=None, notes=None):
    payload = {"filing_type": form}
    if pan:
        payload["pan"] = pan
    with db._connect_raw() as conn:
        cand = unassigned or (pan or (f"CLI_{client_id}" if client_id else "UNKNOWN"))
        key = SeraDatabase.compute_dataset_key(portal, cand, form, period)
        conn.execute(
            "INSERT INTO tracker_dump (client_id, unassigned_identity, service_id, portal, period_label, arn_number,"
            " status, raw_payload_json, captured_by, created_at, dataset_key, notes)"
            " VALUES (?, ?, NULL, ?, ?, ?, 'submitted', ?, 'User 1', '2026-08-01T10:00:00', ?, ?)",
            (client_id, unassigned, portal, period, arn, json.dumps(payload), key, notes),
        )


def _add_timeline(db: SeraDatabase, session_id, client_id, pan):
    with db._connect_raw() as conn:
        conn.execute(
            "INSERT INTO sdc_session_timelines (session_id, client_id, pan, client_name, portal, start_time,"
            " timeline_json, last_updated) VALUES (?, ?, ?, 'INVENTED CO', 'GST', '2026-08-01T10:00:00', '[]',"
            " '2026-08-01T10:05:00')",
            (session_id, client_id, pan),
        )


def _add_audit(db: SeraDatabase, ts, action, detail, client_id=None):
    with db._connect() as conn:
        conn.execute("INSERT INTO audit_log (ts, actor, action, client_id, detail) VALUES (?, 'User 1', ?, ?, ?)",
                     (ts, action, client_id, detail))


@pytest.fixture
def pcs(tmp_path):
    """A diverged pair: ``legacy`` (password + salt key) and ``office`` (DEK), both with data."""
    legacy = tmp_path / "legacy_pc"
    office = tmp_path / "office_pc"
    legacy.mkdir()
    office.mkdir()

    security.generate_and_save_salt(str(legacy / security.SALT_FILE))
    legacy_hex = security.derive_key_hex(LEGACY_PW, security.load_salt(str(legacy / security.SALT_FILE)))
    (legacy / "sera.key").write_text(LEGACY_PW, encoding="utf-8")

    ldb = SeraDatabase(str(legacy / "master.db"), legacy_hex, defer_startup_maintenance=True)
    odb = SeraDatabase(str(office / "master.db"), OFFICE_HEX, defer_startup_maintenance=True)
    lc, oc = _cols(ldb), _cols(odb)
    ls, os_ = _service_ids(ldb), _service_ids(odb)

    # A column only the legacy PC has (its values can't be imported).
    ldb.create_mcl_column("LEGACY NOTE COLUMN", "text")
    lc = _cols(ldb)

    # Office clients
    o_same = odb.add_client({oc["PAN"]: PAN_SHARED_SAME, oc["NAME OF COMPANY"]: "SAME CO"}, "", [os_["GST"]])
    o_diff = odb.add_client({oc["PAN"]: PAN_SHARED_DIFF, oc["NAME OF COMPANY"]: "OFFICE NAME",
                             oc["GSTIN"]: "07BBBPB2222B1Z1"}, "office note", [])
    o_only = odb.add_client({oc["PAN"]: PAN_OFFICE_ONLY, oc["NAME OF COMPANY"]: "OFFICE ONLY CO"}, "", [])

    # Legacy clients (different local ids on purpose: one extra client first)
    l_same = ldb.add_client({lc["PAN"]: PAN_SHARED_SAME.lower() + " ", lc["NAME OF COMPANY"]: "SAME CO"},
                            "", [ls["GST"]])
    l_only = ldb.add_client({lc["PAN"]: PAN_LEGACY_ONLY, lc["NAME OF COMPANY"]: "LEGACY ONLY CO",
                             lc["EMAIL"]: FAKE_EMAIL, lc["LEGACY NOTE COLUMN"]: "only here"},
                            "legacy note", [ls["Income Tax"], ls["EPFO"]])
    l_diff = ldb.add_client({lc["PAN"]: PAN_SHARED_DIFF, lc["NAME OF COMPANY"]: "LEGACY NAME",
                             lc["GSTIN"]: "07BBBPB2222B1Z1"}, "office note", [])
    # A legacy client without a PAN can't be matched safely (stop and ask): inserted by hand,
    # because add_client refuses an empty internal PK.
    with ldb._connect() as conn:
        cur = conn.execute("INSERT INTO clients (notes, created_at, updated_at, client_id_token)"
                           " VALUES ('', '2026-01-01', '2026-01-01', 'NOPAN-1')")
        l_nopan = cur.lastrowid
        conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, 'NO PAN CO')",
                     (l_nopan, lc["NAME OF COMPANY"]))

    # Audit: one shared row, two legacy-only rows (add_client's own "create" rows removed first)
    for db in (ldb, odb):
        with db._connect() as conn:
            conn.execute("DELETE FROM audit_log")
    _add_audit(odb, "2026-07-01T09:00:00", "login", "shared entry")
    _add_audit(ldb, "2026-07-01T09:00:00", "login", "shared entry")
    _add_audit(ldb, "2026-07-02T09:00:00", "update", "legacy only entry", client_id=l_only)
    _add_audit(ldb, "2026-07-03T09:00:00", "export", "legacy only entry 2")

    # Tracker: shared filing (PAN in payload), legacy-only filings, one for the PAN-less client
    _add_tracker(odb, o_same, "GST", "JUL_2026", pan=PAN_SHARED_SAME)
    _add_tracker(ldb, l_same, "GST", "JUL_2026", pan=PAN_SHARED_SAME)
    _add_tracker(ldb, l_same, "GST", "AUG_2026", pan=PAN_SHARED_SAME, arn="AA0000000000002")
    _add_tracker(ldb, l_only, "Income Tax", "AY_2026_27", form="ITR-1", arn="AA0000000000003", notes="hand note")
    _add_tracker(ldb, l_nopan, "GST", "JUL_2026", arn="AA0000000000004")

    # Timelines: one shared, one legacy-only
    _add_timeline(odb, "sess-shared", o_same, PAN_SHARED_SAME)
    _add_timeline(ldb, "sess-shared", l_same, PAN_SHARED_SAME)
    _add_timeline(ldb, "sess-legacy", l_only, PAN_LEGACY_ONLY)

    ids = {"o_same": o_same, "o_diff": o_diff, "o_only": o_only,
           "l_same": l_same, "l_only": l_only, "l_diff": l_diff, "l_nopan": l_nopan}
    _close(ldb)
    _close(odb)
    _checkpoint(legacy, legacy_hex)
    _checkpoint(office, OFFICE_HEX)
    return {"legacy": legacy, "legacy_hex": legacy_hex, "office": office, "ids": ids}


def _plan(pcs, **kw):
    return sync_rejoin.plan_salvage(pcs["legacy"], pcs["legacy_hex"], pcs["office"], OFFICE_HEX, **kw)


def _apply(pcs, **kw):
    kw.setdefault("token_letter", "B")
    return sync_rejoin.apply_salvage(pcs["legacy"], pcs["legacy_hex"], pcs["office"], OFFICE_HEX, **kw)


def _office_client(office: Path, pan: str) -> dict | None:
    conn = _open(office / "master.db", OFFICE_HEX)
    try:
        pk = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()[0]
        row = conn.execute("SELECT c.id, c.notes, c.client_id_token FROM clients c JOIN client_values v"
                           " ON v.client_id = c.id AND v.column_id = ? WHERE UPPER(TRIM(v.value)) = ?",
                           (pk, pan)).fetchone()
        if row is None:
            return None
        values = {label: value for label, value in conn.execute(
            "SELECT m.label, v.value FROM client_values v JOIN mcl_columns m ON m.id = v.column_id"
            " WHERE v.client_id = ?", (row[0],))}
        services = sorted(r[0] for r in conn.execute(
            "SELECT s.name FROM client_services cs JOIN services s ON s.id = cs.service_id WHERE cs.client_id = ?",
            (row[0],)))
        return {"id": row[0], "notes": row[1], "token": row[2], "values": values, "services": services}
    finally:
        conn.close()


# ------------------------------------------------------------------ §5 Accept

def test_salvage_dry_run_changes_nothing(pcs):
    before_hash = {n: _sha(pcs["office"] / n) for n in ("master.db", "rawPayload.db")}
    legacy_hash = {n: _sha(pcs["legacy"] / n) for n in ("master.db", "rawPayload.db", "sera.salt", "sera.key")}
    before = _dump(pcs["office"], OFFICE_HEX)

    report = _plan(pcs)

    assert report.dry_run is True
    assert [c.key for c in report.to_insert] == [PAN_LEGACY_ONLY]
    assert [c.key for c in report.conflicts] == [PAN_SHARED_DIFF]
    assert report.matched_same == 1
    assert report.audit_new == 2
    assert report.tracker_new == 2          # AUG_2026 + the ITR filing
    assert report.timelines_new == 1
    assert _dump(pcs["office"], OFFICE_HEX) == before
    assert {n: _sha(pcs["office"] / n) for n in ("master.db", "rawPayload.db")} == before_hash
    assert {n: _sha(pcs["legacy"] / n) for n in legacy_hash} == legacy_hash
    assert not (pcs["office"] / "logs").exists()


def test_salvage_imports_unmatched_clients(pcs):
    report = _apply(pcs)
    assert report.dry_run is False

    c = _office_client(pcs["office"], PAN_LEGACY_ONLY)
    assert c is not None
    assert c["values"]["NAME OF COMPANY"] == "LEGACY ONLY CO"
    assert c["values"]["EMAIL"] == FAKE_EMAIL
    assert "LEGACY NOTE COLUMN" not in c["values"]            # unknown label: not imported...
    assert report.unknown_labels == {"LEGACY NOTE COLUMN": [PAN_LEGACY_ONLY]}   # ...but listed
    assert c["notes"] == "legacy note"
    assert c["services"] == ["EPFO", "Income Tax"]            # mapped by service name

    # Its tracker row and timeline point at the office's id for it, not the legacy id.
    conn = _open(pcs["office"] / "rawPayload.db", OFFICE_HEX)
    try:
        rows = conn.execute("SELECT client_id, notes FROM tracker_dump WHERE arn_number = 'AA0000000000003'").fetchall()
        assert rows == [(c["id"], "hand note")]
        assert conn.execute("SELECT client_id FROM sdc_session_timelines WHERE session_id = 'sess-legacy'"
                            ).fetchone() == (c["id"],)
    finally:
        conn.close()

    # The office-only and shared clients are still there, once each.
    assert _office_client(pcs["office"], PAN_OFFICE_ONLY) is not None
    conn = _open(pcs["office"] / "master.db", OFFICE_HEX)
    try:
        assert conn.execute("SELECT count(*) FROM clients").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM audit_log WHERE detail LIKE 'legacy only entry%'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM audit_log WHERE detail = 'shared entry'").fetchone()[0] == 1
        linked = conn.execute("SELECT client_id FROM audit_log WHERE detail = 'legacy only entry'").fetchone()[0]
        assert linked == c["id"]
    finally:
        conn.close()


def test_salvage_keeps_office_value_on_conflict_by_default(pcs):
    report = _apply(pcs)
    assert [c.key for c in report.conflicts] == [PAN_SHARED_DIFF]
    assert report.conflicts[0].labels == ["NAME OF COMPANY"]
    assert report.taken == []
    c = _office_client(pcs["office"], PAN_SHARED_DIFF)
    assert c["values"]["NAME OF COMPANY"] == "OFFICE NAME"


# ------------------------------------------------------------------ more salvage behaviour

def test_take_this_pcs_values_for_a_chosen_client(pcs):
    report = _apply(pcs, take_legacy={PAN_SHARED_DIFF.lower()})
    assert report.taken == [PAN_SHARED_DIFF]
    c = _office_client(pcs["office"], PAN_SHARED_DIFF)
    assert c["values"]["NAME OF COMPANY"] == "LEGACY NAME"
    assert c["values"]["GSTIN"] == "07BBBPB2222B1Z1"


def test_take_legacy_never_blanks_an_office_value(pcs):
    conn = _open(pcs["legacy"] / "master.db", pcs["legacy_hex"])
    try:
        conn.execute("DELETE FROM client_values WHERE client_id = ? AND column_id ="
                     " (SELECT id FROM mcl_columns WHERE label = 'GSTIN')", (pcs["ids"]["l_diff"],))
        conn.commit()
    finally:
        conn.close()
    report = _plan(pcs)
    assert report.conflicts[0].labels == ["GSTIN", "NAME OF COMPANY"]
    _apply(pcs, take_legacy={PAN_SHARED_DIFF})
    c = _office_client(pcs["office"], PAN_SHARED_DIFF)
    assert c["values"]["GSTIN"] == "07BBBPB2222B1Z1"
    assert c["values"]["NAME OF COMPANY"] == "LEGACY NAME"


def test_client_without_pk_is_listed_not_inserted(pcs):
    report = _apply(pcs)
    assert [(n.legacy_id, n.token) for n in report.no_pk] == [(pcs["ids"]["l_nopan"], "NOPAN-1")]
    conn = _open(pcs["office"] / "master.db", OFFICE_HEX)
    try:
        assert conn.execute("SELECT count(*) FROM clients WHERE client_id_token = 'NOPAN-1'").fetchone()[0] == 0
    finally:
        conn.close()
    # ...and its tracker row isn't guessed onto some other client.
    assert report.tracker_skipped_client == 1
    conn = _open(pcs["office"] / "rawPayload.db", OFFICE_HEX)
    try:
        assert conn.execute("SELECT count(*) FROM tracker_dump WHERE arn_number = 'AA0000000000004'").fetchone()[0] == 0
    finally:
        conn.close()


def test_duplicate_pk_is_ambiguous_and_not_imported(pcs):
    conn = _open(pcs["legacy"] / "master.db", pcs["legacy_hex"])
    try:
        pk = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()[0]
        cur = conn.execute("INSERT INTO clients (notes, created_at, updated_at, client_id_token)"
                           " VALUES ('', '2026-01-01', '2026-01-01', 'DUP-1')")
        conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                     (cur.lastrowid, pk, PAN_LEGACY_ONLY))
        conn.commit()
    finally:
        conn.close()
    report = _apply(pcs)
    assert sorted(a.key for a in report.ambiguous) == [PAN_LEGACY_ONLY, PAN_LEGACY_ONLY]
    assert report.to_insert == []
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY) is None


def test_salvage_is_idempotent(pcs):
    _apply(pcs)
    after_first = _dump(pcs["office"], OFFICE_HEX)
    second = _apply(pcs)
    assert second.to_insert == [] and second.audit_new == 0
    assert second.tracker_new == 0 and second.timelines_new == 0
    assert _dump(pcs["office"], OFFICE_HEX) == after_first


def test_salvage_never_writes_the_legacy_files(pcs):
    legacy_hash = {n: _sha(pcs["legacy"] / n) for n in ("master.db", "rawPayload.db", "sera.salt", "sera.key")}
    _apply(pcs, take_legacy={PAN_SHARED_DIFF})
    assert {n: _sha(pcs["legacy"] / n) for n in legacy_hash} == legacy_hash
    assert sorted(p.name for p in pcs["legacy"].iterdir()) == ["master.db", "rawPayload.db", "sera.key", "sera.salt"]


def test_colliding_legacy_token_gets_a_letter_token(pcs):
    # The legacy-only client's numeric token is already used by an office client.
    conn = _open(pcs["office"] / "master.db", OFFICE_HEX)
    try:
        used = conn.execute("SELECT client_id_token FROM clients WHERE id = ?", (pcs["ids"]["o_only"],)).fetchone()[0]
        conn.execute("INSERT INTO clients (notes, created_at, updated_at, client_id_token)"
                     " VALUES ('', '2026-01-01', '2026-01-01', 'B-4')")
        conn.commit()
    finally:
        conn.close()
    conn = _open(pcs["legacy"] / "master.db", pcs["legacy_hex"])
    try:
        conn.execute("UPDATE clients SET client_id_token = ? WHERE id = ?", (used, pcs["ids"]["l_only"]))
        conn.commit()
    finally:
        conn.close()
    report = _apply(pcs, token_letter="B")
    c = _office_client(pcs["office"], PAN_LEGACY_ONLY)
    assert c["token"] == "B-5"
    assert report.to_insert[0].token == "B-5"


def _set_token(folder, hex_key, client_id, token):
    conn = _open(folder / "master.db", hex_key)
    try:
        conn.execute("UPDATE clients SET client_id_token = ? WHERE id = ?", (token, client_id))
        conn.commit()
    finally:
        conn.close()


def test_free_numeric_token_at_or_below_office_ids_is_kept(pcs):
    # Office ids go up to 3 and "3" is free: add_client can never hand out "3" again.
    _set_token(pcs["office"], OFFICE_HEX, pcs["ids"]["o_only"], "Z-OLD")
    _set_token(pcs["legacy"], pcs["legacy_hex"], pcs["ids"]["l_only"], "3")
    _apply(pcs)
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY)["token"] == "3"


def test_numeric_token_above_office_ids_gets_a_letter_token(pcs):
    """Review B1: a kept token "977" would be made again by add_client once ids reach 977."""
    _set_token(pcs["legacy"], pcs["legacy_hex"], pcs["ids"]["l_only"], "977")
    report = _apply(pcs, token_letter="B")
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY)["token"] == "B-1"
    assert report.to_insert[0].token == "B-1"


def test_non_numeric_token_is_kept_but_not_another_pcs_letter_token(pcs):
    _set_token(pcs["legacy"], pcs["legacy_hex"], pcs["ids"]["l_only"], "OLD/77")
    _apply(pcs)
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY)["token"] == "OLD/77"


def test_letter_shaped_legacy_token_is_replaced(pcs):
    _set_token(pcs["legacy"], pcs["legacy_hex"], pcs["ids"]["l_only"], "C-7")
    _apply(pcs, token_letter="B")
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY)["token"] == "B-1"


@pytest.mark.parametrize("legacy_token", ["3", "5", "977"])
def test_add_client_after_salvage_never_duplicates_a_token(pcs, legacy_token):
    """Review B1: salvage, then keep adding clients until the ids pass the old token."""
    _set_token(pcs["office"], OFFICE_HEX, pcs["ids"]["o_only"], "Z-OLD")   # frees "3"
    _set_token(pcs["legacy"], pcs["legacy_hex"], pcs["ids"]["l_only"], legacy_token)
    _apply(pcs, token_letter="B")
    odb = SeraDatabase(str(pcs["office"] / "master.db"), OFFICE_HEX, defer_startup_maintenance=True)
    try:
        pk = _cols(odb)["PAN"]
        for i in range(8):
            odb.add_client({pk: f"EEEPE{i:04d}E"}, "", [])
        with odb._connect() as conn:
            tokens = [t.upper() for (t,) in conn.execute("SELECT client_id_token FROM clients")]
            top = conn.execute("SELECT max(id) FROM clients").fetchone()[0]
    finally:
        _close(odb)
    assert top >= 8
    assert len(tokens) == len(set(tokens)), sorted(tokens)


# ------------------------------------------------------------------ archived duplicates (review should-fix 1)

def _add_archived_copy(folder, hex_key, pan, token):
    conn = _open(folder / "master.db", hex_key)
    try:
        pk = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()[0]
        cur = conn.execute("INSERT INTO clients (notes, created_at, updated_at, is_archived, client_id_token)"
                           " VALUES ('', '2026-01-01', '2026-01-01', 1, ?)", (token,))
        conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                     (cur.lastrowid, pk, pan))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_office_archived_duplicate_matches_the_active_client(pcs):
    _add_archived_copy(pcs["office"], OFFICE_HEX, PAN_SHARED_DIFF, "ARCH-1")
    report = _plan(pcs)
    assert report.ambiguous == []
    assert [(c.key, c.office_id) for c in report.conflicts] == [(PAN_SHARED_DIFF, pcs["ids"]["o_diff"])]


def test_legacy_archived_duplicate_imports_only_the_active_client(pcs):
    arch = _add_archived_copy(pcs["legacy"], pcs["legacy_hex"], PAN_LEGACY_ONLY, "ARCH-2")
    report = _apply(pcs)
    assert [c.legacy_id for c in report.to_insert] == [pcs["ids"]["l_only"]]
    assert [(a.legacy_id, a.key) for a in report.ambiguous] == [(arch, PAN_LEGACY_ONLY)]
    assert "archived duplicate" in report.ambiguous[0].reason
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY)["values"]["NAME OF COMPANY"] == "LEGACY ONLY CO"


def test_two_active_office_clients_with_one_pan_stay_ambiguous(pcs):
    conn = _open(pcs["office"] / "master.db", OFFICE_HEX)
    try:
        pk = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()[0]
        cur = conn.execute("INSERT INTO clients (notes, created_at, updated_at, client_id_token)"
                           " VALUES ('', '2026-01-01', '2026-01-01', 'DUP-2')")
        conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                     (cur.lastrowid, pk, PAN_SHARED_DIFF))
        conn.commit()
    finally:
        conn.close()
    report = _plan(pcs)
    assert [a.key for a in report.ambiguous] == [PAN_SHARED_DIFF]
    assert report.conflicts == []


def test_apply_requires_token_letter_when_a_client_is_inserted(pcs):
    with pytest.raises(ValueError):
        _apply(pcs, token_letter=None)


def test_different_pk_column_stops(pcs):
    conn = _open(pcs["office"] / "master.db", OFFICE_HEX)
    try:
        conn.execute("UPDATE mcl_columns SET is_internal_pk = 0")
        conn.execute("UPDATE mcl_columns SET is_internal_pk = 1 WHERE label = 'GSTIN'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SalvageError):
        _plan(pcs)


def test_wrong_legacy_key_is_refused(pcs):
    with pytest.raises(sera_keys.WrongPassword):
        sync_rejoin.plan_salvage(pcs["legacy"], "00" * 32, pcs["office"], OFFICE_HEX)


def test_report_file_lists_pans_but_no_values(pcs):
    report = _apply(pcs)
    path = sync_rejoin.write_salvage_report(pcs["office"], report)
    assert path.parent == pcs["office"] / "logs" and path.name.startswith("salvage-")
    text = path.read_text(encoding="utf-8")
    assert PAN_LEGACY_ONLY in text and PAN_SHARED_DIFF in text and "NOPAN-1" in text
    assert "LEGACY NOTE COLUMN" in text
    for secret in (FAKE_EMAIL, "only here", "LEGACY NAME", "legacy note", LEGACY_PW):
        assert secret not in text


def test_container_notes_are_counted_not_imported(pcs):
    conn = _open(pcs["legacy"] / "rawPayload.db", pcs["legacy_hex"])
    try:
        conn.execute("INSERT INTO client_raw_containers (identity_key, notes, last_updated)"
                     " VALUES ('PAN-CCCPC3333C', 'typed note', '2026-08-01')")
        conn.commit()
    finally:
        conn.close()
    report = _plan(pcs)
    assert report.container_notes_not_imported == 1


# ------------------------------------------------------------------ rejoin: moving the legacy files

def test_move_legacy_files_and_restore(pcs):
    app = pcs["legacy"]
    (app / "master.db-wal").write_bytes(b"")          # a sidecar travels with its DB
    hashes = {p.name: _sha(p) for p in app.iterdir()}
    legacy_dir = sync_rejoin.move_legacy_files(app)
    assert legacy_dir.parent == app / "legacy"
    assert sorted(p.name for p in legacy_dir.iterdir()) == sorted(hashes)
    assert not any((app / n).exists() for n in hashes)
    assert sync_rejoin.read_state(app)["phase"] == "moved"

    assert sync_rejoin.resume_interrupted_rejoin(app) == "rolled_back"
    assert {p.name: _sha(p) for p in app.iterdir() if p.is_file()} == hashes
    assert sync_rejoin.read_state(app) is None
    assert not legacy_dir.exists()


def test_move_refuses_in_office_mode(pcs, monkeypatch):
    monkeypatch.setattr(sera_keys, "load_office", lambda app_dir: object())
    with pytest.raises(RejoinError):
        sync_rejoin.move_legacy_files(pcs["legacy"])


def test_move_failure_puts_everything_back(pcs, monkeypatch):
    app = pcs["legacy"]
    hashes = {p.name: _sha(p) for p in app.iterdir()}
    real_replace = os.replace

    def _replace(src, dst):
        if Path(src).name == "sera.salt":
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(sync_rejoin.os, "replace", _replace)
    with pytest.raises(RejoinError):
        sync_rejoin.move_legacy_files(app)
    monkeypatch.setattr(sync_rejoin.os, "replace", real_replace)
    assert {p.name: _sha(p) for p in app.iterdir() if p.is_file()} == hashes
    assert sync_rejoin.read_state(app) is None


def test_restore_refuses_to_overwrite_a_live_db(pcs):
    app = pcs["legacy"]
    sync_rejoin.move_legacy_files(app)
    (app / "master.db").write_bytes(b"something else")
    with pytest.raises(RejoinError):
        sync_rejoin.resume_interrupted_rejoin(app)
    assert (app / "master.db").read_bytes() == b"something else"


def test_resume_reports_pending_join_and_salvage(pcs, monkeypatch):
    app = pcs["legacy"]
    sync_rejoin.move_legacy_files(app)
    monkeypatch.setattr(sera_keys, "load_office", lambda app_dir: object())
    assert sync_rejoin.resume_interrupted_rejoin(app) == "pending_join"
    (app / "master.db").write_bytes(b"x")
    assert sync_rejoin.resume_interrupted_rejoin(app) == "salvage_pending"


def test_legacy_password_from_saved_key(pcs):
    app = pcs["legacy"]
    legacy_dir = sync_rejoin.move_legacy_files(app)
    assert sync_rejoin.saved_legacy_hex_key(legacy_dir) == pcs["legacy_hex"]
    (legacy_dir / "sera.key").write_text("wrong", encoding="utf-8")
    assert sync_rejoin.saved_legacy_hex_key(legacy_dir) is None
    assert sync_rejoin.legacy_hex_key(legacy_dir, LEGACY_PW) == pcs["legacy_hex"]
    with pytest.raises(sera_keys.WrongPassword):
        sync_rejoin.legacy_hex_key(legacy_dir, "nope")


def test_rejoin_request_round_trip(tmp_path):
    assert sync_rejoin.read_rejoin_request(tmp_path) is None
    sync_rejoin.write_rejoin_request(tmp_path, requested_by="User 1")
    assert sync_rejoin.read_rejoin_request(tmp_path)["requested_by"] == "User 1"
    sync_rejoin.clear_rejoin_request(tmp_path)
    assert sync_rejoin.read_rejoin_request(tmp_path) is None


def test_sync_rejoin_does_not_import_pyside6():
    src = Path(sync_rejoin.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("PySide6") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("PySide6")


# ------------------------------------------------------------------ end to end with a real admin PC

@windows_only
def test_rejoin_end_to_end(pcs, tmp_path):
    import sync_admin
    import sync_identity
    import sync_pairing
    import sync_snapshot
    import sync_transport

    # The admin PC: the office DB from the fixture, now with office keys.
    admin = pcs["office"]
    dek = bytes.fromhex(OFFICE_HEX)
    office = sera_keys.OfficeInfo(office_id=sera_keys.OfficeInfo.new_office_id(), office_name="Invented Office",
                                  key_id=sera_keys.key_id(dek))
    sera_keys.store_dek(admin, dek, MASTER_PW, office.office_id)
    sera_keys.save_office(admin, office)
    pubkey = sync_admin.create_admin_key(admin, MASTER_PW)
    ident = sync_identity.ensure_device_identity(admin)

    def open_db():
        return _open(admin / "master.db", OFFICE_HEX)

    conn = open_db()
    try:
        sync_admin.init_office_membership(admin, conn, ident.cert_pem.decode("ascii"), "Admin PC")
    finally:
        conn.close()

    def members():
        c = open_db()
        try:
            return sync_transport.MemberSet.from_db(c, pubkey, ident.cert_pem)
        finally:
            c.close()

    transport = sync_transport.SyncTransport(sync_identity.load_cert_chain_args(admin), members())
    server = transport.serve(lambda s: sync_snapshot.handle_snapshot_session(s, admin), host="127.0.0.1", port=0)
    window = sync_pairing.PairingWindow(admin, open_db, ident.device_id, host="127.0.0.1", port=0,
                                        on_joined=lambda rec: transport.update_members(members()))
    window.start()
    try:
        app = pcs["legacy"]
        legacy_files = {n: _sha(app / n) for n in ("master.db", "rawPayload.db", "sera.salt", "sera.key")}

        # A wrong code: nothing of the office is written and the legacy files go back.
        with pytest.raises(sync_pairing.WrongCode):
            sync_rejoin.rejoin_office(app, "127.0.0.1", "000000" if window.code != "000000" else "111111",
                                      "Branch PC", pairing_port=window.port, sync_port=server.address[1])
        assert sera_keys.load_office(app) is None
        assert {n: _sha(app / n) for n in legacy_files} == legacy_files
        assert sync_rejoin.read_state(app) is None

        window.close()
        window = sync_pairing.PairingWindow(admin, open_db, ident.device_id, host="127.0.0.1", port=0,
                                            on_joined=lambda rec: transport.update_members(members()))
        window.start()
        result = sync_rejoin.rejoin_office(app, "127.0.0.1", window.code, "Branch PC",
                                           pairing_port=window.port, sync_port=server.address[1])
        assert result.token_letter == "B"
        state = sync_rejoin.read_state(app)
        assert state["phase"] == "salvage" and state["token_letter"] == "B"
        legacy_dir = Path(state["legacy_dir"])
        assert {n: _sha(legacy_dir / n) for n in legacy_files} == legacy_files
        assert sync_rejoin.resume_interrupted_rejoin(app) == "salvage_pending"

        # The PC now runs on the office DB.
        assert sera_keys.key_id(sera_keys.load_dek(app)) == office.key_id
        assert _office_client(app, PAN_OFFICE_ONLY) is not None
        assert _office_client(app, PAN_LEGACY_ONLY) is None

        report = sync_rejoin.salvage_after_rejoin(app, dry_run=True)
        assert [c.key for c in report.to_insert] == [PAN_LEGACY_ONLY]
        report = sync_rejoin.salvage_after_rejoin(app, dry_run=False)
        assert report.report_path is not None and Path(report.report_path).exists()
        assert _office_client(app, PAN_LEGACY_ONLY)["values"]["NAME OF COMPANY"] == "LEGACY ONLY CO"
        assert sync_rejoin.read_state(app) is None
        # The legacy files stay in legacy/<ts>/ (§0 rule 3).
        assert {n: _sha(legacy_dir / n) for n in legacy_files} == legacy_files
    finally:
        window.close()
        server.stop()


# ------------------------------------------------------------------ snapshot install (P2-6 note for P2-8)

def test_snapshot_install_aborts_when_a_sidecar_cannot_be_moved(tmp_path, monkeypatch):
    import sync_snapshot
    db = tmp_path / "master.db"
    db.write_bytes(b"db")
    (tmp_path / "master.db-wal").write_bytes(b"wal")
    real_replace = os.replace

    def _replace(src, dst):
        if str(src).endswith("-wal"):
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(sync_snapshot.os, "replace", _replace)
    with pytest.raises(sync_snapshot.SnapshotError):
        sync_snapshot._backup_db_and_sidecars(db, "20260924_120000")


# ------------------------------------------------------------------ main.py wiring + UI smoke tests

def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _stub_app(app_dir):
    from main import SeraApp

    class Stub:
        _run_pending_rejoin = SeraApp._run_pending_rejoin

        def __init__(self):
            self.app_dir = app_dir

    return Stub()


def test_startup_hook_is_a_noop_without_request_or_state(tmp_path, monkeypatch):
    import ui.dialogs.rejoin_office_dialog as rd
    called = []
    monkeypatch.setattr(rd, "run_pending_rejoin", lambda *a, **k: called.append(a))
    _stub_app(tmp_path)._run_pending_rejoin()
    assert called == []


def test_startup_hook_consumes_request_and_exits_on_error(tmp_path, monkeypatch):
    import ui.dialogs.rejoin_office_dialog as rd
    seen = []
    monkeypatch.setattr(rd, "run_pending_rejoin", lambda app_dir, requested: seen.append(requested) or "exit")
    sync_rejoin.write_rejoin_request(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _stub_app(tmp_path)._run_pending_rejoin()
    assert exc.value.code == 1
    assert seen == [True]
    assert sync_rejoin.read_rejoin_request(tmp_path) is None     # no restart loop


def test_run_pending_rejoin_undoes_an_interrupted_move(pcs, monkeypatch):
    from unittest.mock import patch
    _qapp()
    from ui.dialogs.rejoin_office_dialog import run_pending_rejoin
    app = pcs["legacy"]
    hashes = {p.name: _sha(p) for p in app.iterdir()}
    sync_rejoin.move_legacy_files(app)
    with patch("PySide6.QtWidgets.QMessageBox.warning") as warn:
        assert run_pending_rejoin(app, requested=False) is None
    warn.assert_called_once()
    assert {p.name: _sha(p) for p in app.iterdir() if p.is_file()} == hashes


def test_run_pending_rejoin_imports_after_confirmation(pcs, monkeypatch):
    """Salvage step at start-up: the dry run is shown, the user ticks one conflict and imports."""
    from unittest.mock import patch
    _qapp()
    import ui.dialogs.rejoin_office_dialog as rd
    app = pcs["office"]
    legacy_dir = app / "legacy" / "20260924_120000"
    legacy_dir.mkdir(parents=True)
    for n in ("master.db", "rawPayload.db", "sera.salt", "sera.key"):
        (legacy_dir / n).write_bytes((pcs["legacy"] / n).read_bytes())
    sync_rejoin._write_state(app, {"phase": "salvage", "legacy_dir": str(legacy_dir), "moved": [],
                                   "token_letter": "B"})
    monkeypatch.setattr(sera_keys, "load_office", lambda app_dir: object())
    monkeypatch.setattr(sync_rejoin, "_office_hex", lambda app_dir: OFFICE_HEX)

    shown = []

    def _exec(dlg):
        shown.append(dlg.report)
        dlg._boxes[PAN_SHARED_DIFF].setChecked(True)
        dlg._on_import()
        return 1

    with patch.object(rd.SalvageDialog, "exec", _exec), \
         patch("PySide6.QtWidgets.QMessageBox.information") as info:
        assert rd.run_pending_rejoin(app, requested=False) is None
    assert shown and shown[0].dry_run is True
    info.assert_called_once()
    assert _office_client(app, PAN_LEGACY_ONLY) is not None
    assert _office_client(app, PAN_SHARED_DIFF)["values"]["NAME OF COMPANY"] == "LEGACY NAME"
    assert sync_rejoin.read_state(app) is None
    assert list((app / "logs").glob("salvage-*.txt"))


def test_salvage_later_keeps_state(pcs, monkeypatch):
    from unittest.mock import patch
    _qapp()
    import ui.dialogs.rejoin_office_dialog as rd
    app = pcs["office"]
    legacy_dir = pcs["legacy"]
    sync_rejoin._write_state(app, {"phase": "salvage", "legacy_dir": str(legacy_dir), "moved": [],
                                   "token_letter": "B"})
    monkeypatch.setattr(sera_keys, "load_office", lambda app_dir: object())
    monkeypatch.setattr(sync_rejoin, "_office_hex", lambda app_dir: OFFICE_HEX)
    before = _dump(app, OFFICE_HEX)
    with patch.object(rd.SalvageDialog, "exec", lambda dlg: dlg.reject() or 0):
        assert rd.run_pending_rejoin(app, requested=False) is None
    assert sync_rejoin.read_state(app) is not None
    assert _dump(app, OFFICE_HEX) == before


def _sync_dialog(key_id, db_path):
    from unittest.mock import MagicMock
    _qapp()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    svc = MagicMock()
    svc.get_sync_state.return_value = {"status": "NORMAL"}
    svc.get_peers.return_value = []
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.inv_frames = False
    svc.key_id = key_id
    svc.db_path = str(db_path)
    return SeraSyncDialog(sync_service=svc, db=None, actor="User 1"), svc


def test_rejoin_button_only_in_legacy_mode(tmp_path):
    legacy, _ = _sync_dialog(None, tmp_path / "master.db")
    office, _ = _sync_dialog("a" * 32, tmp_path / "master.db")
    assert not legacy.btn_rejoin.isHidden()
    assert office.btn_rejoin.isHidden()


def test_rejoin_button_writes_request_and_restarts(tmp_path):
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    dlg, svc = _sync_dialog(None, tmp_path / "master.db")
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.No), \
         patch("version.restart_app") as restart:
        dlg._on_rejoin_office()
    assert sync_rejoin.read_rejoin_request(tmp_path) is None
    restart.assert_not_called()
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes), \
         patch("version.restart_app") as restart:
        dlg._on_rejoin_office()
    assert sync_rejoin.read_rejoin_request(tmp_path)["requested_by"] == "User 1"
    svc.stop.assert_called_once()
    restart.assert_called_once()


def test_rejoin_dialog_validation():
    _qapp()
    from ui.dialogs.rejoin_office_dialog import MAX_PASSWORD_ATTEMPTS, RejoinOfficeDialog
    dlg = RejoinOfficeDialog(lambda pw: pw == LEGACY_PW, default_name="Branch PC")
    dlg._on_ok()
    assert dlg.details is None and "address" in dlg.error.text()
    dlg.host.setText("192.168.1.10")
    dlg.code.setText("12345")
    dlg._on_ok()
    assert dlg.details is None and "6 digits" in dlg.error.text()
    dlg.code.setText("123 456")
    dlg.legacy_pw.setText("wrong")
    dlg._on_ok()
    assert dlg.details is None and "password" in dlg.error.text()
    dlg.legacy_pw.setText(LEGACY_PW)
    dlg._on_ok()
    assert dlg.details.code == "123456" and dlg.details.legacy_password == LEGACY_PW
    assert dlg.details.device_name == "Branch PC"

    saved = RejoinOfficeDialog(None, default_name="PC")        # saved sera.key works: no password field
    saved.host.setText("10.0.0.5")
    saved.code.setText("654321")
    saved._on_ok()
    assert saved.details.legacy_password is None

    locked = RejoinOfficeDialog(lambda pw: False, default_name="PC")
    locked.host.setText("10.0.0.5")
    locked.code.setText("654321")
    for _ in range(MAX_PASSWORD_ATTEMPTS):
        locked._on_ok()
    assert locked.details is None and locked.result() == 0


def test_salvage_dialog_lists_conflicts(pcs):
    _qapp()
    from ui.dialogs.rejoin_office_dialog import SalvageDialog
    dlg = SalvageDialog(_plan(pcs))
    assert list(dlg._boxes) == [PAN_SHARED_DIFF]
    dlg._on_import()
    assert dlg.choice == "import" and dlg.take_legacy == set()


# ------------------------------------------------------------------ undo a stuck rejoin (review should-fix 3)

def _fake_paired(app: Path) -> None:
    """What join_office leaves when the download never happens: office.json + pairing.json."""
    kdir = sera_keys.keys_dir(app)
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / sera_keys.OFFICE_FILE).write_text('{"format": 1}', encoding="utf-8")
    join = app / "incoming" / "join"
    join.mkdir(parents=True, exist_ok=True)
    (join / "pairing.json").write_text("{}", encoding="utf-8")


def test_undo_rejoin_restores_legacy_files_and_keeps_office_json_as_backup(pcs):
    app = pcs["legacy"]
    hashes = {n: _sha(app / n) for n in ("master.db", "rawPayload.db", "sera.salt", "sera.key")}
    sync_rejoin.move_legacy_files(app)
    _fake_paired(app)
    sync_rejoin.undo_rejoin(app)
    assert {n: _sha(app / n) for n in hashes} == hashes
    assert sync_rejoin.read_state(app) is None
    kdir = sera_keys.keys_dir(app)
    assert not (kdir / sera_keys.OFFICE_FILE).exists()
    assert len(list(kdir.glob("office.json.bak-*"))) == 1                    # §0 rule 3
    assert len(list((app / "incoming" / "join").glob("pairing.json.bak-*"))) == 1
    assert sera_keys.load_office(app) is None                                 # legacy mode again


def test_undo_rejoin_refused_once_the_office_db_is_installed(pcs):
    app = pcs["legacy"]
    sync_rejoin.move_legacy_files(app)
    _fake_paired(app)
    (app / "master.db").write_bytes(b"office db")
    with pytest.raises(RejoinError):
        sync_rejoin.undo_rejoin(app)
    assert (sera_keys.keys_dir(app) / sera_keys.OFFICE_FILE).exists()
    assert (app / "master.db").read_bytes() == b"office db"


def test_crash_during_undo_is_finished_at_next_start(pcs, monkeypatch):
    app = pcs["legacy"]
    hashes = {n: _sha(app / n) for n in ("master.db", "sera.salt")}
    sync_rejoin.move_legacy_files(app)
    _fake_paired(app)

    def _crash(app_, state):
        raise OSError("power cut")

    monkeypatch.setattr(sync_rejoin, "_restore_legacy_files", _crash)
    with pytest.raises(OSError):
        sync_rejoin.undo_rejoin(app)
    monkeypatch.undo()
    assert sync_rejoin.resume_interrupted_rejoin(app) == "rolled_back"
    assert {n: _sha(app / n) for n in hashes} == hashes


def test_pending_join_dialog_offers_undo(pcs, monkeypatch):
    from unittest.mock import patch
    _qapp()
    import ui.dialogs.rejoin_office_dialog as rd
    app = pcs["legacy"]
    hashes = {n: _sha(app / n) for n in ("master.db", "sera.salt")}
    sync_rejoin.move_legacy_files(app)
    _fake_paired(app)
    monkeypatch.setattr(sera_keys, "load_office",
                        lambda d: object() if (sera_keys.keys_dir(d) / sera_keys.OFFICE_FILE).exists() else None)
    monkeypatch.setattr(rd, "_ask_pending_join", lambda: "undo")
    with patch("PySide6.QtWidgets.QMessageBox.information") as info:
        assert rd.run_pending_rejoin(app, requested=False) is None
    info.assert_called_once()
    assert {n: _sha(app / n) for n in hashes} == hashes


def test_failed_download_at_start_can_be_undone(pcs, monkeypatch):
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    _qapp()
    import ui.dialogs.rejoin_office_dialog as rd
    app = pcs["legacy"]
    sync_rejoin.move_legacy_files(app)
    _fake_paired(app)
    monkeypatch.setattr(sera_keys, "load_office",
                        lambda d: object() if (sera_keys.keys_dir(d) / sera_keys.OFFICE_FILE).exists() else None)
    monkeypatch.setattr(rd, "_ask_pending_join", lambda: "finish")

    def _fail(*a, **k):
        raise RuntimeError("admin PC unreachable")

    monkeypatch.setattr(sync_rejoin, "resume_download", _fail)
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.No):
        assert rd.run_pending_rejoin(app, requested=False) == "exit"
    assert sync_rejoin.read_state(app) is not None                     # still paired, retried next start
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes), \
         patch("PySide6.QtWidgets.QMessageBox.information"):
        assert rd.run_pending_rejoin(app, requested=False) is None
    assert sync_rejoin.read_state(app) is None
    assert (app / "master.db").exists()


def test_snapshot_install_puts_sidecars_back_when_the_db_cannot_be_moved(tmp_path, monkeypatch):
    """Review should-fix 2: the DB move is part of the same rollback as its sidecars."""
    import sync_snapshot
    db = tmp_path / "master.db"
    db.write_bytes(b"db")
    (tmp_path / "master.db-wal").write_bytes(b"wal")
    (tmp_path / "master.db-shm").write_bytes(b"shm")
    real_replace = os.replace

    def _replace(src, dst):
        if Path(src) == db:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(sync_snapshot.os, "replace", _replace)
    with pytest.raises(sync_snapshot.SnapshotError):
        sync_snapshot._backup_db_and_sidecars(db, "20260924_120000")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["master.db", "master.db-shm", "master.db-wal"]


# ------------------------------------------------------------------ re-review fixes

def test_undo_after_a_partial_snapshot_install(pcs):
    """Re-review: the snapshot installs rawPayload.db before master.db. If master.db failed,
    the office rawPayload.db (and its WAL) must be set aside so the legacy files can go back."""
    app = pcs["legacy"]
    hashes = {n: _sha(app / n) for n in ("master.db", "rawPayload.db", "sera.salt", "sera.key")}
    sync_rejoin.move_legacy_files(app)
    _fake_paired(app)
    (app / "rawPayload.db").write_bytes(b"office raw")
    (app / "rawPayload.db-wal").write_bytes(b"office wal")
    sync_rejoin.undo_rejoin(app)
    assert {n: _sha(app / n) for n in hashes} == hashes
    assert not (app / "rawPayload.db-wal").exists()
    baks = sorted(p.name.split(".bak-")[0] for p in app.glob("rawPayload.db*.bak-*"))
    assert baks == ["rawPayload.db", "rawPayload.db-wal"]                  # §0 rule 3: kept, not deleted
    assert next(app.glob("rawPayload.db.bak-*")).read_bytes() == b"office raw"
    assert sync_rejoin.read_state(app) is None
    assert sync_rejoin.resume_interrupted_rejoin(app) is None


def test_non_ascii_digit_token_does_not_abort_the_import(pcs):
    _set_token(pcs["legacy"], pcs["legacy_hex"], pcs["ids"]["l_only"], "²")
    report = _apply(pcs)
    assert _office_client(pcs["office"], PAN_LEGACY_ONLY)["token"] == "²"
    assert [c.key for c in report.to_insert] == [PAN_LEGACY_ONLY]
