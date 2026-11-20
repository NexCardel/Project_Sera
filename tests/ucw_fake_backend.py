"""A made-up reader backend for a real capture worker process in tests (UCW): `--fake-backend
tests.ucw_fake_backend:make`. One window (hwnd 7) showing one fictional portal page; no COM, no browser."""

PORTAL = "https://services.gst.gov.in/services/auth/fowelcome"
HWND = 7
CT_TEXT = 50020
LINES = ("Fictional GST page", "Fictional line two")


def _nodes():
    return [{"parent": -1, "depth": 0, "ctype": CT_TEXT, "name": line, "rect": (0, 0, 10, 10)} for line in LINES]


class Backend:
    def init_thread(self):
        pass

    def process_id(self, hwnd):
        return 1

    def root(self, hwnd):
        return hwnd if hwnd == HWND else None

    def tab_id(self, hwnd, root):
        return "tab-1"

    def documents(self, root):
        return [("doc", PORTAL, False)]

    def address_bar(self, hwnd, root):
        return ""

    def read_document(self, element):
        return _nodes()

    def document_url(self, element):
        return PORTAL

    def read_raw_page(self, hwnd):
        return {"docs": [_nodes()]}


def make():
    return Backend()
