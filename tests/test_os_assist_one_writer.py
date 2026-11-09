"""Blueprint rule 2: only core/os_assist/fill.py (and the spike) may write into a page."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", ".build_venv", "backups", ".restore_points", "build", "dist", "tests",
             ".git", "__pycache__", "node_modules",
             # packaging output: bundled libraries (comtypes' generated wrappers call SetValue)
             "package_build", "package_dist", "installer_output", "tmp_pytest", "temp_cache"}
ALLOWED = (Path("core/os_assist/fill.py"), Path("tools/pre_dev/os_assist"))
FORBIDDEN = ("SetValue(", "SendInput", ".SetFocus(")


def _allowed(rel: Path) -> bool:
    return any(rel == a or a in rel.parents for a in ALLOWED)


def test_only_fill_py_writes():
    hits = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS or part.startswith("venv") for part in rel.parts[:-1]) or _allowed(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for word in FORBIDDEN:
            if word in text:
                hits.append(f"{rel.as_posix()}: {word}")
    assert not hits, "writer calls outside core/os_assist/fill.py:\n" + "\n".join(hits)


# Rule 1: OS Assist never clicks, presses Enter/Tab, submits or dismisses anything - fill.py included.
NEVER = (".Invoke(", ".Toggle(", ".Select(", ".DoDefaultAction(", "mouse_event", "keybd_event",
         "PostMessage", "SendMessage", "VK_RETURN", "VK_TAB", "VK_ESCAPE")


def test_os_assist_never_clicks_or_presses_keys():
    hits = []
    for folder in ("core/os_assist", "ui/os_assist"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits += [f"{path.relative_to(ROOT).as_posix()}: {w}" for w in NEVER if w in text]
    assert not hits, "click / key-press calls in OS Assist:\n" + "\n".join(hits)
