"""
tests/test_sdis_labels.py - statuses and labels from memory (SDIS W3-1, Part I)
================================================================================
PageMemory keeps each client's last view; memory.status(nid) maps verdicts to the dialog's statuses
and memory.label(nid) finds the label in the latest view that holds the node: a real table's column
(and a matrix's row) from core.sdis.tables, else the screen-box rules over the texts clients share.
Fixtures are the fictional pages in tests/class_diff_align/ and tests/class_diff_tables/.
"""

import json
import sys
from pathlib import Path

import pytest

from core.sdis import keys, labels, memory as memory_mod, tables
from core.sdis.memory import PageMemory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools" / "pre_dev" / "class_diff"))

import compare  # noqa: E402
from test_class_diff_align import ALIGN_BROWSERS, EXPECTED, _load  # noqa: E402

FIX_ALIGN = HERE / "class_diff_align"
FIX_TABLES = HERE / "class_diff_tables"
BROWSERS = [b for b in ("edge", "chrome", "firefox") if (FIX_TABLES / f"tables_{b}.json").exists()]


def _client(c, browser="edge"):
    return keys.flatten(_load(c, browser))


def _memory(order, browser="edge"):
    mem = PageMemory("fictional/page", n_promote=2)
    for c in order:
        mem.add(_client(c, browser), c)
    return mem


def _nids(mem, client, text):
    flat, nid_of = mem.views[client]
    return [nid_of[i] for i, e in enumerate(flat) if e["text"] == text]


@pytest.mark.parametrize("browser", ALIGN_BROWSERS)
@pytest.mark.parametrize("order", ["AB", "BA"])
def test_every_expected_value_gets_its_label_from_memory(order, browser):
    mem = _memory(order, browser)
    wrong = {}
    for value, want in EXPECTED.items():
        got = {mem.label(n).strip() for n in _nids(mem, "B", value)}
        if want not in got:
            wrong[value] = got
    assert not wrong


@pytest.mark.parametrize("browser", ALIGN_BROWSERS)
def test_expected_values_are_data_not_template(browser):
    mem = _memory("AB", browser)
    for value in EXPECTED:
        for nid in _nids(mem, "B", value):
            assert mem.status(nid) not in ("fixed", "retired", "composite")


@pytest.mark.parametrize("browser", ALIGN_BROWSERS)
def test_template_texts_are_fixed_and_unknown_nodes_have_no_label(browser):
    mem = _memory("AB", browser)
    assert {mem.status(n) for n in _nids(mem, "B", "Taxpayer details")} == {"fixed"}
    assert mem.label(10 ** 6) == ""


def test_node_is_labelled_from_the_latest_view_that_holds_it():
    mem = _memory("BA")
    nid = _nids(mem, "B", "BETA FOODS")[0]
    client, _i = mem._view_of(nid)
    assert client == "A"
    assert mem.label(nid).strip() == "Legal Name :"


# ── real tables: column names, matrix row names ───────────────────────────────

def _table_flat(browser):
    """The fixture's flat list and its tables, each cell's "node" turned into an index of that list."""
    docs = tables.load_docs(FIX_TABLES / f"tables_{browser}.json")
    tbs = tables.extract(docs)
    for tb in tbs:
        base = sum(len(d) for d in docs[:tb["doc"]])
        for c in tb["cells"]:
            c["node"] += base
    return keys.flatten({"docs": docs}), tbs


def _by_id(tbs, tid):
    return next(tb for tb in tbs if tb["id"] == tid)


@pytest.mark.parametrize("browser", BROWSERS)
def test_ledger_body_cells_get_two_level_column_names(browser):
    flat, tbs = _table_flat(browser)
    got = labels.cell_labels(flat)
    tb = _by_id(tbs, "t_ledger")
    body = [c for c in tb["cells"] if c["row"] >= tb["header_rows"]]
    assert len(body) == 8
    want = ["Ledger", "Tax / IGST", "Tax / CGST", "Tax / SGST"]
    assert {got[c["node"]] for c in body} == set(want)
    for c in body:
        assert got[c["node"]] == want[c["col"]]


@pytest.mark.parametrize("browser", BROWSERS)
def test_matrix_cells_get_row_and_column(browser):
    flat, tbs = _table_flat(browser)
    got = labels.cell_labels(flat)
    tb = _by_id(tbs, "t_matrix")
    assert tb["header_cols"] == 1
    cells = {(c["row"], c["col"]): c for c in tb["cells"]}
    assert got[cells[(1, 1)]["node"]] == "Returns filed / Apr 2026"
    assert got[cells[(1, 3)]["node"]] == "Returns filed / Q1 total"
    assert got[cells[(2, 2)]["node"]] == "Pending / May 2026"
    assert (1, 0) in cells and cells[(1, 0)]["node"] not in got        # the row label itself


@pytest.mark.parametrize("browser", BROWSERS)
def test_header_cells_and_unnamed_columns_are_left_to_the_box_rules(browser):
    flat, tbs = _table_flat(browser)
    got = labels.cell_labels(flat)
    tb = _by_id(tbs, "t_ledger")
    assert all(c["node"] not in got for c in tb["cells"] if c["row"] < tb["header_rows"])
    notes = _by_id(tbs, "t_multiline")                                  # no header row: no column names
    assert all(c["node"] not in got for c in notes["cells"])


@pytest.mark.parametrize("browser", BROWSERS)
def test_memory_label_uses_the_table_for_a_value_inside_it(browser):
    flat, tbs = _table_flat(browser)
    mem = PageMemory("fictional/tables", n_promote=2)
    mem.add(flat, "only")
    cell = next(c for c in _by_id(tbs, "t_ledger")["cells"] if (c["row"], c["col"]) == (2, 2))
    nid = mem.views["only"][1][cell["node"]]
    assert mem.label(nid) == "Tax / CGST"


# ── statuses and furniture on hand-made pages ─────────────────────────────────

SENT1 = "Please ensure that your quarterly tax returns are submitted well before the deadline date."
SENT2 = "The system will be undergoing scheduled weekend maintenance and will remain inaccessible."


def _flat(items):
    """[(type, text)] -> keys.flatten-shaped siblings, one box deep."""
    seen = {}
    out = []
    for typ, text in items:
        seen[typ] = seen.get(typ, 0) + 1
        out.append({"key": f"D1 / {typ}[{seen[typ]}]", "loose": f"D1 / {typ}[{seen[typ]}]", "parent": -1, "depth": 0,
                    "cls": "", "type": typ, "text": text, "node": {"ctype": 50020}})
    return out


def _two(first, second, ids=None):
    mem = PageMemory("fictional/hand", n_promote=2)
    mem.add(_flat(first), "c1")
    mem.add(_flat(second), "c2")
    return mem


def test_statuses_follow_the_verdicts():
    mem = _two([("Text", "Name :"), ("Text", "ALPHA"), ("Text", "Open"), ("Span", "42"), ("Group", "Extra")],
               [("Text", "Name :"), ("Text", "BETA"), ("Text", "Open"), ("Span", "42")])
    by_text = {nd["text"]: mem.status(i) for i, nd in enumerate(mem.nodes)}
    assert by_text["Name :"] == "fixed"
    assert by_text["ALPHA"] == "variable"
    assert by_text["42"] == "semi-variable"                     # a bare number is data
    assert by_text["Extra"] == "waiting"


def test_unlabelled_differing_sentence_is_furniture():
    mem = _two([("Group", SENT1)], [("Group", SENT2)])
    assert mem.label(0) == ""
    assert mem.verdict(0) == "probably furniture"
    assert mem.status(0) == "furniture"


def test_sentence_after_a_shared_label_stays_variable():
    mem = _two([("Text", "Notice :"), ("Group", SENT1)], [("Text", "Notice :"), ("Group", SENT2)])
    assert mem.label(1) == "Notice :"
    assert mem.verdict(1) == "differs between clients"
    assert mem.status(1) == "variable"


def test_a_differing_label_shaped_text_without_label_is_furniture():
    mem = _two([("Text", "Dynamic Section A:")], [("Text", "Dynamic Section B:")])
    assert mem.status(0) == "furniture"


def test_short_differing_text_is_data_even_without_a_label():
    mem = _two([("Text", "Acme Corp Ltd")], [("Text", "Global Tech Pvt")])
    assert mem.status(0) == "variable"


def test_a_shared_text_without_a_letter_labels_nothing():
    mem = _two([("Text", "---"), ("Group", SENT1)], [("Text", "---"), ("Group", SENT2)])
    assert mem.label(1) == ""
    assert mem.status(1) == "furniture"


@pytest.mark.parametrize("gap, labelled", [(5, True), (6, False)])
def test_a_label_further_back_than_the_lookback_does_not_protect(gap, labelled):
    assert labels.LABEL_LOOKBACK == 6
    first = [("Text", "Notice:")] + [("Span", str(i)) for i in range(gap)] + [("Group", SENT1)]
    second = [("Text", "Notice:")] + [("Span", str(i + 10)) for i in range(gap)] + [("Group", SENT2)]
    mem = _two(first, second)
    sent = len(first) - 1
    assert (mem.label(sent) == "Notice:") is labelled
    assert (mem.status(sent) == "variable") is labelled
    assert (mem.status(sent) == "furniture") is not labelled


def test_an_unpaired_repeat_of_a_template_text_can_label():
    first = [("Text", "Period"), ("Text", "Jun 2026")]
    second = [("Text", "Period"), ("Text", "Mar 2026"), ("Text", "Period"), ("Text", "Dec 2025")]
    mem = _two(first, second)
    flat, nid_of = mem.views["c2"]
    assert mem.label(nid_of[3]) == "Period"


# ── compare.py: the default view of memory, one CSV row per node ──────────────

class _Map:
    def __init__(self, client, stamp):
        self.reads = [stamp]
        self._flat = _client(client)

    def to_flat(self):
        return self._flat


def test_compare_default_writes_one_csv_row_per_text_node(tmp_path, monkeypatch, capsys):
    page = "fictional/page"
    monkeypatch.setattr(memory_mod, "client_maps", lambda: {page: {"A": _Map("A", "1"), "B": _Map("B", "2")}})
    monkeypatch.setattr(compare, "OUT_DIR", tmp_path)
    assert compare.main([]) == 0
    out = capsys.readouterr().out
    assert "data nodes" in out and "fixed" in out
    (csv_path,) = list(tmp_path.glob("compare_memory__*.csv"))
    import csv
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert list(rows[0]) == compare.MEMORY_FIELDS
    mem = _memory("AB")
    assert len(rows) == sum(1 for nd in mem.nodes if nd["text"])
    by_value = {r["example_value"]: r for r in rows}
    for value, want in EXPECTED.items():
        assert by_value[value]["label"].strip() == want
    assert by_value["BETA FOODS"]["status"] == "variable" and by_value["BETA FOODS"]["clients"] == "2"
    assert by_value["Taxpayer details"]["status"] == "fixed"
