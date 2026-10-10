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

from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QWidget  # noqa: E402

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


def primitives_page():
    page = QWidget()
    page.setObjectName("gallery")
    page.setStyleSheet(f"QWidget#gallery {{ background: {TOKENS['bg']}; }}")
    grid = QGridLayout(page)
    grid.setContentsMargins(TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"], TOKENS["s_6"])
    grid.setHorizontalSpacing(TOKENS["s_4"])
    grid.setVerticalSpacing(TOKENS["s_3"])
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


def main():
    from ui.utils.theme import get_theme_stylesheet
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(get_theme_stylesheet())     # the kit must hold up under the app's own sheet
    out = Path(ARGS.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page = primitives_page()
    page.resize(page.sizeHint())
    page.show()
    app.processEvents()
    path = out / f"primitives@{int(round(ARGS.scale * 100))}.png"
    page.grab().save(str(path))
    print(path)


if __name__ == "__main__":
    main()
