"""
sync_discovery.py
-----------------
Discovery v3, address book, and gossip for Sera Sync v3 (blueprint §4.3, WP P2-5).

- Beacon v3: UDP datagrams {"magic":"sera-sync-v3","office":<office_tag>,"dev":device_id,
  "name":device_name,"port":49159,"pair":bool}.
  Where office_tag = hmac(dek, b"sera-office-tag-v1").hexdigest()[:16].
  While pairing is open, also includes "office_name" and "pair_port":49158.
  Sent every 10 s to 255.255.255.255, directed broadcasts on active adapters, and unicast
  to all known member addresses.
- In-memory beacon sightings: incoming beacons are kept in memory and never written directly
  to master.db (prevents fake beacon flooding / disk growth).
- Address book: table _local_addresses(device_id, ip, port, last_ok_at, local_ok_at, source)
  in master.db. _local_* tables are local-only and never replicated or included in snapshots.
  Updated on every successful session; source = beacon|gossip|manual.
  - local_ok_at tracks when THIS PC had a successful connection.
  - last_ok_at tracks the latest known successful connection anywhere in the office.
- Gossip: session HELLO (P3-5) carries addresses: [{device_id, ip, port, last_ok_at}]
  seen in the last 7 days.
- Connect order per member: last_ok (worked on this PC) -> other known addresses (newest first)
  -> beacon address (temporary candidate from in-memory sightings).
- Diagnostics: if a member has addresses but none have worked on this PC for 10 min, warns:
  "Can't reach <name> — check that both PCs are on a Private network and that the Wi-Fi doesn't isolate devices".

No PySide6 imports here (§0 rule 7). Heavy/network modules are imported lazily (§0 rule 6).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

BEACON_V3_MAGIC = "sera-sync-v3"
BEACON_PORT = 49156
PAIRING_PORT = 49158
SYNC_PORT_V3 = 49159

OFFICE_TAG_INFO = b"sera-office-tag-v1"
OFFICE_TAG_LEN = 16

BEACON_INTERVAL_SECONDS = 10.0
ADDRESS_BOOK_TABLE = "_local_addresses"
GOSSIP_MAX_AGE_DAYS = 7
GOSSIP_MAX_ENTRIES = 500
MAX_BEACON_SIGHTINGS = 500
BEACON_SIGHTING_MAX_AGE_SECONDS = 60.0
UNREACHABLE_THRESHOLD_SECONDS = 600  # 10 minutes
FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 60  # Clock skew clamp
MAX_STRING_LEN = 200

SOURCE_BEACON = "beacon"
SOURCE_GOSSIP = "gossip"
SOURCE_MANUAL = "manual"

_VALID_SOURCES = frozenset({SOURCE_BEACON, SOURCE_GOSSIP, SOURCE_MANUAL})
_HEX16 = re.compile(r"[0-9a-f]{16}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")


# ---------------------------------------------------------------- Office tag & helpers

def compute_office_tag(dek: bytes) -> str:
    """Computes the 16-character hex office tag from the office DEK:

    office_tag = hmac(dek, b"sera-office-tag-v1", sha256).hexdigest()[:16]
    """
    if not isinstance(dek, (bytes, bytearray)):
        raise TypeError("dek must be bytes")
    if len(dek) != 32:
        raise ValueError("dek must be exactly 32 bytes")
    return hmac.new(dek, OFFICE_TAG_INFO, hashlib.sha256).hexdigest()[:OFFICE_TAG_LEN]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_utc_iso() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_and_normalize_utc(ts: str | datetime | None) -> tuple[datetime | None, str | None]:
    """Parses and normalizes a timestamp to UTC datetime and UTC ISO string (ending with 'Z')."""
    if ts is None:
        return None, None
    if isinstance(ts, datetime):
        dt = ts
    else:
        try:
            dt = datetime.fromisoformat(str(ts).strip())
        except Exception:
            return None, None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Clean microseconds for deterministic formatting
    dt = dt.replace(microsecond=0)
    iso_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt, iso_str


def _is_valid_ip(ip_str: str) -> bool:
    try:
        import ipaddress
        ipaddress.ip_address(str(ip_str).strip())
        return True
    except Exception:
        return False


def _is_valid_gossip_ip(ip_str: str) -> bool:
    """Validates that an IP address from gossip is a valid, routable unicast IPv4 address.

    Rejects 0.0.0.0, 255.255.255.255, loopback (127.*), link-local (169.254.*),
    and multicast (224.* - 239.*).
    """
    try:
        import ipaddress
        ip = ipaddress.ip_address(str(ip_str).strip())
        if ip.version != 4:
            return False
        if (
            ip.is_unspecified
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or str(ip) == "255.255.255.255"
        ):
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- Beacon payload & parsing

def make_beacon_payload(
    office_tag: str,
    device_id: str,
    device_name: str,
    port: int = SYNC_PORT_V3,
    pair: bool = False,
    office_name: str | None = None,
    pair_port: int = PAIRING_PORT,
    beacon_port: int = BEACON_PORT,
) -> dict:
    """Builds a Sera Sync v3 beacon payload."""
    payload = {
        "magic": BEACON_V3_MAGIC,
        "office": str(office_tag),
        "dev": str(device_id),
        "name": str(device_name)[:MAX_STRING_LEN],
        "port": int(port),
        "pair": bool(pair),
    }
    if beacon_port != BEACON_PORT and (1 <= beacon_port <= 65535):
        payload["bport"] = int(beacon_port)
    if pair:
        if office_name is not None:
            payload["office_name"] = str(office_name)[:MAX_STRING_LEN]
        payload["pair_port"] = int(pair_port)
    return payload


def encode_beacon(payload: dict) -> bytes:
    """Encodes beacon payload to UTF-8 JSON bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_beacon(data: bytes, sender_ip: str | None = None) -> dict | None:
    """Parses and validates a Sera Sync v3 beacon datagram.

    Returns the parsed dict (with 'ip' added if sender_ip provided), or None if invalid.
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("magic") != BEACON_V3_MAGIC:
        return None

    office = payload.get("office")
    if not isinstance(office, str) or not _HEX16.match(office):
        return None

    dev = payload.get("dev")
    if not isinstance(dev, str) or not _HEX32.match(dev):
        return None

    name = payload.get("name")
    if not isinstance(name, str) or not name or len(name) > MAX_STRING_LEN:
        return None

    port = payload.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        return None

    pair = payload.get("pair")
    if not isinstance(pair, bool):
        return None

    res = {
        "magic": BEACON_V3_MAGIC,
        "office": office,
        "dev": dev,
        "name": name,
        "port": port,
        "pair": pair,
    }
    if sender_ip:
        res["ip"] = str(sender_ip)

    bport = payload.get("bport")
    if isinstance(bport, int) and (1 <= bport <= 65535):
        res["bport"] = bport

    if pair:
        office_name = payload.get("office_name")
        if isinstance(office_name, str) and len(office_name) <= MAX_STRING_LEN:
            res["office_name"] = office_name
        pair_port = payload.get("pair_port")
        if isinstance(pair_port, int) and (1 <= pair_port <= 65535):
            res["pair_port"] = pair_port

    return res


# ---------------------------------------------------------------- Address book (master.db)

@contextmanager
def _transaction(conn):
    if getattr(conn, "in_transaction", False):
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def ensure_address_book_table(conn) -> None:
    """Ensures the _local_addresses table exists in master.db with the v3 schema."""
    with _transaction(conn):
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ADDRESS_BOOK_TABLE} (
                device_id TEXT NOT NULL,
                ip TEXT NOT NULL,
                port INTEGER NOT NULL,
                last_ok_at TEXT,
                local_ok_at TEXT,
                source TEXT NOT NULL,
                PRIMARY KEY (device_id, ip, port)
            )
            """
        )
        # Handle migration if local_ok_at column was missing
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({ADDRESS_BOOK_TABLE})").fetchall()]
        if "local_ok_at" not in columns:
            conn.execute(f"ALTER TABLE {ADDRESS_BOOK_TABLE} ADD COLUMN local_ok_at TEXT")

        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_local_addresses_dev ON {ADDRESS_BOOK_TABLE}(device_id)"
        )


def record_successful_session(
    conn,
    device_id: str,
    ip: str,
    port: int,
    ok_at: str | datetime | None = None,
    source: str | None = None,
) -> None:
    """Updates _local_addresses on a successful sync session with THIS PC.

    Both local_ok_at (worked here) and last_ok_at (latest known anywhere) are updated.
    Preserves existing source unless an explicit source is provided.
    """
    if not _HEX32.match(str(device_id)):
        return
    if not _is_valid_ip(str(ip)):
        return
    port = int(port)
    if not (1 <= port <= 65535):
        return

    _, timestamp_iso = _parse_and_normalize_utc(ok_at)
    if not timestamp_iso:
        timestamp_iso = _now_utc_iso()

    with _transaction(conn):
        row = conn.execute(
            f"SELECT source FROM {ADDRESS_BOOK_TABLE} WHERE device_id = ? AND ip = ? AND port = ?",
            (device_id, ip, port),
        ).fetchone()

        if row is None:
            actual_source = source if (source in _VALID_SOURCES) else SOURCE_MANUAL
            conn.execute(
                f"""
                INSERT INTO {ADDRESS_BOOK_TABLE} (device_id, ip, port, last_ok_at, local_ok_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device_id, ip, port, timestamp_iso, timestamp_iso, actual_source),
            )
        else:
            actual_source = source if (source in _VALID_SOURCES) else row[0]
            conn.execute(
                f"""
                UPDATE {ADDRESS_BOOK_TABLE}
                SET last_ok_at = ?, local_ok_at = ?, source = ?
                WHERE device_id = ? AND ip = ? AND port = ?
                """,
                (timestamp_iso, timestamp_iso, actual_source, device_id, ip, port),
            )


def upsert_address(
    conn,
    device_id: str,
    ip: str,
    port: int,
    source: str = SOURCE_BEACON,
    last_ok_at: str | datetime | None = None,
    local_ok_at: str | datetime | None = None,
) -> bool:
    """Inserts or updates an address.

    Only updates last_ok_at / local_ok_at if the incoming timestamp is newer and not future-dated.
    Never overwrites a valid timestamp with None.
    Returns True if a row was inserted or updated.
    """
    if not _HEX32.match(str(device_id)):
        return False
    if not _is_valid_ip(str(ip)):
        return False
    port = int(port)
    if not (1 <= port <= 65535):
        return False
    if source not in _VALID_SOURCES:
        source = SOURCE_BEACON

    now_utc = _now_utc()
    max_allowed = now_utc + timedelta(seconds=FUTURE_TIMESTAMP_TOLERANCE_SECONDS)

    dt_last_ok, iso_last_ok = _parse_and_normalize_utc(last_ok_at)
    if dt_last_ok and dt_last_ok > max_allowed:
        # Future-dated timestamp rejected
        dt_last_ok, iso_last_ok = None, None

    dt_local_ok, iso_local_ok = _parse_and_normalize_utc(local_ok_at)
    if dt_local_ok and dt_local_ok > max_allowed:
        dt_local_ok, iso_local_ok = None, None

    with _transaction(conn):
        row = conn.execute(
            f"SELECT last_ok_at, local_ok_at, source FROM {ADDRESS_BOOK_TABLE} WHERE device_id = ? AND ip = ? AND port = ?",
            (device_id, ip, port),
        ).fetchone()

        if row is None:
            conn.execute(
                f"""
                INSERT INTO {ADDRESS_BOOK_TABLE} (device_id, ip, port, last_ok_at, local_ok_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device_id, ip, port, iso_last_ok, iso_local_ok, source),
            )
            return True

        curr_last_raw, curr_local_raw, curr_source = row[0], row[1], row[2]
        curr_last_dt, _ = _parse_and_normalize_utc(curr_last_raw)
        curr_local_dt, _ = _parse_and_normalize_utc(curr_local_raw)

        new_last_iso = curr_last_raw
        if dt_last_ok:
            if not curr_last_dt or dt_last_ok > curr_last_dt:
                new_last_iso = iso_last_ok

        new_local_iso = curr_local_raw
        if dt_local_ok:
            if not curr_local_dt or dt_local_ok > curr_local_dt:
                new_local_iso = iso_local_ok

        new_source = curr_source
        if source == SOURCE_MANUAL or (curr_source == SOURCE_BEACON and source == SOURCE_GOSSIP):
            new_source = source

        changed = (new_last_iso != curr_last_raw) or (new_local_iso != curr_local_raw) or (new_source != curr_source)
        if changed:
            conn.execute(
                f"""
                UPDATE {ADDRESS_BOOK_TABLE}
                SET last_ok_at = ?, local_ok_at = ?, source = ?
                WHERE device_id = ? AND ip = ? AND port = ?
                """,
                (new_last_iso, new_local_iso, new_source, device_id, ip, port),
            )
            return True

        return False


def remove_address(conn, device_id: str, ip: str, port: int) -> bool:
    """Removes a specific address for a device."""
    with _transaction(conn):
        cur = conn.execute(
            f"DELETE FROM {ADDRESS_BOOK_TABLE} WHERE device_id = ? AND ip = ? AND port = ?",
            (device_id, ip, int(port)),
        )
        return cur.rowcount > 0


def get_known_addresses(conn, device_id: str | None = None) -> list[dict]:
    """Returns list of known addresses from _local_addresses."""
    if device_id:
        rows = conn.execute(
            f"SELECT device_id, ip, port, last_ok_at, local_ok_at, source FROM {ADDRESS_BOOK_TABLE} WHERE device_id = ?",
            (device_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT device_id, ip, port, last_ok_at, local_ok_at, source FROM {ADDRESS_BOOK_TABLE}"
        ).fetchall()

    return [
        {
            "device_id": r[0],
            "ip": r[1],
            "port": int(r[2]),
            "last_ok_at": r[3],
            "local_ok_at": r[4],
            "source": r[5],
        }
        for r in rows
    ]


def get_all_destinations(conn) -> set[tuple[str, int]]:
    """Returns set of all (ip, port) destinations in _local_addresses."""
    rows = conn.execute(f"SELECT DISTINCT ip, port FROM {ADDRESS_BOOK_TABLE}").fetchall()
    return {(r[0], int(r[1])) for r in rows}


# ---------------------------------------------------------------- Connect order

def get_connect_order(
    conn,
    device_id: str,
    beacon_addr: tuple[str, int] | None = None,
) -> list[tuple[str, int]]:
    """Connect order per member:

    last_ok (worked on this PC) -> other known addresses (newest first) -> beacon address.
    """
    rows = conn.execute(
        f"SELECT ip, port, last_ok_at, local_ok_at FROM {ADDRESS_BOOK_TABLE} WHERE device_id = ?",
        (device_id,),
    ).fetchall()

    parsed_rows = []
    for r in rows:
        addr = (r[0], int(r[1]))
        last_dt, _ = _parse_and_normalize_utc(r[2])
        local_dt, _ = _parse_and_normalize_utc(r[3])
        parsed_rows.append({
            "addr": addr,
            "last_dt": last_dt,
            "local_dt": local_dt,
        })

    result: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    # 1. last_ok: highest local_ok_at (worked on this PC)
    local_ok_candidates = [r for r in parsed_rows if r["local_dt"] is not None]
    if local_ok_candidates:
        local_ok_candidates.sort(key=lambda r: r["local_dt"], reverse=True)
        top_local = local_ok_candidates[0]["addr"]
        result.append(top_local)
        seen.add(top_local)
    elif parsed_rows:
        # Fallback to highest last_ok_at if no local_ok_at is recorded
        with_last = [r for r in parsed_rows if r["last_dt"] is not None]
        if with_last:
            with_last.sort(key=lambda r: r["last_dt"], reverse=True)
            top_last = with_last[0]["addr"]
            result.append(top_last)
            seen.add(top_last)

    # 2. Other known addresses: ordered by last_ok_at DESC, then nulls
    remaining = [r for r in parsed_rows if r["addr"] not in seen]
    with_ts = [r for r in remaining if r["last_dt"] is not None]
    without_ts = [r for r in remaining if r["last_dt"] is None]

    with_ts.sort(key=lambda r: r["last_dt"], reverse=True)
    for r in with_ts:
        addr = r["addr"]
        if addr not in seen:
            seen.add(addr)
            result.append(addr)

    for r in without_ts:
        addr = r["addr"]
        if addr not in seen:
            seen.add(addr)
            result.append(addr)

    # 3. Beacon address (temporary candidate from in-memory sighting)
    if beacon_addr:
        b_addr = (beacon_addr[0], int(beacon_addr[1]))
        if b_addr not in seen:
            seen.add(b_addr)
            result.append(b_addr)

    return result


# ---------------------------------------------------------------- Gossip

def export_gossip_addresses(
    conn,
    max_age_days: int = GOSSIP_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> list[dict]:
    """Exports addresses seen in the last 7 days for the session HELLO."""
    current_time = now or _now_utc()
    cutoff = current_time - timedelta(days=max_age_days)

    rows = conn.execute(
        f"SELECT device_id, ip, port, last_ok_at FROM {ADDRESS_BOOK_TABLE} WHERE last_ok_at IS NOT NULL"
    ).fetchall()

    valid_entries = []
    for r in rows:
        dev_id = r[0]
        ip = r[1]
        port = int(r[2])
        raw_ts = r[3]
        if not _is_valid_gossip_ip(ip):
            continue
        dt, iso_str = _parse_and_normalize_utc(raw_ts)
        if dt and dt >= cutoff:
            valid_entries.append({
                "device_id": dev_id,
                "ip": ip,
                "port": port,
                "last_ok_at": iso_str,
                "dt": dt,
            })

    valid_entries.sort(key=lambda x: x["dt"], reverse=True)
    return [
        {
            "device_id": x["device_id"],
            "ip": x["ip"],
            "port": x["port"],
            "last_ok_at": x["last_ok_at"],
        }
        for x in valid_entries
    ]


def merge_gossip_addresses(
    conn,
    addresses: Sequence[dict],
    own_device_id: str | None = None,
    is_member: Callable[[str], bool] | None = None,
    max_age_days: int = GOSSIP_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> int:
    """Merges gossiped addresses into _local_addresses.

    - Skips own_device_id and non-members.
    - Caps batch size at GOSSIP_MAX_ENTRIES.
    - Rejects special/non-unicast IPs (0.0.0.0, 255.255.255.255, loopback, link-local, multicast).
    - Clamps/rejects future timestamps (> now + 60s) and items older than max_age_days.
    - Updates last_ok_at (latest known across office), NEVER touches local_ok_at.
    Returns the count of inserted or updated entries.
    """
    current_time = now or _now_utc()
    cutoff = current_time - timedelta(days=max_age_days)
    max_allowed = current_time + timedelta(seconds=FUTURE_TIMESTAMP_TOLERANCE_SECONDS)
    merged_count = 0

    for item in list(addresses)[:GOSSIP_MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        dev = item.get("device_id")
        ip = item.get("ip")
        port = item.get("port")
        last_ok = item.get("last_ok_at")

        if not isinstance(dev, str) or not _HEX32.match(dev):
            continue
        if own_device_id and dev == own_device_id:
            continue
        if is_member and not is_member(dev):
            continue
        if not isinstance(ip, str) or not _is_valid_gossip_ip(ip):
            continue
        if not isinstance(port, int) or not (1 <= port <= 65535):
            continue
        if not isinstance(last_ok, str):
            continue

        dt, iso_str = _parse_and_normalize_utc(last_ok)
        if dt is None or dt < cutoff or dt > max_allowed:
            continue

        if upsert_address(conn, dev, ip, port, source=SOURCE_GOSSIP, last_ok_at=iso_str):
            merged_count += 1

    return merged_count


# ---------------------------------------------------------------- Diagnostics

def check_member_unreachable(
    conn,
    device_id: str,
    member_name: str,
    threshold_seconds: int = UNREACHABLE_THRESHOLD_SECONDS,
    now: datetime | None = None,
) -> str | None:
    """If a member has addresses but none have worked on this PC for 10 min, returns diagnostic warning.

    Evaluates local_ok_at (success on THIS PC).
    """
    rows = conn.execute(
        f"SELECT local_ok_at FROM {ADDRESS_BOOK_TABLE} WHERE device_id = ?",
        (device_id,),
    ).fetchall()

    if not rows:
        return None

    current_time = now or _now_utc()
    cutoff = current_time - timedelta(seconds=threshold_seconds)

    any_recent_local_ok = False
    for r in rows:
        raw_ts = r[0]
        if raw_ts:
            dt, _ = _parse_and_normalize_utc(raw_ts)
            if dt and dt >= cutoff:
                any_recent_local_ok = True
                break

    if not any_recent_local_ok:
        return (
            f"Can't reach {member_name} — check that both PCs are on a Private network "
            "and that the Wi-Fi doesn't isolate devices"
        )
    return None


def get_unreachable_member_warnings(
    conn,
    members: Sequence[dict],
    threshold_seconds: int = UNREACHABLE_THRESHOLD_SECONDS,
    now: datetime | None = None,
) -> dict[str, str]:
    """Returns a dict {device_id: warning_text} for all unreachable members."""
    warnings = {}
    for m in members:
        dev_id = m.get("device_id")
        name = m.get("name") or dev_id
        if dev_id:
            msg = check_member_unreachable(conn, dev_id, name, threshold_seconds=threshold_seconds, now=now)
            if msg:
                warnings[dev_id] = msg
    return warnings


# ---------------------------------------------------------------- DiscoveryService

_OWN_IPS_CACHE: set[str] = set()
_OWN_IPS_CACHE_TIME: float = 0.0


def get_own_ips(force_refresh: bool = False) -> set[str]:
    """Returns the set of local IPv4 addresses. Cached for 15 s."""
    global _OWN_IPS_CACHE, _OWN_IPS_CACHE_TIME
    now = time.monotonic()
    if not force_refresh and _OWN_IPS_CACHE and (now - _OWN_IPS_CACHE_TIME < 15.0):
        return set(_OWN_IPS_CACHE)

    ips: set[str] = set()
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if getattr(ip_info, "is_IPv4", False) and isinstance(ip_info.ip, str):
                    ips.add(ip_info.ip)
    except Exception:
        pass
    try:
        for host in (socket.gethostname(), "localhost"):
            try:
                for addr in socket.gethostbyname_ex(host)[2]:
                    ips.add(addr)
            except Exception:
                pass
    except Exception:
        pass
    ips.add("127.0.0.1")
    _OWN_IPS_CACHE = set(ips)
    _OWN_IPS_CACHE_TIME = now
    return ips


def get_directed_broadcast_addresses() -> set[str]:
    """Computes directed broadcast addresses for all active IPv4 adapters.

    Skips loopback (127.*) and link-local (169.254.*).
    Lazy-imports ifaddr and ipaddress (§0 rule 6).
    """
    bcast_addrs: set[str] = set()
    try:
        import ifaddr
        import ipaddress
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if not getattr(ip_info, "is_IPv4", False):
                    continue
                ip_str = ip_info.ip
                if not isinstance(ip_str, str):
                    continue
                if ip_str.startswith("127.") or ip_str.startswith("169.254."):
                    continue
                prefix = getattr(ip_info, "network_prefix", None)
                if prefix is None or not (0 <= prefix < 31):
                    continue
                try:
                    net = ipaddress.IPv4Network(f"{ip_str}/{prefix}", strict=False)
                    bcast = str(net.broadcast_address)
                    if bcast and not bcast.startswith("127.") and not bcast.startswith("169.254."):
                        bcast_addrs.add(bcast)
                except Exception:
                    pass
    except Exception:
        pass
    return bcast_addrs


class DiscoveryService:
    """Manages UDP beacon broadcasting and listening for Sera Sync v3.

    - Beacons received are kept in memory and never written directly to master.db.
    - Uses open_db factory to obtain dedicated thread-safe database connections for worker
      threads (preferred over sharing a raw db_conn).
    - Dispatches to callbacks without holding database locks.
    - Supports listen=False sender-only mode to prevent duplicate socket binds when wired
      with sync_peer's on_v3_beacon hook.
    """

    def __init__(
        self,
        db_conn: Any = None,
        open_db: Callable[[], Any] | None = None,
        office_tag: str | None = None,
        device_id: str = "",
        device_name: str = "",
        bind_host: str = "0.0.0.0",
        beacon_port: int = BEACON_PORT,
        sync_port: int = SYNC_PORT_V3,
        pair_port: int = PAIRING_PORT,
        enable_broadcast: bool = True,
        manual_destinations: Sequence[tuple[str, int]] | None = None,
        is_member: Callable[[str], bool] | None = None,
        on_peer_discovered: Callable[[dict], None] | None = None,
        on_pairing_beacon: Callable[[dict], None] | None = None,
        listen: bool = True,
        on_poke: Callable[[bytes, str], Any] | None = None,
    ):
        self.db_conn = db_conn
        self.open_db = open_db
        self.office_tag = office_tag
        self.device_id = device_id
        self.device_name = device_name
        self.bind_host = bind_host
        self.beacon_port = beacon_port
        self.sync_port = sync_port
        self.pair_port = pair_port
        self.enable_broadcast = enable_broadcast
        self.manual_destinations: list[tuple[str, int]] = list(manual_destinations or [])
        self.is_member = is_member
        self.listen = bool(listen)
        # P3-5: sync pokes share the beacon port; they go to SyncEngine.handle_poke.
        self.on_poke = on_poke

        self.on_peer_discovered = on_peer_discovered
        self.on_pairing_beacon = on_pairing_beacon

        self._pairing_open = False
        self._office_name: str | None = None

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._udp_sock: socket.socket | None = None
        self._is_running = False

        # In-memory beacon sightings: device_id -> {"ip": str, "sync_port": int, "bport": int, "seen_at": float}
        self._sightings_lock = threading.Lock()
        self._beacon_sightings: dict[str, dict] = {}

        self._reply_lock = threading.Lock()
        self._unicast_reply_cooldown: dict[str, float] = {}

    def get_beacon_sighting(self, device_id: str, max_age_seconds: float = 60.0) -> tuple[str, int] | None:
        """Returns the in-memory (ip, sync_port) from the most recent beacon sighting, if fresh."""
        now = time.monotonic()
        with self._sightings_lock:
            s = self._beacon_sightings.get(device_id)
            if s and (now - s["seen_at"] <= max_age_seconds):
                return (s["ip"], s["sync_port"])
        return None

    def get_all_beacon_sightings(self, max_age_seconds: float = 60.0) -> list[dict]:
        """Returns all fresh in-memory beacon sightings."""
        now = time.monotonic()
        with self._sightings_lock:
            return [
                dict(s) for s in self._beacon_sightings.values()
                if (now - s["seen_at"] <= max_age_seconds)
            ]

    def add_manual_destination(self, ip: str, port: int):
        """Adds a manual (ip, port) destination for unicast beacons."""
        dest = (str(ip).strip(), int(port))
        if dest not in self.manual_destinations:
            self.manual_destinations.append(dest)

    def set_pairing(self, is_open: bool, office_name: str | None = None, pair_port: int | None = None):
        """Toggles pairing mode on the beacon."""
        self._pairing_open = bool(is_open)
        if office_name is not None:
            self._office_name = office_name
        if pair_port is not None:
            self.pair_port = pair_port

    def start(self):
        """Starts the beacon listener (if listen=True) and broadcaster threads (idempotent)."""
        if self._is_running:
            return
        self._is_running = True
        self._stop_event.clear()
        if self.listen:
            self._start_listener()
        self._start_sender()

    def stop(self):
        """Stops the discovery service and closes sockets."""
        if not self._is_running:
            return
        self._is_running = False
        self._stop_event.set()
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except OSError:
                pass
        for t in self._threads:
            try:
                t.join(timeout=1.5)
            except Exception:
                pass
        self._threads.clear()

    def handle_datagram(self, data: bytes, sender_ip: str, sock: socket.socket | None = None):
        """Processes an incoming beacon datagram (from internal listener or external dispatcher)."""
        if self.on_poke is not None and b'"poke"' in bytes(data[:512]):
            # A v3 poke (P3-5) is not a beacon; SyncEngine.handle_poke validates it.
            try:
                self.on_poke(bytes(data), sender_ip)
            except Exception:
                logger.exception("sync poke handler failed")
            return
        beacon = parse_beacon(data, sender_ip=sender_ip)
        if not beacon:
            return

        # Ignore beacons from ourselves
        if beacon.get("dev") == self.device_id:
            return

        # Check membership gate if provided
        dev = beacon["dev"]
        if self.is_member and not self.is_member(dev):
            return

        # Check if it matches our office
        if self.office_tag and beacon.get("office") == self.office_tag:
            peer_sync_port = beacon["port"]
            peer_bport = beacon.get("bport", self.beacon_port)

            # Store sighting strictly in-memory (never in master.db)
            now_mono = time.monotonic()
            with self._sightings_lock:
                if len(self._beacon_sightings) >= MAX_BEACON_SIGHTINGS and dev not in self._beacon_sightings:
                    cutoff_m = now_mono - BEACON_SIGHTING_MAX_AGE_SECONDS
                    self._beacon_sightings = {
                        k: v for k, v in self._beacon_sightings.items()
                        if v.get("seen_at", 0.0) >= cutoff_m
                    }
                    while len(self._beacon_sightings) >= MAX_BEACON_SIGHTINGS:
                        oldest_k = min(
                            self._beacon_sightings,
                            key=lambda k: self._beacon_sightings[k].get("seen_at", 0.0),
                        )
                        del self._beacon_sightings[oldest_k]

                self._beacon_sightings[dev] = {
                    "device_id": dev,
                    "ip": sender_ip,
                    "sync_port": peer_sync_port,
                    "bport": peer_bport,
                    "name": beacon["name"],
                    "seen_at": now_mono,
                }

            if self.on_peer_discovered:
                try:
                    self.on_peer_discovered(beacon)
                except Exception:
                    pass

            # Rate-limited unicast reply back to sender
            now_m = time.monotonic()
            with self._reply_lock:
                last_rep = self._unicast_reply_cooldown.get(sender_ip, 0.0)
                if now_m - last_rep >= 5.0:
                    self._unicast_reply_cooldown[sender_ip] = now_m
                    # Cleanup old cooldown entries
                    if len(self._unicast_reply_cooldown) > 200:
                        cutoff_m = now_m - 60.0
                        self._unicast_reply_cooldown = {
                            k: v for k, v in self._unicast_reply_cooldown.items() if v > cutoff_m
                        }
                    try:
                        self._send_unicast_reply(sock, sender_ip, peer_bport)
                    except Exception:
                        pass

        # If beacon has pairing open, notify pairing listener
        if beacon.get("pair") and self.on_pairing_beacon:
            try:
                self.on_pairing_beacon(beacon)
            except Exception:
                pass

    def _send_unicast_reply(self, sock: socket.socket | None, target_ip: str, target_port: int):
        if not self.office_tag and not self._pairing_open:
            return
        tag = self.office_tag or ("0" * OFFICE_TAG_LEN)
        payload = make_beacon_payload(
            tag,
            self.device_id,
            self.device_name,
            port=self.sync_port,
            pair=self._pairing_open,
            office_name=self._office_name,
            pair_port=self.pair_port,
            beacon_port=self.beacon_port,
        )
        packet = encode_beacon(payload)
        if sock:
            sock.sendto(packet, (target_ip, target_port))
        else:
            ephemeral_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                ephemeral_sock.sendto(packet, (target_ip, target_port))
            finally:
                ephemeral_sock.close()

    def _start_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_BROADCAST"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        sock.bind((self.bind_host, self.beacon_port))
        sock.settimeout(1.0)
        self._udp_sock = sock

        def listener_loop():
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                    self.handle_datagram(data, addr[0], sock=sock)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    continue
                except Exception:
                    continue

        t = threading.Thread(target=listener_loop, name="sync-v3-discovery-listener", daemon=True)
        t.start()
        self._threads.append(t)

    def _get_worker_db(self):
        """Returns a dedicated database connection for the worker thread.

        open_db factory is preferred for thread safety. If only raw db_conn is provided,
        logs a warning.
        """
        if self.open_db:
            return self.open_db()
        if self.db_conn is not None:
            logger.warning(
                "DiscoveryService using raw db_conn across worker threads; provide open_db factory for thread safety."
            )
            return self.db_conn
        return None

    def _start_sender(self):
        def sender_loop():
            while not self._stop_event.is_set():
                try:
                    self.send_beacons_now()
                except Exception:
                    pass
                self._stop_event.wait(BEACON_INTERVAL_SECONDS)

        t = threading.Thread(target=sender_loop, name="sync-v3-discovery-sender", daemon=True)
        t.start()
        self._threads.append(t)

    def send_beacons_now(self):
        """Sends a beacon v3 datagram to broadcast and known unicast destinations."""
        if not self.office_tag and not self._pairing_open:
            return

        tag = self.office_tag or ("0" * OFFICE_TAG_LEN)
        payload = make_beacon_payload(
            tag,
            self.device_id,
            self.device_name,
            port=self.sync_port,
            pair=self._pairing_open,
            office_name=self._office_name,
            pair_port=self.pair_port,
            beacon_port=self.beacon_port,
        )
        packet = encode_beacon(payload)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_BROADCAST"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            # 1. Broadcasts (if enabled)
            if self.enable_broadcast:
                try:
                    sock.sendto(packet, ("255.255.255.255", self.beacon_port))
                except Exception:
                    pass

                for bcast in get_directed_broadcast_addresses():
                    try:
                        sock.sendto(packet, (bcast, self.beacon_port))
                    except Exception:
                        pass

            # 2. Unicast to all known member addresses in address book and manual destinations
            destinations: set[tuple[str, int]] = set()

            # Manual destinations specify (ip, beacon_port) directly
            for ip, b_port in self.manual_destinations:
                destinations.add((ip, b_port))

            # Database addresses specify (ip, sync_port) -> send to target's beacon_port
            worker_db = self._get_worker_db()
            if worker_db:
                try:
                    for ip, _ in get_all_destinations(worker_db):
                        # Send to standard beacon port for that IP
                        destinations.add((ip, self.beacon_port))
                except Exception:
                    pass
                finally:
                    if self.open_db and worker_db is not self.db_conn:
                        try:
                            worker_db.close()
                        except Exception:
                            pass

            own_ips = get_own_ips()
            for ip, port in destinations:
                try:
                    # Skip sending to ourselves on the same beacon port
                    if ip in own_ips and port == self.beacon_port:
                        continue
                    sock.sendto(packet, (ip, port))
                except Exception:
                    pass
        finally:
            sock.close()
