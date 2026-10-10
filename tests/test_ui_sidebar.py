"""R-2: the shell sidebar on the kit — logo, brand, groups, one All Clients entry, Display for everyone,
no Manage Staff Users, the Auto-sync pill, and no style literals in ui/shell."""
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":   # offscreen Qt does not see Windows fonts otherwise (only matters when this file runs first)
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontInfo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.components import kit
from ui.shell.app_shell import AppShell
from ui.shell.sidebar import SIDEBAR_WIDTH, Sidebar
from ui.utils.tokens import TOKENS

APP = QApplication.instance() or QApplication([])
SHELL_DIR = Path(__file__).resolve().parent.parent / "ui" / "shell"


@pytest.fixture
def sidebar():
    bar = Sidebar()
    bar.show()
    APP.processEvents()
    yield bar
    bar.close()
    bar.deleteLater()
    APP.processEvents()


def _visible_text(bar):
    return [w.text() for w in bar.findChildren(kit.NavItem) if w.isVisible()]


def test_width_comes_from_the_token(sidebar):
    assert SIDEBAR_WIDTH == TOKENS["w_side"] == sidebar.width()


def test_the_real_logo_is_drawn_from_the_svg(sidebar):
    logo = sidebar.findChild(type(sidebar.profile_name), "SidebarLogo")
    pix = logo.pixmap()
    assert pix is not None and not pix.isNull()
    image = pix.toImage()
    centre = image.pixelColor(image.width() // 2, int(image.height() * 0.62))
    assert centre.alpha() > 200 and centre.green() > centre.red()   # the evergreen mark, not the initials


def test_brand_name_is_not_truncated(sidebar):
    title = sidebar.brand_title
    assert title.text() == "Aman Associates"
    if "Segoe" not in QFontInfo(title.font()).family():
        pytest.skip("the offscreen platform has no Segoe UI here, so text widths are not the real ones")
    assert title.width() >= title.sizeHint().width()


def test_groups_and_entries_for_staff(sidebar):
    sidebar.set_admin_mode(False)
    APP.processEvents()
    assert _visible_text(sidebar) == ["All Clients", "Tracker Dump", "Import CSV", "Display"]
    labels = [w.text() for w in sidebar.findChildren(kit.NavLabel) if w.isVisible()]
    assert labels == ["CLIENTS", "TOOLS"]


def test_groups_and_entries_for_admin(sidebar):
    sidebar.set_admin_mode(True)
    APP.processEvents()
    assert _visible_text(sidebar) == ["All Clients", "Manage Clients", "Tracker Dump", "Audit Log",
                                      "Import CSV", "Display", "Options"]
    labels = [w.text() for w in sidebar.findChildren(kit.NavLabel) if w.isVisible()]
    assert labels == ["CLIENTS", "TOOLS", "ADMIN"]


def test_search_is_the_same_entry_as_all_clients_and_staff_users_is_gone(sidebar):
    assert sidebar.btn_search is sidebar.btn_all_clients
    assert not hasattr(sidebar, "btn_manage_staff") and not hasattr(sidebar, "action_manage_staff")
    texts = [w.text() for w in sidebar.findChildren(kit.NavItem)]
    assert "Search" not in texts and "Manage Staff Users" not in texts


def test_all_clients_shows_its_ctrl_k_hint(sidebar):
    keys = [k.text() for k in sidebar.btn_all_clients.findChildren(kit.Key)]
    assert keys == ["Ctrl K"]


def test_clicking_an_entry_marks_only_it_active(sidebar):
    sidebar.set_admin_mode(True)
    assert sidebar.btn_all_clients.is_active()
    sidebar.btn_tracker_dump.click()
    active = [w.text() for w in sidebar.findChildren(kit.NavItem) if w.is_active()]
    assert active == ["Tracker Dump"]
    sidebar.set_active_navigation(sidebar.btn_manage_clients)
    assert [w.text() for w in sidebar.findChildren(kit.NavItem) if w.is_active()] == ["Manage Clients"]


def test_nav_item_paints_active_wash_and_edge(sidebar):
    item = sidebar.btn_tracker_dump
    off = item.grab().toImage().pixelColor(2, item.height() // 2)
    item.set_active(True)
    on = item.grab().toImage().pixelColor(2, item.height() // 2)
    assert on != off and on.green() > on.red()


def test_toggle_button_keeps_its_tooltip_and_signal(sidebar):
    seen = []
    sidebar.toggle_requested.connect(lambda: seen.append(1))
    assert "Ctrl+B" in sidebar.btn_toggle_sidebar.toolTip()
    sidebar.btn_toggle_sidebar.click()
    assert seen == [1]


def test_sync_pill_is_a_soft_labelled_action_and_still_broadcasts(sidebar):
    pill = sidebar.sync_status_badge
    assert isinstance(pill, kit.LabelledAction) and pill.variant == "soft"
    assert pill.text() == "Auto-sync active"
    seen = []
    sidebar.action_trigger_sync.connect(lambda: seen.append(1))
    pill.click()
    assert seen == [1] and pill.variant == "primary" and "Syncing" in pill.text()
    sidebar._sync_back_to_idle()
    assert pill.variant == "soft" and pill.text() == "Auto-sync active"


def test_sync_notices_use_the_same_pill_and_return_to_idle(sidebar):
    pill = sidebar.sync_status_badge
    sidebar.notify_sync_sent(2, 3)
    assert pill.text().startswith("Synced 2/3 PCs") and pill.variant == "primary"
    sidebar.notify_sync_received("amit", "PC-01")
    assert pill.text().startswith("Live sync") and "at" in pill.toolTip()
    sidebar._sync_timer.stop()
    sidebar._sync_back_to_idle()
    assert pill.variant == "soft"


def test_pill_text_fits_inside_the_sidebar(sidebar):
    pill = sidebar.sync_status_badge
    sidebar.notify_sync_sent(12, 12)
    assert pill.fontMetrics().horizontalAdvance(pill.text()) + 60 <= pill.width()
    sidebar._sync_timer.stop()


def test_profile_row_opens_sync_for_admins_only(sidebar):
    seen = []
    sidebar.action_open_sera_sync.connect(lambda: seen.append(1))
    sidebar.set_admin_mode(False)
    QTest.mouseClick(sidebar.profile_row, Qt.LeftButton)
    assert seen == []
    sidebar.set_admin_mode(True)
    QTest.mouseClick(sidebar.profile_row, Qt.LeftButton)
    assert seen == [1]


def test_long_staff_name_is_elided(sidebar):
    sidebar.set_user_name("A" * 80)
    assert sidebar.profile_name.text().endswith("…")


def test_shell_uses_the_token_width_and_still_folds_by_width():
    shell = AppShell()
    assert shell.sidebar_target_width == TOKENS["w_side"]


def test_no_style_literals_in_the_shell_sidebar():
    src = (SHELL_DIR / "sidebar.py").read_text(encoding="utf-8")
    assert not re.search(r"setStyleSheet\(\s*[\"'f]", src)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", src)
