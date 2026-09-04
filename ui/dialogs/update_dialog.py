"""
update_dialog.py
----------------
Mandatory Update Dialog for Project Sera.
Blocks application access when a mandatory version update is available on GitHub.
"""

import sys
import threading
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFrame, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont, QIcon

import version


class DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, download_url: str):
        super().__init__()
        self.download_url = download_url

    def run(self):
        try:
            dest_path = version.download_update_payload(
                self.download_url,
                progress_callback=lambda d, t: self.progress.emit(d, t)
            )
            self.finished.emit(dest_path)
        except Exception as e:
            self.error.emit(str(e))


class ForceUpdateDialog(QDialog):
    def __init__(self, update_info: dict, parent=None, auto_start: bool = True):
        super().__init__(parent)
        self.update_info = update_info
        self.mandatory = update_info.get("mandatory", True)
        self.download_path = None
        self.is_downloading = False
        
        self._setup_ui()
        if auto_start:
            self._start_download()

    def _setup_ui(self):
        self.setWindowTitle("Update Required — Project Sera")
        self.setFixedSize(520, 420)
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowStaysOnTopHint | Qt.CustomizeWindowHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header Frame
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #FF4D49;
                border-radius: 10px;
                padding: 12px;
            }
        """)
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(12, 8, 12, 8)

        title_lbl = QLabel("⚠️ Mandatory Update Required")
        title_lbl.setStyleSheet("color: #FFFFFF; font-size: 18px; font-weight: 800;")
        
        subtitle_lbl = QLabel("A new version of Project Sera is required to continue.")
        subtitle_lbl.setStyleSheet("color: #FFE5E5; font-size: 12px;")

        header_layout.addWidget(title_lbl)
        header_layout.addWidget(subtitle_lbl)
        layout.addWidget(header_frame)

        # Info Box / Card Surface
        card_frame = QFrame()
        card_frame.setStyleSheet("""
            QFrame {
                background-color: #0A0A0A;
                border: 1px solid #262626;
                border-radius: 10px;
            }
        """)
        card_layout = QVBoxLayout(card_frame)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        ver_info_lbl = QLabel(
            f"<b>Current Version:</b> {self.update_info.get('current_version', 'Unknown')} &nbsp;&nbsp;➔&nbsp;&nbsp; "
            f"<b>New Version:</b> <span style='color:#FF4D49;'>v{self.update_info.get('latest_version')}</span>"
        )
        ver_info_lbl.setStyleSheet("font-size: 14px; color: #E0E0E0;")
        card_layout.addWidget(ver_info_lbl)

        notes_title = QLabel("Release Notes:")
        notes_title.setStyleSheet("font-weight: 600; color: #E0E0E0; font-size: 12px;")
        card_layout.addWidget(notes_title)

        notes_txt = QTextEdit()
        notes_txt.setReadOnly(True)
        notes_txt.setPlainText(self.update_info.get("release_notes", "Bug fixes and performance enhancements."))
        notes_txt.setStyleSheet("""
            QTextEdit {
                background-color: #171717;
                border: 1px solid #262626;
                border-radius: 6px;
                padding: 8px;
                color: #E0E0E0;
                font-size: 12px;
            }
        """)
        notes_txt.setMaximumHeight(100)
        card_layout.addWidget(notes_txt)

        layout.addWidget(card_frame)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #D8CDB4;
                border-radius: 6px;
                text-align: center;
                background-color: #FFFFFF;
                height: 22px;
                color: #241F1B;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #4CF9B7;
                border-radius: 5px;
            }
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Click 'Update Now' to install the mandatory update.")
        self.status_label.setStyleSheet("color: #5C5347; font-size: 12px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        # Button Row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.exit_btn = QPushButton("Exit Application")
        self.exit_btn.setStyleSheet("""
            QPushButton {
                background-color: #EAE1CB;
                color: #241F1B;
                border: 1px solid #D8CDB4;
                border-radius: 7px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #D8CDB4;
            }
        """)
        self.exit_btn.clicked.connect(self._on_exit_clicked)

        self.update_btn = QPushButton("🚀 Update Now")
        self.update_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CF9B7;
                color: #1A382B;
                border: 1px solid #36D89C;
                border-radius: 7px;
                padding: 8px 24px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #36D89C;
            }
            QPushButton:disabled {
                background-color: #BFE3D7;
                color: #7A9B90;
            }
        """)
        self.update_btn.clicked.connect(self._start_download)

        btn_layout.addWidget(self.exit_btn)
        btn_layout.addWidget(self.update_btn)
        layout.addLayout(btn_layout)

    def closeEvent(self, event):
        """Prevent closing the window to bypass the update requirement."""
        if self.mandatory:
            reply = QMessageBox.warning(
                self,
                "Update Required",
                "Project Sera cannot run without this update. Are you sure you want to exit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                sys.exit(0)
            else:
                event.ignore()
        else:
            super().closeEvent(event)

    def keyPressEvent(self, event):
        """Ignore Escape key to prevent closing modal."""
        if event.key() == Qt.Key_Escape:
            event.ignore()
        else:
            super().keyPressEvent(event)

    def _on_exit_clicked(self):
        sys.exit(0)

    def _start_download(self):
        self.update_btn.setEnabled(False)
        self.exit_btn.setEnabled(False)
        self.is_downloading = True
        self.progress_bar.show()
        self.status_label.setText("Connecting to GitHub & downloading update payload...")

        download_url = self.update_info.get("download_url")

        self.worker = DownloadWorker(download_url)
        self.thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.thread.start()

    def _run_worker(self):
        self.worker.run()

    def _on_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int((downloaded / total) * 100)
            self.progress_bar.setValue(pct)
            mb_dl = downloaded / (1024 * 1024)
            mb_tot = total / (1024 * 1024)
            self.status_label.setText(f"Downloading update: {mb_dl:.1f} MB / {mb_tot:.1f} MB ({pct}%)")
        else:
            mb_dl = downloaded / (1024 * 1024)
            self.status_label.setText(f"Downloading update: {mb_dl:.1f} MB...")

    def _on_finished(self, dest_path: Path):
        self.status_label.setText("Download complete! Applying update silently and restarting...")
        self.progress_bar.setValue(100)
        QApplication.processEvents()
        version.apply_and_restart(dest_path, silent=True)

    def _on_error(self, err_msg: str):
        self.is_downloading = False
        self.update_btn.setEnabled(True)
        self.exit_btn.setEnabled(True)
        self.progress_bar.hide()
        self.status_label.setText("<span style='color:red;'>Download failed. Please try again or contact admin.</span>")
        QMessageBox.critical(self, "Update Download Failed", f"Could not download update package:\n{err_msg}")
