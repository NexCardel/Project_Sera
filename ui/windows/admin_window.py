"""
admin_window.py
----------------
Window 3: CRUD interface for the firm owner. Driven dynamically by MCL.
"""

import re

from PySide6.QtCore import QEvent, QRect, QSize, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QShortcut
try:
    import qtawesome as qta
except Exception:
    qta = None
from pathlib import Path
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import security
from ui.dialogs.csv_import_dialog import CSVImportDialog
from ui.dialogs.mcl_manager_dialog import MCLManagerDialog
from ui.dialogs.service_manager_dialog import ServiceManagerDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.utils.dynamic_form_widgets import make_input_widget, read_input_widget, set_input_widget_value

BACK_ICON = str(Path(__file__).resolve().parents[2] / "assets" / "icons" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


def _safe_qta_icon(name: str, color: str = "#FFFFFF"):
    """Return a QIcon from qtawesome, or a blank QIcon if unavailable."""
    try:
        if qta:
            return qta.icon(name, color=color)
    except Exception:
        pass
    return QIcon()


# Roles carried by the client-list name cell (UserRole + 2 is the activity tag
# that ActivityCellDelegate and _refresh_activity_tags already use).
_ACTIVITY_ROLE = Qt.UserRole + 2
_SUBLINE_ROLE = Qt.UserRole + 3
_TAGS_ROLE = Qt.UserRole + 4

# (background, text) pairs for service tags, picked by the service's position.
_SERVICE_TAG_COLORS = [
    ("#E6EEF9", "#1F4E8C"),
    ("#EAF5EE", "#1E6B41"),
    ("#F3ECF8", "#6A3A8A"),
    ("#FFF1DE", "#98531A"),
    ("#E6F4F7", "#1B5E75"),
    ("#F6EAEA", "#8A2F2F"),
]
_NO_SERVICE_TAG = ("#FDEBDD", "#A2480C")

_SECRET_CLIPBOARD_CLEAR_MS = 30_000


def _scaled_font(base: QFont, delta_px: int, bold: bool = False) -> QFont:
    font = QFont(base)
    if font.pixelSize() > 0:
        font.setPixelSize(max(font.pixelSize() + delta_px, 9))
    elif font.pointSizeF() > 0:
        font.setPointSizeF(max(font.pointSizeF() + delta_px * 0.75, 7.0))
    font.setBold(bold)
    return font


class ClientListDelegate(QStyledItemDelegate):
    """Two-line client row: name + (ID · PAN · GSTIN), service tags and the
    recent-activity tag on the right."""

    ROW_HEIGHT = 50

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        return QSize(hint.width(), self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        if selected:
            painter.fillRect(rect, QColor("#E3F3EA"))
            painter.fillRect(rect.adjusted(0, 0, -(rect.width() - 3), 0), QColor("#2E9B5F"))
        elif hovered:
            painter.fillRect(rect, QColor("#F4FAF6"))
        else:
            painter.fillRect(rect, QColor("#FAF7F0") if index.row() % 2 else QColor("#FFFFFF"))
        painter.setPen(QColor("#EAE3D3"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        content = rect.adjusted(12, 6, -10, -6)
        tags = index.data(_TAGS_ROLE) or []
        activity = str(index.data(_ACTIVITY_ROLE) or "")

        # Right column: service tags on top, activity tag below.
        tag_font = _scaled_font(option.font, -3, bold=True)
        tag_fm = QFontMetrics(tag_font)
        tag_h = tag_fm.height() + 4
        x = content.right()
        right_left = x
        painter.setFont(tag_font)
        shown = list(tags[:3])
        if len(tags) > 3:
            shown.append((f"+{len(tags) - 3}", ("#EEEAE2", "#5B5347")))
        for text, (bg, fg) in reversed(shown):
            text = tag_fm.elidedText(text, Qt.ElideRight, 110)
            w = tag_fm.horizontalAdvance(text) + 12
            x -= w
            tag_rect = QRect(x, content.top(), w, tag_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(tag_rect, 3, 3)
            painter.setPen(QColor(fg))
            painter.drawText(tag_rect, Qt.AlignCenter, text)
            x -= 4
        right_left = min(right_left, x)

        if activity:
            act_font = _scaled_font(option.font, -2)
            painter.setFont(act_font)
            act_fm = painter.fontMetrics()
            act_text = act_fm.elidedText(activity, Qt.ElideRight, 150)
            aw = act_fm.horizontalAdvance(act_text)
            painter.setPen(QColor("#2E7D52"))
            painter.drawText(content.right() - aw, content.bottom() - act_fm.descent(), act_text)
            right_left = min(right_left, content.right() - aw)

        text_w = max(right_left - content.left() - 10, 40)

        name_font = _scaled_font(option.font, 0, bold=True)
        painter.setFont(name_font)
        name_fm = painter.fontMetrics()
        name = name_fm.elidedText(str(index.data(Qt.DisplayRole) or ""), Qt.ElideRight, text_w)
        painter.setPen(QColor("#241F1B"))
        painter.drawText(content.left(), content.top() + name_fm.ascent(), name)

        subline = str(index.data(_SUBLINE_ROLE) or "")
        if subline:
            sub_font = _scaled_font(option.font, -2)
            sub_font.setFamilies(["Consolas", "Cascadia Mono", "Courier New"])
            painter.setFont(sub_font)
            sub_fm = painter.fontMetrics()
            painter.setPen(QColor("#6B645B"))
            painter.drawText(content.left(), content.bottom() - sub_fm.descent(),
                             sub_fm.elidedText(subline, Qt.ElideRight, text_w))
        painter.restore()


# Which card a client field goes in. Matching is on whole words of the label,
# so admin-added columns land somewhere sensible; anything unknown goes to "other".
_CONTACT_WORDS = {"ph", "phone", "mobile", "mob", "email", "mail", "address", "contact", "whatsapp", "tel"}
_IDENTITY_WORDS = {"name", "pan", "gstin", "gst", "tan", "cin", "dob", "birth", "aadhaar", "aadhar",
                   "firm", "company", "proprietor", "trade", "legal"}


def _form_section_for(col: dict) -> str:
    field_type = col.get("field_type", "text")
    label = str(col.get("label", "")).lower().replace("_", " ")
    words = set(re.split(r"[^a-z0-9]+", label))
    if field_type == "password" or "user id" in label or "userid" in label or words & {"username", "login"}:
        return "logins"
    if col.get("is_identity") or col.get("is_internal_pk") or field_type in ("id", "date"):
        return "identity"
    if words & _CONTACT_WORDS or "e-mail" in label:
        return "contact"
    if words & _IDENTITY_WORDS:
        return "identity"
    return "other"


def _is_wide_field(col: dict) -> bool:
    words = set(re.split(r"[^a-z0-9]+", str(col.get("label", "")).lower()))
    return bool(words & {"name", "address"}) and col.get("field_type") != "password"


class NewClientDialog(QDialog):
    """A focused, normal-mode-safe form for creating a client."""
    client_created = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._input_widgets = {}
        self._service_cbs = {}
        self.setWindowTitle("Add Client — Project Sera")
        self.setModal(True)
        self.resize(680, 640)
        self.setMinimumSize(580, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header Frame
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.account-plus-outline", color="#2E9B5F").pixmap(26, 26))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Add New Client")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Enter client identification, credentials, contact details, and compliance services.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Divider
        from PySide6.QtWidgets import QFrame
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        layout.addWidget(divider)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_widget = QWidget()
        self.form_layout = QFormLayout(form_widget)
        self.form_layout.setSpacing(10)
        self.form_layout.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, stretch=1)

        self._build_dynamic_form()

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setIcon(_safe_qta_icon("mdi.close", color="#8E8D88"))
        cancel_btn.clicked.connect(self.reject)

        create_btn = QPushButton("Create Client")
        create_btn.setProperty("class", "primary")
        create_btn.setIcon(_safe_qta_icon("mdi.check", color="#FFFFFF"))
        create_btn.clicked.connect(self._on_create)

        button_row.addWidget(cancel_btn)
        button_row.addWidget(create_btn)
        layout.addLayout(button_row)

    def _build_dynamic_form(self):
        lbl_info = QLabel("CLIENT INFORMATION")
        lbl_info.setProperty("class", "SectionLabel")
        self.form_layout.addRow(lbl_info)
        for col in self.db.get_mcl_columns():
            widget = make_input_widget(col, "", mask_password=False)
            self._input_widgets[col["id"]] = (col, widget)
            is_pk = col.get("is_internal_pk", False)
            if is_pk:
                lbl_col = QLabel(f"<span style='color:#FF5252;'>*</span> <b>{col['label']}</b>:")
                lbl_col.setTextFormat(Qt.RichText)
                lbl_col.setToolTip("Mandatory Internal Primary Key Anchor (e.g. PAN / TAN)")
            else:
                lbl_col = QLabel(f"{col['label']}:")
            lbl_col.setProperty("class", "RowLabel")
            self.form_layout.addRow(lbl_col, widget)

        self.f_notes = QTextEdit()
        self.f_notes.setMaximumHeight(80)
        lbl_notes = QLabel("Notes:")
        lbl_notes.setProperty("class", "RowLabel")
        self.form_layout.addRow(lbl_notes, self.f_notes)

        lbl_svc = QLabel("ATTACHED SERVICES")
        lbl_svc.setProperty("class", "SectionLabel")
        self.form_layout.addRow(lbl_svc)
        for service in self.db.get_services():
            checkbox = QCheckBox(service["name"])
            self._service_cbs[service["id"]] = checkbox
            self.form_layout.addRow("", checkbox)

    def _on_create(self):
        mcl_cols = self.db.get_mcl_columns()
        if not mcl_cols:
            QMessageBox.warning(self, "No Schema", "Define at least one MCL column before adding clients.")
            return

        values = {
            col_id: read_input_widget(col_def, widget)
            for col_id, (col_def, widget) in self._input_widgets.items()
        }
        notes = self.f_notes.toPlainText().strip()
        service_ids = [service_id for service_id, checkbox in self._service_cbs.items() if checkbox.isChecked()]

        duplicates = self.db.find_duplicate_clients(values)
        if duplicates:
            proceed = QMessageBox.question(
                self, "Possible duplicate",
                "A client already exists with this identity value:\n\n"
                + "\n".join(f"• {duplicate}" for duplicate in duplicates)
                + "\n\nCreate this client anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if proceed != QMessageBox.Yes:
                return

        try:
            self.db.add_client(values, notes, service_ids, actor=getattr(self, "actor", "Staff"))
            self.client_created.emit()
            self.accept()
        except ValueError as val_err:
            QMessageBox.warning(self, "Validation Error", str(val_err))
        except Exception as e:
            QMessageBox.critical(self, "Creation Failed", f"Could not create client: {e}")


class AdminPinDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Security Verification — Project Sera")
        self.setModal(True)
        self.setFixedSize(380, 260)
        self._is_first_run = self.db.get_setting("admin_pin_hash") is None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.shield-lock-outline", color="#2E9B5F").pixmap(28, 28))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Create Admin PIN" if self._is_first_run else "Admin Mode Access")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Enter your master PIN to access administrative features." if not self._is_first_run else "Set a master PIN to protect administrator tools.")
        sub_lbl.setStyleSheet("font-size: 11.5px; color: #8E8D88;")
        sub_lbl.setWordWrap(True)
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        from PySide6.QtWidgets import QFrame
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        layout.addWidget(divider)

        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.Password)
        self.pin_input.setPlaceholderText("Enter 4+ digit PIN...")
        self.pin_input.setStyleSheet("font-size: 14px; padding: 8px 12px;")
        self.pin_input.returnPressed.connect(self._on_accept)
        layout.addWidget(self.pin_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setIcon(_safe_qta_icon("mdi.close", color="#8E8D88"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        unlock_btn = QPushButton("Set PIN" if self._is_first_run else "Unlock Admin")
        unlock_btn.setProperty("class", "primary")
        unlock_btn.setIcon(_safe_qta_icon("mdi.lock-open-outline", color="#FFFFFF"))
        unlock_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(unlock_btn)

        layout.addLayout(btn_row)

    def _on_accept(self):
        pin = self.pin_input.text()
        if not pin or len(pin) < 4:
            QMessageBox.warning(self, "Too Short", "PIN must be at least 4 characters.")
            return

        if self._is_first_run:
            self.db.set_setting("admin_pin_hash", security.hash_admin_pin(pin))
            self.accept()
            return

        stored_hash = self.db.get_setting("admin_pin_hash")
        if security.verify_admin_pin(pin, stored_hash):
            self.accept()
        else:
            QMessageBox.critical(self, "Incorrect PIN", "That PIN is incorrect.")
            self.pin_input.clear()

class AdminWindow(QWidget):
    back_requested = Signal()
    request_slide_panel = Signal(QWidget, str)
    toast_requested = Signal(str, int)
    action_alert_requested = Signal(str, str)
    settings_saved = Signal()  # Forwarded from SettingsDialog when user saves

    def __init__(self, db, actor: str = "Admin"):
        super().__init__()
        self.setObjectName("ManageClientsPage")
        self.db = db
        self.actor = actor
        self.selected_client_id = None
        self._input_widgets = {}
        self._service_cbs = {}
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.refresh)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(60000)
        self._activity_timer.timeout.connect(self._refresh_activity_tags)
        self._activity_timer.start()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        page_header = QHBoxLayout()
        back_btn = QPushButton()
        back_btn.setProperty("class", "GhostIconButton")
        back_btn.setIcon(qta.icon("mdi.arrow-left", color="#8E8D88") if qta else QIcon(BACK_ICON))
        back_btn.setIconSize(QSize(20, 20))
        back_btn.setFixedSize(36, 36)
        back_btn.setToolTip("Back to Search (Esc)")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self.back_requested.emit)
        page_header.addWidget(back_btn)
        page_header.addSpacing(4)
        page_title = QLabel("Manage Clients")
        page_title.setProperty("class", "PageTitle")
        page_header.addWidget(page_title)
        page_header.addSpacing(10)
        self.count_label = QLabel("")
        self.count_label.setProperty("class", "CountLabel")
        page_header.addWidget(self.count_label, alignment=Qt.AlignBottom)
        page_header.addStretch()
        self.new_btn = QPushButton("New client")
        self.new_btn.setProperty("class", "primary")
        self.new_btn.setIcon(_safe_qta_icon("mdi.account-plus", "#FFFFFF"))
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.setToolTip("Start a blank client form (Ctrl+N)")
        self.new_btn.clicked.connect(self._on_new)
        page_header.addWidget(self.new_btn)
        layout.addLayout(page_header)

        # Syncthing Conflict Warning Banner
        self.conflict_banner_widget = QWidget()
        self.conflict_banner_widget.setProperty("class", "ConflictBanner")
        banner_layout = QHBoxLayout(self.conflict_banner_widget)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        self.conflict_label = QLabel("⚠️ Syncthing conflict file(s) detected in database directory!")
        self.conflict_label.setProperty("class", "ConflictLabel")
        banner_layout.addWidget(self.conflict_label)
        banner_layout.addStretch()
        btn_inspect_conflict = QPushButton("Inspect Conflicts...")
        if qta:
            btn_inspect_conflict.setIcon(qta.icon("mdi.file-alert-outline", color="#FFFFFF"))
        btn_inspect_conflict.clicked.connect(self._on_inspect_conflicts)
        banner_layout.addWidget(btn_inspect_conflict)

        layout.addWidget(self.conflict_banner_widget)
        self.conflict_banner_widget.hide()

        # The old header with buttons (Settings, Audit Log, etc.) has been removed.
        # These actions are now exclusively accessed via the new Sidebar shell.

        body = QHBoxLayout()
        body.setSpacing(16)

        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)

        # Search Bar for Manage Clients Table
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search clients (ID, Name, PAN, GSTIN...)")
        self.search_input.setClearButtonEnabled(True)
        if qta:
            self.search_input.addAction(qta.icon("mdi.magnify", color="#889988"), QLineEdit.LeadingPosition)
        self.search_input.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self.search_input)

        # Filter chips. filter_combo and show_archived_cb stay as the (hidden)
        # source of truth that refresh() reads; the chips only drive them.
        self.filter_combo = QComboBox(self)
        self.filter_combo.hide()
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        self.show_archived_cb = QCheckBox("Show Archived", self)
        self.show_archived_cb.hide()
        self.show_archived_cb.toggled.connect(self._on_archive_toggle)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self._filter_chips = []
        for text, preset, archived, tip in (
            ("All", None, False, "All active clients"),
            ("Has services", "has_services", False, "Clients with at least one attached service"),
            ("No services", "no_services", False, "Clients with no attached service"),
            ("Archived", None, True, "Archived clients (Restore or Delete permanently)"),
        ):
            chip = QPushButton(text)
            chip.setProperty("class", "FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(tip)
            chip.clicked.connect(lambda _=False, p=preset, a=archived: self._apply_filter(p, a))
            chip.setProperty("filter_preset", preset)
            chip.setProperty("filter_archived", archived)
            chip_row.addWidget(chip)
            self._filter_chips.append(chip)
        self.more_filters_btn = QToolButton()
        self.more_filters_btn.setProperty("class", "FilterChip")
        self.more_filters_btn.setText("More")
        self.more_filters_btn.setCheckable(True)
        self.more_filters_btn.setCursor(Qt.PointingHandCursor)
        self.more_filters_btn.setPopupMode(QToolButton.InstantPopup)
        self.more_filters_btn.setToolTip("Activity, login and per-service filters")
        self._more_filters_menu = QMenu(self.more_filters_btn)
        self.more_filters_btn.setMenu(self._more_filters_menu)
        chip_row.addWidget(self.more_filters_btn)
        chip_row.addStretch()
        left_layout.addLayout(chip_row)

        sort_row = QHBoxLayout()
        sort_lbl = QLabel("Sort")
        sort_lbl.setProperty("class", "RowLabel")
        sort_row.addWidget(sort_lbl)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("ID (Ascending: 1, 2, 3...)", ("id", "asc"))
        self.sort_combo.addItem("ID (Descending: 3, 2, 1...)", ("id", "desc"))
        self.sort_combo.addItem("Client Identity (A → Z)", ("identity", "asc"))
        self.sort_combo.addItem("Client Identity (Z → A)", ("identity", "desc"))
        self.sort_combo.addItem("🔥 Most Viewed / Activity", ("activity", "desc"))
        self.sort_combo.addItem("Recently Added (Newest first)", ("created_at", "desc"))
        self.sort_combo.addItem("Recently Updated", ("updated_at", "desc"))
        self.sort_combo.currentIndexChanged.connect(self.refresh)
        sort_row.addWidget(self.sort_combo, stretch=1)
        left_layout.addLayout(sort_row)

        # Row order comes from sort_combo (applied in refresh), so the table
        # itself doesn't sort -- a header sort indicator used to override it.
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["ID", "Client Identity"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ClientListDelegate.ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)
        self.table.setMinimumWidth(0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.setItemDelegate(ClientListDelegate(self.table))
        self.table.setToolTip("Ctrl- or Shift-click to select several clients")
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        left_layout.addWidget(self.table, stretch=1)

        # Bulk bar: only shown when two or more clients are selected.
        self.bulk_bar = QFrame()
        self.bulk_bar.setProperty("class", "BulkBar")
        bulk_outer = QVBoxLayout(self.bulk_bar)
        bulk_outer.setContentsMargins(10, 6, 8, 8)
        bulk_outer.setSpacing(6)
        bulk_top = QHBoxLayout()
        self.selection_label = QLabel("")
        self.selection_label.setProperty("class", "BulkLabel")
        bulk_top.addWidget(self.selection_label)
        bulk_top.addStretch()
        bulk_outer.addLayout(bulk_top)
        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(6)
        bulk_outer.addLayout(bulk_row)
        bulk_svc_btn = QPushButton("Attach / detach service…")
        bulk_svc_btn.setIcon(_safe_qta_icon("mdi.briefcase-edit-outline", "#FFFFFF"))
        bulk_svc_btn.clicked.connect(self._on_bulk_service)
        self.bulk_archive_btn = QPushButton("Archive")
        self.bulk_archive_btn.setIcon(_safe_qta_icon("mdi.archive-outline", "#FFFFFF"))
        self.bulk_archive_btn.clicked.connect(self._on_archive)
        self.bulk_restore_btn = QPushButton("Restore")
        self.bulk_restore_btn.setIcon(_safe_qta_icon("mdi.backup-restore", "#FFFFFF"))
        self.bulk_restore_btn.clicked.connect(self._on_restore)
        self.bulk_purge_btn = QPushButton("Delete permanently")
        self.bulk_purge_btn.setIcon(_safe_qta_icon("mdi.delete-forever-outline", "#FF6B6B"))
        self.bulk_purge_btn.clicked.connect(self._on_purge)
        bulk_clear_btn = QPushButton("Clear")
        bulk_clear_btn.setProperty("class", "GhostIconButton")
        bulk_clear_btn.clicked.connect(self._on_new)
        bulk_top.addWidget(bulk_clear_btn)
        for btn in (bulk_svc_btn, self.bulk_archive_btn, self.bulk_restore_btn, self.bulk_purge_btn):
            bulk_row.addWidget(btn)
        bulk_row.addStretch()
        left_layout.addWidget(self.bulk_bar)
        self.bulk_bar.hide()

        left_host = QWidget()
        left_host.setLayout(left_layout)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_host.setMinimumWidth(300)
        left_host.setMaximumWidth(460)
        body.addWidget(left_host, stretch=2)

        # ---- Right: editor header + sectioned form ----
        right_layout = QVBoxLayout()
        right_layout.setSpacing(10)

        self.editor_header = QFrame()
        self.editor_header.setProperty("class", "EditorHeader")
        head = QHBoxLayout(self.editor_header)
        head.setContentsMargins(12, 10, 12, 10)
        head.setSpacing(12)
        self.editor_avatar = QLabel("+")
        self.editor_avatar.setProperty("class", "EditorAvatar")
        self.editor_avatar.setFixedSize(40, 40)
        self.editor_avatar.setAlignment(Qt.AlignCenter)
        head.addWidget(self.editor_avatar)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.editor_title = QLabel("New client")
        self.editor_title.setProperty("class", "EditorTitle")
        self.editor_sub = QLabel("")
        self.editor_sub.setProperty("class", "EditorSub")
        self.editor_sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title_box.addWidget(self.editor_title)
        title_box.addWidget(self.editor_sub)
        head.addLayout(title_box, stretch=1)
        self.dirty_label = QLabel("● Unsaved changes")
        self.dirty_label.setProperty("class", "DirtyLabel")
        self.dirty_label.hide()
        head.addWidget(self.dirty_label)

        self.more_btn = QToolButton()
        self.more_btn.setProperty("class", "MoreBtn")
        self.more_btn.setText("More")
        self.more_btn.setIcon(_safe_qta_icon("mdi.dots-horizontal", "#C9D1D9"))
        self.more_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        self.more_btn.setCursor(Qt.PointingHandCursor)
        more_menu = QMenu(self.more_btn)
        self.act_archive = more_menu.addAction(_safe_qta_icon("mdi.archive-outline", "#C9D1D9"), "Archive client")
        self.act_archive.triggered.connect(self._on_archive)
        self.act_restore = more_menu.addAction(_safe_qta_icon("mdi.backup-restore", "#4CF9B7"), "Restore client")
        self.act_restore.triggered.connect(self._on_restore)
        self.act_purge = more_menu.addAction(_safe_qta_icon("mdi.delete-forever-outline", "#FF6B6B"), "Delete permanently…")
        self.act_purge.triggered.connect(self._on_purge)
        self.more_btn.setMenu(more_menu)
        head.addWidget(self.more_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setProperty("class", "primary")
        self.save_btn.setIcon(_safe_qta_icon("mdi.content-save-outline", "#FFFFFF"))
        self.save_btn.setToolTip("Save this client (Ctrl+S)")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save)
        head.addWidget(self.save_btn)
        right_layout.addWidget(self.editor_header)

        self._form_scroll = QScrollArea()
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setFrameShape(QFrame.NoFrame)
        self._form_scroll.setMinimumWidth(0)
        self.form_widget = QWidget()
        self.form_widget.setObjectName("ClientFormCanvas")
        self._cards_grid = QGridLayout(self.form_widget)
        self._cards_grid.setContentsMargins(0, 0, 4, 0)
        self._cards_grid.setSpacing(10)
        self._cards = []
        self._card_cols = 0
        self._secret_widgets = []
        self._loading_form = False
        self._form_scroll.setWidget(self.form_widget)
        self._form_scroll.viewport().installEventFilter(self)
        right_layout.addWidget(self._form_scroll, stretch=1)

        self._build_dynamic_form()

        body.addLayout(right_layout, stretch=3)
        layout.addLayout(body, stretch=1)

        self._active_mode_btns = [self.new_btn, self.save_btn, self.act_archive, self.bulk_archive_btn]
        self._archived_mode_btns = [self.act_restore, self.act_purge, self.bulk_restore_btn, self.bulk_purge_btn]
        for btn in self._archived_mode_btns:
            btn.setVisible(False)

        for seq, slot in ((QKeySequence.Save, self._on_save), (QKeySequence.New, self._on_new)):
            shortcut = QShortcut(seq, self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

        self._reload_filters()
        self._sync_filter_chips()
        self._update_editor_header()

    def _on_search_changed(self):
        self._search_timer.start()

    def _reload_filters(self):
        current = self.filter_combo.currentData()
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All clients", None)
        self.filter_combo.addItem("🔥 Most Viewed / Active", "most_viewed")
        self.filter_combo.addItem("⚡ Active Today", "active_today")
        self.filter_combo.addItem("🌐 Has Attached Services", "has_services")
        self.filter_combo.addItem("⚠️ Unassigned (No Services)", "no_services")
        self.filter_combo.addItem("🔒 Has Login Credentials", "has_passwords")
        self.filter_combo.addItem("⚠️ Missing Passwords", "missing_passwords")
        self.filter_combo.addItem("📦 Archived Only", "archived")
        services = self.db.get_services()
        for s in services:
            self.filter_combo.addItem(f"Service: {s['name']}", s["id"])
        idx = self.filter_combo.findData(current) if current is not None else 0
        self.filter_combo.setCurrentIndex(max(idx, 0))
        self.filter_combo.blockSignals(False)

        # "More" menu: the presets that don't have their own chip, plus one per service.
        menu = self._more_filters_menu
        menu.clear()
        self._more_filter_labels = {}
        for text, preset in (
            ("Most viewed / active", "most_viewed"),
            ("Active today", "active_today"),
            ("Has login credentials", "has_passwords"),
            ("Missing passwords", "missing_passwords"),
        ):
            act = menu.addAction(text)
            act.triggered.connect(lambda _=False, p=preset: self._apply_filter(p, False))
            self._more_filter_labels[preset] = text
        if services:
            menu.addSeparator()
            for s in services:
                act = menu.addAction(f"Service: {s['name']}")
                act.triggered.connect(lambda _=False, sid=s["id"]: self._apply_filter(sid, False))
                self._more_filter_labels[s["id"]] = s["name"]
        self._sync_filter_chips()

    def _apply_filter(self, preset, archived: bool):
        """Point the hidden filter combo / archived checkbox at a chip's filter."""
        idx = self.filter_combo.findData(preset) if preset is not None else 0
        self.filter_combo.blockSignals(True)
        self.filter_combo.setCurrentIndex(max(idx, 0))
        self.filter_combo.blockSignals(False)
        if self.show_archived_cb.isChecked() != archived:
            self.show_archived_cb.setChecked(archived)  # toggled -> _on_archive_toggle -> refresh
        else:
            self.refresh()
        self._sync_filter_chips()

    def _sync_filter_chips(self):
        preset = self.filter_combo.currentData()
        archived = self.show_archived_cb.isChecked() or preset == "archived"
        chip_match = False
        for chip in self._filter_chips:
            on = (bool(chip.property("filter_archived")) == archived
                  and (archived or chip.property("filter_preset") == preset))
            chip.setChecked(on)
            chip_match = chip_match or on
        more_on = not chip_match
        self.more_filters_btn.setChecked(more_on)
        labels = getattr(self, "_more_filter_labels", {})
        self.more_filters_btn.setText(labels.get(preset, "More") if more_on else "More")

    def _resolve_client_data(self, client_values, client_services):
        if isinstance(client_values, int):
            c_data = self.db.get_client(client_values)
            if c_data:
                client_services = client_services or c_data.get("service_ids", [])
                c_vals = dict(c_data.get("values", {}))
                c_vals["notes"] = c_data.get("notes", "")
                client_values = c_vals
            else:
                client_values = {}
        elif isinstance(client_values, dict) and "values" in client_values:
            c_vals = dict(client_values.get("values", {}))
            if "notes" in client_values and "notes" not in c_vals:
                c_vals["notes"] = client_values.get("notes", "")
            client_services = client_services or client_values.get("service_ids", [])
            client_values = c_vals
        elif not isinstance(client_values, dict):
            client_values = {}
        client_services = client_services or []
        return client_values, client_services

    def _build_dynamic_form(self, client_values=None, client_services=None, force_rebuild=False):
        c_vals, c_svcs = self._resolve_client_data(client_values, client_services)

        if not force_rebuild and getattr(self, "_form_built", False) and self._input_widgets:
            self._loading_form = True
            try:
                for col_id, (col, widget) in self._input_widgets.items():
                    val = c_vals.get(col_id, "")
                    set_input_widget_value(col, widget, val)
                if hasattr(self, "f_notes") and self.f_notes is not None:
                    self.f_notes.setPlainText(c_vals.get("notes", ""))
                for sid, cb in self._service_cbs.items():
                    cb.setChecked(sid in c_svcs)
                self._mask_secrets()
            finally:
                self._loading_form = False
            self._set_dirty(False)
            return

        for card in self._cards:
            self._cards_grid.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._cards_order = []
        for col_id, (col, widget) in self._input_widgets.items():
            if widget.parent() is self.form_widget:
                widget.deleteLater()
        self._input_widgets.clear()
        self._service_cbs.clear()
        self._secret_widgets = []
        self._loading_form = True

        sections = {"identity": [], "logins": [], "contact": [], "other": []}
        for col in self.db.get_mcl_columns():
            widget = make_input_widget(col, c_vals.get(col["id"], ""), mask_password=True)
            self._input_widgets[col["id"]] = (col, widget)
            if col.get("field_type") == "id":
                # The auto ID is shown in the editor header; the widget is kept
                # (hidden) so _on_save reads it exactly as before.
                widget.setParent(self.form_widget)
                widget.hide()
                continue
            self._watch_for_changes(col, widget)
            section = _form_section_for(col)
            if section == "logins":
                self._add_field_actions(col, widget)
            sections[section].append((col, widget))

        for key, title, hint in (
            ("identity", "Identity", ""),
            ("logins", "Portal logins", "Hidden — use the eye to show"),
            ("contact", "Contact", ""),
            ("other", "Other details", ""),
        ):
            if sections[key]:
                card = self._make_field_card(title, hint, sections[key])
                self._cards_order.append((card, False))

        # Attached services: toggle chips
        svc_card, svc_body = self._make_card("Attached services", "Click to attach or detach")
        svc_grid = QGridLayout()
        svc_grid.setSpacing(6)
        services = self.db.get_services()
        for i, s in enumerate(services):
            chip = QPushButton(s["name"])
            chip.setProperty("class", "ServiceChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setChecked(s["id"] in c_svcs)
            chip.toggled.connect(self._on_form_edited)
            self._service_cbs[s["id"]] = chip
            svc_grid.addWidget(chip, i // 3, i % 3)
        if not services:
            none_lbl = QLabel("No services defined yet (Options → Services).")
            none_lbl.setProperty("class", "FieldHint")
            svc_grid.addWidget(none_lbl, 0, 0)
        svc_body.addLayout(svc_grid)
        svc_body.addStretch()
        self._cards_order.append((svc_card, False))

        notes_card, notes_body = self._make_card("Notes", "")
        self.f_notes = QTextEdit()
        self.f_notes.setPlaceholderText("Write notes here...")
        self.f_notes.setMinimumHeight(70)
        self.f_notes.setMaximumHeight(120)
        self.f_notes.setPlainText(c_vals.get("notes", ""))
        self.f_notes.textChanged.connect(self._on_form_edited)
        notes_body.addWidget(self.f_notes)
        self._cards_order.append((notes_card, True))

        self._cards = [card for card, _ in self._cards_order]
        self._card_cols = 0
        self._reflow_cards()
        self._loading_form = False
        self._set_dirty(False)
        self._form_built = True

    # ---- form building helpers ----

    def _make_card(self, title: str, hint: str):
        card = QFrame(self.form_widget)
        card.setProperty("class", "FormCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(8)
        head = QHBoxLayout()
        lbl = QLabel(title.upper())
        lbl.setProperty("class", "SectionLabel")
        head.addWidget(lbl)
        head.addStretch()
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setProperty("class", "FieldHint")
            head.addWidget(hint_lbl)
        outer.addLayout(head)
        return card, outer

    def _make_field_card(self, title: str, hint: str, fields: list):
        card, body = self._make_card(title, hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        row, col_idx = 0, 0
        for col, widget in fields:
            wide = _is_wide_field(col)
            if wide and col_idx:
                row, col_idx = row + 1, 0
            if col.get("is_internal_pk"):
                label = QLabel(f"{col['label']} <span style='color:#FF6B6B;'>*</span>")
                label.setTextFormat(Qt.RichText)
                label.setToolTip("Mandatory Internal Primary Key Anchor (e.g. PAN / TAN)")
            else:
                label = QLabel(col["label"])
            label.setProperty("class", "FieldLabel")
            label.setBuddy(widget)
            span = 2 if wide else 1
            grid.addWidget(label, row * 2, col_idx, 1, span)
            grid.addWidget(widget, row * 2 + 1, col_idx, 1, span)
            if wide:
                row += 1
            else:
                col_idx += 1
                if col_idx > 1:
                    row, col_idx = row + 1, 0
        body.addLayout(grid)
        body.addStretch()
        return card

    def _reflow_cards(self):
        if not self._cards:
            return
        cols = 2 if self._form_scroll.viewport().width() >= 760 else 1
        if cols == self._card_cols:
            return
        self._card_cols = cols
        grid = self._cards_grid
        for card in self._cards:
            grid.removeWidget(card)
        for r in range(grid.rowCount()):
            grid.setRowStretch(r, 0)
        row, col_idx = 0, 0
        for card, wide in self._cards_order:
            if wide or cols == 1:
                if col_idx:
                    row, col_idx = row + 1, 0
                grid.addWidget(card, row, 0, 1, cols)
                row += 1
            else:
                grid.addWidget(card, row, col_idx)
                col_idx += 1
                if col_idx >= cols:
                    row, col_idx = row + 1, 0
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1 if cols == 2 else 0)
        grid.setRowStretch(row + (1 if col_idx else 0), 1)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is getattr(self, "_form_scroll", None).viewport():
            self._reflow_cards()
        return super().eventFilter(obj, event)

    def _watch_for_changes(self, col: dict, widget):
        field_type = col.get("field_type", "text")
        if field_type == "dropdown":
            widget.currentIndexChanged.connect(self._on_form_edited)
        elif field_type == "date":
            widget.dateChanged.connect(self._on_form_edited)
        elif isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._on_form_edited)

    def _add_field_actions(self, col: dict, widget):
        """Eye (show/hide) for secrets and copy for every login field."""
        if not isinstance(widget, QLineEdit):
            return
        is_secret = col.get("field_type") == "password"
        if is_secret:
            widget.setEchoMode(QLineEdit.Password)
            self._secret_widgets.append(widget)
            eye = widget.addAction(_safe_qta_icon("mdi.eye-outline", "#8E8D88"), QLineEdit.TrailingPosition)
            eye.setToolTip("Show / hide")

            def _toggle(_=False, w=widget, a=eye):
                shown = w.echoMode() == QLineEdit.Normal
                w.setEchoMode(QLineEdit.Password if shown else QLineEdit.Normal)
                a.setIcon(_safe_qta_icon("mdi.eye-outline" if shown else "mdi.eye-off-outline", "#8E8D88"))
            eye.triggered.connect(_toggle)
        copy = widget.addAction(_safe_qta_icon("mdi.content-copy", "#8E8D88"), QLineEdit.TrailingPosition)
        copy.setToolTip("Copy" + (" (clipboard clears after 30 s)" if is_secret else ""))
        copy.triggered.connect(lambda _=False, c=col, w=widget, sec=is_secret: self._copy_field(c, w, sec))

    def _mask_secrets(self):
        for w in self._secret_widgets:
            w.setEchoMode(QLineEdit.Password)
            for act in w.actions():
                if act.toolTip() == "Show / hide":
                    act.setIcon(_safe_qta_icon("mdi.eye-outline", "#8E8D88"))

    def _copy_field(self, col: dict, widget: QLineEdit, is_secret: bool):
        val = widget.text()
        if not val:
            self.toast_requested.emit(f"{col['label']} is empty.", 2000)
            return
        QApplication.clipboard().setText(val)
        self.toast_requested.emit(f"Copied {col['label']}" + (" — clears in 30 s" if is_secret else ""), 2000)
        try:
            self.db.log_action(self.actor, "manual_copy", client_id=self.selected_client_id,
                               detail=f"Copied field from Manage Clients: {col['label']}")
        except Exception:
            pass
        if is_secret:
            def _clear(v=val):
                try:
                    if QApplication.clipboard().text() == v:
                        QApplication.clipboard().clear()
                except Exception:
                    pass
            QTimer.singleShot(_SECRET_CLIPBOARD_CLEAR_MS, _clear)

    def _on_form_edited(self, *_):
        if not self._loading_form:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        if hasattr(self, "dirty_label"):
            self.dirty_label.setVisible(dirty)

    def _update_editor_header(self, client=None, multi_count: int = 0):
        archived_mode = self.show_archived_cb.isChecked()
        if multi_count > 1:
            self.editor_avatar.setText(str(multi_count))
            self.editor_title.setText(f"{multi_count} clients selected")
            self.editor_sub.setText("Use the bar under the list to change them together.")
            self.more_btn.setEnabled(False)
            self.save_btn.setEnabled(False)
            return
        self.save_btn.setEnabled(True)
        if not client:
            self.editor_avatar.setText("+")
            self.editor_title.setText("New client")
            self.editor_sub.setText("Fill in the details and press Save. Fields marked * are required.")
            self.more_btn.setEnabled(False)
            return
        title = self._get_identity_label(client)
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", title)[:2]).upper() or "#"
        self.editor_avatar.setText(initials)
        self.editor_title.setText(title)
        parts = [f"Client #{client.get('client_id_token') or client.get('id')}"]
        parts += self._client_key_values(client)[:1]
        updated = str(client.get("updated_at") or "")[:10]
        if updated:
            parts.append(f"Updated {updated}")
        if archived_mode or client.get("is_archived"):
            parts.append("Archived")
        self.editor_sub.setText(" · ".join(parts))
        self.more_btn.setEnabled(True)

    def _client_key_values(self, client, mcl_cols=None) -> list:
        """PAN-style anchor values, then GSTIN, for the list subline / header."""
        mcl_cols = mcl_cols if mcl_cols is not None else self.db.get_mcl_columns()
        vals = client.get("values", {}) or {}
        out = []
        for want_pk in (True, False):
            for col in mcl_cols:
                if col.get("field_type") in ("id", "password"):
                    continue
                label = str(col.get("label", "")).lower()
                if want_pk and not col.get("is_internal_pk"):
                    continue
                if not want_pk and (col.get("is_internal_pk") or "gstin" not in label):
                    continue
                v = str(vals.get(col["id"]) or "").strip()
                if v and v not in out:
                    out.append(v)
        return out

    def _get_identity_label(self, client, identity_cols=None):
        if identity_cols is None:
            identity_cols = getattr(self, "_cached_identity_cols", None)
            if identity_cols is None:
                identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c.get("is_identity")]
                self._cached_identity_cols = identity_cols
        vals, seen = [], set()
        for cid in identity_cols:
            v = str(client.get("values", {}).get(cid) or "").strip()
            # Company and proprietor are often the same person: show it once.
            if v and v.casefold() not in seen:
                seen.add(v.casefold())
                vals.append(v)
        return " — ".join(vals) if vals else "[No Identity Data]"

    def refresh(self):
        self._check_sync_conflicts()
        mcl_cols = self.db.get_mcl_columns()
        identity_cols = [c["id"] for c in mcl_cols if c.get("is_identity")]
        self._cached_identity_cols = identity_cols
        services = self.db.get_services()
        service_tags = {
            s["id"]: (s["name"], _SERVICE_TAG_COLORS[i % len(_SERVICE_TAG_COLORS)])
            for i, s in enumerate(services)
        }

        filter_data = self.filter_combo.currentData() if hasattr(self, "filter_combo") else None
        show_archived = self.show_archived_cb.isChecked() if hasattr(self, "show_archived_cb") else False
        search_query = self.search_input.text().strip() if hasattr(self, "search_input") else ""

        svc_id = filter_data if isinstance(filter_data, int) else None
        filter_preset = filter_data if isinstance(filter_data, str) else None
        if show_archived:
            filter_preset = "archived"

        clients = self.db.search_clients(
            search_query,
            service_id=svc_id,
            archived_only=(filter_preset == "archived"),
            filter_preset=filter_preset
        )

        # Apply Sort By Selection
        if hasattr(self, "sort_combo") and self.sort_combo.currentData():
            sort_key, sort_dir = self.sort_combo.currentData()
            reverse = (sort_dir == "desc")
            if sort_key == "id":
                def _id_sort_key(c):
                    token = str(c.get("client_id_token") or c.get("id", ""))
                    if token.isdigit():
                        return (0, int(token), token)
                    return (1, 0, token.lower())
                clients.sort(key=_id_sort_key, reverse=reverse)
            elif sort_key == "identity":
                clients.sort(key=lambda c: self._get_identity_label(c, identity_cols).lower(), reverse=reverse)
            elif sort_key == "activity":
                stats_map = self.db.get_all_activity_stats()
                clients.sort(key=lambda c: (stats_map.get(c["id"], {}).get("view_count", 0) * 3 + stats_map.get(c["id"], {}).get("action_count", 0)), reverse=reverse)
            elif sort_key == "created_at":
                clients.sort(key=lambda c: c.get("created_at") or "", reverse=reverse)
            elif sort_key == "updated_at":
                clients.sort(key=lambda c: c.get("updated_at") or "", reverse=reverse)
        
        # Remember which client you had selected so the screen doesn't wipe
        old_selected = self.selected_client_id
        
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(clients))
        
        from ui.utils.theme import SmartTableWidgetItem
        recent_acts = self.db.get_recent_client_activities(max_age_seconds=1800)

        row_to_select = -1
        for r, c in enumerate(clients):
            self.table.setItem(r, 0, SmartTableWidgetItem(str(c["id"])))
            
            lbl = self._get_identity_label(c, identity_cols)
            item1 = SmartTableWidgetItem(lbl)
            subline = [f"#{c.get('client_id_token') or c['id']}"] + self._client_key_values(c, mcl_cols)
            item1.setData(_SUBLINE_ROLE, " · ".join(subline))
            tags = [service_tags[sid] for sid in c.get("service_ids", []) if sid in service_tags]
            item1.setData(_TAGS_ROLE, tags if tags else ([("No services", _NO_SERVICE_TAG)] if services else []))
            item1.setData(_ACTIVITY_ROLE, "")
            act_list = recent_acts.get(c["id"], [])
            if act_list:
                top = act_list[0]
                action_type = top["action_type"]
                age = top["age_seconds"]
                rel = "just now" if age < 60 else (f"{age // 60}m ago" if age < 3600 else f"{age // 3600}h ago")
                item1.setData(Qt.UserRole + 2, f"{action_type} • {rel}")
                tooltip_lines = [f"• {a['action_type']} ({'just now' if a['age_seconds'] < 60 else str(a['age_seconds']//60) + 'm ago'})" for a in act_list[:4]]
                item1.setToolTip(f"{lbl}\n\nRecent Activity:\n" + "\n".join(tooltip_lines))

            self.table.setItem(r, 1, item1)
            if c["id"] == old_selected:
                row_to_select = r
                
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

        noun = "archived client" if show_archived else "client"
        self.count_label.setText(f"{len(clients)} {noun}{'' if len(clients) == 1 else 's'}")
        self._sync_filter_chips()

        if row_to_select >= 0:
            self.table.selectRow(row_to_select)
        else:
            self._on_new()

    def _refresh_activity_tags(self):
        try:
            recent_acts = self.db.get_recent_client_activities(max_age_seconds=1800)
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                name_item = self.table.item(r, 1)
                if not id_item or not name_item:
                    continue
                try:
                    cid = int(id_item.text())
                except ValueError:
                    continue
                act_list = recent_acts.get(cid, [])
                if act_list:
                    top = act_list[0]
                    action_type = top["action_type"]
                    age = top["age_seconds"]
                    rel = "just now" if age < 60 else (f"{age // 60}m ago" if age < 3600 else f"{age // 3600}h ago")
                    name_item.setData(Qt.UserRole + 2, f"{action_type} • {rel}")
                else:
                    name_item.setData(Qt.UserRole + 2, "")
            self.table.viewport().update()
        except Exception:
            pass



    def _check_sync_conflicts(self):
        conflicts = self.db.get_sync_conflicts()
        if conflicts:
            count = len(conflicts)
            self.conflict_label.setText(
                f"⚠️ Syncthing conflict file(s) detected ({count} conflict file{'s' if count > 1 else ''}) in database directory!"
            )
            self.conflict_banner_widget.show()
        else:
            self.conflict_banner_widget.hide()

    def _on_inspect_conflicts(self):
        conflicts = self.db.get_sync_conflicts()
        if not conflicts:
            self.toast_requested.emit("No Syncthing sync conflict files detected.", 3000)
            return

        msg = "The following Syncthing conflict files were detected:\n\n"
        msg += "\n".join(conflicts)
        msg += "\n\nConflict files happen when two team members edit database files simultaneously.\n"
        msg += "Would you like to open the database folder to resolve them?"

        reply = QMessageBox.question(
            self, "Syncthing Conflict Files", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes and conflicts:
            import os
            import subprocess
            folder = os.path.dirname(conflicts[0])
            subprocess.Popen(f'explorer "{folder}"')

    def _get_selected_client_ids(self) -> list:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        return [int(self.table.item(r, 0).text()) for r in rows]

    def _on_row_selected(self):
        ids = self._get_selected_client_ids()

        if len(ids) == 1:
            client = self.db.get_client(ids[0])
            if not client:
                return
            self.selected_client_id = ids[0]
            client_vals = dict(client["values"])
            client_vals["notes"] = client["notes"]
            self._build_dynamic_form(client_vals, client["service_ids"])
            self.form_widget.setEnabled(True)
            self.selection_label.setText("")
            self.bulk_bar.hide()
            self._update_editor_header(client)
        elif len(ids) > 1:
            # Multiple clients selected: the single-client edit form doesn't
            # apply to a batch, so clear/disable it and route edits through
            # the bulk action buttons (Archive/Restore/Delete/Attach Service)
            # instead, which act on every selected row.
            self.selected_client_id = None
            self._build_dynamic_form()
            self.form_widget.setEnabled(False)
            self.selection_label.setText(f"{len(ids)} selected")
            self.bulk_bar.show()
            self._update_editor_header(multi_count=len(ids))
        else:
            self.selection_label.setText("")
            self.bulk_bar.hide()

    def _on_new(self):
        self.selected_client_id = None
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self._build_dynamic_form()
        self.form_widget.setEnabled(True)
        self.selection_label.setText("")
        self.bulk_bar.hide()
        self._update_editor_header()

    def open_new_client_form(self):
        """Prepare the shared client form for the normal-mode Add Client action."""
        self._on_new()

    def open_client_editor(self, client_id: int):
        """Select a client in the editor so the admin action applies to it."""
        self.refresh()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and int(item.text()) == client_id:
                self.table.selectRow(row)
                self.table.setCurrentCell(row, 1)
                return True
        return False

    def delete_client(self, client_id: int):
        """Permanently remove one client from the admin-only search action."""
        client = self.db.get_client(client_id)
        if not client:
            return
        identity = self._get_identity_label(client)
        if QMessageBox.question(
            self, "Delete client",
            f'Permanently delete "{identity}"? This cannot be undone.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.bulk_delete_clients([client_id])
            self.refresh()

    def manage_client_services(self, client_id: int):
        if self.open_client_editor(client_id):
            self._on_bulk_service()

    def _on_save(self):
        if len(self._get_selected_client_ids()) > 1:
            self.toast_requested.emit("The edit form only applies to one client at a time. Use the bulk buttons for bulk actions.", 3000)
            return

        mcl_cols = self.db.get_mcl_columns()
        if not mcl_cols:
            QMessageBox.warning(self, "No Schema", "Define at least one MCL column before saving clients.")
            return

        values = {col_id: read_input_widget(col_def, widget) for col_id, (col_def, widget) in self._input_widgets.items()}
        notes = self.f_notes.toPlainText().strip()
        service_ids = [sid for sid, cb in self._service_cbs.items() if cb.isChecked()]

        is_new = self.selected_client_id is None

        dupes = self.db.find_duplicate_clients(values, exclude_client_id=self.selected_client_id)
        if dupes:
            proceed = QMessageBox.question(
                self, "Possible duplicate",
                "A client already exists with this identity value:\n\n"
                + "\n".join(f"• {d}" for d in dupes)
                + "\n\nSave anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if proceed != QMessageBox.Yes:
                return

        # FIX: Added actual popups to tell you it worked!
        try:
            if is_new:
                self.selected_client_id = self.db.add_client(values, notes, service_ids, actor=self.actor)
                saved_client = self.db.get_client(self.selected_client_id)
                c_name = self._get_identity_label(saved_client) if saved_client else ""
                self.action_alert_requested.emit("create", c_name)
            else:
                self.db.update_client(self.selected_client_id, values, notes, service_ids, actor=self.actor)
                saved_client = self.db.get_client(self.selected_client_id)
                c_name = self._get_identity_label(saved_client) if saved_client else ""
                self.action_alert_requested.emit("update", c_name)
        except ValueError as val_err:
            QMessageBox.warning(self, "Validation Error", str(val_err))
            return
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save client: {e}")
            return

        self._set_dirty(False)
        self.refresh()

    def _on_archive_toggle(self, checked: bool):
        for btn in self._active_mode_btns:
            btn.setVisible(not checked)
        for btn in self._archived_mode_btns:
            btn.setVisible(checked)
        self.refresh()
        self._sync_filter_chips()

    def _on_archive(self):
        ids = self._get_selected_client_ids()
        if not ids: return
        who = "this client" if len(ids) == 1 else f"these {len(ids)} clients"
        if QMessageBox.question(
            self, "Archive client" if len(ids) == 1 else "Archive clients",
            f"Archive {who}? They'll be hidden from Search and this list "
            "until restored from \"Show Archived\" -- nothing is deleted."
        ) == QMessageBox.Yes:
            self.db.bulk_archive_clients(ids)
            self.action_alert_requested.emit("archive", None)
            self.refresh()

    def _on_restore(self):
        ids = self._get_selected_client_ids()
        if not ids: return
        self.db.bulk_unarchive_clients(ids)
        self.action_alert_requested.emit("unarchive", None)
        self.refresh()

    def _on_purge(self):
        ids = self._get_selected_client_ids()
        if not ids: return
        who = "this client" if len(ids) == 1 else f"these {len(ids)} clients"
        if QMessageBox.question(
            self, "Delete permanently",
            f"Permanently delete {who}? This cannot be undone from within "
            "the app -- only a Syncthing file-version restore could bring it back.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.bulk_delete_clients(ids)
            self.refresh()

    def _on_bulk_service(self):
        ids = self._get_selected_client_ids()
        if not ids:
            self.toast_requested.emit("Select one or more clients first.", 3000)
            return

        services = self.db.get_services()
        if not services:
            self.toast_requested.emit("Define at least one service under 'Manage Services...' first.", 3000)
            return

        names = [s["name"] for s in services]
        name, ok = QInputDialog.getItem(self, "Choose service", "Service:", names, 0, False)
        if not ok:
            return
        service = next(s for s in services if s["name"] == name)

        action = QMessageBox.question(
            self, "Attach or detach",
            f"Attach \"{name}\" to the {len(ids)} selected client(s)?\n\n"
            "Choose No to instead detach it from them.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes
        )
        if action == QMessageBox.Cancel:
            return

        self.db.bulk_set_service(ids, service["id"], attach=(action == QMessageBox.Yes))
        self.refresh()
        if len(ids) == 1:
            self._on_row_selected()

    def _open_unified_settings(self, page="general", on_close_callback=None):
        if getattr(self, "_settings_dialog", None) is None:
            from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
            dlg = UnifiedSettingsDialog(self.db, actor=self.actor, page=page, parent=self.window())
            dlg.toast_requested.connect(self.toast_requested.emit)
            dlg.settings_saved.connect(self.settings_saved.emit)
            self._settings_dialog = dlg
        else:
            self._settings_dialog.set_page(page)

        self._settings_dialog.exec()
        if on_close_callback:
            on_close_callback()

    def _on_backup(self):
        self._open_unified_settings("backup", self.refresh)

    def _on_restore_backup(self):
        self._open_unified_settings("backup", self.refresh)

    def _on_view_audit_log(self):
        from ui.dialogs.audit_log_dialog import AuditLogDialog
        dlg = AuditLogDialog(self.db, actor=self.actor, parent=self)
        dlg.toast_requested.connect(self.toast_requested.emit)
        dlg.exec()

    def _on_export_csv(self):
        self._open_unified_settings("export", self.refresh)

    def _on_download_template(self):
        self._on_export_csv()

    def _on_manage_mcl(self):
        self._open_unified_settings("mcl", lambda: (self._build_dynamic_form(force_rebuild=True), self.refresh()))

    def _on_manage_services(self):
        self._open_unified_settings("services", lambda: (self._reload_filters(), self._build_dynamic_form(force_rebuild=True), self.refresh()))

    def _on_import_csv(self):
        dlg = CSVImportDialog(self.db, self)
        dlg.exec()
        self.refresh()

    def _on_open_settings(self):
        self._open_unified_settings("general")

    def _on_purge_duplicates(self):
        self._open_unified_settings("purge", self.refresh)


    def _on_open_sera_sync(self):
        """Open the Sera Sync dialog for LAN database synchronization."""
        if not hasattr(self, '_sync_service') or self._sync_service is None:
            QMessageBox.warning(self, "Sera Sync", "Sync service is not running. Please restart the app.")
            return
        from ui.dialogs.sera_sync_dialog import SeraSyncDialog
        dlg = SeraSyncDialog(self._sync_service, db=self.db, actor=self.actor, parent=self)
        dlg.exec()

    def set_sync_service(self, sync_service):
        """Inject the SyncPeerService instance for Sera Sync dialog access."""
        self._sync_service = sync_service
