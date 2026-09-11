"""
quick_scc_dialog.py
-------------------
Quick Portal Login modal for launching the Income Tax portal with
MECP (Manual Extension Copy/Paste) and the 4 SCC vault password combinations
for any registered or unregistered client PAN.
"""

import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QWidget, QMessageBox
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon

try:
    import qtawesome as qta
except ImportError:
    qta = None

import automation


def _safe_qta_icon(name: str, color: str = None) -> QIcon:
    if qta:
        try:
            return qta.icon(name, color=color) if color else qta.icon(name)
        except Exception:
            pass
    return QIcon()


class QuickSCCDialog(QDialog):
    """Dialog allowing staff to enter any PAN and immediately launch the Income Tax
    portal with the 4 cleartext SCC combination passwords loaded into MECP.
    Works seamlessly for both registered clients and new/unregistered clients.
    """

    def __init__(self, db, parent=None, prefill_pan: str = ""):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Quick Portal Login (SCC / MECP)")
        self.setFixedSize(500, 460)
        self.setStyleSheet("""
            QDialog {
                background-color: #121815;
                color: #F0F6FC;
                border: 1.5px solid #1E4D34;
                border-radius: 12px;
            }
            QLabel {
                color: #F0F6FC;
            }
            QLineEdit {
                background-color: #0B120E;
                border: 1.5px solid #2E9B5F;
                border-radius: 6px;
                padding: 8px 12px;
                font-family: Consolas, monospace;
                font-size: 15px;
                font-weight: 700;
                color: #4CF9B7;
                letter-spacing: 1.5px;
            }
            QLineEdit:focus {
                border: 1.5px solid #4CF9B7;
                background-color: #102B1E;
            }
            QPushButton.primary {
                background-color: #2E9B5F;
                border: none;
                border-radius: 6px;
                color: #FFFFFF;
                font-weight: 700;
                font-size: 13px;
                padding: 10px 18px;
            }
            QPushButton.primary:hover {
                background-color: #34B76D;
            }
            QPushButton.primary:disabled {
                background-color: #1B3D2B;
                color: #5D806E;
            }
            QPushButton.cancel {
                background-color: transparent;
                border: 1px solid #30363D;
                border-radius: 6px;
                color: #8B949E;
                font-weight: 600;
                font-size: 13px;
                padding: 10px 16px;
            }
            QPushButton.cancel:hover {
                background-color: #21262D;
                color: #F0F6FC;
            }
        """)

        self._setup_ui(prefill_pan)

    def _setup_ui(self, prefill_pan: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.shield-key-outline", "#4CF9B7").pixmap(QSize(28, 28)))
        header_row.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel("Quick Portal Login (SCC)")
        title_lbl.setFont(QFont("Segoe UI", 13, QFont.Bold))
        title_lbl.setStyleSheet("color: #FFFFFF;")
        subtitle_lbl = QLabel("Launch Income Tax portal with MECP and 4 vault combination passwords.")
        subtitle_lbl.setFont(QFont("Segoe UI", 9))
        subtitle_lbl.setStyleSheet("color: #8B949E;")
        title_col.addWidget(title_lbl)
        title_col.addWidget(subtitle_lbl)
        header_row.addLayout(title_col)
        header_row.addStretch()
        layout.addLayout(header_row)

        # PAN Input Section
        input_box = QVBoxLayout()
        input_box.setSpacing(6)
        pan_lbl = QLabel("ENTER CLIENT PAN:")
        pan_lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        pan_lbl.setStyleSheet("color: #8B949E; letter-spacing: 0.5px;")
        input_box.addWidget(pan_lbl)

        self.txt_pan = QLineEdit()
        self.txt_pan.setMaxLength(10)
        self.txt_pan.setPlaceholderText("e.g. ABCDE1234F")
        self.txt_pan.textChanged.connect(self._on_pan_changed)
        input_box.addWidget(self.txt_pan)

        self.lbl_status = QLabel("")
        self.lbl_status.setFont(QFont("Segoe UI", 9))
        self.lbl_status.setStyleSheet("color: #8B949E; margin-top: 2px;")
        input_box.addWidget(self.lbl_status)
        layout.addLayout(input_box)

        # Preview of Combos Box
        preview_frame = QFrame()
        preview_frame.setStyleSheet("""
            QFrame {
                background-color: #0B120E;
                border: 1px solid #1E4D34;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        pf_layout = QVBoxLayout(preview_frame)
        pf_layout.setContentsMargins(10, 8, 10, 8)
        pf_layout.setSpacing(6)

        prev_title = QLabel("VAULT COMBINATIONS PREVIEW:")
        prev_title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        prev_title.setStyleSheet("color: #5D806E; letter-spacing: 0.5px; border: none; background: transparent;")
        pf_layout.addWidget(prev_title)

        self.combo_labels = []
        for i in range(1, 5):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl_tag = QLabel(f"Combo {i}:")
            lbl_tag.setFixedWidth(65)
            lbl_tag.setFont(QFont("Segoe UI", 9, QFont.Bold))
            lbl_tag.setStyleSheet("color: #8B949E; border: none; background: transparent;")
            lbl_val = QLabel("—")
            lbl_val.setFont(QFont("Consolas", 10, QFont.DemiBold))
            lbl_val.setStyleSheet("color: #C9D1D9; border: none; background: transparent;")
            row.addWidget(lbl_tag)
            row.addWidget(lbl_val, 1)
            pf_layout.addLayout(row)
            self.combo_labels.append((lbl_tag, lbl_val))

        layout.addWidget(preview_frame)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setProperty("class", "cancel")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_launch = QPushButton("🚀 Launch Portal (MECP)")
        self.btn_launch.setProperty("class", "primary")
        self.btn_launch.setCursor(Qt.PointingHandCursor)
        self.btn_launch.setEnabled(False)
        self.btn_launch.clicked.connect(self._on_launch)
        btn_row.addWidget(self.btn_launch)

        layout.addLayout(btn_row)

        if prefill_pan:
            clean_prefill = re.sub(r"[^A-Za-z0-9]", "", prefill_pan).upper()[:10]
            self.txt_pan.setText(clean_prefill)

    def _on_pan_changed(self, text: str):
        clean = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if clean != text:
            self.txt_pan.blockSignals(True)
            self.txt_pan.setText(clean)
            self.txt_pan.blockSignals(False)

        is_valid_pan = bool(re.match(r"^[A-Z]{5}\d{4}[A-Z]$", clean))
        self.btn_launch.setEnabled(is_valid_pan)

        if not clean:
            self.lbl_status.setText("")
            for _, val_lbl in self.combo_labels:
                val_lbl.setText("—")
            return

        # Check existing registered client
        client = self.db.get_client_by_pan(clean) if self.db else None
        if client:
            client_name = ""
            for col in self.db.get_mcl_columns():
                lbl = (col.get("label") or "").lower()
                if "name" in lbl or "client" in lbl:
                    client_name = client.get("values", {}).get(col["id"]) or ""
                    if client_name:
                        break
            disp = f"✓ Registered: {client_name}" if client_name else "✓ Registered Client"
            is_verified = self.db.is_client_scc_verified(client["id"])
            if is_verified:
                disp += " (Password already verified)"
            self.lbl_status.setStyleSheet("color: #4CF9B7; font-weight: 600;")
            self.lbl_status.setText(disp)
        else:
            if is_valid_pan:
                self.lbl_status.setStyleSheet("color: #7EE787;")
                self.lbl_status.setText("⚡ Unregistered Client — Will auto-register upon verified login")
            else:
                self.lbl_status.setStyleSheet("color: #8B949E;")
                self.lbl_status.setText(f"{len(clean)}/10 characters")

        # Update preview combinations
        combos = self.db.generate_scc_passwords(clean) if self.db else []
        for i, (tag_lbl, val_lbl) in enumerate(self.combo_labels):
            if i < len(combos):
                c = combos[i]
                tag_lbl.setText(f"{c.get('label', f'Combo {i+1}')}:")
                val_lbl.setText(c.get("value", ""))
            else:
                tag_lbl.setText(f"Combo {i+1}:")
                val_lbl.setText("—")

    def _on_launch(self):
        pan = self.txt_pan.text().strip().upper()
        if not re.match(r"^[A-Z]{5}\d{4}[A-Z]$", pan):
            QMessageBox.warning(self, "Invalid PAN", "Please enter a valid 10-character PAN (e.g. ABCDE1234F).")
            return

        # Resolve Income Tax service
        service = None
        if self.db:
            service = self.db.get_service_for_portal("Income Tax")
        if not service:
            service = {
                "name": "Income Tax",
                "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login",
                "automation_mode": "extension"
            }

        combos = self.db.generate_scc_passwords(pan) if self.db else []
        client = self.db.get_client_by_pan(pan) if self.db else None
        client_id = client["id"] if client else None

        fst_on = self.db.get_setting("fst_enabled", "1") == "1" if self.db else True
        sad_on = self.db.get_setting("sad_enabled", "1") == "1" if self.db else True
        service["_fst_enabled"] = fst_on
        service["_sad_enabled"] = sad_on
        service["_sad_browser_notif_enabled"] = self.db.get_setting("sad_browser_notif_enabled", "1") == "1" if self.db else True
        service["_tracker_enabled"] = fst_on or sad_on
        service["_client_name"] = f"PAN: {pan}" + (f" ({client.get('client_id_token')})" if client else " (Unregistered)")

        automation.trigger_mecp(
            service=service,
            user_id=pan,
            password="",
            client_id=client_id,
            scc_mode=True,
            scc_combos=combos
        )

        self.accept()
