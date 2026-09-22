"""
Memory fixes from the 2026-09-21 measurement, locked in:

  * only the Material Design icon font is loaded (~15 MB) - so no code may ask for an icon from
    another font, or that icon would come out blank;
  * numpy's OpenBLAS runs one thread (~225 MB of private commit) - set before anything imports
    numpy;
  * sdc_parser no longer drags pandas + openpyxl (~60 MB) into the app for the live CSV feed.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ui.utils.icon_fonts import USED_ICON_PREFIXES, restrict_icon_fonts

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"venv", ".build_venv", "package_build", "package_dist", "source_2", ".restore_points",
             "node_modules", ".git", "__pycache__", "tests"}
# Every prefix qtawesome ships. A string literal starting with one of these and a dot is an icon name.
QTA_PREFIXES = ("fa5", "fa5s", "fa5b", "fa6", "fa6s", "fa6b", "ei", "mdi", "mdi6", "ph", "ri", "msc")
ICON_LITERAL = re.compile(r"""["'](%s)\.[a-z0-9][a-z0-9-]*["']""" % "|".join(QTA_PREFIXES))


def _source_files():
    for p in ROOT.rglob("*.py"):
        if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
            yield p


def test_every_icon_the_code_asks_for_comes_from_a_loaded_font():
    offenders = []
    for p in _source_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in ICON_LITERAL.finditer(text):
            if m.group(1) not in USED_ICON_PREFIXES:
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{p.relative_to(ROOT)}:{line} {m.group(0)}")
    assert not offenders, ("These icons use a font that is no longer loaded - add its prefix to "
                           "ui/utils/icon_fonts.py USED_ICON_PREFIXES:\n" + "\n".join(offenders))


def test_restricted_fonts_still_draw_the_apps_icons():
    code = (
        "from ui.utils.icon_fonts import restrict_icon_fonts\n"
        "assert restrict_icon_fonts()\n"
        "import qtawesome\n"
        "assert {f[0] for f in qtawesome._BUNDLED_FONTS} == {'mdi'}\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "icon = qtawesome.icon('mdi.check-decagram', color='#39FF14')\n"
        "assert not icon.pixmap(16, 16).isNull()\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("ok"), out.stderr[-600:]


def test_openblas_is_limited_before_anything_is_imported():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    first_import = next(n.lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                        and not (isinstance(n, ast.Import) and n.names[0].name in ("sys", "os", "socket", "json",
                                                                                    "queue", "threading"))
                        and not (isinstance(n, ast.ImportFrom) and n.module in ("pathlib", "datetime")))
    src = (ROOT / "main.py").read_text(encoding="utf-8").splitlines()
    setting = next(i + 1 for i, l in enumerate(src) if 'setdefault("OPENBLAS_NUM_THREADS", "1")' in l)
    assert setting < first_import


def _fresh(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "SDC_Parser", capture_output=True,
                         text=True, timeout=120)
    return out.stdout + out.stderr


def test_importing_sdc_parser_does_not_load_pandas_or_openpyxl():
    out = _fresh("import sys; sys.path.insert(0, '..'); import sdc_parser; "
                 "print('HEAVY', 'pandas' in sys.modules, 'openpyxl' in sys.modules)")
    assert "HEAVY False False" in out, out[-600:]


def test_live_feed_is_written_without_pandas(tmp_path):
    """The feed's CSV logic on fixed rows: columns, empty values, line breaks, defaulters filter."""
    (tmp_path / "Live_Tracking_Table_Live.xlsx").write_bytes(b"")        # skip the workbook step
    code = f"""
import sys, csv
sys.path.insert(0, '..')
import sdc_parser
rows = [
    {{"Client": "ASHOK SEN", "Submit Status": "Not submitted", "Compliance Alert": "", "Discrepancy Note": "",
      "Note": "line one\\nline two"}},
    {{"Client": "MEERA DAS", "Submit Status": "Submitted & E-verified", "Compliance Alert": "Overdue",
      "Discrepancy Note": "", "Note": None}},
    {{"Client": "RAVI MEHTA", "Submit Status": "Submitted", "Compliance Alert": "due soon", "Discrepancy Note": "",
      "Note": "nan", "Extra": 5}},
]
sdc_parser.get_ltt_dataset = lambda: (rows, {{}})
master, _ = sdc_parser.export_ltt_live_feed(output_dir=r"{tmp_path}")
with open(master, encoding="utf-8-sig", newline="") as f:
    got = list(csv.DictReader(f))
with open(r"{tmp_path}" + "/LTT_Defaulters_Feed.csv", encoding="utf-8-sig", newline="") as f:
    act = [r["Client"] for r in csv.DictReader(f)]
print("COLS", list(got[0].keys()))
print("NOTES", [r["Note"] for r in got], [r["Extra"] for r in got])
print("ACTION", act)
print("HEAVY", "pandas" in sys.modules)
"""
    out = _fresh(code)
    assert "COLS ['Client', 'Submit Status', 'Compliance Alert', 'Discrepancy Note', 'Note', 'Extra']" in out, out[-800:]
    assert "NOTES ['line one ; line two', '', ''] ['', '', '5']" in out, out[-800:]
    assert "ACTION ['ASHOK SEN', 'RAVI MEHTA']" in out, out[-800:]          # E-verified is excluded
    assert "HEAVY False" in out


def test_trim_releases_memory_and_logs_it(tmp_path, monkeypatch):
    from core import memlog
    monkeypatch.setattr(memlog, "_log_path", lambda: tmp_path / "memory.log")
    blob = bytearray(40 * 1024 * 1024)          # touch 40 MB, then let it go idle
    for i in range(0, len(blob), 4096):
        blob[i] = 1
    released = memlog.trim_working_set("test")
    assert released is not None and released > 10
    assert "trim (test): released" in (tmp_path / "memory.log").read_text(encoding="utf-8")
    del blob


def test_periodic_sample_trims_only_above_the_ceiling(tmp_path, monkeypatch):
    from core import memlog
    monkeypatch.setattr(memlog, "_log_path", lambda: tmp_path / "memory.log")
    calls = []
    monkeypatch.setattr(memlog, "trim_working_set", lambda reason: calls.append(reason))
    monkeypatch.setattr(memlog, "snapshot", lambda: (55.0, 130.0, 190.0))    # settled after a trim
    memlog.sample_and_maybe_trim("sample")
    assert calls == []
    monkeypatch.setattr(memlog, "snapshot", lambda: (memlog.TRIM_CEILING_MB + 30, 150.0, 190.0))
    memlog.sample_and_maybe_trim("sample")
    assert len(calls) == 1 and "above" in calls[0]


def test_memory_log(tmp_path, monkeypatch):
    from core import memlog
    monkeypatch.setattr(memlog, "_log_path", lambda: tmp_path / "logs" / "memory.log")
    memlog.mark("test stage one")
    memlog.mark("test stage two")
    lines = (tmp_path / "logs" / "memory.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0].endswith("test stage one")
    assert re.search(r"ws +\d+\.\d MB +\([+-]\d+\.\d\)  priv +\d+\.\d MB", lines[1])
    ws, priv, peak = memlog.snapshot()
    assert ws > 5 and priv > 5 and peak >= ws
