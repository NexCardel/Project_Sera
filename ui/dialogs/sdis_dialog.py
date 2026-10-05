"""
ui/dialogs/sdis_dialog.py - Tracker dump -> Tools -> Distill... (SDIS Part L), admin PC only
============================================================================================
A calm, non-modal window: the datapoints Sera Distill found (one row each, the pages it was found
on as collapsible children), the "Please check" list, and the containers on the right. Built to
docs/sdis/distill-dialog-mockup.html, frames A-E. What the user decides is saved at once to the
synced sdis_decisions / sdis_fields / sdis_mcl / sdis_config tables (core/sdis/distill.py holds the
logic, core/sdis/register.py and config.py the writes) and is never overwritten by a later run.
Find datapoints opens the application-modal loading dialog; mining runs in its own process
(core/sdis/miner_client.py) and the UI thread is never blocked. Every number shown is a percentage
of the clients who visited the page (R7); nothing here is logged.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QTabBar, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.sdis import config, distill, register, store
from ui.dialogs.sdis_containers_dialog import (
    AMBER, BAD, BG, GREEN, LINE, MUTED, OK, PANEL, TEXT, ContainerEditor, LevelsDialog, field_completer, field_named,
)

MIME = "application/x-sera-sdis-datapoint"
KEY_ROLE = Qt.UserRole
C_LABEL, C_EXAMPLE, C_TYPE, C_STATUS, C_REL, C_SURE, C_FOUND, C_SUGGEST, C_ADD = range(9)
HEADERS = ("Label", "Example", "Type", "Status", "Relevance", "Sure", "Found on", "Suggested", "")
CHIPS = {"profile": ("● Profile", "#5aa9ff", "#16304d"), "dataset": ("◆ Dataset", "#3fd17f", "#173d28"),
         "info": ("▲ Others", "#b48cff", "#2c2147"), "": ("? not sure", MUTED, "#26262a")}
LOW_SURE = 60
ROW_CAP = 1000
RELEVANCE_STEPS = (0, 10, 20, 30, 50, 70)
STATUS_ORDER = ("variable", "semi-variable", "variable_alignment", "fixed", "furniture", "waiting", "ambiguous", "retired", "composite")
STATES = ("Hide captured", "Any state", "Captured only", "New since last run", "Too little evidence")

STYLE = f"""
QDialog {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QLabel {{ color: {TEXT}; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#title {{ font-size: 20px; font-weight: 600; }}
QLabel#cardn {{ font-size: 20px; font-weight: 700; }}
QLabel#bad {{ color: {BAD}; }}
QFrame#card, QFrame#box {{ background: {PANEL}; border: 1px solid #2c2c30; border-radius: 8px; }}
QFrame#card[sel="true"] {{ border-color: {GREEN}; }}
QFrame#box[drop="true"] {{ border: 1px dashed {GREEN}; background: #16231b; }}
QFrame#side {{ background: #141415; border-left: 1px solid #2c2c30; }}
QLineEdit, QComboBox {{ background: {PANEL}; color: {TEXT}; border: 1px solid #2c2c30; border-radius: 7px; padding: 5px 9px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {TEXT}; selection-background-color: {GREEN}; }}
QTreeWidget {{ background: {BG}; color: {TEXT}; border: 0; outline: 0; alternate-background-color: {BG}; }}
QTreeWidget::item {{ padding: 5px 4px; border-bottom: 1px solid #222226; }}
QTreeWidget::item:selected {{ background: #18241d; color: {TEXT}; }}
QHeaderView::section {{ background: {BG}; color: {OK}; border: 0; border-bottom: 2px solid #173d28; padding: 6px 8px; font-weight: 600; }}
QPushButton {{ background: {PANEL}; color: {TEXT}; border: 1px solid #2c2c30; border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {GREEN}; }}
QPushButton:disabled {{ color: #6c6e75; }}
QPushButton#primary {{ background: {GREEN}; border-color: {GREEN}; color: #fff; font-weight: 600; }}
QPushButton#small {{ padding: 2px 9px; font-size: 12px; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid #2c2c30; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {GREEN}; font-weight: 600; }}
QProgressBar {{ background: #2a2a2e; border: 0; border-radius: 5px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 5px; }}
QMenu {{ background: #171717; border: 1px solid {GREEN}; border-radius: 8px; padding: 6px; color: {TEXT}; }}
QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {GREEN}; color: #fff; }}
QMenu::item:disabled {{ color: #6c6e75; }}
"""


def when_text(iso: str) -> str:
    """'today 14:05' / 'yesterday 14:05' / '18 Sep 14:05'; never (nothing mined yet)."""
    if not iso:
        return "never"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    days = (datetime.now(t.tzinfo).date() - t.date()).days
    hm = t.strftime("%H:%M")
    return f"today {hm}" if days == 0 else f"yesterday {hm}" if days == 1 else t.strftime("%d %b ") + hm


def _short(text: str, n: int = 90) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


class _Tree(QTreeWidget):
    """Datapoint rows can be dragged onto a container box on the right."""

    def mimeTypes(self) -> List[str]:
        return [MIME]

    def mimeData(self, items: List[QTreeWidgetItem]) -> QMimeData:
        md = QMimeData()
        keys = [i.data(C_LABEL, KEY_ROLE) for i in items if i.parent() is None and i.data(C_LABEL, KEY_ROLE)]
        md.setData(MIME, json.dumps(keys).encode("utf-8"))
        return md


class _Box(QFrame):
    """A container on the side panel: a drop target, a title row and its fields."""
    dropped = Signal(str)
    edit_requested = Signal()

    def __init__(self, chip: str, title: str, meta: str, fields: List[str], note: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("box")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 9, 11, 9)
        head = QHBoxLayout()
        c = QLabel(chip)
        c.setStyleSheet(f"color: {CHIPS[{'●': 'profile', '◆': 'dataset', '▲': 'info'}[chip]][1]}; font-weight: 700;")
        head.addWidget(c)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600;")
        head.addWidget(t, 1)
        m = QLabel(meta)
        m.setObjectName("hint")
        head.addWidget(m)
        edit = QPushButton("✎")
        edit.setObjectName("small")
        edit.setToolTip("Edit")
        edit.clicked.connect(self.edit_requested.emit)
        head.addWidget(edit)
        lay.addLayout(head)
        body = QLabel("  ".join(f"[{f}]" for f in fields) if fields else "No fields yet")
        body.setWordWrap(True)
        body.setObjectName("hint")
        lay.addWidget(body)
        if note:
            n = QLabel(note)
            n.setObjectName("hint")
            n.setWordWrap(True)
            lay.addWidget(n)

    def _drop_style(self, on: bool) -> None:
        self.setProperty("drop", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e: Any) -> None:
        if e.mimeData().hasFormat(MIME):
            self._drop_style(True)
            e.acceptProposedAction()

    def dragLeaveEvent(self, e: Any) -> None:
        self._drop_style(False)

    def dropEvent(self, e: Any) -> None:
        self._drop_style(False)
        for key in json.loads(bytes(e.mimeData().data(MIME)).decode("utf-8")):
            self.dropped.emit(key)
        e.acceptProposedAction()


class _Delegate(QStyledItemDelegate):
    """Draws the label's pen / registered marker, the relevance bar, the suggestion chip and the Add to button."""

    def __init__(self, tree: QTreeWidget) -> None:
        super().__init__(tree)

    def createEditor(self, parent: QWidget, option: Any, index: Any) -> Optional[QWidget]:
        if index.column() != C_LABEL or index.parent().isValid():
            return None
        edit = QLineEdit(parent)
        edit.setPlaceholderText("Name this datapoint")
        dlg = self.parent().window()
        if hasattr(dlg, "library_labels"):
            edit.setCompleter(field_completer(edit, dlg.library_labels()))
        return edit

    def paint(self, p: QPainter, option: Any, index: Any) -> None:
        col = index.column()
        if index.parent().isValid() or col not in (C_LABEL, C_REL, C_SUGGEST, C_ADD):
            return super().paint(p, option, index)
        rect: QRect = option.rect
        p.save()
        if option.state & QStyle.State_Selected:
            p.fillRect(rect, QColor("#18241d"))
        if col == C_REL:
            pct = int(index.data(KEY_ROLE) or 0)
            track = QRect(rect.left() + 6, rect.center().y() - 3, rect.width() - 52, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2a2a2e"))
            p.drawRoundedRect(track, 3, 3)
            p.setBrush(QColor(GREEN))
            p.drawRoundedRect(QRect(track.left(), track.top(), round(track.width() * pct / 100), 6), 3, 3)
            p.setPen(QColor(TEXT))
            p.drawText(QRect(track.right() + 4, rect.top(), 40, rect.height()), Qt.AlignVCenter | Qt.AlignRight, f"{pct}%")
        elif col == C_SUGGEST:
            text, fg, bg = CHIPS.get(index.data(KEY_ROLE) or "", CHIPS[""])
            fm = QFontMetrics(option.font)
            w = fm.horizontalAdvance(text) + 18
            chip = QRect(rect.left() + 4, rect.center().y() - 10, w, 20)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(chip, 10, 10)
            p.setPen(QColor(fg))
            p.drawText(chip, Qt.AlignCenter, text)
        elif col == C_ADD:
            btn = QRect(rect.left() + 2, rect.center().y() - 11, rect.width() - 8, 22)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor("#3a3a40")))
            p.setBrush(QColor(PANEL))
            p.drawRoundedRect(btn, 6, 6)
            p.setPen(QColor(TEXT))
            p.drawText(btn, Qt.AlignCenter, "Add to ▾")
        else:
            label = index.data(Qt.DisplayRole) or ""
            fm = QFontMetrics(option.font)
            x = rect.left() + 4
            font = option.font
            font.setBold(True)
            p.setFont(font)
            fm = QFontMetrics(font)
            if label:
                p.setPen(QColor(TEXT))
                width = min(fm.horizontalAdvance(label), rect.width() - 80)
                p.drawText(QRect(x, rect.top(), width, rect.height()), Qt.AlignVCenter, fm.elidedText(label, Qt.ElideRight, width))
            else:
                p.setPen(QColor(MUTED))
                width = fm.horizontalAdvance("(no label)")
                p.drawText(QRect(x, rect.top(), width, rect.height()), Qt.AlignVCenter, "(no label)")
            font.setBold(False)
            font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
            p.setFont(font)
            p.setPen(QColor(OK if index.data(KEY_ROLE + 1) else "#6c6e75"))
            mark = f"✓ {index.data(KEY_ROLE + 1)}" if index.data(KEY_ROLE + 1) else ("✎ name it" if not label else "✎")
            p.drawText(QRect(x + width + 8, rect.top(), max(0, rect.right() - x - width - 8), rect.height()),
                       Qt.AlignVCenter, mark)
        p.restore()


class SdisLoadingDialog(QDialog):
    """Find datapoints: application-modal (locks Sera, not Windows). The mining child reports through
    MinerClient's callbacks, which run on its reader thread: they only emit Qt signals."""
    progress_sig = Signal(int, int, str)
    done_sig = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None, client_factory: Optional[Callable[..., Any]] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.setWindowTitle("Sera Distill")
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setMinimumWidth(460)
        self.result: Optional[Dict[str, Any]] = None
        self._finished = False
        lay = QVBoxLayout(self)
        title = QLabel("Finding datapoints…")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        lay.addWidget(self._hint("Sera is locked until this finishes. Capture keeps running."))
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.working = QLabel("Working on 0 of 0")
        self.working.setObjectName("hint")
        lay.addWidget(self.working)
        lay.addWidget(self._hint("Finished work is saved as it goes · Cancel loses nothing"))
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)
        from core.sdis.miner_client import MinerClient
        self.client = (client_factory or MinerClient)(on_progress=self.progress_sig.emit, on_done=self.done_sig.emit)
        self.progress_sig.connect(self._on_progress)
        self.done_sig.connect(self._on_done)

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("hint")
        lab.setWordWrap(True)
        return lab

    def start(self, captures: Any, state: Any = None, rebuild: bool = False) -> None:
        try:
            self.client.start(captures, state, rebuild)
        except Exception as e:                  # the child could not be started: counts-only reason
            self.done_sig.emit({"result": "error", "message": f"mining could not start ({type(e).__name__})"})

    def cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.working.setText("Cancelling…")
        self.client.cancel()

    def reject(self) -> None:
        if not self._finished:
            self.cancel()
            return
        super().reject()

    def _on_progress(self, done: int, total: int, page: str) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        self.working.setText(f"Working on {done} of {total} - {_short(page)}")

    def _on_done(self, result: Dict[str, Any]) -> None:
        self.result = result
        self._finished = True
        if result.get("result") == "ok" and not result.get("cancelled"):
            self.accept()
        else:
            super().reject()


class SdisDialog(QDialog):
    def __init__(self, db: Any, parent: Optional[QWidget] = None, state: Optional[Dict[str, Any]] = None,
                 state_path: Optional[Path] = None, portals: Optional[List[str]] = None,
                 portal_of: Optional[Callable[[Any], str]] = None, who: str = "",
                 client_factory: Optional[Callable[..., Any]] = None) -> None:
        super().__init__(parent)
        self.db = db
        self._injected = state
        self._state_path = Path(state_path) if state_path else None
        self._portals = list(portals) if portals is not None else None
        self._portal_of = portal_of or distill.portal_of_datapoint
        self._client_factory = client_factory
        self.who = who or os.environ.get("USERNAME", "") or "admin"
        self.state: Optional[Dict[str, Any]] = None
        self.decided = distill.Decided()
        self.dps: List[Any] = []
        self.items: List[Dict[str, Any]] = []
        self._by_key: Dict[tuple, Any] = {}
        self._examples: Dict[tuple, str] = {}
        self._statuses: Dict[tuple, str] = {}
        self._portal_cache: Dict[tuple, str] = {}
        self._new: set = set()
        self._building = False
        self._dirty = False                     # decisions not yet written to the state file (_persist)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush)
        self._loading_dlg: Optional[SdisLoadingDialog] = None
        self._side_portal = ""
        self._refused = ""
        self.setWindowTitle("Sera Distill")
        self.setModal(False)
        self.resize(1280, 800)
        self.setStyleSheet(STYLE)
        self._build()
        self.reload()

    # ── building the window ──────────────────────────────────────────────────
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(22, 16, 22, 8)
        titles = QVBoxLayout()
        t = QLabel("Sera Distill")
        t.setObjectName("title")
        titles.addWidget(t)
        titles.addWidget(self._hint("Datapoints found by comparing the same page across clients. Pick what to "
                                    "capture — the rest is fluff."))
        head.addLayout(titles, 1)
        self.last_run = QLabel("")
        self.last_run.setObjectName("hint")
        self.last_run.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.last_run)
        self.find_btn = QPushButton("⟳ Find datapoints")
        self.find_btn.setObjectName("primary")
        self.find_btn.clicked.connect(self.find_datapoints)
        head.addWidget(self.find_btn)
        self.side_toggle = QPushButton("≡")
        self.side_toggle.setToolTip("Show or hide the containers")
        self.side_toggle.setCheckable(True)
        self.side_toggle.setChecked(True)
        self.side_toggle.clicked.connect(lambda on: self.side.setVisible(on))
        head.addWidget(self.side_toggle)
        outer.addLayout(head)

        cards = QHBoxLayout()
        cards.setContentsMargins(22, 4, 22, 10)
        self.card_n: Dict[str, QLabel] = {}
        for i, (key, text, colour) in enumerate((("found", "Datapoints found", OK), ("new", "New since last run", "#5aa9ff"),
                                                 ("check", "Please check", AMBER), ("captured", "Already captured", TEXT),
                                                 ("little", "Too little evidence", "#6c6e75"))):
            card = QFrame()
            card.setObjectName("card")
            card.setProperty("sel", i == 0)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 8, 12, 8)
            cl.setSpacing(0)
            n = QLabel("0")
            n.setObjectName("cardn")
            n.setStyleSheet(f"color: {colour};")
            cl.addWidget(n)
            cl.addWidget(self._hint(text))
            self.card_n[key] = n
            cards.addWidget(card, 1)
        outer.addLayout(cards)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_datapoints(), "Datapoints")
        self.tabs.addTab(self._build_check(), "Please check")
        self.tabs.currentChanged.connect(self._tab_changed)
        split.addWidget(self.tabs)
        self.side = self._build_side()
        split.addWidget(self.side)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 380])
        outer.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(22, 8, 22, 10)
        self.showing = self._hint("")
        foot.addWidget(self.showing)
        self.note = self._hint("")
        foot.addWidget(self.note, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        foot.addWidget(close)
        outer.addLayout(foot)

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("hint")
        lab.setWordWrap(True)
        return lab

    def _build_datapoints(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 12, 16, 8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("⌕  Search label, page link, example…")
        self.search.textChanged.connect(lambda _t: self._populate())
        bar.addWidget(self.search, 1)
        self.rel_f = QComboBox()
        for v in RELEVANCE_STEPS:
            self.rel_f.addItem(f"Relevance ≥ {v}%", v)
        self.rel_f.setCurrentIndex(RELEVANCE_STEPS.index(20))
        self.status_f = QComboBox()
        self.page_f = QComboBox()
        self.browser_f = QComboBox()
        self.state_f = QComboBox()
        self.state_f.addItems(STATES)
        for combo in (self.rel_f, self.status_f, self.page_f, self.browser_f, self.state_f):
            combo.currentIndexChanged.connect(lambda _i: self._populate())
            bar.addWidget(combo)
        self.register_btn = QPushButton("Register")
        self.register_btn.setObjectName("primary")
        self.register_btn.setEnabled(False)
        self.register_btn.clicked.connect(self.register_selected)
        bar.addWidget(self.register_btn)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        self.tree = _Tree()
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(list(HEADERS))
        self.tree.setItemDelegate(_Delegate(self.tree))
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setEditTriggers(QTreeWidget.DoubleClicked | QTreeWidget.EditKeyPressed)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QTreeWidget.DragOnly)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(C_LABEL, QHeaderView.Interactive)
        for col, w in ((C_LABEL, 280), (C_EXAMPLE, 140), (C_TYPE, 75), (C_STATUS, 125), (C_REL, 160), (C_SURE, 55),
                       (C_FOUND, 75), (C_SUGGEST, 105), (C_ADD, 85)):
            self.tree.setColumnWidth(col, w)
        hdr.setStretchLastSection(False)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemClicked.connect(self._item_clicked)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.stack.addWidget(self.tree)
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignCenter)
        self.empty_big = QLabel("SDIS hasn't looked yet")
        self.empty_big.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.empty_big.setAlignment(Qt.AlignCenter)
        self.empty_text = self._hint("Find datapoints reads the captures synced to this PC and lists what it finds here.")
        self.empty_text.setAlignment(Qt.AlignCenter)
        el.addWidget(self.empty_big)
        el.addWidget(self.empty_text)
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)
        return page

    def _build_check(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 12, 16, 8)
        lay.addWidget(self._hint("Short, plain sentences; one decision per row. Keep = treat as data (a status or "
                                 "form word); Template = never a datapoint again. Both are saved and synced."))
        bar = QHBoxLayout()
        self.check_search = QLineEdit()
        self.check_search.setPlaceholderText("⌕  Search text, page link, saw…")
        self.check_search.textChanged.connect(lambda _t: self._fill_check())
        bar.addWidget(self.check_search, 1)

        self.check_page_f = QComboBox()
        self.check_browser_f = QComboBox()
        for combo in (self.check_page_f, self.check_browser_f):
            combo.currentIndexChanged.connect(lambda _i: self._fill_check())
            bar.addWidget(combo)
        lay.addLayout(bar)

        self.check_tree = QTreeWidget()
        self.check_tree.setColumnCount(4)
        self.check_tree.setHeaderLabels(["Text", "Where", "What SDIS saw", ""])
        self.check_tree.setRootIsDecorated(False)
        self.check_tree.setColumnWidth(0, 220)
        self.check_tree.setColumnWidth(1, 260)
        self.check_tree.setColumnWidth(2, 380)
        lay.addWidget(self.check_tree, 1)
        return page

    def _build_side(self) -> QWidget:
        side = QFrame()
        side.setObjectName("side")
        side.setMinimumWidth(340)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 12, 16, 12)
        h = QLabel("Containers")
        h.setStyleSheet("font-size: 14px; font-weight: 600;")
        lay.addWidget(h)
        lay.addWidget(self._hint("Drag a datapoint here, or use Add to. Adding it captures it on every PC."))
        self.portal_bar = QTabBar()
        self.portal_bar.setExpanding(False)
        self.portal_bar.currentChanged.connect(self._portal_tab)
        lay.addWidget(self.portal_bar)
        self.side_scroll = QScrollArea()
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.NoFrame)
        self.side_body = QWidget()
        self.side_lay = QVBoxLayout(self.side_body)
        self.side_lay.setContentsMargins(0, 0, 0, 0)
        self.side_lay.setAlignment(Qt.AlignTop)
        self.side_scroll.setWidget(self.side_body)
        lay.addWidget(self.side_scroll, 1)
        new = QPushButton("＋ New dataset container")
        new.clicked.connect(self.new_container)
        lay.addWidget(new)
        row = QHBoxLayout()
        for text, fn in (("Levels…", self.edit_levels), ("Export JSON", self.export_containers),
                         ("Import JSON", self.import_containers)):
            b = QPushButton(text)
            b.setObjectName("small")
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.file_hint = self._hint("")
        lay.addWidget(self.file_hint)
        return side

    # ── loading what there is ────────────────────────────────────────────────
    def path(self) -> Path:
        return self._state_path or store.default_path()

    def reload(self, state: Optional[Dict[str, Any]] = None) -> None:
        """Re-reads the saved mining state and the decision rows. A decision made since the last run
        is pushed into the state file here (never while mining runs: the loading dialog is modal)."""
        self.state = state or self._injected or store.load(self.path())
        self.decided = distill.read_decisions(self.db.list_sdis_decisions())
        self.dps, self.items, self._new = [], [], set()
        if self.state is not None:
            if distill.apply_decisions(self.state, self.decided):
                self._persist()
            self.dps = distill.refresh_datapoints(self.state, self.decided, config.class_exceptions())
            self.items = distill.alignment_items(self.state, self.decided)
            if self._injected is None:
                self._new = distill.roll_seen(self.path().with_name("distill_seen.json"),
                                              self.state.get("last_run") or "", [dp.key for dp in self.dps])
        self._by_key = {dp.key: dp for dp in self.dps}
        mems = (self.state or {}).get("memories") or []
        self._examples = {dp.key: distill.example_of(dp, mems) for dp in self.dps}
        self._statuses = {dp.key: distill.status_of(dp, mems) for dp in self.dps}
        self._portal_cache = {}
        self._fill_filters()
        self._refresh_all()

    def _persist(self) -> None:
        """The state file is big (seconds to write), so a decision only marks it dirty and the write happens
        once, a moment after the last click, before mining starts, and when the dialog closes. The decisions
        themselves are in the database at once."""
        if self._injected is None and self.state is not None:
            self._dirty = True
            self._save_timer.start(3000)

    def _flush(self) -> None:
        self._save_timer.stop()
        if not self._dirty or self._injected is not None or self.state is None:
            return
        self._dirty = False
        try:
            store.save(self.state, self.path())
        except OSError:
            self._dirty = True
            self._say("The mining state could not be saved.", bad=True)

    def _fill_filters(self) -> None:
        statuses = set()
        has_empty = False
        for dp in self.dps:
            st = self._statuses.get(dp.key, "") or getattr(dp, "status", "")
            if not st.strip():
                has_empty = True
            for s in st.split(","):
                s = s.strip()
                if s:
                    statuses.add(s)
        ordered_statuses = sorted(statuses, key=lambda s: (STATUS_ORDER.index(s) if s in STATUS_ORDER else 99, s))
        status_items = [(f"Status: {s}", s) for s in ordered_statuses]
        if has_empty:
            status_items.append(("Status: (none)", "__none__"))

        for combo, first, values in (
            (self.status_f, "Status: all", status_items),
            (self.page_f, "Page: all", [(_short(v, 60), v) for v in sorted({p[0] for dp in self.dps for p in dp.pages})]),
            (self.browser_f, "Browser: all", [(v, v) for v in sorted({p[2] for dp in self.dps for p in dp.pages if p[2]})]),
        ):
            keep_data = combo.currentData()
            keep_text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(first, "")
            for label, data in values:
                combo.addItem(label, data)
            i = combo.findData(keep_data) if keep_data else -1
            if i < 0 and keep_text:
                i = combo.findText(keep_text)
            combo.setCurrentIndex(max(0, i))
            combo.blockSignals(False)

        if hasattr(self, "check_page_f") and hasattr(self, "check_browser_f"):
            check_pages = sorted({(item.get("page") or (item.get("triple", ("",))[0] if item.get("triple") else ""))
                                  for item in self.items
                                  if (item.get("page") or (item.get("triple") and item["triple"][0]))})
            if not check_pages:
                check_pages = sorted({p[0] for dp in self.dps for p in dp.pages})

            check_browsers = sorted({(item.get("browser") or (item.get("where", "").split(" · ")[1].strip() if " · " in item.get("where", "") else ""))
                                    for item in self.items
                                    if (item.get("browser") or " · " in item.get("where", ""))})
            if not check_browsers:
                check_browsers = sorted({p[2] for dp in self.dps for p in dp.pages if p[2]})

            for combo, first, values in (
                (self.check_page_f, "Page: all", [(_short(v, 60), v) for v in check_pages]),
                (self.check_browser_f, "Browser: all", [(v, v) for v in check_browsers]),
            ):
                keep_data = combo.currentData()
                keep_text = combo.currentText()
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(first, "")
                for label, data in values:
                    combo.addItem(label, data)
                i = combo.findData(keep_data) if keep_data else -1
                if i < 0 and keep_text:
                    i = combo.findText(keep_text)
                combo.setCurrentIndex(max(0, i))
                combo.blockSignals(False)

    def _refresh_all(self) -> None:
        self._fill_filters()
        self._populate()
        self._fill_check()
        self._refresh_cards()
        self._refresh_side()

    def _say(self, text: str, bad: bool = False) -> None:
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {BAD};" if bad else "")

    # ── the datapoints list ──────────────────────────────────────────────────
    def library(self) -> Dict[str, Dict[str, Any]]:
        return {r["name"]: r for r in self.db.list_sdis_mcl(active_only=True)}

    def library_labels(self) -> Dict[str, str]:
        return {n: r.get("label") or n for n, r in self.library().items()}

    def shown_label(self, dp: Any) -> str:
        return distill.shown_label(dp, self.decided, self.library_labels())

    def portal_for(self, dp: Any) -> str:
        if dp.key not in self._portal_cache:
            self._portal_cache[dp.key] = self._portal_of(dp) or ""
        return self._portal_cache[dp.key]

    def _visible(self) -> List[Any]:
        q = self.search.text().strip().lower()
        rel = self.rel_f.currentData() or 0
        status_sel = self.status_f.currentData() or ""
        page, browser, state = self.page_f.currentData() or "", self.browser_f.currentData() or "", self.state_f.currentText()
        out = []
        for dp in self.dps:
            if dp.key in self.decided.dismissed or dp.relevance_pct < rel:
                continue
            if status_sel:
                st = self._statuses.get(dp.key, "") or getattr(dp, "status", "")
                if status_sel == "__none__":
                    if st.strip():
                        continue
                else:
                    parts = {p.strip() for p in st.split(",") if p.strip()}
                    if status_sel not in parts:
                        continue
            if page and not any(p[0] == page for p in dp.pages):
                continue
            if browser and not any(p[2] == browser for p in dp.pages):
                continue
            captured = dp.key in self.decided.registered
            if (state == "Hide captured" and captured) or (state == "Captured only" and not captured) \
                    or (state == "New since last run" and dp.key not in self._new) \
                    or (state == "Too little evidence" and dp.class_reason != "not enough evidence"):
                continue
            if q and not (q in self.shown_label(dp).lower() or q in self._examples.get(dp.key, "").lower()
                          or q in self._statuses.get(dp.key, "").lower()
                          or any(q in p[0].lower() for p in dp.pages)):
                continue
            out.append(dp)
        return out

    def _populate(self) -> None:
        if not hasattr(self, "tree"):
            return
        open_keys = {self.tree.topLevelItem(i).data(C_LABEL, KEY_ROLE) for i in range(self.tree.topLevelItemCount())
                     if self.tree.topLevelItem(i).isExpanded()}
        keep = self.selected_key()
        self._building = True
        self.tree.clear()
        visible = self._visible()
        labels = self.library_labels()
        doc = self.doc()
        for dp in visible[:ROW_CAP]:
            top = QTreeWidgetItem(self.tree)
            key_json = json.dumps(list(dp.key), ensure_ascii=False)
            top.setFlags(top.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled)
            top.setText(C_LABEL, distill.shown_label(dp, self.decided, labels))
            top.setData(C_LABEL, KEY_ROLE, key_json)
            top.setData(C_LABEL, KEY_ROLE + 1, self.decided.registered.get(dp.key, ""))
            top.setText(C_EXAMPLE, self._examples.get(dp.key, ""))
            top.setText(C_TYPE, dp.value_type)
            st_text = self._statuses.get(dp.key, "") or getattr(dp, "status", "")
            top.setText(C_STATUS, st_text)
            top.setToolTip(C_STATUS, f"Status: {st_text}" if st_text else "")
            top.setData(C_REL, KEY_ROLE, dp.relevance_pct)
            top.setText(C_SURE, f"{dp.sure_pct}%")
            if dp.sure_pct < LOW_SURE:
                top.setForeground(C_SURE, QColor(AMBER))
            top.setText(C_FOUND, f"{len(dp.pages)} page{'s' if len(dp.pages) != 1 else ''}")
            top.setData(C_SUGGEST, KEY_ROLE, dp.suggested_class or "")
            where = distill.suggest_container(doc, self.portal_for(dp), dp.suggested_class)
            tip = dp.class_reason + (f" - suggested place: {distill.where_label(where)}" if where is not None else "")
            top.setToolTip(C_SUGGEST, tip)
            for link, _screen, browser, share in dp.pages:
                child = QTreeWidgetItem(top)
                child.setFlags(Qt.ItemIsEnabled)
                child.setText(C_LABEL, f"↳ {link} · {round(share * 100)}% of clients" + (f" · {browser}" if browser else ""))
                child.setForeground(C_LABEL, QColor(MUTED))
                child.setFirstColumnSpanned(True)
            top.setExpanded(key_json in open_keys)
            if keep == dp.key:
                self.tree.setCurrentItem(top)
        self._building = False
        self._update_showing()
        self.stack.setCurrentWidget(self.tree if visible else self.empty)
        if not visible:
            looked = self.state is not None
            self.empty_big.setText("No datapoints match" if self.dps else "SDIS hasn't looked yet" if not looked
                                   else "SDIS found no datapoints yet")
            self.empty_text.setText("Change the filters above." if self.dps else
                                    "Find datapoints reads the captures synced to this PC and lists what it finds here.")
        self._selection_changed()

    def _refresh_cards(self) -> None:
        got = distill.counts(self.dps, self.decided, self.items, self._new, set(self.decided.registered))
        for key, label in self.card_n.items():
            label.setText(str(got[key]))
        self.tabs.setTabText(1, f"Please check ({len(self.items)})" if self.items else "Please check")
        run = distill.last_run_summary(self.state) if self.state else {"when": "", "captures": 0, "clients": 0, "browsers": 0}
        self.last_run.setText(f"Last run: {when_text(run['when'])}" + (
            f"\n{run['captures']} captures · {run['clients']} clients · {run['browsers']} browsers" if run["when"] else ""))

    def selected_key(self) -> Optional[tuple]:
        item = self.tree.currentItem()
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()
        raw = item.data(C_LABEL, KEY_ROLE)
        return tuple(json.loads(raw)) if raw else None

    def selected(self) -> Optional[Any]:
        key = self.selected_key()
        return self._by_key.get(key) if key is not None else None

    def _selection_changed(self) -> None:
        dp = self.selected()
        self.register_btn.setEnabled(dp is not None)
        if dp is not None:
            portal = self.portal_for(dp)
            names = [self.portal_bar.tabText(i) for i in range(self.portal_bar.count())]
            if portal in names and portal != self._side_portal:
                self.portal_bar.setCurrentIndex(names.index(portal))

    def _item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        if col == C_ADD and item.parent() is None:
            dp = self._by_key.get(tuple(json.loads(item.data(C_LABEL, KEY_ROLE))))
            if dp is not None:
                rect = self.tree.visualItemRect(item)
                self.add_menu(dp).exec(self.tree.viewport().mapToGlobal(QPoint(rect.right() - 100, rect.bottom())))

    def _item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if self._building or col != C_LABEL or item.parent() is not None:
            return
        dp = self._by_key.get(tuple(json.loads(item.data(C_LABEL, KEY_ROLE))))
        if dp is not None:
            self.save_label(dp, item.text(C_LABEL))
            self._building = True
            item.setText(C_LABEL, self.shown_label(dp))
            self._building = False
            self.tree.viewport().update()

    # ── what the user decides ────────────────────────────────────────────────
    def save_label(self, dp: Any, text: str) -> bool:
        """R6: the edit is saved at once and never overwritten by a later run. A registered datapoint
        renames its field everywhere (rename_sdis_mcl)."""
        text = " ".join((text or "").split())
        if not text or text == self.shown_label(dp):
            return False
        self.db.set_sdis_decision(distill.signature(distill.LABEL, dp.key), distill.LABEL, text)
        self.decided.labels[dp.key] = text
        field = self.decided.registered.get(dp.key)
        if field:
            row = self.library().get(field)
            if row:
                self.db.rename_sdis_mcl(row["gid"], text)
        self._refresh_side()
        return True

    def dismiss(self, dp: Any) -> None:
        self.db.set_sdis_decision(distill.signature(distill.DISMISS, dp.key), distill.DISMISSED, "")
        self.decided.dismissed.add(dp.key)
        self._decided_changed()

    def _decided_changed(self, check_changed: bool = False) -> None:
        if self.state is not None:
            if distill.apply_decisions(self.state, self.decided):
                if self.state.get("memories"):
                    distill.recount(self.state, self.decided, config.class_exceptions())
                self._persist()
            self.dps = distill.refresh_datapoints(self.state, self.decided, config.class_exceptions())
            if check_changed:                   # only a Please check answer changes that list (2 s)
                self.items = distill.alignment_items(self.state, self.decided)
            self._by_key = {dp.key: dp for dp in self.dps}
            mems = self.state.get("memories") or []
            self._examples = {dp.key: distill.example_of(dp, mems) for dp in self.dps}
            self._statuses = {dp.key: distill.status_of(dp, mems) for dp in self.dps}
        self._refresh_all()

    def answer(self, triple: tuple, decision: str) -> None:
        """Please check: Keep as data / Template (reject, sent to the next mining run)."""
        self.db.set_sdis_decision(distill.signature(distill.VA, triple), decision, "")
        self.decided.va[triple] = decision
        self._decided_changed(check_changed=True)

    def _tab_changed(self, _index: int = 0) -> None:
        self._update_showing()

    def _update_showing(self) -> None:
        if not hasattr(self, "showing") or not hasattr(self, "tabs"):
            return
        if self.tabs.currentIndex() == 1:
            visible_check = self._visible_check() if hasattr(self, "check_tree") else self.items
            self.showing.setText(f"Showing {min(len(visible_check), ROW_CAP)} of {len(self.items)} items to check")
        else:
            visible = self._visible() if hasattr(self, "tree") else self.dps
            total = len([dp for dp in self.dps if dp.key not in self.decided.dismissed])
            self.showing.setText(f"Showing {min(len(visible), ROW_CAP)} of {total} datapoints · every number is a "
                                 "percentage of the clients who visited that page")

    def _visible_check(self) -> List[Dict[str, Any]]:
        q = self.check_search.text().strip().lower() if hasattr(self, "check_search") else ""
        page = self.check_page_f.currentData() or "" if hasattr(self, "check_page_f") else ""
        browser = self.check_browser_f.currentData() or "" if hasattr(self, "check_browser_f") else ""
        out = []
        for item in self.items:
            item_page = item.get("page") or (item.get("triple", ("",))[0] if item.get("triple") else "")
            if page:
                if item_page != page and page not in item.get("where", ""):
                    continue
            item_browser = item.get("browser") or ""
            if not item_browser and " · " in item.get("where", ""):
                item_browser = item["where"].split(" · ")[1].strip()
            if browser:
                if item_browser.lower() != browser.lower() and browser.lower() not in item.get("where", "").lower():
                    continue
            if q:
                text = item.get("text", "").lower()
                where = item.get("where", "").lower()
                saw = item.get("saw", "").lower()
                if q not in text and q not in where and q not in saw:
                    continue
            out.append(item)
        return out

    def _fill_check(self) -> None:
        if not hasattr(self, "check_tree"):
            return
        self.check_tree.clear()
        visible = self._visible_check()
        for item in visible[:ROW_CAP]:
            row = QTreeWidgetItem(self.check_tree)
            row.setText(0, item.get("text", ""))
            row.setText(1, _short(item.get("where", ""), 70))
            saw = item.get("saw", "")
            row.setText(2, saw)
            row.setToolTip(2, saw)
            if "triple" in item:
                box = QWidget()
                bl = QHBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                for text, decision in (("Keep as data", distill.KEEP), ("Template", distill.REJECT)):
                    b = QPushButton(text)
                    b.setObjectName("small")
                    b.setProperty("decision", decision)
                    b.clicked.connect(lambda _c=False, t=item["triple"], d=decision: self.answer(t, d))
                    bl.addWidget(b)
                self.check_tree.setItemWidget(row, 3, box)
        self._update_showing()

    # ── containers: the side panel ───────────────────────────────────────────
    def portal_list(self) -> List[str]:
        if self._portals is None:
            from core.sdis.portals import portal_names
            self._portals = portal_names()
        return self._portals

    def doc(self) -> Dict[str, Any]:
        import copy
        row = self.db.get_sdis_containers()
        return copy.deepcopy(row["doc"]) if row else copy.deepcopy(config.current())

    def known(self) -> Dict[str, Any]:
        return {"fields": [r["name"] for r in self.db.list_sdis_mcl()], "portals": self.portal_list()}

    def put(self, doc: Dict[str, Any]) -> int:
        return self.db.put_sdis_containers(doc, updated_by=self.who, portals=self.portal_list())

    def _portal_tab(self, index: int) -> None:
        self._side_portal = self.portal_bar.tabText(index) if index >= 0 else ""
        self._fill_side()

    def _refresh_side(self) -> None:
        if not hasattr(self, "portal_bar"):
            return
        names = self.portal_list()
        self.portal_bar.blockSignals(True)
        while self.portal_bar.count():
            self.portal_bar.removeTab(0)
        for n in names:
            self.portal_bar.addTab(n)
        i = names.index(self._side_portal) if self._side_portal in names else 0
        self.portal_bar.setCurrentIndex(i)
        self.portal_bar.blockSignals(False)
        self._side_portal = names[i] if names else ""
        self._fill_side()

    def _fill_side(self) -> None:
        while self.side_lay.count():
            w = self.side_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        portal, doc, labels = self._side_portal, self.doc(), self.library_labels()
        for where in distill.portal_containers(doc, portal) if portal else ():
            fields = [labels.get(f, f) for f in distill.container_fields(doc, where)]
            if isinstance(where, tuple):
                chip, title = ("●", "Profile builder") if where[0] == "profile" else ("▲", "Others")
                meta, note = f"{len(fields)} field{'s' if len(fields) != 1 else ''}", ""
            else:
                c = next(c for c in doc["containers"] if c["name"] == where)
                chip, title = "◆", where
                n = len(config.counted_fields(c))
                meta = f"{n} counted"
                own = (c.get("exceptions") or {}).get("levels") is not None
                lv = config.levels_of(c, doc)
                note = (f"Own levels: {len(lv)} ({' · '.join(x['name'] for x in lv)})" if own else
                        f"Levels from file: {' · '.join(x['name'] for x in lv)}" if lv else "No levels: shows k of n")
                fields = [self._field_text(c, f, labels) for f in distill.container_fields(doc, where)]
            box = _Box(chip, title, meta, fields, note)
            box.dropped.connect(lambda key, w=where: self._dropped(key, w))
            box.edit_requested.connect(lambda w=where: self.edit_container(w))
            self.side_lay.addWidget(box)
        self.side_lay.addStretch(1)
        row = self.db.get_sdis_containers()
        if self._refused or config.last_errors():
            why = self._refused or config.last_errors()[0]
            self.file_hint.setText(f"Containers file refused: {why}" + (
                f" The previous version (v{row['version']}) is still in use." if row else ""))
            self.file_hint.setStyleSheet(f"color: {BAD};")
        else:
            self.file_hint.setStyleSheet("")
            self.file_hint.setText(f"Containers file v{row['version']} · checked ✓ · saved "
                                   f"{when_text(row['updated_at'])} by {row['updated_by'] or 'admin'}" if row
                                   else "No containers file yet: the first container you add creates it.")

    @staticmethod
    def _field_text(c: Dict[str, Any], f: str, labels: Dict[str, str]) -> str:
        exc = c.get("exceptions") or {}
        marks = [m for m, on in (("period", c.get("period_field") == f), ("form", c.get("form_field") == f),
                                 ("optional", f in (exc.get("optional") or ())),
                                 (f"proves {(exc.get('proves') or {}).get(f)}", f in (exc.get("proves") or {}))) if on]
        return labels.get(f, f) + (f" ({', '.join(marks)})" if marks else "")

    def _dropped(self, key_json: str, where: distill.Where) -> None:
        dp = self._by_key.get(tuple(json.loads(key_json)))
        if dp is not None:
            self.add_to_and_report(dp, where)

    def edit_container(self, where: distill.Where) -> None:
        ed = ContainerEditor(self.doc(), where, self.known(), self.library_labels(), self.put, self)
        ed.exec()
        self._refused = ""
        self._refresh_all()

    def edit_levels(self) -> None:
        LevelsDialog(self.doc(), self.known(), self.library_labels(), self.put, self).exec()
        self._fill_side()

    def new_container(self, field: Optional[str] = None) -> Optional[str]:
        """A new dataset container needs a name and a first field (a container with none is refused)."""
        portal = self._side_portal
        if not portal:
            return None
        name, ok = QInputDialog.getText(self, "New dataset container", "Name of the container")
        name = " ".join((name or "").split())
        if not ok or not name:
            return None
        if field is None:
            labels = self.library_labels()
            if not labels:
                QMessageBox.information(self, "New dataset container",
                                        "Register a datapoint first: a container needs at least one field.")
                return None
            pick, ok = QInputDialog.getItem(self, "New dataset container", "First field",
                                            sorted(labels.values()), 0, False)
            field = field_named(pick, labels) if ok else None
            if field is None:
                return None
        try:
            self.put(config.add_container(self.doc(), name, portal, [field], **self.known()))
        except config.ConfigError as e:
            self._refused = str(e)
            QMessageBox.warning(self, "Containers file refused", str(e))
            self._fill_side()
            return None
        self._refused = ""
        self._fill_side()
        return name

    def export_containers(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export containers file", "sdis_containers.json", "JSON (*.json)")
        if path:
            config.export(self.db, Path(path))
            self._say("Containers file exported.")

    def import_containers(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import containers file", "", "JSON (*.json)")
        if not path:
            return
        try:
            config.import_file(self.db, Path(path), self.who, self.portal_list())
        except config.ConfigError as e:
            self._refused = str(e)
            QMessageBox.warning(self, "Containers file refused", str(e))
        else:
            self._refused = ""
            self._say("Containers file imported.")
        self._fill_side()

    # ── registering ──────────────────────────────────────────────────────────
    def add_menu(self, dp: Any) -> QMenu:
        menu = QMenu(self)
        portal, doc = self.portal_for(dp), self.doc()
        labels = self.library_labels()
        if not portal:
            menu.addAction("Sera can't tell which portal this page is on").setEnabled(False)
        else:
            suggested = distill.suggest_container(doc, portal, dp.suggested_class)
            if suggested is not None:
                menu.addSection("Suggested")
                self._menu_action(menu, f"★ {distill.where_label(suggested)}", dp, suggested)
            menu.addSection(portal)
            for where in distill.portal_containers(doc, portal):
                n = len(distill.container_fields(doc, where))
                self._menu_action(menu, f"{distill.where_label(where)}  ·  {n} field{'s' if n != 1 else ''}", dp, where)
            new = menu.addAction("＋ New dataset container…")
            new.triggered.connect(lambda _c=False: self.add_to_new(dp))
        menu.addSection("Not this one")
        menu.addAction("Dismiss (never suggest again)").triggered.connect(lambda _c=False: self.dismiss(dp))
        return menu

    def _menu_action(self, menu: QMenu, text: str, dp: Any, where: distill.Where) -> None:
        menu.addAction(text).triggered.connect(lambda _c=False: self.add_to_and_report(dp, where))

    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def register_selected(self) -> None:
        """The Register button: Profile / Others as suggested; a dataset datapoint picks its container."""
        dp = self.selected()
        if dp is None:
            return
        label, portal = self.shown_label(dp), self.portal_for(dp)
        if not label:
            QMessageBox.information(self, "Register", "Name it first: double-click its label.")
            return
        where = distill.suggest_container(self.doc(), portal, dp.suggested_class) if portal else None
        if where is None:
            self.add_menu(dp).exec(self.register_btn.mapToGlobal(QPoint(0, self.register_btn.height())))
            return
        text = f"Capture {label} on every PC?"
        if distill.where_class(where) != "profile":
            text += f"\n\nIt goes into {distill.where_label(where)}."
        if self._ask("Register", text):
            self.add_to_and_report(dp, where)

    def add_to_new(self, dp: Any) -> None:
        """＋ New dataset container…: the datapoint becomes the first field of a container of its own."""
        label, portal = self.shown_label(dp), self.portal_for(dp)
        if not (label and portal):
            QMessageBox.information(self, "Register", "Name it first: double-click its label.")
            return
        name, ok = QInputDialog.getText(self, "New dataset container", "Name of the container")
        name = " ".join((name or "").split())
        if not ok or not name:
            return
        mems = (self.state or {}).get("memories") or []
        try:
            field = register.register_field(self.db, dp, mems, portal, field=self._field_for(dp), cls="dataset",
                                            created_by=self.who)["field"]
            self.put(config.add_container(self.doc(), name, portal, [field], **self.known()))
        except (register.NotRegistrable, config.ConfigError) as e:
            self._refused = str(e)
            self._say(f"Not registered: {e}", bad=True)
            self._fill_side()
            return
        self._refused = ""
        self._finish_add(dp, name, field)
        self._say(f"{label} is in {name}.")

    def add_to_and_report(self, dp: Any, where: distill.Where) -> None:
        ok, message = self.add_to(dp, where)
        self._say(message, bad=not ok)
        if not ok:
            QMessageBox.warning(self, "Register", message)

    def _field_for(self, dp: Any) -> Optional[str]:
        """An existing library field the datapoint's label stands for (the same field is reused)."""
        return field_named(self.shown_label(dp), self.library_labels())

    def add_to(self, dp: Any, where: distill.Where) -> tuple:
        """Registers the datapoint as a field of sdis_mcl with its SGT spec (Part Q; a dataset / Others
        field's spec feeds SGT's containers, W4-7) and puts it in `where` (D7). Returns (ok, a
        counts-only message)."""
        label, portal = self.shown_label(dp), self.portal_for(dp)
        if not label:
            return False, "Name the datapoint first: double-click its label."
        if not portal:
            return False, "Sera can't tell which portal this page is on."
        cls = distill.where_class(where)
        mems = (self.state or {}).get("memories") or []
        try:
            try:
                name = register.register_field(self.db, dp, mems, portal, field=self._field_for(dp), cls=cls,
                                               created_by=self.who)["field"]
            except register.NotRegistrable as e:
                if str(e) != register.MIXED_SHAPES or not self._ask(
                        "Register", f"{label}: its values have no common shape.\n\nRegister it anyway? Sera will "
                                    "take any one-line value beside the label, so check what it captures."):
                    raise
                name = register.register_field(self.db, dp, mems, portal, field=self._field_for(dp), cls=cls,
                                               created_by=self.who, loose=True)["field"]
            self.put(config.add_field(self.doc(), where, name, **self.known()))
        except (register.NotRegistrable, config.ConfigError) as e:
            return False, f"Not registered: {e}"
        self._finish_add(dp, where, name)
        return True, f"{label} is in {distill.where_label(where)}."

    def _finish_add(self, dp: Any, where: distill.Where, field: str) -> None:
        self.db.set_sdis_decision(distill.signature(distill.REG, dp.key), distill.REGISTERED, field)
        self.decided.registered[dp.key] = field
        cls = {"profile": "profile", "dataset": "dataset", "others": "info"}[distill.where_class(where)]
        if cls != dp.suggested_class:
            self.db.set_sdis_decision(distill.signature(distill.MOVE, dp.key), cls, "")
            self.decided.moves[dp.key] = cls
        self._decided_changed()

    # ── Find datapoints ──────────────────────────────────────────────────────
    def find_datapoints(self) -> None:
        """Opens the application-modal loading dialog and starts the mining child; the dialog never
        starts mining by itself."""
        if self._loading_dlg is not None:
            return
        self._flush()                           # the child reads the state file: it must be up to date
        try:
            captures = distill.stage_default()
        except OSError:
            self._say("The captures could not be gathered for mining.", bad=True)
            return
        dlg = SdisLoadingDialog(self, self._client_factory)
        self._loading_dlg = dlg
        dlg.finished.connect(lambda _code, d=dlg: self._mined(d))
        dlg.open()
        dlg.start(captures, self.path())

    def _mined(self, dlg: SdisLoadingDialog) -> None:
        self._loading_dlg = None
        result = dlg.result or {}
        if result.get("result") == "error":
            self._say(result.get("message") or "Mining failed.", bad=True)
        else:
            self.reload()
            self._say("Cancelled: the work finished so far is saved." if result.get("cancelled") or
                      result.get("result") == "cancelled" else
                      f"Found {len([d for d in self.dps if d.key not in self.decided.dismissed])} datapoints.")
        dlg.deleteLater()

    def closeEvent(self, event: Any) -> None:
        if self._loading_dlg is not None:
            event.ignore()
            return
        self._flush()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._flush()
        super().done(result)
