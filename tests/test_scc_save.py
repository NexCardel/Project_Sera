"""
tests/test_scc_save.py - SCC-U step 6, the guarded save (core/scc/save.py), on a real database
==============================================================================================
Equal to the saved password -> verified only; none -> saved; different -> replaced (D2 = automatic);
unregistered PAN -> client added (D4 = automatic); GST ignored; the audit line has who + row label,
never the value; the card's credit (automatic pick and button) reaches the save. Fictional data.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scc import Attempt, SccCard, SccSaver, save_verified
from core.scc.save import CREATED, REPLACED, SAVED, VERIFIED
from database import SeraDatabase

PAN = "ABCPD1234E"
OTHER = "XYZAB5678C"
SECRET = "Zx#Fictional-Only-9"
OLD = "Old#Fictional-Only-1"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = SeraDatabase(os.path.join(self.tmp, "scc.db"), "0123456789abcdef" * 4)
        cols = {c["label"]: c["id"] for c in self.db.get_mcl_columns()}
        self.pan_col = cols.get("PAN") or self.db.create_mcl_column("PAN", "alphanumeric", is_identity=1, is_internal_pk=1)
        self.name_col = cols.get("NAME OF COMPANY") or self.db.create_mcl_column("NAME OF COMPANY", "text", is_identity=1)
        self.pwd_col = cols.get("IT_Password") or self.db.create_mcl_column("IT_Password", "password", is_identity=0)
        svc = self.db.get_service_for_portal("Income Tax")
        self.svc_id = svc["id"] if svc else self.db.create_service(
            name="Income Tax", login_page_link="https://eportal.incometax.gov.in/iec/foservices/#/login",
            userid_column_id=self.pan_col, password_column_id=self.pwd_col, username_selector="#panAdhaarUserId",
            password_selector="input[type='password']", automation_mode="extension", extension_flow="double")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, saved="", pan=PAN):
        values = {self.pan_col: pan, self.name_col: "Wasil Taxpayer"}
        if saved:
            values[self.pwd_col] = saved
        return self.db.add_client(values=values, notes="", service_ids=[self.svc_id])

    def saved(self, cid):
        return (self.db.get_client(cid)["values"].get(self.pwd_col) or "")

    def audit(self, cid):
        with self.db._connect() as conn:
            return conn.execute("SELECT actor, action, detail FROM audit_log WHERE client_id = ? ORDER BY id",
                                (cid,)).fetchall()


class TestSaveVerified(Base):
    def test_no_saved_password_saves_and_verifies(self):
        cid = self.client()
        r = save_verified(self.db, SECRET, pan=PAN, client_id=cid, row_label="Combo 3", actor="Operator")
        self.assertEqual((r.outcome, r.client_id), (SAVED, cid))
        self.assertEqual(self.saved(cid), SECRET)
        self.assertTrue(self.db.is_client_scc_verified(client_id=cid))

    def test_equal_saved_password_only_marks_verified(self):
        cid = self.client(saved=SECRET)
        self.assertFalse(self.db.is_client_scc_verified(client_id=cid))
        r = save_verified(self.db, SECRET, pan=PAN, client_id=cid, row_label="Saved password", actor="Operator")
        self.assertEqual(r.outcome, VERIFIED)
        self.assertEqual(self.saved(cid), SECRET)
        self.assertTrue(self.db.is_client_scc_verified(client_id=cid))

    def test_different_saved_password_is_replaced_and_said_so(self):
        cid = self.client(saved=OLD)
        r = save_verified(self.db, SECRET, pan=PAN, row_label="Combo 1", actor="Operator")   # found by PAN
        self.assertEqual((r.outcome, r.client_id), (REPLACED, cid))
        self.assertEqual(self.saved(cid), SECRET)
        self.assertTrue(any("replaced" in (d or "") for _, _, d in self.audit(cid)))

    def test_unregistered_pan_adds_the_client(self):
        self.assertIsNone(self.db.get_client_by_pan(OTHER))
        r = save_verified(self.db, SECRET, pan=OTHER, row_label="Combo 2", client_name="ABC Enterprises",
                          actor="Operator")
        self.assertEqual(r.outcome, CREATED)
        new = self.db.get_client_by_pan(OTHER)
        self.assertEqual(new["id"], r.client_id)
        self.assertEqual(new["values"].get(self.pwd_col), SECRET)
        self.assertEqual(r.client_name, "ABC Enterprises")
        self.assertIn("Password verified via SCC", new["notes"])

    def test_gst_and_empty_are_ignored(self):
        cid = self.client(saved=OLD)
        self.assertIsNone(save_verified(self.db, SECRET, pan=PAN, client_id=cid, portal="GST"))
        self.assertIsNone(save_verified(self.db, "  ", pan=PAN, client_id=cid))
        self.assertEqual(self.saved(cid), OLD)
        self.assertFalse(self.db.is_client_scc_verified(client_id=cid))

    def test_audit_has_who_and_row_label_never_the_value(self):
        cid = self.client(saved=OLD)
        save_verified(self.db, SECRET, pan=PAN, client_id=cid, row_label="Combo 4", actor="Operator")
        rows = self.audit(cid)
        scc = [r for r in rows if "SCC" in (r[2] or "")]
        self.assertTrue(scc)
        self.assertTrue(all(r[0] == "Operator" for r in scc))
        self.assertTrue(any("Combo 4" in r[2] for r in scc))
        for actor, action, detail in rows:
            self.assertNotIn(SECRET, detail or "")
            self.assertNotIn(OLD, detail or "")
        unreg = save_verified(self.db, SECRET, pan=OTHER, row_label="Combo 2", actor="Operator")
        for actor, action, detail in self.audit(unreg.client_id):
            self.assertNotIn(SECRET, detail or "")


class TestCardToSave(Base):
    """SccCard.credit (the automatic pick and "This one worked") -> SccSaver -> master.db."""

    def world(self, client_id, saved_row=True):
        self.results = []
        self.att = Attempt(pan=PAN, hwnd=1, session_id="s", client_id=client_id, saved_row=saved_row, opened_at=0.0)
        self.card = None
        saver = SccSaver(self.db, lambda aid, label: self.card.row_text(aid, label), lambda: "Operator",
                         after=self.results.append, echo=lambda s: None)
        self.card = SccCard(self.db, open_card=lambda *a: True, close_card=lambda a: None,
                            on_worked=saver.on_worked, echo=lambda s: None)
        self.assertTrue(self.card.open(self.att))
        return self.card

    def first_combo(self):
        return self.db.generate_scc_passwords(PAN)[0]["value"]

    def test_button_saves_the_combination(self):
        cid = self.client(saved=OLD)
        card = self.world(cid)
        label = card._rows[self.att.attempt_id][0][0]
        self.assertIsNotNone(card.row_worked(self.att.attempt_id, label))
        self.assertEqual(self.saved(cid), self.first_combo())
        self.assertEqual([r.outcome for r in self.results], [REPLACED])
        self.assertTrue(self.db.is_client_scc_verified(client_id=cid))

    def test_crediting_the_saved_row_marks_verified_only(self):
        cid = self.client(saved=OLD)
        card = self.world(cid)
        self.assertIsNotNone(card.row_worked(self.att.attempt_id, "Saved password"))
        self.assertEqual(self.saved(cid), OLD)
        self.assertEqual([r.outcome for r in self.results], [VERIFIED])
        self.assertTrue(self.db.is_client_scc_verified(client_id=cid))

    def test_unregistered_attempt_adds_the_client(self):
        card = self.world(None, saved_row=False)
        label = card._rows[self.att.attempt_id][1][0]
        card.credit(self.att, label)
        new = self.db.get_client_by_pan(PAN)
        self.assertIsNotNone(new)
        self.assertEqual(new["values"].get(self.pwd_col), self.db.generate_scc_passwords(PAN)[1]["value"])
        self.assertEqual([r.outcome for r in self.results], [CREATED])

    def test_a_refused_row_saves_nothing(self):
        cid = self.client(saved=OLD)
        card = self.world(cid)
        label = card._rows[self.att.attempt_id][0][0]
        card.mark_failed(self.att, label)
        self.assertFalse(card.credit(self.att, label))
        self.assertEqual(self.saved(cid), OLD)
        self.assertEqual(self.results, [])

    def test_a_save_error_is_swallowed(self):
        cid = self.client()
        card = self.world(cid)
        self.db.update_client_single_field = None       # any db failure inside the save
        label = card._rows[self.att.attempt_id][0][0]
        self.assertIsNotNone(card.row_worked(self.att.attempt_id, label))
        self.assertEqual(self.results, [])


if __name__ == "__main__":
    unittest.main()
