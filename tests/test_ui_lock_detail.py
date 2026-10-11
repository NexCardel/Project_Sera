"""Behaviour lock R2 (Client detail): shortcuts per service, masking, clipboard wipe, notes."""
import ui_lock_support as S

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import ui.windows.client_detail_window as cdw
from ui.utils.masking import mask_password
from ui.windows.client_detail_window import ClickableLabel, ClientDetailWindow

AUTOFILL, SMTI, MECP = "autofill", "smti", "mecp"


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    wins = []

    def make():
        w = ClientDetailWindow(db, actor="Admin")
        w.resize(900, 700)
        wins.append(w)
        return w

    yield db, ids, make
    S.dispose(*wins)


def _recorder(win):
    calls = []
    win._launch_extension_autofill = lambda svc: calls.append((AUTOFILL, svc["id"]))
    win._launch_manual_assist = lambda svc: calls.append((SMTI, svc["id"]))
    win._launch_manual_copy = lambda svc: calls.append((MECP, svc["id"]))
    return calls


def _ten_service_client(db):
    """A client with ten services (modes cycle; some with a secondary mode), for the Alt+n rows."""
    c = S.cols_by_label(db)
    modes = [("extension", "smti"), ("smti", ""), ("manual", "extension"), ("extension", ""),
             ("smti", "manual"), ("manual", ""), ("extension", "manual"), ("smti", "extension"),
             ("manual", "smti"), ("extension", "")]
    existing = {s["name"]: s["id"] for s in db.get_services()}
    sids = [existing["GST"], existing["Income Tax"], existing["Email"]]
    for i in range(3, 10):
        sids.append(db.create_service(f"Portal {i + 1}", f"https://portal{i + 1}.example/login", c["USER ID"],
                                      c["GST_Password"], "#u", "#p"))
    for sid, (m1, m2) in zip(sids, modes):
        s = db.get_service(sid)
        db.update_service(sid, s["name"], s["login_page_link"], s["userid_column_id"], s["password_column_id"],
                          s["username_selector"], s["password_selector"], automation_mode=m1,
                          extension_flow=s["extension_flow"], success_selector=s["success_selector"],
                          arn_selector=s["arn_selector"], automation_mode_2=m2)
    cid = db.add_client({c["NAME OF COMPANY"]: "Echo Exports", c["PAN"]: "ABCDE1005F"}, "", sids)
    return cid, sids, modes


_ACTION_OF = {"extension": AUTOFILL, "smti": SMTI, "manual": MECP}


# ---- R2.2 Alt+1..9 / Alt+Ctrl+1..9 ----------------------------------------------------------

def test_alt_n_and_alt_ctrl_n_run_the_services_actions_and_labels_match(world):
    db, ids, make = world
    cid, sids, modes = _ten_service_client(db)
    win = make()
    calls = _recorder(win)
    win.load_client(cid)
    S.pump()

    shortcuts = {sc.key().toString(): sc for sc in win._service_shortcuts}
    buttons = win.scroll_widget.findChildren(QPushButton)
    tips = {b.toolTip(): b for b in buttons if b.toolTip()}

    expected_keys = set()
    for i, (sid, (m1, m2)) in enumerate(zip(sids, modes)):
        n = i + 1
        name = db.get_service(sid)["name"]
        label_of = {AUTOFILL: "Fast Autofill", SMTI: "SMTI Manual Assist", MECP: "MECP Manual Copy"}
        a1 = _ACTION_OF[m1]
        tip1 = f"{label_of[a1]}: {name}" + (f" (Alt+{n})" if n <= 9 else "")
        assert tip1 in tips, tip1                                    # the on-screen label names the real key
        if n <= 9:
            key = QKeySequence(f"Alt+{n}").toString()
            expected_keys.add(key)
            calls.clear()
            shortcuts[key].activated.emit()
            assert calls == [(a1, sid)]
        calls.clear()
        tips[tip1].click()
        assert calls == [(a1, sid)]                                   # the button runs the same action

        a2 = _ACTION_OF.get(m2)
        if m2 and a2 != a1:
            tip2 = f"{label_of[a2]}: {name}" + (f" (Alt+Ctrl+{n})" if n <= 9 else "")
            assert tip2 in tips, tip2
            if n <= 9:
                key2 = QKeySequence(f"Alt+Ctrl+{n}").toString()
                expected_keys.add(key2)
                calls.clear()
                shortcuts[key2].activated.emit()
                assert calls == [(a2, sid)]
            calls.clear()
            tips[tip2].click()
            assert calls == [(a2, sid)]
        else:
            assert not any(t.startswith(label_of.get(a2, "~")) and t.endswith(f"{name} (Alt+Ctrl+{n})") for t in tips)

    assert set(shortcuts) == expected_keys                            # nothing beyond Alt+1..9 / Alt+Ctrl+1..9
    assert not any(k.endswith("0") or "10" in k for k in shortcuts)
    info = [l for l in win.scroll_widget.findChildren(QLabel) if "Alt+1..9" in l.text()]
    assert info and "Alt+Ctrl+1..9" in info[0].toolTip()


def test_alt_digit_key_press_reaches_the_shortcut(world):
    db, ids, make = world
    win = make()
    calls = _recorder(win)
    win.show()
    win.activateWindow()
    win.load_client(ids["A"])
    S.pump()
    QTest.keyClick(win, Qt.Key_1, Qt.AltModifier)
    S.pump()
    if not calls:
        pytest.skip("offscreen platform did not deliver the shortcut (window not active); the QShortcut-level test covers it")
    assert calls == [(AUTOFILL, db.get_client_services(ids["A"])[0]["id"])]


def test_run_service_action_uses_automation_mode_1_of_the_service(world):
    db, ids, make = world
    win = make()
    calls = _recorder(win)
    win.run_service_action(ids["A"], {"id": 77, "automation_mode": "smti"})
    win.run_service_action(ids["A"], {"id": 78, "automation_mode": "manual"})
    win.run_service_action(ids["A"], {"id": 79, "automation_mode": "extension"})
    assert calls == [(SMTI, 77), (MECP, 78), (AUTOFILL, 79)]
    assert win.client is None                                          # the window's own state is restored


# ---- R2.1 Esc, ID chip ----------------------------------------------------------------------

def test_esc_goes_back_once_and_the_back_button_does_the_same(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    got = []
    win.back_requested.connect(lambda: got.append(1))
    assert win.back_shortcut.key() == QKeySequence(Qt.Key_Escape)
    assert "Esc" in win.back_btn.toolTip()
    win.back_shortcut.activated.emit()
    win.back_shortcut.activated.emit()                                 # a second Esc inside the guard is ignored
    assert got == [1]
    QTest.qWait(400)                                                   # guard released
    win.back_btn.click()
    assert got == [1, 1]


def test_id_chip_copies_the_client_token_and_toasts(world):
    db, ids, make = world
    win = make()
    toasts = []
    win.toast_requested.connect(lambda m, ms: toasts.append(m))
    win.load_client(ids["A"])
    token = db.get_client(ids["A"])["client_id_token"]
    assert win.token_badge.text() in (f"CLI-{token.zfill(5)}", f"ID: {token}")
    assert token in win.token_badge.toolTip()
    win.token_badge.click()
    assert QApplication.clipboard().text() == token
    assert toasts == [f"Copied Client Token: {token}"]


def test_header_shows_the_identity_columns(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    prim, sec = win.primary_name_lbl.text(), win.secondary_name_lbl.text()
    identity = [c for c in db.get_mcl_columns() if c["is_identity"]]
    vals = [db.get_client(ids["A"])["values"].get(c["id"], "") for c in identity if db.get_client(ids["A"])["values"].get(c["id"])]
    assert prim == (vals[0] if vals else "Client Profile")
    assert sec == (vals[1] if len(vals) > 1 else "")


def test_missing_client_goes_back_instead_of_showing_a_blank_profile(world, monkeypatch):
    db, ids, make = world
    win = make()
    monkeypatch.setattr(cdw.MessageBox, "warning", lambda *a, **k: None)
    got = []
    win.back_requested.connect(lambda: got.append(1))
    win.load_client(9999)
    assert got == [1]


# ---- R2.3 masking ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode, n, raw, shown", [
    ("last_n", 4, "gstPass#A1x9", "********" + "A1x9"),
    ("last_n", 2, "gstPass#A1x9", "**********" + "x9"),
    ("first_n", 4, "gstPass#A1x9", "gstP" + "********"),
    ("first_n", 3, "itPass#A1y8", "itP" + "********"),
    ("last_n", 4, "abc", "***bc"),                    # short secret: reveal capped at len-1, at least 3 stars
    ("first_n", 4, "abc", "ab***"),
    ("last_n", 4, "x", "***"),
    ("full_dots", 4, "gstPass#A1x9", "*" * 12),
    ("full_dots", 4, "abc", "*" * 8),
])
def test_mask_password_rules(mode, n, raw, shown):
    assert mask_password(raw, mode, n) == shown
    assert mask_password("", mode, n) == ""


def _security_labels(win):
    return [l for l in win.scroll_widget.findChildren(QLabel) if isinstance(l, ClickableLabel) and l.cursor().shape() == Qt.PointingHandCursor]


@pytest.mark.parametrize("mode, n", [("last_n", 4), ("first_n", 4), ("last_n", 2), ("first_n", 3)])
def test_detail_masks_secrets_with_the_saved_mode_and_hides_the_raw_value(world, mode, n):
    db, ids, make = world
    db.set_setting("mask_mode", mode)
    db.set_setting("mask_reveal_count", str(n))
    win = make()
    win.load_client(ids["A"])
    texts = [l.text() for l in win.scroll_widget.findChildren(QLabel)]
    for raw in (S.GST_PW_A, S.IT_PW_A):
        assert raw not in texts
        assert mask_password(raw, mode, n) in texts
    assert "alpha.user" not in texts                                    # USER ID is a password-type column: masked too
    assert mask_password("alpha.user", mode, n) in texts


def test_default_masking_is_last_n_with_four_visible(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    assert mask_password(S.GST_PW_A, "last_n", 4) in [l.text() for l in win.scroll_widget.findChildren(QLabel)]


def test_show_hide_toggles_one_field_and_copy_copies_the_real_value(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    shows = [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Show / Hide Password"]
    copies = [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"]
    assert len(shows) == 3 and len(copies) == 3                         # USER ID, GST_Password, IT_Password
    masked = mask_password(S.GST_PW_A, "last_n", 4)

    def texts():
        return [l.text() for l in win.scroll_widget.findChildren(QLabel)]

    shows[1].setChecked(True)                                           # GST_Password revealed, others not
    assert S.GST_PW_A in texts() and masked not in texts()
    assert S.IT_PW_A not in texts()
    shows[1].setChecked(False)
    assert S.GST_PW_A not in texts() and masked in texts()

    copies[1].click()
    assert QApplication.clipboard().text() == S.GST_PW_A                # the real value, not the mask
    assert QApplication.clipboard().text() != masked


def test_show_hide_button_can_be_switched_off_in_settings(world):
    db, ids, make = world
    db.set_setting("show_hide_btn_enabled", "0")
    win = make()
    win.load_client(ids["A"])
    assert not [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Show / Hide Password"]
    assert [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"]


def test_copy_logs_audit_activity_and_alert(world):
    db, ids, make = world
    win = make()
    alerts = []
    win.action_alert_requested.connect(lambda k, name: alerts.append(k))
    win.load_client(ids["A"])
    copies = [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"]
    copies[2].click()
    logs = [l for l in db.get_audit_logs(client_id=ids["A"]) if l["action"] == "manual_copy"]
    assert len(logs) == 1 and logs[0]["actor"] == "Admin"
    assert logs[0]["detail"] == "Quick copied field: IT_Password"
    assert alerts == ["manual_copy"]
    assert QApplication.clipboard().text() == S.IT_PW_A


# ---- R2.4 clipboard wipe --------------------------------------------------------------------

def _capture_single_shots(monkeypatch):
    calls = []

    class FakeTimer(QTimer):
        @staticmethod
        def singleShot(ms, fn=None, *a):
            calls.append((ms, fn))

    monkeypatch.setattr(cdw, "QTimer", FakeTimer)
    return calls


def test_secret_copy_schedules_a_wipe_after_30s_by_default(world, monkeypatch):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    shots = _capture_single_shots(monkeypatch)
    [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"][1].click()
    assert [ms for ms, _ in shots] == [30000]


def test_wipe_delay_follows_clipboard_clear_seconds(world, monkeypatch):
    db, ids, make = world
    db.set_setting("clipboard_clear_seconds", "45")
    win = make()
    win.load_client(ids["A"])
    shots = _capture_single_shots(monkeypatch)
    [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"][0].click()
    assert [ms for ms, _ in shots] == [45000]


def test_identity_copy_is_never_wiped(world, monkeypatch):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    shots = _capture_single_shots(monkeypatch)
    [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy PAN"][0].click()
    assert QApplication.clipboard().text() == "ABCDE1001F"
    assert shots == []


def test_wipe_clears_only_while_the_clipboard_still_holds_the_copied_value(world):
    db, ids, make = world
    db.set_setting("clipboard_clear_seconds", "1")
    win = make()
    win.load_client(ids["A"])
    copy_gst = [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"][1]

    copy_gst.click()
    assert QApplication.clipboard().text() == S.GST_PW_A
    QTest.qWait(1400)
    assert QApplication.clipboard().text() == ""                        # wiped

    copy_gst.click()
    QApplication.clipboard().setText("something the user copied since")
    QTest.qWait(1400)
    assert QApplication.clipboard().text() == "something the user copied since"   # left alone


# ---- R2.5 notes -----------------------------------------------------------------------------

def test_notes_autosave_after_a_pause(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["B"])
    assert win.notes_edit.toPlainText() == ""
    win.notes_edit.setPlainText("  Call back on Monday  ")
    assert "Saving" in win.notes_status_lbl.text()
    QTest.qWait(800)
    assert db.get_client(ids["B"])["notes"] == "Call back on Monday"
    assert "Saved" in win.notes_status_lbl.text()
    logs = [l for l in db.get_audit_logs(client_id=ids["B"]) if l["action"] == "update_notes"]
    assert len(logs) == 1 and logs[0]["detail"] == "Auto-saved client notes"


def test_leaving_with_esc_right_after_typing_still_saves_the_notes(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["B"])
    got = []
    win.back_requested.connect(lambda: got.append(1))
    win.notes_edit.setPlainText("typed just before Esc")
    win.back_shortcut.activated.emit()                                 # no wait: the timer is still pending
    assert db.get_client(ids["B"])["notes"] == "typed just before Esc"
    assert got == [1]


def test_loading_existing_notes_does_not_resave_them(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["A"])
    assert win.notes_edit.toPlainText() == "Alpha notes"
    QTest.qWait(700)
    assert [l for l in db.get_audit_logs(client_id=ids["A"]) if l["action"] == "update_notes"] == []


# ---- R2.6 quick copy "off" ------------------------------------------------------------------

def test_a_column_with_quick_copy_off_has_no_copy_button_and_no_click_to_copy(world):
    db, ids, make = world
    cols = S.cols_by_label(db)
    allowed = [cid for label, cid in cols.items() if label not in ("PAN", "GST_Password")]
    db.bulk_update_mcl_quick_copy(allowed)
    win = make()
    win.load_client(ids["A"])
    tips = [b.toolTip() for b in win.scroll_widget.findChildren(QPushButton)]
    assert "Copy PAN" not in tips and "Copy GSTIN" in tips
    labels = {l.text(): l for l in win.scroll_widget.findChildren(QLabel)}
    assert not isinstance(labels["ABCDE1001F"], ClickableLabel)         # plain text, nothing to click
    assert isinstance(labels["29ABCDE1001F1Z5"], ClickableLabel)
    assert len([b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Copy Password"]) == 2
    assert not isinstance(labels[mask_password(S.GST_PW_A, "last_n", 4)], ClickableLabel)


def test_empty_fields_show_not_set_and_have_no_copy_button(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["B"])
    texts = [l.text() for l in win.scroll_widget.findChildren(QLabel)]
    assert texts.count("Not set") >= 1
    assert "Copy GSTIN" not in [b.toolTip() for b in win.scroll_widget.findChildren(QPushButton)]
    assert [b for b in win.scroll_widget.findChildren(QPushButton) if b.toolTip() == "Show / Hide Password"] == []


def test_loading_a_client_records_a_view_in_the_audit_log(world):
    db, ids, make = world
    win = make()
    win.load_client(ids["C"])
    for _ in range(40):                                                 # the audit write runs on a thread
        if any(l["action"] == "view" for l in db.get_audit_logs(client_id=ids["C"])):
            break
        QTest.qWait(50)
    assert any(l["action"] == "view" and l["actor"] == "Admin" for l in db.get_audit_logs(client_id=ids["C"]))
