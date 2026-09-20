"""
core/vsdc/vsdc_scope.py — Which websites VSDC may look at
==========================================================
VSDC and VSDC-X are cleared to observe exactly two sites: the Income Tax e-Filing
portal and the GST portal. Everything else a browser shows — a bank, a mail client, a
video, another client's accounting software — is out of scope and must never be
screenshotted, OCR'd or read through UI Automation.

The decision is made on the PARSED HOSTNAME of the address bar, never on a substring or
a regex over the whole URL: "https://www.google.com/search?q=incometax+home" contains the
word incometax but is not the Income Tax portal, and "incometax.gov.in.evil.example" is
not either.

Local test pages (file://, localhost, drive-letter paths) are refused unless
VSDC_ALLOW_LOCAL_TEST=1 is set, which only the test suite and dev tools do.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Registered domains; the domain itself and any subdomain of it is in scope.
INCOME_TAX_DOMAIN = "incometax.gov.in"
GST_DOMAIN = "gst.gov.in"
IN_SCOPE_DOMAINS = (INCOME_TAX_DOMAIN, GST_DOMAIN)

# Extra domains an admin can add WITHOUT a rebuild, for the day a portal moves to a new address.
# An optional JSON file, next to the program (frozen build) or the repo root (development):
#     {"income_tax": ["newportal.example.gov.in"], "gst": []}
# Each entry names a registered domain (subdomains included) and can only ever ADD to the
# portal it is listed under. The file is read once, when the program starts.
SCOPE_CONFIG_NAME = "vsdc_scope.json"
SCOPE_CONFIG_ENV = "VSDC_SCOPE_CONFIG"
_CONFIG_KEYS = {"income_tax": "Income Tax", "gst": "GST Portal"}
# Never accepted as an entry, whatever the file says: a bare registry suffix would put every
# site under it in scope.
_FORBIDDEN_ENTRIES = frozenset({"in", "gov.in", "nic.in", "co.in", "org.in", "net.in", "edu.in", "ac.in", "res.in"})

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.IGNORECASE)
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$")
_DRIVE_PATH_RE = re.compile(r"^[a-zA-Z]:[/\\]")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def local_test_pages_allowed() -> bool:
    return os.environ.get("VSDC_ALLOW_LOCAL_TEST") == "1"


def is_local_reference(url: str) -> bool:
    """file:// pages, drive-letter paths and localhost — the simulation pages used by tests."""
    u = (url or "").strip()
    if not u:
        return False
    scheme = _SCHEME_RE.match(u)
    if scheme and scheme.group(1).lower() == "file":
        return True
    if _DRIVE_PATH_RE.match(u):
        return True
    host = extract_host(u)
    return bool(host and host in _LOCAL_HOSTS)


def extract_host(url: str) -> Optional[str]:
    """
    The lower-cased hostname of an address-bar value, or None when there isn't a valid
    one. Handles browsers that hide the scheme ("eportal.incometax.gov.in/iec/..."),
    ports, user-info ("a@evil.com" tricks) and a trailing dot. Returns None for things
    that are not hostnames at all, such as text the user is still typing into the bar.
    """
    u = (url or "").strip()
    if not u:
        return None
    scheme = _SCHEME_RE.match(u)
    if scheme and u[len(scheme.group(0)):len(scheme.group(0)) + 2] == "//":
        u = u[len(scheme.group(0)) + 2:]
    elif scheme and scheme.group(1).lower() in ("about", "chrome", "edge", "data", "javascript", "view-source", "blob"):
        return None
    authority = re.split(r"[/?#\\]", u, maxsplit=1)[0]
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    authority = authority.strip().lower().rstrip(".")
    if authority.startswith("["):
        return authority if authority in _LOCAL_HOSTS else None
    host = authority.split(":", 1)[0]
    if not host or len(host) > 253 or not _HOSTNAME_RE.match(host):
        return None
    return host


def _in_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _config_path() -> Path:
    override = os.environ.get(SCOPE_CONFIG_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / SCOPE_CONFIG_NAME
    return Path(__file__).resolve().parents[2] / SCOPE_CONFIG_NAME


def load_extra_domains(path: Optional[Path] = None) -> Dict[str, Tuple[str, ...]]:
    """
    The admin-added domains, per portal. Anything that is not a plain registered domain
    (a wildcard, a URL, a bare suffix like gov.in, fewer than three labels) is rejected with a
    console line, so a typo can never widen the scope by accident.
    """
    out: Dict[str, List[str]] = {"Income Tax": [], "GST Portal": []}
    p = path or _config_path()
    try:
        if not p.is_file():
            return {k: () for k in out}
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[VSDC Scope] could not read {p.name}: {e} - using the built-in list only")
        return {k: () for k in out}
    if not isinstance(data, dict):
        print(f"[VSDC Scope] {p.name} must be a JSON object - ignored")
        return {k: () for k in out}
    for key, portal in _CONFIG_KEYS.items():
        for raw in data.get(key) or []:
            dom = str(raw).strip().lower().rstrip(".")
            if (not _HOSTNAME_RE.match(dom) or dom.count(".") < 2 or dom in _FORBIDDEN_ENTRIES
                    or len(dom) > 253):
                print(f"[VSDC Scope] rejected {raw!r} in {p.name}: not a plain registered domain (e.g. newportal.example.gov.in)")
                continue
            out[portal].append(dom)
    if any(out.values()):
        print(f"[VSDC Scope] extra domains from {p.name}: {out}")
    return {k: tuple(v) for k, v in out.items()}


_extra_domains: Optional[Dict[str, Tuple[str, ...]]] = None


def extra_domains() -> Dict[str, Tuple[str, ...]]:
    global _extra_domains
    if _extra_domains is None:
        _extra_domains = load_extra_domains()
    return _extra_domains


def reload_extra_domains() -> Dict[str, Tuple[str, ...]]:
    """Forget the cached config (tests, or after an admin edits the file)."""
    global _extra_domains
    _extra_domains = None
    return extra_domains()


def portal_for_url(url: str) -> Optional[str]:
    """'Income Tax', 'GST Portal', or None when the URL is not one of the two portals."""
    host = extract_host(url)
    if not host:
        return None
    if _in_domain(host, INCOME_TAX_DOMAIN):
        return "Income Tax"
    if _in_domain(host, GST_DOMAIN):
        return "GST Portal"
    for portal, domains in extra_domains().items():
        if any(_in_domain(host, d) for d in domains):
            return portal
    return None


def sanitize_page_url(url: str, max_len: int = 300) -> str:
    """
    The address-bar URL in a form that is safe to store with a capture: no query string (portal
    links can carry session or reference tokens after a "?", including inside a "#/route?x=" hash),
    no user-info, no trailing junk. What is left - host, path and the SPA hash route - is enough to
    tell which page a capture came from. Returns "" for anything that is not a URL.
    """
    u = (url or "").strip()
    if not u or len(u) > 4096 or any(ord(c) < 32 for c in u):
        return ""
    u = u.split("?", 1)[0]
    m = re.match(r"^([a-z][a-z0-9+.\-]*://)([^/#]*)(.*)$", u, re.IGNORECASE)
    if m:
        scheme, authority, rest = m.groups()
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        u = scheme + authority + rest
    elif "@" in re.split(r"[/#]", u, maxsplit=1)[0]:      # scheme-less "user@host/path"
        u = u.split("@", 1)[1]
    return u[:max_len]


def is_in_scope_url(url: str) -> bool:
    """True only for the two portals (or, with VSDC_ALLOW_LOCAL_TEST=1, a local test page)."""
    if portal_for_url(url):
        return True
    return local_test_pages_allowed() and is_local_reference(url)


# ── "Is this an unlisted portal?" tripwire ───────────────────────────────────────────
# If a portal ever moves to a new domain, the scope gate above silently refuses it and every
# capture stops. The tripwire makes that visible - it never widens the scope. It fires only for
# a government-registry host that is NOT on the list, only when the top of the page carries the
# portal's own logo, and it only ever raises a HUD prompt; nothing is captured or stored.
_GOV_SUFFIXES = (".gov.in", ".nic.in")

# Logo names as UI Automation reports them (the image's alt text), e.g. "E Filing logo" and
# "Goods and Services Tax Home". Matched against short lines near the top of the page only.
_LOGO_CUES = (
    ("Income Tax", re.compile(r"^(?:\[image\]\s*)?(?:\([^)]*\)\s*)?(?:e[\s\-]?filing|income\s+tax(?:\s+department)?)\s+logo$", re.IGNORECASE)),
    ("GST Portal", re.compile(r"^(?:\[image\]\s*)?(?:\([^)]*\)\s*)?(?:goods\s+and\s+services\s+tax(?:\s+home)?|gst\s+logo)$", re.IGNORECASE)),
)
TRIPWIRE_TOP_LINES = 30


def is_government_registry_host(url: str) -> bool:
    """A host under gov.in / nic.in that is not one of the listed portals."""
    host = extract_host(url)
    return bool(host and host.endswith(_GOV_SUFFIXES) and not portal_for_url(url))


def portal_logo_cue(lines: Sequence[str]) -> Optional[str]:
    """
    'Income Tax' / 'GST Portal' when one of the first few lines is that portal's logo, else
    None. Only the head of the page is looked at, and only lines short enough to be an image
    name - never body text.
    """
    for ln in list(lines)[:TRIPWIRE_TOP_LINES]:
        ln = (ln or "").strip()
        if not ln or len(ln) > 80:
            continue
        for portal, pat in _LOGO_CUES:
            if pat.match(ln):
                return portal
    return None
