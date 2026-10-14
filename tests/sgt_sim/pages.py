"""
Fictional clients and the pages of one filing, in the shapes the portals' UIA text has (the fixtures of
tests/test_sgt_shadow.py and tests/test_sgt_boundaries.py). A flow writes a whole filing - login, the
client's identity page, the form, the success page, logout - into a tab and returns the submission it
promises: what must be a tracker row, and when it is on screen.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .fake_browser import PORTAL_GST, PORTAL_ITR, Page, Tab, Toast

ITR = "https://eportal.incometax.gov.in/iec/foservices/#/"
GST_SVC = "https://services.gst.gov.in/services/"
GST_RET = "https://return.gst.gov.in/returns/auth/"
ITR_LOGIN, ITR_LOGOUT = ITR + "login", ITR + "logout"
ITR_PROFILE = ITR + "dashboard/myProfile/profileDetail"
ITR_WIZARD = ITR + "foreturns-ay26/fo-itr4-ay2026/personal_information"
ITR_SUCCESS = ITR + "foreturns-ay26/x/done"
ITR_STATUS = ITR + "dashboard/itrStatus"
GST_LOGIN, GST_LOGOUT = GST_SVC + "login", GST_SVC + "logout"
GST_WELCOME = GST_SVC + "auth/fowelcome"
GST_FILE = GST_RET + "file"
DDMMYY = "141026"                 # the run's "today" (harness.TODAY) in a filing number's tail
MONTHS = ["April", "May", "June", "July", "August", "September", "October", "November", "December",
          "January", "February", "March"]
LOGGED_OUT = ["Logged out", "You have been logged out successfully"]

READY = "Submitted & Verified"
NOT_VERIFIED = "Submitted (Not Verified)"


@dataclass(frozen=True)
class Client:
    index: int
    name: str
    pan: str
    gstin: str


@dataclass(frozen=True)
class Submission:
    client: Client
    form: str
    period: str
    arn: str
    status: str
    visible_from: float
    visible_until: float


def _letters(i: int, n: int) -> str:
    return "".join(chr(65 + (i // 26 ** k) % 26) for k in range(n))


def _gstin_check(body: str) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i, c in enumerate(body):
        product = alphabet.index(c) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return alphabet[(36 - total % 36) % 36]


def make_client(i: int) -> Client:
    pan = f"{_letters(i, 2)}CP{_letters(i // 676, 1)}{1000 + i % 9000:04d}{chr(65 + (i * 7) % 26)}"
    body = f"{i % 35 + 1:02d}{pan}1Z"
    return Client(index=i, name=f"SIMCLIENT {_letters(i, 3)}", pan=pan, gstin=body + _gstin_check(body))


def make_clients(n: int, first: int = 1) -> List[Client]:
    return [make_client(i) for i in range(first, first + n)]


def itr_ack(serial: int) -> str:
    return f"{serial:09d}{DDMMYY}"


def gst_arn(serial: int) -> str:
    return f"AA{DDMMYY}{serial:06d}Z"


def _dwell(tab: Tab, t: float, page: Page, sec: float, typing: bool = False) -> float:
    tab.navigate(t, page)
    if typing:
        tab.typing(t + 0.5, t + sec - 0.5)
    return t + sec


def itr_filing(tab: Tab, t: float, client: Client, serial: int, *, ack_visible: float = 10.0,
               read_ms: float = 40.0, success_read_ms: Optional[float] = None, dwell: float = 4.0,
               toast: Optional[Toast] = None, logout: bool = True, login: bool = True,
               work: Optional[Callable[[Tab, float], float]] = None, verify_visible: float = 0.0,
               ) -> Tuple[float, Submission]:
    """verify_visible > 0: the e-verification confirmation (with the same ack) follows for that long."""
    ack = itr_ack(serial)
    if login:
        t = _dwell(tab, t, Page(ITR_LOGIN, ["Login", "Enter your User ID", "Continue"], PORTAL_ITR, read_ms=read_ms),
                   dwell, typing=True)
    profile = [f"{client.name} Individual", "Profile", "Name", client.name, "PAN", client.pan,
               "Date of Birth", "12-Mar-1980"]
    t = _dwell(tab, t, Page(ITR_PROFILE, profile, PORTAL_ITR, read_ms=read_ms), dwell)
    t = _dwell(tab, t, Page(ITR_WIZARD, ["Personal Information", "x", "y"], PORTAL_ITR, read_ms=read_ms), dwell)
    shown = t
    success = ["You have successfully submitted your return!", "Acknowledgement Number :", ack,
               "ITR-4 for A.Y. 2026-27", "You still need to e-Verify within 30 days"]
    tab.navigate(t, Page(ITR_SUCCESS, success, PORTAL_ITR, read_ms=success_read_ms or read_ms))
    if toast is not None:
        tab.toast(t + 0.2, toast)
    t += ack_visible
    if verify_visible > 0:
        done = ["Your return has been e-Verified successfully", "Acknowledgement Number :", ack,
                "ITR-4 for A.Y. 2026-27", "Download ITR-V"]
        t = _dwell(tab, t, Page(ITR_STATUS, done, PORTAL_ITR, read_ms=read_ms), verify_visible)
    sub = Submission(client, "ITR-4", "AY 2026-27", ack, NOT_VERIFIED, shown, t)
    if logout:
        t = _dwell(tab, t, Page(ITR_LOGOUT, LOGGED_OUT, PORTAL_ITR, read_ms=read_ms), 2.0)
    return t, sub


def gst_header(client: Client) -> str:
    return f" {client.name} {client.gstin}"


def gst_filing(tab: Tab, t: float, client: Client, serial: int, *, form: str = "GSTR3B", month: int = 0,
               ack_visible: float = 10.0, read_ms: float = 40.0, success_read_ms: Optional[float] = None,
               dwell: float = 4.0, toast: Optional[Toast] = None, logout: bool = True, login: bool = True,
               work: Optional[Callable[[Tab, float], float]] = None, arn_toast_sec: Optional[float] = None,
               ) -> Tuple[float, Submission]:
    """arn_toast_sec: the ARN is only in a toast of that lifetime; the page behind it just says filed.
    work(tab, t) -> t: whatever the user does between the return page and filing (invoice entry...)."""
    arn = gst_arn(serial)
    period = f"{MONTHS[month % 12]}(Q)" if form == "GSTR1" else MONTHS[month % 12]
    header = gst_header(client)
    if login:
        t = _dwell(tab, t, Page(GST_LOGIN, ["Login", "Username", "Username", "Password", "Password", "LOGIN"],
                                PORTAL_GST, read_ms=read_ms), dwell, typing=True)
    t = _dwell(tab, t, Page(GST_WELCOME, [f"Welcome {client.name} to GST Common Portal", header, "View Profile", "x"],
                            PORTAL_GST, read_ms=read_ms), dwell)
    t = _dwell(tab, t, Page(GST_RET + form.lower(), ["Returns", header, f"{form} - Return", "Status - Not Filed",
                                                     "FY - 2026-27", f"Return Period - {period}", "x"],
                            PORTAL_GST, read_ms=read_ms), dwell)
    if work is not None:
        t = work(tab, t)
    shown = t
    where = "" if arn_toast_sec is not None else arn
    success = ["Dashboard", "Returns", "File", "English",
               form, "of GSTIN -", client.gstin, "for the Return Period - '", period.replace("(Q)", ""), "-", "2026-27",
               "has been successfully filed. The Acknowledgment Reference Number is", where, ". The",
               form, "can be viewed on your Dashboard Login=>Taxpayer Dashboard=>Returns. This message is sent "
               "to your registered Email ID and Mobile Number.",
               "Indicates Mandatory Fields",
               header, "GSTIN -", client.gstin, "Legal Name -", client.name,
               "Return Type -", form, "FY -", "2026-27", "Return Period -", period, "Status -", "Filed",
               "Returns Filing for GST", form]
    tab.navigate(t, Page(GST_FILE, [ln for ln in success if ln], PORTAL_GST, read_ms=success_read_ms or read_ms))
    if toast is not None:
        tab.toast(t + 0.2, toast)
    if arn_toast_sec is not None:
        tab.toast(t + 0.2, arn_toast(arn, arn_toast_sec))
        sub_until = t + 0.2 + arn_toast_sec
        t += max(ack_visible, arn_toast_sec + 0.2)
    else:
        t += ack_visible
        sub_until = t
    sub = Submission(client, form, period, arn, READY, shown, sub_until)
    if logout:
        t = _dwell(tab, t, Page(GST_LOGOUT, LOGGED_OUT, PORTAL_GST, read_ms=read_ms), 2.0)
    return t, sub


def arn_toast(arn: str, lifetime_sec: float = 1.0, cells: int = 12) -> Toast:
    return Toast(["Your return has been filed successfully. ARN: " + arn], lifetime_sec, cells)
