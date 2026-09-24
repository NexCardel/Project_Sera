"""
first_run_dialog.py
-------------------
First-run setup dialog for Project Sera (WP P0-6).
Provides on-screen selection between:
  1. "New Office": Sets up a new office database with user-defined master password.
  2. "Join Office": Discovers LAN peers, requests database snapshot via fetch_snapshot,
     displays a 6-digit authorization code, and installs the verified database.

Also provides JoinApprovalDialog for the serving PC to approve incoming join requests.
"""

import os
import sys
import secrets
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QMessageBox,
    QFrame,
)

try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_icon(name: str, color: Optional[str] = None) -> QIcon:
    if qta:
        try:
            if color:
                return qta.icon(name, color=color)
            return qta.icon(name)
        except Exception:
            pass
    return QIcon()


class FirstRunDialog(QDialog):
    """
    Initial configuration modal displayed when master.db does not exist in APP_DIR.
    Offers 'New Office' and 'Join Office' setup paths.
    """

    # Background worker threads (LAN scan, pairing, snapshot download) call back on their own
    # thread. QTimer.singleShot(0, fn) from a non-GUI thread creates the timer on that thread,
    # which has no Qt event loop, so it never fires -- these signals are true queued
    # cross-thread delivery (Qt detects sender/receiver are on different threads).
    office_peer_found_signal = Signal(dict)
    office_scan_finished_signal = Signal()
    office_join_progress_signal = Signal(str, int, int)
    office_join_done_signal = Signal(bool, str)
    # Same cross-thread fix, applied to the legacy (no-office-key) LAN scan / fetch-snapshot
    # pages (P0-6), which had the identical QTimer.singleShot(0, fn)-from-a-worker-thread bug.
    legacy_peer_found_signal = Signal(dict)
    legacy_scan_finished_signal = Signal()
    legacy_join_status_signal = Signal(str)
    legacy_fetch_done_signal = Signal(bool, str, object, object)

    def __init__(self, app_dir: str | Path, actor_alias: str = "Admin", parent=None):
        super().__init__(parent)
        self.app_dir = Path(app_dir)
        self.actor_alias = actor_alias
        self.master_password = ""

        self.setWindowTitle("Welcome to Project Sera — Initial Setup")
        self.setFixedSize(620, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # 6-digit join authorization code
        self.join_code = f"{secrets.randbelow(1000000):06d}"
        self._staged_db: Optional[Path] = None
        self._staged_salt: Optional[Path] = None
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_stop = threading.Event()
        self._discovered_peers: list[dict] = []

        self._build_ui()

        self.office_peer_found_signal.connect(self._add_office_peer_row)
        self.office_scan_finished_signal.connect(self._on_office_scan_finished)
        self.office_join_progress_signal.connect(self._on_office_join_progress)
        self.office_join_done_signal.connect(self._on_office_join_done)

        self.legacy_peer_found_signal.connect(self._add_peer_row)
        self.legacy_scan_finished_signal.connect(self._on_scan_finished)
        self.legacy_join_status_signal.connect(self.join_status_label.setText)
        self.legacy_fetch_done_signal.connect(self._on_fetch_completed)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)

        self.stack = QStackedWidget(self)
        main_layout.addWidget(self.stack)

        self.page_choice = self._build_choice_page()
        self.page_new = self._build_new_office_page()
        self.page_join = self._build_join_page()
        self.page_verify = self._build_verify_page()
        self.page_office_new = self._build_office_new_page()
        self.page_office_join = self._build_office_join_page()

        self.stack.addWidget(self.page_choice)       # Index 0
        self.stack.addWidget(self.page_new)          # Index 1 (legacy, no office key)
        self.stack.addWidget(self.page_join)          # Index 2 (legacy, no office key)
        self.stack.addWidget(self.page_verify)        # Index 3 (legacy, no office key)
        self.stack.addWidget(self.page_office_new)    # Index 4 (office key, P2-7)
        self.stack.addWidget(self.page_office_join)   # Index 5 (office key, P2-7)

        self.stack.setCurrentIndex(0)

        # Office-mode state (P2-7): set once "New Office" / "Join Office" (office key)
        # completes. main.py checks this to skip the legacy password path entirely.
        self.office_mode = False
        self._office_scan_stop = None
        self._office_scan_thread = None
        self._office_discovered = []  # list of pairing-beacon dicts

    # ---------------- PAGE 0: Mode Choice ----------------

    def _build_choice_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(16)

        title = QLabel("Welcome to Project Sera")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("No local database was detected. Choose how to set up this workstation:")
        subtitle.setFont(QFont("Segoe UI", 10))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #888888;")
        layout.addWidget(subtitle)

        layout.addSpacing(12)

        # Option 1: New Office (office key, P2-7)
        new_btn = QPushButton("  Create a New Office")
        new_btn.setIcon(_safe_icon("mdi.office-building", "#4CAF50"))
        new_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        new_btn.setMinimumHeight(64)
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(4))
        layout.addWidget(new_btn)

        new_desc = QLabel("Set up a new office on this PC. You will choose the office master password.")
        new_desc.setFont(QFont("Segoe UI", 9))
        new_desc.setStyleSheet("color: #777777; margin-left: 16px; margin-bottom: 8px;")
        new_desc.setWordWrap(True)
        layout.addWidget(new_desc)

        # Option 2: Join Office (office key, P2-7 pairing)
        join_btn = QPushButton("  Join an Existing Office")
        join_btn.setIcon(_safe_icon("mdi.lan-connect", "#2196F3"))
        join_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        join_btn.setMinimumHeight(64)
        join_btn.setCursor(Qt.PointingHandCursor)
        join_btn.clicked.connect(self._start_office_join_page)
        layout.addWidget(join_btn)

        join_desc = QLabel(
            "Pair with the office admin PC using the 6-digit code shown in its "
            "\"Add workstation\" dialog, then download the office database."
        )
        join_desc.setFont(QFont("Segoe UI", 9))
        join_desc.setStyleSheet("color: #777777; margin-left: 16px;")
        join_desc.setWordWrap(True)
        layout.addWidget(join_desc)

        layout.addStretch()

        legacy_row = QHBoxLayout()
        legacy_label = QLabel("Advanced (no office key, not recommended for a new PC):")
        legacy_label.setStyleSheet("color: #666666; font-size: 8pt;")
        legacy_row.addWidget(legacy_label)
        legacy_new_btn = QPushButton("Legacy: new")
        legacy_new_btn.setStyleSheet("font-size: 8pt; padding: 2px 8px;")
        legacy_new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        legacy_row.addWidget(legacy_new_btn)
        legacy_join_btn = QPushButton("Legacy: join")
        legacy_join_btn.setStyleSheet("font-size: 8pt; padding: 2px 8px;")
        legacy_join_btn.clicked.connect(self._start_join_page)
        legacy_row.addWidget(legacy_join_btn)
        legacy_row.addStretch()
        layout.addLayout(legacy_row)

        exit_btn = QPushButton("Exit")
        exit_btn.clicked.connect(self.reject)
        layout.addWidget(exit_btn, alignment=Qt.AlignRight)

        return page

    # ---------------- PAGE 1: New Office ----------------

    def _build_new_office_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        title = QLabel("Create New Office")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "Choose a master password to encrypt your office vault.\n"
            "This password will be required when adding new workstations or recovering data."
        )
        desc.setStyleSheet("color: #888888;")
        layout.addWidget(desc)

        layout.addSpacing(8)

        lbl_pwd = QLabel("Master Password (min 8 characters):")
        self.new_pwd_input = QLineEdit()
        self.new_pwd_input.setEchoMode(QLineEdit.Password)
        self.new_pwd_input.setPlaceholderText("Enter master password...")
        layout.addWidget(lbl_pwd)
        layout.addWidget(self.new_pwd_input)

        lbl_confirm = QLabel("Confirm Master Password:")
        self.new_pwd_confirm = QLineEdit()
        self.new_pwd_confirm.setEchoMode(QLineEdit.Password)
        self.new_pwd_confirm.setPlaceholderText("Re-type master password...")
        layout.addWidget(lbl_confirm)
        layout.addWidget(self.new_pwd_confirm)

        self.new_error_label = QLabel("")
        self.new_error_label.setStyleSheet("color: #FF5252;")
        self.new_error_label.setWordWrap(True)
        layout.addWidget(self.new_error_label)

        layout.addStretch()

        btn_box = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.setIcon(_safe_icon("mdi.arrow-left"))
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        btn_box.addWidget(back_btn)

        btn_box.addStretch()

        create_btn = QPushButton("Create Office")
        create_btn.setIcon(_safe_icon("mdi.check-circle", "#4CAF50"))
        create_btn.setDefault(True)
        create_btn.clicked.connect(self._handle_create_new_office)
        btn_box.addWidget(create_btn)

        layout.addLayout(btn_box)
        return page

    def _handle_create_new_office(self):
        pwd = self.new_pwd_input.text()
        pwd_confirm = self.new_pwd_confirm.text()

        from sync_peer import create_new_office
        ok, err = create_new_office(self.app_dir, pwd, pwd_confirm)
        if not ok:
            self.new_error_label.setText(err)
            return

        # Strip to match what create_new_office writes to sera.key (§0 rule: consistent whitespace)
        self.master_password = pwd.strip()
        self.accept()

    # ---------------- PAGE 2: Join Office ----------------

    def _build_join_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        title = QLabel("Join Office Network")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        # Code display box
        code_frame = QFrame()
        code_frame.setStyleSheet("background-color: rgba(33, 150, 243, 0.1); border: 1px solid #2196F3; border-radius: 6px;")
        code_layout = QVBoxLayout(code_frame)
        code_layout.setContentsMargins(12, 10, 12, 10)

        code_title = QLabel("Authorization Code for Approval:")
        code_title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        code_layout.addWidget(code_title)

        formatted_code = f"{self.join_code[:3]}  {self.join_code[3:]}"
        self.code_label = QLabel(formatted_code)
        self.code_label.setFont(QFont("Consolas", 18, QFont.Bold))
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setStyleSheet("color: #2196F3; letter-spacing: 4px;")
        code_layout.addWidget(self.code_label)

        code_hint = QLabel("A prompt with this code will appear on the other PC for confirmation.")
        code_hint.setFont(QFont("Segoe UI", 8))
        code_hint.setStyleSheet("color: #888888;")
        code_hint.setAlignment(Qt.AlignCenter)
        code_layout.addWidget(code_hint)

        layout.addWidget(code_frame)

        # LAN PCs table
        pc_header = QHBoxLayout()
        pc_label = QLabel("Discovered Workstations on Office LAN:")
        pc_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        pc_header.addWidget(pc_label)
        pc_header.addStretch()

        self.scan_btn = QPushButton("Scan LAN")
        self.scan_btn.setIcon(_safe_icon("mdi.refresh"))
        self.scan_btn.clicked.connect(self._scan_peers)
        pc_header.addWidget(self.scan_btn)
        layout.addLayout(pc_header)

        self.peers_table = QTableWidget(0, 3)
        self.peers_table.setHorizontalHeaderLabels(["Workstation", "User", "IP Address"])
        self.peers_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.peers_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.peers_table.setSelectionMode(QTableWidget.SingleSelection)
        self.peers_table.setMaximumHeight(110)
        layout.addWidget(self.peers_table)

        # Manual IP input
        manual_box = QHBoxLayout()
        manual_box.addWidget(QLabel("Or enter IP manually:"))
        self.manual_ip_input = QLineEdit()
        self.manual_ip_input.setPlaceholderText("e.g. 192.168.1.50")
        manual_box.addWidget(self.manual_ip_input)
        layout.addLayout(manual_box)

        # Status & progress
        self.join_status_label = QLabel("")
        self.join_status_label.setStyleSheet("color: #2196F3;")
        self.join_status_label.setWordWrap(True)
        layout.addWidget(self.join_status_label)

        self.join_progress = QProgressBar()
        self.join_progress.setRange(0, 0)  # Indeterminate
        self.join_progress.setVisible(False)
        layout.addWidget(self.join_progress)

        layout.addStretch()

        btn_box = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.setIcon(_safe_icon("mdi.arrow-left"))
        back_btn.clicked.connect(self._back_from_join)
        btn_box.addWidget(back_btn)

        btn_box.addStretch()

        self.connect_btn = QPushButton("Connect & Join")
        self.connect_btn.setIcon(_safe_icon("mdi.download-network", "#2196F3"))
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._handle_connect_and_join)
        btn_box.addWidget(self.connect_btn)

        layout.addLayout(btn_box)
        return page

    def _start_join_page(self):
        self.stack.setCurrentIndex(2)
        self._scan_peers()

    def _back_from_join(self):
        self._scan_stop.set()
        self.stack.setCurrentIndex(0)

    def _scan_peers(self):
        self.peers_table.setRowCount(0)
        self.scan_btn.setEnabled(False)
        self.join_status_label.setText("Listening for workstations on LAN (10s)...")
        self._discovered_peers = []
        self._scan_stop.clear()

        def _worker():
            from sync_peer import discover_lan_peers
            def _on_found(peer):
                self.legacy_peer_found_signal.emit(peer)
            peers = discover_lan_peers(
                timeout_seconds=10.0,
                stop_event=self._scan_stop,
                on_peer_found=_on_found,
            )
            self.legacy_scan_finished_signal.emit()

        self._scan_thread = threading.Thread(target=_worker, daemon=True)
        self._scan_thread.start()

    def _add_peer_row(self, peer: dict):
        host = peer.get("host", "Unknown")
        user = peer.get("username", "Unknown")
        ip = peer.get("ip", "")
        row = self.peers_table.rowCount()
        self.peers_table.insertRow(row)
        self.peers_table.setItem(row, 0, QTableWidgetItem(host))
        self.peers_table.setItem(row, 1, QTableWidgetItem(user))
        self.peers_table.setItem(row, 2, QTableWidgetItem(ip))
        self._discovered_peers.append(peer)

    def _on_scan_finished(self):
        self.scan_btn.setEnabled(True)
        cnt = self.peers_table.rowCount()
        if cnt == 0:
            self.join_status_label.setText("No workstations discovered. Enter the IP manually above.")
        else:
            self.join_status_label.setText(f"Found {cnt} workstation(s). Select one and click Connect & Join.")

    def _handle_connect_and_join(self):
        target_ip = self.manual_ip_input.text().strip()
        target_port = 49157

        if ":" in target_ip:
            parts = target_ip.split(":", 1)
            target_ip = parts[0].strip()
            try:
                target_port = int(parts[1].strip())
            except ValueError:
                pass

        if not target_ip:
            selected_rows = self.peers_table.selectionModel().selectedRows()
            if selected_rows:
                idx = selected_rows[0].row()
                if 0 <= idx < len(self._discovered_peers):
                    p = self._discovered_peers[idx]
                    target_ip = p.get("ip", "")
                    target_port = p.get("sync_port", 49157)

        if not target_ip:
            self.join_status_label.setText("Please select a workstation from the list or enter an IP address.")
            return

        self._scan_stop.set()
        self.connect_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.join_progress.setVisible(True)
        self.join_status_label.setText(f"Connecting to {target_ip}:{target_port}...")

        def _fetch_worker():
            import socket
            from sync_peer import join_office_fetch_snapshot
            local_host = socket.gethostname()

            def _on_prog(msg):
                self.legacy_join_status_signal.emit(msg)

            ok, reason, staged_db, staged_salt = join_office_fetch_snapshot(
                peer_ip=target_ip,
                peer_port=target_port,
                app_dir=self.app_dir,
                host_name=local_host,
                username=self.actor_alias,
                code=self.join_code,
                on_progress=_on_prog,
            )

            self.legacy_fetch_done_signal.emit(ok, reason, staged_db, staged_salt)

        threading.Thread(target=_fetch_worker, daemon=True).start()

    def _on_fetch_completed(self, ok: bool, reason: str, staged_db: Optional[Path], staged_salt: Optional[Path]):
        self.connect_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.join_progress.setVisible(False)

        if not ok:
            self.join_status_label.setText(f"Join failed: {reason}")
            QMessageBox.warning(self, "Join Failed", f"Could not fetch office database:\n\n{reason}")
            return

        self._staged_db = staged_db
        self._staged_salt = staged_salt
        self.stack.setCurrentIndex(3)

    # ---------------- PAGE 3: Verify & Install ----------------

    def _build_verify_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        title = QLabel("Verify Office Database")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "The office database was downloaded successfully.\n"
            "Enter the office master password to verify and install the database:"
        )
        desc.setStyleSheet("color: #888888;")
        layout.addWidget(desc)

        layout.addSpacing(8)

        lbl_pwd = QLabel("Office Master Password:")
        self.verify_pwd_input = QLineEdit()
        self.verify_pwd_input.setEchoMode(QLineEdit.Password)
        self.verify_pwd_input.setPlaceholderText("Enter office master password...")
        layout.addWidget(lbl_pwd)
        layout.addWidget(self.verify_pwd_input)

        self.verify_error_label = QLabel("")
        self.verify_error_label.setStyleSheet("color: #FF5252;")
        self.verify_error_label.setWordWrap(True)
        layout.addWidget(self.verify_error_label)

        layout.addStretch()

        btn_box = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel_verify)
        btn_box.addWidget(cancel_btn)

        btn_box.addStretch()

        finish_btn = QPushButton("Unlock & Complete Setup")
        finish_btn.setIcon(_safe_icon("mdi.check-circle", "#4CAF50"))
        finish_btn.setDefault(True)
        finish_btn.clicked.connect(self._handle_verify_and_install)
        btn_box.addWidget(finish_btn)

        layout.addLayout(btn_box)
        return page

    def _cancel_verify(self):
        # Clean up staged files if user cancels
        if self._staged_db and self._staged_db.exists():
            try:
                self._staged_db.unlink()
            except OSError:
                pass
        if self._staged_salt and self._staged_salt.exists():
            try:
                self._staged_salt.unlink()
            except OSError:
                pass
        self.stack.setCurrentIndex(2)

    def _handle_verify_and_install(self):
        pwd = self.verify_pwd_input.text().strip()
        if not pwd:
            self.verify_error_label.setText("Please enter the office master password.")
            return

        if not self._staged_db or not self._staged_salt:
            self.verify_error_label.setText("Staged files are missing. Please re-run join.")
            return

        from sync_peer import complete_join_office
        ok, reason = complete_join_office(self.app_dir, self._staged_db, self._staged_salt, pwd)
        if not ok:
            self.verify_error_label.setText(reason)
            return

        # pwd is already stripped; complete_join_office writes this exact value to sera.key
        self.master_password = pwd
        self.accept()

    # ---------------- PAGE 4: New Office (office key, P2-7) ----------------

    def _build_office_new_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        title = QLabel("Create New Office")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "This PC becomes the office admin PC. Choose a name for the office and a master "
            "password: it unlocks the office if Windows can't, and is needed to add other "
            "workstations later."
        )
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(8)

        layout.addWidget(QLabel("Office Name:"))
        self.office_name_input = QLineEdit()
        self.office_name_input.setPlaceholderText("e.g. Aman Associates")
        layout.addWidget(self.office_name_input)

        layout.addWidget(QLabel("Master Password (min 8 characters):"))
        self.office_new_pwd_input = QLineEdit()
        self.office_new_pwd_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.office_new_pwd_input)

        layout.addWidget(QLabel("Confirm Master Password:"))
        self.office_new_pwd_confirm = QLineEdit()
        self.office_new_pwd_confirm.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.office_new_pwd_confirm)

        self.office_new_error_label = QLabel("")
        self.office_new_error_label.setStyleSheet("color: #FF5252;")
        self.office_new_error_label.setWordWrap(True)
        layout.addWidget(self.office_new_error_label)

        layout.addStretch()

        btn_box = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.setIcon(_safe_icon("mdi.arrow-left"))
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        btn_box.addWidget(back_btn)

        btn_box.addStretch()

        self.office_create_btn = QPushButton("Create Office")
        self.office_create_btn.setIcon(_safe_icon("mdi.check-circle", "#4CAF50"))
        self.office_create_btn.setDefault(True)
        self.office_create_btn.clicked.connect(self._handle_create_office_v3)
        btn_box.addWidget(self.office_create_btn)

        layout.addLayout(btn_box)
        return page

    def _handle_create_office_v3(self):
        import sync_office
        office_name = self.office_name_input.text()
        pwd = self.office_new_pwd_input.text()
        pwd_confirm = self.office_new_pwd_confirm.text()

        err = sync_office.validate_new_office(office_name, pwd, pwd_confirm)
        if err:
            self.office_new_error_label.setText(err)
            return

        self.office_create_btn.setEnabled(False)
        try:
            sync_office.create_new_office(self.app_dir, office_name, pwd, self.actor_alias)
        except sync_office.SyncOfficeError as exc:
            self.office_new_error_label.setText(str(exc))
            return
        except Exception as exc:
            self.office_new_error_label.setText(f"Could not create the office: {exc}")
            return
        finally:
            self.office_create_btn.setEnabled(True)

        self.office_mode = True
        self.accept()

    # ---------------- PAGE 5: Join Office (office key / pairing, P2-7) ----------------

    def _build_office_join_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        title = QLabel("Join Office (Pairing Code)")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "On the admin PC, open Sera Sync → Add workstation. Enter the 6-digit code "
            "shown there below, or select the admin PC from the scan results."
        )
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        pc_header = QHBoxLayout()
        pc_label = QLabel("Admin PCs with an open pairing window:")
        pc_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        pc_header.addWidget(pc_label)
        pc_header.addStretch()

        self.office_scan_btn = QPushButton("Scan LAN")
        self.office_scan_btn.setIcon(_safe_icon("mdi.refresh"))
        self.office_scan_btn.clicked.connect(self._scan_office_peers)
        pc_header.addWidget(self.office_scan_btn)
        layout.addLayout(pc_header)

        self.office_peers_table = QTableWidget(0, 3)
        self.office_peers_table.setHorizontalHeaderLabels(["Office", "Admin PC", "IP Address"])
        self.office_peers_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.office_peers_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.office_peers_table.setSelectionMode(QTableWidget.SingleSelection)
        self.office_peers_table.setMaximumHeight(90)
        layout.addWidget(self.office_peers_table)

        manual_box = QHBoxLayout()
        manual_box.addWidget(QLabel("Or IP:"))
        self.office_manual_ip_input = QLineEdit()
        self.office_manual_ip_input.setPlaceholderText("e.g. 192.168.1.50")
        manual_box.addWidget(self.office_manual_ip_input)
        manual_box.addWidget(QLabel("Code:"))
        self.office_code_input = QLineEdit()
        self.office_code_input.setPlaceholderText("123 456")
        self.office_code_input.setMaximumWidth(100)
        manual_box.addWidget(self.office_code_input)
        layout.addLayout(manual_box)

        self.office_join_status_label = QLabel("")
        self.office_join_status_label.setStyleSheet("color: #2196F3;")
        self.office_join_status_label.setWordWrap(True)
        layout.addWidget(self.office_join_status_label)

        self.office_join_progress = QProgressBar()
        self.office_join_progress.setRange(0, 0)
        self.office_join_progress.setVisible(False)
        layout.addWidget(self.office_join_progress)

        layout.addStretch()

        btn_box = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.setIcon(_safe_icon("mdi.arrow-left"))
        back_btn.clicked.connect(self._back_from_office_join)
        btn_box.addWidget(back_btn)

        btn_box.addStretch()

        self.office_join_btn = QPushButton("Pair & Join")
        self.office_join_btn.setIcon(_safe_icon("mdi.download-network", "#2196F3"))
        self.office_join_btn.setDefault(True)
        self.office_join_btn.clicked.connect(self._handle_office_join)
        btn_box.addWidget(self.office_join_btn)

        layout.addLayout(btn_box)
        return page

    def _start_office_join_page(self):
        self.stack.setCurrentIndex(5)
        self._scan_office_peers()

    def _back_from_office_join(self):
        if self._office_scan_stop is not None:
            self._office_scan_stop.set()
        self.stack.setCurrentIndex(0)

    def _scan_office_peers(self):
        import threading as _threading
        self.office_peers_table.setRowCount(0)
        self.office_scan_btn.setEnabled(False)
        self.office_join_status_label.setText("Listening for open pairing windows on the LAN (10s)...")
        self._office_discovered = []
        self._office_scan_stop = _threading.Event()
        stop_event = self._office_scan_stop

        def _worker():
            import uuid
            import sync_discovery
            svc = sync_discovery.DiscoveryService(
                device_id=uuid.uuid4().hex, device_name=self.actor_alias,
                enable_broadcast=True, listen=True,
                on_pairing_beacon=lambda peer: self.office_peer_found_signal.emit(peer),
            )
            svc.start()
            stop_event.wait(10.0)
            svc.stop()
            self.office_scan_finished_signal.emit()

        self._office_scan_thread = _threading.Thread(target=_worker, daemon=True)
        self._office_scan_thread.start()

    def _add_office_peer_row(self, peer: dict):
        office_name = peer.get("office_name") or "(unnamed office)"
        admin_name = peer.get("name", "Unknown")
        ip = peer.get("ip", "")
        pair_port = peer.get("pair_port", 49158)
        row = self.office_peers_table.rowCount()
        self.office_peers_table.insertRow(row)
        self.office_peers_table.setItem(row, 0, QTableWidgetItem(office_name))
        self.office_peers_table.setItem(row, 1, QTableWidgetItem(admin_name))
        self.office_peers_table.setItem(row, 2, QTableWidgetItem(ip))
        self._office_discovered.append({"ip": ip, "pair_port": pair_port})

    def _on_office_scan_finished(self):
        self.office_scan_btn.setEnabled(True)
        if self.office_peers_table.rowCount() == 0:
            self.office_join_status_label.setText(
                "No open pairing windows found. Enter the admin PC's IP and code manually.")
        else:
            self.office_join_status_label.setText(
                f"Found {self.office_peers_table.rowCount()} open pairing window(s).")

    def _handle_office_join(self):
        import sync_pairing
        target_ip = self.office_manual_ip_input.text().strip()
        target_port = sync_pairing.PAIRING_PORT
        if not target_ip:
            selected_rows = self.office_peers_table.selectionModel().selectedRows()
            if selected_rows:
                idx = selected_rows[0].row()
                if 0 <= idx < len(self._office_discovered):
                    p = self._office_discovered[idx]
                    target_ip = p.get("ip", "")
                    target_port = p.get("pair_port", target_port)
        code = self.office_code_input.text().strip()

        if not target_ip:
            self.office_join_status_label.setText("Select an admin PC above, or enter its IP manually.")
            return
        if not code:
            self.office_join_status_label.setText("Enter the 6-digit code shown on the admin PC.")
            return

        if self._office_scan_stop is not None:
            self._office_scan_stop.set()
        self.office_join_btn.setEnabled(False)
        self.office_scan_btn.setEnabled(False)
        self.office_join_progress.setRange(0, 0)
        self.office_join_progress.setVisible(True)
        self.office_join_status_label.setText(f"Pairing with {target_ip}:{target_port}...")

        def _worker():
            import sync_office
            import sync_pairing

            def _on_prog(fname, received, size):
                self.office_join_progress_signal.emit(fname, received, size)

            try:
                sync_office.join_office(self.app_dir, target_ip, code, self.actor_alias,
                                        port=target_port, on_progress=_on_prog)
                self.office_join_done_signal.emit(True, "")
            except sync_pairing.PairingError as exc:
                self.office_join_done_signal.emit(False, str(exc))
            except Exception as exc:
                self.office_join_done_signal.emit(False, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_office_join_progress(self, fname: str, received: int, size: int):
        if size:
            pct = int(received * 100 / size)
            self.office_join_progress.setRange(0, 100)
            self.office_join_progress.setValue(pct)
            self.office_join_status_label.setText(f"Downloading {fname}... {pct}%")

    def _on_office_join_done(self, ok: bool, reason: str):
        self.office_join_btn.setEnabled(True)
        self.office_scan_btn.setEnabled(True)
        self.office_join_progress.setVisible(False)
        if not ok:
            self.office_join_status_label.setText(f"Join failed: {reason}")
            QMessageBox.warning(
                self, "Join Failed",
                f"Could not join the office:\n\n{reason}\n\n"
                "If pairing itself succeeded and only the download failed, restart Sera: "
                "it will offer to resume the download automatically."
            )
            return
        self.office_mode = True
        self.accept()


# ==============================================================================
# Serving PC Approval Modal (Rule 3)
# ==============================================================================

class JoinApprovalDialog(QDialog):
    """
    Modal dialog displayed on the serving PC when an incoming join request arrives.
    Shows: "Workstation <host> (<username>) wants to join. Code on their screen: 123 456. Allow?"
    Automatically times out after 120 seconds and rejects the request.
    """

    def __init__(self, host: str, username: str, code: str, timeout_seconds: int = 120, parent=None):
        super().__init__(parent)
        self.host = host
        self.username = username
        self.code = code
        self.timeout_remaining = timeout_seconds

        self.setWindowTitle("Workstation Join Request — Sera Sync")
        self.setFixedSize(480, 280)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()

        # 1-second countdown timer for auto-timeout
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QLabel(f"Workstation <b>{self.host}</b> ({self.username}) wants to join.")
        header.setFont(QFont("Segoe UI", 11))
        header.setWordWrap(True)
        layout.addWidget(header)

        # Code display box
        code_box = QFrame()
        code_box.setStyleSheet("background-color: rgba(33, 150, 243, 0.1); border: 1px solid #2196F3; border-radius: 6px;")
        box_layout = QVBoxLayout(code_box)
        box_layout.setContentsMargins(10, 8, 10, 8)

        lbl_instruct = QLabel("Code on their screen:")
        lbl_instruct.setFont(QFont("Segoe UI", 9))
        box_layout.addWidget(lbl_instruct)

        formatted = f"{self.code[:3]}  {self.code[3:]}" if len(self.code) == 6 else self.code
        self.code_label = QLabel(formatted)
        self.code_label.setFont(QFont("Consolas", 18, QFont.Bold))
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setStyleSheet("color: #2196F3; letter-spacing: 4px;")
        box_layout.addWidget(self.code_label)

        layout.addWidget(code_box)

        self.timer_label = QLabel(f"Allow? (Auto-rejecting in {self.timeout_remaining}s)")
        self.timer_label.setStyleSheet("color: #888888;")
        layout.addWidget(self.timer_label)

        layout.addStretch()

        btn_box = QHBoxLayout()
        self.reject_btn = QPushButton("Reject")
        self.reject_btn.setIcon(_safe_icon("mdi.close", "#F44336"))
        self.reject_btn.clicked.connect(self.reject)
        btn_box.addWidget(self.reject_btn)

        btn_box.addStretch()

        self.allow_btn = QPushButton("Allow")
        self.allow_btn.setIcon(_safe_icon("mdi.check", "#4CAF50"))
        self.allow_btn.setDefault(True)
        self.allow_btn.clicked.connect(self.accept)
        btn_box.addWidget(self.allow_btn)

        layout.addLayout(btn_box)

    def _on_tick(self):
        self.timeout_remaining -= 1
        if self.timeout_remaining <= 0:
            self._timer.stop()
            self.reject()
        else:
            self.timer_label.setText(f"Allow? (Auto-rejecting in {self.timeout_remaining}s)")
