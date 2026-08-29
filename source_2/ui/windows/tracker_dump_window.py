"""
tracker_dump_window.py
----------------------
Dedicated workspace window for inspecting, filtering, and managing captured
filing submissions, ARNs, and raw technical payloads received from the browser
extension and Sera_API_detection (SAD).
"""

import json
import csv
from pathlib import Path
from datetime import datetime, timezone
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QClipboard
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QDialog, QTextEdit, QTextBrowser, QFrame,
    QFileDialog, QScrollArea, QFormLayout, QCheckBox, QTabWidget,
    QApplication
)

from ui.utils.profile_parser import extract_profile_from_payload, map_profile_to_mcl_columns

try:
    import qtawesome as qta
except Exception:
    qta = None


def _format_to_local_time(iso_str: str) -> str:
    """Converts a UTC/ISO timestamp string to local time (IST) in 'YYYY-MM-DD HH:MM:SS' format."""
    if not iso_str:
        return ""
    clean = str(iso_str).strip()
    if not clean:
        return ""
    try:
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return clean[:19].replace("T", " ")


def _safe_qta_icon(icon_name, color="#FFFFFF"):
    if qta is not None:
        try:
            return qta.icon(icon_name, color=color)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()


class AddClientFromCaptureDialog(QDialog):
    """Modal dialog allowing quick 1-click creation of a client record directly from an unassigned SAD capture."""
    def __init__(self, db, item_data: dict, parent=None):
        super().__init__(parent)
        self.db = db
        self.item_data = item_data
        self.created_client_id = None
        self.setWindowTitle("Create Client from Capture — Project Sera")
        self.setModal(True)
        self.resize(520, 580)
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1A1A1A;
                color: #F8F5F2;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel {
                color: #E6EDF3;
                font-size: 12px;
            }
            QLineEdit {
                background-color: #0D1117;
                border: 1px solid #30363D;
                border-radius: 5px;
                color: #F0F6FC;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #2E9B5F;
            }
            QPushButton.PrimaryBtn {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: 700;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton.PrimaryBtn:hover {
                background-color: #247C4C;
            }
            QPushButton.CancelBtn {
                background-color: #262626;
                color: #A0A0A0;
                border: 1px solid #444444;
                border-radius: 5px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton.CancelBtn:hover {
                background-color: #333333;
                color: #FFFFFF;
            }
            QCheckBox {
                color: #F0F6FC;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # Header Info Banner
        header = QFrame()
        header.setStyleSheet("background-color: #0A0A0A; border: 1px solid #2E9B5F; border-radius: 6px; padding: 10px;")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(8, 8, 8, 8)
        h_layout.setSpacing(4)

        title_lbl = QLabel("<b>⚡ Quick Onboard from Government Network Capture</b>")
        title_lbl.setStyleSheet("color: #4CF9B7; font-size: 13.5px;")
        desc_lbl = QLabel(f"Portal: <b>{self.item_data.get('portal', 'Government Portal')}</b> | ARN: <b>{self.item_data.get('arn_number', 'N/A')}</b>")
        desc_lbl.setStyleSheet("color: #A0A0A0; font-size: 11.5px;")
        h_layout.addWidget(title_lbl)
        h_layout.addWidget(desc_lbl)
        layout.addWidget(header)

        # Form Scroll Area for MCL Fields
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #30363D; border-radius: 6px; background-color: #121212; }")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #121212;")
        form = QFormLayout(scroll_content)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        # Extract profile from SRPF container or current capture payload
        extracted_info = self._extract_info_from_payload()
        mcl_cols = self.db.get_mcl_columns()
        mapped_values = map_profile_to_mcl_columns(extracted_info, mcl_cols)

        self.field_inputs = {}
        
        for col in mcl_cols:
            col_id = col["id"]
            lbl_text = col["label"]
            is_pk = col.get("is_internal_pk", False)
            field_type = col.get("field_type", "text")

            # Determine prefill value
            prefill_val = ""
            if field_type == "id":
                prefill_val = str(self._get_next_serial_no())
            else:
                prefill_val = mapped_values.get(col_id, "")

            # Form Label with mandatory badge if Internal PK
            field_label_widget = QLabel()
            if is_pk:
                field_label_widget.setText(f"<span style='color:#FF6B6B;'>*</span> <b>{lbl_text}</b> <span style='color:#4CF9B7; font-size:10px;'>[Internal PK]</span>")
            else:
                field_label_widget.setText(lbl_text)
            field_label_widget.setTextFormat(Qt.RichText)

            inp = QLineEdit(prefill_val)
            self.field_inputs[col_id] = (inp, is_pk, lbl_text)
            form.addRow(field_label_widget, inp)

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        # Service Attachments
        svc_frame = QFrame()
        svc_frame.setStyleSheet("background-color: #121212; border: 1px solid #30363D; border-radius: 6px; padding: 8px;")
        svc_layout = QVBoxLayout(svc_frame)
        svc_layout.setContentsMargins(6, 6, 6, 6)
        svc_layout.setSpacing(6)
        svc_lbl = QLabel("<b>Auto-Attach Compliance Services:</b>")
        svc_lbl.setStyleSheet("color: #E6EDF3; font-size: 11.5px;")
        svc_layout.addWidget(svc_lbl)

        svc_checks_box = QHBoxLayout()
        self.svc_checkboxes = {}
        all_services = self.db.get_services()
        portal_name = (self.item_data.get("portal", "") or "").lower()

        for s in all_services:
            cb = QCheckBox(s["name"])
            # Auto-check matching portal service
            if s["name"].lower() in portal_name or portal_name in s["name"].lower() or ("income" in portal_name and "income" in s["name"].lower()):
                cb.setChecked(True)
            self.svc_checkboxes[s["id"]] = cb
            svc_checks_box.addWidget(cb)
        svc_checks_box.addStretch()
        svc_layout.addLayout(svc_checks_box)
        layout.addWidget(svc_frame)

        # Button Box
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setProperty("class", "CancelBtn")
        btn_cancel.clicked.connect(self.reject)

        btn_save = QPushButton("Save & Link Captures")
        btn_save.setProperty("class", "PrimaryBtn")
        btn_save.setIcon(_safe_qta_icon("mdi.check-circle", "#FFFFFF"))
        btn_save.clicked.connect(self._on_save)

        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)

    def _extract_info_from_payload(self) -> dict:
        from ui.utils.profile_parser import extract_profile_from_payload
        unassigned_key = self.item_data.get("unassigned_identity") or self.item_data.get("pan") or ""
        
        # Check if SRPF container exists in rawPayload.db
        container = self.db.get_client_raw_container(identity_key=unassigned_key) if unassigned_key else None
        if container and (container.get("company_name") or container.get("pan") or container.get("gstin")):
            return container

        # Fallback to current payload extraction
        raw_str = self.item_data.get("raw_payload_json") or "{}"
        parsed = extract_profile_from_payload(raw_str)
        if not parsed.get("pan") and unassigned_key:
            parsed["pan"] = unassigned_key
        return parsed

    def _get_next_serial_no(self) -> int:
        try:
            with self.db._connect() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM clients")
                return cur.fetchone()[0] + 1
        except Exception:
            return 1

    def _on_save(self):
        values = {}
        pan_val = ""
        for col_id, (inp, is_pk, label) in self.field_inputs.items():
            val = inp.text().strip()
            if is_pk:
                if not val:
                    QMessageBox.warning(self, "Mandatory Field Required", f"The Internal Primary Key '{label}' is mandatory and cannot be empty.")
                    inp.setFocus()
                    return
                pan_val = val
            values[col_id] = val

        service_ids = [sid for sid, cb in self.svc_checkboxes.items() if cb.isChecked()]

        try:
            new_cid = self.db.add_client(values=values, notes=f"Auto-created from Tracker Dump capture (ARN: {self.item_data.get('arn_number', 'N/A')})", service_ids=service_ids, actor="Staff")
            self.created_client_id = new_cid
            
            # Retroactively link all unassigned tracker dumps matching this identity
            linked_count = self.db.link_unassigned_tracker_dumps(new_cid, pan_val or self.item_data.get("unassigned_identity") or "")
            QMessageBox.information(
                self, "Client Created Successfully",
                f"Client #{new_cid} was created and successfully linked to {max(linked_count, 1)} capture(s) in Tracker Dump."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Client Creation Failed", f"Could not create client: {e}")


class PayloadInspectorDialog(QDialog):
    """Modal dialog displaying formatted profile details, filing history, and raw technical JSON."""
    def __init__(self, item_data: dict, db=None, is_container: bool = False, parent=None):
        super().__init__(parent)
        self.db = db
        self.item_data = item_data
        self.is_container = is_container or bool(item_data.get("filing_history"))
        title_tag = item_data.get('identity_key') or item_data.get('arn_number') or 'Capture'
        self.setWindowTitle(f"SRPF Container Inspector — {title_tag}")
        self.resize(750, 580)
        self.setStyleSheet("""
            QDialog {
                background-color: #121212;
                color: #F8F5F2;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel {
                color: #F8F5F2;
                font-size: 12px;
            }
            QTabWidget::pane {
                border: 1px solid #30363D;
                background-color: #161B22;
                border-radius: 6px;
            }
            QTabBar::tab {
                background-color: #0D1117;
                color: #8B949E;
                padding: 8px 16px;
                border: 1px solid #30363D;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background-color: #161B22;
                color: #4CF9B7;
                border-bottom: 2px solid #2E9B5F;
            }
            QTextEdit {
                background-color: #0A0A0A;
                color: #39FF14;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid #2E9B5F;
                border-radius: 6px;
                padding: 10px;
            }
            QTableWidget {
                background-color: #0D1117;
                gridline-color: #21262D;
                border: none;
                color: #F0F6FC;
            }
            QHeaderView::section {
                background-color: #161B22;
                color: #4CF9B7;
                font-weight: 700;
                font-size: 11.5px;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #2E9B5F;
            }
            QPushButton {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #247C4C;
            }
            QPushButton.SecondaryBtn {
                background-color: #262626;
                color: #E6EDF3;
                border: 1px solid #444444;
            }
            QPushButton.SecondaryBtn:hover {
                background-color: #333333;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Info Banner
        header = QFrame()
        header.setStyleSheet("background-color: #0D1117; border: 1px solid #30363D; border-radius: 6px; padding: 10px;")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(8, 8, 8, 8)
        h_layout.setSpacing(4)

        client_name = item_data.get('display_name') or item_data.get('client_name') or item_data.get('company_name') or "Client Container"
        is_unreg = item_data.get('is_unassigned') or not item_data.get('client_id')
        name_color = "#FFA657" if is_unreg else "#4CF9B7"

        title_lbl = QLabel(f"<b>Client:</b> <span style='color:{name_color}; font-size:13px;'>{client_name}</span> &nbsp;|&nbsp; <b>Identity Key:</b> <span style='color:#FFFFFF;'>{item_data.get('pan') or item_data.get('identity_key') or 'N/A'}</span>")
        title_lbl.setTextFormat(Qt.RichText)

        sub_info = f"<b>Total Captures:</b> {item_data.get('total_captures', 1)} &nbsp;|&nbsp; <b>Portal:</b> {item_data.get('portal', 'Government Portal')} &nbsp;|&nbsp; <b>Last Updated:</b> {_format_to_local_time(item_data.get('last_updated') or item_data.get('created_at'))}"
        sub_lbl = QLabel(sub_info)
        sub_lbl.setTextFormat(Qt.RichText)
        sub_lbl.setStyleSheet("color: #8B949E; font-size: 11.5px;")

        h_layout.addWidget(title_lbl)
        h_layout.addWidget(sub_lbl)
        layout.addWidget(header)

        # Tab Widget
        tabs = QTabWidget()

        # Tab 1: Profile & Filing History
        tab_summary = QWidget()
        sum_layout = QVBoxLayout(tab_summary)
        sum_layout.setContentsMargins(10, 10, 10, 10)
        sum_layout.setSpacing(10)

        # Extracted Profile Key-Value Cards
        profile_frame = QFrame()
        profile_frame.setStyleSheet("background-color: #0D1117; border: 1px solid #21262D; border-radius: 6px; padding: 8px;")
        pf_layout = QFormLayout(profile_frame)
        pf_layout.setSpacing(6)

        def _add_pf_row(lbl, val):
            if val and str(val).strip():
                l_widget = QLabel(f"<b>{lbl}:</b>")
                l_widget.setStyleSheet("color: #8B949E;")
                v_widget = QLabel(str(val))
                v_widget.setStyleSheet("color: #F0F6FC; font-weight: 600;")
                pf_layout.addRow(l_widget, v_widget)

        _add_pf_row("Firm / Trade Name", item_data.get("company_name"))
        _add_pf_row("Proprietor Name", item_data.get("proprietor_name"))
        _add_pf_row("PAN", item_data.get("pan") or item_data.get("identity_key"))
        _add_pf_row("GSTIN", item_data.get("gstin"))
        _add_pf_row("TAN", item_data.get("tan"))
        _add_pf_row("Primary Mobile", item_data.get("phone"))
        _add_pf_row("Primary Email", item_data.get("email"))
        _add_pf_row("DOB / Incorporation", item_data.get("dob"))
        _add_pf_row("Portal User ID", item_data.get("user_id"))

        sum_layout.addWidget(profile_frame)

        # Filing History Table
        filing_history = item_data.get("filing_history") or []
        if not filing_history and item_data.get("arn_number"):
            filing_history = [{
                "portal": item_data.get("portal"),
                "arn": item_data.get("arn_number"),
                "period_label": item_data.get("period_label"),
                "capture_method": item_data.get("capture_method"),
                "created_at": item_data.get("created_at")
            }]

        hist_lbl = QLabel(f"<b>Captured Filings & Obligations ({len(filing_history)}):</b>")
        hist_lbl.setStyleSheet("color: #4CF9B7; font-size: 12px; margin-top: 4px;")
        sum_layout.addWidget(hist_lbl)

        hist_table = QTableWidget()
        hist_table.setColumnCount(5)
        hist_table.setHorizontalHeaderLabels(["Period / AY", "ARN / Ack Number", "Portal", "Capture Method", "Timestamp"])
        hist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        hist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hist_table.setRowCount(len(filing_history))

        for idx, fh in enumerate(reversed(filing_history)):
            p_item = QTableWidgetItem(fh.get("period_label") or "N/A")
            p_item.setForeground(QColor("#D29922"))
            hist_table.setItem(idx, 0, p_item)

            arn_item = QTableWidgetItem(fh.get("arn") or "N/A")
            arn_item.setFont(QFont("Consolas", 10, QFont.Bold))
            arn_item.setForeground(QColor("#39FF14"))
            hist_table.setItem(idx, 1, arn_item)

            port_item = QTableWidgetItem(fh.get("portal") or "Income Tax")
            port_item.setForeground(QColor("#E6EDF3"))
            hist_table.setItem(idx, 2, port_item)

            m_item = QTableWidgetItem(fh.get("capture_method") or "SAD_API_Interceptor")
            m_item.setForeground(QColor("#4CF9B7"))
            hist_table.setItem(idx, 3, m_item)

            ts_item = QTableWidgetItem(_format_to_local_time(fh.get("created_at") or ""))
            ts_item.setForeground(QColor("#8B949E"))
            hist_table.setItem(idx, 4, ts_item)

        sum_layout.addWidget(hist_table, stretch=1)
        tabs.addTab(tab_summary, "SRPF Unified Container")

        # Tab 2: Raw Technical JSON
        tab_json = QWidget()
        json_layout = QVBoxLayout(tab_json)
        json_layout.setContentsMargins(10, 10, 10, 10)

        self.txt_json = QTextEdit()
        self.txt_json.setReadOnly(True)

        raw_str = item_data.get('raw_payload_json')
        if not raw_str:
            raw_str = json.dumps(item_data, indent=4)
        try:
            parsed = json.loads(raw_str) if isinstance(raw_str, str) else raw_str
            formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
        except Exception:
            formatted = str(raw_str)

        self.txt_json.setText(formatted)
        json_layout.addWidget(self.txt_json)
        tabs.addTab(tab_json, "Raw Intercepted JSON")

        # Tab 3: Timeline (Session Interaction Flow Diagram)
        from ui.utils.timeline_decoder import group_captures_into_sessions, format_timeline_flow_html, format_timeline_flow_plain

        tab_timeline = QWidget()
        tl_layout = QVBoxLayout(tab_timeline)
        tl_layout.setContentsMargins(10, 10, 10, 10)

        self.txt_timeline = QTextBrowser()
        self.txt_timeline.setReadOnly(True)
        self.txt_timeline.setOpenLinks(False)
        self.expanded_repeats = set()

        captures = []
        if self.db and hasattr(self.db, "get_captures_for_container"):
            cid = item_data.get("client_id")
            pan_val = item_data.get("pan")
            ikey = item_data.get("identity_key")
            try:
                captures = self.db.get_captures_for_container(identity_key=ikey, client_id=cid, pan=pan_val)
            except Exception:
                captures = []

        # Also merge any dedicated SDC session timelines for this client/PAN
        if self.db and hasattr(self.db, "get_sdc_session_timelines"):
            try:
                sdc_tls = self.db.get_sdc_session_timelines(client_id=item_data.get("client_id"), pan=item_data.get("pan"))
                for st in sdc_tls:
                    captures.append({
                        "session_id": st.get("session_id"),
                        "client_id": st.get("client_id"),
                        "pan": st.get("pan"),
                        "client_name": st.get("client_name"),
                        "portal": st.get("portal"),
                        "status": st.get("status"),
                        "timestamp": st.get("start_time"),
                        "created_at": st.get("start_time"),
                        "timeline": st.get("timeline")
                    })
            except Exception:
                pass

        if not captures:
            captures = [item_data]

        decoded_sessions = group_captures_into_sessions(captures)
        self.decoded_timeline_sessions = decoded_sessions
        html_content = format_timeline_flow_html(decoded_sessions, title_tag, expanded_step_uids=self.expanded_repeats)
        self.txt_timeline.setHtml(html_content)

        def _on_timeline_anchor_clicked(url):
            raw_url = url.toString()
            frag = url.fragment()
            full_target = frag if frag else raw_url
            
            if "toggle_repeat" in full_target:
                uid = full_target.replace("#", "").replace("toggle_repeat:", "").replace("toggle_repeat_", "")
                if uid in self.expanded_repeats:
                    self.expanded_repeats.remove(uid)
                else:
                    self.expanded_repeats.add(uid)
                
                sb = self.txt_timeline.verticalScrollBar()
                v_scroll = sb.value() if sb else 0
                
                new_html = format_timeline_flow_html(
                    self.decoded_timeline_sessions,
                    title_tag=title_tag,
                    expanded_step_uids=self.expanded_repeats
                )
                self.txt_timeline.setHtml(new_html)
                if sb:
                    sb.setValue(v_scroll)

        self.txt_timeline.anchorClicked.connect(_on_timeline_anchor_clicked)

        tl_layout.addWidget(self.txt_timeline)
        tabs.addTab(tab_timeline, "Timeline")

        layout.addWidget(tabs, stretch=1)

        # Actions Box
        btn_box = QHBoxLayout()

        if is_unreg and self.db:
            btn_create = QPushButton("+ Create Client from Container")
            btn_create.setIcon(_safe_qta_icon("mdi.account-plus", "#FFFFFF"))
            btn_create.clicked.connect(self._create_client)
            btn_box.addWidget(btn_create)

        btn_box.addStretch()

        btn_copy = QPushButton("Copy Container Summary")
        btn_copy.setProperty("class", "SecondaryBtn")

        def _on_tab_changed(idx):
            if idx == 0:
                btn_copy.setText("Copy Container Summary")
            elif idx == 1:
                btn_copy.setText("Copy JSON")
            elif idx == 2:
                btn_copy.setText("Copy Timeline")

        tabs.currentChanged.connect(_on_tab_changed)

        def _handle_bottom_copy():
            curr_tab = tabs.currentIndex()
            if curr_tab == 0:
                summary_lines = [
                    f"Client: {client_name} | Identity Key: {item_data.get('pan') or item_data.get('identity_key') or 'N/A'}",
                    f"Portal: {item_data.get('portal', 'N/A')} | Captures: {item_data.get('total_captures', 1)}",
                    f"Firm Name: {item_data.get('company_name', '')}",
                    f"Proprietor: {item_data.get('proprietor_name', '')}",
                    f"PAN: {item_data.get('pan', '')}",
                    f"GSTIN: {item_data.get('gstin', '')}",
                    f"Mobile: {item_data.get('phone', '')}",
                    f"Email: {item_data.get('email', '')}",
                    f"Latest ARN: {item_data.get('latest_arn') or item_data.get('arn_number') or 'N/A'}"
                ]
                txt = "\n".join(l for l in summary_lines if l)
                try:
                    QGuiApplication.clipboard().setText(txt)
                except Exception:
                    QApplication.clipboard().setText(txt)
                btn_copy.setText("✓ Copied Summary!")
                QTimer.singleShot(1500, lambda: btn_copy.setText("Copy Container Summary"))
            elif curr_tab == 1:
                self.txt_json.selectAll()
                self.txt_json.copy()
                btn_copy.setText("✓ Copied JSON!")
                QTimer.singleShot(1500, lambda: btn_copy.setText("Copy JSON"))
            elif curr_tab == 2:
                txt = format_timeline_flow_plain(self.decoded_timeline_sessions, title_tag)
                try:
                    QGuiApplication.clipboard().setText(txt)
                except Exception:
                    QApplication.clipboard().setText(txt)
                btn_copy.setText("✓ Copied Timeline!")
                QTimer.singleShot(1500, lambda: btn_copy.setText("Copy Timeline"))

        btn_copy.clicked.connect(_handle_bottom_copy)

        btn_close = QPushButton("Close")
        btn_close.setProperty("class", "SecondaryBtn")
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(btn_copy)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

    def _create_client(self):
        if not self.db:
            return
        dlg = AddClientFromCaptureDialog(self.db, self.item_data, self)
        if dlg.exec() == QDialog.Accepted:
            self.accept()


class TrackerDumpWindow(QWidget):
    """Full-featured workspace for inspecting client tracker dumps and SRPF unified containers."""
    
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._dumps_cache = []
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #292929;
                color: #F8F5F2;
                font-family: 'Segoe UI', sans-serif;
            }
            QFrame#HeaderCard {
                background-color: #0A0A0A;
                border-bottom: 2px solid #2E9B5F;
                border-radius: 0px;
            }
            QLabel#TitleLbl {
                font-size: 18px;
                font-weight: 700;
                color: #F8F5F2;
            }
            QLabel#SubtitleLbl {
                font-size: 12px;
                color: #A0A0A0;
            }
            QLineEdit, QComboBox {
                background-color: #171717;
                border: 1px solid #444444;
                border-radius: 5px;
                color: #F8F5F2;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #2E9B5F;
            }
            QPushButton.ActionBtn {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 5px;
                padding: 7px 14px;
                font-size: 12px;
            }
            QPushButton.ActionBtn:hover {
                background-color: #247C4C;
            }
            QPushButton.DangerBtn {
                background-color: #D9534F;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 5px;
                padding: 7px 14px;
                font-size: 12px;
            }
            QPushButton.DangerBtn:hover {
                background-color: #C9302C;
            }
            QTableWidget {
                background-color: #121212;
                alternate-background-color: #1A1A1A;
                gridline-color: #2D2D2D;
                border: 1px solid #333333;
                border-radius: 6px;
                color: #F0F6FC;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
            }
            QTableWidget::item {
                color: #F0F6FC;
                padding: 8px 10px;
                border-bottom: 1px solid #222222;
            }
            QTableWidget::item:selected {
                background-color: #1F6FEB;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #0D1117;
                color: #4CF9B7;
                font-weight: 700;
                font-size: 12px;
                padding: 8px;
                border: none;
                border-bottom: 2px solid #2E9B5F;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # Top Header Card
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)

        title_vbox = QVBoxLayout()
        lbl_title = QLabel("Tracker Dump Workspace")
        lbl_title.setObjectName("TitleLbl")
        lbl_sub = QLabel("Client-connected filing logs & SRPF unified containers captured via Extension & SAD")
        lbl_sub.setObjectName("SubtitleLbl")
        title_vbox.addWidget(lbl_title)
        title_vbox.addWidget(lbl_sub)
        header_layout.addLayout(title_vbox)

        header_layout.addStretch()

        self.lbl_counter = QLabel("Records: 0")
        self.lbl_counter.setStyleSheet("font-weight: 700; color: #4CF9B7; font-size: 13px; background-color: #1A382B; padding: 6px 12px; border-radius: 4px;")
        header_layout.addWidget(self.lbl_counter)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setProperty("class", "ActionBtn")
        btn_refresh.setIcon(_safe_qta_icon("mdi.refresh", "#FFFFFF"))
        btn_refresh.clicked.connect(self.load_data)
        header_layout.addWidget(btn_refresh)

        self.btn_preferences = QPushButton("Preferences")
        self.btn_preferences.setProperty("class", "ActionBtn")
        self.btn_preferences.setIcon(_safe_qta_icon("mdi.cog-outline", "#FFFFFF"))
        self.btn_preferences.clicked.connect(self._show_preferences_menu)
        header_layout.addWidget(self.btn_preferences)


        main_layout.addWidget(header_card)

        # Search & Filter Controls
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(10)

        # View Mode Selector (SRPF Grouped by Client Container as default)
        self.cmb_view_mode = QComboBox()
        self.cmb_view_mode.addItems(["Grouped by Client Container (SRPF)", "Individual Raw Captures"])
        self.cmb_view_mode.setStyleSheet("font-weight: 700; color: #4CF9B7; background-color: #0D1117; padding: 6px 12px;")
        self.cmb_view_mode.currentIndexChanged.connect(self.load_data)
        filter_layout.addWidget(self.cmb_view_mode, stretch=2)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search Client Name, PAN, GSTIN, ARN, Period, Portal...")
        self.txt_search.textChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.txt_search, stretch=3)

        self.cmb_method = QComboBox()
        self.cmb_method.addItems(["All Capture Methods", "SAD_API_Interceptor", "DOM_Tracker", "Manual_Fallback"])
        self.cmb_method.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.cmb_method, stretch=1)

        self.cmb_status = QComboBox()
        self.cmb_status.addItems(["All Statuses", "submitted", "pending", "uncertain"])
        self.cmb_status.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.cmb_status, stretch=1)

        main_layout.addLayout(filter_layout)

        # Data Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        main_layout.addWidget(self.table)

        # Initial Load
        self.load_data()

    def load_data(self):
        """Fetch tracker dumps or SRPF unified containers from database."""
        try:
            is_grouped = (self.cmb_view_mode.currentIndex() == 0)
            if is_grouped:
                self._dumps_cache = self.db.get_srpf_containers(limit=300)
            else:
                self._dumps_cache = self.db.get_tracker_dumps(limit=300)
            self._apply_filters()
        except Exception as e:
            QMessageBox.critical(self, "Error Loading Dumps", f"Could not load tracker dumps: {e}")

    def _apply_filters(self):
        """Filter cached records and populate table."""
        v_val = self.table.verticalScrollBar().value()
        h_val = self.table.horizontalScrollBar().value()
        curr_row = self.table.currentRow()
        curr_col = self.table.currentColumn()

        search_txt = self.txt_search.text().strip().lower()
        method_filter = self.cmb_method.currentText()
        status_filter = self.cmb_status.currentText()
        is_grouped = (self.cmb_view_mode.currentIndex() == 0)

        filtered = []
        for d in self._dumps_cache:
            if not is_grouped:
                if method_filter != "All Capture Methods" and d.get("capture_method") != method_filter:
                    continue
                if status_filter != "All Statuses" and d.get("status") != status_filter:
                    continue

            if search_txt:
                match_fields = [
                    d.get("display_name", ""), d.get("client_name", ""), d.get("pan", ""),
                    d.get("portal", ""), d.get("service_name", ""),
                    d.get("period_label", ""), d.get("period_summary", ""),
                    d.get("latest_arn", ""), d.get("arn_number", ""),
                    d.get("company_name", ""), d.get("proprietor_name", ""),
                    d.get("identity_key", "")
                ]
                if not any(search_txt in str(f).lower() for f in match_fields):
                    continue

            filtered.append(d)

        self.table.setUpdatesEnabled(False)
        try:
            if is_grouped:
                self._populate_grouped_table(filtered)
            else:
                self._populate_raw_table(filtered)

            if curr_row >= 0 and curr_row < self.table.rowCount() and curr_col >= 0 and curr_col < self.table.columnCount():
                self.table.setCurrentCell(curr_row, curr_col)

            if v_val > 0:
                self.table.verticalScrollBar().setValue(v_val)
            if h_val > 0:
                self.table.horizontalScrollBar().setValue(h_val)
        finally:
            self.table.setUpdatesEnabled(True)

    def _populate_grouped_table(self, containers: list[dict]):
        """Populates table in SRPF Grouped Container view: 1 row per unique client container."""
        # Save user-adjusted column widths if previously set
        prev_widths = [self.table.columnWidth(c) for c in range(self.table.columnCount())] if self.table.columnCount() == 8 else []

        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Client Name & PAN", "Client ID", "Portal / Services", "Filings & History",
            "Latest ARN / Ack", "Actions", "Last Updated", "Capture Method"
        ])
        
        # Allow interactive mouse drag resizing on all columns
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)

        default_widths = [320, 100, 180, 230, 160, 210, 140, 150]
        for c, w in enumerate(prev_widths if len(prev_widths) == 8 and prev_widths[0] > 0 else default_widths):
            self.table.setColumnWidth(c, w)

        self.table.setRowCount(0)
        self.lbl_counter.setText(f"Client Containers: {len(containers)}")

        for row_idx, r in enumerate(containers):
            self.table.insertRow(row_idx)

            # 0. Client Name & PAN (Primary Prominent Column)
            disp_name = r.get('display_name') or r.get('company_name') or r.get('proprietor_name') or f"Unregistered ({r.get('identity_key')})"
            c_item = QTableWidgetItem(disp_name)
            c_item.setFont(QFont("Segoe UI", 10.5, QFont.Bold))
            c_item.setToolTip(disp_name)
            if r.get('is_unassigned'):
                c_item.setForeground(QColor("#FFA657"))
            else:
                c_item.setForeground(QColor("#FFFFFF"))
            self.table.setItem(row_idx, 0, c_item)

            # 1. ID / Token
            token_str = str(r.get("client_id_token") or (f"CLI-{r['client_id']:05d}" if r.get("client_id") else "Unregistered"))
            id_item = QTableWidgetItem(token_str)
            id_item.setTextAlignment(Qt.AlignCenter)
            id_item.setToolTip(token_str)
            if r.get('is_unassigned'):
                id_item.setForeground(QColor("#FFA657"))
            else:
                id_item.setForeground(QColor("#4CF9B7"))
            self.table.setItem(row_idx, 1, id_item)

            # 2. Portal
            portal_str = r.get("portal") or "Income Tax Portal"
            p_item = QTableWidgetItem(portal_str)
            p_item.setForeground(QColor("#E6EDF3"))
            p_item.setToolTip(portal_str)
            self.table.setItem(row_idx, 2, p_item)

            # 3. Filings & History Summary
            period_sum = r.get("period_summary") or f"{r.get('total_captures', 1)} Capture(s)"
            hist_item = QTableWidgetItem(period_sum)
            hist_item.setForeground(QColor("#58A6FF"))
            hist_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            hist_item.setToolTip(period_sum)
            self.table.setItem(row_idx, 3, hist_item)

            # 4. Latest ARN
            arn_item = QTableWidgetItem(r.get("latest_arn", "N/A"))
            arn_item.setFont(QFont("Consolas", 10, QFont.Bold))
            arn_item.setForeground(QColor("#39FF14"))
            arn_item.setToolTip(r.get("latest_arn", "N/A"))
            self.table.setItem(row_idx, 4, arn_item)

            # 5. Method
            method_item = QTableWidgetItem(r.get("capture_method", "SAD_API_Interceptor"))
            method_item.setTextAlignment(Qt.AlignCenter)
            method_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            method_item.setForeground(QColor("#4CF9B7"))
            self.table.setItem(row_idx, 7, method_item)

            # 6. Timestamp
            ts_str = _format_to_local_time(r.get("last_updated", ""))
            ts_item = QTableWidgetItem(ts_str)
            ts_item.setForeground(QColor("#8B949E"))
            self.table.setItem(row_idx, 6, ts_item)

            # 7. Actions
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            if r.get('is_unassigned'):
                btn_create = QPushButton("+ Create Client")
                btn_create.setStyleSheet("""
                    QPushButton {
                        background-color: #2E9B5F;
                        color: #FFFFFF;
                        border: none;
                        border-radius: 4px;
                        padding: 3px 8px;
                        font-size: 11px;
                        font-weight: 700;
                    }
                    QPushButton:hover {
                        background-color: #247C4C;
                    }
                """)
                btn_create.clicked.connect(lambda _, item=r: self._create_client_from_capture(item))
                action_layout.addWidget(btn_create)

            tot = r.get('total_captures', 1)
            btn_view = QPushButton(f"Inspect Container ({tot})")
            btn_view.setStyleSheet("""
                QPushButton {
                    background-color: #1A382B;
                    color: #4CF9B7;
                    border: 1px solid #2E9B5F;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #2E9B5F;
                    color: #FFFFFF;
                }
            """)
            btn_view.clicked.connect(lambda _, item=r: self._show_container_dialog(item))
            action_layout.addWidget(btn_view)

            btn_del = QPushButton("Delete")
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: #331A1A;
                    color: #FF6B6B;
                    border: 1px solid #882222;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #D9534F;
                    color: #FFFFFF;
                }
            """)
            btn_del.clicked.connect(lambda _, key=r["identity_key"]: self._delete_srpf_container(key))
            action_layout.addWidget(btn_del)

            self.table.setCellWidget(row_idx, 5, action_widget)

    def _populate_raw_table(self, records: list[dict]):
        """Populates table in granular Individual Raw Captures view."""
        # Save user-adjusted column widths if previously set
        prev_widths = [self.table.columnWidth(c) for c in range(self.table.columnCount())] if self.table.columnCount() == 8 else []

        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Client Name & PAN", "ID", "Service / Portal", "Period",
            "ARN / Ack Number", "Actions", "Timestamp", "Capture Method"
        ])

        # Allow interactive mouse drag resizing on all columns
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)

        default_widths = [320, 80, 180, 140, 160, 190, 140, 150]
        for c, w in enumerate(prev_widths if len(prev_widths) == 8 and prev_widths[0] > 0 else default_widths):
            self.table.setColumnWidth(c, w)

        self.table.setRowCount(0)
        self.lbl_counter.setText(f"Raw Records: {len(records)}")

        for row_idx, r in enumerate(records):
            self.table.insertRow(row_idx)

            # 0. Client Name & PAN
            client_name = r.get('client_name') or "Unknown Client"
            pan_str = f" ({r['pan']})" if r.get('pan') and not r.get('is_unassigned') else ""
            full_c_text = f"{client_name}{pan_str}"
            c_item = QTableWidgetItem(full_c_text)
            c_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            c_item.setToolTip(full_c_text)
            if r.get('is_unassigned') or not r.get('client_id'):
                c_item.setForeground(QColor("#FFA657"))
            else:
                c_item.setForeground(QColor("#FFFFFF"))
            self.table.setItem(row_idx, 0, c_item)

            # 1. ID
            id_item = QTableWidgetItem(str(r["id"]))
            id_item.setTextAlignment(Qt.AlignCenter)
            id_item.setForeground(QColor("#8B949E"))
            self.table.setItem(row_idx, 1, id_item)

            # 2. Service / Portal
            portal_str = r.get("service_name") or r.get("portal") or "Portal"
            portal_item = QTableWidgetItem(portal_str)
            portal_item.setForeground(QColor("#E6EDF3"))
            portal_item.setToolTip(portal_str)
            self.table.setItem(row_idx, 2, portal_item)

            # 3. Period
            period_val = r.get("period_label") or "N/A"
            period_item = QTableWidgetItem(period_val)
            period_item.setForeground(QColor("#D29922") if r.get("period_label") else QColor("#8B949E"))
            period_item.setToolTip(period_val)
            self.table.setItem(row_idx, 3, period_item)

            # 4. ARN Number
            arn_item = QTableWidgetItem(r.get("arn_number", "N/A"))
            arn_item.setFont(QFont("Consolas", 10, QFont.Bold))
            arn_item.setForeground(QColor("#39FF14"))
            arn_item.setToolTip(r.get("arn_number", "N/A"))
            self.table.setItem(row_idx, 4, arn_item)

            # 5. Capture Method
            method = r.get("capture_method", "DOM_Tracker")
            method_item = QTableWidgetItem(method)
            method_item.setTextAlignment(Qt.AlignCenter)
            method_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            if method == "SAD_API_Interceptor":
                method_item.setForeground(QColor("#4CF9B7"))
            elif method == "DOM_Tracker":
                method_item.setForeground(QColor("#58A6FF"))
            else:
                method_item.setForeground(QColor("#FFA657"))
            self.table.setItem(row_idx, 7, method_item)

            # 6. Timestamp
            ts_str = _format_to_local_time(r.get("created_at", ""))
            ts_item = QTableWidgetItem(ts_str)
            ts_item.setForeground(QColor("#8B949E"))
            self.table.setItem(row_idx, 6, ts_item)

            # 7. Actions Column
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            if r.get('is_unassigned') or not r.get('client_id'):
                btn_create = QPushButton("+ Create Client")
                btn_create.setStyleSheet("""
                    QPushButton {
                        background-color: #2E9B5F;
                        color: #FFFFFF;
                        border: none;
                        border-radius: 4px;
                        padding: 3px 8px;
                        font-size: 11px;
                        font-weight: 700;
                    }
                    QPushButton:hover {
                        background-color: #247C4C;
                    }
                """)
                btn_create.clicked.connect(lambda _, item=r: self._create_client_from_capture(item))
                action_layout.addWidget(btn_create)

            btn_view = QPushButton("View Payload")
            btn_view.setStyleSheet("""
                QPushButton {
                    background-color: #1A382B;
                    color: #4CF9B7;
                    border: 1px solid #2E9B5F;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #2E9B5F;
                    color: #FFFFFF;
                }
            """)
            btn_view.clicked.connect(lambda _, item=r: self._show_payload_dialog(item))
            action_layout.addWidget(btn_view)

            btn_del = QPushButton("Delete")
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: #331A1A;
                    color: #FF6B6B;
                    border: 1px solid #882222;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #D9534F;
                    color: #FFFFFF;
                }
            """)
            btn_del.clicked.connect(lambda _, dump_id=r["id"]: self._delete_dump(dump_id))
            action_layout.addWidget(btn_del)

            self.table.setCellWidget(row_idx, 5, action_widget)

    def _show_container_dialog(self, container_item: dict):
        dlg = PayloadInspectorDialog(container_item, db=self.db, is_container=True, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.load_data()

    def _show_payload_dialog(self, dump_item: dict):
        dlg = PayloadInspectorDialog(dump_item, db=self.db, is_container=False, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.load_data()

    def _create_client_from_capture(self, dump_item: dict):
        dlg = AddClientFromCaptureDialog(self.db, dump_item, self)
        if dlg.exec() == QDialog.Accepted:
            self.load_data()

    def _delete_srpf_container(self, identity_key: str):
        if QMessageBox.question(
            self, "Confirm Delete Container",
            f"Are you sure you want to delete this client container ({identity_key}) and all its captured filings?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.delete_srpf_container(identity_key)
            self.load_data()

    def _delete_dump(self, dump_id: int):
        if QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete Tracker Dump record #{dump_id}?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.delete_tracker_dump(dump_id)
            self.load_data()

    def _clear_all_dumps(self):
        if QMessageBox.warning(
            self, "Clear All Dump Logs",
            "Are you sure you want to clear ALL tracker dump logs and containers?\nThis operation cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.clear_tracker_dumps()
            with self.db._connect_raw() as conn:
                conn.execute("DELETE FROM client_raw_containers")
            self.load_data()

    def _export_csv(self):
        if not self._dumps_cache:
            QMessageBox.information(self, "Export CSV", "No records to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Tracker Dump CSV", "Sera_Tracker_Dump_Export.csv", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "ID / Token", "Client Name", "PAN", "Portal",
                    "Periods / Summary", "Latest ARN", "Capture Method",
                    "Total Captures", "Last Updated"
                ])
                for r in self._dumps_cache:
                    writer.writerow([
                        r.get("client_id_token") or r.get("id"),
                        r.get("display_name") or r.get("client_name"),
                        r.get("pan"),
                        r.get("portal") or r.get("service_name"),
                        r.get("period_summary") or r.get("period_label"),
                        r.get("latest_arn") or r.get("arn_number"),
                        r.get("capture_method"),
                        r.get("total_captures", 1),
                        r.get("last_updated") or r.get("created_at")
                    ])
            QMessageBox.information(self, "Export Complete", f"Exported {len(self._dumps_cache)} records to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not write CSV: {e}")

    def _open_raw_dump_txt(self):
        """Opens seraRawPayloadDump.txt in the system's default text editor."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        dump_paths = self.db._get_dump_file_paths()
        target_path = None
        for p in dump_paths:
            if os.path.exists(p):
                target_path = p
                break
        if not target_path and dump_paths:
            self.db.rebuild_raw_payload_dumps_file()
            target_path = dump_paths[0]

        if target_path and os.path.exists(target_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_path))
        else:
            QMessageBox.warning(self, "File Not Found", "Could not locate seraRawPayloadDump.txt on disk.")

    def _open_daily_dump_txt(self):
        """Opens today's Raw_Payload_Dump/seraRawPayloadDump_dd_mm_yy.txt in default text editor."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        today_key = self.db._extract_dump_date_key(None)
        daily_paths = self.db._get_daily_dump_file_paths(today_key)
        target_path = None
        for p in daily_paths:
            if os.path.exists(p):
                target_path = p
                break
        if not target_path and daily_paths:
            self.db.rebuild_raw_payload_dumps_file()
            for p in daily_paths:
                if os.path.exists(p):
                    target_path = p
                    break

        if target_path and os.path.exists(target_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_path))
        else:
            QMessageBox.warning(self, "File Not Found", f"Could not locate seraRawPayloadDump_{today_key}.txt on disk.")

    def _open_backup_dump_txt(self):
        """Opens seraRawPayloadDumpBackup.txt (append-only master archive) in default text editor."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        backup_paths = self.db._get_backup_dump_file_paths()
        target_path = None
        for p in backup_paths:
            if os.path.exists(p):
                target_path = p
                break
        if not target_path and backup_paths:
            self.db.rebuild_raw_payload_dumps_file()
            for p in backup_paths:
                if os.path.exists(p):
                    target_path = p
                    break

        if target_path and os.path.exists(target_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_path))
        else:
            QMessageBox.warning(self, "File Not Found", "Could not locate seraRawPayloadDumpBackup.txt on disk.")

    def _open_dump_folder(self):
        """Opens the Raw_Payload_Dump folder in Windows Explorer."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        folder_paths = self.db._get_dump_folder_paths()
        target_path = None
        for p in folder_paths:
            if os.path.exists(p):
                target_path = p
                break
        if not target_path and folder_paths:
            self.db.rebuild_raw_payload_dumps_file()
            for p in folder_paths:
                if os.path.exists(p):
                    target_path = p
                    break

        if target_path and os.path.exists(target_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target_path))
        else:
            QMessageBox.warning(self, "Folder Not Found", "Could not locate Raw_Payload_Dump folder on disk.")

    def _rebuild_raw_dump_txt(self):
        """Cleanly rebuilds and syncs all daily dumps, canonical dump, and master backup."""
        try:
            count = self.db.rebuild_raw_payload_dumps_file()
            QMessageBox.information(
                self, "Dump Files Rebuilt",
                f"Successfully rebuilt all daily partitioned dumps, canonical dump, and master backup with {count} records."
            )
        except Exception as e:
            QMessageBox.critical(self, "Rebuild Failed", f"Could not rebuild dump file: {e}")

    def _reresolve_identities(self):
        """Scans and re-resolves all captures and rebuilds SRPF containers."""
        try:
            count = self.db.re_resolve_all_tracker_dumps()
            self.load_data()
            QMessageBox.information(
                self, "SRPF Containers Rebuilt",
                f"Successfully re-resolved captures and rebuilt unified SRPF client containers ({count} captures updated)."
            )
        except Exception as e:
            QMessageBox.critical(self, "Re-resolve Error", f"Could not re-resolve captures: {e}")

    def _open_fst_classifier_report(self):
        """Generates and opens the latest FST Classification Excel report from FST_Classifier_1."""
        import os, sys
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        classifier_dir = os.path.join(app_dir, "FST_Classifier_1")
        report_path = os.path.join(classifier_dir, "payload_report.xlsx")
        dump_paths = self.db._get_dump_file_paths() if hasattr(self.db, "_get_dump_file_paths") else []
        
        target_dump = None
        for p in dump_paths:
            if os.path.exists(p) and os.path.getsize(p) > 100:
                target_dump = p
                break
        if not target_dump and dump_paths:
            self.db.rebuild_raw_payload_dumps_file()
            target_dump = dump_paths[0]

        try:
            if classifier_dir not in sys.path:
                sys.path.insert(0, classifier_dir)
            import fst_classifier

            os.makedirs(classifier_dir, exist_ok=True)
            success = fst_classifier.process_data(target_dump, report_path)
            if success and os.path.exists(report_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(report_path))
            else:
                QMessageBox.warning(self, "Classification Notice", "Could not generate classification report from available payloads.")
        except Exception as e:
            QMessageBox.critical(self, "Classifier Error", f"Failed to run FST Classifier: {e}")

    def _open_fst_tracer_report(self):
        """Refreshes and opens the evidence-first FST Tracer Alpha workbook."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        tracer_dir = os.path.join(app_dir, "FST_Tracer_Alpha")
        report_path = os.path.join(tracer_dir, "fst_tracer_alpha_report.xlsx")
        dump_paths = self.db._get_dump_file_paths() if hasattr(self.db, "_get_dump_file_paths") else []
        target_dump = next((p for p in dump_paths if os.path.exists(p) and os.path.getsize(p) > 100), None)

        try:
            if not target_dump and dump_paths:
                self.db.rebuild_raw_payload_dumps_file()
                target_dump = dump_paths[0]
            if not target_dump or not os.path.exists(target_dump):
                QMessageBox.warning(self, "Tracer Notice", "Could not locate a raw payload dump to trace.")
                return

            from FST_Tracer_Alpha.tracer import process_dump
            os.makedirs(tracer_dir, exist_ok=True)
            vault_path = os.path.join(app_dir, "docs", "APP", "Sera FST Tracer Alpha")
            result = process_dump(target_dump, report_path, vault_path)
            actual_report = result.get("outputs", {}).get("excel_path", report_path)
            if os.path.exists(actual_report):
                QDesktopServices.openUrl(QUrl.fromLocalFile(actual_report))
            else:
                QMessageBox.warning(self, "Tracer Notice", "Could not generate the FST Tracer Alpha report.")
        except Exception as e:
            QMessageBox.critical(self, "Tracer Error", f"Failed to run FST Tracer Alpha: {e}")


    def _open_dom_parser_report(self):
        """Generates and opens the latest DOM Parser 1 audit Excel report."""
        import os, sys
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        parser_dir = os.path.join(app_dir, "DOM_Parser_1")
        report_path = os.path.join(parser_dir, "dom_audit_report.xlsx")
        db_path = os.path.join(app_dir, "rawPayload.db")
        dump_paths = self.db._get_dump_file_paths() if hasattr(self.db, "_get_dump_file_paths") else []
        target_dump = db_path if os.path.exists(db_path) else next((p for p in dump_paths if os.path.exists(p) and os.path.getsize(p) > 100), None)

        try:
            if parser_dir not in sys.path:
                sys.path.insert(0, parser_dir)
            import dom_parser

            os.makedirs(parser_dir, exist_ok=True)
            success = dom_parser.process_data(target_dump, report_path)
            if success and os.path.exists(report_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(report_path))
            else:
                QMessageBox.warning(self, "DOM Parser Notice", "Could not generate DOM audit report from available captures.")
        except Exception as e:
            QMessageBox.critical(self, "DOM Parser Error", f"Failed to run DOM Parser 1: {e}")

    def _open_simple_parser_report(self):
        """Refreshes and opens the conservative Simple Parser workbook."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        parser_dir = os.path.join(app_dir, "simpleParser")
        report_path = os.path.join(parser_dir, "simple_parser_report.xlsx")
        dump_paths = self.db._get_dump_file_paths() if hasattr(self.db, "_get_dump_file_paths") else []
        target_dump = next((p for p in dump_paths if os.path.exists(p) and os.path.getsize(p) > 100), None)
        try:
            # Tracker Dump UI reads rawPayload.db directly, while Simple Parser
            # reads the text dump. Rebuild first so newly captured rows cannot
            # be missing from the generated workbook.
            if dump_paths and hasattr(self.db, "rebuild_raw_payload_dumps_file"):
                self.db.rebuild_raw_payload_dumps_file()
                target_dump = next((p for p in dump_paths if os.path.exists(p) and os.path.getsize(p) > 100), None)
            if not target_dump and dump_paths:
                target_dump = dump_paths[0]
            if not target_dump or not os.path.exists(target_dump):
                QMessageBox.warning(self, "Simple Parser Notice", "Could not locate a raw payload dump to parse.")
                return

            from simpleParser.simple_parser import process_dump
            os.makedirs(parser_dir, exist_ok=True)
            result = process_dump(
                target_dump,
                report_path,
                master_pans=self.db._get_master_pans_for_reports() if hasattr(self.db, "_get_master_pans_for_reports") else set(),
            )
            actual_report = result.get("outputs", {}).get("excel_path", report_path)
            if os.path.exists(actual_report):
                QDesktopServices.openUrl(QUrl.fromLocalFile(actual_report))
            else:
                QMessageBox.warning(self, "Simple Parser Notice", "Could not generate the Simple Parser report.")
        except Exception as e:
            QMessageBox.critical(self, "Simple Parser Error", f"Failed to run Simple Parser: {e}")

    def _open_fst_obsidian_vault(self):
        """Refreshes and opens the generated Obsidian timeline folder."""
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        vault_path = os.path.join(app_dir, "docs", "APP", "Sera FST Tracer Alpha")
        try:
            self.db.sync_fst_reports()
            if os.path.isdir(vault_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(vault_path))
            else:
                QMessageBox.warning(self, "Vault Notice", "The Obsidian timeline vault could not be generated.")
        except Exception as e:
            QMessageBox.critical(self, "Vault Error", f"Failed to refresh the Obsidian timeline vault: {e}")

    def _show_preferences_menu(self):
        """Displays a floating Preferences menu for dump utilities, classification, and maintenance."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #171717;
                border: 1px solid #2E9B5F;
                border-radius: 8px;
                padding: 6px;
                color: #F8F5F2;
                font-size: 13px;
            }
            QMenu::item {
                padding: 8px 24px 8px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2E9B5F;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background-color: #333333;
                margin: 4px 8px;
            }
        """)

        act_open_daily = menu.addAction(_safe_qta_icon("mdi.calendar-today", "#4CF9B7"), "Open Today's Dump (TXT)")
        act_open_daily.triggered.connect(self._open_daily_dump_txt)

        act_open_backup = menu.addAction(_safe_qta_icon("mdi.shield-lock-outline", "#4CF9B7"), "Open Master Backup (TXT)")
        act_open_backup.triggered.connect(self._open_backup_dump_txt)

        act_open_txt = menu.addAction(_safe_qta_icon("mdi.file-document-outline", "#4CF9B7"), "Open Full Dump (TXT)")
        act_open_txt.triggered.connect(self._open_raw_dump_txt)

        act_open_folder = menu.addAction(_safe_qta_icon("mdi.folder-open-outline", "#4CF9B7"), "Open Raw_Payload_Dump Folder")
        act_open_folder.triggered.connect(self._open_dump_folder)

        act_rebuild_txt = menu.addAction(_safe_qta_icon("mdi.file-sync-outline", "#4CF9B7"), "Rebuild & Sync All Dumps")
        act_rebuild_txt.triggered.connect(self._rebuild_raw_dump_txt)

        act_reresolve = menu.addAction(_safe_qta_icon("mdi.database-sync", "#4CF9B7"), "Re-Resolve Identities (SRPF)")
        act_reresolve.triggered.connect(self._reresolve_identities)

        menu.addSeparator()

        act_classifier = menu.addAction(_safe_qta_icon("mdi.file-excel", "#4CF9B7"), "FST Classifier (Excel Report)")
        act_classifier.triggered.connect(self._open_fst_classifier_report)

        act_tracer = menu.addAction(_safe_qta_icon("mdi.timeline-text-outline", "#4CF9B7"), "FST Tracer Alpha (Excel Report)")
        act_tracer.triggered.connect(self._open_fst_tracer_report)

        act_dom_parser = menu.addAction(_safe_qta_icon("mdi.view-dashboard-outline", "#4CF9B7"), "DOM Parser 1 (Excel Report)")
        act_dom_parser.triggered.connect(self._open_dom_parser_report)

        act_simple_parser = menu.addAction(_safe_qta_icon("mdi.file-table-outline", "#4CF9B7"), "Simple Parser (Excel Report)")
        act_simple_parser.triggered.connect(self._open_simple_parser_report)

        act_vault = menu.addAction(_safe_qta_icon("mdi.notebook-outline", "#4CF9B7"), "Open Obsidian FST Timeline Vault")
        act_vault.triggered.connect(self._open_fst_obsidian_vault)

        act_export_csv = menu.addAction(_safe_qta_icon("mdi.file-export", "#4CF9B7"), "Export Captures (CSV)")
        act_export_csv.triggered.connect(self._export_csv)

        menu.addSeparator()

        act_clear = menu.addAction(_safe_qta_icon("mdi.delete-sweep", "#FF6B6B"), "Clear All Captures")
        act_clear.triggered.connect(self._clear_all_dumps)

        # Spawn popup directly below Preferences button
        btn = getattr(self, "btn_preferences", None)
        if btn:
            menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec_(self.cursor().pos())

