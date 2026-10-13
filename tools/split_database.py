"""
One-shot, behaviour-preserving split of database.py into mixin modules under sera_db/.

Moves methods by name (text is copied verbatim, nothing is rewritten), writes each mixin with
only the module-level names it uses, and leaves database.py with the core connection/seal/sync
code. Run from the project root:  python tools/split_database.py [--dry-run]
"""
import ast
import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "database.py")
PKG = os.path.join(ROOT, "sera_db")

GROUPS = {
    "schema": ("SchemaMixin", "Schema creation, migrations, auto-heal and first-run seeding.", """
        _migrate_legacy_peer_logs _ensure_column _migrate_tracker_dump_nullable _auto_heal_raw_db
        _init_raw_schema _migrate_tracker_dump_to_raw_payload_db _init_schema _find_ini_file
        _seed_from_ini _seed_default_data load_ini_defaults"""),
    "settings": ("SettingsMixin", "App settings, SCC passwords and the staff alias matrix.", """
        get_setting get_all_settings set_setting set_settings_bulk extract_pan_components
        generate_scc_passwords get_scc_settings save_scc_settings get_staff_matrix
        assign_or_get_alias update_staff_alias reset_staff_matrix add_staff_user remove_staff_user"""),
    "mcl_services": ("MclServicesMixin", "Master Column List (MCL) columns and portal services.", """
        get_mcl_columns get_id_column get_identity_column create_mcl_column update_mcl_column
        delete_mcl_column reorder_mcl_columns bulk_update_mcl_visibility bulk_update_mcl_quick_copy
        bulk_update_mcl_admin_visibility get_services get_service create_service update_service
        auto_populate_service_selectors delete_service get_service_for_portal"""),
    "clients": ("ClientsMixin", "Client CRUD, bulk operations, activity, duplicates, notes, cell formatting, CSV export.", """
        SERVICES_HEADER_ALIASES SYSTEM_HEADERS record_client_activity get_recent_client_activities
        get_all_activity_stats search_clients _fetch_client_full get_client get_client_services
        _validate_internal_pk_values add_client update_client archive_client unarchive_client
        delete_client find_duplicate_clients get_client_by_pan get_client_pan
        update_client_single_field is_client_scc_verified tag_client_scc_verified bulk_import_clients
        bulk_archive_clients bulk_unarchive_clients resequence_client_serial_numbers
        bulk_delete_clients purge_duplicate_clients bulk_set_service export_clients_csv
        export_mcl_schema_csv update_client_notes bulk_set_cell_formatting clear_cell_formatting
        get_cell_formatting_for_clients"""),
    "audit_backup": ("AuditBackupMixin", "Audit log, backup / restore and audit export.", """
        log_action get_audit_logs backup_to restore_from export_audit_log_csv"""),
    "srpf": ("SrpfMixin", "SRPF client containers and identity resolution for captures.", """
        _extract_identity_candidates_from_payload _update_srpf_container get_client_raw_container
        _fetch_clients_batch get_srpf_containers delete_srpf_container
        _resolve_session_proximity_candidate get_srpf_container_media save_srpf_container_media"""),
    "tracker_dump": ("TrackerDumpMixin", "Tracker Dump captures: insert, de-duplicate, peers, re-resolve, read, delete.", """
        compute_dataset_key delete_sgt_rows_by_dataset_key _convert_sgt_shadow_rows
        insert_tracker_dump deduplicate_tracker_dumps store_peer_tracker_dumps
        upsert_sdc_session_timeline link_unassigned_tracker_dumps re_resolve_all_tracker_dumps
        get_tracker_dumps get_tracker_dump_media save_tracker_dump_media delete_tracker_dump
        clear_tracker_dumps"""),
    "maintenance": ("MaintenanceMixin", "Startup maintenance, name clean-up and storage optimisation.", """
        _ICON_LIGATURE_RE _ROLE_TAG_RE run_startup_maintenance _clean_name_retrofix
        _clean_ligature_noise_from_names _upgrade_client_name_if_placeholder
        upgrade_all_placeholder_client_names optimize_storage"""),
}
# Everything not listed above stays on SeraDatabase in database.py (connections, sealing, sync mode).

COMMON_NAMES = ["sqlite3", "datetime", "json", "os", "sys", "shutil", "time", "threading", "re",
                "contextmanager", "security", "_compute_dataset_key", "DB_FILENAME",
                "SKELETON_NAME_REGEX", "DatabaseError"]
IMPORT_LINES = {
    "sqlite3": "import sqlcipher3.dbapi2 as sqlite3", "datetime": "import datetime", "json": "import json",
    "os": "import os", "sys": "import sys", "shutil": "import shutil", "time": "import time",
    "threading": "import threading", "re": "import re", "contextmanager": "from contextlib import contextmanager",
    "security": "import security",
    "_compute_dataset_key": "from core.dataset_key import compute_dataset_key as _compute_dataset_key",
}


def main(dry: bool) -> int:
    text = open(SRC, encoding="utf-8").read()
    lines = text.split("\n")
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SeraDatabase")

    # members with the comment/blank lines that lead into each one
    members = []
    prev_end = cls.lineno
    for node in cls.body:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        name = node.name if isinstance(node, ast.FunctionDef) else node.targets[0].id
        members.append((name, prev_end + 1, node.end_lineno))   # 1-based inclusive
        prev_end = node.end_lineno
    names = [m[0] for m in members]
    assert len(names) == len(set(names)), "duplicate member names"

    assigned = {}
    for key, (_cn, _doc, ms) in GROUPS.items():
        for n in ms.split():
            assert n in names, f"{n} (group {key}) is not a member of SeraDatabase"
            assert n not in assigned, f"{n} assigned twice"
            assigned[n] = key

    def chunk(m):
        return "\n".join(lines[m[1] - 1:m[2]])

    core_members = [m for m in members if m[0] not in assigned]
    by_group = {k: [m for m in members if assigned.get(m[0]) == k] for k in GROUPS}

    # module-level names defined in database.py, used to detect what each part needs
    module_names = set(COMMON_NAMES) | {"_WRITE_GENERATION", "_WRITE_GENERATION_LOCK", "make_snapshot"}
    core_only = {"_WRITE_GENERATION", "_WRITE_GENERATION_LOCK", "make_snapshot"}

    def used_names(src_text):
        t = ast.parse("class _X:\n" + "\n".join("    " + l if l.strip() else l for l in src_text.split("\n")))
        return {n.id for n in ast.walk(t) if isinstance(n, ast.Name)} & module_names

    out_files = {}
    for key, (cname, doc, _ms) in GROUPS.items():
        body = "\n".join(chunk(m) for m in by_group[key])
        used = used_names(body)
        bad = used & core_only
        assert not bad, f"{key} uses core-only names {bad}"
        imps = [IMPORT_LINES[n] for n in COMMON_NAMES if n in used and n in IMPORT_LINES]
        commons = [n for n in ("DB_FILENAME", "SKELETON_NAME_REGEX", "DatabaseError") if n in used]
        head = f'"""\nsera_db/{key}.py - {doc}\n\nSplit out of database.py; methods are unchanged. Mixed into SeraDatabase.\n"""\n\n'
        head += "\n".join(imps) + ("\n" if imps else "")
        if commons:
            head += "\nfrom sera_db.common import " + ", ".join(commons) + "\n"
        out_files[f"{key}.py"] = head + f"\n\nclass {cname}:\n" + body.rstrip("\n") + "\n"

    # common.py: header constants lifted from database.py
    skel_start = next(i for i, l in enumerate(lines) if l.startswith("SKELETON_NAME_REGEX"))
    skel_end = next(i for i in range(skel_start, len(lines)) if lines[i].startswith("    re.I"))
    skel_end += 1  # the closing ")" line
    while not lines[skel_end].startswith(")"):
        skel_end += 1
    skel_text = "\n".join(lines[skel_start:skel_end + 1])
    common = ('"""\nsera_db/common.py - names shared by database.py and its mixins.\n"""\n\n'
              "import re\n\n"
              'DB_FILENAME = "master.db"\n\n' + skel_text + "\n\n\nclass DatabaseError(Exception):\n    pass\n")
    out_files["common.py"] = common
    out_files["__init__.py"] = '"""SeraDatabase mixins (split out of database.py)."""\n'

    # new database.py
    mixin_imports = "\n".join(f"from sera_db.{k} import {GROUPS[k][0]}" for k in GROUPS)
    mixin_names = ", ".join(GROUPS[k][0] for k in GROUPS)
    core_body = "\n".join(chunk(m) for m in core_members)
    gen_start = next(i for i, l in enumerate(lines) if l.startswith("# Rows changed through master.db"))
    gen_end = next(i for i in range(gen_start, len(lines)) if lines[i].startswith("_WRITE_GENERATION_LOCK"))
    gen_text = "\n".join(lines[gen_start:gen_end + 1])
    tail = "\n".join(lines[cls.end_lineno:])      # make_snapshot and anything after the class
    used_core = used_names(core_body) | ({n.id for n in ast.walk(ast.parse(tail)) if isinstance(n, ast.Name)} & module_names)
    core_imps = [IMPORT_LINES[n] for n in COMMON_NAMES if n in used_core and n in IMPORT_LINES]
    core_head = lines[0:next(i for i, l in enumerate(lines) if l.startswith("import sqlcipher3"))]
    new_db = "\n".join(core_head) + "\n" + "\n".join(core_imps) + "\n\n"
    new_db += ("# Re-exported: other modules import these from `database`.\n"
               "from sera_db.common import DB_FILENAME, SKELETON_NAME_REGEX, DatabaseError  # noqa: F401\n"
               + mixin_imports + "\n\n\n" + gen_text + "\n\n\n")
    cls_decl = lines[cls.lineno - 1]
    new_db += cls_decl.replace("SeraDatabase:", f"SeraDatabase({mixin_names}):").replace("SeraDatabase()", f"SeraDatabase({mixin_names})")
    new_db += "\n" + core_body.rstrip("\n") + "\n" + ("\n" + tail if tail.strip() else "")
    # sanity: every module still parses
    for fname, src in list(out_files.items()) + [("database.py", new_db)]:
        ast.parse(src)

    total = sum(len(s.split("\n")) for s in out_files.values()) + len(new_db.split("\n"))
    print(f"{len(out_files)} new files + database.py ({len(new_db.split(chr(10)))} lines); total {total} lines (was {len(lines)})")
    for f, s in out_files.items():
        print(f"  sera_db/{f:<18}{len(s.split(chr(10))):>5} lines")
    if dry:
        return 0
    os.makedirs(PKG, exist_ok=True)
    for f, s in out_files.items():
        open(os.path.join(PKG, f), "w", encoding="utf-8", newline="\n").write(s)
    open(SRC, "w", encoding="utf-8", newline="\n").write(new_db)
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
