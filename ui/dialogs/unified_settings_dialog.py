"""
unified_settings_dialog.py
--------------------------
Obsidian-style unified settings hub for Project Sera.

Combines: General Settings, Action Buttons, Master Column List (MCL),
Services, Column Visibility (Main/Quick-Copy/Admin), Export CSV,
Backup & Restore, Purge Duplicates.

Audit Log and CSV Import are intentionally kept as separate dialogs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome as qta
except Exception:
    qta = None


# ── Palette ───────────────────────────────────────────────────────────────────
_BG_DIALOG    = "#1a1a1a"
_BG_NAV       = "#1e1e1e"
_BG_CONTENT   = "#202020"
_NAV_HOVER    = "#252525"
_NAV_ACTIVE   = "#282828"
_ACCENT       = "#2E9B5F"
_ACCENT_LIGHT = "#4CF9B7"
_DANGER       = "#FF5252"
_TEXT_PRI     = "#e8e8e8"
_TEXT_SEC     = "#808080"
_TEXT_NAV     = "#a0a0a0"
_TEXT_NAV_ACT = "#ffffff"
_TEXT_SECTION = "#4a4a4a"
_BORDER       = "#2a2a2a"
_INPUT_BG     = "#171717"
_INPUT_BORDER = "#333333"


_ICON_CACHE = {}

def _icon(name: str, color: str = _TEXT_PRI) -> QIcon:
    key = (name, color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    try:
        if qta:
            ic = qta.icon(name, color=color)
            _ICON_CACHE[key] = ic
            return ic
    except Exception:
        pass
    ic = QIcon()
    _ICON_CACHE[key] = ic
    return ic


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {_BORDER}; border: none;")
    return f


# ── Nav Item ──────────────────────────────────────────────────────────────────
class _NavItem(QWidget):
    clicked = Signal()

    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._active = False
        self._icon_name = icon_name
        self._pixmap_normal = _icon(icon_name, color=_TEXT_NAV).pixmap(16, 16)
        self._pixmap_active = _icon(icon_name, color=_ACCENT_LIGHT).pixmap(16, 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 12, 0)
        lay.setSpacing(8)

        self._bar = QFrame()
        self._bar.setFixedWidth(3)
        self._bar.setFixedHeight(18)
        self._bar.setStyleSheet("background: transparent; border: none; border-radius: 1px;")
        lay.addWidget(self._bar)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(16, 16)
        self._icon_lbl.setPixmap(self._pixmap_normal)
        lay.addWidget(self._icon_lbl)

        self._lbl = QLabel(label)
        self._lbl.setStyleSheet(f"color: {_TEXT_NAV}; font-size: 13px; background: transparent;")
        lay.addWidget(self._lbl)
        lay.addStretch()

        self._set_style()

    def set_active(self, active: bool):
        self._active = active
        self._set_style()

    def _set_style(self):
        if self._active:
            self.setStyleSheet(f"background: {_NAV_ACTIVE}; border-radius: 5px;")
            self._bar.setStyleSheet(f"background: {_ACCENT}; border: none; border-radius: 1px;")
            self._lbl.setStyleSheet(
                f"color: {_TEXT_NAV_ACT}; font-size: 13px; font-weight: 600; background: transparent;")
            self._icon_lbl.setPixmap(self._pixmap_active)
        else:
            self.setStyleSheet("background: transparent; border-radius: 5px;")
            self._bar.setStyleSheet("background: transparent; border: none; border-radius: 1px;")
            self._lbl.setStyleSheet(f"color: {_TEXT_NAV}; font-size: 13px; background: transparent;")
            self._icon_lbl.setPixmap(self._pixmap_normal)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event):
        if not self._active:
            self.setStyleSheet(f"background: {_NAV_HOVER}; border-radius: 5px;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._active:
            self.setStyleSheet("background: transparent; border-radius: 5px;")
        super().leaveEvent(event)


# ── Section Label ─────────────────────────────────────────────────────────────
class _SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {_TEXT_SECTION}; font-size: 10px; font-weight: 700; "
            f"letter-spacing: 0.8px; padding: 10px 8px 3px 22px; background: transparent;"
        )


# ── Setting row ───────────────────────────────────────────────────────────────
def _setting_row(title: str, subtitle: str,
                 control: "QWidget | None" = None, *, danger: bool = False) -> QWidget:
    row = QWidget()
    row.setObjectName("SettingRow")
    row.setStyleSheet("QWidget#SettingRow { background: transparent; }")

    outer = QVBoxLayout(row)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    h = QHBoxLayout(inner)
    h.setContentsMargins(28, 13, 28, 13)
    h.setSpacing(16)

    t_col = QVBoxLayout()
    t_col.setSpacing(3)

    t = QLabel(title)
    t.setStyleSheet(
        f"font-size: 13px; font-weight: 600; "
        f"color: {_DANGER if danger else _TEXT_PRI}; background: transparent;")
    t_col.addWidget(t)

    if subtitle:
        s = QLabel(subtitle)
        s.setStyleSheet(f"font-size: 11.5px; color: {_TEXT_SEC}; background: transparent;")
        s.setWordWrap(True)
        t_col.addWidget(s)

    h.addLayout(t_col, stretch=1)
    if control is not None:
        h.addWidget(control, alignment=Qt.AlignVCenter)

    outer.addWidget(inner)
    outer.addWidget(_divider())
    return row


def _page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    v = QVBoxLayout(w)
    v.setContentsMargins(28, 22, 28, 4)
    v.setSpacing(2)
    lbl = QLabel(title)
    lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {_TEXT_PRI};")
    v.addWidget(lbl)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setStyleSheet(f"font-size: 12px; color: {_TEXT_SEC};")
        sub.setWordWrap(True)
        v.addWidget(sub)
    v.addWidget(_divider())
    return w


def _sub_header(title: str) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    v = QVBoxLayout(w)
    v.setContentsMargins(28, 16, 28, 0)
    v.setSpacing(0)
    lbl = QLabel(title.upper())
    lbl.setStyleSheet(
        f"font-size: 10px; font-weight: 700; color: {_TEXT_SECTION}; "
        "letter-spacing: 0.5px;")
    v.addWidget(lbl)
    v.addWidget(_divider())
    return w


def _wrap_scroll(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet(
        f"QScrollArea {{ background: {_BG_CONTENT}; border: none; }}"
        f"QScrollBar:vertical {{ background: {_BG_NAV}; width: 6px; border-radius: 3px; }}"
        f"QScrollBar::handle:vertical {{ background: #383838; border-radius: 3px; min-height: 20px; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
    )
    content.setStyleSheet(f"background: {_BG_CONTENT};")
    scroll.setWidget(content)
    return scroll


# ── Page indices ──────────────────────────────────────────────────────────────
_P_GENERAL   = 0
_P_ACTIONS   = 1
_P_MCL       = 2
_P_SERVICES  = 3
_P_MAIN_VIS  = 4
_P_QC        = 5
_P_ADMIN_VIS = 6
_P_EXPORT    = 7
_P_BACKUP    = 8
_P_PURGE     = 9

_PAGE_MAP = {
    "general":  _P_GENERAL,
    "actions":  _P_ACTIONS,
    "mcl":      _P_MCL,
    "services": _P_SERVICES,
    "main_vis": _P_MAIN_VIS,
    "qc":       _P_QC,
    "admin_vis":_P_ADMIN_VIS,
    "export":   _P_EXPORT,
    "backup":   _P_BACKUP,
    "purge":    _P_PURGE,
}

_SETTINGS_PAGES = {_P_GENERAL, _P_ACTIONS, _P_MAIN_VIS, _P_QC, _P_ADMIN_VIS}


# ── Main Dialog ───────────────────────────────────────────────────────────────
class UnifiedSettingsDialog(QDialog):
    toast_requested  = Signal(str, int)
    settings_saved   = Signal()
    mcl_changed      = Signal()
    services_changed = Signal()

    def __init__(self, db, actor: str = "Admin", page: str = "general", parent=None):
        super().__init__(parent)
        self.db          = db
        self.actor       = actor
        self._start_page = page

        self.setWindowTitle("Settings — Project Sera")
        self.setObjectName("UnifiedSettings")
        self.setModal(True)
        self.resize(1060, 680)
        self.setMinimumSize(820, 520)

        self._nav_items: list = []
        self._stack     = None
        self._btn_save  = None

        self.vis_cbs       = {}
        self.qc_cbs        = {}
        self.admin_vis_cbs = {}

        self.mcl_columns = self.db.get_mcl_columns()

        self._page_builders = {
            _P_GENERAL: self._build_page_general,
            _P_ACTIONS: self._build_page_actions,
            _P_MCL: self._build_page_mcl,
            _P_SERVICES: self._build_page_services,
            _P_MAIN_VIS: lambda: self._build_page_visibility(
                "Main Screen Columns",
                "Choose which columns appear on the main client search grid.",
                "show_in_search", self.vis_cbs),
            _P_QC: lambda: self._build_page_visibility(
                "Quick-Copy Fields",
                "Choose which fields employees can click to instantly copy the value.",
                "allow_quick_copy", self.qc_cbs),
            _P_ADMIN_VIS: lambda: self._build_page_visibility(
                "Admin Screen Columns",
                "Choose which columns appear on the client grid in Admin Mode.",
                "admin_show_in_search", self.admin_vis_cbs),
            _P_EXPORT: self._build_page_export,
            _P_BACKUP: self._build_page_backup,
            _P_PURGE: self._build_page_purge,
        }
        self._page_widgets = {}

        self._initial_state = None
        self._apply_global_style()
        self._build_ui()
        # Switch to requested page (or default to general)
        target_page = _PAGE_MAP.get(self._start_page, _P_GENERAL)
        self._switch_page(target_page)
        self._load_settings()
        self._initial_state = self._capture_state()
        self._update_save_btn_state()

    def set_page(self, page):
        self._start_page = page
        target_page = _PAGE_MAP.get(page, _P_GENERAL) if isinstance(page, str) else page
        self._switch_page(target_page)
        self._load_settings()
        self._initial_state = self._capture_state()
        self._update_save_btn_state()

    # ── Global style ──────────────────────────────────────────────────────────
    def _apply_global_style(self):
        self.setStyleSheet(f"""
            QDialog#UnifiedSettings {{ background: {_BG_DIALOG}; }}
            QLabel {{ background: transparent; color: {_TEXT_PRI}; }}
            QLineEdit, QComboBox, QSpinBox {{
                background: {_INPUT_BG};
                color: {_TEXT_PRI};
                border: 1px solid {_INPUT_BORDER};
                border-radius: 5px;
                padding: 5px 9px;
                font-size: 12px;
                min-width: 160px;
            }}
            QComboBox::drop-down {{ border: none; padding-right: 6px; }}
            QComboBox QAbstractItemView {{
                background: #252525; color: {_TEXT_PRI};
                selection-background-color: {_NAV_ACTIVE};
                border: 1px solid {_INPUT_BORDER};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; }}
            QCheckBox {{ color: {_TEXT_PRI}; font-size: 13px; spacing: 8px; background: transparent; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 1px solid {_INPUT_BORDER};
                border-radius: 3px;
                background: {_INPUT_BG};
            }}
            QCheckBox::indicator:checked {{
                background: {_ACCENT}; border-color: {_ACCENT};
            }}

            QPushButton#UnifiedSaveBtn {{
                background-color: {_ACCENT};
                color: #FFFFFF;
                border: 1px solid #34B76D;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#UnifiedSaveBtn:hover {{
                background-color: #34B76D;
                border-color: #4CF9B7;
            }}
            QPushButton#UnifiedSaveBtn:pressed {{
                background-color: #247A49;
                border-color: {_ACCENT};
            }}
            QPushButton#UnifiedSaveBtn:disabled {{
                background-color: #1a2920;
                color: #4f6e5c;
                border: 1px solid #233b2c;
            }}
            QPushButton#UnifiedCloseBtn {{
                background-color: #262626;
                color: #E0E0E0;
                border: 1px solid #383838;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton#UnifiedCloseBtn:hover {{
                background-color: #333333;
                color: #FFFFFF;
                border-color: #4D4D4D;
            }}
            QPushButton#UnifiedCloseBtn:pressed {{
                background-color: #1A1A1A;
            }}
            QPushButton[class="icon-sm"] {{
                background: #2a2a2a; border: 1px solid #383838;
                border-radius: 5px; padding: 4px; min-width: 0;
            }}
            QPushButton[class="icon-sm"]:hover {{ background: #353535; }}
            QListWidget {{
                background: #1a1a1a; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 6px;
                outline: none; font-size: 13px;
            }}
            QListWidget::item {{ padding: 9px 14px; border-radius: 4px; }}
            QListWidget::item:selected {{ background: {_NAV_ACTIVE}; color: {_ACCENT_LIGHT}; }}
            QListWidget::item:hover:!selected {{ background: {_NAV_HOVER}; }}
            QScrollBar:vertical {{ background: {_BG_NAV}; width: 6px; border-radius: 3px; }}
            QScrollBar::handle:vertical {{ background: #383838; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_nav_panel())

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {_BORDER}; border: none;")
        root.addWidget(sep)

        right_w = QWidget()
        right_w.setStyleSheet(f"background: {_BG_CONTENT};")
        rv = QVBoxLayout(right_w)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {_BG_CONTENT};")
        rv.addWidget(self._stack, stretch=1)
        rv.addWidget(self._build_footer())
        root.addWidget(right_w, stretch=1)

    def _build_nav_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(248)
        panel.setStyleSheet(f"background: {_BG_NAV};")

        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 14, 8, 14)
        v.setSpacing(2)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search settings\u2026")
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: #252525; color: {_TEXT_PRI};
                border: 1px solid #333333; border-radius: 6px;
                padding: 7px 10px; font-size: 12px; min-width: 0;
            }}
        """)
        self._search.textChanged.connect(self._filter_nav)
        v.addWidget(self._search)
        v.addSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")

        nav_w = QWidget()
        nav_w.setStyleSheet("background: transparent;")
        self._nav_layout = QVBoxLayout(nav_w)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(1)

        def _sec(txt):
            lbl = _SectionLabel(txt)
            self._nav_layout.addWidget(lbl)

        def _item(icon, label, page_idx):
            it = _NavItem(icon, label)
            it.clicked.connect(lambda idx=page_idx: self._switch_page(idx))
            self._nav_layout.addWidget(it)
            self._nav_items.append((it, page_idx))

        _sec("App Settings")
        _item("mdi.cog-outline",            "General",            _P_GENERAL)
        _item("mdi.gesture-tap-button",     "Action Buttons",     _P_ACTIONS)

        _sec("Column Schema")
        _item("mdi.view-column-outline",    "Master Column List", _P_MCL)
        _item("mdi.server-network",         "Services",           _P_SERVICES)

        _sec("Visibility")
        _item("mdi.monitor",                "Main Screen",        _P_MAIN_VIS)
        _item("mdi.content-copy",           "Quick-Copy Fields",  _P_QC)
        _item("mdi.shield-account-outline", "Admin Screen",       _P_ADMIN_VIS)

        _sec("Data Tools")
        _item("mdi.download",               "Export CSV",         _P_EXPORT)
        _item("mdi.database-arrow-up",      "Backup & Restore",   _P_BACKUP)
        _item("mdi.delete-sweep-outline",   "Purge Duplicates",   _P_PURGE)

        self._nav_layout.addStretch()
        scroll.setWidget(nav_w)
        v.addWidget(scroll)
        return panel

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(54)
        footer.setStyleSheet(f"background: {_BG_DIALOG}; border-top: 1px solid {_BORDER};")

        h = QHBoxLayout(footer)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(10)
        h.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setObjectName("UnifiedCloseBtn")
        btn_close.setIcon(_icon("mdi.close", color=_TEXT_NAV))
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        h.addWidget(btn_close)

        self._btn_save = QPushButton("Save Changes")
        self._btn_save.setObjectName("UnifiedSaveBtn")
        self._btn_save.setIcon(_icon("mdi.check", color="#ffffff"))
        self._btn_save.setCursor(Qt.PointingHandCursor)
        self._btn_save.clicked.connect(self._on_save_settings)
        h.addWidget(self._btn_save)

        return footer

    def _switch_page(self, page_idx: int):
        if page_idx not in self._page_widgets:
            builder = self._page_builders.get(page_idx)
            if builder:
                widget = builder()
                self._page_widgets[page_idx] = widget
                self._stack.addWidget(widget)
                if hasattr(self, "_initial_state") and self._initial_state is not None:
                    self._load_settings()

        target_widget = self._page_widgets.get(page_idx)
        if target_widget:
            self._stack.setCurrentWidget(target_widget)

        for item, idx in self._nav_items:
            item.set_active(idx == page_idx)
        if self._btn_save:
            self._btn_save.setVisible(page_idx in _SETTINGS_PAGES)

    def _filter_nav(self, text: str):
        text = text.strip().lower()
        for item, _ in self._nav_items:
            item.setVisible(not text or text in item._lbl.text().lower())

    # ── Pages ─────────────────────────────────────────────────────────────────
    def _build_page_general(self) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)

        lay.addWidget(_page_header("General",
            "Configure display preferences, masking, automation daemons, and startup behaviour."))

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light Mode (Default)", "light")
        lay.addWidget(_setting_row("Application Theme", "Switch between light and dark themes.", self.theme_combo))

        self.window_mode_combo = QComboBox()
        self.window_mode_combo.addItem("Fullscreen / Maximized", "fullscreen")
        self.window_mode_combo.addItem("Square Mode (1:1 Aspect)", "square")
        self.window_mode_combo.addItem("Rectangular Mode (950 \u00d7 680)", "rectangular")
        lay.addWidget(_setting_row("Window Display Mode",
            "Controls how the app window opens on launch.", self.window_mode_combo))

        lay.addWidget(_sub_header("Security & Masking"))

        self.mask_mode_combo = QComboBox()
        self.mask_mode_combo.addItem("Last N characters visible  (e.g. *****1234)", "last_n")
        self.mask_mode_combo.addItem("First N characters visible  (e.g. 1234*****)", "first_n")
        self.mask_mode_combo.addItem("Full dots masking  (\u25cf\u25cf\u25cf\u25cf\u25cf\u25cf\u25cf\u25cf\u25cf\u25cf)", "full_dots")
        lay.addWidget(_setting_row("Password Masking Mode",
            "How passwords are obscured in the Client Detail view.", self.mask_mode_combo))

        self.reveal_count_spin = QSpinBox()
        self.reveal_count_spin.setRange(1, 10)
        self.reveal_count_spin.setFixedWidth(72)
        lay.addWidget(_setting_row("Visible Character Count",
            "Number of characters shown when masking is active.", self.reveal_count_spin))

        self.clipboard_spin = QSpinBox()
        self.clipboard_spin.setRange(5, 300)
        self.clipboard_spin.setSuffix("  sec")
        self.clipboard_spin.setFixedWidth(90)
        lay.addWidget(_setting_row("Auto Clipboard Clear",
            "Automatically wipe the clipboard after this many seconds.", self.clipboard_spin))

        self.quick_copy_check = QCheckBox()
        lay.addWidget(_setting_row("Enable Quick-Copy",
            "Master toggle \u2014 allows employees to click field values to copy them.", self.quick_copy_check))

        lay.addWidget(_sub_header("Startup & System"))

        self.run_in_bg_check = QCheckBox()
        lay.addWidget(_setting_row("Keep app running in background",
            "Closing the window minimises Sera to the system tray instead of quitting.", self.run_in_bg_check))

        self.autostart_check = QCheckBox()
        lay.addWidget(_setting_row("Launch at Windows startup",
            "Automatically starts Project Sera in the background when Windows boots.", self.autostart_check))

        lay.addWidget(_sub_header("File Submission Tracker (FST) Daemons"))

        self.fst_check = QCheckBox()
        lay.addWidget(_setting_row("Sera DOM \u2014 DOM Detector (FST)",
            "DOM detector that watches on-screen confirmation messages and elements on web pages.",
            self.fst_check))

        self.sad_check = QCheckBox()
        lay.addWidget(_setting_row("Sera SAD \u2014 API Detector (FST)",
            "Passive network API detector (fetch / XHR) for real-time JSON API capture from government backends.",
            self.sad_check))

        self.sca_check = QCheckBox()
        lay.addWidget(_setting_row("SCA \u2014 Sera Clipboard Assist",
            "When a client User ID is copied from a spreadsheet, arms matching portal credentials automatically.",
            self.sca_check))

        self.sca_mode_combo = QComboBox()
        self.sca_mode_combo.addItem("Ambient Autofill  (silently fills password on paste)", "autofill")
        self.sca_mode_combo.addItem("SCA Widget  (floating 1-click password prompt on paste)", "widget")
        lay.addWidget(_setting_row("SCA Action Mode",
            "Whether SCA silently injects credentials or shows a 1-click floating prompt.", self.sca_mode_combo))

        self.sca_max_uses_spin = QSpinBox()
        self.sca_max_uses_spin.setRange(1, 20)
        self.sca_max_uses_spin.setSuffix(" uses")
        lay.addWidget(_setting_row("SCA Uses per Copied UID",
            "Maximum successful SCA fills allowed after one UID is copied.", self.sca_max_uses_spin))

        lay.addWidget(_sub_header("Schema Info"))

        id_col = self.db.get_id_column()
        if id_col:
            id_ctrl = QLabel(f"'{id_col['label']}' \u2014 Wired to Client Auto-Serial Numbers")
            id_ctrl.setStyleSheet(f"color: {_ACCENT_LIGHT}; font-weight: 700; font-size: 12px;")
        else:
            id_ctrl = QLabel("Not assigned \u2014 can be set in Master Column List")
            id_ctrl.setStyleSheet(f"color: {_TEXT_SEC}; font-style: italic; font-size: 12px;")
        lay.addWidget(_setting_row("ID / Primary Key Column",
            "Column used as the client's unique identifier and auto-serial source.", id_ctrl))

        self.theme_combo.currentIndexChanged.connect(self._on_control_changed)
        self.window_mode_combo.currentIndexChanged.connect(self._on_control_changed)
        self.mask_mode_combo.currentIndexChanged.connect(self._on_control_changed)
        self.reveal_count_spin.valueChanged.connect(self._on_control_changed)
        self.clipboard_spin.valueChanged.connect(self._on_control_changed)
        self.quick_copy_check.toggled.connect(self._on_control_changed)
        self.run_in_bg_check.toggled.connect(self._on_control_changed)
        self.autostart_check.toggled.connect(self._on_control_changed)
        self.fst_check.toggled.connect(self._on_control_changed)
        self.sad_check.toggled.connect(self._on_control_changed)
        self.sca_check.toggled.connect(self._on_control_changed)
        self.sca_mode_combo.currentIndexChanged.connect(self._on_control_changed)
        self.sca_max_uses_spin.valueChanged.connect(self._on_control_changed)

        lay.addStretch()
        return _wrap_scroll(w)

    def _build_page_actions(self) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)

        lay.addWidget(_page_header("Action Buttons",
            "Control which action buttons appear in the Client Detail view."))

        self.btn_ext_check = QCheckBox()
        lay.addWidget(_setting_row("Enable \u2018Ext\u2019 Button",
            "Extension Autofill \u2014 fills portal credentials via the Sera browser extension.", self.btn_ext_check))

        self.btn_assist_check = QCheckBox()
        lay.addWidget(_setting_row("Enable \u2018Assist\u2019 Button",
            "SMTI Manual Assist \u2014 manually triggers the multi-tab portal interaction flow.", self.btn_assist_check))

        self.btn_copy_check = QCheckBox()
        lay.addWidget(_setting_row("Enable \u2018Copy\u2019 Button",
            "MECP Manual Copy \u2014 copies all service credentials to the clipboard in one click.", self.btn_copy_check))

        self.show_hide_check = QCheckBox()
        lay.addWidget(_setting_row("Enable \u2018Show / Hide\u2019 Eye Buttons",
            "Shows a password-reveal toggle next to each credential field in Client Detail.", self.show_hide_check))

        self.btn_ext_check.toggled.connect(self._on_control_changed)
        self.btn_assist_check.toggled.connect(self._on_control_changed)
        self.btn_copy_check.toggled.connect(self._on_control_changed)
        self.show_hide_check.toggled.connect(self._on_control_changed)

        lay.addStretch()
        return _wrap_scroll(w)

    def _build_page_mcl(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {_BG_CONTENT};")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(10)

        lay.addWidget(_page_header("Master Column List",
            "Define the schema for all client data fields. Changes apply immediately."))

        tb = QHBoxLayout()
        tb.setSpacing(6)

        def _ib(icon_name, color, tip):
            btn = QPushButton()
            btn.setProperty("class", "icon-sm")
            btn.setIcon(_icon(icon_name, color=color))
            btn.setIconSize(QSize(16, 16))
            btn.setFixedSize(30, 30)
            btn.setToolTip(tip)
            return btn

        self._mcl_btn_add  = _ib("mdi.plus",          _ACCENT_LIGHT, "Add Column")
        self._mcl_btn_edit = _ib("mdi.pencil-outline", _TEXT_NAV,     "Edit Selected")
        self._mcl_btn_del  = _ib("mdi.delete-outline", _DANGER,       "Delete Selected")
        self._mcl_btn_up   = _ib("mdi.arrow-up",       _TEXT_NAV,     "Move Up")
        self._mcl_btn_dn   = _ib("mdi.arrow-down",     _TEXT_NAV,     "Move Down")

        for btn in (self._mcl_btn_edit, self._mcl_btn_del, self._mcl_btn_up, self._mcl_btn_dn):
            btn.setEnabled(False)

        for btn in (self._mcl_btn_add, self._mcl_btn_edit, self._mcl_btn_del):
            tb.addWidget(btn)
        tb.addSpacing(6)
        for btn in (self._mcl_btn_up, self._mcl_btn_dn):
            tb.addWidget(btn)
        tb.addStretch()
        lay.addLayout(tb)

        self._mcl_list = QListWidget()
        self._mcl_list.setSelectionMode(QAbstractItemView.SingleSelection)
        lay.addWidget(self._mcl_list, stretch=1)

        self._mcl_btn_add.clicked.connect(self._mcl_on_add)
        self._mcl_btn_edit.clicked.connect(self._mcl_on_edit)
        self._mcl_btn_del.clicked.connect(self._mcl_on_delete)
        self._mcl_btn_up.clicked.connect(lambda: self._mcl_on_move(-1))
        self._mcl_btn_dn.clicked.connect(lambda: self._mcl_on_move(1))
        self._mcl_list.currentRowChanged.connect(self._mcl_on_sel_changed)

        self._mcl_reload()
        return container

    def _build_page_services(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {_BG_CONTENT};")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(10)

        lay.addWidget(_page_header("Services",
            "Define compliance portals and automation services. Changes apply immediately."))

        tb = QHBoxLayout()
        tb.setSpacing(6)

        def _ib(icon_name, color, tip):
            btn = QPushButton()
            btn.setProperty("class", "icon-sm")
            btn.setIcon(_icon(icon_name, color=color))
            btn.setIconSize(QSize(16, 16))
            btn.setFixedSize(30, 30)
            btn.setToolTip(tip)
            return btn

        self._svc_btn_add  = _ib("mdi.plus",          _ACCENT_LIGHT, "Add Service")
        self._svc_btn_edit = _ib("mdi.pencil-outline", _TEXT_NAV,     "Edit Selected")
        self._svc_btn_del  = _ib("mdi.delete-outline", _DANGER,       "Delete Selected")

        self._svc_btn_edit.setEnabled(False)
        self._svc_btn_del.setEnabled(False)

        for btn in (self._svc_btn_add, self._svc_btn_edit, self._svc_btn_del):
            tb.addWidget(btn)
        tb.addStretch()
        lay.addLayout(tb)

        self._svc_list = QListWidget()
        lay.addWidget(self._svc_list, stretch=1)

        self._svc_btn_add.clicked.connect(self._svc_on_add)
        self._svc_btn_edit.clicked.connect(self._svc_on_edit)
        self._svc_btn_del.clicked.connect(self._svc_on_delete)
        self._svc_list.currentRowChanged.connect(self._svc_on_sel_changed)

        self._svc_reload()
        return container

    def _build_page_visibility(self, title: str, subtitle: str,
                                field: str, cbs: dict) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)

        lay.addWidget(_page_header(title, subtitle))

        for col in self.mcl_columns:
            cb = QCheckBox()
            if field == "show_in_search":
                cb.setChecked(bool(col.get("show_in_search", True)))
            elif field == "allow_quick_copy":
                cb.setChecked(bool(col.get("allow_quick_copy", False)))
            elif field == "admin_show_in_search":
                cb.setChecked(bool(col.get("admin_show_in_search", True)))
            cb.toggled.connect(self._on_control_changed)
            cbs[col["id"]] = cb
            lay.addWidget(_setting_row(
                col["label"], f"Field type: {col.get('field_type', 'text')}", cb))

        lay.addStretch()
        return _wrap_scroll(w)

    def _build_page_export(self) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)

        lay.addWidget(_page_header("Export CSV", "Download client data or a blank import template."))

        export_btn = QPushButton("Export All Clients \u2192")
        export_btn.setProperty("class", "primary")
        export_btn.setIcon(_icon("mdi.download", color="#ffffff"))
        export_btn.clicked.connect(self._on_export_csv)
        lay.addWidget(_setting_row("Export to CSV",
            "Download all active client records as a CSV spreadsheet file.", export_btn))

        tmpl_btn = QPushButton("Download Template \u2192")
        tmpl_btn.setIcon(_icon("mdi.file-download-outline", color=_TEXT_PRI))
        tmpl_btn.clicked.connect(self._on_download_template)
        lay.addWidget(_setting_row("Download Import Template",
            "Get a blank CSV template matching your current Master Column List schema.", tmpl_btn))

        lay.addStretch()
        return _wrap_scroll(w)

    def _build_page_backup(self) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)

        lay.addWidget(_page_header("Backup & Restore",
            "Protect your data with regular backups or roll back to a previous snapshot."))

        backup_btn = QPushButton("Choose Backup Folder \u2192")
        backup_btn.setProperty("class", "primary")
        backup_btn.setIcon(_icon("mdi.database-arrow-up", color="#ffffff"))
        backup_btn.clicked.connect(self._on_backup)
        lay.addWidget(_setting_row("Backup Database",
            "Copy the current encrypted database to a folder of your choice.", backup_btn))

        restore_btn = QPushButton("Restore from Backup \u2192")
        restore_btn.setProperty("class", "danger")
        restore_btn.setIcon(_icon("mdi.database-arrow-down", color=_DANGER))
        restore_btn.clicked.connect(self._on_restore_backup)
        lay.addWidget(_setting_row("Restore Database",
            "Replace the live database with a previous backup. The app will restart. Use with caution.",
            restore_btn, danger=True))

        lay.addStretch()
        return _wrap_scroll(w)

    def _build_page_purge(self) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)

        lay.addWidget(_page_header("Purge Duplicates",
            "Permanently remove duplicate client records that share identical identity values."))

        purge_btn = QPushButton("Run Deduplication \u2192")
        purge_btn.setProperty("class", "danger")
        purge_btn.setIcon(_icon("mdi.delete-sweep-outline", color=_DANGER))
        purge_btn.clicked.connect(self._on_purge_duplicates)
        lay.addWidget(_setting_row("Purge Duplicate Clients",
            "Scans all non-archived clients and deletes duplicates sharing the same identity values. "
            "The oldest record (lowest ID) is kept per group. This cannot be undone.",
            purge_btn, danger=True))

        lay.addStretch()
        return _wrap_scroll(w)

    # ── State Tracking & Validation ───────────────────────────────────────────
    def _capture_state(self) -> dict:
        state = {}
        if hasattr(self, "theme_combo"):
            state["theme"] = self.theme_combo.currentData()
            state["window_mode"] = self.window_mode_combo.currentData()
            state["mask_mode"] = self.mask_mode_combo.currentData()
            state["reveal_count"] = self.reveal_count_spin.value()
            state["clipboard"] = self.clipboard_spin.value()
            state["quick_copy"] = self.quick_copy_check.isChecked()
            state["run_in_bg"] = self.run_in_bg_check.isChecked()
            state["autostart"] = self.autostart_check.isChecked()
            state["fst"] = self.fst_check.isChecked()
            state["sad"] = self.sad_check.isChecked()
            state["sca"] = self.sca_check.isChecked()
            state["sca_mode"] = self.sca_mode_combo.currentData()
            state["sca_max_uses"] = self.sca_max_uses_spin.value()
        if hasattr(self, "btn_ext_check"):
            state["btn_ext"] = self.btn_ext_check.isChecked()
            state["btn_assist"] = self.btn_assist_check.isChecked()
            state["btn_copy"] = self.btn_copy_check.isChecked()
            state["show_hide"] = self.show_hide_check.isChecked()
        state["vis"] = {cid: cb.isChecked() for cid, cb in self.vis_cbs.items()}
        state["qc"] = {cid: cb.isChecked() for cid, cb in self.qc_cbs.items()}
        state["admin_vis"] = {cid: cb.isChecked() for cid, cb in self.admin_vis_cbs.items()}
        return state

    def _on_control_changed(self, *args):
        self._update_save_btn_state()

    def _update_save_btn_state(self):
        if not hasattr(self, "_btn_save") or self._btn_save is None:
            return
        if not hasattr(self, "_initial_state") or self._initial_state is None:
            self._btn_save.setEnabled(False)
            self._btn_save.setIcon(_icon("mdi.check", color="#4f6e5c"))
            self._btn_save.setCursor(Qt.ArrowCursor)
            return
        is_dirty = (self._capture_state() != self._initial_state)
        self._btn_save.setEnabled(is_dirty)
        if is_dirty:
            self._btn_save.setIcon(_icon("mdi.check", color="#ffffff"))
            self._btn_save.setCursor(Qt.PointingHandCursor)
        else:
            self._btn_save.setIcon(_icon("mdi.check", color="#4f6e5c"))
            self._btn_save.setCursor(Qt.ArrowCursor)

    # ── Load settings ─────────────────────────────────────────────────────────
    def _load_settings(self):
        all_settings = self.db.get_all_settings() if hasattr(self.db, "get_all_settings") else {}
        g = lambda k, d="": all_settings.get(k, self.db.get_setting(k, d) if not all_settings else d)

        def _set(combo, val):
            idx = combo.findData(val)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        if hasattr(self, "theme_combo"):
            _set(self.theme_combo,       g("theme", "light"))
            _set(self.window_mode_combo, g("window_mode", "fullscreen"))
            _set(self.mask_mode_combo,   g("mask_mode", "last_n"))

            self.reveal_count_spin.setValue(int(g("mask_reveal_count", "4")))
            self.clipboard_spin.setValue(int(g("clipboard_clear_seconds", "30")))
            self.quick_copy_check.setChecked(g("quick_copy_enabled", "0") == "1")
            self.run_in_bg_check.setChecked(g("run_in_background", "1") == "1")
            self.fst_check.setChecked(g("fst_enabled", "1") == "1")
            self.sad_check.setChecked(g("sad_enabled", "1") == "1")
            self.sca_check.setChecked(g("sca_enabled", "1") == "1")
            try:
                self.sca_max_uses_spin.setValue(max(1, min(int(g("sca_max_uses", "1")), 20)))
            except (TypeError, ValueError):
                self.sca_max_uses_spin.setValue(1)

            sca_mode = g("sca_action_mode", "autofill")
            if sca_mode == "assist":
                sca_mode = "widget"
            _set(self.sca_mode_combo, sca_mode)

            try:
                from ui.utils import autostart
                self.autostart_check.setChecked(autostart.is_autostart_enabled())
            except Exception:
                self.autostart_check.setChecked(False)

        if hasattr(self, "btn_ext_check"):
            self.btn_ext_check.setChecked(g("extension_autofill_enabled", "1") == "1")
            self.btn_assist_check.setChecked(g("manual_assist_enabled", "1") == "1")
            self.btn_copy_check.setChecked(g("manual_copy_btn_enabled", "1") == "1")
            self.show_hide_check.setChecked(g("show_hide_btn_enabled", "1") == "1")

    # ── Save settings ─────────────────────────────────────────────────────────
    def _on_save_settings(self):
        try:
            bulk_settings = {}
            b = lambda cb: "1" if cb.isChecked() else "0"

            if hasattr(self, "theme_combo"):
                bulk_settings["theme"]                   = self.theme_combo.currentData()
                bulk_settings["window_mode"]             = self.window_mode_combo.currentData()
                bulk_settings["mask_mode"]               = self.mask_mode_combo.currentData()
                bulk_settings["mask_reveal_count"]       = str(self.reveal_count_spin.value())
                bulk_settings["clipboard_clear_seconds"] = str(self.clipboard_spin.value())
                bulk_settings["quick_copy_enabled"]      = b(self.quick_copy_check)
                bulk_settings["run_in_background"]       = b(self.run_in_bg_check)
                bulk_settings["fst_enabled"]             = b(self.fst_check)
                bulk_settings["sad_enabled"]             = b(self.sad_check)
                bulk_settings["sca_enabled"]             = b(self.sca_check)
                bulk_settings["sca_action_mode"]         = self.sca_mode_combo.currentData() or "autofill"
                bulk_settings["sca_max_uses"]             = str(self.sca_max_uses_spin.value())
                bulk_settings["tracker_enabled"]         = "1" if (self.fst_check.isChecked() or self.sad_check.isChecked()) else "0"

                try:
                    from automation import update_extension_settings
                    update_extension_settings(
                        fst_enabled=self.fst_check.isChecked(),
                        sad_enabled=self.sad_check.isChecked(),
                        tracker_enabled=(self.fst_check.isChecked() or self.sad_check.isChecked()),
                        sca_enabled=self.sca_check.isChecked(),
                        sca_mode=self.sca_mode_combo.currentData() or "autofill",
                        sca_max_uses=self.sca_max_uses_spin.value(),
                        allowed_services=self.db.get_services(),
                    )
                except Exception:
                    pass

                try:
                    from ui.utils import autostart
                    autostart.set_autostart_enabled(self.autostart_check.isChecked())
                except Exception:
                    pass

            if hasattr(self, "btn_ext_check"):
                bulk_settings["extension_autofill_enabled"] = b(self.btn_ext_check)
                bulk_settings["manual_assist_enabled"]      = b(self.btn_assist_check)
                bulk_settings["manual_copy_btn_enabled"]    = b(self.btn_copy_check)
                bulk_settings["show_hide_btn_enabled"]      = b(self.show_hide_check)

            if hasattr(self.db, "set_settings_bulk"):
                self.db.set_settings_bulk(bulk_settings)
            else:
                for k, v in bulk_settings.items():
                    self.db.set_setting(k, v)

            if self.vis_cbs:
                self.db.bulk_update_mcl_visibility(
                    [cid for cid, cb in self.vis_cbs.items() if cb.isChecked()])
            if self.qc_cbs:
                self.db.bulk_update_mcl_quick_copy(
                    [cid for cid, cb in self.qc_cbs.items() if cb.isChecked()])
            if self.admin_vis_cbs:
                self.db.bulk_update_mcl_admin_visibility(
                    [cid for cid, cb in self.admin_vis_cbs.items() if cb.isChecked()])

            self.db.log_action(self.actor, "update_settings",
                               detail="Saved via Unified Settings Hub")
            self.toast_requested.emit("Settings saved successfully!", 3000)
            self.settings_saved.emit()
            self._initial_state = self._capture_state()
            self._update_save_btn_state()

        except Exception as e:
            QMessageBox.critical(self, "Error Saving Settings", str(e))

    # ── MCL actions ───────────────────────────────────────────────────────────
    def _mcl_reload(self):
        self._mcl_list.clear()
        for c in self.db.get_mcl_columns():
            tags = []
            if c.get("field_type") == "id":        tags.append("ID/Serial")
            if c.get("is_internal_pk"):            tags.append("Internal PK")
            if c.get("is_identity"):               tags.append("Identity")
            if c.get("field_type") == "password":  tags.append("Secret")
            if c.get("field_type") == "dropdown":  tags.append("Dropdown")
            tag_str = f"  [{' \u00b7 '.join(tags)}]" if tags else ""
            item = QListWidgetItem(f"{c['label']}  ({c.get('field_type','text')}){tag_str}")
            item.setData(Qt.UserRole, c["id"])
            self._mcl_list.addItem(item)

    def _mcl_on_sel_changed(self, row: int):
        has = row >= 0
        self._mcl_btn_edit.setEnabled(has)
        self._mcl_btn_del.setEnabled(has)
        self._mcl_btn_up.setEnabled(row > 0)
        self._mcl_btn_dn.setEnabled(has and row < self._mcl_list.count() - 1)

    def _mcl_sel_id(self):
        item = self._mcl_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _mcl_on_add(self):
        from ui.dialogs.mcl_manager_dialog import ColumnEditDialog
        dlg = ColumnEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.db.create_mcl_column(**dlg.result_data())
            self._mcl_reload()
            self.mcl_changed.emit()

    def _mcl_on_edit(self):
        col_id = self._mcl_sel_id()
        if col_id is None:
            return
        cur = next((c for c in self.db.get_mcl_columns() if c["id"] == col_id), None)
        if not cur:
            return
        from ui.dialogs.mcl_manager_dialog import ColumnEditDialog
        dlg = ColumnEditDialog(self, cur["label"], cur.get("field_type", "text"),
                               cur.get("dropdown_options"), cur.get("is_identity", 0), cur.get("is_internal_pk", 0))
        if dlg.exec() == QDialog.Accepted:
            self.db.update_mcl_column(col_id, **dlg.result_data())
            self._mcl_reload()
            self.mcl_changed.emit()

    def _mcl_on_delete(self):
        col_id = self._mcl_sel_id()
        if col_id is None:
            return
        if QMessageBox.question(
            self, "Delete Column",
            "Delete this column? All client values for this column will be permanently removed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.delete_mcl_column(col_id)
            self._mcl_reload()
            self.mcl_changed.emit()

    def _mcl_on_move(self, direction: int):
        col_id = self._mcl_sel_id()
        if col_id is None:
            return
        ids = [c["id"] for c in self.db.get_mcl_columns()]
        idx = ids.index(col_id)
        new_idx = idx + direction
        if 0 <= new_idx < len(ids):
            ids[idx], ids[new_idx] = ids[new_idx], ids[idx]
            self.db.reorder_mcl_columns(ids)
            self._mcl_reload()
            self._mcl_list.setCurrentRow(new_idx)

    # ── Service actions ───────────────────────────────────────────────────────
    def _svc_reload(self):
        self._svc_list.clear()
        for svc in self.db.get_services():
            mode = svc.get("automation_mode", "manual").capitalize()
            item = QListWidgetItem(f"{svc['name']}  [{mode}]")
            item.setData(Qt.UserRole, svc["id"])
            self._svc_list.addItem(item)

    def _svc_on_sel_changed(self, row: int):
        has = row >= 0
        self._svc_btn_edit.setEnabled(has)
        self._svc_btn_del.setEnabled(has)

    def _svc_sel_id(self):
        item = self._svc_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _svc_on_add(self):
        from ui.dialogs.service_manager_dialog import ServiceEditDialog
        dlg = ServiceEditDialog(self.db, self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self.db.create_service(**dlg.result_data())
                self.db.auto_populate_service_selectors()
                self._svc_reload()
                self.services_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _svc_on_edit(self):
        svc_id = self._svc_sel_id()
        if svc_id is None:
            return
        cur = next((s for s in self.db.get_services() if s["id"] == svc_id), None)
        if not cur:
            return
        from ui.dialogs.service_manager_dialog import ServiceEditDialog
        dlg = ServiceEditDialog(self.db, self, cur)
        if dlg.exec() == QDialog.Accepted:
            self.db.update_service(svc_id, **dlg.result_data())
            self.db.auto_populate_service_selectors()
            self._svc_reload()
            self.services_changed.emit()

    def _svc_on_delete(self):
        svc_id = self._svc_sel_id()
        if svc_id is None:
            return
        if QMessageBox.question(
            self, "Delete Service",
            "Delete this service? It will be detached from all client profiles.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.delete_service(svc_id)
            self._svc_reload()
            self.services_changed.emit()

    # ── Data tool actions ─────────────────────────────────────────────────────
    def _on_export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Clients to CSV", "clients_export.csv", "CSV Files (*.csv)")
        if path:
            try:
                self.db.export_clients_csv(path)
                self.db.log_action(self.actor, "csv_export", detail=f"Exported to {path}")
                self.toast_requested.emit(f"Exported successfully to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def _on_download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Download Import Template", "clients_import_template.csv", "CSV Files (*.csv)")
        if path:
            try:
                self.db.export_mcl_schema_csv(path)
                self.toast_requested.emit(f"Template saved to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Download Error", str(e))

    def _on_backup(self):
        dest = QFileDialog.getExistingDirectory(self, "Choose Backup Destination")
        if not dest:
            return
        try:
            path = self.db.backup_to(dest)
            self.db.log_action(self.actor, "backup", detail=f"Backed up to {path}")
            self.toast_requested.emit(f"Backup saved:\n{path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Backup Failed", str(e))

    def _on_restore_backup(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Restore Source")
        msg.setText("How would you like to locate the backup?")
        btn_folder = msg.addButton("Select Backup Folder", QMessageBox.AcceptRole)
        btn_file   = msg.addButton("Select Specific File (.db)", QMessageBox.AcceptRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_file:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose Database File", "",
                "Database Files (*.db *.sqlite *sync-conflict*);;All Files (*)")
        elif clicked == btn_folder:
            path = QFileDialog.getExistingDirectory(self, "Choose Backup Folder")
        else:
            return
        if not path:
            return
        if QMessageBox.warning(
            self, "Confirm Restore",
            "WARNING: This will overwrite your live database.\n\n"
            "If Syncthing is active, the restored database will also sync to all team members' PCs.\n\n"
            "The application will restart. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            try:
                summary = self.db.restore_from(path)
                self.db.log_action(self.actor, "restore", detail=summary)
                QMessageBox.information(
                    self, "Restore Successful",
                    f"{summary}\n\nThe application will now restart.")
                import version
                version.restart_app()
            except Exception as e:
                QMessageBox.critical(self, "Restore Error", str(e))

    def _on_purge_duplicates(self):
        if QMessageBox.question(
            self, "Purge Duplicate Clients",
            "This will permanently delete duplicate clients (keeping the oldest record per group).\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            results = self.db.purge_duplicate_clients()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if results["deleted"] == 0:
            self.toast_requested.emit("No duplicate clients were found.", 3000)
        else:
            self.db.log_action(
                self.actor, "purge_duplicates",
                detail=f"Purged {results['deleted']} duplicate(s) across {results['groups']} group(s)")
            QMessageBox.information(
                self, "Purge Complete",
                f"\u2705  Deleted {results['deleted']} duplicate client(s) "
                f"across {results['groups']} group(s).")
