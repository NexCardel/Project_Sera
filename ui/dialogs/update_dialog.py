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
    QDialog, QVBoxLayout, QHBoxLayout, QProgressBar, QApplication, QWidget
)
from PySide6.QtCore import Qt, Signal, QObject

import version
from ui.components.kit import Banner, Card, DialogFrame, Pill, TextArea, ask_confirm, show_message
from ui.utils.tokens import TOKENS


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
        self.setMinimumWidth(520)
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowStaysOnTopHint | Qt.CustomizeWindowHint)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # closeRequested stays unconnected: Esc is swallowed so the update cannot be bypassed.
        frame = DialogFrame("Mandatory update required",
                            "A new version of Project Sera is required to continue.",
                            self, icon="mdi.cloud-download-outline", tone="amber")
        outer.addWidget(frame)

        versions = QWidget()
        vlay = QHBoxLayout(versions)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(TOKENS["s_2"])
        self.current_pill = Pill(f"Current {self.update_info.get('current_version', 'Unknown')}", "neutral")
        self.new_pill = Pill(f"New v{self.update_info.get('latest_version')}", "emerald")
        vlay.addWidget(self.current_pill)
        vlay.addWidget(self.new_pill)
        vlay.addStretch(1)
        frame.add_widget(versions)

        notes_card = Card("Release notes")
        notes_txt = TextArea(rows=5)
        notes_txt.setReadOnly(True)
        notes_txt.setPlainText(self.update_info.get("release_notes", "Bug fixes and performance enhancements."))
        notes_card.add_widget(notes_txt)
        frame.add_widget(notes_card)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        frame.add_widget(self.progress_bar)

        self.status_label = Banner("Click 'Update now' to install the mandatory update.", "i")
        frame.add_widget(self.status_label)

        self.exit_btn = frame.add_action("Exit application", icon="mdi.close")
        self.exit_btn.clicked.connect(self._on_exit_clicked)
        frame.add_footer_spacer()

        self.update_btn = frame.add_action("Update now", variant="primary", icon="mdi.download")
        self.update_btn.setDefault(True)
        self.update_btn.clicked.connect(self._start_download)

    def _set_status(self, text: str, tone: str = "i"):
        self.status_label.set_tone(tone)
        self.status_label.set_text(text)

    def _confirm_exit(self) -> bool:
        return ask_confirm(
            self,
            "Update Required",
            "Project Sera cannot run without this update. Are you sure you want to exit?",
            yes="Exit",
            no="Stay",
            level="warning",
            danger=True,
        )

    def closeEvent(self, event):
        """Prevent closing the window to bypass the update requirement."""
        if self.mandatory:
            if self._confirm_exit():
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
        if self._confirm_exit():
            sys.exit(0)

    def _start_download(self):
        self.update_btn.setEnabled(False)
        self.exit_btn.setEnabled(False)
        self.is_downloading = True
        self.progress_bar.show()
        self._set_status("Connecting to GitHub & downloading update payload...")

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
            self._set_status(f"Downloading update: {mb_dl:.1f} MB / {mb_tot:.1f} MB ({pct}%)")
        else:
            mb_dl = downloaded / (1024 * 1024)
            self._set_status(f"Downloading update: {mb_dl:.1f} MB...")

    def _on_finished(self, dest_path: Path):
        self._set_status("Download complete! Applying update silently and restarting...", "g")
        self.progress_bar.setValue(100)
        QApplication.processEvents()
        version.apply_and_restart(dest_path, silent=True)

    def _on_error(self, err_msg: str):
        self.is_downloading = False
        self.update_btn.setEnabled(True)
        self.exit_btn.setEnabled(True)
        self.progress_bar.hide()
        self._set_status("Download failed. Please try again or contact admin.", "r")
        show_message(self, "Update Download Failed", f"Could not download update package:\n{err_msg}", "critical")
