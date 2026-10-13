"""
tests/test_sdis_retire.py - SDIS Part H: memory upkeep (retire)
==============================================================
Tests Blueprint Part H:
  - Each node keeps first_ci and last_ci; PageMemory keeps self.clients.
  - After each add(): for every confirmed node in self.order:
      chances = len(self.clients) - first_ci
      seen = number of clients that had it
      misses = len(self.clients) - 1 - last_ci
      p = (seen + 1) / (chances + 2)
      retire when misses >= 2 and (1 - p) ** misses < RETIRE_P (0.01):
        remove from self.order, status 'retired', verdict 'retired'
  - Never retire an unconfirmed node.
  - A retired node that a later client matches through pending comes back as a new pending node.
"""

import pytest

from core.sdis.memory import RETIRE_P, PageMemory


def _full_page():
    return [
        {"key": "H1", "text": "Dashboard", "type": "text", "depth": 0},
        {"key": "Banner", "text": "Special Notice", "type": "text", "depth": 0},
        {"key": "Footer", "text": "Terms of Use", "type": "text", "depth": 0},
    ]


def _missing_page():
    return [
        {"key": "H1", "text": "Dashboard", "type": "text", "depth": 0},
        {"key": "Footer", "text": "Terms of Use", "type": "text", "depth": 0},
    ]


def test_node_in_10_clients_then_missing_in_4_retires():
    """A node in 10 clients then missing in 4 -> retired (p = 11/16, (5/16)^4 = 0.0095 < 0.01)."""
    mem = PageMemory("test.portal/page", n_promote=2)

    # 10 clients have the node
    for i in range(10):
        mem.add(_full_page(), f"client_{i}")

    # Node at index 1 is "Special Notice"
    target_nid = 1
    assert mem.nodes[target_nid]["text"] == "Special Notice"
    assert mem.confirmed(target_nid)
    assert target_nid in mem.order
    assert mem.nodes[target_nid]["first_ci"] == 0
    assert mem.nodes[target_nid]["last_ci"] == 9
    assert len(mem.nodes[target_nid]["clients"]) == 10

    # 4 clients miss the node
    for i in range(10, 14):
        mem.add(_missing_page(), f"client_{i}")

    assert len(mem.clients) == 14
    assert target_nid not in mem.order
    assert mem.nodes[target_nid]["status"] == "retired"
    assert mem.nodes[target_nid]["verdict"] == "retired"
    assert mem.verdict(target_nid) == "retired"
    assert mem.summary()[("retired", "retired")] == 1


def test_node_in_10_clients_then_missing_in_3_not_retired():
    """A node in 10 clients then missing in 3 -> NOT retired (p = 11/15, (4/15)^3 = 0.01896 >= 0.01)."""
    mem = PageMemory("test.portal/page", n_promote=2)

    # 10 clients have the node
    for i in range(10):
        mem.add(_full_page(), f"client_{i}")

    target_nid = 1
    assert mem.confirmed(target_nid)

    # 3 clients miss the node
    for i in range(10, 13):
        mem.add(_missing_page(), f"client_{i}")

    assert len(mem.clients) == 13
    assert target_nid in mem.order
    assert mem.nodes[target_nid]["status"] == "memory"
    assert mem.verdict(target_nid) != "retired"


def test_node_in_2_clients_then_missing_in_4_not_retired():
    """A node in 2 clients then missing in 4 -> NOT retired (p = 3/8, (5/8)^4 = 0.15258 >= 0.01)."""
    mem = PageMemory("test.portal/page", n_promote=2)

    # 2 clients have the node (confirmed at N = 2)
    mem.add(_full_page(), "client_0")
    mem.add(_full_page(), "client_1")

    target_nid = 1
    assert mem.confirmed(target_nid)
    assert mem.nodes[target_nid]["first_ci"] == 0
    assert mem.nodes[target_nid]["last_ci"] == 1

    # 4 clients miss the node
    for i in range(2, 6):
        mem.add(_missing_page(), f"client_{i}")

    assert len(mem.clients) == 6
    assert target_nid in mem.order
    assert mem.nodes[target_nid]["status"] == "memory"
    assert mem.verdict(target_nid) != "retired"


def test_unconfirmed_node_never_retires():
    """An unconfirmed node never retires (rule: only confirmed nodes can retire)."""
    mem = PageMemory("test.portal/page", n_promote=2)

    # Client 0 has a unique node never seen again
    mem.add(_full_page(), "client_0")

    target_nid = 1  # "Special Notice"
    assert not mem.confirmed(target_nid)

    # 10 clients miss the node
    for i in range(1, 11):
        mem.add(_missing_page(), f"client_{i}")

    assert len(mem.clients) == 11
    assert not mem.confirmed(target_nid)
    assert target_nid in mem.order
    assert mem.nodes[target_nid]["status"] != "retired"
    assert mem.verdict(target_nid) != "retired"


def test_unconfirmed_pending_node_never_retires():
    """An unconfirmed node in pending also never retires."""
    mem = PageMemory("test.portal/page", n_promote=2)

    # Client 0 has the base page
    mem.add(_missing_page(), "client_0")

    # Client 1 adds a new pending item
    pending_page = [
        {"key": "H1", "text": "Dashboard", "type": "text", "depth": 0},
        {"key": "PendingExtra", "text": "Pending Feature", "type": "text", "depth": 0},
        {"key": "Footer", "text": "Terms of Use", "type": "text", "depth": 0},
    ]
    mem.add(pending_page, "client_1")
    assert len(mem.pending) == 1
    p_nid = mem.pending[0]
    assert mem.nodes[p_nid]["text"] == "Pending Feature"
    assert not mem.confirmed(p_nid)

    # 10 more clients without this pending item
    for i in range(2, 12):
        mem.add(_missing_page(), f"client_{i}")

    assert p_nid in mem.pending
    assert mem.nodes[p_nid]["status"] == "pending"
    assert mem.verdict(p_nid) != "retired"


def test_retired_node_returns_as_new_pending_node():
    """A retired node that a later client matches through pending comes back as a new pending node."""
    mem = PageMemory("test.portal/page", n_promote=2)

    # 10 clients have the node
    for i in range(10):
        mem.add(_full_page(), f"client_{i}")

    target_nid = 1
    assert target_nid in mem.order

    # 4 misses -> retires
    for i in range(10, 14):
        mem.add(_missing_page(), f"client_{i}")

    assert target_nid not in mem.order
    assert mem.nodes[target_nid]["status"] == "retired"

    # Client 14 has the node again
    mem.add(_full_page(), "client_14")

    # Old node remains retired
    assert mem.nodes[target_nid]["status"] == "retired"
    # A new node was added to pending
    assert len(mem.pending) == 1
    new_nid = mem.pending[0]
    assert new_nid != target_nid
    assert mem.nodes[new_nid]["text"] == "Special Notice"
    assert mem.nodes[new_nid]["status"] == "pending"
    assert mem.nodes[new_nid]["first_ci"] == 14
    assert mem.nodes[new_nid]["last_ci"] == 14

    # Client 15 also has the node -> promoted to memory
    mem.add(_full_page(), "client_15")
    assert new_nid in mem.order
    assert mem.nodes[new_nid]["status"] == "memory"
    assert mem.confirmed(new_nid)


def test_first_ci_and_last_ci_updates():
    """Verify first_ci and last_ci are tracked accurately across client additions."""
    mem = PageMemory("test.portal/page", n_promote=2)

    mem.add(_missing_page(), "c0")
    assert mem.nodes[0]["first_ci"] == 0
    assert mem.nodes[0]["last_ci"] == 0

    mem.add(_missing_page(), "c1")
    assert mem.nodes[0]["first_ci"] == 0
    assert mem.nodes[0]["last_ci"] == 1

    # Adding a client that already exists does not create a new client entry
    mem.add(_missing_page(), "c1")
    assert len(mem.clients) == 2
    assert mem.nodes[0]["last_ci"] == 1
