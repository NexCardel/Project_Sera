"""
client_detail_window.py
------------------------
Window 2: shows client info, masked passwords, and autofill buttons.
"""

import threading

from PySide6.QtCore import Qt, QTimer, QSize, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import automation
try:
    import qtawesome as qta
except Exception:
    qta = None
from automation import _AutofillBridge
from ui.components.kit import (
    MessageBox, Caption, Card, EmptyState, Hint, Key, KeyValueGrid, LabelledAction, ListRow, Pill, RowTitle,
    ServiceIcon, SettingRow, TextArea, sentence_label,
)
from ui.utils.masking import mask_password
from ui.utils.tokens import KIT as K, TOKENS as T


def _get_service_icon(name: str) -> str:
    n = (name or "").lower()
    if "gst" in n:
        return "mdi.percent-outline"
    elif "income" in n or "tax" in n or "itr" in n:
        return "mdi.file-document-outline"
    elif "email" in n or "mail" in n:
        return "mdi.email-outline"
    elif "tds" in n:
        return "mdi.percent"
    elif "mca" in n or "company" in n:
        return "mdi.bank-outline"
    elif "pf" in n or "provident" in n:
        return "mdi.account-group-outline"
    elif "esi" in n:
        return "mdi.asterisk"
    elif "pt" in n or "professional" in n:
        return "mdi.map-marker-outline"
    else:
        return "mdi.web"


def _get_portal_accent_color(name: str) -> str:
    n = (name or "").lower()
    if "gst" in n:
        return T["danger"]
    elif "income" in n or "tax" in n or "itr" in n:
        return T["info"]
    elif "email" in n or "mail" in n:
        return T["purple"]
    elif "tds" in n or "traces" in n:
        return T["warn"]
    elif "pf" in n or "provident" in n:
        return T["mint"]
    return T["muted"]


def _looks_like_code(value):
    """IDs, PAN / GSTIN and numbers read best in the mono face; names and addresses do not."""
    v = str(value).strip()
    return bool(v) and " " not in v and any(c.isdigit() for c in v) and len(v) <= 24


class ClickableLabel(QLabel):

    clicked = Signal()
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

class ClientDetailWindow(QWidget):
    back_requested = Signal()
    toast_requested = Signal(str, int)
    action_alert_requested = Signal(str, str)
    # the header's Edit and ⋯ menu: the main window runs the same actions the All Clients grid runs
    edit_requested = Signal(int)
    manage_services_requested = Signal(int)
    archive_requested = Signal(int)
    delete_requested = Signal(int)
    MANUAL_POPUP_DELAY_MS = 3000

    def __init__(self, db, actor: str = "Staff"):
        super().__init__()
        self.db = db
        self.actor = actor
        self.client = None
        self._bridge = _AutofillBridge()
        self._bridge.failed.connect(self._on_autofill_failed)
        self._service_shortcuts = []
        self._is_navigating_back = False
        self._admin = False
        self._build_ui()

        self.back_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.back_shortcut.activated.connect(self._safe_back_request)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        # the mockup's body: 14 / 18 / 20 with a 12 px gap
        outer.setContentsMargins(K["pad_dlg_h"], K["pad_panel_top"], K["pad_dlg_h"], T["s_5"])
        outer.setSpacing(T["s_2"])

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 2)
        header.setSpacing(T["s_3"])

        self.back_btn = LabelledAction(variant="ghost", icon="mdi.arrow-left", sm=True, tooltip="Back (Esc)")
        self.back_btn.set_icon_only(True)
        self.back_btn.clicked.connect(self._safe_back_request)
        header.addWidget(self.back_btn, 0, Qt.AlignVCenter)

        title_vbox = QVBoxLayout()
        title_vbox.setContentsMargins(0, 0, 0, 0)
        title_vbox.setSpacing(1)

        # the name with the client ID chip right after it (the mockup's .p-top)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(T["s_2"])
        self.primary_name_lbl = QLabel()
        self.primary_name_lbl.setProperty("class", "ClientName")
        name_row.addWidget(self.primary_name_lbl)

        self._token = ""
        self.token_badge = LabelledAction(variant="soft", sm=True)
        self.token_badge.clicked.connect(self._on_token_clicked)
        name_row.addWidget(self.token_badge, 0, Qt.AlignVCenter)
        name_row.addStretch(1)
        title_vbox.addLayout(name_row)

        # the meta line under the name: proprietor, then the recent-activity pill (the mockup's .p-meta);
        # on the name row the pill was squeezed to "Created ..." by Edit and ⋯ at 540 px
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(T["s_2"])
        self.secondary_name_lbl = QLabel()
        self.secondary_name_lbl.setProperty("class", "EditorSub")
        meta_row.addWidget(self.secondary_name_lbl)
        self.activity_pill = Pill("", "emerald")
        self.activity_pill.hide()
        meta_row.addWidget(self.activity_pill, 0, Qt.AlignVCenter)
        meta_row.addStretch(1)
        title_vbox.addLayout(meta_row)

        header.addLayout(title_vbox, stretch=1)

        self.edit_btn = LabelledAction("Edit", variant="soft", sm=True, icon="mdi.pencil-outline",
                                       tooltip="Edit this client")
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        header.addWidget(self.edit_btn, 0, Qt.AlignVCenter)

        self.more_btn = LabelledAction(variant="secondary", sm=True, icon="mdi.dots-horizontal",
                                       tooltip="More actions")
        self.more_btn.set_icon_only(True)
        self._more_menu = QMenu(self.more_btn)
        self.act_services = self._more_menu.addAction("Services…")
        self.act_archive = self._more_menu.addAction("Archive client")
        self.act_delete = self._more_menu.addAction("Delete client…")
        self.act_services.triggered.connect(self._on_services_clicked)
        self.act_archive.triggered.connect(self._on_archive_clicked)
        self.act_delete.triggered.connect(self._on_delete_clicked)
        self.more_btn.setMenu(self._more_menu)
        header.addWidget(self.more_btn, 0, Qt.AlignVCenter)
        self.set_admin_mode(False)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(K["gap_main"])
        scroll.setWidget(self.scroll_widget)
        outer.addWidget(scroll, stretch=1)

    def set_admin_mode(self, active):
        """Archive and delete are admin actions: they appear in the ⋯ menu only in admin mode."""
        self._admin = bool(active)
        self.act_archive.setVisible(self._admin)
        self.act_delete.setVisible(self._admin)

    def _current_client_id(self):
        return self.client.get("id") if self.client else None

    def _on_edit_clicked(self):
        cid = self._current_client_id()
        if cid is not None:
            self.edit_requested.emit(int(cid))

    def _on_services_clicked(self):
        cid = self._current_client_id()
        if cid is not None:
            self.manage_services_requested.emit(int(cid))

    def _on_archive_clicked(self):
        cid = self._current_client_id()
        if cid is not None:
            self.archive_requested.emit(int(cid))

    def _on_delete_clicked(self):
        cid = self._current_client_id()
        if cid is not None:
            self.delete_requested.emit(int(cid))

    def _show_activity(self, client_id):
        """The latest recent action on this client as the pill, when there is one in the last day."""
        acts = self.db.get_recent_client_activities(max_age_seconds=86400).get(client_id, [])
        if not acts:
            self.activity_pill.hide()
            return
        top = acts[0]
        age = top["age_seconds"]
        rel = "just now" if age < 60 else (f"{age // 60}m ago" if age < 3600 else f"{age // 3600}h ago")
        self.activity_pill.set_text(f"{top['action_type']} • {rel}")
        self.activity_pill.show()

    def _on_token_clicked(self):
        self._copy_token_to_clipboard(self._token)

    def _safe_back_request(self):
        if getattr(self, "_is_navigating_back", False):
            return
        if hasattr(self, "_notes_timer") and self._notes_timer.isActive():
            self._notes_timer.stop()
            self._auto_save_notes()
        self._is_navigating_back = True
        self.back_btn.setEnabled(False)
        self.back_shortcut.setEnabled(False)
        self.back_requested.emit()
        QTimer.singleShot(300, self._restore_back_inputs)

    def _copy_token_to_clipboard(self, token: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(token)
        self.toast_requested.emit(f"Copied Client Token: {token}", 2000)

    def _restore_back_inputs(self):
        self._is_navigating_back = False
        self.back_btn.setEnabled(True)
        self.back_shortcut.setEnabled(True)

    def _clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())

    def load_client(self, client_id: int):
        self.scroll_widget.setUpdatesEnabled(False)
        try:
            if self._load_client_internal(client_id) is not False:
                self._record_view_async(client_id)
        finally:
            self.scroll_widget.setUpdatesEnabled(True)

    def _record_view_async(self, client_id: int):
        """The audit entry and the activity counter are three database connections; they are
        bookkeeping, so they run off the UI thread and never delay the panel sliding in."""
        def work():
            try:
                self.db.log_action(self.actor, "view", client_id=client_id)
                self.db.record_client_activity(client_id, "Viewed", "Opened profile")
            except Exception:
                pass
        threading.Thread(target=work, name="client-view-log", daemon=True).start()

    def _load_client_internal(self, client_id: int):
        self._clear_layout(self.scroll_layout)

        for shortcut in self._service_shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._service_shortcuts = []

        self.client = self.db.get_client(client_id)
        if not self.client:
            MessageBox.warning(self, "Not found", "That client record no longer exists.")
            self.back_requested.emit()
            return False

        primary, secondary = self._get_identity_parts(self.client)
        self.primary_name_lbl.setText(primary)
        if secondary:
            self.secondary_name_lbl.setText(secondary)
            self.secondary_name_lbl.show()
        else:
            self.secondary_name_lbl.hide()

        token = self.client.get("client_id_token") or str(client_id)
        self._token = token
        self.token_badge.setText(f"CLI-{token.zfill(5)}" if token.isdigit() else f"ID: {token}")
        self.token_badge.setToolTip(f"Client ID: {token}\nClick to copy to clipboard")
        self._show_activity(int(self.client.get("id") or client_id))

        settings = self.db.get_all_settings()    # one connection, not four
        mask_mode = settings.get("mask_mode", "last_n")
        reveal_count = int(settings.get("mask_reveal_count", "4"))
        show_hide_btn_enabled = settings.get("show_hide_btn_enabled", "1") == "1"

        timeout_sec = 30
        try:
            timeout_sec = int(settings.get("clipboard_clear_seconds", "30"))
        except Exception:
            pass

        mcl_cols = self.db.get_mcl_columns()
        identity_cols = [c for c in mcl_cols if c["field_type"] != "password"]
        security_cols = [c for c in mcl_cols if c["field_type"] == "password"]

        self._add_services_card(self.db.get_client_services(client_id))
        if identity_cols:
            self._add_identity_card(identity_cols, timeout_sec)
        if security_cols:
            self._add_logins_card(security_cols, mask_mode, reveal_count, show_hide_btn_enabled, timeout_sec)
        self._add_notes_card()
        self._add_sdis_values(client_id)

        self.scroll_layout.addStretch()

    @staticmethod
    def _hint(text):
        lbl = QLabel(text)
        lbl.setProperty("class", "HintText")
        return lbl

    @staticmethod
    def _set_eye_icon(button, shown):
        button.setIcon(qta.icon("mdi.eye-off-outline" if shown else "mdi.eye-outline", color=T["text2"]) if qta else QIcon())

    def _copy_button(self, tooltip, value, label_name, is_secret, timeout_sec):
        btn = LabelledAction(variant="ghost", icon="mdi.content-copy", sm=True, tooltip=tooltip)
        btn.set_icon_only(True)
        btn.clicked.connect(lambda _=False, v=value, n=label_name, s=is_secret, t=timeout_sec: self._copy_to_clipboard(v, n, s, t))
        return btn

    def _add_services_card(self, services):
        card = Card("Services")
        if not services:
            card.add_row(EmptyState("No services for this client", "Services are assigned to a client in Admin Mode."))
            self.scroll_layout.addWidget(card)
            return

        hint = Hint("\u24d8 Alt+1..9 \u00b7 Alt+Ctrl+1..9 for mode 2")
        hint.setToolTip(
            "Alt+1..9 runs primary autofill (Mode 1)\n"
            "Alt+Ctrl+1..9 runs secondary autofill (Mode 2, if set)\n"
            "(Fast Autofill, SMTI Assist or MECP Copy, as set in its Automation Mode)."
        )
        card.add_action(hint)

        action_specs = {
            automation.ACTION_AUTOFILL: ("Fast Autofill", "Autofill", "mdi.flash", self._launch_extension_autofill),
            automation.ACTION_SMTI: ("SMTI Manual Assist", "Assist", "mdi.clipboard-account-outline", self._launch_manual_assist),
            automation.ACTION_MECP: ("MECP Manual Copy", "Copy login", "mdi.content-copy", self._launch_manual_copy),
        }

        for i, s in enumerate(services):
            key_num = i + 1
            has_shortcut = key_num <= 9
            mode_1 = automation.service_action_mode(s)
            mode_2 = automation.service_secondary_action_mode(s)
            slots = [(mode_1, f"Alt+{key_num}")]
            if mode_2 and mode_2 in action_specs and mode_2 != mode_1:
                slots.append((mode_2, f"Alt+Ctrl+{key_num}"))

            # the mockup's .svc row: icon tile, name over its modes, the actions, the key
            row = ListRow(first=(i == 0))
            row_layout = row.layout()
            row_layout.addWidget(ServiceIcon(_get_service_icon(s["name"]), _get_portal_accent_color(s["name"])))

            names = QVBoxLayout()
            names.setContentsMargins(0, 0, 0, 0)
            names.setSpacing(0)
            names.addWidget(RowTitle(s["name"]))
            mode_names = [action_specs[m][0] if len(slots) == 1 else action_specs[m][1] for m, _k in slots]
            names.addWidget(Caption(" \u00b7 ".join(mode_names)))
            row_layout.addLayout(names, stretch=1)

            for mode, keys in slots:
                label, short, icon, fn = action_specs[mode]
                btn = LabelledAction(
                    short, variant="primary" if mode == automation.ACTION_AUTOFILL else "secondary",
                    icon=icon, sm=True,
                    tooltip=f"{label}: {s['name']}" + (f" ({keys})" if has_shortcut else ""),
                )
                btn.clicked.connect(lambda _=False, svc=s, fn=fn: fn(svc))
                row_layout.addWidget(btn)
                if has_shortcut:
                    sc = QShortcut(QKeySequence(keys), self)
                    sc.activated.connect(lambda svc=s, fn=fn: fn(svc))
                    self._service_shortcuts.append(sc)
            if has_shortcut:
                row_layout.addWidget(Key(f"Alt {key_num}"))

            card.add_row(row)

        self.scroll_layout.addWidget(card)

    def _value_label(self, shown, raw_val, label_name, is_secret, allow_qc, timeout_sec):
        """The value of one field: click-to-copy when quick copy is on, plain text otherwise."""
        if allow_qc:
            val_label = ClickableLabel(shown)
            val_label.clicked.connect(
                lambda v=raw_val, n=label_name, sec=is_secret, t=timeout_sec: self._on_label_clicked(v, n, sec, t)
            )
            val_label.setCursor(Qt.PointingHandCursor)
            val_label.setToolTip(f"Click to copy {label_name}")
        else:
            val_label = QLabel(shown)
        val_label.setProperty("class", "RowValue")
        return val_label

    def _add_identity_card(self, identity_cols, timeout_sec):
        card = Card("Identity")
        grid = KeyValueGrid()
        not_set = []
        for col in identity_cols:
            raw_val = self.client["values"].get(col["id"], "")
            shown_label = sentence_label(col["label"])
            if not raw_val:
                not_set.append(shown_label)
                continue
            allow_qc = col.get("allow_quick_copy", True)
            val_label = self._value_label(raw_val, raw_val, col["label"], False, allow_qc, timeout_sec)
            buttons = ([self._copy_button(f"Copy {col['label']}", raw_val, col["label"], False, timeout_sec)]
                       if allow_qc else [])
            grid.add_widget_field(shown_label, val_label, mono=_looks_like_code(raw_val), buttons=buttons)
        grid.set_not_set(not_set)
        card.add_row(grid)
        self.scroll_layout.addWidget(card)

    def _add_logins_card(self, security_cols, mask_mode, reveal_count, show_hide_btn_enabled, timeout_sec):
        card = Card("Logins")
        where = "first" if mask_mode == "first_n" else "last"
        card.add_action(Hint(f"Masked: {where} {reveal_count} characters shown \u00b7 "
                             f"clipboard clears after {timeout_sec} s"))
        grid = KeyValueGrid()
        not_set = []
        for col in security_cols:
            raw_val = self.client["values"].get(col["id"], "")
            shown_label = sentence_label(col["label"])
            if not raw_val:
                not_set.append(shown_label)
                continue
            allow_qc = col.get("allow_quick_copy", True)
            masked_val = mask_password(raw_val, mask_mode, reveal_count)
            val_label = self._value_label(masked_val, raw_val, col["label"], True, allow_qc, timeout_sec)
            buttons = []
            if show_hide_btn_enabled:
                btn_show = LabelledAction(variant="ghost", sm=True, tooltip="Show / Hide Password")
                btn_show.set_icon_only(True)
                btn_show.setCheckable(True)
                self._set_eye_icon(btn_show, False)
                btn_show.toggled.connect(
                    lambda checked, lbl_w=val_label, m=masked_val, r=raw_val, btn_w=btn_show: (
                        lbl_w.setText(r if checked else m),
                        self._set_eye_icon(btn_w, checked),
                    )
                )
                buttons.append(btn_show)
            if allow_qc:
                buttons.append(self._copy_button("Copy Password", raw_val, col["label"], True, timeout_sec))
            grid.add_widget_field(shown_label, val_label, mono=True, buttons=buttons)
        grid.set_not_set(not_set)
        card.add_row(grid)
        self.scroll_layout.addWidget(card)

    def _add_notes_card(self):
        card = Card("Notes")
        # the mockup's muted "\u2713 Saved" line in the card header (was an emerald Pill)
        self.notes_status_lbl = Hint("")
        self.notes_status_lbl.hide()
        card.add_action(self.notes_status_lbl)

        self.notes_edit = TextArea(rows=3, placeholder="Add notes for this client...")
        self._loading_notes = True
        self.notes_edit.setPlainText(self.client.get("notes") or "")
        self._loading_notes = False

        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.timeout.connect(self._auto_save_notes)
        self.notes_edit.textChanged.connect(self._on_notes_text_changed)
        card.add_widget(self.notes_edit)
        self.scroll_layout.addWidget(card)

    def _add_sdis_values(self, client_id: int) -> None:
        """SDIS Others and Profile builder values captured on each portal (blueprint S.4), read-only,
        labelled from sdis_mcl. Nothing is shown when the client has none."""
        try:
            from sera_db.srpf import sdis_value_rows
            container = self.db.get_client_raw_container(client_id=client_id) or {}
            rows = sdis_value_rows(container.get("raw_aggregates") or {}, container.get("portal_profiles") or {},
                                   self.db.sdis_field_labels())
        except Exception:
            rows = []
        if not rows:
            return
        card = Card("Portal values")
        card.title_label.setToolTip("Captured by SGT for Sera Distill's Profile builder and Others containers")
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        for portal, pairs in rows:
            body_layout.addWidget(self._section_label(portal))
            for label, value in pairs:
                v = QLabel(value)
                v.setProperty("class", "RowValue")
                v.setWordWrap(True)
                v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                body_layout.addWidget(SettingRow(label, control=v))
        body.setVisible(False)
        toggle = LabelledAction("Show", variant="ghost", sm=True)
        toggle.clicked.connect(lambda _=False, b=toggle, w=body: self._toggle_section(b, w))
        card.add_action(toggle)
        card.add_widget(body)
        self.scroll_layout.addWidget(card)

    @staticmethod
    def _section_label(text):
        lbl = QLabel(text)
        lbl.setProperty("class", "FieldLabel")
        return lbl

    @staticmethod
    def _toggle_section(button, body):
        opening = body.isHidden()
        body.setVisible(opening)
        button.setText("Hide" if opening else "Show")

    def run_service_action(self, client_id: int, service: dict) -> None:
        """Loads the client (without showing the window) and runs the service's Automation Mode 1 action."""
        fresh = self.db.get_client(client_id)
        if not fresh:
            return
        prev = self.client
        self.client = fresh
        try:
            mode_1 = automation.service_action_mode(service)
            action_map = {
                automation.ACTION_AUTOFILL: self._launch_extension_autofill,
                automation.ACTION_SMTI: self._launch_manual_assist,
                automation.ACTION_MECP: self._launch_manual_copy,
            }
            fn = action_map.get(mode_1, self._launch_extension_autofill)
            fn(service)
        finally:
            self.client = prev

    def _get_identity_parts(self, client):
        identity_cols = [c for c in self.db.get_mcl_columns() if c["is_identity"]]
        vals = [client["values"].get(c["id"], "") for c in identity_cols if client["values"].get(c["id"])]
        primary = vals[0] if len(vals) > 0 else "Client Profile"
        secondary = vals[1] if len(vals) > 1 else ""
        return primary, secondary

    def _get_identity_label(self, client):
        primary, secondary = self._get_identity_parts(client)
        return f"{primary} — {secondary}" if secondary else primary

    def _get_credentials(self, service: dict):
        uid = self.client["values"].get(service["userid_column_id"], "")
        pwd = self.client["values"].get(service["password_column_id"], "")
        return uid, pwd

    def _launch_extension_autofill(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            MessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Autofilled")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "autofill_extension",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Extension autofill triggered for {service['name']}"
        )
        self.action_alert_requested.emit("autofill", self._get_identity_label(self.client))

        service["_client_name"] = self._get_identity_label(self.client)
        automation._send_to_extension(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _launch_autofill(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            is_itr = automation.is_itr_service(service)
            scc_cfg = self.db.get_scc_settings()
            scc_enabled = scc_cfg.get("enabled", True)
            client_id = self.client.get("id") if self.client else None
            is_scc_verified = self.db.is_client_scc_verified(client_id=client_id) if client_id else False
            if is_itr and uid and scc_enabled and not is_scc_verified:
                self._launch_manual_copy(service)
                return

            MessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Autofilled")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "autofill",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Autofill triggered for {service['name']}"
        )
        self.action_alert_requested.emit("autofill", self._get_identity_label(self.client))

        if automation.is_manual_portal(service):
            self._launch_manual(service, uid, pwd)
        else:
            automation.autofill_login(
                service, uid, pwd, self.client["id"],
                on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
            )
        self.window().showMinimized()



    def _suppress_sca_for_client(self):
        # MECP/SMTI hand staff this client's ids to copy; those copies must not arm SCA (#15).
        import clipboard_watch
        clipboard_watch.suppress_client(self.client["id"], seconds=300)

    def _launch_manual_copy(self, service: dict):
        uid, pwd = self._get_credentials(service)
        is_itr = automation.is_itr_service(service)
        scc_cfg = self.db.get_scc_settings()
        scc_enabled = scc_cfg.get("enabled", True)
        client_id = self.client.get("id") if self.client else None
        is_scc_verified = self.db.is_client_scc_verified(client_id=client_id) if client_id else False

        scc_mode = bool(is_itr and uid and scc_enabled and not is_scc_verified)

        if not uid or (not pwd and not scc_mode):
            MessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        service["_client_name"] = self._get_identity_label(self.client)
        on_error = lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        self._suppress_sca_for_client()
        if scc_mode:
            # The desktop builds the combinations and the SCC-U attempt; the card is the MECP card in SCC mode.
            from core.scc import manual as scc_manual
            scc_mode = scc_manual.open_card(uid, client_id, on_error)
            if not scc_mode and not pwd:
                MessageBox.warning(self, "Missing credentials", f"Could not open the SCC card for {service['name']}: "
                                    "no password combinations to show and no saved password.")
                return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Manual Copy (SCC)" if scc_mode else "Manual Copy")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "manual_copy",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"MECP manual copy {'with SCC combos ' if scc_mode else ''}triggered for {service['name']}"
        )
        self.action_alert_requested.emit("manual_copy", self._get_identity_label(self.client))

        if not scc_mode:
            automation.trigger_mecp(service, uid, pwd or "", self.client["id"], on_error=on_error)
        self.window().showMinimized()

    def _launch_manual_assist(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            MessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Manual Assist")
        except Exception:
            pass
        self.db.log_action(
            self.actor, "manual_assist", client_id=self.client["id"], service_id=service["id"],
            detail=f"Manual assist triggered for {service['name']}"
        )
        self.action_alert_requested.emit("manual_assist", self._get_identity_label(self.client))
        service["_client_name"] = self._get_identity_label(self.client)
        self._suppress_sca_for_client()
        automation.trigger_manual_assist(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _on_autofill_failed(self, portal: str, msg: str):
        MessageBox.critical(
            self, "Autofill failed",
            f"Could not complete {portal} autofill:\n{msg}\n\n"
            f"Verify the CSS selectors in Admin Mode -> Manage Services."
        )

    def _set_notes_status(self, text, tone):
        self.notes_status_lbl.set_text(text)
        self.notes_status_lbl.set_tone(tone)
        self.notes_status_lbl.show()

    def _on_notes_text_changed(self):
        if getattr(self, "_loading_notes", False):
            return
        if hasattr(self, "notes_status_lbl"):
            self._set_notes_status("Saving...", "muted")
        if hasattr(self, "_notes_timer"):
            self._notes_timer.start(500)

    def _auto_save_notes(self):
        if not self.client or not hasattr(self, "notes_edit"):
            return
        new_notes = self.notes_edit.toPlainText().strip()
        if self.client.get("notes") == new_notes:
            if hasattr(self, "notes_status_lbl"):
                self._set_notes_status("✓ Saved", "muted")
            return

        self.db.update_client_notes(self.client["id"], new_notes)
        self.client["notes"] = new_notes
        self.db.log_action(
            self.actor, "update_notes",
            client_id=self.client["id"],
            detail="Auto-saved client notes"
        )
        if hasattr(self, "notes_status_lbl"):
            self._set_notes_status("✓ Saved", "muted")


    def _on_label_clicked(self, val: str, label_name: str, is_secret: bool, timeout_sec: int):
        self._copy_to_clipboard(val, label_name, is_secret, timeout_sec)

    def _copy_to_clipboard(self, val: str, label_name: str, is_secret: bool, timeout_sec: int):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(val)
        
        c_name = self._get_identity_label(self.client) if self.client else label_name
        self.action_alert_requested.emit("manual_copy", c_name)
        
        if self.client:
            try:
                self.db.record_client_activity(self.client["id"], "Copied", label_name)
            except Exception:
                pass

        self.db.log_action(
            self.actor, "manual_copy",
            client_id=self.client["id"] if self.client else None,
            detail=f"Quick copied field: {label_name}"
        )
        
        if is_secret:
            QTimer.singleShot(timeout_sec * 1000, lambda: self._clear_clipboard_if_matches(val))

    def _clear_clipboard_if_matches(self, val: str):
        try:
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard.text() == val:
                clipboard.clear()
        except Exception:
            pass # Ignore clipboard clear failures
