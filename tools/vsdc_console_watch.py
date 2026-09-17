"""
tools/vsdc_console_watch.py — Standalone VSDC console watcher (no DB, no UI)
==============================================================================
Runs ONLY the VSDC capture pipeline (VSDCWorker) in isolation for a bounded
window — no main window, no encrypted client database, nothing saved anywhere.
VSDCWorker takes no `db` argument; it only emits Qt signals that main.py
normally listens to for saving. Nothing is connected to storage here, so
nothing is written anywhere — just console output.

Every print() already inside core/vsdc/vsdc_router.py (route matches, VSDC-X
UIA activity, captures) goes straight to this console, plus the three Qt
signals wired below for a readable summary.

Usage:
    1. Log into the GST portal in your browser as usual.
    2. Run:  python tools/vsdc_console_watch.py [seconds]
       (default 90 seconds)
    3. Switch to the browser and navigate around the portal normally —
       dashboard, a GSTR-1/GSTR-3B form-details page, ideally one that pops
       the modal — while it's running.
    4. It stops itself after the given duration, or Ctrl+C to stop early.
"""

import os
import sys
import time

# Running this script directly puts tools/ on sys.path, not the project root,
# so `core` isn't importable unless we add the root ourselves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication

from core.vsdc import VSDCWorker


def main():
    duration_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0

    app = QCoreApplication(sys.argv)

    worker = VSDCWorker(check_interval_sec=0.35)
    worker.filing_captured.connect(lambda payload: print(f"\n=== FILING CAPTURED (not saved anywhere) ===\n{payload}\n", flush=True))
    worker.activity_event.connect(lambda et, title, sub: print(f"[activity] {et}: {title} | {sub}", flush=True))
    worker.status_changed.connect(lambda s: print(f"[status] {s}", flush=True))

    print("=" * 70, flush=True)
    print("VSDC console watcher — no database, no UI, nothing saved anywhere.", flush=True)
    print(f"Running for {duration_sec:.0f}s. Log into the GST portal and navigate around now.", flush=True)
    print("=" * 70, flush=True)

    worker.start()

    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < duration_sec:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("Session window ended. Stopping worker...", flush=True)
    worker.stop()
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
