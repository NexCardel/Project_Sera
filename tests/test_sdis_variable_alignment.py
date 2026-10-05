"""
SDIS Part F: the variable_alignment state (core/sdis/memory.py, compare.py)
===========================================================================
A node whose verdict would be 'same for all clients' becomes 'variable_alignment'
when:
  * labels.value_type(text) != 'label' (not ending in ':')
  * it is not composite
  * its ctype is not a choice control (keys.CHOICE_CTYPES)
  * at least one OTHER node with the same shape has verdict 'differs between clients'

A rejected hook accepts signatures (link, shape, text) to turn a flagged node
back to 'same for all clients'.
"""

import json
from pathlib import Path

import pytest

from core.sdis import align, keys, memory
from core.sdis.keys import CT_COMBOBOX, CT_RADIO
from tools.pre_dev.class_diff import compare

FIX = Path(__file__).resolve().parent / "class_diff_align"


def _load(c: str):
    return json.loads((FIX / f"client_{c}.json").read_text(encoding="utf-8"))


def test_fixtures_ab_give_variable_alignment_for_filed():
    """Fixtures A/B give variable_alignment for the shared 'Filed' status cells."""
    fa = _load("A")
    fb = _load("B")
    fla = keys.flatten(fa)
    flb = keys.flatten(fb)

    mem = memory.PageMemory("test_page", n_promote=2)
    mem.add(fla, "client_A")
    mem.add(flb, "client_B")

    summary = mem.summary()
    assert summary[("confirmed", "variable_alignment")] > 0

    # Find the 'Filed' nodes in memory
    filed_nodes = [
        (i, nd) for i, nd in enumerate(mem.nodes)
        if nd.get("text") == "Filed" and mem.confirmed(i)
    ]
    assert len(filed_nodes) >= 2

    # In row 1, Status differs ('Not filed' vs 'Filed') -> differs between clients
    # In other rows, Status is 'Filed' on both sides -> variable_alignment
    verdicts = [mem.verdict(i) for i, _ in filed_nodes]
    assert "differs between clients" in verdicts
    assert "variable_alignment" in verdicts


def test_rejected_signature_turns_variable_alignment_back():
    """A rejected signature (link, shape, text) turns variable_alignment back to 'same for all clients'."""
    fa = _load("A")
    fb = _load("B")
    fla = keys.flatten(fa)
    flb = keys.flatten(fb)

    mem = memory.PageMemory("test_page", n_promote=2)
    mem.add(fla, "client_A")
    mem.add(flb, "client_B")

    # Find a confirmed variable_alignment node
    va_nids = [i for i in range(len(mem.nodes)) if mem.confirmed(i) and mem.verdict(i) == "variable_alignment"]
    assert va_nids
    target_nid = va_nids[0]
    target_nd = mem.nodes[target_nid]
    target_shape = target_nd["shape"]
    target_text = target_nd["text"]

    sig = ("test_page", target_shape, target_text)

    # Rejection via PageMemory attribute / init
    mem_rej = memory.PageMemory("test_page", n_promote=2, rejected={sig})
    mem_rej.add(fla, "client_A")
    mem_rej.add(flb, "client_B")
    assert mem_rej.verdict(target_nid) == "same for all clients"

    # Rejection passed to verdict() directly
    assert mem.verdict(target_nid, rejected={sig}) == "same for all clients"


def test_compare_flat_marks_variable_alignment_and_keeps_labels():
    """compare_flat marks FIXED element sharing shape with VARIABLE as VARIABLE_ALIGNMENT,

    and 'shared' still includes it so table row labels like 'Cash ledger' stay labels.
    """
    fa = _load("A")
    fb = _load("B")
    fla = keys.flatten(fa)
    flb = keys.flatten(fb)

    rows = compare.compare_flat(flb, fla)
    statuses = {r["example_value"]: r["status"] for r in rows}

    # 'Filed' in the table rows becomes VARIABLE_ALIGNMENT
    filed_statuses = [r["status"] for r in rows if r["example_value"] == "Filed"]
    assert compare.VARIABLE_ALIGNMENT in filed_statuses

    # 'Cash ledger' becomes VARIABLE_ALIGNMENT
    assert statuses.get("Cash ledger") == compare.VARIABLE_ALIGNMENT
    assert statuses.get("Credit ledger") == compare.VARIABLE_ALIGNMENT

    # '404' still gets its correct label 'Cash ledger / IGST'
    row_404 = next(r for r in rows if r["example_value"] == "404")
    assert row_404["label"] == "Cash ledger / IGST"


def test_compare_flat_rejected_hook():
    """compare_flat with rejected hook leaves rejected node as FIXED."""
    fa = _load("A")
    fb = _load("B")
    fla = keys.flatten(fa)
    flb = keys.flatten(fb)

    # Find Cash ledger shape
    cash_elem = next(e for e in flb if e["text"] == "Cash ledger")
    cash_shape = align.shape(cash_elem)
    sig = ("", cash_shape, "Cash ledger")

    rows = compare.compare_flat(flb, fla, rejected={sig})
    cash_row = next(r for r in rows if r["example_value"] == "Cash ledger")
    assert cash_row["status"] == compare.FIXED


def test_exclusions_do_not_become_variable_alignment():
    """Labels ending in ':', composites, and choice controls are excluded from variable_alignment."""
    # Node 0: label ending in ':'
    # Node 1: composite
    # Node 2: choice control (ComboBox)
    # Node 3: regular value sharing shape with Node 4
    # Node 4: differs between clients
    shape_shared = "D1 / Text"
    flat_a = [
        {"key": "D1 / Text[1]", "text": "Label :", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
        {"key": "D1 / Text[2]", "text": "Composite Text", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
        {"key": "D1 / Combo[1]", "text": "Option 1", "type": "ComboBox", "depth": 0, "parent": -1, "node": {"ctype": CT_COMBOBOX}},
        {"key": "D1 / Text[3]", "text": "Fixed Val", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
        {"key": "D1 / Text[4]", "text": "A Val", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
    ]
    flat_b = [
        {"key": "D1 / Text[1]", "text": "Label :", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
        {"key": "D1 / Text[2]", "text": "Composite Text", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
        {"key": "D1 / Combo[1]", "text": "Option 1", "type": "ComboBox", "depth": 0, "parent": -1, "node": {"ctype": CT_COMBOBOX}},
        {"key": "D1 / Text[3]", "text": "Fixed Val", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
        {"key": "D1 / Text[4]", "text": "B Val", "type": "Text", "depth": 0, "parent": -1, "node": {"ctype": 50020}},
    ]

    mem = memory.PageMemory("page", n_promote=2)
    mem.add(flat_a, "client_A")
    mem.add(flat_b, "client_B")
    mem.nodes[1]["composite"] = True

    # Node 4 differs
    assert mem.verdict(4) == "differs between clients"

    # Node 3 is regular and shares shape with Node 4 -> variable_alignment
    assert mem.verdict(3) == "variable_alignment"

    # Node 0 ends in ':' -> stays same for all clients
    assert mem.verdict(0) == "same for all clients"

    # Node 1 is composite -> composite
    assert mem.verdict(1) == "composite"

    # Node 2 is ComboBox choice control -> same for all clients
    assert mem.verdict(2) == "same for all clients"
