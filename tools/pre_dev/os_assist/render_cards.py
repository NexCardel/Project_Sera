"""
tools/pre_dev/os_assist/render_cards.py
---------------------------------------
Renders every OS Assist card state offscreen to PNG (docs/os_assist/shots/) for the look-alike
check against the extension's in-page cards. No window is shown, no browser is touched, no UIA.
Fictional data only.

    ../APP/venv/Scripts/python.exe tools/pre_dev/os_assist/render_cards.py [--out DIR]

Each card is painted on a light page-grey margin at 2x (1 CSS px = 2 image px), as slid in, with
its timer bar 30 % run.
"""
import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":                               # offscreen has no font folder of its own
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt, QPoint                      # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap        # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget        # noqa: E402

SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login"}
TRACES = {"id": 9, "name": "TRACES", "login_page_link": "https://www.tdscpc.gov.in/app/login.xhtml"}
CLIENT = "Fictional Traders Pvt Ltd"
USER, PASSWORD = "ABCDE1234F", "Fict-ional#1"
NEEDS_CONFIRM = "This password has not been verified by SCC yet. Inject it only if you expect it to work."
PAGE = QColor("#F3F4F6")
MARGIN = 24


class _NoWin:
    def is_window(self, hwnd):
        return False

    def is_iconic(self, hwnd):
        return False

    def window_rect(self, hwnd):
        return None

    def add_ex_style(self, hwnd, bits):
        pass

    def set_owner(self, hwnd, owner):
        pass


class _NoLocator:
    def probe(self, hwnd, service):
        return (True, None)


def _kw():
    return {"locator": _NoLocator(), "win": _NoWin()}


def _shot(card, path: str, timer_left: float = 0.7) -> None:
    """Paint the card frame (as slid in, timer part-run) on a page-grey margin."""
    card._slid_in = True
    card.refit()
    if card.timer is not None:
        card.timer._remaining = card.timer.duration_ms * timer_left
    frame = card.card
    frame.move(0, 0)
    dpr = 2.0
    out = QPixmap(round((frame.width() + 2 * MARGIN) * dpr), round((frame.height() + 2 * MARGIN) * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(PAGE)
    p = QPainter(out)
    frame.render(p, QPoint(MARGIN, MARGIN), renderFlags=QWidget.RenderFlag.DrawChildren)   # translucent, as on screen
    p.end()
    out.save(path)
    print(os.path.relpath(path, ROOT), f"{frame.width()}x{frame.height()}")
    card.dismiss("render")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "os_assist", "shots"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    QApplication.instance() or QApplication([])

    from ui.os_assist.smti_card import SmtiCard
    from ui.os_assist.mecp_card import MecpCard
    from ui.os_assist.sca_card import ScaCard
    from ui.os_assist.sca_chip import ScaChip

    def at(name):
        return os.path.join(args.out, name + ".png")

    nocopy = lambda *a: None

    _shot(SmtiCard(SERVICE, USER, PASSWORD, client_id=1, client_name=CLIENT, flutter=False,
                   copier=nocopy, **_kw()), at("smti"))

    flutter = SmtiCard(TRACES, USER, PASSWORD, client_id=1, client_name=CLIENT, copier=nocopy, **_kw())
    _shot(flutter, at("smti-flutter"))

    _shot(MecpCard(SERVICE, USER, PASSWORD, client_id=1, client_name=CLIENT, copier=nocopy, **_kw()),
          at("mecp"))

    rows = [{"id": 1, "label": "Saved password", "value": "Fict-ional#1"},
            {"id": 2, "label": "Combo 2", "value": "Fict-ional#2"},
            {"id": 3, "label": "Combo 3", "value": "fict-ional#1"}]
    scc = MecpCard(SERVICE, USER, client_id=1, client_name=CLIENT, scc_rows=rows, attempt_id="render-1",
                   copier=nocopy, scc_send=nocopy, ledger=nocopy, **_kw())
    scc.apply_update(failed=["Saved password"], next_label="Combo 2",
                     message="The portal said the password is wrong. Try Combo 2.")
    _shot(scc, at("mecp-scc"))

    _shot(ScaCard("arm-1", 7, CLIENT, "Fictional Owner", "GST", client_id=1, **_kw()), at("sca"))
    _shot(ScaCard("arm-1", 7, CLIENT, "Fictional Owner", "GST", note=NEEDS_CONFIRM, client_id=1, **_kw()),
          at("sca-confirm"))

    _shot(ScaChip(CLIENT, "GST", **_kw()), at("chip"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
