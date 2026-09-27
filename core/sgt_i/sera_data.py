"""
core/sgt_i/sera_data.py - Sera's own data: a read-only second opinion
======================================================================
Blueprint 14.4 step 8: "Sera already has the office's client list and the tracker. They are the
answer key." This module is the accessor - it never registers as a host.py component and never
changes what the Core captures. Everything it returns is second-opinion material for later steps
(W9-2's tracker expectations and self-healing consume it); nothing here writes to either database.

* **Read-only, for real.** Connections are opened `mode=ro` (SQLite refuses a write at the OS
  level) with `PRAGMA query_only = ON` on top, so a bug here cannot corrupt master.db or
  rawPayload.db even by accident. Only SELECTs are issued.
* **Cached.** Both databases are read at most once per `cache_ttl_sec` (default 30s); SGT-I runs
  on a per-page time budget (host.py) and must not pay a query-plus-decrypt cost every page.
* **OCR constraint correction** (14.4 step 8, "candidates from known confusions ... kept only if
  they pass the type's arithmetic"): a PAN/GSTIN that FAILS its shape is corrected only at the
  positions that violate that shape, using known optical confusions (O/0, I/1, S/5, B/8, Z/2);
  every combination of candidate substitutions is tried, and the correction is used only when
  exactly one survives the type's own check (GSTIN's checksum, or - PAN has none - a unique match
  against a client already on file). A value that is already shape-valid is never touched: rule
  14.4 step 8, "never swapped for another client's".
* **Name <-> PAN cross-check** and **masked-value confirmation** ("98XXXXXX12") are second
  opinions only; a masked value is compared in memory and never stored (14.5).

Privacy (14.5): only PAN/GSTIN/name already on file locally are read, never written anywhere else,
never sent anywhere. Nothing here is a proposal - it is consulted by later steps, which decide
what (if anything) reaches a row.
"""

import itertools
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import sqlcipher3.dbapi2 as sqlite3

__all__ = [
    "ClientRecord", "TrackerRow", "SeraData",
    "correct_shape", "gstin_checksum_ok", "recover_pan", "recover_gstin",
    "name_pan_mismatch", "masked_confirms",
    "PAN_SHAPE", "GSTIN_SHAPE",
]

CACHE_TTL_SEC = 30.0

# ── Shapes: expected class per position ─────────────────────────────────────────────────────────
# 'A' = letter, '9' = digit, '?' = alnum (not OCR-corrected; the type's own check settles it).
PAN_SHAPE = "A" * 5 + "9" * 4 + "A"                       # ABCDE1234F
GSTIN_SHAPE = "9" * 2 + "A" * 5 + "9" * 4 + "A" + "?" * 3  # 22ABCDE1234F1Z5 (last 3: entity/'Z'/checksum)

# 14.4 step 8's own confusion set, both directions. A digit position expecting a letter that
# could have come from more than one glyph (e.g. '0' from O, D or Q) is a genuine ambiguity -
# every candidate is tried, and "more than one survivor" means no correction, not a guess.
_LETTER_FOR_DIGIT: Dict[str, Tuple[str, ...]] = {
    "0": ("O", "D", "Q"), "1": ("I",), "5": ("S",), "8": ("B",), "2": ("Z",),
}
_DIGIT_FOR_LETTER: Dict[str, Tuple[str, ...]] = {
    "O": ("0",), "D": ("0",), "Q": ("0",), "I": ("1",), "S": ("5",), "B": ("8",), "Z": ("2",),
}
_MAX_WRONG_POSITIONS = 3   # more than this many wrong slots is not "one misread character" any more


def _is_wrong(ch: str, cls: str) -> bool:
    if cls == "A":
        return not ch.isalpha()
    if cls == "9":
        return not ch.isdigit()
    return False   # '?' is never "wrong"


def _shape_ok(value: str, shape: str) -> bool:
    return len(value) == len(shape) and not any(_is_wrong(c, s) for c, s in zip(value, shape))


def correct_shape(value: str, shape: str, max_wrong: int = _MAX_WRONG_POSITIONS) -> List[str]:
    """Every shape-valid candidate obtainable by fixing only the positions that violate `shape`,
    using known OCR confusions. Empty when `value` already matches (nothing to correct), a wrong
    position has no known confusion to fix it, or too many positions are wrong at once."""
    value = (value or "").upper()
    if len(value) != len(shape) or _shape_ok(value, shape):
        return []
    wrong = [i for i, (c, s) in enumerate(zip(value, shape)) if _is_wrong(c, s)]
    if not wrong or len(wrong) > max_wrong:
        return []
    options = []
    for i in wrong:
        table = _LETTER_FOR_DIGIT if shape[i] == "A" else _DIGIT_FOR_LETTER
        opts = table.get(value[i], ())
        if not opts:
            return []
        options.append(opts)
    candidates = set()
    for combo in itertools.product(*options):
        chars = list(value)
        for i, c in zip(wrong, combo):
            chars[i] = c
        candidates.add("".join(chars))
    return sorted(candidates)


def gstin_checksum_ok(value: str) -> bool:
    """A GSTIN's 15th character is a mod-36 check character over the first 14 (same maths as
    sgt/sgt_toolbox.c_gstin_checksum; kept local so sgt_i has no dependency on the Core)."""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    v = (value or "").upper()
    if len(v) != 15 or any(c not in alphabet for c in v):
        return False
    total = 0
    for i, c in enumerate(v[:14]):
        product = alphabet.index(c) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return alphabet[(36 - total % 36) % 36] == v[14]


def recover_gstin(raw: str, known_gstins: Iterable[str] = ()) -> Optional[str]:
    """A GSTIN that fails its shape, corrected exactly by its checksum (14.4 step 8). When more
    than one candidate passes the checksum, a unique match against `known_gstins` breaks the tie;
    otherwise the correction is ambiguous and None is returned."""
    raw_u = (raw or "").upper()
    candidates = correct_shape(raw_u, GSTIN_SHAPE)
    if not candidates:
        return None
    passing = [c for c in candidates if gstin_checksum_ok(c)]
    if len(passing) == 1:
        return passing[0]
    if len(passing) > 1:
        known = {g.upper() for g in known_gstins if g}
        matches = [c for c in passing if c in known]
        if len(matches) == 1:
            return matches[0]
    return None


def recover_pan(raw: str, known_pans: Iterable[str] = ()) -> Optional[str]:
    """A PAN that fails its shape has no checksum of its own (14.4 step 8), so recovery needs a
    unique match against a client already on file. A shape-valid PAN is never touched - it is
    never swapped for another client's."""
    raw_u = (raw or "").upper()
    candidates = correct_shape(raw_u, PAN_SHAPE)
    if not candidates:
        return None
    known = {p.upper() for p in known_pans if p}
    matches = [c for c in candidates if c in known]
    return matches[0] if len(matches) == 1 else None


_NAME_STOP_WORDS = {
    "pvt", "ltd", "limited", "llp", "and", "the", "co", "company", "private",
    "proprietor", "proprietorship", "firm", "mr", "mrs", "ms", "m", "s",
}


def _name_words(name: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
            if w not in _NAME_STOP_WORDS and len(w) > 1}


def name_pan_mismatch(name: str, pan: str, known_clients: Sequence["ClientRecord"]) -> bool:
    """True when `pan` belongs to a known client whose name shares no significant word with
    `name` - a sign the page was read under someone else's identity (14.4 step 8, "name <-> PAN
    cross-check ... catches wrong-client reads"). False when there is nothing to compare."""
    pan_u = (pan or "").strip().upper()
    if not pan_u or not name:
        return False
    client = next((c for c in known_clients if c.pan == pan_u), None)
    if client is None or not client.name:
        return False
    wa, wb = _name_words(client.name), _name_words(name)
    if not wa or not wb:
        return False
    return not (wa & wb)


def masked_confirms(masked: str, candidate: str) -> bool:
    """A masked value ("98XXXXXX12") confirms `candidate` when they are the same length and every
    unmasked character agrees. Neither value is ever stored by this function (14.5) - only the
    boolean leaves it."""
    if not masked or not candidate or len(masked) != len(candidate):
        return False
    mask_chars = set("X*.")
    for m, c in zip(masked.upper(), candidate.upper()):
        if m in mask_chars:
            continue
        if m != c:
            return False
    return True


# ── The accessor ─────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClientRecord:
    client_id: int
    pan: str
    gstin: str
    name: str


@dataclass(frozen=True)
class TrackerRow:
    client_id: Optional[int]
    portal: str
    period_label: str
    arn_number: str
    status: str


@dataclass(frozen=True)
class DueService:
    client_id: int
    service: str


def _find_columns(mcl_rows: Sequence[Tuple[int, str]]) -> Tuple[Optional[int], Optional[int], List[int]]:
    """Same label conventions database.py's own resolvers use: word-boundary PAN/GSTIN, excluding
    password columns and (for PAN) the company-name column."""
    pan_id = gstin_id = None
    name_ids: List[int] = []
    for cid, label in mcl_rows:
        up = (label or "").upper()
        if "PASS" in up:
            continue
        if pan_id is None and re.search(r"\bPAN\b", up) and "COMPAN" not in up:
            pan_id = cid
        elif gstin_id is None and re.search(r"\bGSTIN\b|\bGST\b", up):
            gstin_id = cid
        elif re.search(r"\bNAME\b|\bCOMPANY\b|\bPROPRIETOR\b|\bFIRM\b", up):
            name_ids.append(cid)
    return pan_id, gstin_id, name_ids


def _load_clients(conn: "sqlite3.Connection") -> List[ClientRecord]:
    mcl_rows = conn.execute("SELECT id, label FROM mcl_columns ORDER BY sort_order, id").fetchall()
    pan_id, gstin_id, name_ids = _find_columns(mcl_rows)
    col_ids = [c for c in (pan_id, gstin_id, *name_ids) if c is not None]
    if not col_ids:
        return []
    placeholders = ",".join("?" * len(col_ids))
    rows = conn.execute(
        "SELECT c.id, cv.column_id, cv.value FROM clients c "
        "JOIN client_values cv ON cv.client_id = c.id "
        "WHERE c.is_archived = 0 AND cv.column_id IN (%s)" % placeholders,
        col_ids,
    ).fetchall()
    by_client: Dict[int, Dict[int, str]] = {}
    for cid, col_id, value in rows:
        by_client.setdefault(cid, {})[col_id] = value or ""
    out = []
    for cid, vals in by_client.items():
        name = next((vals[nid] for nid in name_ids if vals.get(nid)), "")
        out.append(ClientRecord(
            client_id=cid,
            pan=(vals.get(pan_id) or "").strip().upper() if pan_id is not None else "",
            gstin=(vals.get(gstin_id) or "").strip().upper() if gstin_id is not None else "",
            name=name.strip(),
        ))
    return out


def _load_tracker_rows(conn: "sqlite3.Connection") -> List[TrackerRow]:
    rows = conn.execute(
        "SELECT client_id, portal, period_label, arn_number, status FROM tracker_dump"
    ).fetchall()
    return [TrackerRow(client_id=r[0], portal=r[1] or "", period_label=r[2] or "",
                        arn_number=r[3] or "", status=r[4] or "") for r in rows]


def _load_due_services(conn: "sqlite3.Connection") -> List[DueService]:
    rows = conn.execute(
        "SELECT cs.client_id, s.name FROM client_services cs "
        "JOIN services s ON s.id = cs.service_id "
        "JOIN clients c ON c.id = cs.client_id AND c.is_archived = 0"
    ).fetchall()
    return [DueService(client_id=r[0], service=r[1] or "") for r in rows]


def _load_known_values(conn: "sqlite3.Connection") -> Dict[int, Dict[str, str]]:
    """Every client_values entry, keyed by client then by the column's own label (self-healing,
    14.4 step 8) - not just PAN/GSTIN/name. Password columns are excluded, the same way
    `_find_columns` excludes them; nothing else is filtered, since an office may hold DOB, email
    or any other field under a label of its own choosing."""
    rows = conn.execute(
        "SELECT cv.client_id, mc.label, cv.value FROM client_values cv "
        "JOIN mcl_columns mc ON mc.id = cv.column_id "
        "JOIN clients c ON c.id = cv.client_id AND c.is_archived = 0 "
        "WHERE cv.value IS NOT NULL AND TRIM(cv.value) != ''"
    ).fetchall()
    out: Dict[int, Dict[str, str]] = {}
    for cid, label, value in rows:
        if "PASS" in (label or "").upper():
            continue
        out.setdefault(cid, {})[(label or "").strip()] = value
    return out


class SeraData:
    """Read-only, cached access to Sera's own client list and tracker rows (blueprint 14.4 step
    8). Opens its own connections read-only (`mode=ro` plus `PRAGMA query_only`) and never touches
    the Core's sessions or rows - callers only ever get data back, never write through it."""

    def __init__(self, db_path: str, hex_key: str, raw_db_path: str,
                 cache_ttl_sec: float = CACHE_TTL_SEC,
                 now: Callable[[], float] = time.monotonic) -> None:
        self._db_path = db_path
        self._hex_key = hex_key
        self._raw_db_path = raw_db_path
        self._ttl = float(cache_ttl_sec)
        self._now = now
        self._lock = threading.Lock()
        self._clients: List[ClientRecord] = []
        self._tracker: List[TrackerRow] = []
        self._due: List[DueService] = []
        self._known: Dict[int, Dict[str, str]] = {}
        self._loaded_at = float("-inf")

    def _connect(self, path: str) -> "sqlite3.Connection":
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        conn.execute("PRAGMA key = \"x'%s'\";" % self._hex_key)
        conn.execute("PRAGMA query_only = ON;")
        return conn

    def _refresh_if_stale(self) -> None:
        with self._lock:
            if self._now() - self._loaded_at < self._ttl:
                return
            try:
                conn = self._connect(self._db_path)
                try:
                    self._clients = _load_clients(conn)
                    self._due = _load_due_services(conn)
                    self._known = _load_known_values(conn)
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
            try:
                conn = self._connect(self._raw_db_path)
                try:
                    self._tracker = _load_tracker_rows(conn)
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
            self._loaded_at = self._now()

    def clients(self) -> List[ClientRecord]:
        self._refresh_if_stale()
        return list(self._clients)

    def tracker_rows(self, client_id: Optional[int] = None) -> List[TrackerRow]:
        self._refresh_if_stale()
        if client_id is None:
            return list(self._tracker)
        return [r for r in self._tracker if r.client_id == client_id]

    def due_services(self, client_id: Optional[int] = None) -> List[DueService]:
        self._refresh_if_stale()
        if client_id is None:
            return list(self._due)
        return [d for d in self._due if d.client_id == client_id]

    def known_values(self, client_id: int) -> Dict[str, str]:
        """This client's own field values, keyed by the label the office gave the column
        (14.4 step 8, self-healing) - never just PAN/GSTIN/name. Callers compare in memory and
        never store a value; only the label a match was found under may be kept."""
        self._refresh_if_stale()
        return dict(self._known.get(client_id, {}))

    def find_by_pan(self, pan: str) -> Optional[ClientRecord]:
        pan_u = (pan or "").strip().upper()
        if not pan_u:
            return None
        return next((c for c in self.clients() if c.pan == pan_u), None)

    def find_by_gstin(self, gstin: str) -> Optional[ClientRecord]:
        g = (gstin or "").strip().upper()
        if not g:
            return None
        return next((c for c in self.clients() if c.gstin == g), None)

    def recover_pan(self, raw: str) -> Optional[str]:
        if _shape_ok((raw or "").upper(), PAN_SHAPE):
            return None
        return recover_pan(raw, (c.pan for c in self.clients() if c.pan))

    def recover_gstin(self, raw: str) -> Optional[str]:
        if _shape_ok((raw or "").upper(), GSTIN_SHAPE):
            return None
        return recover_gstin(raw, (c.gstin for c in self.clients() if c.gstin))

    def name_pan_mismatch(self, name: str, pan: str) -> bool:
        return name_pan_mismatch(name, pan, self.clients())
