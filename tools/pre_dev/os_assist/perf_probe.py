"""
tools/pre_dev/os_assist/perf_probe.py
-------------------------------------
Measures what an OS Assist dock card costs while it is on screen, on a real browser window.

Launches a throwaway Chrome (temp profile, occlusion off, never the user's own browser) on a local
"busy portal" page (ticker text, a JS-moved element, an auto-scrolling list, a spinner - the kind of
churn GST/IT portals have) or a static page, docks a MECP card (90 s life, never fills anything)
to it, and reports per phase:

  cpu_ms      CPU time per thread of this process (qt = Qt/main thread, hub = WinEvent hook thread,
              uia = UIA worker) and of the whole Chrome process tree
  hook_calls  WinEvent callbacks that reached Python, and how many of them the card actually used
  paints      paint events on the card and on its countdown bar
  probes      UIA host/page-rect probes and their duration
  slide       frames and longest gap between frames during the slide-in
  drag        card moves while the browser window is moved by SetWindowPos, and the lag

Phases: base (page, no card) -> slide -> steady -> drag. Prints one JSON object.

    ../APP/venv/Scripts/python.exe tools/pre_dev/os_assist/perf_probe.py [--page busy|static]
        [--steady 10] [--label before]

No portal is touched, no box is filled, nothing is clicked.
"""
import argparse
import ctypes
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QObject, QEvent                    # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel                    # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

BUSY = """<!doctype html><html><head><meta charset="utf-8"><title>Busy portal</title><style>
body{font-family:Segoe UI;margin:0;background:#f3f4f6}
header{background:#1d3557;color:#fff;padding:14px 24px;font-size:20px}
#ticker{background:#ffe8a3;padding:6px 24px;white-space:nowrap;overflow:hidden}
#mover{position:absolute;left:40px;width:160px;padding:8px;background:#a8dadc}
.spin{width:28px;height:28px;border:4px solid #ccc;border-top-color:#1d3557;border-radius:50%;
animation:s 0.8s linear infinite;margin:16px 24px}@keyframes s{to{transform:rotate(360deg)}}
#list{height:420px;overflow:auto;margin:0 24px;background:#fff}
#list div{padding:6px 10px;border-bottom:1px solid #eee}
</style></head><body><header>Busy portal (fictional)</header><div id="ticker"></div>
<div class="spin"></div><div id="mover">moving box</div><div id="list"></div>
<script>
const list=document.getElementById('list');
for(let i=0;i<400;i++){const d=document.createElement('div');d.textContent='Notice '+i+': return filing date extended';list.appendChild(d);}
let n=0;setInterval(()=>{document.getElementById('ticker').textContent='Latest: update '+(n++)+' - server time '+new Date().toLocaleTimeString();},100);
let t=0;(function f(){t++;document.getElementById('mover').style.top=(140+60*Math.sin(t/30))+'px';
list.scrollTop=(list.scrollTop+1)%(list.scrollHeight-list.clientHeight);requestAnimationFrame(f);})();
</script></body></html>"""

STATIC = """<!doctype html><html><head><meta charset="utf-8"><title>Static portal</title></head>
<body style="font-family:Segoe UI;background:#f3f4f6"><h1>Static portal (fictional)</h1>
<p>Nothing moves on this page.</p></body></html>"""

# ----------------------------------------------------------------------------------- CPU times
k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32


class FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


def _ft(f: FILETIME) -> float:
    return ((f.hi << 32) | f.lo) / 10000.0          # 100 ns -> ms


def thread_cpu_ms(tid: int) -> float:
    if not tid:
        return 0.0
    h = k32.OpenThread(0x0800, False, tid)
    if not h:
        return 0.0
    a, b, kern, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    ok = k32.GetThreadTimes(h, ctypes.byref(a), ctypes.byref(b), ctypes.byref(kern), ctypes.byref(user))
    k32.CloseHandle(h)
    return _ft(kern) + _ft(user) if ok else 0.0


def process_cpu_ms(pid: int) -> float:
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return 0.0
    a, b, kern, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    ok = k32.GetProcessTimes(h, ctypes.byref(a), ctypes.byref(b), ctypes.byref(kern), ctypes.byref(user))
    k32.CloseHandle(h)
    return _ft(kern) + _ft(user) if ok else 0.0


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def process_tree(root: int) -> list:
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    parent = {}
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        parent[e.th32ProcessID] = e.th32ParentProcessID
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    tree, grew = {root}, True
    while grew:
        grew = False
        for pid, ppid in parent.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                grew = True
    return sorted(tree)


def tid_of(name: str) -> int:
    for t in threading.enumerate():
        if t.name == name and t.native_id:
            return t.native_id
    return 0


# ------------------------------------------------------------------------------------- counters
class Stats:
    def __init__(self) -> None:
        self.hook_calls = 0
        self.hook_used = 0
        self.card_paints = 0
        self.bar_paints = 0
        self.probes = 0
        self.probe_ms = []
        self.paint_times = []           # monotonic times of card-window paints (slide-in frames)
        self.card_moves = []            # (time, x, y) of the card window
        self.browser_moves = []         # (time, x, y) of the SetWindowPos calls

    def snap(self) -> dict:
        return {"hook_calls": self.hook_calls, "hook_used": self.hook_used, "card_paints": self.card_paints,
                "bar_paints": self.bar_paints, "probes": self.probes, "probe_ms_n": len(self.probe_ms)}


S = Stats()


def instrument() -> None:
    from core.os_assist import win_events
    from ui.os_assist import dock_card

    orig_event = win_events.WindowWatch._event

    def counted_event(self, event, hwnd, id_object, id_child):
        S.hook_calls += 1
        if event == win_events.EVENT_OBJECT_FOCUS or (hwnd == self.hwnd and win_events.is_window_event(id_object, id_child)):
            S.hook_used += 1
        return orig_event(self, event, hwnd, id_object, id_child)

    win_events.WindowWatch._event = counted_event
    # Some versions may count raw hub callbacks in another place: count at the hub as well.
    if hasattr(win_events.Hub, "_dispatch"):
        orig_dispatch = win_events.Hub._dispatch

        def counted_dispatch(self, *a):
            S.__dict__["hub_dispatch"] = S.__dict__.get("hub_dispatch", 0) + 1
            return orig_dispatch(self, *a)

        win_events.Hub._dispatch = counted_dispatch

    orig_probe = dock_card._probe

    def timed_probe(hwnd, service):
        t = time.perf_counter()
        try:
            return orig_probe(hwnd, service)
        finally:
            S.probes += 1
            S.probe_ms.append((time.perf_counter() - t) * 1000.0)

    dock_card._probe = timed_probe

    from core.os_assist import locate
    for name in ("read_url", "page_rect"):
        orig = getattr(locate, name)

        def timed(*a, _orig=orig, _name=name):
            t = time.perf_counter()
            try:
                return _orig(*a)
            finally:
                S.__dict__.setdefault("split_" + _name, []).append((time.perf_counter() - t) * 1000.0)

        setattr(locate, name, timed)


class PaintCounter(QObject):
    def __init__(self, card) -> None:
        super().__init__()
        self.card = card

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Paint:
            if obj is self.card.timer:
                S.bar_paints += 1
            elif obj is self.card.card:
                S.card_paints += 1
                S.paint_times.append(time.monotonic())
        elif t == QEvent.Move and obj is self.card:
            S.card_moves.append((time.monotonic(), self.card.x(), self.card.y()))
        return False


# --------------------------------------------------------------------------------------- browser
def serve(page: str):
    d = tempfile.mkdtemp(prefix="sera_perf_page_")
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=d, **kw)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, d


def chrome_window(pid: int, timeout: float = 15.0) -> int:
    found = []
    PROTO = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found.clear()

        def cb(h, _l):
            p = wintypes.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid and u32.IsWindowVisible(h) and u32.GetWindowTextLengthW(h) > 0:
                found.append(int(h))
            return True

        u32.EnumWindows(PROTO(cb), 0)
        if found:
            return found[0]
        time.sleep(0.2)
    return 0


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.002)


def cpu_now(chrome_pid: int) -> dict:
    return {"qt": thread_cpu_ms(threading.main_thread().native_id),
            "hub": thread_cpu_ms(tid_of("os-assist-winevents")),
            "uia": thread_cpu_ms(tid_of("os-assist-uia")),
            "chrome": sum(process_cpu_ms(p) for p in process_tree(chrome_pid))}


def phase(app, name: str, seconds: float, chrome_pid: int, out: dict, during=None) -> None:
    c0, s0, p0 = cpu_now(chrome_pid), S.snap(), len(S.probe_ms)
    t0 = time.monotonic()
    if during is not None:
        during()
    pump(app, max(0.0, seconds - (time.monotonic() - t0)))
    dt = time.monotonic() - t0
    c1, s1 = cpu_now(chrome_pid), S.snap()
    probes = S.probe_ms[p0:]
    out[name] = {
        "seconds": round(dt, 2),
        "cpu_ms_per_s": {k: round((c1[k] - c0[k]) / dt, 1) for k in c0},
        "per_s": {k: round((s1[k] - s0[k]) / dt, 1) for k in s0 if k != "probe_ms_n"},
        "probe_ms_avg": round(sum(probes) / len(probes), 1) if probes else None,
        "probe_ms_max": round(max(probes), 1) if probes else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", choices=["busy", "static"], default="busy")
    ap.add_argument("--steady", type=float, default=10.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--no-card", action="store_true",
                    help="warm UIA on the window (as a launch does) but show no card: the browser's own cost of UIA")
    args = ap.parse_args()

    instrument()
    app = QApplication.instance() or QApplication(sys.argv)
    srv, page_dir = serve(BUSY if args.page == "busy" else STATIC)
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    profile = tempfile.mkdtemp(prefix="sera_perf_chrome_")
    proc = subprocess.Popen([CHROME, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                             "--disable-features=CalculateNativeWinOcclusion",
                             "--disable-backgrounding-occluded-windows", "--window-position=80,60",
                             "--window-size=1200,800", "--new-window", url])
    out = {"label": args.label, "page": args.page}
    card = None
    try:
        hwnd = chrome_window(proc.pid)
        if not hwnd:
            print(json.dumps({"error": "no chrome window"}))
            return 1
        u32.SetForegroundWindow(hwnd)
        pump(app, 5.0)
        phase(app, "base", 6.0, proc.pid, out)

        from ui.os_assist.mecp_card import MecpCard
        service = {"id": 1, "name": "Perf", "login_page_link": url}
        card = MecpCard(service, "PERF0000X", "Fict-ional#1", client_id=1, client_name="Fictional Perf Co",
                        clear_seconds=30, timeout_ms=90000)
        counter = PaintCounter(card)
        for w in (card, card.card, card.timer):
            if w is not None:
                w.installEventFilter(counter)
        from core.os_assist import worker, locate
        worker.submit(locate.host_matches_service, hwnd, service).result(30)   # the launcher warms it the same way
        worker.submit(locate.page_rect, hwnd).result(30)
        for k in [k for k in S.__dict__ if k.startswith("split_")]:
            S.__dict__[k] = []
        if args.no_card:
            phase(app, "steady", args.steady, proc.pid, out)
            print(json.dumps(out, indent=1))
            return 0
        QLabel("warm").sizeHint()                      # the app has loaded its fonts long before a card
        card.attach(hwnd, service)
        S.paint_times.clear()
        t_present = time.monotonic()
        card.present()
        phase(app, "slide", 1.0, proc.pid, out)
        frames = [t for t in S.paint_times if t - t_present <= 0.7]
        gaps = [b - a for a, b in zip(frames, frames[1:])]
        out["slide"]["frames"] = len(frames)
        out["slide"]["max_gap_ms"] = round(max(gaps) * 1000, 1) if gaps else None
        out["slide"]["first_paint_ms"] = round((S.paint_times[0] - t_present) * 1000, 1) if S.paint_times else None
        out["visible"] = card.isVisible()
        out["hidden_reason"] = card.hidden_reason
        out["events_active"] = card.events_active

        phase(app, "steady", args.steady, proc.pid, out)

        moves_before = len(S.card_moves)
        r = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))

        def drag():
            def run():
                for i in range(90):                      # ~1.5 s at 60 Hz, 300 px right and back
                    x = r.left + int(300 * (i / 45 if i < 45 else (90 - i) / 45))
                    S.browser_moves.append((time.monotonic(), x))
                    u32.SetWindowPos(hwnd, 0, x, r.top, 0, 0, 0x0001 | 0x0004 | 0x0010)
                    time.sleep(1 / 60)
            threading.Thread(target=run, daemon=True).start()

        phase(app, "drag", 2.0, proc.pid, out, during=drag)
        moves = S.card_moves[moves_before:]
        lags = []
        for t, _x, _y in moves:
            prior = [bt for bt, _ in S.browser_moves if bt <= t]
            if prior:
                lags.append((t - prior[-1]) * 1000)
        out["drag"]["browser_moves"] = len(S.browser_moves)
        out["drag"]["card_moves"] = len(moves)
        out["drag"]["lag_ms_avg"] = round(sum(lags) / len(lags), 1) if lags else None
        out["drag"]["lag_ms_max"] = round(max(lags), 1) if lags else None
        out["hub_dispatch_total"] = S.__dict__.get("hub_dispatch")
        for k in [k for k in S.__dict__ if k.startswith("split_")]:
            v = S.__dict__[k]
            out[k + "_ms"] = {"n": len(v), "avg": round(sum(v) / len(v), 1) if v else None,
                              "max": round(max(v), 1) if v else None}
    finally:
        if card is not None:
            card.dismiss("close")
            pump(app, 0.5)
        try:
            proc.terminate()
            proc.wait(5)
        except Exception:
            pass
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        srv.shutdown()
        time.sleep(1.0)
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(page_dir, ignore_errors=True)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
