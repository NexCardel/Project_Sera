"""
core/sdis/config.py - sdis_containers.json, the containers and exceptions file (blueprint S.3)
===============================================================================================
Two files are read: the built-in core/sdis/sdis_containers.json (empty), then the office copy at
<Sera data dir>/sdis_containers.json; a top-level section of the office copy replaces the built-in
one. check() refuses the whole file with a reason, and the previous good version keeps running
(the same rule as core/sgt/sgt_specs.py), so a bad edit never takes working scope down.

Checked so far: `version` and `portal_exceptions` (Part T). The other sections are read and passed
through unchecked; W4-6 adds their checks.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CONFIG_FILE_NAME = "sdis_containers.json"
CONFIG_ENV = "SDIS_CONTAINERS_PATH"          # tests and tools point the office copy elsewhere
BUILTIN_PATH = Path(__file__).with_name(CONFIG_FILE_NAME)
SUPPORTED_VERSION = 1
SECTIONS = ("version", "levels", "level_map", "profile", "containers", "others",
            "class_exceptions", "portal_exceptions")
_PORTAL_EXCEPTION_KEYS = ("extra_domains", "never_register")


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


def check(data: Any) -> None:
    """Raises ConfigError naming the first problem."""
    if not isinstance(data, dict):
        raise ConfigError("must be a JSON object")
    ver = data.get("version")
    if isinstance(ver, bool) or not isinstance(ver, int) or not 1 <= ver <= SUPPORTED_VERSION:
        raise ConfigError(f"version must be a whole number from 1 to {SUPPORTED_VERSION}, not {ver!r}")
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
