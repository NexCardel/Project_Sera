"""
core/scc/scc_rules.py - the wording SCC-U reads a login outcome from
=====================================================================
Autofill-tweaks blueprint G.2 step 4. All wording lives in scc_rules.json and is loaded here with
self-tests, like SGT's specs (core/sgt/sgt_specs.py): a rule must carry `examples` it matches and
`counter_examples` it does not, and a rule that fails them - or whose example a rule of a different
outcome claims first - is refused at load. The previous version of that rule keeps running.

  classify(lines, url, portal) -> Outcome | None
      worked / wrong_password / locked / neutral / no_conclusion, or None = not understood.

Pure text in, text out: no password box is read (both UIA readers skip them), nothing is stored.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.sgt.sgt_specs import MAX_LINE_LEN, SpecError, safe_compile

RULES_PATH = Path(__file__).resolve().parent / "scc_rules.json"

OUTCOMES = ("worked", "wrong_password", "locked", "neutral", "no_conclusion")
DEFAULT_PRECEDENCE = OUTCOMES

_RULE_KEYS = {"name", "outcome", "portals", "urls", "patterns", "case", "group", "unconfirmed", "note",
              "examples", "counter_examples", "disabled"}


@dataclass(frozen=True)
class Outcome:
    kind: str                # one of OUTCOMES
    rule: str                # the rule that matched
    unconfirmed: bool = False
    name: str = ""           # the captured name (worked: the header's "<NAME>")
    line: str = ""           # the page line that matched ("" for an address-only rule); memory only


@dataclass(frozen=True)
class Rule:
    name: str
    outcome: str
    portals: Tuple[str, ...]
    urls: Tuple["re.Pattern[str]", ...]
    patterns: Tuple["re.Pattern[str]", ...]
    group: int
    unconfirmed: bool
    examples: Tuple[Any, ...]
    counter_examples: Tuple[Any, ...]

    def match(self, lines: Sequence[str], url: str = "", portal: str = "") -> Optional[str]:
        """None = no match; otherwise the captured group (or "" when the rule captures nothing)."""
        got = self.match_line(lines, url, portal)
        return None if got is None else got[0]

    def match_line(self, lines: Sequence[str], url: str = "", portal: str = "") -> Optional[Tuple[str, str]]:
        """None = no match; otherwise (captured group or "", the line that matched or "")."""
        if self.portals and portal and portal.strip().lower() not in {p.lower() for p in self.portals}:
            return None
        if self.urls and not any(u.search(url or "") for u in self.urls):
            return None
        if not self.patterns:
            return "", ""
        for line in lines:
            if len(line) > MAX_LINE_LEN:
                continue
            for rx in self.patterns:
                m = rx.search(line)
                if m:
                    return ((m.group(self.group) or "").strip() if self.group else ""), line
        return None


@dataclass(frozen=True)
class RuleSet:
    rules: Tuple[Rule, ...] = ()
    precedence: Tuple[str, ...] = DEFAULT_PRECEDENCE
    errors: Tuple[str, ...] = ()
    messages: Tuple[Tuple[str, str], ...] = ()     # what the card tells staff, per outcome ("messages" in the file)

    def message(self, kind: str) -> str:
        """The staff wording for an outcome, from scc_rules.json only ("" when the file has none)."""
        return dict(self.messages).get(kind, "")

    def classify(self, lines: Sequence[str], url: str = "", portal: str = "") -> Optional[Outcome]:
        best: Optional[Tuple[int, Outcome]] = None
        for rule in self.rules:
            got = rule.match_line(lines, url, portal)
            if got is None:
                continue
            rank = self.precedence.index(rule.outcome)
            if best is None or rank < best[0]:
                best = (rank, Outcome(rule.outcome, rule.name, rule.unconfirmed, got[0], got[1]))
        return best[1] if best else None

    @property
    def unconfirmed(self) -> Tuple[str, ...]:
        """Rules whose wording no probe dump has shown yet (the check list asks for one)."""
        return tuple(r.name for r in self.rules if r.unconfirmed)


# ── Building and self-testing one rule ───────────────────────────────────────────
def _str_list(raw: Any, what: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise SpecError(f"{what} must be a list of non-empty strings")
    return tuple(raw)


def build_rule(raw: Dict[str, Any]) -> Rule:
    if not isinstance(raw, dict):
        raise SpecError("a rule must be an object")
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SpecError("'name' is required")
    outcome = raw.get("outcome")
    if outcome not in OUTCOMES:
        raise SpecError(f"'outcome' must be one of {list(OUTCOMES)}")
    case = raw.get("case", "ignore")
    if case not in ("ignore", "sensitive"):
        raise SpecError("'case' must be 'ignore' or 'sensitive'")
    flags = 0 if case == "sensitive" else re.IGNORECASE
    urls = tuple(safe_compile(u, re.IGNORECASE, f"'urls' entry {i + 1}") for i, u in enumerate(_str_list(raw.get("urls"), "'urls'")))
    patterns = tuple(safe_compile(p, flags, f"'patterns' entry {i + 1}")
                     for i, p in enumerate(_str_list(raw.get("patterns"), "'patterns'")))
    if not urls and not patterns:
        raise SpecError("a rule needs 'urls' or 'patterns' - one that matches every page is not a rule")
    group = raw.get("group", 0)
    if not isinstance(group, int) or isinstance(group, bool) or group < 0:
        raise SpecError("'group' must be a capture group number")
    if group and not patterns:
        raise SpecError("'group' needs 'patterns'")
    for rx in patterns:
        if group > rx.groups:
            raise SpecError(f"'group' {group} does not exist in {rx.pattern!r}")
    examples = raw.get("examples")
    counters = raw.get("counter_examples")
    if not isinstance(examples, list) or not examples:
        raise SpecError("every rule needs examples it matches")
    if not isinstance(counters, list) or not counters:
        raise SpecError("every rule needs counter_examples it does not match")
    return Rule(name=name.strip(), outcome=outcome, portals=_str_list(raw.get("portals"), "'portals'"), urls=urls,
                patterns=patterns, group=group, unconfirmed=bool(raw.get("unconfirmed", False)),
                examples=tuple(examples), counter_examples=tuple(counters))


def _page(ex: Any) -> Tuple[List[str], str]:
    """An example is a line, or {"lines": [...], "url": "..."} (either key optional)."""
    if isinstance(ex, str):
        return [ex], ""
    if isinstance(ex, dict) and set(ex) <= {"lines", "url"}:
        lines = ex.get("lines") or []
        url = ex.get("url") or ""
        if isinstance(lines, list) and all(isinstance(x, str) for x in lines) and isinstance(url, str):
            return list(lines), url
    raise SpecError(f"example {ex!r} must be a line or an object with 'lines' / 'url'")


def self_test_rule(rule: Rule) -> None:
    portal = rule.portals[0] if rule.portals else ""
    for ex in rule.examples:
        lines, url = _page(ex)
        if rule.match(lines, url, portal) is None:
            raise SpecError(f"example {ex!r} should match but does not")
    for ex in rule.counter_examples:
        lines, url = _page(ex)
        if rule.match(lines, url, portal) is not None:
            raise SpecError(f"counter-example {ex!r} should not match but does")


# ── Loading ──────────────────────────────────────────────────────────────────────
def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SpecError(f"{path.name} cannot be read: {type(e).__name__}") from None
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise SpecError(f"{path.name} must be an object with a 'rules' list")
    return data


def load_rules(path: Optional[Path] = None, previous: Optional[RuleSet] = None) -> RuleSet:
    """The rules in scc_rules.json. A rule that fails its own examples is refused (listed in
    .errors) and the same-named rule of `previous` stays in its place. A file that cannot be read at
    all gives `previous` back with the reason in .errors (or an empty set on a first load)."""
    prev = {r.name: r for r in (previous.rules if previous else ())}
    errors: List[str] = []
    try:
        data = _read(Path(path) if path else RULES_PATH)
    except SpecError as e:
        base = previous or RuleSet()
        return RuleSet(base.rules, base.precedence, tuple(base.errors) + (str(e),), base.messages)
    precedence = tuple(data.get("precedence") or DEFAULT_PRECEDENCE)
    if sorted(precedence) != sorted(OUTCOMES):
        errors.append(f"'precedence' must list each of {list(OUTCOMES)} once - the default order is used")
        precedence = DEFAULT_PRECEDENCE
    raw_msgs = data.get("messages") or {}
    if not isinstance(raw_msgs, dict):
        errors.append("'messages' must be an object of outcome -> text")
        raw_msgs = {}
    messages = tuple((k, v.strip()) for k, v in raw_msgs.items()
                     if k in OUTCOMES and isinstance(v, str) and v.strip())
    for k in raw_msgs:
        if k not in OUTCOMES:
            errors.append(f"'messages' has {k!r}, which is not an outcome")

    built: Dict[str, Rule] = {}
    for i, raw in enumerate(data["rules"]):
        nm = raw.get("name") if isinstance(raw, dict) else None
        label = nm if isinstance(nm, str) and nm else f"rule #{i + 1}"
        if isinstance(raw, dict) and raw.get("disabled"):
            continue
        try:
            rule = build_rule(raw)
            if rule.name in built:
                raise SpecError("its name is used twice")
            self_test_rule(rule)
            built[rule.name] = rule
        except SpecError as e:
            errors.append(f"{label}: {e}")
            if isinstance(nm, str) and nm in prev:
                built[nm] = prev[nm]

    rules = _cross_check(RuleSet(tuple(built.values()), precedence), errors)
    for outcome in OUTCOMES:
        if not any(r.outcome == outcome for r in rules.rules):
            errors.append(f"no rule left for '{outcome}' - SCC-U will never report it")
    return RuleSet(rules.rules, precedence, tuple(errors), messages)


def _cross_check(rs: RuleSet, errors: List[str]) -> RuleSet:
    """A rule's example must come out as that rule's outcome once every rule is in play."""
    keep = []
    for rule in rs.rules:
        portal = rule.portals[0] if rule.portals else ""
        bad = None
        for ex in rule.examples:
            lines, url = _page(ex)
            got = rs.classify(lines, url, portal)
            if got is None or got.kind != rule.outcome:
                bad = (ex, got.kind if got else "nothing")
                break
        if bad:
            errors.append(f"{rule.name}: example {bad[0]!r} comes out as {bad[1]}, not {rule.outcome} "
                          f"- another rule claims it first")
        else:
            keep.append(rule)
    return RuleSet(tuple(keep), rs.precedence)


_cached: Optional[RuleSet] = None


def get_rules() -> RuleSet:
    """The built-in rules, loaded once per run."""
    global _cached
    if _cached is None:
        _cached = load_rules()
    return _cached
