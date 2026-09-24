"""Tests for Sera Sync v3 P3-3: capture triggers, sealer, hybrid logical clock.

Accept (blueprint §5 P3-3):
  - every public write method of SeraDatabase produces the expected changes
    (parameterised over WRITE_CASES; test_every_public_write_method_is_listed keeps the list
    complete against database.py);
  - an external raw sqlite3 write is captured by the timer seal;
  - no pending rows are left after seal;
  - HLC is monotonic under a stepped-back clock (monkeypatched time).

tmp_path only (§0 rule 2). Invented test data only (§0 rule 12).
"""

import ast
import base64
import json
import re
import secrets
import time
from pathlib import Path

import pytest

import sync_capture
import sync_schema
from sync_capture import HLC, ClockAhead, observe, tick

ROOT = Path(__file__).resolve().parent.parent
PAN_A = "ABCDE1234F"
PAN_B = "PQRSX6789K"
PAN_C = "LMNOP4321Z"


# ---------------------------------------------------------------- fixtures / helpers

def _make_db(dir_path: Path, mode="shadow", device_id=None):
    from database import SeraDatabase
    dir_path.mkdir(parents=True, exist_ok=True)
    db = SeraDatabase(str(dir_path / "master.db"), secrets.token_hex(32),
                      raw_db_path=str(dir_path / "rawPayload.db"),
                      defer_startup_maintenance=True, key_mode="legacy")
    db.set_sync_device_id(device_id or secrets.token_hex(16))
    if mode != "off":
        db.set_sync_mode(mode)
    return db


@pytest.fixture
def admin_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


@pytest.fixture
def db(tmp_path, admin_key):
    d = _make_db(tmp_path / "pc")
    d._admin_signer = lambda: admin_key
    yield d
    d.stop_seal_timer()


def _raw_conn(path, hex_key):
    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    return conn


def _changes(db, which="both", after=None):
    """Sealed changes as dicts, in seq order. `after` = {"master": seq, "raw": seq}."""
    out = []
    for name, opener in (("master", db._connect), ("raw", db._connect_raw)):
        if which not in ("both", name):
            continue
        floor = (after or {}).get(name, 0)
        with opener() as c:
            rows = c.execute("SELECT seq, origin, origin_seq, hlc, tbl, row_key, op, data, sig "
                             "FROM _sync_changes WHERE seq > ? ORDER BY seq", (floor,)).fetchall()
        for r in rows:
            out.append({"db": name, "seq": r[0], "origin": r[1], "origin_seq": r[2], "hlc": r[3],
                        "tbl": r[4], "row_key": r[5], "op": r[6],
                        "data": json.loads(r[7]) if r[7] else None, "sig": r[8]})
    return out


def _mark(db):
    m = {}
    for name, opener in (("master", db._connect), ("raw", db._connect_raw)):
        with opener() as c:
            m[name] = c.execute("SELECT COALESCE(MAX(seq), 0) FROM _sync_changes").fetchone()[0]
    return m


def _pending(db):
    n = 0
    for opener in (db._connect, db._connect_raw):
        with opener() as c:
            n += c.execute("SELECT COUNT(*) FROM _sync_pending").fetchone()[0]
    return n


def _col(db, label):
    for c in db.get_mcl_columns():
        if c["label"] == label:
            return c["id"]
    raise KeyError(label)


def _service(db, name):
    with db._connect() as c:
        return c.execute("SELECT id FROM services WHERE name = ?", (name,)).fetchone()[0]


def _gid(db, table, local_id):
    with db._connect() as c:
        return c.execute(f"SELECT gid FROM {table} WHERE id = ?", (local_id,)).fetchone()[0]


def _add_client(db, pan, company="Invented Traders", services=()):
    return db.add_client({_col(db, "PAN"): pan, _col(db, "NAME OF COMPANY"): company},
                         "", list(services))


def _values(db, client_id, **changes):
    """The client's whole form as update_client() receives it, with {label: value} changes."""
    with db._connect() as c:
        vals = dict(c.execute("SELECT column_id, value FROM client_values WHERE client_id = ?",
                              (client_id,)).fetchall())
    for label, value in changes.items():
        if value is None:
            vals.pop(_col(db, label), None)
        else:
            vals[_col(db, label)] = value
    return vals


def _add_tracker(db, client_id=None, **kw):
    fields = dict(client_id=client_id, unassigned_identity=None, service_id=None, portal="GST",
                  period_label="Apr-2026", arn_number="AA0704260000001", capture_method="DOM_Tracker",
                  status="submitted", raw_payload_json="{}", captured_by="Tester",
                  created_at="2026-09-25T10:00:00+00:00", dataset_key=None)
    fields.update(kw)
    cols = ", ".join(fields)
    with db._connect_raw() as c:
        cur = c.execute(f"INSERT INTO tracker_dump ({cols}) VALUES ({', '.join('?' * len(fields))})",
                        tuple(fields.values()))
        return cur.lastrowid


def _ops(changes):
    return {(c["tbl"], c["op"]) for c in changes}


# ---------------------------------------------------------------- the write-method list

def _setup_clients(db):
    a = _add_client(db, PAN_A, services=[_service(db, "GST")])
    b = _add_client(db, PAN_B, company="Second Invented Co")
    return {"a": a, "b": b}


def _setup_custom_column(db):
    return {"col": db.create_mcl_column("Invented Extra", "text")}


def _setup_tracker(db):
    ctx = _setup_clients(db)
    ctx["dump"] = _add_tracker(db, client_id=ctx["a"])
    return ctx


def _setup_dup_clients(db):
    ctx = _setup_clients(db)
    with db._connect() as c:
        c.execute("UPDATE mcl_columns SET is_identity = 1 WHERE label = 'NAME OF COMPANY'")
        c.execute("UPDATE mcl_columns SET is_identity = 0 WHERE label != 'NAME OF COMPANY'")
        now = "2026-09-25T10:00:00"
        dup = c.execute("INSERT INTO clients (notes, created_at, updated_at) VALUES ('', ?, ?)",
                        (now, now)).lastrowid
        c.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                  (dup, _col(db, "NAME OF COMPANY"), "Invented Traders"))
    ctx["dup"] = dup
    return ctx


def _setup_alias(db):
    with db._connect() as c:
        c.execute("UPDATE staff_users SET alias = 'Desk-1' WHERE name = 'User 1'")
    return {}


def _setup_sgt(db):
    ctx = _setup_clients(db)
    _add_tracker(db, client_id=ctx["a"], capture_method="SGT_shadow", dataset_key="SGT:invented:1")
    return ctx


def _setup_tracker_dups(db):
    ctx = _setup_clients(db)
    _add_tracker(db, client_id=ctx["a"], dataset_key="DUPKEY", created_at="2026-09-25T10:00:00+00:00")
    _add_tracker(db, client_id=ctx["a"], dataset_key="DUPKEY", created_at="2026-09-25T11:00:00+00:00")
    return ctx


def _setup_unassigned(db):
    ctx = _setup_clients(db)
    _add_tracker(db, client_id=None, unassigned_identity=PAN_A, arn_number="AA0704260000002")
    return ctx


def _setup_formatting(db):
    ctx = _setup_clients(db)
    db.bulk_set_cell_formatting([{"client_id": ctx["a"], "column_key": str(_col(db, "PAN")),
                                  "bg_color": "#112233", "fg_color": ""}])
    return ctx


def _setup_container(db):
    with db._connect_raw() as c:
        c.execute("INSERT INTO client_raw_containers (identity_key, last_updated) VALUES ('ABCDE1234F', 'x')")
    return {}


def _setup_timeline(db):
    ctx = _setup_clients(db)
    return ctx


def _setup_placeholder(db):
    ctx = _setup_clients(db)
    db.upsert_sdc_session_timeline({"session_id": "sess-name", "pan": PAN_A,
                                    "client_name": "Real Invented Traders", "timeline": []})
    # after the timeline (which would upgrade the name itself)
    with db._connect() as c:
        c.execute("UPDATE client_values SET value = ? WHERE client_id = ? AND column_id = ?",
                  (f"Client ({PAN_A})", ctx["a"], _col(db, "NAME OF COMPANY")))
    return ctx


def _setup_ini(db):
    ini = Path(db.db_path).parent / "invented_settings.ini"
    ini.write_text("[AppSettings]\ninvented_ini_key = from-ini\n", encoding="utf-8")
    return {"ini": str(ini)}


def _setup_remove_staff(db):
    db.remove_staff_user("User 6")
    return {}


ST = ("staff_users", "upsert")

# name -> (setup, call, required (tbl, op) set, forbidden (tbl, op) set)
WRITE_CASES = {
    "set_setting": (None, lambda db, c: db.set_setting("theme", "invented-dark"),
                    {("app_settings", "upsert")}, set()),
    "set_settings_bulk": (None, lambda db, c: db.set_settings_bulk({"k_one": "1", "k_two": "2"}),
                          {("app_settings", "upsert")}, set()),
    "assign_or_get_alias": (None, lambda db, c: db.assign_or_get_alias("Desk-9"), {ST}, set()),
    "update_staff_alias": (None, lambda db, c: db.update_staff_alias(1, "Desk-7"), {ST}, set()),
    "reset_staff_matrix": (_setup_alias, lambda db, c: db.reset_staff_matrix(), {ST}, set()),
    "add_staff_user": (_setup_remove_staff, lambda db, c: db.add_staff_user("Invented Person"), {ST}, set()),
    "remove_staff_user": (None, lambda db, c: db.remove_staff_user("User 6"),
                          {("staff_users", "delete")}, set()),
    "create_mcl_column": (None, lambda db, c: db.create_mcl_column("Invented Col", "text"),
                          {("mcl_columns", "upsert")}, set()),
    "update_mcl_column": (_setup_custom_column,
                          lambda db, c: db.update_mcl_column(c["col"], "Invented Col 2", "text"),
                          {("mcl_columns", "upsert")}, set()),
    "delete_mcl_column": (_setup_custom_column, lambda db, c: db.delete_mcl_column(c["col"]),
                          {("mcl_columns", "delete")}, {("client_values", "delete")}),
    "reorder_mcl_columns": (None, lambda db, c: db.reorder_mcl_columns(
        [x["id"] for x in reversed(db.get_mcl_columns())]), {("mcl_columns", "upsert")}, set()),
    "bulk_update_mcl_visibility": (None, lambda db, c: db.bulk_update_mcl_visibility(
        [_col(db, "PAN")]), {("mcl_columns", "upsert")}, set()),
    "bulk_update_mcl_quick_copy": (None, lambda db, c: db.bulk_update_mcl_quick_copy(
        [_col(db, "PAN")]), {("mcl_columns", "upsert")}, set()),
    "bulk_update_mcl_admin_visibility": (None, lambda db, c: db.bulk_update_mcl_admin_visibility(
        [_col(db, "PAN")]), {("mcl_columns", "upsert")}, set()),
    "create_service": (None, lambda db, c: db.create_service(
        "Invented Portal", "https://portal.invalid/login", None, None, "#u", "#p"),
        {("services", "upsert")}, set()),
    "update_service": (None, lambda db, c: db.update_service(
        _service(db, "Email"), "Email", "https://mail.invalid/", None, None, "#u2", "#p2"),
        {("services", "upsert")}, set()),
    "delete_service": (_setup_clients, lambda db, c: db.delete_service(_service(db, "GST")),
                       {("services", "delete")}, {("client_services", "delete")}),
    "record_client_activity": (_setup_clients,
                               lambda db, c: db.record_client_activity(c["a"], "View", "opened"),
                               set(), "ALL"),
    "add_client": (None, lambda db, c: _add_client(db, PAN_C, services=[_service(db, "GST")]),
                   {("clients", "upsert"), ("client_values", "upsert"),
                    ("client_services", "upsert"), ("audit_log", "upsert")}, set()),
    "update_client": (_setup_clients, lambda db, c: db.update_client(
        c["a"], _values(db, c["a"], **{"NAME OF COMPANY": "Renamed Invented Traders"}),
        "note", [_service(db, "GST")]),
        {("clients", "upsert"), ("client_values", "upsert"), ("audit_log", "upsert")},
        {("client_values", "delete"), ("client_services", "delete"), ("client_services", "upsert")}),
    "archive_client": (_setup_clients, lambda db, c: db.archive_client(c["a"]),
                       {("clients", "upsert"), ("audit_log", "upsert")}, set()),
    "unarchive_client": (_setup_clients, lambda db, c: db.unarchive_client(c["a"]),
                         {("clients", "upsert"), ("audit_log", "upsert")}, set()),
    "delete_client": (_setup_clients, lambda db, c: db.delete_client(c["a"]),
                      {("clients", "delete"), ("audit_log", "upsert")},
                      {("client_values", "delete"), ("client_services", "delete")}),
    "update_client_single_field": (_setup_clients, lambda db, c: db.update_client_single_field(
        c["a"], _col(db, "GST_Password"), "invented-secret"),
        {("client_values", "upsert"), ("clients", "upsert"), ("audit_log", "upsert")}, set()),
    "bulk_import_clients": (None, lambda db, c: db.bulk_import_clients(
        [{"PAN": PAN_C, "NAME OF COMPANY": "Imported Invented Co"}]),
        {("clients", "upsert"), ("client_values", "upsert")}, set()),
    "bulk_archive_clients": (_setup_clients, lambda db, c: db.bulk_archive_clients([c["a"], c["b"]]),
                             {("clients", "upsert")}, set()),
    "bulk_unarchive_clients": (_setup_clients, lambda db, c: db.bulk_unarchive_clients([c["a"]]),
                               {("clients", "upsert")}, set()),
    "resequence_client_serial_numbers": (_setup_clients, lambda db, c: (
        db.update_client_single_field(c["a"], _col(db, "No."), "99", log_action=False),
        db.seal_pending(), db.resequence_client_serial_numbers()),
        {("client_values", "upsert")}, set()),
    "bulk_delete_clients": (_setup_clients, lambda db, c: db.bulk_delete_clients([c["b"]]),
                            {("clients", "delete")}, {("client_values", "delete")}),
    "purge_duplicate_clients": (_setup_dup_clients, lambda db, c: db.purge_duplicate_clients(),
                                {("clients", "delete")}, set()),
    "bulk_set_service": (_setup_clients, lambda db, c: db.bulk_set_service(
        [c["b"]], _service(db, "Email"), True), {("client_services", "upsert")}, set()),
    "log_action": (None, lambda db, c: db.log_action("Tester", "invented_action", detail="x"),
                   {("audit_log", "upsert")}, set()),
    "update_client_notes": (_setup_clients, lambda db, c: db.update_client_notes(c["a"], "new note"),
                            {("clients", "upsert")}, set()),
    "delete_srpf_container": (_setup_container, lambda db, c: db.delete_srpf_container("ABCDE1234F"),
                              set(), "ALL"),
    "delete_sgt_rows_by_dataset_key": (_setup_sgt, lambda db, c: db.delete_sgt_rows_by_dataset_key(
        "SGT:invented:1"), {("tracker_dump", "delete")}, set()),
    "insert_tracker_dump": (_setup_clients, lambda db, c: db.insert_tracker_dump(
        client_id=c["a"], portal="GST", period_label="May-2026", arn_number="AA0705260000009",
        pan=PAN_A, raw_payload_json=json.dumps({"pan": PAN_A})),
        {("tracker_dump", "upsert")}, set()),
    "deduplicate_tracker_dumps": (_setup_tracker_dups, lambda db, c: db.deduplicate_tracker_dumps(),
                                  {("tracker_dump", "delete")}, set()),
    "store_peer_tracker_dumps": (_setup_clients, lambda db, c: db.store_peer_tracker_dumps([{
        "portal": "GST", "period_label": "Jun-2026", "arn_number": "AA0706260000003",
        "status": "submitted", "captured_by": "Peer", "created_at": "2026-09-25T09:00:00+00:00",
        "raw_payload_json": "{}", "dataset_key": "PEER:KEY:1"}]), {("tracker_dump", "upsert")}, set()),
    "upsert_sdc_session_timeline": (_setup_timeline, lambda db, c: db.upsert_sdc_session_timeline({
        "session_id": "sess-invented-1", "pan": PAN_A, "client_name": "Invented Traders",
        "timeline": [{"step": 1}]}), {("sdc_session_timelines", "upsert")}, set()),
    "link_unassigned_tracker_dumps": (_setup_unassigned,
                                      lambda db, c: db.link_unassigned_tracker_dumps(c["a"], PAN_A),
                                      {("tracker_dump", "upsert")}, set()),
    "save_tracker_dump_media": (_setup_tracker, lambda db, c: db.save_tracker_dump_media(
        c["dump"], "invented note", "shot.png"), {("tracker_dump", "upsert")}, set()),
    "save_srpf_container_media": (_setup_container, lambda db, c: db.save_srpf_container_media(
        "ABCDE1234F", "note", "shot.png"), set(), "ALL"),
    "delete_tracker_dump": (_setup_tracker, lambda db, c: db.delete_tracker_dump(c["dump"]),
                            {("tracker_dump", "delete")}, set()),
    "clear_tracker_dumps": (_setup_tracker, lambda db, c: db.clear_tracker_dumps(),
                            {("tracker_dump", "delete")}, set()),
    "bulk_set_cell_formatting": (_setup_clients, lambda db, c: db.bulk_set_cell_formatting([
        {"client_id": c["a"], "column_key": str(_col(db, "PAN")), "bg_color": "#ffeeaa"},
        {"client_id": c["a"], "column_key": "services", "bg_color": "#00ff00"}]),
        {("cell_formatting", "upsert")}, set()),
    "clear_cell_formatting": (_setup_formatting, lambda db, c: db.clear_cell_formatting(
        [(c["a"], str(_col(db, "PAN")))]), {("cell_formatting", "delete")}, set()),
    "re_resolve_all_tracker_dumps": (_setup_tracker, lambda db, c: db.re_resolve_all_tracker_dumps(),
                                     set(), {("tracker_dump", "delete")}),
    "upgrade_all_placeholder_client_names": (_setup_placeholder,
                                             lambda db, c: db.upgrade_all_placeholder_client_names(),
                                             {("client_values", "upsert")}, set()),
    "load_ini_defaults": (_setup_ini, lambda db, c: db.load_ini_defaults(c["ini"]),
                          {("app_settings", "upsert")}, set()),
    "get_srpf_containers": (_setup_tracker, lambda db, c: db.get_srpf_containers(),
                            set(), {("tracker_dump", "delete")}),
    "save_scc_settings": (None, lambda db, c: db.save_scc_settings({"enabled": False, "opt1_label": "X"}),
                          {("app_settings", "upsert")}, set()),
    "tag_client_scc_verified": (_setup_clients, lambda db, c: db.tag_client_scc_verified(c["a"]),
                                {("clients", "upsert")}, set()),
}

# Public write methods not called by the parameterised test, and why.
NOT_CALLED = {
    "auto_populate_service_selectors": "fetches the portals' login pages over the internet "
                                       "(urllib); its UPDATE of services is the same path as "
                                       "update_service, which is covered",
    "set_sync_mode": "writes only _sync_meta (not replicated); used by every test here",
    "run_startup_maintenance": "also runs sync_fst_reports, which writes an .xlsx next to "
                               "DOM_Parser_1 (outside tmp_path). Its database steps are covered "
                               "one by one: re_resolve_all_tracker_dumps, "
                               "resequence_client_serial_numbers, deduplicate_tracker_dumps, "
                               "upgrade_all_placeholder_client_names; _clean_ligature_noise_from_names "
                               "writes nothing today (it selects tracker_dump.client_name, which "
                               "doesn't exist, so its transaction always rolls back)",
    "restore_from": "replaces the database files; triggers capture no rows. Replacing the file "
                    "rewinds next_seq: open owner decision, see §9 P3-3 review fixes",
}


def _public_write_methods():
    """Public methods that write SQL themselves, or call (transitively) a method that does."""
    src = (ROOT / "database.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SeraDatabase")
    bodies = {f.name: ast.get_source_segment(src, f) for f in cls.body if isinstance(f, ast.FunctionDef)}
    writers = {n for n, b in bodies.items()
               if re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", b) and n not in ("_connect", "_connect_raw")}
    changed = True
    while changed:
        changed = False
        for n, b in bodies.items():
            if n not in writers and any(re.search(r"self\." + w + r"\(", b) for w in writers):
                writers.add(n)
                changed = True
    return {n for n in writers if not n.startswith("_")}


def test_every_public_write_method_is_listed():
    found = _public_write_methods()
    listed = set(WRITE_CASES) | set(NOT_CALLED)
    assert found - listed == set(), f"add these write methods to WRITE_CASES: {sorted(found - listed)}"


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
def test_public_write_method_produces_expected_changes(db, name):
    setup, call, required, forbidden = WRITE_CASES[name]
    ctx = setup(db) if setup else {}
    db.seal_pending()
    before = _mark(db)
    call(db, ctx)
    got = _changes(db, after=before)

    assert _pending(db) == 0, "the after-commit seal left pending rows"
    ops = _ops(got)
    assert required <= ops, f"{name}: expected {required}, got {ops}"
    if forbidden == "ALL":
        assert got == [], f"{name} writes only local tables but produced {ops}"
    else:
        assert not (forbidden & ops), f"{name}: unexpected {forbidden & ops}"
    replicated = {t.name for t in sync_schema.REGISTRY.values() if t.mode != sync_schema.LOCAL}
    for c in got:
        assert c["tbl"] in replicated
        key = json.loads(c["row_key"])
        assert isinstance(key, list) and all(isinstance(p, str) for p in key)
        if c["op"] == "upsert":
            assert "id" not in c["data"] and "gid" not in c["data"]
        if sync_schema.REGISTRY[c["tbl"]].mode == sync_schema.ADMIN_LWW:
            assert c["sig"]


# ---------------------------------------------------------------- Accept: external write, timer

def test_external_raw_write_is_captured_by_timer_seal(db):
    before = _mark(db)
    conn = _raw_conn(db.db_path, db.hex_key)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('external_key', 'from-parser')")
    conn.commit()
    conn.close()
    assert _pending(db) == 1          # captured by the trigger, nobody sealed it yet
    db.start_seal_timer(interval=0.1)
    deadline = time.time() + 5
    got = []
    while time.time() < deadline:
        got = [c for c in _changes(db, after=before) if c["tbl"] == "app_settings"]
        if got:
            break
        time.sleep(0.05)
    db.stop_seal_timer()
    assert got and json.loads(got[0]["row_key"]) == ["external_key"]
    assert got[0]["data"] == {"value": "from-parser"}
    assert _pending(db) == 0


def test_external_raw_payload_write_translates_client_to_gid(db):
    cid = _add_client(db, PAN_A)
    conn = _raw_conn(db.raw_db_path, db.hex_key)
    conn.execute("INSERT INTO tracker_dump (client_id, portal, created_at) VALUES (?, 'GST', 'x')", (cid,))
    conn.commit()
    conn.close()
    before = _mark(db)
    db.seal_pending()
    got = [c for c in _changes(db, after=before) if c["tbl"] == "tracker_dump"]
    assert len(got) == 1
    assert got[0]["data"]["client_id"] == _gid(db, "clients", cid)
    assert got[0]["origin"].endswith(":r")


# ---------------------------------------------------------------- Accept: no pending rows left

def test_no_pending_rows_left_after_seal(db):
    with db._connect() as c:
        for i in range(50):
            c.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (f"bulk_{i}", str(i)))
        c.execute("DELETE FROM app_settings WHERE key = 'bulk_3'")
        c.execute("UPDATE app_settings SET value = 'x' WHERE key = 'bulk_4'")
    # the after-commit seal already ran; seal explicitly too
    db.seal_pending()
    assert _pending(db) == 0
    conn = _raw_conn(db.raw_db_path, db.hex_key)
    conn.execute("INSERT INTO sdc_session_timelines (session_id, start_time, timeline_json, last_updated) "
                 "VALUES ('s-ext', 't', '[]', 't')")
    conn.commit()
    conn.close()
    assert _pending(db) == 1
    res = db.seal_pending()
    assert res["raw"].changes == 1
    assert _pending(db) == 0


# ---------------------------------------------------------------- Accept: HLC under a stepped-back clock

def test_hlc_monotonic_under_stepped_back_clock(db, monkeypatch):
    # wall clock in ms: forward, then stepped back (twice), briefly forward, then far back
    clock = iter([2_000_000_000_000, 2_000_000_000_500, 1_999_999_000_000, 1_999_999_000_000,
                  1_999_000_000_000, 2_000_000_001_000, 1_990_000_000_000, 1_990_000_000_000])
    monkeypatch.setattr(sync_capture, "_now_ms", lambda: next(clock))
    before = _mark(db)
    for i in range(8):
        db.set_setting(f"hlc_probe_{i}", str(i))
    hlcs = [c["hlc"] for c in _changes(db, after=before)]
    assert len(hlcs) == 8
    assert hlcs == sorted(hlcs) and len(set(hlcs)) == len(hlcs)
    parsed = [HLC.parse(h) for h in hlcs]
    assert all(p.ms >= 2_000_000_000_000 for p in parsed)  # never followed the clock backwards
    with db._connect() as c:
        last = c.execute("SELECT value FROM _sync_meta WHERE key = 'last_hlc'").fetchone()[0]
    assert last == hlcs[-1]


def test_tick_rules():
    d = "a" * 32
    assert tick("", d, now_ms=1000) == f"{1000:013d}.0000.{d}"
    assert tick(f"{1000:013d}.0000.{d}", d, now_ms=2000) == f"{2000:013d}.0000.{d}"
    assert tick(f"{5000:013d}.0003.{d}", d, now_ms=2000) == f"{5000:013d}.0004.{d}"
    assert tick(f"{5000:013d}.0003.{d}", d, now_ms=5000) == f"{5000:013d}.0004.{d}"
    # counter overflow moves to the next millisecond, keeping string order
    t = tick(f"{5000:013d}.9999.{d}", d, now_ms=10)
    assert t == f"{5001:013d}.0000.{d}" and t > f"{5000:013d}.9999.{d}"


def test_observe_rules_and_drift_guard():
    d, r = "a" * 32, "b" * 32
    now = 10_000_000
    # remote ahead (within an hour): take the remote ms, counter + 1
    assert observe(f"{now:013d}.0002.{d}", f"{now + 500:013d}.0007.{r}", d, now_ms=now) == \
        f"{now + 500:013d}.0008.{d}"
    # equal ms: max counter + 1
    assert observe(f"{now:013d}.0002.{d}", f"{now:013d}.0005.{r}", d, now_ms=now - 10) == \
        f"{now:013d}.0006.{d}"
    # physical time ahead of both: counter 0
    assert observe(f"{now:013d}.0002.{d}", f"{now - 5:013d}.0009.{r}", d, now_ms=now + 1) == \
        f"{now + 1:013d}.0000.{d}"
    # local ahead
    assert observe(f"{now + 50:013d}.0002.{d}", f"{now:013d}.0009.{r}", d, now_ms=now) == \
        f"{now + 50:013d}.0003.{d}"
    with pytest.raises(ClockAhead) as e:
        observe(f"{now:013d}.0000.{d}", f"{now + 3_600_001:013d}.0000.{r}", d, now_ms=now)
    assert e.value.ahead_ms == 3_600_001
    # exactly one hour ahead is still accepted
    observe(f"{now:013d}.0000.{d}", f"{now + 3_600_000:013d}.0000.{r}", d, now_ms=now)


def test_hlc_parse_rejects_garbage():
    for bad in ("", "123", "abc.def.x", f"{1:012d}.0000.x", f"{1:013d}.00.x"):
        with pytest.raises(ValueError):
            HLC.parse(bad)


# ---------------------------------------------------------------- sealer rules

def test_mode_off_captures_nothing(tmp_path):
    d = _make_db(tmp_path / "off", mode="off")
    d.set_setting("theme", "x")
    _add_client(d, PAN_A)
    assert _pending(d) == 0
    assert _changes(d) == []
    assert d.seal_pending() == {}


def test_applying_flag_suppresses_capture(db):
    with db._connect() as c:
        c.execute("UPDATE _sync_flags SET value = 1 WHERE name = 'applying'")
        c.execute("INSERT INTO app_settings (key, value) VALUES ('applied_remote', '1')")
        c.execute("UPDATE _sync_flags SET value = 0 WHERE name = 'applying'")
    assert _pending(db) == 0
    assert not [c for c in _changes(db) if c["row_key"] == '["applied_remote"]']


def test_first_seal_sends_all_columns_then_only_changed(db):
    before = _mark(db)
    cid = _add_client(db, PAN_A)
    first = [c for c in _changes(db, after=before) if c["tbl"] == "clients"]
    assert len(first) == 1
    assert set(first[0]["data"]) == {"notes", "created_at", "updated_at", "is_archived", "client_id_token"}
    before = _mark(db)
    db.update_client_notes(cid, "changed note")
    got = [c for c in _changes(db, after=before) if c["tbl"] == "clients"]
    assert len(got) == 1 and "notes" in got[0]["data"] and "created_at" not in got[0]["data"]
    # rewriting the same value produces no change
    before = _mark(db)
    with db._connect() as c:
        c.execute("UPDATE clients SET notes = notes WHERE id = ?", (cid,))
    assert _changes(db, after=before) == []


def test_one_hlc_per_row_and_contiguous_origin_seq(db):
    before = _mark(db)
    db.set_settings_bulk({"a1": "1", "a2": "2", "a3": "3"})
    got = _changes(db, after=before)
    seqs = [c["origin_seq"] for c in got]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    assert len({c["hlc"] for c in got}) == len(got)
    with db._connect() as c:
        stream = c.execute("SELECT value FROM _sync_meta WHERE key = 'stream_id'").fetchone()[0]
        vec = c.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (stream,)).fetchone()[0]
        nxt = int(c.execute("SELECT value FROM _sync_meta WHERE key = 'next_seq'").fetchone()[0])
    assert all(c["origin"] == stream for c in got) and stream.endswith(":m")
    assert vec == seqs[-1] == nxt - 1


def test_new_device_id_starts_a_new_stream_at_seq_1(db):
    db.set_settings_bulk({"s1": "1", "s2": "2"})
    new_dev = secrets.token_hex(16)
    db.set_sync_device_id(new_dev)
    before = _mark(db)
    db.set_setting("s3", "3")
    got = _changes(db, "master", after=before)
    assert [(c["origin"], c["origin_seq"]) for c in got] == [(f"{new_dev}:m", 1)]
    assert HLC.parse(got[0]["hlc"]).device_id == new_dev


def test_update_client_resubmit_is_not_a_delete(db):
    cid = _add_client(db, PAN_A, services=[_service(db, "GST")])
    before = _mark(db)
    db.update_client(cid, _values(db, cid), "", [_service(db, "GST")])
    got = _changes(db, after=before)
    assert not [c for c in got if c["tbl"] in ("client_values", "client_services")]
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM _sync_tombstones").fetchone()[0] == 0


def test_cleared_field_is_a_delete_with_tombstone_and_recreate_resends_all(db):
    cid = _add_client(db, PAN_A)
    col = _col(db, "NAME OF COMPANY")
    key = json.dumps([_gid(db, "clients", cid), _gid(db, "mcl_columns", col)], separators=(",", ":"))
    before = _mark(db)
    db.update_client(cid, _values(db, cid, **{"NAME OF COMPANY": None}), "", [])
    got = [c for c in _changes(db, after=before) if c["tbl"] == "client_values"]
    assert [(c["op"], c["row_key"]) for c in got] == [("delete", key)]
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM _sync_tombstones WHERE tbl='client_values' AND row_key=?",
                         (key,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM _sync_clock WHERE tbl='client_values' AND row_key=?",
                         (key,)).fetchone()[0] == 0
    before = _mark(db)
    db.update_client_single_field(cid, col, "Back Again Co", log_action=False)
    got = [c for c in _changes(db, after=before) if c["tbl"] == "client_values"]
    assert got[0]["op"] == "upsert" and got[0]["data"] == {"value": "Back Again Co"}
    with db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM _sync_tombstones WHERE row_key=?", (key,)).fetchone()[0] == 0


def test_composite_key_uses_gids_and_cascade_children_are_skipped(db):
    cid = _add_client(db, PAN_A, services=[_service(db, "GST")])
    cg = _gid(db, "clients", cid)
    before = _mark(db)
    db.delete_client(cid)
    got = _changes(db, after=before)
    assert [(c["tbl"], c["op"], json.loads(c["row_key"])) for c in got if c["op"] == "delete"] == \
        [("clients", "delete", [cg])]
    with db._connect() as c:
        rows = c.execute("SELECT row_key FROM _sync_changes WHERE tbl='client_values'").fetchall()
    assert all(json.loads(r[0])[0] == cg for r in rows)


def test_row_key_change_emits_delete_of_old_key(db):
    a = _add_client(db, PAN_A)
    b = _add_client(db, PAN_B)
    extra = db.create_mcl_column("Invented Extra", "text")
    db.update_client_single_field(a, extra, "moving value", log_action=False)
    before = _mark(db)
    with db._connect() as c:
        c.execute("UPDATE client_values SET client_id = ? WHERE client_id = ? AND column_id = ?", (b, a, extra))
    got = [(c["op"], json.loads(c["row_key"])) for c in _changes(db, after=before)]
    eg = _gid(db, "mcl_columns", extra)
    assert ("delete", [_gid(db, "clients", a), eg]) in got
    assert ("upsert", [_gid(db, "clients", b), eg]) in got


def test_cell_formatting_services_literal_and_numeric_key(db):
    cid = _add_client(db, PAN_A)
    before = _mark(db)
    db.bulk_set_cell_formatting([{"client_id": cid, "column_key": "services", "bg_color": "#010203"},
                                 {"client_id": cid, "column_key": str(_col(db, "PAN")), "bg_color": "#040506"}])
    keys = {c["row_key"] for c in _changes(db, after=before) if c["tbl"] == "cell_formatting"}
    cg = _gid(db, "clients", cid)
    assert keys == {json.dumps([cg, "services"], separators=(",", ":")),
                    json.dumps([cg, _gid(db, "mcl_columns", _col(db, "PAN"))], separators=(",", ":"))}
    before = _mark(db)
    db.clear_cell_formatting([(cid, "services")])
    got = _changes(db, after=before)
    assert [(c["op"], json.loads(c["row_key"])) for c in got] == [("delete", [cg, "services"])]


def test_fk_columns_hold_gids(db):
    col_u, col_p = _col(db, "USER ID"), _col(db, "GST_Password")
    before = _mark(db)
    db.create_service("Invented Portal", "https://portal.invalid/", col_u, col_p, "#u", "#p")
    svc = [c for c in _changes(db, after=before) if c["tbl"] == "services"][0]
    assert svc["data"]["userid_column_id"] == _gid(db, "mcl_columns", col_u)
    assert svc["data"]["password_column_id"] == _gid(db, "mcl_columns", col_p)
    cid = _add_client(db, PAN_A)
    before = _mark(db)
    db.log_action("Tester", "x", client_id=cid, service_id=_service(db, "GST"))
    log = [c for c in _changes(db, after=before) if c["tbl"] == "audit_log"][0]
    assert log["data"]["client_id"] == _gid(db, "clients", cid)
    assert log["data"]["service_id"] == _gid(db, "services", _service(db, "GST"))
    # unknown referent -> NULL, never the local id
    before = _mark(db)
    db.log_action("Tester", "y", client_id=987654)
    log = [c for c in _changes(db, after=before) if c["tbl"] == "audit_log"][0]
    assert log["data"]["client_id"] is None


def test_admin_scoped_changes_are_signed_and_verify(db, admin_key):
    from sync_admin import public_key_b64
    before = _mark(db)
    db.update_staff_alias(1, "Desk-3")
    got = [c for c in _changes(db, after=before) if c["tbl"] == "staff_users"]
    assert len(got) == 1
    pub = public_key_b64(admin_key)
    assert sync_capture.verify_change(got[0], pub)
    tampered = dict(got[0], data={"alias": "Desk-4"})
    assert not sync_capture.verify_change(tampered, pub)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    assert not sync_capture.verify_change(got[0], public_key_b64(Ed25519PrivateKey.generate()))


def test_admin_scoped_change_without_admin_key_is_not_sealed(tmp_path, caplog):
    d = _make_db(tmp_path / "member")
    d._admin_signer = lambda: None
    before = _mark(d)
    with caplog.at_level("WARNING", logger="sera.sync.capture"):
        d.update_staff_alias(1, "Desk-5")
        d.set_setting("theme", "still-sealed")
    got = _changes(d, after=before)
    assert _ops(got) == {("app_settings", "upsert")}
    assert _pending(d) == 0
    assert "office admin key" in caplog.text


def test_seal_does_not_recurse_or_break_on_nested_connections(db):
    before = _mark(db)
    with db._connect() as outer:
        outer.execute("INSERT INTO app_settings (key, value) VALUES ('outer', '1')")
        with db._connect_raw() as inner:
            inner.execute("INSERT INTO sdc_session_timelines (session_id, start_time, timeline_json, "
                          "last_updated) VALUES ('s-nested', 't', '[]', 't')")
    ops = _ops(_changes(db, after=before))
    assert {("app_settings", "upsert"), ("sdc_session_timelines", "upsert")} <= ops
    assert _pending(db) == 0


def test_seal_failure_never_breaks_the_write(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("sealer down")
    monkeypatch.setattr(sync_capture, "seal", boom)
    db.set_setting("still_written", "yes")
    assert db.get_setting("still_written") == "yes"
    assert _pending(db) == 1   # left for the timer
    monkeypatch.undo()
    db.seal_pending()
    assert _pending(db) == 0


def test_triggers_exist_for_every_replicated_table_and_survive_reinit(tmp_path):
    from database import SeraDatabase
    d = _make_db(tmp_path / "t")
    for name, opener, db_name in (("master", d._connect, "master"), ("raw", d._connect_raw, "raw")):
        with opener() as c:
            trig = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            live = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for spec in sync_schema.replicated_tables_for(db_name):
            if spec.name in live:
                assert set(sync_capture.trigger_names(spec.name)) <= trig, spec.name
        for spec in sync_schema.tables_for(db_name):
            if spec.mode == sync_schema.LOCAL:
                assert not any(t.endswith("_" + spec.name) and t.startswith("_sync_") for t in trig)
    # opening again (migration re-runs) keeps working and keeps the mode
    d2 = SeraDatabase(d.db_path, d.hex_key, raw_db_path=d.raw_db_path,
                      defer_startup_maintenance=True, key_mode="legacy")
    assert d2.get_sync_mode() == "shadow"
    before = _mark(d2)
    d2.insert_tracker_dump(portal="GST", period_label="Jul-2026", arn_number="AA0707260000004")
    assert ("tracker_dump", "upsert") in _ops(_changes(d2, after=before))


def test_no_pyside6_import():
    tree = ast.parse((ROOT / "sync_capture.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any("PySide6" in a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and "PySide6" in node.module)


# ---------------------------------------------------------------- review fixes

def test_change_signature_is_domain_separated_from_admin_records(db, admin_key):
    from sync_admin import public_key_b64
    import sync_admin
    before = _mark(db)
    db.update_staff_alias(1, "Desk-8")
    change = [c for c in _changes(db, after=before) if c["tbl"] == "staff_users"][0]
    pub = public_key_b64(admin_key)
    # a raw signature over the undecorated body (the record format) is not a valid change signature
    body_sig = base64.b64encode(admin_key.sign(sync_capture.canonical_json(
        sync_capture.change_body(change)))).decode("ascii")
    assert not sync_capture.verify_change(dict(change, sig=body_sig), pub)
    # and a change signature doesn't verify as a record signature over the same body
    rec = dict(sync_capture.change_body(change), sig=change["sig"])
    with pytest.raises(sync_admin.SyncAdminError):
        sync_admin.verify_record(rec, pub)


def test_seal_in_small_batches_keeps_net_effect_and_numbering(db):
    cid = _add_client(db, PAN_A, services=[_service(db, "GST")])
    before = _mark(db)
    # another process's write: captured by the triggers, not sealed yet
    conn = _raw_conn(db.db_path, db.hex_key)
    conn.execute("DELETE FROM client_values WHERE client_id = ?", (cid,))
    conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                 (cid, _col(db, "PAN"), PAN_A))
    conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                 (cid, _col(db, "NAME OF COMPANY"), "Batch Renamed Co"))
    for i in range(7):
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (f"batch_{i}", str(i)))
    conn.commit()
    conn.close()
    res = sync_capture.seal(db.db_path, db.hex_key, "master", batch_rows=3)
    assert _pending(db) == 0
    got = _changes(db, "master", after=before)
    assert res.changes == len(got)
    seqs = [c["origin_seq"] for c in got]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    cv = [c for c in got if c["tbl"] == "client_values"]
    # "No." was deleted and not re-inserted -> the only delete; PAN unchanged -> nothing
    assert [c["op"] for c in cv].count("delete") == 1
    assert {c["op"] for c in cv if c["data"] and c["data"].get("value") == "Batch Renamed Co"} == {"upsert"}
    assert len([c for c in got if c["tbl"] == "app_settings"]) == 7


def test_raw_payload_mode_follows_master_at_startup(tmp_path):
    from database import SeraDatabase
    d = _make_db(tmp_path / "heal")
    with d._connect_raw() as c:
        c.execute("UPDATE _sync_meta SET value = 'off' WHERE key = 'mode'")
    d2 = SeraDatabase(d.db_path, d.hex_key, raw_db_path=d.raw_db_path,
                      defer_startup_maintenance=True, key_mode="legacy")
    with d2._connect_raw() as c:
        assert c.execute("SELECT value FROM _sync_meta WHERE key = 'mode'").fetchone()[0] == "shadow"
    before = _mark(d2)
    d2.upsert_sdc_session_timeline({"session_id": "after-heal", "pan": PAN_A, "timeline": []})
    assert ("sdc_session_timelines", "upsert") in _ops(_changes(d2, after=before))


def test_set_sync_mode_refuses_without_device_id(tmp_path):
    from database import SeraDatabase
    import secrets as _s
    (tmp_path / "nodev").mkdir()
    d = SeraDatabase(str(tmp_path / "nodev" / "master.db"), _s.token_hex(32),
                     raw_db_path=str(tmp_path / "nodev" / "rawPayload.db"),
                     defer_startup_maintenance=True, key_mode="legacy")
    with pytest.raises(ValueError):
        d.set_sync_mode("shadow")
    assert d.get_sync_mode() == "off"
    d.set_sync_mode("off")


def test_failed_connect_does_not_leak_connection_depth(db, monkeypatch):
    import database
    real = database.sqlite3.connect

    def fail(*a, **k):
        raise database.sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(database.sqlite3, "connect", fail)
    for opener in (db._connect, db._connect_raw):
        with pytest.raises(Exception):
            with opener():
                pass
    monkeypatch.setattr(database.sqlite3, "connect", real)
    assert getattr(db._local, "conn_depth", 0) == 0
    before = _mark(db)
    db.set_setting("after_failed_connect", "1")
    assert _pending(db) == 0 and _changes(db, after=before)


# ---------------------------------------------------------------- seq high-water mark (owner option A)

def _copy_db_files(src_dir: Path, dst_dir: Path):
    import shutil
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in ("master.db", "rawPayload.db"):
        shutil.copy2(src_dir / name, dst_dir / name)


def test_replaced_db_file_never_reuses_sequence_numbers(tmp_path):
    from database import SeraDatabase
    d = _make_db(tmp_path / "pc")
    d.set_setting("before_backup", "1")
    d.stop_seal_timer()
    # "backup": checkpoint and copy the files while nothing is open
    with d._connect() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with d._connect_raw() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _copy_db_files(tmp_path / "pc", tmp_path / "backup")
    backup_top = max(c["origin_seq"] for c in _changes(d, "master"))
    d.set_settings_bulk({"after_1": "1", "after_2": "2", "after_3": "3"})
    sent = _changes(d, "master")
    top = max(c["origin_seq"] for c in sent)
    stream = sent[0]["origin"]
    state = json.loads((tmp_path / "pc" / "keys" / "sync_seq.json").read_text(encoding="utf-8"))
    assert state[stream] == top

    # restore the older file (as restore_from / a staged swap would)
    for s in ("-wal", "-shm"):
        for name in ("master.db", "rawPayload.db"):
            p = tmp_path / "pc" / (name + s)
            if p.exists():
                p.rename(p.with_name(p.name + ".old"))
    _copy_db_files(tmp_path / "backup", tmp_path / "pc")
    d2 = SeraDatabase(d.db_path, d.hex_key, raw_db_path=d.raw_db_path,
                      defer_startup_maintenance=True, key_mode="legacy")
    # the restored file really is the older one: the three later changes aren't in it
    assert not [c for c in _changes(d2, "master") if c["row_key"] == '["after_1"]']
    d2.set_setting("after_restore", "1")
    # everything sealed after the restore (start-up writes included) continues after the
    # mark, with no gap, and never reuses backup_top+1..top
    new = [c["origin_seq"] for c in _changes(d2, "master")
           if c["origin"] == stream and c["origin_seq"] > backup_top]
    assert new and new == list(range(top + 1, top + 1 + len(new)))


def test_new_stream_after_rejoin_respects_the_mark(db):
    new_dev = secrets.token_hex(16)
    stream = f"{new_dev}:m"
    sync_capture.record_seq_mark(sync_capture.seq_state_path(db.db_path), stream, 41)
    db.set_sync_device_id(new_dev)
    before = _mark(db)
    db.set_setting("rejoined", "1")
    assert [c["origin_seq"] for c in _changes(db, "master", after=before)] == [42]


def test_seq_mark_only_goes_up(tmp_path):
    path = str(tmp_path / "keys" / "sync_seq.json")
    assert sync_capture.read_seq_mark(path, "d:m") == 0
    sync_capture.record_seq_mark(path, "d:m", 10)
    sync_capture.record_seq_mark(path, "d:m", 4)
    sync_capture.record_seq_mark(path, "d:r", 2)
    assert sync_capture.read_seq_mark(path, "d:m") == 10
    assert sync_capture.read_seq_mark(path, "d:r") == 2


def test_unreadable_seq_mark_stops_sealing_and_keeps_pending(db):
    path = Path(sync_capture.seq_state_path(db.db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    before = _mark(db)
    db.set_setting("blocked", "1")            # the write itself still succeeds
    assert db.get_setting("blocked") == "1"
    assert _changes(db, after=before) == []
    assert _pending(db) == 1
    with pytest.raises(sync_capture.SeqStateError):
        db.seal_pending()
    path.write_text("{}", encoding="utf-8")
    db.seal_pending()
    assert _pending(db) == 0 and len(_changes(db, after=before)) == 1
