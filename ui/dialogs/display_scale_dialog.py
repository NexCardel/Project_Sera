"""
display_scale_dialog.py
-----------------------
Per-PC display scale picker (plan: docs/ui-scale-adaptive-layout-plan.md, A3).

Used in two places: the General page of Settings (admin only) and the sidebar's Display entry, which
ordinary staff can open. The choice is saved in QSettings on this PC only and applies at the next start.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ui.utils import ui_scale

_OPTIONS = (
    ("Automatic (recommended)", None),
    ("80 %", 0.80),
    ("90 %", 0.90),
    ("100 %", 1.00),
    ("110 %", 1.10),
    ("125 %", 1.25),
)


class DisplayScaleControl(QWidget):
    """Scale combo, Apply (saves) and Restart now. Saves straight to this PC's QSettings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        mode, value = ui_scale.read_settings()

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(180)
        for text, option in _OPTIONS:
            self.combo.addItem(text, option)
        selected = 0
        if mode == ui_scale.MODE_MANUAL and value is not None:
            selected = next((i for i, (_t, opt) in enumerate(_OPTIONS)
                             if opt is not None and abs(opt - value) < 0.001), 0)
        self.combo.setCurrentIndex(selected)
        row.addWidget(self.combo)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setProperty("class", "ActionBtnGhost")
        self.btn_apply.clicked.connect(self._apply)
        row.addWidget(self.btn_apply)

        self.btn_restart = QPushButton("Restart now")
        self.btn_restart.setProperty("class", "ActionBtnGhost")
        self.btn_restart.clicked.connect(self._restart_now)
        row.addWidget(self.btn_restart)
        row.addStretch()

        self.lbl_status = QLabel(f"Running at {round(ui_scale.running_scale() * 100)} %.")
        self.lbl_status.setStyleSheet("color: #8B949E; font-size: 12px;")
        self.lbl_status.setWordWrap(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        outer.addLayout(row)
        outer.addWidget(self.lbl_status)

    def _save(self) -> None:
        option = self.combo.currentData()
        if option is None:
            ui_scale.write_settings(ui_scale.MODE_AUTO)
        else:
            ui_scale.write_settings(ui_scale.MODE_MANUAL, option)

    def _apply(self) -> None:
        self._save()
        self.lbl_status.setText(
            f"Saved on this PC. Restart Sera to apply (running at {round(ui_scale.running_scale() * 100)} %).")

    def _restart_now(self) -> None:
        answer = QMessageBox.question(
            self, "Restart Sera",
            "Save this scale and restart Sera now? Anything unsaved in Sera will be lost.")
        if answer != QMessageBox.Yes:
            return
        self._save()
        import version
        version.restart_app()


class DisplayScaleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display scale")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Display scale")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        hint = QLabel("Makes Sera's text and layout smaller or larger on this PC only. "
                      "Applies after restarting Sera. Automatic fits the screen it starts on.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8B949E; font-size: 12px;")

        self.control = DisplayScaleControl()
        close = QPushButton("Close")
        close.setProperty("class", "ActionBtnGhost")
        close.clicked.connect(self.accept)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.control)
        layout.addWidget(close, alignment=Qt.AlignRight)
