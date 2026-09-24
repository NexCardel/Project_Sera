"""
tools/sync_v3_tracker.py
------------------------
Records Sera Sync v3 progress. Agents use this instead of editing anything by hand.

Status lives in two small text files that the viewer workbook
(docs/sera-sync-v3-agents.xlsx) reads with Power Query, so the workbook can stay
open in Excel while agents work:

  docs/sera-sync-v3-status.csv   one row per WP
  docs/sera-sync-v3-checks.csv   one row per hands-on phase check
  docs/sera-sync-v3-plan.json    fixed data (tiers, dependencies, models); built by tools/build_sync_v3_tracker.py

  venv\\Scripts\\python tools\\sync_v3_tracker.py show
  venv\\Scripts\\python tools\\sync_v3_tracker.py show P0-1
  venv\\Scripts\\python tools\\sync_v3_tracker.py set P0-1 --status "In progress" --model "Claude Haiku 4.5"
  venv\\Scripts\\python tools\\sync_v3_tracker.py set P0-1 --status "In review" --notes "debounce 60 s"
  venv\\Scripts\\python tools\\sync_v3_tracker.py set P0-3 --reviewed-by "Claude Opus 5.5"
  venv\\Scripts\\python tools\\sync_v3_tracker.py set P0-1 --status Done --commit 1a2b3c4
  venv\\Scripts\\python tools\\sync_v3_tracker.py checks
  venv\\Scripts\\python tools\\sync_v3_tracker.py check 7 --result Pass --notes "tested on reception PC"

Refuses (exit code 2) when an update breaks the blueprint's rules: unknown model,
model not allowed for the WP's tier, starting a WP whose dependencies are not Done,
or marking Done without a commit or without the required review.
"""

import argparse
import csv
import datetime
import io
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DOCS = REPO / "docs"
STATUSES = ["Not started", "In progress", "In review", "Done", "Blocked"]
RESULTS = ["Not run", "Pass", "Fail"]
STATUS_FIELDS = ["WP", "Status", "Model used", "Reviewed by", "Commit", "Notes", "Updated"]
CHECK_FIELDS = ["Check", "Result", "Date", "Notes"]


class TrackerError(Exception):
    pass


class Tracker:
    def __init__(self, docs: Path):
        self.docs = docs
        self.plan_path = docs / "sera-sync-v3-plan.json"
        self.status_path = docs / "sera-sync-v3-status.csv"
        self.checks_path = docs / "sera-sync-v3-checks.csv"
        if not self.plan_path.exists():
            raise TrackerError(f"{self.plan_path.name} not found. Run tools/build_sync_v3_tracker.py first.")
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.wps = {w["wp"]: w for w in self.plan["wps"]}
        self.models = {m["name"]: m for m in self.plan["models"]}
        self.checks = {c["n"]: c for c in self.plan["phase_checks"]}

    # ---------------- files

    def _read_csv(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        for attempt in range(20):
            try:
                with open(path, encoding="utf-8", newline="") as f:
                    return list(csv.DictReader(f))
            except PermissionError:
                time.sleep(0.25)
        raise TrackerError(f"{path.name} stayed locked for 5 s. Try again.")

    def _write_csv(self, path: Path, fields: list, rows: list[dict]):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\r\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") or "" for k in fields})
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
            f.flush()
            os.fsync(f.fileno())
        # Excel's periodic refresh may be reading the file for a moment; retry instead of failing
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.25)
        raise TrackerError(f"Couldn't replace {path.name} (locked for 5 s). Try again.")

    def _lock(self):
        lock = self.docs / ".sera-sync-v3-tracker.lock"
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return lock
            except FileExistsError:
                if time.time() - lock.stat().st_mtime > 60:   # left behind by a crashed run
                    try:
                        os.remove(lock)
                    except OSError:
                        pass
                    continue
                if time.time() > deadline:
                    raise TrackerError("Another tracker update is running. Try again in a few seconds.")
                time.sleep(0.2)

    def status_rows(self) -> dict:
        rows = {r["WP"]: r for r in self._read_csv(self.status_path) if r.get("WP")}
        for wp in self.wps:
            rows.setdefault(wp, {"WP": wp, "Status": "Not started"})
            if not rows[wp].get("Status"):
                rows[wp]["Status"] = "Not started"
        return rows

    def unfinished_deps(self, wp: str, rows: dict) -> list:
        return [d for d in self.wps[wp]["deps"] if rows.get(d, {}).get("Status") != "Done"]

    # ---------------- commands

    def show(self, wp: str | None):
        rows = self.status_rows()
        if wp:
            if wp not in self.wps:
                raise TrackerError(f"Unknown WP {wp!r}. Valid: {', '.join(self.wps)}")
            w, r = self.wps[wp], rows[wp]
            info = [("WP", wp), ("Phase", w["phase"]), ("Tier", w["tier"]), ("What", w["what"]),
                    ("Depends on", ", ".join(w["deps"]) or "none"), ("Review before merge", w["review"]),
                    ("Status", r.get("Status")), ("Model used", r.get("Model used")),
                    ("Reviewed by", r.get("Reviewed by")), ("Commit", r.get("Commit")),
                    ("Notes", r.get("Notes")), ("Updated", r.get("Updated")),
                    ("Unfinished deps", ", ".join(self.unfinished_deps(wp, rows)) or "none")]
            for k, v in info:
                print(f"{k:>20}: {v if v is not None else ''}")
            return
        for wp, w in self.wps.items():
            state = rows[wp]["Status"]
            if state == "Not started" and not self.unfinished_deps(wp, rows):
                state += " (ready)"
            print(f"{wp:<7} {w['tier']:<3} {state:<24} {w['what']}")

    def set(self, wp, status=None, model=None, reviewed_by=None, commit=None, notes=None, append_notes=False):
        if wp not in self.wps:
            raise TrackerError(f"Unknown WP {wp!r}. Valid: {', '.join(self.wps)}")
        if status is not None and status not in STATUSES:
            raise TrackerError(f"Status must be one of: {', '.join(STATUSES)}")
        for label, name in (("--model", model), ("--reviewed-by", reviewed_by)):
            if name is not None and name not in self.models:
                raise TrackerError(f"{label}: unknown model {name!r}. Use a name from the Models sheet: "
                                   f"{', '.join(self.models)}")
        tier = self.wps[wp]["tier"]
        messages = []
        if model is not None:
            allowed = self.models[model][tier]
            if allowed == "No":
                raise TrackerError(f"{model} is not allowed for {tier} work ({wp}). See the Models sheet.")
            if allowed == "Caution":
                messages.append(f"NOTE: {model} on {tier} needs a Fable 5.1 / Opus 5.5 review before merge.")
        if reviewed_by is not None and self.models[reviewed_by]["review"] != "Yes":
            raise TrackerError(f"{reviewed_by} can't be the reviewer. Reviews need Claude Fable 5.1 or Opus 5.5.")

        lock = self._lock()
        try:
            rows = self.status_rows()
            row = rows[wp]
            if status in ("In progress", "In review", "Done"):
                unfinished = self.unfinished_deps(wp, rows)
                if unfinished:
                    raise TrackerError(f"{wp} depends on work that isn't Done yet: {', '.join(unfinished)}")
            if status == "Done":
                if not (commit or row.get("Commit")):
                    raise TrackerError("Can't mark Done without a commit hash (--commit).")
                used = model or row.get("Model used")
                if not used:
                    raise TrackerError("Can't mark Done without the model that did the work (--model).")
                needs_review = self.wps[wp]["review_required"] or (
                    used in self.models and self.models[used][tier] == "Caution")
                if needs_review and not (reviewed_by or row.get("Reviewed by")):
                    raise TrackerError(f"{wp} needs a Fable 5.1 / Opus 5.5 review before Done (--reviewed-by).")

            for field, value in (("Status", status), ("Model used", model), ("Reviewed by", reviewed_by),
                                 ("Commit", commit)):
                if value is not None:
                    row[field] = value
            if notes is not None:
                entry = f"{datetime.date.today().isoformat()}: {notes}"
                row["Notes"] = f"{row['Notes']}\n{entry}" if (append_notes and row.get("Notes")) else entry
            row["Updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            self._write_csv(self.status_path, STATUS_FIELDS, [rows[w] for w in self.wps])
        finally:
            os.remove(lock)
        for m in messages:
            print(m)
        print(f"Updated {wp}. (Excel: Data → Refresh All to see it.)")

    def list_checks(self):
        results = {int(r["Check"]): r for r in self._read_csv(self.checks_path) if r.get("Check")}
        for n, c in self.checks.items():
            res = results.get(n, {}).get("Result") or "Not run"
            print(f"{n:>3}  phase {c['phase']}  [{res}]  {c['check']}")

    def check(self, number: int, result: str, notes=None):
        if result not in RESULTS:
            raise TrackerError(f"--result must be one of: {', '.join(RESULTS)}")
        if number not in self.checks:
            raise TrackerError(f"No phase check number {number}. Run 'checks' to list them.")
        lock = self._lock()
        try:
            rows = {int(r["Check"]): r for r in self._read_csv(self.checks_path) if r.get("Check")}
            row = rows.setdefault(number, {"Check": number})
            row["Result"] = result
            row["Date"] = datetime.date.today().isoformat()
            if notes is not None:
                row["Notes"] = notes
            ordered = [rows.get(n, {"Check": n, "Result": "Not run"}) for n in self.checks]
            self._write_csv(self.checks_path, CHECK_FIELDS, ordered)
        finally:
            os.remove(lock)
        print(f"Updated check {number}.")


def main(argv=None) -> int:
    # The plan contains characters like "→" that the default Windows console code page can't print
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Record Sera Sync v3 progress.")
    ap.add_argument("--docs", type=Path, default=DEFAULT_DOCS, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_show = sub.add_parser("show", help="show all WPs, or one WP in detail")
    p_show.add_argument("wp", nargs="?")
    p_set = sub.add_parser("set", help="update a WP")
    p_set.add_argument("wp")
    p_set.add_argument("--status", choices=STATUSES)
    p_set.add_argument("--model")
    p_set.add_argument("--reviewed-by")
    p_set.add_argument("--commit")
    p_set.add_argument("--notes")
    p_set.add_argument("--append-notes", action="store_true", help="add to existing notes instead of replacing")
    sub.add_parser("checks", help="list the hands-on phase checks")
    p_chk = sub.add_parser("check", help="record the result of a phase check")
    p_chk.add_argument("number", type=int)
    p_chk.add_argument("--result", required=True, choices=RESULTS)
    p_chk.add_argument("--notes")
    a = ap.parse_args(argv)
    try:
        t = Tracker(a.docs)
        if a.cmd == "show":
            t.show(a.wp)
        elif a.cmd == "set":
            t.set(a.wp, a.status, a.model, a.reviewed_by, a.commit, a.notes, a.append_notes)
        elif a.cmd == "checks":
            t.list_checks()
        elif a.cmd == "check":
            t.check(a.number, a.result, a.notes)
    except TrackerError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
