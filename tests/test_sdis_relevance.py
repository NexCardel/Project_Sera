"""
tests/test_sdis_relevance.py - relevance by occurrences + slots (SDIS W3-2, Part J)
===================================================================================
Hand-made fictional pages fed to PageMemory; only counts and structure, no real data.
"""

from core.sdis.memory import PageMemory
from core.sdis.relevance import FULL_SURE_CLIENTS, datapoints


def _flat(items):
    """[(type, text)] -> keys.flatten-shaped siblings, one box deep."""
    seen = {}
    out = []
    for typ, text in items:
        seen[typ] = seen.get(typ, 0) + 1
        out.append({"key": f"D1 / {typ}[{seen[typ]}]", "loose": f"D1 / {typ}[{seen[typ]}]", "parent": -1, "depth": 0,
                    "cls": "", "type": typ, "text": text, "node": {"ctype": 50020}})
    return out


def _page(link, per_client):
    mem = PageMemory(link, n_promote=2)
    for client, items in per_client.items():
        mem.add(_flat(items), client)
    return mem


def _two(link, label, v1, v2):
    return _page(link, {"c1": [("Text", label), ("Text", v1)], "c2": [("Text", label), ("Text", v2)]})


def test_fixed_nodes_never_become_datapoints():
    mem = _page("fic/a", {"c1": [("Text", "Name :"), ("Text", "ALPHA")], "c2": [("Text", "Name :"), ("Text", "BETA")]})
    dps = datapoints([mem])
    assert [dp.label for dp in dps] == ["Name"]
    assert dps[0].nodes == [(0, 1)]


def test_same_label_on_two_pages_joins_into_one_datapoint():
    a = _two("fic/a", "Name :", "ALPHA", "BETA")
    b = _two("fic/b", "NAME:", "GAMMA", "DELTA")
    dps = datapoints([a, b])
    assert len(dps) == 1
    assert [p[0] for p in dps[0].pages] == ["fic/a", "fic/b"]
    assert dps[0].relevance_pct == 100
    assert dps[0].sure_pct == round(100 * 2 / FULL_SURE_CLIENTS)


def test_a_50_row_list_counts_once_per_client():
    def rows(prefix):
        out = [("Text", "Item :")]
        out += [("Text", f"{prefix}{i}") for i in range(50)]
        return out
    dps = datapoints([_page("fic/list", {"c1": rows("AA"), "c2": rows("BB")})])
    assert len(dps[0].nodes) > 1                       # the rows are many nodes of one datapoint
    assert [p[3] for p in dps[0].pages] == [1.0]       # but two clients on one page are one share, not 100
    assert dps[0].relevance_pct == 100


def test_share_uses_the_pages_own_client_count():
    # 'Name' is on a page with 2 clients and has both; 'Code' is on a 4-client page and only 2 have it
    busy = _page("fic/busy", {
        "c1": [("Text", "Code :"), ("Text", "A1")], "c2": [("Text", "Code :"), ("Text", "B2")],
        "c3": [("Text", "Code :")], "c4": [("Text", "Code :")]})
    small = _two("fic/small", "Name :", "ALPHA", "BETA")
    by_label = {dp.label: dp for dp in datapoints([busy, small])}
    assert by_label["Name"].pages == [("fic/small", 1, "", 1.0)]
    assert by_label["Code"].pages == [("fic/busy", 1, "", 0.5)]
    assert by_label["Name"].relevance_pct == 100
    assert by_label["Code"].relevance_pct == 50


def test_picked_counts_one_and_a_half_and_rejected_is_left_out():
    busy = _page("fic/busy", {
        "c1": [("Text", "Code :"), ("Text", "A1")], "c2": [("Text", "Code :"), ("Text", "B2")],
        "c3": [("Text", "Code :")], "c4": [("Text", "Code :")]})
    small = _two("fic/small", "Name :", "ALPHA", "BETA")
    code = datapoints([busy, small])[1].key
    assert {dp.key: dp.relevance_pct for dp in datapoints([busy, small])}[code] == 50
    assert {dp.key: dp.relevance_pct for dp in datapoints([busy, small], picked=[code])}[code] == 75
    assert [dp.key for dp in datapoints([busy, small], rejected=[code])] != [code]
    assert len(datapoints([busy, small], rejected=[(code[0].upper() + ":", code[1])])) == 1


def test_slots_and_surprise():
    clients = {f"c{i}": [("Text", "Amount :"), ("Text", str(100 + i))] for i in range(9)}
    clients["c9"] = [("Text", "Amount :"), ("Text", "Pending")]
    mem = _page("fic/slots", clients)
    dp = datapoints([mem])[0]
    assert dp.label == "Amount"
    assert dp.slot_type_pct == 90
    assert dp.surprise is True
    assert dp.sure_pct == 100

    even = _two("fic/even", "Amount :", "100", "Pending")
    dp2 = datapoints([even])[0]
    assert dp2.slot_type_pct == 50
    assert dp2.surprise is False


def test_a_node_with_no_label_is_its_own_datapoint():
    mem = _page("fic/nolabel", {"c1": [("Text", "Acme Corp Ltd")], "c2": [("Text", "Global Tech Pvt")]})
    dps = datapoints([mem])
    assert len(dps) == 1 and dps[0].label == ""
    assert dps[0].key[0] == "fic/nolabel"


def test_rejected_variable_alignment_does_not_count():
    first = [("Text", "Name :"), ("Text", "ALPHA"), ("Text", "Open")]
    second = [("Text", "Name :"), ("Text", "BETA"), ("Text", "Open")]
    mem = _page("fic/va", {"c1": first, "c2": second})
    assert mem.status(2) == "variable_alignment"
    assert datapoints([mem])[0].nodes == [(0, 1), (0, 2)]
    mem.rejected.add((mem.link, mem.nodes[2]["shape"], "Open"))
    assert datapoints([mem])[0].nodes == [(0, 1)]
