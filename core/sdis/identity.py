"""
core/sdis/identity.py - who the client of one map is
====================================================
Moved unchanged from the pre-dev compare.py, which imports it back.
"""

from datetime import date

CLIENT_FIELDS = ("pan", "gstin")
_registry = None


def client_ids(m: "link_map.LinkMap") -> set:
    """Who the client of one map is: the PAN / GSTIN that SGT-C's own specs (sgt_fields.json,
    checksums included) find in the text SGT sees there, as {"gstin:...", "pan:..."}. Empty when
    the page never shows them, or the link is no portal."""
    from core.sgt.sgt_resolver import resolve_page          # importing core.vsdc loads Qt: only when needed
    from core.sgt.sgt_specs import load_registry
    from core.vsdc.vsdc_scope import portal_for_url
    global _registry
    url = "https://" + m.page
    portal = portal_for_url(url)
    if not portal:
        return set()
    if _registry is None:
        _registry = load_registry()
    lines = [e["text"] for e in m.to_flat() if e["text"] and e["node"].get("sgt")]
    res = resolve_page(_registry, lines, portal, url, date.today())
    return {f"{k}:{h.value}" for k, h in res.profile.items() if k in CLIENT_FIELDS}


def masked(ids: set) -> str:
    """Client ids for the console: first and last 2 characters only."""
    return ", ".join(sorted(f"{i.split(':', 1)[0]} {v[:2]}..{v[-2:]}" for i in ids for v in [i.split(":", 1)[1]])) or "unknown"
