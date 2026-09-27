"""
core/sgt_i/assertions.py - the assertion checker: what a sentence claims (NegEx-style)
========================================================================================
Blueprint 14.4 step 6, first half. "Your return has been successfully e-verified" and "you will
receive an acknowledgement" both use the right words; only one of them says something happened.
This module tells the two apart from the words alone, the way NegEx tells a clinical finding's
negation apart from a positive statement in a patient note: trigger phrases, each with a scope,
no model.

Four classes (14.4 step 6's table):
  happened            - "Your return has been successfully e-verified"
  negated             - "e-Verification failed", "not yet filed"
  future_conditional  - "You will receive an acknowledgement...", "pending e-verification"
  reference_to_past   - "Acknowledgement Number of Original Return: ..."

Trigger words live in core/sgt_i/sgt_i_config.json under "assertions" - the same mechanism
covers the past-tense rule, the future-stepper rule and the ack gate on every portal (14.4 step
6), so a new portal's wording is a config change, never a code change.

NegEx scope: a "self" trigger asserts its class wherever it appears - most of our triggers are
this shape, since the domain's negative/future/reference wordings are their own fixed phrases
("failed", "of original return", "will receive"). A "pre" trigger is a generic negator (bare
"not", "could not be", ...) whose scope runs forward from itself to the next terminator (config's
"terminators": "but", "however", a comma, ...) or the end of the text; a "happened" trigger word
falling inside that scope is negated, not happened - "the e-verification could not be completed"
is negated even though "completed" alone would read as happened. "post" triggers are the mirror
(scope runs backward to the previous terminator) - the config ships none yet, since every
domain wording we have negates forward, but the mechanism is exercised by tests/test_sgt_i_
assertions.py so a future portal wording ("...was declined") is a config addition, not new code.

Priority when more than one class's triggers fire outside each other's scope: negated >
reference_to_past > future_conditional > happened - a negative or a reference to an earlier
filing reads before a bare positive word does (14.1's bug table: the original-return ack must
never win over "it happened").

This module classifies wording only. A stepper step not yet reached that already carries
"Successfully Verified" text (14.1's bug table) still classifies as `happened` here - telling
that apart from what has actually happened is step 6's *other* half (page kinds: a stepper's
un-reached step vs its current one), not this checker's job.
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CONFIG_PATH = Path(__file__).with_name("sgt_i_config.json")

ASSERTION_CLASSES: Tuple[str, ...] = ("negated", "reference_to_past", "future_conditional", "happened")

# Used only if sgt_i_config.json is missing or corrupt, so the checker degrades rather than dies -
# the same "set aside and carry on" spirit as atlas.py's own corrupt-file handling.
_FALLBACK_CONFIG: Dict[str, Any] = {
    "terminators": ["\\bbut\\b", ";", ","],
    "negated": {"self": ["\\bfailed\\b", "not\\s+yet\\s+\\w+"], "pre": ["\\bnot\\b"], "post": []},
    "reference_to_past": {"self": ["original\\s+return"]},
    "future_conditional": {"self": ["\\bwill\\s+\\w+", "\\bpending\\b"]},
    "happened": {"self": ["successfully", "\\bverified\\b", "\\bfiled\\b", "\\bsubmitted\\b"]},
}

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_path: Optional[Path] = None


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """The "assertions" section of sgt_i_config.json, cached by path. Falls back to a small
    built-in set (never raises) if the file is missing or not valid JSON."""
    global _cache, _cache_path
    p = path or CONFIG_PATH
    with _lock:
        if _cache is not None and _cache_path == p:
            return _cache
        cfg = _FALLBACK_CONFIG
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            section = loaded.get("assertions") if isinstance(loaded, dict) else None
            if isinstance(section, dict):
                cfg = section
        except (OSError, ValueError, AttributeError):
            pass
        _cache = cfg
        _cache_path = p
        return cfg


def _patterns(cfg: Dict[str, Any], cls: str, key: str) -> List[str]:
    return list((cfg.get(cls) or {}).get(key) or ())


def _compile_all(patterns: List[str]) -> List["re.Pattern[str]"]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


@dataclass(frozen=True)
class Assertion:
    """What one piece of text claims. `cls` is one of ASSERTION_CLASSES, or None if no trigger
    fired. `trigger` is the phrase that decided it - kept for the evidence ledger (step 7), never
    the value itself (this module never sees a container, only the sentence text)."""
    cls: Optional[str]
    trigger: Optional[str]


_NONE = Assertion(None, None)


def _terminator_positions(text: str, terminators: List[str]) -> List[int]:
    positions: List[int] = []
    for pat in terminators:
        for m in re.finditer(pat, text, re.IGNORECASE):
            positions.append(m.start())
    return sorted(positions)


def _scope_end(pos: int, terminators: List[int], text_len: int) -> int:
    after = [t for t in terminators if t > pos]
    return min(after) if after else text_len


def _scope_start(pos: int, terminators: List[int]) -> int:
    before = [t for t in terminators if t < pos]
    return max(before) if before else 0


def classify(text: str, config: Optional[Dict[str, Any]] = None) -> Assertion:
    """What `text` claims, or `Assertion(None, None)` if nothing in config matches. Pure: no I/O,
    no state beyond the cached, read-only config."""
    if not text or not text.strip():
        return _NONE
    cfg = config if config is not None else load_config()
    t = text.lower()

    # 1. Direct ("self") triggers - the phrase alone decides the class, in priority order.
    for cls in ("negated", "reference_to_past", "future_conditional"):
        for pat in _patterns(cfg, cls, "self"):
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                return Assertion(cls, m.group(0))

    # 2. Generic negators: a "happened" word inside a pre/post negator's scope is negated, not
    #    happened - NegEx's own scope idea, so "could not be completed" reads as negated even
    #    though "completed" alone would be a happened trigger.
    happened_matches = []
    for pat in _patterns(cfg, "happened", "self"):
        for m in re.finditer(pat, t, re.IGNORECASE):
            happened_matches.append((m.start(), m.end(), m.group(0)))
    if happened_matches:
        term_pos = _terminator_positions(t, cfg.get("terminators") or [])
        for pat in _patterns(cfg, "negated", "pre"):
            for m in re.finditer(pat, t, re.IGNORECASE):
                end = _scope_end(m.end(), term_pos, len(t))
                if any(m.end() <= hs < end for hs, _he, _ in happened_matches):
                    return Assertion("negated", m.group(0))
        for pat in _patterns(cfg, "negated", "post"):
            for m in re.finditer(pat, t, re.IGNORECASE):
                start = _scope_start(m.start(), term_pos)
                if any(start <= hs < m.start() for hs, _he, _ in happened_matches):
                    return Assertion("negated", m.group(0))

    # 3. Plain "happened".
    if happened_matches:
        return Assertion("happened", happened_matches[0][2])

    return _NONE


def classify_many(texts: Sequence[str],
                  config: Optional[Dict[str, Any]] = None) -> Tuple[Assertion, ...]:
    """classify() over several lines/pairs at once, sharing one loaded config."""
    cfg = config if config is not None else load_config()
    return tuple(classify(t, cfg) for t in texts)
