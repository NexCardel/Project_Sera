"""
display_scale_dialog.py
-----------------------
Per-PC display scale picker (plan: docs/ui-scale-adaptive-layout-plan.md, A3).

Used in two places: the General page of Settings (admin only) and the sidebar's Display entry, which
ordinary staff can open. The choice is saved in QSettings on this PC only and applies at the next start.
"""
from PySide6.QtWidgets import QDialog, QMessageBox, QVBoxLayout

from ui.components.kit import MessageBox, DialogFrame, LabelledAction, Select, SettingRow, Switch
from ui.utils import ui_scale

_OPTIONS = (
    ("Automatic (recommended)", None),
    ("80 %", 0.80),
    ("90 %", 0.90),
    ("100 %", 1.00),
    ("110 %", 1.10),
    ("125 %", 1.25),
)

_HINT = "Makes text and layout smaller or larger on this PC only. Applies after a restart."


def _running_text():
    return f"Running at {round(ui_scale.running_scale() * 100)} %."


class DisplayScaleControl(SettingRow):
    """Scale picker row: Apply saves to this PC's QSettings, Restart now saves and restarts."""

    def __init__(self, parent=None):
        mode, value = ui_scale.read_settings()

        combo = Select()
        combo.setMinimumWidth(180)
        for text, option in _OPTIONS:
            combo.addItem(text, option)
        selected = 0
        if mode == ui_scale.MODE_MANUAL and value is not None:
            selected = next((i for i, (_t, opt) in enumerate(_OPTIONS)
                             if opt is not None and abs(opt - value) < 0.001), 0)
        combo.setCurrentIndex(selected)

        apply_btn = LabelledAction("Apply", variant="soft")
        apply_btn.clicked.connect(self._apply)
        restart_btn = LabelledAction("Restart now", variant="secondary", icon="mdi.refresh")
        restart_btn.clicked.connect(self._restart_now)

        super().__init__("Screen scale", _running_text(), [combo, apply_btn, restart_btn], parent, width="auto")
        self.combo = combo
        self.btn_apply = apply_btn
        self.btn_restart = restart_btn

    def _save(self) -> None:
        option = self.combo.currentData()
        if option is None:
            ui_scale.write_settings(ui_scale.MODE_AUTO)
        else:
            ui_scale.write_settings(ui_scale.MODE_MANUAL, option)

    def _apply(self) -> None:
        self._save()
        self.set_sub(f"Saved on this PC. Restart Sera to apply (running at {round(ui_scale.running_scale() * 100)} %).")

    def _restart_now(self) -> None:
        answer = MessageBox.question(
            self, "Restart Sera",
            "Save this scale and restart Sera now? Anything unsaved in Sera will be lost.")
        if answer != QMessageBox.Yes:
            return
        self._save()
        import version
        version.restart_app()


_SHARP_SUB = "Crisper text on low-resolution screens (Windows GDI text engine). On this PC only."


class SharpTextControl(SettingRow):
    """On / off switch for sharp text. Saves to this PC's QSettings at once; applies after a restart."""

    def __init__(self, parent=None):
        switch = Switch(checked=ui_scale.read_sharp_text(), tooltip="Sharp text")
        super().__init__("Sharp text", _SHARP_SUB, switch, parent, width="auto")
        self.switch = switch
        switch.toggled.connect(self._save)

    def _save(self, enabled: bool) -> None:
        ui_scale.write_sharp_text(enabled)
        self.set_sub(f"Saved: sharp text {'on' if enabled else 'off'}. Restart Sera to apply.")


class DisplayScaleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display scale")
        self.setMinimumWidth(600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.frame = DialogFrame("Display scale", _HINT, self, icon="mdi.monitor")
        self.control = DisplayScaleControl()
        # Stacked: beside three controls the "Running at ..." line had no room (clipped at 520 px).
        self.control.set_compact(True)
        self.frame.add_widget(self.control)
        self.sharp_text = SharpTextControl()
        self.sharp_text.set_compact(True)
        self.frame.add_widget(self.sharp_text)
        close = self.frame.add_action("Close", variant="secondary")
        close.clicked.connect(self.accept)
        self.frame.closeRequested.connect(self.accept)
        layout.addWidget(self.frame)
