"""Width modes for adaptive pages (ui/utils/responsive.py): thresholds and the hysteresis gap."""
import pytest

from ui.utils.responsive import COMPACT_BELOW, WIDE_ABOVE, WidthMode, mode_for, WidthAware


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


# ---- WidthAware mixin (R-1) ------------------------------------------------------

def _qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_width_aware_initial_state_and_mode_switching():
    _qapp()
    class DummyScreen(WidthAware):
        pass

    screen = DummyScreen()
    assert screen.width_mode is WidthMode.WIDE
    assert not screen.is_compact

    screen.apply_width_mode(WidthMode.COMPACT)
    assert screen.width_mode is WidthMode.COMPACT
    assert screen.is_compact

    screen.apply_width_mode("wide")
    assert screen.width_mode is WidthMode.WIDE
    assert not screen.is_compact

    screen.apply_width_mode("compact")
    assert screen.width_mode is WidthMode.COMPACT
    assert screen.is_compact


def test_width_aware_check_width_mode_with_hysteresis():
    _qapp()
    class DummyScreen(WidthAware):
        pass

    screen = DummyScreen()
    assert screen.width_mode is WidthMode.WIDE

    # Below 1080 -> COMPACT
    assert screen.check_width_mode(1050) is WidthMode.COMPACT
    assert screen.is_compact

    # Inside gap (1080 - 1120) -> stays COMPACT
    assert screen.check_width_mode(1100) is WidthMode.COMPACT
    assert screen.is_compact

    # Above 1120 -> WIDE
    assert screen.check_width_mode(1130) is WidthMode.WIDE
    assert not screen.is_compact

    # Inside gap (1080 - 1120) -> stays WIDE
    assert screen.check_width_mode(1090) is WidthMode.WIDE
    assert not screen.is_compact


def test_width_aware_resize_event():
    app = _qapp()
    from PySide6.QtWidgets import QWidget

    class ResponsiveScreen(WidthAware, QWidget):
        def __init__(self):
            super().__init__()

    screen = ResponsiveScreen()
    screen.show()
    screen.resize(1300, 700)
    app.processEvents()
    assert screen.width_mode is WidthMode.WIDE

    screen.resize(1000, 700)
    app.processEvents()
    assert screen.width_mode is WidthMode.COMPACT
    assert screen.is_compact

    # Inside gap: stays compact
    screen.resize(1100, 700)
    app.processEvents()
    assert screen.width_mode is WidthMode.COMPACT

    # Above 1120: back to wide
    screen.resize(1200, 700)
    app.processEvents()
    assert screen.width_mode is WidthMode.WIDE
    assert not screen.is_compact
    screen.close()


def test_width_aware_propagates_to_kit_bars_and_grids():
    _qapp()
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    from ui.components.kit import ChipBar, SelectionBar, DataGrid, GridFrame, GridFooter, PageHeader, Tile

    class Screen(WidthAware, QWidget):
        def __init__(self):
            super().__init__()
            lay = QVBoxLayout(self)

            self.header = PageHeader("All Clients", count=42, lead="Active clients in this office")
            lay.addWidget(self.header)

            self.chip_bar = ChipBar()
            self.chip_bar.add_chip("All")
            self.chip_bar.add_chip("Archived", compact_hide=True)
            lay.addWidget(self.chip_bar)

            self.selection_bar = SelectionBar()
            self.selection_bar.set_who("3 clients")
            self.act_edit = self.selection_bar.add_action("Edit")
            lay.addWidget(self.selection_bar)

            self.grid = DataGrid(rows=3, columns=3)
            self.grid.set_column(0, kind="text", width=200)
            self.grid.set_column(1, kind="text", width=150)
            self.grid.set_column(2, kind="text", width=120, compact_hidden=True)
            self.footer = GridFooter()
            self.footer.add_hint("Enter", "opens the top result", compact_hide=True)
            self.frame = GridFrame(self.grid, self.footer)
            lay.addWidget(self.frame)

            self.tile = Tile(value="15", label="Active")
            lay.addWidget(self.tile)

    screen = Screen()
    screen.resize(1300, 800)

    # Initial WIDE state
    assert screen.width_mode is WidthMode.WIDE
    assert not screen.header.is_compact()
    assert not screen.header.lead_label.isHidden()
    assert not screen.chip_bar.is_compact()
    assert not screen.chip_bar.chip("Archived").isHidden()
    assert not screen.selection_bar.is_compact()
    assert not screen.act_edit.is_icon_only()
    assert not screen.grid.compact
    assert not screen.frame.footer.compact
    assert not screen.tile.is_compact()

    # Switch to COMPACT
    screen.apply_width_mode(WidthMode.COMPACT)
    assert screen.width_mode is WidthMode.COMPACT
    assert screen.header.is_compact()
    assert screen.header.lead_label.isHidden()
    assert screen.chip_bar.is_compact()
    assert screen.chip_bar.chip("Archived").isHidden()
    assert screen.selection_bar.is_compact()
    assert screen.act_edit.is_icon_only()
    assert screen.grid.compact
    assert screen.grid.isColumnHidden(2)
    assert screen.frame.footer.compact
    assert screen.tile.is_compact()

    # Switch back to WIDE
    screen.apply_width_mode(WidthMode.WIDE)
    assert screen.width_mode is WidthMode.WIDE
    assert not screen.header.is_compact()
    assert not screen.header.lead_label.isHidden()
    assert not screen.chip_bar.is_compact()
    assert not screen.chip_bar.chip("Archived").isHidden()
    assert not screen.selection_bar.is_compact()
    assert not screen.act_edit.is_icon_only()
    assert not screen.grid.compact
    assert not screen.grid.isColumnHidden(2)
    assert not screen.frame.footer.compact
    assert not screen.tile.is_compact()


def test_width_aware_responsive_columns():
    _qapp()
    from PySide6.QtWidgets import QWidget, QTableWidget, QVBoxLayout

    class Screen(WidthAware, QWidget):
        def __init__(self):
            super().__init__()
            lay = QVBoxLayout(self)
            self.table = QTableWidget(3, 4)
            lay.addWidget(self.table)
            self.setup_responsive_columns(self.table, hidden_columns=[1, 3])

    screen = Screen()
    assert not screen.table.isColumnHidden(1)
    assert not screen.table.isColumnHidden(3)

    screen.apply_width_mode(WidthMode.COMPACT)
    assert screen.table.isColumnHidden(1)
    assert screen.table.isColumnHidden(3)

    screen.apply_width_mode(WidthMode.WIDE)
    assert not screen.table.isColumnHidden(1)
    assert not screen.table.isColumnHidden(3)


def test_width_aware_declarative_compact_hidden_columns():
    _qapp()
    from PySide6.QtWidgets import QWidget, QTableWidget, QVBoxLayout

    class Screen(WidthAware, QWidget):
        compact_hidden_columns = [0, 2]

        def __init__(self):
            super().__init__()
            lay = QVBoxLayout(self)
            self.grid = QTableWidget(2, 3)
            lay.addWidget(self.grid)

    screen = Screen()
    screen.apply_width_mode(WidthMode.COMPACT)
    assert screen.grid.isColumnHidden(0)
    assert screen.grid.isColumnHidden(2)
    assert not screen.grid.isColumnHidden(1)

    screen.apply_width_mode(WidthMode.WIDE)
    assert not screen.grid.isColumnHidden(0)
    assert not screen.grid.isColumnHidden(2)


def test_width_aware_responsive_chips_move_between_layouts():
    app = _qapp()
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
    from ui.components.kit import Chip

    class Screen(WidthAware, QWidget):
        def __init__(self):
            super().__init__()
            root = QVBoxLayout(self)

            # Row 1: Search + Wide filter row
            self.filter_row = QWidget()
            self.filter_layout = QHBoxLayout(self.filter_row)
            self.search_lbl = QLabel("Search")
            self.filter_layout.addWidget(self.search_lbl)

            self.chip_a = Chip("A")
            self.chip_b = Chip("B")
            self.filter_layout.addWidget(self.chip_a)
            self.filter_layout.addWidget(self.chip_b)
            self.filter_layout.addStretch(1)
            root.addWidget(self.filter_row)

            # Row 2: Compact chips row (hidden by default in WIDE)
            self.chip_row = QWidget()
            self.chip_layout = QHBoxLayout(self.chip_row)
            self.chip_layout.addStretch(1)
            self.chip_row.setVisible(False)
            root.addWidget(self.chip_row)

            self.setup_responsive_chips(
                chips=[self.chip_a, self.chip_b],
                wide_layout=self.filter_layout,
                compact_layout=self.chip_layout,
                compact_row=self.chip_row,
            )

    screen = Screen()
    screen.resize(1300, 700)
    screen.show()
    app.processEvents()

    # Initial WIDE: chips are in filter_layout, chip_row is hidden
    assert screen.filter_layout.indexOf(screen.chip_a) >= 0
    assert screen.filter_layout.indexOf(screen.chip_b) >= 0
    assert screen.chip_layout.indexOf(screen.chip_a) == -1
    assert screen.chip_row.isHidden()

    # Switch to COMPACT: chips move to chip_layout, chip_row becomes visible
    screen.apply_width_mode(WidthMode.COMPACT)
    assert not screen.chip_row.isHidden()
    assert screen.chip_layout.indexOf(screen.chip_a) >= 0
    assert screen.chip_layout.indexOf(screen.chip_b) >= 0
    assert screen.filter_layout.indexOf(screen.chip_a) == -1
    assert screen.filter_layout.indexOf(screen.chip_b) == -1

    # Switch back to WIDE: chips return to filter_layout, chip_row hidden
    screen.apply_width_mode(WidthMode.WIDE)
    assert screen.chip_row.isHidden()
    assert screen.filter_layout.indexOf(screen.chip_a) >= 0
    assert screen.filter_layout.indexOf(screen.chip_b) >= 0
    assert screen.chip_layout.indexOf(screen.chip_a) == -1
    assert screen.chip_layout.indexOf(screen.chip_b) == -1
    screen.close()


def test_width_aware_subclass_hooks_and_legacy_compatibility():
    _qapp()
    calls = []

    class HookScreen(WidthAware):
        def on_width_mode_changed(self, mode):
            calls.append(("on_width_mode_changed", mode))

    s1 = HookScreen()
    s1.apply_width_mode(WidthMode.COMPACT)
    assert calls == [("on_width_mode_changed", WidthMode.COMPACT)]

    legacy_calls = []

    class LegacyScreen(WidthAware):
        def _apply_width_mode(self, mode):
            legacy_calls.append(mode)

    s2 = LegacyScreen()
    s2.apply_width_mode(WidthMode.COMPACT)
    assert legacy_calls == [WidthMode.COMPACT]


def test_width_aware_register_explicit_target():
    _qapp()
    received = []

    class CustomWidget:
        def apply_width_mode(self, mode):
            received.append(mode)

    screen = WidthAware()
    custom = CustomWidget()
    screen.register_width_aware(custom)

    screen.apply_width_mode(WidthMode.COMPACT)
    assert received == [WidthMode.COMPACT]
    screen.apply_width_mode(WidthMode.WIDE)
    assert received == [WidthMode.COMPACT, WidthMode.WIDE]

