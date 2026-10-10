"""S-6: the Columns menu on All Clients writes the same per-column flags as Settings -> Main screen."""
import ui_lock_support as S

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ui.components.kit import Switch
from ui.windows.search_window import SearchWindow


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    S.seed_clients(db)
    win = SearchWindow(db)
    win.resize(1100, 600)
    win.show()
    S.pump()
    yield db, win
    S.dispose(win)


def _open_menu(win, act):
    """Open the menu (it blocks), run act(switches) once it is up, then close it."""
    def run():
        menu = win._columns_menu
        act(menu.findChildren(Switch))
        menu.close()
    QTimer.singleShot(150, run)
    win._show_columns_menu()


def _flags(db, field):
    return {c["id"]: bool(c[field]) for c in db.get_mcl_columns()}


def test_toggle_writes_the_staff_flag_only(world):
    db, win = world
    before = _flags(db, "show_in_search")
    admin_before = _flags(db, "admin_show_in_search")
    seen = []
    win.columns_changed.connect(lambda: seen.append(1))
    target = next(cid for cid, on in before.items() if on)

    def act(switches):
        assert len(switches) == len(before)
        switches[list(before).index(target)].setChecked(False)
        QApplication.processEvents()

    _open_menu(win, act)
    after = _flags(db, "show_in_search")
    assert after[target] is False
    assert {k: v for k, v in after.items() if k != target} == {k: v for k, v in before.items() if k != target}
    assert _flags(db, "admin_show_in_search") == admin_before
    assert seen == [1]


def test_last_visible_column_cannot_be_switched_off(world):
    db, win = world
    ids = list(_flags(db, "show_in_search"))
    keep = ids[0]
    db.bulk_update_mcl_visibility([keep])

    def act(switches):
        sw = switches[0]
        assert not sw.isEnabled()
        assert sw.isChecked()

    _open_menu(win, act)
    assert [cid for cid, on in _flags(db, "show_in_search").items() if on] == [keep]


def test_admin_mode_writes_the_admin_flag(world):
    db, win = world
    win.set_admin_mode(True)
    staff_before = _flags(db, "show_in_search")
    admin_before = _flags(db, "admin_show_in_search")
    target = next(cid for cid, on in admin_before.items() if on)

    def act(switches):
        switches[list(admin_before).index(target)].setChecked(False)
        QApplication.processEvents()

    _open_menu(win, act)
    assert _flags(db, "admin_show_in_search")[target] is False
    assert _flags(db, "show_in_search") == staff_before
