"""
sera_db/settings.py - App settings, SCC passwords and the staff alias matrix.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import re


class SettingsMixin:


    # ---------------- App settings ----------------

    def get_setting(self, key: str, default=None):
        with self._connect() as conn:
            cur = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def get_all_settings(self) -> dict:
        with self._connect() as conn:
            cur = conn.execute("SELECT key, value FROM app_settings")
            return {r[0]: r[1] for r in cur.fetchall()}

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )

    def set_settings_bulk(self, settings_dict: dict):
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                [(k, str(v) if v is not None else "") for k, v in settings_dict.items()]
            )

    @staticmethod
    def extract_pan_components(pan: str) -> tuple[str, str]:
        """Extracts (first_4_letters_lowercase, 4_digits) from a PAN string."""
        if not pan or not isinstance(pan, str):
            return ("", "")
        clean = str(pan).strip().upper()
        if not clean:
            return ("", "")
        import re
        letters_m = re.search(r"^[A-Z]{4}", clean)
        letters = letters_m.group(0).lower() if letters_m else clean[:4].lower()
        digits_m = re.search(r"\d{4}", clean)
        digits = digits_m.group(0) if digits_m else (clean[5:9] if len(clean) >= 9 else "")
        return (letters, digits)

    def generate_scc_passwords(self, pan: str) -> list[dict]:
        """Generates the 4 SCC password combinations for a given PAN.

        Option 1: First 4 letters of PAN (lowercase) + Fixed String 1 + 4 digits of PAN
        Option 2: Fixed String 2 + 4 digits of PAN
        Option 3: Plain Fixed String 3
        Option 4: Plain Fixed String 4
        """
        cfg = self.get_scc_settings()
        letters, digits = self.extract_pan_components(pan)

        opt1_str = cfg.get("opt1_fixed_str", "@")
        opt2_str = cfg.get("opt2_fixed_str", "")
        opt3_str = cfg.get("opt3_fixed_str", "")
        opt4_str = cfg.get("opt4_fixed_str", "")

        opt1_lbl = cfg.get("opt1_label", "Combo 1")
        opt2_lbl = cfg.get("opt2_label", "Combo 2")
        opt3_lbl = cfg.get("opt3_label", "Combo 3")
        opt4_lbl = cfg.get("opt4_label", "Combo 4")

        val1 = f"{letters}{opt1_str}{digits}" if (letters or digits) else opt1_str
        val2 = f"{opt2_str}{digits}" if digits else opt2_str
        val3 = opt3_str
        val4 = opt4_str

        combos = [
            {"id": 1, "label": opt1_lbl, "value": val1},
            {"id": 2, "label": opt2_lbl, "value": val2},
        ]
        if val3:
            combos.append({"id": 3, "label": opt3_lbl, "value": val3})
        if val4:
            combos.append({"id": 4, "label": opt4_lbl, "value": val4})
        return combos

    def get_scc_settings(self) -> dict:
        """Returns the SCC (Sera Credential Capture) configuration dict."""
        return {
            "enabled": self.get_setting("scc_enabled", "1") in ("1", "true", "True"),
            "opt1_label": self.get_setting("scc_opt1_label", "Combo 1"),
            "opt1_fixed_str": self.get_setting("scc_opt1_fixed_str", "@"),
            "opt2_label": self.get_setting("scc_opt2_label", "Combo 2"),
            "opt2_fixed_str": self.get_setting("scc_opt2_fixed_str", ""),
            "opt3_label": self.get_setting("scc_opt3_label", "Combo 3"),
            "opt3_fixed_str": self.get_setting("scc_opt3_fixed_str", ""),
            "opt4_label": self.get_setting("scc_opt4_label", "Combo 4"),
            "opt4_fixed_str": self.get_setting("scc_opt4_fixed_str", ""),
        }

    def save_scc_settings(self, settings_dict: dict):
        """Persists the SCC configuration into app_settings."""
        to_set = {}
        if "enabled" in settings_dict:
            to_set["scc_enabled"] = "1" if settings_dict["enabled"] else "0"
        for i in range(1, 5):
            lbl_k = f"opt{i}_label"
            str_k = f"opt{i}_fixed_str"
            if lbl_k in settings_dict:
                to_set[f"scc_{lbl_k}"] = settings_dict[lbl_k]
            if str_k in settings_dict:
                to_set[f"scc_{str_k}"] = settings_dict[str_k]
        if to_set:
            self.set_settings_bulk(to_set)

    def get_staff_matrix(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, alias FROM staff_users ORDER BY id").fetchall()
            return [{"id": r[0], "name": r[1], "alias": r[2] or ""} for r in rows]

    def assign_or_get_alias(self, alias_input: str) -> tuple[str, str]:
        clean_alias = (alias_input or "").strip()
        if not clean_alias:
            clean_alias = "Station-1"
            
        with self._connect() as conn:
            # Ensure 6 slots exist
            cur = conn.execute("SELECT COUNT(*) FROM staff_users")
            if cur.fetchone()[0] == 0:
                for i in range(1, 7):
                    sname = f"User {i}"
                    sgid = sync_schema.seed_gid("staff_users", sname)
                    conn.execute("INSERT INTO staff_users (name, alias, gid) VALUES (?, ?, ?)", (sname, None, sgid))

            rows = conn.execute("SELECT id, name, alias FROM staff_users ORDER BY id").fetchall()
            
            # Check if this alias is already bound to a slot
            for r_id, name, alias in rows:
                if alias and alias.lower() == clean_alias.lower():
                    return (name, alias)
            
            # Find first unassigned slot
            for r_id, name, alias in rows:
                if not alias or not alias.strip():
                    conn.execute("UPDATE staff_users SET alias = ? WHERE id = ?", (clean_alias, r_id))
                    return (name, clean_alias)
            
            # If all 6 slots are assigned, bind to first slot or last slot
            r_id, name, _ = rows[0]
            conn.execute("UPDATE staff_users SET alias = ? WHERE id = ?", (clean_alias, r_id))
            return (name, clean_alias)

    def update_staff_alias(self, user_id: int, new_alias: str):
        clean = (new_alias or "").strip()
        with self._connect() as conn:
            conn.execute("UPDATE staff_users SET alias = ? WHERE id = ?", (clean if clean else None, user_id))

    def reset_staff_matrix(self):
        with self._connect() as conn:
            conn.execute("UPDATE staff_users SET alias = NULL")

    def add_staff_user(self, name: str) -> str:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Staff name cannot be empty.")
        with self._connect() as conn:
            if conn.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] >= 6:
                raise ValueError("The staff roster can contain at most 6 users.")
            conn.execute("INSERT INTO staff_users (name) VALUES (?)", (clean,))
        return clean

    def remove_staff_user(self, name: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM staff_users WHERE name = ?", ((name or "").strip(),))
