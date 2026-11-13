"""ui/dialogs/drs_config_dialog.py - DRS (Deadline Reminder System) configuration.

Per return form: which columns the DRS workbook tab shows, in what order, and whether the tab
lists only clients with Tracker Dump captures (LTT mode); for all forms: the client roster scope.
Built on the kit (DialogFrame, cards, DarkListTable, Select, Switch); changes are staged until
"Save & refresh". Below 900 px the two cards stack and the body scrolls.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QDialog,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.drs.config import (
    SCOPE_ALL_MCL,
    SCOPE_SERVICE_ONLY,
    default_drs_columns_for_form,
    default_drs_config,
    load_drs_config,
    save_drs_config,
)
from core.drs.engine import detect_forms_and_services
from core.drs.export import export_drs, open_drs_workbook
from ui.components.kit import (
    MessageBox,
    ROLE_PILL, ROLE_TAG, Caption, Card, DarkListTable, DialogFrame, IconButton, LabelledAction, Select,
    SettingRow, Switch, sentence_label,
)
from ui.utils.tokens import TOKENS

# below this dialog width the columns card and the forms card stack
_STACK_BELOW = 900

# column type -> (tag text, tag tone)
_SOURCE_TAGS = {
    "mcl": ("MCL", "neutral"),
    "mcl_secret": ("MCL secret", "red"),
    "fixed": ("DRS core", "emerald"),
    "tracker": ("Tracker", "blue"),
}


class DRSConfigDialog(QDialog):
    """Deadline Reminder System configuration and column arrangement."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.app_dir = db.app_dir
        self.setWindowTitle("Deadline Reminder System (DRS)")
        self.setModal(True)
        self.setMinimumSize(560, 480)
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail:
            self.resize(min(1000, max(560, avail.width() - 40)), min(720, max(480, avail.height() - 70)))
        else:
            self.resize(960, 600)

        self._detected_forms: list[dict[str, Any]] = []
        self._config: dict[str, Any] = {}
        self._current_form: str = ""

        self._build_ui()
        self._load_data()

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Deadline reminders (DRS)",
                            "Columns, order and data feed of each return form's tab in the DRS workbook",
                            self, icon="mdi.calendar-clock", scroll=True)
        outer.addWidget(frame)
        frame.closeRequested.connect(self.accept)

        # settings: the form being edited, its LTT switch, and the roster scope (all forms)
        settings = Card()
        self.form_combo = Select(settings)
        self.form_combo.setMinimumWidth(160)
        self.form_combo.currentIndexChanged.connect(self._on_form_changed)
        settings.add_row(SettingRow("Return form", "The workbook tab to arrange", control=self.form_combo))

        self.chk_tracker_only = Switch(tooltip="Tracker dump only (LTT mode)")
        self.chk_tracker_only.toggled.connect(self._on_tracker_only_toggled)
        settings.add_row(SettingRow(
            "Tracker dump only (LTT mode)",
            "List only clients with captures in Tracker Dump for this form",
            control=self.chk_tracker_only))

        self.combo_scope = Select(settings)
        self.combo_scope.addItem("Whole MCL roster (all clients)", SCOPE_ALL_MCL)
        self.combo_scope.addItem("Service-enrolled only", SCOPE_SERVICE_ONLY)
        self.combo_scope.currentIndexChanged.connect(self._on_scope_changed)
        settings.add_row(SettingRow("Client roster", "Applies to every form", control=self.combo_scope))
        frame.add_widget(settings)

        # body: columns (left) and detected forms (right); stacks when narrow
        body_host = QWidget()
        body_host.setProperty("class", "FormHost")
        self._body = QBoxLayout(QBoxLayout.LeftToRight, body_host)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(TOKENS["s_3"])

        cols_card = Card("Columns")
        self.lbl_cols_header = cols_card.title_label
        self.btn_up = IconButton("mdi.arrow-up", "Move the selected column up", variant="bordered", sm=True)
        self.btn_down = IconButton("mdi.arrow-down", "Move the selected column down", variant="bordered", sm=True)
        self.btn_up.clicked.connect(lambda: self._move_column(-1))
        self.btn_down.clicked.connect(lambda: self._move_column(1))
        cols_card.add_action(self.btn_up)
        cols_card.add_action(self.btn_down)
        self.col_list = DarkListTable(0, 2)
        self.col_list.setHorizontalHeaderLabels(["Column", "Source"])
        self.col_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.col_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.col_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.col_list.set_column(0, kind="strong", minimum=160)
        self.col_list.set_column(1, width=120)
        self.col_list.itemChanged.connect(self._on_item_checked)
        self.col_list.currentCellChanged.connect(lambda row, *_: self._update_move_buttons(row))
        cols_card.add_widget(self.col_list, 1)
        self.btn_reset_form = LabelledAction("Reset this form", variant="ghost", icon="mdi.restore", sm=True,
                                             tooltip="Asks first: this form's columns go back to the defaults")
        self.btn_reset_form.clicked.connect(self._reset_current_form_defaults)
        self.btn_reset_all = LabelledAction("Reset all forms", variant="ghost", icon="mdi.restore", sm=True,
                                            tooltip="Asks first: every form, column and the roster scope go "
                                                    "back to the defaults")
        self.btn_reset_all.clicked.connect(self._reset_all_defaults)
        reset_row = QWidget()
        reset_row.setProperty("class", "FormHost")
        rr = QBoxLayout(QBoxLayout.LeftToRight, reset_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(TOKENS["s_2"])
        rr.addWidget(self.btn_reset_form)
        rr.addWidget(self.btn_reset_all)
        rr.addStretch(1)
        cols_card.add_widget(reset_row)
        cols_card.setMinimumHeight(300)
        self._body.addWidget(cols_card, 1)

        forms_card = Card("Detected forms")
        self.forms_table = DarkListTable(0, 4)
        self.forms_table.setHorizontalHeaderLabels(["Form", "Portal", "Clients", "Feed"])
        self.forms_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.forms_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.forms_table.set_column(0, kind="strong", minimum=80)
        self.forms_table.set_column(1, minimum=70, priority=2)
        self.forms_table.set_column(2, kind="num", width=88, priority=1)
        self.forms_table.set_column(3, width=120)
        forms_card.add_widget(self.forms_table, 1)
        self.lbl_forms_summary = Caption("Scanning filing history…")
        self.lbl_forms_summary.setWordWrap(True)
        forms_card.add_widget(self.lbl_forms_summary)
        forms_note = Caption(
            "Each form is one tab in the workbook. The workbook refreshes by itself when new captures "
            "arrive; Save and refresh writes it now.")
        forms_note.setWordWrap(True)
        forms_card.add_widget(forms_note)
        forms_card.setMinimumHeight(220)
        self._body.addWidget(forms_card, 1)
        frame.add_widget(body_host, 1)

        self.btn_open_excel = frame.add_action("Open workbook", icon="mdi.microsoft-excel",
                                               tooltip="Saves, then opens the DRS workbook in Excel")
        self.btn_open_excel.clicked.connect(self._on_open_workbook)
        frame.add_footer_spacer()
        self.btn_close = frame.add_action("Close", icon="mdi.close")
        self.btn_close.clicked.connect(self.accept)
        self.btn_save = frame.add_action("Save and refresh", variant="primary", icon="mdi.content-save-outline",
                                         tooltip="Save this arrangement and rebuild the workbook now")
        self.btn_save.clicked.connect(self._on_save_and_refresh)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        direction = QBoxLayout.TopToBottom if self.width() < _STACK_BELOW else QBoxLayout.LeftToRight
        if self._body.direction() != direction:
            self._body.setDirection(direction)

    # ------------------------------------------------------------------ data

    def _load_data(self):
        """Discovers filing forms, loads per-form configs, and updates UI."""
        self._detected_forms = detect_forms_and_services(self.db)
        form_names = [f["form"] for f in self._detected_forms] or ["GSTR-3B", "GSTR-1", "ITR"]

        self._config = load_drs_config(self.db, detected_forms=form_names)

        current_scope = self._config.get("client_roster_scope", SCOPE_ALL_MCL)
        scope_idx = self.combo_scope.findData(current_scope)
        if scope_idx >= 0:
            self.combo_scope.blockSignals(True)
            self.combo_scope.setCurrentIndex(scope_idx)
            self.combo_scope.blockSignals(False)

        self.form_combo.blockSignals(True)
        self.form_combo.clear()
        for fname in form_names:
            self.form_combo.addItem(fname)
        self.form_combo.blockSignals(False)

        if form_names:
            self._current_form = form_names[0]
            self.form_combo.setCurrentIndex(0)
            self._render_current_form_columns()

        self._render_forms_summary()

    def _render_current_form_columns(self):
        """Renders the column list for the currently selected form."""
        if not self._current_form:
            return

        self.lbl_cols_header.setText(f"Columns for {self._current_form}")

        form_cfg = self._config.get("forms", {}).get(self._current_form, {})
        cols = form_cfg.get("columns", [])
        is_tracker_only = bool(form_cfg.get("tracker_only", False))

        self.chk_tracker_only.blockSignals(True)
        self.chk_tracker_only.setChecked(is_tracker_only)
        self.chk_tracker_only.blockSignals(False)

        table = self.col_list
        table.blockSignals(True)
        table.setRowCount(len(cols))
        for r, col in enumerate(cols):
            ctype = col.get("type", "")
            if ctype == "mcl" and (col.get("is_secret") or col.get("field_type") == "password"):
                ctype = "mcl_secret"
            tag_text, tone = _SOURCE_TAGS.get(ctype, ("Field", "neutral"))
            name = QTableWidgetItem(sentence_label(col.get("label", "Untitled")))
            name.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            name.setCheckState(Qt.Checked if col.get("enabled", True) else Qt.Unchecked)
            name.setData(Qt.UserRole, col.get("key"))
            table.setItem(r, 0, name)
            tag = QTableWidgetItem(tag_text)
            tag.setData(ROLE_TAG, (tag_text, tone))
            tag.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            table.setItem(r, 1, tag)
        table.blockSignals(False)
        self._update_move_buttons(table.currentRow())

    def _update_move_buttons(self, row: int):
        pick = None if 0 <= row < self.col_list.rowCount() else "Select a column first"
        self.btn_up.set_disabled_reason(pick or ("Already the first column" if row == 0 else None))
        self.btn_down.set_disabled_reason(
            pick or ("Already the last column" if row == self.col_list.rowCount() - 1 else None))

    def _render_forms_summary(self):
        """One row per detected form: portal, how many clients its tab lists, and its feed."""
        table = self.forms_table
        if not self._detected_forms:
            table.setRowCount(0)
            self.lbl_forms_summary.setText("No return forms detected in Tracker Dump yet.")
            self.lbl_forms_summary.show()
            return
        self.lbl_forms_summary.hide()

        all_clients = self.db.search_clients("", include_archived=False) if hasattr(self.db, "search_clients") else []
        scope = self._config.get("client_roster_scope", SCOPE_ALL_MCL)

        table.setRowCount(len(self._detected_forms))
        for r, fdef in enumerate(self._detected_forms):
            fname = fdef["form"]
            svc = fdef["service"]
            svc_name = svc["name"] if svc else "Unlinked"
            svc_id = svc["id"] if svc else None
            is_ltt = bool(self._config.get("forms", {}).get(fname, {}).get("tracker_only", False))

            if is_ltt:
                feed, tone, count = "Tracker only", "amber", "—"
            elif scope == SCOPE_SERVICE_ONLY and svc_id is not None:
                feed, tone = "Enrolled", "neutral"
                count = str(sum(1 for c in all_clients if svc_id in c.get("service_ids", [])))
            else:
                feed, tone, count = "Whole roster", "emerald", str(len(all_clients))

            form_item = QTableWidgetItem(fname)
            form_item.setToolTip(f"{fname}\nService: {svc_name}")
            table.setItem(r, 0, form_item)
            table.setItem(r, 1, QTableWidgetItem(fdef["portal"]))
            table.setItem(r, 2, QTableWidgetItem(count))
            feed_item = QTableWidgetItem(feed)
            feed_item.setData(ROLE_PILL, (feed, tone))
            table.setItem(r, 3, feed_item)

    # ------------------------------------------------------------------ edits (staged until Save)

    def _on_form_changed(self, index: int):
        self._current_form = self.form_combo.currentText()
        self._render_current_form_columns()

    def _on_tracker_only_toggled(self, checked: bool):
        if not self._current_form:
            return
        self._config.setdefault("forms", {}).setdefault(self._current_form, {})["tracker_only"] = checked
        self._render_forms_summary()

    def _on_scope_changed(self, index: int):
        self._config["client_roster_scope"] = self.combo_scope.currentData()
        self._render_forms_summary()

    def _on_item_checked(self, item: QTableWidgetItem):
        if not self._current_form or item.column() != 0:
            return
        key = item.data(Qt.UserRole)
        is_checked = item.checkState() == Qt.Checked
        cols = self._config.get("forms", {}).get(self._current_form, {}).get("columns", [])
        for col in cols:
            if col.get("key") == key:
                col["enabled"] = is_checked
                break

    def _move_column(self, direction: int):
        if not self._current_form:
            return
        cur_row = self.col_list.currentRow()
        if cur_row < 0:
            return
        cols = self._config.get("forms", {}).get(self._current_form, {}).get("columns", [])
        new_row = cur_row + direction
        if 0 <= new_row < len(cols):
            col = cols.pop(cur_row)
            cols.insert(new_row, col)
            self._render_current_form_columns()
            self.col_list.setCurrentCell(new_row, 0)

    def _reset_current_form_defaults(self):
        if not self._current_form:
            return
        if MessageBox.question(
            self, "Reset form columns",
            f"Reset the columns of {self._current_form} to the default order and visibility?",
        ) == QMessageBox.Yes:
            f_cfg = self._config.setdefault("forms", {}).setdefault(self._current_form, {})
            f_cfg["columns"] = default_drs_columns_for_form(self.db, self._current_form)
            f_cfg["tracker_only"] = False
            self._render_current_form_columns()
            self._render_forms_summary()

    def _reset_all_defaults(self):
        if MessageBox.question(
            self, "Reset all forms",
            "Reset every return form, its columns and the client roster scope to the defaults?",
            danger=True,
        ) == QMessageBox.Yes:
            form_names = [f["form"] for f in self._detected_forms]
            self._config = default_drs_config(self.db, form_names)
            self.combo_scope.blockSignals(True)
            self.combo_scope.setCurrentIndex(0)
            self.combo_scope.blockSignals(False)
            self._render_current_form_columns()
            self._render_forms_summary()

    def _save_current(self):
        save_drs_config(self.db, self._config)

    def _on_save_and_refresh(self):
        self._save_current()
        try:
            xlsx = export_drs(self.db, force_rebuild=False)
            MessageBox.information(self, "DRS refreshed",
                                   f"Saved, and the workbook is up to date.\n\nLocation: {xlsx}")
        except Exception as e:
            MessageBox.critical(self, "Export failed", f"Could not refresh the DRS workbook:\n{e}")

    def _on_open_workbook(self):
        self._save_current()
        try:
            xlsx = export_drs(self.db, force_rebuild=False)
            if not open_drs_workbook(self.app_dir):
                MessageBox.warning(self, "Workbook not found",
                                   f"Could not open the DRS workbook. Check that the file exists at:\n{xlsx}")
        except Exception as e:
            MessageBox.critical(self, "Open failed", f"Could not open the DRS workbook:\n{e}")
