"""
SCK: a successful login is seen when the service has NO dashboard link (every service on a
default install), and SGT's frozen observations are read correctly.

Real case (2026-11-15): Income Tax, login link ``#/login``, dashboard link empty. After Fill &
Test the portal moved to ``#/dashboard/fileIncomeTaxReturn``; the watcher dropped every page off
the login route before classifying it, so "worked" never reached the desktop and nothing was
saved. These tests use the real rule set and ``make_observation`` (MappingProxyType values).
"""

from core.rotation.watcher import SckWatcher, profile_dict, profile_value
from core.sgt_i.observation import make_observation

LOGIN_LINK = "https://eportal.incometax.gov.in/iec/foservices/#/login"
PASSWORD_PAGE = "https://eportal.incometax.gov.in/iec/foservices/#/login/password"
DASHBOARD = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fileIncomeTaxReturn"
PAN = "ABCDE1234F"


def _service():
    return {"id": 2, "name": "Income Tax", "login_page_link": LOGIN_LINK, "dashboard_page_link": ""}


def _obs(url, lines, page_profile=None, session_profile=None):
    result = {"profile": {k: {"value": v, "confidence": 0.9} for k, v in (page_profile or {}).items()}}
    return make_observation(session_id="s1", portal="Income Tax", url=url, title="", source="uia",
                            lines=lines, result=result, profile=dict(session_profile or {}),
                            draft={}, ts=1.0, today="2026-11-15")


class _Harness:
    def __init__(self, client=(None, "")):
        self.failures, self.offers, self.outcomes = [], [], []
        self.watcher = SckWatcher(
            services=lambda: [_service()],
            resolve_client=lambda svc, obs: client,
            on_failure=self.failures.append,
            on_unregistered=self.offers.append,
            on_outcome=lambda sid, kind, hwnd, ident, prof: self.outcomes.append((sid, kind, hwnd, ident, prof)),
        )


WRONG = ["Login", "PAN", ":", PAN, "Enter password for your e-Filing account", "Password*",
         "Invalid Password, Please retry.", "Forgot Password?"]
LOGGED_IN = ["E Filing logo", "Profile Image", "individual", "ASHOK KUMAR SEN Individual",
             "Dashboard", "e-File", "Services"]


def test_worked_seen_on_dashboard_without_dashboard_link():
    h = _Harness()
    assert h.watcher.observe(_obs(PASSWORD_PAGE, WRONG, session_profile={"pan": PAN}), 11) == "no_client"
    assert len(h.failures) == 1
    out = h.watcher.observe(_obs(DASHBOARD, LOGGED_IN, session_profile={"pan": PAN, "name": "ASHOK KUMAR SEN"}), 11)
    assert out == "cleared"
    assert [(o[0], o[1], o[2]) for o in h.outcomes] == [(2, "worked", 11)]
    assert h.outcomes[0][3] == PAN
    assert h.outcomes[0][4]["name"] == "ASHOK KUMAR SEN"
    assert h.watcher.pending() == []


def test_failure_wording_off_login_page_is_ignored():
    h = _Harness(client=(42, "Acme"))
    assert h.watcher.observe(_obs(DASHBOARD, WRONG), 11) is None
    assert h.failures == [] and h.outcomes == []


def test_other_site_is_ignored():
    h = _Harness()
    assert h.watcher.observe(_obs("https://www.google.com/search", LOGGED_IN), 11) is None
    assert h.outcomes == []


def test_frozen_page_profile_is_read_not_stringified():
    obs = _obs(PASSWORD_PAGE, [], page_profile={"pan": PAN, "name": "ASHOK KUMAR SEN"})
    assert profile_value(obs, "pan") == PAN
    got = profile_dict(obs)
    assert got["pan"] == PAN
    assert got["name"] == "ASHOK KUMAR SEN"
    assert not any("mappingproxy" in v for v in got.values())


def test_unknown_pan_offer_from_frozen_page_profile():
    h = _Harness()
    lines = ["Login", "PAN", ":", PAN, "Enter password for your e-Filing account", "Password*"]
    assert h.watcher.observe(_obs(PASSWORD_PAGE, lines, page_profile={"pan": PAN}), 11) == "unregistered"
    assert h.offers[0].identity == PAN


# Real case (2026-11-15): the Income Tax password page always shows its "Secure Access Message",
# which the rules read as a "neutral" step. The new-client offer only ran on "no verdict", so an
# unknown PAN never got the card there - only SCA's "no client has this UID" notice.
SECURE_ACCESS_PAGE = ["Login", "PAN : " + PAN, "Secure Access Message", "Income",
                      "Please confirm your secure access message displayed above", "*",
                      "Enter password for your e-Filing account", "Password", "*",
                      "Forgot Password?", "Continue"]


def test_unknown_pan_offered_on_the_secure_access_password_page():
    h = _Harness()
    obs = _obs(PASSWORD_PAGE, SECURE_ACCESS_PAGE, page_profile={"pan": PAN})
    assert h.watcher.observe(obs, 11) == "unregistered"
    assert len(h.offers) == 1 and h.offers[0].identity == PAN and h.offers[0].client_id is None
    assert h.watcher.observe(obs, 11) == "pending"                  # once per window and ID
    assert len(h.offers) == 1


def test_known_pan_on_the_secure_access_password_page_is_not_offered():
    h = _Harness(client=(42, "Acme"))
    assert h.watcher.observe(_obs(PASSWORD_PAGE, SECURE_ACCESS_PAGE, page_profile={"pan": PAN}), 11) is None
    assert h.offers == [] and h.failures == []
