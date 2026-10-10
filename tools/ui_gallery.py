"""
tools/ui_gallery.py — render the UI kit (ui/components/kit.py) to PNGs
=======================================================================
    python tools/ui_gallery.py [out_dir] [--scale F]

Writes one PNG per kit section to out_dir (default Mockups/built/kit/), named
<section>@<scale>.png. --scale sets QT_SCALE_FACTOR before the app starts, so run one process
per scale. Offscreen; nothing opens on screen. K-1 adds the primitives page; K-7 adds every
other component and state.
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

from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget  # noqa: E402

from ui.components import kit  # noqa: E402
from ui.utils.tokens import TOKENS  # noqa: E402


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
    page.setFixedWidth(820)

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


def main():
    from ui.utils.theme import get_theme_stylesheet
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(get_theme_stylesheet())     # the kit must hold up under the app's own sheet
    out = Path(ARGS.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, build in (("primitives", primitives_page), ("inputs", inputs_page),
                        ("containers", containers_page), ("bars", bars_page)):
        page = build()
        page.resize(page.sizeHint())
        page.show()
        app.processEvents()
        path = out / f"{name}@{int(round(ARGS.scale * 100))}.png"
        page.grab().save(str(path))
        print(path)
        page.close()


if __name__ == "__main__":
    main()
