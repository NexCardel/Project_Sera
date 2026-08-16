import unittest
import os
import re
import sys
from PySide6.QtWidgets import QApplication

class TestQtAwesomeIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_all_mdi_icons_are_valid(self):
        import qtawesome as qta
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        mdi_icons = set()
        for root, dirs, files in os.walk(root_dir):
            if any(p in root for p in [".git", "__pycache__", "venv", ".gemini", "installer_output", "build_tools"]):
                continue
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
                            content = file_obj.read()
                        matches = re.findall(r'[\"\']mdi\.([a-zA-Z0-9_-]+)[\"\']', content)
                        for m in matches:
                            mdi_icons.add((m, os.path.relpath(path, root_dir)))
                    except Exception:
                        pass

        invalid_icons = []
        for icon_name, file_rel in sorted(mdi_icons):
            try:
                icon = qta.icon("mdi." + icon_name)
                if icon.isNull():
                    invalid_icons.append((icon_name, file_rel, "Icon returned null"))
            except Exception as e:
                invalid_icons.append((icon_name, file_rel, str(e)))

        if invalid_icons:
            msg = "\n".join([f"mdi.{name} in {f}: {err}" for name, f, err in invalid_icons])
            self.fail(f"Found invalid mdi icons:\n{msg}")

if __name__ == "__main__":
    unittest.main()
