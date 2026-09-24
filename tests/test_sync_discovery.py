"""Tests for sync_discovery.py (Sera Sync v3, WP P2-5): Discovery v3, address book, gossip.

Accept requirements from docs/sera-sync-v3-blueprint.md §5 P2-5 and review resolutions:
- Tests for the address ordering (local_ok comes first, other known newest first, beacon last)
- Tests for gossip merge (7-day filter, future-date clamp, never touches local_ok_at)
- Localhost test: discovery with broadcast disabled
- Diagnostics for unreachable members (checks local_ok_at with 10-minute threshold)
- Beacons never write fake entries directly into master.db (in-memory sightings only)
- Beacon v3 format (office tag HMAC, pair port/office_name, length/hex validation)
- Shared UDP 49156 dispatch integration with sync_peer
- No PySide6 imports
"""

import ast
import hashlib
import os
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sync_discovery
from sync_discovery import (
    ADDRESS_BOOK_TABLE,
    BEACON_INTERVAL_SECONDS,
    BEACON_PORT,
    BEACON_V3_MAGIC,
    GOSSIP_MAX_AGE_DAYS,
    PAIRING_PORT,
    SOURCE_BEACON,
    SOURCE_GOSSIP,
    SOURCE_MANUAL,
    SYNC_PORT_V3,
    UNREACHABLE_THRESHOLD_SECONDS,
    DiscoveryService,
    check_member_unreachable,
    compute_office_tag,
    encode_beacon,
    ensure_address_book_table,
    export_gossip_addresses,
    get_all_destinations,
    get_connect_order,
    get_known_addresses,
    get_unreachable_member_warnings,
    make_beacon_payload,
    merge_gossip_addresses,
    parse_beacon,
    record_successful_session,
    remove_address,
    upsert_address,
)

DEK_A = b"\x01" * 32
DEK_B = b"\x02" * 32
DEV_1 = "11111111111111111111111111111111"
DEV_2 = "22222222222222222222222222222222"
DEV_3 = "33333333333333333333333333333333"


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_address_book_table(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------- beacon & tag

def test_office_tag_computation():
    tag1 = compute_office_tag(DEK_A)
    assert isinstance(tag1, str)
    assert len(tag1) == 16
    assert all(c in "0123456789abcdef" for c in tag1)

    # Idempotent
    assert compute_office_tag(DEK_A) == tag1

    # Different key produces different tag
    tag2 = compute_office_tag(DEK_B)
    assert tag2 != tag1

    # Manual HMAC-SHA256 check
    import hmac
    expected = hmac.new(DEK_A, b"sera-office-tag-v1", hashlib.sha256).hexdigest()[:16]
    assert tag1 == expected

    with pytest.raises((ValueError, TypeError)):
        compute_office_tag(b"short")


def test_beacon_v3_format_normal():
    tag = compute_office_tag(DEK_A)
    payload = make_beacon_payload(tag, DEV_1, "Reception-PC", port=49159, pair=False)
    assert payload == {
        "magic": BEACON_V3_MAGIC,
        "office": tag,
        "dev": DEV_1,
        "name": "Reception-PC",
        "port": 49159,
        "pair": False,
    }
    encoded = encode_beacon(payload)
    parsed = parse_beacon(encoded, sender_ip="192.168.1.50")
    assert parsed is not None
    assert parsed["magic"] == BEACON_V3_MAGIC
    assert parsed["office"] == tag
    assert parsed["dev"] == DEV_1
    assert parsed["name"] == "Reception-PC"
    assert parsed["port"] == 49159
    assert parsed["pair"] is False
    assert parsed["ip"] == "192.168.1.50"


def test_beacon_v3_format_pairing():
    tag = compute_office_tag(DEK_A)
    payload = make_beacon_payload(
        tag,
        DEV_1,
        "Admin-PC",
        port=49159,
        pair=True,
        office_name="Aman & Associates",
        pair_port=49158,
    )
    assert payload["pair"] is True
    assert payload["office_name"] == "Aman & Associates"
    assert payload["pair_port"] == 49158

    encoded = encode_beacon(payload)
    parsed = parse_beacon(encoded, sender_ip="192.168.1.10")
    assert parsed is not None
    assert parsed["pair"] is True
    assert parsed["office_name"] == "Aman & Associates"
    assert parsed["pair_port"] == 49158
    assert parsed["ip"] == "192.168.1.10"


def test_parse_beacon_invalid():
    assert parse_beacon(b"not json") is None
    assert parse_beacon(b"{}") is None
    # Wrong magic
    assert parse_beacon(b'{"magic":"sera-sync-v2","dev":"abc"}') is None
    # Non-hex dev or wrong length dev
    tag = compute_office_tag(DEK_A)
    bad_dev = make_beacon_payload(tag, "short_id", "PC")
    assert parse_beacon(encode_beacon(bad_dev)) is None
    # Non-hex office
    bad_office = {"magic": BEACON_V3_MAGIC, "office": "not_hex_office!!", "dev": DEV_1, "name": "PC", "port": 49159, "pair": False}
    assert parse_beacon(encode_beacon(bad_office)) is None
    # Name too long
    long_name = make_beacon_payload(tag, DEV_1, "X" * 300)
    assert parse_beacon(encode_beacon(long_name)) is not None
    assert len(parse_beacon(encode_beacon(long_name))["name"]) <= 200
    # Invalid port
    bad_port = make_beacon_payload(tag, DEV_1, "PC", port=99999)
    assert parse_beacon(encode_beacon(bad_port)) is None


# ---------------------------------------------------------------- address ordering (Accept)

def test_address_ordering_last_ok_first(db_conn):
    """Accept: Connect order per member starts with the last_ok address that worked on this PC."""
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t2 = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upsert_address(db_conn, DEV_1, "192.168.1.10", 49159, SOURCE_BEACON, local_ok_at=t1, last_ok_at=t1)
    upsert_address(db_conn, DEV_1, "192.168.1.20", 49159, SOURCE_GOSSIP, local_ok_at=t2, last_ok_at=t2)

    order = get_connect_order(db_conn, DEV_1)
    assert len(order) == 2
    # 192.168.1.20 has the newest local_ok_at, so it is tried first
    assert order[0] == ("192.168.1.20", 49159)
    assert order[1] == ("192.168.1.10", 49159)


def test_address_ordering_local_ok_beats_newer_gossip(db_conn):
    """Review Finding 2: Address that worked on THIS PC takes priority over a newer gossiped address."""
    now = datetime.now(timezone.utc)
    t_local = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_gossip_newer = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Addr 1 worked on this PC at t_local
    upsert_address(db_conn, DEV_1, "192.168.1.10", 49159, SOURCE_MANUAL, local_ok_at=t_local, last_ok_at=t_local)
    # Addr 2 was gossiped from another PC with a newer timestamp, but never worked on this PC (local_ok_at=None)
    upsert_address(db_conn, DEV_1, "192.168.1.20", 49159, SOURCE_GOSSIP, local_ok_at=None, last_ok_at=t_gossip_newer)

    order = get_connect_order(db_conn, DEV_1)
    # Addr 1 (local_ok on this PC) MUST come first!
    assert order[0] == ("192.168.1.10", 49159)
    assert order[1] == ("192.168.1.20", 49159)


def test_address_ordering_newest_first_and_nulls(db_conn):
    """Accept: other known addresses are ordered newest first, followed by never-connected."""
    now = datetime.now(timezone.utc)
    t_old = (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_mid = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_new = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    upsert_address(db_conn, DEV_1, "192.168.1.1", 49159, SOURCE_BEACON, last_ok_at=t_old, local_ok_at=None)
    upsert_address(db_conn, DEV_1, "192.168.1.2", 49159, SOURCE_GOSSIP, last_ok_at=t_new, local_ok_at=t_new)
    upsert_address(db_conn, DEV_1, "192.168.1.3", 49159, SOURCE_MANUAL, last_ok_at=t_mid, local_ok_at=None)
    upsert_address(db_conn, DEV_1, "192.168.1.4", 49159, SOURCE_MANUAL, last_ok_at=None, local_ok_at=None)

    order = get_connect_order(db_conn, DEV_1)
    expected = [
        ("192.168.1.2", 49159),  # last_ok (worked on this PC, t_new)
        ("192.168.1.3", 49159),  # other known (t_mid)
        ("192.168.1.1", 49159),  # other known (t_old)
        ("192.168.1.4", 49159),  # other known (never connected, last_ok_at is None)
    ]
    assert order == expected


def test_address_ordering_with_beacon_address(db_conn):
    """Accept: beacon address comes after known addresses, or deduped if already present."""
    t_ok = "2026-09-24T10:00:00Z"
    upsert_address(db_conn, DEV_1, "192.168.1.10", 49159, SOURCE_GOSSIP, local_ok_at=t_ok, last_ok_at=t_ok)
    upsert_address(db_conn, DEV_1, "192.168.1.20", 49159, SOURCE_MANUAL, last_ok_at=None, local_ok_at=None)

    # Case 1: beacon address is new -> placed at the end
    order = get_connect_order(db_conn, DEV_1, beacon_addr=("192.168.1.50", 49159))
    assert order == [
        ("192.168.1.10", 49159),
        ("192.168.1.20", 49159),
        ("192.168.1.50", 49159),
    ]

    # Case 2: beacon address already matches last_ok -> remains first, not duplicated
    order_dedup = get_connect_order(db_conn, DEV_1, beacon_addr=("192.168.1.10", 49159))
    assert order_dedup == [
        ("192.168.1.10", 49159),
        ("192.168.1.20", 49159),
    ]


def test_address_ordering_beacon_only(db_conn):
    """When no stored addresses exist, beacon address is returned."""
    order = get_connect_order(db_conn, DEV_2, beacon_addr=("10.0.0.5", 49159))
    assert order == [("10.0.0.5", 49159)]

    # When neither exists, returns empty list
    assert get_connect_order(db_conn, DEV_3) == []


# ---------------------------------------------------------------- gossip merge (Accept)

def test_gossip_export_filters_7_days(db_conn):
    """Accept: gossip carries addresses seen in the last 7 days."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_3_days_ago = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_6_days_ago = (now - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_8_days_ago = (now - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")

    upsert_address(db_conn, DEV_1, "192.168.1.1", 49159, SOURCE_BEACON, last_ok_at=t_3_days_ago)
    upsert_address(db_conn, DEV_2, "192.168.1.2", 49159, SOURCE_BEACON, last_ok_at=t_6_days_ago)
    upsert_address(db_conn, DEV_3, "192.168.1.3", 49159, SOURCE_BEACON, last_ok_at=t_8_days_ago)
    upsert_address(db_conn, DEV_3, "192.168.1.4", 49159, SOURCE_MANUAL, last_ok_at=None)

    exported = export_gossip_addresses(db_conn, max_age_days=7, now=now)
    assert len(exported) == 2
    devs = {x["device_id"] for x in exported}
    assert devs == {DEV_1, DEV_2}
    assert DEV_3 not in devs


def test_gossip_merge_adds_new_addresses(db_conn):
    """Accept: gossip merge adds previously unknown addresses without setting local_ok_at."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    incoming = [
        {"device_id": DEV_1, "ip": "192.168.1.10", "port": 49159, "last_ok_at": t_recent},
        {"device_id": DEV_2, "ip": "192.168.1.20", "port": 49159, "last_ok_at": t_recent},
    ]

    added = merge_gossip_addresses(db_conn, incoming, own_device_id=DEV_3, now=now)
    assert added == 2

    known = get_known_addresses(db_conn)
    assert len(known) == 2
    for item in known:
        assert item["source"] == SOURCE_GOSSIP
        assert item["last_ok_at"] == t_recent
        assert item["local_ok_at"] is None  # Never set by gossip!


def test_gossip_merge_never_overwrites_local_ok(db_conn):
    """Review Finding 2: Gossip merge never overwrites or clears local_ok_at."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_local = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_gossip = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    upsert_address(db_conn, DEV_1, "192.168.1.10", 49159, SOURCE_MANUAL, local_ok_at=t_local, last_ok_at=t_local)

    incoming = [{"device_id": DEV_1, "ip": "192.168.1.10", "port": 49159, "last_ok_at": t_gossip}]
    merge_gossip_addresses(db_conn, incoming, now=now)

    known = get_known_addresses(db_conn, DEV_1)
    assert known[0]["last_ok_at"] == t_gossip
    assert known[0]["local_ok_at"] == t_local  # Preserved intact!


def test_gossip_merge_rejects_future_timestamps(db_conn):
    """Review Finding 2: Future timestamps (from skewed clocks or 9999-01-01) are rejected."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    future_time = (now + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    insane_future = "9999-01-01T00:00:00Z"

    incoming = [
        {"device_id": DEV_1, "ip": "192.168.1.10", "port": 49159, "last_ok_at": future_time},
        {"device_id": DEV_2, "ip": "192.168.1.20", "port": 49159, "last_ok_at": insane_future},
    ]
    count = merge_gossip_addresses(db_conn, incoming, now=now)
    assert count == 0
    assert get_known_addresses(db_conn) == []


def test_gossip_merge_skips_own_device_and_non_members(db_conn):
    """Gossip does not record addresses for own_device_id and checks is_member."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_valid = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    incoming = [
        {"device_id": DEV_1, "ip": "192.168.1.99", "port": 49159, "last_ok_at": t_valid},
        {"device_id": DEV_2, "ip": "192.168.1.88", "port": 49159, "last_ok_at": t_valid},
    ]
    # DEV_1 is own_device_id; is_member rejects DEV_2
    count = merge_gossip_addresses(
        db_conn, incoming, own_device_id=DEV_1, is_member=lambda dev: dev != DEV_2, now=now
    )
    assert count == 0
    assert get_known_addresses(db_conn) == []


def test_gossip_merge_malformed_ignored(db_conn):
    """Malformed or invalid gossip entries are rejected safely."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    bad_items = [
        {},  # empty
        {"device_id": "bad", "ip": "1.2.3.4", "port": 49159, "last_ok_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"device_id": DEV_1, "ip": "not_an_ip", "port": 49159, "last_ok_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"device_id": DEV_1, "ip": "1.2.3.4", "port": -5, "last_ok_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"device_id": DEV_1, "ip": "1.2.3.4", "port": 49159, "last_ok_at": "invalid-date"},
        # Older than 7 days
        {"device_id": DEV_1, "ip": "1.2.3.4", "port": 49159, "last_ok_at": (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")},
    ]
    count = merge_gossip_addresses(db_conn, bad_items, now=now)
    assert count == 0
    assert get_known_addresses(db_conn) == []


# ---------------------------------------------------------------- diagnostics

def test_diagnostics_unreachable_member_checks_local_ok(db_conn):
    """Review Finding 2: Warning triggers when THIS PC has not reached the member for 10 min,

    even if another PC gossiped that it reached that member recently!
    """
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_15m_ago = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_2m_ago_gossip = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # This PC reached member 15 minutes ago, but another PC gossiped that it reached it 2 min ago
    upsert_address(
        db_conn,
        DEV_1,
        "192.168.1.10",
        49159,
        SOURCE_MANUAL,
        local_ok_at=t_15m_ago,
        last_ok_at=t_2m_ago_gossip,
    )

    warn = check_member_unreachable(db_conn, DEV_1, "Reception", threshold_seconds=600, now=now)
    # Warning MUST be shown on THIS PC!
    assert warn == "Can't reach Reception — check that both PCs are on a Private network and that the Wi-Fi doesn't isolate devices"

    # Also test batch helper
    members = [{"device_id": DEV_1, "name": "Reception"}]
    warnings = get_unreachable_member_warnings(db_conn, members, threshold_seconds=600, now=now)
    assert DEV_1 in warnings
    assert "Reception" in warnings[DEV_1]


def test_diagnostics_recent_local_ok_no_warning(db_conn):
    """If THIS PC worked in the last 10 minutes, no warning is returned."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_5m_ago = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upsert_address(db_conn, DEV_1, "192.168.1.10", 49159, SOURCE_BEACON, local_ok_at=t_5m_ago, last_ok_at=t_5m_ago)

    warn = check_member_unreachable(db_conn, DEV_1, "Reception", threshold_seconds=600, now=now)
    assert warn is None


def test_diagnostics_no_addresses_no_warning(db_conn):
    """If no addresses exist for the member, returns None."""
    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    warn = check_member_unreachable(db_conn, DEV_2, "Accountant", threshold_seconds=600, now=now)
    assert warn is None


# ---------------------------------------------------------------- session updates

def test_record_successful_session(db_conn):
    """Updating address book on successful session updates local_ok_at and last_ok_at."""
    record_successful_session(db_conn, DEV_1, "192.168.1.10", 49159, ok_at="2026-09-24T10:00:00Z")
    known = get_known_addresses(db_conn, DEV_1)
    assert len(known) == 1
    assert known[0]["last_ok_at"] == "2026-09-24T10:00:00Z"
    assert known[0]["local_ok_at"] == "2026-09-24T10:00:00Z"

    # Subsequent session updates both
    record_successful_session(db_conn, DEV_1, "192.168.1.10", 49159, ok_at="2026-09-24T11:00:00Z")
    known = get_known_addresses(db_conn, DEV_1)
    assert len(known) == 1
    assert known[0]["last_ok_at"] == "2026-09-24T11:00:00Z"
    assert known[0]["local_ok_at"] == "2026-09-24T11:00:00Z"

    destinations = get_all_destinations(db_conn)
    assert ("192.168.1.10", 49159) in destinations

    # Removal
    remove_address(db_conn, DEV_1, "192.168.1.10", 49159)
    assert get_known_addresses(db_conn, DEV_1) == []


# ---------------------------------------------------------------- beacon sightings & isolation (Accept)

def test_beacons_never_write_to_database():
    """Review Finding 1: Incoming beacons must NEVER write directly to master.db."""
    conn = sqlite3.connect(":memory:")
    ensure_address_book_table(conn)
    office_tag = compute_office_tag(DEK_A)

    svc = DiscoveryService(
        db_conn=conn,
        office_tag=office_tag,
        device_id=DEV_2,
        device_name="Node-B",
        bind_host="127.0.0.1",
        enable_broadcast=False,
    )

    # Deliver beacon directly via handle_datagram
    payload = make_beacon_payload(office_tag, DEV_1, "Fake-Or-Real-Node", port=49159)
    packet = encode_beacon(payload)
    svc.handle_datagram(packet, "192.168.1.99")

    # DB MUST remain completely empty!
    assert get_known_addresses(conn) == []

    # Sighting is accessible in memory
    sighting = svc.get_beacon_sighting(DEV_1)
    assert sighting == ("192.168.1.99", 49159)
    conn.close()


def test_discovery_with_broadcast_disabled_localhost():
    """Accept: Localhost test: discovery with broadcast disabled.

    Starts two DiscoveryServices on 127.0.0.1 with different UDP ports.
    Broadcast is disabled (enable_broadcast=False) on both nodes.
    Node A is given Node B's UDP beacon port in manual_destinations.
    Node A unicasts a beacon to Node B.
    Node B receives it, verifies office tag, records sighting in memory, and replies.
    """
    conn_a = sqlite3.connect(":memory:", check_same_thread=False)
    conn_b = sqlite3.connect(":memory:", check_same_thread=False)
    ensure_address_book_table(conn_a)
    ensure_address_book_table(conn_b)

    office_tag = compute_office_tag(DEK_A)

    discovered_by_b = []
    event_b = threading.Event()

    def on_peer_b(peer):
        discovered_by_b.append(peer)
        event_b.set()

    discovered_by_a = []
    event_a = threading.Event()

    def on_peer_a(peer):
        discovered_by_a.append(peer)
        event_a.set()

    # Pick dynamic free UDP ports
    sock_temp1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_temp1.bind(("127.0.0.1", 0))
    port_a = sock_temp1.getsockname()[1]
    sock_temp1.close()

    sock_temp2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_temp2.bind(("127.0.0.1", 0))
    port_b = sock_temp2.getsockname()[1]
    sock_temp2.close()

    svc_b = DiscoveryService(
        db_conn=conn_b,
        office_tag=office_tag,
        device_id=DEV_2,
        device_name="Node-B",
        bind_host="127.0.0.1",
        beacon_port=port_b,
        sync_port=49159,
        enable_broadcast=False,  # Broadcast strictly disabled!
        on_peer_discovered=on_peer_b,
    )
    svc_a = DiscoveryService(
        db_conn=conn_a,
        office_tag=office_tag,
        device_id=DEV_1,
        device_name="Node-A",
        bind_host="127.0.0.1",
        beacon_port=port_a,
        sync_port=49159,
        enable_broadcast=False,  # Broadcast strictly disabled!
        manual_destinations=[("127.0.0.1", port_b)],  # Node A knows Node B's UDP beacon port!
        on_peer_discovered=on_peer_a,
    )

    try:
        svc_b.start()
        svc_a.start()

        # Send unicast beacon from A
        svc_a.send_beacons_now()

        # Node B should receive Node A's unicast beacon and discover Node A
        assert event_b.wait(timeout=5.0), "Node B did not receive unicast beacon with broadcast disabled"
        assert len(discovered_by_b) >= 1
        peer_b = discovered_by_b[0]
        assert peer_b["dev"] == DEV_1
        assert peer_b["name"] == "Node-A"
        assert peer_b["port"] == 49159
        assert peer_b["ip"] == "127.0.0.1"

        # Check Node B recorded Node A in in-memory sightings
        sighting_on_b = svc_b.get_beacon_sighting(DEV_1)
        assert sighting_on_b == ("127.0.0.1", 49159)

        # Node A should receive Node B's unicast reply and discover Node B as well
        assert event_a.wait(timeout=5.0), "Node A did not receive unicast reply from Node B"
        assert len(discovered_by_a) >= 1
        peer_a = discovered_by_a[0]
        assert peer_a["dev"] == DEV_2
        assert peer_a["name"] == "Node-B"
        assert peer_a["port"] == 49159
        assert peer_a["ip"] == "127.0.0.1"

        sighting_on_a = svc_a.get_beacon_sighting(DEV_2)
        assert sighting_on_a == ("127.0.0.1", 49159)
    finally:
        svc_a.stop()
        svc_b.stop()
        conn_a.close()
        conn_b.close()


def test_sync_peer_dispatches_v3_beacon(tmp_path):
    """Review Finding 3: sync_peer listener dispatches sera-sync-v3 datagrams to on_v3_beacon."""
    import sync_peer
    received_v3 = []

    service = sync_peer.SyncPeerService(
        db_path=str(tmp_path / "test.db"),
        salt_path=str(tmp_path / "test.salt"),
        username="test_user",
        enable_broadcast=False,
    )
    service.on_v3_beacon = lambda data, ip: received_v3.append((data, ip))

    payload = make_beacon_payload("1234567890abcdef", DEV_1, "Node-A")
    raw = encode_beacon(payload)

    # Call _handle_beacon directly
    service._handle_beacon(raw, "192.168.1.100")
    assert len(received_v3) == 1
    assert received_v3[0][1] == "192.168.1.100"


def test_discovery_service_listen_false_and_ephemeral_reply():
    """Refinement A: listen=False skips listener thread, and handle_datagram(sock=None) sends reply via ephemeral socket."""
    tag = "1234567890abcdef"
    discovered = []

    svc = DiscoveryService(
        office_tag=tag,
        device_id=DEV_1,
        device_name="SenderNode",
        enable_broadcast=False,
        listen=False,
        on_peer_discovered=lambda b: discovered.append(b),
    )
    svc.start()
    try:
        assert svc._udp_sock is None, "Listener socket should not be opened when listen=False"
        assert len(svc._threads) == 1, "Only sender thread should be running"

        # Create a receiver socket on localhost to act as the peer
        rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx_sock.bind(("127.0.0.1", 0))
        rx_sock.settimeout(2.0)
        rx_port = rx_sock.getsockname()[1]

        try:
            # Send peer beacon to svc.handle_datagram with sock=None (as if routed from sync_peer)
            peer_payload = make_beacon_payload(tag, DEV_2, "PeerNode", port=49159, beacon_port=rx_port)
            peer_packet = encode_beacon(peer_payload)

            svc.handle_datagram(peer_packet, "127.0.0.1", sock=None)

            assert len(discovered) == 1
            assert discovered[0]["dev"] == DEV_2

            # Check that rx_sock received the unicast reply sent from ephemeral socket
            data, _ = rx_sock.recvfrom(4096)
            reply = parse_beacon(data)
            assert reply is not None
            assert reply["dev"] == DEV_1
            assert reply["name"] == "SenderNode"
        finally:
            rx_sock.close()
    finally:
        svc.stop()


def test_beacon_sightings_capping_and_pruning():
    """Refinement B: in-memory beacon sightings are capped at 500 entries to prevent RAM exhaustion."""
    tag = "1234567890abcdef"
    svc = DiscoveryService(
        office_tag=tag,
        device_id=DEV_1,
        device_name="TestNode",
        enable_broadcast=False,
        listen=False,
    )

    # Feed 550 distinct devices into handle_datagram
    for i in range(550):
        fake_dev = f"{i:032x}"
        payload = make_beacon_payload(tag, fake_dev, f"Dev-{i}")
        raw = encode_beacon(payload)
        svc.handle_datagram(raw, f"192.168.1.{(i % 200) + 1}", sock=None)

    # Sightings must be strictly capped at 500
    with svc._sightings_lock:
        sighting_count = len(svc._beacon_sightings)
    assert sighting_count == 500

    # Oldest devices (0 to 49) should have been evicted, newest (500 to 549) must be present
    newest_dev = f"{549:032x}"
    oldest_dev = f"{0:032x}"
    assert svc.get_beacon_sighting(newest_dev) is not None
    assert svc.get_beacon_sighting(oldest_dev) is None


def test_gossip_ip_validation(db_conn):
    """Refinement Minor: Reject special/invalid unicast IPs in gossip (0.0.0.0, 255.255.255.255, loopback, link-local, multicast)."""
    assert not sync_discovery._is_valid_gossip_ip("0.0.0.0")
    assert not sync_discovery._is_valid_gossip_ip("255.255.255.255")
    assert not sync_discovery._is_valid_gossip_ip("127.0.0.1")
    assert not sync_discovery._is_valid_gossip_ip("127.1.2.3")
    assert not sync_discovery._is_valid_gossip_ip("169.254.10.20")
    assert not sync_discovery._is_valid_gossip_ip("224.0.0.251")
    assert not sync_discovery._is_valid_gossip_ip("239.255.255.250")
    assert not sync_discovery._is_valid_gossip_ip("not-an-ip")
    assert sync_discovery._is_valid_gossip_ip("192.168.1.100")
    assert sync_discovery._is_valid_gossip_ip("10.0.0.1")

    now = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    gossip_batch = [
        {"device_id": DEV_2, "ip": "127.0.0.1", "port": 49159, "last_ok_at": ts},
        {"device_id": DEV_2, "ip": "169.254.1.1", "port": 49159, "last_ok_at": ts},
        {"device_id": DEV_2, "ip": "224.0.0.1", "port": 49159, "last_ok_at": ts},
        {"device_id": DEV_2, "ip": "255.255.255.255", "port": 49159, "last_ok_at": ts},
        {"device_id": DEV_2, "ip": "0.0.0.0", "port": 49159, "last_ok_at": ts},
        {"device_id": DEV_2, "ip": "192.168.1.55", "port": 49159, "last_ok_at": ts},
    ]

    merged = merge_gossip_addresses(db_conn, gossip_batch, now=now)
    assert merged == 1

    addrs = get_known_addresses(db_conn, DEV_2)
    assert len(addrs) == 1
    assert addrs[0]["ip"] == "192.168.1.55"

    # Export also filters out invalid IPs if any existed in database
    db_conn.execute(
        f"INSERT INTO {ADDRESS_BOOK_TABLE} (device_id, ip, port, last_ok_at, local_ok_at, source) "
        f"VALUES ('{DEV_1}', '127.0.0.1', 49159, '{ts}', '{ts}', 'manual')"
    )
    exported = export_gossip_addresses(db_conn, now=now)
    exported_ips = [e["ip"] for e in exported]
    assert "127.0.0.1" not in exported_ips
    assert "192.168.1.55" in exported_ips


def test_worker_db_factory_and_raw_conn_warning(caplog):
    """Refinement C: open_db factory is preferred for worker threads; warning logged if only raw db_conn is provided."""
    import logging

    calls = []

    def fake_factory():
        conn = sqlite3.connect(":memory:")
        ensure_address_book_table(conn)
        calls.append(conn)
        return conn

    svc_factory = DiscoveryService(
        open_db=fake_factory,
        office_tag="1234567890abcdef",
        device_id=DEV_1,
        device_name="TestNode",
        enable_broadcast=False,
        listen=False,
    )
    svc_factory.send_beacons_now()
    assert len(calls) == 1

    # Raw connection warning check
    raw_conn = sqlite3.connect(":memory:")
    ensure_address_book_table(raw_conn)
    svc_raw = DiscoveryService(
        db_conn=raw_conn,
        office_tag="1234567890abcdef",
        device_id=DEV_1,
        device_name="TestNode",
        enable_broadcast=False,
        listen=False,
    )
    with caplog.at_level(logging.WARNING):
        svc_raw.send_beacons_now()
    assert any("raw db_conn" in record.message for record in caplog.records)
    raw_conn.close()


def test_service_idempotent_start_stop():
    """Calling start() twice does not spawn duplicate threads, and stop() is clean."""
    svc = DiscoveryService(
        office_tag="1234567890abcdef",
        device_id=DEV_1,
        device_name="Test",
        bind_host="127.0.0.1",
        beacon_port=0,
        enable_broadcast=False,
    )
    svc.start()
    assert svc._is_running
    thread_count = len(svc._threads)
    svc.start()  # Idempotent call
    assert len(svc._threads) == thread_count

    svc.stop()
    assert not svc._is_running
    assert len(svc._threads) == 0


def test_no_pyside6_import():
    """Rule 7: sync_discovery must not import PySide6."""
    source_path = Path(sync_discovery.__file__).resolve()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PySide6" not in alias.name, f"Import of {alias.name} forbidden in {source_path.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "PySide6" not in node.module, f"Import from {node.module} forbidden in {source_path.name}"
