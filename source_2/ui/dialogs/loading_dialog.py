from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QWidget, QHBoxLayout
from PySide6.QtGui import QFont

class StartupLoadingDialog(QDialog):
    """
    Sleek, modern loading modal displayed during PBKDF2 key derivation
    and database initialization to ensure visual feedback during launch.
    """
    def __init__(self, parent=None, title="Project Sera"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(360, 160)
        self._build_ui()

    def _build_ui(self):
        container = QWidget(self)
        container.setFixedSize(360, 160)
        container.setStyleSheet("""
            QWidget {
                background-color: #2E9B5F;
                border-radius: 12px;
                border: 1px solid #164A68;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        # App Brand Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(28, 28)
        
        from pathlib import Path
        from PySide6.QtGui import QPixmap
        logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "icon_here.png"
        if not logo_path.exists():
            logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "files" / "sera_icon_whitegold.png"
        if not logo_path.exists():
            logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "sera_icon.png"
            
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo.setPixmap(pix)
        else:
            logo.setText("PS")
            logo.setStyleSheet("background-color: #164A68; border-radius: 6px; color: #F8F5F2; font-size: 11px; font-weight: 700;")
        header_layout.addWidget(logo)

        app_title = QLabel("Project Sera")
        app_title.setStyleSheet("color: #F8F5F2; font-size: 16px; font-weight: 700; border: none; background: transparent;")
        header_layout.addWidget(app_title)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Status text
        self.lbl_status = QLabel("Initializing security vault...")
        self.lbl_status.setStyleSheet("color: #E2E8F0; font-size: 12px; font-weight: 500; border: none; background: transparent;")
        layout.addWidget(self.lbl_status)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0) # Indeterminate animated loading
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.2);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #FF4D4D;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress)

    def set_status(self, text: str):
        self.lbl_status.setText(text)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
