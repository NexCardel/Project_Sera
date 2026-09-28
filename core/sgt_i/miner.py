"""
core/sgt_i/miner.py - the miner: from what SGT-I learned to proposals
=====================================================================
Blueprint 14.4 step 11. Everything steps 1-10 learned becomes a short list of drafted Core specs.
**Every proposal waits for the user's approval** (decision 2026-09-28): nothing here writes to
`sgt_fields.json`; the SGT lab (W12-2) shows the cards and writes an accepted one to the local
override file. All counting and rules - no AI.

The five sources (14.4 step 11):

1. **template vs data** - atlas slots (a fixed container, a changing value) no Core spec claims;
2. **known-value anchoring** - step 8's synonyms: a new wording for a field the Core knows;
3. **repeated structure** - repeating atlas slots under one heading become a list record;
4. **status wording from outcomes** - template phrases just before a settled dataset's identifier
   first showed up; the user maps each to a ladder level, the miner never picks one;
5. **graduation candidates** - second opinions (steps 7-8) a verdict shows would have been right.

A drafted spec is filled from step 3: the regex from the shape grammar (shapes.py), the checks
from the proven rules (checksum, a date inside the value), profile vs dataset from the container's
kind (stats.py). Its examples are **fictional**, from a small generator per type (a structurally
valid PAN, a GSTIN with a valid check character, an ack ending in a real date) - real values never
reach a spec, and the atlas holds none anyway.

Gates: support (>= `min_clients` different clients), type consistency (>= `type_share` of a slot's
values one type), placement (never help / navigation / controls, never a placeholder, never a label
still masked in the atlas), the Core loader's own build + self-tests, and a replay diff over the
recorded corpus - a proposal that would change any existing capture is flagged red.

`proposals.json` (`~/AmanAssociates_Sera/sgt_i/`) holds structure, counts and fictional examples
only (14.5 rule 7: it may sync). The replay diff is kept as counts; its lines name real values.
"""

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..sgt import sgt_specs, sgt_toolbox
from ..sgt.sgt_resolver import extract_value, prepare_lines
from ..sgt.sgt_specs import SELF_TEST_TODAY, SpecError
from ..sgt.sgt_toolbox import CHECKS, SUBMIT_LEVELS, TRANSFORMS, submit_level
from .atlas import ATLAS_DIR, SEP, Atlas
from .pairs import mask_shape
from .shapes import induce_shape_grammar
from .stats import sgt_i_dir

__all__ = ["SOURCES", "DEFAULT_CONFIG", "load_config", "proposals_path", "fictional_pan", "fictional_gstin",
           "fictional_ack", "fictional_date", "fill_shape", "value_regex", "draft_field", "from_atlas",
           "from_synonyms", "from_status_phrases", "from_second_opinions", "check_spec", "replay_diff",
           "spec_for_accept", "mine", "write_proposals"]

SOURCES = ("template_vs_data", "known_value", "list_record", "status_wording", "graduation")
FORMAT = 1
PROPOSALS_FILE = "proposals.json"
CONFIG_PATH = Path(__file__).with_name("sgt_i_config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "min_clients": 3, "type_share": 0.98, "zones": ["main", "dialog", "header"],
    "placeholder_words": [r"\bselect\b", r"\benter\b", r"\bsearch\b", r"\bchoose\b"],
    "field_aliases": {"ack": "arn"}, "status_window": 3, "status_phrase_words": [2, 8],
    "status_settled_level": 2, "require_share": 0.9, "max_proposals": 60,
}

# Section names as sgt_fields.json spells them.
PROFILE, CURRENT, RECORDS = "profile", "current_dataset", "records"
_BOUND_L, _BOUND_R = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"
_DIGITS = "2345678912"
_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_SHAPE = re.compile(r"^99AAAAA9999A[9A]A[9A]$")

# Types whose values have no fixed shape: one generic regex and fictional examples each.
_TYPE_PATTERNS: Dict[str, Tuple[str, Tuple[str, ...], Tuple[str, ...]]] = {
    # type: (regex with one group, fictional examples, checks)
    "amount": (r"^([₹$€£]?\s?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?)$", ("12,345.00", "₹ 1,25,000"), ()),
    "percentage": (r"^([+-]?\d{1,3}(?:\.\d{1,2})?\s?%)$", ("12.5%", "18%"), ()),
    "email": (r"([A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,64}\.[A-Za-z]{2,10})",
              ("someone@example.com", "office@example.org"), ()),
    "phone": (r"^(\+?\d{1,3}[-\s]?\d{5}[-\s]?\d{5}|\d{10})$", ("9000000001", "+91 90000 00002"), ()),
    "yes/no": (r"^(yes|no|y|n)$", ("Yes", "No"), ()),
    "text": (r"^([A-Za-z][A-Za-z0-9 .,&'()/-]{0,79})$", ("Sample Trading Co", "Example Enterprises"),
             ("not_ui_chrome",)),
}


# ── Config and paths ─────────────────────────────────────────────────────────────
def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """The "miner" section of sgt_i_config.json over DEFAULT_CONFIG. Never raises."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
        section = data.get("miner") if isinstance(data, dict) else None
        if isinstance(section, dict):
            cfg.update({k: v for k, v in section.items() if k != "note"})
    except Exception:
        pass
    return cfg


def proposals_path() -> Path:
    return sgt_i_dir() / PROPOSALS_FILE


# ── Fictional examples (14.4 step 11: "Examples must be fictional") ─────────────
def fill_shape(shape: str, n: int = 0) -> str:
    """A made-up value of `shape`: A -> a letter, 9 -> a digit, everything else kept."""
    out, li, di = [], 0, 0
    for ch in shape:
        if ch == "A":
            out.append(_LETTERS[(n * 5 + li) % len(_LETTERS)])
            li += 1
        elif ch == "9":
            out.append(_DIGITS[(n * 3 + di) % len(_DIGITS)])
            di += 1
        else:
            out.append(ch)
    return "".join(out)


def fictional_pan(n: int = 0) -> str:
    """Structurally valid (AAAAA9999A, 4th letter P = an individual), belonging to nobody known."""
    v = fill_shape("AAAAA9999A", n)
    return v[:3] + "P" + v[4:]


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, c in enumerate(first14.upper()):
        product = _GSTIN_ALPHABET.index(c) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_ALPHABET[(36 - total % 36) % 36]


def fictional_gstin(n: int = 0) -> str:
    """State code + a fictional PAN + entity number + Z + a valid mod-36 check character."""
    body = "%02d%s1Z" % (n % 35 + 1, fictional_pan(n))
    return body + gstin_check_char(body)


def fictional_ack(n: int = 0, length: int = 15) -> str:
    """Digits ending in DDMMYY of a real date before the self-test day (an ITR ack's own date)."""
    when = SELF_TEST_TODAY - timedelta(days=5 + n)
    return fill_shape("9" * (length - 6), n) + when.strftime("%d%m%y")


def fictional_date(shape: str, n: int = 0) -> Optional[str]:
    """A date before the self-test day printed in whichever portal format has `shape`."""
    when = SELF_TEST_TODAY - timedelta(days=20 + n)
    for fmt in getattr(sgt_toolbox, "_DATE_FORMATS", ()):
        text = when.strftime(fmt)
        if mask_shape(text) == shape:
            return text
    return None


# ── Drafting a field from the maths ──────────────────────────────────────────────
class Drop(Exception):
    """A candidate that fails a gate; the reason is counted, never shown with a value."""


def _expand(shapes: Dict[str, int], cap: int = 200) -> List[str]:
    out: List[str] = []
    for s, n in sorted(shapes.items(), key=lambda kv: -kv[1]):
        out.extend([s.rstrip("…")] * min(int(n), cap))
    return out


def _positional(shapes: Sequence[str]) -> Optional[str]:
    """Same-length shapes with no punctuation (a GSTIN's last three characters are letter OR
    digit): one character class per position."""
    if not shapes or len({len(s) for s in shapes}) != 1 or any(c not in "A9" for s in shapes for c in s):
        return None
    parts: List[str] = []
    for i in range(len(shapes[0])):
        seen = {s[i] for s in shapes}
        cls = "[A-Za-z0-9]" if len(seen) > 1 else ("[A-Za-z]" if seen == {"A"} else r"\d")
        if parts and parts[-1][0] == cls:
            parts[-1][1] += 1
        else:
            parts.append([cls, 1])
    return "".join(c if n == 1 else "%s{%d}" % (c, n) for c, n in parts)


def identity_of(shapes: Sequence[str], rules: Dict[str, Any]) -> Optional[str]:
    """Which fictional generator fits: 'pan', 'gstin', 'ack' or None (plain shape fill)."""
    uniq = set(shapes)
    if uniq == {"AAAAA9999A"}:
        return "pan"
    if uniq and all(_GSTIN_SHAPE.match(s) for s in uniq):
        return "gstin"
    if rules.get("date_tail") and uniq and all(re.fullmatch(r"9{6,}", s) for s in uniq):
        return "ack"
    return None


def value_regex(typ: str, shapes: Dict[str, int], type_share: float) -> Tuple[str, Optional[str]]:
    """(the value regex with one group, the majority shape for examples or None). A shaped type
    (code, number, date) gets its regex from the shape grammar, with word boundaries round it;
    the rest a generic one. Raises Drop when the shapes do not agree well enough."""
    if typ in _TYPE_PATTERNS:
        return _TYPE_PATTERNS[typ][0], None
    if typ not in ("code", "number", "date"):
        raise Drop("type has no drafted pattern (%s)" % typ)
    flat = _expand(shapes)
    grammar = induce_shape_grammar(flat)
    if grammar is None:
        raise Drop("no shapes")
    share = 1 - grammar.exceptions / grammar.n
    core = grammar.pattern[1:-1]
    majority = Counter(flat).most_common(1)[0][0]
    if share < type_share:
        core = _positional(flat)
        if core is None:
            raise Drop("mixed shapes")
    return "%s(%s)%s" % (_BOUND_L, core, _BOUND_R), majority


def _checks_for(typ: str, identity: Optional[str], rules: Dict[str, Any]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(transforms, checks) from the type and the proven rules. Only rules the toolbox can
    enforce become checks; the others stay on the card as evidence."""
    transforms: List[str] = []
    checks: List[str] = []
    if typ == "date":
        transforms.append("parse_date")
        checks.append("is_real_date")
    if identity == "gstin" and rules.get("checksum") == "mod36":
        checks.append("gstin_checksum")
    if rules.get("date_tail"):
        checks.extend(["all_digits", "ddmmyy_tail_real"])
    if typ == "text":
        checks.append("not_ui_chrome")
    return tuple(transforms), tuple(dict.fromkeys(checks))


def _fictional_values(typ: str, shape: Optional[str], identity: Optional[str], n: int) -> Optional[str]:
    if typ in _TYPE_PATTERNS:
        ex = _TYPE_PATTERNS[typ][1]
        return ex[n % len(ex)] if n < len(ex) else None
    if identity == "pan":
        return fictional_pan(n)
    if identity == "gstin":
        return fictional_gstin(n)
    if identity == "ack":
        return fictional_ack(n, len(shape or "9" * 15))
    if typ == "date":
        return fictional_date(shape or "", n)
    return fill_shape(shape, n) if shape else None


def _passes(pattern: "re.Pattern[str]", transforms: Sequence[str], checks: Sequence[str],
            value: str) -> Optional[str]:
    """The value the Core would take from `value` alone, or None."""
    m = pattern.search(value)
    if not m:
        return None
    got: Optional[str] = next((g for g in m.groups() if g), None) if pattern.groups else m.group(0)
    for t in transforms:
        got = TRANSFORMS[t](got) if got else None
    if not got or not all(CHECKS[c](got, SELF_TEST_TODAY) for c in checks):
        return None
    return got


def _counters(identity: Optional[str], value: str) -> List[str]:
    out = [value + value]                   # the same shape run on: never a whole value
    if identity == "gstin":
        wrong = next(c for c in "0123456789" if c != value[-1])
        out.append(value[:-1] + wrong)
    if identity == "ack":
        out.append(value[:-4] + "13" + value[-2:])    # month 13: not a real date
    return out


def draft_field(label: str, typ: str, shapes: Dict[str, int], field: str, section: str,
                portal: str = "", rules: Optional[Dict[str, Any]] = None, confidence: int = 70,
                type_share: float = 0.98) -> Dict[str, Any]:
    """A field spec as sgt_fields.json writes it, filled from the maths, with fictional examples.
    `section` decides the example form: record fields carry value strings only (the record holds
    the page examples); profile and current_dataset fields also get a page example."""
    rules = rules or {}
    pattern_text, shape = value_regex(typ, shapes, type_share)
    flat = _expand(shapes) if shapes else []
    identity = identity_of(flat, rules) if flat else None
    transforms, checks = _checks_for(typ, identity, rules)
    case_insensitive = typ in ("yes/no", "email")
    try:
        compiled = sgt_specs.safe_compile(pattern_text, re.IGNORECASE if case_insensitive else 0)
    except SpecError as e:
        raise Drop("pattern refused by the Core: %s" % e)
    examples: List[Tuple[str, str]] = []
    for n in range(40):
        v = _fictional_values(typ, shape, identity, n)
        if v is None:
            break
        got = _passes(compiled, transforms, checks, v)
        if got is not None and v not in (e[0] for e in examples):
            examples.append((v, got))
        if len(examples) == 2:
            break
    if not examples:
        raise Drop("no fictional example fits the drafted pattern")
    spec: Dict[str, Any] = {"field": field, "labels": [label], "within": 2, "pattern": pattern_text,
                            "confidence": confidence}
    if case_insensitive:
        spec["case"] = "insensitive"
    if portal:
        spec["portals"] = [portal]
    if transforms:
        spec["transforms"] = list(transforms)
    if checks:
        spec["checks"] = list(checks)
    spec["examples"] = [v for v, _ in examples]
    if section != RECORDS:
        spec["examples"].append({"lines": [label, examples[0][0]], "expect": examples[0][1]})
    counters = [c for c in _counters(identity, examples[0][0]) if _passes(compiled, transforms, checks, c) is None]
    if counters and shape is not None:
        spec["counter_examples"] = counters
    spec["note"] = "Drafted by the SGT-I miner; examples are fictional."
    return spec


# ── Helpers shared by the sources ────────────────────────────────────────────────
def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:36] or "value"
    return s if s[0].isalpha() else "f_" + s


def _field_name(label: str, taken: Iterable[str]) -> str:
    """A lower_case field name from the label that never lands on a Core field (it would merge
    into that field's captures); the user can rename it in the lab."""
    s = _slug(label)
    return s if s not in set(taken) else (s[:34] + "_mined")


def _is_masked(label: str) -> bool:
    """The atlas stores a label in the clear only once it is template; until then it is a masked
    shape (only A, 9 and punctuation) or has a «type» hole."""
    return "«" in label or not re.search(r"[B-Zb-z0-8]", label) or bool(re.search(r"\d{4}", label))


def _placeholder(label: str, cfg: Dict[str, Any]) -> bool:
    return any(re.search(p, label, re.IGNORECASE) for p in cfg.get("placeholder_words") or ())


def _all_fields(registry: Any) -> List[Any]:
    out = list(registry.profile) + list(registry.current)
    for r in registry.records:
        out.extend(r.fields)
    return out


def _claimed(label: str, registry: Any) -> bool:
    """A Core spec already reads a value after exactly this label."""
    for f in _all_fields(registry):
        m = f.label_re.search(label) if f.label_re is not None else None
        if m and not m.group("rest").strip():
            return True
    return False


def _pid(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


class _Clients:
    """Counts different clients by an in-memory salted hash; nothing is kept after the run."""

    def __init__(self) -> None:
        self._salt = os.urandom(16)

    def key(self, row: Dict[str, Any]) -> Optional[str]:
        who = row.get("pan") or row.get("gstin")
        if not who:
            return None
        return hashlib.sha256(self._salt + str(who).upper().encode("utf-8")).hexdigest()[:16]


def _proposal(source: str, portal: str, key: str, **kw: Any) -> Dict[str, Any]:
    p = {"id": _pid(source, portal, key), "source": source, "portal": portal, "status": "pending",
         "needs": [], "rules": {}, "red": False}
    p.update(kw)
    return p


# ── Source 1 and 3: the atlas ─────────────────────────────────────────────────────
def _slot_gate(slot: Dict[str, Any], label: str, cfg: Dict[str, Any], registry: Any) -> Optional[str]:
    if slot.get("fading"):
        return "fading"
    if slot.get("zone") not in cfg["zones"]:
        return "placement: zone"
    if _is_masked(label):
        return "label not template yet"
    if _placeholder(label, cfg):
        return "placement: placeholder"
    if slot.get("claimed_by") or _claimed(label, registry):
        return "already claimed"
    if int(slot.get("clients") or 0) < cfg["min_clients"]:
        return "support"
    types = slot.get("types") or {}
    total = sum(types.values())
    if not total or max(types.values()) / total < cfg["type_share"]:
        return "mixed type"
    return None


def from_atlas(atlas: Atlas, portals: Iterable[str], registry: Any, cfg: Dict[str, Any],
               proven: Optional[Dict[str, Dict[str, Any]]] = None,
               dropped: Optional[Counter] = None) -> List[Dict[str, Any]]:
    """Sources 1 (single slots -> profile / current_dataset fields) and 3 (repeating slots under
    one heading -> a list record). `proven` maps a container to step 3's proven rules
    ({"checksum": "mod36", "date_tail": True}); a slot may also carry its own "proven"."""
    dropped = dropped if dropped is not None else Counter()
    proven = proven or {}
    taken = {f.field for f in _all_fields(registry)}
    out: List[Dict[str, Any]] = []
    for portal in portals:
        pa = atlas.portal(portal)
        for pid, page in pa.pages.items():
            groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
            for sid, slot in (page.get("slots") or {}).items():
                container = str(slot.get("container") or "")
                label = container.split(SEP)[-1].strip()
                why = _slot_gate(slot, label, cfg, registry)
                if why:
                    dropped[why] += 1
                    continue
                if slot.get("repeating"):
                    groups.setdefault(SEP.join(container.split(SEP)[:-1]), []).append((sid, slot))
                    continue
                kind = slot.get("kind")
                section = PROFILE if kind == "profile" else CURRENT if kind in ("dataset", "identifier") else None
                if section is None:
                    dropped["kind unknown"] += 1
                    continue
                rules = dict(slot.get("proven") or proven.get(container) or {})
                try:
                    spec = draft_field(label, slot["type"], slot.get("shapes") or {}, _field_name(label, taken),
                                       section, portal, rules, type_share=cfg["type_share"])
                except Drop as e:
                    dropped[str(e).split(":")[0]] += 1
                    continue
                spec["name"] = "mined.%s.%s" % (_slug(portal), spec["field"])
                out.append(_proposal("template_vs_data", portal, container, page=pid, container=container,
                                     type=slot["type"], kind=kind, rules=rules, section=section, spec=spec,
                                     support={"clients": slot.get("clients", 0), "seen": slot.get("seen", 0)}))
            for heading, slots in groups.items():
                rec = _draft_record(portal, pid, heading, slots, taken, cfg, proven, dropped)
                if rec is not None:
                    out.append(rec)
    return out


def _draft_record(portal: str, pid: str, heading: str, slots: List[Tuple[str, Dict[str, Any]]],
                  taken: Iterable[str], cfg: Dict[str, Any], proven: Dict[str, Dict[str, Any]],
                  dropped: Counter) -> Optional[Dict[str, Any]]:
    """A card list: every repeating slot of one heading, in the order the atlas first saw them;
    a card starts at the first field's label; fields present on (nearly) every card are required."""
    slots = sorted(slots, key=lambda s: int(re.sub(r"\D", "", s[0]) or 0))
    fields: List[Dict[str, Any]] = []
    labels: List[str] = []
    kept: List[Dict[str, Any]] = []
    used = set(taken)
    for _, slot in slots:
        label = str(slot["container"]).split(SEP)[-1].strip()
        rules = dict(slot.get("proven") or proven.get(slot["container"]) or {})
        try:
            f = draft_field(label, slot["type"], slot.get("shapes") or {}, _field_name(label, used),
                            RECORDS, "", rules, type_share=cfg["type_share"])
        except Drop as e:
            dropped[str(e).split(":")[0]] += 1
            continue
        used.add(f["field"])
        fields.append(f)
        labels.append(label)
        kept.append(slot)
    if not fields:
        return None
    most = max(int(s.get("seen") or 0) for s in kept) or 1
    require = [f["field"] for f, s in zip(fields, kept) if int(s.get("seen") or 0) >= cfg["require_share"] * most]
    start = r"^\s*" + r"\s+".join(re.escape(w) for w in labels[0].split()) + r"(?![A-Za-z0-9])"
    cards: List[str] = []
    expect: List[Dict[str, str]] = []
    for n in range(2):
        want: Dict[str, str] = {}
        for f, label in zip(fields, labels):
            v = f["examples"][n % len(f["examples"])]
            cards.extend([label, v])
            got = v
            for t in f.get("transforms") or ():
                got = TRANSFORMS[t](got)
            want[f["field"]] = got
        expect.append(want)
    name = "mined.%s.%s" % (_slug(portal), _slug(heading.split(SEP)[-1] if heading else labels[0]))
    spec = {"name": name, "start": start, "max_lines": min(400, 2 * len(fields) + 4),
            "portals": [portal], "fields": fields, "require": require or [fields[0]["field"]],
            "examples": [{"lines": cards, "expect": expect}],
            "note": "Drafted by the SGT-I miner from a repeating block; examples are fictional."}
    clients = min(int(s.get("clients") or 0) for s in kept)
    return _proposal("list_record", portal, heading + "|" + "|".join(labels), page=pid, container=heading,
                     type="record", section=RECORDS, spec=spec,
                     support={"clients": clients, "seen": most, "fields": len(fields)})


# ── Source 2: known-value anchoring (step 8's synonyms) ──────────────────────────
def _sgt_i(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("raw_payload")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    got = (raw or {}).get("sgt_i") if isinstance(raw, dict) else None
    return got if isinstance(got, dict) else {}


def _donor(registry: Any, field: str) -> Optional[Tuple[str, Any]]:
    """The Core spec a new wording borrows its pattern and checks from: profile, then
    current_dataset, then a record's field (drafted as a whole-page record of its own)."""
    for section, specs in ((PROFILE, registry.profile), (CURRENT, registry.current)):
        for s in specs:
            if s.field == field and s.take == "after_label":
                return section, s
    for r in registry.records:
        for s in r.fields:
            if s.field == field and s.take == "after_label":
                return RECORDS, s
    return None


def from_synonyms(rows: Iterable[Dict[str, Any]], registry: Any, cfg: Dict[str, Any],
                  dropped: Optional[Counter] = None) -> List[Dict[str, Any]]:
    """Source 2. Rows are tracker payloads; each synonym ({"field", "container"}) is counted per
    client. Only a wording seen for >= min_clients different clients is template text."""
    dropped = dropped if dropped is not None else Counter()
    clients = _Clients()
    seen: Dict[Tuple[str, str, str], set] = {}
    for row in rows:
        who = clients.key(row)
        for syn in ((_sgt_i(row).get("expectations") or {}).get("synonyms") or []):
            container = syn.get("container") or []
            label = str(container[-1] if isinstance(container, list) and container else container).strip()
            if who and label:
                seen.setdefault((str(row.get("portal") or ""), str(syn.get("field") or ""), label), set()).add(who)
    aliases = cfg.get("field_aliases") or {}
    out: List[Dict[str, Any]] = []
    for (portal, raw_field, label), who in sorted(seen.items()):
        if len(who) < cfg["min_clients"]:
            dropped["support"] += 1
            continue
        if _placeholder(label, cfg) or re.search(r"\d{4}", label):
            dropped["placement: placeholder"] += 1
            continue
        if _claimed(label, registry):
            dropped["already claimed"] += 1
            continue
        field = aliases.get(_slug(raw_field), _slug(raw_field))
        donor = _donor(registry, field)
        if donor is None:
            dropped["no Core field to anchor to"] += 1
            continue
        section, s = donor
        spec = _copy_with_label(s, label, portal)
        if spec is None:
            dropped["no fictional example fits the drafted pattern"] += 1
            continue
        name = "mined.%s.%s.%s" % (_slug(portal), field, _slug(label))
        if section == RECORDS:
            ex = spec["examples"][0]
            expect = {field: ex["expect"]}
            spec["examples"] = [e for e in spec["examples"] if isinstance(e, str)]
            spec = {"name": name, "portals": spec.pop("portals", []) or [], "fields": [spec], "require": [field],
                    "examples": [{"lines": ex["lines"], "expect": [expect]}],
                    "note": "A new wording for the Core's %r, found by known-value anchoring." % field}
            if not spec["portals"]:
                del spec["portals"]
        else:
            spec["name"] = name
        out.append(_proposal("known_value", portal, field + "|" + label, container=label, type=field,
                             section=section, spec=spec, support={"clients": len(who)}))
    return out


def _copy_with_label(s: Any, label: str, portal: str) -> Optional[Dict[str, Any]]:
    """The donor's pattern, transforms and checks under the new label. Examples: the donor's own
    string examples (already fictional - they ship in sgt_fields.json), else a generator's."""
    values = [e for e in s.examples if isinstance(e, str)]
    if not values:
        gen = {"pan": fictional_pan, "gstin": fictional_gstin, "arn": fictional_ack, "tan": None}.get(s.field)
        values = [gen(0), gen(1)] if gen else []
    good = [(v, _passes(s.pattern, s.transforms, s.checks, v)) for v in values]
    good = [(v, g) for v, g in good if g is not None]
    if not good:
        return None
    spec: Dict[str, Any] = {"field": s.field, "labels": [label], "within": s.within, "pattern": s.pattern.pattern,
                            "confidence": min(s.confidence, 80)}
    if s.pattern.flags & re.IGNORECASE:
        spec["case"] = "insensitive"
    if portal:
        spec["portals"] = [portal]
    if s.transforms:
        spec["transforms"] = list(s.transforms)
    if s.checks:
        spec["checks"] = list(s.checks)
    if s.slot == "profile" and s.merge != "latch":
        spec["merge"] = s.merge
    spec["examples"] = [{"lines": [label, good[0][0]], "expect": good[0][1]}] + [v for v, _ in good[:2]]
    spec["note"] = "Drafted by the SGT-I miner: a new wording for %r; examples are fictional." % s.field
    return spec


# ── Source 4: status wording from outcomes ───────────────────────────────────────
def _status_known(phrase: str, registry: Any) -> bool:
    return any(f.field == "status" and extract_value(f, phrase, SELF_TEST_TODAY) is not None
               for f in _all_fields(registry))


def _phrase(line: str, cfg: Dict[str, Any]) -> Optional[str]:
    text = " ".join(line.split()).strip(" \t:;,.-–|!")
    lo, hi = cfg.get("status_phrase_words") or (2, 8)
    if not text or re.search(r"\d", text) or not lo <= len(text.split()) <= hi:
        return None
    if not CHECKS["not_ui_chrome"](text, SELF_TEST_TODAY):
        return None
    return text


def _status_regex(phrase: str) -> str:
    return r"\s+".join(re.escape(w) for w in phrase.split())


def from_status_phrases(pages: Sequence[Dict[str, Any]], replayed: Dict[str, Dict[str, Any]],
                        registry: Any, cfg: Dict[str, Any],
                        dropped: Optional[Counter] = None) -> List[Dict[str, Any]]:
    """Source 4. For each dataset the Core settled (submit level >= `status_settled_level`), the
    lines just before its identifier first appeared are candidate wording. A phrase counts once
    per client; with >= min_clients it is template text and becomes a proposal whose ladder
    level the USER picks (`needs: ["level"]`)."""
    from ..sgt.sgt_corpus import group_sessions

    dropped = dropped if dropped is not None else Counter()
    clients = _Clients()
    counts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    window = int(cfg.get("status_window") or 3)
    for sid, group in group_sessions(list(pages)).items():
        rows = ((replayed.get(sid) or {}).get("rows") or {}).values()
        for row in rows:
            arn = str(row.get("arn") or "")
            if not arn or arn == "N/A" or submit_level(row.get("status")) < int(cfg["status_settled_level"]):
                continue
            who = clients.key(row) or sid
            for page in group:
                lines = prepare_lines(page.get("lines") or [])
                at = next((i for i, ln in enumerate(lines) if arn in ln), None)
                if at is None:
                    continue
                for ln in lines[max(0, at - window):at + 1]:
                    ph = _phrase(ln.replace(arn, " "), cfg)
                    if ph is None or _status_known(ph, registry) or _claimed(ph, registry):
                        continue
                    c = counts.setdefault((str(page.get("portal") or ""), ph.casefold()),
                                          {"phrase": ph, "clients": set(), "levels": Counter()})
                    c["clients"].add(who)
                    c["levels"][row.get("status")] += 1
                break
    out: List[Dict[str, Any]] = []
    for (portal, _), c in sorted(counts.items()):
        if len(c["clients"]) < cfg["min_clients"]:
            dropped["support"] += 1
            continue
        rx = _status_regex(c["phrase"])
        spec = {"name": "mined.%s.status.%s" % (_slug(portal), _slug(c["phrase"])), "field": "status",
                "take": "anywhere", "case": "insensitive", "pattern": r"^\s*(%s)\s*[.!]?\s*$" % rx,
                "map": [[rx, None]], "confidence": 80, "examples": [c["phrase"]],
                "counter_examples": ["Status", c["phrase"] + " failed"],
                "note": "Drafted by the SGT-I miner from wording seen before a settled dataset's identifier."}
        if portal:
            spec["portals"] = [portal]
        out.append(_proposal("status_wording", portal, c["phrase"].casefold(), container=c["phrase"],
                             type="status", section=CURRENT, spec=spec, needs=["level"],
                             support={"clients": len(c["clients"]),
                                      "levels_seen": {str(k): v for k, v in c["levels"].items()}}))
    return out


# ── Source 5: graduation candidates ──────────────────────────────────────────────
Verdict = Callable[[Dict[str, Any], Dict[str, Any]], Optional[bool]]


def from_second_opinions(rows: Iterable[Dict[str, Any]], verdict: Optional[Verdict], cfg: Dict[str, Any],
                         dropped: Optional[Counter] = None) -> List[Dict[str, Any]]:
    """Source 5. `verdict(row, opinion)` says whether a second opinion would have been right
    (True / False / None = cannot tell) - e.g. from a replay against the dataset's settled value.
    A rule right for >= min_clients clients and never wrong is offered as a toolbox rule; it
    carries no spec (a developer builds the tool, 14.2 graduation)."""
    dropped = dropped if dropped is not None else Counter()
    if verdict is None:
        return []
    clients = _Clients()
    tally: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        who = clients.key(row)
        for ds in ((_sgt_i(row).get("ledger") or {}).get("datasets") or []):
            for op in ds.get("second_opinions") or []:
                rule = str(op.get("rule") or "")
                if not rule:
                    continue
                t = tally.setdefault((str(row.get("portal") or ""), rule), {"right": set(), "wrong": 0, "unknown": 0})
                v = verdict(row, op)
                if v is True and who:
                    t["right"].add(who)
                elif v is False:
                    t["wrong"] += 1
                else:
                    t["unknown"] += 1
    out: List[Dict[str, Any]] = []
    for (portal, rule), t in sorted(tally.items()):
        if t["wrong"]:
            dropped["graduation: wrong at least once"] += 1
            continue
        if len(t["right"]) < cfg["min_clients"]:
            dropped["support"] += 1
            continue
        out.append(_proposal("graduation", portal, rule, container=rule, type="toolbox rule", section=None,
                             spec=None, needs=["developer"],
                             support={"clients": len(t["right"]), "wrong": 0, "unknown": t["unknown"]}))
    return out


# ── Gates: the Core loader, the replay diff ──────────────────────────────────────
def _as_file(section: Optional[str], spec: Dict[str, Any]) -> Dict[str, Any]:
    if section == CURRENT:
        return {"current_dataset": {"fields": [spec]}}
    return {section: [spec]}


def spec_for_accept(proposal: Dict[str, Any], level: Optional[str] = None) -> Dict[str, Any]:
    """The spec to write on Accept. A status-wording proposal needs the user's ladder level."""
    spec = json.loads(json.dumps(proposal["spec"]))
    if "level" in (proposal.get("needs") or []):
        if level not in SUBMIT_LEVELS:
            raise ValueError("pick a level of the submit ladder: %s" % ", ".join(SUBMIT_LEVELS))
        spec["map"] = [[rx, level] for rx, _ in spec["map"]]
    return spec


def _tmp_json(data: Dict[str, Any]) -> Path:
    fd, name = tempfile.mkstemp(prefix="sgt_miner_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return Path(name)


def check_spec(section: str, spec: Dict[str, Any], base_paths: Sequence[Path]) -> Optional[str]:
    """None when the Core loader builds the spec and its self-tests pass beside every existing
    spec (the same stop-at-a-label rule as live pages); else the loader's reason."""
    path = _tmp_json(_as_file(section, spec))
    try:
        reg = sgt_specs.load_registry(list(base_paths) + [path])
    finally:
        path.unlink(missing_ok=True)
    name = spec.get("name") or spec.get("field")
    errors = [e for e in reg.errors if repr(name) in e]
    if errors:
        return errors[0].split(": ", 1)[-1]
    if name not in reg.by_name():
        return "not loaded"
    return None


def replay_diff(section: str, spec: Dict[str, Any], pages: Sequence[Dict[str, Any]],
                base_paths: Sequence[Path], before: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What the proposal would change over the recorded corpus, as counts (diff lines name real
    values, so they never reach proposals.json). `red` = any existing capture changes."""
    from ..sgt.sgt_replay import diff, replay
    from ..sgt.sgt_specs import SpecStore

    if not pages:
        return {"pages": 0, "adds": 0, "added": 0, "changed": 0, "removed": 0, "held": 0, "red": False}
    quiet = lambda *_a, **_k: None
    if before is None:
        before = replay(pages, SpecStore(list(base_paths), log=quiet))
    path = _tmp_json(_as_file(section, spec))
    try:
        store = SpecStore(list(base_paths) + [path], log=quiet)
        after = replay(pages, store)
        reg = store.get()
    finally:
        path.unlink(missing_ok=True)
    lines = diff(before, after)
    counts = Counter(ln.split("]", 1)[-1].strip().split(":")[0].split()[0] for ln in lines)
    new = reg.by_name().get(spec.get("name") or spec.get("field"))
    adds = 0
    for page in pages:
        lns = prepare_lines(page.get("lines") or [])
        if new is None or not new.applies(str(page.get("portal") or ""), str(page.get("url") or "")):
            continue
        if isinstance(new, sgt_specs.RecordSpec):
            from ..sgt.sgt_resolver import resolve_records
            adds += bool(resolve_records(new, lns, SELF_TEST_TODAY))
        else:
            from ..sgt.sgt_resolver import resolve_single
            adds += resolve_single(new, lns, SELF_TEST_TODAY, link=str(page.get("url") or ""),
                                   title=str(page.get("title") or "")) is not None
    out = {"pages": len(pages), "adds": adds, "added": counts.get("newly", 0),
           "changed": counts.get("changed", 0), "removed": counts.get("no", 0), "held": counts.get("held", 0)}
    out["red"] = bool(out["changed"] or out["removed"] or out["held"])
    return out


# ── The whole run ────────────────────────────────────────────────────────────────
def _base_paths() -> List[Path]:
    return [sgt_specs.BUILTIN_FIELDS_PATH, sgt_specs.override_path()]


def mine(atlas: Optional[Atlas] = None, portals: Optional[Iterable[str]] = None,
         pages: Sequence[Dict[str, Any]] = (), rows: Iterable[Dict[str, Any]] = (),
         verdict: Optional[Verdict] = None, proven: Optional[Dict[str, Dict[str, Any]]] = None,
         config: Optional[Dict[str, Any]] = None, base_paths: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    """Every source, every gate. `pages` = recorded corpus pages (sgt_corpus.load_pages());
    `rows` = tracker payloads carrying raw_payload['sgt_i']. Returns the proposals.json dict."""
    from ..sgt.sgt_replay import replay
    from ..sgt.sgt_specs import SpecStore

    cfg = dict(load_config())
    cfg.update(config or {})
    paths = list(base_paths) if base_paths is not None else _base_paths()
    registry = sgt_specs.load_registry(paths)
    rows = list(rows)
    pages = list(pages)
    dropped: Counter = Counter()
    before = replay(pages, SpecStore(paths, log=lambda *_a, **_k: None)) if pages else {}

    found: List[Dict[str, Any]] = []
    if atlas is not None:
        names = list(portals) if portals is not None else _atlas_portals(atlas, registry)
        found += from_atlas(atlas, names, registry, cfg, proven, dropped)
    found += from_synonyms(rows, registry, cfg, dropped)
    if pages:
        found += from_status_phrases(pages, before, registry, cfg, dropped)
    found += from_second_opinions(rows, verdict, cfg, dropped)

    out: List[Dict[str, Any]] = []
    seen_ids = set()
    for p in found:
        if p["id"] in seen_ids:
            continue
        seen_ids.add(p["id"])
        if p["spec"] is not None:
            preview = p["spec"]
            if "level" in p["needs"]:
                levels = p["support"].get("levels_seen") or {}
                pick = max(levels, key=levels.get) if levels else SUBMIT_LEVELS[2]
                preview = spec_for_accept(p, pick if pick in SUBMIT_LEVELS else SUBMIT_LEVELS[2])
            why = check_spec(p["section"], preview, paths)
            if why:
                dropped["Core self-test: " + why.split(":")[0][:60]] += 1
                continue
            p["replay"] = replay_diff(p["section"], preview, pages, paths, before)
            p["red"] = p["replay"]["red"]
        out.append(p)
    out.sort(key=lambda p: (p["red"], -int(p["support"].get("clients") or 0), -(p.get("replay") or {}).get("adds", 0)))
    return {"format": FORMAT, "generated": datetime.now().isoformat(timespec="seconds"),
            "proposals": out[:int(cfg["max_proposals"])], "dropped": dict(sorted(dropped.items()))}


def _atlas_portals(atlas: Atlas, registry: Any) -> List[str]:
    """Portals with an atlas file, spelled the way the Core's specs spell them (the atlas keys
    its files by the lower-cased name; a spec's "portals" must match the Core's exactly)."""
    base = Path(getattr(atlas, "_dir", None) or sgt_i_dir()) / ATLAS_DIR
    known = {p.casefold(): p for f in _all_fields(registry) for p in f.portals}
    known.update({p.casefold(): p for r in registry.records for p in r.portals})
    out = []
    for f in sorted(base.glob("*.json")) if base.is_dir() else []:
        try:
            name = str(json.loads(f.read_text(encoding="utf-8")).get("portal") or "")
        except Exception:
            continue
        if name:
            out.append(known.get(name.casefold(), name))
    return out


def write_proposals(result: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Atomically writes proposals.json, keeping the user's decision (accepted / rejected) on any
    proposal that is proposed again."""
    path = path or proposals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = {p["id"]: p for p in json.loads(path.read_text(encoding="utf-8")).get("proposals") or []}
    except Exception:
        old = {}
    for p in result.get("proposals") or []:
        prev = old.get(p["id"])
        if prev and prev.get("status") not in (None, "pending"):
            p["status"] = prev["status"]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path
