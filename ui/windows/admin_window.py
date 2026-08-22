"""
admin_window.py
----------------
Window 3: CRUD interface for the firm owner. Driven dynamically by MCL.
"""

from PySide6.QtCore import QSize, QTimer, Signal, Qt
from PySide6.QtGui import QIcon
try:
    import qtawesome as qta
except Exception:
    qta = None
from pathlib import Path
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import security
from ui.dialogs.csv_import_dialog import CSVImportDialog
from ui.dialogs.mcl_manager_dialog import MCLManagerDialog
from ui.dialogs.service_manager_dialog import ServiceManagerDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.utils.dynamic_form_widgets import make_input_widget, read_input_widget, set_input_widget_value
from ui.windows.search_window import ActivityCellDelegate

BACK_ICON = str(Path(__file__).resolve().parents[2] / "assets" / "icons" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


def _safe_qta_icon(name: str, color: str = "#FFFFFF"):
    """Return a QIcon from qtawesome, or a blank QIcon if unavailable."""
    try:
        if qta:
            return qta.icon(name, color=color)
    except Exception:
        pass
    return QIcon()


class NewClientDialog(QDialog):
    """A focused, normal-mode-safe form for creating a client."""
    client_created = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._input_widgets = {}
        self._service_cbs = {}
        self.setWindowTitle("Add Client — Project Sera")
        self.setModal(True)
        self.resize(680, 640)
        self.setMinimumSize(580, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header Frame
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.account-plus-outline", color="#2E9B5F").pixmap(26, 26))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Add New Client")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Enter client identification, credentials, contact details, and compliance services.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Divider
        from PySide6.QtWidgets import QFrame
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        layout.addWidget(divider)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_widget = QWidget()
        self.form_layout = QFormLayout(form_widget)
        self.form_layout.setSpacing(10)
        self.form_layout.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, stretch=1)

        self._build_dynamic_form()

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setIcon(_safe_qta_icon("mdi.close", color="#8E8D88"))
        cancel_btn.clicked.connect(self.reject)

        create_btn = QPushButton("Create Client")
        create_btn.setProperty("class", "primary")
        create_btn.setIcon(_safe_qta_icon("mdi.check", color="#FFFFFF"))
        create_btn.clicked.connect(self._on_create)

        button_row.addWidget(cancel_btn)
        button_row.addWidget(create_btn)
        layout.addLayout(button_row)

    def _build_dynamic_form(self):
        lbl_info = QLabel("CLIENT INFORMATION")
        lbl_info.setProperty("class", "SectionLabel")
        self.form_layout.addRow(lbl_info)
        for col in self.db.get_mcl_columns():
            widget = make_input_widget(col, "", mask_password=False)
            self._input_widgets[col["id"]] = (col, widget)
            lbl_col = QLabel(f"{col['label']}:")
            lbl_col.setProperty("class", "RowLabel")
            self.form_layout.addRow(lbl_col, widget)

        self.f_notes = QTextEdit()
        self.f_notes.setMaximumHeight(80)
        lbl_notes = QLabel("Notes:")
        lbl_notes.setProperty("class", "RowLabel")
        self.form_layout.addRow(lbl_notes, self.f_notes)

        lbl_svc = QLabel("ATTACHED SERVICES")
        lbl_svc.setProperty("class", "SectionLabel")
        self.form_layout.addRow(lbl_svc)
        for service in self.db.get_services():
            checkbox = QCheckBox(service["name"])
            self._service_cbs[service["id"]] = checkbox
            self.form_layout.addRow("", checkbox)

    def _on_create(self):
        mcl_cols = self.db.get_mcl_columns()
        if not mcl_cols:
            QMessageBox.warning(self, "No Schema", "Define at least one MCL column before adding clients.")
            return

        values = {
            col_id: read_input_widget(col_def, widget)
            for col_id, (col_def, widget) in self._input_widgets.items()
        }
        notes = self.f_notes.toPlainText().strip()
        service_ids = [service_id for service_id, checkbox in self._service_cbs.items() if checkbox.isChecked()]

        duplicates = self.db.find_duplicate_clients(values)
        if duplicates:
            proceed = QMessageBox.question(
                self, "Possible duplicate",
                "A client already exists with this identity value:\n\n"
                + "\n".join(f"• {duplicate}" for duplicate in duplicates)
                + "\n\nCreate this client anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if proceed != QMessageBox.Yes:
                return

        self.db.add_client(values, notes, service_ids, actor=getattr(self, "actor", "Staff"))
        self.client_created.emit()
        self.accept()


class AdminPinDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Security Verification — Project Sera")
        self.setModal(True)
        self.setFixedSize(380, 260)
        self._is_first_run = self.db.get_setting("admin_pin_hash") is None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.shield-lock-outline", color="#2E9B5F").pixmap(28, 28))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Create Admin PIN" if self._is_first_run else "Admin Mode Access")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Enter your master PIN to access administrative features." if not self._is_first_run else "Set a master PIN to protect administrator tools.")
        sub_lbl.setStyleSheet("font-size: 11.5px; color: #8E8D88;")
        sub_lbl.setWordWrap(True)
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        from PySide6.QtWidgets import QFrame
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        layout.addWidget(divider)

        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.Password)
        self.pin_input.setPlaceholderText("Enter 4+ digit PIN...")
        self.pin_input.setStyleSheet("font-size: 14px; padding: 8px 12px;")
        self.pin_input.returnPressed.connect(self._on_accept)
        layout.addWidget(self.pin_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setIcon(_safe_qta_icon("mdi.close", color="#8E8D88"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        unlock_btn = QPushButton("Set PIN" if self._is_first_run else "Unlock Admin")
        unlock_btn.setProperty("class", "primary")
        unlock_btn.setIcon(_safe_qta_icon("mdi.lock-open-outline", color="#FFFFFF"))
        unlock_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(unlock_btn)

        layout.addLayout(btn_row)

    def _on_accept(self):
        pin = self.pin_input.text()
        if not pin or len(pin) < 4:
            QMessageBox.warning(self, "Too Short", "PIN must be at least 4 characters.")
            return

        if self._is_first_run:
            self.db.set_setting("admin_pin_hash", security.hash_admin_pin(pin))
            self.accept()
            return

        stored_hash = self.db.get_setting("admin_pin_hash")
        if security.verify_admin_pin(pin, stored_hash):
            self.accept()
        else:
            QMessageBox.critical(self, "Incorrect PIN", "That PIN is incorrect.")
            self.pin_input.clear()

class AdminWindow(QWidget):
    back_requested = Signal()
    request_slide_panel = Signal(QWidget, str)
    toast_requested = Signal(str, int)
    action_alert_requested = Signal(str, str)
    settings_saved = Signal()  # Forwarded from SettingsDialog when user saves

    def __init__(self, db, actor: str = "Admin"):
        super().__init__()
        self.setObjectName("ManageClientsPage")
        self.db = db
        self.actor = actor
        self.selected_client_id = None
        self._input_widgets = {}
        self._service_cbs = {}
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.refresh)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(60000)
        self._activity_timer.timeout.connect(self._refresh_activity_tags)
        self._activity_timer.start()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        page_header = QHBoxLayout()
        back_btn = QPushButton()
        back_btn.setProperty("class", "GhostIconButton")
        back_btn.setIcon(qta.icon("mdi.arrow-left", color="#8E8D88") if qta else QIcon(BACK_ICON))
        back_btn.setIconSize(QSize(20, 20))
        back_btn.setFixedSize(36, 36)
        back_btn.setToolTip("Back to Search (Esc)")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self.back_requested.emit)
        page_header.addWidget(back_btn)
        page_header.addSpacing(4)
        page_title = QLabel("Manage Clients")
        page_title.setProperty("class", "PageTitle")
        page_header.addWidget(page_title)
        page_header.addStretch()
        layout.addLayout(page_header)

        # Syncthing Conflict Warning Banner
        self.conflict_banner_widget = QWidget()
        self.conflict_banner_widget.setProperty("class", "ConflictBanner")
        banner_layout = QHBoxLayout(self.conflict_banner_widget)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        self.conflict_label = QLabel("⚠️ Syncthing conflict file(s) detected in database directory!")
        self.conflict_label.setProperty("class", "ConflictLabel")
        banner_layout.addWidget(self.conflict_label)
        banner_layout.addStretch()
        btn_inspect_conflict = QPushButton("Inspect Conflicts...")
        if qta:
            btn_inspect_conflict.setIcon(qta.icon("mdi.file-alert-outline", color="#FFFFFF"))
        btn_inspect_conflict.clicked.connect(self._on_inspect_conflicts)
        banner_layout.addWidget(btn_inspect_conflict)
        
        layout.addWidget(self.conflict_banner_widget)
        self.conflict_banner_widget.hide()

        # The old header with buttons (Settings, Audit Log, etc.) has been removed.
        # These actions are now exclusively accessed via the new Sidebar shell.

        body = QHBoxLayout()

        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)

        # Search Bar for Manage Clients Table
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search clients (ID, Name, PAN, GSTIN...)")
        self.search_input.setClearButtonEnabled(True)
        if qta:
            self.search_input.addAction(qta.icon("mdi.magnify", color="#889988"), QLineEdit.LeadingPosition)
        self.search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_input)
        left_layout.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.filter_combo, stretch=1)
        self.show_archived_cb = QCheckBox("Show Archived")
        self.show_archived_cb.toggled.connect(self._on_archive_toggle)
        filter_row.addWidget(self.show_archived_cb)
        left_layout.addLayout(filter_row)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Sort:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("ID (Ascending: 1, 2, 3...)", ("id", "asc"))
        self.sort_combo.addItem("ID (Descending: 3, 2, 1...)", ("id", "desc"))
        self.sort_combo.addItem("Client Identity (A → Z)", ("identity", "asc"))
        self.sort_combo.addItem("Client Identity (Z → A)", ("identity", "desc"))
        self.sort_combo.addItem("🔥 Most Viewed / Activity", ("activity", "desc"))
        self.sort_combo.addItem("Recently Added (Newest first)", ("created_at", "desc"))
        self.sort_combo.addItem("Recently Updated", ("updated_at", "desc"))
        self.sort_combo.currentIndexChanged.connect(self.refresh)
        sort_row.addWidget(self.sort_combo, stretch=1)
        left_layout.addLayout(sort_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["ID", "Client Identity"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumWidth(0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.setItemDelegate(ActivityCellDelegate(self.table))
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        left_layout.addWidget(self.table, stretch=1)


        self.selection_label = QLabel("")
        self.selection_label.setProperty("class", "InfoText")
        left_layout.addWidget(self.selection_label)

        bulk_row = QHBoxLayout()
        bulk_svc_btn = QPushButton("Attach/Detach Service on Selected...")
        if qta:
            bulk_svc_btn.setIcon(qta.icon("mdi.briefcase-edit-outline", color="#FFFFFF"))
        bulk_svc_btn.clicked.connect(self._on_bulk_service)
        bulk_row.addWidget(bulk_svc_btn)
        left_layout.addLayout(bulk_row)

        body.addLayout(left_layout, stretch=1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(0)
        self.form_widget = QWidget()
        self.form_layout = QFormLayout(self.form_widget)
        scroll.setWidget(self.form_widget)
        
        self._build_dynamic_form()

        btn_row = QHBoxLayout()
        new_btn = QPushButton("New")
        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "primary")
        archive_btn = QPushButton("Archive")
        restore_btn = QPushButton("Restore")
        purge_btn = QPushButton("Delete Permanently")

        if qta:
            new_btn.setIcon(qta.icon("mdi.account-plus", color="#FFFFFF"))
            save_btn.setIcon(qta.icon("mdi.content-save-outline", color="#FFFFFF"))
            archive_btn.setIcon(qta.icon("mdi.archive-outline", color="#FFFFFF"))
            restore_btn.setIcon(qta.icon("mdi.backup-restore", color="#FFFFFF"))
            purge_btn.setIcon(qta.icon("mdi.delete-forever-outline", color="#FF4D4D"))

        new_btn.clicked.connect(self._on_new)
        save_btn.clicked.connect(self._on_save)
        archive_btn.clicked.connect(self._on_archive)
        restore_btn.clicked.connect(self._on_restore)
        purge_btn.clicked.connect(self._on_purge)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(archive_btn)
        btn_row.addWidget(restore_btn)
        btn_row.addWidget(purge_btn)
        self._active_mode_btns = [new_btn, save_btn, archive_btn]
        self._archived_mode_btns = [restore_btn, purge_btn]
        for btn in self._archived_mode_btns:
            btn.setVisible(False)
        
        right_layout = QVBoxLayout()
        right_layout.addWidget(scroll, stretch=1)
        right_layout.addLayout(btn_row)
        body.addLayout(right_layout, stretch=2)

        layout.addLayout(body)
        self._reload_filters()

    def _on_search_changed(self):
        self._search_timer.start()

    def _reload_filters(self):
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All clients", None)
        self.filter_combo.addItem("🔥 Most Viewed / Active", "most_viewed")
        self.filter_combo.addItem("⚡ Active Today", "active_today")
        self.filter_combo.addItem("🌐 Has Attached Services", "has_services")
        self.filter_combo.addItem("⚠️ Unassigned (No Services)", "no_services")
        self.filter_combo.addItem("🔒 Has Login Credentials", "has_passwords")
        self.filter_combo.addItem("⚠️ Missing Passwords", "missing_passwords")
        self.filter_combo.addItem("📦 Archived Only", "archived")
        for s in self.db.get_services():
            self.filter_combo.addItem(f"Service: {s['name']}", s["id"])
        self.filter_combo.blockSignals(False)

    def _resolve_client_data(self, client_values, client_services):
        if isinstance(client_values, int):
            c_data = self.db.get_client(client_values)
            if c_data:
                client_services = client_services or c_data.get("service_ids", [])
                c_vals = dict(c_data.get("values", {}))
                c_vals["notes"] = c_data.get("notes", "")
                client_values = c_vals
            else:
                client_values = {}
        elif isinstance(client_values, dict) and "values" in client_values:
            c_vals = dict(client_values.get("values", {}))
            if "notes" in client_values and "notes" not in c_vals:
                c_vals["notes"] = client_values.get("notes", "")
            client_services = client_services or client_values.get("service_ids", [])
            client_values = c_vals
        elif not isinstance(client_values, dict):
            client_values = {}
        client_services = client_services or []
        return client_values, client_services

    def _build_dynamic_form(self, client_values=None, client_services=None, force_rebuild=False):
        c_vals, c_svcs = self._resolve_client_data(client_values, client_services)

        if not force_rebuild and getattr(self, "_form_built", False) and self._input_widgets:
            for col_id, (col, widget) in self._input_widgets.items():
                val = c_vals.get(col_id, "")
                set_input_widget_value(col, widget, val)
            if hasattr(self, "f_notes") and self.f_notes is not None:
                self.f_notes.setPlainText(c_vals.get("notes", ""))
            for sid, cb in self._service_cbs.items():
                cb.setChecked(sid in c_svcs)
            return

        while self.form_layout.rowCount() > 0:
            self.form_layout.removeRow(0)
                
        self._input_widgets.clear()
        self._service_cbs.clear()

        lbl_info = QLabel("CLIENT INFORMATION")
        lbl_info.setProperty("class", "SectionLabel")
        self.form_layout.addRow(lbl_info)
        for col in self.db.get_mcl_columns():
            val = c_vals.get(col["id"], "")
            widget = make_input_widget(col, val, mask_password=False)
            self._input_widgets[col["id"]] = (col, widget)
            lbl_col = QLabel(f"{col['label']}:")
            lbl_col.setProperty("class", "RowLabel")
            self.form_layout.addRow(lbl_col, widget)
            
        self.f_notes = QTextEdit()
        self.f_notes.setMaximumHeight(80)
        self.f_notes.setPlainText(c_vals.get("notes", ""))
        lbl_notes = QLabel("Notes:")
        lbl_notes.setProperty("class", "RowLabel")
        self.form_layout.addRow(lbl_notes, self.f_notes)

        lbl_svc = QLabel("ATTACHED SERVICES")
        lbl_svc.setProperty("class", "SectionLabel")
        self.form_layout.addRow(lbl_svc)
        for s in self.db.get_services():
            cb = QCheckBox(s["name"])
            cb.setChecked(s["id"] in c_svcs)
            self._service_cbs[s["id"]] = cb
            self.form_layout.addRow("", cb)

        self._form_built = True

    def _get_identity_label(self, client, identity_cols=None):
        if identity_cols is None:
            identity_cols = getattr(self, "_cached_identity_cols", None)
            if identity_cols is None:
                identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c.get("is_identity")]
                self._cached_identity_cols = identity_cols
        vals = [client["values"].get(cid, "") for cid in identity_cols if client.get("values", {}).get(cid)]
        return " — ".join(vals) if vals else "[No Identity Data]"

    def refresh(self):
        self._check_sync_conflicts()
        identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c.get("is_identity")]
        self._cached_identity_cols = identity_cols

        filter_data = self.filter_combo.currentData() if hasattr(self, "filter_combo") else None
        show_archived = self.show_archived_cb.isChecked() if hasattr(self, "show_archived_cb") else False
        search_query = self.search_input.text().strip() if hasattr(self, "search_input") else ""

        svc_id = filter_data if isinstance(filter_data, int) else None
        filter_preset = filter_data if isinstance(filter_data, str) else None
        if show_archived:
            filter_preset = "archived"

        clients = self.db.search_clients(
            search_query,
            service_id=svc_id,
            archived_only=(filter_preset == "archived"),
            filter_preset=filter_preset
        )

        # Apply Sort By Selection
        if hasattr(self, "sort_combo") and self.sort_combo.currentData():
            sort_key, sort_dir = self.sort_combo.currentData()
            reverse = (sort_dir == "desc")
            if sort_key == "id":
                def _id_sort_key(c):
                    token = str(c.get("client_id_token") or c.get("id", ""))
                    if token.isdigit():
                        return (0, int(token), token)
                    return (1, 0, token.lower())
                clients.sort(key=_id_sort_key, reverse=reverse)
            elif sort_key == "identity":
                clients.sort(key=lambda c: self._get_identity_label(c, identity_cols).lower(), reverse=reverse)
            elif sort_key == "activity":
                stats_map = self.db.get_all_activity_stats()
                clients.sort(key=lambda c: (stats_map.get(c["id"], {}).get("view_count", 0) * 3 + stats_map.get(c["id"], {}).get("action_count", 0)), reverse=reverse)
            elif sort_key == "created_at":
                clients.sort(key=lambda c: c.get("created_at") or "", reverse=reverse)
            elif sort_key == "updated_at":
                clients.sort(key=lambda c: c.get("updated_at") or "", reverse=reverse)
        
        # Remember which client you had selected so the screen doesn't wipe
        old_selected = self.selected_client_id
        
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["ID", "Client Identity"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setRowCount(len(clients))
        
        from ui.utils.theme import SmartTableWidgetItem
        recent_acts = self.db.get_recent_client_activities(max_age_seconds=1800)

        row_to_select = -1
        for r, c in enumerate(clients):
            self.table.setItem(r, 0, SmartTableWidgetItem(str(c["id"])))
            
            lbl = self._get_identity_label(c, identity_cols)
            item1 = SmartTableWidgetItem(lbl)
            act_list = recent_acts.get(c["id"], [])
            if act_list:
                top = act_list[0]
                action_type = top["action_type"]
                age = top["age_seconds"]
                rel = "just now" if age < 60 else (f"{age // 60}m ago" if age < 3600 else f"{age // 3600}h ago")
                item1.setData(Qt.UserRole + 2, f"{action_type} • {rel}")
                tooltip_lines = [f"• {a['action_type']} ({'just now' if a['age_seconds'] < 60 else str(a['age_seconds']//60) + 'm ago'})" for a in act_list[:4]]
                item1.setToolTip(f"{lbl}\n\nRecent Activity:\n" + "\n".join(tooltip_lines))

            self.table.setItem(r, 1, item1)
            if c["id"] == old_selected:
                row_to_select = r
                
        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.setUpdatesEnabled(True)
        
        if row_to_select >= 0:
            self.table.selectRow(row_to_select)
        else:
            self._on_new()

    def _refresh_activity_tags(self):
        try:
            recent_acts = self.db.get_recent_client_activities(max_age_seconds=1800)
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                name_item = self.table.item(r, 1)
                if not id_item or not name_item:
                    continue
                try:
                    cid = int(id_item.text())
                except ValueError:
                    continue
                act_list = recent_acts.get(cid, [])
                if act_list:
                    top = act_list[0]
                    action_type = top["action_type"]
                    age = top["age_seconds"]
                    rel = "just now" if age < 60 else (f"{age // 60}m ago" if age < 3600 else f"{age // 3600}h ago")
                    name_item.setData(Qt.UserRole + 2, f"{action_type} • {rel}")
                else:
                    name_item.setData(Qt.UserRole + 2, "")
            self.table.viewport().update()
        except Exception:
            pass



    def _check_sync_conflicts(self):
        conflicts = self.db.get_sync_conflicts()
        if conflicts:
            count = len(conflicts)
            self.conflict_label.setText(
                f"⚠️ Syncthing conflict file(s) detected ({count} conflict file{'s' if count > 1 else ''}) in database directory!"
            )
            self.conflict_banner_widget.show()
        else:
            self.conflict_banner_widget.hide()

    def _on_inspect_conflicts(self):
        conflicts = self.db.get_sync_conflicts()
        if not conflicts:
            self.toast_requested.emit("No Syncthing sync conflict files detected.", 3000)
            return

        msg = "The following Syncthing conflict files were detected:\n\n"
        msg += "\n".join(conflicts)
        msg += "\n\nConflict files happen when two team members edit database files simultaneously.\n"
        msg += "Would you like to open the database folder to resolve them?"

        reply = QMessageBox.question(
            self, "Syncthing Conflict Files", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes and conflicts:
            import os
            import subprocess
            folder = os.path.dirname(conflicts[0])
            subprocess.Popen(f'explorer "{folder}"')

    def _get_selected_client_ids(self) -> list:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        return [int(self.table.item(r, 0).text()) for r in rows]

    def _on_row_selected(self):
        ids = self._get_selected_client_ids()

        if len(ids) == 1:
            client = self.db.get_client(ids[0])
            if not client:
                return
            self.selected_client_id = ids[0]
            client_vals = client["values"]
            client_vals["notes"] = client["notes"]
            self._build_dynamic_form(client_vals, client["service_ids"])
            self.form_widget.setEnabled(True)
            self.selection_label.setText("")
        elif len(ids) > 1:
            # Multiple clients selected: the single-client edit form doesn't
            # apply to a batch, so clear/disable it and route edits through
            # the bulk action buttons (Archive/Restore/Delete/Attach Service)
            # instead, which act on every selected row.
            self.selected_client_id = None
            self._build_dynamic_form()
            self.form_widget.setEnabled(False)
            self.selection_label.setText(f"{len(ids)} clients selected.")
        else:
            self.selection_label.setText("")

    def _on_new(self):
        self.selected_client_id = None
        self.table.clearSelection()
        self._build_dynamic_form()
        self.form_widget.setEnabled(True)
        self.selection_label.setText("")

    def open_new_client_form(self):
        """Prepare the shared client form for the normal-mode Add Client action."""
        self._on_new()

    def open_client_editor(self, client_id: int):
        """Select a client in the editor so the admin action applies to it."""
        self.refresh()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and int(item.text()) == client_id:
                self.table.selectRow(row)
                self.table.setCurrentCell(row, 1)
                return True
        return False

    def delete_client(self, client_id: int):
        """Permanently remove one client from the admin-only search action."""
        client = self.db.get_client(client_id)
        if not client:
            return
        identity = self._get_identity_label(client)
        if QMessageBox.question(
            self, "Delete client",
            f'Permanently delete "{identity}"? This cannot be undone.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.bulk_delete_clients([client_id])
            self.refresh()

    def manage_client_services(self, client_id: int):
        if self.open_client_editor(client_id):
            self._on_bulk_service()

    def _on_save(self):
        if len(self._get_selected_client_ids()) > 1:
            self.toast_requested.emit("The edit form only applies to one client at a time. Use the bulk buttons for bulk actions.", 3000)
            return

        mcl_cols = self.db.get_mcl_columns()
        if not mcl_cols:
            QMessageBox.warning(self, "No Schema", "Define at least one MCL column before saving clients.")
            return

        values = {col_id: read_input_widget(col_def, widget) for col_id, (col_def, widget) in self._input_widgets.items()}
        notes = self.f_notes.toPlainText().strip()
        service_ids = [sid for sid, cb in self._service_cbs.items() if cb.isChecked()]

        is_new = self.selected_client_id is None

        dupes = self.db.find_duplicate_clients(values, exclude_client_id=self.selected_client_id)
        if dupes:
            proceed = QMessageBox.question(
                self, "Possible duplicate",
                "A client already exists with this identity value:\n\n"
                + "\n".join(f"• {d}" for d in dupes)
                + "\n\nSave anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if proceed != QMessageBox.Yes:
                return

        # FIX: Added actual popups to tell you it worked!
        if is_new:
            self.selected_client_id = self.db.add_client(values, notes, service_ids, actor=self.actor)
            saved_client = self.db.get_client(self.selected_client_id)
            c_name = self._get_identity_label(saved_client) if saved_client else ""
            self.action_alert_requested.emit("create", c_name)
        else:
            self.db.update_client(self.selected_client_id, values, notes, service_ids, actor=self.actor)
            saved_client = self.db.get_client(self.selected_client_id)
            c_name = self._get_identity_label(saved_client) if saved_client else ""
            self.action_alert_requested.emit("update", c_name)
            
        self.refresh()

    def _on_archive_toggle(self, checked: bool):
        for btn in self._active_mode_btns:
            btn.setVisible(not checked)
        for btn in self._archived_mode_btns:
            btn.setVisible(checked)
        self.refresh()

    def _on_archive(self):
        ids = self._get_selected_client_ids()
        if not ids: return
        who = "this client" if len(ids) == 1 else f"these {len(ids)} clients"
        if QMessageBox.question(
            self, "Archive client" if len(ids) == 1 else "Archive clients",
            f"Archive {who}? They'll be hidden from Search and this list "
            "until restored from \"Show Archived\" -- nothing is deleted."
        ) == QMessageBox.Yes:
            self.db.bulk_archive_clients(ids)
            self.action_alert_requested.emit("archive", None)
            self.refresh()

    def _on_restore(self):
        ids = self._get_selected_client_ids()
        if not ids: return
        self.db.bulk_unarchive_clients(ids)
        self.action_alert_requested.emit("unarchive", None)
        self.refresh()

    def _on_purge(self):
        ids = self._get_selected_client_ids()
        if not ids: return
        who = "this client" if len(ids) == 1 else f"these {len(ids)} clients"
        if QMessageBox.question(
            self, "Delete permanently",
            f"Permanently delete {who}? This cannot be undone from within "
            "the app -- only a Syncthing file-version restore could bring it back.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.bulk_delete_clients(ids)
            self.refresh()

    def _on_bulk_service(self):
        ids = self._get_selected_client_ids()
        if not ids:
            self.toast_requested.emit("Select one or more clients first.", 3000)
            return

        services = self.db.get_services()
        if not services:
            self.toast_requested.emit("Define at least one service under 'Manage Services...' first.", 3000)
            return

        names = [s["name"] for s in services]
        name, ok = QInputDialog.getItem(self, "Choose service", "Service:", names, 0, False)
        if not ok:
            return
        service = next(s for s in services if s["name"] == name)

        action = QMessageBox.question(
            self, "Attach or detach",
            f"Attach \"{name}\" to the {len(ids)} selected client(s)?\n\n"
            "Choose No to instead detach it from them.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes
        )
        if action == QMessageBox.Cancel:
            return

        self.db.bulk_set_service(ids, service["id"], attach=(action == QMessageBox.Yes))
        self.refresh()
        if len(ids) == 1:
            self._on_row_selected()

    def _open_unified_settings(self, page="general", on_close_callback=None):
        if getattr(self, "_settings_dialog", None) is None:
            from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
            dlg = UnifiedSettingsDialog(self.db, actor=self.actor, page=page, parent=self.window())
            dlg.toast_requested.connect(self.toast_requested.emit)
            dlg.settings_saved.connect(self.settings_saved.emit)
            self._settings_dialog = dlg
        else:
            self._settings_dialog.set_page(page)

        self._settings_dialog.exec()
        if on_close_callback:
            on_close_callback()

    def _on_backup(self):
        self._open_unified_settings("backup", self.refresh)

    def _on_restore_backup(self):
        self._open_unified_settings("backup", self.refresh)

    def _on_view_audit_log(self):
        from ui.dialogs.audit_log_dialog import AuditLogDialog
        dlg = AuditLogDialog(self.db, actor=self.actor, parent=self)
        dlg.toast_requested.connect(self.toast_requested.emit)
        dlg.exec()

    def _on_export_csv(self):
        self._open_unified_settings("export", self.refresh)

    def _on_download_template(self):
        self._on_export_csv()

    def _on_manage_mcl(self):
        self._open_unified_settings("mcl", lambda: (self._build_dynamic_form(force_rebuild=True), self.refresh()))

    def _on_manage_services(self):
        self._open_unified_settings("services", lambda: (self._reload_filters(), self._build_dynamic_form(force_rebuild=True), self.refresh()))

    def _on_import_csv(self):
        dlg = CSVImportDialog(self.db, self)
        dlg.exec()
        self.refresh()

    def _on_open_settings(self):
        self._open_unified_settings("general")

    def _on_purge_duplicates(self):
        self._open_unified_settings("purge", self.refresh)


    def _on_open_sera_sync(self):
        """Open the Sera Sync dialog for LAN database synchronization."""
        if not hasattr(self, '_sync_service') or self._sync_service is None:
            QMessageBox.warning(self, "Sera Sync", "Sync service is not running. Please restart the app.")
            return
        from ui.dialogs.sera_sync_dialog import SeraSyncDialog
        dlg = SeraSyncDialog(self._sync_service, db=self.db, actor=self.actor, parent=self)
        dlg.exec()

    def set_sync_service(self, sync_service):
        """Inject the SyncPeerService instance for Sera Sync dialog access."""
        self._sync_service = sync_service
