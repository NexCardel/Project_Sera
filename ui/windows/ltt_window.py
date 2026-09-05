"""
ltt_window.py
-------------
Live Tracking Table (LTT) Interactive Workspace & Compliance Dashboard for Project Sera.
Provides:
- Real-time KPI summary metrics bar (Verified, Pending e-Verification, Defaulters, Compliance %).
- Live Interactive Table with instant search, multi-criteria filters, and Material Design badges.
- Filing Matrix Grid View for GST (Clients vs Months) and Income Tax (Clients vs AY).
- Dedicated Action-Required / Defaulter Tracker with one-click follow-up copy.
- Seamless integration with SDC_Parser's multi-sheet Excel export.
"""

import os
import sys
import json
from datetime import datetime, date
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QClipboard
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QTabWidget, QFrame, QCheckBox,
    QScrollArea, QMenu, QApplication
)

try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_qta_icon(icon_name, color="#FFFFFF"):
    if qta is not None:
        try:
            return qta.icon(icon_name, color=color)
        except Exception:
            pass
    return None


class LttWorkspaceWindow(QDialog):
    """Full-featured interactive Live Tracking Table (LTT) workspace dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live Tracking Table (LTT) Workspace - Sera Compliance")
        self.setMinimumSize(1150, 720)
        self.resize(1260, 800)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self._data = []
        self._kpis = {}
        self._filtered_data = []

        self._build_ui()
        self.load_data()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0D1117;
                color: #C9D1D9;
                font-family: 'Segoe UI', 'Calibri', sans-serif;
            }
            QFrame#HeaderCard {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 10px;
            }
            QLabel#TitleLbl {
                font-size: 18px;
                font-weight: 700;
                color: #F0F6FC;
            }
            QLabel#SubtitleLbl {
                font-size: 12px;
                color: #8B949E;
            }
            QTabWidget::pane {
                border: 1px solid #30363D;
                background-color: #161B22;
                border-radius: 4px;
            }
            QTabBar::tab {
                background: #21262D;
                color: #8B949E;
                font-weight: 600;
                padding: 8px 18px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #161B22;
                color: #4CF9B7;
                border-bottom: 2px solid #4CF9B7;
            }
            QTabBar::tab:hover:!selected {
                background: #30363D;
                color: #F0F6FC;
            }
            QTableWidget {
                background-color: #0D1117;
                alternate-background-color: #161B22;
                gridline-color: #21262D;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 4px;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #161B22;
                color: #8B949E;
                font-weight: 700;
                font-size: 11px;
                padding: 6px;
                border: 1px solid #21262D;
            }
            QPushButton.ActionBtn {
                background-color: #21262D;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton.ActionBtn:hover {
                background-color: #30363D;
                border-color: #8B949E;
            }
            QLineEdit, QComboBox {
                background-color: #161B22;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #58A6FF;
            }
        """)

        main_vbox = QVBoxLayout(self)
        main_vbox.setContentsMargins(14, 12, 14, 12)
        main_vbox.setSpacing(10)

        # 1. Top Header Card
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(12, 8, 12, 8)

        title_vbox = QVBoxLayout()
        title_row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.table-large", "#58A6FF").pixmap(24, 24) if qta else None)
        title_row.addWidget(icon_lbl)
        
        lbl_title = QLabel("Live Tracking Table (LTT) Workspace")
        lbl_title.setObjectName("TitleLbl")
        title_row.addWidget(lbl_title)
        title_row.addStretch()
        title_vbox.addLayout(title_row)

        lbl_sub = QLabel("Comprehensive return compliance, cross-form consistency & authoritative audit records")
        lbl_sub.setObjectName("SubtitleLbl")
        title_vbox.addWidget(lbl_sub)
        h_layout.addLayout(title_vbox)

        h_layout.addStretch()

        # Action Buttons
        btn_refresh = QPushButton(" Refresh")
        btn_refresh.setProperty("class", "ActionBtn")
        btn_refresh.setIcon(_safe_qta_icon("mdi.refresh", "#FFFFFF"))
        btn_refresh.clicked.connect(self.load_data)
        h_layout.addWidget(btn_refresh)

        btn_export = QPushButton(" Export Multi-Sheet Excel")
        btn_export.setProperty("class", "ActionBtn")
        btn_export.setStyleSheet("background-color: #1F6FEB; color: #FFFFFF; font-weight: 700;")
        btn_export.setIcon(_safe_qta_icon("mdi.file-excel", "#FFFFFF"))
        btn_export.clicked.connect(self._export_excel)
        h_layout.addWidget(btn_export)

        btn_close = QPushButton(" Close")
        btn_close.setProperty("class", "ActionBtn")
        btn_close.setIcon(_safe_qta_icon("mdi.close", "#FFFFFF"))
        btn_close.clicked.connect(self.accept)
        h_layout.addWidget(btn_close)

        main_vbox.addWidget(header_card)

        # 2. KPI Summary Cards Banner
        self.kpi_banner = QFrame()
        self.kpi_banner.setStyleSheet("background-color: #161B22; border: 1px solid #30363D; border-radius: 6px; padding: 6px;")
        kpi_layout = QHBoxLayout(self.kpi_banner)
        kpi_layout.setContentsMargins(8, 4, 8, 4)
        kpi_layout.setSpacing(12)

        self.kpi_total = self._create_kpi_card("TOTAL FILINGS", "0", "#58A6FF", "#16283D")
        self.kpi_verified = self._create_kpi_card("SUBMITTED & VERIFIED", "0", "#39FF14", "#1A382B")
        self.kpi_pending = self._create_kpi_card("E-VERIF PENDING", "0", "#F1E05A", "#382F1A")
        self.kpi_defaulters = self._create_kpi_card("ACTION REQUIRED / OVERDUE", "0", "#FF6B6B", "#381A1A")
        self.kpi_compliance = self._create_kpi_card("OVERALL COMPLIANCE", "0%", "#4CF9B7", "#0D3326")

        kpi_layout.addWidget(self.kpi_total)
        kpi_layout.addWidget(self.kpi_verified)
        kpi_layout.addWidget(self.kpi_pending)
        kpi_layout.addWidget(self.kpi_defaulters)
        kpi_layout.addWidget(self.kpi_compliance)

        main_vbox.addWidget(self.kpi_banner)

        # 3. Main Tab Widget
        self.tabs = QTabWidget()

        # Tab 1: Live Interactive Table
        tab_live = QWidget()
        live_vbox = QVBoxLayout(tab_live)
        live_vbox.setContentsMargins(8, 8, 8, 8)
        live_vbox.setSpacing(8)

        # Filter Bar
        filter_box = QHBoxLayout()
        filter_box.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search Client, PAN, GSTIN, ARN, Form, Period...")
        self.txt_search.textChanged.connect(self._apply_filters)
        filter_box.addWidget(self.txt_search, stretch=3)

        self.cmb_portal = QComboBox()
        self.cmb_portal.addItems(["All Portals", "Income Tax (ITD)", "GST Portal", "TRACES / TDS"])
        self.cmb_portal.currentIndexChanged.connect(self._apply_filters)
        filter_box.addWidget(self.cmb_portal, stretch=2)

        self.cmb_status = QComboBox()
        self.cmb_status.addItems([
            "All Statuses",
            "Submitted & E-verified",
            "Submitted (e-verification pending)",
            "Other EVC",
            "Not submitted",
            "Not Applicable (NA)",
            "Option Expired (NA)"
        ])
        self.cmb_status.currentIndexChanged.connect(self._apply_filters)
        filter_box.addWidget(self.cmb_status, stretch=2)

        self.cmb_period = QComboBox()
        self.cmb_period.addItem("All Periods")
        self.cmb_period.currentIndexChanged.connect(self._apply_filters)
        filter_box.addWidget(self.cmb_period, stretch=2)

        self.chk_action_only = QCheckBox("Action Required Only")
        self.chk_action_only.setStyleSheet("color: #FF7B72; font-weight: 600; font-size: 11px;")
        self.chk_action_only.stateChanged.connect(self._apply_filters)
        filter_box.addWidget(self.chk_action_only, stretch=1)

        btn_reset = QPushButton("Reset")
        btn_reset.setProperty("class", "ActionBtn")
        btn_reset.setIcon(_safe_qta_icon("mdi.filter-off", "#8B949E"))
        btn_reset.clicked.connect(self._reset_filters)
        filter_box.addWidget(btn_reset, stretch=0)

        live_vbox.addLayout(filter_box)

        # Table
        self.tbl_live = QTableWidget()
        self.tbl_live.setAlternatingRowColors(True)
        self.tbl_live.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_live.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_live.customContextMenuRequested.connect(self._on_table_context_menu)
        self.tbl_live.cellDoubleClicked.connect(self._on_row_double_clicked)
        live_vbox.addWidget(self.tbl_live)

        self.lbl_table_count = QLabel("Showing: 0 records")
        self.lbl_table_count.setStyleSheet("color: #8B949E; font-size: 11px; font-weight: 600;")
        live_vbox.addWidget(self.lbl_table_count)

        self.tabs.addTab(tab_live, _safe_qta_icon("mdi.table-eye", "#4CF9B7") or "", "Live Table View")

        # Tab 2: Filing Matrix Grid
        tab_matrix = QWidget()
        matrix_vbox = QVBoxLayout(tab_matrix)
        matrix_vbox.setContentsMargins(8, 8, 8, 8)
        matrix_vbox.setSpacing(8)

        # Matrix Sub Tabs
        self.matrix_subtabs = QTabWidget()

        # GST Matrix
        self.tbl_gst_matrix = QTableWidget()
        self.tbl_gst_matrix.setAlternatingRowColors(True)
        self.tbl_gst_matrix.setSelectionBehavior(QTableWidget.SelectRows)
        self.matrix_subtabs.addTab(self.tbl_gst_matrix, "GST Compliance Grid (Clients vs Periods)")

        # IT Matrix
        self.tbl_it_matrix = QTableWidget()
        self.tbl_it_matrix.setAlternatingRowColors(True)
        self.tbl_it_matrix.setSelectionBehavior(QTableWidget.SelectRows)
        self.matrix_subtabs.addTab(self.tbl_it_matrix, "Income Tax Assessment Year Grid (Clients vs AY)")

        matrix_vbox.addWidget(self.matrix_subtabs)
        self.tabs.addTab(tab_matrix, _safe_qta_icon("mdi.grid", "#58A6FF") or "", "Filing Matrix (Grid View)")

        # Tab 3: Action-Required & Defaulters
        tab_defaulters = QWidget()
        def_vbox = QVBoxLayout(tab_defaulters)
        def_vbox.setContentsMargins(8, 8, 8, 8)
        def_vbox.setSpacing(8)

        def_bar = QHBoxLayout()
        self.lbl_defaulters_banner = QLabel("Items requiring urgent follow-up (Overdue, Pending e-Verification, Discrepancies)")
        self.lbl_defaulters_banner.setStyleSheet("color: #FF7B72; font-weight: 700; font-size: 12px;")
        def_bar.addWidget(self.lbl_defaulters_banner)
        def_bar.addStretch()

        btn_copy_defaulters = QPushButton(" Copy Defaulter Summary")
        btn_copy_defaulters.setProperty("class", "ActionBtn")
        btn_copy_defaulters.setIcon(_safe_qta_icon("mdi.content-copy", "#FFFFFF"))
        btn_copy_defaulters.clicked.connect(self._copy_defaulter_summary)
        def_bar.addWidget(btn_copy_defaulters)

        def_vbox.addLayout(def_bar)

        self.tbl_defaulters = QTableWidget()
        self.tbl_defaulters.setAlternatingRowColors(True)
        self.tbl_defaulters.setSelectionBehavior(QTableWidget.SelectRows)
        def_vbox.addWidget(self.tbl_defaulters)

        self.tabs.addTab(tab_defaulters, _safe_qta_icon("mdi.alert-decagram", "#FF7B72") or "", "Action Required / Defaulters")

        main_vbox.addWidget(self.tabs)

    def _create_kpi_card(self, title, val, color_hex, bg_hex):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_hex};
                border: 1px solid {color_hex}40;
                border-radius: 6px;
                padding: 6px 12px;
            }}
        """)
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("font-size: 10px; font-weight: 700; color: #8B949E; text-transform: uppercase;")
        vbox.addWidget(lbl_t)

        lbl_v = QLabel(val)
        lbl_v.setObjectName("val_label")
        lbl_v.setStyleSheet(f"font-size: 17px; font-weight: 800; color: {color_hex};")
        vbox.addWidget(lbl_v)

        return frame

    def _update_kpi_card(self, card, new_val):
        lbl = card.findChild(QLabel, "val_label")
        if lbl:
            lbl.setText(str(new_val))

    def load_data(self):
        """Loads authoritative LTT data directly from sdc_parser.get_ltt_dataset()."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            sdc_parser_dir = os.path.abspath(os.path.join(base_dir, '..', '..', 'SDC_Parser'))
            if sdc_parser_dir not in sys.path:
                sys.path.insert(0, sdc_parser_dir)
            
            import sdc_parser
            import importlib
            importlib.reload(sdc_parser)

            self._data, self._kpis = sdc_parser.get_ltt_dataset()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error Loading LTT", f"Failed to extract LTT data: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        # Update KPI Cards
        self._update_kpi_card(self.kpi_total, self._kpis.get("total_filings", 0))
        self._update_kpi_card(self.kpi_verified, self._kpis.get("verified_count", 0))
        self._update_kpi_card(self.kpi_pending, self._kpis.get("pending_verif_count", 0))
        self._update_kpi_card(self.kpi_defaulters, self._kpis.get("defaulters_count", 0))
        self._update_kpi_card(self.kpi_compliance, f"{self._kpis.get('compliance_rate', 0.0)}%")

        # Populate Period Filter dropdown dynamically
        periods = sorted(list({d.get("Filing Period", "") for d in self._data if d.get("Filing Period")}), reverse=True)
        self.cmb_period.blockSignals(True)
        self.cmb_period.clear()
        self.cmb_period.addItem("All Periods")
        for p in periods:
            self.cmb_period.addItem(p)
        self.cmb_period.blockSignals(False)

        self._apply_filters()
        self._render_matrices()
        self._render_defaulters()

    def _apply_filters(self):
        """Applies real-time search, portal, status, period, and action-only filters."""
        search_txt = self.txt_search.text().strip().lower()
        portal_filter = self.cmb_portal.currentText()
        status_filter = self.cmb_status.currentText()
        period_filter = self.cmb_period.currentText()
        action_only = self.chk_action_only.isChecked()

        filtered = []
        for d in self._data:
            # 1. Action Required Only filter
            if action_only:
                alert = d.get("Compliance Alert", "")
                disc = d.get("Discrepancy Note", "")
                st = d.get("Submit Status", "")
                is_action = (
                    st not in ("Submitted & E-verified", "Option Expired (NA)", "Not Applicable (NA)") and
                    ("Overdue" in alert or "Expiring" in alert or bool(disc) or st == "Not submitted")
                )
                if not is_action:
                    continue

            # 2. Portal filter
            if portal_filter != "All Portals" and d.get("Portal") != portal_filter:
                continue

            # 3. Status filter
            if status_filter != "All Statuses" and d.get("Submit Status") != status_filter:
                continue

            # 4. Period filter
            if period_filter != "All Periods" and d.get("Filing Period") != period_filter:
                continue

            # 5. Search text filter
            if search_txt:
                match_fields = [
                    d.get("Client Name", ""), d.get("PAN", ""), d.get("GSTIN", ""),
                    d.get("ARN", ""), d.get("Filing Type", ""), d.get("Filing Period", ""),
                    d.get("Filing Preference", ""), d.get("Submit Status", ""),
                    d.get("Compliance Alert", ""), d.get("Discrepancy Note", "")
                ]
                if not any(search_txt in str(val).lower() for val in match_fields):
                    continue

            filtered.append(d)

        self._filtered_data = filtered
        self._render_live_table()

    def _render_live_table(self):
        """Renders the main interactive table with styled status pills and icons."""
        cols = [
            "#", "Client Name", "PAN", "GSTIN", "Portal", "Preference", "Filing Type",
            "Filing Period", "Submit Status", "ARN", "Due Date",
            "Compliance Alert", "Discrepancy Note", "Last Updated"
        ]
        self.tbl_live.clear()
        self.tbl_live.setColumnCount(len(cols))
        self.tbl_live.setHorizontalHeaderLabels(cols)
        self.tbl_live.setRowCount(len(self._filtered_data))

        font_item = QFont("Segoe UI", 9)
        bold_font = QFont("Segoe UI", 9, QFont.Bold)

        for row_idx, r in enumerate(self._filtered_data):
            # 0: #
            it_idx = QTableWidgetItem(str(row_idx + 1))
            it_idx.setTextAlignment(Qt.AlignCenter)
            it_idx.setFont(font_item)
            it_idx.setForeground(QColor("#8B949E"))
            self.tbl_live.setItem(row_idx, 0, it_idx)

            # 1: Client Name
            c_name = r.get("Client Name") or "-"
            it_name = QTableWidgetItem(c_name)
            it_name.setFont(bold_font)
            it_name.setForeground(QColor("#F0F6FC"))
            self.tbl_live.setItem(row_idx, 1, it_name)

            # 2: PAN
            it_pan = QTableWidgetItem(r.get("PAN") or "-")
            it_pan.setFont(bold_font)
            it_pan.setTextAlignment(Qt.AlignCenter)
            it_pan.setForeground(QColor("#58A6FF"))
            self.tbl_live.setItem(row_idx, 2, it_pan)

            # 3: GSTIN
            it_gst = QTableWidgetItem(r.get("GSTIN") or "-")
            it_gst.setFont(font_item)
            it_gst.setTextAlignment(Qt.AlignCenter)
            it_gst.setForeground(QColor("#C9D1D9"))
            self.tbl_live.setItem(row_idx, 3, it_gst)

            # 4: Portal
            it_portal = QTableWidgetItem(r.get("Portal") or "-")
            it_portal.setFont(font_item)
            it_portal.setTextAlignment(Qt.AlignCenter)
            self.tbl_live.setItem(row_idx, 4, it_portal)

            # 5: Filing Preference
            pref = r.get("Filing Preference") or "-"
            it_pref = QTableWidgetItem(pref)
            it_pref.setFont(font_item)
            it_pref.setTextAlignment(Qt.AlignCenter)
            if pref == "Quarterly":
                it_pref.setForeground(QColor("#79C0FF"))
            elif pref == "Monthly":
                it_pref.setForeground(QColor("#D2A8FF"))
            else:
                it_pref.setForeground(QColor("#8B949E"))
            self.tbl_live.setItem(row_idx, 5, it_pref)

            # 6: Filing Type
            it_form = QTableWidgetItem(r.get("Filing Type") or "-")
            it_form.setFont(bold_font)
            it_form.setTextAlignment(Qt.AlignCenter)
            it_form.setForeground(QColor("#4CF9B7"))
            self.tbl_live.setItem(row_idx, 6, it_form)

            # 7: Filing Period
            it_period = QTableWidgetItem(r.get("Filing Period") or "-")
            it_period.setFont(font_item)
            it_period.setTextAlignment(Qt.AlignCenter)
            self.tbl_live.setItem(row_idx, 7, it_period)

            # 8: Submit Status
            st = r.get("Submit Status") or "Not submitted"
            it_st = QTableWidgetItem(st)
            it_st.setFont(bold_font)
            it_st.setTextAlignment(Qt.AlignCenter)
            if st == "Submitted & E-verified":
                it_st.setForeground(QColor("#39FF14"))
                it_st.setBackground(QColor("#1A382B"))
            elif st == "Submitted (e-verification pending)":
                it_st.setForeground(QColor("#F1E05A"))
                it_st.setBackground(QColor("#382F1A"))
            elif st == "Other EVC":
                it_st.setForeground(QColor("#58A6FF"))
                it_st.setBackground(QColor("#16283D"))
            elif st in ("Option Expired (NA)", "Not Applicable (NA)"):
                it_st.setForeground(QColor("#8B949E"))
                it_st.setBackground(QColor("#21262D"))
            else:
                it_st.setForeground(QColor("#FF6B6B"))
                it_st.setBackground(QColor("#381A1A"))
            self.tbl_live.setItem(row_idx, 8, it_st)

            # 9: ARN
            arn_val = r.get("ARN") or "-"
            it_arn = QTableWidgetItem(arn_val)
            it_arn.setFont(font_item)
            it_arn.setTextAlignment(Qt.AlignCenter)
            if arn_val != "-":
                it_arn.setForeground(QColor("#39FF14"))
            else:
                it_arn.setForeground(QColor("#8B949E"))
            self.tbl_live.setItem(row_idx, 9, it_arn)

            # 10: Due Date
            it_due = QTableWidgetItem(r.get("Due Date") or "-")
            it_due.setFont(font_item)
            it_due.setTextAlignment(Qt.AlignCenter)
            self.tbl_live.setItem(row_idx, 10, it_due)

            # 11: Compliance / Aging Alert
            alert = r.get("Compliance Alert") or "-"
            it_alert = QTableWidgetItem(alert)
            it_alert.setFont(bold_font)
            it_alert.setTextAlignment(Qt.AlignCenter)
            if "Overdue" in alert or "🚨" in alert:
                it_alert.setForeground(QColor("#FF7B72"))
                it_alert.setBackground(QColor("#381A1A"))
            elif "Soon" in alert or "⚠️" in alert:
                it_alert.setForeground(QColor("#E3B341"))
                it_alert.setBackground(QColor("#382F1A"))
            elif "Complied" in alert:
                it_alert.setForeground(QColor("#39FF14"))
            elif "Not Applicable" in alert or "NA" in alert:
                it_alert.setForeground(QColor("#8B949E"))
                it_alert.setBackground(QColor("#21262D"))
            self.tbl_live.setItem(row_idx, 11, it_alert)

            # 12: Discrepancy Note
            disc = r.get("Discrepancy Note") or "-"
            it_disc = QTableWidgetItem(disc)
            it_disc.setFont(bold_font if disc != "-" else font_item)
            if disc != "-":
                it_disc.setForeground(QColor("#FF7B72"))
            else:
                it_disc.setForeground(QColor("#8B949E"))
            self.tbl_live.setItem(row_idx, 12, it_disc)

            # 13: Last Updated
            lu = str(r.get("Last Updated") or "-")[:19].replace("T", " ")
            it_lu = QTableWidgetItem(lu)
            it_lu.setFont(font_item)
            it_lu.setTextAlignment(Qt.AlignCenter)
            it_lu.setForeground(QColor("#8B949E"))
            self.tbl_live.setItem(row_idx, 13, it_lu)

        self.tbl_live.resizeColumnsToContents()
        self.tbl_live.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.lbl_table_count.setText(f"Showing: {len(self._filtered_data)} of {len(self._data)} records")

    def _render_matrices(self):
        """Renders the GST and Income Tax compliance grid matrices."""
        font_item = QFont("Segoe UI", 9)
        bold_font = QFont("Segoe UI", 9, QFont.Bold)

        # 1. GST Matrix (Clients vs Periods)
        gst_records = [d for d in self._data if d.get("Portal") == "GST Portal"]
        gst_clients = {}
        for d in gst_records:
            gstin = d.get("GSTIN") or ""
            if gstin and gstin not in gst_clients:
                gst_clients[gstin] = d.get("Client Name") or ""

        gst_periods = sorted(list({d.get("Filing Period") for d in gst_records if d.get("Filing Period")}), reverse=True)

        gst_cols = ["GSTIN", "Client Name"] + gst_periods
        self.tbl_gst_matrix.clear()
        self.tbl_gst_matrix.setColumnCount(len(gst_cols))
        self.tbl_gst_matrix.setHorizontalHeaderLabels(gst_cols)
        self.tbl_gst_matrix.setRowCount(len(gst_clients))

        for r_idx, (gstin, cname) in enumerate(gst_clients.items()):
            it_g = QTableWidgetItem(gstin)
            it_g.setFont(bold_font)
            it_g.setTextAlignment(Qt.AlignCenter)
            it_g.setForeground(QColor("#58A6FF"))
            self.tbl_gst_matrix.setItem(r_idx, 0, it_g)

            it_n = QTableWidgetItem(cname or "-")
            it_n.setFont(bold_font)
            it_n.setForeground(QColor("#F0F6FC"))
            self.tbl_gst_matrix.setItem(r_idx, 1, it_n)

            for p_idx, period in enumerate(gst_periods, start=2):
                matches = [d for d in gst_records if d.get("GSTIN") == gstin and d.get("Filing Period") == period]
                if matches:
                    parts = []
                    has_verified = False
                    has_pending = False
                    has_not_sub = False
                    has_na = False
                    for m in matches:
                        ft = m.get("Filing Type", "")
                        f_short = "G1" if "1" in ft else ("3B" if "3B" in ft else ft)
                        st = m.get("Submit Status", "")
                        if "verified" in st.lower():
                            parts.append(f"{f_short}:✓")
                            has_verified = True
                        elif "pending" in st.lower():
                            parts.append(f"{f_short}:⏳")
                            has_pending = True
                        elif "na" in st.lower() or "not applicable" in st.lower() or "option expired" in st.lower():
                            parts.append(f"{f_short}:NA")
                            has_na = True
                        else:
                            parts.append(f"{f_short}:✗")
                            has_not_sub = True

                    cell_txt = " | ".join(parts)
                    it_cell = QTableWidgetItem(cell_txt)
                    it_cell.setFont(bold_font)
                    it_cell.setTextAlignment(Qt.AlignCenter)
                    if has_not_sub:
                        it_cell.setForeground(QColor("#FF7B72"))
                        it_cell.setBackground(QColor("#381A1A"))
                    elif has_pending:
                        it_cell.setForeground(QColor("#F1E05A"))
                        it_cell.setBackground(QColor("#382F1A"))
                    elif has_verified:
                        it_cell.setForeground(QColor("#39FF14"))
                        it_cell.setBackground(QColor("#1A382B"))
                    elif has_na:
                        it_cell.setForeground(QColor("#8B949E"))
                        it_cell.setBackground(QColor("#21262D"))
                    self.tbl_gst_matrix.setItem(r_idx, p_idx, it_cell)
                else:
                    it_empty = QTableWidgetItem("-")
                    it_empty.setTextAlignment(Qt.AlignCenter)
                    it_empty.setForeground(QColor("#484F58"))
                    self.tbl_gst_matrix.setItem(r_idx, p_idx, it_empty)

        self.tbl_gst_matrix.resizeColumnsToContents()

        # 2. Income Tax Matrix (Clients vs AY)
        it_records = [d for d in self._data if d.get("Portal") == "Income Tax (ITD)"]
        it_clients = {}
        for d in it_records:
            pan = d.get("PAN") or ""
            if pan and pan not in it_clients:
                it_clients[pan] = d.get("Client Name") or ""

        it_periods = sorted(list({d.get("Filing Period") for d in it_records if d.get("Filing Period")}), reverse=True)
        it_cols = ["PAN", "Client Name"] + it_periods
        self.tbl_it_matrix.clear()
        self.tbl_it_matrix.setColumnCount(len(it_cols))
        self.tbl_it_matrix.setHorizontalHeaderLabels(it_cols)
        self.tbl_it_matrix.setRowCount(len(it_clients))

        for r_idx, (pan, cname) in enumerate(it_clients.items()):
            it_p = QTableWidgetItem(pan)
            it_p.setFont(bold_font)
            it_p.setTextAlignment(Qt.AlignCenter)
            it_p.setForeground(QColor("#58A6FF"))
            self.tbl_it_matrix.setItem(r_idx, 0, it_p)

            it_n = QTableWidgetItem(cname or "-")
            it_n.setFont(bold_font)
            it_n.setForeground(QColor("#F0F6FC"))
            self.tbl_it_matrix.setItem(r_idx, 1, it_n)

            for p_idx, period in enumerate(it_periods, start=2):
                matches = [d for d in it_records if d.get("PAN") == pan and d.get("Filing Period") == period]
                if matches:
                    m = matches[0]
                    form_name = m.get("Filing Type") or "ITR"
                    st = m.get("Submit Status") or ""
                    badge_txt = f"{form_name}: {st}"
                    it_cell = QTableWidgetItem(badge_txt)
                    it_cell.setFont(bold_font)
                    it_cell.setTextAlignment(Qt.AlignCenter)
                    if "verified" in st.lower():
                        it_cell.setForeground(QColor("#39FF14"))
                        it_cell.setBackground(QColor("#1A382B"))
                    elif "pending" in st.lower():
                        it_cell.setForeground(QColor("#F1E05A"))
                        it_cell.setBackground(QColor("#382F1A"))
                    else:
                        it_cell.setForeground(QColor("#FF7B72"))
                        it_cell.setBackground(QColor("#381A1A"))
                    self.tbl_it_matrix.setItem(r_idx, p_idx, it_cell)
                else:
                    it_empty = QTableWidgetItem("-")
                    it_empty.setTextAlignment(Qt.AlignCenter)
                    it_empty.setForeground(QColor("#484F58"))
                    self.tbl_it_matrix.setItem(r_idx, p_idx, it_empty)

        self.tbl_it_matrix.resizeColumnsToContents()

    def _render_defaulters(self):
        """Renders the Action-Required & Defaulters tab."""
        defaulters = [
            d for d in self._data
            if d.get("Submit Status") not in ("Option Expired (NA)", "Not Applicable (NA)", "Submitted & E-verified") and
               ("Overdue" in d.get("Compliance Alert", "") or 
                "Expiring" in d.get("Compliance Alert", "") or 
                bool(d.get("Discrepancy Note")) or
                d.get("Submit Status") == "Not submitted")
        ]

        cols = ["Client Name", "PAN", "GSTIN", "Portal", "Filing Type", "Period", "Submit Status", "Alert Reason", "Discrepancy"]
        self.tbl_defaulters.clear()
        self.tbl_defaulters.setColumnCount(len(cols))
        self.tbl_defaulters.setHorizontalHeaderLabels(cols)
        self.tbl_defaulters.setRowCount(len(defaulters))

        font_item = QFont("Segoe UI", 9)
        bold_font = QFont("Segoe UI", 9, QFont.Bold)

        for row_idx, d in enumerate(defaulters):
            it_name = QTableWidgetItem(d.get("Client Name") or "-")
            it_name.setFont(bold_font)
            it_name.setForeground(QColor("#F0F6FC"))
            self.tbl_defaulters.setItem(row_idx, 0, it_name)

            it_pan = QTableWidgetItem(d.get("PAN") or "-")
            it_pan.setFont(bold_font)
            it_pan.setTextAlignment(Qt.AlignCenter)
            it_pan.setForeground(QColor("#58A6FF"))
            self.tbl_defaulters.setItem(row_idx, 1, it_pan)

            it_gst = QTableWidgetItem(d.get("GSTIN") or "-")
            it_gst.setFont(font_item)
            it_gst.setTextAlignment(Qt.AlignCenter)
            self.tbl_defaulters.setItem(row_idx, 2, it_gst)

            it_portal = QTableWidgetItem(d.get("Portal") or "-")
            it_portal.setFont(font_item)
            it_portal.setTextAlignment(Qt.AlignCenter)
            self.tbl_defaulters.setItem(row_idx, 3, it_portal)

            it_form = QTableWidgetItem(d.get("Filing Type") or "-")
            it_form.setFont(bold_font)
            it_form.setTextAlignment(Qt.AlignCenter)
            it_form.setForeground(QColor("#4CF9B7"))
            self.tbl_defaulters.setItem(row_idx, 4, it_form)

            it_period = QTableWidgetItem(d.get("Filing Period") or "-")
            it_period.setFont(font_item)
            it_period.setTextAlignment(Qt.AlignCenter)
            self.tbl_defaulters.setItem(row_idx, 5, it_period)

            it_st = QTableWidgetItem(d.get("Submit Status") or "-")
            it_st.setFont(bold_font)
            it_st.setTextAlignment(Qt.AlignCenter)
            it_st.setForeground(QColor("#FF7B72"))
            it_st.setBackground(QColor("#381A1A"))
            self.tbl_defaulters.setItem(row_idx, 6, it_st)

            it_alert = QTableWidgetItem(d.get("Compliance Alert") or "-")
            it_alert.setFont(bold_font)
            it_alert.setTextAlignment(Qt.AlignCenter)
            it_alert.setForeground(QColor("#F1E05A"))
            self.tbl_defaulters.setItem(row_idx, 7, it_alert)

            it_disc = QTableWidgetItem(d.get("Discrepancy Note") or "-")
            it_disc.setFont(bold_font)
            it_disc.setForeground(QColor("#FF7B72"))
            self.tbl_defaulters.setItem(row_idx, 8, it_disc)

        self.tbl_defaulters.resizeColumnsToContents()
        self.tbl_defaulters.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.lbl_defaulters_banner.setText(f"Found {len(defaulters)} filings requiring immediate compliance intervention.")

    def _reset_filters(self):
        """Resets all search and filter dropdowns."""
        self.txt_search.blockSignals(True)
        self.txt_search.clear()
        self.txt_search.blockSignals(False)

        self.cmb_portal.blockSignals(True)
        self.cmb_portal.setCurrentIndex(0)
        self.cmb_portal.blockSignals(False)

        self.cmb_status.blockSignals(True)
        self.cmb_status.setCurrentIndex(0)
        self.cmb_status.blockSignals(False)

        self.cmb_period.blockSignals(True)
        self.cmb_period.setCurrentIndex(0)
        self.cmb_period.blockSignals(False)

        self.chk_action_only.blockSignals(True)
        self.chk_action_only.setChecked(False)
        self.chk_action_only.blockSignals(False)

        self._apply_filters()

    def _on_table_context_menu(self, pos):
        """Context menu for copying identifiers and records."""
        curr_row = self.tbl_live.currentRow()
        if curr_row < 0 or curr_row >= len(self._filtered_data):
            return

        rec = self._filtered_data[curr_row]
        menu = QMenu(self)

        act_pan = menu.addAction(_safe_qta_icon("mdi.content-copy") or "", f"Copy PAN: {rec.get('PAN')}")
        act_pan.triggered.connect(lambda: QGuiApplication.clipboard().setText(rec.get("PAN", "")))

        if rec.get("GSTIN"):
            act_gst = menu.addAction(_safe_qta_icon("mdi.content-copy") or "", f"Copy GSTIN: {rec.get('GSTIN')}")
            act_gst.triggered.connect(lambda: QGuiApplication.clipboard().setText(rec.get("GSTIN", "")))

        if rec.get("ARN"):
            act_arn = menu.addAction(_safe_qta_icon("mdi.identifier") or "", f"Copy ARN: {rec.get('ARN')}")
            act_arn.triggered.connect(lambda: QGuiApplication.clipboard().setText(rec.get("ARN", "")))

        menu.addSeparator()

        act_reminder = menu.addAction(_safe_qta_icon("mdi.message-text") or "", "Copy Client Compliance Reminder")
        act_reminder.triggered.connect(lambda: self._copy_single_reminder(rec))

        menu.exec_(self.tbl_live.viewport().mapToGlobal(pos))

    def _copy_single_reminder(self, rec):
        """Copies a professional compliance status reminder without exposing private data."""
        c_name = rec.get("Client Name") or "Taxpayer"
        pan = rec.get("PAN") or ""
        form = rec.get("Filing Type") or ""
        period = rec.get("Filing Period") or ""
        status = rec.get("Submit Status") or ""
        due = rec.get("Due Date") or "N/A"
        arn = rec.get("ARN") or "Pending"

        text = (
            f"Dear {c_name},\n\n"
            f"Compliance Status Update:\n"
            f"• Form: {form}\n"
            f"• Period / AY: {period}\n"
            f"• Status: {status}\n"
            f"• ARN / Ref: {arn}\n"
            f"• Due Date: {due}\n\n"
            f"Please verify if any action is pending on your end.\n"
            f"- Aman & Associates"
        )
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", "Compliance reminder copied to clipboard!")

    def _copy_defaulter_summary(self):
        """Copies complete defaulters list to clipboard for team dispatch."""
        defaulters = [
            d for d in self._data
            if d.get("Submit Status") not in ("Option Expired (NA)", "Not Applicable (NA)", "Submitted & E-verified") and
               ("Overdue" in d.get("Compliance Alert", "") or 
                "Expiring" in d.get("Compliance Alert", "") or 
                bool(d.get("Discrepancy Note")) or
                d.get("Submit Status") == "Not submitted")
        ]

        lines = [f"=== LTT ACTION REQUIRED / DEFAULTERS LIST ({len(defaulters)} Records) ==="]
        for idx, d in enumerate(defaulters, start=1):
            c_name = d.get("Client Name") or "Unknown"
            pan = d.get("PAN") or ""
            gstin = d.get("GSTIN") or ""
            id_str = gstin if gstin else pan
            form = d.get("Filing Type") or ""
            period = d.get("Filing Period") or ""
            alert = d.get("Compliance Alert") or d.get("Submit Status") or ""
            disc = f" [{d.get('Discrepancy Note')}]" if d.get("Discrepancy Note") else ""
            lines.append(f"{idx}. {c_name} ({id_str}) - {form} {period} -> {alert}{disc}")

        summary_text = "\n".join(lines)
        QGuiApplication.clipboard().setText(summary_text)
        QMessageBox.information(self, "Copied", f"Copied {len(defaulters)} defaulter entries to clipboard!")

    def _on_row_double_clicked(self, row, col):
        """Opens a detailed record inspection dialog."""
        if row < 0 or row >= len(self._filtered_data):
            return
        rec = self._filtered_data[row]
        
        dlg = QDialog(self)
        dlg.setWindowTitle(f"LTT Record Details - {rec.get('Client Name', '')} ({rec.get('Filing Type', '')})")
        dlg.resize(600, 480)
        dlg.setStyleSheet("background-color: #0D1117; color: #F0F6FC;")

        vbox = QVBoxLayout(dlg)
        vbox.setContentsMargins(16, 16, 16, 16)
        vbox.setSpacing(10)

        title = QLabel(f"Compliance Record: {rec.get('Client Name', 'N/A')}")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #58A6FF;")
        vbox.addWidget(title)

        info_box = QFrame()
        info_box.setStyleSheet("background-color: #161B22; border: 1px solid #30363D; border-radius: 6px; padding: 12px;")
        info_layout = QVBoxLayout(info_box)
        info_layout.setSpacing(8)

        fields = [
            ("Client Name", rec.get("Client Name")),
            ("PAN", rec.get("PAN")),
            ("GSTIN", rec.get("GSTIN") or "-"),
            ("Portal", rec.get("Portal")),
            ("Filing Preference", rec.get("Filing Preference") or "Regular / Non-QRMP"),
            ("Filing Type", rec.get("Filing Type")),
            ("Filing Period", rec.get("Filing Period")),
            ("Submit Status", rec.get("Submit Status")),
            ("ARN / Acknowledgment", rec.get("ARN") or "Not Available"),
            ("Due Date", rec.get("Due Date") or "N/A"),
            ("Compliance / Aging Alert", rec.get("Compliance Alert") or "-"),
            ("Discrepancy Note", rec.get("Discrepancy Note") or "-"),
            ("Session ID", rec.get("Session ID")),
            ("Last Updated", rec.get("Last Updated")),
            ("Site History / Route", rec.get("Site History"))
        ]

        for k, v in fields:
            row_l = QHBoxLayout()
            lbl_k = QLabel(f"{k}:")
            lbl_k.setStyleSheet("color: #8B949E; font-weight: 600; min-width: 160px;")
            lbl_v = QLabel(str(v or "-"))
            lbl_v.setStyleSheet("color: #F0F6FC; font-weight: 700;")
            lbl_v.setWordWrap(True)
            row_l.addWidget(lbl_k)
            row_l.addWidget(lbl_v, stretch=1)
            info_layout.addLayout(row_l)

        vbox.addWidget(info_box)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_c = QPushButton("Close")
        btn_c.setProperty("class", "ActionBtn")
        btn_c.clicked.connect(dlg.accept)
        btn_box.addWidget(btn_c)
        vbox.addLayout(btn_box)

        dlg.exec_()

    def _export_excel(self):
        """Generates and opens the enhanced multi-sheet LTT Excel workbook."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            import sdc_parser
            out_file = sdc_parser.generate_ltt_excel()
            QApplication.restoreOverrideCursor()
            if out_file and os.path.exists(out_file):
                QMessageBox.information(
                    self, "Export Complete",
                    f"Enhanced Multi-Sheet LTT Workbook generated successfully!\n\nSaved to:\n{out_file}"
                )
                try:
                    os.startfile(out_file)
                except Exception:
                    pass
            else:
                QMessageBox.warning(self, "Export Failed", "Parser completed but target file was not created.")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export Error", f"Failed to generate Excel report: {e}")
