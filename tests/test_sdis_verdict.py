"""
tests/test_sdis_verdict.py - SDIS Phase 2 review (W2-R): the one verdict order in memory
=========================================================================================
  - PageMemory.verdict is one function, its order written in its docstring;
  - an unsure look-alike pairing has no vote (Part E), also not in a confirmed node's verdict;
  - the memory side of a look-alike pairing scores its screen column too (Part E);
  - changes with time needs days seen by 2+ clients (Part C rule 2, rule 6);
  - probably furniture does not depend on which client came first (Part C rule 3);
  - a retired node's shape makes nothing a 'repeat'; retirement can be switched off (Part H).
Fictional texts only.
"""

from core.sdis.memory import PageMemory
from core.sdis.noise import changes_with_time


def _e(key, text, x=10, y=20):
    return {"key": key, "text": text, "type": "text", "cls": "", "depth": 0, "parent": -1,
            "node": {"rect": [x, y, 100, 30]}}


def _cards(a="Alpha", b="Beta", xb=10):
    return [_e("Head / Text[1]", "Title", 0, 0), _e("Card / Text[1]", a, 10, 20), _e("Card / Text[2]", b, xb, 60)]


def test_verdict_docstring_lists_the_order():
    doc = PageMemory.verdict.__doc__
    order = ["retired", "composite", "changes within one client", "changes with time", "ambiguous",
             "repeat", "variable_alignment", "probably furniture", "same for all clients"]
    pos = [doc.index(v) for v in order]
    assert pos == sorted(pos)


def test_unsure_lookalike_has_no_vote_in_a_confirmed_node():
    for clients in (("c1", "c2", "c3"), ("c2", "c1", "c3")):
        pm = PageMemory("p", n_promote=2)
        pm.add(_cards(), clients[0])
        pm.add(_cards(), clients[1])
        pm.add([_e("Head / Text[1]", "Title", 0, 0), _e("Card / Text[1]", "Gamma", 10, 20)], "c3")
        unsure = [i for i in (1, 2) if "c3" in pm.nodes[i]["clients"]]
        assert len(unsure) == 1 and pm.nodes[unsure[0]]["clients"]["c3"]["sure"] is False
        assert pm.verdict(1) == pm.verdict(2) == "same for all clients"


def test_memory_side_scores_the_screen_column():
    pm = PageMemory("p", n_promote=2)
    pm.add(_cards(xb=300), "c1")
    pm.add(_cards(xb=300), "c2")
    pm.add([_e("Head / Text[1]", "Title", 0, 0), _e("Card / Text[1]", "Gamma", 300, 20)], "c3")
    assert pm.nodes[2]["clients"]["c3"]["sure"] is True       # only Beta's column overlaps
    assert "c3" not in pm.nodes[1]["clients"]


def _node(*days_texts):
    return {"clients": {f"c{i}": {"texts": [t], "history": [[f"{d}_100000", t]]}
                        for i, (d, t) in enumerate(days_texts)}}


def test_changes_with_time_needs_days_seen_by_two_clients():
    assert not changes_with_time(_node(("20261001", "Name One"), ("20261002", "Name Two")))
    assert not changes_with_time(_node(("20261001", "X"), ("20261001", "X"), ("20261002", "Y")))
    assert changes_with_time(_node(("20261001", "X"), ("20261001", "X"), ("20261002", "Y"), ("20261002", "Y")))


def test_probably_furniture_does_not_depend_on_client_order():
    sentence = "This is a fictional notice that has more than eight words in it"
    for first, second in ((sentence, "AB1234"), ("AB1234", sentence)):
        pm = PageMemory("p", n_promote=2)
        pm.add([{"key": "P", "text": first, "type": "text"}], "c1")
        pm.add([{"key": "P", "text": second, "type": "text"}], "c2")
        assert pm.verdict(0) == "differs between clients"


def _page(banner=True):
    return [{"key": "H1", "text": "Dashboard", "type": "text", "depth": 0}] + \
        ([{"key": "Banner", "text": "Special Notice", "type": "text", "depth": 0}] if banner else []) + \
        [{"key": "Footer", "text": "Terms of Use", "type": "text", "depth": 0}]


def test_retired_shape_is_not_repeat_and_retire_can_be_off():
    for retire in (False, True):
        pm = PageMemory("p", n_promote=2, retire=retire)
        for i in range(10):
            pm.add(_page(), f"c{i}")
        for i in range(10, 14):
            pm.add(_page(banner=False), f"c{i}")
        assert (pm.verdict(1) == "retired") is retire
    pm.add(_page(), "c14")                                     # retired: it comes back through pending
    back = pm.pending[-1]
    assert pm.nodes[back]["shape"] == "Banner"
    assert pm.verdict(back) == "only one client so far"
