"""Counts lines of code across the repo, broken down by file extension."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
    "env", "build", "dist", "artifacts", "installer_output",
    "package_build", "package_dist", ".idea", ".vscode", "site-packages",
    ".restore_points", "source_2",
}

EXCLUDE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz", ".exe", ".dll", ".pyc",
    ".pyd", ".so", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".avi", ".mov", ".db", ".sqlite", ".sqlite3", ".lock", ".txt",
    ".md", ".json", ".html", ".css",
}


def is_binary(path, sample_size=1024):
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
    except OSError:
        return True
    return b"\0" in chunk


def main():
    counts = {}
    total_lines = 0
    total_files = 0

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".git")]

        for filename in filenames:
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

    print(f"Repo: {ROOT}\n")
    print(f"{'Extension':<20}{'Lines':>12}")
    print("-" * 32)
    for ext, lines in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{ext:<20}{lines:>12,}")
    print("-" * 32)
    print(f"{'TOTAL':<20}{total_lines:>12,}")
    print(f"\nFiles scanned: {total_files:,}")


if __name__ == "__main__":
    sys.exit(main())
