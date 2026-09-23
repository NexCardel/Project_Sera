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

        self.stack.addWidget(self.page_choice)  # Index 0
        self.stack.addWidget(self.page_new)     # Index 1
        self.stack.addWidget(self.page_join)    # Index 2
        self.stack.addWidget(self.page_verify)  # Index 3

        self.stack.setCurrentIndex(0)

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

        # Option 1: New Office
        new_btn = QPushButton("  Create a New Office")
        new_btn.setIcon(_safe_icon("mdi.office-building", "#4CAF50"))
        new_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        new_btn.setMinimumHeight(64)
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        layout.addWidget(new_btn)

        new_desc = QLabel("Set up a new office vault on this PC. You will choose the office master password.")
        new_desc.setFont(QFont("Segoe UI", 9))
        new_desc.setStyleSheet("color: #777777; margin-left: 16px; margin-bottom: 8px;")
        new_desc.setWordWrap(True)
        layout.addWidget(new_desc)

        # Option 2: Join Office
        join_btn = QPushButton("  Join an Existing Office")
        join_btn.setIcon(_safe_icon("mdi.lan-connect", "#2196F3"))
        join_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        join_btn.setMinimumHeight(64)
        join_btn.setCursor(Qt.PointingHandCursor)
        join_btn.clicked.connect(self._start_join_page)
        layout.addWidget(join_btn)

        join_desc = QLabel("Connect to another PC on your local office network to copy the database.")
        join_desc.setFont(QFont("Segoe UI", 9))
        join_desc.setStyleSheet("color: #777777; margin-left: 16px;")
        join_desc.setWordWrap(True)
        layout.addWidget(join_desc)

        layout.addStretch()

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
                QTimer.singleShot(0, lambda: self._add_peer_row(peer))
            peers = discover_lan_peers(
                timeout_seconds=10.0,
                stop_event=self._scan_stop,
                on_peer_found=_on_found,
            )
            QTimer.singleShot(0, self._on_scan_finished)

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
                QTimer.singleShot(0, lambda: self.join_status_label.setText(msg))

            ok, reason, staged_db, staged_salt = join_office_fetch_snapshot(
                peer_ip=target_ip,
                peer_port=target_port,
                app_dir=self.app_dir,
                host_name=local_host,
                username=self.actor_alias,
                code=self.join_code,
                on_progress=_on_prog,
            )

            QTimer.singleShot(0, lambda: self._on_fetch_completed(ok, reason, staged_db, staged_salt))

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
