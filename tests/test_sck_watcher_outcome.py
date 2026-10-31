"""
SCK: the failed-login watcher's outcome stream for the new-password card.

The card is opened by ``on_failure`` and then needs to hear three things while it stays open:

* ``wrong_password`` when the portal rejects a *retry* (the failure is already pending, so
  ``on_failure`` does not fire again),
* ``locked`` for the same reason,
* ``worked`` when the service's dashboard page is reached - with or without a pending failure,
  because the card can stay open after its pending failure was cleared or expired.

These tests use a fake rule set so the wording of the portal's pages never matters here.
"""

from types import SimpleNamespace

from core.rotation.watcher import SckWatcher


class _Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _Outcome:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.rule = "fake-rule"
        self.line = "fake line"
        self.unconfirmed = False


class _Rules:
    """classify() returns whatever the test set, like the real portal wording would."""

    def __init__(self) -> None:
        self.next = None

    def classify(self, lines, url="", portal=""):
        return self.next


def _service() -> dict:
    return {
        "id": 7,
        "name": "GST",
        "login_page_link": "https://www.gst.gov.in/login",
        "dashboard_page_link": "https://www.gst.gov.in/dashboard",
    }


def _obs(url: str, profile=None) -> SimpleNamespace:
    return SimpleNamespace(url=url, lines=["some page text"], portal="GST",
                           result={}, profile=dict(profile or {}))


class _Harness:
    def __init__(self) -> None:
        self.rules = _Rules()
        self.clock = _Clock()
        self.failures = []
        self.cleared = []
        self.outcomes = []
        self.watcher = SckWatcher(
            services=lambda: [_service()],
            resolve_client=lambda svc, obs: (42, "Acme Ltd"),
            on_failure=self.failures.append,
            on_clear=self.cleared.append,
            on_outcome=lambda sid, kind: self.outcomes.append((sid, kind)),
            rules=lambda: self.rules,
            monotonic=self.clock,
        )


LOGIN = "https://www.gst.gov.in/login"
DASHBOARD = "https://www.gst.gov.in/dashboard/home"


def test_first_failure_carries_pan_and_fires_once():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")

    result = h.watcher.observe(_obs(LOGIN, {"pan": "abcde1234f"}), hwnd=1234)

    assert result == "wrong_password"
    assert len(h.failures) == 1
    failure = h.failures[0]
    assert failure.service_id == 7
    assert failure.client_id == 42
    assert failure.client_name == "Acme Ltd"
    assert failure.pan == "ABCDE1234F"
    assert failure.hwnd == 1234
    assert h.outcomes == []


def _reload(h):
    """One read while the page is reloading / shows no verdict, like a submit in flight."""
    h.rules.next = None
    h.watcher.observe(_obs(LOGIN), hwnd=1)


def test_pending_retry_rejection_is_reported_to_the_card():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    h.clock.now += 10.0
    _reload(h)                            # staff submitted; the page came back with the error
    h.rules.next = _Outcome("wrong_password")
    result = h.watcher.observe(_obs(LOGIN), hwnd=1)

    assert result == "pending"
    assert len(h.failures) == 1          # the card does not fire again
    assert h.outcomes == [(7, "wrong_password")]


def test_pending_locked_is_reported_to_the_card():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    h.rules.next = _Outcome("locked")
    h.clock.now += 10.0
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    assert h.outcomes == [(7, "locked")]


def test_the_error_line_still_on_screen_is_not_a_new_rejection():
    """Staff pressed Fill & Test while the old error was visible: do not call it rejected."""
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    for _ in range(4):
        h.clock.now += 10.0
        h.watcher.observe(_obs(LOGIN), hwnd=1)

    assert h.outcomes == []


def test_a_new_attempt_resets_the_time_throttle():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    h.clock.now += 10.0
    _reload(h)
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)          # first retry rejection reported

    h.watcher.reset_outcome_throttle(7)             # staff pressed Fill & Test again
    _reload(h)
    h.rules.next = _Outcome("wrong_password")
    h.clock.now += 1.0                              # inside the old 5 s window
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    assert h.outcomes == [(7, "wrong_password"), (7, "wrong_password")]


def test_dashboard_clears_and_reports_worked():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    result = h.watcher.observe(_obs(DASHBOARD), hwnd=1)

    assert result == "cleared"
    assert [f.service_id for f in h.cleared] == [7]
    assert (7, "worked") in h.outcomes
    assert h.watcher.pending() == []


def test_dashboard_reports_worked_without_a_pending_failure():
    """The card can still be open after its pending failure was cleared: no failure fires now."""
    h = _Harness()

    h.watcher.observe(_obs(DASHBOARD), hwnd=1)

    assert h.cleared == []
    assert h.outcomes == [(7, "worked")]


def test_worked_on_the_login_route_clears_the_pending_failure():
    """Some portals keep the login URL and swap the form for a welcome page."""
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    h.rules.next = _Outcome("worked")
    h.clock.now += 10.0
    result = h.watcher.observe(_obs(LOGIN), hwnd=1)

    assert result == "cleared"
    assert [f.service_id for f in h.cleared] == [7]
    assert (7, "worked") in h.outcomes
    assert h.watcher.pending() == []


def test_mark_resolved_lets_the_next_failure_fire():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    h.watcher.mark_resolved(7, 42)
    h.clock.now += 10.0
    result = h.watcher.observe(_obs(LOGIN), hwnd=1)

    assert result == "wrong_password"
    assert len(h.failures) == 2


def test_outcome_errors_never_break_detection():
    h = _Harness()
    h.rules.next = _Outcome("wrong_password")
    h.watcher.observe(_obs(LOGIN), hwnd=1)

    def boom(sid, kind):
        raise RuntimeError("card gone")

    h.watcher._on_outcome = boom
    h.clock.now += 10.0

    assert h.watcher.observe(_obs(LOGIN), hwnd=1) == "pending"
