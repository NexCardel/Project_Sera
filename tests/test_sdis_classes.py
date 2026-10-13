"""
tests/test_sdis_classes.py - class suggestion (SDIS W3-5, Part S.1)
===================================================================
Hand-made fictional value histories fed to PageMemory; only counts and structure.
"""

from core.sdis.classes import CLASS_AGREE, annotate, client_class, read_period, suggest
from core.sdis.memory import PageMemory
from core.sdis.relevance import datapoints


def _flat(history):
    def node(key, text, hist):
        return {"key": key, "loose": key, "parent": -1, "depth": 0, "cls": "", "type": "Text", "text": text,
                "node": {"ctype": 50020}, "history": hist}
    return [node("D1 / Text[1]", "Name :", []), node("D1 / Text[2]", history[-1][1], [list(h) for h in history])]


def _mem(per_client):
    """{client: ([(stamp, text) changes], [(stamp, period) reads])}"""
    mem = PageMemory("fic/page", n_promote=2)
    for client, (history, reads) in per_client.items():
        mem.add(_flat(history), client, [{"stamp": s, "period": p} for s, p in reads])
    return mem


def _run(per_client):
    mem = _mem(per_client)
    dps = datapoints([mem])
    assert len(dps) == 1
    return suggest(dps, [mem])[dps[0].key]


DAYS = ["20261001_100000", "20261002_100000", "20261003_100000"]


def test_one_value_per_client_over_three_days_is_profile():
    got = _run({"c1": ([(DAYS[0], "AAA1")], [(d, "") for d in DAYS]),
                "c2": ([(DAYS[0], "BBB2")], [(d, "") for d in DAYS])})
    assert got[0] == "profile"
    assert "2 of 2" in got[1]


def test_values_constant_within_a_period_but_changing_between_periods_is_dataset():
    reads = [(DAYS[0], "q1"), (DAYS[1], "q1"), (DAYS[2], "q2"), ("20261004_100000", "q2")]
    got = _run({"c1": ([(DAYS[0], "AAA1"), (DAYS[2], "AAA2")], reads),
                "c2": ([(DAYS[0], "BBB1"), (DAYS[2], "BBB2")], reads)})
    assert got[0] == "dataset"


def test_values_changing_within_a_period_is_info():
    reads = [(DAYS[0], "q1"), (DAYS[1], "q1"), (DAYS[2], "q2"), ("20261004_100000", "q2")]
    got = _run({"c1": ([(DAYS[0], "AAA1"), (DAYS[1], "AAA2"), (DAYS[2], "AAA3")], reads),
                "c2": ([(DAYS[0], "BBB1"), (DAYS[1], "BBB2"), (DAYS[2], "BBB3")], reads)})
    assert got[0] == "info"


def test_one_observation_gives_no_suggestion():
    got = _run({"c1": ([(DAYS[0], "AAA1")], [(DAYS[0], "")]), "c2": ([(DAYS[0], "BBB2")], [(DAYS[0], "")])})
    assert got == (None, "not enough evidence")


def test_two_of_three_clients_agreeing_is_not_enough():
    steady = [(d, "") for d in DAYS]
    got = _run({"c1": ([(DAYS[0], "AAA1")], steady), "c2": ([(DAYS[0], "BBB2")], steady),
                "c3": ([(DAYS[0], "CCC3"), (DAYS[1], "CCC4")], steady)})
    assert got == (None, "clients disagree")
    assert CLASS_AGREE == 0.9


def test_client_class_needs_days_or_periods_for_a_constant_value():
    obs = [("20261001", "", "x"), ("20261002", "", "x")]
    assert client_class(obs) == "profile"
    assert client_class([("20261001", "", "x"), ("20261001", "", "x")]) is None
    assert client_class([("20261001", "", "x"), ("20261002", "", "y")]) == "info"


def test_a_move_by_the_user_overrides():
    steady = [(d, "") for d in DAYS]
    mem = _mem({"c1": ([(DAYS[0], "AAA1")], steady), "c2": ([(DAYS[0], "BBB2")], steady)})
    dps = datapoints([mem])
    key = dps[0].key
    assert suggest(dps, [mem], moves={(key[0].upper() + " :", key[1]): "info"})[key] == ("info", "moved by the user")
    annotate(dps, [mem])
    assert (dps[0].suggested_class, dps[0].class_reason.startswith("same on every")) == ("profile", True)


def test_reads_without_read_info_use_the_history_items():
    mem = PageMemory("fic/page", n_promote=2)
    mem.add(_flat([(DAYS[0], "AAA1"), (DAYS[1], "AAA1x")]), "c1")
    mem.add(_flat([(DAYS[0], "BBB2"), (DAYS[1], "BBB2x")]), "c2")
    dps = datapoints([mem])
    assert suggest(dps, [mem])[dps[0].key][0] == "info"


def test_period_from_the_page_when_shown_once_else_from_the_masked_link():
    assert read_period("h/a/2026-27", "h/a/{v}", ["A.Y. 2026-27", "Name", "x"]) == "a.y. 2026-27"
    assert read_period("h/a/2026-27", "h/a/{v}", ["Name"]) == "2026-27"
    assert read_period("h/a/2026-27", "h/a/{v}", ["A.Y. 2026-27", "A.Y. 2025-26"]) == "2026-27"
    assert read_period("h/a/xyz", "h/a/{v}", ["Name"]) == ""
    assert read_period("h/a/2026-27", "h/a/2026-27", ["Name"]) == ""
