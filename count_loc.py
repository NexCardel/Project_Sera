"""Counts lines of code across the repo, broken down by file extension."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
    ".build_venv", "env", "build", "dist", "artifacts", "installer_output",
    "package_build", "package_dist", ".idea", ".vscode", "site-packages",
    ".restore_points", "source_2", "tests", "tools", "SUDR", "backups",
    "scratch", "build_tools", ".claude", ".pytest_cache", ".ruff_cache",
    "package_assets", "data", "temp_cache", "docs", "Mockups",
    "tracker_dump_parser",
}

EXCLUDE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz", ".exe", ".dll", ".pyc",
    ".pyd", ".so", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".avi", ".mov", ".db", ".sqlite", ".sqlite3", ".lock", ".txt",
    ".md", ".json", ".html", ".css",
}


EXCLUDE_FILES = {"count_loc.py"}


def is_binary(path, sample_size=1024):
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
    except OSError:
        return True
    return b"\0" in chunk


def count_file(filepath):
    total = blank = comment = code = 0
    in_docstring = False
    doc_delim = ""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            total += 1
            s = line.strip()
            if not s:
                blank += 1
                continue
            if in_docstring:
                comment += 1
                if doc_delim in s:
                    in_docstring = False
                continue
            if s.startswith("#"):
                comment += 1
                continue
            if s.startswith('"""') or s.startswith("'''"):
                comment += 1
                delim = s[:3]
                if delim not in s[3:]:
                    in_docstring = True
                    doc_delim = delim
                continue
            code += 1
    return total, code, comment, blank


def main():
    show_py_breakdown = "--python" in sys.argv or "--py" in sys.argv or "--live" in sys.argv
    counts = {}
    total_lines = 0
    total_files = 0

    py_subsystems = {}

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".git")]

        for filename in filenames:
            if filename in EXCLUDE_FILES:
                continue

            ext = os.path.splitext(filename)[1].lower()
            if ext in EXCLUDE_EXTS:
                continue

            full_path = os.path.join(dirpath, filename)

            if is_binary(full_path):
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    line_count = sum(1 for _ in f)
            except OSError:
                continue

            key = ext if ext else "(no extension)"
            counts[key] = counts.get(key, 0) + line_count
            total_lines += line_count
            total_files += 1

            if ext == ".py":
                rel = os.path.relpath(full_path, ROOT).replace("\\", "/")
                parts = rel.split("/")
                if len(parts) == 1:
                    grp = "App Root (main, database, sync, etc.)"
                elif parts[0] == "core":
                    sub = parts[1] if len(parts) > 2 else "core (base)"
                    grp = f"core/{sub}"
                elif parts[0] == "ui":
                    sub = parts[1] if len(parts) > 2 else "ui (root)"
                    grp = f"ui/{sub}"
                else:
                    grp = parts[0]

                t, c, cm, b = count_file(full_path)
                if grp not in py_subsystems:
                    py_subsystems[grp] = {"files": 0, "total": 0, "code": 0, "comment": 0, "blank": 0}
                py_subsystems[grp]["files"] += 1
                py_subsystems[grp]["total"] += t
                py_subsystems[grp]["code"] += c
                py_subsystems[grp]["comment"] += cm
                py_subsystems[grp]["blank"] += b

    print(f"Repo: {ROOT}\n")
    print(f"{'Extension':<20}{'Lines':>12}")
    print("-" * 32)
    for ext, lines in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{ext:<20}{lines:>12,}")
    print("-" * 32)
    print(f"{'TOTAL':<20}{total_lines:>12,}")
    print(f"\nFiles scanned: {total_files:,}")

    if show_py_breakdown or True:
        print("\n" + "=" * 80)
        print("LIVE RUNNING APP - PYTHON BREAKDOWN")
        print("=" * 80)
        print(f"{'Subsystem / Module':<38} | {'Files':>5} | {'Code':>7} | {'Comment':>7} | {'Blank':>6} | {'Total':>7}")
        print("-" * 80)
        p_tot = {"files": 0, "total": 0, "code": 0, "comment": 0, "blank": 0}
        for grp, s in sorted(py_subsystems.items(), key=lambda x: x[1]["total"], reverse=True):
            print(f"{grp:<38} | {s['files']:>5} | {s['code']:>7,} | {s['comment']:>7,} | {s['blank']:>6,} | {s['total']:>7,}")
            for k in p_tot:
                p_tot[k] += s[k]
        print("-" * 80)
        print(f"{'TOTAL LIVE PYTHON APP':<38} | {p_tot['files']:>5} | {p_tot['code']:>7,} | {p_tot['comment']:>7,} | {p_tot['blank']:>6,} | {p_tot['total']:>7,}")
        print("=" * 80)


if __name__ == "__main__":
    sys.exit(main())
