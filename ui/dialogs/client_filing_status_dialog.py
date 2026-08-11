"""
client_filing_status_dialog.py
--------------------------------
Dedicated window/dialog for managing a specific client's filing obligations,
active returns (enabling/disabling), status updates per period, schedule variants,
and ARN/Acknowledgement numbers.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import drs
from ui.utils.tag_widget import TagWidget


class ClientFilingStatusDialog(QDialog):
    def __init__(self, db, client_id: int, actor: str = "Staff", parent=None):
        super().__init__(parent)
        self.db = db
        self.client_id = client_id
        self.actor = actor
        self.client = self.db.get_client(client_id)
        
        self.setWindowTitle("DRS Client Filing Status Manager")
        self.resize(780, 560)
        self._build_ui()
        self._load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header row: Client Name & Switch Client Combo
        header = QHBoxLayout()
        self.lbl_client = QLabel()
        self.lbl_client.setProperty("class", "ClientName")
        header.addWidget(self.lbl_client)
        header.addStretch()

        header.addWidget(QLabel("Switch Client:"))
        self.combo_switch_client = QComboBox()
        self.combo_switch_client.setMinimumWidth(220)
        
        # Populate all non-archived clients
        all_clients = self.db.search_clients("")
        identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c["is_identity"]]
        for c in all_clients:
            vals = [c["values"].get(cid, "") for cid in identity_cols if c["values"].get(cid)]
            c_name = " — ".join(vals) if vals else f"Client #{c['id']}"
            self.combo_switch_client.addItem(c_name, userData=c["id"])
            
        idx = self.combo_switch_client.findData(self.client_id)
        if idx >= 0:
            self.combo_switch_client.blockSignals(True)
            self.combo_switch_client.setCurrentIndex(idx)
            self.combo_switch_client.blockSignals(False)

        self.combo_switch_client.currentIndexChanged.connect(self._on_switch_client)
        header.addWidget(self.combo_switch_client)
        layout.addLayout(header)

        # Guidance Banner
        guidance = QLabel("Changes here will immediately log to the database and re-calculate overdue metrics.")
        guidance.setProperty("class", "GuidanceText")
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        # Scroll area for filing types
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        scroll.setWidget(self.scroll_content)
        layout.addWidget(scroll, stretch=1)

        # Footer Actions
        footer = QHBoxLayout()
        footer.addStretch()
        btn_close = QPushButton("Done / Close")
        btn_close.setFixedWidth(120)
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        layout.addLayout(footer)

    def _on_switch_client(self, idx: int):
        new_cid = self.combo_switch_client.currentData()
        if new_cid and new_cid != self.client_id:
            self.client_id = new_cid
            self.client = self.db.get_client(new_cid)
            self._load_data()

    def _clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())

    def _load_data(self):
        self._clear_layout(self.scroll_layout)

        if not self.client:
            self.lbl_client.setText("Client Not Found")
            return

        # Update client label header
        identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c["is_identity"]]
        vals = [self.client["values"].get(cid, "") for cid in identity_cols if self.client["values"].get(cid)]
        c_name = " — ".join(vals) if vals else f"Client #{self.client_id}"
        self.lbl_client.setText(f"📋 {c_name}")

        # Get all filing types for attached services
        client_fts = self.db.get_client_filing_types(self.client_id, enabled_only=False)

        if not client_fts:
            no_fts_lbl = QLabel(
                "No filing rules configured for this client's attached service(s).\n"
                "To import filing period structures, go to Admin Mode → Import Filing Periods..."
            )
            no_fts_lbl.setProperty("class", "NoDataLabel")
            no_fts_lbl.setAlignment(Qt.AlignCenter)
            self.scroll_layout.addWidget(no_fts_lbl)
            return

        # Group filing types by service
        svcs_map = {}
        for ft in client_fts:
            sname = ft["service_name"]
            if sname not in svcs_map:
                svcs_map[sname] = []
            svcs_map[sname].append(ft)

        for sname, fts in svcs_map.items():
            box = QGroupBox(f"Service: {sname}")
            box_layout = QVBoxLayout(box)
            box_layout.setSpacing(10)

            for ft in fts:
                ft_id = ft["id"]
                is_enabled = ft.get("is_enabled", True)

                card = QFrame()
                card.setProperty("class", "ServiceCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(8, 8, 8, 8)

                # Top row: Checkbox, Name, Schedule Variant
                top_row = QHBoxLayout()

                cb_enable = QCheckBox("Track Return")
                cb_enable.setChecked(is_enabled)
                top_row.addWidget(cb_enable)

                name_lbl = QLabel(f"<b>{ft['name']}</b> ({ft['code']})")
                name_lbl.setProperty("class", "ClientName")
                top_row.addWidget(name_lbl, stretch=1)

                if ft.get("variants"):
                    top_row.addWidget(QLabel("Schedule:"))
                    var_combo = QComboBox()
                    var_combo.addItem("Default", userData=None)
                    for v in ft["variants"]:
                        var_combo.addItem(v.get("tag", "Variant"), userData=v.get("tag"))

                    curr_tag = ft.get("variant_tag")
                    if curr_tag:
                        v_idx = var_combo.findText(curr_tag, Qt.MatchFixedString)
                        if v_idx >= 0:
                            var_combo.setCurrentIndex(v_idx)

                    var_combo.currentIndexChanged.connect(
                        lambda idx, f_id=ft_id, combo=var_combo: self.db.attach_client_filing_type(
                            self.client_id, f_id, combo.currentData()
                        )
                    )
                    top_row.addWidget(var_combo)

                card_layout.addLayout(top_row)

                # Status Controls Row (only visible if enabled)
                controls_widget = QWidget()
                ctrl_layout = QHBoxLayout(controls_widget)
                ctrl_layout.setContentsMargins(0, 4, 0, 0)

                # Period selector (Current, Previous, 2 Periods Ago)
                periods_list = [
                    drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=off)
                    for off in [0, -1, -2]
                ]
                period_combo = QComboBox()
                for p in periods_list:
                    due_str = p.get("due_date_formatted", p["due_date"])
                    period_combo.addItem(f"{p['period_label']} (Due: {due_str})", userData=p)
                ctrl_layout.addWidget(period_combo, stretch=2)

                # Status Tag Badge
                initial_p = periods_list[0]
                db_stat = self.db.get_filing_status(self.client_id, ft_id, initial_p['period_label'])
                eval_stat = drs.DRSEngine.evaluate_status(db_stat, initial_p['due_date'], initial_p['grace_days'])

                tag = TagWidget(tag_type=eval_stat)
                ctrl_layout.addWidget(tag)

                # Status Change Combo
                stat_combo = QComboBox()
                stat_combo.addItem("Pending", "pending")
                stat_combo.addItem("In-Progress", "in_progress")
                stat_combo.addItem("Submitted", "submitted")

                # ARN Input
                arn_edit = QLineEdit()
                arn_edit.setPlaceholderText("ARN / Ack No.")
                arn_edit.setFixedWidth(140)
                if db_stat and db_stat.get("arn_number"):
                    arn_edit.setText(db_stat["arn_number"])

                def sync_period(p_info, ft_id=ft_id, tag=tag, stat_combo=stat_combo, arn_edit=arn_edit):
                    st = self.db.get_filing_status(self.client_id, ft_id, p_info['period_label'])
                    ev_st = drs.DRSEngine.evaluate_status(st, p_info['due_date'], p_info['grace_days'])
                    tag.set_tag(ev_st)

                    c_st = (st.get("status") if st else ev_st).lower()
                    if c_st == "overdue":
                        c_st = "pending"
                    s_idx = stat_combo.findData(c_st)
                    if s_idx >= 0:
                        stat_combo.blockSignals(True)
                        stat_combo.setCurrentIndex(s_idx)
                        stat_combo.blockSignals(False)

                    arn_edit.blockSignals(True)
                    arn_edit.setText((st.get("arn_number") if st else "") or "")
                    arn_edit.blockSignals(False)

                sync_period(initial_p)

                period_combo.currentIndexChanged.connect(
                    lambda idx, combo=period_combo: sync_period(combo.currentData())
                )

                def save_status(new_st, ft_id=ft_id, period_combo=period_combo, arn_edit=arn_edit):
                    p_info = period_combo.currentData()
                    arn_val = arn_edit.text().strip() or None
                    self.db.set_filing_status(
                        client_id=self.client_id,
                        filing_type_id=ft_id,
                        period_label=p_info['period_label'],
                        status=new_st,
                        arn_number=arn_val,
                        updated_by=self.actor
                    )
                    sync_period(p_info)

                stat_combo.currentIndexChanged.connect(
                    lambda idx, combo=stat_combo: save_status(combo.currentData())
                )
                arn_edit.editingFinished.connect(
                    lambda combo=stat_combo: save_status(combo.currentData())
                )

                ctrl_layout.addWidget(stat_combo)
                ctrl_layout.addWidget(arn_edit)

                card_layout.addWidget(controls_widget)

                # Checkbox toggle effect
                controls_widget.setVisible(is_enabled)

                def on_toggle_enable(checked, f_id=ft_id, widget=controls_widget):
                    widget.setVisible(checked)
                    self.db.set_client_filing_type_enabled(self.client_id, f_id, is_enabled=checked)

                cb_enable.toggled.connect(on_toggle_enable)

                box_layout.addWidget(card)

            self.scroll_layout.addWidget(box)

        self.scroll_layout.addStretch()
