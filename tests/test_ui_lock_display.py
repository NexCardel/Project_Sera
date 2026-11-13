"""
W0-2 behaviour lock, R9: display scale and the sidebar. test_ui_scale.py pins the scale maths, the per-PC storage and
the Display control; test_responsive.py pins the sidebar folding. This adds the Ctrl+= / Ctrl+- / Ctrl+0 handlers
(SeraApp, called on a stand-in), the shortcut wiring in main.py, and the sidebar's Display entry and admin-only entries.
"""
import inspect
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from main import SeraApp
from ui.shell.sidebar import Sidebar
from ui.utils import ui_scale

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def ini_settings(tmp_path, monkeypatch):
    path = str(tmp_path / "scale.ini")
    monkeypatch.setattr(ui_scale, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    return path


def _app(scale):
    alerts = []
    shell = SimpleNamespace(show_alert=lambda text, level="info", duration=0: alerts.append((text, level, duration)))
    return SimpleNamespace(ui_scale=scale, shell=shell), alerts


def test_ctrl_equals_steps_up_from_the_scale_in_use_and_saves_a_manual_value(ini_settings):
    app, alerts = _app(0.90)
    SeraApp._step_display_scale(app, 0.05)
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, pytest.approx(0.95))
    assert alerts == [("Display scale 95 % — restart Sera to apply", "info", 3000)]


def test_later_steps_start_from_the_saved_value_not_the_scale_in_use(ini_settings):
    app, alerts = _app(0.90)
    SeraApp._step_display_scale(app, 0.05)
    SeraApp._step_display_scale(app, 0.05)
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, pytest.approx(1.0))
    SeraApp._step_display_scale(app, -0.05)
    SeraApp._step_display_scale(app, -0.05)
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, pytest.approx(0.90))
    assert [a[0] for a in alerts] == ["Display scale 95 % — restart Sera to apply", "Display scale 100 % — restart Sera to apply",
                                      "Display scale 95 % — restart Sera to apply", "Display scale 90 % — restart Sera to apply"]


def test_steps_stop_at_80_and_125_percent(ini_settings):
    app, _ = _app(1.25)
    SeraApp._step_display_scale(app, 0.05)
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, pytest.approx(1.25))
    app, _ = _app(0.80)
    ui_scale.write_settings(ui_scale.MODE_AUTO)
    SeraApp._step_display_scale(app, -0.05)
    assert ui_scale.read_settings() == (ui_scale.MODE_MANUAL, pytest.approx(0.80))


def test_ctrl_zero_goes_back_to_automatic(ini_settings):
    app, alerts = _app(1.0)
    ui_scale.write_settings(ui_scale.MODE_MANUAL, 0.85)
    SeraApp._reset_display_scale(app)
    assert ui_scale.read_settings() == (ui_scale.MODE_AUTO, None)
    assert alerts == [("Display scale set back to Automatic — restart Sera to apply", "info", 3000)]


def test_the_three_shortcuts_are_wired_on_the_shell():
    src = inspect.getsource(SeraApp)
    for line in ('QShortcut(QKeySequence("Ctrl+="), self.shell)', 'QShortcut(QKeySequence("Ctrl+-"), self.shell)',
                 'QShortcut(QKeySequence("Ctrl+0"), self.shell)', "self._step_display_scale(0.05)",
                 "self._step_display_scale(-0.05)", "self.sc_scale_reset.activated.connect(self._reset_display_scale)",
                 "sidebar.action_display_scale.connect(self._open_display_scale_dialog)"):
        assert line in src, line


@pytest.fixture
def sidebar():
    bar = Sidebar()
    yield bar
    bar.deleteLater()
    APP.processEvents()


def test_the_display_entry_is_there_for_everyone_and_emits_its_signal(sidebar):
    seen = []
    sidebar.action_display_scale.connect(lambda: seen.append("display"))
    for admin in (False, True):
        sidebar.set_admin_mode(admin)
        assert sidebar.btn_display_scale.text().strip() == "Display" and not sidebar.btn_display_scale.isHidden()
        sidebar.btn_display_scale.click()
    assert seen == ["display", "display"]


def test_admin_only_entries_follow_admin_mode(sidebar):
    admin_only = [sidebar.btn_manage_clients, sidebar.btn_audit, sidebar.btn_settings]
    for w in admin_only:
        assert w in sidebar.admin_widgets
    sidebar.set_admin_mode(False)
    assert all(w.isHidden() for w in admin_only)
    assert not any(w.isHidden() for w in (sidebar.btn_search, sidebar.btn_all_clients, sidebar.btn_tracker_dump,
                                          sidebar.btn_new_clients, sidebar.btn_display_scale))
    sidebar.set_admin_mode(True)
    assert not any(w.isHidden() for w in admin_only)
    assert sidebar.toggle_admin.isChecked()


def test_every_entry_emits_its_signal(sidebar):
    seen = []
    for name in ("go_to_search", "action_import_csv", "action_manage_clients", "action_tracker_dump", "action_audit_log",
                 "action_settings"):
        getattr(sidebar, name).connect(lambda n=name: seen.append(n))
    sidebar.set_admin_mode(True)
    for btn in (sidebar.btn_search, sidebar.btn_all_clients, sidebar.btn_new_clients, sidebar.btn_manage_clients,
                sidebar.btn_tracker_dump, sidebar.btn_audit, sidebar.btn_settings):
        btn.click()
    assert seen == ["go_to_search", "go_to_search", "action_import_csv", "action_manage_clients", "action_tracker_dump",
                    "action_audit_log", "action_settings"]
