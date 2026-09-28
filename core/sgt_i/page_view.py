"""
core/sgt_i/page_view.py - the one page map every SGT-I component reads
=======================================================================
Until 2026-09-28 each component stacked the Core's plain lines into its own page map, so nothing
had zones: the portal's menu, header and footer counted as main content ("e-file unavailable" read
as an error page, "My Profile" as a profile page). Now:

* **Structure first.** When the Core already read the page's node tree (it does while pages are
  being recorded, corpus format v2), the map is built from it: landmarks, dialogs, headings and
  table cells are real, and the navigation / footer the markup declares are left out.
* **Learnt furniture next.** The atlas counts which texts the portal shows on many different pages;
  runs of them become navigation too (page_map.mark_furniture). This covers what the markup does
  not label, and plain-line reads (OCR, or recording switched off).
* **Lines otherwise**, exactly as before.

The Core never reads more for this: nodes are only used when they were read anyway. One map per
Observation is built and shared by the components that run on it, in order, on SGT-I's thread.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import page_map as pm

MAX_LINES = 400          # the ledger's bound: a huge table is not worth mapping per read
MAX_NODES = 1500         # above this (a 300-row table) a node map costs ~0.4 s; use the lines

_cache: Dict[str, Any] = {"key": None, "obs": None, "page": None}


def split_inline_labels(lines: Sequence[str]) -> List[str]:
    """"Label: value" on one line -> "Label:" and "value" stacked, so the page map pairs them the
    same way it pairs a label above its value. A line with nothing after the colon is kept."""
    out: List[str] = []
    for ln in lines:
        head, sep, tail = str(ln).partition(":")
        if sep and any(c.isalpha() for c in head) and tail.strip() and not tail.startswith("//"):
            out.extend((head.strip() + ":", tail.strip()))
        else:
            out.append(str(ln))
    return out


def map_from_lines(lines: Sequence[str], furniture: Optional[Callable[[str], bool]] = None,
                   min_run: int = 3) -> pm.PageMap:
    """A page map from plain lines: each line a node stacked in reading order. Lines carry no
    geometry or roles, so there is no header band and no dialog; learnt furniture still applies."""
    boxes = [{"text": t, "x": 20, "y": 24 * i, "width": 8 * max(1, len(t)), "height": 18}
             for i, t in enumerate(lines)]
    return pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0,
                             furniture=furniture, furniture_min_run=min_run)


def _furniture(obs: Any, atlas: Any) -> Tuple[Optional[Callable[[str], bool]], int, Any]:
    if atlas is None or not getattr(obs, "portal", ""):
        return None, 3, None
    portal = atlas.portal(obs.portal)
    return portal.furniture_test(), int(portal.config.get("furniture_min_run", 3)), portal._furniture


def node_count(obs: Any) -> int:
    return sum(len(doc) for doc in (getattr(obs, "nodes", None) or ()))


def page_for(obs: Any, atlas: Any = None, split_labels: bool = False,
             max_lines: int = MAX_LINES) -> Optional[pm.PageMap]:
    """The page map for one Observation, or None when there is nothing to map. A line-only page
    keeps its first `max_lines` lines. `atlas` (atlas.Atlas) supplies learnt furniture;
    `split_labels` stacks "Label: value" lines (plain-line maps only - nodes already separate
    them)."""
    frequent, min_run, version = _furniture(obs, atlas)
    nodes = getattr(obs, "nodes", None) or ()
    use_nodes = bool(nodes) and node_count(obs) <= MAX_NODES
    # Label splitting and the line cap only shape a plain-line map: a node map is shared by all.
    key = (id(obs), id(version)) + (() if use_nodes else (split_labels, max_lines))
    if _cache["key"] == key and _cache["obs"] is obs:
        return _cache["page"]
    page: Optional[pm.PageMap] = None
    if use_nodes:
        try:
            page = pm.build_page_map(pm.nodes_from_uia(nodes), furniture=frequent, furniture_min_run=min_run)
        except Exception:
            page = None                                   # a malformed dump: fall back to the lines
        if page is not None and not any(n.text for n in page.nodes):
            page = None
    if page is None:
        lines = [ln for ln in (obs.lines or ()) if ln][:max_lines]
        if lines:
            page = map_from_lines(split_inline_labels(lines) if split_labels else lines, frequent, min_run)
    _cache.update(key=key, obs=obs, page=page)
    return page
