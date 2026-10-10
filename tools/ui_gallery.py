"""
tools/ui_gallery.py — render the UI kit (ui/components/kit.py) to PNGs
=======================================================================
    python tools/ui_gallery.py [out_dir] [--scale F]

Writes one PNG per kit section to out_dir (default Mockups/built/kit/), named
<section>@<scale>.png. --scale sets QT_SCALE_FACTOR before the app starts, so run one process
per scale. Offscreen; nothing opens on screen. Sections: primitives, inputs, containers, bars,
grid, frames, and states (normal / hover / focus / disabled of every interactive component).
Disabled, checked, long text, empty and COMPACT states sit on each section's own page. The images
are the contract: a screen package may not change them (BUILD-PLAN rule 14). Both scales:
    python tools/ui_gallery.py && python tools/ui_gallery.py --scale 1.25
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_ap = argparse.ArgumentParser()
_ap.add_argument("out_dir", nargs="?", default=str(ROOT / "Mockups" / "built" / "kit"))
_ap.add_argument("--scale", type=float, default=1.0)
ARGS = _ap.parse_args()
os.environ["QT_SCALE_FACTOR"] = f"{ARGS.scale:.2f}"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Offscreen Qt on Windows does not see the system fonts and falls back to an all-caps face;
# point it at them so the PNGs show Segoe UI as the real app does.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget  # noqa: E402

from ui.components import kit  # noqa: E402
from ui.utils.tokens import KIT, TOKENS  # noqa: E402


def _row(grid, r, caption, widgets):
    cap = QLabel(caption)
    cap.setStyleSheet(f"color: {TOKENS['muted']}; font-size: {TOKENS['f_s']}px;")
    grid.addWidget(cap, r, 0)
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(TOKENS["s_2"])
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    grid.addWidget(holder, r, 1)


def _blank_page():
    page = QWidget()
    page.setObjectName("gallery")
    page.setStyleSheet(f"QWidget#gallery {{ background: {TOKENS['bg']}; }}")
    grid = QGridLayout(page)
    grid.setContentsMargins(TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"])
    grid.setHorizontalSpacing(TOKENS["s_4"])
    grid.setVerticalSpacing(TOKENS["s_3"])
    return page, grid


def inputs_page():
    page, grid = _blank_page()

    def fixed(w, width=230):
        w.setFixedWidth(width)
        return w

    r = 0
    _row(grid, r, "FieldLabel", [kit.FieldLabel("Name of company"), kit.FieldLabel("PAN", required=True)])
    r += 1
    long_in = fixed(kit.Input())
    long_in.setText("A very long client name that runs past the right edge of the field")
    long_in.setCursorPosition(0)
    off = fixed(kit.Input(placeholder="Disabled", icon="mdi.lock-outline"))
    off.setEnabled(False)
    _row(grid, r, "Input plain, placeholder, icon, long, disabled", [
        fixed(kit.Input()), fixed(kit.Input(placeholder="Search clients")),
        fixed(kit.Input(placeholder="Search clients", icon="mdi.magnify")), long_in, off])
    r += 1
    pw = fixed(kit.Input())
    pw.setText("fictional-secret")
    pw.setEchoMode(kit.Input.EchoMode.Password)
    pw.add_trailing(kit.IconButton("mdi.eye-outline", "Show / hide", sm=True))
    pw.add_trailing(kit.IconButton("mdi.content-copy", "Copy", sm=True))
    focus = fixed(kit.Input())
    focus.setText("focused (mint edge)")
    _row(grid, r, "Input with trailing actions, focus", [pw, focus])
    focus.setFocus()
    r += 1
    sel_off = fixed(kit.Select(items=["Disabled"]))
    sel_off.set_disabled_reason("Admin only")
    _row(grid, r, "Select", [fixed(kit.Select(items=["Dark", "Light", "Auto"])), sel_off])
    r += 1
    notes = kit.TextArea(rows=3, placeholder="Add notes for this client…")
    notes.setFixedWidth(300)
    filled = kit.TextArea(rows=3)
    filled.setPlainText("Demo client\nSecond line")
    filled.setFixedWidth(300)
    _row(grid, r, "TextArea empty, filled", [notes, filled])
    r += 1
    step_off = fixed(kit.Stepper(value=5, unit=" min"), 160)
    step_off.set_disabled_reason("Locked")
    _row(grid, r, "Stepper normal, at max, disabled", [
        fixed(kit.Stepper(value=30, minimum=5, maximum=60, unit=" s"), 160),
        fixed(kit.Stepper(value=60, minimum=5, maximum=60, unit=" s"), 160), step_off])
    r += 1
    cb_off = kit.Checkbox("Disabled", checked=True)
    cb_off.set_disabled_reason("Archived")
    cb_long = kit.Checkbox("A long list entry that is squeezed")
    cb_long.setFixedWidth(120)
    _row(grid, r, "Checkbox off, on, disabled, elided",
         [kit.Checkbox("Select row"), kit.Checkbox("Selected", checked=True), cb_off, cb_long])
    r += 1
    sw_off = kit.Switch(checked=True, tooltip="Disabled")
    sw_off.set_disabled_reason("Needs admin")
    _row(grid, r, "Switch off, on, disabled", [kit.Switch(tooltip="Off"), kit.Switch(checked=True, tooltip="On"), sw_off])
    return page


def primitives_page():
    page, grid = _blank_page()
    r = 0
    for variant in kit.LabelledAction.VARIANTS:
        icon = {"primary": "mdi.check", "danger": "mdi.delete-outline"}.get(variant, "mdi.content-copy")
        sm = kit.LabelledAction("Small", variant=variant, sm=True)
        off = kit.LabelledAction("Disabled", variant=variant, icon=icon)
        off.set_disabled_reason("Select a client first")
        only = kit.LabelledAction("Copy", variant=variant, icon=icon)
        only.set_icon_only(True)
        _row(grid, r, f"LabelledAction {variant}", [
            kit.LabelledAction(variant.title(), variant=variant, icon=icon),
            kit.LabelledAction("No icon", variant=variant), sm, off, only])
        r += 1
    fill = kit.LabelledAction("Fill", icon="mdi.format-color-fill")
    fill.set_swatch(TOKENS["warn"])
    text = kit.LabelledAction("Text", icon="mdi.format-color-text")
    text.set_swatch(TOKENS["info"])
    _row(grid, r, "LabelledAction swatch", [fill, text])
    r += 1
    icons = []
    for variant in kit.IconButton.VARIANTS:
        icons.append(kit.IconButton("mdi.refresh", f"Refresh ({variant})", variant=variant))
        icons.append(kit.IconButton("mdi.refresh", f"Refresh ({variant}, small)", variant=variant, sm=True))
    dis = kit.IconButton("mdi.undo", "Undo", variant="bordered")
    dis.set_disabled_reason("Nothing to undo")
    _row(grid, r, "IconButton plain / bordered / primary, sm, disabled", icons + [dis])
    r += 1
    seg_off = kit.Segmented(["Cards", "List"], current=1)
    seg_off.setEnabled(False)
    _row(grid, r, "Segmented", [kit.Segmented(["All", "Pending", "Done"]), kit.Segmented(["Only"]), seg_off])
    r += 1
    _row(grid, r, "Key (dark, light)", [kit.Key("Ctrl+B"), kit.Key("Alt+1"), kit.Key("Enter", light=True)])
    r += 1
    _row(grid, r, "Pill", [kit.Pill(t.title(), t) for t in kit.Pill.TONES])
    r += 1
    long_pill = kit.Pill("A very long status text that has to elide", "amber", dot=True)
    long_pill.setMaximumWidth(140)
    _row(grid, r, "Pill with dot, elided", [kit.Pill("Online", "emerald", dot=True),
                                             kit.Pill("Offline", "neutral", dot=True), long_pill])
    r += 1
    long_tag = kit.Tag("password [Secret] and more", "red")
    long_tag.setMaximumWidth(110)
    _row(grid, r, "Tag", [kit.Tag(t.upper(), t) for t in kit.Tag.TONES] + [long_tag])
    r += 1
    _row(grid, r, "StatusDot", [kit.StatusDot(t) for t in kit.StatusDot.TONES])
    return page


def containers_page():
    page = QWidget()
    page.setObjectName("gallery")
    page.setStyleSheet(f"QWidget#gallery {{ background: {TOKENS['bg']}; }}")
    root = QVBoxLayout(page)
    root.setContentsMargins(TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"])
    root.setSpacing(TOKENS["s_3"])
    page.setFixedWidth(760)

    root.addWidget(kit.SectionLabel("Section label"))

    general = kit.Card("Display")
    general.add_action(kit.Pill("Saved", "emerald", dot=True))
    sel = kit.Select(items=["Dark"])
    general.add_row(kit.SettingRow("Theme", "Dark is the only theme for now", sel))
    general.add_row(kit.SettingRow("Mask passwords", "Follows Password Masking Mode",
                                   kit.Switch(checked=True, tooltip="Mask passwords")))
    general.add_row(kit.SettingRow("Idle lock", "Lock after this long without input",
                                   kit.Stepper(value=5, minimum=1, maximum=60, unit=" min")))
    general.add_row(kit.SettingRow("Backup folder", "A long sub-label that wraps onto a second line "
                                   "because the text column is narrower than the sentence.",
                                   kit.LabelledAction("Browse", icon="mdi.folder-outline", sm=True), width="auto"))
    general.add_row(kit.SettingRow("Notes template", "Wide control column",
                                   kit.Input(placeholder="Template"), width="wide"))
    root.addWidget(general)

    em = kit.Card("Active engine", edge="em")
    em.add_widget(QLabel("Body text sits in the padded body."))
    root.addWidget(em)

    danger = kit.Card("Danger zone", edge="danger")
    danger.add_row(kit.SettingRow("Purge archived clients", "Removes them for good",
                                  kit.LabelledAction("Purge", variant="danger", icon="mdi.delete-outline"),
                                  width="auto", danger=True))
    root.addWidget(danger)

    compact = kit.Card("Compact row")
    row = kit.SettingRow("Autofill delay", "Stacked under the text in COMPACT width",
                         kit.Stepper(value=30, minimum=5, maximum=60, unit=" s"))
    row.set_compact(True)
    compact.add_row(row)
    root.addWidget(compact)

    empty = kit.Card("Empty")
    empty.add_row(kit.EmptyState("No clients yet", "Add a client or import a list to begin.",
                                 icon="mdi.account-group-outline"))
    es = kit.EmptyState("Nothing here")
    es.add_action(kit.LabelledAction("Refresh", variant="soft", icon="mdi.refresh", sm=True))
    empty.add_row(es)
    root.addWidget(empty)

    for tone, text in (("w", "Warning: the portal list is older than 7 days."),
                       ("r", "Error: the backup folder cannot be written."),
                       ("g", "Done: 12 clients imported."),
                       ("i", "Info banner with a very long sentence that wraps onto a second line "
                             "when the page is narrow enough to need it, as COMPACT width will.")):
        b = kit.Banner(text, tone)
        if tone == "w":
            b.add_action(kit.LabelledAction("Fix", variant="secondary", sm=True))
        root.addWidget(b)

    for level, text in (("success", "Copied to clipboard"), ("error", "Could not write the backup file")):
        stage = QWidget()
        stage.setFixedHeight(76)
        kit.Toast(stage).show_alert(text, level, 0)
        root.addWidget(stage)
    return page


def bars_page():
    page = QWidget()
    page.setObjectName("gallery")
    page.setStyleSheet(f"QWidget#gallery {{ background: {TOKENS['bg']}; }}")
    root = QVBoxLayout(page)
    root.setContentsMargins(TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"])
    root.setSpacing(TOKENS["s_3"])
    page.setFixedWidth(1080)        # the WIDE selection bar needs a WIDE page (>= 1080 in the mockup)

    # 1. Chips and ChipBar
    root.addWidget(kit.SectionLabel("Chips and ChipBar"))
    bar = kit.ChipBar(single_select=True)
    bar.add_chip("All", count=16, checked=True)
    bar.add_chip("Active today")
    bar.add_chip("Has services")
    bar.add_chip("No services")
    bar.add_chip("Highlighted")
    bar.add_chip("Archived")
    from PySide6.QtWidgets import QMenu
    menu = QMenu(bar)
    menu.addAction("Most viewed / active")
    menu.addAction("Has login credentials")
    bar.add_overflow_chip(menu=menu, text="More")
    root.addWidget(bar)

    # Individual chips: value-when-on, disabled with reason
    val_chip = kit.Chip("Client", value_when_on="Client: Unregistered", checked=True, has_menu=True)
    dis_chip = kit.Chip("Disabled", tooltip="Admin only")
    dis_chip.set_disabled_reason("Requires admin PIN")
    row_chips = kit.ChipBar()
    row_chips.add_chip(val_chip)
    row_chips.add_chip(dis_chip)
    root.addWidget(row_chips)

    # 2. StatusStrip
    root.addWidget(kit.SectionLabel("StatusStrip"))
    strip = kit.StatusStrip()
    strip.add_pill("Sync on", tone="emerald", dot=True)
    strip.add_fact("1", "other PC online")
    strip.add_separator()
    strip.add_text("This PC:", muted=True)
    strip.add_text("OFFICE-PC1", bold=True)
    strip.add_text("· v2.12.0 · rev 1492", muted=True)
    strip.add_separator()
    strip.add_pill("Mode: live")
    root.addWidget(strip)

    # 3. SelectionBar
    root.addWidget(kit.SectionLabel("SelectionBar (empty)"))
    sel_empty = kit.SelectionBar()
    root.addWidget(sel_empty)

    root.addWidget(kit.SectionLabel("SelectionBar (active)"))
    sel_active = kit.SelectionBar()
    sel_active.set_who("Sharma Traders Pvt Ltd")
    sel_active.add_action("Open", variant="soft", icon="mdi.arrow-right", keep_label=True)
    sel_active.add_action("Edit", icon="mdi.pencil")
    sel_active.add_action("Services", icon="mdi.server")
    sel_active.add_action("Archive", icon="mdi.archive")
    sel_active.add_divider()
    sel_active.add_group_label("Cell")
    fill = sel_active.add_action("Fill", icon="mdi.format-color-fill")
    fill.set_swatch(TOKENS["warn"])
    text_act = sel_active.add_action("Text", icon="mdi.format-color-text")
    text_act.set_swatch(TOKENS["em"])
    sel_active.add_action("Clear", icon="mdi.eraser")
    sel_active.add_action(kit.IconButton("mdi.undo", "Undo (Ctrl+Z)", sm=True))
    sel_active.add_action(kit.IconButton("mdi.redo", "Redo (Ctrl+Y)", sm=True))
    sel_active.add_spacer()
    sel_active.add_danger_action("Delete…", icon="mdi.delete-outline")
    root.addWidget(sel_active)

    root.addWidget(kit.SectionLabel("SelectionBar (compact: icon-only, Open keeps label)"))
    sel_compact = kit.SelectionBar()
    sel_compact.set_who("Sharma Traders Pvt Ltd")
    sel_compact.add_action("Open", variant="soft", icon="mdi.arrow-right", keep_label=True)
    sel_compact.add_action("Edit", icon="mdi.pencil")
    sel_compact.add_action("Services", icon="mdi.server")
    sel_compact.add_action("Archive", icon="mdi.archive")
    sel_compact.add_divider()
    sel_compact.add_group_label("Cell")
    fill_c = sel_compact.add_action("Fill", icon="mdi.format-color-fill")
    fill_c.set_swatch(TOKENS["warn"])
    text_c = sel_compact.add_action("Text", icon="mdi.format-color-text")
    text_c.set_swatch(TOKENS["em"])
    sel_compact.add_action("Clear", icon="mdi.eraser")
    sel_compact.add_spacer()
    sel_compact.add_danger_action("Delete…", icon="mdi.delete-outline")
    sel_compact.set_compact(True)
    root.addWidget(sel_compact)

    # 4. UnsavedBar
    root.addWidget(kit.SectionLabel("UnsavedBar"))
    unsaved = kit.UnsavedBar()
    unsaved.set_count(2)
    root.addWidget(unsaved)

    return page


_SVC = {"GST": "#ff6a3d", "Income Tax": "#4d8dff", "Email": "#a77bff", "TRACES": "#e3b341", "EPFO": "#35c4a0"}
_PEOPLE = [   # fictional clients, as in Mockups/fleet/index.html
    ("Sharma Traders Pvt Ltd", "Rohan Sharma", "19ABCDE1000F1Z5", "ABCDE1000F", "98000 00001", ["GST", "Income Tax"]),
    ("Gupta & Sons", "Meera Gupta", "19ABCDE1001F1Z3", "ABCDE1001F", "98000 00002", ["GST", "Income Tax", "Email"]),
    ("Mehta Textiles LLP", "Kiran Mehta", "", "ABCDE1002F", "", ["GST"]),
    ("Bose Electricals", "Anirban Bose", "19ABCDE1003F1Z9", "ABCDE1003F", "98000 00004", ["Income Tax", "TRACES"]),
    ("Roy Pharma Distributors Private Limited", "Sunita Roy", "19ABCDE1004F1Z7", "ABCDE1004F", "98000 00005",
     ["GST", "Income Tax", "Email", "TRACES", "EPFO"]),
    ("Das Hardware", "Pritam Das", "", "ABCDE1005F", "98000 00006", ["Income Tax"]),
]


def _white_grid(compact=False):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QTableWidgetItem
    heads = ["No.", "Name of company", "Name of proprietor", "GSTIN", "PAN", "Ph. no.", "Services"]
    grid = kit.DataGrid(len(_PEOPLE), len(heads))
    grid.setHorizontalHeaderLabels(heads)
    grid.setSortingEnabled(False)
    grid.horizontalHeader().setSortIndicatorShown(True)
    grid.horizontalHeader().setSortIndicator(1, Qt.AscendingOrder)
    grid.set_column(0, kind="num", width=56)
    grid.set_column(1, minimum=160, stretch=2)
    grid.set_column(2, minimum=120, max_share=0.24)
    grid.set_column(3, kind="mono", width=150, minimum=130, priority=2, compact_hidden=True)
    grid.set_column(4, kind="mono", width=112, minimum=100, priority=1)
    grid.set_column(5, kind="num", width=110, minimum=96, priority=3, compact_hidden=True)
    grid.set_column(6, width=210, minimum=120, priority=1)
    for r, (name, owner, gstin, pan, phone, svcs) in enumerate(_PEOPLE):
        for c, text in enumerate([str(r + 1), name, owner, gstin or "—", pan, phone or "—"]):
            grid.setItem(r, c, QTableWidgetItem(text))
        item = QTableWidgetItem(", ".join(svcs))
        item.setData(kit.ROLE_SERVICES, [(s, _SVC[s]) for s in svcs])
        grid.setItem(r, 6, item)
    grid.item(3, 2).setBackground(QColor("#fff3cd"))       # a cell-format highlight from the engine
    grid.item(3, 2).setForeground(QColor("#6b4e00"))
    grid.selectRow(1)
    grid._set_hover(4, QPoint(5, 5))                       # the hover row, drawn as under the mouse
    footer = kit.GridFooter()
    footer.set_count(f"{len(_PEOPLE)} clients")
    footer.add_hint("Enter", "opens the top result")
    footer.add_hint(["↑", "↓"], "move · Ctrl C copies the selected cells", compact_hide=True)
    footer.add_hint("Esc", "closes the panel")
    frame = kit.GridFrame(grid, footer)
    frame.setFixedSize(760 if compact else 1060, 330)
    if compact:
        frame.apply_width_mode("compact")
    return frame


def _dark_grid():
    from PySide6.QtWidgets import QTableWidgetItem
    heads = ["Client", "Filing type", "Period", "Submission status", "Actions", "Updated", "Captured by"]
    rows = [("Sharma Traders Pvt Ltd", "#1 · ABCDE1000F", "GSTR-3B", "Aug 2026", "Filed", "emerald", "Today 11:02", "OFFICE-PC1"),
            ("Unregistered capture", "ABCDE1009F", "ITR-4", "AY 2026-27", "Awaiting e-verification", "amber", "Yesterday 17:40", "FRONT-DESK"),
            ("Bose Electricals", "#4 · ABCDE1003F", "24Q", "Q2 FY 2026-27", "Not submitted", "red", "2 Oct 16:40", "OFFICE-PC1"),
            ("Gupta & Sons", "#2 · ABCDE1001F", "GSTR-1", "Sep 2026", "Draft saved", "blue", "28 Sep 12:01", "FRONT-DESK")]
    grid = kit.DataGrid(len(rows), len(heads), tone="dark")
    grid.setHorizontalHeaderLabels(heads)
    grid.set_tall_rows(True)
    for c, spec in enumerate([dict(minimum=240), dict(width=90, priority=3), dict(width=170),
                              dict(width=210), dict(width=110), dict(kind="num", width=120, priority=2),
                              dict(width=110, priority=1)]):
        grid.set_column(c, **spec)
    for r, (name, sub, filing, period, status, tone, when, pc) in enumerate(rows):
        item = QTableWidgetItem(name)
        item.setData(kit.ROLE_SUB, sub)
        grid.setItem(r, 0, item)
        for c, text in ((1, filing), (2, period), (5, when), (6, pc)):
            grid.setItem(r, c, QTableWidgetItem(text))
        pill = QTableWidgetItem(status)
        pill.setData(kit.ROLE_PILL, (status, tone, True))
        grid.setItem(r, 3, pill)
        acts = QTableWidgetItem("")
        acts.setData(kit.ROLE_ACTIONS, [
            kit.CellAction("view", "mdi.eye-outline", "View raw payload"),
            kit.CellAction("autofill", "mdi.lightning-bolt", "Fast Autofill: Income Tax", primary=True,
                           colour=_SVC["Income Tax"] if r == 2 else None),
            kit.CellAction("more", "mdi.dots-vertical", "More: Inspect · + Create client · Delete container",
                           enabled=r != 3)])
        grid.setItem(r, 4, acts)
    grid.selectRow(0)
    footer = kit.GridFooter(tone="dark")
    footer.set_count("Showing 1 to 4 of 4 entries")
    footer.add_hint("F5", "refreshes")
    frame = kit.GridFrame(grid, footer)
    frame.setFixedSize(1060, 290)
    return frame


def _list_table():
    from PySide6.QtWidgets import QTableWidgetItem
    cols = [("NAME OF COMPANY", "text", "Identity", "emerald", True), ("PAN", "text", "Internal PK anchor", "amber", True),
            ("ID", "number", "ID / auto-serial", "emerald", False), ("GST PASSWORD", "password", "Secret", "red", False),
            ("EMAIL", "password", "", "", True)]
    t = kit.DarkListTable(len(cols), 4)
    t.setHorizontalHeaderLabels(["Column", "Type", "Role", "Main"])
    t.set_column(0, kind="strong", minimum=160)
    t.set_column(1, width=110)
    t.set_column(2, width=190)
    t.set_column(3, width=70)
    for r, (name, typ, role, tone, main) in enumerate(cols):
        t.setItem(r, 0, QTableWidgetItem(name))
        tag = QTableWidgetItem(typ)
        tag.setData(kit.ROLE_TAG, (typ, "red" if typ == "password" else "neutral"))
        t.setItem(r, 1, tag)
        pill = QTableWidgetItem(role or "—")
        if role:
            pill.setData(kit.ROLE_PILL, (role, tone))
        t.setItem(r, 2, pill)
        box = QTableWidgetItem("")
        box.setFlags(box.flags() | Qt.ItemIsUserCheckable)
        box.setCheckState(Qt.Checked if main else Qt.Unchecked)
        t.setItem(r, 3, box)
    t.selectRow(0)
    card = kit.Card("Columns")
    card.add_widget(t)
    t.setFixedHeight(KIT["h_list_head"] + len(cols) * KIT["h_list_row"] + 2)
    card.setFixedWidth(620)
    return card


def grid_page():
    page = QWidget()
    page.setObjectName("gallery")
    page.setStyleSheet(f"QWidget#gallery {{ background: {TOKENS['bg']}; }}")
    root = QVBoxLayout(page)
    root.setContentsMargins(TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"])
    root.setSpacing(TOKENS["s_3"])
    root.addWidget(kit.SectionLabel("DataGrid white: zebra, selected row, highlight cell, service dots, footer"))
    root.addWidget(_white_grid())
    root.addWidget(kit.SectionLabel("DataGrid white, COMPACT (GSTIN and Ph. no. hidden, hint dropped)"))
    root.addWidget(_white_grid(compact=True))
    root.addWidget(kit.SectionLabel("DataGrid dark: two-line cells, status pills, drawn row actions"))
    root.addWidget(_dark_grid())
    root.addWidget(kit.SectionLabel("DarkListTable in a Card: tags, pills, checkboxes"))
    root.addWidget(_list_table())
    return page


def _dialog(title, purpose, *, icon, tone="emerald", full=False, danger=False):
    d = kit.DialogFrame(title, purpose, icon=icon, tone=tone, full=full)
    d.add_close_button()
    d.add_widget(kit.FieldLabel("Name of company"))
    d.add_widget(kit.Input(placeholder="Search clients", icon="mdi.magnify"))
    d.add_action("Cancel")
    d.add_action("Delete" if danger else "Save", variant="danger" if danger else "primary")
    return d


def _scrim_demo():
    desk = QWidget()
    desk.setFixedSize(520, 300)
    desk.setStyleSheet(f"background: {TOKENS['bg']};")
    scrim = kit.Scrim(desk, _dialog("Export clients", "Choose what goes into the file.", icon="mdi.download"))
    scrim.cover_parent()
    scrim.show()
    return desk


def _panel_demo(width, compact):
    host = QWidget()
    host.setFixedSize(width, 340)
    host.setStyleSheet(f"background: {TOKENS['bg']};")
    from ui.shell.slide_panel import SlidePanel
    panel = SlidePanel(host)
    body = QLabel("Panel body")
    body.setStyleSheet(f"color: {TOKENS['text2']}; padding: {TOKENS['s_4']}px; background: transparent;")
    panel.set_widget(body, "Client details")
    panel.set_compact(compact)
    panel_w = panel._calc_panel_width(width)
    panel.setGeometry(width - panel_w, 0, panel_w, 340)
    panel.show()
    return host


def frames_page():
    page = QWidget()
    page.setObjectName("gallery")
    page.setStyleSheet(f"QWidget#gallery {{ background: {TOKENS['bg']}; }}")
    root = QVBoxLayout(page)
    root.setContentsMargins(TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"])
    root.setSpacing(TOKENS["s_3"])

    root.addWidget(kit.SectionLabel("DialogFrame: emerald, amber, danger footer"))
    row = QHBoxLayout()
    row.setSpacing(TOKENS["s_4"])
    for d in (_dialog("Add client", "One line says what this does.", icon="mdi.account-plus"),
              _dialog("Review changes", "Needs a look before saving.", icon="mdi.alert-outline", tone="amber"),
              _dialog("Delete client", "This cannot be undone.", icon="mdi.trash-can-outline", danger=True)):
        d.setFixedSize(340, 220)
        row.addWidget(d)
    row.addStretch(1)
    root.addLayout(row)

    root.addWidget(kit.SectionLabel("DialogFrame full variant, and inside a Scrim"))
    row = QHBoxLayout()
    row.setSpacing(TOKENS["s_4"])
    full = _dialog("Manage clients", "Full-window variant: no rounded corners.", icon="mdi.account-group", full=True)
    full.setFixedSize(420, 260)
    row.addWidget(full)
    row.addWidget(_scrim_demo())
    row.addStretch(1)
    root.addLayout(row)

    root.addWidget(kit.SectionLabel("PageHeader WIDE and COMPACT"))
    for compact in (False, True):
        hd = kit.PageHeader("Manage Clients", count="16 clients", lead="Search, edit and archive client records.")
        hd.add_leading(kit.IconButton("mdi.arrow-left", "Back to Search (Esc)", variant="bordered"))
        hd.add_action(kit.LabelledAction("New client", variant="primary", icon="mdi.plus"))
        hd.set_compact(compact)
        root.addWidget(hd)

    root.addWidget(kit.SectionLabel("Tile: neutral, tones, checked, disabled, COMPACT"))
    row = QHBoxLayout()
    row.setSpacing(TOKENS["s_2"])
    tiles = [kit.Tile("12", "Worked"), kit.Tile("4", "Awaiting", tone="amber"),
             kit.Tile("2", "Not submitted", tone="red"), kit.Tile("38", "Verified", tone="emerald", checked=True),
             kit.Tile("7", "Syncing", tone="blue"), kit.Tile("9", "A long tile label that must elide")]
    tiles[4].set_disabled_reason("Offline")
    small = kit.Tile("21", "Compact", tone="emerald")
    small.set_compact(True)
    tiles.append(small)
    for t in tiles:
        t.setFixedWidth(130)
        row.addWidget(t)
    row.addStretch(1)
    root.addLayout(row)

    root.addWidget(kit.SectionLabel("LogView: tag colours, timestamps"))
    log = kit.LogView()
    log.setFixedSize(720, 170)
    for ts, tag, msg in (("10:02:11", "sync", "Pulled 4 clients from OFFICE-PC1"),
                         ("10:02:14", "guard", "Skipped 1 row: PAN already in use (demo)"),
                         ("10:02:20", "peer", "FRONT-DESK came online"),
                         ("10:02:31", "net", "Reconnected after 3 s"),
                         ("10:02:40", "err", "Push failed: timeout (will retry)"),
                         ("10:02:41", "", "Plain line without a tag")):
        log.add_line(msg, tag, ts=ts)
    root.addWidget(log)

    root.addWidget(kit.SectionLabel("SlidePanel 540 px (WIDE), full width (COMPACT)"))
    row = QHBoxLayout()
    row.setSpacing(TOKENS["s_4"])
    row.addWidget(_panel_demo(900, False))
    row.addWidget(_panel_demo(420, True))
    row.addStretch(1)
    root.addLayout(row)
    return page


STATES = ("normal", "hover", "focus", "disabled")


def _shot(host, make, state, width=None):
    """Grab one widget in one interaction state: the mouse really moves over it, focus is real."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    stage = QWidget(host)
    stage.setObjectName("stage")
    stage.setStyleSheet(f"QWidget#stage {{ background: {TOKENS['bg']}; }}")
    lay = QVBoxLayout(stage)
    lay.setContentsMargins(TOKENS["s_2"], TOKENS["s_2"], TOKENS["s_2"], TOKENS["s_2"])
    w = make()
    if width:
        w.setFixedWidth(width)
    lay.addWidget(w)
    stage.move(0, 0)
    stage.show()
    stage.adjustSize()
    QApplication.processEvents()
    if state == "disabled":
        w.setEnabled(False)
    elif state == "hover":
        QTest.mouseMove(w, w.rect().center())
    elif state == "focus":
        w.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    pm = stage.grab()
    QTest.mouseMove(host, QPoint(host.width() - 2, host.height() - 2))
    stage.hide()
    stage.deleteLater()
    return pm


def _state_row(grid, r, host, caption, make, *, states=STATES, width=None):
    cells = []
    for state in states:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        pic = QLabel()
        pic.setPixmap(_shot(host, make, state, width))
        tag = QLabel(state)
        tag.setStyleSheet(f"color: {TOKENS['faint']}; font-size: {TOKENS['f_xs']}px;")
        lay.addWidget(pic)
        lay.addWidget(tag)
        cells.append(col)
    _row(grid, r, caption, cells)


def states_page():
    """Normal / hover / focus / disabled for every interactive component (checked variants get rows)."""
    page, grid = _blank_page()
    host = QWidget()
    host.resize(520, 200)
    host.show()
    host.activateWindow()
    QApplication.processEvents()
    page._host = host
    r = 0

    def add(caption, make, **kw):
        nonlocal r
        _state_row(grid, r, host, caption, make, **kw)
        r += 1

    for variant in kit.LabelledAction.VARIANTS:
        add(f"LabelledAction {variant}", lambda v=variant: kit.LabelledAction(v.title(), variant=v, icon="mdi.check"))
    for variant in kit.IconButton.VARIANTS:
        add(f"IconButton {variant}", lambda v=variant: kit.IconButton("mdi.refresh", "Refresh", variant=v))
    add("Segmented", lambda: kit.Segmented(["All", "Pending", "Done"], current=1))
    add("Input", lambda: kit.Input(placeholder="Search clients", icon="mdi.magnify"), width=230)
    add("Select", lambda: kit.Select(items=["Dark", "Light"]), width=230)
    add("TextArea", lambda: kit.TextArea(rows=2, placeholder="Add notes…"), width=230)
    add("Stepper", lambda: kit.Stepper(value=30, minimum=5, maximum=60, unit=" s"), width=160)
    add("Checkbox off", lambda: kit.Checkbox("Select row"))
    add("Checkbox on", lambda: kit.Checkbox("Selected", checked=True))
    add("Switch off", lambda: kit.Switch(tooltip="Off"))
    add("Switch on", lambda: kit.Switch(checked=True, tooltip="On"))
    add("Chip", lambda: kit.Chip("Active today"))
    add("Chip checked, count", lambda: kit.Chip("All", count=16, checked=True))
    add("Chip menu", lambda: kit.Chip("Client", has_menu=True))
    add("Tile", lambda: kit.Tile("12", "Worked"), width=130)
    add("Tile checked", lambda: kit.Tile("38", "Verified", tone="emerald", checked=True), width=130)
    return page


def _close_host(page):
    host = getattr(page, "_host", None)
    if host is not None:
        host.close()


def main():
    from ui.utils.theme import get_theme_stylesheet
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(get_theme_stylesheet())     # the kit must hold up under the app's own sheet
    out = Path(ARGS.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, build in (("primitives", primitives_page), ("inputs", inputs_page),
                        ("containers", containers_page), ("bars", bars_page), ("grid", grid_page),
                        ("frames", frames_page), ("states", states_page)):
        page = build()
        page.resize(page.sizeHint())
        page.show()
        app.processEvents()
        path = out / f"{name}@{int(round(ARGS.scale * 100))}.png"
        page.grab().save(str(path))
        print(path)
        page.close()
        _close_host(page)


if __name__ == "__main__":
    main()
