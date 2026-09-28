"""
core/sgt_i/stats.py - container kinds from salted-hash statistics
====================================================================
Blueprint 14.4 step 3's last bullet (the container-kind table) and 14.5 rule 3. Unlike shapes.py
and invariants.py (pure functions over a batch the caller already holds), a container's kind can
only be told apart by counting across MANY pages and MANY sessions, so this module is the one place
in SGT-I that keeps a small amount of state on disk between runs.

* **The salt.** A random value generated once per PC (`generate_salt`, `secrets.token_hex`) and
  never written anywhere but `sgt_i_dir()` - not the atlas, not a proposal, never synced. Privacy
  rule 3 (14.5) exists because some containers (a PAN, say) come from a small enough space that an
  unsalted hash could just be tried exhaustively; a random per-PC salt closes that door.
* **The hash.** `salted_hash(value, salt, domain)` is an HMAC-SHA256 over the value, truncated to
  HASH_LEN hex characters - plenty to tell values apart, far too little to be "the value, encoded".
  `domain` ("value" vs "client") keeps a container's values and the client identifiers used to
  group them in separate hash spaces, so the two can never collide into one bucket by accident.
* **What is kept.** Per container: how many times it was seen, its distinct value-hashes (capped -
  see MAX_TRACKED_VALUES - so a container that turns out to be an identifier cannot grow this file
  without bound), and per client-hash, how many distinct value-hashes THAT client has shown (capped
  small - MAX_TRACKED_VALUES_PER_CLIENT - since telling "stable" from "changed" never needs more
  than a couple). No value, no client identifier, ever in the clear.
* **The table.** `classify_container` is the pure part: given only counts (never a hash, even),
  it picks the one row of 14.4 step 3's table the evidence supports - or None when it does not yet
  support exactly one row. `ContainerStats.classify` is the impure part: turns one container's
  stored hash counts into those plain counts and calls it.

Where maths stops (same as shapes.py/invariants.py): a kind is *structure*, proven by counting -
never *meaning*. Not wired into a component yet (no page has called `observe` in the running app) -
for the atlas (step 4, a later WP) to call once it accumulates a container's values across visits.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME
from .shapes import shape_grammar_confidence

__all__ = [
    "KINDS", "generate_salt", "salted_hash", "sgt_i_dir", "load_or_create_salt",
    "ContainerKind", "classify_container", "ContainerStats",
]

STATS_DIR_ENV = "SGT_I_STATS_DIR"
SALT_FILE = "salt"
STATS_FILE = "container_stats.json"
SAVE_EVERY_SEC = 60.0

SALT_BYTES = 32          # secrets.token_hex(32) -> a 64-hex-char (256-bit) key
HASH_LEN = 24            # hex chars kept from the HMAC - 96 bits, plenty to count distinct values
MAX_TRACKED_VALUES = 4096            # per container: bounds the file, not the count of observations
MAX_TRACKED_CLIENTS = 4096           # per container
MAX_TRACKED_VALUES_PER_CLIENT = 6    # only ever need 1 (stable) vs 2+ (changed) to tell the two apart

MIN_SUPPORT = 3          # rule-of-three bias: no kind is claimed from fewer than 3 observations
VOCAB_MAX_DISTINCT = 8   # 14.4 step 3: "a few values that repeat"

KINDS = ("template", "vocabulary", "profile", "dataset", "identifier")


def sgt_i_dir() -> Path:
    env = os.environ.get(STATS_DIR_ENV)
    return Path(env) if env else Path.home() / SERA_DATA_DIR_NAME / "sgt_i"


def generate_salt() -> str:
    """A fresh per-PC salt - random, and never derived from anything about this PC or its user."""
    return secrets.token_hex(SALT_BYTES)


def salted_hash(value: str, salt: str, domain: str = "value") -> str:
    """An HMAC-SHA256 of `value` under `salt`, truncated to HASH_LEN hex characters. `domain`
    ("value" or "client") is mixed in ahead of the value so the same string hashes differently
    depending on which space it is meant to count in."""
    key = bytes.fromhex(salt)
    msg = domain.encode("utf-8") + b"\0" + (value or "").encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:HASH_LEN]


@dataclass(frozen=True)
class ContainerKind:
    """One container's kind, from pure counts (blueprint 14.4 step 3's table).

    kind             - one of KINDS, or None when the counts do not (yet) pick out exactly one row.
    n                - how many observations this was decided from.
    distinct         - how many different value-hashes were seen (capped at MAX_TRACKED_VALUES;
                       a capped container's true distinct count may be higher, but that only ever
                       pushes a container further from "template"/"identifier"'s equality tests,
                       never falsely into them - see classify_container).
    clients          - how many different client-hashes contributed an observation.
    confidence       - the rule-of-three bound (shapes.shape_grammar_confidence) on the evidence
                       that decided `kind`, when that evidence had zero exceptions; None otherwise
                       (including whenever `kind` is None, or `kind` is "vocabulary", which is a
                       counting rule with no "zero exceptions" of its own to bound).
    """
    kind: Optional[str]
    n: int
    distinct: int
    clients: int
    confidence: Optional[float]


def classify_container(n: int, distinct: int, clients: int, stable_clients: int,
                        changed_clients: int, confidence: float = 0.95) -> ContainerKind:
    """Blueprint 14.4 step 3's container-kind table, from counts alone - never a value or a hash.
    `stable_clients` / `changed_clients` count only clients seen more than once for this container:
    stable = every sighting hashed the same, changed = at least two different hashes appeared for
    that one client. A client seen only once proves neither and is counted in neither, so it never
    forces a guess. Returns kind=None, rather than guess, whenever the counts fit more than one row
    or fewer than MIN_SUPPORT observations have been made."""
    if n < MIN_SUPPORT:
        return ContainerKind(None, n, distinct, clients, None)

    if distinct == 1 and clients >= 2:
        # every client, every sighting, the same value: template text, not data. (A single client
        # ever seen is not enough - that looks identical to a profile datapoint no second client
        # has yet disagreed with.)
        conf = shape_grammar_confidence(clients, 0, confidence)
        return ContainerKind("template", n, distinct, clients, conf)

    if distinct == n:
        # never once repeated - unique every time.
        conf = shape_grammar_confidence(n, 0, confidence)
        return ContainerKind("identifier", n, distinct, clients, conf)

    if stable_clients >= 2 and changed_clients == 0 and distinct > 1:
        # every client seen more than once kept its own value, but clients disagree with each
        # other - a profile datapoint.
        conf = shape_grammar_confidence(stable_clients, 0, confidence)
        return ContainerKind("profile", n, distinct, clients, conf)

    if changed_clients >= 1:
        # at least one client's own value moved from one sighting to the next - a dataset
        # datapoint. Zero-exception framing: of the clients seen more than once, none stayed put.
        conf = shape_grammar_confidence(changed_clients, 0, confidence) if stable_clients == 0 else None
        return ContainerKind("dataset", n, distinct, clients, conf)

    if 1 < distinct <= VOCAB_MAX_DISTINCT and n >= distinct * 2:
        # a handful of values, each one repeating - a vocabulary (status wording and the like).
        return ContainerKind("vocabulary", n, distinct, clients, None)

    return ContainerKind(None, n, distinct, clients, None)


def _new_cell() -> Dict[str, Any]:
    return {"n": 0, "hashes": {}, "hash_overflow": 0, "clients": {}, "client_overflow": 0}


class ContainerStats:
    """The salt and the per-container hash counts, persisted under sgt_i_dir() (privacy rule 3,
    14.5): counts and hashes only, and the salt file is never read by anything that syncs. `save()`
    is debounced (SAVE_EVERY_SEC) like sgt_health.SpecStats; pass `directory=` in tests so nothing
    real ever gets touched."""

    def __init__(self, directory: Optional[Path] = None, clock=None) -> None:
        self._dir = directory
        self._clock = clock or time.time
        self._salt: Optional[str] = None
        self.data: Dict[str, Any] = {"containers": {}}
        self._dirty = False
        self._last_save = 0.0
        self._load()

    @property
    def directory(self) -> Path:
        return self._dir or sgt_i_dir()

    @property
    def salt(self) -> str:
        if self._salt is None:
            self._salt = self._load_or_create_salt()
        return self._salt

    def observe(self, container: str, value: str, client: Optional[str] = None) -> None:
        """One more sighting of `value` in `container` (a container-path string, e.g. pairs.py's
        `container_path()` joined by any separator the caller likes - this module never inspects
        it). `client` is whatever the caller uses to tell clients apart (a PAN, a session key); it
        is hashed here and never kept in the clear. Only the resulting hashes are stored."""
        value_hash = salted_hash(value, self.salt, "value")
        cell = self.data["containers"].setdefault(container, _new_cell())
        cell["n"] += 1
        hashes = cell["hashes"]
        if value_hash in hashes:
            hashes[value_hash] += 1
        elif len(hashes) < MAX_TRACKED_VALUES:
            hashes[value_hash] = 1
        else:
            cell["hash_overflow"] += 1

        if client:
            client_hash = salted_hash(client, self.salt, "client")
            clients = cell["clients"]
            entry = clients.get(client_hash)
            if entry is None:
                if len(clients) >= MAX_TRACKED_CLIENTS:
                    cell["client_overflow"] += 1
                else:
                    entry = {"hashes": [], "n": 0}
                    clients[client_hash] = entry
            if entry is not None:
                entry["n"] += 1
                if value_hash not in entry["hashes"] and len(entry["hashes"]) < MAX_TRACKED_VALUES_PER_CLIENT:
                    entry["hashes"].append(value_hash)
        self._touch()

    def classify(self, container: str) -> Optional[ContainerKind]:
        """`classify_container` fed from this container's stored counts, or None when `container`
        has never been observed."""
        cell = self.data["containers"].get(container)
        if cell is None:
            return None
        stable = sum(1 for e in cell["clients"].values() if e["n"] >= 2 and len(e["hashes"]) == 1)
        changed = sum(1 for e in cell["clients"].values() if e["n"] >= 2 and len(e["hashes"]) >= 2)
        return classify_container(cell["n"], len(cell["hashes"]), len(cell["clients"]), stable, changed)

    # ── persistence ────────────────────────────────────────────────────────────
    def _touch(self, force: bool = False) -> None:
        self._dirty = True
        now = self._clock()
        if force or now - self._last_save >= SAVE_EVERY_SEC:
            self.save()

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / STATS_FILE
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(path)
            self._dirty = False
            self._last_save = self._clock()
        except OSError:
            pass

    def _load(self) -> None:
        try:
            loaded = json.loads((self.directory / STATS_FILE).read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("containers"), dict):
                self.data["containers"] = loaded["containers"]
        except (OSError, ValueError):
            pass

    def _load_or_create_salt(self) -> str:
        return load_or_create_salt(self.directory)


def load_or_create_salt(directory: Optional[Path] = None) -> str:
    """This PC's salt from `directory` (default sgt_i_dir()), created on first use. Shared by
    every SGT-I module that hashes (stats, the atlas's private side)."""
    directory = directory or sgt_i_dir()
    path = directory / SALT_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    salt = generate_salt()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(salt, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return salt
