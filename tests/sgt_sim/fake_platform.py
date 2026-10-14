"""
The capture loop's Platform (core/vsdc/vsdc_capture.Platform) over the scripted browser.

Screenshots are small grey images built from what is on screen at the virtual time: the page's own
shade, one more 10 x 10 cell for every typed character, and `cells` cells for every toast showing. The
fake OCR reads the text the image carries (`sim_lines`), which is the page's text plus any toast's -
what a human sees.
"""
from typing import Any, List

from PIL import Image, ImageDraw

from core.vsdc import vsdc_windows
from core.vsdc.vsdc_windows import WindowInfo

from .fake_backend import VirtualClock
from .fake_browser import Browser

SIZE = 320                        # 32 x 32 cells of 10 px: the gate's signature grid
TYPED_ROW, TOAST_ROW = 5, 20


class FakePlatform:
    def __init__(self, browser: Browser, clock: VirtualClock) -> None:
        self._b, self._vc = browser, clock
        self.shots = 0

    def foreground(self) -> int:
        return self._b.front_at(self._vc.rel())

    def windows(self, foreground: int) -> List[WindowInfo]:
        """Foreground only, as vsdc_windows.browser_windows does today."""
        w = self._b.window_of(foreground)
        if w is None or not w.alive_at(self._vc.rel()):
            return []
        page = w.page_at(self._vc.rel())
        title = page.window_title() if page is not None else "New Tab - Google Chrome"
        info = WindowInfo(hwnd=w.hwnd, title=title, proc_name="chrome.exe")
        return [info] if vsdc_windows.is_watchable(info) else []

    def screenshot(self, hwnd: int) -> Any:
        self.shots += 1
        t = self._vc.rel()
        w = self._b.window_of(hwnd)
        if w is None or not w.alive_at(t):
            return None
        tab = w.selected_at(t)
        page = tab.page_at(t)
        base = page.shade() if page is not None else 8
        img = Image.new("L", (SIZE, SIZE), base)
        draw = ImageDraw.Draw(img)
        for i in range(tab.typed_cells_at(t)):
            x, y = i % 32, TYPED_ROW + i // 32
            draw.rectangle((x * 10, y * 10, x * 10 + 9, y * 10 + 9), fill=(base + 64) % 256)
        lines = list(page.lines) if page is not None else []
        for toast in tab.toasts_at(t):
            for i in range(toast.cells):
                x, y = i % 32, TOAST_ROW + i // 32
                draw.rectangle((x * 10, y * 10, x * 10 + 9, y * 10 + 9), fill=(base + 112) % 256)
            lines += toast.lines
        img.sim_lines = lines
        return img

    def is_window(self, hwnd: int) -> bool:
        w = self._b.window_of(hwnd)
        return w is not None and w.alive_at(self._vc.rel())

    def now(self) -> float:
        return self._vc.now()


class FakeOcr:
    """scan_image(frame, region_type=...) -> {"lines": [...]}: the text the frame carries."""

    def __init__(self) -> None:
        self.scans = 0

    def scan_image(self, img: Any, region_type: str = "full", **_kw) -> dict:
        self.scans += 1
        return {"lines": list(getattr(img, "sim_lines", []) or [])}
