"""
tracker_dump_window.py
----------------------
Dedicated workspace window for inspecting, filtering, and managing captured
filing submissions, ARNs, and raw technical payloads received from the browser
extension and VSDC.
"""

import json
import re
import os
import shutil
import time
from pathlib import Path
from datetime import datetime, timezone
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QThread
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QClipboard, QPixmap, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QDialog, QTextEdit, QTextBrowser, QFrame,
    QFileDialog, QScrollArea, QFormLayout, QCheckBox, QTabWidget,
    QApplication, QSizePolicy, QMenu, QButtonGroup, QToolButton, QGridLayout,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem
)

from ui.utils.profile_parser import extract_profile_from_payload, map_profile_to_mcl_columns
import automation



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


def _resolve_ltt_submission_status(record: dict) -> tuple[str, dict]:
    """
    Evaluates raw record status into an authoritative
    human-readable LTT submission status and corresponding UI display theme.
    Returns: (status_text, theme_dict)
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

    # If raw_payload has explicit status or ltt captures. List rows come without payloads
    # (get_srpf_containers(slim=True)) and carry the payload's status ready-made.
    raw_json = record.get("raw_payload_json") or ""
    if not raw_json and filing_hist:
        raw_json = filing_hist[-1].get("raw_payload_json") or ""

    if record.get("payload_status"):
        raw_status = record["payload_status"]
    elif raw_json:
        try:
            p_obj = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if isinstance(p_obj, dict):
                raw_p = p_obj.get("raw_payload") if isinstance(p_obj.get("raw_payload"), dict) else {}
                raw_status = p_obj.get("status") or raw_p.get("status") or raw_status
        except Exception:
            pass

    raw_lower = str(raw_status).lower().strip()
    if "not filed" in raw_lower or "unfiled" in raw_lower or "to be filed" in raw_lower or "not submitted" in raw_lower:
        ltt_status = "Not submitted"
    elif "visited" in raw_lower or "in progress" in raw_lower or "form selected" in raw_lower or "draft" in raw_lower:
        ltt_status = "Not submitted"
    elif ("not e-verified" in raw_lower or "not verified" in raw_lower or "pending" in raw_lower
          or "verify later" in raw_lower or "unverified" in raw_lower):
        ltt_status = "Submitted (e-verification pending)"
    elif "filed" in raw_lower or "portal confirmed" in raw_lower or "verified" in raw_lower:
        ltt_status = "Submitted & E-verified"
    elif "submitted" in raw_lower or "submit" in raw_lower or "success" in raw_lower:
        ltt_status = "Submitted & E-verified"
    elif "evc" in raw_lower:
        ltt_status = "Other EVC"
    elif "option expired" in raw_lower:
        ltt_status = "Option Expired (NA)"
    elif re.search(r"\b(?:not applicable|na)\b", raw_lower):
        ltt_status = "Not Applicable (NA)"
    else:
        ltt_status = "Not submitted"

    # Only promote if an ARN is present and status is "Not submitted",
    # but NEVER override pending verification!
    if arn and arn != "N/A" and ltt_status == "Not submitted":
        raw_lower = str(raw_status).lower()
        if "not e-verified" in raw_lower or "not verified" in raw_lower or "pending" in raw_lower or "verify later" in raw_lower:
            ltt_status = "Submitted (e-verification pending)"
        else:
            ltt_status = "Submitted & E-verified"

    # The acknowledgement is what makes it "& E-verified": a submit message on its own (form + period +
    # message) is only "Submitted"; form + period + message + ARN is "Submitted & E-verified".
    if ltt_status == "Submitted & E-verified" and not (arn and arn.upper() != "N/A"):
        ltt_status = "Submitted"

    # Assign color palette matching Google Material / Sera design
    theme = _get_status_theme(ltt_status)
    return ltt_status, theme


STATUS_THEMES = {
    "Submitted & E-verified": {                  # dark green
        "fg": "#39FF14",
        "bg": "#0B2A17",
        "border": "#1E7A43",
        "cell_bg": "#1A5936",
        "icon": "mdi.check-circle"
    },
    "Submitted": {                               # light green: a submit message, no ARN yet
        "fg": "#0A2616",
        "bg": "#7FD8A0",
        "border": "#A8EBC0",
        "cell_bg": "#7FD8A0",
        "icon": "mdi.check"
    },
    "Submitted (e-verification pending)": {
        "fg": "#F1E05A",
        "bg": "#2D2612",
        "border": "#997B22",
        "cell_bg": "#594713",
        "icon": "mdi.clock-alert"
    },
    "Not submitted": {
        "fg": "#FF6B6B",
        "bg": "#2D1616",
        "border": "#882222",
        "cell_bg": "#521414",
        "icon": "mdi.alert-circle"
    },
    "Other EVC": {
        "fg": "#58A6FF",
        "bg": "#122338",
        "border": "#1F6FEB",
        "cell_bg": "#12438A",
        "icon": "mdi.shield-key"
    },
    "Option Expired (NA)": {
        "fg": "#8B949E",
        "bg": "#1C2128",
        "border": "#30363D",
        "cell_bg": "#1D2126",
        "icon": "mdi.close-circle"
    },
    "Not Applicable (NA)": {
        "fg": "#8B949E",
        "bg": "#1C2128",
        "border": "#30363D",
        "cell_bg": "#1D2126",
        "icon": "mdi.minus-circle"
    },
}


def _get_status_theme(status_text: str) -> dict:
    return STATUS_THEMES.get(status_text, {
        "fg": "#FF6B6B",
        "bg": "#2D1616",
        "border": "#882222",
        "cell_bg": "#521414",
        "icon": "mdi.alert-circle"
    })


def _status_pill_style(status_text: str) -> str:
    theme = _get_status_theme(status_text)
    return (f"QLabel {{ color: {theme['fg']}; background-color: {theme['bg']}; "
            f"border: 1px solid {theme['border']}; border-radius: 11px; padding: 3px 11px; }}")


def _set_status_cell(table, row: int, col: int, status_text: str, bg_color: str = "", tooltip: str = "") -> None:
    """
    Puts a status pill in (row, col), REUSING the widget already there. The table is
    refreshed after every capture; building a new styled widget per row each time leaked ~3 MB
    of memory per refresh (measured 2026-09-21: +335 MB over 100 refreshes).
    bg_color is unused (kept for callers); the pill takes its colours from STATUS_THEMES.
    """
    existing = table.cellWidget(row, col)
    label = existing.findChild(QLabel, "status_label") if existing is not None else None
    if label is None:
        table.setCellWidget(row, col, _create_colored_cell_widget(status_text, bg_color, tooltip=tooltip))
        return
    if label.text() != status_text:               # restyle only when the status changes
        label.setStyleSheet(_status_pill_style(status_text))
        label.setText(status_text)
    existing.setToolTip(tooltip)
    label.setToolTip(tooltip)


def _create_colored_cell_widget(status_text: str, bg_color: str = "", fg_color: str = "", tooltip: str = "") -> QWidget:
    """A transparent cell holding a rounded status pill (dot + text)."""
    container = QWidget()
    container.setStyleSheet("QWidget { background: transparent; }")

    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(0)
    layout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

    lbl = QLabel(status_text)
    lbl.setObjectName("status_label")
    lbl.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
    lbl.setStyleSheet(_status_pill_style(status_text))
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    layout.addWidget(lbl)

    if tooltip:
        container.setToolTip(tooltip)
        lbl.setToolTip(tooltip)

    return container


def _capture_method_color(method: str) -> str:
    """
    Color-codes the Capture Method column by data source, so VSDC-X (exact,
    UI Automation) reads visually distinct from legacy VSDC (visual/OCR) in
    the table rather than both showing identically — capture_method values
    are now dynamically "VSDC-X_<crosshair_id>" or "VSDC_<crosshair_id>"
    (see core/vsdc/vsdc_assembler.py's `engine` param), matching the same
    blue/green convention already used for the VSDC-X HUD toast tags.
    """
    if not method:
        return "#FFA657"
    # Same colours as the HUD pill's source tags: VSDC247 purple, SGT orange.
    if method.startswith("SGT"):
        return "#FFA657"
    if method.startswith("VSDC247_"):
        return "#D2A8FF"
    if method.startswith("VSDC-X_"):
        return "#58A6FF"
    if method.startswith("VSDC_") or method == "SAD_API_Interceptor":
        return "#4CF9B7"
    if method == "DOM_Tracker":
        return "#58A6FF"
    return "#FFA657"


def _passes_source_filter(record: dict, source_filter: str, is_grouped: bool) -> bool:
    """
    "Hide SGT" / "SGT Only" for the Source dropdown. A raw row is SGT's when its capture
    method starts with "SGT". A client container can hold rows from several engines: "Hide SGT"
    hides a container made ONLY of SGT rows, "SGT Only" shows any container with an SGT row.
    """
    if is_grouped:
        methods = [str(h.get("capture_method") or "") for h in (record.get("filing_history") or [])]
        methods = methods or [str(record.get("capture_method") or "")]
    else:
        methods = [str(record.get("capture_method") or "")]
    sgt = [m.startswith("SGT") for m in methods]
    if source_filter == "Hide SGT":
        return not all(sgt)
    if source_filter == "SGT Only":
        return any(sgt)
    return True


def _portal_short(text: str) -> tuple[str, str]:
    """Short portal label + colour for the table; the full text goes in the tooltip."""
    low = str(text or "").lower()
    if "income tax" in low or "itr" in low:
        return "Income Tax", "#58A6FF"
    if "gst" in low or "cmp" in low:
        return "GST", "#4CF9B7"
    if any(k in low for k in ("traces", "tds", "26q", "24q", "27q")):
        return "TDS", "#D2A8FF"
    return str(text or "—"), "#8B949E"


def _filing_type(portal: str, fallback: str) -> str:
    """'GST Portal (GSTR-3B)' -> 'GSTR-3B'; a portal text without a filing type shows the short portal name."""
    m = re.search(r"\(([^()]+)\)\s*$", str(portal or ""))
    return m.group(1).strip() if m else fallback


def _method_short(method: str) -> str:
    """'VSDC-X_itr_submitted' -> 'VSDC-X'; 'SGT_live' -> 'SGT live'. Full value in the tooltip."""
    method = str(method or "").strip()
    if not method:
        return "Unknown"
    if method.startswith("SGT"):
        return method.replace("_", " ")
    head = method.split("_", 1)[0]
    return "Extension" if head.lower().startswith("extension") else head


def _short_local_time(ts_raw) -> str:
    """'Today 14:02', 'Yesterday 09:10', '20 Sep 11:42', or '20 Sep 2025' for older years."""
    dt = _parse_record_datetime(ts_raw)
    if not dt:
        return str(ts_raw or "")[:16]
    today = datetime.now().astimezone().date()
    days = (today - dt.date()).days
    if days == 0:
        return f"Today {dt:%H:%M}"
    if days == 1:
        return f"Yesterday {dt:%H:%M}"
    if dt.year == today.year:
        return f"{dt.day} {dt:%b %H:%M}"
    return f"{dt.day} {dt:%b %Y}"


def _filings_short(summary: str) -> str:
    """'2 Filings (June (FY 2026-27), AY 2026-27)' -> 'June (FY 2026-27)  +1 more'. Full text in the tooltip."""
    text = str(summary or "").strip()
    m = re.match(r"^(\d+)\s+Filings?\s*\((.*)\)$", text)
    if not m:
        return text
    count, inner = int(m.group(1)), m.group(2)
    depth, first = 0, inner
    for i, ch in enumerate(inner):                  # first entry = up to the first top-level comma
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            first = inner[:i]
            break
    first = re.sub(r"\bAssessment Year\b", "AY", first.strip())
    return first if count <= 1 else f"{first}  +{count - 1} more"


def _strip_pan_suffix(name: str, pan: str) -> str:
    """'RAHUL MONDAL (BEBPM9120B)' -> 'RAHUL MONDAL' when the PAN is already on the line below."""
    if name and pan:
        return re.sub(r"\s*\(\s*" + re.escape(pan) + r"\s*\)\s*$", "", name, flags=re.I) or name
    return name


_SUBLINE_ROLE = Qt.UserRole + 3
_UNASSIGNED_ROLE = Qt.UserRole + 4


class _ClientCellDelegate(QStyledItemDelegate):
    """Client column: bold name over a muted '#ID · PAN' line (orange when unregistered)."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name, opt.text = opt.text, ""
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        painter.save()
        rect = option.rect.adjusted(10, 5, -8, -5)
        unassigned = bool(index.data(_UNASSIGNED_ROLE))
        selected = bool(option.state & QStyle.State_Selected)

        name_font = QFont("Segoe UI", 10, QFont.Bold)
        painter.setFont(name_font)
        fm = painter.fontMetrics()
        painter.setPen(QColor("#FFFFFF" if selected else ("#FFA657" if unassigned else "#F0F6FC")))
        painter.drawText(rect.left(), rect.top() + fm.ascent(),
                         fm.elidedText(name, Qt.ElideRight, rect.width()))

        subline = str(index.data(_SUBLINE_ROLE) or "")
        if subline:
            sub_font = QFont("Consolas", 9)
            painter.setFont(sub_font)
            sfm = painter.fontMetrics()
            painter.setPen(QColor("#DDE6F0" if selected else ("#C98A55" if unassigned else "#8B949E")))
            painter.drawText(rect.left(), rect.bottom() - sfm.descent(),
                             sfm.elidedText(subline, Qt.ElideRight, rect.width()))
        painter.restore()


def _safe_qta_icon(icon_name, color="#FFFFFF"):
    if qta is not None:
        try:
            return qta.icon(icon_name, color=color)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()


def _service_matches_portal(service_name: str, portal_name: str) -> bool:
    """Matches a compliance service to a portal name using substring and keyword rules."""
    s_name = (service_name or "").strip().lower()
    p_name = (portal_name or "").strip().lower()
    if not s_name or not p_name:
        return False
    return s_name in p_name or p_name in s_name or ("income" in p_name and "income" in s_name)


class AddClientFromCaptureDialog(QDialog):
    """Modal dialog allowing quick 1-click creation of a client record directly from an unassigned capture."""
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
            if _service_matches_portal(s["name"], portal_name):
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

        # Priority 1: Extract profile from current payload 
        raw_str = self.item_data.get("raw_payload_json") or "{}"
        parsed = extract_profile_from_payload(raw_str)

        # Priority 2: Fallback to existing container in rawPayload.db if current payload yielded no names
        if not parsed.get("proprietor_name") and not parsed.get("company_name"):
            container = self.db.get_client_raw_container(identity_key=unassigned_key) if unassigned_key else None
            if container and (container.get("company_name") or container.get("pan") or container.get("gstin")):
                return container

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
        self.resize(1000, 700)
        self.setMinimumSize(760, 560)
        # Every rule is scoped by objectName so frame styles never cascade onto the labels
        # inside them (that cascade is what drew a box around every single label before).
        self.setStyleSheet("""
            QDialog { background-color: #1B1B1B; color: #F0F6FC; font-family: 'Segoe UI', sans-serif; }
            QLabel { color: #F0F6FC; font-size: 12px; background: transparent; border: none; }
            QFrame#InspCard { background-color: #141414; border: 1px solid #262626; border-radius: 8px; }
            QLabel#InspName { font-size: 18px; font-weight: 700; }
            QLabel#InspMeta { color: #8B949E; font-size: 12px; }
            QLabel#CardTitle { color: #8E8D88; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; }
            QLabel#FieldKey { color: #8E8D88; font-size: 11px; }
            QLabel#FieldVal { color: #F0F6FC; font-size: 13px; font-weight: 600; }
            QLabel#UnregBadge {
                color: #FFA657; background-color: #2E1D0E; border: 1px solid #6B4214;
                border-radius: 10px; padding: 2px 9px; font-size: 11px; font-weight: 600;
            }
            QLabel#EmptyNote { color: #6F6E6A; font-size: 12px; }
            QTabWidget::pane { border: none; border-top: 1px solid #262626; background: transparent; }
            QTabBar { background: transparent; qproperty-drawBase: 0; }
            QTabBar::tab {
                background: transparent; color: #8B949E; padding: 8px 14px; border: none;
                border-bottom: 2px solid transparent; font-weight: 600; font-size: 12px;
            }
            QTabBar::tab:hover { color: #F0F6FC; }
            QTabBar::tab:selected { color: #F0F6FC; border-bottom: 2px solid #2E9B5F; }
            QTextEdit, QTextBrowser {
                background-color: #111111; color: #D1D5DA; font-size: 12px;
                border: 1px solid #262626; border-radius: 8px; padding: 10px;
            }
            QTextEdit#JsonView { font-family: 'Consolas', 'Courier New', monospace; color: #9BE9A8; }
            QTableWidget {
                background-color: #141414; alternate-background-color: #171717;
                border: none; color: #F0F6FC; gridline-color: #262626;
                selection-background-color: #1A382B; selection-color: #FFFFFF;
            }
            QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid #222222; }
            QHeaderView::section {
                background-color: #141414; color: #8E8D88; font-weight: 700; font-size: 11px;
                padding: 6px 8px; border: none; border-bottom: 1px solid #2E9B5F;
            }
            QPushButton {
                background-color: #2E9B5F; color: #FFFFFF; font-weight: 600;
                border: 1px solid #2E9B5F; border-radius: 6px; padding: 7px 16px;
            }
            QPushButton:hover { background-color: #34B76D; }
            QPushButton.SecondaryBtn { background-color: #202020; color: #E6EDF3; border: 1px solid #3A3A3A; }
            QPushButton.SecondaryBtn:hover { background-color: #2A2A2A; border-color: #555555; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        # Extract profile from payload
        raw_json = item_data.get("raw_payload_json") or "{}"
        profile_data = extract_profile_from_payload(raw_json)

        # SGT-I's enrichment (blueprint 14.2 rule 4): its own key, beside the Core's values,
        # never in place of them - shown read-only, never editable from here.
        sgt_i_info = {}
        try:
            pj_i = json.loads(raw_json) if isinstance(raw_json, str) else (raw_json or {})
            rp_i = pj_i.get("raw_payload") if isinstance(pj_i.get("raw_payload"), dict) else {}
            if isinstance(rp_i.get("sgt_i"), dict):
                sgt_i_info = rp_i["sgt_i"]
        except Exception:
            pass

        comp_name = item_data.get("company_name") or profile_data.get("company_name") or ""
        prop_name = item_data.get("proprietor_name") or profile_data.get("proprietor_name") or ""
        pan_val = item_data.get("pan") or profile_data.get("pan") or item_data.get("identity_key") or ""
        gstin_val = item_data.get("gstin") or profile_data.get("gstin") or ""

        # Resolve clean display name for header (kept as before: also used by Copy Summary)
        client_name = item_data.get('display_name')
        if not client_name or client_name.startswith("Unregistered"):
            if prop_name or comp_name:
                client_name = f"{prop_name or comp_name} ({pan_val or gstin_val})"
            elif item_data.get('client_name') and not item_data.get('client_name').startswith("Unregistered"):
                client_name = item_data.get('client_name')
            else:
                client_name = f"Unregistered ({pan_val or item_data.get('identity_key') or 'N/A'})"

        is_unreg = item_data.get('is_unassigned') or not item_data.get('client_id')
        identity_key = pan_val or item_data.get('identity_key') or 'N/A'

        # ---- Header: name, badges, one line of facts, latest status ----
        header = QHBoxLayout()
        header.setSpacing(12)
        head_text = QVBoxLayout()
        head_text.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel(_strip_pan_suffix(client_name, pan_val))
        name_lbl.setObjectName("InspName")
        name_lbl.setStyleSheet(f"color: {'#FFA657' if is_unreg else '#F0F6FC'};")
        name_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        name_row.addWidget(name_lbl)
        if is_unreg:
            b = QLabel("No matching client")
            b.setObjectName("UnregBadge")
            name_row.addWidget(b)
        name_row.addStretch()
        head_text.addLayout(name_row)

        client_ref = str(item_data.get("client_id_token") or item_data.get("client_id") or "")
        captures = item_data.get('total_captures', 1)
        meta_parts = [p for p in (
            f"Client #{client_ref}" if client_ref and not is_unreg else "",
            f"PAN {identity_key}" if identity_key != "N/A" else "",
            str(item_data.get('portal') or "Government Portal"),
            f"{captures} capture{'s' if captures != 1 else ''}",
            f"Updated {_format_to_local_time(item_data.get('last_updated') or item_data.get('created_at'))}",
        ) if p]
        meta_lbl = QLabel("  ·  ".join(meta_parts))
        meta_lbl.setObjectName("InspMeta")
        meta_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meta_lbl.setToolTip(f"Identity key: {item_data.get('identity_key') or identity_key}")
        head_text.addWidget(meta_lbl)
        header.addLayout(head_text, stretch=1)

        head_status, _ = _resolve_ltt_submission_status(item_data)
        status_pill = QLabel(head_status)
        status_pill.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        status_pill.setStyleSheet(_status_pill_style(head_status))
        status_pill.setToolTip("Latest submission status")
        header.addWidget(status_pill, alignment=Qt.AlignTop)
        layout.addLayout(header)

        # Tab Widget
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Tab 1: Overview -- details cards over the filing history
        tab_summary = QWidget()
        sum_layout = QVBoxLayout(tab_summary)
        sum_layout.setContentsMargins(0, 14, 0, 0)
        sum_layout.setSpacing(12)

        def _card(title: str):
            frame = QFrame()
            frame.setObjectName("InspCard")
            v = QVBoxLayout(frame)
            v.setContentsMargins(14, 12, 14, 14)
            v.setSpacing(10)
            t = QLabel(title.upper())
            t.setObjectName("CardTitle")
            v.addWidget(t)
            return frame, v

        def _field_grid(pairs, cols):
            grid = QGridLayout()
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(10)
            shown = [(k, v) for k, v in pairs if v and str(v).strip()]
            for i, (key, val) in enumerate(shown):
                cell = QVBoxLayout()
                cell.setSpacing(1)
                k = QLabel(key)
                k.setObjectName("FieldKey")
                v = QLabel(str(val))
                v.setObjectName("FieldVal")
                v.setWordWrap(True)
                v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                cell.addWidget(k)
                cell.addWidget(v)
                grid.addLayout(cell, i // cols, i % cols, Qt.AlignTop)
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            return grid, len(shown)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        client_card, client_v = _card("Client details")
        grid, n_client = _field_grid([
            ("Firm / trade name", comp_name),
            ("Proprietor name", prop_name),
            ("PAN", pan_val),
            ("GSTIN", gstin_val),
            ("TAN", item_data.get("tan") or profile_data.get("tan")),
            ("Primary mobile", item_data.get("phone") or profile_data.get("phone")),
            ("Primary email", item_data.get("email") or profile_data.get("email")),
            ("DOB / incorporation", item_data.get("dob") or profile_data.get("dob")),
            ("Portal user ID", item_data.get("user_id") or profile_data.get("user_id")),
        ], cols=2)
        client_v.addLayout(grid)
        if not n_client:
            empty = QLabel("No client details in this capture.")
            empty.setObjectName("EmptyNote")
            client_v.addWidget(empty)
        client_v.addStretch()
        cards_row.addWidget(client_card, stretch=3)

        if sgt_i_info:
            sgti_card, sgti_v = _card("SGT-I — advisory, read-only")
            sgti_card.setToolTip("SGT Intelligence's own facts about this row (route, explanation, second "
                                  "opinion). They sit beside the Core's captured values and never replace "
                                  "them (blueprint 14.2 rule 4).")
            sgti_text = QLabel(json.dumps(sgt_i_info, indent=2, sort_keys=True, ensure_ascii=False))
            sgti_text.setObjectName("FieldVal")
            sgti_text.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 11px; font-weight: 400;")
            sgti_text.setWordWrap(True)
            sgti_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            sgti_v.addWidget(sgti_text)
            sgti_v.addStretch()
            cards_row.addWidget(sgti_card, stretch=2)

        sum_layout.addLayout(cards_row)

        # SDIS Others and Profile builder values, per portal (blueprint S.4), sdis_mcl labels
        for portal, pairs in self._sdis_values(item_data, raw_json):
            sdis_card, sdis_v = _card(f"{portal} — portal values (SDIS)")
            grid, _ = _field_grid(pairs, cols=3)
            sdis_v.addLayout(grid)
            sum_layout.addWidget(sdis_card)

        # Filing History Table
        filing_history = item_data.get("filing_history") or []
        if not filing_history and item_data.get("arn_number"):
            filing_history = [{
                "portal": item_data.get("portal"),
                "arn": item_data.get("arn_number"),
                "period_label": item_data.get("period_label"),
                "capture_method": item_data.get("capture_method"),
                "status": item_data.get("status"),
                "raw_payload_json": raw_json,
                "created_at": item_data.get("created_at")
            }]

        hist_card, hist_v = _card(f"Captured filings & obligations ({len(filing_history)})")
        hist_table = QTableWidget()
        hist_table.setColumnCount(6)
        hist_table.setHorizontalHeaderLabels(["Period / AY", "ARN / Ack number", "Submission status", "Portal", "Captured by", "Captured at"])
        hist_table.verticalHeader().setVisible(False)
        hist_table.verticalHeader().setDefaultSectionSize(40)
        hist_table.setShowGrid(False)
        hist_table.setAlternatingRowColors(True)
        hist_table.setEditTriggers(QTableWidget.NoEditTriggers)
        hist_table.setSelectionBehavior(QTableWidget.SelectRows)
        hh = hist_table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for c in range(6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)       # holds a pill widget: contents don't size it
        hist_table.setColumnWidth(2, 270)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hist_table.setWordWrap(False)
        hist_table.setRowCount(len(filing_history))

        for idx, fh in enumerate(reversed(filing_history)):
            p_item = QTableWidgetItem(fh.get("period_label") or "N/A")
            p_item.setForeground(QColor("#E3B341"))
            hist_table.setItem(idx, 0, p_item)

            arn_val = fh.get("arn") or "N/A"
            arn_item = QTableWidgetItem(arn_val)
            arn_item.setFont(QFont("Consolas", 10))
            arn_item.setForeground(QColor("#F0F6FC" if arn_val != "N/A" else "#6F6E6A"))
            hist_table.setItem(idx, 1, arn_item)

            s_text, _ = _resolve_ltt_submission_status(fh)
            hist_table.setItem(idx, 2, QTableWidgetItem(""))  # Empty text to prevent bleed
            hist_table.setCellWidget(idx, 2, _create_colored_cell_widget(s_text, tooltip=f"Submission Status: {s_text}"))

            portal_full = fh.get("portal") or "Income Tax"
            short, color = _portal_short(portal_full)
            port_item = QTableWidgetItem(short)
            port_item.setForeground(QColor(color))
            port_item.setToolTip(portal_full)
            hist_table.setItem(idx, 3, port_item)

            method = fh.get("capture_method") or "Unknown"
            m_item = QTableWidgetItem(_method_short(method))
            m_item.setForeground(QColor(_capture_method_color(method)))
            m_item.setToolTip(method)
            hist_table.setItem(idx, 4, m_item)

            ts_raw = fh.get("created_at") or ""
            ts_item = QTableWidgetItem(_short_local_time(ts_raw))
            ts_item.setForeground(QColor("#8B949E"))
            ts_item.setToolTip(_format_to_local_time(ts_raw))
            hist_table.setItem(idx, 5, ts_item)

        hist_table.setMinimumHeight(40 + 40 * min(max(len(filing_history), 1), 3))
        hist_v.addWidget(hist_table, stretch=1)
        sum_layout.addWidget(hist_card, stretch=1)

        overview_scroll = QScrollArea()
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setFrameShape(QFrame.NoFrame)
        overview_scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        overview_scroll.setWidget(tab_summary)
        tabs.addTab(overview_scroll, "Overview")

        # Tab 2: Raw Technical JSON
        tab_json = QWidget()
        json_layout = QVBoxLayout(tab_json)
        json_layout.setContentsMargins(0, 14, 0, 0)

        self.txt_json = QTextEdit()
        self.txt_json.setObjectName("JsonView")
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
        tabs.addTab(tab_json, "Raw JSON")

        # Tab 3: Notes & Media
        tab_notes = QWidget()
        notes_layout = QVBoxLayout(tab_notes)
        notes_layout.setContentsMargins(0, 14, 0, 0)
        notes_layout.setSpacing(10)

        self.txt_notes = QTextEdit()
        self.txt_notes.setPlaceholderText("Write notes here...")

        media_toolbar = QHBoxLayout()
        
        self.btn_attach_img = QPushButton()
        self.btn_attach_img.setToolTip("Attach Image")
        self.btn_attach_img.setIcon(_safe_qta_icon("mdi.paperclip", "#FFFFFF"))
        self.btn_attach_img.setFixedSize(36, 36)
        self.btn_attach_img.setProperty("class", "SecondaryBtn")
        
        self.btn_paste_img = QPushButton()
        self.btn_paste_img.setToolTip("Paste Image")
        self.btn_paste_img.setIcon(_safe_qta_icon("mdi.content-paste", "#4CF9B7"))
        self.btn_paste_img.setFixedSize(36, 36)
        self.btn_paste_img.setProperty("class", "SecondaryBtn")
        
        self.btn_ocr_img = QPushButton()
        self.btn_ocr_img.setToolTip("Scan Text (OCR)")
        self.btn_ocr_img.setIcon(_safe_qta_icon("mdi.text-recognition", "#58A6FF"))
        self.btn_ocr_img.setFixedSize(36, 36)
        self.btn_ocr_img.setProperty("class", "SecondaryBtn")
        
        self.btn_clear_img = QPushButton()
        self.btn_clear_img.setToolTip("Clear Image")
        self.btn_clear_img.setIcon(_safe_qta_icon("mdi.close-circle-outline", "#FF6B6B"))
        self.btn_clear_img.setStyleSheet("QPushButton { background-color: #201414; border: 1px solid #5A2626; border-radius: 6px; }"
                                         "QPushButton:hover { border-color: #FF6B6B; }")
        self.btn_clear_img.setFixedSize(36, 36)
        
        media_toolbar.addWidget(self.btn_attach_img)
        media_toolbar.addWidget(self.btn_paste_img)
        media_toolbar.addWidget(self.btn_ocr_img)
        media_toolbar.addWidget(self.btn_clear_img)
        media_toolbar.addStretch()

        self.lbl_save_status = QLabel("")
        self.lbl_save_status.setStyleSheet("color: #4CF9B7; font-size: 12px; font-weight: bold;")
        media_toolbar.addWidget(self.lbl_save_status)
        
        self.btn_expand_img = QPushButton()
        self.btn_expand_img.setToolTip("View Full Image")
        self.btn_expand_img.setIcon(_safe_qta_icon("mdi.fullscreen", "#FFFFFF"))
        self.btn_expand_img.setProperty("class", "SecondaryBtn")
        self.btn_expand_img.setFixedSize(36, 36)
        media_toolbar.addWidget(self.btn_expand_img)

        self.lbl_image_preview = QLabel("No image attached")
        self.lbl_image_preview.setAlignment(Qt.AlignCenter)
        self.lbl_image_preview.setStyleSheet("border: 1px dashed #3A3A3A; border-radius: 8px; color: #6F6E6A;")
        self.lbl_image_preview.setMinimumHeight(200)

        notes_layout.addWidget(self.txt_notes, stretch=1)
        notes_layout.addLayout(media_toolbar)
        notes_layout.addWidget(self.lbl_image_preview)

        tabs.addTab(tab_notes, "Notes && Media")  # && = a literal & in tab text

        # Connect signals
        self.current_screenshot_path = ""
        self.btn_attach_img.clicked.connect(self._attach_image)
        self.btn_paste_img.clicked.connect(self._paste_image)
        self.btn_ocr_img.clicked.connect(self._trigger_ocr_manually)
        self.btn_clear_img.clicked.connect(self._clear_image)
        self.btn_expand_img.clicked.connect(self._expand_image)
        
        # Auto-save setup
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._save_notes)
        self.txt_notes.textChanged.connect(self._on_notes_changed)
        
        # Load existing data
        QTimer.singleShot(0, self._load_notes_and_media)

        layout.addWidget(tabs, stretch=1)

        # Actions Box
        btn_box = QHBoxLayout()

        if is_unreg and self.db:
            btn_create = QPushButton("Create client from this container")
            btn_create.setIcon(_safe_qta_icon("mdi.account-plus", "#FFFFFF"))
            btn_create.clicked.connect(self._create_client)
            btn_box.addWidget(btn_create)

        btn_box.addStretch()

        btn_copy = QPushButton("Copy Container Summary")
        btn_copy.setProperty("class", "SecondaryBtn")

        def _on_tab_changed(idx):
            btn_copy.setVisible(idx != 2)
            if idx == 0:
                btn_copy.setText("Copy Container Summary")
            elif idx == 1:
                btn_copy.setText("Copy JSON")

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

        btn_copy.clicked.connect(_handle_bottom_copy)

        btn_close = QPushButton("Close")
        btn_close.setProperty("class", "SecondaryBtn")
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(btn_copy)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

    def _load_notes_and_media(self):
        if not self.db: return
        try:
            notes = ""
            spath = ""
            if self.is_container:
                ikey = self.item_data.get('identity_key')
                if ikey and hasattr(self.db, 'get_srpf_container_media'):
                    media_data = self.db.get_srpf_container_media(ikey)
                    notes = media_data.get("notes", "")
                    spath = media_data.get("screenshot_path", "")
            else:
                uid = self.item_data.get('id')
                if uid and hasattr(self.db, 'get_tracker_dump_media'):
                    media_data = self.db.get_tracker_dump_media(uid)
                    notes = media_data.get("notes", "")
                    spath = media_data.get("screenshot_path", "")
            
            if notes:
                self.txt_notes.setPlainText(notes)
            if spath and os.path.exists(spath):
                self.current_screenshot_path = spath
                self._show_preview(spath)
        except Exception as e:
            print(f"Error loading media: {e}")

    def _sdis_values(self, item_data: dict, raw_json) -> list:
        """[(portal, [(label, value)])]: a container's SRPF columns, or a single row's own 'sdis' key."""
        from sera_db.srpf import fold_sdis, sdis_info_of, sdis_value_rows
        aggregates = item_data.get("raw_aggregates") or {}
        profiles = item_data.get("portal_profiles") or {}
        if not (aggregates or profiles):
            try:
                payload = json.loads(raw_json) if isinstance(raw_json, str) else (raw_json or {})
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                aggregates, profiles = {}, {}
                fold_sdis(aggregates, profiles, sdis_info_of(payload))
        if not (aggregates or profiles):
            return []
        labels = self.db.sdis_field_labels() if self.db is not None else {}
        return sdis_value_rows(aggregates, profiles, labels)

    def _show_preview(self, path):
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(600, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_image_preview.setPixmap(scaled_pixmap)
            self.lbl_image_preview.setText("")
        else:
            self.lbl_image_preview.setText("Failed to load image")

    def _on_notes_changed(self):
        self.lbl_save_status.setText("Saving...")
        self.lbl_save_status.setStyleSheet("color: #F1E05A; font-size: 12px; font-weight: bold;")
        self._save_timer.start()

    def _clear_image(self):
        self.current_screenshot_path = ""
        self.lbl_image_preview.setPixmap(QPixmap())
        self.lbl_image_preview.setText("No image attached")
        self._save_notes()

    def _expand_image(self):
        if self.current_screenshot_path and os.path.exists(self.current_screenshot_path):
            try:
                os.startfile(self.current_screenshot_path)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not open image: {e}")

    def _attach_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if file_path:
            self.current_screenshot_path = file_path
            self._show_preview(file_path)
            self._save_notes()
            self._run_ocr(file_path)

    def _paste_image(self):
        clipboard = QGuiApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                temp_path = os.path.join(os.path.expanduser("~"), "temp_sera_paste.png")
                image.save(temp_path, "PNG")
                self.current_screenshot_path = temp_path
                self._show_preview(temp_path)
                self._save_notes()
                self._run_ocr(temp_path)
        else:
            QMessageBox.information(self, "No Image", "No image found in clipboard.")

    def _trigger_ocr_manually(self):
        if hasattr(self, 'current_screenshot_path') and self.current_screenshot_path and os.path.exists(self.current_screenshot_path):
            self._run_ocr(self.current_screenshot_path)
        else:
            QMessageBox.information(self, "No Image", "Please attach or paste an image first to scan text.")

    def _run_ocr(self, img_path):
        from ui.utils.ocr_worker import OCRWorker
        self.lbl_save_status.setText("Scanning OCR...")
        self.lbl_save_status.setStyleSheet("color: #58A6FF; font-size: 12px; font-weight: bold;")
        self.ocr_thread = OCRWorker(img_path)
        self.ocr_thread.finished.connect(self._on_ocr_finished)
        self.ocr_thread.start()

    def _on_ocr_finished(self, text):
        if text and not text.startswith("OCR Error") and not text.startswith("OCR Failed") and not text.startswith("OCR Engine"):
            current_text = self.txt_notes.toPlainText()
            if text.strip() not in current_text:
                new_text = f"{current_text}\n\n--- OCR Extracted Text ---\n{text}".strip()
                self.txt_notes.setPlainText(new_text)
                self._save_notes()
        else:
            self.lbl_save_status.setText("OCR Failed")
            self.lbl_save_status.setStyleSheet("color: #FF6B6B; font-size: 12px; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.lbl_save_status.setText(""))

    def _save_notes(self):
        if not self.db: return
        notes = self.txt_notes.toPlainText()
        final_path = ""
        
        if self.current_screenshot_path and os.path.exists(self.current_screenshot_path):
            try:
                media_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "media"))
                os.makedirs(media_dir, exist_ok=True)
                
                # Copy file if it's not already in the media directory
                if not self.current_screenshot_path.startswith(media_dir):
                    ext = os.path.splitext(self.current_screenshot_path)[1]
                    if not ext: ext = ".png"
                    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                    final_path = os.path.join(media_dir, filename)
                    shutil.copy2(self.current_screenshot_path, final_path)
                    self.current_screenshot_path = final_path
                else:
                    final_path = self.current_screenshot_path
            except Exception as e:
                self.lbl_save_status.setText("Failed to save image")
                self.lbl_save_status.setStyleSheet("color: #FF6B6B; font-size: 12px; font-weight: bold;")
                return
        
        try:
            if self.is_container:
                ikey = self.item_data.get('identity_key')
                if ikey and hasattr(self.db, 'save_srpf_container_media'):
                    self.db.save_srpf_container_media(ikey, notes, final_path)
            else:
                uid = self.item_data.get('id')
                if uid and hasattr(self.db, 'save_tracker_dump_media'):
                    self.db.save_tracker_dump_media(uid, notes, final_path)
                    
            self.lbl_save_status.setText("✓ Saved!")
            self.lbl_save_status.setStyleSheet("color: #4CF9B7; font-size: 12px; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.lbl_save_status.setText(""))
        except Exception as e:
            self.lbl_save_status.setText("Failed to save to database")
            self.lbl_save_status.setStyleSheet("color: #FF6B6B; font-size: 12px; font-weight: bold;")

    def _create_client(self):
        if not self.db:
            return
        dlg = AddClientFromCaptureDialog(self.db, self.item_data, self)
        if dlg.exec() == QDialog.Accepted:
            self.accept()


class _DatapointScanThread(QThread):
    """Reads the captured datapoints off the UI thread (every DB call opens its own connection)."""
    scanned = Signal(list)
    failed = Signal(str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db

    def run(self):
        try:
            self.scanned.emit(self._db.get_capture_datapoints())
        except Exception as e:
            self.failed.emit(str(e))


_LIVE_SCAN_THREADS: set = set()   # a scan outlives a dialog closed mid-scan until it finishes


class _McLColumnDelegate(QStyledItemDelegate):
    """One shared combo editor for the "Map to" column instead of a combo widget per row."""
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self._options = options          # [(label, column_id)]

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItem("—", None)
        for label, col_id in self._options:
            combo.addItem(label, col_id)
        combo.activated.connect(lambda _=0, c=combo: self.commitData.emit(c))
        combo.activated.connect(lambda _=0, c=combo: self.closeEditor.emit(c))
        QTimer.singleShot(0, combo.showPopup)
        return combo

    def setEditorData(self, editor, index):
        idx = editor.findData(index.data(Qt.UserRole))
        editor.setCurrentIndex(idx if idx >= 0 else 0)

    def setModelData(self, editor, model, index):
        col_id = editor.currentData()
        model.setData(index, editor.currentText() if col_id is not None else "—", Qt.DisplayRole)
        model.setData(index, col_id, Qt.UserRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class McLMappingDialog(QDialog):
    """Maps captured datapoints (payload keys) to Master Column List columns.

    Used when the automatic mapping misses a column. Saved mappings are applied whenever a
    capture arrives for an already registered client (blank MCL columns only are filled).
    """
    _KEY, _SEEN, _SAMPLE, _AUTO, _MAP = range(5)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Map Captured Datapoints to MCL Columns")
        self.setModal(True)
        self.resize(1040, 680)
        self._mcl_cols = [c for c in self.db.get_mcl_columns() if c.get("field_type") != "id"]
        self._col_label = {c["id"]: c["label"] for c in self._mcl_cols}
        self._scan = None
        self._setup_ui()
        self._start_scan()

    # ---------------- UI ----------------
    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #141414; color: #F8F5F2; font-family: 'Segoe UI', sans-serif; }
            QLabel { color: #C9D1D9; font-size: 12px; }
            QLabel#title { color: #F8F5F2; font-size: 17px; font-weight: 700; }
            QLabel#hint { color: #8B949E; font-size: 12px; }
            QLabel#stat { color: #8B949E; font-size: 11.5px; }
            QLineEdit { background-color: #0D1117; border: 1px solid #30363D; border-radius: 8px;
                        color: #F0F6FC; padding: 8px 12px; font-size: 13px; }
            QLineEdit:focus { border: 1px solid #2E9B5F; }
            QComboBox { background-color: #0D1117; border: 1px solid #2E9B5F; border-radius: 6px;
                        color: #F0F6FC; padding: 3px 8px; font-size: 12.5px; }
            QComboBox QAbstractItemView { background-color: #0D1117; color: #F0F6FC;
                                          selection-background-color: #2E9B5F; border: 1px solid #30363D; }
            QCheckBox { color: #C9D1D9; font-size: 12px; spacing: 6px; }
            QTableWidget { background-color: #0F0F0F; alternate-background-color: #141414;
                           border: 1px solid #262626; border-radius: 8px; color: #E6EDF3;
                           font-size: 12.5px; outline: 0; }
            QTableWidget::item { padding: 0 10px; border: none; }
            QTableWidget::item:hover { background-color: #1B2A22; }
            QTableWidget::item:selected { background-color: #1B2A22; color: #E6EDF3; }
            QHeaderView::section { background-color: #0F0F0F; color: #8B949E; border: none;
                                   border-bottom: 1px solid #2E9B5F; padding: 8px 10px;
                                   font-size: 11px; font-weight: 700; text-align: left; }
            QScrollBar:vertical { background: #0F0F0F; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #333333; border-radius: 5px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #2E9B5F; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QPushButton { background-color: #1F1F1F; color: #E6EDF3; border: 1px solid #3A3A3A;
                          border-radius: 8px; padding: 9px 18px; font-size: 13px; }
            QPushButton:hover { background-color: #2A2A2A; border-color: #4A4A4A; }
            QPushButton:disabled { color: #5A5A5A; border-color: #262626; }
            QPushButton#primary { background-color: #2E9B5F; color: #FFFFFF; font-weight: 700; border: none; }
            QPushButton#primary:hover { background-color: #36B06D; }
            QPushButton#primary:disabled { background-color: #1F3A2B; color: #5E7F6C; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("Map captured datapoints to MCL columns")
        title.setObjectName("title")
        hint = QLabel("Pick the MCL column a datapoint belongs to where auto-mapping misses it. "
                      "When a capture arrives for a registered client, mapped values fill that client's "
                      "blank columns; existing values are never overwritten.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(14)
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Search datapoints or sample values…")
        self.txt_filter.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(120)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.txt_filter.textChanged.connect(lambda _: self._filter_timer.start())
        self.chk_unmapped = QCheckBox("Only unmapped")
        self.chk_unmapped.toggled.connect(self._apply_filter)
        bar.addWidget(self.txt_filter, stretch=1)
        bar.addWidget(self.chk_unmapped)
        layout.addLayout(bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["DATAPOINT", "SEEN", "SAMPLE VALUE", "AUTO-MAPPED TO", "MAP TO MCL COLUMN"])
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setSectionResizeMode(self._KEY, QHeaderView.Interactive)
        hdr.setSectionResizeMode(self._SEEN, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self._SAMPLE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self._AUTO, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self._MAP, QHeaderView.Fixed)
        self.table.setColumnWidth(self._KEY, 210)
        self.table.setColumnWidth(self._SEEN, 64)
        self.table.setColumnWidth(self._AUTO, 170)
        self.table.setColumnWidth(self._MAP, 210)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setItemDelegateForColumn(
            self._MAP, _McLColumnDelegate([(c["label"], c["id"]) for c in self._mcl_cols], self.table))
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table, stretch=1)

        foot = QHBoxLayout()
        self.lbl_status = QLabel("Reading recent captures…")
        self.lbl_status.setObjectName("stat")
        foot.addWidget(self.lbl_status, stretch=1)
        layout.addLayout(foot)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.btn_apply = QPushButton("Save && apply to registered clients")
        self.btn_apply.setToolTip("Saves the mappings, then fills blank MCL columns of registered clients from their existing captures.")
        self.btn_apply.clicked.connect(lambda: self._save(apply_now=True))
        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(lambda: self._save(apply_now=False))
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        self.btn_save.setEnabled(False)
        self.btn_apply.setEnabled(False)
        btns.addWidget(self.btn_apply)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        btns.addWidget(self.btn_save)
        layout.addLayout(btns)

    # ---------------- loading (off the UI thread) ----------------
    def _start_scan(self):
        self._scan = _DatapointScanThread(self.db)
        _LIVE_SCAN_THREADS.add(self._scan)
        self._scan.finished.connect(lambda t=self._scan: _LIVE_SCAN_THREADS.discard(t))
        self._scan.scanned.connect(self._populate)
        self._scan.failed.connect(lambda msg: self.lbl_status.setText(f"Could not read captures: {msg}"))
        self._scan.start()

    def done(self, result):
        if self._scan is not None:
            try:
                self._scan.scanned.disconnect(self._populate)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

    def _populate(self, points: list):
        saved = {m["source"]: m["column_id"] for m in self.db.get_capture_mcl_mappings()}
        # a saved mapping stays editable even when its datapoint has aged out of recent captures
        known = {p["key"] for p in points}
        for key in saved:
            if key not in known:
                points.append({"key": key, "count": 0, "sample": "", "auto_column_id": None})

        green, cyan, dim = QColor("#4CF9B7"), QColor("#58C4FF"), QColor("#6E7681")
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(points))
        for r, p in enumerate(points):
            key_item = QTableWidgetItem(p["key"])
            key_item.setToolTip(p["key"])
            key_item.setFont(QFont("Consolas", 10))
            seen_item = QTableWidgetItem(str(p["count"]))
            seen_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            seen_item.setForeground(dim)
            sample_item = QTableWidgetItem(p["sample"])
            sample_item.setToolTip(p["sample"])
            sample_item.setForeground(QColor("#9DA7B3"))
            auto_item = QTableWidgetItem(self._col_label.get(p["auto_column_id"], ""))
            auto_item.setForeground(green)
            col_id = saved.get(p["key"])
            map_item = QTableWidgetItem(self._col_label.get(col_id, "—"))
            map_item.setData(Qt.UserRole, col_id)
            map_item.setForeground(cyan if col_id is not None else dim)
            for item in (key_item, seen_item, sample_item, auto_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            map_item.setFlags(map_item.flags() | Qt.ItemIsEditable)
            for c, item in enumerate((key_item, seen_item, sample_item, auto_item, map_item)):
                self.table.setItem(r, c, item)
        self.table.setUpdatesEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self._apply_filter()

    # ---------------- interaction ----------------
    def _on_cell_clicked(self, row, col):
        if col == self._MAP:
            self.table.editItem(self.table.item(row, col))

    def _is_mapped(self, row: int) -> bool:
        return bool(self.table.item(row, self._AUTO).text()) or self.table.item(row, self._MAP).data(Qt.UserRole) is not None

    def _apply_filter(self, *_):
        needle = self.txt_filter.text().strip().lower()
        only_unmapped = self.chk_unmapped.isChecked()
        shown = mapped = 0
        self.table.setUpdatesEnabled(False)
        for r in range(self.table.rowCount()):
            hay = f"{self.table.item(r, self._KEY).text()} {self.table.item(r, self._SAMPLE).text()}".lower()
            is_mapped = self._is_mapped(r)
            mapped += is_mapped
            hide = (bool(needle) and needle not in hay) or (only_unmapped and is_mapped)
            self.table.setRowHidden(r, hide)
            shown += 0 if hide else 1
        self.table.setUpdatesEnabled(True)
        total = self.table.rowCount()
        self.lbl_status.setText(f"{shown} of {total} datapoints shown  ·  {mapped} mapped, {total - mapped} unmapped")

    def _save(self, apply_now: bool):
        mappings = []
        for r in range(self.table.rowCount()):
            col_id = self.table.item(r, self._MAP).data(Qt.UserRole)
            if col_id is not None:
                mappings.append({"source": self.table.item(r, self._KEY).text(), "column_id": col_id})
        try:
            self.db.save_capture_mcl_mappings(mappings)
            if apply_now:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    changed = self.db.enrich_registered_clients_from_containers()
                finally:
                    QApplication.restoreOverrideCursor()
                QMessageBox.information(self, "Mappings saved",
                                        f"{len(mappings)} mapping(s) saved. {changed} registered client record(s) updated.")
            else:
                QMessageBox.information(self, "Mappings saved",
                                        f"{len(mappings)} mapping(s) saved. They apply to future captures of registered clients.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not save the mappings: {e}")


class TrackerDumpWindow(QWidget):
    """Full-featured workspace for inspecting client tracker dumps and SRPF unified containers."""

    skip_page_fade = True

    service_action_requested = Signal(int, dict)

    # Table columns (same order in both views)
    COL_CLIENT, COL_FILING, COL_PERIOD, COL_STATUS, COL_ACTIONS, COL_UPDATED, COL_METHOD = range(7)
    COLUMN_COUNT = 7
    
    def __init__(self, db, parent=None, defer_first_load: bool = False):
        super().__init__(parent)
        self.db = db
        # The app builds this page hidden at start-up; filling its table then cost ~0.25 s before
        # the window appeared. With defer_first_load it fills the first time it is shown (or the
        # first time anything calls load_data, e.g. a capture arriving).
        self._first_load_pending = False
        self._defer_first_load = defer_first_load
        self._dumps_cache = []
        self._filtered_cache = []
        self._current_page = 1
        self._page_size = 25
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_filters)
        self._col_resize_timer = QTimer(self)
        self._col_resize_timer.setSingleShot(True)
        self._col_resize_timer.setInterval(40)
        self._col_resize_timer.timeout.connect(self._adjust_table_columns)
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #202020;
                color: #F8F5F2;
                font-family: 'Segoe UI', sans-serif;
            }
            QFrame#HeaderCard { background: transparent; border: none; }
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
            QPushButton.ActionBtnGhost {
                background-color: transparent;
                color: #C9C9C9;
                font-weight: 600;
                border: none;
                border-radius: 5px;
                padding: 7px 14px;
                font-size: 12px;
            }
            QPushButton.ActionBtnGhost:hover {
                background-color: #3A3A3A;
                color: #FFFFFF;
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
                border-radius: 8px;
                color: #F0F6FC;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
            }
            QTableWidget::item {
                padding: 9px 10px;
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
            QPushButton#SummaryTile {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 7px;
                padding: 0;
                text-align: left;
            }
            QPushButton#SummaryTile:hover { border-color: #3F3F3F; }
            QPushButton#SummaryTile:checked { border: 2px solid #2E9B5F; }
            QToolButton#FilterChip {
                background-color: #1B1B1B;
                border: 1px solid #333333;
                border-radius: 12px;
                padding: 4px 11px;
                color: #C9D1D9;
                font-size: 12px;
            }
            QToolButton#FilterChip:hover { border-color: #4F4F4F; color: #FFFFFF; }
            QToolButton#FilterChip:checked { background-color: #1A382B; border-color: #2B5E46; color: #4CF9B7; font-weight: 600; }
            QToolButton#FilterChip::menu-indicator { image: none; width: 0; }
            QPushButton#ToolsBtn {
                background-color: #1B1B1B;
                border: 1px solid #333333;
                border-radius: 5px;
                color: #E6EDF3;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton#ToolsBtn:hover { background-color: #262626; border-color: #4F4F4F; }
            QPushButton#SummaryTile QLabel { background: transparent; }
            QLabel#TileValue { font-size: 20px; font-weight: 700; }
            QLabel#TileCaption { font-size: 12px; color: #A0A0A0; }
            QPushButton#SegBtn {
                background-color: #171717;
                border: 1px solid #444444;
                color: #C9C9C9;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton#SegBtn:checked { background-color: #2E9B5F; border-color: #2E9B5F; color: #FFFFFF; font-weight: 700; }
            QComboBox[active="true"] {
                color: #4CF9B7;
                border: 1px solid #2E9B5F;
                background-color: #10231A;
                font-weight: 600;
            }
            QFrame#HeaderCard QLabel { background: transparent; }
            QFrame#StatusBar { background: transparent; border-top: 1px solid #333333; }
            QFrame#StatusBar QLabel { background: transparent; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 10)
        main_layout.setSpacing(10)

        # ---- Header: title on the left, page actions on the right ----
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        header_card_layout = QVBoxLayout(header_card)
        header_card_layout.setContentsMargins(2, 0, 2, 0)
        header_card_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_card_layout.addLayout(header_layout)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        lbl_title = QLabel("Tracker Dump")
        lbl_title.setObjectName("TitleLbl")
        lbl_sub = QLabel("Filings captured by the browser extension, VSDC and SGT, grouped per client")
        lbl_sub.setObjectName("SubtitleLbl")
        title_vbox.addWidget(lbl_title)
        title_vbox.addWidget(lbl_sub)
        header_layout.addLayout(title_vbox)
        header_layout.addStretch()

        btn_refresh = QPushButton()
        btn_refresh.setProperty("class", "ActionBtnGhost")
        btn_refresh.setIcon(_safe_qta_icon("mdi.refresh", "#C9D1D9"))
        btn_refresh.setToolTip("Refresh (F5)")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self.load_data)
        header_layout.addWidget(btn_refresh)
        refresh_sc = QShortcut(QKeySequence(Qt.Key_F5), self)
        refresh_sc.setContext(Qt.WidgetWithChildrenShortcut)
        refresh_sc.activated.connect(self.load_data)

        self.btn_preferences = QPushButton("Tools  ▾")
        self.btn_preferences.setObjectName("ToolsBtn")
        self.btn_preferences.setIcon(_safe_qta_icon("mdi.cog-outline", "#C9D1D9"))
        self.btn_preferences.setToolTip("Re-resolve identities, reports, CSV export, clear captures")
        self.btn_preferences.setCursor(Qt.PointingHandCursor)
        self.btn_preferences.clicked.connect(self._show_preferences_menu)
        header_layout.addWidget(self.btn_preferences)

        # ---- Summary strip: totals over everything loaded; each tile applies a filter ----
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(8)
        self._summary_tiles = {}
        for key, caption, color, tip in (
            ("total", "Client containers", "#F0F6FC", "Show everything (clears all filters)"),
            ("unregistered", "Need a client", "#FFA657", "Captures not matched to a registered client"),
            ("pending", "Awaiting e-verification", "#E3B341", "Submitted, e-verification still pending"),
            ("not_submitted", "Not submitted", "#FF8A8A", "Not submitted or option expired"),
            ("today", "Captured today", "#4CF9B7", "Updated since midnight"),
        ):
            tile = QPushButton()
            tile.setObjectName("SummaryTile")
            tile.setCheckable(True)
            tile.setCursor(Qt.PointingHandCursor)
            tile.setToolTip(tip)
            tile.setMinimumHeight(50)
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(12, 6, 12, 6)
            tl.setSpacing(0)
            val = QLabel("0")
            val.setObjectName("TileValue")
            val.setStyleSheet(f"color: {color};")
            cap = QLabel(caption)
            cap.setObjectName("TileCaption")
            for lbl in (val, cap):
                lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
                tl.addWidget(lbl)
            tile.clicked.connect(lambda _=False, k=key: self._on_summary_tile(k))
            tiles_row.addWidget(tile, stretch=1)
            self._summary_tiles[key] = (tile, val, cap)
        header_card_layout.addLayout(tiles_row)

        # ---- Filter row ----
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        header_card_layout.addLayout(filter_layout)

        # The view-mode combo stays the source of truth (load_data reads it); the segmented
        # buttons in front of it just drive it.
        self.cmb_view_mode = QComboBox(self)
        self.cmb_view_mode.addItems(["Grouped by Client Container (SRPF)", "Individual Raw Captures"])
        self.cmb_view_mode.hide()
        self.cmb_view_mode.currentIndexChanged.connect(self.load_data)
        self.cmb_view_mode.currentIndexChanged.connect(self._sync_view_buttons)

        seg = QHBoxLayout()
        seg.setSpacing(0)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        for idx, (text, tip, radius) in enumerate((
            ("Containers", "One row per client (SRPF container)", "border-top-left-radius: 5px; border-bottom-left-radius: 5px;"),
            ("Raw captures", "One row per individual capture", "border-top-right-radius: 5px; border-bottom-right-radius: 5px; border-left: none;"),
        )):
            b = QPushButton(text)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(f"QPushButton#SegBtn {{ {radius} }}")
            bold = QFont(b.font())
            bold.setBold(True)
            b.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(text) + 32)
            self._view_group.addButton(b, idx)
            seg.addWidget(b)
        self._view_group.idClicked.connect(self.cmb_view_mode.setCurrentIndex)
        filter_layout.addLayout(seg)
        filter_layout.addSpacing(4)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search client, PAN, GSTIN, ARN, period, portal…")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.addAction(_safe_qta_icon("mdi.magnify", "#8B949E"), QLineEdit.LeadingPosition)
        self.txt_search.textChanged.connect(self._on_search_text_changed)
        self.txt_search.setFixedWidth(320)
        filter_layout.addWidget(self.txt_search)

        self.cmb_status = QComboBox()
        self.cmb_status.addItems([
            "All Statuses",
            "Submitted & E-verified",
            "Submitted",
            "Pending e-Verification",
            "Other EVC",
            "Not submitted"
        ])
        self.cmb_status.setToolTip("Filter by evaluated LTT filing submission status")
        self.cmb_status.currentIndexChanged.connect(self._on_filter_changed)

        self.cmb_portal = QComboBox()
        self.cmb_portal.addItems([
            "All Portals",
            "Income Tax (ITR)",
            "GST Portal",
            "TRACES / TDS"
        ])
        self.cmb_portal.setToolTip("Filter by government compliance portal")
        self.cmb_portal.currentIndexChanged.connect(self._on_filter_changed)

        self.cmb_client = QComboBox()
        self.cmb_client.addItems([
            "All Clients",
            "Registered Clients",
            "Unregistered / Action Required"
        ])
        self.cmb_client.setToolTip("Filter by client registration / assignment status")
        self.cmb_client.currentIndexChanged.connect(self._on_filter_changed)

        self.cmb_date = QComboBox()
        self.cmb_date.addItems([
            "All Time",
            "Today",
            "Past 7 Days",
            "Past 30 Days"
        ])
        self.cmb_date.setToolTip("Filter by capture / update recency")
        self.cmb_date.currentIndexChanged.connect(self._on_filter_changed)

        self.cmb_source = QComboBox()
        self.cmb_source.addItems([
            "All Sources",
            "Hide SGT",
            "SGT Only"
        ])
        self.cmb_source.setToolTip("SGT (Sera Global Tracker) is the capture engine; its rows are tagged "
                                   "\"SGT live\" (or \"SGT shadow\" while it runs beside the other engines "
                                   "for comparison). Hide them, or show only them.")
        self.cmb_source.currentIndexChanged.connect(self._on_filter_changed)

        # Filter chips: "Status ▾" etc.; a chip that's switched on shows its value, e.g. "Client: Unregistered".
        self._filter_chips = []
        for combo, label, short in (
            (self.cmb_status, "Status", {"Submitted & E-verified": "E-verified", "Submitted": "Submitted (no ARN)", "Pending e-Verification": "Pending e-verification"}),
            (self.cmb_portal, "Portal", {"Income Tax (ITR)": "Income Tax", "GST Portal": "GST", "TRACES / TDS": "TDS"}),
            (self.cmb_client, "Client", {"Registered Clients": "Registered", "Unregistered / Action Required": "Unregistered"}),
            (self.cmb_date, "Time", {}),
            (self.cmb_source, "Source", {}),
        ):
            combo.setParent(self)
            combo.hide()
            chip = QToolButton()
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setPopupMode(QToolButton.InstantPopup)
            chip.setToolTip(combo.toolTip())
            menu = QMenu(chip)
            for i in range(combo.count()):
                act = menu.addAction(combo.itemText(i))
                act.triggered.connect(lambda _=False, c=combo, idx=i: c.setCurrentIndex(idx))
                if i == 0:
                    menu.addSeparator()
            chip.setMenu(menu)
            filter_layout.addWidget(chip)
            self._filter_chips.append((chip, combo, label, short))
        filter_layout.addStretch()

        self.btn_reset_filters = QPushButton(" Reset")
        self.btn_reset_filters.setProperty("class", "ActionBtnGhost")
        self.btn_reset_filters.setIcon(_safe_qta_icon("mdi.filter-off", "#C9D1D9"))
        self.btn_reset_filters.setToolTip("Reset all search & filter options")
        self.btn_reset_filters.clicked.connect(self._reset_filters)
        filter_layout.addWidget(self.btn_reset_filters, stretch=0)

        main_layout.addWidget(header_card)

        # Data Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self._user_col_widths = {}
        self.table.horizontalHeader().sectionResized.connect(self._on_section_resized)
        self.table.setItemDelegateForColumn(self.COL_CLIENT, _ClientCellDelegate(self.table))
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        main_layout.addWidget(self.table, stretch=1)

        # ---- Status bar: AI meter on the left, paging on the right ----
        status_bar = QFrame()
        status_bar.setObjectName("StatusBar")
        pagination_layout = QHBoxLayout(status_bar)
        pagination_layout.setContentsMargins(4, 6, 4, 0)
        pagination_layout.setSpacing(10)

        pagination_layout.addStretch()

        self.lbl_page_info = QLabel("Showing 0 to 0 of 0 entries")
        self.lbl_page_info.setStyleSheet("color: #8B949E; font-size: 12px; font-weight: 500;")
        pagination_layout.addWidget(self.lbl_page_info)
        pagination_layout.addSpacing(8)

        lbl_per_page = QLabel("Rows per page:")
        lbl_per_page.setStyleSheet("color: #8B949E; font-size: 12px;")
        pagination_layout.addWidget(lbl_per_page)

        self.cmb_page_size = QComboBox()
        self.cmb_page_size.addItems(["25", "50", "100", "200", "All"])
        self.cmb_page_size.setCurrentText("25")
        self.cmb_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        self.cmb_page_size.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        pagination_layout.addWidget(self.cmb_page_size)

        self.btn_prev_page = QPushButton("◀ Prev")
        self.btn_prev_page.setProperty("class", "ActionBtnGhost")
        self.btn_prev_page.clicked.connect(self._prev_page)
        pagination_layout.addWidget(self.btn_prev_page)

        self.lbl_current_page = QLabel("Page 1 / 1")
        self.lbl_current_page.setStyleSheet("font-weight: 700; color: #4CF9B7; padding: 0 6px; font-size: 12px;")
        pagination_layout.addWidget(self.lbl_current_page)

        self.btn_next_page = QPushButton("Next ▶")
        self.btn_next_page.setProperty("class", "ActionBtnGhost")
        self.btn_next_page.clicked.connect(self._next_page)
        pagination_layout.addWidget(self.btn_next_page)

        main_layout.addWidget(status_bar)
        self._sync_view_buttons()
        self._sync_filter_indicators()

        # Initial Load
        if self._defer_first_load:
            self._first_load_pending = True
        else:
            self.load_data()

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_load_pending:
            self.load_data()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_col_resize_timer"):
            self._col_resize_timer.start()

    def _adjust_table_columns(self):
        """Client column takes the spare width; the others get a fixed width, or the width the
        user dragged them to. Nothing ratchets wider on refresh, so no horizontal scrollbar."""
        if not hasattr(self, "table") or self.table.columnCount() != self.COLUMN_COUNT:
            return
        is_grouped = self.cmb_view_mode.currentIndex() == 0 if hasattr(self, "cmb_view_mode") else True
        widths = {
            self.COL_FILING: 120,
            self.COL_PERIOD: 250 if is_grouped else 170,
            self.COL_STATUS: 280,
            self.COL_METHOD: 130,
            self.COL_UPDATED: 150,
            self.COL_ACTIONS: 120,
        }
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_CLIENT, QHeaderView.Stretch)
        header.setMinimumSectionSize(60)
        self._auto_sizing = True
        try:
            for col, w in widths.items():
                self.table.setColumnWidth(col, self._user_col_widths.get((is_grouped, col), w))
        finally:
            self._auto_sizing = False

    def _on_section_resized(self, col: int, _old: int, new: int):
        if not getattr(self, "_auto_sizing", False) and col != self.COL_CLIENT:
            is_grouped = self.cmb_view_mode.currentIndex() == 0
            self._user_col_widths[(is_grouped, col)] = new

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

        self.cmb_source.blockSignals(True)
        self.cmb_source.setCurrentIndex(0)
        self.cmb_source.blockSignals(False)

        self._current_page = 1
        self._apply_filters()

    # ---- summary strip / filter indicators ----

    _TILE_FILTERS = {
        # tile key -> (combo attribute, index that tile selects)
        "unregistered": ("cmb_client", 2),
        "pending": ("cmb_status", 2),
        "not_submitted": ("cmb_status", 4),
        "today": ("cmb_date", 1),
    }

    def _on_summary_tile(self, key: str):
        """A tile sets its filter; clicking the active tile again clears it. 'total' clears all."""
        if key == "total":
            self._reset_filters()
            return
        attr, idx = self._TILE_FILTERS[key]
        combo = getattr(self, attr)
        combo.setCurrentIndex(0 if combo.currentIndex() == idx else idx)  # -> _on_filter_changed

    def _update_summary(self):
        """Totals over everything loaded (not just this page or the current filters)."""
        if not hasattr(self, "_summary_tiles"):
            return
        is_grouped = self.cmb_view_mode.currentIndex() == 0
        today = datetime.now().astimezone().date()
        counts = {"total": len(self._dumps_cache), "unregistered": 0, "pending": 0, "not_submitted": 0, "today": 0}
        for d in self._dumps_cache:
            if d.get("is_unassigned") or not d.get("client_id"):
                counts["unregistered"] += 1
            status, _ = _resolve_ltt_submission_status(d)
            if status == "Submitted (e-verification pending)":
                counts["pending"] += 1
            elif status in ("Not submitted", "Option Expired (NA)"):
                counts["not_submitted"] += 1
            rec_dt = _parse_record_datetime(d.get("last_updated") if is_grouped else d.get("created_at"))
            if rec_dt and rec_dt.date() == today:
                counts["today"] += 1
        for key, (tile, val, cap) in self._summary_tiles.items():
            val.setText(f"{counts[key]:,}")
        self._summary_tiles["total"][2].setText("Client containers" if is_grouped else "Raw captures")

    def _sync_filter_indicators(self):
        """Highlights filters that are switched on, checks the matching tile, shows Reset only when useful."""
        if not hasattr(self, "_summary_tiles"):
            return
        combos = (self.cmb_status, self.cmb_portal, self.cmb_client, self.cmb_date, self.cmb_source)
        any_active = bool(self.txt_search.text().strip())
        for combo in combos:
            active = combo.currentIndex() > 0
            any_active = any_active or active
            if combo.property("active") != active:
                combo.setProperty("active", active)
                combo.style().unpolish(combo)
                combo.style().polish(combo)
        for key, (tile, _, _) in self._summary_tiles.items():
            if key == "total":
                tile.setChecked(not any_active)
            else:
                attr, idx = self._TILE_FILTERS[key]
                tile.setChecked(getattr(self, attr).currentIndex() == idx)
        self.btn_reset_filters.setVisible(any_active)
        for chip, combo, label, short in getattr(self, "_filter_chips", []):
            on = combo.currentIndex() > 0
            chip.setChecked(on)
            value = short.get(combo.currentText(), combo.currentText())
            chip.setText(f"{label}: {value}  ▾" if on else f"{label}  ▾")

    def _sync_view_buttons(self, *_):
        if hasattr(self, "_view_group"):
            btn = self._view_group.button(self.cmb_view_mode.currentIndex())
            if btn is not None:
                btn.setChecked(True)

    def load_data(self):
        """Fetch tracker dumps or SRPF unified containers from database."""
        self._first_load_pending = False
        try:
            gen_at_start = self.db.data_generation() if hasattr(self.db, "data_generation") else None
            raw_gen_at_start = self.db.raw_generation() if hasattr(self.db, "raw_generation") else None
            is_grouped = (self.cmb_view_mode.currentIndex() == 0)
            if is_grouped:
                self._dumps_cache = self.db.get_srpf_containers(limit=200, slim=True)
                # Default chronological organisation: latest entry at top
                self._dumps_cache.sort(key=lambda c: str(c.get("last_updated") or ""), reverse=True)
            else:
                self._dumps_cache = self.db.get_tracker_dumps(limit=200)
                # Default chronological organisation: latest entry at top
                self._dumps_cache.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("id") or 0), reverse=True)
            self._current_page = 1
            self._update_summary()
            self._apply_filters()
            self._loaded_state = (gen_at_start, raw_gen_at_start, self.cmb_view_mode.currentIndex(), time.monotonic())
        except Exception as e:
            QMessageBox.critical(self, "Error Loading Dumps", f"Could not load tracker dumps: {e}")

    REUSE_DATA_FOR_S = 60.0

    def load_data_if_stale(self):
        """Opening the tracker: reload only if something was written or the view changed."""
        state = getattr(self, "_loaded_state", None)
        if state is not None and not self._first_load_pending:
            gen, raw_gen, view, when = state
            db_gen = self.db.data_generation() if hasattr(self.db, "data_generation") else None
            db_raw = self.db.raw_generation() if hasattr(self.db, "raw_generation") else None
            if (db_gen is not None and db_raw is not None
                    and gen == db_gen and raw_gen == db_raw
                    and view == self.cmb_view_mode.currentIndex()
                    and time.monotonic() - when < self.REUSE_DATA_FOR_S):
                return
        self.load_data()

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
        source_filter = self.cmb_source.currentText()
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
                elif status_filter == "Submitted" and res_status != "Submitted":
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

            # 5. Capture Source Filter (SGT rows)
            if source_filter != "All Sources" and not _passes_source_filter(d, source_filter, is_grouped):
                continue

            # 6. Search Text Filter
            if search_txt:
                match_fields = [
                    d.get("display_name", ""), d.get("client_name", ""), d.get("pan", ""),
                    d.get("portal", ""), d.get("service_name", ""),
                    d.get("period_label", ""), d.get("period_summary", ""),
                    d.get("latest_arn", ""), d.get("arn_number", ""),
                    d.get("company_name", ""), d.get("proprietor_name", ""),
                    d.get("identity_key", ""), d.get("notes", "")
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
        self._sync_filter_indicators()

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

    def _set_action_cell(self, row_idx: int, item: dict, is_grouped: bool) -> None:
        """
        Puts the Actions cell in a row, REUSING the one already there: the table is refreshed
        after every capture, and a new pair of styled buttons per row per refresh - each holding
        a copy of the row's full payload history in its click handler - leaked ~3 MB per
        refresh (measured 2026-09-21). The cell only remembers its row number; the buttons look
        the row's current item up when clicked.
        """
        cell = self.table.cellWidget(row_idx, self.COL_ACTIONS)
        if cell is None or cell.findChild(QPushButton, "act_view") is None or cell.findChild(QPushButton, "act_autofill") is None:
            cell = self._build_action_cell(item, is_grouped)
            self.table.setCellWidget(row_idx, self.COL_ACTIONS, cell)
        cell.setProperty("row", row_idx)
        cell.setProperty("grouped", bool(is_grouped))
        btn_view = cell.findChild(QPushButton, "act_view")
        if is_grouped:
            tot = item.get('total_captures', 1)
            btn_view.setToolTip(f"Inspect ({tot} capture{'s' if tot != 1 else ''})")
        else:
            btn_view.setToolTip("View Payload")
        needs_create = item.get('is_unassigned') or (not is_grouped and not item.get('client_id'))
        btn_create = cell.findChild(QPushButton, "act_create")
        if btn_create is not None:
            btn_create.setVisible(bool(needs_create))
        btn_autofill = cell.findChild(QPushButton, "act_autofill")
        if btn_autofill is not None:
            has_client = bool(item.get("client_id")) and not item.get("is_unassigned")
            btn_autofill.setVisible(has_client)
            if has_client:
                service = self._service_for_row(item)
                if service:
                    mode_1 = automation.service_action_mode(service)
                    action_specs = {
                        automation.ACTION_AUTOFILL: (
                            "Fast Autofill", "mdi.flash", "#FFFFFF",
                            "QPushButton { background-color: #FF4D4D; border: none; border-radius: 6px; padding: 3px 0px; } QPushButton:hover { background-color: #E63939; }"
                        ),
                        automation.ACTION_SMTI: (
                            "SMTI Manual Assist", "mdi.clipboard-account-outline", "#FF4D4D",
                            "QPushButton { background-color: #1A1A1A; border: 1.5px solid #FF4D4D; border-radius: 6px; padding: 3px 0px; } QPushButton:hover { background-color: rgba(255, 77, 77, 0.2); }"
                        ),
                        automation.ACTION_MECP: (
                            "MECP Manual Copy", "mdi.content-copy", "#FF4D4D",
                            "QPushButton { background-color: #1A1A1A; border: 1.5px solid #FF4D4D; border-radius: 6px; padding: 3px 0px; } QPushButton:hover { background-color: rgba(255, 77, 77, 0.2); }"
                        ),
                    }
                    action_label, action_icon, icon_color, btn_style = action_specs.get(mode_1, action_specs[automation.ACTION_AUTOFILL])
                    btn_autofill.setIcon(_safe_qta_icon(action_icon, icon_color))
                    btn_autofill.setStyleSheet(btn_style)
                    btn_autofill.setToolTip(f"{action_label}: {service.get('name', 'Service')}")
                else:
                    portal_name = item.get("service_name") or item.get("portal") or "Portal"
                    btn_autofill.setIcon(_safe_qta_icon("mdi.flash", "#FFFFFF"))
                    btn_autofill.setStyleSheet("QPushButton { background-color: #FF4D4D; border: none; border-radius: 6px; padding: 3px 0px; } QPushButton:hover { background-color: #E63939; }")
                    btn_autofill.setToolTip(f"Fast Autofill: {portal_name}")

    def _on_action_create(self, cell: QWidget) -> None:
        item = self._action_cell_item(cell)
        if item is not None:
            self._create_client_from_capture(item)

    def _service_for_row(self, item: dict):
        cid = item.get("client_id")
        if not cid:
            return None
        cache = getattr(self, "_client_services_cache", None)
        if cache is not None and cid in cache:
            services = cache[cid]
        else:
            services = self.db.get_client_services(cid)
            if cache is not None:
                cache[cid] = services
        if not services:
            return None
        portal_name = (item.get("service_name") or item.get("portal") or "").strip()
        if not portal_name:
            return None
        p_lower = portal_name.lower()
        for s in services:
            if s.get("name", "").strip().lower() == p_lower:
                return s
        matched = [s for s in services if _service_matches_portal(s.get("name", ""), portal_name)]
        if matched:
            return matched[0]
        return None

    def _on_action_autofill(self, cell: QWidget) -> None:
        item = self._action_cell_item(cell)
        if item is None:
            return
        cid = item.get("client_id")
        if not cid:
            return
        service = self._service_for_row(item)
        if not service:
            portal_name = item.get("service_name") or item.get("portal") or "Unknown portal"
            client_name = (
                item.get("display_name")
                or item.get("client_name")
                or item.get("company_name")
                or item.get("proprietor_name")
                or item.get("pan")
                or f"Client #{cid}"
            )
            QMessageBox.information(
                self,
                "No matching service",
                f"No service matching portal '{portal_name}' was found for {client_name}."
            )
            return
        self.service_action_requested.emit(int(cid), service)

    def _action_cell_item(self, cell: QWidget):
        """The item currently shown in the action cell's row (None if the page changed under it)."""
        row = cell.property("row")
        items = getattr(self, "_current_page_items", None) or []
        if row is None or not (0 <= int(row) < len(items)):
            return None
        return items[int(row)]

    def _on_action_view(self, cell: QWidget) -> None:
        item = self._action_cell_item(cell)
        if item is None:
            return
        if cell.property("grouped"):
            self._show_container_dialog(item)
        else:
            self._show_payload_dialog(item)

    def _on_action_more(self, cell: QWidget, anchor: QPushButton) -> None:
        item = self._action_cell_item(cell)
        if item is not None:
            self._show_row_action_menu(item, bool(cell.property("grouped")), anchor)

    def _build_action_cell(self, item: dict, is_grouped: bool) -> QWidget:
        """
        Builds the per-row Actions cell: an Automation 1 autofill button,
        a primary Inspect/View button, and a kebab (⋮) overflow menu for
        Create Client / Delete.
        """
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        widget.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # Built once per table row and reused (see _set_action_cell); the handlers find the
        # row's current item when clicked instead of holding a copy of it.
        btn_autofill = QPushButton()
        btn_autofill.setObjectName("act_autofill")
        btn_autofill.setCursor(Qt.PointingHandCursor)
        btn_autofill.setFixedSize(34, 26)
        btn_autofill.setIconSize(QSize(16, 16))
        btn_autofill.setIcon(_safe_qta_icon("mdi.flash", "#FFFFFF"))
        btn_autofill.setToolTip("Automation 1 (Fast Autofill)")
        btn_autofill.setStyleSheet("""
            QPushButton {
                background-color: #FF4D4D;
                border: none;
                border-radius: 6px;
                padding: 3px 0px;
            }
            QPushButton:hover {
                background-color: #E63939;
            }
        """)
        btn_autofill.clicked.connect(lambda _=False, c=widget: self._on_action_autofill(c))
        layout.addWidget(btn_autofill)

        btn_view = QPushButton()
        btn_view.setObjectName("act_view")
        btn_view.clicked.connect(lambda _=False, c=widget: self._on_action_view(c))
        btn_view.setIcon(_safe_qta_icon("mdi.eye-outline", "#8FA6C2"))
        btn_view.setFixedWidth(30)
        # Styled inline rather than via the QSS class selector: buttons embedded through
        # setCellWidget() don't reliably repolish dynamic-property selectors in Qt, so the
        # class-based rule silently falls back to default chrome for these specifically.
        btn_view.setStyleSheet("""
            QPushButton {
                background-color: #1E1E1E;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 3px 0px;
            }
            QPushButton:hover {
                background-color: #2A2A2A;
                border: 1px solid #8FA6C2;
            }
        """)
        layout.addWidget(btn_view)

        btn_more = QPushButton("⋮")
        btn_more.setFixedWidth(24)
        btn_more.setToolTip("More actions (Create Client, Delete)")
        btn_more.setStyleSheet("""
            QPushButton {
                background-color: #1E1E1E;
                color: #C9C9C9;
                border: 1px solid #333333;
                border-radius: 4px;
                font-size: 13px;
                font-weight: 700;
                padding: 3px 0px;
            }
            QPushButton:hover {
                background-color: #2A2A2A;
                color: #FFFFFF;
                border: 1px solid #4CF9B7;
            }
        """)
        btn_more.setObjectName("act_more")
        btn_more.clicked.connect(lambda _=False, c=widget, b=btn_more: self._on_action_more(c, b))
        layout.addWidget(btn_more)

        # Only shown on rows with no matching client (see _set_action_cell)
        btn_create = QPushButton()
        btn_create.setObjectName("act_create")
        btn_create.setIcon(_safe_qta_icon("mdi.account-plus", "#4CF9B7"))
        btn_create.setFixedWidth(30)
        btn_create.setToolTip("Create a client from this capture")
        btn_create.setStyleSheet("""
            QPushButton {
                background-color: #10231A;
                border: 1px solid #245A3E;
                border-radius: 4px;
                padding: 3px 0px;
            }
            QPushButton:hover {
                background-color: #1A382B;
                border: 1px solid #4CF9B7;
            }
        """)
        btn_create.clicked.connect(lambda _=False, c=widget: self._on_action_create(c))
        btn_create.hide()
        layout.addWidget(btn_create)
        layout.addStretch()

        return widget

    def _show_row_action_menu(self, item: dict, is_grouped: bool, anchor: QPushButton):
        """Overflow menu anchored to the kebab button — mirrors the right-click context menu."""
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #1E1E1E; color: #FFFFFF; border: 1px solid #333333;")

        if is_grouped:
            needs_create = item.get('is_unassigned')
        else:
            needs_create = item.get('is_unassigned') or not item.get('client_id')

        if needs_create:
            act_create = menu.addAction(_safe_qta_icon("mdi.account-plus", "#2E9B5F"), "+ Create Client")
            act_create.triggered.connect(lambda: self._create_client_from_capture(item))

        if is_grouped:
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", "#FF6B6B"), "Delete Container")
            act_del.triggered.connect(lambda: self._delete_srpf_container(item.get("identity_key")))
        else:
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", "#FF6B6B"), "Delete Record")
            act_del.triggered.connect(lambda: self._delete_dump(item.get("id")))

        menu.exec_(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _prepare_columns(self, is_grouped: bool):
        """Sets headers and widths for the current view."""
        self.table.setColumnCount(self.COLUMN_COUNT)
        self.table.setHorizontalHeaderLabels([
            "Client", "Filing type", "Filings & History" if is_grouped else "Period",
            "Submission Status", "", "Updated", "Captured by"
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._adjust_table_columns()

    def _populate_grouped_table(self, containers: list[dict]):
        """Populates table in SRPF Grouped Container view: 1 row per unique client container."""
        self._client_services_cache = {}
        self._prepare_columns(is_grouped=True)
        self.table.setRowCount(len(containers))

        for row_idx, r in enumerate(containers):
            def _get_item(col, color=None, align=None):
                item = self.table.item(row_idx, col)
                if not item:
                    item = QTableWidgetItem()
                    self.table.setItem(row_idx, col, item)
                if color: item.setForeground(QColor(color))
                if align is not None: item.setTextAlignment(align)
                return item

            unassigned = bool(r.get('is_unassigned'))
            identity_key = r.get('identity_key') or ""

            # Client: name over "#ID · PAN" (ID and PAN used to be separate, truncated columns)
            disp_name = r.get('display_name') or r.get('company_name') or r.get('proprietor_name')
            if unassigned:
                name = disp_name or "Unregistered"
                subline = f"{identity_key} · no matching client"
            else:
                name = _strip_pan_suffix(disp_name or identity_key or "Unknown client", r.get("pan") or "")
                token_str = str(r.get("client_id_token") or (f"CLI-{r['client_id']:05d}" if r.get("client_id") else ""))
                subline = " · ".join(p for p in (f"#{token_str}" if token_str else "", r.get("pan") or "") if p)
            c_item = _get_item(self.COL_CLIENT)
            c_item.setText(name)
            c_item.setData(_SUBLINE_ROLE, subline)
            c_item.setData(_UNASSIGNED_ROLE, unassigned)
            tooltip_txt = f"{name}\n{subline}"
            if unassigned:
                tooltip_txt += "\nUse the + button, ⋮ or right-click → Create Client to register this client."
            c_item.setToolTip(tooltip_txt)

            portal_str = r.get("portal") or "Income Tax Portal"
            short, color = _portal_short(portal_str)
            p_item = _get_item(self.COL_FILING, color=color)
            p_item.setText(_filing_type(portal_str, short))
            p_item.setToolTip(portal_str)

            period_sum = r.get("period_summary") or f"{r.get('total_captures', 1)} Capture(s)"
            hist_item = _get_item(self.COL_PERIOD, color="#58A6FF")
            hist_item.setText(_filings_short(period_sum))
            hist_item.setToolTip(period_sum)

            # Submission Status
            status_text, status_theme = _resolve_ltt_submission_status(r)
            _get_item(self.COL_STATUS).setText("")  # Prevent text bleed
            arn_val = r.get("latest_arn", "N/A")
            tooltip_lines = [f"Submission Status: {status_text}"]
            if arn_val and arn_val != "N/A":
                tooltip_lines.append(f"Latest ARN / Ack: {arn_val}")
            _set_status_cell(self.table, row_idx, self.COL_STATUS, status_text, status_theme["cell_bg"],
                             tooltip="\n".join(tooltip_lines))

            method_val = r.get("capture_method", "Unknown")
            method_item = _get_item(self.COL_METHOD, color=_capture_method_color(method_val))
            method_item.setText(_method_short(method_val))
            method_item.setToolTip(method_val)

            ts_raw = r.get("last_updated", "")
            ts_item = _get_item(self.COL_UPDATED, color="#8B949E")
            ts_item.setText(_short_local_time(ts_raw))
            ts_item.setToolTip(_format_to_local_time(ts_raw))

            # Actions - reused; the buttons read this row's item when clicked
            self._set_action_cell(row_idx, r, is_grouped=True)

    def _populate_raw_table(self, records: list[dict]):
        """Populates table in granular Individual Raw Captures view."""
        self._client_services_cache = {}
        self._prepare_columns(is_grouped=False)
        self.table.setRowCount(len(records))

        for row_idx, r in enumerate(records):
            def _get_item(col, color=None, align=None):
                item = self.table.item(row_idx, col)
                if not item:
                    item = QTableWidgetItem()
                    self.table.setItem(row_idx, col, item)
                if color: item.setForeground(QColor(color))
                if align is not None: item.setTextAlignment(align)
                return item

            unassigned = bool(r.get('is_unassigned')) or not r.get('client_id')
            client_name = _strip_pan_suffix(r.get('client_name') or "Unknown Client", r.get('pan') or "")
            subline_parts = [f"Record #{r['id']}"]
            if r.get('pan') and not r.get('is_unassigned'):
                subline_parts.append(r['pan'])
            if unassigned:
                subline_parts.append("no matching client")
            subline = " · ".join(subline_parts)
            c_item = _get_item(self.COL_CLIENT)
            c_item.setText(client_name)
            c_item.setData(_SUBLINE_ROLE, subline)
            c_item.setData(_UNASSIGNED_ROLE, unassigned)
            raw_tooltip = f"{client_name}\n{subline}"
            c_item.setToolTip(raw_tooltip)

            portal_str = r.get("service_name") or r.get("portal") or "Portal"
            short, color = _portal_short(portal_str)
            portal_item = _get_item(self.COL_FILING, color=color)
            portal_item.setText(_filing_type(portal_str, short))
            portal_item.setToolTip(portal_str)

            period_val = r.get("period_label") or "N/A"
            period_item = _get_item(self.COL_PERIOD, color="#D29922" if r.get("period_label") else "#8B949E")
            period_item.setText(re.sub(r"\bAssessment Year\b", "AY", period_val))
            period_item.setToolTip(period_val)

            # Submission Status
            status_text, status_theme = _resolve_ltt_submission_status(r)
            _get_item(self.COL_STATUS).setText("")  # Prevent text bleed
            arn_val = r.get("arn_number", "N/A")
            tooltip_lines = [f"Submission Status: {status_text}"]
            if arn_val and arn_val != "N/A":
                tooltip_lines.append(f"ARN / Ack Number: {arn_val}")
            _set_status_cell(self.table, row_idx, self.COL_STATUS, status_text, status_theme["cell_bg"],
                             tooltip="\n".join(tooltip_lines))

            method = r.get("capture_method", "DOM_Tracker")
            method_item = _get_item(self.COL_METHOD, color=_capture_method_color(method))
            method_item.setText(_method_short(method))
            method_item.setToolTip(method)

            ts_raw = r.get("created_at", "")
            ts_item = _get_item(self.COL_UPDATED, color="#8B949E")
            ts_item.setText(_short_local_time(ts_raw))
            ts_item.setToolTip(_format_to_local_time(ts_raw))

            # Actions Column - reused; the buttons read this row's item when clicked
            self._set_action_cell(row_idx, r, is_grouped=False)

    def _full_container(self, item: dict) -> dict:
        """List rows are slim (no payloads); dialogs need the whole container."""
        if item.get("_slim") and item.get("identity_key"):
            full = self.db.get_srpf_containers(limit=1, identity_key=item["identity_key"])
            if full:
                return full[0]
        return item

    def _show_container_dialog(self, container_item: dict):
        container_item = self._full_container(container_item)
        dlg = PayloadInspectorDialog(container_item, db=self.db, is_container=True, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.load_data()

    def _show_payload_dialog(self, dump_item: dict):
        dlg = PayloadInspectorDialog(dump_item, db=self.db, is_container=False, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.load_data()

    def _create_client_from_capture(self, dump_item: dict):
        dump_item = self._full_container(dump_item)
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
        pass

    def _open_dump_folder(self):
        pass

    def _rebuild_raw_dump_txt(self):
        pass

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

    def _open_mcl_mapping_dialog(self):
        try:
            McLMappingDialog(self.db, self).exec()
        except Exception as e:
            QMessageBox.critical(self, "Mapping Error", f"Could not open the mapping dialog: {e}")

    def _open_ltt_rules(self):
        """Plain-language setup of the return forms the LTT sheet tracks (core/ltt)."""
        from ui.windows.ltt_rules_dialog import LttRulesDialog
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dlg = LttRulesDialog(self.db, self)
        finally:
            QApplication.restoreOverrideCursor()
        dlg.exec()

    def _open_ltt_sheet(self):
        """Refreshes ltt_feed.csv, builds ltt_tracker.xlsx the first time, and opens it in Excel."""
        from core.ltt import feed as ltt_feed
        from core.ltt.rules import WORKBOOK_FILE
        app_dir = Path(self.db.app_dir)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ltt_feed.export_feed(self.db, app_dir)
            sheet = app_dir / WORKBOOK_FILE
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
            import build_ltt_tracker
            if not build_ltt_tracker.is_current(sheet):     # missing, or built before a column was added
                build_ltt_tracker.build(app_dir)
            os.startfile(str(sheet))
        except Exception as e:
            QMessageBox.warning(self, "LTT sheet", f"Could not open the LTT sheet: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _open_distill(self):
        """Sera Distill (SDIS Part L): the admin PC only; the dialog is non-modal and kept alive here."""
        if not self.db.is_admin_pc():
            QMessageBox.information(self, "Sera Distill", "Sera Distill runs on the admin PC only.")
            return
        dlg = getattr(self, "_distill_dialog", None)
        if dlg is None:
            from ui.dialogs.sdis_dialog import SdisDialog
            dlg = self._distill_dialog = SdisDialog(self.db, self)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

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

        act_reresolve = menu.addAction(_safe_qta_icon("mdi.database-sync", "#4CF9B7"), "Re-Resolve Identities (SRPF)")
        act_reresolve.triggered.connect(self._reresolve_identities)

        act_map = menu.addAction(_safe_qta_icon("mdi.table-arrow-right", "#4CF9B7"), "Map Datapoints to MCL Columns…")
        act_map.triggered.connect(self._open_mcl_mapping_dialog)

        menu.addSeparator()

        act_ltt_rules = menu.addAction(_safe_qta_icon("mdi.calendar-check", "#4CF9B7"), "LTT form rules…")
        act_ltt_rules.triggered.connect(self._open_ltt_rules)
        act_ltt_open = menu.addAction(_safe_qta_icon("mdi.microsoft-excel", "#4CF9B7"), "Open LTT sheet")
        act_ltt_open.triggered.connect(self._open_ltt_sheet)

        menu.addSeparator()

        act_export_csv = menu.addAction(_safe_qta_icon("mdi.file-export", "#4CF9B7"), "Export Captures (CSV)")
        act_export_csv.triggered.connect(self._export_csv)

        act_distill = menu.addAction(_safe_qta_icon("mdi.flask-outline", "#4CF9B7"), "Distill…")
        act_distill.triggered.connect(self._open_distill)

        menu.addSeparator()

        act_clear = menu.addAction(_safe_qta_icon("mdi.delete-sweep", "#FF6B6B"), "Clear All Captures")
        act_clear.triggered.connect(self._clear_all_dumps)

        # Spawn popup directly below Preferences button
        btn = getattr(self, "btn_preferences", None)
        if btn:
            menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec_(self.cursor().pos())

