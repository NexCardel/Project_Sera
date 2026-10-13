"""
sera_db/audit_backup.py - Audit log, backup / restore and audit export.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import sqlcipher3.dbapi2 as sqlite3
import datetime
import os
import shutil
import security


class AuditBackupMixin:

    # ---------------- Audit Log ----------------

    def log_action(self, actor: str, action: str, client_id: int = None, service_id: int = None, detail: str = None):
        actor_name = actor or "System"
        ts = datetime.datetime.utcnow().isoformat()
        if not detail:
            if action == "view":
                detail = f"Viewed client profile" if client_id else "Viewed client list"
            elif action == "manual_assist":
                detail = "Manual assist triggered"
            elif action == "autofill":
                detail = "Autofill triggered"
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO audit_log (ts, actor, action, client_id, service_id, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, actor_name, action, client_id, service_id, detail)
            )
        
        # Automatically record client activity breadcrumbs on mutations
        if client_id:
            try:
                act_norm = str(action).lower()
                tag = "Edited"
                if "update" in act_norm or "edit" in act_norm or "save" in act_norm:
                    tag = "Edited"
                elif "create" in act_norm or "add" in act_norm:
                    tag = "Created"
                elif "archive" in act_norm:
                    tag = "Archived"
                elif "unarchive" in act_norm or "restore" in act_norm:
                    tag = "Restored"
                elif "note" in act_norm:
                    tag = "Note"
                elif "service" in act_norm:
                    tag = "Services"
                elif "view" in act_norm:
                    tag = "Viewed"
                elif "copy" in act_norm:
                    tag = "Copied"
                elif "sca" in act_norm:
                    tag = "SCA"
                else:
                    tag = action.capitalize()[:10]
                self.record_client_activity(int(client_id), tag, detail or f"{tag} by {actor_name}")
            except Exception:
                pass

        # "view" is read-only and shouldn't trigger a sync push; everything
        # else logged here already represents a real data mutation somewhere
        # in this module, so it's the cheapest reliable place to hook.
        if action != "view":
            self._bump_sync_revision_if_configured()

    def get_audit_logs(
        self,
        client_id: int = None,
        actor: str = None,
        action: str = None,
        from_date: str = None,
        to_date: str = None,
        resolve_names: bool = True,
        limit: int = 500
    ) -> list[dict]:
        with self._connect() as conn:
            sql = "SELECT id, ts, actor, action, client_id, service_id, detail FROM audit_log"
            where = []
            params = []
            if client_id is not None:
                where.append("client_id = ?")
                params.append(client_id)
            if actor:
                where.append("actor LIKE ?")
                params.append(f"%{actor}%")
            if action and action != "All Actions":
                where.append("action = ?")
                params.append(action)
            if from_date:
                where.append("ts >= ?")
                params.append(from_date)
            if to_date:
                if to_date.endswith("T00:00:00"):
                    where.append("ts < ?")
                else:
                    where.append("ts <= ?")
                params.append(to_date)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            rows = cur.fetchall()

            client_name_map = {}
            if resolve_names:
                # Only look up the clients that actually appear in these rows --
                # scanning every client's values on each refresh froze the UI.
                wanted = sorted({r[4] for r in rows if r[4]})
                try:
                    for i in range(0, len(wanted), 500):
                        chunk = wanted[i:i + 500]
                        marks = ",".join("?" * len(chunk))
                        cur_vals = conn.execute(f"""
                            SELECT cv.client_id, cv.value
                            FROM client_values cv
                            JOIN mcl_columns mc ON mc.id = cv.column_id
                            WHERE cv.client_id IN ({marks})
                              AND cv.value IS NOT NULL AND cv.value != ''
                              AND LOWER(TRIM(mc.label)) NOT IN ('no', 'no.', 'sl no', 'sl. no.', 's.no.', 'sno', 'id', '#')
                            ORDER BY mc.is_identity DESC, mc.sort_order ASC
                        """, chunk).fetchall()
                        for cid_val, val_text in cur_vals:
                            if cid_val not in client_name_map:
                                client_name_map[cid_val] = val_text
                except Exception:
                    pass

            results = []
            for r in rows:
                cid = r[4]
                cname = client_name_map.get(cid) if cid else None
                client_label = cname if cname else (f"CLI-{cid:05d}" if cid else "—")
                results.append({
                    "id": r[0], "ts": r[1], "actor": r[2], "action": r[3],
                    "client_id": cid, "client_name": client_label,
                    "client_token": f"CLI-{cid:05d}" if cid else "",
                    "service_id": r[5], "detail": r[6]
                })
            return results

    # ---------------- Backup & Restore ----------------

    def backup_to(self, dest_dir: str) -> str:
        """Backs up databases into a timestamped directory under dest_dir using sqlcipher_export."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        target_folder = os.path.join(dest_dir, f"sera_backup_{now_str}")
        try:
            import sync_backup
            sync_backup.create_backup(
                os.path.dirname(self.db_path),
                dest_dir=target_folder,
                reason="manual_backup",
                hex_key=self.hex_key,
                db=self,
            )
            return target_folder
        except Exception:
            os.makedirs(target_folder, exist_ok=True)
            target_db = os.path.join(target_folder, "master.db")
            shutil.copy2(self.db_path, target_db)
            salt_path = os.path.join(os.path.dirname(self.db_path), security.SALT_FILE)
            if os.path.exists(salt_path):
                shutil.copy2(salt_path, os.path.join(target_folder, security.SALT_FILE))
            return target_folder

    def restore_from(self, target_path: str, master_password: str = None) -> str:
        """
        Restores database and salt from target_path (folder or file path).
        Supports Syncthing conflict files (master.db.sync-conflict-*),
        built-in sync_peer conflict files (master.db.conflict-*), and pre-sync backups.
        Validates SQLCipher decryption before overwriting live database.
        Returns a human-readable summary string of restored components.
        """
        if not target_path or not os.path.exists(target_path):
            raise FileNotFoundError(f"Selected restore path does not exist: {target_path}")

        if os.path.isfile(target_path):
            backup_dir = os.path.dirname(target_path)
            candidate_dbs = [target_path]
        else:
            backup_dir = target_path
            candidate_dbs = []
            standard_db = os.path.join(backup_dir, "master.db")
            if os.path.exists(standard_db):
                candidate_dbs.append(standard_db)
            
            # Scan for Syncthing conflict files, sync_peer conflict files, and other .db files
            for entry in os.listdir(backup_dir):
                full_p = os.path.join(backup_dir, entry)
                if os.path.isfile(full_p) and full_p not in candidate_dbs:
                    lower = entry.lower()
                    if lower.endswith(".db") or "sync-conflict" in lower or ".conflict-" in lower or ".pre-sync-" in lower or lower.startswith("master"):
                        candidate_dbs.append(full_p)
            
            # Sort candidates by modification time, newest first
            candidate_dbs.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        if not candidate_dbs:
            raise FileNotFoundError(f"No valid database (.db) files found in {backup_dir}")

        # Scan for candidate salt files
        live_dir = os.path.dirname(self.db_path)
        live_salt = os.path.join(live_dir, security.SALT_FILE)
        
        candidate_salts = []
        standard_salt = os.path.join(backup_dir, security.SALT_FILE)
        if os.path.exists(standard_salt):
            candidate_salts.append(standard_salt)
            
        for entry in os.listdir(backup_dir):
            full_p = os.path.join(backup_dir, entry)
            if os.path.isfile(full_p) and full_p not in candidate_salts:
                lower = entry.lower()
                if "salt" in lower or lower.endswith(".salt") or "sync-conflict" in lower or ".conflict-" in lower:
                    candidate_salts.append(full_p)
                    
        if os.path.exists(live_salt) and live_salt not in candidate_salts:
            candidate_salts.append(live_salt)

        if not candidate_salts:
            raise FileNotFoundError(f"No salt (sera.salt) files found in backup or live folder.")

        # Attempt to find a valid (db, salt, hex_key) pair that decrypts cleanly
        matched_db = None
        matched_salt = None
        matched_hex_key = None

        for db_file in candidate_dbs:
            for salt_file in candidate_salts:
                try:
                    salt_bytes = security.load_salt(salt_file)
                    if master_password:
                        test_key = security.derive_key_hex(master_password, salt_bytes)
                    else:
                        test_key = self.hex_key
                    
                    # Test SQLCipher opening and table query
                    conn = sqlite3.connect(db_file)
                    conn.execute(f"PRAGMA key = \"x'{test_key}'\";")
                    cur = conn.cursor()
                    cur.execute("SELECT count(*) FROM sqlite_master;")
                    cur.fetchone()
                    conn.close()

                    # Decryption succeeded!
                    matched_db = db_file
                    matched_salt = salt_file
                    matched_hex_key = test_key
                    break
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
            if matched_db:
                break

        if not matched_db or not matched_salt or not matched_hex_key:
            raise ValueError(
                "Could not decrypt any database candidate in the backup folder.\n"
                "Please verify that the master password is correct or that the matching salt file is present."
            )

        # Create safety backup of current live database
        now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        pre_db = os.path.join(live_dir, f"master.db.pre-restore-{now_str}")
        shutil.copy2(self.db_path, pre_db)
        if os.path.exists(live_salt):
            pre_salt = os.path.join(live_dir, f"sera.salt.pre-restore-{now_str}")
            shutil.copy2(live_salt, pre_salt)

        # Close any open connections before file replacement
        self.close()

        # Overwrite live files with validated Syncthing conflict/backup pair
        shutil.copy2(matched_db, self.db_path)
        shutil.copy2(matched_salt, live_salt)
        self.hex_key = matched_hex_key

        db_name = os.path.basename(matched_db)
        salt_name = os.path.basename(matched_salt)
        return f"Database restored from '{db_name}' using salt '{salt_name}'."

    def export_audit_log_csv(self, filepath: str):
        import csv
        logs = self.get_audit_logs(limit=10000, resolve_names=False)
        with open(filepath, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Log ID", "Timestamp (UTC)", "Actor", "Action", "Client Token", "Service ID", "Detail"])
            for l in logs:
                token = f"CLI-{l['client_id']:05d}" if l.get("client_id") else "—"
                writer.writerow([l["id"], l["ts"], l["actor"], l["action"], token, l["service_id"] or "", l["detail"] or ""])
