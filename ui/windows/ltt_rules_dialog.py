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
    QDateEdit, QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from core.ltt import feed
from core.ltt.rules import (
    COVERS_CHOICES, EVERY_CHOICES, FormRule, load_rules, save_rules, validate,
)
from ui.components.kit import (
    Banner, Card, DialogFrame, FieldLabel, Input, LabelledAction, Select, show_message,
)
from ui.utils.tokens import TOKENS


class LttRulesDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.app_dir = db.app_dir
        self.setWindowTitle("LTT form rules")
        self.setMinimumSize(880, 600)

        self._rules = load_rules(self.app_dir)
        self._current = -1
        self._loading = False
        self._clients = {}            # PAN -> {"name", "history"}
        self._build()
        self._load_clients()
        self._refresh_form_list(select=0 if self._rules else -1)

    # ---------------- layout ----------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("LTT form rules", "Return forms the office tracks, and who files each one",
                            self, icon="mdi.calendar-check-outline", full=True, scroll=True)
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        frame.add_widget(Banner(
            "Choose the return forms the office tracks. For each one, say how often it is filed "
            "and give one real due date; Sera works out the current month from that. "
            "The dates below are only suggestions: please check them.", "i"))

        wrap = QWidget()
        body = QHBoxLayout(wrap)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(TOKENS["s_4"])
        frame.add_widget(wrap, 1)

        left = Card("Return forms")
        self.lst_forms = QListWidget()
        self.lst_forms.setObjectName("KitList")
        self.lst_forms.setMinimumHeight(120)
        self.lst_forms.currentRowChanged.connect(self._on_form_selected)
        left.add_widget(self.lst_forms)
        row_w = QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS["s_2"])
        btn_add = LabelledAction("Add form", variant="secondary", icon="mdi.plus")
        btn_add.clicked.connect(self._add_form)
        btn_del = LabelledAction("Remove", variant="secondary", icon="mdi.delete-outline")
        btn_del.clicked.connect(self._remove_form)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch(1)
        left.add_widget(row_w)
        body.addWidget(left)

        self.editor = Card("Selected form")
        ed = self.editor
        ed.add_widget(FieldLabel("Form name (as it appears on the portal, e.g. GSTR-3B)"))
        self.txt_form = Input()
        self.txt_form.textChanged.connect(self._on_edited)
        ed.add_widget(self.txt_form)

        ed.add_widget(FieldLabel("How often is it filed?"))
        self.cmb_every = Select(items=EVERY_CHOICES)
        self.cmb_every.currentIndexChanged.connect(self._on_edited)
        ed.add_widget(self.cmb_every)

        ed.add_widget(FieldLabel("Any one real due date for this form (the others are worked out from it)"))
        self.dt_due = QDateEdit()
        self.dt_due.setCalendarPopup(True)
        self.dt_due.setDisplayFormat("dd MMM yyyy")
        self.dt_due.dateChanged.connect(self._on_edited)
        ed.add_widget(self.dt_due)

        ed.add_widget(FieldLabel("Which month does a due date file for?"))
        self.cmb_covers = Select()
        for key, text in COVERS_CHOICES:
            self.cmb_covers.addItem(text, key)
        self.cmb_covers.currentIndexChanged.connect(self._on_edited)
        ed.add_widget(self.cmb_covers)

        self.lbl_preview = Banner("", "i")
        ed.add_widget(self.lbl_preview)

        ed.add_widget(FieldLabel("Who files this form? (clients already seen filing it are ticked)"))
        self.txt_find = Input(placeholder="Search clients by name or PAN…", icon="mdi.magnify")
        self.txt_find.textChanged.connect(self._filter_clients)
        ed.add_widget(self.txt_find)
        self.lst_clients = QListWidget()
        self.lst_clients.setObjectName("KitList")
        self.lst_clients.setMinimumHeight(160)
        self.lst_clients.itemChanged.connect(self._on_client_toggled)
        ed.add_widget(self.lst_clients)
        body.addWidget(self.editor, 1)

        self.lbl_error = Banner("", "r")
        frame.add_widget(self.lbl_error)
        self._set_note(self.lbl_preview, "")
        self._set_note(self.lbl_error, "")

        btn_cancel = frame.add_action("Cancel", variant="secondary")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = frame.add_action("Save and update sheet", variant="primary", icon="mdi.content-save-outline")
        self.btn_save.clicked.connect(self._save)

    @staticmethod
    def _set_note(banner, text):
        banner.set_text(text)
        banner.setVisible(bool(text))

    # ---------------- data ----------------
    def _load_clients(self):
        try:
            self._clients = feed.clients_from(self.db.get_srpf_containers(limit=1_000_000, slim=True))
        except Exception as e:
            self._clients = {}
            self._set_note(self.lbl_error, f"Could not read the client list: {e}")

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
            self._set_note(self.lbl_preview, "")

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
            self._set_note(self.lbl_preview, r.problem())
            return
        loc = r.engine().locate(date.today())
        cur = loc["current"]
        self._set_note(self.lbl_preview,
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
            self._set_note(self.lbl_error, problem)
            return
        try:
            save_rules(self.app_dir, self._rules)
            path, n = feed.export_feed(self.db, self.app_dir)
        except Exception as e:
            show_message(self, "LTT form rules", f"Could not save: {e}", "warning")
            return
        self.accept()
