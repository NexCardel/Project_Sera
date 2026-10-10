"""Width modes for adaptive pages (ui/utils/responsive.py): thresholds and the hysteresis gap."""
import pytest

from ui.utils.responsive import COMPACT_BELOW, WIDE_ABOVE, WidthMode, mode_for


def test_thresholds_are_the_planned_ones():
    assert (COMPACT_BELOW, WIDE_ABOVE) == (1080, 1120)


def test_goes_compact_below_1080():
    assert mode_for(1079, WidthMode.WIDE) is WidthMode.COMPACT
    assert mode_for(1080, WidthMode.WIDE) is WidthMode.WIDE


def test_comes_back_to_wide_only_above_1120():
    assert mode_for(1121, WidthMode.COMPACT) is WidthMode.WIDE
    assert mode_for(1120, WidthMode.COMPACT) is WidthMode.COMPACT


@pytest.mark.parametrize("start", [WidthMode.WIDE, WidthMode.COMPACT])
def test_no_flapping_across_the_gap(start):
    mode = start
    for width in (1100, 1090, 1110, 1085, 1115, 1080, 1120, 1100):
        mode = mode_for(width, mode)
        assert mode is start


def test_a_full_sweep_switches_once_each_way():
    mode, switches = WidthMode.WIDE, 0
    widths = list(range(1300, 900, -10)) + list(range(900, 1300, 10))
    for width in widths:
        new = mode_for(width, mode)
        switches += new is not mode
        mode = new
    assert switches == 2 and mode is WidthMode.WIDE


# ---- app shell: the sidebar follows the window width unless the user has toggled it -------------

def _shell():
    from PySide6.QtWidgets import QApplication
    from ui.shell.app_shell import AppShell
    app = QApplication.instance() or QApplication([])
    shell = AppShell()
    shell.resize(1300, 800)
    shell.show()
    for _ in range(4):
        app.processEvents()
    return app, shell


def test_the_sidebar_folds_on_a_narrow_window_and_opens_on_a_wide_one():
    app, shell = _shell()
    try:
        shell.resize(900, 700)
        app.processEvents()
        assert shell.sidebar_collapsed
        shell.resize(1040, 700)                     # inside the gap: stays folded
        app.processEvents()
        assert shell.sidebar_collapsed
        shell.resize(1100, 700)
        app.processEvents()
        assert not shell.sidebar_collapsed
    finally:
        shell.close()


def test_a_manual_toggle_wins_over_the_window_width():
    app, shell = _shell()
    try:
        shell.toggle_sidebar()                      # the user folds it by hand
        app.processEvents()
        assert shell.sidebar_collapsed
        shell.resize(1200, 700)                     # wide again, but the user chose
        app.processEvents()
        assert shell.sidebar_collapsed
    finally:
        shell.close()
