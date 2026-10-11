"""
audit_log_dialog.py
--------------------
Redesigned wide SSAL (Sera-Sync Audit Log) window for Project Sera.
Displays the audit log, which now replicates from every workstation under Sera
Sync v3 (the legacy per-workstation "peer_logs/" viewer was removed in P4-1 --
see docs/sera-sync-v3-blueprint.md §5 -- since audit_log itself carries every
PC's entries now).
Includes date presets, custom date range, full-text live search, action filter,
local timezone timestamp conversion, service name resolution, token-safe CSV export,
and rich row drill-down details.

Built on the shared kit (ui/components/kit.py): the Action / Staff / Date chips open menus that
drive the same combo, input and date widgets the filter code always read (action_combo, actor_input,
cmb_date_preset, dt_from, dt_to), which stay as the hidden source of truth.
"""

import csv
import datetime
from PySide6.QtCore import Qt, Signal, QDate, QTimer
from PySide6.QtGui import QActionGroup, QGuiApplication
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    QAbstractItemView,
)

from ui.components.kit import (
    Card,
    ChipBar,
    DarkListTable,
    DataGrid,
    DialogFrame,
    FieldLabel,
    GridFrame,
    IconButton,
    Input,
    LabelledAction,
    Pill,
    Select,
    TextArea,
    ROLE_PILL,
)
from ui.utils.responsive import WidthAware, WidthMode
from ui.utils.tokens import TOKENS

_ACTIONS = [
    "All Actions", "view", "manual_assist", "autofill", "manual_copy",
    "create", "update", "archive", "unarchive", "delete",
    "csv_import", "backup", "restore", "csv_export",
    "filing_submitted", "sync_pushed", "sync_received", "audit_log_viewed",
]
_DATE_PRESETS = ["All Time", "Today", "Yesterday", "Last 7 Days", "Last 30 Days", "Custom Date Range"]
_CUSTOM_RANGE = "Custom Date Range"

_WS_WIDTH = 200
_CARD_WIDTH = 280
_EXPORT_TEXT_MIN_WIDTH = 1180


def _format_to_local_time(iso_str: str) -> tuple[str, str]:
    """Converts a UTC ISO timestamp to formatted local time (IST) and returns (local_display, raw_utc)."""
    if not iso_str:
        return "—", ""
    clean = str(iso_str).strip()
    if not clean:
        return "—", ""
    try:
        raw_clean = clean
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        local_dt = dt.astimezone()
        local_str = local_dt.strftime("%d-%b-%Y %I:%M:%S %p")
        return local_str, raw_clean
    except Exception:
        return clean[:19].replace("T", " "), clean


def _get_action_badge(action: str) -> tuple[str, str, str]:
    """Returns (display_label, pill_text, pill_tone) for an action. Groups: creates green, views
    grey, assists and edits amber, audit-log views and deletes red, sync blue; unknown types grey."""
    act = (action or "").lower().strip()
    if act in ("create", "add"):
        return "➕ Create", "Create", "green"
    elif act in ("unarchive", "restore"):
        return "♻️ Restore", "Restore", "green"
    elif act == "view":
        return "👁️ View", "View", "neutral"
    elif act == "audit_log_viewed":
        return "🛡️ Audit View", "Audit View", "red"
    elif act in ("autofill", "sca_autofill", "autofill_extension", "autofill_playwright"):
        return "🪄 Autofill", "Autofill", "amber"
    elif act in ("manual_assist", "sca_widget"):
        return "⚡ Manual Assist", "Manual Assist", "amber"
    elif act == "manual_copy":
        return "📋 Copy", "Copy", "amber"
    elif act in ("update", "update_notes", "update_settings"):
        return "✏️ Update", "Update", "amber"
    elif act in ("archive", "delete"):
        word = "Delete" if act == "delete" else "Archive"
        return ("🗑️ " if act == "delete" else "📦 ") + word, word, "red"
    elif act in ("csv_export", "csv_import", "backup"):
        word = act.replace("_", " ").title()
        return "💾 " + word, word, "neutral"
    elif act in ("sync_pushed", "sync_received"):
        return "🔄 Sync", "Sync", "blue"
    elif "filing" in act:
        return "📑 Filing", "Filing", "green"
    else:
        word = act.replace("_", " ").title()[:15]
        return word, word, "neutral"


class AuditLogDialog(WidthAware, QDialog):
    toast_requested = Signal(str, int)

    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Project Sera — Sera-Sync Audit Log (SSAL)")
        self.resize(1180, 720)
        self.setMinimumSize(1020, 580)

        # "local" is the only workstation entry since P4-1: audit_log itself now replicates
        # under Sera Sync v3, so this PC's audit_log already carries every PC's entries
        # (the legacy peer_logs/ per-workstation viewer was removed; see database.py's
        # _migrate_legacy_peer_logs and docs/sera-sync-v3-blueprint.md §5).
        self.selected_host = "local"
        self._raw_logs_cache = []

        # Live search debounce timer
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_client_side_filter)

        self._build_ui()
        self._load_workstations()
        self._load_logs()

        # Self-referential audit log entry
        try:
            self.db.log_action(self.actor, "audit_log_viewed", detail="Opened SSAL Audit Log Window")
        except Exception:
            pass

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = DialogFrame(
            "Audit log",
            "Sera-Sync Audit Log (SSAL): every view, assist, copy and change, from this PC and the other Sera PCs",
            self, icon="mdi.shield-search", full=True)
        outer.addWidget(self.frame)
        self.frame.closeRequested.connect(self.accept)

        self.host_badge = Pill("Host aggregator active", "emerald", dot=True)
        self.host_badge.setToolTip("This PC collects the other workstations' entries")
        self.frame.add_header_widget(self.host_badge)
        self.frame.add_close_button()

        body = QWidget()
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(TOKENS["s_3"])
        self.frame.add_widget(body, 1)

        # ---------------- Left: workstations with their entry counts ----------------
        self._ws_card = Card("Workstations")
        self._ws_card.setFixedWidth(_WS_WIDTH)
        self.workstation_list = DarkListTable(0, 2)
        self.workstation_list.horizontalHeader().hide()
        self.workstation_list.set_column(0, minimum=90)
        self.workstation_list.set_column(1, kind="num", width=44, stretch=0)
        self.workstation_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.workstation_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.workstation_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.workstation_list.currentCellChanged.connect(self._on_workstation_changed)
        self._ws_card.add_widget(self.workstation_list, 1)
        body_row.addWidget(self._ws_card)

        # ---------------- Middle: filters, count, grid ----------------
        centre = QWidget()
        centre_col = QVBoxLayout(centre)
        centre_col.setContentsMargins(0, 0, 0, 0)
        centre_col.setSpacing(TOKENS["s_2"])
        body_row.addWidget(centre, 1)

        # The state behind the chips: the same widgets the filter code always read.
        self.action_combo = Select(self, _ACTIONS)
        self.cmb_date_preset = Select(self, _DATE_PRESETS)
        self.cmb_date_preset.setCurrentText("Last 30 Days")
        self.actor_input = Input(self, placeholder="All staff…", icon="mdi.account-outline")
        self.actor_input.setMinimumWidth(200)
        self.dt_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.dt_to = QDateEdit(QDate.currentDate())
        for edit in (self.dt_from, self.dt_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        self.action_combo.hide()
        self.cmb_date_preset.hide()

        filter_row = QHBoxLayout()
        filter_row.setSpacing(TOKENS["s_2"])
        self.txt_search = Input(placeholder="Search client, PAN, actor, action, service, details…",
                                icon="mdi.magnify")
        self.txt_search.setMinimumWidth(160)
        self.txt_search.setAccessibleName("Search audit log")
        self.txt_search.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self.txt_search, 1)

        self.chip_bar = ChipBar()
        self.chip_action = self.chip_bar.add_chip(
            "Action", id="action", menu=self._build_action_menu(),
            tooltip="All actions, or one of the 17 action types")
        self.chip_staff = self.chip_bar.add_chip(
            "Staff", id="staff", menu=self._build_staff_menu(), tooltip="All staff, or type a name")
        self.chip_date = self.chip_bar.add_chip(
            "Date", id="date", menu=self._build_date_menu(),
            tooltip="All time, Today, Yesterday, Last 7 / 30 days, or a custom range (From / To)")
        filter_row.addWidget(self.chip_bar)

        self.btn_refresh = IconButton("mdi.refresh", "Refresh", variant="bordered")
        self.btn_refresh.clicked.connect(self._load_logs)
        filter_row.addWidget(self.btn_refresh)

        self.btn_export = LabelledAction("Export CSV...", variant="soft", icon="mdi.file-delimited-outline",
                                         tooltip="Export the entries matching the current filters")
        self.btn_export.clicked.connect(self._on_export_log_csv)
        filter_row.addWidget(self.btn_export)
        centre_col.addLayout(filter_row)

        info_row = QHBoxLayout()
        info_row.setSpacing(TOKENS["s_2"])
        self.lbl_record_count = QLabel("0 log entries")
        self.lbl_record_count.setProperty("class", "HintText")
        info_row.addWidget(self.lbl_record_count)
        self.lbl_range = QLabel("")
        self.lbl_range.setProperty("class", "HintText")
        info_row.addWidget(self.lbl_range)
        info_row.addStretch(1)

        self._custom_row = QWidget()
        custom = QHBoxLayout(self._custom_row)
        custom.setContentsMargins(0, 0, 0, 0)
        custom.setSpacing(TOKENS["s_2"])
        custom.addWidget(FieldLabel("From", for_widget=self.dt_from))
        custom.addWidget(self.dt_from)
        custom.addWidget(FieldLabel("To", for_widget=self.dt_to))
        custom.addWidget(self.dt_to)
        info_row.addWidget(self._custom_row)
        centre_col.addLayout(info_row)

        # Wide Table (7 Columns)
        self.table = DataGrid(0, 7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Date & Time (Local)", "Actor", "Action", "Client (Name / Token)", "Service", "Detail"
        ])
        self.table.set_column(0, kind="num", width=52)
        self.table.set_column(1, kind="num", width=200, minimum=170)
        self.table.set_column(2, width=92, minimum=72)
        self.table.set_column(3, width=112, minimum=100)
        self.table.set_column(4, kind="strong", minimum=140)
        self.table.set_column(5, width=100, minimum=80, priority=2, compact_hidden=True)
        self.table.set_column(6, minimum=150, priority=1, compact_hidden=True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        centre_col.addWidget(GridFrame(self.table), 1)

        # ---------------- Right: details of the picked entry ----------------
        self._entry_card = Card("Entry")
        self._entry_card.setFixedWidth(_CARD_WIDTH)
        self.entry_pill = Pill("—", "neutral")
        self.entry_pill.hide()
        self._entry_card.add_widget(self.entry_pill)

        self.inspector_box = TextArea(rows=9, placeholder="Pick a row to see the full entry: time, actor, client, service and detail.")
        self.inspector_box.setReadOnly(True)
        self._entry_card.add_widget(self.inspector_box)

        self.btn_copy_insp = LabelledAction("Copy details", variant="secondary", icon="mdi.content-copy", sm=True,
                                            tooltip="Copy the full entry to the clipboard")
        self.btn_copy_insp.clicked.connect(self._on_copy_inspector)
        self.btn_filter_client = LabelledAction("Show only this client", variant="secondary",
                                                icon="mdi.account-search", sm=True,
                                                tooltip="Search for this client's entries only")
        self.btn_filter_client.clicked.connect(self._on_filter_by_selected_client)
        for btn in (self.btn_copy_insp, self.btn_filter_client):
            self._entry_card.add_widget(btn)
        body_row.addWidget(self._entry_card, 0, Qt.AlignTop)

        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        self.cmb_date_preset.currentIndexChanged.connect(self._on_date_preset_changed)
        self.actor_input.textChanged.connect(self._on_staff_changed)
        self.dt_from.dateChanged.connect(self._on_custom_date_changed)
        self.dt_to.dateChanged.connect(self._on_custom_date_changed)
        self._sync_filter_chips()

    # ---------------- chip menus ----------------
    def _build_action_menu(self):
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._action_menu_items = []
        for i, name in enumerate(_ACTIONS):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setActionGroup(group)
            act.triggered.connect(lambda _checked=False, idx=i: self.action_combo.setCurrentIndex(idx))
            self._action_menu_items.append(act)
        return menu

    def _build_staff_menu(self):
        menu = QMenu(self)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(self.actor_input)
        menu.addAction(holder)
        menu.addSeparator()
        menu.addAction("All staff").triggered.connect(lambda _checked=False: self.actor_input.setText(""))
        return menu

    def _build_date_menu(self):
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._date_menu_items = []
        for i, name in enumerate(_DATE_PRESETS):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setActionGroup(group)
            act.triggered.connect(lambda _checked=False, idx=i: self.cmb_date_preset.setCurrentIndex(idx))
            self._date_menu_items.append(act)
        return menu

    def _sync_filter_chips(self):
        action_idx = self.action_combo.currentIndex()
        self.chip_action.set_value(self.action_combo.currentText() if action_idx > 0 else None)
        for i, act in enumerate(self._action_menu_items):
            act.setChecked(i == action_idx)

        staff = self.actor_input.text().strip()
        self.chip_staff.set_value(staff or None)

        preset_idx = self.cmb_date_preset.currentIndex()
        preset = self.cmb_date_preset.currentText()
        self.chip_date.setText(preset)
        self.chip_date.setChecked(preset != "All Time")
        for i, act in enumerate(self._date_menu_items):
            act.setChecked(i == preset_idx)
        self._custom_row.setVisible(preset == _CUSTOM_RANGE)

    def on_width_mode_changed(self, mode):
        compact = mode is WidthMode.COMPACT
        self._ws_card.setVisible(not compact)
        self._fit_export_button()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_export_button()

    def _fit_export_button(self):
        tight = self.width_mode is WidthMode.COMPACT or self.width() < _EXPORT_TEXT_MIN_WIDTH
        self.btn_export.set_icon_only(tight)

    # ---------------- filter handlers ----------------
    def _on_search_changed(self):
        self._search_timer.start()

    def _on_action_changed(self, _index=0):
        self._sync_filter_chips()
        self._load_logs()

    def _on_staff_changed(self, _text=""):
        self._sync_filter_chips()
        self._on_search_changed()

    def _on_date_preset_changed(self):
        preset = self.cmb_date_preset.currentText()
        today = QDate.currentDate()
        self.dt_from.blockSignals(True)
        self.dt_to.blockSignals(True)

        if preset == "Today":
            self.dt_from.setDate(today)
            self.dt_to.setDate(today)
        elif preset == "Yesterday":
            self.dt_from.setDate(today.addDays(-1))
            self.dt_to.setDate(today.addDays(-1))
        elif preset == "Last 7 Days":
            self.dt_from.setDate(today.addDays(-7))
            self.dt_to.setDate(today)
        elif preset == "Last 30 Days":
            self.dt_from.setDate(today.addDays(-30))
            self.dt_to.setDate(today)
        elif preset == "All Time":
            self.dt_from.setDate(QDate(2020, 1, 1))
            self.dt_to.setDate(today)

        self.dt_from.blockSignals(False)
        self.dt_to.blockSignals(False)
        self._sync_filter_chips()
        self._load_logs()

    def _on_custom_date_changed(self):
        if self.cmb_date_preset.currentText() != _CUSTOM_RANGE:
            self.cmb_date_preset.blockSignals(True)
            self.cmb_date_preset.setCurrentText(_CUSTOM_RANGE)
            self.cmb_date_preset.blockSignals(False)
        self._sync_filter_chips()
        self._load_logs()

    def _load_workstations(self):
        # Since P4-1, audit_log replicates under Sera Sync v3, so the local audit log
        # already carries every workstation's entries -- there is no separate per-peer
        # log to switch to any more (see the note in __init__).
        self.workstation_list.blockSignals(True)
        self.workstation_list.setRowCount(0)
        self.workstation_list.setRowCount(1)
        item_local = QTableWidgetItem("All (this office)")
        item_local.setToolTip("All Workstations (this office)")
        item_local.setData(Qt.UserRole, "local")
        self.workstation_list.setItem(0, 0, item_local)
        self.workstation_list.setItem(0, 1, QTableWidgetItem("0"))
        self.workstation_list.setCurrentCell(0, 0)
        self.workstation_list.blockSignals(False)

    def _update_workstation_counts(self):
        item = self.workstation_list.item(0, 1)
        if item is not None:
            item.setText(str(len(self._raw_logs_cache)))

    def _on_workstation_changed(self, row, _column=0, _prev_row=-1, _prev_column=-1):
        item = self.workstation_list.item(row, 0) if row >= 0 else None
        if item is None:
            return
        self.selected_host = item.data(Qt.UserRole)
        self._load_logs()

    def _date_window(self):
        preset = self.cmb_date_preset.currentText()
        if preset == "All Time":
            return None, None
        return (self.dt_from.date().toString("yyyy-MM-dd") + "T00:00:00",
                self.dt_to.date().addDays(1).toString("yyyy-MM-dd") + "T00:00:00")

    def _load_logs(self):
        actor_filter = self.actor_input.text().strip()
        action_filter = self.action_combo.currentText()
        from_date, to_date = self._date_window()

        logs = self.db.get_audit_logs(
            actor=actor_filter, action=action_filter,
            from_date=from_date, to_date=to_date, resolve_names=True, limit=1000
        )

        self._raw_logs_cache = logs
        if self.cmb_date_preset.currentText() == "All Time":
            self.lbl_range.setText("· all time")
        else:
            self.lbl_range.setText(f"· {self.dt_from.date().toString('dd MMM')} – "
                                   f"{self.dt_to.date().toString('dd MMM yyyy')}")
        self._update_workstation_counts()
        self._apply_client_side_filter()

    def _apply_client_side_filter(self):
        query = self.txt_search.text().strip().lower()
        actor_filter = self.actor_input.text().strip().lower()

        filtered = []
        for l in self._raw_logs_cache:
            if actor_filter and actor_filter not in str(l.get("actor", "")).lower():
                continue
            if query:
                match_fields = [
                    str(l.get("id", "")),
                    str(l.get("client_name", "")),
                    str(l.get("client_token", "")),
                    str(l.get("actor", "")),
                    str(l.get("action", "")),
                    str(l.get("service_name", "")),
                    str(l.get("detail", ""))
                ]
                if not any(query in f.lower() for f in match_fields):
                    continue
            filtered.append(l)

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(filtered))
            self.lbl_record_count.setText(f"{len(filtered)} log entries")

            for row, log in enumerate(filtered):
                # 0. ID
                self.table.setItem(row, 0, QTableWidgetItem(str(log["id"])))

                # 1. Date & Time (Local IST Conversion)
                local_ts, raw_utc = _format_to_local_time(log["ts"])
                ts_item = QTableWidgetItem(local_ts)
                ts_item.setToolTip(f"Local Time: {local_ts}\nUTC: {raw_utc}")
                self.table.setItem(row, 1, ts_item)

                # 2. Actor
                self.table.setItem(row, 2, QTableWidgetItem(str(log["actor"] or "System")))

                # 3. Action pill (the item text stays the readable label for search and copy)
                badge_label, pill_text, pill_tone = _get_action_badge(log["action"])
                action_item = QTableWidgetItem(badge_label)
                action_item.setData(ROLE_PILL, (pill_text, pill_tone))
                action_item.setToolTip(f"Action: {log['action']}")
                self.table.setItem(row, 3, action_item)

                # 4. Client (Name / Token)
                client_display = log.get("client_name") or (f"CLI-{log['client_id']:05d}" if log.get("client_id") else "—")
                item_client = QTableWidgetItem(client_display)
                item_client.setData(Qt.UserRole, log)
                item_client.setToolTip(f"Client: {client_display} (ID: {log.get('client_id', 'N/A')})")
                self.table.setItem(row, 4, item_client)

                # 5. Service Name
                srv_str = log.get("service_name") or (str(log["service_id"]) if log.get("service_id") else "—")
                srv_item = QTableWidgetItem(srv_str)
                srv_item.setToolTip(f"Service: {srv_str}")
                self.table.setItem(row, 5, srv_item)

                # 6. Detail
                det_str = log.get("detail") or ("Viewed client profile" if log.get("action") == "view" else "—")
                det_item = QTableWidgetItem(det_str)
                det_item.setToolTip(det_str)
                self.table.setItem(row, 6, det_item)
        finally:
            self.table.setUpdatesEnabled(True)

    def _on_row_selected(self):
        row = self.table.currentRow()
        if row < 0 or not self.table.selectedItems():
            self.inspector_box.clear()
            self._entry_card.set_title("Entry")
            self.entry_pill.hide()
            return
        item_client = self.table.item(row, 4)
        if not item_client:
            return
        log = item_client.data(Qt.UserRole)
        if not log:
            return

        local_ts, raw_utc = _format_to_local_time(log.get("ts", ""))
        srv_name = log.get("service_name") or (f"ID: {log['service_id']}" if log.get("service_id") else "None")
        det_val = log.get("detail") or ("Viewed client profile" if log.get("action") == "view" else "None")

        detail_text = (
            f"📌 Log ID: {log.get('id')}  |  🕒 Local Time: {local_ts}  |  🌍 UTC: {raw_utc}\n"
            f"💻 Workstation: {self.selected_host}  |  👤 Actor: {log.get('actor', 'System')}  |  ⚡ Action: {log.get('action')}\n"
            f"🏢 Client: {log.get('client_name', '—')} (Token: {log.get('client_token', 'N/A')} | ID: {log.get('client_id', 'N/A')})  |  🌐 Service: {srv_name}\n"
            f"📝 Detail Payload: {det_val}"
        )
        self.inspector_box.setPlainText(detail_text)
        self._entry_card.set_title(f"Entry #{log.get('id')}")
        _label, pill_text, pill_tone = _get_action_badge(log.get("action"))
        self.entry_pill.set_text(pill_text)
        self.entry_pill.set_tone("emerald" if pill_tone == "green" else pill_tone)
        self.entry_pill.show()

    def _on_copy_inspector(self):
        text = self.inspector_box.toPlainText().strip()
        if text:
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(text)
            self.toast_requested.emit("Audit details copied to clipboard!", 2500)

    def _on_filter_by_selected_client(self):
        row = self.table.currentRow()
        if row < 0: return
        item_client = self.table.item(row, 4)
        if item_client:
            log = item_client.data(Qt.UserRole)
            if log and log.get("client_name") and log["client_name"] != "—":
                self.txt_search.setText(log["client_name"])
            elif log and log.get("client_token"):
                self.txt_search.setText(log["client_token"])

    def _on_export_log_csv(self):
        default_filename = f"audit_log_{self.selected_host}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export SSAL Audit Log ({self.selected_host}) to CSV", default_filename, "CSV Files (*.csv)"
        )
        if path:
            try:
                actor_filter = self.actor_input.text().strip()
                action_filter = self.action_combo.currentText()
                from_date, to_date = self._date_window()

                logs = self.db.get_audit_logs(
                    actor=actor_filter, action=action_filter,
                    from_date=from_date, to_date=to_date, resolve_names=True, limit=10000
                )

                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "ID", "Local Time (IST)", "Timestamp (UTC)", "Workstation", "Actor", "Action",
                        "Client Token", "Client Name", "Service", "Detail"
                    ])
                    for l in logs:
                        local_ts, raw_utc = _format_to_local_time(l.get("ts", ""))
                        writer.writerow([
                            l.get("id"),
                            local_ts,
                            raw_utc,
                            self.selected_host,
                            l.get("actor"),
                            l.get("action"),
                            l.get("client_token", ""),
                            l.get("client_name", ""),
                            l.get("service_name") or l.get("service_id", ""),
                            l.get("detail", "")
                        ])

                try:
                    self.db.log_action(self.actor, "csv_export", detail=f"Exported SSAL audit log ({self.selected_host}) to {path}")
                except Exception:
                    pass

                self.toast_requested.emit(f"SSAL Audit log ({self.selected_host}) exported successfully to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Could not export audit log CSV:\n{e!s}")
