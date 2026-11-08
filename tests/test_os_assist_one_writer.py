"""Blueprint rule 2: only core/os_assist/fill.py (and the spike) may write into a page."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", ".build_venv", "backups", ".restore_points", "build", "dist", "tests",
             ".git", "__pycache__", "node_modules"}
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
