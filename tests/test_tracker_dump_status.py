"""Tracker dump status pills (2026-10-06): a submit message with no ARN is "Submitted" (light green); with an ARN
it is "Submitted & E-verified" (dark green). Nothing stored changes, only what the table shows."""
import pytest
from PySide6.QtWidgets import QApplication

import ui.windows.tracker_dump_window as tdw

APP = QApplication.instance() or QApplication([])


def shown(**rec):
    return tdw._resolve_ltt_submission_status(rec)[0]


@pytest.mark.parametrize("rec,expected", [
    (dict(status="Submitted & Verified", arn_number="N/A"), "Submitted"),
    (dict(status="Submitted & Verified", arn_number=""), "Submitted"),
    (dict(status="Submitted & Verified", arn_number="AA290121000069S"), "Submitted & E-verified"),
    (dict(latest_status="Submitted & Verified", latest_arn="N/A"), "Submitted"),
    (dict(latest_status="Submitted & Verified", latest_arn="123456789150726"), "Submitted & E-verified"),
    (dict(status="Submitted (Not Verified)", arn_number="123456789150726"), "Submitted (e-verification pending)"),
    (dict(status="Draft", arn_number="N/A"), "Not submitted"),
    (dict(status="Not Submitted", arn_number="N/A"), "Not submitted"),
])
def test_the_pill_text(rec, expected):
    assert shown(**rec) == expected


def test_the_two_greens_differ_light_for_submitted_dark_for_everified():
    def lightness(hex_colour):
        h = hex_colour.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return (r + g + b) / 3
    light = tdw.STATUS_THEMES["Submitted"]["bg"]
    dark = tdw.STATUS_THEMES["Submitted & E-verified"]["bg"]
    assert lightness(light) > 2 * lightness(dark)
    for theme in (tdw.STATUS_THEMES["Submitted"], tdw.STATUS_THEMES["Submitted & E-verified"]):
        h = theme["border"].lstrip("#")
        assert int(h[2:4], 16) > int(h[0:2], 16) and int(h[2:4], 16) > int(h[4:6], 16)      # green dominant


def test_the_status_filter_separates_submitted_from_everified(tmp_path):
    import json
    import security
    from database import SeraDatabase
    salt = str(tmp_path / "t.salt")
    security.generate_and_save_salt(salt)
    key = security.derive_key_hex("testpass123", security.load_salt(salt))
    db = SeraDatabase(str(tmp_path / "m.db"), key, raw_db_path=str(tmp_path / "r.db"))
    for arn, pan in (("N/A", "ABCPD1234E"), ("AA290121000069S", "XYZAB9876C")):
        db.insert_tracker_dump(portal="GST Portal (GSTR-3B)", period_label="June (FY 2026-27)", arn_number=arn,
                               capture_method="SGT_live", status="Submitted & Verified", pan=pan, filing_type="GSTR-3B",
                               raw_payload_json=json.dumps({"pan": pan}))
    win = tdw.TrackerDumpWindow(db)
    win.cmb_view_mode.setCurrentIndex(1)
    APP.processEvents()
    items = [win.cmb_status.itemText(i) for i in range(win.cmb_status.count())]
    assert "Submitted" in items

    def pills():
        return sorted(win.table.cellWidget(r, win.COL_STATUS).findChild(tdw.QLabel, "status_label").text()
                      for r in range(win.table.rowCount()))
    assert pills() == ["Submitted", "Submitted & E-verified"]
    win.cmb_status.setCurrentText("Submitted")
    APP.processEvents()
    assert pills() == ["Submitted"]
    win.cmb_status.setCurrentText("Submitted & E-verified")
    APP.processEvents()
    assert pills() == ["Submitted & E-verified"]
    win.deleteLater()
