"""
dashboard_window.py
-------------------
DRS Collective Dashboard Window.
Provides an aggregate view of compliance filing statuses across all clients.
"""

from PySide6.QtCore import Qt, Signal, QSize
try:
    import qtawesome as qta
except Exception:
    qta = None
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import drs


class DashboardWindow(QWidget):
    back_requested = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("DRS Compliance Dashboard")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header = QHBoxLayout()
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
        header.addWidget(back_btn)

        title = QLabel("DRS Compliance Dashboard <span style='color: #888888; font-size: 13px;'>(Offline)</span>")
        title.setProperty("class", "PageTitle")
        header.addWidget(title)
        header.addStretch()

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.setEnabled(False)
        btn_refresh.setToolTip("DRS (Deadline Reminder System) is currently offline for system maintenance.")
        btn_refresh.clicked.connect(self.refresh)
        header.addWidget(btn_refresh)
        layout.addLayout(header)

        # Offline Banner Notice
        offline_banner = QWidget()
        offline_banner.setStyleSheet("background-color: #FFF3CD; border: 1px solid #FFECB5; border-radius: 6px; padding: 8px;")
        ob_layout = QHBoxLayout(offline_banner)
        ob_layout.setContentsMargins(12, 6, 12, 6)
        ob_label = QLabel("⚠️ <b>Notice:</b> DRS (Deadline Reminder System) & Filing Success Tracker are currently offline for system maintenance.")
        ob_label.setStyleSheet("color: #856404; font-size: 12px; border: none;")
        ob_layout.addWidget(ob_label)
        layout.addWidget(offline_banner)

        # Stats Cards Banner
        stats_row = QHBoxLayout()
        
        self.card_total = self._make_stat_card("Total Clients Tracked", "0", "#1565c0")
        self.card_pending = self._make_stat_card("Pending Filings", "0", "#f57f17")
        self.card_overdue = self._make_stat_card("OVERDUE Filings", "0", "#c62828")
        self.card_submitted = self._make_stat_card("Submitted", "0", "#2e7d32")

        stats_row.addWidget(self.card_total)
        stats_row.addWidget(self.card_pending)
        stats_row.addWidget(self.card_overdue)
        stats_row.addWidget(self.card_submitted)
        layout.addLayout(stats_row)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("View Mode:"))
        self.view_combo = QComboBox()
        self.view_combo.addItem("👥 Group by Client (1 Row / Client)", userData="summary")
        self.view_combo.addItem("📋 Detailed Return Breakdown", userData="detailed")
        self.view_combo.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.view_combo)

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Filter by Service:"))
        self.svc_combo = QComboBox()
        self.svc_combo.addItem("All Services", userData=None)
        for s in self.db.get_services():
            self.svc_combo.addItem(s["name"], userData=s["id"])
        self.svc_combo.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.svc_combo)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Data Table
        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table, stretch=1)


        self.refresh()

    def _make_stat_card(self, title: str, val: str, color: str) -> QGroupBox:
        box = QGroupBox()
        box.setProperty("class", "stat-card")
        box.setStyleSheet(f"border-left: 5px solid {color};")
        l = QVBoxLayout(box)
        t_lbl = QLabel(title)
        t_lbl.setProperty("class", "stat-title")
        val_lbl = QLabel(val)
        val_lbl.setObjectName("stat_val")
        val_lbl.setStyleSheet(f"color: {color};")
        l.addWidget(t_lbl)
        l.addWidget(val_lbl)
        return box

    def _update_stat_card(self, card_box: QGroupBox, val: str):
        lbl = card_box.findChild(QLabel, "stat_val")
        if lbl:
            lbl.setText(str(val))

    def refresh(self):
        svc_id = self.svc_combo.currentData()
        batch_data = self.db.get_dashboard_batch_data(service_id=svc_id)
        items = batch_data["items"]
        status_map = batch_data["status_map"]

        client_ids_seen = set()
        total_pending = 0
        total_overdue = 0
        total_submitted = 0

        for item in items:
            c_id = item["client_id"]
            client_ids_seen.add(c_id)
            ft = item["filing_type"]

            curr_info = drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=0)
            db_stat = status_map.get((c_id, ft["id"], curr_info["period_label"]))
            eval_stat = drs.DRSEngine.evaluate_status(db_stat, curr_info["due_date"], curr_info["grace_days"])

            prev_info = drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=-1)
            db_prev_stat = status_map.get((c_id, ft["id"], prev_info["period_label"]))
            eval_prev_stat = drs.DRSEngine.evaluate_status(db_prev_stat, prev_info["due_date"], prev_info["grace_days"])

            for st in (eval_stat, eval_prev_stat):
                if st == "submitted":
                    total_submitted += 1
                elif st == "overdue":
                    total_overdue += 1
                elif st == "pending":
                    total_pending += 1

        self._update_stat_card(self.card_total, len(client_ids_seen))
        self._update_stat_card(self.card_pending, total_pending)
        self._update_stat_card(self.card_overdue, total_overdue)
        self._update_stat_card(self.card_submitted, total_submitted)

        mode = self.view_combo.currentData() if hasattr(self, "view_combo") else "summary"

        if mode == "summary":
            clients_map = {}
            for item in items:
                cid = item["client_id"]
                if cid not in clients_map:
                    clients_map[cid] = {
                        "client_id": cid,
                        "client_name": item["client_name"],
                        "items": []
                    }
                clients_map[cid]["items"].append(item)

            summary_rows = []
            for cid, c_data in clients_map.items():
                active_svcs = sorted({it["filing_type"]["service_name"] for it in c_data["items"]})
                svc_summary_str = ", ".join(active_svcs)

                curr_statuses = []
                prev_statuses = []
                for it in c_data["items"]:
                    ft = it["filing_type"]
                    curr_info = drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=0)
                    db_stat = status_map.get((cid, ft["id"], curr_info["period_label"]))
                    eval_stat = drs.DRSEngine.evaluate_status(db_stat, curr_info["due_date"], curr_info["grace_days"])
                    curr_statuses.append(eval_stat)

                    prev_info = drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=-1)
                    db_prev_stat = status_map.get((cid, ft["id"], prev_info["period_label"]))
                    eval_prev_stat = drs.DRSEngine.evaluate_status(db_prev_stat, prev_info["due_date"], prev_info["grace_days"])
                    prev_statuses.append(eval_prev_stat)

                def get_worst_status(st_list):
                    if "overdue" in st_list:
                        return "overdue"
                    if "pending" in st_list:
                        return "pending"
                    if "in_progress" in st_list:
                        return "in_progress"
                    return "submitted"

                summary_rows.append({
                    "client_id": cid,
                    "client_name": c_data["client_name"],
                    "services": svc_summary_str,
                    "filing_count": len(c_data["items"]),
                    "curr_status": get_worst_status(curr_statuses),
                    "prev_status": get_worst_status(prev_statuses)
                })

            self.table.setUpdatesEnabled(False)
            self.table.setSortingEnabled(False)
            self.table.setColumnCount(5)
            dash_headers = ["Client Name", "Active Services Tracked", "Total Returns", "Current Period Status", "Previous Period Status"]
            self.table.setHorizontalHeaderLabels(dash_headers)
            self.table.setRowCount(len(summary_rows))

            for i, r in enumerate(summary_rows):
                name_item = QTableWidgetItem(r["client_name"])
                name_item.setData(Qt.UserRole, r["client_id"])
                self.table.setItem(i, 0, name_item)

                self.table.setItem(i, 1, QTableWidgetItem(r["services"]))
                self.table.setItem(i, 2, QTableWidgetItem(f"{r['filing_count']} return(s)"))

                curr_item = self._create_summary_status_item(r["curr_status"])
                curr_item.setData(Qt.UserRole, r["client_id"])
                self.table.setItem(i, 3, curr_item)

                prev_item = self._create_summary_status_item(r["prev_status"])
                prev_item.setData(Qt.UserRole, r["client_id"])
                self.table.setItem(i, 4, prev_item)

            self.table.resizeColumnsToContents()
            for c_idx in range(len(dash_headers)):
                col_w = self.table.columnWidth(c_idx)
                text_w = self.table.fontMetrics().horizontalAdvance(dash_headers[c_idx]) + 30
                self.table.setColumnWidth(c_idx, max(col_w + 24, text_w, 110))
            self.table.setSortingEnabled(True)
            self.table.horizontalHeader().setSortIndicatorShown(True)
            self.table.horizontalHeader().setSectionsClickable(True)
            self.table.setUpdatesEnabled(True)
            return

        # Detailed breakdown mode
        table_rows = []
        for item in items:
            c_id = item["client_id"]
            c_name = item["client_name"]
            ft = item["filing_type"]

            curr_info = drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=0)
            db_stat = status_map.get((c_id, ft["id"], curr_info["period_label"]))
            eval_stat = drs.DRSEngine.evaluate_status(db_stat, curr_info["due_date"], curr_info["grace_days"])

            prev_info = drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=-1)
            db_prev_stat = status_map.get((c_id, ft["id"], prev_info["period_label"]))
            eval_prev_stat = drs.DRSEngine.evaluate_status(db_prev_stat, prev_info["due_date"], prev_info["grace_days"])

            table_rows.append({
                "client_id": c_id,
                "filing_type_id": ft["id"],
                "client_name": c_name,
                "service_name": ft["service_name"],
                "filing_name": ft["name"],
                "curr_period": curr_info["period_label"],
                "curr_due": curr_info.get("due_date_formatted", curr_info["due_date"]),
                "curr_status": eval_stat,
                "curr_db_stat": db_stat,
                "prev_period": prev_info["period_label"],
                "prev_status": eval_prev_stat,
                "prev_db_stat": db_prev_stat
            })

        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(6)
        dash_headers = ["Client", "Filing", "Current Period", "Current Due Date", "Current Status", "Previous Period Status"]
        self.table.setHorizontalHeaderLabels(dash_headers)
        self.table.setRowCount(len(table_rows))

        for i, r in enumerate(table_rows):
            self.table.setItem(i, 0, QTableWidgetItem(r["client_name"]))
            self.table.setItem(i, 1, QTableWidgetItem(f"{r['filing_name']} ({r['service_name']})"))
            self.table.setItem(i, 2, QTableWidgetItem(r["curr_period"]))
            self.table.setItem(i, 3, QTableWidgetItem(r["curr_due"]))

            curr_item = self._create_status_item(r["curr_status"], r["client_id"], r["filing_type_id"], r["curr_period"])
            self.table.setItem(i, 4, curr_item)

            prev_item = self._create_status_item(r["prev_status"], r["client_id"], r["filing_type_id"], r["prev_period"])
            self.table.setItem(i, 5, prev_item)

        self.table.resizeColumnsToContents()
        for c_idx in range(len(dash_headers)):
            col_w = self.table.columnWidth(c_idx)
            text_w = self.table.fontMetrics().horizontalAdvance(dash_headers[c_idx]) + 30
            self.table.setColumnWidth(c_idx, max(col_w + 24, text_w, 100))
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.setUpdatesEnabled(True)


    def _create_summary_status_item(self, eval_stat: str) -> QTableWidgetItem:
        labels = {
            "submitted": "🟢 All Submitted",
            "in_progress": "🟡 In-Progress",
            "pending": "🔴 Pending Returns",
            "overdue": "🍷 OVERDUE (Action Required)"
        }
        text = labels.get(eval_stat, eval_stat.title())
        item = QTableWidgetItem(text)
        
        if eval_stat == "submitted":
            item.setForeground(Qt.darkGreen)
        elif eval_stat == "in_progress":
            item.setForeground(Qt.darkYellow)
        elif eval_stat == "overdue":
            item.setForeground(Qt.darkRed)
        else:
            item.setForeground(Qt.red)
        return item

    def _create_status_item(self, eval_stat: str, client_id: int, filing_type_id: int, period_label: str) -> QTableWidgetItem:
        labels = {
            "submitted": "🟢 Submitted  [Click to change]",
            "in_progress": "🟡 In-Progress  [Click to change]",
            "pending": "🔴 Pending  [Click to change]",
            "overdue": "🍷 OVERDUE  [Click to change]"
        }
        text = labels.get(eval_stat, eval_stat.title())
        item = QTableWidgetItem(text)
        
        if eval_stat == "submitted":
            item.setForeground(Qt.darkGreen)
        elif eval_stat == "in_progress":
            item.setForeground(Qt.darkYellow)
        elif eval_stat == "overdue":
            item.setForeground(Qt.darkRed)
        else:
            item.setForeground(Qt.red)
            
        item.setData(Qt.UserRole, {
            "client_id": client_id,
            "filing_type_id": filing_type_id,
            "period_label": period_label,
            "eval_stat": eval_stat
        })
        return item

    def _on_cell_clicked(self, row: int, col: int):
        if col not in (4, 5):
            return
        item = self.table.item(row, col)
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not data:
            return

        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        a_sub = menu.addAction("🟢 Mark as Submitted")
        a_prog = menu.addAction("🟡 Mark as In-Progress")
        a_pend = menu.addAction("🔴 Mark as Pending")

        action = menu.exec_(QCursor.pos())
        new_st = None
        if action == a_sub:
            new_st = "submitted"
        elif action == a_prog:
            new_st = "in_progress"
        elif action == a_pend:
            new_st = "pending"

        if new_st:
            self.db.set_filing_status(
                client_id=data["client_id"],
                filing_type_id=data["filing_type_id"],
                period_label=data["period_label"],
                status=new_st,
                updated_by="Staff"
            )
            self.refresh()

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        if not item:
            return
        data = item.data(Qt.UserRole)
        client_id = None
        if isinstance(data, dict):
            client_id = data.get("client_id")
        elif isinstance(data, int):
            client_id = data
            
        if client_id:
            from ui.dialogs.client_filing_status_dialog import ClientFilingStatusDialog
            dlg = ClientFilingStatusDialog(self.db, client_id, parent=self)
            if dlg.exec_():
                self.refresh()
