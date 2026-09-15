"""
core/vsdc/vsdc_worker.py — Background QThread Worker for Visual SDC (VSDC)
==========================================================================
Coordinates the VSDC monitoring loop in a dedicated background Qt thread.
Emits Qt signals upon detecting route steps and capturing verified filings.
"""

import time
from typing import Optional, Dict, Any
from PySide6.QtCore import QThread, Signal

from .vsdc_router import VSDCRouter
from .vsdc_ocr import VSDCOcrEngine
from .vsdc_assembler import VisualSessionAssembler


class VSDCWorker(QThread):
    """
    Background worker thread running the VSDC route sniffer & visual harvester.
    """

    # Signals
    filing_captured = Signal(dict)   # Emitted when a verified filing payload is sealed
    step_recorded = Signal(str, str)  # Emitted on route transition: (crosshair_id, url)
    activity_event = Signal(str, str, str)  # Emitted on live events: (event_type, title, subtitle)
    status_changed = Signal(str)     # Emitted for status messages (e.g. 'Active', 'Idle')

    def __init__(self, check_interval_sec: float = 0.35, parent=None):
        super().__init__(parent)
        self.check_interval_sec = check_interval_sec
        self._running = False
        self._paused = False

        self.ocr_engine = VSDCOcrEngine()
        self.assembler = VisualSessionAssembler()
        self.router = VSDCRouter(
            ocr_engine=self.ocr_engine,
            assembler=self.assembler,
            on_activity=self._on_router_activity,
        )

    def _on_router_activity(self, event_type: str, title: str, subtitle: str = ""):
        self.activity_event.emit(event_type, title, subtitle)

    def run(self):
        self._running = True
        self.status_changed.emit("Running")
        print("⚡ VSDC (Visual SDC Engine): Worker started (High-Speed Burst Mode).")

        while self._running:
            sleep_time = self.check_interval_sec
            if not self._paused:
                try:
                    payload = self.router.evaluate_tick()
                    if payload:
                        print(f"🎯 VSDC Captured Filing! Form={payload.get('filing_type')} ARN={payload.get('arn')}")
                        self.filing_captured.emit(payload)

                    # Dynamic Burst Snapping: If route changed, poll rapidly (15ms) before user can scroll
                    if getattr(self.router, "burst_ticks_remaining", 0) > 0:
                        self.router.burst_ticks_remaining -= 1
                        sleep_time = 0.015  # Ultra-fast 15ms burst loop
                    else:
                        sleep_time = self.check_interval_sec
                except Exception as e:
                    print(f"⚠️ VSDC Worker Tick Error: {e}")

            time.sleep(sleep_time)

        self.status_changed.emit("Stopped")
        print("⚡ VSDC: Worker stopped.")

    def pause(self):
        self._paused = True
        self.status_changed.emit("Paused")

    def resume(self):
        self._paused = False
        self.status_changed.emit("Running")

    def stop(self):
        self._running = False
        try:
            if hasattr(self, "assembler") and getattr(self.assembler, "_session_started", False):
                self.assembler.logger.end_session(
                    reason="Application Shutdown",
                    summary_items=list(self.assembler.captures.values()),
                )
                self.assembler._session_started = False
        except Exception:
            pass
        self.wait(3000)
