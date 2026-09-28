"""
tools/sgt_i_uia_node_bench.py - time the cached UIA node read against today's line reader (W2-1)
================================================================================================
Opens local test pages in a real Edge window (Playwright; it only loads them - nothing is
clicked or typed) and reads each one, alternately, with
  * core/vsdc/vsdc_uia_text.read_page_text   (today: element-by-element, include_selection=True)
  * core/sgt_i/uia_nodes.read_page_nodes     (one FindAllBuildCache, control view and raw view)
and prints median / min milliseconds, node and line counts, and whether the lines rebuilt from
the nodes are identical to today's (the blueprint 14.2 gate).

Pages: two tests/*.html portal mocks, plus a generated heavy page - a 300-row, 7-column table of
fictional values (the "heavy table page" of blueprint section 7).

Usage:
    python tools/sgt_i_uia_node_bench.py [repeats]
"""

import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("VSDC_ALLOW_LOCAL_TEST", "1")

from playwright.sync_api import sync_playwright

from core.sgt_i import uia_nodes
from core.vsdc import vsdc_uia_text
from tools.vsdc_x_html_sample_test import find_hwnd_by_title_substring

TIMEOUT_SEC = 20.0
NORMAL_PAGES = ("tests/test_page_personal_info.html", "tests/test_page_gst_submission.html")


def heavy_page_html(rows: int = 300) -> str:
    """A fictional heavy dataset table: header, a few labels, then rows x 7 cells."""
    cells = []
    for r in range(1, rows + 1):
        cells.append(
            f"<tr><td>{r}</td><td>27ZZZZZ{r:04d}Z1Z{r % 10}</td><td>Test Trader {r}</td>"
            f"<td>INV-{r:05d}</td><td>0{1 + r % 9}-04-2026</td><td>{r * 137 % 99991}.00</td>"
            f"<td>{'Accepted' if r % 3 else 'Pending'}</td></tr>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>SGT Heavy Table Bench</title></head>"
        "<body><header><h1>Test Portal</h1><nav><a href='#'>Home</a> <a href='#'>Datasets</a></nav></header>"
        "<main><h2>Invoice details</h2><label>Period <input value='April 2026' readonly></label>"
        "<table><thead><tr><th>#</th><th>GSTIN</th><th>Trade name</th><th>Invoice</th><th>Date</th>"
        "<th>Taxable value</th><th>Status</th></tr></thead><tbody>" + "".join(cells) +
        "</tbody></table></main><footer>Fictional test page</footer></body></html>")


def _ms(fn):
    t0 = time.perf_counter()
    out = fn()
    return (time.perf_counter() - t0) * 1000.0, out


def bench_page(hwnd: int, label: str, repeats: int) -> None:
    old_ms, ctl_ms, raw_ms = [], [], []
    old = ctl = raw = None
    for i in range(repeats + 2):              # the first two reads warm Edge's accessibility tree
        t_old, old = _ms(lambda: vsdc_uia_text.read_page_text(hwnd, TIMEOUT_SEC, include_selection=True))
        t_ctl, ctl = _ms(lambda: uia_nodes.read_page_nodes(hwnd, TIMEOUT_SEC))
        t_raw, raw = _ms(lambda: uia_nodes.read_page_nodes(hwnd, TIMEOUT_SEC, raw_view=True))
        if i >= 2:
            old_ms.append(t_old)
            ctl_ms.append(t_ctl)
            raw_ms.append(t_raw)
    old_lines = old["lines"]
    print(f"\n== {label}  (hwnd={hwnd}, {repeats} reads each)")
    print(f"  line reader today : median {statistics.median(old_ms):7.1f} ms  min {min(old_ms):7.1f}  "
          f"lines {len(old_lines)}")
    for view, times, res in (("control", ctl_ms, ctl), ("raw", raw_ms, raw)):
        docs = res["docs"]
        lines = uia_nodes.lines_from_nodes(docs, include_selection=True)
        n_nodes = sum(len(d) for d in docs)
        same = "IDENTICAL" if lines == old_lines else f"DIFFER ({len(lines)} lines)"
        print(f"  node read {view:8s}: median {statistics.median(times):7.1f} ms  min {min(times):7.1f}  "
              f"nodes {n_nodes}  lines {same}")
        if lines != old_lines:
            for k, (a, b) in enumerate(zip(old_lines, lines)):
                if a != b:
                    print(f"    first difference at line {k}: today={a[:60]!r} nodes={b[:60]!r}")
                    break
    nodes = ctl["docs"][0] if ctl["docs"] else []
    kinds = {
        "headings": sum(1 for n in nodes if n.get("heading")),
        "landmarks": sum(1 for n in nodes if n.get("landmark")),
        "grid cells": sum(1 for n in nodes if n.get("grid")),
        "values": sum(1 for n in nodes if n.get("value")),
        "with rect": sum(1 for n in nodes if n.get("rect")),
        "max depth": max((n["depth"] for n in nodes), default=0),
    }
    print("  node structure    : " + ", ".join(f"{k} {v}" for k, v in kinds.items()))


def main():
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    heavy = os.path.join(tempfile.gettempdir(), "sgt_heavy_table_bench.html")
    with open(heavy, "w", encoding="utf-8") as f:
        f.write(heavy_page_html())
    pages = [os.path.abspath(p) for p in NORMAL_PAGES] + [heavy]

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        for path in pages:
            page.goto("file:///" + path.replace(os.sep, "/"))
            page.wait_for_timeout(800)
            title = page.title()
            hwnd, _ = find_hwnd_by_title_substring(title)
            if not hwnd:
                print(f"!! no window titled {title!r} - skipped")
                continue
            bench_page(hwnd, os.path.basename(path), repeats)
        browser.close()


if __name__ == "__main__":
    main()
