"""
ltt_rules_dialog.py
-------------------
"LTT form rules": the plain-language setup behind the LTT tracker sheet. For each return form
(GSTR-3B, ITR, ...) the office says how often it is filed, gives one real due date, says which
month it files for, and ticks the clients who file it. Clients already seen filing that form are
ticked for them. The dialog also rewrites ltt_feed.csv so the sheet is current when it closes.
"""

from datetime import date

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from core.ltt import feed
from core.ltt.rules import (
    COVERS_CHOICES, EVERY_CHOICES, FormRule, load_rules, save_rules, validate,
)


class LttRulesDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.app_dir = db.app_dir
        self.setWindowTitle("LTT form rules")
        self.setMinimumSize(880, 600)
        self.setStyleSheet("""
            QDialog { background-color: #0A0A0A; }
            QLabel { color: #C9D1D9; font-size: 12.5px; }
            QLabel#title { color: #F0F6FC; font-size: 17px; font-weight: 700; }
            QLabel#hint { color: #8B949E; font-size: 12px; }
            QLabel#preview { color: #4CF9B7; font-size: 12.5px; font-weight: 600; }
            QLabel#error { color: #FF6B6B; font-size: 12px; }
            QLineEdit, QDateEdit, QComboBox { background-color: #0D1117; border: 1px solid #30363D;
                border-radius: 6px; color: #F0F6FC; padding: 5px 8px; font-size: 12.5px; }
            QLineEdit:focus, QDateEdit:focus, QComboBox:focus { border: 1px solid #2E9B5F; }
            QComboBox QAbstractItemView { background-color: #0D1117; color: #F0F6FC;
                selection-background-color: #2E9B5F; }
            QListWidget { background-color: #0F0F0F; border: 1px solid #262626; border-radius: 8px;
                color: #E6EDF3; font-size: 12.5px; outline: 0; }
            QListWidget::item { padding: 5px 8px; }
            QListWidget::item:selected { background-color: #1B2A22; color: #E6EDF3; }
            QCheckBox { color: #C9D1D9; font-size: 12px; }
            QPushButton { background-color: #1F1F1F; color: #E6EDF3; border: 1px solid #3A3A3A;
                border-radius: 8px; padding: 8px 16px; font-size: 13px; }
            QPushButton:hover { background-color: #2A2A2A; }
            QPushButton#primary { background-color: #2E9B5F; color: #FFFFFF; font-weight: 700; border: none; }
            QPushButton#primary:hover { background-color: #36B06D; }
        """)

        self._rules = load_rules(self.app_dir)
        self._current = -1
        self._loading = False
        self._clients = {}            # PAN -> {"name", "history"}
        self._build()
        self._load_clients()
        self._refresh_form_list(select=0 if self._rules else -1)

    # ---------------- layout ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        title = QLabel("LTT form rules")
        title.setObjectName("title")
        hint = QLabel("Choose the return forms the office tracks. For each one, say how often it is filed "
                      "and give one real due date; Sera works out the current month from that. "
                      "The dates below are only suggestions: please check them.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, stretch=1)

        left = QVBoxLayout()
        left.addWidget(QLabel("Return forms"))
        self.lst_forms = QListWidget()
        self.lst_forms.setFixedWidth(200)
        self.lst_forms.currentRowChanged.connect(self._on_form_selected)
        left.addWidget(self.lst_forms, stretch=1)
        row = QHBoxLayout()
        btn_add = QPushButton("Add form")
        btn_add.clicked.connect(self._add_form)
        btn_del = QPushButton("Remove")
        btn_del.clicked.connect(self._remove_form)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        left.addLayout(row)
        body.addLayout(left)

        self.editor = QWidget()
        ed = QVBoxLayout(self.editor)
        ed.setContentsMargins(0, 0, 0, 0)
        ed.setSpacing(8)
        ed.addWidget(QLabel("Form name (as it appears on the portal, e.g. GSTR-3B)"))
        self.txt_form = QLineEdit()
        self.txt_form.textChanged.connect(self._on_edited)
        ed.addWidget(self.txt_form)

        ed.addWidget(QLabel("How often is it filed?"))
        self.cmb_every = QComboBox()
        self.cmb_every.addItems(EVERY_CHOICES)
        self.cmb_every.currentIndexChanged.connect(self._on_edited)
        ed.addWidget(self.cmb_every)

        ed.addWidget(QLabel("Any one real due date for this form (the others are worked out from it)"))
        self.dt_due = QDateEdit()
        self.dt_due.setCalendarPopup(True)
        self.dt_due.setDisplayFormat("dd MMM yyyy")
        self.dt_due.dateChanged.connect(self._on_edited)
        ed.addWidget(self.dt_due)

        ed.addWidget(QLabel("Which month does a due date file for?"))
        self.cmb_covers = QComboBox()
        for key, text in COVERS_CHOICES:
            self.cmb_covers.addItem(text, key)
        self.cmb_covers.currentIndexChanged.connect(self._on_edited)
        ed.addWidget(self.cmb_covers)

        self.lbl_preview = QLabel("")
        self.lbl_preview.setObjectName("preview")
        self.lbl_preview.setWordWrap(True)
        ed.addWidget(self.lbl_preview)

        ed.addWidget(QLabel("Who files this form? (clients already seen filing it are ticked)"))
        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("Search clients by name or PAN…")
        self.txt_find.textChanged.connect(self._filter_clients)
        ed.addWidget(self.txt_find)
        self.lst_clients = QListWidget()
        self.lst_clients.itemChanged.connect(self._on_client_toggled)
        ed.addWidget(self.lst_clients, stretch=1)
        body.addWidget(self.editor, stretch=1)

        self.lbl_error = QLabel("")
        self.lbl_error.setObjectName("error")
        self.lbl_error.setWordWrap(True)
        root.addWidget(self.lbl_error)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Save and update sheet")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save)
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_save)
        root.addLayout(btns)

    # ---------------- data ----------------
    def _load_clients(self):
        try:
            self._clients = feed.clients_from(self.db.get_srpf_containers(limit=1_000_000, slim=True))
        except Exception as e:
            self._clients = {}
            self.lbl_error.setText(f"Could not read the client list: {e}")

    def _refresh_form_list(self, select=-1):
        self.lst_forms.blockSignals(True)
        self.lst_forms.clear()
        for r in self._rules:
            self.lst_forms.addItem(r.form or "(new form)")
        self.lst_forms.blockSignals(False)
        self._current = -1
        if select >= 0 and self._rules:
            self.lst_forms.setCurrentRow(min(select, len(self._rules) - 1))
        else:
            self.editor.setEnabled(False)
            self.lbl_preview.setText("")

    def _on_form_selected(self, row):
        self._commit_current()
        self._current = row
        self.editor.setEnabled(0 <= row < len(self._rules))
        if not self.editor.isEnabled():
            return
        r = self._rules[row]
        self._loading = True
        self.txt_form.setText(r.form)
        self.cmb_every.setCurrentText(next((c for c in EVERY_CHOICES if c.lower() == r.every.lower()), "Monthly"))
        d = QDate.fromString(r.deadline, "yyyy-MM-dd")
        self.dt_due.setDate(d if d.isValid() else QDate.currentDate())
        self.cmb_covers.setCurrentIndex(max(0, self.cmb_covers.findData(r.covers)))
        self._fill_clients(r)
        self._loading = False
        self._update_preview()

    def _fill_clients(self, rule):
        owed = set(feed.forms_for(rule, self._clients))
        self.lst_clients.blockSignals(True)
        self.lst_clients.clear()
        for pan, c in sorted(self._clients.items(), key=lambda kv: (kv[1]["name"].lower(), kv[0])):
            it = QListWidgetItem(f"{c['name'] or '(no name)'}   —   {pan}")
            it.setData(Qt.UserRole, pan)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if pan in owed else Qt.Unchecked)
            self.lst_clients.addItem(it)
        self.lst_clients.blockSignals(False)
        self._filter_clients()

    def _filter_clients(self):
        q = self.txt_find.text().strip().lower()
        for i in range(self.lst_clients.count()):
            it = self.lst_clients.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _commit_current(self):
        """Writes the editor's fields back to the rule being edited."""
        if not (0 <= self._current < len(self._rules)):
            return
        r = self._rules[self._current]
        r.form = self.txt_form.text().strip()
        r.every = self.cmb_every.currentText()
        r.deadline = self.dt_due.date().toString("yyyy-MM-dd")
        r.covers = self.cmb_covers.currentData() or "month_before"
        item = self.lst_forms.item(self._current)
        if item is not None:
            item.setText(r.form or "(new form)")

    def _on_edited(self, *_):
        if self._loading:
            return
        self._commit_current()
        self._update_preview()
        # who-files list depends on the form's name: refresh the ticks for the new name
        if 0 <= self._current < len(self._rules):
            self._fill_clients(self._rules[self._current])

    def _on_client_toggled(self, item):
        if self._loading or not (0 <= self._current < len(self._rules)):
            return
        r = self._rules[self._current]
        pan = item.data(Qt.UserRole)
        seen = pan in set(feed.forms_for(FormRule(r.form), self._clients))
        ticked = item.checkState() == Qt.Checked
        for lst in (r.include, r.exclude):
            while pan in lst:
                lst.remove(pan)
        if ticked and not seen:
            r.include.append(pan)
        elif not ticked and seen:
            r.exclude.append(pan)

    def _update_preview(self):
        if not (0 <= self._current < len(self._rules)):
            return
        r = self._rules[self._current]
        if r.problem():
            self.lbl_preview.setText(r.problem())
            return
        loc = r.engine().locate(date.today())
        cur = loc["current"]
        self.lbl_preview.setText(
            f"Right now: {cur.filing}  (due {cur.end:%d %b %Y}).   "
            f"Last: {loc['previous'][0].filing}.   Next: {loc['upcoming'][0].filing}.")

    # ---------------- add / remove / save ----------------
    def _add_form(self):
        self._commit_current()
        self._rules.append(FormRule("", "Monthly", date.today().isoformat(), "month_before"))
        self._refresh_form_list(select=len(self._rules) - 1)
        self.txt_form.setFocus()

    def _remove_form(self):
        if 0 <= self._current < len(self._rules):
            del self._rules[self._current]
            self._refresh_form_list(select=max(0, self._current - 1))

    def _save(self):
        self._commit_current()
        problem = validate(self._rules)
        if problem:
            self.lbl_error.setText(problem)
            return
        try:
            save_rules(self.app_dir, self._rules)
            path, n = feed.export_feed(self.db, self.app_dir)
        except Exception as e:
            QMessageBox.warning(self, "LTT form rules", f"Could not save: {e}")
            return
        self.accept()
