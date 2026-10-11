from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from ui.components.kit import Card
from ui.utils.tokens import TOKENS


class StartupLoadingDialog(QDialog):
    """
    Sleek, modern loading modal displayed during PBKDF2 key derivation
    and database initialization to ensure visual feedback during launch.
    """
    def __init__(self, parent=None, title="Project Sera"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setMinimumWidth(380)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = Card()
        outer.addWidget(card)

        # App Brand Header
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(TOKENS["s_2"])

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(28, 28)

        logo_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "logo"
        logo_path = logo_dir / "icon_here.png"
        if not logo_path.exists():
            logo_path = logo_dir / "files" / "sera_icon_whitegold.png"
        if not logo_path.exists():
            logo_path = logo_dir / "sera_icon.png"

        if logo_path.exists():
            logo.setPixmap(QPixmap(str(logo_path)).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            logo.setText("PS")
            logo.setFont(QFont("Segoe UI", 10, QFont.Bold))
        header_layout.addWidget(logo)

        app_title = QLabel("Project Sera")
        app_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        header_layout.addWidget(app_title)
        header_layout.addStretch()
        card.add_widget(header)

        self.lbl_status = QLabel("Initializing security vault...")
        self.lbl_status.setWordWrap(True)
        card.add_widget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)  # Indeterminate animated loading
        card.add_widget(self.progress)

    def set_status(self, text: str):
        self.lbl_status.setText(text)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
