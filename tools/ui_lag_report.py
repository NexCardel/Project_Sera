"""
tools/ui_lag_report.py - what makes Sera's window lag, ranked (reads logs/ui_lag.log from core/ui_lag.py)
=======================================================================================================
    python tools/ui_lag_report.py                      this PC's log (and its rotated copies)
    python tools/ui_lag_report.py --log D:\\ui_lag.log  a log copied from an office PC
    python tools/ui_lag_report.py --days 3 --all       last 3 days, including minutes the window was not in front
    python tools/ui_lag_report.py --out report.txt

Prints, for the minutes Sera's window was in use (in front): lag per day (p95 and worst), hitches per
hour, the causes ranked by the time they blocked the window, the threads that were busy during those
hitches, and the usual stack of each top cause. Only code locations and times are in the log.
"""

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_LOG = Path.home() / "AmanAssociates_Sera" / "logs" / "ui_lag.log"
_CAUSE_RE = re.compile(r"^(.*) (\d+)ms(?:/(\d+)x)?$")
_KV_RE = re.compile(r"(\w+)=([^\s]+)")


def log_files(path: Path) -> List[Path]:
    files = [path.with_name(f"{path.name}.{i}") for i in (3, 2, 1)] + [path]
    return [p for p in files if p.exists()]


def _causes(text: str) -> List[Tuple[str, float, int]]:
    out = []
    for part in text.split(" | "):
        m = _CAUSE_RE.match(part.strip())
        if m:
            out.append((m.group(1), float(m.group(2)), int(m.group(3) or 1)))
    return out


def parse(lines: List[str]) -> Tuple[List[dict], List[dict], int]:
    minutes, hitches, runs = [], [], 0
    for ln in lines:
        ln = ln.rstrip("\n")
        if len(ln) < 20:
            continue
        try:
            at = datetime.strptime(ln[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        rest = ln[20:]
        if rest.startswith("START"):
            runs += 1
        elif rest.startswith("MIN "):
            head, _, top = rest[4:].partition(" top= ")
            rec = {k: v for k, v in _KV_RE.findall(head)}
            rec["at"] = at
            rec["top"] = _causes(top) if top else []
            minutes.append(rec)
        elif rest.startswith("HITCH "):
            body = rest[6:]
            stack = ""
            if " stack= " in body:
                body, stack = body.split(" stack= ", 1)
            busy = ""
            if " busy= " in body:
                body, busy = body.split(" busy= ", 1)
            head, _, main = body.partition(" main= ")
            rec = {k: v for k, v in _KV_RE.findall(head)}
            rec.update(at=at, main=_causes(main), busy=_causes(busy), stack=stack)
            hitches.append(rec)
    return minutes, hitches, runs


def _f(rec: dict, key: str) -> float:
    try:
        return float(str(rec.get(key, "0")).rstrip("%"))
    except ValueError:
        return 0.0


def report(minutes: List[dict], hitches: List[dict], runs: int, include_idle: bool = False) -> str:
    out: List[str] = []
    use = [m for m in minutes if include_idle or m.get("in_use") == "yes"]
    huse = [h for h in hitches if include_idle or h.get("in_use") == "yes"]
    if not minutes:
        return "No ui_lag.log records yet. Run Sera for a while (the meter writes once a minute when it lags)."
    out.append(f"Period: {minutes[0]['at']:%Y-%m-%d %H:%M} to {minutes[-1]['at']:%Y-%m-%d %H:%M}, "
               f"{runs} app start(s), {len(minutes)} minutes logged, {len(use)} with the window in use"
               + ("" if not include_idle else " (all minutes counted)"))
    out.append("Note: quiet minutes are written only every 10 minutes, so the per-hour figures count logged minutes.\n")

    out.append("Lag per day (window in use)  -  p95 of ticks: under 50 ms feels instant, over 150 ms feels slow")
    out.append(f"  {'day':10s} {'minutes':>7s} {'p95 med':>8s} {'p95 worst':>9s} {'max':>7s} {'>=100ms/h':>9s} "
               f"{'>=250ms/h':>9s} {'>=1s/h':>7s} {'blocked/h':>9s} {'cpu med':>7s}")
    by_day: Dict[str, List[dict]] = defaultdict(list)
    for m in use:
        by_day[m["at"].strftime("%Y-%m-%d")].append(m)
    for day, ms in sorted(by_day.items()):
        hours = max(len(ms) / 60.0, 1 / 60.0)
        p95 = [_f(m, "p95") for m in ms]
        out.append(f"  {day:10s} {len(ms):7d} {statistics.median(p95):8.0f} {max(p95):9.0f} "
                   f"{max(_f(m, 'max') for m in ms):7.0f} {sum(_f(m, 'hitch100') for m in ms) / hours:9.1f} "
                   f"{sum(_f(m, 'hitch250') for m in ms) / hours:9.1f} {sum(_f(m, 'hitch1s') for m in ms) / hours:7.1f} "
                   f"{sum(_f(m, 'blocked_ms') for m in ms) / 1000 / hours:8.1f}s "
                   f"{statistics.median(_f(m, 'cpu') for m in ms):6.1f}%")

    total: Dict[str, float] = defaultdict(float)
    count: Counter = Counter()
    for m in use:
        for key, ms, n in m["top"]:
            total[key] += ms
            count[key] += n
    blocked = sum(_f(m, "blocked_ms") for m in use) or 1.0
    out.append("\nWhat blocked the window, ranked (each minute's top 5 causes, hitches of 100 ms and over)")
    for i, (key, ms) in enumerate(sorted(total.items(), key=lambda kv: -kv[1])[:20], 1):
        out.append(f"  {i:2d}. {ms / 1000:7.1f} s  {100 * ms / blocked:5.1f}%  {count[key]:5d} hitches  {key}")

    busy: Dict[str, float] = defaultdict(float)
    for h in huse:
        for key, ms, _n in h["busy"]:
            busy[key] += ms
    if busy:
        out.append("\nOther threads active during hitches of 250 ms and over (GIL / CPU competition)")
        for key, ms in sorted(busy.items(), key=lambda kv: -kv[1])[:12]:
            out.append(f"  {ms / 1000:7.1f} s  {key}")

    stacks: Dict[str, Counter] = defaultdict(Counter)
    for h in huse:
        if h["main"] and h["stack"]:
            stacks[h["main"][0][0]][h["stack"]] += 1
    top_keys = [k for k, _ms in sorted(total.items(), key=lambda kv: -kv[1])[:6] if k in stacks]
    if top_keys:
        out.append("\nUsual stack of the top causes (innermost first)")
        for key in top_keys:
            stack, n = stacks[key].most_common(1)[0]
            out.append(f"  {key}\n      {n}x: {stack}")

    slow = sorted(huse, key=lambda h: -_f(h, "ms"))[:10]
    if slow:
        out.append("\nLongest hitches")
        for h in slow:
            cause = h["main"][0][0] if h["main"] else "?"
            out.append(f"  {h['at']:%Y-%m-%d %H:%M:%S} {_f(h, 'ms'):6.0f} ms  {cause}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--days", type=float, default=0, help="only the last N days")
    ap.add_argument("--all", action="store_true", help="also minutes the window was not in front")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    lines: List[str] = []
    for p in log_files(a.log):
        lines.extend(p.read_text(encoding="utf-8", errors="replace").splitlines())
    minutes, hitches, runs = parse(lines)
    if a.days:
        since = datetime.now() - timedelta(days=a.days)
        minutes = [m for m in minutes if m["at"] >= since]
        hitches = [h for h in hitches if h["at"] >= since]
    text = report(minutes, hitches, runs, include_idle=a.all)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
        print(f"written to {a.out}")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
