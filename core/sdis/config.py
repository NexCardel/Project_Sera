"""
core/sdis/config.py - sdis_containers.json, the containers and exceptions file (blueprint S.3)
===============================================================================================
Two files are read: the built-in core/sdis/sdis_containers.json (empty), then the office copy at
<Sera data dir>/sdis_containers.json; a top-level section of the office copy replaces the built-in
one. check() refuses the whole file with a reason, and the previous good version keeps running
(the same rule as core/sgt/sgt_specs.py), so a bad edit never takes working scope down.

Every section is checked (S.3 load checks). Completion levels are never in code: no level name or
threshold is assumed here; level_for() only evaluates what the file says. The checks that need
the office database - field names exist in sdis_mcl, portals are registered - run when the caller
passes `fields` / `portals` (put() and import do); loading the written file skips them, because
that document was checked when it was put.

The office copy is one row of the synced table sdis_config (sera_db/sdis.py); every PC writes it
to the file with write_file() when the row changes and at start-up.
"""

import copy
import json
import math
import os
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

CONFIG_FILE_NAME = "sdis_containers.json"
CONFIG_ENV = "SDIS_CONTAINERS_PATH"          # tests and tools point the office copy elsewhere
BUILTIN_PATH = Path(__file__).with_name(CONFIG_FILE_NAME)
SUPPORTED_VERSION = 1
SECTIONS = ("version", "levels", "level_map", "profile", "containers", "others",
            "class_exceptions", "portal_exceptions")
_PORTAL_EXCEPTION_KEYS = ("extra_domains", "never_register")
_CONTAINER_KEYS = ("name", "portal", "form_field", "period_field", "fields", "exceptions", "examples")
_EXCEPTION_KEYS = ("optional", "proves", "levels", "level_map")
_WHEN_KEYS = ("captured", "fields")
CLASSES = ("profile", "dataset", "info")
PORTAL_KINDS = ("profile", "others")        # one Profile builder and one Others per portal (D19)
_SHARE = re.compile(r"^(\d+(?:\.\d+)?)%$")


class ConfigError(ValueError):
    pass


def empty() -> Dict[str, Any]:
    return {"version": SUPPORTED_VERSION, "levels": [], "level_map": {}, "profile": {}, "containers": [],
            "others": {}, "class_exceptions": {},
            "portal_exceptions": {"extra_domains": {}, "never_register": []}}


def office_path() -> Path:
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME   # importing core.vsdc loads Qt: only when needed
    return Path.home() / SERA_DATA_DIR_NAME / CONFIG_FILE_NAME


def _domain_entry_ok(raw: Any) -> bool:
    """Part T's domain checks: a plain hostname that gives a registered domain (never a bare
    suffix, localhost or a URL)."""
    from core.vsdc.vsdc_scope import extract_host, registered_domain
    if not isinstance(raw, str):
        return False
    dom = raw.strip().lower().rstrip(".")
    return bool(dom) and extract_host(dom) == dom and registered_domain(dom) is not None


def check(data: Any, fields: Optional[Iterable[str]] = None,
          portals: Optional[Iterable[str]] = None) -> None:
    """Raises ConfigError naming the first problem. fields: every sdis_mcl field name; portals:
    the registered portal names; either None skips that membership check."""
    if not isinstance(data, dict):
        raise ConfigError("must be a JSON object")
    ver = data.get("version")
    if isinstance(ver, bool) or not isinstance(ver, int) or not 1 <= ver <= SUPPORTED_VERSION:
        raise ConfigError(f"version must be a whole number from 1 to {SUPPORTED_VERSION}, not {ver!r}")
    _check_portal_exceptions(data)
    known = set(fields) if fields is not None else None
    ports = set(portals) if portals is not None else None
    file_levels = _check_levels(data.get("levels", []), "levels")
    _check_level_map(data.get("level_map", {}), file_levels, "level_map")

    def field_ok(f: Any, where: str) -> None:
        if not isinstance(f, str) or not f.strip():
            raise ConfigError(f"{where}: a field name must be a non-empty text")
        if known is not None and f not in known:
            raise ConfigError(f"{where}: field {f!r} is not in sdis_mcl")

    def portal_ok(p: Any, where: str) -> None:
        if not isinstance(p, str) or not p.strip():
            raise ConfigError(f"{where}: a portal name must be a non-empty text")
        if ports is not None and p not in ports:
            raise ConfigError(f"{where}: portal {p!r} is not a registered portal")

    held: Dict[Tuple[str, str], str] = {}       # (portal, field) -> the container holding it

    def hold(portal: str, f: str, owner: str) -> None:
        other = held.get((portal, f))
        if other is not None and other != owner:
            raise ConfigError(f"field {f!r} is in two containers on portal {portal!r}: {other} and {owner}")
        held[(portal, f)] = owner

    for kind in PORTAL_KINDS:
        sec = data.get(kind, {})
        if not isinstance(sec, dict):
            raise ConfigError(f"{kind} must be an object {{portal: [fields]}}")
        for portal, flist in sec.items():
            portal_ok(portal, kind)
            if not isinstance(flist, list):
                raise ConfigError(f"{kind}[{portal!r}] must be a list of fields")
            if len(set(map(str, flist))) != len(flist):
                raise ConfigError(f"{kind}[{portal!r}]: a field is listed twice")
            for f in flist:
                field_ok(f, f"{kind}[{portal!r}]")
                hold(portal, f, f"{kind} of {portal}")

    conts = data.get("containers", [])
    if not isinstance(conts, list):
        raise ConfigError("containers must be a list")
    names = set()
    for c in conts:
        if not isinstance(c, dict):
            raise ConfigError("containers: every entry must be an object")
        name = c.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("containers: a container has no name")
        if name in names:
            raise ConfigError(f"containers: the name {name!r} is used twice")
        names.add(name)
        where = f"container {name!r}"
        for key in c:
            if key not in _CONTAINER_KEYS:
                raise ConfigError(f"{where}: unknown key {key!r} (allowed: {', '.join(_CONTAINER_KEYS)})")
        portal_ok(c.get("portal"), where)
        flist = c.get("fields")
        if not isinstance(flist, list):
            raise ConfigError(f"{where}: fields must be a list")
        if len(set(map(str, flist))) != len(flist):
            raise ConfigError(f"{where}: a field is listed twice")
        for f in flist:
            field_ok(f, where)
            hold(c["portal"], f, where)
        mine = set(flist)
        for key in ("period_field", "form_field"):
            v = c.get(key)
            if v is not None and v not in mine:
                raise ConfigError(f"{where}: {key} {v!r} is not one of its fields")
        exc = c.get("exceptions") or {}
        if not isinstance(exc, dict):
            raise ConfigError(f"{where}: exceptions must be an object")
        for key in exc:
            if key not in _EXCEPTION_KEYS:
                raise ConfigError(f"{where}: unknown exception {key!r} (allowed: {', '.join(_EXCEPTION_KEYS)})")
        optional = exc.get("optional") or []
        if not isinstance(optional, list):
            raise ConfigError(f"{where}: exceptions.optional must be a list of its fields")
        for f in optional:
            if f not in mine:
                raise ConfigError(f"{where}: exceptions.optional names {f!r}, not one of its fields")
            if f == c.get("period_field"):
                raise ConfigError(f"{where}: the period field can never be optional")
        if not [f for f in flist if f not in optional]:
            raise ConfigError(f"{where}: no field counts (every field is optional or there are none)")
        if exc.get("levels") is not None:
            levels = _check_levels(exc["levels"], f"{where}: exceptions.levels")
            lmap = exc.get("level_map")
            _check_level_map({} if lmap is None else lmap, levels, f"{where}: exceptions.level_map")
        else:
            levels = file_levels
            if exc.get("level_map") is not None:
                _check_level_map(exc["level_map"], levels, f"{where}: exceptions.level_map")
        for lv in (exc["levels"] if exc.get("levels") is not None else data.get("levels") or []):
            for f in lv["when"].get("fields") or []:
                if f not in mine:
                    raise ConfigError(f"{where}: level {lv['name']!r} needs field {f!r}, not one of its fields")
        proves = exc.get("proves") or {}
        if not isinstance(proves, dict):
            raise ConfigError(f"{where}: exceptions.proves must be an object {{field: level}}")
        for f, lv in proves.items():
            if f not in mine:
                raise ConfigError(f"{where}: exceptions.proves names {f!r}, not one of its fields")
            if lv not in levels:
                raise ConfigError(f"{where}: exceptions.proves[{f!r}] names {lv!r}, not a defined level")
        examples = c.get("examples", [])
        if not isinstance(examples, list):
            raise ConfigError(f"{where}: examples must be a list")
        for i, ex in enumerate(examples):
            if not isinstance(ex, dict) or not isinstance(ex.get("captured"), list) or "level" not in ex:
                raise ConfigError(f"{where}: example {i + 1} must be {{\"captured\": [fields], \"level\": name or null}}")
            for f in ex["captured"]:
                if f not in mine:
                    raise ConfigError(f"{where}: example {i + 1} captures {f!r}, not one of its fields")
            got = level_for(c, ex["captured"], data)
            if got != ex["level"]:
                raise ConfigError(f"{where}: example {i + 1} claims level {ex['level']!r} but gives {got!r}")

    ce = data.get("class_exceptions", {})
    if not isinstance(ce, dict):
        raise ConfigError("class_exceptions must be an object {field: class}")
    for f, cls in ce.items():
        field_ok(f, "class_exceptions")
        if cls not in CLASSES:
            raise ConfigError(f"class_exceptions[{f!r}]: {cls!r} is not a class ({', '.join(CLASSES)})")


def _check_levels(levels: Any, where: str) -> List[str]:
    """The level names, lowest first; raises on a malformed list."""
    if not isinstance(levels, list):
        raise ConfigError(f"{where} must be a list")
    names: List[str] = []
    for lv in levels:
        if not isinstance(lv, dict) or not isinstance(lv.get("name"), str) or not lv["name"].strip():
            raise ConfigError(f"{where}: every level needs a name")
        if lv["name"] in names:
            raise ConfigError(f"{where}: level {lv['name']!r} is defined twice")
        names.append(lv["name"])
        when = lv.get("when")
        if not isinstance(when, dict) or not when:
            raise ConfigError(f"{where}: level {lv['name']!r} needs a when with captured and/or fields")
        for key in when:
            if key not in _WHEN_KEYS:
                raise ConfigError(f"{where}: level {lv['name']!r}: when may use only captured / fields, not {key!r}")
        if "captured" in when:
            _parse_captured(when["captured"], f"{where}: level {lv['name']!r}")
        if "fields" in when:
            fl = when["fields"]
            if not isinstance(fl, list) or not fl or not all(isinstance(f, str) and f for f in fl):
                raise ConfigError(f"{where}: level {lv['name']!r}: when.fields must be a non-empty list of fields")
    return names


def _check_level_map(lmap: Any, levels: Sequence[str], where: str) -> None:
    from core.sgt.sgt_toolbox import SUBMIT_LEVELS
    if not isinstance(lmap, dict):
        raise ConfigError(f"{where} must be an object {{level: ladder status}}")
    for lv, status in lmap.items():
        if lv not in levels:
            raise ConfigError(f"{where}: {lv!r} is not a defined level")
        if status not in SUBMIT_LEVELS:
            raise ConfigError(f"{where}[{lv!r}]: {status!r} is not a status of the submit ladder")


def _parse_captured(value: Any, where: str = "captured") -> Tuple[str, Union[int, Fraction]]:
    """('count', k) | ('share', p) | ('all', 0)."""
    if value == "all":
        return "all", 0
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise ConfigError(f"{where}: captured must be 1 or more, not {value!r}")
        return "count", value
    m = _SHARE.match(value) if isinstance(value, str) else None
    if m:
        p = Fraction(m.group(1))
        if 1 <= p <= 100:
            return "share", p
    raise ConfigError(f"{where}: captured must be a number, a share between 1% and 100%, or \"all\", not {value!r}")


# ── Evaluating the levels (no level name or threshold is assumed) ────────────────

def counted_fields(container: Dict[str, Any]) -> List[str]:
    """The fields that count in n: every field minus exceptions.optional."""
    optional = set((container.get("exceptions") or {}).get("optional") or ())
    return [f for f in container.get("fields") or () if f not in optional]


def levels_of(container: Dict[str, Any], doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    own = (container.get("exceptions") or {}).get("levels")
    return list(own if own is not None else doc.get("levels") or [])


def level_map_of(container: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, str]:
    exc = container.get("exceptions") or {}
    if exc.get("level_map") is not None:
        return dict(exc["level_map"])
    return {} if exc.get("levels") is not None else dict(doc.get("level_map") or {})


def _when_holds(when: Dict[str, Any], k: int, n: int, captured: set) -> bool:
    if "captured" in when:
        kind, v = _parse_captured(when["captured"])
        need = n if kind == "all" else v if kind == "count" else math.ceil(v * n / 100)
        if (k != n) if kind == "all" else (k < need):
            return False
    return all(f in captured for f in when.get("fields") or ())


def level_for(container: Dict[str, Any], captured_fields: Iterable[str], doc: Dict[str, Any]) -> Optional[str]:
    """The level an instance with these captured fields is at: the highest level whose `when`
    holds, raised to every `proves` hit; None when there are no levels (or none holds) - the
    caller then shows only 'k of n'. Promotion over time (never lower) is the caller's."""
    levels = levels_of(container, doc)
    if not levels:
        return None
    captured = set(captured_fields)
    counted = counted_fields(container)
    n, k = len(counted), len([f for f in counted if f in captured])
    best = -1
    for i, lv in enumerate(levels):
        if _when_holds(lv.get("when") or {}, k, n, captured):
            best = i
    names = [lv["name"] for lv in levels]
    for f, lv in ((container.get("exceptions") or {}).get("proves") or {}).items():
        if f in captured and lv in names:
            best = max(best, names.index(lv))
    return names[best] if best >= 0 else None


def k_of_n(container: Dict[str, Any], captured_fields: Iterable[str]) -> Tuple[int, int]:
    captured = set(captured_fields)
    counted = counted_fields(container)
    return len([f for f in counted if f in captured]), len(counted)


# ── Editing helpers: each returns a NEW document that passes check() ─────────────
# `where` names a dataset container by its name, or a portal's Profile builder / Others as
# ("profile", portal) / ("others", portal). `known` = check()'s fields= / portals=.

Where = Union[str, Tuple[str, str]]


def _done(doc: Dict[str, Any], known: Dict[str, Any]) -> Dict[str, Any]:
    check(doc, **known)
    return doc


def _container(doc: Dict[str, Any], name: str) -> Dict[str, Any]:
    for c in doc.get("containers") or []:
        if c.get("name") == name:
            return c
    raise ConfigError(f"no container named {name!r}")


def _field_list(doc: Dict[str, Any], where: Where, create: bool = False) -> List[str]:
    if isinstance(where, tuple):
        kind, portal = where
        if kind not in PORTAL_KINDS:
            raise ConfigError(f"unknown container kind {kind!r}")
        sec = doc.setdefault(kind, {})
        if portal not in sec:
            if not create:
                raise ConfigError(f"no {kind} fields on portal {portal!r}")
            sec[portal] = []
        return sec[portal]
    return _container(doc, where).setdefault("fields", [])


def add_container(doc: Dict[str, Any], name: str, portal: str, members: Sequence[str],
                  period_field: Optional[str] = None, form_field: Optional[str] = None,
                  **known: Any) -> Dict[str, Any]:
    """A new dataset container holding `members` (at least one counted field)."""
    new = copy.deepcopy(doc)
    new.setdefault("containers", []).append({
        "name": name, "portal": portal, "form_field": form_field, "period_field": period_field,
        "fields": list(members), "exceptions": {"optional": [], "proves": {}, "levels": None, "level_map": None},
        "examples": []})
    return _done(new, known)


def rename_container(doc: Dict[str, Any], old: str, new_name: str, **known: Any) -> Dict[str, Any]:
    new = copy.deepcopy(doc)
    _container(new, old)["name"] = new_name
    return _done(new, known)


def delete_container(doc: Dict[str, Any], name: str, **known: Any) -> Dict[str, Any]:
    new = copy.deepcopy(doc)
    _container(new, name)
    new["containers"] = [c for c in new["containers"] if c.get("name") != name]
    return _done(new, known)


def add_field(doc: Dict[str, Any], where: Where, field: str, **known: Any) -> Dict[str, Any]:
    new = copy.deepcopy(doc)
    flist = _field_list(new, where, create=True)
    if field not in flist:
        flist.append(field)
    return _done(new, known)


def _drop_field(doc: Dict[str, Any], where: Where, field: str) -> None:
    flist = _field_list(doc, where)
    if field not in flist:
        raise ConfigError(f"field {field!r} is not in that container")
    flist.remove(field)
    if isinstance(where, tuple):
        if not flist:
            del doc[where[0]][where[1]]
        return
    c = _container(doc, where)
    for key in ("period_field", "form_field"):
        if c.get(key) == field:
            c[key] = None
    exc = c.get("exceptions") or {}
    if field in (exc.get("optional") or ()):
        exc["optional"] = [f for f in exc["optional"] if f != field]
    (exc.get("proves") or {}).pop(field, None)
    # an example that captured the field describes a combination that no longer exists
    c["examples"] = [ex for ex in c.get("examples") or () if field not in (ex.get("captured") or ())]


def remove_field(doc: Dict[str, Any], where: Where, field: str, **known: Any) -> Dict[str, Any]:
    new = copy.deepcopy(doc)
    _drop_field(new, where, field)
    return _done(new, known)


def move_field(doc: Dict[str, Any], field: str, src: Where, dst: Where, **known: Any) -> Dict[str, Any]:
    new = copy.deepcopy(doc)
    _drop_field(new, src, field)
    flist = _field_list(new, dst, create=True)
    if field not in flist:
        flist.append(field)
    return _done(new, known)


def mark_key(doc: Dict[str, Any], container: str, field: Optional[str], key: str = "period",
             **known: Any) -> Dict[str, Any]:
    """Marks `field` as the container's period (or form) field; None clears the mark."""
    if key not in ("period", "form"):
        raise ConfigError("key must be 'period' or 'form'")
    new = copy.deepcopy(doc)
    _container(new, container)[key + "_field"] = field
    return _done(new, known)


def set_exception(doc: Dict[str, Any], container: str, kind: str, field: str, value: Any,
                  **known: Any) -> Dict[str, Any]:
    """kind 'optional': value True/False; kind 'proves': value a level name, or None to clear."""
    new = copy.deepcopy(doc)
    exc = _container(new, container).setdefault("exceptions", {})
    if kind == "optional":
        opt = [f for f in exc.get("optional") or [] if f != field]
        exc["optional"] = opt + [field] if value else opt
    elif kind == "proves":
        proves = dict(exc.get("proves") or {})
        if value is None:
            proves.pop(field, None)
        else:
            proves[field] = value
        exc["proves"] = proves
    else:
        raise ConfigError(f"unknown exception {kind!r}")
    return _done(new, known)


def set_levels(doc: Dict[str, Any], levels: Optional[List[Dict[str, Any]]],
               level_map: Optional[Dict[str, str]] = None, container: Optional[str] = None,
               **known: Any) -> Dict[str, Any]:
    """The file's levels (container None; levels None = none), or a container's own (levels None
    = use the file's again)."""
    new = copy.deepcopy(doc)
    if container is None:
        new["levels"] = copy.deepcopy(levels or [])
        new["level_map"] = dict(level_map or {})
    else:
        exc = _container(new, container).setdefault("exceptions", {})
        exc["levels"] = copy.deepcopy(levels)
        exc["level_map"] = dict(level_map) if level_map is not None else None
    return _done(new, known)


def _check_portal_exceptions(data: Dict[str, Any]) -> None:
    pe = data.get("portal_exceptions", {})
    if not isinstance(pe, dict):
        raise ConfigError("portal_exceptions must be an object")
    for key in pe:
        if key not in _PORTAL_EXCEPTION_KEYS:
            raise ConfigError(f"portal_exceptions: unknown key {key!r} (allowed: {', '.join(_PORTAL_EXCEPTION_KEYS)})")
    extra = pe.get("extra_domains", {})
    if not isinstance(extra, dict):
        raise ConfigError("portal_exceptions.extra_domains must be an object {portal: [domains]}")
    for portal, doms in extra.items():
        if not portal.strip():
            raise ConfigError("portal_exceptions.extra_domains: a portal name is empty")
        if not isinstance(doms, list):
            raise ConfigError(f"portal_exceptions.extra_domains[{portal!r}] must be a list of domains")
        for d in doms:
            if not _domain_entry_ok(d):
                raise ConfigError(f"portal_exceptions.extra_domains[{portal!r}]: {d!r} is not a plain domain "
                                  "(a bare suffix, localhost, an IP or a URL is never accepted)")
    never = pe.get("never_register", [])
    if not isinstance(never, list):
        raise ConfigError("portal_exceptions.never_register must be a list of domains")
    for d in never:
        if not _domain_entry_ok(d):
            raise ConfigError(f"portal_exceptions.never_register: {d!r} is not a plain domain")


def _read(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """(data, "") or (None, reason); a missing file is (None, "")."""
    try:
        if not path.is_file():
            return None, ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"could not be read: {e}"
    if not isinstance(data, dict):
        return None, "must be a JSON object"
    return data, ""


def load(paths: Optional[Sequence[Path]] = None,
         previous: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """
    (config, errors). Each file is laid over the result so far; a file that is unreadable or fails
    check() is refused whole, and the result so far - or `previous`, the last good config - keeps
    running.
    """
    paths = list(paths) if paths is not None else [BUILTIN_PATH, office_path()]
    errors: List[str] = []
    cfg = empty()
    keep = "the previous version keeps running" if previous is not None else "the built-in version is used"
    for path in paths:
        data, why = _read(path)
        merged = None
        if data is not None:
            merged = dict(cfg)
            merged.update({k: v for k, v in data.items() if k in SECTIONS})
            try:
                check(merged)
            except ConfigError as e:
                why, merged = str(e), None
        if merged is not None:
            cfg = merged
        elif why:
            errors.append(f"{path.name}: refused: {why} - {keep}")
            if previous is not None:
                cfg = previous
    for e in errors:
        print(f"[SDIS Config] {e}")
    return cfg, errors


_current: Optional[Dict[str, Any]] = None
_errors: List[str] = []


def current() -> Dict[str, Any]:
    """The config in force (loaded on first use, never at import time)."""
    global _current, _errors
    if _current is None:
        _current, _errors = load()
    return _current


def reload(paths: Optional[Sequence[Path]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Re-reads the files; a refused file keeps the previous good config. Returns (config, errors)."""
    global _current, _errors
    _current, _errors = load(paths, previous=_current)
    return _current, list(_errors)


def last_errors() -> List[str]:
    return list(_errors)


def portal_exceptions() -> Dict[str, Any]:
    pe = current().get("portal_exceptions") or {}
    return {"extra_domains": dict(pe.get("extra_domains") or {}), "never_register": list(pe.get("never_register") or [])}


def class_exceptions() -> Dict[str, str]:
    return dict(current().get("class_exceptions") or {})


# ── The office row -> the file on every PC; export / import for the admin ────────

def dumps(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1)


def write_file(doc: Optional[Dict[str, Any]], path: Optional[Path] = None) -> Optional[Path]:
    """Writes the document atomically. No document (an empty table) or the same bytes -> the file
    is not touched. Returns the path written, or None."""
    if doc is None:
        return None
    path = Path(path) if path is not None else office_path()
    text = dumps(doc)
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return None
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def refresh(db: Any, path: Optional[Path] = None) -> Optional[Path]:
    """Start-up and after sync applied remote rows: the sdis_config row -> the file; the config in
    force is reloaded when the file changed."""
    row = db.get_sdis_containers()
    written = write_file(row["doc"] if row else None, path)
    if written is not None and path is None:
        reload()
    return written


def known_names(db: Any) -> Dict[str, Any]:
    """check()'s fields= / portals= from the office database and the registered portals."""
    from core.sdis.portals import portal_names
    return {"fields": [r["name"] for r in db.list_sdis_mcl()], "portals": portal_names()}


def export(db: Any, path: Path) -> Path:
    """The office document (or the one in force, when the table is empty) to a file the admin
    can edit by hand."""
    row = db.get_sdis_containers()
    path = Path(path)
    path.write_text(dumps(row["doc"] if row else current()), encoding="utf-8")
    return path


def import_file(db: Any, path: Path, updated_by: str = "", portals: Optional[Iterable[str]] = None) -> int:
    """Reads a hand-edited file and puts it (the same checks as put); a failing file raises
    ConfigError and the office document stays as it was. Returns the new version."""
    data, why = _read(Path(path))
    if data is None:
        raise ConfigError(why or "file not found")
    return db.put_sdis_containers(data, updated_by=updated_by, portals=portals)
