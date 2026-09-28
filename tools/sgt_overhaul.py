"""
tools/sgt_overhaul.py
---------------------
Tracker and dispatcher for the SGT overhaul (docs/sgt-blueprint.md section 14).

Nothing here is an AI. The dispatcher picks the next work package (WP) whose dependencies are
Done, starts ONE fresh Claude Code CLI session for it with the WP's model (a fresh session per
WP is the "/clear"), records the run, and moves on. When the plan's usage limit is hit it sleeps
until the limit resets, then carries on, until the deadline in the plan.

Files (all in docs/ of the sgt-overhaul worktree):
  sgt-overhaul-plan.json       fixed plan: WPs, deps, models, CLI permissions   (edit by hand)
  sgt-overhaul-runner.md       the prompt every worker session gets              (edit by hand)
  sgt-overhaul-status.csv      one row per WP                                    (this tool only)
  sgt-overhaul-runs.csv        one row per CLI run: tokens, cost, outcome        (this tool only)
  sgt-overhaul-decisions.csv   decisions agents took where the user was needed   (this tool only)
  sgt-overhaul-checks.csv      hands-on checks left for the user                 (this tool only)
  sgt-overhaul-report.md / sgt-overhaul-agents.xlsx   read-only views, rebuilt after every run

  PY=../APP/venv/Scripts/python.exe
  $PY tools/sgt_overhaul.py show [WP]
  $PY tools/sgt_overhaul.py next
  $PY tools/sgt_overhaul.py finish W0-1 --commit 1a2b3c4 --notes "..."
  $PY tools/sgt_overhaul.py block W0-1 --reason "..."
  $PY tools/sgt_overhaul.py ask W0-1 --question "..." --options "A|B" --default B   (pop-up; 4 min, then the default)
  $PY tools/sgt_overhaul.py answer 3 "A"                                             (answer a question without the pop-up)
  $PY tools/sgt_overhaul.py decide W0-1 --question "..." --choice "..." --why "..."
  $PY tools/sgt_overhaul.py viewer                                                   (build the live Excel viewer once)
  $PY tools/sgt_overhaul.py check-add W2-1 --text "..."
  $PY tools/sgt_overhaul.py check 3 --result Pass --notes "..."
  $PY tools/sgt_overhaul.py report
  $PY tools/sgt_overhaul.py run [--once] [--dry-run]

Exit code 2 = refused (unknown WP, dependencies not Done, Done without a commit...).
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
LOG_DIR = REPO / "logs" / "sgt-overhaul"
LOCK = REPO / ".sgt-overhaul.lock"

STATUSES = ["Not started", "In progress", "Retry", "Done", "Blocked"]
STATUS_FIELDS = ["WP", "Phase", "What", "Status", "Model", "Attempts", "Commit", "Notes", "Updated"]
RUN_FIELDS = ["Run", "WP", "Model", "Attempt", "Started", "Minutes", "Outcome", "Input tokens",
              "Output tokens", "Cache read", "Cache write", "Cost USD", "Turns", "Summary"]
DECISION_FIELDS = ["WP", "Question", "Choice", "By", "Why", "Date"]
CHECK_FIELDS = ["Check", "WP", "Text", "Result", "Date", "Notes"]
QUESTION_FIELDS = ["Q", "WP", "Question", "Options", "Default", "Answer", "By", "Asked", "Answered"]
LOCK_STALE_S = 600
ASK_WAIT_S = 240                                # the user has 4 minutes to answer a worker's question


class Refused(Exception):
    pass


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def stamp() -> str:
    return now().strftime("%Y-%m-%d %H:%M")


# ── Files ─────────────────────────────────────────────────────────────────────────
def _read_csv(path: Path, fields: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (r.get(k) or "") for k in fields} for r in csv.DictReader(f)]


def _write_csv(path: Path, fields: List[str], rows: List[Dict[str, str]]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    for _ in range(600):                        # Excel may hold the file (up to 5 minutes)
        try:
            tmp.write_text(buf.getvalue(), encoding="utf-8")
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.5)
    raise Refused(f"{path.name} is locked (close it in Excel)")


class Tracker:
    def __init__(self, docs: Optional[Path] = None):
        self.docs = docs = docs or DOCS
        self.plan = json.loads((docs / "sgt-overhaul-plan.json").read_text(encoding="utf-8"))
        self.wps = {w["wp"]: w for w in self.plan["wps"]}
        self.order = [w["wp"] for w in self.plan["wps"]]
        self.p_status = docs / "sgt-overhaul-status.csv"
        self.p_runs = docs / "sgt-overhaul-runs.csv"
        self.p_decisions = docs / "sgt-overhaul-decisions.csv"
        self.p_checks = docs / "sgt-overhaul-checks.csv"
        self.p_questions = docs / "sgt-overhaul-questions.csv"
        rows = {r["WP"]: r for r in _read_csv(self.p_status, STATUS_FIELDS)}
        self.status = {wp: rows.get(wp) or {"WP": wp, "Status": "Not started", "Model": self.model_id(wp),
                                            "Attempts": "0", "Commit": "", "Notes": "", "Updated": ""}
                       for wp in self.order}
        for wp, row in self.status.items():     # plan columns, so the live sheet explains itself
            row["Phase"], row["What"] = str(self.wps[wp]["phase"]), self.wps[wp]["what"]
        for p, fields in ((self.p_runs, RUN_FIELDS), (self.p_decisions, DECISION_FIELDS),
                          (self.p_checks, CHECK_FIELDS), (self.p_questions, QUESTION_FIELDS)):
            if not p.exists():                  # the viewer's queries need every file, even empty
                _write_csv(p, fields, [])
        if not self.p_status.exists():
            self.save()

    # plan
    def model_id(self, wp: str) -> str:
        """The model a WP last ran on, else its tier's last-resort (Claude) model."""
        used = getattr(self, "status", {}).get(wp, {}).get("Model")
        return used or self.plan["models"][self.plan["tiers"][self.wps[wp]["model"]][-1]]["id"]

    def deadline(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.plan["deadline"])

    def wp(self, wp: str) -> Dict:
        if wp not in self.wps:
            raise Refused(f"unknown WP {wp}")
        return self.wps[wp]

    # status
    def save(self) -> None:
        _write_csv(self.p_status, STATUS_FIELDS, [self.status[w] for w in self.order])

    def set(self, wp: str, **kw: str) -> None:
        self.wp(wp)
        row = self.status[wp]
        for k, v in kw.items():
            row[k] = v
        row["Updated"] = stamp()
        self.save()

    def deps_done(self, wp: str) -> bool:
        return all(self.status[d]["Status"] == "Done" for d in self.wp(wp)["deps"])

    def next_ready(self) -> Optional[str]:
        for wp in self.order:
            if self.status[wp]["Status"] in ("Not started", "Retry") and self.deps_done(wp):
                return wp
        return None

    def finish(self, wp: str, commit: str, notes: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{7,40}", commit or ""):
            raise Refused("Done needs the commit hash (git rev-parse --short HEAD)")
        if not self.deps_done(wp):
            raise Refused(f"{wp}: dependencies are not Done")
        self.set(wp, Status="Done", Commit=commit, Notes=notes)

    def block(self, wp: str, reason: str) -> None:
        self.set(wp, Status="Blocked", Notes=reason)

    # other tables
    def add_row(self, path: Path, fields: List[str], row: Dict[str, str]) -> None:
        rows = _read_csv(path, fields)
        rows.append({k: row.get(k, "") for k in fields})
        _write_csv(path, fields, rows)

    def decide(self, wp: str, question: str, choice: str, why: str) -> None:
        self.wp(wp)
        self.add_row(self.p_decisions, DECISION_FIELDS,
                     {"WP": wp, "Question": question, "Choice": choice, "By": "agent", "Why": why, "Date": stamp()})

    def check_add(self, wp: str, text: str) -> int:
        self.wp(wp)
        rows = _read_csv(self.p_checks, CHECK_FIELDS)
        n = len(rows) + 1
        self.add_row(self.p_checks, CHECK_FIELDS,
                     {"Check": str(n), "WP": wp, "Text": text, "Result": "Not run", "Date": stamp()})
        return n

    def check(self, n: int, result: str, notes: str) -> None:
        if result not in ("Pass", "Fail", "Not run"):
            raise Refused("result must be Pass, Fail or Not run")
        rows = _read_csv(self.p_checks, CHECK_FIELDS)
        for r in rows:
            if r["Check"] == str(n):
                r.update(Result=result, Notes=notes, Date=stamp())
                _write_csv(self.p_checks, CHECK_FIELDS, rows)
                return
        raise Refused(f"no check {n}")

    # views
    def show(self, wp: Optional[str] = None) -> str:
        if wp:
            w, s = self.wp(wp), self.status[wp]
            return (f"{wp} [{w['kind']}, {w['size']}, {self.model_id(wp)}] {w['what']}\n"
                    f"deps: {', '.join(w['deps']) or '-'}   status: {s['Status']}   attempts: {s['Attempts']}\n"
                    f"notes: {s['Notes']}\n\nfocus: {w['focus']}")
        lines = []
        for x in self.order:
            s = self.status[x]
            lines.append(f"{x:6} {s['Status']:12} {self.wps[x]['model']:6} {s['Commit'][:7]:7} {self.wps[x]['what']}")
        return "\n".join(lines)

    def report(self) -> str:
        runs = _read_csv(self.p_runs, RUN_FIELDS)
        counts = {k: sum(1 for s in self.status.values() if s["Status"] == k) for k in STATUSES}
        tok = sum(int(r["Output tokens"] or 0) for r in runs)
        cost = sum(float(r["Cost USD"] or 0) for r in runs)
        out = [f"# SGT overhaul — report ({stamp()})", "",
               f"Deadline: {self.plan['deadline']}", "",
               "| Status | WPs |", "| :--- | ---: |"] + [f"| {k} | {v} |" for k, v in counts.items()] + [
               "", f"Runs: {len(runs)}   output tokens: {tok}   API-equivalent cost: ${cost:.2f}", "",
               "| WP | Status | Model | Commit | What | Notes |", "| :--- | :--- | :--- | :--- | :--- | :--- |"]
        for x in self.order:
            s = self.status[x]
            out.append(f"| {x} | {s['Status']} | {self.wps[x]['model']} | {s['Commit'][:7]} | "
                       f"{self.wps[x]['what']} | {s['Notes'][:160].replace('|', '/')} |")
        dec = _read_csv(self.p_decisions, DECISION_FIELDS)
        if dec:
            out += ["", "## Decisions taken for you", ""] + [f"- **{d['WP']}** {d['Question']} → {d['Choice']} ({d['Why']})" for d in dec]
        chk = [c for c in _read_csv(self.p_checks, CHECK_FIELDS) if c["Result"] != "Pass"]
        if chk:
            out += ["", "## Checks waiting for you", ""] + [f"- #{c['Check']} ({c['WP']}) {c['Text']} — {c['Result']}" for c in chk]
        text = "\n".join(out) + "\n"
        (self.docs / "sgt-overhaul-report.md").write_text(text, encoding="utf-8")
        return text

    # questions: a worker asks, the user has ASK_WAIT_S to answer, then the default stands
    def ask(self, wp: str, question: str, options: List[str], default: str, wait_s: int = ASK_WAIT_S,
            popup: bool = True) -> Dict[str, str]:
        self.wp(wp)
        if default not in options:
            raise Refused("the default must be one of the options")
        rows = _read_csv(self.p_questions, QUESTION_FIELDS)
        q = str(len(rows) + 1)
        self.add_row(self.p_questions, QUESTION_FIELDS, {"Q": q, "WP": wp, "Question": question,
                     "Options": " | ".join(options), "Default": default, "Asked": stamp()})
        answer = _popup(q, wp, question, options, default, wait_s, self._answer_in_csv) if popup else None
        if answer is None:                      # no window (locked desktop / tests): the CSV only
            end = time.time() + wait_s
            while answer is None and time.time() < end:
                answer = self._answer_in_csv(q)
                if answer is None:
                    time.sleep(2)
        by = "user" if answer else "default (no answer in time)"
        answer = answer or default
        rows = _read_csv(self.p_questions, QUESTION_FIELDS)
        for r in rows:
            if r["Q"] == q:
                r.update(Answer=answer, By=by, Answered=stamp())
        _write_csv(self.p_questions, QUESTION_FIELDS, rows)
        self.add_row(self.p_decisions, DECISION_FIELDS, {"WP": wp, "Question": question, "Choice": answer,
                     "By": by, "Why": f"asked as Q{q}", "Date": stamp()})
        return {"q": q, "answer": answer, "by": by}

    def _answer_in_csv(self, q: str) -> Optional[str]:
        for r in _read_csv(self.p_questions, QUESTION_FIELDS):
            if r["Q"] == q and r["Answer"].strip():
                return r["Answer"].strip()
        return None

    def answer(self, q: int, choice: str) -> None:
        rows = _read_csv(self.p_questions, QUESTION_FIELDS)
        for r in rows:
            if r["Q"] == str(q):
                r.update(Answer=choice, By="user", Answered=stamp())
                _write_csv(self.p_questions, QUESTION_FIELDS, rows)
                return
        raise Refused(f"no question {q}")

    def viewer(self) -> Path:
        """
        Builds docs/sgt-overhaul-agents.xlsx once: a How-to sheet, the fixed plan, and one sheet per
        CSV, each a Power Query table that refreshes when the workbook opens and every minute - so
        the workbook can stay open while the agents work. Needs Excel (COM); close the file first.
        """
        from openpyxl import Workbook
        out = self.docs / "sgt-overhaul-agents.xlsx"
        if out.with_name("~$" + out.name).exists():
            raise Refused(f"{out.name} is open in Excel - close it and run again")
        wb = Workbook()
        ws = wb.active
        ws.title = "How to use"
        for line in ("SGT overhaul tracker - read-only viewer.",
                     "Every sheet except Plan is a live copy of a CSV in this folder: it refreshes when the",
                     "file opens and every minute (or Data -> Refresh All). Nothing typed here is saved back.",
                     "Answer a worker's question in the pop-up window, or:",
                     r"  ..\APP\venv\Scripts\python.exe tools\sgt_overhaul.py answer <Q> <choice>",
                     f"Deadline: {self.plan['deadline']}"):
            ws.append([line])
        ws.column_dimensions["A"].width = 100
        plan = wb.create_sheet("Plan")
        plan.append(["WP", "Phase", "What", "Model", "Kind", "Size", "Deps", "Focus"])
        for w in self.plan["wps"]:
            plan.append([w["wp"], w["phase"], w["what"], self.model_id(w["wp"]), w["kind"], w["size"],
                         ", ".join(w["deps"]), w["focus"]])
        plan.freeze_panes = "A2"
        links = [("Status", self.p_status), ("Questions", self.p_questions), ("Decisions", self.p_decisions),
                 ("Checks", self.p_checks), ("Runs", self.p_runs)]
        for name, _ in links:
            wb.create_sheet(name)
        wb.save(out)
        _attach_power_query(out, links)
        return out


def _popup(q: str, wp: str, question: str, options: List[str], default: str, wait_s: int,
           poll) -> Optional[str]:
    """A small always-on-top window with one button per option and a countdown. Returns the
    user's choice, None when the time runs out (or no window can be shown)."""
    try:
        import tkinter as tk
    except ImportError:
        return None
    got: Dict[str, Optional[str]] = {"v": None}
    try:
        root = tk.Tk()
    except Exception:
        return None
    end = time.time() + wait_s
    root.title(f"SGT overhaul - {wp} asks (Q{q})")
    root.attributes("-topmost", True)
    tk.Label(root, text=question, wraplength=520, justify="left", font=("Segoe UI", 11)).pack(padx=16, pady=(14, 8))
    left = tk.Label(root, font=("Segoe UI", 9))
    left.pack()

    def pick(v):
        got["v"] = v
        root.destroy()

    for o in options:
        tk.Button(root, text=o + ("   (default)" if o == default else ""), wraplength=500,
                  command=lambda v=o: pick(v)).pack(fill="x", padx=16, pady=3)

    def tick():
        remaining = int(end - time.time())
        csv_answer = poll(q)                    # answered from the command line instead
        if csv_answer:
            pick(csv_answer)
            return
        if remaining <= 0:
            root.destroy()
            return
        left.config(text=f"No answer in {remaining // 60}:{remaining % 60:02d} -> the default is used")
        root.after(1000, tick)

    root.bell()
    tick()
    root.mainloop()
    return got["v"]


def _attach_power_query(xlsx: Path, links) -> None:
    """Excel itself (COM) adds one CSV query per sheet, refreshes them and saves (as the Sera Sync
    v3 viewer does)."""
    import comtypes.client

    def m_formula(path: Path) -> str:
        p = str(path.resolve()).replace('"', '""')
        return ('let Source = Csv.Document(File.Contents("' + p + '"),[Delimiter=",", Encoding=65001, '
                'QuoteStyle=QuoteStyle.Csv]), Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), '
                'AsText = Table.TransformColumnTypes(Promoted, List.Transform(Table.ColumnNames(Promoted), '
                'each {_, type text})) in AsText')

    xl = comtypes.client.CreateObject("Excel.Application")
    try:
        xl.Visible = False
        xl.DisplayAlerts = False
        wb = xl.Workbooks.Open(str(xlsx.resolve()))
        for sheet_name, path in links:
            query = f"Sgt{sheet_name}Csv"
            wb.Queries.Add(query, m_formula(path))
            ws = wb.Worksheets(sheet_name)
            ws.Cells.Clear()
            lo = ws.ListObjects.Add(0, 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
                                       f'Location={query};Extended Properties=""', None, 1, ws.Range("$A$1"))
            lo.Name = query
            qt = lo.QueryTable
            qt.CommandType = 2
            qt.CommandText = f"SELECT * FROM [{query}]"
            qt.BackgroundQuery = False
            qt.Refresh(False)
            conn = qt.WorkbookConnection.OLEDBConnection
            conn.BackgroundQuery = True
            conn.RefreshOnFileOpen = True
            conn.RefreshPeriod = 1
        wb.Worksheets("Status").Activate()
        wb.Save()
        wb.Close(False)
    finally:
        xl.Quit()


# ── The dispatcher ────────────────────────────────────────────────────────────────
# A WP names a TIER ("sonnet" = Sonnet-level work). plan["tiers"] lists the models allowed for it,
# in order of preference; each model says which RUNNER (agent CLI) runs it. The first model whose
# runner is enabled, installed and not held by a usage limit is used - so Gemini can take
# Sonnet-level work and save the Claude budget for Opus work, and Claude takes over when Gemini's
# quota runs out (and the other way round).
def runner_exe(t: Tracker, runner: str) -> Optional[List[str]]:
    r = t.plan["runners"][runner]
    env = os.environ.get(f"SGT_RUNNER_{runner.upper()}") or (os.environ.get("SGT_CLAUDE") if runner == "claude" else None)
    if env:
        return json.loads(env)
    if not r.get("enabled"):
        return None
    exe = shutil.which(r["exe"]) if r.get("exe") else None
    return [exe] if exe else None


def pick_model(t: Tracker, wp: str, held: Dict[str, dt.datetime]) -> Optional[str]:
    """The model key to run `wp` with now, or None if every allowed runner is unavailable."""
    w = t.wp(wp)
    for key in t.plan["tiers"][w["model"]]:
        runner = t.plan["models"][key]["runner"]
        if w.get("claude_only") and runner != "claude":
            continue
        if held.get(runner) and held[runner] > now():
            continue
        if runner_exe(t, runner):
            return key
    return None


def build_cmd(t: Tracker, key: str) -> List[str]:
    m = t.plan["models"][key]
    r, cli = t.plan["runners"][m["runner"]], t.plan["cli"]
    fill = {"{model}": m["id"], "{permission_mode}": cli["permission_mode"],
            "{allowed_tools}": ",".join(cli["allowed_tools"]), "{disallowed_tools}": ",".join(cli["disallowed_tools"])}
    args = []
    for a in r["args"]:
        for k, v in fill.items():
            a = a.replace(k, v)
        args.append(a)
    return runner_exe(t, m["runner"]) + args


def build_prompt(t: Tracker, wp: str, key: Optional[str] = None) -> str:
    w, s = t.wp(wp), t.status[wp]
    key = key or t.plan["tiers"][w["model"]][-1]
    m = t.plan["models"][key]
    text = (t.docs / "sgt-overhaul-runner.md").read_text(encoding="utf-8")
    for k, v in {"{WP}": wp, "{WHAT}": w["what"], "{FOCUS}": w["focus"], "{KIND}": w["kind"],
                 "{MODEL}": m["id"], "{COAUTHOR}": t.plan["runners"][m["runner"]]["coauthor"],
                 "{ATTEMPT}": str(int(s["Attempts"] or 0)),
                 "{PREVIOUS}": s["Notes"] or "none", "{DEADLINE}": t.plan["deadline"]}.items():
        text = text.replace(k, v)
    return text


_EPOCH = re.compile(r"\|\s*(\d{10})\b")
_CLOCK = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)
_LIMIT = re.compile(r"(usage|rate|session|weekly|5-hour|hour)\s+limit|limit\s+(reached|exceeded|hit)|"
                    r"quota|resource[_ ]exhausted|too many requests|\b429\b", re.I)


_AUTH = re.compile(r"authentication required|please (visit|log ?in|sign ?in)|not (logged|signed) in|invalid api key|oauth", re.I)
_TRANSIENT = re.compile(r"can.t reach the api|ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN|socket hang up|network|"
                        r"overloaded|API Error: 5\d\d\b|internal server error|service unavailable|connection (reset|refused|error)", re.I)


def limit_reset(text: str, ref: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """When `text` says a usage limit was hit: the moment it resets (best guess), else None."""
    if not _LIMIT.search(text or ""):
        return None
    ref = ref or now()
    m = _EPOCH.search(text)
    if m:
        return dt.datetime.fromtimestamp(int(m.group(1))).astimezone()
    m = _CLOCK.search(text)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24:
            at = ref.replace(hour=h, minute=mi, second=0, microsecond=0)
            return at if at > ref else at + dt.timedelta(days=1)
    return ref + dt.timedelta(minutes=30)


def _parse(stdout: str) -> Dict:
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                return d
        except ValueError:
            continue
    try:
        return json.loads(stdout)
    except (ValueError, TypeError):
        return {}


class _Lock:
    def __enter__(self):
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < LOCK_STALE_S:
            raise Refused(f"another dispatcher is running ({LOCK.read_text(errors='ignore').strip()})")
        LOCK.write_text(f"pid {os.getpid()} since {stamp()}")
        self._stop = threading.Event()
        threading.Thread(target=self._beat, daemon=True).start()
        return self

    def _beat(self):
        while not self._stop.wait(60):
            try:
                os.utime(LOCK)
            except OSError:
                pass

    def __exit__(self, *exc):
        self._stop.set()
        try:
            LOCK.unlink()
        except OSError:
            pass


def run_one(t: Tracker, wp: str, key: Optional[str] = None, dry: bool = False, log=print) -> Optional[dt.datetime]:
    """Runs one WP in a fresh CLI session. Returns a reset time when a usage limit stopped it."""
    cli = t.plan["cli"]
    key = key or pick_model(t, wp, {})
    if not key:
        raise Refused(f"{wp}: no runner available for tier {t.wp(wp)['model']}")
    model_id, runner = t.plan["models"][key]["id"], t.plan["models"][key]["runner"]
    attempt = int(t.status[wp]["Attempts"] or 0) + 1
    cmd = build_cmd(t, key)
    prompt = build_prompt(t, wp, key)
    as_arg = t.plan["runners"][runner].get("prompt") == "arg"
    if as_arg:
        cmd = cmd + [prompt]
    if dry:
        log(" ".join(cmd if not as_arg else cmd[:-1]) + "\n\n" + prompt)
        return None
    t.set(wp, Status="In progress", Attempts=str(attempt), Model=model_id)
    log(f"[{stamp()}] {wp} attempt {attempt} on {model_id}: {t.wps[wp]['what']}")
    started = time.time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # A worker's "ask" waits up to 4 minutes for the user; the CLI's shell timeout must outlast it.
        # SGT_OVERHAUL_WORKER makes the repository's pre-push hook refuse any push from a worker.
        env = {**os.environ, "BASH_DEFAULT_TIMEOUT_MS": str((ASK_WAIT_S + 90) * 1000),
               "BASH_MAX_TIMEOUT_MS": "600000", "SGT_OVERHAUL_WORKER": wp}
        p = subprocess.run(cmd, input=None if as_arg else prompt, cwd=REPO, env=env, capture_output=True,
                           text=True, encoding="utf-8",
                           errors="replace", timeout=cli["run_timeout_min"] * 60)
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = (e.stdout or ""), "timeout", -1
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    run_id = now().strftime("%Y%m%d-%H%M%S")
    (LOG_DIR / f"{run_id}-{wp}.json").write_text(out + ("\n--- stderr ---\n" + err if err else ""), encoding="utf-8")
    d = _parse(out)
    usage = d.get("usage") or {}
    result = str(d.get("result") or d.get("response") or (out if not d else "") or err or "")[:4000]
    # Claude reports is_error; Antigravity reports status (and puts its reason on stderr).
    failed = (d.get("is_error") or d.get("error") or code != 0 or not d
              or str(d.get("status", "SUCCESS")).upper() not in ("SUCCESS", "OK"))
    if not d.get("result") and not d.get("response") and err:
        result = err[:4000]
    reset = limit_reset(result + " " + (err or "")) if failed else None
    if failed and not reset and _AUTH.search(result + " " + (err or "")):
        reset = now() + dt.timedelta(hours=12)      # the runner needs a sign-in: set it aside, no attempt spent
    if failed and not reset and _TRANSIENT.search(result + " " + (err or "")):
        reset = now() + dt.timedelta(minutes=5)     # network / API hiccup: wait, retry, no attempt spent

    fresh = Tracker(t.docs)                     # the worker updated the status file itself
    state = fresh.status[wp]["Status"]
    if reset:
        both = result + " " + (err or "")
        outcome = ("usage limit" if _LIMIT.search(both) else
                   "runner needs sign-in" if _AUTH.search(both) else "network/API error")
        if state not in ("Done", "Blocked"):
            fresh.set(wp, Status="Retry", Attempts=str(attempt - 1),
                      Notes=f"stopped by {outcome}, resumes after {reset:%H:%M}")
    elif state in ("Done", "Blocked"):
        outcome = state
    else:
        outcome = "timeout" if code == -1 else ("error" if code else "no finish")
        if attempt >= cli["max_attempts"]:
            fresh.block(wp, f"{outcome} after {attempt} attempts; see logs/sgt-overhaul/{run_id}-{wp}.json")
        else:
            fresh.set(wp, Status="Retry", Notes=f"attempt {attempt}: {outcome}. {result[:300]}")
    fresh.add_row(fresh.p_runs, RUN_FIELDS, {
        "Run": run_id, "WP": wp, "Model": model_id, "Attempt": str(attempt),
        "Started": dt.datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M"),
        "Minutes": f"{(time.time() - started) / 60:.1f}", "Outcome": outcome,
        "Input tokens": str(usage.get("input_tokens", "")), "Output tokens": str(usage.get("output_tokens", "")),
        "Cache read": str(usage.get("cache_read_input_tokens", "")),
        "Cache write": str(usage.get("cache_creation_input_tokens", "")),
        "Cost USD": str(d.get("total_cost_usd", "")), "Turns": str(d.get("num_turns", "")),
        "Summary": result[:300].replace("\n", " ")})
    fresh.report()
    log(f"[{stamp()}] {wp}: {outcome}")
    return reset


def run_loop(once: bool = False, dry: bool = False, log=print) -> int:
    t = Tracker()
    for wp, s in t.status.items():             # a crash mid-run leaves a WP "In progress"
        if s["Status"] == "In progress":
            t.set(wp, Status="Retry", Notes=(s["Notes"] + " | dispatcher restarted").strip(" |"))
    held: Dict[str, dt.datetime] = {}           # runner -> when its usage limit resets
    with _Lock():
        while True:
            t = Tracker()
            if now() >= t.deadline():
                log(f"[{stamp()}] deadline reached")
                return 0
            wp = t.next_ready()
            if not wp:
                log(f"[{stamp()}] nothing ready: " + ", ".join(f"{k} {v}" for k, v in
                    {s: sum(1 for x in t.status.values() if x['Status'] == s) for s in STATUSES}.items() if v))
                return 0
            key = pick_model(t, wp, held)
            if not key:
                waits = [r for r in held.values() if r > now()]
                if not waits:
                    log(f"[{stamp()}] {wp}: no runner installed/enabled for tier {t.wps[wp]['model']}")
                    return 2
                wait = min((min(waits) - now()).total_seconds() + 120, (t.deadline() - now()).total_seconds())
                log(f"[{stamp()}] every runner for {wp} is at its usage limit: sleeping {wait / 60:.0f} min")
                countdown(max(60, wait), f"{wp} waits for the usage limit / connection to clear")
                continue
            reset = run_one(t, wp, key, dry=dry, log=log)
            if once or dry:
                return 0
            if reset:
                runner = t.plan["models"][key]["runner"]
                held[runner] = reset
                log(f"[{stamp()}] {runner} usage limit until {reset:%H:%M}; other runners carry on")


WAIT_FILE = LOG_DIR / "waiting.json"


def countdown(seconds: float, reason: str) -> None:
    """Sleeps, showing a live 'resumes in mm:ss' timer in the console, and leaves the end time in
    logs/sgt-overhaul/waiting.json so 'watch' can show the same timer."""
    until = now() + dt.timedelta(seconds=seconds)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WAIT_FILE.write_text(json.dumps({"until": until.isoformat(), "reason": reason}), encoding="utf-8")
    live = bool(getattr(sys.stdout, "isatty", lambda: False)())
    try:
        while (left := (until - now()).total_seconds()) > 0:
            if live:
                h, rem = divmod(int(left), 3600)
                sys.stdout.write(f"\r  {reason}: resumes at {until:%H:%M} (in {h}:{rem // 60:02d}:{rem % 60:02d})   ")
                sys.stdout.flush()
            time.sleep(1 if live else min(30, left))
        if live:
            print()
    finally:
        try:
            WAIT_FILE.unlink()
        except OSError:
            pass


def watch(poll_s: float = 1.0) -> None:
    """Follows the newest Claude worker transcript and prints what the worker does, readably.
    Switches to a newer transcript when the next WP starts. Ctrl+C to stop."""
    folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(REPO))
    current, pos = None, 0
    while True:
        files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
        if files and files[-1] != current:
            current, pos = files[-1], 0
            print(f"\n===== {current.name} ({dt.datetime.fromtimestamp(current.stat().st_mtime):%H:%M}) =====", flush=True)
        if current:
            with open(current, encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    for part in (d.get("message") or {}).get("content") or []:
                        if not isinstance(part, dict):
                            continue
                        ts = (d.get("timestamp") or "")[11:19]
                        if part.get("type") == "text" and d.get("type") == "assistant":
                            print(f"{ts} SAYS  {part['text'].strip()[:400]}", flush=True)
                        elif part.get("type") == "tool_use":
                            inp = part.get("input") or {}
                            what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or json.dumps(inp)[:120]
                            print(f"{ts} {part.get('name', ''):5} {str(what)[:200]}", flush=True)
                pos = f.tell()
        try:                                    # the dispatcher is waiting: show its timer
            w = json.loads(WAIT_FILE.read_text(encoding="utf-8"))
            left = (dt.datetime.fromisoformat(w["until"]) - now()).total_seconds()
            if left > 0:
                h, rem = divmod(int(left), 3600)
                print(f"\r  WAITING  {w['reason']}: resumes at {w['until'][11:16]} (in {h}:{rem // 60:02d}:{rem % 60:02d})   ",
                      end="", flush=True)
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(poll_s)


# ── CLI ───────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SGT overhaul tracker and dispatcher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("wp", nargs="?")
    sub.add_parser("next")
    s = sub.add_parser("finish"); s.add_argument("wp"); s.add_argument("--commit", required=True); s.add_argument("--notes", default="")
    s = sub.add_parser("block"); s.add_argument("wp"); s.add_argument("--reason", required=True)
    s = sub.add_parser("decide"); s.add_argument("wp"); s.add_argument("--question", required=True)
    s.add_argument("--choice", required=True); s.add_argument("--why", default="")
    s = sub.add_parser("check-add"); s.add_argument("wp"); s.add_argument("--text", required=True)
    s = sub.add_parser("check"); s.add_argument("n", type=int); s.add_argument("--result", required=True); s.add_argument("--notes", default="")
    s = sub.add_parser("reset"); s.add_argument("wp")
    s = sub.add_parser("ask"); s.add_argument("wp"); s.add_argument("--question", required=True)
    s.add_argument("--options", required=True, help="choices separated by |"); s.add_argument("--default", required=True)
    s.add_argument("--wait", type=int, default=ASK_WAIT_S)
    s = sub.add_parser("answer"); s.add_argument("q", type=int); s.add_argument("choice")
    sub.add_parser("viewer")
    sub.add_parser("watch")
    sub.add_parser("report")
    s = sub.add_parser("run"); s.add_argument("--once", action="store_true"); s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if sys.stdout is None:                      # started by pythonw (the scheduled task): log to a file
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(LOG_DIR / "dispatcher.log", "a", encoding="utf-8", buffering=1)
    try:
        if a.cmd == "run":
            return run_loop(once=a.once, dry=a.dry_run)
        t = Tracker()
        if a.cmd == "show":
            print(t.show(a.wp))
        elif a.cmd == "next":
            print(t.next_ready() or "nothing ready")
        elif a.cmd == "finish":
            t.finish(a.wp, a.commit, a.notes)
        elif a.cmd == "block":
            t.block(a.wp, a.reason)
        elif a.cmd == "decide":
            t.decide(a.wp, a.question, a.choice, a.why)
        elif a.cmd == "check-add":
            print(f"check {t.check_add(a.wp, a.text)} added")
        elif a.cmd == "check":
            t.check(a.n, a.result, a.notes)
        elif a.cmd == "ask":
            got = t.ask(a.wp, a.question, [o.strip() for o in a.options.split("|") if o.strip()],
                        a.default.strip(), a.wait)
            print(f"ANSWER: {got['answer']}   (by {got['by']}, Q{got['q']})")
        elif a.cmd == "answer":
            t.answer(a.q, a.choice)
        elif a.cmd == "watch":
            watch()
        elif a.cmd == "viewer":
            print(f"built {t.viewer()}")
        elif a.cmd == "reset":
            t.set(a.wp, Status="Not started", Attempts="0", Notes="reset by hand")
        elif a.cmd == "report":
            print(t.report())
        return 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
