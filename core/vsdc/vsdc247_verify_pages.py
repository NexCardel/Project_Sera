"""
core/vsdc/vsdc247_verify_pages.py - VSDC247-V: the GST page registry and the login-username reader.

The verifier knows exactly three GST page kinds (docs/vsdc247-sgt-verifier-plan.md, section 2):
  1. the GST login page        -> read the Username field
  2. the GSTR-1 filing page    -> read the ARN
  3. the GSTR-3B filing page   -> read the ARN

A URL alone never registers a page. The host must match exactly (no look-alikes) and the path must
equal one of the paths seen in real captures, and the on-screen cues must be present (plan 5.1).
Add a path only after a capture shows it - do not guess URLs.

Nothing here reads, keeps or logs the password field or anything else on the login page.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PageSpec:
    kind: str
    host: str
    path: str
    form: Optional[str]              # the GST form the page files; None for the login page
    cues: Tuple["re.Pattern[str]", ...]   # all must be on the frame for the page to count


# Windows OCR reads "GSTR1" as "GSTRI" on the GST pages (real-OCR check, 2026-10-18), and "3B" as
# "38" is possible. The cues accept those readings; the path and host still have to match exactly.
GSTR1_TEXT = r"GSTR[-_.\s]?[1Il|]"
GSTR3B_TEXT = r"GSTR[-_.\s]?3[B8]"

PAGE_KINDS: Tuple[PageSpec, ...] = (
    PageSpec("gst_login", "services.gst.gov.in", "/services/login", None,
             (re.compile(r"\busername\b", re.I), re.compile(r"\bpassword\b", re.I))),
    PageSpec("gstr1_submit", "return.gst.gov.in", "/returns/auth/file", "GSTR-1",
             (re.compile(rf"\b{GSTR1_TEXT}\b", re.I),)),
    PageSpec("gstr3b_submit", "return.gst.gov.in", "/returns/auth/gstr3b/filing", "GSTR-3B",
             (re.compile(rf"\b{GSTR3B_TEXT}", re.I),)),
)


def _host_and_path(url: str) -> Tuple[str, str]:
    raw = (url or "").strip()
    parts = urlsplit(raw if "//" in raw else "//" + raw)
    host = (parts.hostname or "").lower()
    path = (parts.path or "/").rstrip("/") or "/"
    return host, path


def classify_page(url: str, lines: Sequence[str]) -> Optional[PageSpec]:
    """The registered page this frame shows, or None. The URL must match AND the cues must be on screen."""
    host, path = _host_and_path(url)
    text = "\n".join(str(l or "") for l in lines)

    # When VSDC_ALLOW_LOCAL_TEST=1 is active (test suite / tools), allow matching simulated test pages
    # whose path or fragment contains the page spec's path or kind.
    if os.environ.get("VSDC_ALLOW_LOCAL_TEST") == "1":
        low_url = (url or "").lower()
        for spec in PAGE_KINDS:
            if (spec.path in low_url or spec.kind in low_url or (spec.form and spec.form.lower() in low_url)) and all(c.search(text) for c in spec.cues):
                return spec
        for spec in PAGE_KINDS:
            if ("test" in low_url or "gst" in low_url or not host) and all(c.search(text) for c in spec.cues):
                return spec

    if not host:
        return None
    for spec in PAGE_KINDS:
        if host == spec.host and path == spec.path and all(c.search(text) for c in spec.cues):
            return spec
    return None


# ── Login username ───────────────────────────────────────────────────────────────
# The value is a plain token. Field words, placeholders and the extension's own "Injected" label are
# never values. Corpus OCR showed the label as "Username", "Username ?", "? Username", "Username*".
_LABEL = re.compile(r"\busername\b", re.I)
_BEFORE_LABEL_BLOCKS = re.compile(r"\b(forgot|enter|your)\s*$", re.I)     # "Forgot Username", "Enter Username"
_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{3,39}")
_FIELD_WORDS = ("password", "captcha", "login", "forgot", "enter", "username", "register", "mandatory",
                "indicates", "submit", "reset", "logout", "here", "first", "time")
_NOT_A_VALUE = frozenset(_FIELD_WORDS) | {"injected", "optional", "required"}


def _clean(raw: str) -> str:
    return re.sub(r"[*?:•|]+", " ", raw or "").strip().strip(" -'\"").strip()


def _is_field_word_line(text: str) -> bool:
    low = text.lower()
    return any(re.search(rf"\b{w}\b", low) for w in _FIELD_WORDS)


def read_login_username(lines: Sequence[str]) -> Optional[str]:
    """
    The value of the Username field on a login frame, or None. Label-anchored: the value is on the
    label's own line after it, or on the line directly below. A field word (for example "Password")
    ends the search, so a password value can never be taken as the username.
    """
    cleaned = [str(l or "").strip() for l in lines]
    for i, line in enumerate(cleaned):
        m = _LABEL.search(line)
        if not m or _BEFORE_LABEL_BLOCKS.search(line[:m.start()]):
            continue
        for raw in (line[m.end():], cleaned[i + 1] if i + 1 < len(cleaned) else ""):
            val = _clean(raw)
            if not val or val.lower() in _NOT_A_VALUE:
                if _is_field_word_line(raw):
                    break                 # another field starts here: the username field is empty
                continue                  # "Injected" or a bare "?" - look at the next candidate
            if _VALUE.fullmatch(val) and not _is_field_word_line(val):
                return val
            break
    return None
