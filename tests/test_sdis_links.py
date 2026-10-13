"""
tests/test_sdis_links.py - tests for smart page link resolution (Part N)
========================================================================
Tests:
- /returns/2026-27/summary for 3 clients -> one resolved link;
- /returns/gstr1 and /returns/gstr2b visited by the same client -> unchanged;
- a value position seen for only one client -> unchanged;
- value votes count clients, not link pairs (R8);
- a route segment ('#/auth/ARN123') resolved the same way;
- letters-only segment visited with another value by the same client -> never masked;
- host is never masked;
- build_maps with link_of keys maps by resolved link;
- memory.client_maps resolves value links across clients.
"""

from typing import Any, Dict, List
import pytest

from core.sdis.links import parse_link, reconstruct_link, resolve
from core.sdis import link_map, memory


def test_three_clients_value_position_resolved_to_one_link():
    """/returns/2026-27/summary for 3 clients -> one resolved link."""
    links_by_client = {
        "client1": {"/returns/2024-25/summary"},
        "client2": {"/returns/2025-26/summary"},
        "client3": {"/returns/2026-27/summary"},
    }
    resolved = resolve(links_by_client)
    assert resolved["/returns/2024-25/summary"] == "/returns/{v}/summary"
    assert resolved["/returns/2025-26/summary"] == "/returns/{v}/summary"
    assert resolved["/returns/2026-27/summary"] == "/returns/{v}/summary"
    assert len(set(resolved.values())) == 1


def test_same_client_different_pages_unchanged():
    """/returns/gstr1 and /returns/gstr2b visited by the same client -> unchanged."""
    links_by_client = {
        "client1": {"/returns/gstr1", "/returns/gstr2b"},
    }
    resolved = resolve(links_by_client)
    assert resolved["/returns/gstr1"] == "/returns/gstr1"
    assert resolved["/returns/gstr2b"] == "/returns/gstr2b"

    # Even when other clients also visit these pages:
    links_multi_client = {
        "client1": {"/returns/gstr1", "/returns/gstr2b"},
        "client2": {"/returns/gstr1"},
        "client3": {"/returns/gstr2b"},
    }
    resolved_multi = resolve(links_multi_client)
    assert resolved_multi["/returns/gstr1"] == "/returns/gstr1"
    assert resolved_multi["/returns/gstr2b"] == "/returns/gstr2b"


def test_value_position_seen_for_only_one_client_unchanged():
    """A value position seen for only one client -> unchanged."""
    links_by_client = {
        "client1": {"/returns/2026-27/summary"},
    }
    resolved = resolve(links_by_client)
    assert resolved["/returns/2026-27/summary"] == "/returns/2026-27/summary"

    # Only 2 clients (2 value votes < VALUE_CLIENTS):
    links_two = {
        "client1": {"/returns/2025-26/summary"},
        "client2": {"/returns/2026-27/summary"},
    }
    resolved_two = resolve(links_two)
    assert resolved_two["/returns/2025-26/summary"] == "/returns/2025-26/summary"
    assert resolved_two["/returns/2026-27/summary"] == "/returns/2026-27/summary"


def test_value_votes_count_clients_not_link_pairs():
    """R8: two clients with three sibling pages each are still two clients - not masked; four
    clients sharing only two years on one page are four clients - masked."""
    two_clients = {
        "client1": {"/returns/2025-26/gstr1", "/returns/2025-26/gstr3b", "/returns/2025-26/gstr9"},
        "client2": {"/returns/2026-27/gstr1", "/returns/2026-27/gstr3b", "/returns/2026-27/gstr9"},
    }
    resolved = resolve(two_clients)
    assert all(resolved[l] == l for links in two_clients.values() for l in links)

    four_clients = {
        "client1": {"/returns/2025-26/summary"},
        "client2": {"/returns/2025-26/summary"},
        "client3": {"/returns/2026-27/summary"},
        "client4": {"/returns/2026-27/summary"},
    }
    resolved = resolve(four_clients)
    assert set(resolved.values()) == {"/returns/{v}/summary"}


def test_route_segment_resolved_same_way():
    """A route segment ('#/auth/ARN123') resolved the same way."""
    links_by_client = {
        "c1": {"portal.gov.in/returns#/auth/ARN111"},
        "c2": {"portal.gov.in/returns#/auth/ARN222"},
        "c3": {"portal.gov.in/returns#/auth/ARN333"},
    }
    resolved = resolve(links_by_client)
    expected = "portal.gov.in/returns#/auth/{v}"
    assert resolved["portal.gov.in/returns#/auth/ARN111"] == expected
    assert resolved["portal.gov.in/returns#/auth/ARN222"] == expected
    assert resolved["portal.gov.in/returns#/auth/ARN333"] == expected

    # Route without path:
    links_no_path = {
        "c1": {"#/auth/ARN111"},
        "c2": {"#/auth/ARN222"},
        "c3": {"#/auth/ARN333"},
    }
    resolved_no_path = resolve(links_no_path)
    assert resolved_no_path["#/auth/ARN111"] == "#/auth/{v}"
    assert resolved_no_path["#/auth/ARN222"] == "#/auth/{v}"
    assert resolved_no_path["#/auth/ARN333"] == "#/auth/{v}"


def test_letters_only_same_client_never_masked():
    """Never mask a segment of letters only that the same client also visited with another value."""
    links_by_client = {
        "c1": {"host/returns/form/view", "host/returns/dashboard/view"},
        "c2": {"host/returns/report/view"},
        "c3": {"host/returns/analytics/view"},
    }
    resolved = resolve(links_by_client)
    # c1 visited 'form' and 'dashboard' at pos 1, both letters only.
    # Therefore position 1 must not be masked.
    assert resolved["host/returns/form/view"] == "host/returns/form/view"
    assert resolved["host/returns/dashboard/view"] == "host/returns/dashboard/view"


def test_never_mask_host():
    """Host is never masked even across different domains."""
    links_by_client = {
        "c1": {"portalA.gov.in/returns/summary"},
        "c2": {"portalB.gov.in/returns/summary"},
        "c3": {"portalC.gov.in/returns/summary"},
    }
    resolved = resolve(links_by_client)
    # Each host is its own group, so links are unchanged
    assert resolved["portalA.gov.in/returns/summary"] == "portalA.gov.in/returns/summary"
    assert resolved["portalB.gov.in/returns/summary"] == "portalB.gov.in/returns/summary"
    assert resolved["portalC.gov.in/returns/summary"] == "portalC.gov.in/returns/summary"


def test_parse_and_reconstruct_link():
    """Check parse_link and reconstruct_link on various URL shapes."""
    samples = [
        "return.gst.gov.in/returns/auth/dashboard",
        "/returns/2026-27/summary",
        "portal.gov.in/returns#/auth/ARN123",
        "#/auth/ARN123",
        "example.com#/auth/ARN123",
        "title: Goods & Service Tax (GST) | User Dashboard",
        "",
    ]
    for s in samples:
        p = parse_link(s)
        rebuilt = reconstruct_link(p, p.segments)
        assert rebuilt == s


def _make_rec(text: str) -> Dict[str, Any]:
    return {
        "docs": [[
            {"ctype": 50020, "type_name": "Text", "name": text, "cls": "c", "parent": -1}
        ]]
    }


def _make_rec_data(nums: List[str]) -> Dict[str, Any]:
    return {
        "docs": [[
            {"ctype": 50020, "type_name": "Text", "name": n, "cls": f"c{i}", "parent": -1}
            for i, n in enumerate(nums)
        ]]
    }


def test_build_maps_with_link_of():
    """link_map.build_maps takes an optional link_of mapping and keys maps by resolved link."""
    sources = [
        ("20260101_000000", "session1", "/returns/2024-25/summary", _make_rec("Summary A")),
        ("20260101_000100", "session2", "/returns/2025-26/summary", _make_rec("Summary B")),
    ]
    link_of = {
        "/returns/2024-25/summary": "/returns/{v}/summary",
        "/returns/2025-26/summary": "/returns/{v}/summary",
    }
    maps = link_map.build_maps(sources=sources, link_of=link_of)
    assert ("session1", "/returns/{v}/summary") in maps
    assert ("session2", "/returns/{v}/summary") in maps
    assert maps[("session1", "/returns/{v}/summary")].link == "/returns/{v}/summary"


def test_memory_client_maps_resolves_value_links():
    """memory.client_maps computes clients' raw links, calls resolve(), and builds with mapping."""
    sources = [
        ("20260101_000000", "session1", "/returns/2024-25/summary", _make_rec_data(["100", "200", "300"])),
        ("20260101_000100", "session2", "/returns/2025-26/summary", _make_rec_data(["400", "500", "600"])),
        ("20260101_000200", "session3", "/returns/2026-27/summary", _make_rec_data(["700", "800", "900"])),
    ]
    cm = memory.client_maps(sources=sources)
    # Since 3 sessions/clients visited 3 different year links, they should resolve to /returns/{v}/summary
    assert "/returns/{v}/summary" in cm
    assert len(cm["/returns/{v}/summary"]) == 3

