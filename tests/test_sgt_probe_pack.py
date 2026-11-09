"""tools/sgt_probe_pack.py - the WP0 measurements tool. Fakes and fictional data only: no window,
no browser and no screen is touched here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import pytest
from PIL import Image

import sgt_probe_pack as pp

# Fictional identifiers (shape only).
PAN = "ABCDE1234F"
GSTIN = "27ABCDE1234F1Z5"
ARN = "AA2709251234567"


def _cell_frame(*boxes):
    """A 320x320 grey frame = 32x32 cells of 10x10 px; each box is (col, row, cols, rows, grey)."""
    img = Image.new("L", (320, 320), 0)
    for col, row, cols, rows, grey in boxes:
        img.paste(grey, (col * 10, row * 10, (col + cols) * 10, (row + rows) * 10))
    return img


def test_report_lines_hold_no_identifiers(tmp_path):
    echoed = []
    rep = pp.Report(tmp_path / "logs" / "probe_pack_test.txt", echo=echoed.append)
    rep.section("doc-url")
    rep.line(f"sample | Taxpayer {PAN} registered as {GSTIN}")
    rep.line(f"    | Acknowledgement {ARN} generated, ref 123456789012")
    t0 = 100.0
    seen = frozenset({ARN})
    for ln in pp.arn_visibility([(t0 + 1, seen), (t0 + 2, seen), (t0 + 3, frozenset())],
                                [(t0 + 1, frozenset())], [(t0 + 1.5, "toast", seen)], t0):
        rep.line(ln)
    rep.line(pp.describe_url("https://bank.example.org/account/9999?pan=" + PAN))

    text = (tmp_path / "logs" / "probe_pack_test.txt").read_text(encoding="utf-8")
    for secret in (PAN, GSTIN, ARN, "123456789012", "bank.example.org", "account/9999"):
        assert secret not in text
        assert not any(secret in e for e in echoed)
    assert "ARN-1: UIA: first +1.0s, visible 1.00s" in text
    assert "OCR: never seen" in text
    assert "FlashWatcher: caught +1.5s" in text


def test_gate_counts_from_fake_frames():
    caret = (31, 31, 1, 1, 255)
    toast = (0, 20, 3, 1, 255)
    faint = (0, 25, 10, 1, 16)           # one grey level apart: under the 2-level delta
    modal = (0, 0, 10, 10, 255)
    frames = [
        _cell_frame(),
        _cell_frame(caret),
        _cell_frame(caret, toast),
        _cell_frame(caret, toast, faint),
        _cell_frame(caret, toast, faint, modal),
    ]
    sigs = [pp.frame_signature(f) for f in frames]
    assert all(len(s) == pp.GATE_SIZE * pp.GATE_SIZE for s in sigs)
    assert [pp.cells_changed(a, b) for a, b in zip(sigs, sigs[1:])] == [1, 3, 0, 100]
    assert pp.cells_changed(sigs[0], None) == pp.GATE_SIZE * pp.GATE_SIZE
    assert pp.frame_signature(None) is None

    stats = pp.GateStats()
    for n, (a, b) in enumerate(zip(frames, frames[1:])):
        stats.add(n * 0.2, 8.0, 0.5, pp.cells_changed(pp.frame_signature(a), pp.frame_signature(b)),
                  pp.legacy_hash(a) != pp.legacy_hash(b))
    stats.skipped = 2
    total = "\n".join(stats.total_lines())
    assert "frames=4" in total
    assert "0:1  1:1  2:0  3-4:1  5-7:0  8-15:0  16-63:0  64-1024:1" in total
    assert f">= GATE_MIN_CELLS({pp.GATE_MIN_CELLS}): 2" in total
    assert f">= FLASH_MIN_CELLS({pp.FLASH_MIN_CELLS}): 1" in total
    assert "legacy 32x32 hash() fired: 4" in total
    assert "frame tick ms (screenshot+signature) median=8.5" in total
    assert "ticks with no portal page in front: 2" in total
    assert "+0.6s: 100" in total


def test_serialisation_verdict_reads_the_ratio():
    serial = [{"seq_sum_ms": 800.0, "conc_wall_ms": 790.0}] * 3
    parallel = [{"seq_sum_ms": 800.0, "conc_wall_ms": 420.0}] * 3
    light = [{"seq_sum_ms": 60.0, "conc_wall_ms": 55.0}] * 3
    assert "SERIALISED" in "\n".join(pp.serialisation_verdict(serial))
    assert "PARALLEL" in "\n".join(pp.serialisation_verdict(parallel))
    assert "too light" in "\n".join(pp.serialisation_verdict(light))


def test_switch_counter_counts_per_minute():
    c = pp.SwitchCounter()
    c.feed(1, 10, True, "a")
    c.feed(2, 10, True, "b")             # tab change
    c.feed(3, 11, True, "x")             # other browser window
    c.feed(4, 99, False, None)           # out of the browser
    c.feed(65, 10, True, "b")            # minute 2: back into a browser
    out = "\n".join(c.lines())
    assert "minute 1: foreground changes=2  between browser windows=1  into/out of a browser=1  tab changes=1" in out
    assert "minute 2: foreground changes=1" in out
    assert "TOTAL foreground changes: 3" in out


def test_read_summary_flags_drift():
    rows = [{"status": "ok", "ms_text": 100.0 + i * 10, "ms_nodes": 50.0, "elements": 900, "lines_text": 300,
             "identical": True, "ws_mb": 400.0 + i} for i in range(12)]
    rows.append({"status": "timeout", "ms_text": 20000.0, "ms_nodes": 20000.0, "elements": 0, "lines_text": 0,
                 "identical": False, "ws_mb": None})
    out = "\n".join(pp.read_summary(rows))
    assert "samples=13  ok=12" in out and "timeout=1" in out
    assert "identical lines: 12/12" in out
    assert "first quarter" in out and "last quarter" in out


def test_urls_agree_ignores_scheme_and_query():
    assert pp.urls_agree("services.gst.gov.in/x/y", "https://services.gst.gov.in/x/y?q=1")
    assert not pp.urls_agree("services.gst.gov.in/x", "services.gst.gov.in/y")


def test_help_lists_every_probe(capsys):
    with pytest.raises(SystemExit) as stop:
        pp.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for name in ("doc-url", "read-timing", "gate", "toast", "switches"):
        assert name in out
