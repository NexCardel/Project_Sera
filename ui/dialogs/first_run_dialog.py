"""
first_run_dialog.py
-------------------
First-run setup dialog for Project Sera (WP P0-6/P2-7).
Provides on-screen selection between:
  1. "Create a New Office": this PC becomes the office admin PC (office key, P2-7).
  2. "Join an Existing Office": pairs with the admin PC using a 6-digit code and downloads
     the office database (office key, P2-7).

The old "Legacy: new"/"Legacy: join" (no office key, password+salt) pages were removed in
P4-2: every real PC already has an office key since Sera Sync v3 went live (P3-9). A PC found
with an old-style database but no office key is offered the P2-8 rejoin/salvage import instead
(see main.py's ``_resolve_encryption_key``).

Also provides JoinApprovalDialog for the serving PC to approve incoming join requests.
"""

import html
import secrets
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.components.kit import (
    Banner, Card, DarkListTable, DialogFrame, FieldLabel, Input, LabelledAction, SectionLabel,
)
from ui.utils.tokens import TOKENS


def _field(label: FieldLabel, widget: QWidget) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(TOKENS["s_1"])
    lay.addWidget(label)
    lay.addWidget(widget)
    return box


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

    def __init__(self, app_dir: str | Path, actor_alias: str = "Admin", parent=None):
        super().__init__(parent)
        self.app_dir = Path(app_dir)
        self.actor_alias = actor_alias

        self.setWindowTitle("Welcome to Project Sera — Initial Setup")
        self.setMinimumSize(620, 560)
        self.resize(680, 600)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # 6-digit join authorization code
        self.join_code = f"{secrets.randbelow(1000000):06d}"

        self._build_ui()

        self.office_peer_found_signal.connect(self._add_office_peer_row)
        self.office_scan_finished_signal.connect(self._on_office_scan_finished)
        self.office_join_progress_signal.connect(self._on_office_join_progress)
        self.office_join_done_signal.connect(self._on_office_join_done)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget(self)
        main_layout.addWidget(self.stack)

        self.page_choice = self._build_choice_page()
        self.page_office_new = self._build_office_new_page()
        self.page_office_join = self._build_office_join_page()

        self.stack.addWidget(self.page_choice)       # Index 0
        self.stack.addWidget(self.page_office_new)    # Index 1 (office key, P2-7)
        self.stack.addWidget(self.page_office_join)   # Index 2 (office key, P2-7)

        self.stack.setCurrentIndex(0)

        # Office-mode state (P2-7): set once "New Office" / "Join Office" completes.
        self.office_mode = False
        self._office_scan_stop = None
        self._office_scan_thread = None
        self._office_discovered = []  # list of pairing-beacon dicts

    # ---------------- PAGE 0: Mode Choice ----------------

    def _choice_card(self, button: LabelledAction, text: str) -> Card:
        desc = QLabel(text)
        desc.setWordWrap(True)
        card = Card()
        card.add_widget(button)
        card.add_widget(desc)
        return card

    def _build_choice_page(self) -> QWidget:
        frame = DialogFrame("Welcome to Project Sera",
                            "No local database was detected. Choose how to set up this workstation.",
                            self, icon="mdi.office-building")
        frame.closeRequested.connect(self.reject)

        self.new_office_btn = LabelledAction("Create a new office", icon="mdi.office-building",
                                             tooltip="Make this PC the office admin PC")
        self.new_office_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        frame.add_widget(self._choice_card(
            self.new_office_btn,
            "Set up a new office on this PC. You will choose the office master password."))

        self.join_office_btn = LabelledAction("Join an existing office", icon="mdi.lan-connect",
                                              tooltip="Pair with the office admin PC")
        self.join_office_btn.clicked.connect(self._start_office_join_page)
        frame.add_widget(self._choice_card(
            self.join_office_btn,
            "Pair with the office admin PC using the 6-digit code shown in its "
            "\"Add workstation\" dialog, then download the office database."))

        self.exit_btn = frame.add_action("Exit", icon="mdi.close")
        self.exit_btn.clicked.connect(self.reject)
        return frame

    # ---------------- PAGE 1: New Office (office key, P2-7) ----------------

    def _build_office_new_page(self) -> QWidget:
        frame = DialogFrame("Create new office",
                            "This PC becomes the office admin PC.",
                            self, icon="mdi.office-building")
        frame.closeRequested.connect(lambda: self.stack.setCurrentIndex(0))

        frame.add_widget(Banner(
            "Choose a name for the office and a master password: it unlocks the office if "
            "Windows can't, and is needed to add other workstations later.", "i"))

        self.office_name_input = Input(placeholder="e.g. Aman Associates")
        frame.add_widget(_field(FieldLabel("Office name", required=True, for_widget=self.office_name_input),
                                self.office_name_input))

        self.office_new_pwd_input = Input(placeholder="Master password (min 8 characters)")
        self.office_new_pwd_input.setEchoMode(Input.Password)
        self.office_new_pwd_input.setAccessibleName("Master password")
        frame.add_widget(_field(FieldLabel("Master password", required=True, for_widget=self.office_new_pwd_input),
                                self.office_new_pwd_input))

        self.office_new_pwd_confirm = Input(placeholder="Confirm master password")
        self.office_new_pwd_confirm.setEchoMode(Input.Password)
        self.office_new_pwd_confirm.setAccessibleName("Confirm master password")
        frame.add_widget(_field(FieldLabel("Confirm master password", required=True,
                                           for_widget=self.office_new_pwd_confirm),
                                self.office_new_pwd_confirm))

        self.office_new_error_label = Banner("", "r")
        self.office_new_error_label.hide()
        frame.add_widget(self.office_new_error_label)

        back_btn = frame.add_action("Back", icon="mdi.arrow-left")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        frame.add_footer_spacer()

        self.office_create_btn = frame.add_action("Create office", variant="primary", icon="mdi.check-circle")
        self.office_create_btn.setDefault(True)
        self.office_create_btn.clicked.connect(self._handle_create_office_v3)
        return frame

    def _show_new_error(self, message: str):
        self.office_new_error_label.set_text(message)
        self.office_new_error_label.show()

    def _handle_create_office_v3(self):
        import sync_office
        office_name = self.office_name_input.text()
        pwd = self.office_new_pwd_input.text()
        pwd_confirm = self.office_new_pwd_confirm.text()

        err = sync_office.validate_new_office(office_name, pwd, pwd_confirm)
        if err:
            self._show_new_error(err)
            return

        self.office_create_btn.setEnabled(False)
        try:
            sync_office.create_new_office(self.app_dir, office_name, pwd, self.actor_alias)
        except sync_office.SyncOfficeError as exc:
            self._show_new_error(str(exc))
            return
        except Exception as exc:
            self._show_new_error(f"Could not create the office: {exc}")
            return
        finally:
            self.office_create_btn.setEnabled(True)

        self.office_mode = True
        self.accept()

    # ---------------- PAGE 2: Join Office (office key / pairing, P2-7) ----------------

    def _build_office_join_page(self) -> QWidget:
        frame = DialogFrame("Join office",
                            "Pair with the admin PC using its 6-digit code.",
                            self, icon="mdi.lan-connect", scroll=True)
        frame.closeRequested.connect(self._back_from_office_join)

        frame.add_widget(Banner(
            "On the admin PC, open Sera Sync → Add workstation. Enter the 6-digit code "
            "shown there below, or select the admin PC from the scan results.", "i"))

        head = QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(0, 0, 0, 0)
        head_lay.setSpacing(TOKENS["s_3"])
        head_lay.addWidget(SectionLabel("Admin PCs with an open pairing window"), 1)
        self.office_scan_btn = LabelledAction("Scan LAN", icon="mdi.refresh",
                                              tooltip="Listen for open pairing windows on the LAN")
        self.office_scan_btn.clicked.connect(self._scan_office_peers)
        head_lay.addWidget(self.office_scan_btn)
        frame.add_widget(head)

        self.office_peers_table = DarkListTable(0, 3)
        self.office_peers_table.setHorizontalHeaderLabels(["Office", "Admin PC", "IP Address"])
        self.office_peers_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.office_peers_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.office_peers_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.office_peers_table.set_column(0, kind="strong", minimum=140)
        self.office_peers_table.set_column(1, minimum=120)
        self.office_peers_table.set_column(2, minimum=110)
        self.office_peers_table.setMinimumHeight(110)
        self.office_peers_table.setMaximumHeight(150)
        frame.add_widget(self.office_peers_table)

        manual = QWidget()
        manual_lay = QHBoxLayout(manual)
        manual_lay.setContentsMargins(0, 0, 0, 0)
        manual_lay.setSpacing(TOKENS["s_3"])
        self.office_manual_ip_input = Input(placeholder="e.g. 192.168.1.50")
        manual_lay.addWidget(_field(FieldLabel("Or enter the admin PC's IP", for_widget=self.office_manual_ip_input),
                                    self.office_manual_ip_input), 3)
        self.office_code_input = Input(placeholder="123 456")
        manual_lay.addWidget(_field(FieldLabel("Pairing code", for_widget=self.office_code_input),
                                    self.office_code_input), 2)
        frame.add_widget(manual)

        self.office_join_status_label = QLabel("")
        self.office_join_status_label.setWordWrap(True)
        frame.add_widget(self.office_join_status_label)

        self.office_join_progress = QProgressBar()
        self.office_join_progress.setRange(0, 0)
        self.office_join_progress.setTextVisible(False)
        self.office_join_progress.setVisible(False)
        frame.add_widget(self.office_join_progress)

        back_btn = frame.add_action("Back", icon="mdi.arrow-left")
        back_btn.clicked.connect(self._back_from_office_join)
        frame.add_footer_spacer()

        self.office_join_btn = frame.add_action("Pair and join", variant="primary", icon="mdi.download-network")
        self.office_join_btn.setDefault(True)
        self.office_join_btn.clicked.connect(self._handle_office_join)
        return frame

    def _start_office_join_page(self):
        self.stack.setCurrentIndex(2)
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
        self.setMinimumWidth(480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()

        # 1-second countdown timer for auto-timeout
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Workstation join request",
                            "Check that the code below matches the one on their screen.",
                            self, icon="mdi.lan-connect")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        header = QLabel(f"Workstation <b>{html.escape(self.host)}</b> ({html.escape(self.username)}) wants to join.")
        header.setWordWrap(True)
        frame.add_widget(header)

        formatted = f"{self.code[:3]}  {self.code[3:]}" if len(self.code) == 6 else self.code
        self.code_label = QLabel(formatted)
        self.code_label.setFont(QFont("Consolas", 22, QFont.Bold))
        self.code_label.setAlignment(Qt.AlignCenter)
        code_card = Card("Code on their screen")
        code_card.add_widget(self.code_label)
        frame.add_widget(code_card)

        self.timer_label = Banner(f"Allow? (Auto-rejecting in {self.timeout_remaining}s)", "w")
        frame.add_widget(self.timer_label)

        self.reject_btn = frame.add_action("Reject", icon="mdi.close")
        self.reject_btn.clicked.connect(self.reject)
        frame.add_footer_spacer()

        self.allow_btn = frame.add_action("Allow", variant="primary", icon="mdi.check")
        self.allow_btn.setDefault(True)
        self.allow_btn.clicked.connect(self.accept)

    def _on_tick(self):
        self.timeout_remaining -= 1
        if self.timeout_remaining <= 0:
            self._timer.stop()
            self.reject()
        else:
            self.timer_label.set_text(f"Allow? (Auto-rejecting in {self.timeout_remaining}s)")
