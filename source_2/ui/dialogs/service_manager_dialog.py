"""
service_manager_dialog.py
-----------------------------
Admin Mode -> "Manage Services". Maps login endpoints to MCL columns.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class ServiceEditDialog(QDialog):
    def __init__(self, db, parent=None, service_data=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Service Configuration")
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit(service_data["name"] if service_data else "")
        form.addRow("Service Name:", self.name_input)

        self.url_input = QLineEdit(service_data["login_page_link"] if service_data else "")
        form.addRow("Login URL:", self.url_input)

        mcl = self.db.get_mcl_columns()
        self.uid_combo = QComboBox()
        self.pwd_combo = QComboBox()
        self.uid_combo.addItem("-- Select --", None)
        self.pwd_combo.addItem("-- Select --", None)
        
        for c in mcl:
            self.uid_combo.addItem(c["label"], c["id"])
            self.pwd_combo.addItem(c["label"], c["id"])

        if service_data:
            idx_uid = self.uid_combo.findData(service_data["userid_column_id"])
            idx_pwd = self.pwd_combo.findData(service_data["password_column_id"])
            self.uid_combo.setCurrentIndex(max(idx_uid, 0))
            self.pwd_combo.setCurrentIndex(max(idx_pwd, 0))

        form.addRow("User ID Column:", self.uid_combo)
        form.addRow("Password Column:", self.pwd_combo)

        self.uid_sel = QLineEdit(service_data.get("username_selector", "") if service_data else "")
        self.pwd_sel = QLineEdit(service_data.get("password_selector", "") if service_data else "")
        form.addRow("Username Selector:", self.uid_sel)
        form.addRow("Password Selector:", self.pwd_sel)

        self.success_sel = QLineEdit(service_data.get("success_selector", "") if service_data else "")
        self.success_sel.setPlaceholderText("e.g. div.success-msg, or text:Submitted Successfully!")
        self.arn_sel = QLineEdit(service_data.get("arn_selector", "") if service_data else "")
        self.arn_sel.setPlaceholderText("e.g. #arn-value, or text:Transaction ID")
        form.addRow("Success Msg Selector:", self.success_sel)
        form.addRow("ARN Field Selector:", self.arn_sel)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Automated (Playwright)", "automated")
        self.mode_combo.addItem("Manual (Clipboard Copy)", "manual")
        self.mode_combo.addItem("Extension (Browser Tab)", "extension")
        
        if service_data:
            idx_mode = self.mode_combo.findData(service_data["automation_mode"])
            self.mode_combo.setCurrentIndex(max(idx_mode, 0))

        form.addRow("Automation Mode:", self.mode_combo)

        self.ext_flow_combo = QComboBox()
        self.ext_flow_combo.addItem("Two-Step (Username first, then Password)", "double")
        self.ext_flow_combo.addItem("Single Page (Username & Password together)", "single")
        
        if service_data:
            idx_ext_flow = self.ext_flow_combo.findData(service_data.get("extension_flow", "double"))
            self.ext_flow_combo.setCurrentIndex(max(idx_ext_flow, 0))
        
        form.addRow("Extension Login Flow:", self.ext_flow_combo)
        layout.addLayout(form)

        # Wire real-time portal selector presets on typing
        self.name_input.textChanged.connect(self._auto_detect_portal_presets)
        self.url_input.textChanged.connect(self._auto_detect_portal_presets)

        if not service_data or not self.uid_sel.text().strip():
            self._auto_detect_portal_presets()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _auto_detect_portal_presets(self):
        name = self.name_input.text().strip().lower()
        url = self.url_input.text().strip().lower()
        combined = f"{name} {url}"
        
        presets = [
            (['tdscpc', 'traces', 'tds'], "input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single', ['traces', 'tds', 'tan', 'pan', 'user'], ['traces', 'tds', 'pass', 'pwd']),
            (['gst.gov.in', 'gst'], '#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single', ['gst', 'user', 'pan'], ['gst', 'pass', 'pwd']),
            (['incometax', 'itr', 'eportal'], '#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double', ['pan', 'itr', 'user'], ['itr', 'tax', 'pass', 'pwd']),
            (['gmail', 'google', 'accounts.google'], '#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double', ['email', 'gmail', 'google', 'user'], ['gmail', 'google', 'pass', 'pwd']),
            (['epfindia', 'epfo', 'unifiedportal', 'pf'], '#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single', ['epfo', 'uan', 'pf', 'user'], ['epfo', 'pf', 'pass', 'pwd']),
            (['icegate'], '#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single', ['icegate', 'user'], ['icegate', 'pass', 'pwd']),
            (['mca.gov.in', 'mca21', 'mca'], '#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double', ['mca', 'user', 'din', 'pan'], ['mca', 'pass', 'pwd']),
        ]
        
        for kws, u_sel, p_sel, def_url, def_flow, u_mcl_hints, p_mcl_hints in presets:
            if any(k in combined for k in kws):
                if not self.uid_sel.text().strip() or self.uid_sel.text() in ['#username', "input[type='text']"]:
                    self.uid_sel.setText(u_sel)
                if not self.pwd_sel.text().strip() or self.pwd_sel.text() in ['#password', "input[type='password']"]:
                    self.pwd_sel.setText(p_sel)
                if not self.url_input.text().strip():
                    self.url_input.setText(def_url)
                idx = self.ext_flow_combo.findData(def_flow)
                if idx >= 0:
                    self.ext_flow_combo.setCurrentIndex(idx)
                
                # Auto-select MCL column if unselected
                if self.uid_combo.currentIndex() <= 0:
                    for i in range(1, self.uid_combo.count()):
                        lbl = self.uid_combo.itemText(i).lower()
                        if any(h in lbl for h in u_mcl_hints):
                            self.uid_combo.setCurrentIndex(i)
                            break
                if self.pwd_combo.currentIndex() <= 0:
                    for i in range(1, self.pwd_combo.count()):
                        lbl = self.pwd_combo.itemText(i).lower()
                        if any(h in lbl for h in p_mcl_hints):
                            self.pwd_combo.setCurrentIndex(i)
                            break
                break

    def _on_accept(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Missing Name", "Service name is required.")
            return
        self.accept()

    def result_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "login_page_link": self.url_input.text().strip(),
            "userid_column_id": self.uid_combo.currentData(),
            "password_column_id": self.pwd_combo.currentData(),
            "username_selector": self.uid_sel.text().strip(),
            "password_selector": self.pwd_sel.text().strip(),
            "automation_mode": self.mode_combo.currentData(),
            "extension_flow": self.ext_flow_combo.currentData(),
            "success_selector": self.success_sel.text().strip(),
            "arn_selector": self.arn_sel.text().strip()
        }

class ServiceManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Manage Services")
        self.setModal(True)
        self.resize(450, 400)
        self._build_ui()
        try:
            self.db.auto_populate_service_selectors()
        except Exception:
            pass
        self._reload_services()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        edit_btn = QPushButton("Edit")
        del_btn = QPushButton("Delete")
        
        add_btn.clicked.connect(self._on_add)
        edit_btn.clicked.connect(self._on_edit)
        del_btn.clicked.connect(self._on_delete)
        
        for btn in (add_btn, edit_btn, del_btn):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _reload_services(self):
        self.list_widget.clear()
        for s in self.db.get_services():
            item = QListWidgetItem(f"{s['name']} ({s['automation_mode']})")
            item.setData(Qt.UserRole, s["id"])
            self.list_widget.addItem(item)

    def _selected_id(self):
        items = self.list_widget.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _on_add(self):
        dlg = ServiceEditDialog(self.db, self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self.db.create_service(**dlg.result_data())
                self.db.auto_populate_service_selectors()
                self._reload_services()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _on_edit(self):
        sid = self._selected_id()
        if not sid: return
        current = next((s for s in self.db.get_services() if s["id"] == sid), None)
        if not current: return
        dlg = ServiceEditDialog(self.db, self, current)
        if dlg.exec() == QDialog.Accepted:
            self.db.update_service(sid, **dlg.result_data())
            self.db.auto_populate_service_selectors()
            self._reload_services()

    def _on_delete(self):
        sid = self._selected_id()
        if not sid: return
        if QMessageBox.question(self, "Confirm", "Delete this service? It will be unattached from all clients.") == QMessageBox.Yes:
            self.db.delete_service(sid)
            self._reload_services()
