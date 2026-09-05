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


def _parse_record_datetime(ts_str: str) -> datetime | None:
    """Parses a UTC/ISO or local timestamp string into a timezone-aware datetime object."""
    if not ts_str:
        return None
    clean = str(ts_str).strip()
    if not clean:
        return None
    try:
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        pass
    try:
        dt = datetime.strptime(clean[:19], "%Y-%m-%d %H:%M:%S")
        return dt.astimezone()
    except Exception:
        return None


def _resolve_ltt_submission_status(record: dict) -> tuple[str, str]:
    """
    Evaluates raw record status via SDC_Parser logic into an authoritative
    human-readable LTT submission status and corresponding UI display color.
    Returns: (status_text, hex_color)
    """
    raw_status = record.get("status") or record.get("latest_status") or ""
    arn = (record.get("arn_number") or record.get("latest_arn") or "").strip()

    # If it's a container and top-level raw_status is empty, inspect filing_history
    filing_hist = record.get("filing_history") or []
    if not raw_status and filing_hist:
        latest_f = filing_hist[-1]
        raw_status = latest_f.get("status") or ""
        if not arn:
            arn = (latest_f.get("arn") or "").strip()

    # If raw_payload has explicit status or ltt captures
    raw_json = record.get("raw_payload_json") or ""
    if not raw_json and filing_hist:
        raw_json = filing_hist[-1].get("raw_payload_json") or ""

    if raw_json:
        try:
            p_obj = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if isinstance(p_obj, dict):
                raw_p = p_obj.get("raw_payload") if isinstance(p_obj.get("raw_payload"), dict) else {}
                raw_status = p_obj.get("status") or raw_p.get("status") or raw_status
        except Exception:
            pass

    # Evaluate using SDC_Parser's standard logic
    try:
        from SDC_Parser.sdc_parser import evaluate_status
        ltt_status = evaluate_status(raw_status)
    except Exception:
        raw_lower = str(raw_status).lower()
        if "pending" in raw_lower:
            ltt_status = "Submitted (e-verification pending)"
        elif "filed" in raw_lower or "portal confirmed" in raw_lower:
            ltt_status = "Submitted & E-verified"
        elif "evc" in raw_lower:
            ltt_status = "Other EVC"
        elif "option expired" in raw_lower:
            ltt_status = "Option Expired (NA)"
        else:
            ltt_status = "Not submitted"

    # Only promote if an ARN is present and status is "Not submitted",
    # but NEVER override pending verification!
    if arn and arn != "N/A" and ltt_status == "Not submitted":
        raw_lower = str(raw_status).lower()
        if "pending" in raw_lower:
            ltt_status = "Submitted (e-verification pending)"
        else:
            ltt_status = "Submitted & E-verified"

    # Assign color palette matching Google Material / Sera design
    if ltt_status == "Submitted & E-verified":
        color = "#39FF14"  # Neon / Emerald Green
    elif ltt_status == "Submitted (e-verification pending)":
        color = "#F1E05A"  # Amber / Yellow
    elif ltt_status == "Other EVC":
        color = "#58A6FF"  # Soft Blue
    elif ltt_status == "Option Expired (NA)":
        color = "#8B949E"  # Muted Gray
    else:
        color = "#FF6B6B"  # Soft Red / Not submitted

    return ltt_status, color


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
        hist_table.setColumnCount(6)
        hist_table.setHorizontalHeaderLabels(["Period / AY", "ARN / Ack Number", "Submission Status", "Portal", "Capture Method", "Timestamp"])
        hist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hist_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hist_table.setRowCount(len(filing_history))

        for idx, fh in enumerate(reversed(filing_history)):
            p_item = QTableWidgetItem(fh.get("period_label") or "N/A")
            p_item.setForeground(QColor("#D29922"))
            hist_table.setItem(idx, 0, p_item)

            arn_item = QTableWidgetItem(fh.get("arn") or "N/A")
            arn_item.setFont(QFont("Consolas", 10, QFont.Bold))
            arn_item.setForeground(QColor("#39FF14"))
            hist_table.setItem(idx, 1, arn_item)

            s_text, s_color = _resolve_ltt_submission_status(fh)
            s_item = QTableWidgetItem(s_text)
            s_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            s_item.setForeground(QColor(s_color))
            hist_table.setItem(idx, 2, s_item)

            port_item = QTableWidgetItem(fh.get("portal") or "Income Tax")
            port_item.setForeground(QColor("#E6EDF3"))
            hist_table.setItem(idx, 3, port_item)

            m_item = QTableWidgetItem(fh.get("capture_method") or "SAD_API_Interceptor")
            m_item.setForeground(QColor("#4CF9B7"))
            hist_table.setItem(idx, 4, m_item)

            ts_item = QTableWidgetItem(_format_to_local_time(fh.get("created_at") or ""))
            ts_item.setForeground(QColor("#8B949E"))
            hist_table.setItem(idx, 5, ts_item)

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
        self._filtered_cache = []
        self._current_page = 1
        self._page_size = 25
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_filters)
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
            QPushButton.ActionCreateBtn {
                background-color: #2E9B5F;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton.ActionCreateBtn:hover {
                background-color: #247C4C;
            }
            QPushButton.ActionInspectBtn {
                background-color: #1A382B;
                color: #4CF9B7;
                border: 1px solid #2E9B5F;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.ActionInspectBtn:hover {
                background-color: #2E9B5F;
                color: #FFFFFF;
            }
            QPushButton.ActionDelBtn {
                background-color: #331A1A;
                color: #FF6B6B;
                border: 1px solid #882222;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.ActionDelBtn:hover {
                background-color: #D9534F;
                color: #FFFFFF;
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

        self.btn_ltt_report = QPushButton("Live Tracking Table (LTT)")
        self.btn_ltt_report.setProperty("class", "ActionBtn")
        self.btn_ltt_report.setStyleSheet("background-color: #2F6BA8; color: #FFFFFF; font-weight: 700;")
        self.btn_ltt_report.setIcon(_safe_qta_icon("mdi.file-excel", "#FFFFFF"))
        self.btn_ltt_report.setToolTip("Generate and open Multi-Sheet Live Tracking Table (LTT) Excel Report")
        self.btn_ltt_report.clicked.connect(self._export_ltt_excel)
        self.btn_ltt_report.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btn_ltt_report.customContextMenuRequested.connect(self._show_ltt_menu)
        header_layout.addWidget(self.btn_ltt_report)

        btn_excel_report = QPushButton("SDC Audit Report (Excel)")
        btn_excel_report.setProperty("class", "ActionBtn")
        btn_excel_report.setIcon(_safe_qta_icon("mdi.file-excel", "#FFFFFF"))
        btn_excel_report.clicked.connect(self._open_dom_parser_report)
        header_layout.addWidget(btn_excel_report)

        self.btn_preferences = QPushButton("Preferences")
        self.btn_preferences.setProperty("class", "ActionBtn")
        self.btn_preferences.setIcon(_safe_qta_icon("mdi.cog-outline", "#FFFFFF"))
        self.btn_preferences.clicked.connect(self._show_preferences_menu)
        header_layout.addWidget(self.btn_preferences)


        main_layout.addWidget(header_card)

        # Search & Filter Controls
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(8)

        # View Mode Selector (SRPF Grouped by Client Container as default)
        self.cmb_view_mode = QComboBox()
        self.cmb_view_mode.addItems(["Grouped by Client Container (SRPF)", "Individual Raw Captures"])
        self.cmb_view_mode.setStyleSheet("font-weight: 700; color: #4CF9B7; background-color: #0D1117; padding: 6px 12px; border: 1px solid #30363D; border-radius: 4px;")
        self.cmb_view_mode.currentIndexChanged.connect(self.load_data)
        filter_layout.addWidget(self.cmb_view_mode, stretch=2)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search Client, PAN, GSTIN, ARN, Period, Portal...")
        self.txt_search.setStyleSheet("padding: 6px 10px; background-color: #161B22; color: #F0F6FC; border: 1px solid #30363D; border-radius: 4px;")
        self.txt_search.textChanged.connect(self._on_search_text_changed)
        filter_layout.addWidget(self.txt_search, stretch=3)

        self.cmb_status = QComboBox()
        self.cmb_status.addItems([
            "All Statuses",
            "Submitted & E-verified",
            "Pending e-Verification",
            "Other EVC",
            "Not submitted"
        ])
        self.cmb_status.setToolTip("Filter by evaluated LTT filing submission status")
        self.cmb_status.setStyleSheet("padding: 6px 8px; background-color: #161B22; color: #F0F6FC; border: 1px solid #30363D; border-radius: 4px;")
        self.cmb_status.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.cmb_status, stretch=2)

        self.cmb_portal = QComboBox()
        self.cmb_portal.addItems([
            "All Portals",
            "Income Tax (ITR)",
            "GST Portal",
            "TRACES / TDS"
        ])
        self.cmb_portal.setToolTip("Filter by government compliance portal")
        self.cmb_portal.setStyleSheet("padding: 6px 8px; background-color: #161B22; color: #F0F6FC; border: 1px solid #30363D; border-radius: 4px;")
        self.cmb_portal.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.cmb_portal, stretch=1)

        self.cmb_client = QComboBox()
        self.cmb_client.addItems([
            "All Clients",
            "Registered Clients",
            "Unregistered / Action Required"
        ])
        self.cmb_client.setToolTip("Filter by client registration / assignment status")
        self.cmb_client.setStyleSheet("padding: 6px 8px; background-color: #161B22; color: #F0F6FC; border: 1px solid #30363D; border-radius: 4px;")
        self.cmb_client.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.cmb_client, stretch=2)

        self.cmb_date = QComboBox()
        self.cmb_date.addItems([
            "All Time",
            "Today",
            "Past 7 Days",
            "Past 30 Days"
        ])
        self.cmb_date.setToolTip("Filter by capture / update recency")
        self.cmb_date.setStyleSheet("padding: 6px 8px; background-color: #161B22; color: #F0F6FC; border: 1px solid #30363D; border-radius: 4px;")
        self.cmb_date.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.cmb_date, stretch=1)

        self.btn_reset_filters = QPushButton(" Reset")
        self.btn_reset_filters.setIcon(_safe_qta_icon("mdi.filter-off", "#8B949E"))
        self.btn_reset_filters.setToolTip("Reset all search & filter options")
        self.btn_reset_filters.setStyleSheet("""
            QPushButton {
                padding: 6px 12px;
                background-color: #21262D;
                color: #C9D1D9;
                border: 1px solid #30363D;
                border-radius: 4px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #30363D;
                color: #FFFFFF;
                border-color: #8B949E;
            }
        """)
        self.btn_reset_filters.clicked.connect(self._reset_filters)
        filter_layout.addWidget(self.btn_reset_filters, stretch=0)

        main_layout.addLayout(filter_layout)

        # Data Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        main_layout.addWidget(self.table)

        # Pagination & Stats Bar
        pagination_layout = QHBoxLayout()
        pagination_layout.setContentsMargins(4, 2, 4, 2)
        pagination_layout.setSpacing(10)

        self.lbl_page_info = QLabel("Showing 0 to 0 of 0 entries")
        self.lbl_page_info.setStyleSheet("color: #8B949E; font-size: 12px; font-weight: 500;")
        pagination_layout.addWidget(self.lbl_page_info)

        pagination_layout.addStretch()

        lbl_per_page = QLabel("Rows per page:")
        lbl_per_page.setStyleSheet("color: #8B949E; font-size: 12px;")
        pagination_layout.addWidget(lbl_per_page)

        self.cmb_page_size = QComboBox()
        self.cmb_page_size.addItems(["25", "50", "100", "200", "All"])
        self.cmb_page_size.setCurrentText("25")
        self.cmb_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        self.cmb_page_size.setStyleSheet("background-color: #121212; color: #F0F6FC; padding: 3px 8px; font-size: 12px;")
        pagination_layout.addWidget(self.cmb_page_size)

        self.btn_prev_page = QPushButton("◀ Prev")
        self.btn_prev_page.setProperty("class", "ActionBtn")
        self.btn_prev_page.clicked.connect(self._prev_page)
        pagination_layout.addWidget(self.btn_prev_page)

        self.lbl_current_page = QLabel("Page 1 / 1")
        self.lbl_current_page.setStyleSheet("font-weight: 700; color: #4CF9B7; padding: 0 6px; font-size: 12px;")
        pagination_layout.addWidget(self.lbl_current_page)

        self.btn_next_page = QPushButton("Next ▶")
        self.btn_next_page.setProperty("class", "ActionBtn")
        self.btn_next_page.clicked.connect(self._next_page)
        pagination_layout.addWidget(self.btn_next_page)

        main_layout.addLayout(pagination_layout)

        # Initial Load
        self.load_data()

    def _on_search_text_changed(self):
        """Debounced search filter."""
        self._current_page = 1
        self._search_timer.start()

    def _on_filter_changed(self):
        """Instant filter on dropdown change."""
        self._current_page = 1
        self._apply_filters()

    def _on_page_size_changed(self):
        txt = self.cmb_page_size.currentText()
        self._page_size = -1 if txt == "All" else int(txt)
        self._current_page = 1
        self._apply_filters()

    def _prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._apply_filters()

    def _next_page(self):
        total_items = len(self._filtered_cache)
        if self._page_size > 0:
            total_pages = max(1, (total_items + self._page_size - 1) // self._page_size)
            if self._current_page < total_pages:
                self._current_page += 1
                self._apply_filters()

    def _on_cell_double_clicked(self, row, column):
        """Fast double click inspection."""
        if hasattr(self, "_current_page_items") and 0 <= row < len(self._current_page_items):
            item = self._current_page_items[row]
            is_grouped = (self.cmb_view_mode.currentIndex() == 0)
            if is_grouped:
                self._show_container_dialog(item)
            else:
                self._show_payload_dialog(item)

    def _on_table_context_menu(self, pos):
        """Right click context menu for quick actions."""
        row = self.table.rowAt(pos.y())
        if row < 0 or not hasattr(self, "_current_page_items") or row >= len(self._current_page_items):
            return
        item = self._current_page_items[row]
        is_grouped = (self.cmb_view_mode.currentIndex() == 0)

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #1E1E1E; color: #FFFFFF; border: 1px solid #333333;")

        if is_grouped:
            act_inspect = menu.addAction(_safe_qta_icon("mdi.eye", "#4CF9B7"), "Inspect Client Container")
            act_inspect.triggered.connect(lambda: self._show_container_dialog(item))
            if item.get('is_unassigned'):
                act_create = menu.addAction(_safe_qta_icon("mdi.account-plus", "#2E9B5F"), "+ Create Client")
                act_create.triggered.connect(lambda: self._create_client_from_capture(item))
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", "#FF6B6B"), "Delete Container")
            act_del.triggered.connect(lambda: self._delete_srpf_container(item.get("identity_key")))
        else:
            act_inspect = menu.addAction(_safe_qta_icon("mdi.code-json", "#4CF9B7"), "View Raw Payload")
            act_inspect.triggered.connect(lambda: self._show_payload_dialog(item))
            if item.get('is_unassigned') or not item.get('client_id'):
                act_create = menu.addAction(_safe_qta_icon("mdi.account-plus", "#2E9B5F"), "+ Create Client")
                act_create.triggered.connect(lambda: self._create_client_from_capture(item))
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", "#FF6B6B"), "Delete Record")
            act_del.triggered.connect(lambda: self._delete_dump(item.get("id")))

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _reset_filters(self):
        """Resets all search and filter dropdowns to their default state."""
        self.txt_search.blockSignals(True)
        self.txt_search.clear()
        self.txt_search.blockSignals(False)

        self.cmb_status.blockSignals(True)
        self.cmb_status.setCurrentIndex(0)
        self.cmb_status.blockSignals(False)

        self.cmb_portal.blockSignals(True)
        self.cmb_portal.setCurrentIndex(0)
        self.cmb_portal.blockSignals(False)

        self.cmb_client.blockSignals(True)
        self.cmb_client.setCurrentIndex(0)
        self.cmb_client.blockSignals(False)

        self.cmb_date.blockSignals(True)
        self.cmb_date.setCurrentIndex(0)
        self.cmb_date.blockSignals(False)

        self._current_page = 1
        self._apply_filters()

    def load_data(self):
        """Fetch tracker dumps or SRPF unified containers from database."""
        try:
            is_grouped = (self.cmb_view_mode.currentIndex() == 0)
            if is_grouped:
                self._dumps_cache = self.db.get_srpf_containers(limit=200)
                # Default chronological organisation: latest entry at top
                self._dumps_cache.sort(key=lambda c: str(c.get("last_updated") or ""), reverse=True)
            else:
                self._dumps_cache = self.db.get_tracker_dumps(limit=200)
                # Default chronological organisation: latest entry at top
                self._dumps_cache.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("id") or 0), reverse=True)
            self._current_page = 1
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
        status_filter = self.cmb_status.currentText()
        portal_filter = self.cmb_portal.currentText()
        client_filter = self.cmb_client.currentText()
        date_filter = self.cmb_date.currentText()
        is_grouped = (self.cmb_view_mode.currentIndex() == 0)

        now_dt = datetime.now().astimezone()
        today_date = now_dt.date()

        filtered = []
        for d in self._dumps_cache:
            # 1. Submission Status Filter (Evaluated through authoritative LTT logic)
            if status_filter != "All Statuses":
                res_status, _ = _resolve_ltt_submission_status(d)
                if status_filter == "Submitted & E-verified" and res_status != "Submitted & E-verified":
                    continue
                elif status_filter == "Pending e-Verification" and res_status != "Submitted (e-verification pending)":
                    continue
                elif status_filter == "Other EVC" and res_status != "Other EVC":
                    continue
                elif status_filter == "Not submitted" and res_status not in ("Not submitted", "Option Expired (NA)"):
                    continue

            # 2. Portal / Jurisdiction Filter
            if portal_filter != "All Portals":
                p_text = f"{d.get('portal', '')} {d.get('service_name', '')} {d.get('form_type', '')}".lower()
                if portal_filter == "Income Tax (ITR)":
                    if not any(k in p_text for k in ("income tax", "itr")):
                        continue
                elif portal_filter == "GST Portal":
                    if not any(k in p_text for k in ("gst", "gstr", "cmp")):
                        continue
                elif portal_filter == "TRACES / TDS":
                    if not any(k in p_text for k in ("traces", "tds", "26q", "24q", "27q")):
                        continue

            # 3. Client Registration / Assignment Filter
            if client_filter != "All Clients":
                is_unreg = bool(d.get("is_unassigned")) or not bool(d.get("client_id"))
                if client_filter == "Registered Clients" and is_unreg:
                    continue
                elif client_filter == "Unregistered / Action Required" and not is_unreg:
                    continue

            # 4. Date / Recency Filter
            if date_filter != "All Time":
                ts_raw = d.get("last_updated") if is_grouped else d.get("created_at")
                rec_dt = _parse_record_datetime(ts_raw)
                if not rec_dt:
                    continue
                if date_filter == "Today":
                    if rec_dt.date() != today_date:
                        continue
                elif date_filter == "Past 7 Days":
                    delta_sec = (now_dt - rec_dt).total_seconds()
                    if delta_sec < 0 or delta_sec > 7 * 86400:
                        continue
                elif date_filter == "Past 30 Days":
                    delta_sec = (now_dt - rec_dt).total_seconds()
                    if delta_sec < 0 or delta_sec > 30 * 86400:
                        continue

            # 5. Search Text Filter
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

        # Ensure default chronological order is strictly maintained (latest at top)
        if is_grouped:
            filtered.sort(key=lambda c: str(c.get("last_updated") or ""), reverse=True)
        else:
            filtered.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("id") or 0), reverse=True)

        self._filtered_cache = filtered
        total_items = len(filtered)
        
        if self._page_size > 0:
            total_pages = max(1, (total_items + self._page_size - 1) // self._page_size)
            self._current_page = max(1, min(self._current_page, total_pages))
            start_idx = (self._current_page - 1) * self._page_size
            end_idx = min(start_idx + self._page_size, total_items)
            page_items = filtered[start_idx:end_idx]
            self.lbl_page_info.setText(f"Showing {start_idx + 1 if total_items > 0 else 0} to {end_idx} of {total_items} entries")
            self.lbl_current_page.setText(f"Page {self._current_page} / {total_pages}")
            self.btn_prev_page.setEnabled(self._current_page > 1)
            self.btn_next_page.setEnabled(self._current_page < total_pages)
        else:
            page_items = filtered
            self.lbl_page_info.setText(f"Showing 1 to {total_items} of {total_items} entries")
            self.lbl_current_page.setText("Page 1 / 1")
            self.btn_prev_page.setEnabled(False)
            self.btn_next_page.setEnabled(False)

        self._current_page_items = page_items

        self.table.setUpdatesEnabled(False)
        try:
            if is_grouped:
                self._populate_grouped_table(page_items)
            else:
                self._populate_raw_table(page_items)

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
            "Submission Status", "Actions", "Last Updated", "Capture Method"
        ])
        
        # Allow interactive mouse drag resizing on all columns
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)

        default_widths = [300, 95, 170, 210, 185, 210, 140, 150]
        for c, w in enumerate(prev_widths if len(prev_widths) == 8 and prev_widths[0] > 0 else default_widths):
            self.table.setColumnWidth(c, w)

        self.table.setRowCount(len(containers))
        self.lbl_counter.setText(f"Client Containers: {len(containers)}")

        for row_idx, r in enumerate(containers):
            def _get_item(col, font=None, color=None, align=None):
                item = self.table.item(row_idx, col)
                if not item:
                    item = QTableWidgetItem()
                    self.table.setItem(row_idx, col, item)
                if font: item.setFont(font)
                if color: item.setForeground(QColor(color))
                if align is not None: item.setTextAlignment(align)
                return item

            # 0. Client Name & PAN
            disp_name = r.get('display_name') or r.get('company_name') or r.get('proprietor_name') or f"Unregistered ({r.get('identity_key')})"
            c_item = _get_item(0, font=QFont("Segoe UI", 10.5, QFont.Bold), color="#FFA657" if r.get('is_unassigned') else "#FFFFFF")
            c_item.setText(disp_name)
            c_item.setToolTip(disp_name)

            # 1. ID / Token
            token_str = str(r.get("client_id_token") or (f"CLI-{r['client_id']:05d}" if r.get("client_id") else "Unregistered"))
            id_item = _get_item(1, align=Qt.AlignCenter, color="#FFA657" if r.get('is_unassigned') else "#4CF9B7")
            id_item.setText(token_str)
            id_item.setToolTip(token_str)

            # 2. Portal
            portal_str = r.get("portal") or "Income Tax Portal"
            p_item = _get_item(2, color="#E6EDF3")
            p_item.setText(portal_str)
            p_item.setToolTip(portal_str)

            # 3. Filings & History Summary
            period_sum = r.get("period_summary") or f"{r.get('total_captures', 1)} Capture(s)"
            hist_item = _get_item(3, font=QFont("Segoe UI", 9, QFont.Bold), color="#58A6FF")
            hist_item.setText(period_sum)
            hist_item.setToolTip(period_sum)

            # 4. Submission Status (Hooked to LTT / SDC_Parser)
            status_text, status_color = _resolve_ltt_submission_status(r)
            status_item = _get_item(4, font=QFont("Segoe UI", 9, QFont.Bold), color=status_color)
            status_item.setText(status_text)
            arn_val = r.get("latest_arn", "N/A")
            tooltip_lines = [f"Submission Status: {status_text}"]
            if arn_val and arn_val != "N/A":
                tooltip_lines.append(f"Latest ARN / Ack: {arn_val}")
            status_item.setToolTip("\n".join(tooltip_lines))

            # 5. Method
            method_item = _get_item(7, font=QFont("Segoe UI", 9, QFont.Bold), align=Qt.AlignCenter, color="#4CF9B7")
            method_item.setText(r.get("capture_method", "SAD_API_Interceptor"))

            # 6. Timestamp
            ts_str = _format_to_local_time(r.get("last_updated", ""))
            ts_item = _get_item(6, color="#8B949E")
            ts_item.setText(ts_str)

            # 7. Actions (always rebuild to capture current 'r')
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            if r.get('is_unassigned'):
                btn_create = QPushButton("+ Create Client")
                btn_create.setProperty("class", "ActionCreateBtn")
                btn_create.clicked.connect(lambda _, item=r: self._create_client_from_capture(item))
                action_layout.addWidget(btn_create)

            tot = r.get('total_captures', 1)
            btn_view = QPushButton(f"Inspect ({tot})")
            btn_view.setProperty("class", "ActionInspectBtn")
            btn_view.clicked.connect(lambda _, item=r: self._show_container_dialog(item))
            action_layout.addWidget(btn_view)

            btn_del = QPushButton("Delete")
            btn_del.setProperty("class", "ActionDelBtn")
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
            "Submission Status", "Actions", "Timestamp", "Capture Method"
        ])

        # Allow interactive mouse drag resizing on all columns
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)

        default_widths = [300, 75, 170, 130, 185, 190, 140, 150]
        for c, w in enumerate(prev_widths if len(prev_widths) == 8 and prev_widths[0] > 0 else default_widths):
            self.table.setColumnWidth(c, w)

        self.table.setRowCount(len(records))
        self.lbl_counter.setText(f"Raw Records: {len(records)}")

        for row_idx, r in enumerate(records):
            def _get_item(col, font=None, color=None, align=None):
                item = self.table.item(row_idx, col)
                if not item:
                    item = QTableWidgetItem()
                    self.table.setItem(row_idx, col, item)
                if font: item.setFont(font)
                if color: item.setForeground(QColor(color))
                if align is not None: item.setTextAlignment(align)
                return item

            # 0. Client Name & PAN
            client_name = r.get('client_name') or "Unknown Client"
            pan_str = f" ({r['pan']})" if r.get('pan') and not r.get('is_unassigned') else ""
            full_c_text = f"{client_name}{pan_str}"
            c_item = _get_item(0, font=QFont("Segoe UI", 10, QFont.Bold), color="#FFA657" if r.get('is_unassigned') or not r.get('client_id') else "#FFFFFF")
            c_item.setText(full_c_text)
            c_item.setToolTip(full_c_text)

            # 1. ID
            id_item = _get_item(1, align=Qt.AlignCenter, color="#8B949E")
            id_item.setText(str(r["id"]))

            # 2. Service / Portal
            portal_str = r.get("service_name") or r.get("portal") or "Portal"
            portal_item = _get_item(2, color="#E6EDF3")
            portal_item.setText(portal_str)
            portal_item.setToolTip(portal_str)

            # 3. Period
            period_val = r.get("period_label") or "N/A"
            period_item = _get_item(3, color="#D29922" if r.get("period_label") else "#8B949E")
            period_item.setText(period_val)
            period_item.setToolTip(period_val)

            # 4. Submission Status (Hooked to LTT / SDC_Parser)
            status_text, status_color = _resolve_ltt_submission_status(r)
            status_item = _get_item(4, font=QFont("Segoe UI", 9, QFont.Bold), color=status_color)
            status_item.setText(status_text)
            arn_val = r.get("arn_number", "N/A")
            tooltip_lines = [f"Submission Status: {status_text}"]
            if arn_val and arn_val != "N/A":
                tooltip_lines.append(f"ARN / Ack Number: {arn_val}")
            status_item.setToolTip("\n".join(tooltip_lines))

            # 5. Capture Method
            method = r.get("capture_method", "DOM_Tracker")
            if method == "SAD_API_Interceptor": color = "#4CF9B7"
            elif method == "DOM_Tracker": color = "#58A6FF"
            else: color = "#FFA657"
            method_item = _get_item(7, font=QFont("Segoe UI", 9, QFont.Bold), align=Qt.AlignCenter, color=color)
            method_item.setText(method)

            # 6. Timestamp
            ts_str = _format_to_local_time(r.get("created_at", ""))
            ts_item = _get_item(6, color="#8B949E")
            ts_item.setText(ts_str)

            # 7. Actions Column
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            if r.get('is_unassigned') or not r.get('client_id'):
                btn_create = QPushButton("+ Create Client")
                btn_create.setProperty("class", "ActionCreateBtn")
                btn_create.clicked.connect(lambda _, item=r: self._create_client_from_capture(item))
                action_layout.addWidget(btn_create)

            btn_view = QPushButton("View Payload")
            btn_view.setProperty("class", "ActionInspectBtn")
            btn_view.clicked.connect(lambda _, item=r: self._show_payload_dialog(item))
            action_layout.addWidget(btn_view)

            btn_del = QPushButton("Delete")
            btn_del.setProperty("class", "ActionDelBtn")
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
        """Cleanly rebuilds and syncs all date-partitioned daily dumps."""
        try:
            count = self.db.rebuild_raw_payload_dumps_file()
            QMessageBox.information(
                self, "Dump Files Rebuilt",
                f"Successfully rebuilt all date-partitioned daily dumps with {count} records."
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
        dump_paths = self.db._get_daily_dump_file_paths(self.db._extract_dump_date_key(None))
        
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

    def _open_dom_parser_report(self):
        """Generates and opens the latest DOM Parser 1 audit Excel report."""
        import os, sys
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        base_dir = os.path.dirname(os.path.abspath(__file__))
        app_dir = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(base_dir))
        parser_dir = os.path.join(app_dir, "DOM_Parser_1")
        live_dir = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera")
        os.makedirs(live_dir, exist_ok=True)
        report_path = os.path.join(live_dir, "dom_audit_report.xlsx")

        live_db = os.path.join(live_dir, "rawPayload.db")
        app_db = os.path.join(app_dir, "rawPayload.db")
        db_path = live_db if os.path.exists(live_db) else app_db
        dump_paths = self.db._get_daily_dump_file_paths(self.db._extract_dump_date_key(None))
        target_dump = db_path if os.path.exists(db_path) else next((p for p in dump_paths if os.path.exists(p) and os.path.getsize(p) > 100), None)

        try:
            if parser_dir not in sys.path:
                sys.path.insert(0, parser_dir)
            try:
                import dom_parser
            except ImportError:
                from DOM_Parser_1 import dom_parser

            success = dom_parser.process_data(target_dump, report_path)
            if success and os.path.exists(report_path):
                try:
                    os.startfile(report_path)
                except Exception:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(report_path))
            else:
                QMessageBox.warning(self, "DOM Parser Notice", "Could not generate DOM audit report from available captures.")
        except Exception as e:
            QMessageBox.critical(self, "DOM Parser Error", f"Failed to run DOM Parser 1: {e}")

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

        act_open_folder = menu.addAction(_safe_qta_icon("mdi.folder-open-outline", "#4CF9B7"), "Open Raw_Payload_Dump Folder")
        act_open_folder.triggered.connect(self._open_dump_folder)

        act_rebuild_txt = menu.addAction(_safe_qta_icon("mdi.file-sync-outline", "#4CF9B7"), "Rebuild Daily Dumps")
        act_rebuild_txt.triggered.connect(self._rebuild_raw_dump_txt)

        act_reresolve = menu.addAction(_safe_qta_icon("mdi.database-sync", "#4CF9B7"), "Re-Resolve Identities (SRPF)")
        act_reresolve.triggered.connect(self._reresolve_identities)

        menu.addSeparator()

        act_ltt_ws = menu.addAction(_safe_qta_icon("mdi.table-eye", "#4CF9B7"), "Live Tracking Table (LTT) Workspace")
        act_ltt_ws.triggered.connect(self._open_ltt_workspace)

        act_classifier = menu.addAction(_safe_qta_icon("mdi.file-excel", "#4CF9B7"), "FST Classifier (Excel Report)")
        act_classifier.triggered.connect(self._open_fst_classifier_report)

        act_dom_parser = menu.addAction(_safe_qta_icon("mdi.view-dashboard-outline", "#4CF9B7"), "DOM Parser 1 (Excel Report)")
        act_dom_parser.triggered.connect(self._open_dom_parser_report)

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


    def _open_ltt_workspace(self):
        """Opens the full-featured interactive Live Tracking Table (LTT) workspace."""
        try:
            from ui.windows.ltt_window import LttWorkspaceWindow
            win = LttWorkspaceWindow(self)
            win.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open LTT Workspace: {e}")

    def _generate_ltt(self):
        """Backward-compatible alias for generating the LTT Excel report."""
        self._export_ltt_excel()

    def _show_ltt_menu(self, pos):
        """Context menu for LTT button providing quick actions."""
        menu = QMenu(self)
        act_open = menu.addAction(_safe_qta_icon("mdi.table-eye", "#4CF9B7") or "", "Open Interactive LTT Workspace")
        act_open.triggered.connect(self._open_ltt_workspace)

        act_export = menu.addAction(_safe_qta_icon("mdi.file-excel", "#58A6FF") or "", "Export Multi-Sheet Excel Report")
        act_export.triggered.connect(self._export_ltt_excel)

        btn = getattr(self, "btn_ltt_report", None)
        if btn:
            menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec_(self.cursor().pos())

    def _export_ltt_excel(self):
        """Generates and opens the enhanced multi-sheet LTT Excel file."""
        import os, sys
        base_dir = os.path.dirname(os.path.abspath(__file__))
        sdc_parser_dir = os.path.abspath(os.path.join(base_dir, '..', '..', 'SDC_Parser'))
        if getattr(sys, 'frozen', False):
            sdc_parser_dir = os.path.join(sys._MEIPASS, 'SDC_Parser')
        if sdc_parser_dir not in sys.path:
            sys.path.insert(0, sdc_parser_dir)
            
        ltt_output = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera", "Live_Tracking_Table_LTT.xlsx")
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            import sdc_parser
            import importlib
            importlib.reload(sdc_parser)
            generated_path = sdc_parser.generate_ltt_excel()
            target_file = generated_path if (generated_path and os.path.exists(generated_path)) else ltt_output
            QApplication.restoreOverrideCursor()
            if os.path.exists(target_file):
                QMessageBox.information(self, "Success", f"Multi-Sheet Live Tracking Table (LTT) generated successfully!\n\nSaved to:\n{target_file}")
                try:
                    os.startfile(target_file)
                except Exception: pass
            else:
                QMessageBox.warning(self, "Warning", "Parser ran, but the LTT Excel file was not found.")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error", f"Failed to generate LTT Excel: {e}")

