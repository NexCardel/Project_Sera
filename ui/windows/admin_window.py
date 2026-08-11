"""
admin_window.py
----------------
Window 3: CRUD interface for the firm owner. Driven dynamically by MCL.
"""

from PySide6.QtCore import QSize, Signal, Qt
from PySide6.QtGui import QIcon
import qtawesome as qta
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
from ui.dialogs.audit_log_dialog import AuditLogDialog
from ui.dialogs.csv_import_dialog import CSVImportDialog
from ui.dialogs.filing_period_import_dialog import FilingPeriodImportDialog
from ui.dialogs.filing_type_manager_dialog import FilingTypeManagerDialog
from ui.dialogs.mcl_manager_dialog import MCLManagerDialog
from ui.dialogs.service_manager_dialog import ServiceManagerDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.utils.dynamic_form_widgets import make_input_widget, read_input_widget

BACK_ICON = str(Path(__file__).resolve().parents[2] / "Version SKY" / "Sera_SVG" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


class NewClientDialog(QDialog):
    """A focused, normal-mode-safe form for creating a client."""
    client_created = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._input_widgets = {}
        self._service_cbs = {}
        self.setWindowTitle("Add Client")
        self.setModal(True)
        self.resize(680, 640)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("<b>Add New Client</b>")
        title.setProperty("class", "DialogTitle")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QWidget()
        self.form_layout = QFormLayout(form_widget)
        self.form_layout.setSpacing(10)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, stretch=1)

        self._build_dynamic_form()

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        create_btn = QPushButton("Create Client")
        create_btn.setProperty("class", "primary")
        cancel_btn.clicked.connect(self.reject)
        create_btn.clicked.connect(self._on_create)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(create_btn)
        layout.addLayout(button_row)

    def _build_dynamic_form(self):
        self.form_layout.addRow(QLabel("<b>Client Information</b>"))
        for col in self.db.get_mcl_columns():
            widget = make_input_widget(col, "")
            self._input_widgets[col["id"]] = (col, widget)
            self.form_layout.addRow(f"{col['label']}:", widget)

        self.f_notes = QTextEdit()
        self.f_notes.setMaximumHeight(80)
        self.form_layout.addRow("Notes:", self.f_notes)

        self.form_layout.addRow(QLabel("<b>Attached Services</b>"))
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

        self.db.add_client(values, notes, service_ids)
        self.client_created.emit()
        self.accept()


class AdminPinDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Admin Mode")
        self.setModal(True)
        self._is_first_run = self.db.get_setting("admin_pin_hash") is None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Create Admin PIN:" if self._is_first_run else "Enter Admin PIN:"))

        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.pin_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        pin = self.pin_input.text()
        if not pin or len(pin) < 4:
            QMessageBox.warning(self, "Too short", "PIN must be at least 4 characters.")
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

    def __init__(self, db, actor: str = "Admin"):
        super().__init__()
        self.setObjectName("ManageClientsPage")
        self.db = db
        self.actor = actor
        self.selected_client_id = None
        self._input_widgets = {}
        self._service_cbs = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        page_header = QHBoxLayout()
        back_btn = QPushButton()
        back_btn.setIcon(qta.icon("mdi.arrow-left", color="#000000"))
        back_btn.setIconSize(QSize(22, 22))
        back_btn.setToolTip("Back to Search")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #D8CDB4;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #E6DCB8;
            }
        """)
        back_btn.clicked.connect(self.back_requested.emit)
        page_header.addWidget(back_btn)
        page_title = QLabel("<b>Manage Clients</b>")
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
        btn_inspect_conflict.clicked.connect(self._on_inspect_conflicts)
        banner_layout.addWidget(btn_inspect_conflict)
        
        layout.addWidget(self.conflict_banner_widget)
        self.conflict_banner_widget.hide()

        # The old header with buttons (Settings, Audit Log, etc.) has been removed.
        # These actions are now exclusively accessed via the new Sidebar shell.

        body = QHBoxLayout()

        left_layout = QVBoxLayout()
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.filter_combo, stretch=1)
        self.show_archived_cb = QCheckBox("Show Archived")
        self.show_archived_cb.toggled.connect(self._on_archive_toggle)
        filter_row.addWidget(self.show_archived_cb)
        left_layout.addLayout(filter_row)

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
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        left_layout.addWidget(self.table, stretch=1)


        self.selection_label = QLabel("")
        self.selection_label.setProperty("class", "InfoText")
        left_layout.addWidget(self.selection_label)

        bulk_row = QHBoxLayout()
        bulk_svc_btn = QPushButton("Attach/Detach Service on Selected...")
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

    def _reload_filters(self):
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All clients", None)
        for s in self.db.get_services():
            self.filter_combo.addItem(s["name"], s["id"])
        self.filter_combo.blockSignals(False)

    def _build_dynamic_form(self, client_values=None, client_services=None):
        # FIX: Properly destroy old UI rows so they don't block you from typing!
        while self.form_layout.rowCount() > 0:
            self.form_layout.removeRow(0)
                
        self._input_widgets.clear()
        self._service_cbs.clear()
        
        # Defensive check: Accept client_id (int), full client object (dict), or client_values dict
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

        self.form_layout.addRow(QLabel("<b>Client Information</b>"))
        for col in self.db.get_mcl_columns():
            val = client_values.get(col["id"], "")
            widget = make_input_widget(col, val)
            self._input_widgets[col["id"]] = (col, widget)
            self.form_layout.addRow(f"{col['label']}:", widget)
            
        self.f_notes = QTextEdit()
        self.f_notes.setMaximumHeight(80)
        self.f_notes.setPlainText(client_values.get("notes", ""))
        self.form_layout.addRow("Notes:", self.f_notes)

        self.form_layout.addRow(QLabel("<b>Attached Services</b>"))
        for s in self.db.get_services():
            cb = QCheckBox(s["name"])
            cb.setChecked(s["id"] in client_services)
            self._service_cbs[s["id"]] = cb
            self.form_layout.addRow("", cb)

    def _get_identity_label(self, client):
        identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c["is_identity"]]
        vals = [client["values"].get(cid, "") for cid in identity_cols if client["values"].get(cid)]
        return " — ".join(vals) if vals else "[No Identity Data]"

    def refresh(self):
        self._check_sync_conflicts()
        svc_id = self.filter_combo.currentData()
        show_archived = self.show_archived_cb.isChecked()
        clients = self.db.search_clients("", service_id=svc_id, archived_only=show_archived)
        
        # FIX: Remember which client you had selected so the screen doesn't wipe
        old_selected = self.selected_client_id
        
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["ID", "Client Identity"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        
        from ui.utils.theme import SmartTableWidgetItem

        row_to_select = -1
        for c in clients:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, SmartTableWidgetItem(str(c["id"])))
            self.table.setItem(r, 1, SmartTableWidgetItem(self._get_identity_label(c)))
            if c["id"] == old_selected:
                row_to_select = r
                
        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        
        if row_to_select >= 0:
            self.table.selectRow(row_to_select)
        else:
            self._on_new()



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
            self.selected_client_id = self.db.add_client(values, notes, service_ids)
            saved_client = self.db.get_client(self.selected_client_id)
            c_name = self._get_identity_label(saved_client) if saved_client else ""
            self.action_alert_requested.emit("create", c_name)
        else:
            self.db.update_client(self.selected_client_id, values, notes, service_ids)
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

    def _on_backup(self):
        dest_dir = QFileDialog.getExistingDirectory(self, "Choose backup destination")
        if not dest_dir:
            return
        try:
            backup_path = self.db.backup_to(dest_dir)
            self.db.log_action(self.actor, "backup", detail=f"Backed up to {backup_path}")
            self.action_alert_requested.emit("backup", None)
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", str(e))

    def _on_restore_backup(self):
        backup_dir = QFileDialog.getExistingDirectory(self, "Choose backup folder to restore")
        if not backup_dir:
            return
        confirm = QMessageBox.warning(
            self, "Confirm Database Restore",
            "WARNING: Restoring a database backup will overwrite your current live database.\n\n"
            "If Syncthing is active, this restored database will also sync to other team members' computers.\n\n"
            "Are you sure you want to proceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            try:
                self.db.restore_from(backup_dir)
                self.db.log_action(self.actor, "restore", detail=f"Restored from {backup_dir}")
                self.action_alert_requested.emit("restore", None)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Restore Error", f"Failed to restore backup:\n{e!s}")

    def _on_view_audit_log(self):
        dlg = AuditLogDialog(self.db, actor=self.actor, parent=self)
        dlg.toast_requested.connect(self.toast_requested.emit)
        self.request_slide_panel.emit(dlg, "Audit Log")

    def _on_export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Clients to CSV", "clients_export.csv", "CSV Files (*.csv)"
        )
        if path:
            try:
                self.db.export_clients_csv(path)
                self.db.log_action(self.actor, "csv_export", detail=f"Exported clients to {path}")
                self.action_alert_requested.emit("csv_export", None)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export CSV:\n{e!s}")

    def _on_download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Download Import Template", "clients_import_template.csv", "CSV Files (*.csv)"
        )
        if path:
            try:
                self.db.export_mcl_schema_csv(path)
                self.toast_requested.emit(f"Successfully downloaded template to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Download Error", f"Failed to download template:\n{e!s}")

    def _on_manage_mcl(self):
        dlg = MCLManagerDialog(self.db, self)
        dlg.finished.connect(self._build_dynamic_form)
        self.request_slide_panel.emit(dlg, "Manage Master Client List")

    def _on_manage_services(self):
        dlg = ServiceManagerDialog(self.db, self)
        dlg.finished.connect(self._reload_filters)
        dlg.finished.connect(self._build_dynamic_form)
        self.request_slide_panel.emit(dlg, "Manage Services")

    def _on_import_csv(self):
        dlg = CSVImportDialog(self.db, self)
        dlg.finished.connect(self.refresh)
        self.request_slide_panel.emit(dlg, "Import CSV")

    def _on_import_fps(self):
        dlg = FilingPeriodImportDialog(self.db, actor=self.actor, parent=self)
        dlg.toast_requested.connect(self.toast_requested.emit)
        dlg.finished.connect(self.refresh)
        self.request_slide_panel.emit(dlg, "Import Filing Periods")

    def _on_manage_filing_types(self):
        dlg = FilingTypeManagerDialog(self.db, self)
        self.request_slide_panel.emit(dlg, "Manage Filing Types")

    def _on_open_settings(self):
        dlg = SettingsDialog(self.db, actor=self.actor, parent=self)
        dlg.toast_requested.connect(self.toast_requested.emit)
        dlg.finished.connect(self.refresh)
        self.request_slide_panel.emit(dlg, "Settings")

    def _on_purge_duplicates(self):
        confirm = QMessageBox.question(
            self, "Purge Duplicate Clients",
            "This will scan all non-archived clients and permanently delete "
            "any duplicates that share the same identity column values.\n\n"
            "For each set of duplicates, the oldest record (lowest ID) is kept "
            "and the newer copies are deleted.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            results = self.db.purge_duplicate_clients()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to purge duplicates:\n{e!s}")
            return

        if results["deleted"] == 0:
            self.toast_requested.emit("No duplicate clients were detected.", 3000)
            return

        self.db.log_action(
            self.actor, "purge_duplicates",
            detail=f"Purged {results['deleted']} duplicate(s) across {results['groups']} group(s)"
        )


        class _DedupResultDialog(QDialog):
            def __init__(self, res, parent=None):
                super().__init__(parent)
                self.setWindowTitle("Purge Duplicates — Complete")
                self.resize(600, 400)
                layout = QVBoxLayout(self)
                from PySide6.QtWidgets import QTextEdit as _QTE
                layout.addWidget(QLabel(
                    f"<b>Deleted:</b> {res['deleted']} duplicate client(s)<br>"
                    f"<b>Groups:</b> {res['groups']} identity group(s) had duplicates"
                ))
                if res.get("details"):
                    layout.addWidget(QLabel("<b>Details:</b>"))
                    te = _QTE()
                    te.setReadOnly(True)
                    te.setPlainText("\n".join(res["details"]))
                    layout.addWidget(te, stretch=1)
                btn = QPushButton("OK")
                btn.setDefault(True)
                btn.clicked.connect(self.accept)
                brow = QHBoxLayout()
                brow.addStretch()
                brow.addWidget(btn)
                layout.addLayout(brow)

        dlg = _DedupResultDialog(results, self)
        dlg.exec()

    def _on_manage_staff_users(self):
        """Open the Admin-only Username <-> Alias Matrix Dialog."""
        from ui.dialogs.staff_alias_matrix_dialog import StaffAliasMatrixDialog
        dlg = StaffAliasMatrixDialog(self.db, parent=self)
        dlg.exec()
        self.refresh()

