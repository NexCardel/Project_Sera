"""Shared set-up for the tests/test_ui_lock_*.py behaviour lock (W0-2).

Offscreen Qt, a throwaway encrypted database, and a fixed cast of fictional clients. Nothing
here touches master.db, a backup or ~/AmanAssociates_Sera.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from PySide6.QtWidgets import QApplication

from database import SeraDatabase

KEY = "0123456789abcdef" * 4

app = QApplication.instance() or QApplication([])

# Fixed fictional cast. Passwords are made-up strings used only to check masking.
GST_PW_A = "gstPass#A1x9"
IT_PW_A = "itPass#A1y8"
GST_PW_C = "gstPass#C3z7"


def make_db(tmp_path, name="lock.db"):
    return SeraDatabase(str(tmp_path / name), KEY, raw_db_path=str(tmp_path / "raw.db"))


def cols_by_label(db):
    return {c["label"]: c["id"] for c in db.get_mcl_columns()}


def services_by_name(db):
    return {s["name"]: s["id"] for s in db.get_services()}


def seed_clients(db):
    """Four fictional clients. Returns {'A': id, 'B': id, 'C': id, 'D': id}.

    A: two services (GST, Income Tax), two passwords.  B: no services, no passwords.
    C: Email service, GST password only.  D: archived, no services.
    """
    c = cols_by_label(db)
    s = services_by_name(db)
    ids = {}
    ids["A"] = db.add_client({
        c["NAME OF COMPANY"]: "Alpha Traders Pvt Ltd", c["NAME OF PROPRIETOR"]: "Asha Verma",
        c["GSTIN"]: "29ABCDE1001F1Z5", c["PAN"]: "ABCDE1001F", c["PH. NO."]: "9000000001",
        c["USER ID"]: "alpha.user", c["GST_Password"]: GST_PW_A, c["IT_Password"]: IT_PW_A,
    }, "Alpha notes", [s["GST"], s["Income Tax"]])
    ids["B"] = db.add_client({
        c["NAME OF COMPANY"]: "Bravo Textiles LLP", c["NAME OF PROPRIETOR"]: "Bhanu Rao",
        c["PAN"]: "ABCDE1002F", c["PH. NO."]: "9000000002",
    }, "", [])
    ids["C"] = db.add_client({
        c["NAME OF COMPANY"]: "Charlie Foods", c["NAME OF PROPRIETOR"]: "Chitra Nair",
        c["PAN"]: "ABCDE1003F", c["USER ID"]: "charlie.user", c["GST_Password"]: GST_PW_C,
    }, "", [s["Email"]])
    ids["D"] = db.add_client({
        c["NAME OF COMPANY"]: "Delta Clinic", c["NAME OF PROPRIETOR"]: "Dev Iyer",
        c["PAN"]: "ABCDE1004F",
    }, "", [])
    db.archive_client(ids["D"], actor="Admin")
    return ids


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def dispose(*widgets):
    """Close and delete widgets so the interpreter does not exit with live Qt objects."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication as _A
    for w in widgets:
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass
    _A.sendPostedEvents(None, QEvent.DeferredDelete)
    pump(2)
