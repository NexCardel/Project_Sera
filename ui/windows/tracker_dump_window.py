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
from datetime import datetime, timezone
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSettings
from PySide6.QtGui import QColor, QCursor, QFont,QFontMetrics, QGuiApplication, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QDialog, QFrame,
    QFileDialog, QScrollArea, QStackedWidget,
    QApplication, QSizePolicy, QMenu, QGridLayout, QStyledItemDelegate
)

from ui.utils.profile_parser import extract_profile_from_payload, map_profile_to_mcl_columns
from ui.utils.responsive import WidthMode, mode_for
from ui.components.kit import (
    Banner, CellAction, Card, Chip, ChipBar, Checkbox, DarkListTable, DataGrid, DialogFrame, GridFooter,
    GridFrame, Pill, TextArea,
    IconButton, Input, LabelledAction, PageHeader, Segmented, Select, SettingRow, Tile, ROLE_ACTIONS,
    ROLE_PILL, ROLE_SUB, show_message,
)
from ui.utils.tokens import KIT as K, TOKENS as T
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


_STATUS_PILL_TONES = {
    "Submitted & E-verified": "emerald",
    "Submitted": "green",
    "Submitted (e-verification pending)": "amber",
    "Not submitted": "red",
    "Other EVC": "blue",
    "Option Expired (NA)": "neutral",
    "Not Applicable (NA)": "neutral",
}


def _status_pill(status_text: str) -> tuple[str, str]:
    """The grid delegate's pill for a submission status: (text, tone)."""
    return status_text, _STATUS_PILL_TONES.get(status_text, "red")


def _portal_ink(label: str) -> str:
    """Token colour for the short portal label that _portal_short returns."""
    return {"Income Tax": K["blue_tx"], "GST": T["mint"], "TDS": T["purple"]}.get(label, T["muted"])


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


def _extract_device_name(record: dict) -> str:
    """Extracts device_name from payload JSON or captured_by field, with sensible fallback."""
    if not isinstance(record, dict):
        return "Unknown"
    # 1. Direct device_name in record
    dev = record.get("device_name")
    if dev:
        return str(dev).strip()
    # 2. Check raw_payload_json
    raw_json = record.get("raw_payload_json")
    if raw_json:
        try:
            p = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if isinstance(p, dict):
                dev = p.get("device_name") or (p.get("raw_payload", {}).get("device_name") if isinstance(p.get("raw_payload"), dict) else None)
                if dev:
                    return str(dev).strip()
        except Exception:
            pass
    # 3. Check filing_history for containers
    for fh in reversed(record.get("filing_history") or []):
        dev = _extract_device_name(fh)
        if dev and dev != "Unknown":
            return dev
    # 4. Fallback to captured_by or Unknown
    cb = record.get("captured_by")
    return str(cb).strip() if cb else "Unknown"


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
_TIP_BASE_ROLE = Qt.UserRole + 5        # Client cell tooltip without the auto-hidden values
_HIDDEN_VALUES_ROLE = Qt.UserRole + 6   # {column: text} for the Client tooltip if that column is auto-hidden

CLIENT_MIN_PX = 240          # Client keeps at least this much width whenever the table can give it
CLIENT_FLOOR_PX = 60         # absolute minimum (the table's minimum section size)


def fit_columns(available: int, is_grouped: bool, user_widths: dict, user_hidden: set,
                user_shown: set = frozenset()) -> tuple[dict, set]:
    """Column widths and hidden columns for a table `available` px wide.

    Preferred widths are the ones the table has always used. When they leave Client less than
    CLIENT_MIN_PX, every column first shrinks toward its minimum. Only if the minimums still leave
    too little room are columns hidden, in the order Device -> Updated -> Filing (Status, Period and
    Actions are never hidden). user_hidden is always hidden; user_shown is never auto-hidden;
    user_widths replace the preferred widths. Returns ({col: width} for the visible columns, including
    Client, and the full set of hidden columns, user-hidden and auto-hidden together).
    """
    C = TrackerDumpWindow
    prefs = {
        C.COL_FILING: 120,
        C.COL_PERIOD: 250 if is_grouped else 170,
        C.COL_STATUS: 280,
        C.COL_ACTIONS: 120,
        C.COL_UPDATED: 150,
        C.COL_DEVICE: 130,
    }
    # Minimums are the narrowest a cell can get without clipping its content. They are a little above
    # the plan's first numbers (Filing 90, Status 200, Actions 110, Updated 120): at 1024 px at 80 %
    # those clipped the status pill and the time, and the plan's own rule then hides Device instead.
    mins = {
        C.COL_FILING: 110,
        C.COL_PERIOD: 170 if is_grouped else 140,
        C.COL_STATUS: 270,
        C.COL_ACTIONS: 120,
        C.COL_UPDATED: 130,
        C.COL_DEVICE: 100,
    }
    never_hidden = {C.COL_PERIOD, C.COL_STATUS, C.COL_ACTIONS}
    for col, width in user_widths.items():
        if col in prefs and width > 0:
            prefs[col] = width
    for col in prefs:
        mins[col] = min(mins[col], prefs[col])

    hidden = {c for c in user_hidden if c in prefs and c not in never_hidden}
    budget = available - CLIENT_MIN_PX

    def visible():
        return [c for c in prefs if c not in hidden]

    # Hide only when even the minimums do not fit: Device, then Updated, then Filing.
    for col in (C.COL_DEVICE, C.COL_UPDATED, C.COL_FILING):
        if sum(mins[c] for c in visible()) <= budget:
            break
        if col not in hidden and col not in user_shown:
            hidden.add(col)

    cols = visible()
    widths = {c: prefs[c] for c in cols}
    deficit = sum(prefs[c] for c in cols) - budget
    room = {c: prefs[c] - mins[c] for c in cols}
    total_room = sum(room.values())
    if deficit > 0 and total_room > 0:
        # Shrink in proportion to how much room each column has above its minimum.
        cut = min(deficit, total_room)
        exact = {c: cut * room[c] / total_room for c in cols}
        cuts = {c: int(exact[c]) for c in cols}
        left = cut - sum(cuts.values())
        for c in sorted(cols, key=lambda c: exact[c] - cuts[c], reverse=True):
            if left <= 0:
                break
            if cuts[c] < room[c]:
                cuts[c] += 1
                left -= 1
        widths = {c: prefs[c] - cuts[c] for c in cols}

    if user_shown and sum(widths.values()) > available - CLIENT_FLOOR_PX:
        # A column the user forced on must never push the table past its edge: drop the last
        # forced column (Device first) and fit again. The saved choice stays, so it returns when there is room.
        drop = next((c for c in (C.COL_DEVICE, C.COL_UPDATED, C.COL_FILING) if c in user_shown), None)
        if drop is not None:
            return fit_columns(available, is_grouped, user_widths, user_hidden, set(user_shown) - {drop})

    widths[C.COL_CLIENT] = max(CLIENT_FLOOR_PX, available - sum(widths.values()))
    return widths, hidden


def _optional_cols() -> tuple:
    """Columns the header menu may show or hide. Status, Period and Actions are always visible."""
    C = TrackerDumpWindow
    return (C.COL_FILING, C.COL_UPDATED, C.COL_DEVICE)


def _load_column_choices() -> tuple[set, set]:
    """Per-PC header-menu choices: (hidden, shown). Kept in QSettings, never app_settings (that syncs)."""
    try:
        s = QSettings("AmanAssociates", "ProjectSera")
        optional = _optional_cols()

        def _read(key):
            raw = s.value(key, [])
            if isinstance(raw, str):
                raw = [raw] if raw else []
            return {int(v) for v in raw if str(v).isdigit() and int(v) in optional}

        return _read("tracker_dump_hidden_cols"), _read("tracker_dump_shown_cols")
    except Exception:
        return set(), set()


def _save_column_choices(hidden: set, shown: set) -> None:
    try:
        s = QSettings("AmanAssociates", "ProjectSera")
        s.setValue("tracker_dump_hidden_cols", sorted(hidden))
        s.setValue("tracker_dump_shown_cols", sorted(shown))
        s.sync()
    except Exception:
        pass


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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame(
            "Create client from capture",
            f"Portal: {self.item_data.get('portal', 'Government Portal')} | ARN: {self.item_data.get('arn_number', 'N/A')}",
            self, icon="mdi.account-plus-outline", full=True, scroll=True)
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        frame.add_widget(Banner("Quick onboard from a government network capture. "
                                "Check the prefilled fields, then save to link the captures.", "g"))

        # Extract profile from SRPF container or current capture payload
        extracted_info = self._extract_info_from_payload()
        mcl_cols = self.db.get_mcl_columns()
        mapped_values = map_profile_to_mcl_columns(extracted_info, mcl_cols)

        fields = Card("Client fields")
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

            inp = Input()
            inp.setText(prefill_val)
            self.field_inputs[col_id] = (inp, is_pk, lbl_text)
            fields.add_row(SettingRow(lbl_text, "Internal primary key, required" if is_pk else "", inp, width="wide"))
        frame.add_widget(fields)

        # Service Attachments
        svc_card = Card("Auto-attach compliance services")
        svc_row = QWidget()
        svc_checks_box = QHBoxLayout(svc_row)
        svc_checks_box.setContentsMargins(0, 0, 0, 0)
        self.svc_checkboxes = {}
        all_services = self.db.get_services()
        portal_name = (self.item_data.get("portal", "") or "").lower()

        for s in all_services:
            # Auto-check matching portal service
            cb = Checkbox(s["name"], checked=_service_matches_portal(s["name"], portal_name))
            self.svc_checkboxes[s["id"]] = cb
            svc_checks_box.addWidget(cb)
        svc_checks_box.addStretch()
        svc_card.add_widget(svc_row)
        frame.add_widget(svc_card)

        btn_cancel = frame.add_action("Cancel", variant="secondary")
        btn_cancel.clicked.connect(self.reject)
        btn_save = frame.add_action("Save & Link Captures", variant="primary", icon="mdi.check-circle")
        btn_save.clicked.connect(self._on_save)

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
                    show_message(self, "Mandatory Field Required",
                                 f"The Internal Primary Key '{label}' is mandatory and cannot be empty.", "warning")
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
            show_message(self, "Client Created Successfully",
                         f"Client #{new_cid} was created and successfully linked to {max(linked_count, 1)} capture(s) in Tracker Dump.",
                         "info")
            self.accept()
        except Exception as e:
            show_message(self, "Client Creation Failed", f"Could not create client: {e}", "critical")


class PayloadInspectorDialog(QDialog):
    """Modal dialog displaying formatted profile details, filing history, and raw technical JSON."""
    def __init__(self, item_data: dict, db=None, is_container: bool = False, parent=None):
        super().__init__(parent)
        self.db = db
        self.item_data = item_data
        self.is_container = is_container or bool(item_data.get("filing_history"))
        self.current_screenshot_path = ""
        title_tag = item_data.get('identity_key') or item_data.get('arn_number') or 'Capture'
        self.setWindowTitle(f"SRPF Container Inspector — {title_tag}")
        self.resize(1000, 700)
        self.setMinimumSize(760, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

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
        client_ref = str(item_data.get("client_id_token") or item_data.get("client_id") or "")
        captures = item_data.get('total_captures', 1)
        meta_text = "  ·  ".join(p for p in (
            f"Client #{client_ref}" if client_ref and not is_unreg else "",
            f"PAN {identity_key}" if identity_key != "N/A" else "",
            str(item_data.get('portal') or "Government Portal"),
            f"{captures} capture{'s' if captures != 1 else ''}",
            f"Updated {_format_to_local_time(item_data.get('last_updated') or item_data.get('created_at'))}",
        ) if p)

        frame = DialogFrame(_strip_pan_suffix(client_name, pan_val), meta_text, self,
                            icon="mdi.card-account-details-outline", full=True)
        frame.purpose_label.setToolTip(f"Identity key: {item_data.get('identity_key') or identity_key}")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        if is_unreg:
            frame.add_header_widget(Pill("No matching client", "amber"))
        head_status, _ = _resolve_ltt_submission_status(item_data)
        _, head_tone = _status_pill(head_status)
        status_pill = Pill(head_status, "emerald" if head_tone == "green" else head_tone)
        status_pill.setToolTip("Latest submission status")
        frame.add_header_widget(status_pill)

        tabs = Segmented(["Overview", "Raw JSON", "Notes && Media"])
        pages = QStackedWidget()
        tabs.currentChanged.connect(pages.setCurrentIndex)
        frame.add_widget(tabs)
        frame.add_widget(pages, 1)

        # Tab 1: Overview -- details cards over the filing history
        tab_summary = QWidget()
        sum_layout = QVBoxLayout(tab_summary)
        sum_layout.setContentsMargins(0, 0, 0, 0)
        sum_layout.setSpacing(T["s_3"])

        def _field_grid(pairs, cols):
            host = QWidget()
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(T["s_4"])
            grid.setVerticalSpacing(T["s_2"])
            shown = [(k, v) for k, v in pairs if v and str(v).strip()]
            for i, (key, val) in enumerate(shown):
                row, col = (i // cols) * 2, i % cols
                k = QLabel(key)
                k.setObjectName("KitFieldKey")
                v = QLabel(str(val))
                v.setObjectName("KitFieldVal")
                v.setWordWrap(True)
                v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(k, row, col, Qt.AlignTop)
                grid.addWidget(v, row + 1, col, Qt.AlignTop)
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            return host, len(shown)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(T["s_3"])

        client_card = Card("Client details")
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
        client_card.add_widget(grid)
        if not n_client:
            empty = QLabel("No client details in this capture.")
            empty.setObjectName("KitEmpty")
            client_card.add_widget(empty)
        cards_row.addWidget(client_card, stretch=3)

        if sgt_i_info:
            sgti_card = Card("SGT-I — advisory, read-only")
            sgti_card.setToolTip("SGT Intelligence's own facts about this row (route, explanation, second "
                                  "opinion). They sit beside the Core's captured values and never replace "
                                  "them (blueprint 14.2 rule 4).")
            sgti_text = QLabel(json.dumps(sgt_i_info, indent=2, sort_keys=True, ensure_ascii=False))
            sgti_text.setFont(QFont("Consolas", 10))
            sgti_text.setWordWrap(True)
            sgti_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            sgti_card.add_widget(sgti_text)
            cards_row.addWidget(sgti_card, stretch=2)

        sum_layout.addLayout(cards_row)

        # SDIS Others and Profile builder values, per portal (blueprint S.4), sdis_mcl labels
        for portal, pairs in self._sdis_values(item_data, raw_json):
            sdis_card = Card(f"{portal} — portal values (SDIS)")
            grid, _ = _field_grid(pairs, cols=3)
            sdis_card.add_widget(grid)
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

        hist_card = Card(f"Captured filings & obligations ({len(filing_history)})")
        hist_table = DarkListTable(0, 6)
        hist_table.setHorizontalHeaderLabels(["Period / AY", "ARN / Ack number", "Submission status", "Portal", "Captured by", "Captured at"])
        hist_table.set_column(0, kind="strong", width=120)
        hist_table.set_column(1, kind="mono", width=170)
        hist_table.set_column(2, kind="text", width=170, stretch=0)
        hist_table.set_column(3, kind="text")
        hist_table.set_column(4, kind="text", width=150)
        hist_table.set_column(5, kind="mono", width=140)
        hist_table.setRowCount(len(filing_history))

        for idx, fh in enumerate(reversed(filing_history)):
            p_item = QTableWidgetItem(fh.get("period_label") or "N/A")
            p_item.setForeground(QColor(T["warn"]))
            hist_table.setItem(idx, 0, p_item)

            arn_val = fh.get("arn") or "N/A"
            arn_item = QTableWidgetItem(arn_val)
            arn_item.setForeground(QColor(T["text"] if arn_val != "N/A" else T["faint"]))
            hist_table.setItem(idx, 1, arn_item)

            s_text, _ = _resolve_ltt_submission_status(fh)
            status_item = QTableWidgetItem()
            status_item.setData(ROLE_PILL, _status_pill(s_text))
            status_item.setToolTip(f"Submission Status: {s_text}")
            hist_table.setItem(idx, 2, status_item)

            portal_full = fh.get("portal") or "Income Tax"
            short, _ = _portal_short(portal_full)
            port_item = QTableWidgetItem(short)
            port_item.setForeground(QColor(_portal_ink(short)))
            port_item.setToolTip(portal_full)
            hist_table.setItem(idx, 3, port_item)

            method = _extract_device_name(fh)
            m_item = QTableWidgetItem(method)
            m_item.setForeground(QColor(T["warn"]))
            m_item.setToolTip(f"Device: {method}")
            hist_table.setItem(idx, 4, m_item)

            ts_raw = fh.get("created_at") or ""
            ts_item = QTableWidgetItem(_short_local_time(ts_raw))
            ts_item.setForeground(QColor(T["muted"]))
            ts_item.setToolTip(_format_to_local_time(ts_raw))
            hist_table.setItem(idx, 5, ts_item)

        hist_card.add_widget(hist_table, 1)
        sum_layout.addWidget(hist_card, stretch=1)

        overview_scroll = QScrollArea()
        overview_scroll.setObjectName("KitDlgScroll")
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setFrameShape(QFrame.NoFrame)
        overview_scroll.setWidget(tab_summary)
        pages.addWidget(overview_scroll)

        # Tab 2: Raw Technical JSON
        tab_json = QWidget()
        json_layout = QVBoxLayout(tab_json)
        json_layout.setContentsMargins(0, 0, 0, 0)

        self.txt_json = TextArea()
        self.txt_json.setReadOnly(True)
        self.txt_json.setFont(QFont("Consolas", 10))

        raw_str = item_data.get('raw_payload_json')
        if not raw_str:
            raw_str = json.dumps(item_data, indent=4)
        try:
            parsed = json.loads(raw_str) if isinstance(raw_str, str) else raw_str
            formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
        except Exception:
            formatted = str(raw_str)

        self.txt_json.setPlainText(formatted)
        json_layout.addWidget(self.txt_json, 1)
        pages.addWidget(tab_json)

        # Tab 3: Notes & Media
        tab_notes = QWidget()
        notes_layout = QVBoxLayout(tab_notes)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        notes_layout.setSpacing(T["s_3"])

        self.txt_notes = TextArea(placeholder="Write notes here...")

        media_toolbar = QHBoxLayout()
        media_toolbar.setSpacing(T["s_2"])

        self.btn_attach_img = IconButton("mdi.paperclip", "Attach Image", tab_notes, variant="bordered")
        self.btn_paste_img = IconButton("mdi.content-paste", "Paste Image", tab_notes, variant="bordered")
        self.btn_ocr_img = IconButton("mdi.text-recognition", "Scan Text (OCR)", tab_notes, variant="bordered")
        self.btn_clear_img = IconButton("mdi.close-circle-outline", "Clear Image", tab_notes, variant="bordered")

        media_toolbar.addWidget(self.btn_attach_img)
        media_toolbar.addWidget(self.btn_paste_img)
        media_toolbar.addWidget(self.btn_ocr_img)
        media_toolbar.addWidget(self.btn_clear_img)
        media_toolbar.addStretch()

        self.lbl_save_status = Pill("", "neutral")
        self.lbl_save_status.setVisible(False)
        media_toolbar.addWidget(self.lbl_save_status)

        self.btn_expand_img = IconButton("mdi.fullscreen", "View Full Image", tab_notes, variant="bordered")
        media_toolbar.addWidget(self.btn_expand_img)

        self.lbl_image_preview = QLabel("No image attached")
        self.lbl_image_preview.setObjectName("KitPreview")
        self.lbl_image_preview.setAlignment(Qt.AlignCenter)
        self.lbl_image_preview.setMinimumHeight(200)

        notes_layout.addWidget(self.txt_notes, stretch=1)
        notes_layout.addLayout(media_toolbar)
        notes_layout.addWidget(self.lbl_image_preview)

        pages.addWidget(tab_notes)

        # Connect signals
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

        # Actions
        if is_unreg and self.db:
            btn_create = frame.add_action("Create client from this container", variant="primary",
                                          icon="mdi.account-plus")
            btn_create.clicked.connect(self._create_client)
        frame.add_footer_spacer()

        btn_copy = frame.add_action("Copy Container Summary", variant="secondary")

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

        btn_close = frame.add_action("Close", variant="secondary")
        btn_close.clicked.connect(self.accept)

    def _set_save_status(self, text, tone="neutral"):
        self.lbl_save_status.set_text(text)
        self.lbl_save_status.set_tone(tone)
        self.lbl_save_status.setVisible(bool(text))

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
        self._set_save_status("Saving...", "amber")
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
                show_message(self, "Error", f"Could not open image: {e}", "warning")

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
            show_message(self, "No Image", "No image found in clipboard.")

    def _trigger_ocr_manually(self):
        if hasattr(self, 'current_screenshot_path') and self.current_screenshot_path and os.path.exists(self.current_screenshot_path):
            self._run_ocr(self.current_screenshot_path)
        else:
            show_message(self, "No Image", "Please attach or paste an image first to scan text.")

    def _run_ocr(self, img_path):
        from ui.utils.ocr_worker import OCRWorker
        self._set_save_status("Scanning OCR...", "blue")
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
            self._set_save_status("OCR Failed", "red")
            QTimer.singleShot(2500, lambda: self._set_save_status(""))

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
                self._set_save_status("Failed to save image", "red")
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

            self._set_save_status("✓ Saved!", "emerald")
            QTimer.singleShot(2500, lambda: self._set_save_status(""))
        except Exception as e:
            self._set_save_status("Failed to save to database", "red")

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


class _ResolveThread(QThread):
    """Re-resolves every capture into its SRPF container, off the UI thread (the periodic pass)."""
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db

    def run(self):
        try:
            self.done.emit(self._db.re_resolve_all_tracker_dumps())
        except Exception as e:
            self.failed.emit(str(e))


_LIVE_RESOLVE_THREADS: set = set()   # a pass outlives the window closed mid-pass until it finishes


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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Map captured datapoints to MCL columns", "", self,
                            icon="mdi.table-arrow-right", full=True)
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)
        frame.add_close_button()

        hint = QLabel("Pick the MCL column a datapoint belongs to where auto-mapping misses it. "
                      "When a capture arrives for a registered client, mapped values fill that client's "
                      "blank columns; existing values are never overwritten.")
        hint.setObjectName("KitDlgMessage")
        hint.setWordWrap(True)
        frame.add_widget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(T["s_4"])
        self.txt_filter = Input()
        self.txt_filter.setPlaceholderText("Search datapoints or sample values…")
        self.txt_filter.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(120)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.txt_filter.textChanged.connect(lambda _: self._filter_timer.start())
        self.chk_unmapped = Checkbox("Only unmapped")
        self.chk_unmapped.toggled.connect(self._apply_filter)
        bar.addWidget(self.txt_filter, stretch=1)
        bar.addWidget(self.chk_unmapped)
        frame.body_layout.addLayout(bar)

        self.table = DarkListTable(0, 5)
        self.table.setHorizontalHeaderLabels(["DATAPOINT", "SEEN", "SAMPLE VALUE", "AUTO-MAPPED TO", "MAP TO MCL COLUMN"])
        self.table.set_column(self._KEY, kind="mono", width=210, stretch=0)
        self.table.set_column(self._SEEN, kind="num", width=64, stretch=0)
        self.table.set_column(self._SAMPLE, kind="text", stretch=1)
        self.table.set_column(self._AUTO, kind="text", width=170, stretch=0)
        self.table.set_column(self._MAP, kind="text", width=210, stretch=0)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setItemDelegateForColumn(
            self._MAP, _McLColumnDelegate([(c["label"], c["id"]) for c in self._mcl_cols], self.table))
        self.table.cellClicked.connect(self._on_cell_clicked)
        frame.add_widget(self.table, 1)

        footer = GridFooter(tone="list")
        self.lbl_status = footer.count_label
        self.lbl_status.setText("Reading recent captures…")
        frame.add_widget(footer)

        self.btn_apply = frame.add_action(
            "Save && apply to registered clients",
            tooltip="Saves the mappings, then fills blank MCL columns of registered clients from their existing captures.")
        frame.add_footer_spacer()
        btn_close = frame.add_action("Close")
        btn_close.clicked.connect(self.reject)
        self.btn_save = frame.add_action("Save", variant="primary")
        self.btn_apply.clicked.connect(lambda: self._save(apply_now=True))
        self.btn_save.clicked.connect(lambda: self._save(apply_now=False))
        self.btn_save.setEnabled(False)
        self.btn_apply.setEnabled(False)

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

        green, dim = QColor(T["mint"]), QColor(T["faint"])
        muted, text = QColor(T["muted"]), QColor(T["text"])
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(points))
        for r, p in enumerate(points):
            key_item = QTableWidgetItem(p["key"])
            key_item.setToolTip(p["key"])
            seen_item = QTableWidgetItem(str(p["count"]))
            seen_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            seen_item.setForeground(dim)
            sample_item = QTableWidgetItem(p["sample"])
            sample_item.setToolTip(p["sample"])
            sample_item.setForeground(muted)
            auto_item = QTableWidgetItem(self._col_label.get(p["auto_column_id"], ""))
            auto_item.setForeground(green)
            col_id = saved.get(p["key"])
            map_item = QTableWidgetItem(self._col_label.get(col_id, "—"))
            map_item.setData(Qt.UserRole, col_id)
            map_item.setForeground(text if col_id is not None else dim)
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
                show_message(self, "Mappings saved",
                             f"{len(mappings)} mapping(s) saved. {changed} registered client record(s) updated.")
            else:
                show_message(self, "Mappings saved",
                             f"{len(mappings)} mapping(s) saved. They apply to future captures of registered clients.")
            self.accept()
        except Exception as e:
            show_message(self, "Save failed", f"Could not save the mappings: {e}", "critical")


class TrackerDumpWindow(QWidget):
    """Full-featured workspace for inspecting client tracker dumps and SRPF unified containers."""

    skip_page_fade = True

    service_action_requested = Signal(int, dict)

    # Table columns (same order in both views)
    COL_CLIENT, COL_FILING, COL_PERIOD, COL_STATUS, COL_ACTIONS, COL_UPDATED, COL_DEVICE = range(7)
    COL_METHOD = COL_DEVICE  # Backwards compatibility alias
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
        # Periodic SRPF re-resolve: runs every 30 seconds while this window is open (see showEvent)
        self._resolve_timer = QTimer(self)
        self._resolve_timer.setInterval(30_000)
        self._resolve_timer.timeout.connect(self._start_periodic_resolve)
        self._resolve_thread = None
        # Width mode (WIDE / COMPACT) follows the page's own width; see ui/utils/responsive.py.
        self._width_mode = WidthMode.WIDE
        self._auto_hidden_cols = set()
        self._user_hidden_cols, self._user_shown_cols = _load_column_choices()
        self._seg_buttons = []       # (button, full text, short text)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(T["s_6"], T["s_6"], T["s_6"], T["s_6"])
        main_layout.setSpacing(T["s_4"])

        # ---- Header: title on the left, page actions on the right ----
        self.page_header = PageHeader(
            "Tracker Dump", lead="Filings captured by the browser extension, VSDC and SGT, grouped per client")
        self._lbl_subtitle = self.page_header.lead_label

        btn_refresh = IconButton("mdi.refresh", "Refresh (F5)", variant="bordered")
        btn_refresh.clicked.connect(self.load_data)
        self.page_header.add_action(btn_refresh)
        refresh_sc = QShortcut(QKeySequence(Qt.Key_F5), self)
        refresh_sc.setContext(Qt.WidgetWithChildrenShortcut)
        refresh_sc.activated.connect(self.load_data)

        self.btn_preferences = LabelledAction(
            "Tools  ▾", variant="secondary", icon="mdi.cog-outline",
            tooltip="Re-resolve identities, reports, CSV export, clear captures")
        self.btn_preferences.clicked.connect(self._show_preferences_menu)
        self.page_header.add_action(self.btn_preferences)
        main_layout.addWidget(self.page_header)

        # ---- Summary strip: totals over everything loaded; each tile applies a filter ----
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(T["s_2"])
        self._summary_tiles = {}
        for key, caption, tone, tip in (
            ("total", "Client containers", None, "Show everything (clears all filters)"),
            ("unregistered", "Need a client", "amber", "Captures not matched to a registered client"),
            ("pending", "Awaiting e-verification", "amber", "Submitted, e-verification still pending"),
            ("not_submitted", "Not submitted", "red", "Not submitted or option expired"),
            ("today", "Captured today", "emerald", "Updated since midnight"),
        ):
            tile = Tile("0", caption, tone=tone, tooltip=tip)
            tile.clicked.connect(lambda _=False, k=key: self._on_summary_tile(k))
            tiles_row.addWidget(tile, stretch=1)
            self._summary_tiles[key] = tile
        main_layout.addLayout(tiles_row)

        # ---- Filter row ----
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(T["s_2"])
        main_layout.addLayout(filter_layout)
        self._filter_layout = filter_layout
        # COMPACT moves the filter chips onto this second row (hidden in WIDE, so it takes no space).
        self._chip_row = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_row)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(T["s_2"])
        self._chip_layout.addStretch()
        self._chip_row.hide()
        self._chips_on_row = False
        main_layout.addWidget(self._chip_row)

        # The view-mode combo stays the source of truth (load_data reads it); the segmented
        # switch in front of it just drives it.
        self.cmb_view_mode = QComboBox(self)
        self.cmb_view_mode.addItems(["Grouped by Client Container (SRPF)", "Individual Raw Captures"])
        self.cmb_view_mode.hide()
        self.cmb_view_mode.currentIndexChanged.connect(self.load_data)
        self.cmb_view_mode.currentIndexChanged.connect(self._sync_view_buttons)

        self.view_switch = Segmented(["Containers", "Raw captures"])
        for button, (text, short, tip) in zip(self.view_switch.buttons, (
            ("Containers", "Containers", "One row per client (SRPF container)"),
            ("Raw captures", "Raw", "One row per individual capture"),
        )):
            button.setToolTip(tip)
            self._seg_buttons.append((button, text, short))
        self.view_switch.currentChanged.connect(self.cmb_view_mode.setCurrentIndex)
        filter_layout.addWidget(self.view_switch)

        self.txt_search = Input(placeholder="Search client, PAN, GSTIN, ARN, period, portal…", icon="mdi.magnify")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._on_search_text_changed)
        self.txt_search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        self.chip_bar = ChipBar()
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
            chip = Chip(label, tooltip=combo.toolTip())
            menu = QMenu(chip)
            for i in range(combo.count()):
                act = menu.addAction(combo.itemText(i))
                act.triggered.connect(lambda _=False, c=combo, idx=i: c.setCurrentIndex(idx))
                if i == 0:
                    menu.addSeparator()
            chip.setMenu(menu)
            self.chip_bar.add_chip(chip, id=label)
            self._filter_chips.append((chip, combo, label, short))
        filter_layout.addWidget(self.chip_bar)
        filter_layout.addStretch()

        self.btn_reset_filters = LabelledAction(
            "Reset", variant="ghost", icon="mdi.filter-off", tooltip="Reset all search & filter options")
        self.btn_reset_filters.clicked.connect(self._reset_filters)
        filter_layout.addWidget(self.btn_reset_filters, stretch=0)

        # Data table: cells, pills and row buttons are drawn by the grid's delegate
        self.table = DataGrid(tone="dark")
        self.table.set_tall_rows(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._user_col_widths = {}
        self.table.horizontalHeader().sectionResized.connect(self._on_section_resized)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._on_header_context_menu)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.actionTriggered.connect(self._on_grid_action)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        # ---- Footer: entry count on the left, paging on the right ----
        self.grid_footer = GridFooter(tone="dark")
        self.lbl_page_info = self.grid_footer.count_label
        self.lbl_page_info.setText("Showing 0 to 0 of 0 entries")

        self._lbl_per_page = QLabel("Rows per page:")
        self._lbl_per_page.setObjectName("KitGridFootText")
        self.grid_footer.add_widget(self._lbl_per_page)

        self.cmb_page_size = Select(items=["25", "50", "100", "200", "All"])
        self.cmb_page_size.setFixedHeight(K["h_sm"])
        self.cmb_page_size.setCurrentText("25")
        self.cmb_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        self.grid_footer.add_widget(self.cmb_page_size)

        self.btn_prev_page = LabelledAction("◀ Prev", variant="ghost", sm=True)
        self.btn_prev_page.clicked.connect(self._prev_page)
        self.grid_footer.add_widget(self.btn_prev_page)

        self.lbl_current_page = QLabel("Page 1 / 1")
        self.lbl_current_page.setObjectName("KitGridFootText")
        self.grid_footer.add_widget(self.lbl_current_page)

        self.btn_next_page = LabelledAction("Next ▶", variant="ghost", sm=True)
        self.btn_next_page.clicked.connect(self._next_page)
        self.grid_footer.add_widget(self.btn_next_page)

        self.grid_frame = GridFrame(self.table, self.grid_footer)
        main_layout.addWidget(self.grid_frame, stretch=1)

        self._sync_view_buttons()
        self._sync_filter_indicators()
        self._apply_width_mode(WidthMode.WIDE)

        # Initial Load
        if self._defer_first_load:
            self._first_load_pending = True
        else:
            self.load_data()

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_load_pending:
            self.load_data()
        if hasattr(self, "_col_resize_timer"):
            self._col_resize_timer.start()
        if hasattr(self, "_resolve_timer"):
            self._resolve_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "_resolve_timer"):
            self._resolve_timer.stop()

    def _start_periodic_resolve(self):
        """Timer tick: one re-resolve pass in the background, unless the previous pass is still running."""
        if self._resolve_thread is not None and self._resolve_thread.isRunning():
            return
        thread = _ResolveThread(self.db, self)
        thread.done.connect(self._on_periodic_resolve_done)
        thread.failed.connect(lambda msg: print(f"[SRPF] periodic re-resolve failed: {msg}"))
        thread.finished.connect(lambda t=thread: _LIVE_RESOLVE_THREADS.discard(t))
        _LIVE_RESOLVE_THREADS.add(thread)
        self._resolve_thread = thread
        thread.start()

    def _on_periodic_resolve_done(self, updated: int):
        """Reloads the list only when the pass changed a capture's client, and keeps the page the user is on."""
        if not updated or not self.isVisible():
            return
        page = self._current_page
        self.load_data()          # resets to page 1 ...
        self._current_page = page  # ... so put the user's page back
        self._apply_filters()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_col_resize_timer"):
            self._col_resize_timer.start()
        current = getattr(self, "_width_mode", WidthMode.WIDE)
        mode = mode_for(self.width(), current)
        if mode is not current:
            self._apply_width_mode(mode)

    def _apply_width_mode(self, mode: WidthMode):
        """WIDE is the layout Tracker Dump has always had. COMPACT trims the header and filters for a
        narrow page (see the plan, docs/ui-scale-adaptive-layout-plan.md, B2)."""
        self._width_mode = mode
        compact = mode is WidthMode.COMPACT
        self._lbl_subtitle.setVisible(not compact)

        self.txt_search.setMinimumWidth(180)
        self.txt_search.setMaximumWidth(320)

        for button, text, short in self._seg_buttons:
            label = short if compact else text
            button.setText(label)
            bold = QFont(button.font())
            bold.setBold(True)
            button.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(label) + 32)

        self._move_chips(compact)

        for tile in self._summary_tiles.values():
            tile.apply_width_mode(mode)

        self._lbl_per_page.setVisible(not compact)
        self._col_resize_timer.start()

    def _move_chips(self, compact: bool):
        if compact != self._chips_on_row:
            if compact:
                self._filter_layout.removeWidget(self.chip_bar)
                self._chip_layout.insertWidget(self._chip_layout.count() - 1, self.chip_bar)   # before the stretch
            else:
                self._chip_layout.removeWidget(self.chip_bar)
                self._filter_layout.insertWidget(self._filter_spacer_index(), self.chip_bar)
            self._chips_on_row = compact
        self._chip_row.setVisible(compact)

    def _filter_spacer_index(self) -> int:
        """Index of the stretch that sits just before the Reset button (the last spacer in the row)."""
        for i in reversed(range(self._filter_layout.count())):
            if self._filter_layout.itemAt(i).spacerItem() is not None:
                return i
        return self._filter_layout.count()

    def _adjust_table_columns(self):
        """Fits the columns to the table's width (see fit_columns). Nothing ratchets wider on refresh,
        so no horizontal scrollbar. A column dragged by hand keeps its width while there is room."""
        if not hasattr(self, "table") or self.table.columnCount() != self.COLUMN_COUNT:
            return
        is_grouped = self.cmb_view_mode.currentIndex() == 0 if hasattr(self, "cmb_view_mode") else True
        user_widths = {col: w for (grouped, col), w in self._user_col_widths.items() if grouped == is_grouped}
        widths, hidden = fit_columns(self.table.viewport().width(), is_grouped, user_widths,
                                     self._user_hidden_cols, self._user_shown_cols)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_CLIENT, QHeaderView.Stretch)
        header.setMinimumSectionSize(60)
        self._auto_sizing = True
        try:
            for col in range(self.COLUMN_COUNT):
                if col == self.COL_CLIENT:
                    continue
                self.table.setColumnHidden(col, col in hidden)
                if col not in hidden:
                    self.table.setColumnWidth(col, widths[col])
        finally:
            self._auto_sizing = False
        auto_hidden = hidden - self._user_hidden_cols
        if auto_hidden != self._auto_hidden_cols:
            self._auto_hidden_cols = auto_hidden
            self._refresh_client_tooltips()

    def _on_header_context_menu(self, pos):
        """Tick the optional columns to show or hide them. Choices are kept per PC and beat auto-hiding."""
        header = self.table.horizontalHeader()
        labels = {self.COL_FILING: "Filing type", self.COL_UPDATED: "Updated", self.COL_DEVICE: "Captured by"}
        menu = QMenu(self)
        for col in _optional_cols():
            act = menu.addAction(labels[col])
            act.setCheckable(True)
            act.setChecked(not self.table.isColumnHidden(col))
            act.toggled.connect(lambda checked, c=col: self._set_column_visible(c, checked))
        menu.addSeparator()
        menu.addAction("Automatic (fit to width)").triggered.connect(self._reset_column_choices)
        menu.exec(header.mapToGlobal(pos))

    def _set_column_visible(self, col: int, visible: bool):
        if visible:
            self._user_shown_cols.add(col)
            self._user_hidden_cols.discard(col)
        else:
            self._user_hidden_cols.add(col)
            self._user_shown_cols.discard(col)
        _save_column_choices(self._user_hidden_cols, self._user_shown_cols)
        self._adjust_table_columns()

    def _reset_column_choices(self):
        self._user_hidden_cols.clear()
        self._user_shown_cols.clear()
        _save_column_choices(self._user_hidden_cols, self._user_shown_cols)
        self._adjust_table_columns()

    def _client_tooltip(self, item) -> str:
        """Client tooltip, plus the values of any column that fitting the width has hidden."""
        base = item.data(_TIP_BASE_ROLE) or ""
        values = item.data(_HIDDEN_VALUES_ROLE) or {}
        parts = [f"{label}: {values[col]}"
                 for col, label in ((self.COL_FILING, "Filing"), (self.COL_UPDATED, "Updated"), (self.COL_DEVICE, "Device"))
                 if col in self._auto_hidden_cols and values.get(col)]
        return "\n".join([base, " · ".join(parts)]) if parts else base

    def _finish_client_tooltip(self, c_item, row_idx: int):
        """Called once a row's cells are filled: remembers what a hidden column would show, then sets the tooltip."""
        c_item.setData(_HIDDEN_VALUES_ROLE, {
            col: self.table.item(row_idx, col).text()
            for col in (self.COL_FILING, self.COL_UPDATED, self.COL_DEVICE)
        })
        c_item.setToolTip(self._client_tooltip(c_item))

    def _refresh_client_tooltips(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_CLIENT)
            if item is not None and item.data(_TIP_BASE_ROLE) is not None:
                item.setToolTip(self._client_tooltip(item))

    def _on_section_resized(self, col: int, _old: int, new: int):
        # A hidden column reports size 0 as Qt lays out; that is not a width the user dragged to.
        if getattr(self, "_auto_sizing", False) or col == self.COL_CLIENT or new <= 0 or self.table.isColumnHidden(col):
            return
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

        if is_grouped:
            act_inspect = menu.addAction(_safe_qta_icon("mdi.eye", T["mint"]), "Inspect Client Container")
            act_inspect.triggered.connect(lambda: self._show_container_dialog(item))
            if item.get('is_unassigned'):
                act_create = menu.addAction(_safe_qta_icon("mdi.account-plus", T["em2"]), "+ Create Client")
                act_create.triggered.connect(lambda: self._create_client_from_capture(item))
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", T["dangertx"]), "Delete Container")
            act_del.triggered.connect(lambda: self._delete_srpf_container(item.get("identity_key")))
        else:
            act_inspect = menu.addAction(_safe_qta_icon("mdi.code-json", T["mint"]), "View Raw Payload")
            act_inspect.triggered.connect(lambda: self._show_payload_dialog(item))
            if item.get('is_unassigned') or not item.get('client_id'):
                act_create = menu.addAction(_safe_qta_icon("mdi.account-plus", T["em2"]), "+ Create Client")
                act_create.triggered.connect(lambda: self._create_client_from_capture(item))
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", T["dangertx"]), "Delete Record")
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
        # tile key -> (combo attribute, item text that tile selects). Looked up by text, not by
        # position, so the tile cannot drift from the dropdown when items are added or reordered.
        "unregistered": ("cmb_client", "Unregistered / Action Required"),
        "pending": ("cmb_status", "Pending e-Verification"),
        "not_submitted": ("cmb_status", "Not submitted"),
        "today": ("cmb_date", "Today"),
    }

    def _tile_index(self, key: str) -> int:
        attr, text = self._TILE_FILTERS[key]
        return getattr(self, attr).findText(text)

    def _on_summary_tile(self, key: str):
        """A tile sets its filter; clicking the active tile again clears it. 'total' clears all."""
        if key == "total":
            self._reset_filters()
            return
        attr, _ = self._TILE_FILTERS[key]
        combo = getattr(self, attr)
        idx = self._tile_index(key)
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
        for key, tile in self._summary_tiles.items():
            tile.set_value(f"{counts[key]:,}")
        self._summary_tiles["total"].set_label("Client containers" if is_grouped else "Raw captures")

    def _sync_filter_indicators(self):
        """Highlights filters that are switched on, checks the matching tile, shows Reset only when useful."""
        if not hasattr(self, "_summary_tiles"):
            return
        combos = (self.cmb_status, self.cmb_portal, self.cmb_client, self.cmb_date, self.cmb_source)
        any_active = bool(self.txt_search.text().strip()) or any(combo.currentIndex() > 0 for combo in combos)
        for key, tile in self._summary_tiles.items():
            if key == "total":
                tile.setChecked(not any_active)
            else:
                attr, _ = self._TILE_FILTERS[key]
                tile.setChecked(getattr(self, attr).currentIndex() == self._tile_index(key))
        self.btn_reset_filters.setVisible(any_active)
        for chip, combo, label, short in getattr(self, "_filter_chips", []):
            on = combo.currentIndex() > 0
            chip.setChecked(on)
            value = short.get(combo.currentText(), combo.currentText())
            chip.setText(f"{label}: {value}" if on else label)     # the chip draws its own ▾

    def _sync_view_buttons(self, *_):
        if hasattr(self, "view_switch"):
            self.view_switch.set_current(self.cmb_view_mode.currentIndex())

    def load_data(self):
        """Fetch tracker dumps or SRPF unified containers from database."""
        self._first_load_pending = False
        try:
            gen_at_start = self.db.data_generation() if hasattr(self.db, "data_generation") else None
            raw_gen_at_start = self.db.raw_generation() if hasattr(self.db, "raw_generation") else None
            is_grouped = (self.cmb_view_mode.currentIndex() == 0)
            # Every row is loaded so search and the filters see the whole database; the slim reads
            # keep payloads out of the list, and a dialog fetches its one row's payload on demand.
            if is_grouped:
                self._dumps_cache = self.db.get_srpf_containers(limit=-1, slim=True)
                # Default chronological organisation: latest entry at top
                self._dumps_cache.sort(key=lambda c: str(c.get("last_updated") or ""), reverse=True)
            else:
                self._dumps_cache = self.db.get_tracker_dumps(limit=-1, slim=True)
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
                # A client container matches when ANY of its filings is on the portal, not just the latest
                filings = d.get("filing_history") or [d]
                p_text = " ".join(f"{f.get('portal', '')} {f.get('service_name', '')} {f.get('form_type', '')}"
                                  for f in filings).lower()
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
                # a container also matches on any of its earlier filings' ARNs, periods and portals
                for h in d.get("filing_history") or ():
                    match_fields += [h.get("arn", ""), h.get("period_label", ""), h.get("portal", "")]
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

    def _row_actions(self, item: dict, is_grouped: bool) -> list:
        """The buttons the grid draws in a row's Actions cell: autofill (registered clients), inspect,
        create client (unregistered rows) and the ⋮ overflow menu. A click arrives in _on_grid_action."""
        actions = []
        if bool(item.get("client_id")) and not item.get("is_unassigned"):
            actions.append(self._autofill_action(item))
        if is_grouped:
            tot = item.get("total_captures", 1)
            actions.append(CellAction("view", "mdi.eye-outline", f"Inspect ({tot} capture{'s' if tot != 1 else ''})"))
        else:
            actions.append(CellAction("view", "mdi.eye-outline", "View Payload"))
        if item.get("is_unassigned") or (not is_grouped and not item.get("client_id")):
            actions.append(CellAction("create", "mdi.account-plus", "Create a client from this capture"))
        actions.append(CellAction("more", "mdi.dots-vertical", "More actions (Create Client, Delete)"))
        return actions

    def _autofill_action(self, item: dict) -> CellAction:
        service = self._service_for_row(item)
        if not service:
            portal_name = item.get("service_name") or item.get("portal") or "Portal"
            return CellAction("autofill", "mdi.flash", f"Fast Autofill: {portal_name}", primary=True, colour=T["danger"])
        specs = {
            automation.ACTION_AUTOFILL: ("Fast Autofill", "mdi.flash"),
            automation.ACTION_SMTI: ("SMTI Manual Assist", "mdi.clipboard-account-outline"),
            automation.ACTION_MECP: ("MECP Manual Copy", "mdi.content-copy"),
        }
        mode = automation.service_action_mode(service)
        label, icon = specs.get(mode, specs[automation.ACTION_AUTOFILL])
        tip = f"{label}: {service.get('name', 'Service')}"
        if mode in specs and mode != automation.ACTION_AUTOFILL:
            return CellAction("autofill", _safe_qta_icon(icon, T["danger"]), tip)
        return CellAction("autofill", icon, tip, primary=True, colour=T["danger"])

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

    def _on_grid_action(self, row: int, _col: int, key: str) -> None:
        items = getattr(self, "_current_page_items", None) or []
        if not 0 <= row < len(items):
            return
        item = items[row]
        is_grouped = self.cmb_view_mode.currentIndex() == 0
        if key == "autofill":
            self._on_action_autofill(item)
        elif key == "view":
            if is_grouped:
                self._show_container_dialog(item)
            else:
                self._show_payload_dialog(item)
        elif key == "create":
            self._create_client_from_capture(item)
        elif key == "more":
            self._show_row_action_menu(item, is_grouped, QCursor.pos())

    def _on_action_autofill(self, item: dict) -> None:
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

    def _show_row_action_menu(self, item: dict, is_grouped: bool, global_pos):
        """Overflow menu under the ⋮ button - mirrors the right-click context menu."""
        menu = QMenu(self)

        if is_grouped:
            needs_create = item.get('is_unassigned')
        else:
            needs_create = item.get('is_unassigned') or not item.get('client_id')

        if needs_create:
            act_create = menu.addAction(_safe_qta_icon("mdi.account-plus", T["em2"]), "+ Create Client")
            act_create.triggered.connect(lambda: self._create_client_from_capture(item))

        if is_grouped:
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", T["dangertx"]), "Delete Container")
            act_del.triggered.connect(lambda: self._delete_srpf_container(item.get("identity_key")))
        else:
            act_del = menu.addAction(_safe_qta_icon("mdi.trash-can-outline", T["dangertx"]), "Delete Record")
            act_del.triggered.connect(lambda: self._delete_dump(item.get("id")))

        menu.exec_(global_pos)

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

    def _table_item(self, row_idx: int, col: int, color=None) -> QTableWidgetItem:
        """The row's item in `col` (created on first use); its text colour is reset on every refresh."""
        item = self.table.item(row_idx, col)
        if not item:
            item = QTableWidgetItem()
            self.table.setItem(row_idx, col, item)
        if color:
            item.setForeground(QColor(color))
        else:
            item.setData(Qt.ForegroundRole, None)
        return item

    def _populate_grouped_table(self, containers: list[dict]):
        """Populates table in SRPF Grouped Container view: 1 row per unique client container."""
        self._client_services_cache = {}
        self._prepare_columns(is_grouped=True)
        self.table.setRowCount(len(containers))

        for row_idx, r in enumerate(containers):
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
            c_item = self._table_item(row_idx, self.COL_CLIENT, T["warn"] if unassigned else None)
            c_item.setText(name)
            c_item.setData(ROLE_SUB, subline)
            c_item.setData(_SUBLINE_ROLE, subline)
            c_item.setData(_UNASSIGNED_ROLE, unassigned)
            tooltip_txt = f"{name}\n{subline}"
            if unassigned:
                tooltip_txt += "\nUse the + button, ⋮ or right-click → Create Client to register this client."
            c_item.setData(_TIP_BASE_ROLE, tooltip_txt)

            portal_str = r.get("portal") or "Income Tax Portal"
            short, _ = _portal_short(portal_str)
            p_item = self._table_item(row_idx, self.COL_FILING, _portal_ink(short))
            p_item.setText(_filing_type(portal_str, short))
            p_item.setToolTip(portal_str)

            period_sum = r.get("period_summary") or f"{r.get('total_captures', 1)} Capture(s)"
            hist_item = self._table_item(row_idx, self.COL_PERIOD, K["blue_tx"])
            hist_item.setText(_filings_short(period_sum))
            hist_item.setToolTip(period_sum)

            # Submission Status
            status_text, _ = _resolve_ltt_submission_status(r)
            arn_val = r.get("latest_arn", "N/A")
            tooltip_lines = [f"Submission Status: {status_text}"]
            if arn_val and arn_val != "N/A":
                tooltip_lines.append(f"Latest ARN / Ack: {arn_val}")
            s_item = self._table_item(row_idx, self.COL_STATUS)
            s_item.setText("")
            s_item.setData(ROLE_PILL, _status_pill(status_text))
            s_item.setToolTip("\n".join(tooltip_lines))

            dev_name = _extract_device_name(r)
            dev_item = self._table_item(row_idx, self.COL_DEVICE, K["orange"])
            dev_item.setText(dev_name)
            method_val = r.get("capture_method", "Unknown")
            dev_item.setToolTip(f"Device: {dev_name}\nCapture Method: {method_val}")

            ts_raw = r.get("last_updated", "")
            ts_item = self._table_item(row_idx, self.COL_UPDATED, T["muted"])
            ts_item.setText(_short_local_time(ts_raw))
            ts_item.setToolTip(_format_to_local_time(ts_raw))

            # Actions: drawn by the grid; a click comes back through _on_grid_action
            a_item = self._table_item(row_idx, self.COL_ACTIONS)
            a_item.setData(ROLE_ACTIONS, self._row_actions(r, is_grouped=True))
            self._finish_client_tooltip(c_item, row_idx)

    def _populate_raw_table(self, records: list[dict]):
        """Populates table in granular Individual Raw Captures view."""
        self._client_services_cache = {}
        self._prepare_columns(is_grouped=False)
        self.table.setRowCount(len(records))

        for row_idx, r in enumerate(records):
            unassigned = bool(r.get('is_unassigned')) or not r.get('client_id')
            client_name = _strip_pan_suffix(r.get('client_name') or "Unknown Client", r.get('pan') or "")
            subline_parts = [f"Record #{r['id']}"]
            if r.get('pan') and not r.get('is_unassigned'):
                subline_parts.append(r['pan'])
            if unassigned:
                subline_parts.append("no matching client")
            subline = " · ".join(subline_parts)
            c_item = self._table_item(row_idx, self.COL_CLIENT, T["warn"] if unassigned else None)
            c_item.setText(client_name)
            c_item.setData(ROLE_SUB, subline)
            c_item.setData(_SUBLINE_ROLE, subline)
            c_item.setData(_UNASSIGNED_ROLE, unassigned)
            raw_tooltip = f"{client_name}\n{subline}"
            c_item.setData(_TIP_BASE_ROLE, raw_tooltip)

            portal_str = r.get("service_name") or r.get("portal") or "Portal"
            short, _ = _portal_short(portal_str)
            portal_item = self._table_item(row_idx, self.COL_FILING, _portal_ink(short))
            portal_item.setText(_filing_type(portal_str, short))
            portal_item.setToolTip(portal_str)

            period_val = r.get("period_label") or "N/A"
            period_item = self._table_item(row_idx, self.COL_PERIOD, T["warn"] if r.get("period_label") else T["muted"])
            period_item.setText(re.sub(r"\bAssessment Year\b", "AY", period_val))
            period_item.setToolTip(period_val)

            # Submission Status
            status_text, _ = _resolve_ltt_submission_status(r)
            arn_val = r.get("arn_number", "N/A")
            tooltip_lines = [f"Submission Status: {status_text}"]
            if arn_val and arn_val != "N/A":
                tooltip_lines.append(f"ARN / Ack Number: {arn_val}")
            s_item = self._table_item(row_idx, self.COL_STATUS)
            s_item.setText("")
            s_item.setData(ROLE_PILL, _status_pill(status_text))
            s_item.setToolTip("\n".join(tooltip_lines))

            dev_name = _extract_device_name(r)
            dev_item = self._table_item(row_idx, self.COL_DEVICE, K["orange"])
            dev_item.setText(dev_name)
            method = r.get("capture_method", "DOM_Tracker")
            dev_item.setToolTip(f"Device: {dev_name}\nCapture Method: {method}")

            ts_raw = r.get("created_at", "")
            ts_item = self._table_item(row_idx, self.COL_UPDATED, T["muted"])
            ts_item.setText(_short_local_time(ts_raw))
            ts_item.setToolTip(_format_to_local_time(ts_raw))

            a_item = self._table_item(row_idx, self.COL_ACTIONS)
            a_item.setData(ROLE_ACTIONS, self._row_actions(r, is_grouped=False))
            self._finish_client_tooltip(c_item, row_idx)

    def _full_container(self, item: dict) -> dict:
        """List rows are slim (no payloads); dialogs need the whole container, or the whole raw row."""
        if not item.get("_slim"):
            return item
        if item.get("identity_key"):
            full = self.db.get_srpf_containers(limit=1, identity_key=item["identity_key"])
            if full:
                return full[0]
        elif item.get("id") is not None:
            full = self.db.get_tracker_dumps(limit=1, dump_id=item["id"])
            if full:
                return full[0]
        return item

    def _show_container_dialog(self, container_item: dict):
        container_item = self._full_container(container_item)
        dlg = PayloadInspectorDialog(container_item, db=self.db, is_container=True, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.load_data()

    def _show_payload_dialog(self, dump_item: dict):
        dump_item = self._full_container(dump_item)
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
        """Writes this month's LTT workbook (latest period filed per client, a sheet per form) and opens it."""
        from core.ltt import monthly as ltt_monthly
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            path = ltt_monthly.export_month(self.db, create=True)
            os.startfile(str(path))
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
        act_reresolve = menu.addAction(_safe_qta_icon("mdi.database-sync", T["mint"]), "Re-Resolve Identities (SRPF)")
        act_reresolve.triggered.connect(self._reresolve_identities)

        act_map = menu.addAction(_safe_qta_icon("mdi.table-arrow-right", T["mint"]), "Map Datapoints to MCL Columns…")
        act_map.triggered.connect(self._open_mcl_mapping_dialog)

        menu.addSeparator()

        act_ltt_open = menu.addAction(_safe_qta_icon("mdi.microsoft-excel", T["mint"]), "Open LTT sheet")
        act_ltt_open.triggered.connect(self._open_ltt_sheet)

        menu.addSeparator()

        act_export_csv = menu.addAction(_safe_qta_icon("mdi.file-export", T["mint"]), "Export Captures (CSV)")
        act_export_csv.triggered.connect(self._export_csv)

        act_distill = menu.addAction(_safe_qta_icon("mdi.flask-outline", T["mint"]), "Distill…")
        act_distill.triggered.connect(self._open_distill)

        menu.addSeparator()

        act_clear = menu.addAction(_safe_qta_icon("mdi.delete-sweep", T["dangertx"]), "Clear All Captures")
        act_clear.triggered.connect(self._clear_all_dumps)

        # Spawn popup directly below Preferences button
        btn = getattr(self, "btn_preferences", None)
        if btn:
            menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec_(self.cursor().pos())

