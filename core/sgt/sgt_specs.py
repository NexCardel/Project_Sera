"""
core/sgt/sgt_specs.py — loading and policing the field specs
=============================================================
sgt_fields.json holds every datapoint SGT knows about. A regex in an editable file is
powerful and dangerous - a wrong one captures garbage, a badly written one can hang the
worker - so nothing is installed until it has earned it:

  * every pattern is compiled under a size bound and a nesting check (no "(x+)+" shapes,
    no back-references), then timed against a short adversarial string;
  * every tool a spec names must exist in sgt_toolbox;
  * every spec carries its own examples (must capture) and counter-examples (must not),
    and they are RUN before the spec is accepted.

A spec that fails is refused with a console line naming it and the reason. When the file is
edited while the app runs, a refused spec does not take the old one down with it: the last
good version of that spec keeps running.

Two files are read, in order: the built-in core/sgt/sgt_fields.json, then an optional
sgt_fields.json next to the app (or at SGT_FIELDS_PATH). A spec in the second file replaces
the built-in spec of the same name; "disabled": true removes it. Adding or subtracting a
datapoint is therefore a file edit, never a code change.
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .sgt_toolbox import CHECKS, MERGES, SUBMIT_LEVELS, TRANSFORMS, ddmmyy_tail_date, is_submit_level, period_start

try:                                    # Python 3.11+
    import re._parser as _sre_parse     # type: ignore[import-not-found]
    import re._constants as _sre_c      # type: ignore[import-not-found]
except ImportError:                     # pragma: no cover - older interpreters
    import sre_parse as _sre_parse      # type: ignore[no-redef]
    import sre_constants as _sre_c      # type: ignore[no-redef]

FIELDS_FILE_NAME = "sgt_fields.json"
FIELDS_PATH_ENV = "SGT_FIELDS_PATH"
BUILTIN_FIELDS_PATH = Path(__file__).resolve().parent / FIELDS_FILE_NAME

MAX_PATTERN_LEN = 400
MAX_LINE_LEN = 400          # the resolver never feeds a pattern a longer line than this
STRESS_LIMIT_MS = 20.0

TAKES = ("after_label", "anywhere", "nearest_above")
PICKS = ("first", "map_order")
SLOTS = ("profile", "dataset", "current")
# Where a spec looks: the page's text, the page's link, or the browser window's title.
SOURCES = ("page", "link", "title")
# What a single-value spec (profile / current return) does when a page shows more than one
# different value: "conflict" takes none of them (a list, not THE value); "latest_period"
# takes the latest period among them (a page naming several years is about the latest one).
MULTIPLES = ("conflict", "latest_period")
# What the text after a label on the same line means:
#   value      - it is the value if it has the value's shape, otherwise the line is a
#                different label ("PAN Status" is not "PAN") and is skipped
#   separated  - as "value", but only after a ":" or "-" ("Name: X" yes, "Name of the Bank" no)
#   ignore     - disregarded; the value is looked for on the following lines
LABEL_RESTS = ("value", "separated", "ignore")
# Where a record field looks: inside its own block (a card), or anywhere on the page - for a
# fact the page states once about every card on it ("select the return you would like to
# verify" makes every card on that page one awaiting verification).
SCOPES = ("record", "page")


class SpecError(ValueError):
    pass


# ── Pattern safety ───────────────────────────────────────────────────────────────
_REPEATS = {getattr(_sre_c, n) for n in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT") if hasattr(_sre_c, n)}
_UNBOUNDED = getattr(_sre_c, "MAXREPEAT", 4294967295)


def _nesting(parsed: Any) -> int:
    """Deepest nesting of unbounded repeats ("x+" = 1, "(x+)+" = 2). Raises on back-references."""
    deepest = 0
    for op, av in parsed:
        if op in _REPEATS:
            lo, hi, sub = av
            inner = _nesting(sub)
            deepest = max(deepest, inner + (1 if (hi == _UNBOUNDED or hi > 20) else 0))
        elif op == _sre_c.SUBPATTERN:
            deepest = max(deepest, _nesting(av[-1]))
        elif op == _sre_c.BRANCH:
            deepest = max([deepest] + [_nesting(b) for b in av[1]])
        elif op in (_sre_c.ASSERT, _sre_c.ASSERT_NOT):
            deepest = max(deepest, _nesting(av[1]))
        elif op == getattr(_sre_c, "ATOMIC_GROUP", object()):
            deepest = max(deepest, _nesting(av))
        elif op in (_sre_c.GROUPREF, getattr(_sre_c, "GROUPREF_EXISTS", object())):
            raise SpecError("back-references are not allowed")
    return deepest


_STRESS_INPUTS = ("a" * 20 + "!", "1" * 20 + "!", "A" * 20 + "!", " " * 20 + "!", "a1" * 10 + "!", ". " * 10 + "!")


def safe_compile(pattern: Any, flags: int = 0, what: str = "pattern") -> "re.Pattern[str]":
    if not isinstance(pattern, str) or not pattern:
        raise SpecError(f"{what} must be a non-empty string")
    if len(pattern) > MAX_PATTERN_LEN:
        raise SpecError(f"{what} is longer than {MAX_PATTERN_LEN} characters")
    try:
        parsed = _sre_parse.parse(pattern, flags)
        compiled = re.compile(pattern, flags)
    except re.error as e:
        raise SpecError(f"{what} is not a valid regex: {e}") from None
    if _nesting(parsed) >= 2:
        raise SpecError(f"{what} nests a repeat inside a repeat (like (x+)+), which can hang - flatten it")
    for s in _STRESS_INPUTS:
        t0 = time.perf_counter()
        compiled.search(s)
        if (time.perf_counter() - t0) * 1000 > STRESS_LIMIT_MS:
            raise SpecError(f"{what} is too slow on a 20-character input - it would stall the worker")
    return compiled


def label_regex(labels: Sequence[str], at: str) -> "re.Pattern[str]":
    """Labels are plain words, not regex: 'Acknowledgement No' matches 'Acknowledgement  No :'."""
    phrases = []
    # Longest first, so "Date of Birth / Formation" is tried before its prefix "Date of Birth".
    for lab in sorted(labels, key=len, reverse=True):
        words = [re.escape(w) for w in str(lab).split()]
        if not words:
            raise SpecError("a label is empty")
        phrases.append(r"\s+".join(words))
    lead = r"^\s*" if at == "start" else r"(?<![A-Za-z0-9])"
    return re.compile(lead + "(?:" + "|".join(phrases) + r")(?![A-Za-z0-9])\s*(?P<sep>[:\-–]*)\s*(?P<rest>.*)$",
                      re.IGNORECASE)


def stop_regex(labels: Any) -> Optional["re.Pattern[str]"]:
    """A line that is nothing but one of `labels` (with an optional ':' or '-' after it)."""
    phrases = sorted({r"\s+".join(re.escape(w) for w in str(l).split()) for l in labels if str(l).split()},
                     key=len, reverse=True)
    if not phrases:
        return None
    return re.compile(r"^\s*(?:" + "|".join(phrases) + r")\s*[:\-–]*\s*$", re.IGNORECASE)


# ── Spec objects ─────────────────────────────────────────────────────────────────
@dataclass
class FieldSpec:
    name: str
    field: str
    slot: str
    take: str
    pattern: "re.Pattern[str]"
    labels: Tuple[str, ...] = ()
    label_re: Optional["re.Pattern[str]"] = None
    label_rest: str = "value"               # see LABEL_RESTS
    within: int = 3
    pick: str = "first"
    value_map: Tuple[Tuple["re.Pattern[str]", Optional[str]], ...] = ()
    transforms: Tuple[str, ...] = ()
    checks: Tuple[str, ...] = ()
    portals: Tuple[str, ...] = ()
    urls: Tuple["re.Pattern[str]", ...] = ()
    confidence: int = 80
    merge: str = "latch"                    # profile only - see sgt_toolbox.MERGES
    source: str = "page"                    # see SOURCES
    multiple: str = "conflict"              # see MULTIPLES
    scope: str = "record"                   # record fields only - see SCOPES
    # Every label known to the whole spec file (set by the loader). Looking for a value after
    # a label stops at a line that is itself a label: the field is empty, and the next field's
    # label ("PAN" after an empty "Last Name") must never be read as its value.
    stop_re: Optional["re.Pattern[str]"] = None
    examples: Tuple[Any, ...] = ()
    counter_examples: Tuple[Any, ...] = ()

    def applies(self, portal: str, url: str) -> bool:
        return (not self.portals or portal in self.portals) and (not self.urls or any(u.search(url or "") for u in self.urls))


@dataclass
class RecordSpec:
    name: str
    fields: Tuple[FieldSpec, ...]
    start: Optional["re.Pattern[str]"] = None
    max_lines: int = 60
    require: Tuple[str, ...] = ()
    portals: Tuple[str, ...] = ()
    urls: Tuple["re.Pattern[str]", ...] = ()
    examples: Tuple[Any, ...] = ()

    def applies(self, portal: str, url: str) -> bool:
        return (not self.portals or portal in self.portals) and (not self.urls or any(u.search(url or "") for u in self.urls))


@dataclass
class CurrentRules:
    """
    How the dataset being worked on is assembled from pieces seen across pages.
      compose          builds a field from others once they are all present, e.g.
                       {"period": "{tax_period} (FY {fy})"}
      complete_when    the fields a dataset must have before it can ever be dispatched
      in_progress_status / in_progress_needs_link
                       the status given to a complete dataset nothing has submitted yet
                       ("Draft") - only when some piece came from the page LINK (the portals
                       only put the form / year in the link while you are inside that filing)
    """
    compose: Tuple[Tuple[str, str], ...] = ()
    complete_when: Tuple[str, ...] = ("form", "period")
    in_progress_status: Optional[str] = "Draft"
    in_progress_needs_link: bool = True


@dataclass
class SubmitRules:
    """
    How a dataset's submit status is decided beyond its own status wording. The portal's
    submission identifier (ARN / ack) is always the dataset field "arn".
      identifier_proves  the exceptions to the ladder's rule that an ARN proves "Submitted
                         (Not Verified)": a portal whose ARN proves more on its own. The GST
                         portal issues the ARN only once the return is filed with DSC/EVC
                         (-> "Submitted & Verified"). A submit message can still raise it.
    """
    identifier_proves: Tuple[Tuple[str, str], ...] = ()

    def proven_by_identifier(self, portal: str) -> str:
        return dict(self.identifier_proves).get(portal) or SUBMIT_LEVELS[2]


@dataclass
class ProfileRules:
    """compose: builds a profile datapoint from parts, e.g.
    {"name": "{first_name} {middle_name?} {last_name}"} ('?' = may be missing)."""
    compose: Tuple[Tuple[str, str], ...] = ()


@dataclass
class DatasetRule:
    """
    A check on a WHOLE dataset before it is written - every field can look plausible on its own
    and still not fit together (an ITR with a GST month for its period; an ack dated before its
    assessment year began). A dataset that breaks a rule is HELD, with the reason, not written.
      portals     the portals it applies to (empty = all)
      when        field -> regex: the rule applies only if every one matches
      require     field -> regex: a present value must match (a missing one is not this rule's job)
      checks      field -> toolbox check names (sgt_toolbox.CHECKS)
      date_tail   {"field", "not_future", "not_before_period_start"}: the DDMMYY date an ITR ack
                  carries in its last six digits must be a real date inside the period's window
      message     what staff see when it holds a dataset
    """
    name: str
    message: str
    portals: Tuple[str, ...] = ()
    when: Tuple[Tuple[str, "re.Pattern[str]"], ...] = ()
    require: Tuple[Tuple[str, "re.Pattern[str]"], ...] = ()
    checks: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    date_tail: Optional[Tuple[str, bool, bool]] = None
    examples: Tuple[Dict[str, Any], ...] = ()

    def problem(self, portal: str, values: Dict[str, str], today: date) -> Optional[str]:
        if self.portals and portal not in self.portals:
            return None
        for fld, rx in self.when:
            if not values.get(fld) or not rx.search(str(values[fld])):
                return None
        for fld, rx in self.require:
            if values.get(fld) and not rx.search(str(values[fld])):
                return self.message
        for fld, names in self.checks:
            if values.get(fld) and not all(CHECKS[n](str(values[fld]), today) for n in names):
                return self.message
        if self.date_tail and values.get(self.date_tail[0]):
            fld, not_future, not_before = self.date_tail
            d = ddmmyy_tail_date(str(values[fld]))
            if d is None:
                return self.message
            if not_future and d > today:
                return self.message
            start = period_start(values.get("period"))
            if not_before and start is not None and d < start:
                return self.message
        return None


@dataclass
class DatasetRules:
    rules: Tuple[DatasetRule, ...] = ()

    def problems(self, portal: str, values: Dict[str, str], today: date) -> List[str]:
        out = []
        for r in self.rules:
            p = r.problem(portal, values, today)
            if p:
                out.append(f"{r.name}: {p}")
        return out


@dataclass
class Registry:
    profile: Tuple[FieldSpec, ...] = ()
    records: Tuple[RecordSpec, ...] = ()
    current: Tuple[FieldSpec, ...] = ()
    current_rules: CurrentRules = field(default_factory=CurrentRules)
    profile_rules: ProfileRules = field(default_factory=ProfileRules)
    submit_rules: SubmitRules = field(default_factory=SubmitRules)
    errors: Tuple[str, ...] = ()
    sources: Tuple[str, ...] = ()
    dataset_rules: DatasetRules = field(default_factory=DatasetRules)

    def by_name(self) -> Dict[str, Any]:
        return {s.name: s for s in (*self.profile, *self.records, *self.current)}

    def merge_policy(self, field_name: str) -> str:
        """A profile datapoint keeps being looked for if ANY of its specs does not latch."""
        policies = {s.merge for s in self.profile if s.field == field_name}
        return next((p for p in sorted(policies) if p != "latch"), "latch")

    def latching_fields(self) -> set:
        return {s.field for s in self.profile if self.merge_policy(s.field) == "latch"}


# ── Building specs from JSON ─────────────────────────────────────────────────────
def _str_list(raw: Any, what: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise SpecError(f"{what} must be a list of strings")
    return tuple(raw)


def _int(raw: Any, default: int, lo: int, hi: int, what: str) -> int:
    if raw is None:
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or not lo <= raw <= hi:
        raise SpecError(f"{what} must be a whole number from {lo} to {hi}")
    return raw


_FIELD_KEYS = {"name", "field", "slot", "take", "pattern", "case", "labels", "label_at", "label_rest",
               "within", "pick", "map", "transforms", "checks", "portals", "urls", "confidence", "merge",
               "source", "multiple", "scope",
               "examples", "counter_examples", "disabled", "note"}


def build_field(raw: Dict[str, Any], slot: str, owner: str = "") -> FieldSpec:
    if not isinstance(raw, dict):
        raise SpecError("a field spec must be a JSON object")
    unknown = set(raw) - _FIELD_KEYS
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    fld = raw.get("field")
    if not isinstance(fld, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", fld):
        raise SpecError("'field' must be a lower_case name such as 'pan' or 'filing_date'")
    name = raw.get("name") or (f"{owner}.{fld}" if owner else fld)
    take = raw.get("take", "after_label")
    if take not in TAKES:
        raise SpecError(f"'take' must be one of {TAKES}")
    pick = raw.get("pick", "first")
    if pick not in PICKS:
        raise SpecError(f"'pick' must be one of {PICKS}")
    flags = re.IGNORECASE if raw.get("case", "sensitive") == "insensitive" else 0
    pattern = safe_compile(raw.get("pattern"), flags, "'pattern'")

    labels = _str_list(raw.get("labels"), "'labels'")
    label_at = raw.get("label_at", "start")
    if label_at not in ("start", "anywhere"):
        raise SpecError("'label_at' must be 'start' or 'anywhere'")
    label_rest = raw.get("label_rest", "value")
    if label_rest not in LABEL_RESTS:
        raise SpecError(f"'label_rest' must be one of {LABEL_RESTS}")
    if take == "after_label" and not labels:
        raise SpecError("take 'after_label' needs at least one label")

    value_map: List[Tuple["re.Pattern[str]", Optional[str]]] = []
    for entry in raw.get("map") or []:
        if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                or not (entry[1] is None or isinstance(entry[1], str))):
            raise SpecError("each 'map' entry must be [\"regex\", \"output\"] (output may be null)")
        value_map.append((safe_compile(entry[0], re.IGNORECASE, f"map entry {entry[0]!r}"), entry[1]))
    if pick == "map_order" and not value_map:
        raise SpecError("pick 'map_order' needs a 'map'")

    transforms = _str_list(raw.get("transforms"), "'transforms'")
    checks = _str_list(raw.get("checks"), "'checks'")
    for t in transforms:
        if t not in TRANSFORMS:
            raise SpecError(f"unknown transform {t!r} (available: {', '.join(sorted(TRANSFORMS))})")
    for c in checks:
        if c not in CHECKS:
            raise SpecError(f"unknown check {c!r} (available: {', '.join(sorted(CHECKS))})")
    merge = raw.get("merge", "latch")
    if merge not in MERGES:
        raise SpecError(f"unknown merge {merge!r} (available: {', '.join(sorted(MERGES))})")
    if merge != "latch" and slot != "profile":
        raise SpecError("'merge' is for profile datapoints; dataset fields never latch anyway")
    source = raw.get("source", "page")
    if source not in SOURCES:
        raise SpecError(f"'source' must be one of {SOURCES}")
    multiple = raw.get("multiple", "conflict")
    if multiple not in MULTIPLES:
        raise SpecError(f"'multiple' must be one of {MULTIPLES}")
    if slot == "dataset" and (source != "page" or multiple != "conflict"):
        raise SpecError("'source' / 'multiple' are for profile and current_dataset fields")
    scope = raw.get("scope", "record")
    if scope not in SCOPES:
        raise SpecError(f"'scope' must be one of {SCOPES}")
    if scope != "record" and slot != "dataset":
        raise SpecError("'scope' is for record fields")
    if fld == "status":
        # One vocabulary on every portal: the page's wording is evidence, the map turns it
        # into a level of the submit ladder.
        if not value_map:
            raise SpecError(f"a status field needs a 'map' onto the submit ladder {list(SUBMIT_LEVELS)}")
        bad = sorted({out for _, out in value_map if out is not None and not is_submit_level(out)})
        if bad:
            raise SpecError(f"status map output(s) {bad} are not on the submit ladder {list(SUBMIT_LEVELS)}")

    return FieldSpec(
        name=str(name), field=fld, slot=slot, take=take, pattern=pattern,
        labels=labels, label_re=label_regex(labels, label_at) if labels else None,
        label_rest=label_rest, within=_int(raw.get("within"), 3, 1, 12, "'within'"),
        pick=pick, value_map=tuple(value_map), transforms=transforms, checks=checks,
        portals=_str_list(raw.get("portals"), "'portals'"),
        urls=tuple(safe_compile(u, re.IGNORECASE, "'urls' entry") for u in _str_list(raw.get("urls"), "'urls'")),
        confidence=_int(raw.get("confidence"), 80, 1, 100, "'confidence'"), merge=merge,
        source=source, multiple=multiple, scope=scope,
        examples=tuple(raw.get("examples") or ()), counter_examples=tuple(raw.get("counter_examples") or ()),
    )


_RECORD_KEYS = {"name", "fields", "start", "max_lines", "require", "portals", "urls", "examples", "disabled", "note"}


def build_record(raw: Dict[str, Any]) -> RecordSpec:
    if not isinstance(raw, dict):
        raise SpecError("a record spec must be a JSON object")
    unknown = set(raw) - _RECORD_KEYS
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise SpecError("a record needs a 'name'")
    fields = tuple(build_field(f, "dataset", owner=name) for f in (raw.get("fields") or []))
    if not fields:
        raise SpecError("a record needs at least one field")
    require = _str_list(raw.get("require"), "'require'")
    missing = set(require) - {f.field for f in fields}
    if missing:
        raise SpecError(f"'require' names field(s) the record does not define: {sorted(missing)}")
    start = raw.get("start")
    return RecordSpec(
        name=name, fields=fields,
        start=safe_compile(start, re.IGNORECASE, "'start'") if start is not None else None,
        max_lines=_int(raw.get("max_lines"), 60, 1, 400, "'max_lines'"),
        require=require, portals=_str_list(raw.get("portals"), "'portals'"),
        urls=tuple(safe_compile(u, re.IGNORECASE, "'urls' entry") for u in _str_list(raw.get("urls"), "'urls'")),
        examples=tuple(raw.get("examples") or ()),
    )


# ── Self-tests ───────────────────────────────────────────────────────────────────
# A fixed "today" for examples, so a spec using a date check (date_is_today, not_future)
# tests the same way on every day. Examples that depend on it are written for this date.
SELF_TEST_TODAY = date(2026, 7, 15)


def _page_example(ex: Any) -> bool:
    return isinstance(ex, dict) and (isinstance(ex.get("lines"), list) or "link" in ex or "title" in ex)


def self_test_field(spec: FieldSpec) -> None:
    from .sgt_resolver import extract_value, resolve_single

    def run(ex: Dict[str, Any]):
        got = resolve_single(spec, ex.get("lines") or [], SELF_TEST_TODAY,
                             link=str(ex.get("link") or ""), title=str(ex.get("title") or ""))
        return got.value if got else None

    if not spec.examples:
        raise SpecError("has no 'examples' - every spec must prove it captures something")
    for ex in spec.examples:
        if isinstance(ex, str):
            if extract_value(spec, ex, SELF_TEST_TODAY) is None:
                raise SpecError(f"example {ex!r} was not captured")
        elif _page_example(ex):
            got, want = run(ex), ex.get("expect")
            if got != want:
                raise SpecError(f"page example expected {want!r}, got {got!r}")
        else:
            raise SpecError("an example must be a string, or {\"lines\"/\"link\"/\"title\": ..., \"expect\": ...}")
    for ex in spec.counter_examples:
        if isinstance(ex, str):
            v = extract_value(spec, ex, SELF_TEST_TODAY)
            if v is not None:
                raise SpecError(f"counter-example {ex!r} was captured as {v[0]!r}")
        elif _page_example(ex):
            got = run(ex)
            if got is not None:
                raise SpecError(f"page counter-example was captured as {got!r}")
        else:
            raise SpecError("a counter-example must be a string, or {\"lines\"/\"link\"/\"title\": ...}")


def self_test_record(spec: RecordSpec) -> None:
    from .sgt_resolver import resolve_records

    for f in spec.fields:
        # Field-level examples of a record field are value strings; page examples belong
        # to the record, where the block structure exists.
        for ex in f.examples:
            if not isinstance(ex, str):
                raise SpecError(f"field {f.field!r}: record field examples must be strings")
        self_test_field(f)
    if not spec.examples:
        raise SpecError("has no page 'examples' - every record must prove it captures something")
    for ex in spec.examples:
        if not isinstance(ex, dict) or not isinstance(ex.get("lines"), list) or not isinstance(ex.get("expect"), list):
            raise SpecError("a record example must be {\"lines\": [...], \"expect\": [{...}, ...]}")
        got = [d.values() for d in resolve_records(spec, ex["lines"], SELF_TEST_TODAY)]
        want = ex["expect"]
        if len(got) != len(want):
            raise SpecError(f"page example expected {len(want)} record(s), got {len(got)}: {got}")
        for g, w in zip(got, want):
            diff = {k: (w[k], g.get(k)) for k in w if g.get(k) != w[k]}
            if diff:
                raise SpecError(f"page example mismatch (expected, got): {diff}")


# ── Loading ──────────────────────────────────────────────────────────────────────
def override_path() -> Path:
    """Installed app: next to the program first, else the PC's Sera data folder (writable
    without admin rights - the program itself sits in Program Files)."""
    env = os.environ.get(FIELDS_PATH_ENV)
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        beside_exe = Path(sys.executable).resolve().parent / FIELDS_FILE_NAME
        if beside_exe.is_file():
            return beside_exe
        return Path.home() / "AmanAssociates_Sera" / FIELDS_FILE_NAME
    return Path(__file__).resolve().parents[2] / FIELDS_FILE_NAME


def _read_json(path: Path, errors: List[str]) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"{path.name}: could not be read ({e}) - the whole file was ignored")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path.name}: must be a JSON object - ignored")
        return None
    return data


def load_registry(paths: Optional[Sequence[Path]] = None, previous: Optional[Registry] = None) -> Registry:
    """
    Builds a registry from the spec files, later files overriding earlier ones by spec name.
    A spec that fails to build or fails its self-test is refused; if `previous` held a good
    version of it, that version is kept, so a bad edit never removes working capture.
    """
    paths = list(paths) if paths is not None else [BUILTIN_FIELDS_PATH, override_path()]
    errors: List[str] = []
    rules = previous.current_rules if previous else CurrentRules()
    prules = previous.profile_rules if previous else ProfileRules()
    srules = previous.submit_rules if previous else SubmitRules()
    prev_drules = {r.name: r for r in (previous.dataset_rules.rules if previous else ())}
    drules: Dict[str, DatasetRule] = {}
    drules_order: List[str] = []
    raw_specs: Dict[str, Tuple[str, Dict[str, Any], str]] = {}      # name -> (kind, raw, file)
    order: List[str] = []
    sources: List[str] = []
    for path in paths:
        data = _read_json(path, errors)
        if data is None:
            continue
        sources.append(str(path))
        if "profile_rules" in data:
            try:
                prules = build_profile_rules(data["profile_rules"])
            except SpecError as e:
                errors.append(f"{path.name}: profile_rules refused: {e} - the previous rules keep running")
        if "submit_rules" in data:
            try:
                srules = build_submit_rules(data["submit_rules"])
            except SpecError as e:
                errors.append(f"{path.name}: submit_rules refused: {e} - the previous rules keep running")
        section = data.get("dataset_rules")
        for raw in (section.get("rules") if isinstance(section, dict) else None) or []:
            nm = raw.get("name") if isinstance(raw, dict) else None
            if not isinstance(nm, str) or not nm:
                errors.append(f"{path.name}: a dataset rule has no name - skipped")
                continue
            if nm not in drules_order:
                drules_order.append(nm)
            if raw.get("disabled") is True:
                drules.pop(nm, None)
                continue
            try:
                rule = build_dataset_rule(raw)
                self_test_dataset_rule(rule)
                drules[nm] = rule
            except SpecError as e:
                kept = prev_drules.get(nm)
                errors.append(f"{path.name}: dataset rule {nm!r} refused: {e}"
                              + (" - the previous version keeps running" if kept else ""))
                if kept is not None:
                    drules[nm] = kept
        # "current_return" is the section's old name; an override file written before the
        # rename still works.
        ckey = "current_dataset" if isinstance(data.get("current_dataset"), dict) else "current_return"
        current = data.get(ckey) if isinstance(data.get(ckey), dict) else {}
        if "rules" in current:
            try:
                rules = build_current_rules(current["rules"])
            except SpecError as e:
                errors.append(f"{path.name}: {ckey} rules refused: {e} - the previous rules keep running")
        for kind, entries, key in (("profile", data.get("profile"), "profile"),
                                   ("record", data.get("records"), "records"),
                                   ("current", current.get("fields"), f"{ckey}.fields")):
            for raw in entries or []:
                if not isinstance(raw, dict):
                    errors.append(f"{path.name}: an entry under '{key}' is not an object - skipped")
                    continue
                nm = raw.get("name") or raw.get("field")
                if not isinstance(nm, str) or not nm:
                    errors.append(f"{path.name}: an entry under '{key}' has no name - skipped")
                    continue
                if nm not in raw_specs:
                    order.append(nm)
                raw_specs[nm] = (kind, raw, path.name)

    prev = previous.by_name() if previous else {}
    profile: List[FieldSpec] = []
    records: List[RecordSpec] = []
    current: List[FieldSpec] = []

    def refuse(nm: str, fname: str, e: Exception) -> None:
        kept = prev.get(nm)
        tail = " - the previous version keeps running" if kept is not None else ""
        errors.append(f"{fname}: spec {nm!r} refused: {e}{tail}")
        if isinstance(kept, FieldSpec):
            (profile if kept.slot == "profile" else current).append(kept)
        elif isinstance(kept, RecordSpec):
            records.append(kept)

    # Pass 1: build. Pass 2: share every label with every spec. Pass 3: self-test - after
    # pass 2, so the examples run under the same stop-at-a-label rule as live pages.
    built: List[Tuple[str, str, str, Any]] = []
    for nm in order:
        kind, raw, fname = raw_specs[nm]
        if raw.get("disabled") is True:
            continue
        try:
            if kind in ("profile", "current"):
                spec = build_field(raw, kind)
                spec.name = nm
                built.append((nm, kind, fname, spec))
            else:
                built.append((nm, kind, fname, build_record(raw)))
        except SpecError as e:
            refuse(nm, fname, e)

    all_fields: List[FieldSpec] = []
    for _, kind, _, obj in built:
        all_fields.extend(obj.fields if isinstance(obj, RecordSpec) else [obj])
    stop = stop_regex({lab for f in all_fields for lab in f.labels})
    for f in all_fields:
        f.stop_re = stop

    for nm, kind, fname, obj in built:
        try:
            if isinstance(obj, RecordSpec):
                self_test_record(obj)
                records.append(obj)
            else:
                self_test_field(obj)
                (profile if kind == "profile" else current).append(obj)
        except SpecError as e:
            refuse(nm, fname, e)
    return Registry(tuple(profile), tuple(records), tuple(current), rules, prules, srules,
                    tuple(errors), tuple(sources),
                    DatasetRules(tuple(drules[n] for n in drules_order if n in drules)))


def build_dataset_rule(raw: Dict[str, Any]) -> DatasetRule:
    known = {"name", "note", "message", "portals", "when", "require", "checks", "date_tail", "examples", "disabled"}
    unknown = set(raw) - known
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    message = raw.get("message")
    if not isinstance(message, str) or not message.strip():
        raise SpecError("'message' (what staff see when the rule holds a dataset) is required")

    def patterns(key: str) -> Tuple[Tuple[str, "re.Pattern[str]"], ...]:
        spec = raw.get(key) or {}
        if not isinstance(spec, dict):
            raise SpecError(f"'{key}' must map a field to a regex")
        return tuple((f, safe_compile(rx, re.IGNORECASE, f"{key}.{f}")) for f, rx in spec.items())

    checks_raw = raw.get("checks") or {}
    if not isinstance(checks_raw, dict):
        raise SpecError("'checks' must map a field to a list of check names")
    checks = []
    for fld, names in checks_raw.items():
        names = _str_list(names, f"checks.{fld}")
        bad = [n for n in names if n not in CHECKS]
        if bad:
            raise SpecError(f"unknown check(s) {bad} - the toolbox has {sorted(CHECKS)}")
        checks.append((fld, names))
    dt = raw.get("date_tail")
    date_tail = None
    if dt is not None:
        if not isinstance(dt, dict) or not isinstance(dt.get("field"), str):
            raise SpecError("'date_tail' must be an object naming its 'field'")
        date_tail = (dt["field"], bool(dt.get("not_future", True)), bool(dt.get("not_before_period_start", True)))
    examples = raw.get("examples") or []
    if not isinstance(examples, list) or not examples:
        raise SpecError("every dataset rule needs examples - at least one it passes and one it holds")
    rule = DatasetRule(name=raw["name"], message=message.strip(), portals=_str_list(raw.get("portals"), "'portals'"),
                       when=patterns("when"), require=patterns("require"), checks=tuple(checks),
                       date_tail=date_tail, examples=tuple(examples))
    if not (rule.when or rule.require or rule.checks or rule.date_tail):
        raise SpecError("a dataset rule must check something (when / require / checks / date_tail)")
    return rule


def self_test_dataset_rule(rule: DatasetRule) -> None:
    """Each example: {"portal", "dataset": {...}, "ok": true|false, "today": "YYYY-MM-DD"?}.
    It needs at least one it passes and one it holds, so a rule that never fires is caught."""
    outcomes = set()
    for ex in rule.examples:
        if not isinstance(ex, dict) or not isinstance(ex.get("dataset"), dict) or "ok" not in ex:
            raise SpecError("each example must be an object with 'dataset' and 'ok'")
        today = date.fromisoformat(ex["today"]) if ex.get("today") else date.today()
        portal = ex.get("portal") or (rule.portals[0] if rule.portals else "")
        got = rule.problem(portal, {k: str(v) for k, v in ex["dataset"].items()}, today) is None
        if got != bool(ex["ok"]):
            raise SpecError(f"example {ex['dataset']} should be {'passed' if ex['ok'] else 'held'} "
                            f"but was {'passed' if got else 'held'}")
        outcomes.add(got)
    if outcomes != {True, False}:
        raise SpecError("examples must include at least one dataset it passes and one it holds")


_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)(\??)\}")


def build_compose(raw: Any) -> Tuple[Tuple[str, str], ...]:
    """{field: template}. A template names fields as {name}, or {name?} when it may be missing."""
    compose = raw or {}
    if not isinstance(compose, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in compose.items()):
        raise SpecError("'compose' must map a field to a template such as \"{tax_period} (FY {fy})\"")
    for k, tpl in compose.items():
        names = _PLACEHOLDER.findall(tpl)
        if not names or re.search(r"\{(?![a-z][a-z0-9_]*\??\})", tpl):
            raise SpecError(f"compose template for {k!r} must name fields like {{fy}} or {{middle_name?}} "
                            f"and nothing else in braces")
        if all(opt for _, opt in names):
            raise SpecError(f"compose template for {k!r} needs at least one field that is not optional")
    return tuple(compose.items())


def compose_values(values: Dict[str, str], compose: Sequence[Tuple[str, str]]) -> Dict[str, str]:
    """
    The composed fields that can be built from `values`: every required part present
    ({name}); optional parts ({name?}) are left out when missing, and the spaces collapse.
    """
    out: Dict[str, str] = {}
    for fld, tpl in compose or ():
        parts = _PLACEHOLDER.findall(tpl)
        if not all(values.get(n) for n, opt in parts if not opt):
            continue
        text = _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1)) or ""), tpl)
        out[fld] = re.sub(r"\s+", " ", text).strip()
    return out


def build_profile_rules(raw: Any) -> ProfileRules:
    if not isinstance(raw, dict):
        raise SpecError("'profile_rules' must be a JSON object")
    unknown = set(raw) - {"compose", "note"}
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    return ProfileRules(compose=build_compose(raw.get("compose")))


def build_current_rules(raw: Any) -> CurrentRules:
    if not isinstance(raw, dict):
        raise SpecError("'rules' must be a JSON object")
    unknown = set(raw) - {"compose", "complete_when", "in_progress_status", "in_progress_needs_link", "note"}
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    status = raw.get("in_progress_status", "Draft")
    if status is not None and not is_submit_level(status):
        raise SpecError(f"'in_progress_status' must be a submit-ladder level {list(SUBMIT_LEVELS)} or null")
    return CurrentRules(
        compose=build_compose(raw.get("compose")),
        complete_when=_str_list(raw.get("complete_when", ["form", "period"]), "'complete_when'"),
        in_progress_status=status,
        in_progress_needs_link=bool(raw.get("in_progress_needs_link", True)),
    )


def build_submit_rules(raw: Any) -> SubmitRules:
    if not isinstance(raw, dict):
        raise SpecError("'submit_rules' must be a JSON object")
    unknown = set(raw) - {"identifier_proves", "note"}
    if unknown:
        raise SpecError(f"unknown key(s) {sorted(unknown)} - check the spelling")
    proves = raw.get("identifier_proves") or {}
    if not isinstance(proves, dict):
        raise SpecError("'identifier_proves' must map a portal to a submit-ladder level")
    for portal, level in proves.items():
        if not isinstance(portal, str) or not is_submit_level(level):
            raise SpecError(f"'identifier_proves' for {portal!r} must be one of {list(SUBMIT_LEVELS)}")
    return SubmitRules(identifier_proves=tuple(proves.items()))


class SpecStore:
    """The live registry, reloaded when either spec file changes (checked at most every few seconds)."""

    RECHECK_SEC = 5.0

    def __init__(self, paths: Optional[Sequence[Path]] = None, log=print) -> None:
        self._paths = list(paths) if paths is not None else None
        self._log = log
        self._registry: Optional[Registry] = None
        self._stamp: Tuple[Any, ...] = ()
        self._checked = 0.0

    def _current_paths(self) -> List[Path]:
        return self._paths if self._paths is not None else [BUILTIN_FIELDS_PATH, override_path()]

    def _stamps(self) -> Tuple[Any, ...]:
        out = []
        for p in self._current_paths():
            try:
                st = p.stat()
                out.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(p), None, None))
        return tuple(out)

    def get(self, now: Optional[float] = None) -> Registry:
        now = time.monotonic() if now is None else now
        if self._registry is not None and now - self._checked < self.RECHECK_SEC:
            return self._registry
        self._checked = now
        stamp = self._stamps()
        if self._registry is None or stamp != self._stamp:
            self._registry = load_registry(self._current_paths(), previous=self._registry)
            self._stamp = stamp
            for err in self._registry.errors:
                self._log(f"[SGT Specs] {err}")
            self._log(f"[SGT Specs] loaded {len(self._registry.profile)} profile spec(s), "
                      f"{len(self._registry.records)} record spec(s)")
        return self._registry
