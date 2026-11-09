"""
tools/pre_dev/class_diff/views_ui.py - PRE-DEV: one small window for the six HTML views
======================================================================================
Pick a capture or comp page from output/ (newest first), tick the views, press Make views. Each
view runs its own tool (render.py, shape_map.py, tables.py) as a child process, exactly the command
you would type, and the pages it writes open in the default browser. The log shows the tools' console
lines, which hold counts only.

  render                render.py FILE
  groups (cards)        render.py FILE --groups
  arrangement           render.py FILE --arrange
  outline               render.py FILE --outline
  shape map             shape_map.py FILE
  tables                tables.py CAPTURE --html   (a comp page is mapped to its capture_ file: same stamp)

New capture runs key_probe.py in its own console window (switch to the browser within 8 s); the list
refreshes when it ends. Launch with views.bat (tools/pre_dev/class_diff/views.bat).
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
    ("groups (cards.py)", "render.py", ["--groups"], False),
    ("arrangement (arrange.py)", "render.py", ["--arrange"], False),
    ("outline (container shapes)", "render.py", ["--outline"], False),
    ("shape map (record sets)", "shape_map.py", [], False),
    ("tables (tables.py)", "tables.py", ["--html"], True),
]
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


def command(view, path: Path):
    _, script, extra, raw = view
    src = capture_of(path) if raw else path
    return [child_python(), str(HERE / script), str(src)] + extra


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
            var = tk.BooleanVar(value=v[0] in ("outline (container shapes)", "shape map (record sets)"))
            ttk.Checkbutton(vf, text=v[0], variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 18))
            self.ticks.append(var)
        self.open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(vf, text="open pages when done", variable=self.open_var).grid(row=2, column=0, sticky="w", pady=(6, 0))
        btns = ttk.Frame(vf)
        btns.grid(row=2, column=1, columnspan=2, sticky="e", pady=(6, 0))
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
        views = [v for v, t in zip(VIEWS, self.ticks) if t.get()]
        if self.busy or not path or not views:
            if not path:
                self.say("pick a file first")
            elif not views:
                self.say("tick at least one view")
            return
        self.busy = True
        self.go.configure(state="disabled")
        threading.Thread(target=self._work, args=(path, views, self.open_var.get()), daemon=True).start()

    def _work(self, path, views, open_pages):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self.root.after(0, self.say, f"== {path.name}")
        for v in views:
            cmd = command(v, path)
            self.root.after(0, self.say, f"-- {v[0]}")
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
