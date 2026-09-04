"""
test_updater.py
---------------
Unit tests for version comparison and update metadata.
"""

import unittest
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch
import version


class TestUpdater(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(version.parse_version("2.3.0"), (2, 3, 0))
        self.assertEqual(version.parse_version("v2.3.1"), (2, 3, 1))
        self.assertEqual(version.parse_version("3.0.0.1"), (3, 0, 0, 1))

    def test_is_update_available(self):
        self.assertTrue(version.is_update_available("2.3.1", "2.3.0"))
        self.assertTrue(version.is_update_available("3.0.0", "2.3.0"))
        self.assertFalse(version.is_update_available("2.3.0", "2.3.0"))
        self.assertFalse(version.is_update_available("2.2.9", "2.3.0"))

    def test_current_mandatory_metadata_does_not_force_self_update(self):
        """The mandatory flag only applies when the published version is newer."""
        self.assertFalse(version.is_update_available("2.3.0", version.APP_VERSION))

    def test_missing_remote_metadata_is_silent_no_update(self):
        error = urllib.error.HTTPError(version.VERSION_URL, 404, "Not Found", None, None)
        with patch("version.urllib.request.urlopen", side_effect=error), patch("builtins.print") as printed:
            self.assertIsNone(version.check_for_updates())
        printed.assert_not_called()

    def test_version_json_format(self):
        v_file = Path(__file__).resolve().parent.parent / "version.json"
        self.assertTrue(v_file.exists(), "version.json must exist in root")
        
        with open(v_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        self.assertIn("version", data)
        self.assertIn("min_required_version", data)
        self.assertIn("mandatory", data)
        self.assertIn("download_url", data)

    def test_apply_and_restart_silent_script_generation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            installer_path = Path(tmpdir) / "Amas_Sera_Setup_v2.3.1.exe"
            installer_path.write_bytes(b"dummy")
            target_exe = Path(tmpdir) / "Amas_Sera.exe"
            target_exe.write_bytes(b"dummy")

            with patch("subprocess.Popen") as mock_popen, patch("sys.exit") as mock_exit:
                version.apply_and_restart(installer_path, silent=True, target_exe=str(target_exe.resolve()))
                
                bat_file = Path(tmpdir) / "run_installer.bat"
                self.assertTrue(bat_file.exists())
                content = bat_file.read_text(encoding="utf-8")
                self.assertIn("/VERYSILENT", content)
                self.assertIn("/SUPPRESSMSGBOXES", content)
                self.assertIn("/NORESTART", content)
                self.assertIn(str(installer_path.resolve()), content)
                self.assertIn(str(target_exe.resolve()), content)
                self.assertTrue(mock_popen.called)
                self.assertTrue(mock_exit.called)

    def test_background_update_manager(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_installer = Path(tmpdir) / "downloaded_setup.exe"
            mock_installer.write_bytes(b"mock installer binary")

            mock_update_info = {
                "latest_version": "2.3.1",
                "current_version": "2.3.0",
                "download_url": "https://github.com/dummy/release.exe",
                "mandatory": True,
            }

            found_events = []
            ready_events = []

            with patch("version.check_for_updates", return_value=mock_update_info), \
                 patch("version.download_update_payload", return_value=mock_installer):
                
                mgr = version.BackgroundUpdateManager(
                    check_interval_seconds=3600,
                    on_update_found=lambda info: found_events.append(info),
                    on_update_ready=lambda path, info: ready_events.append((path, info))
                )

                # Trigger synchronous execution of one check cycle
                mgr._check_and_download()

                self.assertEqual(len(found_events), 1)
                self.assertEqual(found_events[0]["latest_version"], "2.3.1")
                self.assertEqual(len(ready_events), 1)
                self.assertEqual(ready_events[0][0], mock_installer)
                self.assertEqual(ready_events[0][1]["latest_version"], "2.3.1")
                self.assertEqual(mgr.downloaded_payload, mock_installer)


if __name__ == "__main__":
    unittest.main()
