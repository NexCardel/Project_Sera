"""
responsive.py
-------------
Width modes for pages that rearrange themselves in a narrow window (plan: docs/ui-scale-adaptive-layout-plan.md).

Pages measure their own width (not the screen) and call mode_for() from resizeEvent. The two
thresholds leave a gap, so a page sitting near the edge does not flip between modes on every resize.

WidthAware mixin:
Mix into screens (e.g. `class MyScreen(WidthAware, QWidget):`) so the screen automatically tracks
width mode with hysteresis and propagates `apply_width_mode(mode)` to kit bars, grids, headers,
footers, tiles and panels. Screens only need to list which columns and chips move.
"""
from enum import Enum

COMPACT_BELOW = 1080   # go COMPACT when the page gets narrower than this (logical px)
WIDE_ABOVE = 1120      # come back to WIDE only once the page is wider than this


class WidthMode(Enum):
    WIDE = "wide"
    COMPACT = "compact"


def mode_for(width: int, current: WidthMode) -> WidthMode:
    if current is WidthMode.WIDE and width < COMPACT_BELOW:
        return WidthMode.COMPACT
    if current is WidthMode.COMPACT and width > WIDE_ABOVE:
        return WidthMode.WIDE
    return current


class WidthAware:
    """Mixin for QWidget / QDialog / QMainWindow screens that adapt between WIDE and COMPACT.

    Mix into screens (always place WidthAware before QWidget/QMainWindow/QDialog in the inheritance list):
        class AllClientsWindow(WidthAware, QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                ...
                # Declarative columns & chips:
                self.setup_responsive_columns(self.grid, hidden_columns=[COL_GSTIN, COL_PHONE])
                self.setup_responsive_chips(
                    chips=self.filter_chips,
                    wide_layout=self.filter_layout,
                    compact_layout=self.chip_layout,
                    compact_row=self.chip_row,
                )

            # Optional hook for custom movement:
            def on_width_mode_changed(self, mode: WidthMode):
                compact = mode is WidthMode.COMPACT
                ...
    """

    def _ensure_width_aware(self):
        if not hasattr(self, "_width_mode"):
            self._width_mode = WidthMode.WIDE
        if not hasattr(self, "_width_aware_targets"):
            self._width_aware_targets = []
        if not hasattr(self, "_responsive_chips_cfg"):
            self._responsive_chips_cfg = None
        if not hasattr(self, "_chips_in_compact"):
            self._chips_in_compact = False
        if not hasattr(self, "_responsive_cols_cfg"):
            self._responsive_cols_cfg = None

    @property
    def width_mode(self) -> WidthMode:
        self._ensure_width_aware()
        return self._width_mode

    @property
    def is_compact(self) -> bool:
        return self.width_mode is WidthMode.COMPACT

    def register_width_aware(self, widget):
        """Explicitly register a widget to receive apply_width_mode(mode) calls."""
        self._ensure_width_aware()
        if widget is not None and widget not in self._width_aware_targets:
            self._width_aware_targets.append(widget)
        return widget

    add_width_target = register_width_aware

    def setup_responsive_columns(self, table=None, hidden_columns=None):
        """Register columns to be hidden in COMPACT and shown in WIDE mode.

        Args:
            table: QTableView/QTableWidget or None (falls back to self.grid/self.table).
            hidden_columns: Iterable of integer column indices to hide in COMPACT mode.
        """
        self._ensure_width_aware()
        self._responsive_cols_cfg = {
            "table": table,
            "columns": list(hidden_columns or []),
        }
        self._apply_responsive_columns(self._width_mode)

    def setup_responsive_chips(self, chips, wide_layout, compact_layout, compact_row=None,
                               wide_index=None, compact_index=None):
        """Register chips to move between wide_layout and compact_layout.

        Args:
            chips: Iterable of widgets (or tuples whose first element is a QWidget), or a ChipBar.
            wide_layout: QLayout where chips live in WIDE mode.
            compact_layout: QLayout where chips live in COMPACT mode.
            compact_row: Optional QWidget row holding compact_layout (hidden in WIDE, shown in COMPACT).
            wide_index: Optional index (or callable returning index) in wide_layout.
            compact_index: Optional index (or callable returning index) in compact_layout.
        """
        self._ensure_width_aware()
        self._responsive_chips_cfg = {
            "chips": chips,
            "wide_layout": wide_layout,
            "compact_layout": compact_layout,
            "compact_row": compact_row,
            "wide_index": wide_index,
            "compact_index": compact_index,
        }
        self._apply_responsive_chips(self._width_mode)

    def move_chips(self, compact: bool):
        """Move configured responsive chips to compact (True) or wide (False) layout."""
        mode = WidthMode.COMPACT if compact else WidthMode.WIDE
        self._apply_responsive_chips(mode)

    def check_width_mode(self, width: int | None = None) -> WidthMode:
        """Measure given or current width and apply new mode if hysteresis threshold crossed."""
        self._ensure_width_aware()
        if width is None:
            if hasattr(self, "width") and callable(self.width):
                width = self.width()
            else:
                width = 0
        new_mode = mode_for(width, self._width_mode)
        if new_mode is not self._width_mode:
            self.apply_width_mode(new_mode)
        return self._width_mode

    def resizeEvent(self, event):
        try:
            super().resizeEvent(event)
        except (AttributeError, TypeError):
            pass
        w = None
        if hasattr(event, "size") and callable(event.size):
            sz = event.size()
            if hasattr(sz, "width"):
                w = sz.width()
        if w is None and hasattr(self, "width") and callable(self.width):
            w = self.width()
        if w is not None:
            self.check_width_mode(w)

    def apply_width_mode(self, mode):
        """Switch to given mode ('wide'/'compact' or WidthMode enum) and propagate to targets."""
        self._ensure_width_aware()
        val = getattr(mode, "value", mode)
        mode_str = str(val).lower()
        if mode_str == "compact":
            target_mode = WidthMode.COMPACT
        elif mode_str == "wide":
            target_mode = WidthMode.WIDE
        else:
            target_mode = mode if isinstance(mode, WidthMode) else WidthMode.WIDE

        self._width_mode = target_mode

        # 1. Propagate to kit bars, grids, headers, tiles, panels
        self._propagate_width_mode(target_mode)

        # 2. Responsive columns
        self._apply_responsive_columns(target_mode)

        # 3. Responsive chips
        self._apply_responsive_chips(target_mode)

        # 4. Subclass hooks
        if hasattr(self, "on_width_mode_changed") and callable(self.on_width_mode_changed):
            self.on_width_mode_changed(target_mode)
        elif hasattr(self, "_on_width_mode_changed") and callable(self._on_width_mode_changed):
            self._on_width_mode_changed(target_mode)

        # Legacy _apply_width_mode hook (avoid recursion if it is this method)
        legacy_hook = getattr(self, "_apply_width_mode", None)
        if legacy_hook is not None and callable(legacy_hook):
            func = getattr(legacy_hook, "__func__", legacy_hook)
            my_func = getattr(self.apply_width_mode, "__func__", self.apply_width_mode)
            if func is not my_func:
                try:
                    legacy_hook(target_mode)
                except TypeError:
                    pass

    def _propagate_width_mode(self, mode: WidthMode):
        targets = []
        seen = {id(self)}

        def add_target(t):
            if t is not None and id(t) not in seen:
                if hasattr(t, "apply_width_mode") and callable(t.apply_width_mode):
                    targets.append(t)
                    seen.add(id(t))

        for t in getattr(self, "_width_aware_targets", []):
            add_target(t)

        for attr in ("grid", "table", "data_grid", "chip_bar", "selection_bar",
                     "header", "page_header", "footer", "grid_footer", "grid_frame",
                     "panel", "slide_panel", "status_strip"):
            add_target(getattr(self, attr, None))

        if hasattr(self, "findChildren") and callable(self.findChildren):
            try:
                from PySide6.QtWidgets import QWidget
                for child in self.findChildren(QWidget):
                    add_target(child)
            except Exception:
                pass

        for target in targets:
            try:
                target.apply_width_mode(mode)
            except Exception:
                pass

    def _apply_responsive_columns(self, mode: WidthMode):
        table = None
        columns = None

        if self._responsive_cols_cfg:
            table = self._responsive_cols_cfg.get("table")
            columns = self._responsive_cols_cfg.get("columns")

        if columns is None:
            columns = getattr(self, "compact_hidden_columns", None)
            if columns is None:
                columns = getattr(self, "compact_columns", None)

        if columns is not None:
            if table is None:
                table = getattr(self, "grid", None) or getattr(self, "table", None) or getattr(self, "results_table", None)
            if table is not None and hasattr(table, "setColumnHidden"):
                compact = (mode is WidthMode.COMPACT)
                for col in columns:
                    try:
                        table.setColumnHidden(col, compact)
                    except Exception:
                        pass

    def _apply_responsive_chips(self, mode: WidthMode):
        cfg = self._responsive_chips_cfg
        if not cfg:
            return

        compact = (mode is WidthMode.COMPACT)
        if compact == getattr(self, "_chips_in_compact", False):
            compact_row = cfg.get("compact_row")
            if compact_row is not None and hasattr(compact_row, "setVisible"):
                compact_row.setVisible(compact)
            return

        chips = cfg["chips"]
        wide_layout = cfg["wide_layout"]
        compact_layout = cfg["compact_layout"]
        compact_row = cfg.get("compact_row")
        wide_index = cfg.get("wide_index")
        compact_index = cfg.get("compact_index")

        def _find_spacer_or_end(layout):
            for i in reversed(range(layout.count())):
                item = layout.itemAt(i)
                if item is not None and item.spacerItem() is not None:
                    return i
            return layout.count()

        if hasattr(chips, "chips") and callable(chips.chips):
            items_to_move = [chips]
        elif isinstance(chips, (list, tuple)):
            items_to_move = []
            for item in chips:
                if hasattr(item, "setParent"):
                    items_to_move.append(item)
                elif isinstance(item, (list, tuple)) and len(item) > 0 and hasattr(item[0], "setParent"):
                    items_to_move.append(item[0])
        else:
            items_to_move = [chips] if hasattr(chips, "setParent") else []

        if compact:
            for w in items_to_move:
                wide_layout.removeWidget(w)
                idx = compact_index() if callable(compact_index) else compact_index
                if idx is None:
                    idx = _find_spacer_or_end(compact_layout)
                compact_layout.insertWidget(idx, w)
                w.setVisible(True)
            if compact_row is not None and hasattr(compact_row, "setVisible"):
                compact_row.setVisible(True)
        else:
            for w in items_to_move:
                compact_layout.removeWidget(w)
                idx = wide_index() if callable(wide_index) else wide_index
                if idx is None:
                    idx = _find_spacer_or_end(wide_layout)
                wide_layout.insertWidget(idx, w)
                w.setVisible(True)
            if compact_row is not None and hasattr(compact_row, "setVisible"):
                compact_row.setVisible(False)

        self._chips_in_compact = compact
