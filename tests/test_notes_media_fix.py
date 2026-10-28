import json
import os
import shutil
import tempfile
import time
import unittest

from PySide6.QtWidgets import QApplication

import security
from database import SeraDatabase


class TestNotesMediaFix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_master.db")
        self.raw_db_path = os.path.join(self.tmp_dir.name, "rawPayload.db")
        self.salt_path = os.path.join(self.tmp_dir.name, "test_sera.salt")
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.hex_key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, self.hex_key, raw_db_path=self.raw_db_path)

        # Create user media dir
        self.user_media_dir = os.path.join(self.tmp_dir.name, "media")
        os.makedirs(self.user_media_dir, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_01_recapture_preserves_media(self):
        """Phase 1.1: Re-inserting the same dataset_key preserves user notes and screenshot."""
        payload1 = {"client_id": 1, "status": "Draft", "period_label": "AY 2026-27", "form": "ITR-1"}
        dkey = "ITR:TESTPAN001:AY 2026-27:ITR-1:0"

        res1 = self.db.insert_tracker_dump(
            client_id=1,
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number=None,
            capture_method="VSDC",
            status="Draft",
            raw_payload_json=json.dumps(payload1),
            dataset_key=dkey
        )
        row1_id = res1["id"]

        # User saves notes and screenshot on this capture
        self.db.save_tracker_dump_media(row1_id, "User note before re-capture", "media/screenshot_test1.png")
        saved_m = self.db.get_tracker_dump_media(row1_id)
        self.assertEqual(saved_m["notes"], "User note before re-capture")
        self.assertEqual(saved_m["screenshot_path"], "media/screenshot_test1.png")

        # Now re-capture arrives (e.g. status updated to Filed, same dataset_key)
        payload2 = {"client_id": 1, "status": "Filed", "period_label": "AY 2026-27", "form": "ITR-1", "arn": "ARN123456"}
        res2 = self.db.insert_tracker_dump(
            client_id=1,
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number="ARN123456",
            capture_method="VSDC",
            status="Filed",
            raw_payload_json=json.dumps(payload2),
            dataset_key=dkey
        )
        row2_id = res2["id"]
        self.assertNotEqual(row1_id, row2_id, "Replacement INSERT should generate a new row id")

        # Verify the media was carried over to the new row
        m2 = self.db.get_tracker_dump_media(row2_id)
        self.assertEqual(m2["notes"], "User note before re-capture", "Notes should survive re-capture")
        self.assertEqual(m2["screenshot_path"], "media/screenshot_test1.png", "Screenshot should survive re-capture")

    def test_02_draft_purge_preserves_media(self):
        """Phase 1.2: Unsubmitted draft purge preserves user notes and screenshot."""
        payload_draft = {"taxpayer_id": "PANPURGE01", "status": "Draft", "period_label": "AY 2026-27"}
        dkey_draft = "ITR:PANPURGE01:AY 2026-27:DRAFT:1"

        res_draft = self.db.insert_tracker_dump(
            pan="PANPURGE01",
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number=None,
            capture_method="SDC_DOM",
            status="Draft",
            raw_payload_json=json.dumps(payload_draft),
            dataset_key=dkey_draft
        )
        draft_id = res_draft["id"]
        self.db.save_tracker_dump_media(draft_id, "Draft notes that must not vanish", "media/draft_snap.png")

        # A new draft arrives for the same assessee & period with a slightly different key
        payload_draft2 = {"taxpayer_id": "PANPURGE01", "status": "Draft", "period_label": "AY 2026-27", "step": "2"}
        dkey_draft2 = "ITR:PANPURGE01:AY 2026-27:DRAFT:2"
        res_draft2 = self.db.insert_tracker_dump(
            pan="PANPURGE01",
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number=None,
            capture_method="SDC_DOM",
            status="Draft",
            raw_payload_json=json.dumps(payload_draft2),
            dataset_key=dkey_draft2
        )
        new_id = res_draft2["id"]

        m = self.db.get_tracker_dump_media(new_id)
        self.assertEqual(m["notes"], "Draft notes that must not vanish")
        self.assertEqual(m["screenshot_path"], "media/draft_snap.png")

    def test_03_url_fallback_preserves_media(self):
        """Phase 1.3: Legacy URL fallback replacement preserves media."""
        url = "https://services.gst.gov.in/services/returns/gstr3b"
        p1 = {"url": url, "status": "In Progress"}
        res1 = self.db.insert_tracker_dump(
            pan="GSTIN001",
            portal="GST Portal",
            period_label=None,
            arn_number=None,
            capture_method="DOM_Tracker",
            status="In Progress",
            raw_payload_json=json.dumps(p1),
            dataset_key=None
        )
        self.db.save_tracker_dump_media(res1["id"], "URL fallback note", "media/gst.png")

        # Replacement by URL
        p2 = {"url": url, "status": "Filed"}
        res2 = self.db.insert_tracker_dump(
            pan="GSTIN001",
            portal="GST Portal",
            period_label=None,
            arn_number="AA0707240001",
            capture_method="DOM_Tracker",
            status="Filed",
            raw_payload_json=json.dumps(p2),
            dataset_key=None
        )

        m = self.db.get_tracker_dump_media(res2["id"])
        self.assertEqual(m["notes"], "URL fallback note")
        self.assertEqual(m["screenshot_path"], "media/gst.png")

    def test_04_container_media_survives_rebuild_same_key(self):
        """Phase 2: Container media survives re_resolve_all_tracker_dumps() when identity key is unchanged."""
        c_key = "CLI-00010"
        self.db.save_srpf_container_media(c_key, "Container note stable", "media/cont10.png", client_id=10)

        # Check client_container_notes table directly
        with self.db._connect_raw() as conn:
            row = conn.execute("SELECT notes, screenshot_path, client_id FROM client_container_notes WHERE identity_key = ?", (c_key,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "Container note stable")
            self.assertEqual(row[1], "media/cont10.png")
            self.assertEqual(row[2], 10)

        # Run re_resolve_all_tracker_dumps (which deletes client_raw_containers)
        self.db.re_resolve_all_tracker_dumps()

        # Check get_srpf_container_media
        m = self.db.get_srpf_container_media(c_key)
        self.assertEqual(m["notes"], "Container note stable")
        self.assertEqual(m["screenshot_path"], "media/cont10.png")

    def test_05_container_media_follows_key_change(self):
        """Phase 2: Container media follows key change when an unassigned identity resolves to a registered client."""
        pan = "ABCDE9999Z"
        # 1. Insert dump for unregistered taxpayer
        self.db.insert_tracker_dump(
            pan=pan,
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number="ARN999",
            capture_method="VSDC",
            status="Filed",
            raw_payload_json=json.dumps({"pan": pan, "status": "Filed"}),
            dataset_key=f"ITR:{pan}:AY 2026-27:ITR-1:0"
        )

        # Save media on the unassigned container (key = pan)
        self.db.save_srpf_container_media(pan, "Notes on unassigned container", "media/pan9999.png")

        # 2. Register client in master DB with this PAN
        with self.db._connect() as conn:
            cur = conn.execute(
                "INSERT INTO clients (notes, created_at, updated_at) VALUES ('', datetime('now'), datetime('now'))"
            )
            cid = cur.lastrowid
            conn.execute(
                "INSERT INTO client_values (client_id, column_id, value) VALUES (?, 1, ?)",
                (cid, pan)
            )

        # 3. Run re_resolve_all_tracker_dumps
        self.db.re_resolve_all_tracker_dumps()

        # 4. Check new container key CLI-0000X
        new_key = f"CLI-{cid:05d}"
        m = self.db.get_srpf_container_media(new_key)
        self.assertEqual(m["notes"], "Notes on unassigned container", "Notes should follow container key migration")
        self.assertEqual(m["screenshot_path"], "media/pan9999.png", "Screenshot should follow container key migration")

    def test_06_missing_file_guard_and_clear_image(self):
        """Phase 4: Missing file guard does not wipe DB path, while explicit clear removes it."""
        from ui.windows.tracker_dump_window import PayloadInspectorDialog

        item_data = {"id": 999, "identity_key": "CLI-00099", "client_id": 99}
        self.db.save_srpf_container_media("CLI-00099", "Some notes", "media/missing_screenshot.png")

        dialog = PayloadInspectorDialog.__new__(PayloadInspectorDialog)
        dialog.db = self.db
        dialog.is_container = True
        dialog.item_data = item_data
        dialog._saved_screenshot_raw = "media/missing_screenshot.png"
        dialog.current_screenshot_path = os.path.join(self.user_media_dir, "missing_screenshot.png")
        dialog._image_cleared = False

        class DummyTxt:
            def toPlainText(self):
                return "Updated notes text"
        dialog.txt_notes = DummyTxt()

        class DummyLbl:
            def setText(self, text):
                pass
            def setStyleSheet(self, s):
                pass
            def setPixmap(self, p):
                pass
        dialog.lbl_save_status = DummyLbl()
        dialog.lbl_image_preview = DummyLbl()

        # Calling _save_notes when file is missing on disk
        dialog._save_notes()

        # Check DB: screenshot_path should STILL be "media/missing_screenshot.png"
        m = self.db.get_srpf_container_media("CLI-00099")
        self.assertEqual(m["notes"], "Updated notes text")
        self.assertEqual(m["screenshot_path"], "media/missing_screenshot.png", "Missing file must NOT wipe DB path")

        # Now simulate explicit clear
        dialog._clear_image()
        m_cleared = self.db.get_srpf_container_media("CLI-00099")
        self.assertEqual(m_cleared["screenshot_path"], "", "Explicit clear MUST wipe screenshot_path")

    def test_07_legacy_absolute_path_backfill(self):
        """Phase 6: _backfill_legacy_media_paths migrates absolute paths to relative 'media/...'."""
        fake_abs_path = r"C:\OldApp\data\media\screenshot_legacy.png"
        fake_abs_path_2 = r"C:\OldApp\data\media\login_legacy.png"
        with self.db._connect_raw() as conn:
            conn.execute(
                "INSERT INTO tracker_dump (id, client_id, portal, period_label, status, raw_payload_json, created_at, dataset_key, screenshot_path, screenshot_path_2) "
                "VALUES (101, 1, 'Income Tax', 'AY 2026-27', 'Filed', '{}', datetime('now'), 'TEST:DUMP:101', ?, ?)",
                (fake_abs_path, fake_abs_path_2)
            )

        # Run maintenance backfill
        self.db._backfill_legacy_media_paths()

        with self.db._connect_raw() as conn:
            row = conn.execute("SELECT screenshot_path, screenshot_path_2 FROM tracker_dump WHERE id = 101").fetchone()
            self.assertEqual(row[0], "media/screenshot_legacy.png")
            self.assertEqual(row[1], "media/login_legacy.png")


    def test_17_notes_autosave_does_not_clobber_evidence_attached_while_open(self):
        """A VSDC247 frame attached after the dialog opened wins over the stale empty slot."""
        from PIL import Image
        from ui.windows.tracker_dump_window import NotesAndMediaDialog

        res = self.db.insert_tracker_dump(
            pan="PAN247005",
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number="901036690231027",
            capture_method="VSDC247_itr_ack",
            status="Filed",
            raw_payload_json=json.dumps({"pan": "PAN247005"}),
            dataset_key="ITR:PAN247005:AY 2026-27:ITR-4:0",
        )
        dlg = NotesAndMediaDialog({"id": res["id"]}, db=self.db)
        dlg._save_timer.stop()
        dlg._load_notes_and_media()

        evidence = os.path.join(self.user_media_dir, "vsdc247_late.png")
        Image.new("RGB", (64, 48), (240, 240, 240)).save(evidence, "PNG")
        self.db.save_tracker_dump_media(res["id"], "", "media/vsdc247_late.png")

        dlg.txt_notes.setPlainText("typed while open")
        dlg._save_timer.stop()
        dlg._save_notes()

        row = self.db.get_tracker_dump_media(res["id"])
        self.assertEqual(row["notes"], "typed while open")
        self.assertEqual(row["screenshot_path"], "media/vsdc247_late.png")
        dlg.close()

    def test_18_a_slot_the_user_touched_is_not_replaced_by_a_later_frame(self):
        """Adopting external evidence must never take a slot the user edited in the dialog."""
        from PIL import Image
        from ui.windows.tracker_dump_window import NotesAndMediaDialog

        res = self.db.insert_tracker_dump(
            pan="PAN247006",
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number="901036690231028",
            capture_method="VSDC247_itr_ack",
            status="Filed",
            raw_payload_json=json.dumps({"pan": "PAN247006"}),
            dataset_key="ITR:PAN247006:AY 2026-27:ITR-4:0",
        )
        dlg = NotesAndMediaDialog({"id": res["id"]}, db=self.db)
        dlg._save_timer.stop()
        dlg._load_notes_and_media()

        user_pick = os.path.join(self.user_media_dir, "user_pick.png")
        Image.new("RGB", (64, 48), (10, 10, 10)).save(user_pick, "PNG")
        dlg._slot_dirty[0] = True
        dlg._image_cleareds[0] = False
        dlg.current_screenshot_paths[0] = user_pick

        evidence = os.path.join(self.user_media_dir, "vsdc247_late_2.png")
        Image.new("RGB", (64, 48), (240, 240, 240)).save(evidence, "PNG")
        self.db.save_tracker_dump_media(res["id"], "note", "media/vsdc247_late_2.png")

        dlg._save_notes()
        self.assertEqual(self.db.get_tracker_dump_media(res["id"])["screenshot_path"],
                         "media/user_pick.png")
        dlg.close()

    def test_09_vsdc247_evidence_saved_to_notes_and_media(self):
        """Phase 9: VSDC247's evidence frame lands on the capture row and its container."""
        from PIL import Image
        from core.vsdc.vsdc247_evidence import attach_capture_evidence

        source = os.path.join(self.tmp_dir.name, "vsdc247_frame.png")
        Image.new("RGB", (64, 48), (240, 240, 240)).save(source, "PNG")

        res = self.db.insert_tracker_dump(
            pan="PAN247001",
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number="901036690231024",
            capture_method="VSDC247_itr_ack",
            status="Filed",
            raw_payload_json=json.dumps({"pan": "PAN247001", "arn": "901036690231024"}),
            dataset_key="ITR:PAN247001:AY 2026-27:ITR-4:0"
        )
        rel = attach_capture_evidence(
            self.db, source, res["id"],
            client_id=res.get("client_id"),
            unassigned_identity=res.get("unassigned_identity"),
            arn="901036690231024"
        )

        self.assertTrue(rel.startswith("media/vsdc247_"), rel)
        self.assertTrue(os.path.exists(os.path.join(self.tmp_dir.name, rel)))

        row = self.db.get_tracker_dump_media(res["id"])
        self.assertEqual(row["screenshot_path"], rel)
        container = self.db.get_srpf_container_media(res["unassigned_identity"])
        self.assertEqual(container["screenshot_path"], rel)

    def test_10_vsdc247_evidence_does_not_replace_user_media(self):
        """A screenshot the user attached by hand is never replaced by an evidence frame."""
        from PIL import Image
        from core.vsdc.vsdc247_evidence import attach_capture_evidence

        source = os.path.join(self.tmp_dir.name, "vsdc247_frame.png")
        Image.new("RGB", (64, 48), (240, 240, 240)).save(source, "PNG")

        res = self.db.insert_tracker_dump(
            pan="PAN247002",
            portal="Income Tax",
            period_label="AY 2026-27",
            arn_number="901036690231025",
            capture_method="VSDC247_itr_ack",
            status="Filed",
            raw_payload_json=json.dumps({"pan": "PAN247002"}),
            dataset_key="ITR:PAN247002:AY 2026-27:ITR-4:0"
        )
        self.db.save_tracker_dump_media(res["id"], "my note", "media/user_row.png")
        self.db.save_srpf_container_media(res["unassigned_identity"], "container note", "media/user_container.png")

        rel = attach_capture_evidence(
            self.db, source, res["id"],
            client_id=res.get("client_id"),
            unassigned_identity=res.get("unassigned_identity"),
            arn="901036690231025"
        )

        self.assertEqual(rel, "")
        self.assertEqual(self.db.get_tracker_dump_media(res["id"]),
                         {"notes": "my note", "screenshot_path": "media/user_row.png", "screenshot_path_2": ""})
        self.assertEqual(self.db.get_srpf_container_media(res["unassigned_identity"]),
                         {"notes": "container note", "screenshot_path": "media/user_container.png", "screenshot_path_2": ""})

    def test_08_notes_and_media_dialog_and_switch(self):
        """Phase 8: NotesAndMediaDialog replaces tab 3 with dedicated 2-pane modal and switch button."""
        from PySide6.QtWidgets import QTabWidget, QPushButton
        from ui.windows.tracker_dump_window import NotesAndMediaDialog, PayloadInspectorDialog, TrackerDumpWindow

        item = {
            "id": 1,
            "identity_key": "CLI-00001",
            "client_name": "Test Client",
            "pan": "ABCDE1234F",
            "portal": "Income Tax",
            "period_label": "AY 2026-27",
            "notes": "Some notes here",
            "screenshot_path": "media/test.png",
        }

        # 1. PayloadInspectorDialog only has 2 tabs (Overview and Raw JSON), Tab 3 is scrapped
        insp = PayloadInspectorDialog(item, db=self.db, is_container=True)
        tab_widget = insp.findChild(QTabWidget)
        self.assertIsNotNone(tab_widget)
        self.assertEqual(tab_widget.count(), 2, "Inspector should only have 2 tabs now")
        self.assertIn("Overview", tab_widget.tabText(0))
        self.assertIn("Raw JSON", tab_widget.tabText(1))

        # 2. NotesAndMediaDialog modal has notes editor, toolbar, and image preview
        dlg = NotesAndMediaDialog(item, db=self.db, is_container=True)
        self.assertIsNotNone(dlg.txt_notes)
        self.assertIsNotNone(dlg.lbl_image_preview)
        self.assertIsNotNone(dlg.btn_attach_img)
        self.assertIsNotNone(dlg.btn_paste_img)
        self.assertIsNotNone(dlg.btn_ocr_img)
        self.assertIsNotNone(dlg.btn_clear_img)
        self.assertIsNotNone(dlg.btn_expand_img)
        self.assertIsNotNone(dlg.media_tabs)
        self.assertEqual(dlg.media_tabs.count(), 2, "Notes & Media should offer two screenshot slots")
        self.assertIn("Screenshot 1", dlg.media_tabs.tabText(0))
        self.assertIn("Screenshot 2", dlg.media_tabs.tabText(1))

        # 3. TrackerDumpWindow action cell has act_notes button ("switch")
        win = TrackerDumpWindow.__new__(TrackerDumpWindow)
        win.table = None
        act_cell = win._build_action_cell(item, is_grouped=True)
        btn_notes = act_cell.findChild(QPushButton, "act_notes")
        self.assertIsNotNone(btn_notes, "Action cell MUST include act_notes button switch")

    def test_11_login_evidence_fills_second_slot_without_touching_first(self):
        """Login evidence goes to slot 2 only; slot 1 (filing evidence) and notes survive."""
        from PIL import Image
        from core.vsdc.vsdc247_evidence import attach_login_evidence

        source = os.path.join(self.tmp_dir.name, "gst_login_frame.png")
        Image.new("RGB", (64, 48), (250, 250, 250)).save(source, "PNG")

        key = "CLI-00042"
        self.db.save_srpf_container_media(key, "container note", "media/user_filing.png", client_id=42)

        rel = attach_login_evidence(self.db, source, client_id=42, username="ABCDE1234F")
        self.assertTrue(rel.startswith("media/vsdc247_login_"), rel)
        self.assertTrue(os.path.exists(os.path.join(self.tmp_dir.name, rel)))

        media = self.db.get_srpf_container_media(key)
        self.assertEqual(media["notes"], "container note")
        self.assertEqual(media["screenshot_path"], "media/user_filing.png")
        self.assertEqual(media["screenshot_path_2"], rel)

        # A hand-attached login screenshot is never replaced by a later auto frame.
        self.db.save_srpf_container_media(
            key, "container note", "media/user_filing.png", client_id=42,
            screenshot_path_2="media/user_login.png"
        )
        self.assertEqual(
            attach_login_evidence(self.db, source, client_id=42, username="ABCDE1234F"), ""
        )
        self.assertEqual(
            self.db.get_srpf_container_media(key)["screenshot_path_2"], "media/user_login.png"
        )

    def test_12_router_captures_login_frame_into_second_slot(self):
        """The router saves the OCR'd login frame to slot 2 for the matched client."""
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter

        img = Image.new("RGB", (80, 60), (245, 245, 245))
        router = object.__new__(VSDCRouter)
        router.db = self.db
        rel = router._capture_login_evidence(4242, "ABCDE1234F", img, client_id=42)

        self.assertTrue(rel.startswith("media/vsdc247_login_"), rel)
        media = self.db.get_srpf_container_media("CLI-00042")
        self.assertEqual(media["screenshot_path_2"], rel)
        self.assertEqual(media["screenshot_path"], "")


    def test_13_arn_capture_carries_login_frame_to_row_and_container(self):
        """The UID/login frame read before an ARN lands in slot 2 of the capture row AND the
        client container, beside the filing evidence in slot 1."""
        from PIL import Image
        from core.vsdc.vsdc247_evidence import (
            attach_capture_evidence, attach_login_evidence_to_capture,
        )

        filing = os.path.join(self.tmp_dir.name, "filing_frame.png")
        login = os.path.join(self.tmp_dir.name, "uid_frame.png")
        Image.new("RGB", (64, 48), (230, 230, 230)).save(filing, "PNG")
        Image.new("RGB", (64, 48), (250, 250, 250)).save(login, "PNG")

        res = self.db.insert_tracker_dump(
            pan="PAN247003",
            portal="GST Portal",
            period_label="September - 2026-27",
            arn_number="AA270826009900Z",
            capture_method="VSDC247_gstr1_ack",
            status="Filed",
            raw_payload_json=json.dumps({"pan": "PAN247003", "arn": "AA270826009900Z"}),
            dataset_key="GST:PAN247003:September - 2026-27:GSTR-1:0",
        )

        filing_rel = attach_capture_evidence(
            self.db, filing, res["id"],
            client_id=res.get("client_id"),
            unassigned_identity=res.get("unassigned_identity"),
            arn="AA270826009900Z",
        )
        login_rel = attach_login_evidence_to_capture(
            self.db, login, res["id"],
            client_id=res.get("client_id"),
            unassigned_identity=res.get("unassigned_identity"),
            username="UIDPANEL01",
        )

        self.assertTrue(login_rel.startswith("media/vsdc247_login_"), login_rel)
        self.assertTrue(os.path.exists(os.path.join(self.tmp_dir.name, login_rel)))

        row = self.db.get_tracker_dump_media(res["id"])
        self.assertEqual(row["screenshot_path"], filing_rel)
        self.assertEqual(row["screenshot_path_2"], login_rel, "capture row must show the UID frame")

        container = self.db.get_srpf_container_media(res["unassigned_identity"])
        self.assertEqual(container["screenshot_path"], filing_rel)
        self.assertEqual(container["screenshot_path_2"], login_rel)

        # A second ARN in the same login session reuses the stored auto frame (no duplicate file).
        self.assertEqual(
            attach_login_evidence_to_capture(
                self.db, login, res["id"],
                client_id=res.get("client_id"),
                unassigned_identity=res.get("unassigned_identity"),
                username="UIDPANEL01",
            ),
            login_rel,
        )

    def test_14_login_frame_never_replaces_hand_attached_slots(self):
        """Hand-attached screenshots in either second slot are never replaced."""
        from PIL import Image
        from core.vsdc.vsdc247_evidence import attach_login_evidence_to_capture

        login = os.path.join(self.tmp_dir.name, "uid_frame.png")
        Image.new("RGB", (64, 48), (250, 250, 250)).save(login, "PNG")

        res = self.db.insert_tracker_dump(
            pan="PAN247004",
            portal="GST Portal",
            period_label="September - 2026-27",
            arn_number="AA270826009901Z",
            capture_method="VSDC247_gstr1_ack",
            status="Filed",
            raw_payload_json=json.dumps({"pan": "PAN247004"}),
            dataset_key="GST:PAN247004:September - 2026-27:GSTR-1:0",
        )
        self.db.save_tracker_dump_media(
            res["id"], "row note", "media/user_row.png",
            screenshot_path_2="media/user_row_login.png",
        )
        self.db.save_srpf_container_media(
            res["unassigned_identity"], "container note", "media/user_container.png",
            screenshot_path_2="media/user_container_login.png",
        )

        self.assertEqual(
            attach_login_evidence_to_capture(
                self.db, login, res["id"],
                client_id=res.get("client_id"),
                unassigned_identity=res.get("unassigned_identity"),
                username="UIDPANEL02",
            ),
            "",
        )
        self.assertEqual(
            self.db.get_tracker_dump_media(res["id"])["screenshot_path_2"],
            "media/user_row_login.png",
        )
        self.assertEqual(
            self.db.get_srpf_container_media(res["unassigned_identity"])["screenshot_path_2"],
            "media/user_container_login.png",
        )

    def test_15_router_carries_pending_login_frame_onto_arn_payload(self):
        """The router keeps the login frame after the login page and hands it to the ARN payload."""
        from core.vsdc.vsdc_router import VSDCRouter

        login = os.path.join(self.tmp_dir.name, "uid_frame.png")
        with open(login, "wb") as fh:
            fh.write(b"png")

        router = object.__new__(VSDCRouter)
        router._login_frames = {
            4242: {"username": "UIDPANEL03", "path": login, "saved_at": time.time()},
        }

        payload = {"portal": "GST Portal", "arn": "AA270826009902Z"}
        router._attach_pending_login_evidence(payload, 4242)
        self.assertEqual(payload["_login_evidence_screenshot"], login)
        self.assertEqual(payload["_login_evidence_username"], "UIDPANEL03")

        # Non-GST captures never receive the GST login frame.
        other = {"portal": "Income Tax", "arn": "901036690231026"}
        router._attach_pending_login_evidence(other, 4242)
        self.assertNotIn("_login_evidence_screenshot", other)

        # An expired frame is not used.
        router._login_frames[4242]["saved_at"] = time.time() - router._LOGIN_FRAME_TTL_SEC - 1
        stale = {"portal": "GST Portal", "arn": "AA270826009903Z"}
        router._attach_pending_login_evidence(stale, 4242)
        self.assertNotIn("_login_evidence_screenshot", stale)


    def test_16_router_carries_itr_login_frame_onto_itr_payload(self):
        """The router keeps the ITR login frame (PAN) and hands it to ITR captures, not GST."""
        from core.vsdc.vsdc_router import VSDCRouter

        login = os.path.join(self.tmp_dir.name, "itr_uid_frame.png")
        with open(login, "wb") as fh:
            fh.write(b"png")

        router = object.__new__(VSDCRouter)
        router._login_frames = {
            9999: {"username": "ABCDE1234F", "path": login, "saved_at": time.time(), "portal": "Income Tax"},
        }

        # ITR capture receives the ITR login frame
        payload = {"portal": "Income Tax", "arn": "901036690231026"}
        router._attach_pending_login_evidence(payload, 9999)
        self.assertEqual(payload["_login_evidence_screenshot"], login)
        self.assertEqual(payload["_login_evidence_username"], "ABCDE1234F")

        # GST captures never receive the ITR login frame
        other = {"portal": "GST Portal", "arn": "AA270826009904Z"}
        router._attach_pending_login_evidence(other, 9999)
        self.assertNotIn("_login_evidence_screenshot", other)


if __name__ == "__main__":
    unittest.main()
