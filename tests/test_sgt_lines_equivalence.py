"""The 14.2 gate itself: today's line reader and the node-rebuilt lines must be identical.
Runs tools/sgt_lines_equivalence.py's synthetic fixtures - a fake UIA layer, no browser or COM -
so this is a real side-by-side of vsdc_uia_text._collect_descendant_lines and
core.sgt_i.uia_nodes.lines_from_nodes over the same element tree, not just hand-checked
expectations."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import sgt_lines_equivalence as eq


def test_synthetic_pages_exist():
    assert len(eq.synthetic_pages()) >= 4


def test_node_lines_match_today_with_selection():
    mismatches = eq.check_synthetic(include_selection=True)
    assert mismatches == []


def test_node_lines_match_today_without_selection():
    mismatches = eq.check_synthetic(include_selection=False)
    assert mismatches == []


def test_corpus_mode_reports_no_comparable_records_yet(tmp_path):
    # No node dumps exist until W2-4 - this must report that plainly, never crash or false-pass.
    seen, no_nodes, mismatches = eq.check_corpus(tmp_path)
    assert seen == no_nodes == 0
    assert mismatches == []
