"""
sera_db/sdis.py - Sera Distill (SDIS Parts Q, U, S.3): registered datapoints, the user's
decisions, the field library (sdis_mcl) and the containers document (sdis_config).

Every table lives in rawPayload.db and replicates like tracker_dump (sync_schema.py), so every PC
has them. A field is never deleted: retiring it is an UPDATE of status, so the retire reaches
every PC. After a local change of sdis_fields this PC rewrites <Sera data>/sdis_fields.json
(core/sdis/register.py), the spec file SGT loads third; after a put of the containers document it
rewrites <Sera data>/sdis_containers.json (core/sdis/config.py). Mixed into SeraDatabase.
"""

import datetime
import hashlib
import json
import uuid


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


_FIELD_COLS = ("gid", "name", "portal", "section", "spec_json", "label", "status", "created_by", "updated_at",
               "mcl_gid")
_MCL_COLS = ("gid", "name", "label", "value_type", "class", "portal", "status", "created_by", "updated_at")
CONTAINERS_NAME = "containers"
# The one sdis_config row has the same gid on every PC, so two PCs that create it at once write
# the same row (LWW) instead of two.
CONTAINERS_GID = hashlib.md5(b"sdis_config:containers").hexdigest()


class SdisMixin:

    def add_sdis_field(self, name: str, portal: str, section: str, spec: dict, label: str = "",
                       created_by: str = "", mcl_gid: str = "") -> str:
        """Registers (or re-activates) a field by its spec name; returns its gid. A label the
        user already gave the field is kept: only an empty label is filled in."""
        spec_json = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        with self._connect_raw() as conn:
            row = conn.execute("SELECT gid, label, mcl_gid FROM sdis_fields WHERE name = ? ORDER BY id LIMIT 1",
                               (name,)).fetchone()
            if row:
                gid = row[0]
                conn.execute(
                    "UPDATE sdis_fields SET portal = ?, section = ?, spec_json = ?, label = ?, "
                    "status = 'active', mcl_gid = ?, updated_at = ? WHERE gid = ?",
                    (portal, section, spec_json, row[1] or label, mcl_gid or row[2], _now(), gid))
            else:
                gid = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO sdis_fields (gid, name, portal, section, spec_json, label, status, "
                    "created_by, updated_at, mcl_gid) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                    (gid, name, portal, section, spec_json, label, created_by, _now(), mcl_gid or None))
        self._sdis_fields_changed()
        return gid

    def rename_sdis_field(self, gid: str, label: str) -> bool:
        """Changes only the label; what SGT captures stays the same."""
        with self._connect_raw() as conn:
            cur = conn.execute("UPDATE sdis_fields SET label = ?, updated_at = ? WHERE gid = ?",
                               (label, _now(), gid))
            done = cur.rowcount > 0
        if done:
            self._sdis_fields_changed()
        return done

    def retire_sdis_field(self, gid: str) -> bool:
        with self._connect_raw() as conn:
            cur = conn.execute(
                "UPDATE sdis_fields SET status = 'retired', updated_at = ? WHERE gid = ? AND status != 'retired'",
                (_now(), gid))
            done = cur.rowcount > 0
        if done:
            self._sdis_fields_changed()
        return done

    def list_sdis_fields(self, active_only: bool = False) -> list[dict]:
        sql = f"SELECT {', '.join(_FIELD_COLS)} FROM sdis_fields"
        if active_only:
            sql += " WHERE status = 'active'"
        with self._connect_raw() as conn:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(zip(_FIELD_COLS, r))
            try:
                d["spec"] = json.loads(d["spec_json"] or "{}")
            except ValueError:
                d["spec"] = {}
            out.append(d)
        return out

    def _sdis_fields_changed(self) -> None:
        try:
            from core.sdis.register import write_fields_file
            write_fields_file(self.list_sdis_fields(active_only=True))
        except Exception as e:
            print(f"[database] sdis_fields.json not written: {type(e).__name__}")

    def set_sdis_decision(self, signature: str, decision: str, label: str = "") -> str:
        """One row per signature (a rejected variable_alignment, a dismissed datapoint, a container
        move); a later decision on the same signature replaces the earlier one. Returns its gid."""
        with self._connect_raw() as conn:
            row = conn.execute("SELECT gid FROM sdis_decisions WHERE signature = ? ORDER BY id LIMIT 1",
                               (signature,)).fetchone()
            if row:
                gid = row[0]
                conn.execute("UPDATE sdis_decisions SET decision = ?, label = ?, updated_at = ? WHERE gid = ?",
                             (decision, label, _now(), gid))
            else:
                gid = uuid.uuid4().hex
                conn.execute("INSERT INTO sdis_decisions (gid, signature, decision, label, updated_at) "
                             "VALUES (?, ?, ?, ?, ?)", (gid, signature, decision, label, _now()))
        return gid

    def list_sdis_decisions(self) -> list[dict]:
        cols = ("gid", "signature", "decision", "label", "updated_at")
        with self._connect_raw() as conn:
            rows = conn.execute(f"SELECT {', '.join(cols)} FROM sdis_decisions ORDER BY id").fetchall()
        return [dict(zip(cols, r)) for r in rows]

    # ── sdis_mcl: the field library (Part U) ─────────────────────────────────────

    def add_sdis_mcl(self, name: str, label: str = "", value_type: str = "", cls: str = "",
                     portal: str = "all", created_by: str = "") -> str:
        """Adds (or re-activates) a field by name; returns its gid. A label the user already
        gave the field is kept; a field seen on a second portal becomes portal 'all'."""
        with self._connect_raw() as conn:
            row = conn.execute("SELECT gid, label, portal FROM sdis_mcl WHERE name = ? ORDER BY id LIMIT 1",
                               (name,)).fetchone()
            if row:
                gid = row[0]
                both = row[2] if row[2] in (portal, "all") or not portal else "all"
                conn.execute("UPDATE sdis_mcl SET label = ?, portal = ?, status = 'active', updated_at = ? "
                             "WHERE gid = ?", (row[1] or label, both, _now(), gid))
            else:
                gid = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO sdis_mcl (gid, name, label, value_type, class, portal, status, created_by, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (gid, name, label, value_type, cls, portal or "all", created_by, _now()))
        return gid

    def rename_sdis_mcl(self, gid: str, label: str) -> bool:
        """Renames a field everywhere (R6): its label and the label of every spec of it. The name
        containers list it by, and what SGT captures, stay the same."""
        with self._connect_raw() as conn:
            cur = conn.execute("UPDATE sdis_mcl SET label = ?, updated_at = ? WHERE gid = ?", (label, _now(), gid))
            done = cur.rowcount > 0
            specs = conn.execute("UPDATE sdis_fields SET label = ?, updated_at = ? WHERE mcl_gid = ?",
                                 (label, _now(), gid)).rowcount if done else 0
        if specs:
            self._sdis_fields_changed()
        return done

    def retire_sdis_mcl(self, gid: str) -> bool:
        with self._connect_raw() as conn:
            cur = conn.execute(
                "UPDATE sdis_mcl SET status = 'retired', updated_at = ? WHERE gid = ? AND status != 'retired'",
                (_now(), gid))
            return cur.rowcount > 0

    def list_sdis_mcl(self, active_only: bool = False) -> list[dict]:
        sql = f"SELECT {', '.join(_MCL_COLS)} FROM sdis_mcl"
        if active_only:
            sql += " WHERE status = 'active'"
        with self._connect_raw() as conn:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
        return [dict(zip(_MCL_COLS, r)) for r in rows]

    # ── sdis_config: the containers document (Part S.3), one row ─────────────────

    def get_sdis_containers(self):
        """{doc, version, updated_by, updated_at} or None when it was never put."""
        with self._connect_raw() as conn:
            row = conn.execute(
                "SELECT doc_json, version, updated_by, updated_at FROM sdis_config WHERE name = ? "
                "ORDER BY version DESC, updated_at DESC LIMIT 1", (CONTAINERS_NAME,)).fetchone()
        if not row:
            return None
        try:
            doc = json.loads(row[0])
        except ValueError:
            return None
        return {"doc": doc, "version": row[1] or 0, "updated_by": row[2], "updated_at": row[3]}

    def put_sdis_containers(self, doc: dict, updated_by: str = "", portals=None) -> int:
        """Checks the document (every S.3 load check, field names against sdis_mcl, portals
        against the registered ones) and stores it as version + 1; a failing document raises
        core.sdis.config.ConfigError and the stored one stays. Returns the new version."""
        from core.sdis import config
        known = config.known_names(self) if portals is None else {
            "fields": [r["name"] for r in self.list_sdis_mcl()], "portals": list(portals)}
        config.check(doc, **known)
        doc_json = json.dumps(doc, ensure_ascii=False)
        with self._connect_raw() as conn:
            row = conn.execute("SELECT gid, version FROM sdis_config WHERE name = ? "
                               "ORDER BY version DESC LIMIT 1", (CONTAINERS_NAME,)).fetchone()
            version = (row[1] or 0) + 1 if row else 1
            if row:
                conn.execute("UPDATE sdis_config SET doc_json = ?, version = ?, updated_by = ?, updated_at = ? "
                             "WHERE gid = ?", (doc_json, version, updated_by, _now(), row[0]))
            else:
                conn.execute("INSERT INTO sdis_config (gid, name, doc_json, version, updated_by, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (CONTAINERS_GID, CONTAINERS_NAME, doc_json, version, updated_by, _now()))
        self._sdis_config_changed()
        return version

    def _sdis_config_changed(self) -> None:
        try:
            from core.sdis.config import refresh
            refresh(self)
        except Exception as e:
            print(f"[database] sdis_containers.json not written: {type(e).__name__}")
