"""tools/sgt_soak_report.py: the layer 1 live-soak table of blueprint 8.2, on fixture logs (fictional data)."""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import sgt_soak_report as sr

START = datetime(2026, 10, 15, 9, 0, 0)
END = START + timedelta(hours=5)
SUBMITTED = "Submitted (Not Verified)"
CLIENTS = ("ZZZZZ9999Z", "27ZZZZZ9999Z1Z5", "Test Trader Alpha")       # fictional PAN, GSTIN, name


def _arn(i: int) -> str:
    return f"AA151026{900001 + i:06d}Z"


def _beat(at, *, last_ms=60, reads=5, state="ok", abandoned=0):
    return (f"{at:%Y-%m-%d %H:%M:%S} beat ticks=170 errors=0 max_tick_ms=40 threads=22 front=msedge "
            f"health_state={state} sgt_reads=5 sgt_blind=0 sgt_dropped=0 uia_reads={reads} uia_scope_reads=20 "
            f"uia_max_ms={last_ms * 2} uia_last_ms={last_ms} uia_late=0 uia_abandoned={abandoned} uia_stuck=0\n")


def make_soak(root: Path, *, tail_ms=60, ws_end=95.0, extra_vsdc="", skip_beats=None, arns=6,
              statuses=None, drop=()) -> list:
    """A clean 5 h soak: a beat a minute, memory every 10 minutes, `arns` filings seen. Returns the ARNs."""
    logs, shadow = root / "logs", root / "sgt_shadow"
    logs.mkdir(parents=True)
    shadow.mkdir()
    beats = []
    for m in range(0, 5 * 60 + 1):
        at = START + timedelta(minutes=m)
        if skip_beats and skip_beats[0] <= m < skip_beats[1]:
            continue
        beats.append(_beat(at, last_ms=tail_ms if m >= 4 * 60 else 60))
    (logs / "vsdc.log").write_text("".join(beats) + extra_vsdc, encoding="utf-8")
    mem = []
    for m in range(0, 5 * 60 + 1, 10):
        ws = 80.0 + (ws_end - 80.0) * m / 300
        mem.append(f"{START + timedelta(minutes=m):%Y-%m-%d %H:%M:%S} +{m * 60:7.1f}s  ws {ws:6.1f} MB"
                   f"  (+0.1)  priv {ws + 20:6.1f} MB  peak {ws + 30:6.1f} MB  periodic\n")
    (logs / "memory.log").write_text("".join(mem), encoding="utf-8")
    out, events = [], []
    for i in range(arns):
        arn = _arn(i)
        out.append(arn)
        if i in drop:
            continue
        status = (statuses or {}).get(i, SUBMITTED)
        at = START + timedelta(minutes=20 + i * 45)
        events.append({"ts": at.isoformat(timespec="seconds"), "event": "dataset", "session": "s1",
                       "portal": "GST Portal", "change": "new", "record": "gst_return", "source": "uia",
                       "page": "https://return.gst.gov.in/returns/auth/gstr3b",
                       "values": {"form": "GSTR3B", "period": "October", "status": status, "arn": arn,
                                  "name": CLIENTS[2], "gstin": CLIENTS[1], "pan": CLIENTS[0]}})
    events.append({"ts": (START + timedelta(minutes=30)).isoformat(timespec="seconds"), "event": "session_end",
                   "session": "s1", "reason": "idle 4 hr", "stats": {"reads": 120, "ocr_reads": 3},
                   "payload": {"client_profile": {"pan": CLIENTS[0], "name": CLIENTS[2]}, "datasets": []}})
    events.append({"ts": (START + timedelta(minutes=31)).isoformat(timespec="seconds"),
                   "event": "late_absorbed_after_end", "session": "s1", "page": "https://example.invalid/x"})
    (shadow / "sgt_shadow_2026-10-15.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return out


def run(root: Path, tmp_path: Path, arns=None) -> sr.Report:
    expected = None
    if arns is not None:
        f = tmp_path / "expected.txt"
        f.write_text("# test filings\n" + "\n".join(arns) + "\n\n", encoding="utf-8")
        expected = sr.read_expected(f)
    return sr.build_report(root, START, END, expected)


def verdicts(rep: sr.Report) -> dict:
    return {r.name.split(":")[0]: r.verdict for r in rep.rows}


def test_pass_fail_from_fixture_logs(tmp_path):
    good = tmp_path / "good"
    arns = make_soak(good)
    rep = run(good, tmp_path, arns)
    v = verdicts(rep)
    assert rep.ok, sr.format_report(rep)
    for name in ("data", "missed scripted submissions", "p95 read time", "working set growth", "stalls",
                 "beat gaps over 3 min", "abandoned workers"):
        assert v[name] == sr.PASS, (name, sr.format_report(rep))

    cases = {
        "p95 read time": dict(tail_ms=400),
        "working set growth": dict(ws_end=210.0),
        "stalls": dict(extra_vsdc=f"{START + timedelta(hours=2):%Y-%m-%d %H:%M:%S} capture worker STALLED: "
                                  "no tick finished for 31 s - stacks in hang.log\n"),
        "abandoned workers": dict(extra_vsdc=f"{START + timedelta(hours=3):%Y-%m-%d %H:%M:%S} reader content "
                                             "read stuck 20 s - replaced its worker (1 this run, 1 still stuck)\n"),
        "beat gaps over 3 min": dict(skip_beats=(100, 110)),
    }
    for row, kwargs in cases.items():
        root = tmp_path / row.replace(" ", "_")
        arns = make_soak(root, **kwargs)
        rep = run(root, tmp_path, arns)
        assert verdicts(rep)[row] == sr.FAIL, (row, sr.format_report(rep))
        assert not rep.ok

    root = tmp_path / "missed"
    arns = make_soak(root, drop=(2,), statuses={4: "Draft"})
    rep = run(root, tmp_path, arns)
    row = next(r for r in rep.rows if r.name == "missed scripted submissions")
    assert row.verdict == sr.FAIL
    assert "1 missed, 1 under-stated of 6" in row.value
    assert "lines 3" in row.value and "lines 5" in row.value


def test_empty_window_and_missing_expected_do_not_pass_silently(tmp_path):
    empty = tmp_path / "empty"
    (empty / "logs").mkdir(parents=True)
    rep = sr.build_report(empty, START, END, None)
    assert verdicts(rep)["data"] == sr.FAIL and not rep.ok
    root = tmp_path / "noexp"
    make_soak(root)
    rep = sr.build_report(root, START, END, None)
    assert verdicts(rep)["missed scripted submissions"] == sr.NA
    assert rep.ok and "could not be checked" in sr.format_report(rep)


def test_a_beat_gap_with_an_app_restart_is_counted_apart(tmp_path):
    restart = (f"{START + timedelta(minutes=101):%Y-%m-%d %H:%M:%S} capture worker stopped\n"
               f"{START + timedelta(minutes=108):%Y-%m-%d %H:%M:%S} capture worker started\n")
    make_soak(tmp_path / "r", extra_vsdc=restart, skip_beats=(100, 110))
    rep = sr.build_report(tmp_path / "r", START, END, None)
    row = next(r for r in rep.rows if r.name.startswith("beat gaps"))
    assert row.verdict == sr.PASS and "+1 around an app restart" in row.value


def test_the_rolled_log_is_read_too(tmp_path):
    root = tmp_path / "rolled"
    make_soak(root)
    log = root / "logs" / "vsdc.log"
    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    (root / "logs" / "vsdc.log.1").write_text("".join(lines[:150]), encoding="utf-8")
    log.write_text("".join(lines[150:]), encoding="utf-8")
    assert verdicts(sr.build_report(root, START, END, None))["data"] == sr.PASS


def test_report_holds_no_identifiers(tmp_path, capsys):
    root = tmp_path / "priv"
    arns = make_soak(root, drop=(1,))
    exp = tmp_path / "arns.txt"
    exp.write_text("\n".join(arns) + "\n", encoding="utf-8")
    code = sr.main(["--root", str(root), "--start", f"{START:%Y-%m-%d %H:%M}", "--end", f"{END:%Y-%m-%d %H:%M}",
                    "--expected", str(exp)])
    out = capsys.readouterr().out
    assert code == 1 and "FAIL" in out
    for secret in (*arns, *CLIENTS, "return.gst.gov.in", "example.invalid", str(tmp_path)):
        assert secret.lower() not in out.lower()
    assert not re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", out)      # GSTIN
    assert not re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", out)                            # PAN
    assert not re.search(r"\b[A-Z]{2}\d{11,14}[A-Z0-9]?\b", out)                    # ARN
    assert "lines 2" in out


def test_bad_arguments_exit_2(tmp_path, capsys):
    assert sr.main(["--root", str(tmp_path), "--start", "not a date"]) == 2
    assert sr.main(["--root", str(tmp_path), "--start", "2026-10-15 10:00", "--end", "2026-10-15 09:00"]) == 2
    assert sr.main(["--root", str(tmp_path), "--hours", "1", "--expected", str(tmp_path / "missing.txt")]) == 2
    capsys.readouterr()


def test_soak_page_shows_one_fictional_arn_per_toast():
    import sgt_i_uia_node_bench as bench
    arns = bench.soak_arns(5, datetime(2026, 10, 15))
    assert len(arns) == 60 and len(set(arns)) == 60
    assert all(re.fullmatch(r"AA\d{12}Z", a) for a in arns)
    html = bench.soak_page_html(arns)
    assert all(a in html for a in arns)
    assert f"ROW_MS={bench.SOAK_ROW_SEC * 1000}" in html and f"TOAST_EVERY_MS={bench.SOAK_TOAST_EVERY_SEC * 1000}" in html
    assert bench.SOAK_ROW_SEC == 60 and bench.SOAK_TOAST_EVERY_SEC == 300
    assert "<input" not in html and "password" not in html.lower()
