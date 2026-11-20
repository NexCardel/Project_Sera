"""
tools/ucw_probe.py - UCW WP0: measure the capture worker's costs on an office PC
================================================================================
docs/ucw/ucw-plan.md, WP0. A one-off tool the owner runs on an office PC with a few portal pages open
in the browser (nothing is clicked, typed or focused: property reads only). It measures:

  1. start: a new capture worker process, from launch to "UIA ready" (the same exe Sera starts:
     the packaged exe with --capture-worker, or `python main.py --capture-worker` in the venv);
  2. per in-scope page (each visible browser window's on-screen Documents whose URL is on a portal):
     the control-view read and the raw-view read, in ms (median of --repeats);
  3. per page: whether the raw view filtered to control elements (IsControlElement) gives exactly the
     same lines as the control-view read (decides WP5's path), and its node counts;
  4. per page: the pipe cost - one request/result frame round trip through a real worker process with
     that page's nodes (pickle + pipe, both ways);
  5. hang.log: the capture-thread stalls, grouped by where the capture thread was (code locations only).

Output: counts and times only - never page text, URLs or values. Portal pages are named by their
portal and their order ("GST Portal #1"). Prints a table, and writes the same as JSON with --out.

Usage (venv):      python tools/ucw_probe.py [--repeats 5] [--out probe.json] [--exe path\\to\\Sera.exe]
Hang summary only: python tools/ucw_probe.py --hang-only
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---- 5. hang.log ------------------------------------------------------------------------------

_HEADER = re.compile(r"^==== (.*?) ====$", re.M)
_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
CAPTURE_MARKERS = ("vsdc_worker.py",)       # the capture thread's run() lives there


def _short(path: str) -> str:
    p = path.replace("/", "\\")
    for anchor in ("\\core\\", "\\ui\\", "\\tools\\"):
        if anchor in p:
            return p[p.index(anchor) + 1:]
    return p.rsplit("\\", 1)[-1]


def hang_summary(paths: List[Path], top: int = 12) -> Dict[str, Any]:
    """The capture-thread stalls in hang.log files: how many dumps, the longest stall, and where the
    capture thread was (its last Sera frames and the innermost frame), most common first."""
    text = ""
    for p in paths:
        try:
            text += p.read_text(encoding="utf-8", errors="replace") + "\n"
        except OSError:
            pass
    parts = _HEADER.split(text)
    dumps, longest, where = 0, 0.0, Counter()
    for i in range(1, len(parts), 2):
        title, body = parts[i], parts[i + 1]
        if "capture worker stalled" not in title:
            continue
        dumps += 1
        m = re.search(r"stalled (\d+(?:\.\d+)?) s", title)
        if m:
            longest = max(longest, float(m.group(1)))
        for block in re.split(r"^--- ", body, flags=re.M):
            if not any(k in block for k in CAPTURE_MARKERS):
                continue
            frames = _FRAME.findall(block)
            sera = [f"{_short(f)}:{fn}" for f, _l, fn in frames if "site-packages" not in f and "Python3" not in f]
            inner = f"{_short(frames[-1][0])}:{frames[-1][2]}" if frames else "?"
            where[" > ".join(sera[-3:]) + "  ||  " + inner] += 1
    return {"capture_dumps": dumps, "longest_s": longest, "where": where.most_common(top)}


# ---- 1. worker start --------------------------------------------------------------------------

def worker_command(exe: Optional[str]) -> List[str]:
    if exe:
        return [exe, "--capture-worker"]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--capture-worker"]
    return [sys.executable, str(ROOT / "main.py"), "--capture-worker"]


def measure_start(exe: Optional[str], tries: int = 3) -> List[float]:
    """ms from launch to the worker's 'ready' frame (UIA object made in the child)."""
    from core.capture.remote import WorkerProcess
    out = []
    for _ in range(tries):
        t0 = time.perf_counter()
        proc = WorkerProcess(worker_command(exe))
        ok = proc.wait_ready(timeout=30.0)
        took = (time.perf_counter() - t0) * 1000.0
        proc.kill()
        out.append(round(took) if ok else -1)
    return out


# ---- 2-4. pages -------------------------------------------------------------------------------

def _uia():
    import comtypes
    import comtypes.client
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass                # comtypes' import made this thread single-threaded already: reads work there too
    client = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(client.CUIAutomation, interface=client.IUIAutomation)
    return uia, client


def _ms(fn, repeats: int) -> Tuple[float, Any]:
    times, last = [], None
    for _ in range(repeats):
        t0 = time.perf_counter()
        last = fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(times), last


def probe_pages(repeats: int, worker: Any) -> List[Dict[str, Any]]:
    from core.capture import scope, uia_text, windows
    from core.capture.rawfilter import control_view_of
    from core.sgt_i import uia_nodes

    uia, client = _uia()
    rows: List[Dict[str, Any]] = []
    by_portal: Counter = Counter()
    fg = 0
    try:
        import ctypes
        fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        pass
    for w in windows.browser_windows(fg):
        root = uia.ElementFromHandle(w.hwnd)
        if not root:
            continue
        for element, url, offscreen in uia_text.documents_with_urls(uia, client, root):
            if offscreen or not url or not scope.is_in_scope_url(url):
                continue
            portal = scope.portal_for_url(url) or "portal"
            by_portal[portal] += 1
            name = f"{portal} #{by_portal[portal]}"
            ctl_ms, ctl = _ms(lambda: uia_nodes.read_cached_subtree(uia, client, element), repeats)
            raw_ms, raw = _ms(lambda: uia_nodes.read_cached_subtree(uia, client, element, raw_view=True,
                                                                    control_flag=True), repeats)
            ctl_lines = uia_nodes.lines_from_nodes([ctl], include_selection=True)
            filtered = control_view_of(raw)
            same = uia_nodes.lines_from_nodes([filtered], include_selection=True) == ctl_lines
            row = {"page": name, "control_ms": round(ctl_ms), "raw_ms": round(raw_ms), "control_nodes": len(ctl),
                   "raw_nodes": len(raw), "filtered_nodes": len(filtered), "lines": len(ctl_lines),
                   "filtered_lines_same": same, "gecko": bool(ctl and ctl[0].get(uia_nodes.GECKO_KEY))}
            if worker is not None:
                row["pipe_ms"] = round(worker.echo_ms([raw]), 2)
            rows.append(row)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--exe", default=None, help="the packaged Sera exe (default: this Python with main.py)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--hang-only", action="store_true")
    args = ap.parse_args(argv)

    logs = Path.home() / "AmanAssociates_Sera" / "logs"
    report: Dict[str, Any] = {"hang": hang_summary([logs / "hang.log.1", logs / "hang.log"])}
    if not args.hang_only:
        report["start_ms"] = measure_start(args.exe)
        from core.capture.remote import WorkerProcess
        worker = WorkerProcess(worker_command(args.exe))
        try:
            worker.wait_ready(timeout=30.0)
            report["pages"] = probe_pages(max(1, args.repeats), worker)
        finally:
            worker.kill()

    h = report["hang"]
    print(f"hang.log: {h['capture_dumps']} capture-stall dumps, longest {h['longest_s']:.0f} s")
    for where, n in h["where"]:
        print(f"  {n:4d}  {where}")
    if "start_ms" in report:
        print(f"worker start to UIA ready (ms): {report['start_ms']}")
        print(f"{'page':<18}{'ctl ms':>8}{'raw ms':>8}{'ctl n':>7}{'raw n':>7}{'filt n':>7}{'lines':>7}"
              f"{'same':>6}{'pipe ms':>9}")
        for r in report.get("pages", []):
            print(f"{r['page']:<18}{r['control_ms']:>8}{r['raw_ms']:>8}{r['control_nodes']:>7}{r['raw_nodes']:>7}"
                  f"{r['filtered_nodes']:>7}{r['lines']:>7}{'yes' if r['filtered_lines_same'] else 'NO':>6}"
                  f"{r.get('pipe_ms', 0):>9}")
        if not report.get("pages"):
            print("no portal page is open in a visible browser window")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
