"""
core/sgt_i/page_kinds.py - what kind of page this is (NegEx's other half)
===========================================================================
Blueprint 14.4 step 6, second half. "your return has been verified" reads differently on a
stepper's un-reached step than on a confirmation page - assertions.py tells what a sentence
claims; this module tells what the page itself is, from four content signals named in 14.4:

  a stepper           -> wizard_step
  a page mostly inputs -> login / payment
  repeated blocks      -> list / dashboard
  a dialog holding an identifier + "happened" wording -> confirmation

plus generic vocabulary in sgt_i_config.json's "page_kinds" section (login/payment/profile/error
words shared by many portals, never one portal's own field names) and, optionally, a couple of
counts already known about this page from the atlas (atlas.py) as a last-resort tie-breaker.
Rules can then say "confirmation + identifier + happened" once, for every portal, with no URLs
and no per-page specs (14.4 step 6).

Pure and stateless like page_map.py/pairs.py/assertions.py: only nodes and pairs already reduced
to what SGT-I may keep (never a raw value) go in; a kind, or None, comes out. Unsure -> abstains
(None) rather than guess - a second opinion (step 7) is only as good as knowing when it has none.
"""

import json
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import page_map as pm
from .pairs import ValuePair, pairs_from_page
from .assertions import Assertion, classify as classify_assertion

CONFIG_PATH = Path(__file__).with_name("sgt_i_config.json")

PAGE_KINDS: Tuple[str, ...] = (
    "wizard_step", "error", "confirmation", "payment", "login", "list", "dashboard", "profile",
)

# Used only if sgt_i_config.json is missing or corrupt (assertions.py's own "carry on" spirit).
_FALLBACK_VOCAB: Dict[str, List[str]] = {
    "login": ["log\\s*in", "sign\\s*in", "\\bpassword\\b"],
    "payment": ["\\bpayment\\b", "\\bpay\\s*now\\b"],
    "profile": ["\\bprofile\\b", "my\\s+account"],
    "error": ["\\berror\\b", "something\\s+went\\s+wrong"],
}

_TRUSTED = (pm.DIALOG, pm.MAIN)
_IDENTIFIER_TYPES = frozenset({"code", "number"})
_MIN_IDENTIFIER_LEN = 6              # digits/letters in the masked shape, spaces ignored
_REPEAT_MIN = 3                      # sibling containers needed to call a pattern "repeated"
_INPUT_MIN_COUNT = 2
_INPUT_MIN_RATIO = 0.6
_MESSAGE_MAX_WORDS = 12              # longer than this, outside a dialog = help text, not an error

_lock = threading.Lock()
_cache: Optional[Dict[str, List[str]]] = None
_cache_path: Optional[Path] = None


def load_vocab(path: Optional[Path] = None) -> Dict[str, List[str]]:
    """The "page_kinds" section of sgt_i_config.json, cached by path. Never raises."""
    global _cache, _cache_path
    p = path or CONFIG_PATH
    with _lock:
        if _cache is not None and _cache_path == p:
            return _cache
        vocab = _FALLBACK_VOCAB
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            section = loaded.get("page_kinds") if isinstance(loaded, dict) else None
            if isinstance(section, dict):
                vocab = {k: list(v) for k, v in section.items() if isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            pass
        _cache = vocab
        _cache_path = p
        return vocab


def _matches(text: str, patterns: Sequence[str]) -> Optional[str]:
    low = text.lower()
    for pat in patterns:
        m = re.search(pat, low, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _vocab_texts(page: pm.PageMap, pairs: Sequence[ValuePair]) -> List[str]:
    """Headings, free text and pair labels in the trusted zones - never a pair's value, never a
    control's own entered text."""
    texts = [n.text for n in page.nodes if n.zone in _TRUSTED and n.text and (n.is_content or n.heading)]
    texts += [p.container[-1] for p in pairs if p.zone in _TRUSTED and p.container]
    return texts


def _vocab_hit(texts: Sequence[str], patterns: Sequence[str]) -> Optional[str]:
    for t in texts:
        hit = _matches(t, patterns)
        if hit:
            return hit
    return None


@dataclass(frozen=True)
class PageKind:
    """What page_kinds.classify() decided. `kind` is one of PAGE_KINDS, or None when the page
    could not be told confidently. `evidence` are short reason tags for the evidence ledger
    (step 7) - never page text, never a value."""
    kind: Optional[str]
    evidence: Tuple[str, ...] = ()


_NONE = PageKind(None, ())


def _content_texts(page: pm.PageMap, zones: Sequence[str] = _TRUSTED) -> List[pm.Node]:
    return [n for n in page.nodes if n.zone in zones and n.text and (n.is_content or n.heading)]


def _assertions(page: pm.PageMap, cfg: Optional[Dict[str, Any]] = None,
                zones: Sequence[str] = _TRUSTED) -> List[Assertion]:
    return [classify_assertion(n.text, cfg) for n in _content_texts(page, zones)]


def _is_message(n: pm.Node) -> bool:
    """A line that can speak for the page: anything in a dialog, else a short line. Longer text
    outside a dialog is help or instructions."""
    return n.zone == pm.DIALOG or len(n.text.split()) <= _MESSAGE_MAX_WORDS


def _identifier_pairs(pairs: Sequence[ValuePair]) -> List[ValuePair]:
    return [p for p in pairs if p.zone in _TRUSTED and p.type in _IDENTIFIER_TYPES
            and len(p.shape.replace(" ", "")) >= _MIN_IDENTIFIER_LEN]


def _stepper_current(page: pm.PageMap) -> bool:
    return any(n.zone == pm.STEPPER and n.step == "current" for n in page.nodes)


def _input_dominance(page: pm.PageMap, zones: Sequence[str] = _TRUSTED) -> Tuple[int, float]:
    """How much of the page's content is input controls, by node count - not by pair, since an
    empty field (nothing typed yet, the common case on a login page) never becomes a pair at all
    (page_map.py's own rule: a control pairs only once it carries a value)."""
    inputs = [n for n in page.nodes if n.zone in zones and n.role in ("edit", "combobox")]
    other = [n for n in page.nodes if n.zone in zones and (n.is_content or n.heading)]
    total = len(inputs) + len(other)
    return len(inputs), (len(inputs) / total if total else 0.0)


def _children(nodes: Sequence[pm.Node]) -> Dict[int, List[pm.Node]]:
    kids: Dict[int, List[pm.Node]] = {}
    for n in nodes:
        if n.parent >= 0:
            kids.setdefault(n.parent, []).append(n)
    return kids


def _repeated_blocks(page: pm.PageMap, min_repeat: int = _REPEAT_MIN) -> List[List[pm.Node]]:
    """Sibling containers under the same parent, in the main zone, sharing an identical
    child-role signature, repeated at least `min_repeat` times - "3 cards and 14 cards are one
    pattern" (atlas.py's own idea, applied here to whole blocks rather than single elements)."""
    kids = _children(page.nodes)
    groups: Dict[Tuple[int, Tuple[Tuple[str, int], ...]], List[int]] = {}
    for parent_idx, children in kids.items():
        parent = page.nodes[parent_idx]
        if parent.zone != pm.MAIN or len(children) < 1:
            continue
        sig = tuple(sorted(Counter(c.role for c in children).items()))
        if not sig:
            continue
        groups.setdefault((parent.parent, sig), []).append(parent_idx)
    return [[page.nodes[i] for i in idxs] for idxs in groups.values() if len(idxs) >= min_repeat]


def _block_kind(block_parents: List[pm.Node], page: pm.PageMap) -> str:
    """"list" (data rows: mostly inputs/text) vs "dashboard" (a menu of links/buttons)."""
    kids = _children(page.nodes)
    roles = Counter()
    for parent in block_parents:
        for c in kids.get(parent.index, ()):
            roles[c.role] += 1
    nav = roles.get("link", 0) + roles.get("button", 0)
    data = roles.get("edit", 0) + roles.get("text", 0) + roles.get("combobox", 0)
    return "dashboard" if nav >= data else "list"


def classify(page: pm.PageMap, config: Optional[Dict[str, Any]] = None,
            atlas_hint: Optional[Dict[str, Any]] = None) -> PageKind:
    """What kind of page this is, or PageKind(None, ()) when unsure. `config` overrides
    sgt_i_config.json's "assertions" section (passed straight to assertions.classify); vocab
    always comes from load_vocab() (its own cache). `atlas_hint` is optional and only ever a
    tie-breaker, never a requirement: {"in_degree": int} - a page nothing else leads to."""
    vocab = load_vocab()
    pairs = pairs_from_page(page)
    texts = _vocab_texts(page, pairs)

    # 1. wizard_step - a stepper on this very page, with a current step. Unambiguous: nothing
    #    else on the page can override what its own progress indicator says about itself.
    if _stepper_current(page):
        return PageKind("wizard_step", ("stepper",))

    # 2. error - a negated assertion, or error vocabulary, in a trusted zone. Checked before
    #    confirmation so "payment failed for TXN123" (identifier + negated) reads as an error,
    #    not a confirmation, even though an identifier is present.
    #    Only a negated MESSAGE counts: a short line, or anything in a dialog. A long sentence is
    #    help or instructions ("please check whether your return was rejected in ...") and a
    #    negative word inside it says nothing about this page having failed.
    content = _content_texts(page)
    asserts = _assertions(page, config)
    negated = [a for n, a in zip(content, asserts) if a.cls == "negated" and _is_message(n)]
    if negated:
        return PageKind("error", ("negated", negated[0].trigger))
    err_hit = _vocab_hit(texts, vocab.get("error", []))
    if err_hit:
        return PageKind("error", ("vocab:error", err_hit))

    # 3. confirmation - an identifier (an ack/reference number, not a value SGT-I keeps) beside
    #    "happened" wording, both in a trusted zone (dialog/main - never the stepper, so a
    #    stepper's own un-reached-step text can never win this, 14.1's bug table).
    #    Outside a dialog the wording must read as a statement ("has been filed", "submitted
    #    successfully"), not a menu or column label that merely holds the word ("View Filed Returns"),
    #    and a short message, not help text. Outside a dialog, several identifiers of one shape are
    #    a list of earlier records each carrying its own status ("Successfully e-verified" beside
    #    every filed return), not one confirmation.
    ids = _identifier_pairs(pairs)
    statement = vocab.get("statement") or []
    happened = [(n, a) for n, a in zip(content, asserts) if a.cls == "happened" and _is_message(n)
                and (n.zone == pm.DIALOG or not statement or _matches(n.text, statement))]
    if ids and happened:
        in_dialog = any(n.zone == pm.DIALOG for n, _a in happened)
        records = max(Counter(p.shape for p in ids).values())
        if not in_dialog and records >= 2:
            return PageKind("list", ("records", str(records)))
        return PageKind("confirmation", ("identifier", "happened", happened[0][1].trigger))

    # 4. payment - an amount plus payment vocabulary, both required so a salary field on some
    #    other page never reads as a payment screen on its own.
    amounts = [p for p in pairs if p.zone in _TRUSTED and p.type == "amount"]
    pay_hit = _vocab_hit(texts, vocab.get("payment", []))
    if amounts and pay_hit:
        return PageKind("payment", ("amount", "vocab:payment", pay_hit))

    # 5. login - mostly inputs, with login wording or a password-shaped field; a form that is
    #    merely input-dominant with no such wording is left unclassified (rule 5, 6).
    controls, ratio = _input_dominance(page)
    login_hit = _vocab_hit(texts, vocab.get("login", []))
    if controls >= _INPUT_MIN_COUNT and ratio >= _INPUT_MIN_RATIO and login_hit:
        return PageKind("login", ("input-dominant", "vocab:login", login_hit))

    # 6. list - a table with more than one data row.
    table_rows = {p.row for p in pairs if p.method == "column" and p.row is not None}
    if len(table_rows) >= 2:
        return PageKind("list", ("table", str(len(table_rows))))

    # 7. list / dashboard - repeated sibling blocks that are not a table: a menu of cards
    #    (mostly links/buttons) reads as dashboard, repeated data rows read as list.
    blocks = _repeated_blocks(page)
    if blocks:
        kind = _block_kind(blocks[0], page)
        return PageKind(kind, ("repeated-blocks", str(len(blocks[0]))))

    # 8. profile - an identity-shaped value under profile wording; vocab is required here too
    #    (an identifier alone is too common a shape to mean "this is a profile page" by itself).
    profile_hit = _vocab_hit([t for t in texts if len(t.split()) <= _MESSAGE_MAX_WORDS],
                             vocab.get("profile", []))
    identity_types = {p.type for p in pairs if p.zone in _TRUSTED}
    if profile_hit and identity_types & {"code", "email", "phone", "date"}:
        return PageKind("profile", ("vocab:profile", profile_hit))

    # 9. atlas tie-break, last resort only: a page nothing else in the portal leads to, that is
    #    also input-dominant with no vocabulary hit at all, is very likely still a login (a
    #    portal's own entry page) - never used to call anything else.
    if atlas_hint and atlas_hint.get("in_degree") == 0 and controls >= _INPUT_MIN_COUNT \
            and ratio >= _INPUT_MIN_RATIO:
        return PageKind("login", ("input-dominant", "atlas:in_degree=0"))

    return _NONE
