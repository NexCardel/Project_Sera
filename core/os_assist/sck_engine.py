"""
OS Assist SCK engine: the new-password card and its fill, without the extension.

A Qt-thread QObject, one instance, no polling of its own. It acts only on calls from
``automation.py`` (show / outcome / save result / hide) and on the card's signals:

* ``show(attempt, service)``: builds the docked ``SckCard``, attaches it to the attempt's browser
  window and asks the worker to find the password box early (``warm_job``).
* Fill & Test: the typed text is taken from the card, kept as the attempt's candidate
  (``tested=True``) and a job on the OS Assist worker fills the portal's password box with
  ``fill.fill`` (the one writer; it checks the host right before the write). The job is retried
  every ``RETRY_MS`` until a visible password box exists or ``FILL_WAIT_MS`` (10 s) passed: staff are
  looking at the page. After a fill the browser is brought to the front, so staff land on the page
  for the CAPTCHA. SCK never presses Login.
* Save Directly: the candidate is stored with ``tested=False`` and the app's ``commit`` hook runs
  (the vault / new-client write happens in main.py, on the Qt thread).
* Outcomes from the watcher (wrong_password / locked / worked) are shown on the card; ``worked``
  and ``locked`` are acted on by main.py through the same hooks.

The password is read from the card only inside the click handlers, held in the attempt (memory,
expires) and passed to the worker job; it is never logged. A password box is never read.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from core.os_assist import fill, locate, worker
from core.rotation.attempts import SckAttempt, SckAttempts

import logging

log = logging.getLogger("os_assist.sck")

FILL_WAIT_MS = 10_000
RETRY_MS = 400


def fill_job(hwnd: int, service: dict, password: str, selector: str):
    """The password box of that window, filled. None while there is nothing to fill yet (another
    site, no visible box). The card itself is in front when staff press Fill & Test, so unlike
    SCA's job the browser is not required to be the foreground window: ``fill.fill`` brings it to
    the front itself when it has to type, and the host is checked right before the write."""
    if not locate.host_matches_service(hwnd, service):
        _last_miss[int(hwnd or 0)] = "the window is not on the portal's site"
        return None
    user, box = locate.find_fields_cached(hwnd, None, selector or None, want="pass")
    if box is None:
        _last_miss[int(hwnd or 0)] = ("only a non-password box on the page (User ID step?)"
                                      if user is not None else "no input box on the page")
        return None
    _last_miss.pop(int(hwnd or 0), None)
    return fill.fill(hwnd, box, password, True, service, late_check="background")


# hwnd -> why the last fill_job found nothing to fill (for the log when the wait runs out). Never
# holds a value.
_last_miss: Dict[int, str] = {}


def warm_job(hwnd: int, selector: str) -> bool:
    return locate.warm_fields(hwnd, None, selector or None)


def _default_card(attempt: SckAttempt, service: dict, edit: bool):
    from ui.os_assist.sck_card import SckCard
    return SckCard(attempt.attempt_id, service, client_id=attempt.client_id,
                   client_name=attempt.client_name, portal=attempt.portal, kind=attempt.kind,
                   identity=attempt.identity, edit=edit)


def _default_front(hwnd: int) -> None:
    from core.os_assist import launcher
    launcher._bring_to_front(hwnd)


class SckEngine(QObject):
    _deliver = Signal(object)

    def __init__(self, attempts: SckAttempts, commit: Optional[Callable[[SckAttempt], None]] = None,
                 closed: Optional[Callable[[SckAttempt, str], None]] = None,
                 fill_started: Optional[Callable[[int], None]] = None,
                 submit: Callable = None, filler: Callable = None, warmer: Callable = None,
                 card_factory: Callable = None, front: Callable = None,
                 now: Callable[[], float] = None, parent=None):
        super().__init__(parent)
        self.attempts = attempts
        self._commit = commit
        self._closed = closed
        self._fill_started = fill_started
        self._submit = submit or worker.submit_urgent
        self._fill = filler or fill_job
        self._warm = warmer or (warm_job if submit is None else None)
        self._card_factory = card_factory or _default_card
        self._front = front or _default_front
        import time
        self._now = now or (lambda: time.monotonic() * 1000.0)
        self._deliver.connect(lambda fn: fn(), Qt.QueuedConnection)
        self.cards: Dict[str, object] = {}
        self._jobs: Dict[str, dict] = {}          # attempt id -> the running fill {deadline, job}

    # -- show / hide ----------------------------------------------------------------------------
    def show(self, attempt: SckAttempt, service: dict, edit: bool = False):
        """Dock a card for that attempt on its browser window. Returns the card (None if the
        window is unknown or the card could not be built)."""
        if not attempt.hwnd:
            return None
        old = self.cards.get(attempt.attempt_id)
        if old is not None:
            return old
        card = None
        try:
            card = self._card_factory(attempt, service, edit)
            card.attach(attempt.hwnd, _card_service(service))
        except Exception:
            if card is not None:
                card.dismiss("error")
            return None
        self.cards[attempt.attempt_id] = card
        card.fill_requested.connect(self._on_fill_requested)
        card.save_requested.connect(self._on_save_requested)
        card.dismissed.connect(lambda reason, c=card, a=attempt: self._on_dismissed(a, c, reason))
        card.present()
        if self._warm is not None:
            try:
                self._submit(self._warm, attempt.hwnd, (service or {}).get("password_selector") or "")
            except Exception:
                pass
        return card

    def call(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the Qt thread (queued; safe from any thread)."""
        self._deliver.emit(fn)

    def card(self, attempt_id: str):
        return self.cards.get(attempt_id)

    def hide(self, attempt_id: str, reason: str = "closed") -> None:
        card = self.cards.get(attempt_id)
        if card is not None and not card.is_dismissed:
            card.dismiss(reason)

    def _on_dismissed(self, attempt: SckAttempt, card, reason: str) -> None:
        if self.cards.get(attempt.attempt_id) is card:
            del self.cards[attempt.attempt_id]
        job = self._jobs.pop(attempt.attempt_id, None)
        if job and job.get("job") is not None:
            try:
                job["job"].cancel()
            except Exception:
                pass
        self.attempts.close(attempt)
        if self._closed is not None:
            try:
                self._closed(attempt, reason)
            except Exception:
                pass

    # -- Fill & Test ------------------------------------------------------------------------------
    def _attempt_card(self, attempt_id: str):
        card = self.cards.get(attempt_id)
        att = self.attempts.by_id(attempt_id)
        return att, card

    def _on_fill_requested(self, attempt_id: str) -> None:
        att, card = self._attempt_card(attempt_id)
        if att is None or card is None or card.is_dismissed:
            return
        password = card.password()
        if not password:
            card.set_fill_failed("Type the new password first.")
            return
        self.attempts.set_candidate(att, password, tested=True)
        card.hold_secret("fill", password)
        self._jobs[attempt_id] = {"deadline": self._now() + FILL_WAIT_MS, "job": None, "password": password}
        password = ""
        self._tick_fill(attempt_id)

    def _tick_fill(self, attempt_id: str) -> None:
        p = self._jobs.get(attempt_id)
        att, card = self._attempt_card(attempt_id)
        if p is None or att is None or card is None or card.is_dismissed:
            self._jobs.pop(attempt_id, None)
            return
        if p.get("job") is not None:
            return
        if self._now() > p["deadline"]:
            log.info("sck  no-box  after %d s: %s", FILL_WAIT_MS // 1000,
                     _last_miss.pop(int(att.hwnd or 0), "unknown"))
            self._end_fill(attempt_id, card, None, "Sera could not find the password box. "
                                                 "Open the portal's login page and try again.")
            return
        service = card.service if isinstance(getattr(card, "service", None), dict) else _card_service({})
        job = p["job"] = self._submit(self._fill, att.hwnd, service, p["password"],
                                      service.get("password_selector") or "")
        self._later(job, lambda r: self._on_fill(attempt_id, p, r))

    def _on_fill(self, attempt_id: str, p: dict, result) -> None:
        if self._jobs.get(attempt_id) is not p:
            return
        p["job"] = None
        att, card = self._attempt_card(attempt_id)
        if att is None or card is None or card.is_dismissed:
            self._jobs.pop(attempt_id, None)
            return
        if result is None:                          # no box yet: look again shortly
            QTimer.singleShot(RETRY_MS, lambda: self._tick_fill(attempt_id))
            return
        if result.outcome != "filled":
            self._end_fill(attempt_id, card, False, result.reason or "Sera could not fill the password box.")
            return
        late = getattr(result, "late", None)
        if late is None:
            self._end_fill(attempt_id, card, True, "")
            return
        p["job"] = late
        self._later(late, lambda verdict: self._on_late(attempt_id, p, verdict))

    def _on_late(self, attempt_id: str, p: dict, verdict) -> None:
        if self._jobs.get(attempt_id) is not p:
            return
        p["job"] = None
        card = self.cards.get(attempt_id)
        if card is None or card.is_dismissed:
            self._jobs.pop(attempt_id, None)
            return
        if verdict == "":
            self._end_fill(attempt_id, card, True, "")
        else:
            self._end_fill(attempt_id, card, False, verdict or "The page dropped the password.")

    def _end_fill(self, attempt_id: str, card, ok: Optional[bool], reason: str) -> None:
        p = self._jobs.pop(attempt_id, None)
        if p is not None:
            p.pop("password", None)
        att = self.attempts.by_id(attempt_id)
        if ok:
            card.set_filled()
            if att is not None:
                try:
                    if self._fill_started is not None:
                        self._fill_started(att.service_id)
                    self._front(att.hwnd)          # staff land on the page for the CAPTCHA
                except Exception:
                    pass
        else:
            if att is not None:
                self.attempts.take_candidate(att)  # nothing was entered: nothing to save
            card.set_fill_failed(reason)

    def _later(self, fut, fn) -> None:
        def done(f):
            try:
                result = f.result()
            except Exception:
                result = None
            self._deliver.emit(lambda: fn(result))
        fut.add_done_callback(done)

    # -- Save Directly ----------------------------------------------------------------------------
    def _on_save_requested(self, attempt_id: str) -> None:
        att, card = self._attempt_card(attempt_id)
        if att is None or card is None or card.is_dismissed:
            return
        password = card.password()
        if not password:
            card.set_save_failed("Type the new password first.")
            return
        self.attempts.set_candidate(att, password, tested=False)
        password = ""
        if self._commit is not None:
            self._commit(att)

    # -- from the app -----------------------------------------------------------------------------
    def outcome(self, attempt_id: str, kind: str) -> None:
        card = self.cards.get(attempt_id)
        if card is not None and not card.is_dismissed:
            card.set_outcome(kind)

    def save_result(self, attempt_id: str, ok: bool, message: str = "") -> None:
        card = self.cards.get(attempt_id)
        if card is None or card.is_dismissed:
            return
        if ok:
            card.set_saved(message)
        else:
            card.set_save_failed(message)

    def close_all(self, reason: str = "engine") -> None:
        for aid in list(self.cards):
            self.hide(aid, reason)


def _card_service(service: dict) -> dict:
    """What the card / fill need of a service: its login link (host check) and name."""
    service = service or {}
    return {"login_page_link": service.get("login_page_link") or service.get("url") or "",
            "name": service.get("name") or "",
            "password_selector": service.get("password_selector") or ""}


_engine: Optional[SckEngine] = None


def default() -> Optional[SckEngine]:
    return _engine


def install(attempts: SckAttempts, **hooks) -> SckEngine:
    """Create the one engine (Qt thread). Hooks: commit, closed, fill_started."""
    global _engine
    _engine = SckEngine(attempts, **hooks)
    return _engine
