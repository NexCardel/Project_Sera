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
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.components.kit import (
    MessageBox,
    ROLE_ACTIONS, ROLE_PILL, ROLE_TAG, Banner, Card, CellAction, DarkListTable, DialogFrame, EmptyState,
    FieldLabel, Input, LabelledAction, NavItem, NavLabel, PageHeader, Pill, Select, SettingRow, Stepper,
    Switch, Tag, Tile, UnsavedBar, sidebar_qss,
)
from ui.dialogs.mcl_manager_dialog import mcl_role as _mcl_role
from ui.utils.masking import mask_password
from ui.utils.tokens import TOKENS

try:
    import qtawesome as qta
except Exception:
    qta = None


# ── Page indices ──────────────────────────────────────────────────────────────
_P_GENERAL   = 0
_P_ACTIONS   = 1
_P_SCC       = 2
_P_MCL       = 3
_P_SERVICES  = 4
_P_MAIN_VIS  = 5
_P_QC        = 6
_P_ADMIN_VIS = 7
_P_EXPORT    = 8
_P_BACKUP    = 9
_P_PURGE     = 10
_P_TRACKER   = 11

_PAGE_MAP = {
    "general":  _P_GENERAL,
    "actions":  _P_ACTIONS,
    "scc":      _P_SCC,
    "mcl":      _P_MCL,
    "services": _P_SERVICES,
    "main_vis": _P_MAIN_VIS,
    "qc":       _P_QC,
    "admin_vis":_P_ADMIN_VIS,
    "export":   _P_EXPORT,
    "backup":   _P_BACKUP,
    "purge":    _P_PURGE,
    "tracker":  _P_TRACKER,
}

_SETTINGS_PAGES = {_P_GENERAL, _P_ACTIONS, _P_SCC, _P_MAIN_VIS, _P_QC, _P_ADMIN_VIS, _P_TRACKER}

# Setting rows stack (title above control) when the page pane gets this narrow; the two limits leave
# a gap so a pane sitting on the edge does not flip on every resize.
_PANE_COMPACT_BELOW = 700
_PANE_WIDE_ABOVE = 740

_MASK_SAMPLE = "Abwf@12345"      # made-up password for the masking preview

# state key -> control attribute, for Discard (see _capture_state)
_STATE_COMBOS = {"theme": "theme_combo", "window_mode": "window_mode_combo", "mask_mode": "mask_mode_combo",
                 "sca_mode": "sca_mode_combo", "scc_detect_mode": "scc_detect_combo",
                 "sgt_mode": "sgt_mode_combo", "sgt_i_mode": "sgt_i_combo"}
_STATE_STEPPERS = {"reveal_count": "reveal_count_spin", "clipboard": "clipboard_spin",
                   "sca_max_uses": "sca_max_uses_spin"}
_STATE_CHECKS = {"quick_copy": "quick_copy_check", "run_in_bg": "run_in_bg_check",
                 "autostart": "autostart_check", "sca": "sca_check", "show_hide": "show_hide_check",
                 "scc_enabled": "scc_check", "vsdc_enabled": "vsdc_check", "vsdc_x_enabled": "vsdc_x_check",
                 "vsdc247_enabled": "vsdc247_check", "vsdc_hud_enabled": "vsdc_hud_check",
                 "sgt_record_pages": "sgt_record_check", "sdis_record": "sdis_record_check"}
_STATE_EDITS = {f"scc_opt{n}_{part}": f"scc_opt{n}_{part}_edit" for n in (1, 2, 3, 4) for part in ("label", "str")}

# The three per-column visibility flags: state key, mcl_columns field, default when the row lacks it.
_FLAGS = (("vis", "show_in_search", True), ("qc", "allow_quick_copy", False),
          ("admin_vis", "admin_show_in_search", True))
_FLAG_DEFAULTS = {field: default for _key, field, default in _FLAGS}

# Master Column List table: Column · Type · Role · Shows on M · Q · A · Edit
_MCL_HEADS = ["Column", "Type", "Role", "M", "Q", "A", ""]
_MCL_FLAG_COLS = {3: ("show_in_search", "Shows on the Main screen"),
                  4: ("allow_quick_copy", "Quick-copy field"),
                  5: ("admin_show_in_search", "Shows on the Admin screen")}
_MCL_COL_EDIT = 6

# Services table: Service · Automation mode · Mode 2 · Login URL · Edit
_SVC_HEADS = ["Service", "Automation mode", "Mode 2", "Login URL", ""]
_SVC_COL_EDIT = 4


def _flag(col: dict, field: str) -> bool:
    return bool(col.get(field, _FLAG_DEFAULTS[field]))


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
        self._nav_groups: list = []
        self._stack     = None
        self._btn_save  = None
        self._unsaved   = None
        self._restoring = False
        self._compact   = False

        self.vis_cbs       = {}
        self.qc_cbs        = {}
        self.admin_vis_cbs = {}
        self._vis_headers  = {}
        self._painting     = False

        # One model of the columns and their three visibility flags; the Columns table and the three
        # visibility pages are views of it, and Save writes it (4.3).
        self.mcl_columns = self.db.get_mcl_columns()

        self._page_builders = {
            _P_GENERAL: self._build_page_general,
            _P_ACTIONS: self._build_page_actions,
            _P_SCC: self._build_page_scc,
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
            _P_TRACKER: self._build_page_tracker,
        }
        self._page_widgets = {}

        self._initial_state = None
        self._loaded_groups = set()
        self._build_ui()

        # Only the requested page is built now (building all eight settings pages cost ~0.75-1.5 s
        # before the dialog could show); each other page is built, loaded and added to the
        # dirty-tracking baseline the first time it is visited - see _on_page_built.
        target_page = _PAGE_MAP.get(self._start_page, _P_GENERAL)
        self._switch_page(target_page)
        self._load_settings()
        self._initial_state = self._capture_state()
        self._update_save_btn_state()

    def set_page(self, page):
        self._start_page = page
        target_page = _PAGE_MAP.get(page, _P_GENERAL) if isinstance(page, str) else page
        self._switch_page(target_page)
        self._load_settings(force=True)         # reopening shows the saved values again
        self._initial_state = self._capture_state()
        self._update_save_btn_state()

    def _on_page_built(self):
        """A page was just built: load its controls and treat their loaded values as 'unchanged'
        - without touching what the user may have changed on pages already built."""
        self._load_settings()
        if self._initial_state is None:
            return
        for key, value in self._capture_state().items():
            if isinstance(value, dict):
                base = self._initial_state.setdefault(key, {})
                for cid, checked in value.items():
                    base.setdefault(cid, checked)
            else:
                self._initial_state.setdefault(key, value)
        self._update_save_btn_state()

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._frame = DialogFrame("Settings", "Project Sera \u00b7 this PC", self, icon="mdi.cog", full=True)
        self._frame.add_close_button()
        self._frame.closeRequested.connect(self.reject)
        outer.addWidget(self._frame)

        body = self._frame.body_layout
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        hub = QWidget()
        row = QHBoxLayout(hub)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_nav_panel())

        pane = QWidget()
        pv = QVBoxLayout(pane)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        self._unsaved = UnsavedBar(reserved_space=True)
        self._unsaved.discard_clicked.connect(self._on_discard)
        self._unsaved.save_clicked.connect(self._on_save_settings)
        bar_wrap = QHBoxLayout()
        bar_wrap.setContentsMargins(TOKENS["s_5"], TOKENS["s_3"], TOKENS["s_5"], 0)
        bar_wrap.addWidget(self._unsaved)
        pv.addLayout(bar_wrap)
        self._stack = QStackedWidget()
        pv.addWidget(self._stack, 1)
        row.addWidget(pane, 1)
        body.addWidget(hub, 1)
        body.setAlignment(hub, Qt.AlignmentFlag(0))

        self._build_footer()

    def _build_nav_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Sidebar")
        panel.setStyleSheet(sidebar_qss())
        panel.setFixedWidth(TOKENS["w_setnav"])

        v = QVBoxLayout(panel)
        v.setContentsMargins(TOKENS["s_3"], TOKENS["s_3"], TOKENS["s_3"], TOKENS["s_3"])
        v.setSpacing(TOKENS["s_2"])

        self._search = Input(placeholder="Search settings\u2026", icon="mdi.magnify")
        self._search.setAccessibleName("Search settings")
        self._search.textChanged.connect(self._filter_nav)
        v.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setObjectName("SidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        nav_w = QWidget()
        nav_w.setObjectName("SidebarScrollContent")
        self._nav_layout = QVBoxLayout(nav_w)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(1)

        def _sec(txt):
            lbl = NavLabel(txt)
            self._nav_layout.addWidget(lbl)
            self._nav_groups.append((lbl, []))

        def _item(icon, label, page_idx):
            it = NavItem(label, icon=icon)
            it.clicked.connect(lambda _checked=False, idx=page_idx: self._switch_page(idx))
            self._nav_layout.addWidget(it)
            self._nav_items.append((it, page_idx))
            self._nav_groups[-1][1].append(it)

        _sec("App Settings")
        _item("mdi.cog-outline",            "General",            _P_GENERAL)
        _item("mdi.gesture-tap-button",     "Action Buttons",     _P_ACTIONS)
        _item("mdi.shield-key-outline",     "SCC Vault Presets",  _P_SCC)
        _item("mdi.radar",                  "Tracker",            _P_TRACKER)

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
        v.addWidget(scroll, 1)
        return panel

    def _build_footer(self):
        btn_close = self._frame.add_action("Close", icon="mdi.close")
        btn_close.clicked.connect(self.reject)
        self._btn_save = self._frame.add_action(
            "Save changes", variant="primary", icon="mdi.check",
            tooltip="Writes every pending change on every page at once.")
        self._btn_save.clicked.connect(self._on_save_settings)

    def _switch_page(self, page_idx: int):
        if page_idx not in self._page_widgets:
            builder = self._page_builders.get(page_idx)
            if builder:
                widget = builder()
                self._page_widgets[page_idx] = widget
                self._stack.addWidget(widget)
                if page_idx in _SETTINGS_PAGES:
                    self._on_page_built()

        target_widget = self._page_widgets.get(page_idx)
        if target_widget:
            self._stack.setCurrentWidget(target_widget)
            self._apply_compact(target_widget)

        for item, idx in self._nav_items:
            item.set_active(idx == page_idx)
        if self._btn_save:
            self._btn_save.setVisible(page_idx in _SETTINGS_PAGES)

    def _filter_nav(self, text: str):
        text = text.strip().lower()
        for item, _ in self._nav_items:
            item.setVisible(not text or text in item.text().lower())
        for label, items in self._nav_groups:
            label.setVisible(any(not it.isHidden() for it in items))

    def _apply_compact(self, page):
        for child in page.findChildren(QWidget):
            if hasattr(child, "set_compact") and callable(child.set_compact):
                child.set_compact(self._compact)

    def _sync_compact(self):
        pane_w = self.width() - TOKENS["w_setnav"] - 1
        if self._compact and pane_w > _PANE_WIDE_ABOVE:
            self._compact = False
        elif not self._compact and pane_w < _PANE_COMPACT_BELOW:
            self._compact = True
        else:
            return
        for page in self._page_widgets.values():
            self._apply_compact(page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_compact()

    # ── Pages ─────────────────────────────────────────────────────────────────
    def _kit_page(self, *blocks) -> QScrollArea:
        """Scrolling settings page: the blocks stacked with kit spacing, no legacy colours."""
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(TOKENS["s_5"], TOKENS["s_3"], TOKENS["s_5"], TOKENS["s_6"])
        lay.setSpacing(TOKENS["s_4"])
        for block in blocks:
            lay.addWidget(block)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_page_general(self) -> QScrollArea:
        header = PageHeader("General",
            lead="Display preferences, masking, automation and start-up behaviour.")

        display = Card("Display")
        self.theme_combo = Select()
        self.theme_combo.addItem("Dark", "light")
        display.add_row(SettingRow("Application theme", "Switch between light and dark themes.",
                                   self.theme_combo))
        self.window_mode_combo = Select()
        self.window_mode_combo.addItem("Fullscreen / Maximized", "fullscreen")
        self.window_mode_combo.addItem("Square Mode (1:1 Aspect)", "square")
        self.window_mode_combo.addItem("Rectangular Mode (950 × 680)", "rectangular")
        display.add_row(SettingRow("Window display mode",
                                   "Controls how the app window opens on launch.", self.window_mode_combo))
        # Per PC, not saved with the other settings (app_settings syncs to every PC); saves on its own buttons.
        from ui.dialogs.display_scale_dialog import DisplayScaleControl
        display.add_row(DisplayScaleControl())

        secure = Card("Passwords and clipboard")
        self.mask_mode_combo = Select()
        self.mask_mode_combo.addItem("Last N characters visible  (e.g. *****1234)", "last_n")
        self.mask_mode_combo.addItem("First N characters visible  (e.g. 1234*****)", "first_n")
        self.mask_mode_combo.addItem("Full dots masking  (●●●●●●●●●●)", "full_dots")
        self._mask_row = SettingRow("Password masking mode", "", self.mask_mode_combo, width="wide")
        secure.add_row(self._mask_row)
        self.reveal_count_spin = Stepper(value=4, minimum=1, maximum=10, step=1)
        secure.add_row(SettingRow("Visible character count",
                                  "Number of characters shown when masking is active.", self.reveal_count_spin))
        self.clipboard_spin = Stepper(value=30, minimum=5, maximum=300, step=5, unit=" s")
        secure.add_row(SettingRow("Auto clipboard clear",
                                  "Automatically wipe the clipboard after this many seconds.", self.clipboard_spin))
        self.quick_copy_check = Switch()
        self.quick_copy_check.setAccessibleName("Enable Quick-Copy")
        secure.add_row(SettingRow("Enable Quick-Copy",
                                  "Master toggle — allows employees to click field values to copy them.",
                                  self.quick_copy_check))

        startup = Card("Start-up")
        self.run_in_bg_check = Switch()
        self.run_in_bg_check.setAccessibleName("Keep app running in background")
        startup.add_row(SettingRow("Keep app running in background",
                                   "Closing the window minimises Sera to the system tray instead of quitting.",
                                   self.run_in_bg_check))
        self.autostart_check = Switch()
        self.autostart_check.setAccessibleName("Launch at Windows startup")
        startup.add_row(SettingRow("Launch at Windows startup",
                                   "Automatically starts Project Sera in the background when Windows boots.",
                                   self.autostart_check))

        assist = Card("Clipboard assist (SCA)")
        self.sca_check = Switch()
        self.sca_check.setAccessibleName("SCA — Sera Clipboard Assist")
        assist.add_row(SettingRow("SCA — Sera Clipboard Assist",
                                  "When a client User ID is copied from a spreadsheet, arms matching portal credentials automatically.",
                                  self.sca_check))
        self.sca_mode_combo = Select()
        self.sca_mode_combo.addItem("Ambient Autofill  (silently fills password on paste)", "autofill")
        self.sca_mode_combo.addItem("SCA Widget  (floating 1-click password prompt on paste)", "widget")
        assist.add_row(SettingRow("SCA action mode",
                                  "Whether SCA silently injects credentials or shows a 1-click floating prompt.",
                                  self.sca_mode_combo, width="wide"))
        self.sca_max_uses_spin = Stepper(value=1, minimum=1, maximum=20, step=1, unit=" uses")
        assist.add_row(SettingRow("SCA uses per copied UID",
                                  "Maximum successful SCA fills allowed after one UID is copied.",
                                  self.sca_max_uses_spin))

        schema = Card("Schema info")
        id_col = self.db.get_id_column()
        if id_col:
            id_ctrl = Pill(f"'{id_col['label']}' — wired to client auto-serial numbers", "emerald")
        else:
            id_ctrl = Pill("Not assigned — set in Master Column List", "neutral")
        schema.add_row(SettingRow("ID / Primary Key column",
                                  "Column used as the client's unique identifier and auto-serial source.",
                                  id_ctrl, width="auto"))

        for combo in (self.theme_combo, self.window_mode_combo, self.mask_mode_combo, self.sca_mode_combo):
            combo.currentIndexChanged.connect(self._on_control_changed)
        for stepper in (self.reveal_count_spin, self.clipboard_spin, self.sca_max_uses_spin):
            stepper.valueChanged.connect(self._on_control_changed)
        for switch in (self.quick_copy_check, self.run_in_bg_check, self.autostart_check, self.sca_check):
            switch.toggled.connect(self._on_control_changed)
        self.mask_mode_combo.currentIndexChanged.connect(self._update_mask_preview)
        self.reveal_count_spin.valueChanged.connect(self._update_mask_preview)
        self._update_mask_preview()

        return self._kit_page(header, display, secure, startup, assist, schema)

    def _update_mask_preview(self, *args):
        masked = mask_password(_MASK_SAMPLE, self.mask_mode_combo.currentData() or "last_n",
                               self.reveal_count_spin.value())
        self._mask_row.set_sub(f"How passwords are obscured in the Client Detail view. Example: {masked}")

    def _build_page_actions(self) -> QScrollArea:
        header = PageHeader("Action Buttons",
            lead="Each service gets one autofill button in Client Detail. Its type (Fast Autofill, "
                 "SMTI Assist or MECP Copy) is chosen per service in Services \u2192 Automation Mode.")

        self.show_hide_check = Switch()
        self.show_hide_check.setAccessibleName("Enable Show / Hide Eye Buttons")
        self.show_hide_check.toggled.connect(self._on_control_changed)
        buttons = Card()
        buttons.add_row(SettingRow("Enable \u2018Show / Hide\u2019 Eye Buttons",
            "Shows a password-reveal toggle next to each credential field in Client Detail.", self.show_hide_check))

        return self._kit_page(header, buttons)

    def _build_page_scc(self) -> QScrollArea:
        header = PageHeader("SCC Vault Presets",
            lead="Configure Sera Credential Capture, the 2 dedicated PAN-based password formulas, and 2 plain fixed passwords.")

        verify = Card()
        self.scc_check = Switch()
        self.scc_check.setChecked(self.db.get_setting("scc_enabled", "1") in ("1", "true", "True"))
        self.scc_check.setAccessibleName("Enable SCC One-Time Verification")
        verify.add_row(SettingRow("Enable SCC One-Time Verification",
            "When logging into a tax portal for an unverified client, show 4 resolved password buttons in the in-browser SMTI widget.",
            self.scc_check))
        self.scc_detect_combo = Select()
        self.scc_detect_combo.addItem("Off", "off")
        self.scc_detect_combo.addItem("On", "on")
        verify.add_row(SettingRow("Detect login automatically",
            "Uses the Income Tax pages SGT already reads to tell whether a copied password worked. It never "
            "changes what SGT captures. If it ever fails or runs slow it switches itself off until the app "
            "restarts. Needs SGT on.", self.scc_detect_combo))

        counts = Card("Counts (local, this session and earlier)")
        tile_grid = QWidget()
        tg = QGridLayout(tile_grid)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setSpacing(TOKENS["s_3"])
        self.scc_count_tiles = []
        for i, (name, meaning) in enumerate((
                ("Attempts", "Attempts opened"), ("Worked", "Login succeeded"),
                ("Failed", "Wrong password"), ("Locked", "Locked out"),
                ("No conclusion", "Other outcome"), ("Asked", "Card asked which row"),
                ("Saved", "Password saved"), ("Not understood", "Portal wording not recognized"))):
            tile = Tile("", name, tooltip=meaning)
            tile.setCheckable(False)
            tile.setFocusPolicy(Qt.NoFocus)
            tile.setCursor(Qt.ArrowCursor)
            tg.addWidget(tile, i // 4, i % 4)
            self.scc_count_tiles.append(tile)
        counts.add_widget(tile_grid)
        self.scc_counts_note = Banner("Counts not available.", tone="w")
        self.scc_counts_note.setVisible(False)
        counts.add_widget(self.scc_counts_note)
        self._update_scc_counts_display()

        def _formula(parts) -> QWidget:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(TOKENS["s_2"])
            h.addWidget(QLabel("Formula:"))
            for text, tone in parts:
                h.addWidget(Tag(text, tone) if tone else QLabel(text))
            h.addStretch(1)
            return row

        def _option(n, title, desc, parts, str_label, str_ph):
            card = Card(title)
            label_edit = Input(placeholder=f"e.g. Combo {n}")
            str_edit = Input(placeholder=str_ph)
            card.add_row(SettingRow(f"Combination Option {n}", desc, _formula(parts), width="auto"))
            grid = QWidget()
            g = QGridLayout(grid)
            g.setContentsMargins(0, 0, 0, 0)
            g.setSpacing(TOKENS["s_2"])
            g.addWidget(FieldLabel(f"Option {n} label", for_widget=label_edit), 0, 0)
            g.addWidget(FieldLabel(str_label, for_widget=str_edit), 0, 1)
            g.addWidget(label_edit, 1, 0)
            g.addWidget(str_edit, 1, 1)
            card.add_widget(grid)
            return card, label_edit, str_edit

        opt1_card, self.scc_opt1_label_edit, self.scc_opt1_str_edit = _option(1,
            "Option 1: [4 Letters] + [Fixed String] + [4 Digits]",
            "Generates password with the first 4 lowercase letters of the client's PAN, your fixed string, and the 4 PAN digits.",
            (("pan_letters (4 smallcase)", "neutral"), ("+", None), ("fixed string", "emerald"),
             ("+", None), ("pan_digits (4)", "neutral")),
            "Fixed string", "e.g. @ or #")
        opt2_card, self.scc_opt2_label_edit, self.scc_opt2_str_edit = _option(2,
            "Option 2: [Fixed String] + [4 Digits]",
            "Generates password starting with your fixed string followed directly by the 4 digits of the client's PAN.",
            (("fixed string", "emerald"), ("+", None), ("pan_digits (4)", "neutral")),
            "Fixed string", "e.g. Pass# or Tax@")
        opt3_card, self.scc_opt3_label_edit, self.scc_opt3_str_edit = _option(3,
            "Option 3: Plain Fixed String",
            "A static, firm-wide fixed password injected as-is without any PAN components.",
            (("fixed string", "emerald"), ("(No PAN substitution)", None)),
            "Plain fixed password", "e.g. FirmMaster@2026")
        opt4_card, self.scc_opt4_label_edit, self.scc_opt4_str_edit = _option(4,
            "Option 4: Plain Fixed String",
            "A second static, firm-wide fixed password injected as-is without any PAN components.",
            (("fixed string", "emerald"), ("(No PAN substitution)", None)),
            "Plain fixed password", "e.g. BackupPass#1")

        preview = Card("Live combination preview")
        self.scc_sample_pan_edit = Input()
        self.scc_sample_pan_edit.setText("ABCDE1234F")
        preview.add_row(SettingRow("Test with PAN",
            "Shows exactly how the 4 combination buttons will appear in the in-browser SMTI widget for the test PAN.",
            self.scc_sample_pan_edit))
        lines = QWidget()
        lv = QVBoxLayout(lines)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(TOKENS["s_1"])
        self.scc_preview_lbl1 = Tag("", "emerald")
        self.scc_preview_lbl2 = Tag("", "emerald")
        self.scc_preview_lbl3 = Tag("", "emerald")
        self.scc_preview_lbl4 = Tag("", "emerald")
        for tag in (self.scc_preview_lbl1, self.scc_preview_lbl2, self.scc_preview_lbl3, self.scc_preview_lbl4):
            lv.addWidget(tag, alignment=Qt.AlignLeft)
        preview.add_widget(lines)

        def _update_previews():
            pan = self.scc_sample_pan_edit.text().strip().upper() or "ABCDE1234F"
            import re
            letters_m = re.search(r"^[A-Z]{4}", pan)
            letters = letters_m.group(0).lower() if letters_m else pan[:4].lower()
            digits_m = re.search(r"\d{4}", pan)
            digits = digits_m.group(0) if digits_m else (pan[5:9] if len(pan) >= 9 else "")

            lbl1 = self.scc_opt1_label_edit.text().strip() or "Combo 1"
            str1 = self.scc_opt1_str_edit.text()
            val1 = f"{letters}{str1}{digits}"

            lbl2 = self.scc_opt2_label_edit.text().strip() or "Combo 2"
            str2 = self.scc_opt2_str_edit.text()
            val2 = f"{str2}{digits}"

            lbl3 = self.scc_opt3_label_edit.text().strip() or "Combo 3"
            val3 = self.scc_opt3_str_edit.text()

            lbl4 = self.scc_opt4_label_edit.text().strip() or "Combo 4"
            val4 = self.scc_opt4_str_edit.text()

            self.scc_preview_lbl1.setText(f"  • {lbl1}:  🔑  {val1}")
            self.scc_preview_lbl2.setText(f"  • {lbl2}:  🔑  {val2}")
            self.scc_preview_lbl3.setText(f"  • {lbl3}:  🔑  {val3 if val3 else '(disabled / blank)'}")
            self.scc_preview_lbl4.setText(f"  • {lbl4}:  🔑  {val4 if val4 else '(disabled / blank)'}")

        self.scc_sample_pan_edit.textChanged.connect(_update_previews)
        self.scc_opt1_label_edit.textChanged.connect(_update_previews)
        self.scc_opt1_str_edit.textChanged.connect(_update_previews)
        self.scc_opt2_label_edit.textChanged.connect(_update_previews)
        self.scc_opt2_str_edit.textChanged.connect(_update_previews)
        self.scc_opt3_label_edit.textChanged.connect(_update_previews)
        self.scc_opt3_str_edit.textChanged.connect(_update_previews)
        self.scc_opt4_label_edit.textChanged.connect(_update_previews)
        self.scc_opt4_str_edit.textChanged.connect(_update_previews)

        self.scc_check.toggled.connect(self._on_control_changed)
        self.scc_detect_combo.currentIndexChanged.connect(self._on_control_changed)
        self.scc_opt1_label_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt1_str_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt2_label_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt2_str_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt3_label_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt3_str_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt4_label_edit.textChanged.connect(self._on_control_changed)
        self.scc_opt4_str_edit.textChanged.connect(self._on_control_changed)

        _update_previews()

        return self._kit_page(header, verify, counts, opt1_card, opt2_card, opt3_card, opt4_card, preview)

    def _build_page_mcl(self) -> QWidget:
        header = PageHeader("Master Column List",
            lead="The schema for every client field; changes apply at once. "
                 "Shows on ticks (M · Q · A) wait for Save.")
        self._mcl_btn_add = LabelledAction("Add column", variant="primary", icon="mdi.plus",
                                           tooltip="Add a column to the Master Column List")
        header.add_action(self._mcl_btn_add)

        self._mcl_sel_tag = Tag("Selected: —")
        self._mcl_btn_edit = LabelledAction("Edit", icon="mdi.pencil-outline",
                                            tooltip="Edit the selected column")
        self._mcl_btn_up = LabelledAction("Move up", icon="mdi.arrow-up",
                                          tooltip="Move the selected column up (Alt+↑)")
        self._mcl_btn_dn = LabelledAction("Move down", icon="mdi.arrow-down",
                                          tooltip="Move the selected column down (Alt+↓)")
        self._mcl_btn_del = LabelledAction("Delete column…", variant="danger", icon="mdi.delete-outline",
                                           tooltip="Asks first: all client values in this column are removed")
        bar = QWidget()
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(TOKENS["s_2"])
        tb.addWidget(self._mcl_sel_tag)
        tb.addStretch(1)
        for btn in (self._mcl_btn_edit, self._mcl_btn_up, self._mcl_btn_dn):
            tb.addWidget(btn)
        tb.addSpacing(TOKENS["s_5"])                       # Delete is set apart from the safe actions
        tb.addWidget(self._mcl_btn_del)

        self._mcl_list = DarkListTable(0, len(_MCL_HEADS))
        self._mcl_list.setHorizontalHeaderLabels(_MCL_HEADS)
        for col, (_field, tip) in _MCL_FLAG_COLS.items():
            self._mcl_list.horizontalHeaderItem(col).setToolTip(tip)
        self._mcl_list.horizontalHeaderItem(_MCL_COL_EDIT).setToolTip("Edit")
        self._mcl_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._mcl_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._mcl_list.set_column(0, kind="strong", minimum=140)
        self._mcl_list.set_column(1, width=110)
        self._mcl_list.set_column(2, minimum=120, max_share=0.4, priority=1)
        for col in _MCL_FLAG_COLS:
            self._mcl_list.set_column(col, width=44)
        self._mcl_list.set_column(_MCL_COL_EDIT, width=52)
        table_card = Card()
        table_card.add_widget(self._mcl_list, 1)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(TOKENS["s_5"], TOKENS["s_3"], TOKENS["s_5"], TOKENS["s_5"])
        lay.setSpacing(TOKENS["s_3"])
        lay.addWidget(header)
        lay.addWidget(bar)
        lay.addWidget(table_card, 1)

        self._mcl_btn_add.clicked.connect(self._mcl_on_add)
        self._mcl_btn_edit.clicked.connect(self._mcl_on_edit)
        self._mcl_btn_del.clicked.connect(self._mcl_on_delete)
        self._mcl_btn_up.clicked.connect(lambda: self._mcl_on_move(-1))
        self._mcl_btn_dn.clicked.connect(lambda: self._mcl_on_move(1))
        self._mcl_list.currentCellChanged.connect(lambda row, *_: self._mcl_on_sel_changed(row))
        self._mcl_list.itemChanged.connect(self._mcl_on_item_changed)
        self._mcl_list.actionTriggered.connect(self._mcl_on_cell_action)
        for key, step in ((Qt.Key_Up, -1), (Qt.Key_Down, 1)):
            sc = QShortcut(QKeySequence(Qt.ALT | key), self._mcl_list)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda step=step: self._mcl_on_move(step))

        self._mcl_reload()
        return page

    def _build_page_services(self) -> QWidget:
        header = PageHeader("Services",
            lead="Define compliance portals and automation services. Changes apply at once.")
        self._svc_btn_add = LabelledAction("Add service", variant="primary", icon="mdi.plus",
                                           tooltip="Add a compliance service")
        header.add_action(self._svc_btn_add)

        self._svc_btn_edit = LabelledAction("Edit", icon="mdi.pencil-outline",
                                            tooltip="Edit the selected service")
        self._svc_btn_del = LabelledAction("Delete service…", variant="danger", icon="mdi.delete-outline",
                                           tooltip="Asks first: the service is detached from all client profiles")
        bar = QWidget()
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(TOKENS["s_2"])
        tb.addWidget(self._svc_btn_edit)
        tb.addStretch(1)
        tb.addWidget(self._svc_btn_del)

        self._svc_list = DarkListTable(0, len(_SVC_HEADS))
        self._svc_list.setHorizontalHeaderLabels(_SVC_HEADS)
        self._svc_list.horizontalHeaderItem(_SVC_COL_EDIT).setToolTip("Edit")
        self._svc_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._svc_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._svc_list.set_column(0, kind="strong", minimum=120)
        self._svc_list.set_column(1, minimum=130)
        self._svc_list.set_column(2, minimum=110, priority=2)
        self._svc_list.set_column(3, kind="mono", minimum=160, priority=1)
        self._svc_list.set_column(_SVC_COL_EDIT, width=52)
        table_card = Card()
        table_card.add_widget(self._svc_list, 1)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(TOKENS["s_5"], TOKENS["s_3"], TOKENS["s_5"], TOKENS["s_5"])
        lay.setSpacing(TOKENS["s_3"])
        lay.addWidget(header)
        lay.addWidget(bar)
        lay.addWidget(table_card, 1)

        self._svc_btn_add.clicked.connect(self._svc_on_add)
        self._svc_btn_edit.clicked.connect(self._svc_on_edit)
        self._svc_btn_del.clicked.connect(self._svc_on_delete)
        self._svc_list.currentCellChanged.connect(lambda row, *_: self._svc_on_sel_changed(row))
        self._svc_list.actionTriggered.connect(self._svc_on_cell_action)

        self._svc_reload()
        return page

    def _build_page_visibility(self, title: str, subtitle: str,
                                field: str, cbs: dict) -> QScrollArea:
        # A view of the shared column model (self.mcl_columns): built from it, so a page opened after
        # a Shows-on tick on the Columns page shows the ticked value, and every switch writes to it.
        header = PageHeader(title, lead=subtitle)
        find = Input(placeholder="Find a column", icon="mdi.magnify")
        find.setAccessibleName("Find a column")
        find.setMinimumWidth(220)
        header.add_action(find)
        rows = {}
        card = Card()
        for col in self.mcl_columns:
            ftype = col.get("field_type", "text")
            sw = Switch(tooltip=f"{title}: {col['label']}")
            sw.setChecked(_flag(col, field))
            sw.toggled.connect(lambda on, cid=col["id"]: self._set_flag(cid, field, on))
            cbs[col["id"]] = sw
            row = SettingRow(col["label"], f"Field type: {ftype}",
                             [Tag(ftype, "red" if ftype == "password" else "neutral"), sw], width="auto")
            rows[col["id"]] = row
            card.add_row(row)
        if not rows:
            card.add_row(EmptyState("No columns yet", "Add columns on the Master Column List page."))
        find.textChanged.connect(lambda text: [
            row.setVisible(not text.strip() or text.strip().lower() in row.title_label.text().lower())
            for row in rows.values()])
        self._vis_headers[field] = header
        self._paint_vis_count(field)
        return self._kit_page(header, card)

    def _paint_vis_count(self, field: str):
        header = self._vis_headers.get(field)
        if header is not None:
            on = sum(1 for col in self.mcl_columns if _flag(col, field))
            header.set_count(f"{on} of {len(self.mcl_columns)} shown")


    def _build_page_tracker(self) -> QScrollArea:
        header = PageHeader("Tracker",
            lead="SGT (Sera Global Tracker) is the capture engine. VSDC, VSDC-X and VSDC 24/7 are the older "
                 "engines; they stay off unless you need one beside SGT.")

        sgt = Card("SGT - Sera Global Tracker", edge="em")
        self.sgt_mode_combo = Select()
        self.sgt_mode_combo.addItem("Live (main capture engine)", "live")
        self.sgt_mode_combo.addItem("Shadow (separate rows, for comparison)", "shadow")
        self.sgt_mode_combo.addItem("Off", "off")
        sgt.add_row(SettingRow("SGT mode",
            "Reads every portal page and works out every datapoint from its field list, without crosshairs. "
            "Live saves its captures as the tracker's rows (tagged SGT, orange): a filing another engine "
            "already saved is updated in place, and its status never moves down. A submission whose client "
            "was never identified is saved unassigned and raises the phone alert. Shadow keeps SGT's rows "
            "separate from the other engines' instead, for comparison. Either way the HUD pill shows its "
            "captures and a detailed log stays on this PC (sgt_shadow folder).", self.sgt_mode_combo))
        self.sgt_record_check = Switch()
        self.sgt_record_check.setAccessibleName("Record pages for SGT testing")
        sgt.add_row(SettingRow("Record pages for SGT testing",
            "While SGT is on, keeps the text of each portal page it reads on this PC (sgt_corpus folder, "
            "deleted after 30 days) so a change to SGT's field list can be replayed against real pages "
            "before it is used. The pages contain client details; they never leave this PC.",
            self.sgt_record_check))
        self.sdis_record_check = Switch()
        self.sdis_record_check.setAccessibleName("Record pages for Sera Distill (SDIS)")
        sgt.add_row(SettingRow("Record pages for Sera Distill (SDIS)",
            "While SGT is on, also keeps the full structure of each changed portal page on this PC "
            "(sdis_capture folder, at most 500 MB - the oldest go first) so Sera Distill can find the "
            "datapoints of a page. Read after SGT's own read and dropped if it takes over 1.5 s, so SGT "
            "captures exactly as before. The pages contain client details; they leave this PC only for "
            "the admin PC.", self.sdis_record_check))
        self.sgt_i_combo = Select()
        self.sgt_i_combo.addItem("Off", "off")
        self.sgt_i_combo.addItem("On", "on")
        sgt.add_row(SettingRow("SGT-I",
            "SGT's Intelligence half: watches the pages SGT reads, on its own, and learns how each portal "
            "is built. It never changes what SGT captures - it may only add its own notes to a row and ask "
            "SGT to read a page again. If it ever fails or runs slow it switches itself off until the app "
            "restarts. Off = SGT exactly as before.", self.sgt_i_combo))
        lab_btn = LabelledAction("Open SGT lab →", variant="secondary", icon="mdi.flask-outline")
        lab_btn.clicked.connect(self._on_open_sgt_lab)
        sgt.add_row(SettingRow("SGT lab",
            "New datapoints SGT-I found on portals, drawn only from counting and maths (no AI). "
            "Accept writes one to this PC's local override file; Reject only remembers your choice.",
            lab_btn))
        self.vsdc_hud_check = Switch()
        self.vsdc_hud_check.setAccessibleName("Show HUD pill")
        sgt.add_row(SettingRow("Show HUD pill",
            "The small bottom-left pop-up that appears when a page, client or submission is captured. Turning it off only hides the pill - capturing and saving carry on as normal.",
            self.vsdc_hud_check))

        older = Card("Older engines")
        self.vsdc_check = Switch()
        self.vsdc_check.setAccessibleName("Enable VSDC")
        older.add_row(SettingRow("Enable VSDC",
            "Extracts structured data from client-facing compliance portals visually using DOM coordinates.", self.vsdc_check))
        self.vsdc_x_check = Switch()
        self.vsdc_x_check.setAccessibleName("Enable VSDC-X")
        older.add_row(SettingRow("Enable VSDC-X",
            "Advanced fallback capture method that navigates modal overlays and complex popups using UI Automation exact-text reading.", self.vsdc_x_check))
        self.vsdc247_check = Switch()
        self.vsdc247_check.setAccessibleName("Enable VSDC 24/7")
        older.add_row(SettingRow("Enable VSDC 24/7",
            "Always-on background optical screen harvester. Periodically takes screenshots of designated web portals and runs OCR to detect compliance receipts.", self.vsdc247_check))

        self.vsdc_check.toggled.connect(self._on_control_changed)
        self.vsdc_x_check.toggled.connect(self._on_control_changed)
        self.vsdc247_check.toggled.connect(self._on_control_changed)
        self.vsdc_hud_check.toggled.connect(self._on_control_changed)
        self.sgt_mode_combo.currentIndexChanged.connect(self._on_control_changed)
        self.sgt_record_check.toggled.connect(self._on_control_changed)
        self.sdis_record_check.toggled.connect(self._on_control_changed)
        self.sgt_i_combo.currentIndexChanged.connect(self._on_control_changed)

        return self._kit_page(header, sgt, older)

    def _build_page_export(self) -> QScrollArea:
        header = PageHeader("Export CSV", lead="Download client data or a blank import template.")

        export_btn = LabelledAction("Export All Clients \u2192", variant="soft", icon="mdi.download")
        export_btn.clicked.connect(self._on_export_csv)
        tmpl_btn = LabelledAction("Download Template \u2192", icon="mdi.file-download-outline")
        tmpl_btn.clicked.connect(self._on_download_template)
        data = Card()
        data.add_row(SettingRow("Export to CSV",
            "Download all active client records as a CSV spreadsheet file.", export_btn))
        data.add_row(SettingRow("Download Import Template",
            "Get a blank CSV template matching your current Master Column List schema.", tmpl_btn))

        return self._kit_page(header, data)

    def _build_page_backup(self) -> QScrollArea:
        header = PageHeader("Backup & Restore",
            lead="Protect your data with regular backups or roll back to a previous snapshot.")

        backup_btn = LabelledAction("Choose Backup Folder \u2192", variant="soft", icon="mdi.database-arrow-up")
        backup_btn.clicked.connect(self._on_backup)
        backup = Card()
        backup.add_row(SettingRow("Backup Database",
            "Copy the current encrypted database to a folder of your choice.", backup_btn))
        blocks = [header, backup]

        import sera_keys
        from pathlib import Path
        db_path = getattr(self.db, "db_path", None)
        app_dir = Path(db_path).parent if db_path else None
        try:
            office_info = sera_keys.load_office(app_dir) if app_dir else None
        except sera_keys.SeraKeysError:
            office_info = None

        if office_info is not None:
            export_kit_btn = LabelledAction("Export Recovery Kit \u2192", icon="mdi.file-key-outline")
            export_kit_btn.clicked.connect(lambda: self._on_export_recovery_kit(app_dir))
            change_pw_btn = LabelledAction("Change Password \u2192", icon="mdi.lock-reset")
            change_pw_btn.clicked.connect(lambda: self._on_change_master_password(app_dir))
            office = Card("Office Recovery Kit & Security")
            office.add_row(SettingRow("Export Recovery Kit (.serakit)",
                "Export encrypted office credentials to a USB drive for disaster recovery.", export_kit_btn))
            office.add_row(SettingRow("Change Office Master Password",
                "Update the master password used to recover office credentials without modifying client data.",
                change_pw_btn))
            blocks.append(office)

        restore_btn = LabelledAction("Restore from Backup \u2192", variant="danger", icon="mdi.database-arrow-down")
        restore_btn.clicked.connect(self._on_restore_backup)
        danger = Card("Danger zone", edge="danger")
        danger.add_row(SettingRow("Restore Database",
            "Make a previous backup the data again (in an office: on every PC). The app will restart. Use with caution.",
            restore_btn, danger=True))
        blocks.append(danger)

        return self._kit_page(*blocks)

    def _build_page_purge(self) -> QScrollArea:
        header = PageHeader("Purge Duplicates",
            lead="Permanently remove duplicate client records that share identical identity values.")

        purge_btn = LabelledAction("Run Deduplication \u2192", variant="danger", icon="mdi.delete-sweep-outline")
        purge_btn.clicked.connect(self._on_purge_duplicates)
        danger = Card("Danger zone", edge="danger")
        danger.add_row(SettingRow("Purge Duplicate Clients",
            "Scans all non-archived clients and deletes duplicates sharing the same identity values. "
            "The oldest record (lowest ID) is kept per group. This cannot be undone.",
            purge_btn, danger=True))

        return self._kit_page(header, danger)

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
            state["sca"] = self.sca_check.isChecked()
            state["sca_mode"] = self.sca_mode_combo.currentData()
            state["sca_max_uses"] = self.sca_max_uses_spin.value()
        if hasattr(self, "show_hide_check"):
            state["show_hide"] = self.show_hide_check.isChecked()
        if hasattr(self, "scc_check"):
            state["scc_enabled"] = self.scc_check.isChecked()
            state["scc_detect_mode"] = self.scc_detect_combo.currentData()
            state["scc_opt1_label"] = getattr(self, "scc_opt1_label_edit", QLineEdit()).text()
            state["scc_opt1_str"] = getattr(self, "scc_opt1_str_edit", QLineEdit()).text()
            state["scc_opt2_label"] = getattr(self, "scc_opt2_label_edit", QLineEdit()).text()
            state["scc_opt2_str"] = getattr(self, "scc_opt2_str_edit", QLineEdit()).text()
            state["scc_opt3_label"] = getattr(self, "scc_opt3_label_edit", QLineEdit()).text()
            state["scc_opt3_str"] = getattr(self, "scc_opt3_str_edit", QLineEdit()).text()
            state["scc_opt4_label"] = getattr(self, "scc_opt4_label_edit", QLineEdit()).text()
            state["scc_opt4_str"] = getattr(self, "scc_opt4_str_edit", QLineEdit()).text()

        if hasattr(self, "vsdc_check"):
            state["vsdc_enabled"] = self.vsdc_check.isChecked()
            state["vsdc_x_enabled"] = self.vsdc_x_check.isChecked()
            state["vsdc247_enabled"] = self.vsdc247_check.isChecked()
            state["vsdc_hud_enabled"] = self.vsdc_hud_check.isChecked()
            state["sgt_mode"] = self.sgt_mode_combo.currentData()
            state["sgt_record_pages"] = self.sgt_record_check.isChecked()
            state["sdis_record"] = self.sdis_record_check.isChecked()
            state["sgt_i_mode"] = self.sgt_i_combo.currentData()
        for key, field, _default in _FLAGS:
            state[key] = {col["id"]: _flag(col, field) for col in self.mcl_columns}
        return state

    def _flags_dirty(self, key: str) -> bool:
        base = (self._initial_state or {}).get(key, {})
        return any(base.get(cid) != on for cid, on in self._capture_state()[key].items())

    def _set_flag(self, cid, field: str, on: bool):
        """A tick in any view (Columns table or a visibility page): stage it in the model, repaint."""
        if self._painting:
            return
        col = next((c for c in self.mcl_columns if c["id"] == cid), None)
        if col is None:
            return
        col[field] = 1 if on else 0
        self._paint_flags()
        self._on_control_changed()

    def _paint_flags(self):
        """Every view of the column model shows the model's flags (switches, table ticks, counts)."""
        self._painting = True
        try:
            model = {col["id"]: col for col in self.mcl_columns}
            for (_key, field, _default), boxes in zip(_FLAGS, (self.vis_cbs, self.qc_cbs, self.admin_vis_cbs)):
                for cid, box in boxes.items():
                    if cid in model:
                        box.setChecked(_flag(model[cid], field))
                self._paint_vis_count(field)
            table = getattr(self, "_mcl_list", None)
            if table is not None:
                for row in range(table.rowCount()):
                    col = model.get(table.item(row, 0).data(Qt.UserRole))
                    for c, (field, _tip) in _MCL_FLAG_COLS.items():
                        if col is not None:
                            table.item(row, c).setCheckState(Qt.Checked if _flag(col, field) else Qt.Unchecked)
        finally:
            self._painting = False

    def _on_control_changed(self, *args):
        if self._restoring:
            return
        self._update_save_btn_state()

    def _dirty_count(self) -> int:
        if self._initial_state is None:
            return 0
        now = self._capture_state()
        count = 0
        for key, value in now.items():
            base = self._initial_state.get(key)
            if isinstance(value, dict):
                base = base or {}
                count += sum(1 for cid, checked in value.items() if base.get(cid) != checked)
            elif base != value:
                count += 1
        return count

    def _update_save_btn_state(self):
        count = self._dirty_count()
        if self._btn_save is not None:
            self._btn_save.setEnabled(count > 0)
        if self._unsaved is not None:
            self._unsaved.set_count(count)

    def _apply_state(self, state: dict):
        """Put every built control back to `state` (the baseline) without counting it as an edit."""
        self._restoring = True
        try:
            for key, name in _STATE_COMBOS.items():
                combo = getattr(self, name, None)
                if combo is not None and key in state:
                    idx = combo.findData(state[key])
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
            for key, name in _STATE_STEPPERS.items():
                stepper = getattr(self, name, None)
                if stepper is not None and key in state:
                    stepper.setValue(state[key])
            for key, name in _STATE_CHECKS.items():
                check = getattr(self, name, None)
                if check is not None and key in state:
                    check.setChecked(state[key])
            for key, name in _STATE_EDITS.items():
                edit = getattr(self, name, None)
                if edit is not None and key in state:
                    edit.setText(state[key])
            for key, field, _default in _FLAGS:
                saved = state.get(key, {})
                for col in self.mcl_columns:
                    if col["id"] in saved:
                        col[field] = 1 if saved[col["id"]] else 0
            self._paint_flags()
        finally:
            self._restoring = False

    def _on_discard(self):
        if self._initial_state is not None:
            self._apply_state(self._initial_state)
        self._update_save_btn_state()

    def reject(self):
        if self._dirty_count() == 0:
            super().reject()
            return
        clicked = MessageBox.choose(
            self, "Unsaved changes", f"You have {self._dirty_count()} unsaved change(s) in Settings.",
            [("Discard changes", "secondary"), ("Keep editing", "secondary"), ("Save changes", "primary")],
            level="warning")
        if clicked == 2:
            self._on_save_settings()
            if self._dirty_count() == 0:
                super().reject()
        elif clicked == 0:
            super().reject()

    def _update_scc_counts_display(self):
        """Update the SCC count tiles."""
        try:
            from core.scc.counts import load_counts
            c = load_counts()
            values = (c.attempts, c.worked, c.failed, c.locked, c.no_conclusion, c.asked, c.saved, c.not_understood)
            self.scc_counts_note.setVisible(False)
        except Exception:
            values = ("–",) * 8
            self.scc_counts_note.setVisible(True)
        for tile, value in zip(self.scc_count_tiles, values):
            tile.set_value(value)

    # ── Load settings into controls ───────────────────────────────────────────
    def _pending(self, group: str, sentinel: str, force: bool) -> bool:
        """True when `group` of controls exists (its page is built) and still needs its values."""
        if not hasattr(self, sentinel):
            return False
        if group in self._loaded_groups and not force:
            return False
        self._loaded_groups.add(group)
        return True

    def _load_settings(self, force: bool = False):
        # one database connection for all ~30 reads (each get_setting opened its own, ~8 ms)
        snapshot = self.db.get_all_settings()
        g = lambda key, default=None: snapshot.get(key, default)
        def _set(combo: QComboBox, val: str):
            idx = combo.findData(val)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        if self._pending("general", "theme_combo", force):
            _set(self.theme_combo,       g("theme", "light"))
            _set(self.window_mode_combo, g("window_mode", "fullscreen"))
            _set(self.mask_mode_combo,   g("mask_mode", "last_n"))

            self.reveal_count_spin.setValue(int(g("mask_reveal_count", "4")))
            self.clipboard_spin.setValue(int(g("clipboard_clear_seconds", "30")))
            self.quick_copy_check.setChecked(g("quick_copy_enabled", "0") == "1")
            self.run_in_bg_check.setChecked(g("run_in_background", "1") == "1")
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

        if self._pending("scc", "scc_check", force):
            self.scc_check.setChecked(g("scc_enabled", "1") == "1")
            _set(self.scc_detect_combo, g("scc_detect_mode", "off"))
            if hasattr(self, "scc_opt1_label_edit"):
                self.scc_opt1_label_edit.setText(g("scc_opt1_label", "Combo 1"))
            if hasattr(self, "scc_opt1_str_edit"):
                self.scc_opt1_str_edit.setText(g("scc_opt1_fixed_str", "@"))
            if hasattr(self, "scc_opt2_label_edit"):
                self.scc_opt2_label_edit.setText(g("scc_opt2_label", "Combo 2"))
            if hasattr(self, "scc_opt2_str_edit"):
                self.scc_opt2_str_edit.setText(g("scc_opt2_fixed_str", ""))
            if hasattr(self, "scc_opt3_label_edit"):
                self.scc_opt3_label_edit.setText(g("scc_opt3_label", "Combo 3"))
            if hasattr(self, "scc_opt3_str_edit"):
                self.scc_opt3_str_edit.setText(g("scc_opt3_fixed_str", ""))
            if hasattr(self, "scc_opt4_label_edit"):
                self.scc_opt4_label_edit.setText(g("scc_opt4_label", "Combo 4"))
            if hasattr(self, "scc_opt4_str_edit"):
                self.scc_opt4_str_edit.setText(g("scc_opt4_fixed_str", ""))

        if self._pending("actions", "show_hide_check", force):
            self.show_hide_check.setChecked(g("show_hide_btn_enabled", "1") == "1")


        if self._pending("tracker", "vsdc_check", force):
            # Same defaults as core/vsdc/vsdc_engines.py, so the page shows what runs.
            self.vsdc_check.setChecked(g("vsdc_enabled", "0") == "1")
            self.vsdc_x_check.setChecked(g("vsdc_x_enabled", "0") == "1")
            self.vsdc247_check.setChecked(g("vsdc247_enabled", "0") == "1")
            self.vsdc_hud_check.setChecked(g("vsdc_hud_enabled", "1") == "1")
            _set(self.sgt_mode_combo, g("sgt_mode", "live"))
            self.sgt_record_check.setChecked(g("sgt_record_pages", "1") == "1")
            self.sdis_record_check.setChecked(g("sdis_record", "1") == "1")
            _set(self.sgt_i_combo, g("sgt_i_mode", "off"))

    #── Save settings ─────────────────────────────────────────────────────────
    def _on_save_settings(self):
        try:
            bulk_settings = {}
            b = lambda cb: "1" if cb.isChecked() else "0"

            if hasattr(self, "theme_combo"):
                bulk_settings["theme"]                      = self.theme_combo.currentData()
                bulk_settings["window_mode"]                = self.window_mode_combo.currentData()
                bulk_settings["mask_mode"]                  = self.mask_mode_combo.currentData()
                bulk_settings["mask_reveal_count"]          = str(self.reveal_count_spin.value())
                bulk_settings["clipboard_clear_seconds"]    = str(self.clipboard_spin.value())
                bulk_settings["quick_copy_enabled"]         = b(self.quick_copy_check)
                bulk_settings["run_in_background"]          = b(self.run_in_bg_check)
                bulk_settings["sca_enabled"]                = b(self.sca_check)
                bulk_settings["sca_action_mode"]            = self.sca_mode_combo.currentData() or "autofill"
                bulk_settings["sca_max_uses"]               = str(self.sca_max_uses_spin.value())

                try:
                    from automation import update_extension_settings
                    update_extension_settings(
                        sca_enabled=self.sca_check.isChecked(),
                        sca_mode=self.sca_mode_combo.currentData() or "autofill",
                        sca_max_uses=self.sca_max_uses_spin.value(),
                        allowed_services=self.db.get_services(),
                        clipboard_clear_seconds=self.clipboard_spin.value(),
                    )
                except Exception:
                    pass

                try:
                    from ui.utils import autostart
                    autostart.set_autostart_enabled(self.autostart_check.isChecked())
                except Exception:
                    pass

            if hasattr(self, "scc_check"):
                bulk_settings["scc_enabled"] = b(self.scc_check)
                bulk_settings["scc_detect_mode"] = self.scc_detect_combo.currentData() or "off"
                if hasattr(self, "scc_opt1_label_edit"):
                    bulk_settings["scc_opt1_label"] = self.scc_opt1_label_edit.text().strip() or "Combo 1"
                if hasattr(self, "scc_opt1_str_edit"):
                    bulk_settings["scc_opt1_fixed_str"] = self.scc_opt1_str_edit.text()
                if hasattr(self, "scc_opt2_label_edit"):
                    bulk_settings["scc_opt2_label"] = self.scc_opt2_label_edit.text().strip() or "Combo 2"
                if hasattr(self, "scc_opt2_str_edit"):
                    bulk_settings["scc_opt2_fixed_str"] = self.scc_opt2_str_edit.text()
                if hasattr(self, "scc_opt3_label_edit"):
                    bulk_settings["scc_opt3_label"] = self.scc_opt3_label_edit.text().strip() or "Combo 3"
                if hasattr(self, "scc_opt3_str_edit"):
                    bulk_settings["scc_opt3_fixed_str"] = self.scc_opt3_str_edit.text()
                if hasattr(self, "scc_opt4_label_edit"):
                    bulk_settings["scc_opt4_label"] = self.scc_opt4_label_edit.text().strip() or "Combo 4"
                if hasattr(self, "scc_opt4_str_edit"):
                    bulk_settings["scc_opt4_fixed_str"] = self.scc_opt4_str_edit.text()

            if hasattr(self, "show_hide_check"):
                bulk_settings["show_hide_btn_enabled"]      = b(self.show_hide_check)


            if hasattr(self, "vsdc_check"):
                bulk_settings["vsdc_enabled"] = b(self.vsdc_check)
                bulk_settings["vsdc_x_enabled"] = b(self.vsdc_x_check)
                bulk_settings["vsdc247_enabled"] = b(self.vsdc247_check)
                bulk_settings["vsdc_hud_enabled"] = b(self.vsdc_hud_check)
                bulk_settings["sgt_mode"] = self.sgt_mode_combo.currentData() or "live"
                bulk_settings["sgt_record_pages"] = b(self.sgt_record_check)
                bulk_settings["sdis_record"] = b(self.sdis_record_check)
                bulk_settings["sgt_i_mode"] = self.sgt_i_combo.currentData() or "off"

            if hasattr(self.db, "set_settings_bulk"):
                self.db.set_settings_bulk(bulk_settings)
            else:
                for k, v in bulk_settings.items():
                    self.db.set_setting(k, v)

            # A flag set is written when its page was opened (as before) or a Shows-on tick changed it.
            for (key, field, _default), boxes, write in zip(
                    _FLAGS, (self.vis_cbs, self.qc_cbs, self.admin_vis_cbs),
                    (self.db.bulk_update_mcl_visibility, self.db.bulk_update_mcl_quick_copy,
                     self.db.bulk_update_mcl_admin_visibility)):
                if boxes or self._flags_dirty(key):
                    write([col["id"] for col in self.mcl_columns if _flag(col, field)])

            self.db.log_action(self.actor, "update_settings",
                               detail="Saved via Unified Settings Hub")
            self.toast_requested.emit("Settings saved successfully!", 3000)
            self.settings_saved.emit()
            self._initial_state = self._capture_state()
            self._update_save_btn_state()

        except Exception as e:
            MessageBox.critical(self, "Error Saving Settings", str(e))

    # ── MCL actions ───────────────────────────────────────────────────────────
    def _mcl_refresh_model(self):
        """Take the columns from the database (add / edit / delete / move apply at once) and keep the
        Shows-on flags staged so far. A built visibility page that no longer matches is dropped and
        rebuilt from the model on its next visit."""
        staged = {c["id"]: c for c in self.mcl_columns}
        fresh = self.db.get_mcl_columns()
        for col in fresh:
            old = staged.get(col["id"])
            for key, field, _default in _FLAGS:
                if old is not None and field in old:
                    col[field] = old[field]
                elif self._initial_state is not None:     # a new column: its loaded flags are the baseline
                    self._initial_state.setdefault(key, {})[col["id"]] = _flag(col, field)
        if self._initial_state is not None:
            ids = {col["id"] for col in fresh}
            for key, _field, _default in _FLAGS:
                base = self._initial_state.get(key, {})
                for cid in [cid for cid in base if cid not in ids]:
                    del base[cid]
        shape = lambda cols: [(c["id"], c["label"], c.get("field_type")) for c in cols]
        changed = shape(fresh) != shape(self.mcl_columns)
        self.mcl_columns = fresh
        if changed:
            for page_idx, boxes in ((_P_MAIN_VIS, self.vis_cbs), (_P_QC, self.qc_cbs),
                                    (_P_ADMIN_VIS, self.admin_vis_cbs)):
                page = self._page_widgets.pop(page_idx, None)
                if page is not None:
                    self._stack.removeWidget(page)
                    page.deleteLater()
                boxes.clear()

    def _mcl_reload(self):
        self._mcl_refresh_model()
        table = self._mcl_list
        self._painting = True
        try:
            table.setRowCount(0)
            table.setRowCount(len(self.mcl_columns))
            for r, c in enumerate(self.mcl_columns):
                ftype = c.get("field_type", "text")
                name = QTableWidgetItem(c["label"])
                name.setData(Qt.UserRole, c["id"])
                table.setItem(r, 0, name)
                tag = QTableWidgetItem(ftype)
                tag.setData(ROLE_TAG, (ftype, "red" if ftype == "password" else "neutral"))
                table.setItem(r, 1, tag)
                role = _mcl_role(c)
                cell = QTableWidgetItem(role[0] if role else "\u2014")
                if role:
                    cell.setData(ROLE_PILL, role)
                table.setItem(r, 2, cell)
                for col, (field, tip) in _MCL_FLAG_COLS.items():
                    box = QTableWidgetItem("")
                    box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    box.setCheckState(Qt.Checked if _flag(c, field) else Qt.Unchecked)
                    box.setToolTip(f"{tip}: {c['label']} (staged until Save)")
                    table.setItem(r, col, box)
                edit = QTableWidgetItem("")
                edit.setData(ROLE_ACTIONS, [CellAction("edit", "mdi.pencil-outline", f"Edit {c['label']}")])
                table.setItem(r, _MCL_COL_EDIT, edit)
        finally:
            self._painting = False
        self._mcl_on_sel_changed(table.currentRow())

    def _mcl_on_item_changed(self, item):
        if self._painting or item.column() not in _MCL_FLAG_COLS:
            return
        name = self._mcl_list.item(item.row(), 0)
        if name is not None:
            field = _MCL_FLAG_COLS[item.column()][0]
            self._set_flag(name.data(Qt.UserRole), field, item.checkState() == Qt.Checked)

    def _mcl_on_cell_action(self, row: int, col: int, key: str):
        if key == "edit":
            self._mcl_list.setCurrentCell(row, 0)
            self._mcl_on_edit()

    def _mcl_on_sel_changed(self, row: int):
        table = self._mcl_list
        name = table.item(row, 0) if row >= 0 else None
        pick = None if name is not None else "Select a column first"
        self._mcl_btn_edit.set_disabled_reason(pick)
        self._mcl_btn_del.set_disabled_reason(pick)
        self._mcl_btn_up.set_disabled_reason(pick or ("Already the first column" if row == 0 else None))
        self._mcl_btn_dn.set_disabled_reason(
            pick or ("Already the last column" if row == table.rowCount() - 1 else None))
        self._mcl_sel_tag.setText(f"Selected: {name.text()}" if name is not None else "Selected: \u2014")

    def _mcl_sel_id(self):
        row = self._mcl_list.currentRow()
        item = self._mcl_list.item(row, 0) if row >= 0 else None
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
        if MessageBox.question(
            self, "Delete Column",
            "Delete this column? All client values for this column will be permanently removed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No, danger=True
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
            self._mcl_list.setCurrentCell(new_idx, 0)

    # ── Service actions ───────────────────────────────────────────────────────
    def _svc_reload(self):
        from ui.dialogs.service_manager_dialog import fill_service_table
        fill_service_table(self._svc_list, self.db.get_services(), url_col=3, edit_col=_SVC_COL_EDIT)
        self._svc_on_sel_changed(self._svc_list.currentRow())

    def _svc_on_cell_action(self, row: int, col: int, key: str):
        if key == "edit":
            self._svc_list.setCurrentCell(row, 0)
            self._svc_on_edit()

    def _svc_on_sel_changed(self, row: int):
        pick = None if (row >= 0 and self._svc_list.item(row, 0) is not None) else "Select a service first"
        self._svc_btn_edit.set_disabled_reason(pick)
        self._svc_btn_del.set_disabled_reason(pick)

    def _svc_sel_id(self):
        row = self._svc_list.currentRow()
        item = self._svc_list.item(row, 0) if row >= 0 else None
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
                MessageBox.critical(self, "Error", str(e))

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
        if MessageBox.question(
            self, "Delete Service",
            "Delete this service? It will be detached from all client profiles.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No, danger=True
        ) == QMessageBox.Yes:
            self.db.delete_service(svc_id)
            self._svc_reload()
            self.services_changed.emit()

    def _on_open_sgt_lab(self):
        from ui.dialogs.sgt_lab_dialog import SgtLabDialog
        SgtLabDialog(self).exec()

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
                MessageBox.critical(self, "Export Error", str(e))

    def _on_download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Download Import Template", "clients_import_template.csv", "CSV Files (*.csv)")
        if path:
            try:
                self.db.export_mcl_schema_csv(path)
                self.toast_requested.emit(f"Template saved to:\n{path}", 3000)
            except Exception as e:
                MessageBox.critical(self, "Download Error", str(e))

    def _on_backup(self):
        dest = QFileDialog.getExistingDirectory(self, "Choose Backup Destination")
        if not dest:
            return
        try:
            path = self.db.backup_to(dest)
            self.db.log_action(self.actor, "backup", detail=f"Backed up to {path}")
            self.toast_requested.emit(f"Backup saved:\n{path}", 3000)
        except Exception as e:
            MessageBox.critical(self, "Backup Failed", str(e))

    def _on_restore_backup(self):
        if getattr(self.db, "key_mode", None) == "office":
            self._on_restore_office_backup()
            return
        clicked = MessageBox.choose(
            self, "Restore Source", "How would you like to locate the backup?",
            [("Cancel", "secondary"), ("Select Backup Folder", "secondary"),
             ("Select Specific File (.db)", "secondary")])
        if clicked == 2:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose Database File", "",
                "Database Files (*.db *.sqlite *sync-conflict*);;All Files (*)")
        elif clicked == 1:
            path = QFileDialog.getExistingDirectory(self, "Choose Backup Folder")
        else:
            return
        if not path:
            return
        if MessageBox.warning(
            self, "Confirm Restore",
            "WARNING: This will overwrite your live database.\n\n"
            "If Syncthing is active, the restored database will also sync to all team members' PCs.\n\n"
            "The application will restart. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No, danger=True
        ) == QMessageBox.Yes:
            try:
                summary = self.db.restore_from(path)
                self.db.log_action(self.actor, "restore", detail=summary)
                MessageBox.information(
                    self, "Restore Successful",
                    f"{summary}\n\nThe application will now restart.")
                import version
                version.restart_app()
            except Exception as e:
                MessageBox.critical(self, "Restore Error", str(e))

    def _on_restore_office_backup(self):
        """Sera Sync v3 (P4-3b, D9): the restored backup becomes the data on every PC. Admin PC
        only; a dry run with counts, and the user types RESTORE. Done at the next start."""
        import os
        import sync_restore
        start = os.path.join(os.path.dirname(os.path.abspath(self.db.db_path)), "backups")
        path = QFileDialog.getExistingDirectory(self, "Choose Backup Folder",
                                                start if os.path.isdir(start) else "")
        if not path:
            return
        try:
            plan = sync_restore.plan_restore(self.db, path)
        except sync_restore.RestoreRefused as e:
            MessageBox.warning(self, "Restore Not Possible", str(e))
            return
        except Exception as e:
            MessageBox.critical(self, "Restore Error", str(e))
            return
        if not self._confirm_office_restore(plan):
            return
        try:
            sync_restore.stage_restore(self.db, path, actor=self.actor)
        except (sync_restore.RestoreRefused, sync_restore.RestoreError) as e:
            MessageBox.critical(self, "Restore Error", str(e))
            return
        MessageBox.information(
            self, "Restore Ready",
            "The backup is ready to be restored. Sera will now restart to do it.")
        import version
        version.restart_app()

    def _confirm_office_restore(self, plan) -> bool:
        import sync_restore
        title = "Restore Backup for the Whole Office"
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame(title, "Every Sera PC in the office gets this backup", dlg,
                            icon="mdi.backup-restore", tone="amber")
        frame.closeRequested.connect(dlg.reject)
        lay.addWidget(frame)
        frame.add_widget(Banner(plan.confirmation_text(), "w"))
        edit = Input()
        frame.add_widget(FieldLabel(f"Type {sync_restore.CONFIRM_WORD} to continue", for_widget=edit))
        frame.add_widget(edit)
        frame.add_footer_spacer()
        cancel = frame.add_action("Cancel")
        ok = frame.add_action("Restore", variant="danger", icon="mdi.backup-restore")
        reason = f"Type {sync_restore.CONFIRM_WORD} first"
        ok.set_disabled_reason(reason)
        edit.textChanged.connect(
            lambda t: ok.set_disabled_reason(None if t.strip() == sync_restore.CONFIRM_WORD else reason))
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        return dlg.exec() == QDialog.Accepted and edit.text().strip() == sync_restore.CONFIRM_WORD

    def _on_purge_duplicates(self):
        if MessageBox.question(
            self, "Purge Duplicate Clients",
            "This will permanently delete duplicate clients (keeping the oldest record per group).\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No, danger=True
        ) != QMessageBox.Yes:
            return
        try:
            results = self.db.purge_duplicate_clients()
        except Exception as e:
            MessageBox.critical(self, "Error", str(e))
            return
        if results["deleted"] == 0:
            self.toast_requested.emit("No duplicate clients were found.", 3000)
        else:
            self.db.log_action(
                self.actor, "purge_duplicates",
                detail=f"Purged {results['deleted']} duplicate(s) across {results['groups']} group(s)")
            MessageBox.information(
                self, "Purge Complete",
                f"\u2705  Deleted {results['deleted']} duplicate client(s) "
                f"across {results['groups']} group(s).")

    def _on_export_recovery_kit(self, app_dir):
        from ui.dialogs.change_master_password_dialog import export_recovery_kit_flow
        export_recovery_kit_flow(self, app_dir)

    def _on_change_master_password(self, app_dir):
        from ui.dialogs.change_master_password_dialog import ChangeMasterPasswordDialog
        dlg = ChangeMasterPasswordDialog(app_dir, parent=self)
        dlg.exec()
