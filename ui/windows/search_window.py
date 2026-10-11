"""
search_window.py
----------------
Window 1: employee-facing search bar + spreadsheet-style results.
"""

import re
import time
from PySide6.QtCore import QEvent, Qt, QTimer, Signal, QMimeData, QPoint
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from PySide6.QtGui import QBrush, QColor
try:
    import qtawesome as qta
except Exception:
    qta = None
from ui.components.kit import (
    ROLE_SERVICES, Card, ChipBar, DataGrid, GridFooter, GridFrame, IconButton, Input, LabelledAction,
    PageHeader, SelectionBar, SettingRow, Switch,
)
from ui.utils.responsive import WidthAware
from ui.utils.theme import SmartTableWidgetItem
from ui.utils.tokens import KIT, TOKENS as T

# Quick-filter chips: chip id -> service_filter data (the hidden combo stays the source of truth).
_QUICK_FILTERS = (
    ("all", "All", None),
    ("active_today", "Active today", "active_today"),
    ("has_services", "Has services", "has_services"),
    ("no_services", "No services", "no_services"),
    ("has_formatting", "Highlighted", "has_formatting"),
    ("archived", "Archived", "archived"),
)
_MORE_FILTERS = (
    ("most_viewed", "Most viewed / active"),
    ("has_passwords", "Has login credentials"),
    ("missing_passwords", "Missing passwords"),
)
_SERVICE_DOTS = (T["em"], T["info"], T["purple"], T["warn"], KIT["orange"])
_LAST_COLUMN_REASON = "At least one column must stay visible"
_NO_ROW_REASON = "Select a client row first"
_NO_CELL_REASON = "Select grid cells first"
_NO_UNDO_REASON = "Nothing to undo (Ctrl+Z)"
_NO_REDO_REASON = "Nothing to redo (Ctrl+Y)"


def _brush_colour(brush, default):
    """A cell brush's colour for the Fill / Text swatches; no brush or a see-through one is the default."""
    if not brush or brush.style() == Qt.NoBrush or brush.color().alpha() == 0:
        return default
    return brush.color().name()


def _column_spec(col):
    """DataGrid.set_column arguments for one master-column-list column (fixed small widths for the
    narrow kinds, the rest share what is left; name columns have a maximum share)."""
    lbl = col.get("label", "").strip().lower()
    key = f"{lbl} {col.get('field_name', '').lower()}"
    if (col.get("field_type") == "id"
            or lbl in {"id", "client id", "token", "no", "no.", "sl no", "sl. no.", "s.no.", "sno", "numer", "number"}):
        return dict(kind="num", width=52, minimum=44)
    words = set(re.findall(r"[a-z0-9]+", key))
    if words & {"password", "pass", "pwd", "pin"}:
        return dict(kind="mono", width=104, minimum=96, priority=3)
    if words & {"gst", "gstin"}:
        return dict(kind="mono", width=140, minimum=110, compact_hidden=True)
    if words & {"pan", "tan"}:
        return dict(kind="mono", width=104, minimum=96)
    if words & {"ph", "phone", "mobile", "contact"}:
        return dict(kind="num", width=104, minimum=90, compact_hidden=True)
    if any(k in key for k in ("company", "trade", "firm", "business")):
        return dict(kind="strong", stretch=3, minimum=130, max_share=0.34)
    if any(k in key for k in ("name", "client", "proprietor")):
        return dict(kind="text", stretch=2, minimum=110, max_share=0.28)
    if "email" in key:
        return dict(kind="text", stretch=2, minimum=120, priority=2)
    return dict(kind="text", stretch=1, minimum=90, priority=1)


class SearchWindow(WidthAware, QWidget):

    skip_page_fade = True
    columns_changed = Signal()      # the Columns menu wrote a show-in-search flag (Settings may refresh)

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
        self._activity_refresh_timer.timeout.connect(self._on_activity_tick)
        self._activity_stale = False
        self._activity_refresh_timer.start()

        self._current_mcl_cols = []

        # _build_ui() ends with set_admin_mode(False), which used to run a full search that
        # refresh() then ran again (~0.1 s of start-up each) - search once.
        self._building = True
        self._build_ui()
        self._building = False
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T["s_6"], T["s_6"], T["s_6"], T["s_6"])
        layout.setSpacing(T["s_4"])

        # Header: sidebar toggle, title with live count, Columns, Refresh and the one primary
        self.page_header = PageHeader("All Clients")
        self.btn_toggle_sidebar = IconButton("mdi.dock-left", "Toggle Sidebar (Ctrl+B)")
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar_requested.emit)
        self.page_header.add_leading(self.btn_toggle_sidebar)

        self.btn_columns = LabelledAction(
            "Columns ▾", variant="secondary", icon="mdi.view-column-outline",
            tooltip="Which columns show here (same switches as Settings → Main screen)")
        self.btn_columns.clicked.connect(self._show_columns_menu)
        self.page_header.add_action(self.btn_columns)

        self.btn_sync = IconButton(
            "mdi.refresh", "Refresh table data (pull latest from synced database)", variant="bordered")
        self.btn_sync.clicked.connect(self._on_manual_refresh)
        self.page_header.add_action(self.btn_sync)

        self.btn_add_client = LabelledAction(
            "Add client", variant="primary", icon="mdi.plus", tooltip="Add a new client")
        self.btn_add_client.clicked.connect(self.add_client_requested.emit)
        self.page_header.add_action(self.btn_add_client)
        layout.addWidget(self.page_header)

        # Search box + filter chips (chips drop to their own row in COMPACT)
        search_row = QHBoxLayout()
        search_row.setSpacing(T["s_3"])
        self.search_box = Input(placeholder="Search clients across all columns...", icon="mdi.magnify")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(220)
        self.search_box.setMaximumWidth(560)
        self.search_box.textChanged.connect(self._on_search_input_changed)
        self.search_box.returnPressed.connect(self._activate_current_result)
        self.search_box.installEventFilter(self)
        search_row.addWidget(self.search_box, 3)

        # The old preset combo stays as the hidden source of truth; the chips drive it.
        self.service_filter = QComboBox(self)
        self.service_filter.hide()
        self.service_filter.currentIndexChanged.connect(self._on_search_input_changed)
        self.service_filter.currentIndexChanged.connect(self._sync_chips_from_combo)

        self._syncing_chips = False
        self._more_labels = {}
        self.chip_bar = ChipBar(single_select=True)
        for chip_id, label, _data in _QUICK_FILTERS:
            self.chip_bar.add_chip(label, id=chip_id, checked=(chip_id == "all"))
        self._more_menu = QMenu(self)
        self.chip_bar.add_overflow_chip(self._more_menu, text="More", title="More filters")
        self.chip_bar.currentChanged.connect(self._on_chip_changed)
        search_row.addWidget(self.chip_bar)
        search_row.addStretch(1)
        layout.addLayout(search_row)

        self.chip_row = QWidget(self)
        chip_row_lay = QHBoxLayout(self.chip_row)
        chip_row_lay.setContentsMargins(0, 0, 0, 0)
        chip_row_lay.addStretch(1)
        layout.addWidget(self.chip_row)
        self.setup_responsive_chips(
            self.chip_bar, wide_layout=search_row, compact_layout=chip_row_lay, compact_row=self.chip_row)

        # Selection bar: fixed height, contents follow the grid selection (IMPLEMENTATION 4.1)
        self.selection_bar = SelectionBar()
        self.btn_open_client = LabelledAction(
            "Open", variant="soft", icon="mdi.arrow-right", sm=True,
            tooltip="Open the selected client (Enter)")
        self.btn_open_client.clicked.connect(self._request_open_client)
        self.btn_edit_client = LabelledAction(
            "Edit", variant="secondary", icon="mdi.pencil-outline", sm=True,
            tooltip="Edit selected client profile")
        self.btn_edit_client.clicked.connect(self._request_edit_client)
        self.btn_manage_services = LabelledAction(
            "Services", variant="secondary", icon="mdi.cog-outline", sm=True,
            tooltip="Attach / Detach Services for selected client")
        self.btn_manage_services.clicked.connect(self._request_manage_services)
        self.btn_archive_client = LabelledAction(
            "Archive", variant="secondary", icon="mdi.archive-outline", sm=True,
            tooltip="Archive selected client")
        self.btn_archive_client.clicked.connect(self._request_archive_client)
        self.btn_fill_color = LabelledAction(
            "Fill", variant="secondary", icon="mdi.format-color-fill", sm=True,
            tooltip="Cell Fill Color (Background)")
        self.btn_fill_color.clicked.connect(self._open_fill_menu_from_toolbar)
        self.btn_fill_color.set_swatch(T["g_bg"])
        self.btn_text_color = LabelledAction(
            "Text", variant="secondary", icon="mdi.format-color-text", sm=True,
            tooltip="Cell Text Color (Foreground)")
        self.btn_text_color.clicked.connect(self._open_text_menu_from_toolbar)
        self.btn_text_color.set_swatch(T["g_text"])
        self.btn_clear_fmt = LabelledAction(
            "Clear", variant="secondary", icon="mdi.eraser", sm=True,
            tooltip="Clear Selected Cell Formatting")
        self.btn_clear_fmt.clicked.connect(self._clear_selected_formatting_from_toolbar)
        self.btn_undo = IconButton("mdi.undo", "Undo Cell Formatting (Ctrl+Z)", variant="bordered", sm=True)
        self.btn_undo.clicked.connect(self._undo_last_action)
        self.btn_redo = IconButton("mdi.redo", "Redo Cell Formatting (Ctrl+Y)", variant="bordered", sm=True)
        self.btn_redo.clicked.connect(self._redo_last_action)
        self._update_undo_redo_buttons()
        self.btn_delete_client = LabelledAction(
            "Delete…", variant="danger", icon="mdi.delete-outline", sm=True,
            tooltip="Permanently delete selected client record")
        self.btn_delete_client.clicked.connect(self._request_delete_client)

        self._client_buttons = (self.btn_open_client, self.btn_edit_client, self.btn_manage_services,
                                self.btn_archive_client, self.btn_delete_client)
        self._cell_buttons = (self.btn_fill_color, self.btn_text_color, self.btn_clear_fmt)

        bar = self.selection_bar
        for w in self._client_buttons[:-1]:
            bar.add_action(w)
        bar.add_divider()
        bar.add_group_label("Cell")
        for w in self._cell_buttons + (self.btn_undo, self.btn_redo):
            bar.add_action(w)
        bar.add_spacer()
        bar.add_action(self.btn_delete_client)
        layout.addWidget(self.selection_bar)

        self.results_table = DataGrid()
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.results_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results_table.viewport().setCursor(Qt.PointingHandCursor)
        self.results_table.setSortingEnabled(True)
        self.results_table.horizontalHeader().setSortIndicatorShown(True)
        self.results_table.horizontalHeader().setSectionsClickable(True)
        self.results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self._show_cell_formatting_menu)
        self.results_table.itemActivated.connect(self._on_item_activated)
        self.results_table.installEventFilter(self)
        self.results_table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.results_table.horizontalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.results_table.selectionModel().selectionChanged.connect(self._update_selection_bar)
        self.results_table.selectionModel().currentChanged.connect(self._update_selection_bar)

        from PySide6.QtGui import QKeySequence, QShortcut
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.results_table)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self._copy_selection_to_clipboard)

        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.activated.connect(self._undo_last_action)

        self.redo_shortcut = QShortcut(QKeySequence.Redo, self)
        self.redo_shortcut.activated.connect(self._redo_last_action)

        self.grid_footer = GridFooter()
        self.grid_footer.add_hint("Enter", "opens the selected result")
        self.grid_footer.add_hint(["↑", "↓"], "move", compact_hide=True)
        self.grid_footer.add_hint("Ctrl+C", "copies the selected cells", compact_hide=True)
        self.grid_frame = GridFrame(self.results_table, self.grid_footer)
        layout.addWidget(self.grid_frame, 1)

        self.set_admin_mode(False)
        self._update_selection_bar()

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
            mime = QMimeData()
            mime.setText(text)
            # Preserve the Sera-origin marker for a single UID copied from
            # the employee search grid; multi-cell copies remain ordinary
            # spreadsheet text and are not eligible for SCA arming.
            if len(rows_data) == 1 and "\t" not in text:
                mime.setData("application/x-sera-uid", b"1")
            QApplication.clipboard().setMimeData(mime)

            if len(rows_data) == 1 and "\t" not in text:
                msg = f"Copied '{text}' to clipboard"
            elif len(rows_data) == 1:
                msg = "Copied selected record to clipboard"
            else:
                msg = f"Copied {len(rows_data)} rows to clipboard"
                
            self.toast_requested.emit(msg, "success")

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
        keep = self.service_filter.currentData()
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

        idx = self.service_filter.findData(keep)
        if idx >= 0:
            self.service_filter.setCurrentIndex(idx)
        self.service_filter.blockSignals(False)
        self._rebuild_more_menu()
        self._sync_chips_from_combo()

    def _rebuild_more_menu(self):
        """The chips' More menu: the presets without a chip of their own, then one entry per service."""
        menu = self._more_menu
        menu.clear()
        self._more_actions = {}
        for data, label in _MORE_FILTERS:
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, d=data, t=label: self._pick_more_filter(d, t))
            self._more_actions[data] = (act, label)
        if self._cached_services:
            menu.addSeparator()
            for s in self._cached_services:
                act = menu.addAction(f"Service: {s['name']}")
                act.triggered.connect(lambda _=False, d=s["id"], t=s["name"]: self._pick_more_filter(d, t))
                self._more_actions[s["id"]] = (act, s["name"])

    def _pick_more_filter(self, data, label):
        idx = self.service_filter.findData(data)
        if idx >= 0:
            self.service_filter.setCurrentIndex(idx)

    def _on_chip_changed(self, chip_id):
        if self._syncing_chips:
            return
        data = {cid: d for cid, _l, d in _QUICK_FILTERS}.get(chip_id, "__keep__")
        if data == "__keep__":
            return
        idx = self.service_filter.findData(data)
        if idx >= 0 and idx != self.service_filter.currentIndex():
            self.service_filter.setCurrentIndex(idx)

    def _sync_chips_from_combo(self, *_):
        """Show the combo's current preset on the chips: a quick chip lights up, or More carries its name."""
        if getattr(self, "_syncing_chips", True) or not hasattr(self, "chip_bar"):
            return
        data = self.service_filter.currentData()
        self._syncing_chips = True
        try:
            for chip_id, _label, d in _QUICK_FILTERS:
                if d == data:
                    self.chip_bar.set_selected(chip_id)
                    self.chip_bar.chip("__overflow__").set_value_when_on(None)
                    return
            more = self.chip_bar.chip("__overflow__")
            entry = getattr(self, "_more_actions", {}).get(data)
            if more is not None and entry is not None:
                for chip in self.chip_bar.chips():
                    if chip is not more and chip.isChecked():
                        chip.blockSignals(True)
                        chip.setChecked(False)
                        chip.blockSignals(False)
                        chip.update()
                more.blockSignals(True)
                more.set_value(entry[1])
                more.blockSignals(False)
        finally:
            self._syncing_chips = False

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

    def _on_activity_tick(self):
        """The once-a-minute "5m ago" tag refresh rebuilds the whole grid on the UI thread - only
        worth it while someone can see it. Minimised / in the tray / another page: note it and
        let SeraApp run it when the window is back (2026-09-26: the app froze while minimised)."""
        if not self.isVisible() or self.window().isMinimized():
            self._activity_stale = True
            return
        self._activity_stale = False
        self._on_search_changed()

    def _on_search_changed(self, *_):
        state_at_start = self._grid_state()     # before reading: a write during the rebuild counts
        v_val = self.results_table.verticalScrollBar().value()
        h_val = self.results_table.horizontalScrollBar().value()
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
            for c_idx, col in enumerate(mcl_cols):
                self.results_table.set_column(c_idx, **_column_spec(col))
            self.results_table.set_column(len(mcl_cols), kind="text", stretch=2, minimum=140)

            if isinstance(svc_val, int):
                clients = self.db.search_clients(text, service_id=svc_val)
            elif isinstance(svc_val, str):
                clients = self.db.search_clients(text, filter_preset=svc_val)
            else:
                clients = self.db.search_clients(text)
            self.results_table.setRowCount(len(clients))

            services_map = {s["id"]: s["name"] for s in services}
            service_dots = {s["id"]: _SERVICE_DOTS[i % len(_SERVICE_DOTS)] for i, s in enumerate(services)}

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
                svc_item = SmartTableWidgetItem(svc_val_str)
                svc_item.setData(Qt.UserRole, client_id)
                svc_item.setData(Qt.UserRole + 1, "services")
                svc_item.setData(ROLE_SERVICES, [
                    (services_map[s_id], service_dots[s_id]) for s_id in client_svc_ids if s_id in services_map])

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

            # What the grid now shows, for refresh() to reuse (unfiltered only)
            self._drawn_grid = (state_at_start, time.monotonic()) if not text else None

            self._current_mcl_cols = mcl_cols
            self.results_table.fit_columns()
            self._update_counts(len(clients), text, svc_val)
            self._update_selection_bar()

            # Retain scroll positions precisely
            if v_val > 0:
                self.results_table.verticalScrollBar().setValue(v_val)
            if h_val > 0:
                self.results_table.horizontalScrollBar().setValue(h_val)
        finally:
            self.results_table.setSortingEnabled(True)
            self.results_table.horizontalHeader().setSortIndicatorShown(True)
            self.results_table.horizontalHeader().setSectionsClickable(True)
            self.results_table.setUpdatesEnabled(True)

    def _update_counts(self, shown, text, svc_val):
        word = "client" if shown == 1 else "clients"
        self.page_header.set_count(f"{shown} {word}")
        filtered = bool(text) or svc_val is not None
        self.grid_footer.set_count(f"{shown} {word} shown" if filtered else f"{shown} {word}")

    # ---- Columns menu: a second editor for the per-column "show on this screen" flag
    def _flag_field(self):
        return "admin_show_in_search" if getattr(self, "is_admin_mode", False) else "show_in_search"

    def _write_column_flags(self, state):
        ids = [cid for cid, on in state.items() if on]
        if getattr(self, "is_admin_mode", False):
            self.db.bulk_update_mcl_admin_visibility(ids)
        else:
            self.db.bulk_update_mcl_visibility(ids)

    def _show_columns_menu(self):
        field = self._flag_field()
        cols = self.db.get_mcl_columns()
        state = {c["id"]: bool(c.get(field, 1)) for c in cols}
        switches = {}

        def lock_last():
            on_ids = [cid for cid, on in state.items() if on]
            for cid, sw in switches.items():
                sw.set_disabled_reason(_LAST_COLUMN_REASON if on_ids == [cid] else None)

        def toggled(cid, on):
            if state.get(cid) == on:
                return
            state[cid] = on
            self._write_column_flags(state)
            lock_last()
            self._on_search_changed()
            self.columns_changed.emit()

        card = Card("Columns on this screen")
        rows = QWidget(card)
        rows_lay = QVBoxLayout(rows)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(0)
        for i, col in enumerate(cols):
            sw = Switch(checked=state[col["id"]], tooltip=f"Show {col['label']} on this screen")
            sw.toggled.connect(lambda on, cid=col["id"]: toggled(cid, on))
            switches[col["id"]] = sw
            row = SettingRow(col["label"], control=sw, width="auto")
            row.set_first(i == 0)
            rows_lay.addWidget(row)
        lock_last()
        scroll = QScrollArea(card)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(rows)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        rows.setAutoFillBackground(False)
        scroll.setMinimumWidth(260)
        scroll.setFixedHeight(min(rows.sizeHint().height() + 2, max(160, int(self.height() * 0.6))))
        card.add_widget(scroll)
        note = QLabel("Admin mode: writes the admin-view flag of each column." if self.is_admin_mode
                      else "Writes the same per-column flag as Settings → Main screen.", card)
        note.setWordWrap(True)
        note.setProperty("class", "HintText")
        card.add_widget(note)

        menu = QMenu(self)
        action = QWidgetAction(menu)
        action.setDefaultWidget(card)
        menu.addAction(action)
        self._columns_menu = menu
        menu.exec(self.btn_columns.mapToGlobal(QPoint(0, self.btn_columns.height() + T["s_1"])))

    def _on_item_activated(self, item: QTableWidgetItem):
        client_id = item.data(Qt.UserRole)
        if client_id is not None:
            self.client_selected.emit(client_id)

    def _update_undo_redo_buttons(self):
        if hasattr(self, "btn_undo"):
            self.btn_undo.set_disabled_reason(None if self._undo_stack else _NO_UNDO_REASON)
        if hasattr(self, "btn_redo"):
            self.btn_redo.set_disabled_reason(None if self._redo_stack else _NO_REDO_REASON)

    # ---- Selection bar: what the current grid selection allows (IMPLEMENTATION 4.1)
    def _selected_rows(self):
        return sorted({ix.row() for ix in self.results_table.selectedIndexes()})

    def _who_text(self, row):
        """The client's name for the bar: the first company / name column shown, else the client id."""
        t = self.results_table
        for c_idx, col in enumerate(getattr(self, "_current_mcl_cols", [])):
            if _column_spec(col)["kind"] in ("strong", "text") and c_idx < t.columnCount():
                item = t.item(row, c_idx)
                if item is not None and item.text().strip():
                    return item.text().strip()
        item = t.item(row, 0)
        return f"Client {item.data(Qt.UserRole)}" if item is not None else ""

    def _update_selection_bar(self, *_):
        if not hasattr(self, "selection_bar"):
            return
        rows = self._selected_rows()
        if not rows:
            # Nothing selected: only the hint shows; the actions are off too (Delete was always clickable).
            for btn in self._client_buttons:
                btn.set_disabled_reason(_NO_ROW_REASON)
            for btn in self._cell_buttons:
                btn.set_disabled_reason(_NO_CELL_REASON)
            self.selection_bar.set_who("")
            return
        if len(rows) == 1:
            reason = None
            self.selection_bar.set_who(self._who_text(rows[0]))
        else:
            # Every client action works on one client today: never act on just one of several rows.
            reason = f"Works on one client; {len(rows)} rows are selected"
            self.selection_bar.set_who(f"{len(rows)} clients selected")
        for btn in self._client_buttons:
            btn.set_disabled_reason(reason)
        for btn in self._cell_buttons:
            btn.set_disabled_reason(None)
        item = self.results_table.currentItem()
        self.btn_fill_color.set_swatch(_brush_colour(item and item.background(), T["g_bg"]))
        self.btn_text_color.set_swatch(_brush_colour(item and item.foreground(), T["g_text"]))

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

        # The selection bar's client actions, so each has a keyboard path (menu key) too
        client_acts = [b for b in self._client_buttons if b.isVisibleTo(self) and b.isEnabled()]
        for btn in client_acts:
            act = menu.addAction(btn.toolTip() or btn.text())
            act.setData(("client", btn))
        if client_acts:
            menu.addSeparator()

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
        if action_type == "client":
            val.click()
            return

        if val == "custom":
            color = QColorDialog.getColor(parent=self)
            if not color.isValid():
                return
            val = color.name()
            
        self._execute_formatting_operation(action_type, val)

    def _selected_client_id(self):
        rows = self._selected_rows()
        if len(rows) == 1:                  # the bar names this row: act on it, not on a stray current cell
            item = self.results_table.item(rows[0], 0)
            if item is not None:
                return item.data(Qt.UserRole)
        item = self.results_table.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _request_open_client(self):
        client_id = self._selected_client_id()
        if client_id is not None:
            self.client_selected.emit(client_id)

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
        if hasattr(self, "results_table") and not getattr(self, "_building", False):
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

    # Switching back to the search tab calls refresh(), which rebuilt every cell (~0.2 s for a
    # few hundred clients, measured 2026-09-22) even when nothing had changed. If no row has been
    # written since the grid was last drawn unfiltered, it is kept - for at most this long, so a
    # change made by another program and the "5m ago" activity tags still catch up.
    REUSE_GRID_FOR_S = 60.0

    def _grid_state(self):
        gen = getattr(self.db, "data_generation", None)
        return (gen() if callable(gen) else None, self.service_filter.currentData(),
                bool(getattr(self, "is_admin_mode", False)))

    def _grid_is_current(self) -> bool:
        drawn = getattr(self, "_drawn_grid", None)
        if drawn is None or drawn[0][0] is None or self.search_box.text().strip():
            return False
        state, when = drawn
        return (state == self._grid_state() and self.results_table.rowCount() > 0
                and time.monotonic() - when < self.REUSE_GRID_FOR_S)

    def refresh(self):
        if self._grid_is_current():
            self._search_timer.stop()
            self.search_box.setFocus()
            return
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
