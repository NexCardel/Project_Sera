"""
core/sgt/sgt_repair.py - repair the GST rows SGT rewrote to another period (2026-10-06 bug)
============================================================================================
Before the fix, `_update_draft` could MOVE an already-written GST row to another period: a
finished June GSTR-3B became "October, Submitted & Verified" (the dashboard's period dropdown had
changed). The row was rewritten in place, so the genuine June row is gone and a false one sits in
the tracker. This module undoes that in two separate steps, because the two halves live on
different PCs:

  1. build_plan(log_dir)       reads THIS PC's own SGT log (sgt_shadow_*.jsonl) - no database, no
                               key - and lists every GST row it moved to another period, with the
                               values the row had before. Run it on every PC that ran the fault.
  2. apply_plan(db, plan, ..)  on the admin PC (data-rewriting maintenance is admin-only, and the
                               sync spreads the result), puts each false row back to what it was,
                               IN PLACE (same row, same client, same session), so nothing is
                               invented. Dry run by default; with apply=True a JSON backup of
                               every row it touches is written first.

It touches only rows SGT wrote (capture method SGT...), never an ARN-bearing row, and only a row
that still looks exactly like the false one (key, status). Anything else is reported, not changed.
Status "Submitted & Verified" with no ARN read from the dashboard tile cannot be judged from the
log alone: those are listed under "review" for a person to check against the portal.
"""

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.dataset_key import compute_dataset_key

GST = "GST Portal"
_NORM = re.compile(r"[^A-Z0-9]")


def _norm(x: Any) -> str:
    return _NORM.sub("", str(x or "").upper())


def row_keys(gstin: str, form: str, period: str) -> List[str]:
    """Every tracker key a GST row can carry: the live canonical key and shadow mode's own."""
    if not (gstin and form and period):
        return []
    return [compute_dataset_key(GST, gstin, form, period),
            f"SGT:GST:{_norm(gstin)}:{_norm(form)}:{_norm(period)}"]


def _read_events(log_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in sorted(Path(log_dir).glob("sgt_shadow_*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
        except OSError:
            continue
    return out


def _ident(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """session -> {gstin, name}: the last value each profile event gave."""
    who: Dict[str, Dict[str, str]] = {}
    for e in events:
        if e.get("event") == "profile" and e.get("portal") == GST and e.get("field") in ("gstin", "name", "pan"):
            who.setdefault(e["session"], {})[e["field"]] = str(e.get("value") or "")
    return who


def build_plan(log_dir: Path, pc: str = "") -> Dict[str, Any]:
    """The moves this PC's log shows, as a plan. Pure: reads the log, writes nothing."""
    events = _read_events(log_dir)
    who = _ident(events)
    chains: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        if e.get("event") != "dataset" or e.get("change") != "moved" or e.get("portal") != GST:
            continue
        prev, new = e.get("previous") or {}, e.get("values") or {}
        if prev.get("period") and new.get("period") and prev.get("period") != new.get("period"):
            chains.setdefault(e["session"], []).append(e)
    items: List[Dict[str, Any]] = []
    done_ident = set()
    for session, moves in chains.items():
        # A row moved twice (June -> September -> October) is one chain: undo it back to its start.
        used = [False] * len(moves)
        for i, first in enumerate(moves):
            if used[i]:
                continue
            start, end = first, first
            used[i] = True
            for j in range(i + 1, len(moves)):
                nxt = moves[j]
                if (not used[j] and (nxt.get("previous") or {}).get("form") == (end.get("values") or {}).get("form")
                        and (nxt.get("previous") or {}).get("period") == (end.get("values") or {}).get("period")):
                    end, used[j] = nxt, True
            keep, false = dict(start["previous"]), dict(end["values"])
            gstin = who.get(session, {}).get("gstin", "")
            if not gstin:
                continue                                    # nobody to put the row back for
            ident = (gstin, keep.get("form"), keep.get("period"), false.get("period"))
            if ident in done_ident:
                continue                                    # the same row moved again on another day
            done_ident.add(ident)
            items.append({
                "session": session, "ts": end.get("ts"), "gstin": gstin, "name": who.get(session, {}).get("name", ""),
                "form": keep.get("form"), "restore": keep, "false": false,
                "restore_keys": row_keys(gstin, keep.get("form"), keep.get("period")),
                "false_keys": row_keys(gstin, false.get("form"), false.get("period")),
            })
    review = _review(events, who)
    return {"made": datetime.now().isoformat(timespec="seconds"), "pc": pc, "items": items, "review": review}


def _review(events: List[Dict[str, Any]], who: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    """GST rows 'Submitted & Verified' with no ARN, built from page pieces: a person should check
    them (before the fix the status could come from another return's tile on the dashboard)."""
    out, seen = [], set()
    for e in events:
        if e.get("event") != "dataset" or e.get("portal") != GST or e.get("record") != "current_dataset":
            continue
        v = e.get("values") or {}
        if e.get("change") != "new" or v.get("status") != "Submitted & Verified" or v.get("arn"):
            continue
        key = (who.get(e["session"], {}).get("gstin", ""), v.get("form"), v.get("period"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"session": e["session"], "ts": e.get("ts"), "gstin": key[0],
                    "name": who.get(e["session"], {}).get("name", ""), "form": v.get("form"),
                    "period": v.get("period"), "evidence": v.get("status_evidence")})
    return out


def write_plan(plan: Dict[str, Any], out_dir: Path) -> List[Path]:
    """plan JSON (to send to the admin PC) and a readable CSV. Never overwrites: a stamp in the name."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pc = re.sub(r"[^A-Za-z0-9_-]", "", plan.get("pc") or "pc") or "pc"
    jp = out_dir / f"sgt_repair_plan_{pc}_{stamp}.json"
    jp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    cp = out_dir / f"sgt_repair_plan_{pc}_{stamp}.csv"
    with open(cp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "when", "gstin", "client", "form", "restore_period", "restore_status",
                    "false_period", "false_status"])
        for it in plan["items"]:
            w.writerow(["fix", it["ts"], it["gstin"], it["name"], it["form"], it["restore"].get("period"),
                        it["restore"].get("status"), it["false"].get("period"), it["false"].get("status")])
        for r in plan["review"]:
            w.writerow(["review", r["ts"], r["gstin"], r["name"], r["form"], r["period"], "Submitted & Verified",
                        "", r.get("evidence") or ""])
    return [jp, cp]


# ── Applying (admin PC) ──────────────────────────────────────────────────────────
def _patched_payload(raw: str, restore: Dict[str, Any], key: str) -> str:
    """The row's JSON with period, status, key and the dataset values put back."""
    try:
        p = json.loads(raw) if raw else {}
    except ValueError:
        p = {}
    if not isinstance(p, dict):
        p = {}
    p["period_label"] = restore.get("period", "")
    p["status"] = restore.get("status") or p.get("status")
    p["dataset_key"] = key
    p["supersedes_dataset_key"] = None
    rp = p.get("raw_payload") if isinstance(p.get("raw_payload"), dict) else None
    if rp is not None:
        rp["dataset_key"] = key
        if isinstance(rp.get("sgt_dataset"), dict):
            rp["sgt_dataset"] = dict(restore)
    p["repaired"] = "sgt_repair 2026-10-06: row moved to another period, put back"
    return json.dumps(p, ensure_ascii=False)


def apply_to_connection(conn: Any, plan: Dict[str, Any], apply: bool = False,
                        backup_to: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Dry run unless apply. One report line per plan item. With apply, the rows about to change are
    saved to `backup_to` BEFORE anything is written."""
    report: List[Dict[str, Any]] = []
    todo: List[Dict[str, Any]] = []
    for it in plan.get("items", []):
        line = {"gstin": it["gstin"], "form": it["form"], "from": it["false"].get("period"),
                "to": it["restore"].get("period")}
        fk, rk = it.get("false_keys") or [], it.get("restore_keys") or []
        if not fk or not rk:
            report.append({**line, "result": "skipped: plan item incomplete"})
            continue
        rows = conn.execute(
            f"SELECT id, dataset_key, status, arn_number, capture_method, raw_payload_json, period_label "
            f"FROM tracker_dump WHERE dataset_key IN ({','.join('?' * len(fk))}) AND capture_method LIKE 'SGT%'",
            fk).fetchall()
        true_exists = conn.execute(f"SELECT 1 FROM tracker_dump WHERE dataset_key IN ({','.join('?' * len(rk))})",
                                   rk).fetchone() is not None
        if not rows:
            report.append({**line, "result": "nothing to do: no false row here"
                           + (" (the right row exists)" if true_exists else "")})
            continue
        if len(rows) > 1:
            report.append({**line, "result": f"skipped: {len(rows)} rows carry the false key - needs a person"})
            continue
        rid, _key, status, arn, _cm, raw, _period = rows[0]
        if str(arn or "N/A").upper() != "N/A" or status != it["false"].get("status"):
            report.append({**line, "result": f"skipped: row no longer looks like the false one "
                                             f"(status {status!r}, ARN {arn!r})"})
            continue
        action = "delete" if true_exists else "revert"
        words = {"delete": ("delete the false row (the right row already exists)", "deleted the false row (the right row already exists)"),
                 "revert": ("put the row back", "put the row back")}[action]
        report.append({**line, "result": ("would " if not apply else "") + (words[0] if not apply else words[1]), "row": rid})
        todo.append({"id": rid, "action": action, "item": it, "raw": raw, "before": [str(x) if x is not None else None for x in rows[0]]})
    if apply and todo:
        if backup_to is not None:
            Path(backup_to).parent.mkdir(parents=True, exist_ok=True)
            Path(backup_to).write_text(json.dumps({"plan_pc": plan.get("pc"), "rows": [
                {"id": x["id"], "action": x["action"], "before": x["before"]} for x in todo]},
                ensure_ascii=False, indent=1), encoding="utf-8")
        for x in todo:
            if x["action"] == "delete":
                conn.execute("DELETE FROM tracker_dump WHERE id = ?", (x["id"],))
            else:
                it = x["item"]
                new_key = it["restore_keys"][0]
                conn.execute(
                    "UPDATE tracker_dump SET dataset_key = ?, period_label = ?, status = ?, raw_payload_json = ? "
                    "WHERE id = ?",
                    (new_key, it["restore"].get("period"), it["restore"].get("status"),
                     _patched_payload(x["raw"], it["restore"], new_key), x["id"]))
    return report


def apply_plan(db: Any, plan: Dict[str, Any], apply: bool = False, backup_to: Optional[Path] = None):
    """apply_to_connection on the app's own raw-payload connection (it commits when the block ends)."""
    with db._connect_raw() as conn:
        return apply_to_connection(conn, plan, apply=apply, backup_to=backup_to)


# ── Admin-PC startup hook ────────────────────────────────────────────────────────
def repair_dir() -> Path:
    from core.sgt.sgt_shadow import SERA_DATA_DIR_NAME
    return Path.home() / SERA_DATA_DIR_NAME / "sgt_repair"


def run_pending(db: Any, base: Optional[Path] = None, echo=print) -> int:
    """Plans copied into <base>/plans/ are checked on every admin-PC start. Without the file
    <base>/APPLY it only writes a dry-run report to <base>/reports/; with it, applies, backs up the rows
    to <base>/backups/ and moves the plan to <base>/done/. Returns the number of plans handled."""
    base = Path(base) if base else repair_dir()
    plans = sorted((base / "plans").glob("*.json")) if (base / "plans").is_dir() else []
    if not plans:
        return 0
    do_apply = (base / "APPLY").exists()
    handled = 0
    for pf in plans:
        try:
            plan = json.loads(pf.read_text(encoding="utf-8"))
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report = apply_plan(db, plan, apply=do_apply,
                                backup_to=(base / "backups" / f"{pf.stem}_{stamp}.json") if do_apply else None)
            rdir = base / ("done" if do_apply else "reports")
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / f"{pf.stem}_{stamp}_{'applied' if do_apply else 'dryrun'}.json").write_text(
                json.dumps({"plan": plan.get("pc"), "applied": do_apply, "report": report,
                            "review": plan.get("review", [])}, ensure_ascii=False, indent=1), encoding="utf-8")
            if do_apply:
                pf.replace(base / "done" / pf.name)
            echo(f"[SGT repair] {pf.name}: {'applied' if do_apply else 'dry run'}, {len(report)} item(s)")
            handled += 1
        except Exception as e:                                  # never stop the app's start
            echo(f"[SGT repair] {pf.name} skipped: {e}")
    return handled
