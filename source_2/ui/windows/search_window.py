"""
search_window.py
----------------
Window 1: employee-facing search bar + spreadsheet-style results.
"""

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, QSize
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QStyledItemDelegate,
    QStyle,
)
from PySide6.QtGui import QPainter, QColor, QFont, QBrush, QFontMetrics
try:
    import qtawesome as qta
except Exception:
    qta = None
from ui.utils.theme import SmartTableWidgetItem


class ActivityCellDelegate(QStyledItemDelegate):
    """
    Renders table cells with a clean, modern spreadsheet-style selection cursor
    (vivid blue highlight fill with crisp white text when selected), custom formatting support,
    and activity breadcrumb badges for Client ID cells.
    """
    def paint(self, painter: QPainter, option, index):
        painter.save()
        self.initStyleOption(option, index)

        is_selected = bool(option.state & QStyle.State_Selected)
        parent_widget = option.widget

        # Draw cell background (selection, copy-flash, or custom format background)
        if is_selected:
            if parent_widget and parent_widget.property("is_flashing"):
                painter.fillRect(option.rect, QColor("#2E9B5F"))
            else:
                painter.fillRect(option.rect, option.palette.highlight())
        else:
            bg = index.data(Qt.BackgroundRole)
            if bg:
                painter.fillRect(option.rect, bg)
            else:
                painter.fillRect(option.rect, option.palette.base())

        rect = option.rect.adjusted(6, 0, -6, 0)
        main_text = str(index.data(Qt.DisplayRole) or "")
        activity_tag = index.data(Qt.UserRole + 2)

        main_font = option.font
        painter.setFont(main_font)
        fm = painter.fontMetrics()

        # Text color
        if is_selected:
            painter.setPen(QColor("#FFFFFF"))
        else:
            fg = index.data(Qt.ForegroundRole)
            if fg:
                painter.setPen(fg.color() if hasattr(fg, "color") else fg)
            else:
                painter.setPen(QColor("#241F1B"))

        y_center = rect.top() + (rect.height() + fm.ascent() - fm.descent()) // 2

        if activity_tag:
            main_w = fm.horizontalAdvance(main_text)
            painter.drawText(rect.left(), y_center, main_text)

            # Activity breadcrumb (smaller font, soft grey/light grey when selected)
            small_font = QFont(main_font)
            small_font.setPointSize(max(main_font.pointSize() - 2, 8))
            painter.setFont(small_font)
            small_fm = painter.fontMetrics()

            tag_color = QColor("#D1D5DB") if is_selected else QColor("#8E8E93")
            painter.setPen(tag_color)

            tag_x = rect.left() + main_w + 6
            tag_y = rect.top() + (rect.height() + small_fm.ascent() - small_fm.descent()) // 2
            painter.drawText(tag_x, tag_y, f"({activity_tag})")
        else:
            avail_w = max(rect.width(), 0)
            elided = fm.elidedText(main_text, Qt.ElideRight, avail_w)
            painter.drawText(rect.left(), y_center, elided)

        painter.restore()

    def sizeHint(self, option, index):
        base_size = super().sizeHint(option, index)
        activity_tag = index.data(Qt.UserRole + 2)
        if not activity_tag:
            return base_size

        main_text = str(index.data(Qt.DisplayRole) or "")
        fm = option.fontMetrics
        main_w = fm.horizontalAdvance(main_text)

        small_font = QFont(option.font)
        small_font.setPointSize(max(option.font.pointSize() - 2, 8))
        small_fm = QFontMetrics(small_font)
        tag_w = small_fm.horizontalAdvance(f"({activity_tag})")

        return QSize(main_w + tag_w + 20, base_size.height())



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
    toggle_sidebar_requested = Signal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._undo_stack = []
        self._redo_stack = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._on_search_changed)

        self._scroll_save_timer = QTimer(self)
        self._scroll_save_timer.setSingleShot(True)
        self._scroll_save_timer.setInterval(200)
        self._scroll_save_timer.timeout.connect(self._save_scroll_position)

        self._activity_refresh_timer = QTimer(self)
        self._activity_refresh_timer.setInterval(60000)
        self._activity_refresh_timer.timeout.connect(self._on_search_changed)
        self._activity_refresh_timer.start()

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Top Header Row
        header_row = QHBoxLayout()
        
        self.btn_toggle_sidebar = QPushButton()
        self.btn_toggle_sidebar.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_toggle_sidebar.setIcon(qta.icon("mdi.dock-left", color="#8E8D88"))
            self.btn_toggle_sidebar.setIconSize(QSize(20, 20))
        self.btn_toggle_sidebar.setFixedSize(36, 36)
        self.btn_toggle_sidebar.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_sidebar.setToolTip("Toggle Sidebar (Ctrl+B)")
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar_requested.emit)
        header_row.addWidget(self.btn_toggle_sidebar)
        header_row.addSpacing(6)

        title = QLabel("All Clients / Search")
        title.setProperty("class", "PageTitle")
        header_row.addWidget(title)
        
        # Cell Formatting & Undo/Redo Toolbar (Visible in both Normal and Admin modes)
        header_row.addSpacing(16)
        
        self.btn_fill_color = QPushButton()
        self.btn_fill_color.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_fill_color.setIcon(qta.icon("mdi.format-color-fill", color="#8E8D88"))
            self.btn_fill_color.setIconSize(QSize(20, 20))
        self.btn_fill_color.setFixedSize(36, 36)
        self.btn_fill_color.setToolTip("Cell Fill Color (Background)")
        self.btn_fill_color.clicked.connect(self._open_fill_menu_from_toolbar)
        header_row.addWidget(self.btn_fill_color)

        self.btn_text_color = QPushButton()
        self.btn_text_color.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_text_color.setIcon(qta.icon("mdi.format-color-text", color="#8E8D88"))
            self.btn_text_color.setIconSize(QSize(20, 20))
        self.btn_text_color.setFixedSize(36, 36)
        self.btn_text_color.setToolTip("Cell Text Color (Foreground)")
        self.btn_text_color.clicked.connect(self._open_text_menu_from_toolbar)
        header_row.addWidget(self.btn_text_color)

        self.btn_clear_fmt = QPushButton()
        self.btn_clear_fmt.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_clear_fmt.setIcon(qta.icon("mdi.eraser", color="#8E8D88"))
            self.btn_clear_fmt.setIconSize(QSize(20, 20))
        self.btn_clear_fmt.setFixedSize(36, 36)
        self.btn_clear_fmt.setToolTip("Clear Selected Cell Formatting")
        self.btn_clear_fmt.clicked.connect(self._clear_selected_formatting_from_toolbar)
        header_row.addWidget(self.btn_clear_fmt)

        header_row.addSpacing(8)

        self.btn_undo = QPushButton()
        self.btn_undo.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_undo.setIcon(qta.icon("mdi.undo", color="#8E8D88"))
            self.btn_undo.setIconSize(QSize(20, 20))
        self.btn_undo.setFixedSize(36, 36)
        self.btn_undo.setToolTip("Undo Cell Formatting (Ctrl+Z)")
        self.btn_undo.clicked.connect(self._undo_last_action)
        self.btn_undo.setEnabled(False)
        header_row.addWidget(self.btn_undo)

        self.btn_redo = QPushButton()
        self.btn_redo.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_redo.setIcon(qta.icon("mdi.redo", color="#8E8D88"))
            self.btn_redo.setIconSize(QSize(20, 20))
        self.btn_redo.setFixedSize(36, 36)
        self.btn_redo.setToolTip("Redo Cell Formatting (Ctrl+Y)")
        self.btn_redo.clicked.connect(self._redo_last_action)
        self.btn_redo.setEnabled(False)
        header_row.addWidget(self.btn_redo)

        header_row.addStretch()

        # Add Client button
        self.btn_add_client = QPushButton("+ Add Client")
        self.btn_add_client.setProperty("class", "primary")
        self.btn_add_client.setMinimumHeight(36)
        self.btn_add_client.setCursor(Qt.PointingHandCursor)
        self.btn_add_client.clicked.connect(self.add_client_requested.emit)
        header_row.addWidget(self.btn_add_client)
        header_row.addSpacing(8)

        # Sync Trigger (Refresh) button
        self.btn_sync = QPushButton()
        self.btn_sync.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_sync.setIcon(qta.icon("mdi.refresh", color="#8E8D88"))
            self.btn_sync.setIconSize(QSize(20, 20))
        self.btn_sync.setFixedSize(36, 36)
        self.btn_sync.setCursor(Qt.PointingHandCursor)
        self.btn_sync.setToolTip("Refresh table data (pull latest from synced database)")
        self.btn_sync.clicked.connect(self._on_manual_refresh)
        header_row.addWidget(self.btn_sync)

        layout.addLayout(header_row)

        # Search Bar + Services Row
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search clients across all columns...")
        self.search_box.setClearButtonEnabled(True)
        if qta:
            self.search_box.addAction(qta.icon("mdi.magnify", color="#8E8D88"), QLineEdit.LeadingPosition)
        self.search_box.setMinimumHeight(36)
        self.search_box.textChanged.connect(self._on_search_input_changed)
        self.search_box.returnPressed.connect(self._activate_current_result)
        self.search_box.installEventFilter(self)
        search_row.addWidget(self.search_box, stretch=3)

        lbl_services = QLabel("Filter:")
        lbl_services.setProperty("class", "SectionLabel")
        lbl_services.setStyleSheet("color: #8E8D88;")
        search_row.addWidget(lbl_services)
        
        self.service_filter = QComboBox()
        self.service_filter.setMinimumHeight(36)
        self.service_filter.setMinimumWidth(180)
        self.service_filter.currentIndexChanged.connect(self._on_search_input_changed)
        search_row.addWidget(self.service_filter)
        
        search_row.addStretch(1)

        self.btn_edit_client = QPushButton()
        self.btn_edit_client.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_edit_client.setIcon(qta.icon("mdi.pencil-outline", color="#8E8D88"))
            self.btn_edit_client.setIconSize(QSize(20, 20))
        self.btn_edit_client.setFixedSize(36, 36)
        self.btn_edit_client.setToolTip("Edit selected client profile")
        self.btn_edit_client.clicked.connect(self._request_edit_client)
        search_row.addWidget(self.btn_edit_client)

        self.btn_delete_client = QPushButton()
        self.btn_delete_client.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_delete_client.setIcon(qta.icon("mdi.delete-outline", color="#C62828"))
            self.btn_delete_client.setIconSize(QSize(20, 20))
        self.btn_delete_client.setFixedSize(36, 36)
        self.btn_delete_client.setToolTip("Permanently delete selected client record")
        self.btn_delete_client.clicked.connect(self._request_delete_client)
        search_row.addWidget(self.btn_delete_client)

        self.btn_manage_services = QPushButton()
        self.btn_manage_services.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_manage_services.setIcon(qta.icon("mdi.cog-outline", color="#8E8D88"))
            self.btn_manage_services.setIconSize(QSize(20, 20))
        self.btn_manage_services.setFixedSize(36, 36)
        self.btn_manage_services.setToolTip("Attach / Detach Services for selected client")
        self.btn_manage_services.clicked.connect(self._request_manage_services)
        search_row.addWidget(self.btn_manage_services)

        self.btn_archive_client = QPushButton()
        self.btn_archive_client.setProperty("class", "GhostIconButton")
        if qta:
            self.btn_archive_client.setIcon(qta.icon("mdi.archive-outline", color="#8E8D88"))
            self.btn_archive_client.setIconSize(QSize(20, 20))
        self.btn_archive_client.setFixedSize(36, 36)
        self.btn_archive_client.setToolTip("Archive selected client")
        self.btn_archive_client.clicked.connect(self._request_archive_client)
        search_row.addWidget(self.btn_archive_client)

        layout.addLayout(search_row)

        self.results_table = QTableWidget()
        self.results_table.setFrameShape(QFrame.NoFrame)
        self.results_table.setLineWidth(0)
        self.results_table.setMidLineWidth(0)
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
        self.results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self._show_cell_formatting_menu)
        self.results_table.setItemDelegate(ActivityCellDelegate(self.results_table))
        self.results_table.itemActivated.connect(self._on_item_activated)
        self.results_table.installEventFilter(self)
        self.results_table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.results_table.horizontalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.results_table.setStyleSheet("""
            QTableWidget {
                background-color: #FFFFFF;
                alternate-background-color: #FFFFFF;
                color: #241F1B;
                gridline-color: #D8CDB4;
                border: none;
                outline: none;
                selection-background-color: #0078D7;
                selection-color: #FFFFFF;
            }
            QHeaderView {
                background-color: #0A0A0A;
                border: none;
            }
            QHeaderView::section {
                background-color: #0A0A0A;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #262626;
                border-right: 1px solid #262626;
                padding: 6px;
            }
            QTableCornerButton::section {
                background-color: #0A0A0A;
                border: none;
            }
        """)
        
        from PySide6.QtGui import QKeySequence, QShortcut
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.results_table)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self._copy_selection_to_clipboard)

        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.activated.connect(self._undo_last_action)

        self.redo_shortcut = QShortcut(QKeySequence.Redo, self)
        self.redo_shortcut.activated.connect(self._redo_last_action)
        
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
        
        self.results_table.setProperty("is_flashing", True)
        self.results_table.viewport().update()

        def restore_style():
            try:
                self.results_table.setProperty("is_flashing", False)
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
        
        # Smart Filter Presets
        self.service_filter.addItem("All Clients", None)
        self.service_filter.addItem("🔥 Most Viewed / Active", "most_viewed")
        self.service_filter.addItem("⚡ Active Today", "active_today")
        self.service_filter.addItem("🌐 Has Attached Services", "has_services")
        self.service_filter.addItem("⚠️ Unassigned (No Services)", "no_services")
        self.service_filter.addItem("🎨 Formatted / Highlighted Cells", "has_formatting")
        self.service_filter.addItem("🔒 Has Login Credentials", "has_passwords")
        self.service_filter.addItem("⚠️ Missing Passwords", "missing_passwords")
        self.service_filter.addItem("📦 Archived Clients", "archived")
        
        # Specific Portal Services
        for s in self._cached_services:
            self.service_filter.addItem(f"Service: {s['name']}", s["id"])
            
        self.service_filter.blockSignals(False)

    def _on_scroll_changed(self, *_):
        if not getattr(self, "_restoring_scroll", False):
            self._scroll_save_timer.start()

    def _save_scroll_position(self):
        try:
            from PySide6.QtCore import QSettings
            settings = QSettings("AmanAssociates", "ProjectSera")
            v_val = self.results_table.verticalScrollBar().value()
            h_val = self.results_table.horizontalScrollBar().value()
            settings.setValue("search_grid_vscroll", v_val)
            settings.setValue("search_grid_hscroll", h_val)
        except Exception:
            pass

    def _restore_scroll_position(self):
        try:
            from PySide6.QtCore import QSettings
            settings = QSettings("AmanAssociates", "ProjectSera")
            v_val = settings.value("search_grid_vscroll", None)
            h_val = settings.value("search_grid_hscroll", None)
            self._restoring_scroll = True
            if v_val is not None:
                self.results_table.verticalScrollBar().setValue(int(v_val))
            if h_val is not None:
                self.results_table.horizontalScrollBar().setValue(int(h_val))
            self._restoring_scroll = False
        except Exception:
            self._restoring_scroll = False

    def _on_search_changed(self, *_):
        curr_row = self.results_table.currentRow()
        curr_col = self.results_table.currentColumn()
        saved_ranges = [
            (r.topRow(), r.bottomRow(), r.leftColumn(), r.rightColumn())
            for r in self.results_table.selectedRanges()
        ]

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

            if isinstance(svc_val, int):
                clients = self.db.search_clients(text, service_id=svc_val)
            elif isinstance(svc_val, str):
                clients = self.db.search_clients(text, filter_preset=svc_val)
            else:
                clients = self.db.search_clients(text)
            self.results_table.setRowCount(len(clients))

            services_map = {s["id"]: s["name"] for s in services}
            col_max_lens = [len(h) for h in headers]

            client_ids = [c["id"] for c in clients]
            fmt_map = self.db.get_cell_formatting_for_clients(client_ids)
            recent_acts = self.db.get_recent_client_activities(max_age_seconds=1800)
            
            # Pre-compute column metadata and brush cache for high-speed rendering
            col_meta = []
            for c_idx, col in enumerate(mcl_cols):
                col_id = col["id"]
                col_key = str(col_id)
                col_lbl = col["label"].strip().lower()
                is_id = (col.get("field_type") == "id" or col_lbl in {"id", "client id", "token"})
                is_num = col_lbl in {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "numer", "number"}
                col_meta.append((c_idx, col_id, col_key, is_id, is_num))

            brush_cache = {}
            def _get_brush(color_str):
                if not color_str:
                    return None
                if color_str not in brush_cache:
                    brush_cache[color_str] = QBrush(QColor(color_str))
                return brush_cache[color_str]

            self.results_table.blockSignals(True)

            for r, client in enumerate(clients):
                client_id = client["id"]
                client_vals = client.get("values", {})
                act_list = recent_acts.get(client_id, [])
                
                for c_idx, col_id, col_key, is_id_col, is_num_col in col_meta:
                    val = client_vals.get(col_id, "")
                    
                    if is_id_col:
                        raw_id = client.get("client_id_token") or str(client_id)
                        val = raw_id
                        act_tag = ""
                        if act_list:
                            top = act_list[0]
                            action_type = top["action_type"]
                            age = top["age_seconds"]
                            rel = "just now" if age < 60 else (f"{age // 60}m ago" if age < 3600 else f"{age // 3600}h ago")
                            act_tag = f"{action_type} • {rel}"
                    elif is_num_col:
                        val = str(r + 1)
                        act_tag = ""
                    else:
                        act_tag = ""
                        
                    calc_len = len(val) + (len(act_tag) + 4 if act_tag else 0)
                    if calc_len > col_max_lens[c_idx]:
                        col_max_lens[c_idx] = calc_len
                    item = SmartTableWidgetItem(val)
                    item.setData(Qt.UserRole, client_id)
                    item.setData(Qt.UserRole + 1, col_key)
                    if act_tag:
                        item.setData(Qt.UserRole + 2, act_tag)

                    if is_id_col and act_list:
                        tooltip_lines = [f"• {a['action_type']} ({'just now' if a['age_seconds'] < 60 else str(a['age_seconds']//60) + 'm ago'})" for a in act_list[:4]]
                        item.setToolTip(f"Client ID: {client.get('client_id_token') or str(client_id)}\n\nRecent Activity:\n" + "\n".join(tooltip_lines))

                    fmt = fmt_map.get((client_id, col_key))
                    if fmt:
                        font = item.font()
                        bg_b = _get_brush(fmt.get("bg_color"))
                        if bg_b:
                            item.setBackground(bg_b)
                            font.setBold(True)
                        fg_b = _get_brush(fmt.get("fg_color"))
                        if fg_b:
                            item.setForeground(fg_b)
                            font.setBold(True)
                        item.setFont(font)

                    self.results_table.setItem(r, c_idx, item)
                    
                # Services column
                client_svc_ids = client.get("service_ids", [])
                svc_names = [services_map[s_id] for s_id in client_svc_ids if s_id in services_map]
                svc_val_str = ", ".join(svc_names)
                if len(svc_val_str) > col_max_lens[-1]:
                    col_max_lens[-1] = len(svc_val_str)
                svc_item = SmartTableWidgetItem(svc_val_str)
                svc_item.setData(Qt.UserRole, client_id)
                svc_item.setData(Qt.UserRole + 1, "services")

                svc_fmt = fmt_map.get((client_id, "services"))
                if svc_fmt:
                    font = svc_item.font()
                    bg_b = _get_brush(svc_fmt.get("bg_color"))
                    if bg_b:
                        svc_item.setBackground(bg_b)
                        font.setBold(True)
                    fg_b = _get_brush(svc_fmt.get("fg_color"))
                    if fg_b:
                        svc_item.setForeground(fg_b)
                        font.setBold(True)
                    svc_item.setFont(font)

                self.results_table.setItem(r, len(mcl_cols), svc_item)

            self.results_table.blockSignals(False)


            if self.results_table.rowCount() > 0:
                if curr_row >= 0 and curr_col >= 0 and curr_row < self.results_table.rowCount() and curr_col < self.results_table.columnCount():
                    self.results_table.setCurrentCell(curr_row, curr_col)
                    for top, bottom, left, right in saved_ranges:
                        if bottom < self.results_table.rowCount() and right < self.results_table.columnCount():
                            from PySide6.QtWidgets import QTableWidgetSelectionRange
                            self.results_table.setRangeSelected(
                                QTableWidgetSelectionRange(top, left, bottom, right), True
                            )
                else:
                    self.results_table.setCurrentCell(0, 0)

            # Auto-fit column widths: compact fit for ID / Serial columns, comfortable fit for data columns
            fm = self.results_table.fontMetrics()
            char_w = fm.horizontalAdvance("M")
            for c_idx, col in enumerate(mcl_cols):
                col_lbl = col["label"].strip().lower()
                is_compact = (col.get("field_type") == "id" or col_lbl in {"id", "client id", "token", "no", "no.", "sl no", "sl. no.", "s.no.", "sno", "numer", "number"})
                if is_compact:
                    self.results_table.resizeColumnToContents(c_idx)
                    self.results_table.setColumnWidth(c_idx, max(self.results_table.columnWidth(c_idx) + 16, 50))
                else:
                    sample_chars = min(col_max_lens[c_idx], 40)
                    calc_w = max(sample_chars * char_w + 24, fm.horizontalAdvance(col["label"]) + 28, 80)
                    self.results_table.setColumnWidth(c_idx, calc_w)

            # Services column (last section stretches to fill remaining space)
            if len(headers) > len(mcl_cols):
                svc_idx = len(mcl_cols)
                self.results_table.setColumnWidth(
                    svc_idx, max(min(col_max_lens[-1], 50) * char_w + 24, fm.horizontalAdvance("Services") + 28, 100)
                )

            if not getattr(self, "_has_restored_scroll", False):
                self._has_restored_scroll = True
                QTimer.singleShot(100, self._restore_scroll_position)
        finally:
            self.results_table.setSortingEnabled(True)
            self.results_table.horizontalHeader().setSortIndicatorShown(True)
            self.results_table.horizontalHeader().setSectionsClickable(True)
            self.results_table.setUpdatesEnabled(True)




    def _on_item_activated(self, item: QTableWidgetItem):
        client_id = item.data(Qt.UserRole)
        if client_id is not None:
            self.client_selected.emit(client_id)

    def _update_undo_redo_buttons(self):
        if hasattr(self, "btn_undo"):
            self.btn_undo.setEnabled(len(self._undo_stack) > 0)
        if hasattr(self, "btn_redo"):
            self.btn_redo.setEnabled(len(self._redo_stack) > 0)

    def _undo_last_action(self):
        if hasattr(self, "search_box") and self.search_box.hasFocus():
            self.search_box.undo()
            return

        if not self._undo_stack:
            return

        op = self._undo_stack.pop()
        self._redo_stack.append(op)

        to_set = [st for st in op["prev"] if st.get("bg_color") or st.get("fg_color")]
        to_clear = [(st["client_id"], st["column_key"]) for st in op["prev"] if not (st.get("bg_color") or st.get("fg_color"))]

        if to_set:
            self.db.bulk_set_cell_formatting(to_set)
        if to_clear:
            self.db.clear_cell_formatting(to_clear)

        self._on_search_changed()
        self._update_undo_redo_buttons()
        self.toast_requested.emit("Undid cell formatting (Ctrl+Z)", "info")

    def _redo_last_action(self):
        if hasattr(self, "search_box") and self.search_box.hasFocus():
            self.search_box.redo()
            return

        if not self._redo_stack:
            return

        op = self._redo_stack.pop()
        self._undo_stack.append(op)

        to_set = [st for st in op["new"] if st.get("bg_color") or st.get("fg_color")]
        to_clear = [(st["client_id"], st["column_key"]) for st in op["new"] if not (st.get("bg_color") or st.get("fg_color"))]

        if to_set:
            self.db.bulk_set_cell_formatting(to_set)
        if to_clear:
            self.db.clear_cell_formatting(to_clear)

        self._on_search_changed()
        self._update_undo_redo_buttons()
        self.toast_requested.emit("Redid cell formatting (Ctrl+Y)", "info")

    def _execute_formatting_operation(self, action_type: str, val: str):
        from PySide6.QtCore import QPoint
        selected_ranges = self.results_table.selectedRanges()
        if not selected_ranges:
            self.toast_requested.emit("Please select grid cells to format", "info")
            return

        client_ids = set()
        for r_range in selected_ranges:
            for r in range(r_range.topRow(), r_range.bottomRow() + 1):
                item = self.results_table.item(r, 0)
                if item:
                    cid = item.data(Qt.UserRole)
                    if cid:
                        client_ids.add(cid)

        existing_map = self.db.get_cell_formatting_for_clients(list(client_ids))

        prev_states = []
        new_states = []
        cells_to_format = []
        cells_to_clear = []

        for r_range in selected_ranges:
            for r in range(r_range.topRow(), r_range.bottomRow() + 1):
                for c in range(r_range.leftColumn(), r_range.rightColumn() + 1):
                    item = self.results_table.item(r, c)
                    if item:
                        cid = item.data(Qt.UserRole)
                        ckey = item.data(Qt.UserRole + 1)
                        if cid and ckey:
                            cur_fmt = existing_map.get((cid, str(ckey))) or {}
                            old_bg = cur_fmt.get("bg_color") or ""
                            old_fg = cur_fmt.get("fg_color") or ""
                            prev_states.append({"client_id": cid, "column_key": ckey, "bg_color": old_bg, "fg_color": old_fg})

                            if action_type == "fill":
                                new_bg = "" if val == "clear" else val
                                new_fg = old_fg
                            elif action_type == "text":
                                new_bg = old_bg
                                new_fg = "" if val == "clear" else val
                            elif action_type == "all" and val == "clear":
                                new_bg = ""
                                new_fg = ""

                            new_states.append({"client_id": cid, "column_key": ckey, "bg_color": new_bg, "fg_color": new_fg})

                            if new_bg or new_fg:
                                cells_to_format.append({"client_id": cid, "column_key": ckey, "bg_color": new_bg, "fg_color": new_fg})
                            else:
                                cells_to_clear.append((cid, ckey))

        if cells_to_format:
            self.db.bulk_set_cell_formatting(cells_to_format)
        if cells_to_clear:
            self.db.clear_cell_formatting(cells_to_clear)

        self._undo_stack.append({"prev": prev_states, "new": new_states})
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

        self._on_search_changed()
        count = len(prev_states)
        if action_type == "all" and val == "clear":
            self.toast_requested.emit(f"Cleared cell formatting for {count} item(s)", "info")
        else:
            self.toast_requested.emit(f"Applied cell formatting to {count} item(s)", "success")

    def _open_fill_menu_from_toolbar(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QColorDialog
        menu = self._build_formatting_menu_fill_only()
        pos = self.btn_fill_color.mapToGlobal(QPoint(0, self.btn_fill_color.height()))
        chosen = menu.exec_(pos)
        if chosen and chosen.data():
            action_type, val = chosen.data()
            if val == "custom":
                color = QColorDialog.getColor(parent=self)
                if not color.isValid():
                    return
                val = color.name()
            self._execute_formatting_operation(action_type, val)

    def _open_text_menu_from_toolbar(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QColorDialog
        menu = self._build_formatting_menu_text_only()
        pos = self.btn_text_color.mapToGlobal(QPoint(0, self.btn_text_color.height()))
        chosen = menu.exec_(pos)
        if chosen and chosen.data():
            action_type, val = chosen.data()
            if val == "custom":
                color = QColorDialog.getColor(parent=self)
                if not color.isValid():
                    return
                val = color.name()
            self._execute_formatting_operation(action_type, val)

    def _clear_selected_formatting_from_toolbar(self):
        self._execute_formatting_operation("all", "clear")

    def _build_formatting_menu_fill_only(self):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        fill_presets = [
            ("Vivid Yellow", "#FFF200"),
            ("Soft Yellow", "#FFF3CD"),
            ("Vivid Green", "#A8D08D"),
            ("Soft Green", "#D4EDDA"),
            ("Vivid Red", "#FF9999"),
            ("Soft Red", "#F8D7DA"),
            ("Vivid Blue", "#9BC2E6"),
            ("Soft Blue", "#CCE5FF"),
            ("Amber Orange", "#FFC000"),
            ("Lavender Purple", "#C5A5CF"),
            ("Medium Gray", "#AEAEAE"),
            ("Dark Navy Header", "#2F5597"),
        ]
        for label, hex_val in fill_presets:
            act = menu.addAction(label)
            if qta:
                act.setIcon(qta.icon("mdi.circle", color=hex_val))
            act.setData(("fill", hex_val))
        menu.addSeparator()
        custom_fill_act = menu.addAction("Custom Fill Color...")
        if qta:
            custom_fill_act.setIcon(qta.icon("mdi.palette-outline", color="#241F1B"))
        custom_fill_act.setData(("fill", "custom"))
        clear_fill_act = menu.addAction("Clear Fill Color")
        if qta:
            clear_fill_act.setIcon(qta.icon("mdi.format-color-fill", color="#999999"))
        clear_fill_act.setData(("fill", "clear"))
        return menu

    def _build_formatting_menu_text_only(self):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        text_presets = [
            ("Dark Red", "#9C0006"),
            ("Dark Green", "#006100"),
            ("Dark Yellow", "#9C6500"),
            ("Deep Navy", "#002060"),
            ("Pure Black", "#000000"),
            ("Pure White", "#FFFFFF"),
            ("Deep Purple", "#7030A0"),
        ]
        for label, hex_val in text_presets:
            act = menu.addAction(label)
            if qta:
                act.setIcon(qta.icon("mdi.circle", color=hex_val))
            act.setData(("text", hex_val))
        menu.addSeparator()
        custom_text_act = menu.addAction("Custom Text Color...")
        if qta:
            custom_text_act.setIcon(qta.icon("mdi.palette-outline", color="#241F1B"))
        custom_text_act.setData(("text", "custom"))
        clear_text_act = menu.addAction("Clear Text Color")
        if qta:
            clear_text_act.setIcon(qta.icon("mdi.format-color-text", color="#999999"))
        clear_text_act.setData(("text", "clear"))
        return menu

    def _show_cell_formatting_menu(self, pos):
        from PySide6.QtWidgets import QMenu, QColorDialog
        from PySide6.QtGui import QColor, QBrush, QFont
        
        selected_ranges = self.results_table.selectedRanges()
        if not selected_ranges:
            return
            
        menu = QMenu(self)
        
        # Fill Color Submenu
        fill_menu = menu.addMenu("Cell Fill Color (Background)")
        if qta:
            fill_menu.setIcon(qta.icon("mdi.format-color-fill", color="#241F1B"))
            
        fill_presets = [
            ("Vivid Yellow", "#FFF200"),
            ("Soft Yellow", "#FFF3CD"),
            ("Vivid Green", "#A8D08D"),
            ("Soft Green", "#D4EDDA"),
            ("Vivid Red", "#FF9999"),
            ("Soft Red", "#F8D7DA"),
            ("Vivid Blue", "#9BC2E6"),
            ("Soft Blue", "#CCE5FF"),
            ("Amber Orange", "#FFC000"),
            ("Lavender Purple", "#C5A5CF"),
            ("Medium Gray", "#AEAEAE"),
            ("Dark Navy Header", "#2F5597"),
        ]
        for label, hex_val in fill_presets:
            act = fill_menu.addAction(label)
            if qta:
                act.setIcon(qta.icon("mdi.circle", color=hex_val))
            act.setData(("fill", hex_val))
            
        fill_menu.addSeparator()
        custom_fill_act = fill_menu.addAction("Custom Fill Color...")
        if qta:
            custom_fill_act.setIcon(qta.icon("mdi.palette-outline", color="#241F1B"))
        custom_fill_act.setData(("fill", "custom"))
        
        clear_fill_act = fill_menu.addAction("Clear Fill Color")
        if qta:
            clear_fill_act.setIcon(qta.icon("mdi.format-color-fill", color="#999999"))
        clear_fill_act.setData(("fill", "clear"))
        
        # Text Color Submenu
        text_menu = menu.addMenu("Cell Text Color (Foreground)")
        if qta:
            text_menu.setIcon(qta.icon("mdi.format-color-text", color="#241F1B"))
            
        text_presets = [
            ("Dark Red", "#9C0006"),
            ("Dark Green", "#006100"),
            ("Dark Yellow", "#9C6500"),
            ("Deep Navy", "#002060"),
            ("Pure Black", "#000000"),
            ("Pure White", "#FFFFFF"),
            ("Deep Purple", "#7030A0"),
        ]
        for label, hex_val in text_presets:
            act = text_menu.addAction(label)
            if qta:
                act.setIcon(qta.icon("mdi.circle", color=hex_val))
            act.setData(("text", hex_val))
            
        text_menu.addSeparator()
        custom_text_act = text_menu.addAction("Custom Text Color...")
        if qta:
            custom_text_act.setIcon(qta.icon("mdi.palette-outline", color="#241F1B"))
        custom_text_act.setData(("text", "custom"))
        
        clear_text_act = text_menu.addAction("Clear Text Color")
        if qta:
            clear_text_act.setIcon(qta.icon("mdi.format-color-text", color="#999999"))
        clear_text_act.setData(("text", "clear"))
        
        menu.addSeparator()
        clear_all_act = menu.addAction("Clear All Cell Formatting")
        if qta:
            clear_all_act.setIcon(qta.icon("mdi.eraser", color="#D9383A"))
        clear_all_act.setData(("all", "clear"))
        
        chosen_action = menu.exec_(self.results_table.viewport().mapToGlobal(pos))
        if not chosen_action or not chosen_action.data():
            return
            
        action_type, val = chosen_action.data()
        
        if val == "custom":
            color = QColorDialog.getColor(parent=self)
            if not color.isValid():
                return
            val = color.name()
            
        self._execute_formatting_operation(action_type, val)

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
