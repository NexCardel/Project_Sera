"""
sera_db/sdis.py - Sera Distill (SDIS Part Q): registered datapoints and the user's decisions.

Both tables live in rawPayload.db and replicate like tracker_dump (sync_schema.py), so every PC
has them. A field is never deleted: retiring it is an UPDATE of status, so the retire reaches
every PC. After a local change of sdis_fields this PC rewrites <Sera data>/sdis_fields.json
(core/sdis/register.py), the spec file SGT loads third. Mixed into SeraDatabase.
"""

import datetime
import json
import uuid


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


_FIELD_COLS = ("gid", "name", "portal", "section", "spec_json", "label", "status", "created_by", "updated_at")


class SdisMixin:

    def add_sdis_field(self, name: str, portal: str, section: str, spec: dict, label: str = "",
                       created_by: str = "") -> str:
        """Registers (or re-activates) a field by its spec name; returns its gid. A label the
        user already gave the field is kept: only an empty label is filled in."""
        spec_json = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        with self._connect_raw() as conn:
            row = conn.execute("SELECT gid, label FROM sdis_fields WHERE name = ? ORDER BY id LIMIT 1",
                               (name,)).fetchone()
            if row:
                gid = row[0]
                conn.execute(
                    "UPDATE sdis_fields SET portal = ?, section = ?, spec_json = ?, label = ?, "
                    "status = 'active', updated_at = ? WHERE gid = ?",
                    (portal, section, spec_json, row[1] or label, _now(), gid))
            else:
                gid = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO sdis_fields (gid, name, portal, section, spec_json, label, status, "
                    "created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (gid, name, portal, section, spec_json, label, created_by, _now()))
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
