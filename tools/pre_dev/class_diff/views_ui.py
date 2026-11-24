"""
tools/pre_dev/class_diff/views_ui.py - PRE-DEV: one small window for the seven HTML views
=========================================================================================
Pick a capture or comp page from output/ (newest first), tick the views, press Make views. Each
view runs its own tool (render.py, shape_map.py, tables.py) as a child process, exactly the command
you would type, and the pages it writes open in the default browser. The log shows the tools' console
lines, which hold counts only.

  render                render.py FILE
  outline              render.py FILE --outline
  shape map             shape_map.py FILE --no-match
  card match            shape_map.py FILE   (the shape map plus card_match.py's percentages and structure;
                                             with both ticked only this one runs: same page)
  tables                tables.py CAPTURE --html   (a comp page is mapped to its capture_ file: same stamp)
  page map              comp_map.py FILE   (the comp page over time, for a non-technical reader: everything
                                            it showed, a slider to any moment, what happened in plain words;
                                            comp_ and capture_ files only)
  changelog             changelog.py FILE   (every read of the capture stored as only what changed: the replay
                                            check SAME / NOT THE SAME, the sizes, and per read what changed -
                                            texts, new, gone, moved; a capture_ / comp_ file shows its own page
                                            from the capture's changelog_<start>.jsonl; Browse... also takes a
                                            changelog_ file or any .jsonl of full reads, e.g. a recorder day)

Both shape map views find the record sets from the boxes (lined up, and matching inside). "match inside"
is that percentage: it goes to shape_map.py as --match-min when it is not shape_map.MATCH_MIN. Lower it
and unlike boxes become one set; raise it and cards that differ a little fall apart. The page says how
many pairs lined up, how many matched and the highest score that was turned down.

New capture runs key_probe.py in its own console window (switch to the browser within 8 s); the list
refreshes when it ends. Clear all captures sends every capture, comp page and view page in output/ to the
Recycle Bin after a yes (CLEAR_PREFIXES; other files and sub-folders stay). The groups and arrangement
views left the window on 2026-11-21 (card match replaced them); render.py --groups / --arrange still work. Launch with views.bat (tools/pre_dev/class_diff/views.bat).
"""

import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE / "output"

# (label, script, extra args, needs a capture/read rather than a comp page)
VIEWS = [
    ("render", "render.py", [], False),
    ("outline (container shapes)", "render.py", ["--outline"], False),
    ("shape map (record sets)", "shape_map.py", ["--no-match"], False),
    ("card match (shape map + %)", "shape_map.py", [], False),
    ("tables (tables.py)", "tables.py", ["--html"], True),
    ("page map (changes over time)", "comp_map.py", [], False),
    ("changelog (what changed per read)", "changelog.py", [], False),
]
SHAPE_MAP, CARD_MATCH, PAGE_MAP = "shape map (record sets)", "card match (shape map + %)", "page map (changes over time)"
CHANGELOG = "changelog (what changed per read)"
DEFAULT_TICKS = ("outline (container shapes)", CARD_MATCH, PAGE_MAP, CHANGELOG)
MATCH_MIN = 50.0        # shape_map.MATCH_MIN (a test keeps the two equal; this window imports no tool)


def match_min_args(text) -> list:
    """The --match-min arguments for the "match inside" box: none for the default, an empty box or
    anything that is not a percentage from 0 to 100."""
    try:
        v = float(str(text).strip())
    except ValueError:
        return []
    if not 0 <= v <= 100 or v == MATCH_MIN:
        return []
    return ["--match-min", f"{v:g}"]


def picked(views):
    """Card match writes the same shape_map_<file>.html page with the match added: when both are ticked,
    run only card match (one page, one run)."""
    names = {v[0] for v in views}
    return [v for v in views if not (v[0] == SHAPE_MAP and CARD_MATCH in names)]


# Clear all captures: the captures and comp pages, and every page or table the tools made from them.
# Files with other names in output/ and any sub-folder stay.
CLEAR_PREFIXES = ("capture_", "comp_", "key_probe_", "record_", "render_", "shape_map_", "tables_", "groups_",
                  "arrange_", "compare_", "cards_", "map_", "reads_", "changelog_")


# Of those, the captures themselves (the rest are pages made from them).
CAPTURE_FILES = ("capture_", "comp_", "key_probe_", "record_", "reads_", "changelog_")


def clearable(folder: Path = None):
    """The files Clear all captures takes: top-level files of output/ with a capture or view prefix."""
    folder = folder or OUT
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name.startswith(CLEAR_PREFIXES))


def to_recycle_bin(paths) -> bool:
    """Send files to the Windows Recycle Bin in one shell call (undoable). True when the shell reports success."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    paths = [str(Path(p).resolve()) for p in paths]
    if not paths:
        return True
    op = SHFILEOPSTRUCTW(None, FO_DELETE, "\0".join(paths) + "\0\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc == 0 and not op.fAnyOperationsAborted


WROTE = re.compile(r"^(?:->|HTML:)\s*(.+\.html)\s*$")


def child_python() -> str:
    """The console python beside this interpreter (pythonw has no stdout for the children to share)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def inputs():
    """comp_ and capture_ files in output/, newest first."""
    files = [p for p in OUT.glob("*.json") if p.name.startswith(("comp_", "capture_", "key_probe_"))]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def capture_of(path: Path) -> Path:
    """tables.py reads raw docs: a comp page's own capture (same stamp and page), else the file itself."""
    if path.name.startswith("comp_"):
        cap = path.with_name("capture_" + path.name[len("comp_"):])
        if cap.exists():
            return cap
    return path


def command(view, path: Path, match_min=None):
    """The child command of one view. match_min (the "match inside" box) goes to shape_map.py only."""
    _, script, extra, raw = view
    src = capture_of(path) if raw else path
    more = match_min_args(match_min) if script == "shape_map.py" and match_min is not None else []
    return [child_python(), str(HERE / script), str(src)] + extra + more


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("SDIS pre-dev views")
        root.geometry("900x620")
        root.minsize(640, 420)
        self.files = []
        self.busy = False

        top = ttk.Frame(root, padding=8)
        top.pack(fill="both", expand=True)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        top.rowconfigure(4, weight=1)

        bar = ttk.Frame(top)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="Captures and comp pages in output/ (newest first)").pack(side="left")
        ttk.Button(bar, text="Open output folder", command=lambda: os.startfile(OUT)).pack(side="right")
        ttk.Button(bar, text="Clear all captures", command=self.clear_all).pack(side="right", padx=(0, 4))
        ttk.Button(bar, text="Browse...", command=self.browse).pack(side="right", padx=4)
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="right")
        ttk.Button(bar, text="New capture", command=self.capture).pack(side="right", padx=4)

        lf = ttk.Frame(top)
        lf.grid(row=1, column=0, sticky="nsew", pady=(4, 8))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.list = tk.Listbox(lf, activestyle="dotbox", exportselection=False, font=("Consolas", 10))
        self.list.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.list.configure(yscrollcommand=sb.set)
        self.list.bind("<Double-Button-1>", lambda e: self.run())

        vf = ttk.LabelFrame(top, text="Views", padding=6)
        vf.grid(row=2, column=0, sticky="ew")
        self.ticks = []
        for i, v in enumerate(VIEWS):
            var = tk.BooleanVar(value=v[0] in DEFAULT_TICKS)
            ttk.Checkbutton(vf, text=v[0], variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 18))
            self.ticks.append(var)
        last = (len(VIEWS) + 2) // 3
        mf = ttk.Frame(vf)
        mf.grid(row=last, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(mf, text="Record sets from boxes: lined-up boxes must match inside").pack(side="left")
        self.match_var = tk.StringVar(value=f"{MATCH_MIN:g}")
        ttk.Spinbox(mf, from_=0, to=100, increment=5, width=5, textvariable=self.match_var).pack(side="left", padx=4)
        ttk.Label(mf, text=f"%  (default {MATCH_MIN:g}; shape map and card match views)").pack(side="left")
        self.open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(vf, text="open pages when done", variable=self.open_var).grid(row=last + 1, column=0, sticky="w", pady=(6, 0))
        btns = ttk.Frame(vf)
        btns.grid(row=last + 1, column=1, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="All", command=lambda: [t.set(True) for t in self.ticks]).pack(side="left")
        ttk.Button(btns, text="None", command=lambda: [t.set(False) for t in self.ticks]).pack(side="left", padx=4)
        self.go = ttk.Button(btns, text="Make views", command=self.run)
        self.go.pack(side="left", padx=(12, 0))

        ttk.Label(top, text="Log (the tools print counts only)").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.log = tk.Text(top, height=10, font=("Consolas", 9), wrap="none", state="disabled")
        self.log.grid(row=4, column=0, sticky="nsew")
        self.refresh()

    # ── list ─────────────────────────────────────────────────────────────────
    def refresh(self, keep=None):
        self.files = inputs()
        if keep and keep not in self.files:
            self.files.insert(0, keep)
        self.list.delete(0, "end")
        for p in self.files:
            self.list.insert("end", p.name)
        if self.files:
            self.list.selection_set(0)

    def browse(self):
        f = filedialog.askopenfilename(initialdir=OUT, title="A capture, comp page, read or record file",
                                       filetypes=[("UIA reads", "*.json *.jsonl"), ("All files", "*.*")])
        if f:
            self.refresh(keep=Path(f))
            self.list.selection_clear(0, "end")
            self.list.selection_set(self.files.index(Path(f)))

    def selected(self):
        sel = self.list.curselection()
        return self.files[sel[0]] if sel else None

    # ── running ──────────────────────────────────────────────────────────────
    def say(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run(self):
        path = self.selected()
        views = picked([v for v, t in zip(VIEWS, self.ticks) if t.get()])
        if self.busy or not path or not views:
            if not path:
                self.say("pick a file first")
            elif not views:
                self.say("tick at least one view")
            return
        self.busy = True
        self.go.configure(state="disabled")
        threading.Thread(target=self._work, args=(path, views, self.open_var.get(), self.match_var.get()),
                         daemon=True).start()

    def _work(self, path, views, open_pages, match_min=None):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self.root.after(0, self.say, f"== {path.name}")
        for v in views:
            cmd = command(v, path, match_min)
            self.root.after(0, self.say, f"-- {v[0]}" + "".join(f" {x}" for x in cmd[3 + len(v[2]):]))
            try:
                r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as e:
                self.root.after(0, self.say, f"   could not start: {e}")
                continue
            for line in (r.stdout + r.stderr).splitlines():
                self.root.after(0, self.say, "   " + line)
                m = WROTE.match(line.strip())
                if m and r.returncode == 0 and open_pages:
                    page = Path(m.group(1))
                    page = page if page.is_absolute() else REPO / page
                    webbrowser.open(page.resolve().as_uri())
            if r.returncode != 0:
                self.root.after(0, self.say, f"   failed (exit {r.returncode})")
        self.root.after(0, self._done)

    def _done(self):
        self.busy = False
        self.go.configure(state="normal")
        self.say("done")

    def clear_all(self):
        """Every capture and comp page in output/ and the views made from them, to the Recycle Bin, after a yes."""
        from tkinter import messagebox
        if self.busy:
            self.say("wait until the views are made")
            return
        files = clearable()
        if not files:
            self.say("nothing to clear: output/ holds no captures or views")
            return
        mb = sum(p.stat().st_size for p in files) / 1e6
        caps = sum(p.name.startswith(CAPTURE_FILES) and not p.name.startswith("changelog_view_") for p in files)
        if not messagebox.askyesno(
                "Clear all captures",
                f"Send {len(files)} files ({mb:.1f} MB) in output/ to the Recycle Bin?\n\n"
                f"{caps} capture / comp files and {len(files) - caps} pages made from them.\n"
                "They can be restored from the Recycle Bin.", icon="warning", default="no", parent=self.root):
            self.say("clear cancelled")
            return
        ok = to_recycle_bin(files)
        left = [p for p in files if p.exists()]
        self.say(f"cleared {len(files) - len(left)} of {len(files)} files to the Recycle Bin"
                 + ("" if ok and not left else f" ({len(left)} could not be moved: open in another program?)"))
        self.refresh()

    def capture(self):
        """key_probe.py in its own console: the user switches to the browser while it counts down."""
        cmd = [child_python(), str(HERE / "key_probe.py")]
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        p = subprocess.Popen(cmd, cwd=REPO, creationflags=flags, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.say("capture started in its own window: switch to the browser within 8 s")

        def wait():
            p.wait()
            self.root.after(0, self.say, f"capture ended (exit {p.returncode})")
            self.root.after(0, self.refresh)
        threading.Thread(target=wait, daemon=True).start()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
