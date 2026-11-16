"""
SCK new-client capture (core/rotation/new_client.py) against a temp database: a login that worked
for an ID Sera does not know either creates the client (PK + name known) or is kept as a
tracker-dump row; the password in that row is hidden in every reader and removed once the client
is saved. Identifiers and passwords are fictional.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.rotation import new_client
from database import SeraDatabase
from sera_db.tracker_dump import REDACTED, SCK_LOGIN, is_complete, redact_payload

PW = "Fict#Pw-91"
PAN = "ABCDE1234F"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = SeraDatabase(os.path.join(self.tmp, "t.db"),
                               "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                               raw_db_path=os.path.join(self.tmp, "raw.db"))
        cols = self.db.get_mcl_columns()
        self.pan_col = next(c["id"] for c in cols if c.get("is_internal_pk"))
        self.company_col = next(c["id"] for c in cols if c["label"] == "NAME OF COMPANY")
        self.uid_col = next(c["id"] for c in cols if c["label"] == "USER ID")
        self.pwd_col = next(c["id"] for c in cols if c["label"] == "IT_Password")
        self.svc = self.db.create_service(name="Fict Portal", login_page_link="https://e.example.test/login",
                                          userid_column_id=self.uid_col, password_column_id=self.pwd_col,
                                          username_selector="#u", password_selector="#p",
                                          automation_mode="manual")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_login(self, identity=PAN, name="Fict Traders Pvt Ltd", tested=True, user_id="", password=PW):
        profile = {"pan": identity} if identity else {}
        if name:
            profile["company_name"] = name
        return new_client.handle_login(self.db, service_id=self.svc, portal="Fict Portal",
                                       identity=identity, user_id=user_id, password=password,
                                       tested=tested, profile=profile, actor="Staff")

    def dump_rows(self):
        return [r for r in self.db.get_tracker_dumps(limit=-1) if r["capture_method"] == SCK_LOGIN]


class TestCreate(_Base):
    def test_creates_with_pk_and_name(self):
        r = self.run_login()
        self.assertEqual(r.outcome, "created")
        client = self.db.get_client(r.client_id)
        v = client["values"]
        self.assertEqual(v[self.pan_col], PAN)
        self.assertEqual(v[self.company_col], "Fict Traders Pvt Ltd")
        self.assertEqual(v[self.uid_col], PAN)             # no separate user id typed: the ID itself
        self.assertEqual(v[self.pwd_col], PW)
        self.assertEqual(self.dump_rows(), [])

    def test_user_id_column_gets_the_typed_login_id(self):
        r = self.run_login(user_id="fict.user")
        self.assertEqual(self.db.get_client(r.client_id)["values"][self.uid_col], "fict.user")

    def test_a_tested_password_tags_the_client_an_untested_one_does_not(self):
        with patch.object(self.db, "tag_client_scc_verified") as tag:
            self.run_login(tested=True)
        tag.assert_called_once()
        self.tearDown()
        self.setUp()
        with patch.object(self.db, "tag_client_scc_verified") as tag:
            self.run_login(tested=False)
        tag.assert_not_called()

    def test_audit_line_never_holds_the_password(self):
        with patch.object(self.db, "log_action") as log:
            self.run_login()
        for call in log.call_args_list:
            self.assertNotIn(PW, json.dumps(call.kwargs, default=str))

    def test_links_earlier_tracker_dumps_of_the_same_id(self):
        with patch.object(self.db, "link_unassigned_tracker_dumps", return_value=2) as link:
            r = self.run_login()
        link.assert_called_once_with(r.client_id, PAN)


class TestDump(_Base):
    def test_no_name_goes_to_the_tracker_dump(self):
        r = self.run_login(name="")
        self.assertEqual(r.outcome, "dumped")
        self.assertEqual(r.missing, ["name"])
        self.assertIn("client name", r.message)
        self.assertIsNone(self.db.get_client_by_pan(PAN))          # no client was created
        rows = self.dump_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(is_complete(rows[0]["status"], rows[0]["arn_number"]))

    def test_no_pk_goes_to_the_tracker_dump(self):
        r = self.run_login(identity="", user_id="jsmith")
        self.assertEqual(r.outcome, "dumped")
        self.assertEqual(r.missing, ["pk"])
        row = self.dump_rows()[0]
        login = self.db.get_sck_login(row["id"])
        self.assertEqual(login["user_id"], "jsmith")
        self.assertEqual(login["password"], PW)

    def test_the_password_is_hidden_in_the_reader_and_kept_in_the_row(self):
        self.run_login(name="")
        row = self.dump_rows()[0]
        full = self.db.get_tracker_dumps(limit=1, dump_id=row["id"])[0]
        self.assertNotIn(PW, full["raw_payload_json"])
        self.assertIn(REDACTED, full["raw_payload_json"])
        self.assertEqual(self.db.get_sck_login(row["id"])["password"], PW)

    def test_the_slim_list_never_carries_the_payload(self):
        self.run_login(name="")
        for r in self.db.get_tracker_dumps(limit=-1, slim=True):
            self.assertNotIn(PW, json.dumps(r, default=str))

    def test_drs_history_skips_a_kept_login(self):
        from core.drs.engine import _tracker_history_entries
        self.run_login(name="")
        self.assertEqual(_tracker_history_entries(self.db), [])

    def test_strip_removes_the_password_after_promotion(self):
        self.run_login(name="")
        row = self.dump_rows()[0]
        self.assertTrue(self.db.strip_sck_password(row["id"]))
        login = self.db.get_sck_login(row["id"])
        self.assertNotIn("password", login)
        self.assertTrue(login["password_moved_to_client"])
        self.assertFalse(self.db.strip_sck_password(row["id"]))


class TestDuplicateAndEdges(_Base):
    def test_a_duplicate_pk_falls_back_to_saving_on_the_existing_client(self):
        existing = self.db.add_client(values={self.pan_col: PAN, self.company_col: "Fict Traders Pvt Ltd",
                                              self.pwd_col: "Old#Pw"}, notes="", service_ids=[self.svc])
        r = self.run_login()
        self.assertEqual(r.outcome, "existing")
        self.assertEqual(r.client_id, existing)
        self.assertEqual(self.db.get_client(existing)["values"][self.pwd_col], PW)

    def test_no_password_keeps_nothing(self):
        r = self.run_login(password="")
        self.assertEqual(r.outcome, "failed")
        self.assertEqual(self.dump_rows(), [])


class TestRedactHelper(unittest.TestCase):
    def test_redacts_string_and_dict(self):
        raw = json.dumps({"sck_login": {"password": PW, "user_id": "u"}})
        self.assertNotIn(PW, redact_payload(raw))
        self.assertEqual(redact_payload({"sck_login": {"password": PW}})["sck_login"]["password"], REDACTED)

    def test_other_payloads_are_untouched(self):
        raw = json.dumps({"a": 1})
        self.assertIs(redact_payload(raw), raw)
        self.assertEqual(redact_payload("not json sck_login"), "not json sck_login")
        self.assertEqual(redact_payload(None), None)
