"""Generate every Sera logo asset from one geometry.

The mark is the evergreen (logo study A, "Evergreen refined"). The app icon places it,
knocked out, on an emerald binder page so the icon also reads as "document".

Usage:  venv\\Scripts\\python tools\\make_logo.py [--preview out.png]

Writes:
  assets/logo/sera_mark.svg            bare evergreen mark (64-unit grid)
  assets/logo/sera_page.svg            app icon: evergreen on the binder page
  assets/logo/icon_here.png/.ico       app + installer icon (square canvas)
  assets/logo/sera_icon.png/.ico       fallback copies used by main.py
  sera_extension*/icon{16,32,48,128}.png

The first run moves the previous files into assets/logo/legacy/.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPainterPathStroker, QPolygonF, QTransform,
)

ROOT = Path(__file__).resolve().parent.parent
LOGO_DIR = ROOT / "assets" / "logo"
EXT_DIRS = [ROOT / "sera_extension", ROOT / "sera_extension_firefox"]

BRAND = "#2E9B5F"
GRID = 64.0
FILL = 0.94  # share of the canvas the mark's bounds occupy
STROKE = 2.4  # round-join stroke that softens the tier corners

TIERS = [
    [(32, 7.5), (41, 20), (23, 20)],
    [(26.5, 25.5), (37.5, 25.5), (47.5, 39), (16.5, 39)],
    [(23.5, 44.5), (40.5, 44.5), (51, 57.5), (13, 57.5)],
]
# (cx, cy, r) punch notches on the centre line; the last one is the trunk cut-out
NOTCHES = [(32, 25.5, 3.0), (32, 44.5, 3.4), (32, 58.5, 4.2)]
# Below this pixel size the two upper notches fall under a pixel, so only the trunk stays.
SMALL_PX = 24

# Binder page, same 64-unit grid
PAGE = QRectF(10, 4, 44, 56)
PAGE_R = 6.5
BITES = [(10, 17, 5.25), (10, 32, 5.25), (10, 47, 5.25)]
TREE_BOX = QRectF(19.5, 11, 29.25, 42)  # right of the bites, centred in the remaining page
# At 16 px the tree on the page shrinks to ~8 px, so that size uses the bare mark.
PAGE_MIN_PX = 24


def mark_path(size_px: int) -> QPainterPath:
    tiers = QPainterPath()
    for pts in TIERS:
        tiers.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        tiers.closeSubpath()
    stroker = QPainterPathStroker()
    stroker.setWidth(STROKE)
    stroker.setJoinStyle(Qt.RoundJoin)
    body = tiers.united(stroker.createStroke(tiers)).simplified()

    notches = NOTCHES if size_px > SMALL_PX else NOTCHES[-1:]
    for cx, cy, r in notches:
        hole = QPainterPath()
        hole.addEllipse(QPointF(cx, cy), r, r)
        body = body.subtracted(hole)
    return body


def tree_transform() -> QTransform:
    """Maps the bare mark (at full detail) into TREE_BOX on the page."""
    br = mark_path(256).boundingRect()
    k = min(TREE_BOX.width() / br.width(), TREE_BOX.height() / br.height())
    t = QTransform()
    t.translate(TREE_BOX.center().x(), TREE_BOX.center().y())
    t.scale(k, k)
    t.translate(-br.center().x(), -br.center().y())
    return t


def page_path(size_px: int) -> QPainterPath:
    page = QPainterPath()
    page.addRoundedRect(PAGE, PAGE_R, PAGE_R)
    for cx, cy, r in BITES:
        bite = QPainterPath()
        bite.addEllipse(QPointF(cx, cy), r, r)
        page = page.subtracted(bite)
    return page.subtracted(tree_transform().map(mark_path(size_px)))


def icon_path(size_px: int) -> QPainterPath:
    return page_path(size_px) if size_px >= PAGE_MIN_PX else mark_path(size_px)


def render(size_px: int, path_fn=icon_path, color: str = BRAND) -> QImage:
    img = QImage(size_px, size_px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    # Fit the shape's real bounds into the canvas (with a small margin) instead of the 64 grid,
    # so it fills the icon like neighbouring apps do.
    path = path_fn(size_px)
    br = path.boundingRect()
    k = size_px * FILL / max(br.width(), br.height())
    p.translate(size_px / 2, size_px / 2)
    p.scale(k, k)
    p.translate(-br.center().x(), -br.center().y())
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.end()
    return img


def _svg_tiers(fill: str) -> str:
    return "\n      ".join(
        f'<path d="M' + " ".join(f"{x:g} {y:g}" for x, y in pts) + f'Z" fill="{fill}" stroke="{fill}"/>'
        for pts in TIERS
    )


def _svg_circles(circles, fill: str) -> str:
    return "\n      ".join(f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{fill}"/>' for cx, cy, r in circles)


def svg_mark() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <title>Sera</title>
  <mask id="notches" maskUnits="userSpaceOnUse" x="0" y="0" width="64" height="64">
    <rect width="64" height="64" fill="#fff"/>
    {_svg_circles(NOTCHES, "#000")}
  </mask>
  <g mask="url(#notches)" stroke-width="{STROKE:g}" stroke-linejoin="round">
      {_svg_tiers(BRAND)}
  </g>
</svg>
"""


def svg_page() -> str:
    t = tree_transform()
    matrix = f"matrix({t.m11():.4f} 0 0 {t.m22():.4f} {t.dx():.4f} {t.dy():.4f})"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <title>Sera</title>
  <mask id="cut" maskUnits="userSpaceOnUse" x="0" y="0" width="64" height="64">
    <rect width="64" height="64" fill="#fff"/>
    {_svg_circles(BITES, "#000")}
    <g transform="{matrix}" stroke-width="{STROKE:g}" stroke-linejoin="round">
      {_svg_tiers("#000")}
      {_svg_circles(NOTCHES, "#fff")}
    </g>
  </mask>
  <rect x="{PAGE.x():g}" y="{PAGE.y():g}" width="{PAGE.width():g}" height="{PAGE.height():g}" rx="{PAGE_R:g}" fill="{BRAND}" mask="url(#cut)"/>
</svg>
"""


def backup_legacy(files: list[Path]) -> None:
    legacy = LOGO_DIR / "legacy"
    if legacy.exists():
        return  # already backed up on an earlier run
    for f in files:
        if f.exists():
            dest = legacy / f.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)


def qimage_to_pil(img: QImage):
    from PIL import Image

    img = img.convertToFormat(QImage.Format_RGBA8888)
    return Image.frombytes("RGBA", (img.width(), img.height()), bytes(img.constBits()))


def write_ico(path: Path, sizes: list[int]) -> None:
    # Pillow's ICO writer downsamples one image, which would smear the notches at small sizes,
    # so each size is rendered on its own and packed with append_images.
    frames = [qimage_to_pil(render(s)) for s in sizes]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])


def preview_sheet(path: Path) -> None:
    sizes = [16, 24, 32, 48, 64, 128]
    pad, w = 24, 24 + sum(s + 24 for s in sizes)
    h = 128 + 2 * pad
    sheet = QImage(w, h * 2, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(sheet)
    for row, bg in enumerate(["#141414", "#FFFFFF"]):
        p.fillRect(0, row * h, w, h, QColor(bg))
        x = pad
        for s in sizes:
            p.drawImage(x, row * h + pad + (128 - s), render(s))
            x += s + 24
    p.end()
    sheet.save(str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=Path, help="only write a preview sheet PNG to this path")
    args = ap.parse_args()
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

    if args.preview:
        preview_sheet(args.preview)
        return 0

    targets = [LOGO_DIR / n for n in ("icon_here.png", "icon_here.ico", "sera_icon.png", "sera_icon.ico")]
    targets += [d / f"icon{s}.png" for d in EXT_DIRS for s in (16, 32, 48, 128)]
    backup_legacy(targets)

    (LOGO_DIR / "sera_mark.svg").write_text(svg_mark(), encoding="utf-8")
    (LOGO_DIR / "sera_page.svg").write_text(svg_page(), encoding="utf-8")
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    for stem in ("icon_here", "sera_icon"):
        render(256).save(str(LOGO_DIR / f"{stem}.png"))
        write_ico(LOGO_DIR / f"{stem}.ico", ico_sizes)
    # The toolbar shows the extension icon on its own, so the page is used at every size there.
    for d in EXT_DIRS:
        for s in (16, 32, 48, 128):
            render(s, page_path).save(str(d / f"icon{s}.png"))
    print("Logo assets written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
