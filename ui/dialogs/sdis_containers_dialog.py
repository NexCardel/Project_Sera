"""
ui/dialogs/sdis_containers_dialog.py - the container editor and the levels editor (SDIS Parts L + S)
====================================================================================================
Opened from the Distill dialog's side panel. Every change goes through core/sdis/config.py's editing
helpers, which return a checked copy or raise ConfigError: a refused edit shows the check's reason
in words and the document stays at its last good state. Save writes the synced office row through
the `put` function the Distill dialog passes (db.put_sdis_containers).
"""

import copy
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QStringListModel, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.sdis import config, distill

BG, PANEL, TEXT, MUTED, LINE, GREEN = "#202020", "#171717", "#F8F5F2", "#9a9ca3", "#444444", "#2E9B5F"
OK, BAD, AMBER = "#3fd17f", "#e5534b", "#f0a830"

STYLE = f"""
QDialog {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QLabel {{ color: {TEXT}; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#sect {{ color: {MUTED}; font-size: 11px; }}
QLineEdit, QComboBox, QTableWidget {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; border-radius: 6px; padding: 3px 6px; }}
QTableWidget {{ gridline-color: #2c2c30; padding: 0; }}
QHeaderView::section {{ background: {PANEL}; color: {OK}; border: 0; border-bottom: 2px solid #173d28; padding: 6px; font-weight: 600; }}
QPushButton {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {GREEN}; }}
QPushButton:disabled {{ color: #6c6e75; }}
QPushButton#primary {{ background: {GREEN}; border-color: {GREEN}; color: #fff; font-weight: 600; }}
QPushButton#primary:disabled {{ background: #1d3d2c; border-color: #1d3d2c; color: #6c8c7a; }}
QPushButton#danger {{ background: #3d1a18; border-color: #6b2a26; color: #ffb4ae; }}
QCheckBox, QRadioButton {{ color: {TEXT}; }}
"""

NOT_SET = "—"


def _label(text: str, name: str = "") -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(True)
    return lab


def _center(widget: QWidget) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setAlignment(Qt.AlignCenter)
    lay.addWidget(widget)
    return box


def field_completer(parent: QWidget, labels: Dict[str, str]) -> QCompleter:
    """Type-ahead over the sdis_mcl library: the users' labels first, then names."""
    comp = QCompleter(parent)
    comp.setModel(QStringListModel(sorted({v or k for k, v in labels.items()}), comp))
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    comp.setFilterMode(Qt.MatchContains)
    return comp


def field_named(text: str, labels: Dict[str, str]) -> Optional[str]:
    """The library field a typed label or name stands for."""
    t = " ".join((text or "").split()).lower()
    for name, label in labels.items():
        if t and t in (name.lower(), (label or "").lower()):
            return name
    return None


class LevelsTable(QWidget):
    """The levels list: name, reached when captured (1 / 51% / all), also needs (fields), tracker status."""
    changed = Signal()
    HEAD = ("Level", "Reached when captured", "Also needs", "Tracker status")

    def __init__(self, levels: List[Dict[str, Any]], level_map: Dict[str, str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._building = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.HEAD))
        self.table.setHorizontalHeaderLabels(list(self.HEAD))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(lambda _i: self._edited())
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, fn in (("＋ Level", self.add_level), ("✕ Remove", self.remove_level),
                         ("▲", lambda: self.move(-1)), ("▼", lambda: self.move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.set_value(levels, level_map)

    def _edited(self) -> None:
        if not self._building:
            self.changed.emit()

    def set_value(self, levels: List[Dict[str, Any]], level_map: Dict[str, str]) -> None:
        self._building = True
        self.table.setRowCount(0)
        for lv in levels:
            self._add_row(lv.get("name", ""), lv.get("when") or {}, (level_map or {}).get(lv.get("name", ""), ""))
        self._building = False

    def _add_row(self, name: str, when: Dict[str, Any], status: str) -> None:
        from core.sgt.sgt_toolbox import SUBMIT_LEVELS
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        self.table.setItem(r, 1, QTableWidgetItem("" if "captured" not in when else str(when["captured"])))
        self.table.setItem(r, 2, QTableWidgetItem(", ".join(when.get("fields") or ())))
        combo = QComboBox()
        combo.addItems([NOT_SET, *SUBMIT_LEVELS])
        combo.setCurrentIndex(max(0, combo.findText(status)))
        combo.currentIndexChanged.connect(lambda _i: self._edited())
        self.table.setCellWidget(r, 3, combo)

    def add_level(self) -> None:
        self._add_row("", {"captured": "all"}, "")
        self._edited()

    def remove_level(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._edited()

    def move(self, step: int) -> None:
        r = self.table.currentRow()
        if r < 0 or not 0 <= r + step < self.table.rowCount():
            return
        levels, lmap = self.value()
        levels[r], levels[r + step] = levels[r + step], levels[r]
        self.set_value(levels, lmap)
        self.table.selectRow(r + step)
        self._edited()

    def value(self) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        levels: List[Dict[str, Any]] = []
        lmap: Dict[str, str] = {}
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text().strip()
            when: Dict[str, Any] = {}
            cap = self.table.item(r, 1).text().strip()
            if cap:
                when["captured"] = int(cap) if re.fullmatch(r"\d+", cap) else cap
            fields = [f.strip() for f in self.table.item(r, 2).text().split(",") if f.strip()]
            if fields:
                when["fields"] = fields
            levels.append({"name": name, "when": when})
            status = self.table.cellWidget(r, 3).currentText()
            if status != NOT_SET and name:
                lmap[name] = status
        return levels, lmap


class _EditorBase(QDialog):
    """Shared by the editors: a working copy of the document, a status line, Save through `put`."""

    def __init__(self, doc: Dict[str, Any], known: Dict[str, Any], labels: Dict[str, str],
                 put: Callable[[Dict[str, Any]], int], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.doc = copy.deepcopy(doc)
        self.known, self.labels, self.put = known, labels, put
        self.saved: Optional[int] = None
        self.status = _label("", "hint")
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)

    def say(self, text: str, ok: bool = True) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {OK if ok else BAD};")

    def apply(self, fn: Callable[..., Dict[str, Any]], *args: Any, **kw: Any) -> bool:
        """One edit through a config helper. Refused: the reason is shown and the document stays."""
        try:
            self.doc = fn(self.doc, *args, **kw, **self.known)
        except config.ConfigError as e:
            self.say(str(e), ok=False)
            QTimer.singleShot(0, self.refill)
            return False
        self.say("✓ All checks pass")
        QTimer.singleShot(0, self.refill)
        return True

    def refill(self) -> None:
        raise NotImplementedError

    def save(self) -> None:
        try:
            self.saved = self.put(self.doc)
        except config.ConfigError as e:
            self.say(str(e), ok=False)
            return
        self.accept()

    def _footer(self, *left: QWidget) -> QHBoxLayout:
        foot = QHBoxLayout()
        for w in left:
            foot.addWidget(w)
        foot.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        foot.addWidget(self.save_btn)
        return foot


class ContainerEditor(_EditorBase):
    """A dataset container (where = its name): name, fields with period / form / optional / proves,
    its own levels or the file's. A Profile builder or Others (where = (kind, portal)): its fields."""

    def __init__(self, doc: Dict[str, Any], where: distill.Where, known: Dict[str, Any], labels: Dict[str, str],
                 put: Callable[[Dict[str, Any]], int], parent: Optional[QWidget] = None) -> None:
        super().__init__(doc, known, labels, put, parent)
        self.where = where
        self.name = where if isinstance(where, str) else ""
        self.deleted = False
        self.setWindowTitle("Edit container" if self.name else "Edit " + distill.where_label(where))
        self.resize(1000 if self.name else 520, 560 if self.name else 420)
        outer = QVBoxLayout(self)

        body = QHBoxLayout()
        left = QVBoxLayout()
        if self.name:
            head = QHBoxLayout()
            head.addWidget(QLabel("Name"))
            self.name_edit = QLineEdit(self.name)
            self.name_edit.editingFinished.connect(self._rename)
            head.addWidget(self.name_edit, 1)
            head.addWidget(QLabel("Portal"))
            self.portal_label = QLabel(self._container().get("portal", ""))
            head.addWidget(self.portal_label)
            left.addLayout(head)
        left.addWidget(_label("FIELDS (FROM sdis_mcl)", "sect"))
        cols = ("Field", "Period", "Form", "Optional", "Proves", "") if self.name else ("Field", "")
        self.fields = QTableWidget(0, len(cols))
        self.fields.setHorizontalHeaderLabels(list(cols))
        self.fields.verticalHeader().setVisible(False)
        self.fields.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        left.addWidget(self.fields, 1)
        add = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("＋ Add field (type-ahead over the field library)")
        self.add_edit.setCompleter(field_completer(self, labels))
        self.add_edit.returnPressed.connect(self._add_field)
        add.addWidget(self.add_edit, 1)
        btn = QPushButton("Add")
        btn.clicked.connect(self._add_field)
        add.addWidget(btn)
        left.addLayout(add)
        if self.name:
            left.addWidget(_label("No field marked Form: the form is the container's name.", "hint"))
        body.addLayout(left, 11)

        if self.name:
            right = QVBoxLayout()
            right.addWidget(_label("LEVELS", "sect"))
            modes = QHBoxLayout()
            self.use_file = QRadioButton("Use the file's levels")
            self.use_own = QRadioButton("This container's own")
            self.use_file.toggled.connect(self._levels_mode)
            modes.addWidget(self.use_file)
            modes.addWidget(self.use_own)
            modes.addStretch(1)
            right.addLayout(modes)
            self.levels = LevelsTable([], {})
            self.levels.changed.connect(self._levels_edited)
            right.addWidget(self.levels, 1)
            self.preview = _label("")
            right.addWidget(self.preview)
            body.addLayout(right, 10)
        outer.addLayout(body, 1)
        outer.addWidget(self.status)

        if self.name:
            delete = QPushButton("Delete container")
            delete.setObjectName("danger")
            delete.clicked.connect(self._delete)
            outer.addLayout(self._footer(delete))
            self._load_levels()
        else:
            outer.addLayout(self._footer())
        self.refill()
        self.say("✓ All checks pass")

    def _container(self) -> Dict[str, Any]:
        for c in self.doc.get("containers") or ():
            if c.get("name") == self.name:
                return c
        return {}

    def _label_of(self, field: str) -> str:
        return self.labels.get(field) or field

    # ── fields ──
    def refill(self) -> None:
        c = self._container()
        names = distill.container_fields(self.doc, self.where)
        exc = c.get("exceptions") or {}
        level_names = [lv["name"] for lv in config.levels_of(c, self.doc)] if self.name else []
        self.fields.setRowCount(0)
        for f in names:
            r = self.fields.rowCount()
            self.fields.insertRow(r)
            self.fields.setItem(r, 0, QTableWidgetItem(self._label_of(f)))
            self.fields.item(r, 0).setFlags(Qt.ItemIsEnabled)
            if self.name:
                for col, key in ((1, "period"), (2, "form")):
                    box = QCheckBox()
                    box.setChecked(c.get(key + "_field") == f)
                    box.clicked.connect(lambda on, f=f, key=key: self.apply(config.mark_key, self.name, f if on else None, key=key))
                    self.fields.setCellWidget(r, col, _center(box))
                opt = QCheckBox()
                opt.setChecked(f in (exc.get("optional") or ()))
                opt.clicked.connect(lambda on, f=f: self.apply(config.set_exception, self.name, "optional", f, bool(on)))
                self.fields.setCellWidget(r, 3, _center(opt))
                combo = QComboBox()
                combo.addItems([NOT_SET, *level_names])
                combo.setCurrentIndex(max(0, combo.findText((exc.get("proves") or {}).get(f, ""))))
                combo.activated.connect(lambda _i, f=f, combo=combo: self.apply(
                    config.set_exception, self.name, "proves", f, None if combo.currentText() == NOT_SET else combo.currentText()))
                self.fields.setCellWidget(r, 4, combo)
            gone = QPushButton("✕")
            gone.clicked.connect(lambda _c=False, f=f: self.apply(config.remove_field, self.where, f))
            self.fields.setCellWidget(r, 5 if self.name else 1, gone)
        if self.name:
            self._show_preview()

    def _add_field(self) -> None:
        field = field_named(self.add_edit.text(), self.labels)
        if field is None:
            self.say("No field with that name in the field library.", ok=False)
            return
        if self.apply(config.add_field, self.where, field):
            self.add_edit.clear()

    def _rename(self) -> None:
        new = " ".join(self.name_edit.text().split())
        if new and new != self.name and self.apply(config.rename_container, self.name, new):
            self.name = self.where = new
        self.name_edit.setText(self.name)

    # ── levels ──
    def _load_levels(self) -> None:
        own = (self._container().get("exceptions") or {}).get("levels") is not None
        for b in (self.use_file, self.use_own):
            b.blockSignals(True)
        self.use_own.setChecked(own)
        self.use_file.setChecked(not own)
        for b in (self.use_file, self.use_own):
            b.blockSignals(False)
        c = self._container()
        self.levels.set_value(config.levels_of(c, self.doc), config.level_map_of(c, self.doc))
        self.levels.setEnabled(own)

    def _levels_mode(self, file_on: bool) -> None:
        if file_on:
            ok = self.apply(config.set_levels, None, None, container=self.name)
        else:
            ok = self.apply(config.set_levels, copy.deepcopy(self.doc.get("levels") or []),
                            dict(self.doc.get("level_map") or {}), container=self.name)
        if ok:
            self._load_levels()
        else:
            self.use_file.blockSignals(True)
            self.use_own.blockSignals(True)
            self.use_file.setChecked(not file_on)
            self.use_own.setChecked(file_on)
            self.use_file.blockSignals(False)
            self.use_own.blockSignals(False)

    def _levels_edited(self) -> None:
        levels, lmap = self.levels.value()
        ok = self.apply(config.set_levels, levels, lmap, container=self.name)
        self.save_btn.setEnabled(ok)

    def _show_preview(self) -> None:
        c = self._container()
        counted = config.counted_fields(c)
        n = len(counted)
        steps = []
        for k in range(n + 1):
            lv = config.level_for(c, counted[:k], self.doc) if k else None
            steps.append(f"{k} captured: {lv or ('Not started' if k == 0 else 'no level')}")
        proves = ", ".join(f"{self._label_of(f)} alone → {lv}" for f, lv in ((c.get("exceptions") or {}).get("proves") or {}).items())
        self.preview.setText(f"Preview: {n} counted field{'s' if n != 1 else ''}\n" + " · ".join(steps)
                             + (f"\n{proves}. A level never moves down." if proves else "\nA level never moves down."))

    def _delete(self) -> None:
        if QMessageBox.question(self, "Delete container", f"Delete {self.name}? Its fields stay in the field library.") \
                != QMessageBox.Yes:
            return
        if self.apply(config.delete_container, self.name):
            self.deleted = True
            self.save()


class LevelsDialog(_EditorBase):
    """The file-wide levels and their tracker statuses."""

    def __init__(self, doc: Dict[str, Any], known: Dict[str, Any], labels: Dict[str, str],
                 put: Callable[[Dict[str, Any]], int], parent: Optional[QWidget] = None) -> None:
        super().__init__(doc, known, labels, put, parent)
        self.setWindowTitle("Levels")
        self.resize(720, 420)
        outer = QVBoxLayout(self)
        outer.addWidget(_label("Levels for every container that has no levels of its own. A level is reached "
                               "when the captured fields satisfy its rule; it maps to a status of the tracker's "
                               "submit ladder.", "hint"))
        self.levels = LevelsTable(self.doc.get("levels") or [], self.doc.get("level_map") or {})
        self.levels.changed.connect(self._edited)
        outer.addWidget(self.levels, 1)
        outer.addWidget(self.status)
        outer.addLayout(self._footer())

    def refill(self) -> None:
        pass

    def _edited(self) -> None:
        levels, lmap = self.levels.value()
        self.save_btn.setEnabled(self.apply(config.set_levels, levels, lmap))
