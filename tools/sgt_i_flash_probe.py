"""
tools/sgt_i_flash_probe.py - live check for SGT-I step 9's UIA event listener (W10-1)
====================================================================================
Open tools/sgt_i_flash_page.html in Edge yourself (a fictional page that shows a 1-second toast
every 3 s, an alert and a dialog every 6 s), then run:

    python tools/sgt_i_flash_probe.py [seconds]

It finds that window by its title, points FlashWatcher at it and prints what flashed. Read-only:
it never opens, focuses or clicks anything. Needs an unlocked desktop.
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.sgt_i.uia_events import FlashWatcher  # noqa: E402

TITLE = "SGT flash test page"
user32 = ctypes.windll.user32


def find_window():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if TITLE in buf.value and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(_enum, 0)
    return found[0] if found else None


def main(seconds: float = 15.0) -> int:
    hwnd = find_window()
    if not hwnd:
        print(f"No window titled '{TITLE}' - open tools/sgt_i_flash_page.html in Edge first.")
        return 1
    w = FlashWatcher()
    w.watch(hwnd, "sgt-i-flash-page")
    end = time.time() + seconds
    while time.time() < end and not w.disabled:
        time.sleep(0.5)
        for f in w.take(hwnd):
            print(f"{f.kind:14s} {list(f.lines)}")
    print(f"caught {w.caught}" + (f" - listening off: {w.disabled}" if w.disabled else ""))
    w.stop()
    return 0 if w.caught else 2


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 15.0))
