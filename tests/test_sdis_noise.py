"""
tests/test_sdis_noise.py - tests for noise over time and furniture heuristics (SDIS W2-2)
========================================================================================
Tests Blueprint Part C:
  - changes_within_client: any client with 2+ texts
  - changes_with_time: on every day seen, all clients agree, and texts differ between days
  - probably_furniture (its tests are in test_sdis_labels.py: it needs real labels)
"""

import pytest

from core.sdis.memory import PageMemory
from core.sdis.noise import changes_with_time, changes_within_client


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
