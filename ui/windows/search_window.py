"""
search_window.py
----------------
Window 1: employee-facing search bar + spreadsheet-style results.
"""

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, QSize
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
try:
    import qtawesome as qta
except Exception:
    qta = None
from ui.utils.theme import SmartTableWidgetItem



class SearchWindow(QWidget):

    client_selected = Signal(int)
    add_client_requested = Signal()
    edit_client_requested = Signal(int)
    delete_client_requested = Signal(int)
    manage_services_requested = Signal(int)
    archive_client_requested = Signal(int)
    admin_mode_requested = Signal()
    dashboard_requested = Signal()
    toast_requested = Signal(str, str)
    action_alert_requested = Signal(str, str)

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._on_search_changed)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Top Header Row
        header_row = QHBoxLayout()
        title = QLabel("All Clients / Search")
        title.setProperty("class", "PageTitle")
        header_row.addWidget(title)
        header_row.addStretch()

        self.btn_refresh = QPushButton()
        if qta:
            self.btn_refresh.setIcon(qta.icon("mdi.refresh", color="#241F1B"))
            self.btn_refresh.setIconSize(QSize(20, 20))
        self.btn_refresh.setFixedSize(36, 36)
        self.btn_refresh.setToolTip("Refresh workspace & trigger LAN database sync")
        self.btn_refresh.clicked.connect(self._on_manual_refresh)
        header_row.addWidget(self.btn_refresh)

        self.btn_archive_client = QPushButton()
        if qta:
            self.btn_archive_client.setIcon(qta.icon("mdi.archive-outline", color="#241F1B"))
            self.btn_archive_client.setIconSize(QSize(20, 20))
        self.btn_archive_client.setFixedSize(36, 36)
        self.btn_archive_client.setToolTip("Archive selected client")
        self.btn_archive_client.clicked.connect(self._request_archive_client)
        header_row.addWidget(self.btn_archive_client)

        self.btn_add_client = QPushButton(" Add Client")
        if qta:
            self.btn_add_client.setIcon(qta.icon("mdi.plus", color="#FFFFFF"))
            self.btn_add_client.setIconSize(QSize(18, 18))
        self.btn_add_client.setMinimumHeight(36)
        self.btn_add_client.setProperty("class", "primary")
        self.btn_add_client.clicked.connect(self.add_client_requested.emit)
        header_row.addWidget(self.btn_add_client)
        layout.addLayout(header_row)

        # Search & Filter Row
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by company, proprietor, ID...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumHeight(36)
        self.search_box.textChanged.connect(self._on_search_input_changed)
        self.search_box.returnPressed.connect(self._activate_current_result)
        self.search_box.installEventFilter(self)
        search_row.addWidget(self.search_box, stretch=3)

        lbl_services = QLabel("Services:")
        lbl_services.setProperty("class", "SidebarSection")
        lbl_services.setStyleSheet("color: black;")
        search_row.addWidget(lbl_services)
        
        self.service_filter = QComboBox()
        self.service_filter.setMinimumHeight(36)
        self.service_filter.setMinimumWidth(160)
        self.service_filter.currentIndexChanged.connect(self._on_search_input_changed)
        search_row.addWidget(self.service_filter)
        
        search_row.addStretch(1)

        self.btn_edit_client = QPushButton()
        if qta:
            self.btn_edit_client.setIcon(qta.icon("mdi.pencil-outline", color="#241F1B"))
            self.btn_edit_client.setIconSize(QSize(20, 20))
        self.btn_edit_client.setFixedSize(36, 36)
        self.btn_edit_client.setToolTip("Edit selected client profile")
        self.btn_edit_client.clicked.connect(self._request_edit_client)
        search_row.addWidget(self.btn_edit_client)

        self.btn_delete_client = QPushButton()
        if qta:
            self.btn_delete_client.setIcon(qta.icon("mdi.delete-outline", color="#D9383A"))
            self.btn_delete_client.setIconSize(QSize(20, 20))
        self.btn_delete_client.setFixedSize(36, 36)
        self.btn_delete_client.setToolTip("Permanently delete selected client record")
        self.btn_delete_client.clicked.connect(self._request_delete_client)
        search_row.addWidget(self.btn_delete_client)

        self.btn_manage_services = QPushButton()
        if qta:
            self.btn_manage_services.setIcon(qta.icon("mdi.cog-outline", color="#241F1B"))
            self.btn_manage_services.setIconSize(QSize(20, 20))
        self.btn_manage_services.setFixedSize(36, 36)
        self.btn_manage_services.setToolTip("Attach / Detach Services for selected client")
        self.btn_manage_services.clicked.connect(self._request_manage_services)
        search_row.addWidget(self.btn_manage_services)

        layout.addLayout(search_row)

        self.results_table = QTableWidget()
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.results_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setShowGrid(True)
        self.results_table.setAlternatingRowColors(False)
        self.results_table.viewport().setCursor(Qt.PointingHandCursor)
        self.results_table.setSortingEnabled(True)
        self.results_table.horizontalHeader().setSortIndicatorShown(True)
        self.results_table.horizontalHeader().setSectionsClickable(True)
        self.results_table.itemActivated.connect(self._on_item_activated)
        self.results_table.installEventFilter(self)
        self.results_table.viewport().installEventFilter(self)
        
        from PySide6.QtGui import QKeySequence, QShortcut
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.results_table)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self._copy_selection_to_clipboard)
        
        layout.addWidget(self.results_table)

        self.set_admin_mode(False)

        hint = QLabel(
            "Type to search, press Enter to open the top result "
            "(\u2193/\u2191 to pick a different one first)."
        )
        hint.setProperty("class", "HintText")
        layout.addWidget(hint)

        self.search_box.setFocus()

    def eventFilter(self, obj, event):
        try:
            if obj is self.search_box and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down and self.results_table.rowCount() > 0:
                self.results_table.setFocus()
                if self.results_table.currentRow() < 0:
                    self.results_table.setCurrentCell(0, 0)
                return True
            elif (obj is self.results_table or obj is self.results_table.viewport()) and event.type() == QEvent.KeyPress:
                if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_C:
                    self._copy_selection_to_clipboard()
                    return True
        except Exception:
            pass
        return False

    def _copy_selection_to_clipboard(self):
        from PySide6.QtWidgets import QApplication
        selected_ranges = self.results_table.selectedRanges()
        rows_data = []

        if selected_ranges:
            for r_range in selected_ranges:
                for r in range(r_range.topRow(), r_range.bottomRow() + 1):
                    row_cells = []
                    for c in range(r_range.leftColumn(), r_range.rightColumn() + 1):
                        item = self.results_table.item(r, c)
                        row_cells.append(item.text() if item else "")
                    rows_data.append("\t".join(row_cells))
        else:
            item = self.results_table.currentItem()
            if item:
                rows_data.append(item.text())

        if rows_data:
            text = "\n".join(rows_data)
            QApplication.clipboard().setText(text)
            self._flash_copied_items()
            
            if len(rows_data) == 1 and "\t" not in text:
                msg = f"Copied '{text}' to clipboard"
            elif len(rows_data) == 1:
                msg = "Copied selected record to clipboard"
            else:
                msg = f"Copied {len(rows_data)} rows to clipboard"
                
            self.toast_requested.emit(msg, "success")

    def _flash_copied_items(self):
        from PySide6.QtCore import QTimer
        
        orig_style = self.results_table.styleSheet() or ""
        # Flash selection as bright green (#2E9B5F)
        flash_style = orig_style + """
            QTableWidget::item:selected {
                background-color: #2E9B5F !important;
                color: #FFFFFF !important;
            }
        """
        self.results_table.setStyleSheet(flash_style)
        self.results_table.viewport().update()

        def restore_style():
            try:
                self.results_table.setStyleSheet(orig_style)
                self.results_table.viewport().update()
            except Exception:
                pass

        QTimer.singleShot(500, restore_style)

    def _on_search_input_changed(self, *_):
        self._search_timer.start(150)

    def _on_clear_search(self):
        self._search_timer.stop()
        self.search_box.clear()
        self.search_box.setFocus()
        self._on_search_changed()

    def _activate_current_result(self):
        if self._search_timer.isActive():
            self._search_timer.stop()
            self._on_search_changed()
        item = self.results_table.currentItem()
        if item is not None:
            self._on_item_activated(item)

    def _reload_filters(self):
        is_admin = getattr(self, "is_admin_mode", False)
        all_cols = self.db.get_mcl_columns()
        if is_admin:
            self._cached_mcl_cols = [c for c in all_cols if c.get("admin_show_in_search", True)]
        else:
            self._cached_mcl_cols = [c for c in all_cols if c.get("show_in_search", True)]

        self._cached_services = self.db.get_services()
        self.service_filter.blockSignals(True)
        self.service_filter.clear()
        self.service_filter.addItem("All Services", None)
        for s in self._cached_services:
            self.service_filter.addItem(s["name"], s["id"])
        self.service_filter.addItem("📦 Archived Clients", "archived")
        self.service_filter.blockSignals(False)

    def _on_search_changed(self, *_):
        text = self.search_box.text().strip()
        svc_val = self.service_filter.currentData()
        
        is_admin = getattr(self, "is_admin_mode", False)
        all_cols = self.db.get_mcl_columns()
        if is_admin:
            mcl_cols = [c for c in all_cols if c.get("admin_show_in_search", True)]
        else:
            mcl_cols = [c for c in all_cols if c.get("show_in_search", True)]
        self._cached_mcl_cols = mcl_cols
            
        services = getattr(self, "_cached_services", None)
        if services is None:
            services = self.db.get_services()
            self._cached_services = services
        
        headers = [col["label"] for col in mcl_cols]
        headers.append("Services")

        self.results_table.setUpdatesEnabled(False)
        self.results_table.setSortingEnabled(False)
        try:
            self.results_table.setColumnCount(len(headers))
            self.results_table.setHorizontalHeaderLabels(headers)
            self.results_table.horizontalHeader().setStretchLastSection(True)

            if svc_val == "archived":
                clients = self.db.search_clients(text, service_id=None, archived_only=True)
            else:
                clients = self.db.search_clients(text, service_id=svc_val)
            self.results_table.setRowCount(len(clients))

            services_map = {s["id"]: s["name"] for s in services}
            col_max_lens = [len(h) for h in headers]

            for r, client in enumerate(clients):


                client_id = client["id"]
                client_vals = client.get("values", {})
                
                for c_idx, col in enumerate(mcl_cols):
                    val = client_vals.get(col["id"], "")
                    col_lbl = col["label"].strip().lower()
                    if col.get("field_type") == "id":
                        val = client.get("client_id_token") or f"CLI-{client_id:05d}"
                    elif col_lbl in {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "numer", "number"}:
                        val = str(r + 1)
                    if len(val) > col_max_lens[c_idx]:
                        col_max_lens[c_idx] = len(val)
                    item = SmartTableWidgetItem(val)
                    item.setData(Qt.UserRole, client_id)
                    self.results_table.setItem(r, c_idx, item)
                    
                client_svc_ids = client.get("service_ids", [])
                svc_names = [services_map[s_id] for s_id in client_svc_ids if s_id in services_map]
                svc_val = ", ".join(svc_names)
                if len(svc_val) > col_max_lens[-1]:
                    col_max_lens[-1] = len(svc_val)
                svc_item = SmartTableWidgetItem(svc_val)
                svc_item.setData(Qt.UserRole, client_id)
                self.results_table.setItem(r, len(mcl_cols), svc_item)


            if self.results_table.rowCount() > 0:
                self.results_table.setCurrentCell(0, 0)

            # Ultra-fast O(1) column width calculation without scanning all table cell widgets
            fm = self.results_table.fontMetrics()
            char_w = fm.horizontalAdvance("M")
            for c_idx, h_text in enumerate(headers):
                sample_chars = min(col_max_lens[c_idx], 40)
                calc_w = max(sample_chars * char_w + 32, fm.horizontalAdvance(h_text) + 36, 95)
                self.results_table.setColumnWidth(c_idx, calc_w)
        finally:
            self.results_table.setSortingEnabled(True)
            self.results_table.horizontalHeader().setSortIndicatorShown(True)
            self.results_table.horizontalHeader().setSectionsClickable(True)
            self.results_table.setUpdatesEnabled(True)




    def _on_item_activated(self, item: QTableWidgetItem):
        client_id = item.data(Qt.UserRole)
        if client_id is not None:
            self.client_selected.emit(client_id)

    def _selected_client_id(self):
        item = self.results_table.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _request_edit_client(self):
        client_id = self._selected_client_id()
        if client_id is not None:
            self.edit_client_requested.emit(client_id)

    def _request_delete_client(self):
        client_id = self._selected_client_id()
        if client_id is not None:
            self.delete_client_requested.emit(client_id)

    def _request_manage_services(self):
        client_id = self._selected_client_id()
        if client_id is not None:
            self.manage_services_requested.emit(client_id)

    def _request_archive_client(self):
        client_id = self._selected_client_id()
        if client_id is not None:
            self.archive_client_requested.emit(client_id)

    def set_admin_mode(self, active: bool):
        """Expose detailed admin mutation controls when Admin Mode is active."""
        self.is_admin_mode = active
        admin_buttons = [
            getattr(self, "btn_edit_client", None),
            getattr(self, "btn_delete_client", None),
            getattr(self, "btn_manage_services", None),
        ]
        for btn in admin_buttons:
            if btn is not None:
                btn.setVisible(active)
        if hasattr(self, "results_table"):
            self._reload_filters()
            self._on_search_changed()



    def _on_manual_refresh(self):
        self.refresh()
        self.toast_requested.emit("Workspace refreshed & LAN database synced", "info")
        try:
            if hasattr(self.db, "_bump_sync_revision_if_configured"):
                self.db._bump_sync_revision_if_configured()
        except Exception:
            pass

    def refresh(self):
        self._reload_filters()
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.results_table.clearContents()
        self.results_table.setRowCount(0)
        self.search_box.setFocus()
        self._search_timer.stop()
        self._on_search_changed()

    def focus_and_select_search(self):
        self.search_box.setFocus()
        self.search_box.selectAll()
