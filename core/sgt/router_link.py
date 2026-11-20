"""
core/sgt/router_link.py - how the VSDC router builds and drives SGT
==================================================================
SgtLink is a part of core/vsdc/vsdc_router.VSDCRouter (a mixin): the router's SGT state, the SGT half
of Settings -> Tracker, building the engine (core/sgt/engine) and its capture loop
(core/sgt/capture/loop) on first use, SGT's tick, attaching SGT-I and SCC-U, and the HUD and
status-demotion callbacks SGT raises. The router keeps everything that is not SGT's: the crosshair
engines, VSDC247 and VSDC247-V (which also enriches each SGT row before it is saved).

It reads the router's own attributes (ocr, health, alerts, _dispatched_ids, _verifier, the HUD
buffer) and never imports core.vsdc while it loads, so either package can be imported first.

SGT has two modes: off and live. A stored "shadow" (Settings or SGT_MODE, from before shadow mode
was removed) is read as live.
"""

import os
import time
from typing import Any, Dict, Optional

SGT_OFF = "off"
SGT_LIVE = "live"


def sgt_mode_of(value: Any, unset: Optional[str] = SGT_OFF) -> Optional[str]:
    """A stored or typed SGT mode as "off" / "live". "shadow" (retired) is live; anything else is
    `unset`."""
    v = str(value or "").strip().lower()
    if v in (SGT_LIVE, "shadow"):
        return SGT_LIVE
    if v == SGT_OFF:
        return SGT_OFF
    return unset


class SgtLink:
    """The router's SGT half (part of VSDCRouter)."""

    def _init_sgt(self) -> None:
        """The router's SGT state, set once from VSDCRouter.__init__."""
        # SGT - Sera Global Tracker (core/sgt): the capture engine, its rows merge with the tracker's.
        # Off until apply_engine_settings() (Settings -> Tracker) or SGT_MODE says otherwise.
        self._sgt_mode: str = self._env_sgt_mode() or SGT_OFF
        self._sgt_alone: bool = False              # SGT on, the other three off: no crosshair routing
        self._sgt = None                           # core.sgt.engine.SgtEngine, built on first use
        # SGT's capture loop (core/sgt/capture/loop): its own windows, scope gate and reader lanes,
        # built on first use while SGT is on. The crosshair engines keep this router's own URL read
        # (R7). The three below are for tests and tools: None = the real Windows platform, the real
        # UI Automation backend and time.monotonic.
        self.capture = None
        self.capture_platform = None
        self.capture_backend = None
        self.capture_clock = None
        if self._sgt_mode != SGT_OFF:
            print(f"[SGT] mode = {self._sgt_mode.upper()} (from SGT_MODE)")

    @staticmethod
    def _env_sgt_mode() -> Optional[str]:
        """SGT_MODE, the developer override: "off" or "live" ("shadow", from before shadow mode was
        removed, means live). None when it is not set."""
        return sgt_mode_of(os.environ.get("SGT_MODE", ""), unset=None)

    def _apply_sgt_switches(self, sgt: str, sgt_record: bool, sgt_i: bool, scc_detect: bool,
                            sdis_record: bool) -> bool:
        """The SGT half of VSDCRouter.apply_engine_settings: SGT off / live, page recording for replay
        and for SDIS, SGT-I and SCC-U. True when SGT is on."""
        sgt_mode = self._env_sgt_mode() or sgt_mode_of(sgt)
        self._sgt_record = bool(sgt_record)
        if self._sgt is not None and getattr(self._sgt, "_recorder", None) is not None:
            self._sgt._recorder.enabled = self._sgt_record
        self._sdis_record = bool(sdis_record)
        if self._sgt is not None and getattr(self._sgt, "_sdis", None) is not None:
            self._sgt._sdis.enabled = self._sdis_record
        self._sgt_i_on = bool(sgt_i)
        self._apply_sgt_i()
        self._scc_on = bool(scc_detect)
        self._apply_scc()
        if sgt_mode == SGT_OFF and self._sgt_mode != SGT_OFF and self._sgt is not None:
            # Open sessions end now; a read asked before the switch is never absorbed after it.
            reason = "SGT switched off"
            if self.capture is not None:
                self.capture.end_all(reason)
            else:
                self._sgt.end_all(reason)
            self._drop_capture()
        self._sgt_mode = sgt_mode
        return sgt_mode != SGT_OFF

    def _apply_sgt_i(self) -> None:
        """Attaches SGT-I to SGT when switched on. One host per run: one that switched itself
        off after a failure stays off, even if the switch is flipped again."""
        if self._sgt is None:
            return
        on = getattr(self, "_sgt_i_on", False)
        host = getattr(self, "_sgt_i_host", None)
        if on and host is None:
            from core.sgt_i import SgtIntelligence, default_components
            from core.sgt_i.uia_events import FlashWatcher
            host = self._sgt_i_host = SgtIntelligence(default_components(), flashes=FlashWatcher())
        if host is not None:
            host.set_enabled(on)
            self._sgt.set_intelligence(host if on else None)

    def set_client_lookup(self, lookup) -> None:
        """lookup(portal, portal user ID) -> {"gstin", "pan"} of the saved client who owns it, or None.
        SGT uses it where a page shows the user ID but not the client's GSTIN."""
        self._client_lookup = lookup
        if self._sgt is not None:
            self._sgt.set_client_lookup(lookup)

    def set_digest(self, digest) -> None:
        """digest(today) -> the daily capture digest, run by SGT's first tick of each day (WP12)."""
        self._digest = digest
        if self._sgt is not None:
            self._sgt.set_digest(digest)

    def sessions_open(self) -> bool:
        """SGT has a session with content touched in the last few minutes (holds back a mandatory
        update restart). False when SGT never started."""
        return self._sgt is not None and self._sgt.sessions_open()

    def set_scc_handlers(self, factory) -> None:
        """factory() -> the handlers to register on SCC-U's host. Called each time the switch is
        applied, so a handler that shares the host (SCK) can join while SCC-U's own rules are off."""
        self._scc_handlers = factory

    def _apply_scc(self) -> None:
        """Attaches SCC-U to SGT when switched on, the same way as SGT-I: one host per run, and
        one that switched itself off after a failure stays off. The host may be shared with a
        handler that has its own switch (SCK), so the handler list is refreshed on every apply."""
        if self._sgt is None:
            return
        on = getattr(self, "_scc_on", False)
        host = getattr(self, "_scc_host", None)
        if on and host is None:
            from core.scc import SccHost
            host = self._scc_host = SccHost()
        if on and host is not None:
            factory = getattr(self, "_scc_handlers", None)
            if factory is not None:
                try:
                    host.set_handlers(factory())
                except Exception as e:
                    print(f"[vsdc_router] SCC handlers not refreshed: {type(e).__name__}")
        if host is not None:
            host.set_enabled(on)
            self._sgt.set_scc(host if on else None)

    def _ensure_sgt(self) -> None:
        """Builds SGT on first use. Its tracker rows leave through its own outbox (evaluate_tick
        hands them out one per tick), never through a tick's result."""
        if self._sgt is None:
            from core.sdis.recorder import SdisRecorder
            from core.sgt.corpus import PageRecorder
            from core.sgt.spec_health import SpecStats
            from core.sgt.engine import SgtEngine, sgt_data_dir
            folder = sgt_data_dir()
            # recorder: pages for replay; sdis: raw pages for Sera Distill; stats: portal-change
            # watch; state: crash recovery; alert: a live submission whose client never became
            # known (phone, like VSDC247)
            self._sgt = SgtEngine(dispatched_ids=lambda: list(self._dispatched_ids), notify=self.notify_sgt,
                                  recorder=PageRecorder(enabled=getattr(self, "_sgt_record", True)),
                                  sdis=SdisRecorder(enabled=getattr(self, "_sdis_record", True)),
                                  stats=SpecStats(folder), state_path=folder / "sessions_state.json",
                                  alert_unattributed=lambda form, page: self.alerts.notify_unattributed_submission(form, page),
                                  ask_demotion=self.notify_demotion,
                                  resolve_client=getattr(self, "_client_lookup", None),
                                  on_read=self.health.read_result, on_hits=self.health.hits,
                                  on_ocr_row=lambda: self.health.row("ocr_only"))
            self._sgt.set_digest(getattr(self, "_digest", None))
            self._apply_sgt_i()
            self._apply_scc()
            if self._verifier is not None:
                self._sgt.set_verifier(self._verifier)
                reconciler = getattr(self._verifier, "reconciler", None)
                if reconciler is not None and hasattr(reconciler, "set_sgt_router"):
                    reconciler.set_sgt_router(self._sgt)
                if reconciler is not None and hasattr(reconciler, "set_db"):
                    reconciler.set_db(getattr(self, "db", None))

    def _run_capture(self) -> None:
        """SGT's tick: the capture loop watches the browser windows, asks the reader for page reads
        and hands SGT what comes back (core/sgt/capture/loop). Its errors are logged like a tick's
        and never stop the crosshair engines' tick."""
        from core.vsdc import vsdc_diag
        try:
            if self.capture is None:
                self._ensure_sgt()
                from core.sgt.capture.loop import CaptureLoop, WinPlatform
                from core.sgt.capture.reader import PageReader
                clock = self.capture_clock or time.monotonic
                reader = PageReader(backend=self.capture_backend, clock=clock)
                self.capture = CaptureLoop(self.capture_platform or WinPlatform(self.ocr), reader, self._sgt,
                                           self.ocr, self.health, clock=clock,
                                           on_out_of_scope=self._check_unlisted_portal)
                vsdc_diag.register_reader(reader)
            self.capture.tick()
            self.diag_tick.update(self.capture.diag())
        except Exception as e:
            vsdc_diag.tick_error(e)

    def _drop_capture(self) -> None:
        """SGT is off: the loop's reader lanes close and the heartbeat reads the old reader again."""
        from core.vsdc import vsdc_diag
        loop, self.capture = self.capture, None
        if loop is not None:
            vsdc_diag.register_reader(None)
            loop.close()

    on_demotion = None          # set by the app: shows the "lower this status?" dialog (called from SGT's thread)

    def notify_demotion(self, info: Dict[str, Any]) -> None:
        """SGT saw a lower status for a filing that has an ARN and asks the user before lowering it."""
        cb = self.on_demotion
        if callable(cb):
            cb(info)

    def answer_demotion(self, token: str, lower: bool) -> None:
        if self._sgt is not None:
            self._sgt.answer_demotion(token, lower)

    def notify_sgt(self, event_type: str, title: str, subtitle: str, context: Dict[str, Optional[str]]) -> None:
        """
        A HUD event from SGT. It carries SGT's OWN return context (form / type / period as SGT
        resolved them), which must not be replaced by VSDC's assembler context at flush time -
        the two engines can disagree, and the pill must show what SGT saw.
        """
        own = {k: context.get(k) for k in ("portal", "form", "filing_pref", "period")}
        own["_own"] = True
        if self._hud_buffering:
            self._hud_buffer.append((event_type, title, subtitle, own))
        else:
            self._emit_activity(event_type, title, subtitle, {k: v for k, v in own.items() if k != "_own"})
