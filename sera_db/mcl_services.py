"""
sera_db/mcl_services.py - Master Column List (MCL) columns and portal services.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import json
import re
import functools
import time


def _drops_mcl_cache(method):
    """A method that changes mcl_columns: forget the remembered column list afterwards."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            self._mcl_cache = None
    return wrapper


class MclServicesMixin:


    # ---------------- Master Column List (MCL) ----------------

    # The column list is read several times by every screen (opening one client profile did it
    # three times, ~9 ms each: one database connection per read) and changes almost never. It is
    # remembered until a method below changes it, or _MCL_CACHE_TTL_S passes - the cap covers
    # changes this class does not see (a peer's change applied by sync, another program).
    # (data_generation is no use as the key: every audit-log write moves it.)
    _MCL_CACHE_TTL_S = 10.0

    def get_mcl_columns(self) -> list[dict]:
        cached = getattr(self, "_mcl_cache", None)
        if cached and time.monotonic() - cached[0] < self._MCL_CACHE_TTL_S:
            cols = cached[1]
        else:
            cols = self._read_mcl_columns()
            self._mcl_cache = (time.monotonic(), cols)
        return [{**c, "dropdown_options": list(c["dropdown_options"])} for c in cols]

    def _read_mcl_columns(self) -> list[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT id, label, field_type, dropdown_options, is_identity, sort_order, show_in_search, allow_quick_copy, admin_show_in_search, is_internal_pk 
                   FROM mcl_columns ORDER BY sort_order"""
            )
            return [
                {
                    "id": r[0],
                    "label": r[1],
                    "field_type": r[2],
                    "dropdown_options": json.loads(r[3]) if r[3] else [],
                    "is_identity": bool(r[4]),
                    "sort_order": r[5],
                    "show_in_search": bool(r[6]),
                    "allow_quick_copy": bool(r[7]),
                    "admin_show_in_search": bool(r[8]) if len(r) > 8 else True,
                    "is_internal_pk": bool(r[9]) if len(r) > 9 else False,
                }
                for r in cur.fetchall()
            ]

    def get_id_column(self) -> Optional[dict]:
        for col in self.get_mcl_columns():
            if col.get("field_type") == "id":
                return col
        return None

    def get_identity_column(self) -> Optional[dict]:
        ignored_labels = {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "id", "#"}
        mcl = self.get_mcl_columns()
        for col in mcl:
            if col.get("is_identity") and col.get("label", "").strip().lower() not in ignored_labels:
                return col
        for col in mcl:
            if col.get("label", "").strip().lower() not in ignored_labels:
                return col
        return None

    @_drops_mcl_cache
    def create_mcl_column(self, label: str, field_type: str, dropdown_options=None, is_identity: int = 0, is_internal_pk: int = 0) -> int:
        opts_json = json.dumps(dropdown_options) if dropdown_options else None
        with self._connect() as conn:
            if field_type == "id":
                conn.execute("UPDATE mcl_columns SET field_type = 'text' WHERE field_type = 'id'")
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM mcl_columns")
            next_order = cur.fetchone()[0]
            cur = conn.execute(
                """INSERT INTO mcl_columns (label, field_type, dropdown_options, is_identity, sort_order, is_internal_pk)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (label.strip(), field_type, opts_json, is_identity, next_order, is_internal_pk)
            )
            return cur.lastrowid

    @_drops_mcl_cache
    def update_mcl_column(self, column_id: int, label: str, field_type: str, dropdown_options=None, is_identity: int = None, is_internal_pk: int = None):
        opts_json = json.dumps(dropdown_options) if dropdown_options else None
        with self._connect() as conn:
            if field_type == "id":
                conn.execute("UPDATE mcl_columns SET field_type = 'text' WHERE field_type = 'id' AND id != ?", (column_id,))
            
            # Fetch current values for unspecified flags
            cur = conn.execute("SELECT is_identity, is_internal_pk FROM mcl_columns WHERE id = ?", (column_id,))
            row = cur.fetchone()
            curr_ident = row[0] if row else 0
            curr_pk = row[1] if row and len(row) > 1 else 0

            target_ident = int(is_identity) if is_identity is not None else curr_ident
            target_pk = int(is_internal_pk) if is_internal_pk is not None else curr_pk

            conn.execute(
                """UPDATE mcl_columns SET label=?, field_type=?, dropdown_options=?, is_identity=?, is_internal_pk=? 
                   WHERE id=?""",
                (label.strip(), field_type, opts_json, target_ident, target_pk, column_id)
            )

    @_drops_mcl_cache
    def delete_mcl_column(self, column_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM mcl_columns WHERE id=?", (column_id,))

    @_drops_mcl_cache
    def reorder_mcl_columns(self, ordered_column_ids: list):
        with self._connect() as conn:
            for idx, col_id in enumerate(ordered_column_ids):
                conn.execute("UPDATE mcl_columns SET sort_order=? WHERE id=?", (idx, col_id))

    @_drops_mcl_cache
    def bulk_update_mcl_visibility(self, visible_ids: list[int]):
        with self._connect() as conn:
            conn.execute("UPDATE mcl_columns SET show_in_search=0")
            for cid in visible_ids:
                conn.execute("UPDATE mcl_columns SET show_in_search=1 WHERE id=?", (cid,))

    @_drops_mcl_cache
    def bulk_update_mcl_quick_copy(self, allowed_ids: list[int]):
        with self._connect() as conn:
            conn.execute("UPDATE mcl_columns SET allow_quick_copy=0")
            for cid in allowed_ids:
                conn.execute("UPDATE mcl_columns SET allow_quick_copy=1 WHERE id=?", (cid,))

    @_drops_mcl_cache
    def bulk_update_mcl_admin_visibility(self, admin_visible_ids: list[int]):
        with self._connect() as conn:
            conn.execute("UPDATE mcl_columns SET admin_show_in_search=0")
            for cid in admin_visible_ids:
                conn.execute("UPDATE mcl_columns SET admin_show_in_search=1 WHERE id=?", (cid,))


    # ---------------- Services ----------------

    def get_services(self) -> list[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT id, name, login_page_link, userid_column_id, password_column_id,
                          username_selector, password_selector, automation_mode, extension_flow,
                          success_selector, arn_selector, sort_order, automation_mode_2, browser
                   FROM services ORDER BY sort_order"""
            )
            return [
                {
                    "id": r[0], "name": r[1], "login_page_link": r[2],
                    "userid_column_id": r[3], "password_column_id": r[4],
                    "username_selector": r[5], "password_selector": r[6],
                    "automation_mode": ("extension" if r[7] in ("automated", "playwright") or not r[7] else r[7]),
                    "extension_flow": r[8],
                    "success_selector": r[9], "arn_selector": r[10], "sort_order": r[11],
                    "automation_mode_2": r[12] if len(r) > 12 and r[12] else "",
                    "browser": r[13] if len(r) > 13 and r[13] else "",
                }
                for r in cur.fetchall()
            ]

    def get_service(self, service_id: int) -> dict | None:
        """Finds service by ID."""
        for s in self.get_services():
            if s.get("id") == service_id:
                return s
        return None

    def create_service(self, name: str, login_page_link: str, userid_column_id: int,
                       password_column_id: int, username_selector: str, password_selector: str,
                       automation_mode: str = "extension", extension_flow: str = "double",
                       success_selector: str = "", arn_selector: str = "",
                       automation_mode_2: str = "", browser: str = "") -> int:
        u_sel = (username_selector or "").strip()
        p_sel = (password_selector or "").strip()
        link = (login_page_link or "").strip()
        if automation_mode in ("automated", "playwright") or not automation_mode:
            automation_mode = "extension"

        # If selectors are missing, resolve from verified presets
        if not u_sel or not p_sel:
            presets = [
                (['tdscpc', 'traces', 'tds'], "input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single'),
                (['gst.gov.in', 'gst'], '#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single'),
                (['incometax', 'itr', 'eportal'], '#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double'),
                (['gmail', 'google', 'accounts.google'], '#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double'),
                (['epfindia', 'epfo', 'unifiedportal', 'pf'], '#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single'),
                (['icegate'], '#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single'),
                (['mca.gov.in', 'mca21', 'mca'], '#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double'),
            ]
            combined = f"{name.lower()} {link.lower()}"
            for kws, def_u, def_p, def_url, def_flow in presets:
                if any(k in combined for k in kws):
                    if not u_sel: u_sel = def_u
                    if not p_sel: p_sel = def_p
                    if not link: link = def_url
                    if not extension_flow: extension_flow = def_flow
                    break

        with self._connect() as conn:
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM services")
            next_order = cur.fetchone()[0]
            cur = conn.execute(
                """INSERT INTO services (name, login_page_link, userid_column_id, password_column_id,
                                         username_selector, password_selector, automation_mode, automation_mode_2,
                                         extension_flow, success_selector, arn_selector, sort_order, browser)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name.strip(), link, userid_column_id, password_column_id,
                 u_sel, p_sel, automation_mode, (automation_mode_2 or "").strip(), extension_flow,
                 success_selector, arn_selector, next_order, (browser or "").strip().lower())
            )
            return cur.lastrowid

    def update_service(self, service_id: int, name: str, login_page_link: str, userid_column_id: int,
                       password_column_id: int, username_selector: str, password_selector: str,
                       automation_mode: str = "extension", extension_flow: str = "double",
                       success_selector: str = "", arn_selector: str = "",
                       automation_mode_2: str = "", browser: str = ""):
        u_sel = (username_selector or "").strip()
        p_sel = (password_selector or "").strip()
        link = (login_page_link or "").strip()
        if automation_mode in ("automated", "playwright") or not automation_mode:
            automation_mode = "extension"

        # If selectors are missing, resolve from verified presets
        if not u_sel or not p_sel:
            presets = [
                (['tdscpc', 'traces', 'tds'], "input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single'),
                (['gst.gov.in', 'gst'], '#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single'),
                (['incometax', 'itr', 'eportal'], '#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double'),
                (['gmail', 'google', 'accounts.google'], '#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double'),
                (['epfindia', 'epfo', 'unifiedportal', 'pf'], '#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single'),
                (['icegate'], '#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single'),
                (['mca.gov.in', 'mca21', 'mca'], '#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double'),
            ]
            combined = f"{name.lower()} {link.lower()}"
            for kws, def_u, def_p, def_url, def_flow in presets:
                if any(k in combined for k in kws):
                    if not u_sel: u_sel = def_u
                    if not p_sel: p_sel = def_p
                    if not link: link = def_url
                    if not extension_flow: extension_flow = def_flow
                    break

        with self._connect() as conn:
            conn.execute(
                """UPDATE services SET name=?, login_page_link=?, userid_column_id=?,
                                       password_column_id=?, username_selector=?, password_selector=?,
                                       automation_mode=?, automation_mode_2=?, extension_flow=?,
                                       success_selector=?, arn_selector=?, browser=? WHERE id=?""",
                (name.strip(), link, userid_column_id, password_column_id,
                 u_sel, p_sel, automation_mode, (automation_mode_2 or "").strip(), extension_flow,
                 success_selector, arn_selector, (browser or "").strip().lower(), service_id)
            )

    def auto_populate_service_selectors(self):
        """
        Auto-scrapes and resolves username & password CSS selectors for services
        that have a login_page_link or recognizable name but are missing valid selectors.
        """
        import urllib.request
        import re

        # Known portal definitions: (keywords_in_name_or_url, (u_sel, p_sel, default_url, default_flow))
        known_portals = [
            (
                ['tdscpc', 'traces', 'tds'],
                ("input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single')
            ),
            (
                ['gst.gov.in', 'gst'],
                ('#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single')
            ),
            (
                ['incometax', 'itr', 'eportal'],
                ('#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double')
            ),
            (
                ['gmail', 'google', 'accounts.google'],
                ('#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double')
            ),
            (
                ['epfindia', 'epfo', 'unifiedportal'],
                ('#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single')
            ),
            (
                ['icegate'],
                ('#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single')
            ),
            (
                ['mca.gov.in', 'mca21', 'mca'],
                ('#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double')
            ),
        ]

        def _match_known(name: str, url: str) -> tuple[str, str, str, str]:
            combined = f"{name or ''} {url or ''}".lower()
            for keywords, (u_sel, p_sel, def_url, def_flow) in known_portals:
                for kw in keywords:
                    if kw in combined:
                        return (u_sel, p_sel, def_url, def_flow)
            return ("", "", "", "")

        def _scrape_url(url: str) -> tuple[str, str]:
            if not url or not url.strip():
                return ("", "")
            url_clean = url.strip()
            try:
                req = urllib.request.Request(url_clean, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    html = resp.read().decode('utf-8', errors='ignore')
                    
                    p_match = re.search(r'<input[^>]*type=["\']password["\'][^>]*>', html, re.I)
                    p_sel = "input[type='password']"
                    if p_match:
                        id_m = re.search(r'id=["\']([^"\']+)["\']', p_match.group(0), re.I)
                        if id_m: p_sel = '#' + id_m.group(1)
                        
                    u_match = re.search(r'<input[^>]*type=["\'](text|email)["\'][^>]*>', html, re.I)
                    u_sel = "input[type='text']"
                    if u_match:
                        id_m = re.search(r'id=["\']([^"\']+)["\']', u_match.group(0), re.I)
                        if id_m: u_sel = '#' + id_m.group(1)
                    return (u_sel, p_sel)
            except Exception:
                return ("input[type='text'], input[name='userId'], #username", "input[type='password'], #password, #psw")

        services = self.get_services()
        for svc in services:
            svc_id = svc["id"]
            svc_name = svc.get("name", "")
            link = svc.get("login_page_link", "")
            u_sel = (svc.get("username_selector") or "").strip()
            p_sel = (svc.get("password_selector") or "").strip()
            flow = svc.get("extension_flow", "double")

            k_u, k_p, k_url, k_flow = _match_known(svc_name, link)
            
            # If known portal, apply high-accuracy verified selectors
            if k_u and k_p:
                final_u = u_sel if (u_sel and u_sel not in ["input[type='text']", "#username"]) else k_u
                final_p = p_sel if (p_sel and p_sel not in ["input[type='password']", "#password"]) else k_p
                final_link = link if link else k_url
                final_flow = flow if flow else k_flow
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE services SET username_selector = ?, password_selector = ?, login_page_link = ?, extension_flow = ? WHERE id = ?",
                        (final_u, final_p, final_link, final_flow, svc_id)
                    )
            elif link and (not u_sel or not p_sel):
                new_u_sel, new_p_sel = _scrape_url(link)
                final_u = u_sel if u_sel else new_u_sel
                final_p = p_sel if p_sel else new_p_sel
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE services SET username_selector = ?, password_selector = ? WHERE id = ?",
                        (final_u, final_p, svc_id)
                    )

    def delete_service(self, service_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM services WHERE id=?", (service_id,))

    def get_service_for_portal(self, portal_name: str) -> dict | None:
        """Finds the registered service record matching a portal name or keyword (e.g. 'Income Tax', 'ITR', 'GST')."""
        if not portal_name:
            return None
        clean = str(portal_name).strip().lower()
        services = self.get_services()
        # 1. Exact match
        for s in services:
            sname = (s.get("name") or "").strip().lower()
            if sname == clean:
                return s
        # 2. Income Tax / ITR Portal matching
        if any(kw in clean for kw in ("income tax", "incometax", "itr", "eportal")):
            for s in services:
                sname = (s.get("name") or "").strip().lower()
                slink = (s.get("login_page_link") or "").strip().lower()
                if "income" in sname or "itr" in sname or "incometax" in slink:
                    return s
        # 3. GST Portal matching
        if "gst" in clean:
            for s in services:
                sname = (s.get("name") or "").strip().lower()
                if "gst" in sname:
                    return s
        # 4. Fallback substring
        for s in services:
            sname = (s.get("name") or "").strip().lower()
            if sname in clean or clean in sname:
                return s
        return None
