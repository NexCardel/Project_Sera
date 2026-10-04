"""
tests/test_sdis_noise.py - tests for noise over time and furniture heuristics (SDIS W2-2)
========================================================================================
Tests Blueprint Part C:
  - changes_within_client: any client with 2+ texts
  - changes_with_time: on every day seen, all clients agree, and texts differ between days
  - probably_furniture: differs between clients, sentence/label shaped, no label beside it
"""

import pytest

from core.sdis.memory import PageMemory
from core.sdis.noise import (
    LABEL_LOOKBACK,
    changes_with_time,
    changes_within_client,
    probably_furniture,
)


def _make_mem(link: str = "test.gov.in/page", n: int = 2) -> PageMemory:
    return PageMemory(link, n_promote=n)


def test_changes_within_client():
    """Client with 2+ texts triggers changes_within_client."""
    node_single = {
        "clients": {
            "c1": {"texts": ["Alpha"], "history": []},
            "c2": {"texts": ["Beta"], "history": []},
        }
    }
    assert not changes_within_client(node_single)

    node_multi = {
        "clients": {
            "c1": {"texts": ["Alpha", "Gamma"], "history": []},
            "c2": {"texts": ["Beta"], "history": []},
        }
    }
    assert changes_within_client(node_multi)


def test_changes_with_time_shared_across_days():
    """Same text for 2 clients on day 1, another shared text on day 2 -> changes with time."""
    mem = _make_mem()
    node = {
        "shape": "p",
        "key": "p",
        "text": "Site last updated on 01/10/2026",
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {
                "texts": ["Site last updated on 01/10/2026"],
                "history": [["20261001_100000", "Site last updated on 01/10/2026"]],
                "sure": True,
            },
            "c2": {
                "texts": ["Site last updated on 01/10/2026"],
                "history": [["20261001_110000", "Site last updated on 01/10/2026"]],
                "sure": True,
            },
            "c3": {
                "texts": ["Site last updated on 02/10/2026"],
                "history": [["20261002_090000", "Site last updated on 02/10/2026"]],
                "sure": True,
            },
            "c4": {
                "texts": ["Site last updated on 02/10/2026"],
                "history": [["20261002_140000", "Site last updated on 02/10/2026"]],
                "sure": True,
            },
        },
        "composite": False,
    }
    assert changes_with_time(node)

    mem.nodes.append(node)
    mem.order.append(0)
    assert mem.verdict(0) == "changes with time"


def test_changes_with_time_different_texts_same_day():
    """Different texts for 2 clients on the same day -> not changes with time."""
    mem = _make_mem()
    node = {
        "shape": "p",
        "key": "p",
        "text": "Notice A",
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {
                "texts": ["Notice A"],
                "history": [["20261001_100000", "Notice A"]],
                "sure": True,
            },
            "c2": {
                "texts": ["Notice B"],
                "history": [["20261001_110000", "Notice B"]],
                "sure": True,
            },
            "c3": {
                "texts": ["Notice C"],
                "history": [["20261002_090000", "Notice C"]],
                "sure": True,
            },
        },
        "composite": False,
    }
    assert not changes_with_time(node)

    mem.nodes.append(node)
    mem.order.append(0)
    # Since day 1 has two different texts, changes_with_time is False
    assert mem.verdict(0) != "changes with time"


def test_changes_with_time_same_text_all_days():
    """If all days have the exact same text, it did not change between days -> not."""
    node = {
        "clients": {
            "c1": {
                "texts": ["Fixed Header"],
                "history": [["20261001_100000", "Fixed Header"]],
            },
            "c2": {
                "texts": ["Fixed Header"],
                "history": [["20261002_100000", "Fixed Header"]],
            },
        },
        "composite": False,
    }
    assert not changes_with_time(node)


def test_changes_with_time_single_day():
    """Only 1 day of observation cannot establish changes with time."""
    node = {
        "clients": {
            "c1": {
                "texts": ["Notice A"],
                "history": [["20261001_100000", "Notice A"]],
            },
            "c2": {
                "texts": ["Notice A"],
                "history": [["20261001_120000", "Notice A"]],
            },
        },
        "composite": False,
    }
    assert not changes_with_time(node)


def test_changes_within_client_precedes_changes_with_time_in_verdict():
    """If a single client has 2+ texts across days, changes within one client wins."""
    mem = _make_mem()
    node = {
        "shape": "p",
        "key": "p",
        "text": "Notice A",
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {
                "texts": ["Notice A", "Notice B"],
                "history": [
                    ["20261001_100000", "Notice A"],
                    ["20261002_100000", "Notice B"],
                ],
                "sure": True,
            },
            "c2": {
                "texts": ["Notice A", "Notice B"],
                "history": [
                    ["20261001_110000", "Notice A"],
                    ["20261002_110000", "Notice B"],
                ],
                "sure": True,
            },
        },
        "composite": False,
    }
    # Direct function evaluation:
    assert changes_within_client(node)
    assert changes_with_time(node)
    # Verdict precedence: changes within one client comes first
    mem.nodes.append(node)
    mem.order.append(0)
    assert mem.verdict(0) == "changes within one client"


def test_probably_furniture_long_unlabelled_sentence():
    """A long unlabelled sentence that differs -> probably furniture."""
    mem = _make_mem()
    sent1 = "Please ensure that your quarterly tax returns are submitted well before the deadline date."
    sent2 = "The system will be undergoing scheduled weekend maintenance and will remain inaccessible."
    node = {
        "shape": "p",
        "key": "p",
        "text": sent1,
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {"texts": [sent1], "history": [], "sure": True},
            "c2": {"texts": [sent2], "history": [], "sure": True},
        },
        "composite": False,
    }
    mem.nodes.append(node)
    mem.order.append(0)

    assert probably_furniture(mem, 0)
    assert mem.verdict(0) == "probably furniture"


def test_probably_furniture_sentence_with_label_stays_differs():
    """A long sentence that differs with a confirmed label before it -> stays 'differs between clients'."""
    mem = _make_mem()
    # Node 0: confirmed label 'same for all clients' with letters
    label_node = {
        "shape": "label",
        "key": "lbl",
        "text": "Portal Announcement:",
        "type": "label",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {"texts": ["Portal Announcement:"], "history": [], "sure": True},
            "c2": {"texts": ["Portal Announcement:"], "history": [], "sure": True},
        },
        "composite": False,
    }
    # Node 1: long sentence that differs
    sent1 = "Please ensure that your quarterly tax returns are submitted well before the deadline date."
    sent2 = "The system will be undergoing scheduled weekend maintenance and will remain inaccessible."
    sentence_node = {
        "shape": "p",
        "key": "p",
        "text": sent1,
        "type": "text",
        "status": "memory",
        "anchor": 0,
        "clients": {
            "c1": {"texts": [sent1], "history": [], "sure": True},
            "c2": {"texts": [sent2], "history": [], "sure": True},
        },
        "composite": False,
    }
    mem.nodes.extend([label_node, sentence_node])
    mem.order.extend([0, 1])

    assert mem.verdict(0) == "same for all clients"
    assert not probably_furniture(mem, 1)
    assert mem.verdict(1) == "differs between clients"


def test_probably_furniture_unlabelled_differing_label_shape():
    """Text ending in ':' that differs between clients without a preceding label -> probably furniture."""
    mem = _make_mem()
    lbl1 = "Dynamic Section A:"
    lbl2 = "Dynamic Section B:"
    node = {
        "shape": "span",
        "key": "span",
        "text": lbl1,
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {"texts": [lbl1], "history": [], "sure": True},
            "c2": {"texts": [lbl2], "history": [], "sure": True},
        },
        "composite": False,
    }
    mem.nodes.append(node)
    mem.order.append(0)

    assert probably_furniture(mem, 0)
    assert mem.verdict(0) == "probably furniture"


def test_not_furniture_when_value_type_is_normal_data_or_text():
    """Short client names or codes that differ stay 'differs between clients' even without label."""
    mem = _make_mem()
    node = {
        "shape": "span",
        "key": "span",
        "text": "Acme Corp Ltd",
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {"texts": ["Acme Corp Ltd"], "history": [], "sure": True},
            "c2": {"texts": ["Global Tech Pvt"], "history": [], "sure": True},
        },
        "composite": False,
    }
    mem.nodes.append(node)
    mem.order.append(0)

    # Not a sentence (only 3 words), not ending in ':'
    assert not probably_furniture(mem, 0)
    assert mem.verdict(0) == "differs between clients"


def test_probably_furniture_label_without_letters_does_not_protect():
    """A preceding node with symbols/numbers but no letters (e.g. '---') does not count as label."""
    mem = _make_mem()
    sym_node = {
        "shape": "hr",
        "key": "hr",
        "text": "---",
        "type": "text",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {"texts": ["---"], "history": [], "sure": True},
            "c2": {"texts": ["---"], "history": [], "sure": True},
        },
        "composite": False,
    }
    sent1 = "Please ensure that your quarterly tax returns are submitted well before the deadline date."
    sent2 = "The system will be undergoing scheduled weekend maintenance and will remain inaccessible."
    sent_node = {
        "shape": "p",
        "key": "p",
        "text": sent1,
        "type": "text",
        "status": "memory",
        "anchor": 0,
        "clients": {
            "c1": {"texts": [sent1], "history": [], "sure": True},
            "c2": {"texts": [sent2], "history": [], "sure": True},
        },
        "composite": False,
    }
    mem.nodes.extend([sym_node, sent_node])
    mem.order.extend([0, 1])

    assert probably_furniture(mem, 1)
    assert mem.verdict(1) == "probably furniture"


def test_probably_furniture_lookback_boundary():
    """Label at distance 6 protects the sentence; at distance 7 (beyond LABEL_LOOKBACK) it does not."""
    sent1 = "Please ensure that your quarterly tax returns are submitted well before the deadline date."
    sent2 = "The system will be undergoing scheduled weekend maintenance and will remain inaccessible."

    # Distance 6: label at index 0, sentence at index 6 (5 intervening nodes)
    mem6 = _make_mem()
    label_node = {
        "shape": "label",
        "key": "lbl",
        "text": "Notice:",
        "type": "label",
        "status": "memory",
        "anchor": None,
        "clients": {
            "c1": {"texts": ["Notice:"], "sure": True},
            "c2": {"texts": ["Notice:"], "sure": True},
        },
        "composite": False,
    }
    mem6.nodes.append(label_node)
    mem6.order.append(0)

    # 5 dummy intervening nodes (not same for all clients with letters, e.g. numbers)
    for i in range(1, 6):
        dummy = {
            "shape": "span",
            "key": f"d{i}",
            "text": str(i),
            "type": "number",
            "status": "memory",
            "anchor": 0,
            "clients": {
                "c1": {"texts": [str(i)], "sure": True},
                "c2": {"texts": [str(i + 10)], "sure": True},
            },
            "composite": False,
        }
        mem6.nodes.append(dummy)
        mem6.order.append(i)

    # Sentence at index 6: distance is 6 (within LABEL_LOOKBACK=6)
    sent_node = {
        "shape": "p",
        "key": "sent",
        "text": sent1,
        "type": "text",
        "status": "memory",
        "anchor": 0,
        "clients": {
            "c1": {"texts": [sent1], "sure": True},
            "c2": {"texts": [sent2], "sure": True},
        },
        "composite": False,
    }
    mem6.nodes.append(sent_node)
    mem6.order.append(6)

    assert not probably_furniture(mem6, 6)
    assert mem6.verdict(6) == "differs between clients"

    # Now add one more dummy node before the sentence (distance 7 > LABEL_LOOKBACK=6)
    mem7 = _make_mem()
    mem7.nodes.append(label_node)
    mem7.order.append(0)
    for i in range(1, 7):  # 6 intervening dummy nodes
        dummy = {
            "shape": "span",
            "key": f"d{i}",
            "text": str(i),
            "type": "number",
            "status": "memory",
            "anchor": 0,
            "clients": {
                "c1": {"texts": [str(i)], "sure": True},
                "c2": {"texts": [str(i + 10)], "sure": True},
            },
            "composite": False,
        }
        mem7.nodes.append(dummy)
        mem7.order.append(i)

    # Sentence at index 7: distance is 7 (beyond LABEL_LOOKBACK=6)
    mem7.nodes.append(sent_node)
    mem7.order.append(7)

    assert probably_furniture(mem7, 7)
    assert mem7.verdict(7) == "probably furniture"
