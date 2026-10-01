"""
core/sgt_i/uia_nodes.py - read the page as a map of nodes, in one cached UIA call
=================================================================================
Blueprint 14.4 step 1 (and 14.2 "the one shared part"). Today's reader,
core/vsdc/vsdc_uia_text.read_page_text, asks the browser element by element: a FindAll, then
one cross-process call per Name, per ControlType, per value. This module asks ONCE: a UIA
CacheRequest with TreeScope_Subtree is attached to the FindAll that finds the Document
elements (FindAllBuildCache), so every property below - and the parent/child structure - comes
back in that single call and is read afterwards from the in-process cache.

Strictly a read: property values only, never a pattern method (no SetValue, Select, Toggle...).
Runs on vsdc_uia_text's guarded COM worker, so a wedged read is abandoned the same way.

Not wired into the Core. lines_from_nodes() rebuilds today's lines from the nodes so the 14.2
gate ("identical lines on the whole corpus before the Core switches") can be checked;
tools/sgt_i_uia_node_bench.py measures both readers side by side (W2-1).
"""

from typing import Any, Dict, List, Optional

# UIA property ids (UIAutomationClient.h). Literals, because older comtypes-generated wrappers
# lack the Windows 10 ones (landmark, heading).
PID_BOUNDING_RECT = 30001
PID_CONTROL_TYPE = 30003
PID_NAME = 30005
PID_AUTOMATION_ID = 30011
PID_IS_OFFSCREEN = 30022
PID_IS_PASSWORD = 30019
PID_VALUE = 30045
PID_GRID_ROW = 30064
PID_GRID_COLUMN = 30065
PID_SELECTION_IS_SELECTED = 30079
PID_TOGGLE_STATE = 30086
PID_ARIA_ROLE = 30101
PID_LANDMARK_TYPE = 30157
PID_HEADING_LEVEL = 30173

_CACHED_PROPERTIES = (
    PID_BOUNDING_RECT, PID_CONTROL_TYPE, PID_NAME, PID_AUTOMATION_ID, PID_IS_OFFSCREEN, PID_IS_PASSWORD,
    PID_VALUE, PID_GRID_ROW, PID_GRID_COLUMN, PID_SELECTION_IS_SELECTED, PID_TOGGLE_STATE, PID_ARIA_ROLE,
    PID_LANDMARK_TYPE, PID_HEADING_LEVEL,
)

# Control types (same literals as vsdc_uia_text).
CT_CHECKBOX = 50002
CT_COMBOBOX = 50003
CT_EDIT = 50004
CT_RADIOBUTTON = 50013
CT_SPINNER = 50016
CT_TABITEM = 50019
CT_DATAITEM = 50029
CT_DOCUMENT = 50030
_VALUE_BEARING = frozenset({CT_COMBOBOX, CT_EDIT, CT_SPINNER})
_CONTAINERS = frozenset({50025, 50026, 50033})      # Custom, Group, Pane: what a host <div> becomes
OWN_ID_PREFIX = "sera-"                             # element ids Sera's browser extension gives its UI
_CHOICE = frozenset({CT_RADIOBUTTON, CT_CHECKBOX})
SELECTED_PREFIX = "Selected: "      # same as vsdc_uia_text.SELECTED_PREFIX

# UIA HeadingLevel_None = 80050, HeadingLevel1..9 = 80051..80059.
_HEADING_NONE = 80050

# TreeScope values (UIAutomationClient.h).
_SCOPE_ELEMENT = 1
_SCOPE_CHILDREN = 2
_SCOPE_DESCENDANTS = 4
_SCOPE_SUBTREE = _SCOPE_ELEMENT | _SCOPE_CHILDREN | _SCOPE_DESCENDANTS

# AutomationElementMode_None: cached values only, no live reference kept per element.
_ELEMENT_MODE_NONE = 0


def _cached(el, pid) -> Any:
    try:
        return el.GetCachedPropertyValue(pid)
    except Exception:
        return None


def _node(el, parent: int, depth: int) -> Dict[str, Any]:
    """One cached element as a plain dict. Only the cheap, always-useful properties are unpacked
    for every node; value / choice / grid only for the control types that can carry them."""
    ctype = _cached(el, PID_CONTROL_TYPE) or 0
    name = (_cached(el, PID_NAME) or "").strip()
    rect = _cached(el, PID_BOUNDING_RECT)
    node: Dict[str, Any] = {
        "parent": parent, "depth": depth, "ctype": ctype, "name": name,
        "rect": tuple(int(v) for v in rect) if rect else None,   # left, top, width, height
    }
    heading = _cached(el, PID_HEADING_LEVEL)
    if heading and heading > _HEADING_NONE:
        node["heading"] = heading - _HEADING_NONE
    landmark = _cached(el, PID_LANDMARK_TYPE)
    if landmark:
        node["landmark"] = landmark
    role = _cached(el, PID_ARIA_ROLE)
    if role:
        node["role"] = role
    # The element's HTML id (Chromium's AutomationId), kept when the markup sets one: the corpus
    # carries it so tools/sgt_i_id_census.py can measure whether portal ids are stable node keys.
    aid = _cached(el, PID_AUTOMATION_ID)
    aid = aid.strip() if isinstance(aid, str) else ""
    if aid:
        node["aid"] = aid
    if ctype in _VALUE_BEARING:
        # A password field's content must never reach a node, the corpus, or a log - the label
        # (name, above) is kept, the value simply isn't cached.
        if not _cached(el, PID_IS_PASSWORD):
            node["value"] = (_cached(el, PID_VALUE) or "").strip()
    elif ctype in (CT_RADIOBUTTON, CT_TABITEM):     # a tab item's selection = the current step
        node["selected"] = bool(_cached(el, PID_SELECTION_IS_SELECTED))
    elif ctype == CT_CHECKBOX:
        node["selected"] = _cached(el, PID_TOGGLE_STATE) == 1
    elif ctype == CT_DATAITEM or role in ("cell", "gridcell", "columnheader", "rowheader"):
        row, col = _cached(el, PID_GRID_ROW), _cached(el, PID_GRID_COLUMN)
        if isinstance(row, int) and isinstance(col, int):
            node["grid"] = (row, col)
    elif ctype in _CONTAINERS:
        # Sera's own extension panel injected into the portal page (sera-manual-assist-host,
        # sera-mecp-host, ...): not the portal - page_map keeps it out of content and structure.
        if aid.startswith(OWN_ID_PREFIX):
            node["own"] = True
    return node


def _walk(el, parent: int, depth: int, out: List[Dict[str, Any]]) -> None:
    """Pre-order over the cached subtree - the same document order FindAll(Descendants) gives."""
    try:
        children = el.GetCachedChildren()
        count = children.Length if children else 0
    except Exception:
        return
    for i in range(count):
        child = children.GetElement(i)
        out.append(_node(child, parent, depth))
        _walk(child, len(out) - 1, depth + 1, out)


def build_cache_request(uia, raw_view: bool = False):
    """The CacheRequest: every property in _CACHED_PROPERTIES, over the whole subtree. The
    default tree filter is UIA's control view; raw_view=True caches every raw element."""
    request = uia.CreateCacheRequest()
    for pid in _CACHED_PROPERTIES:
        try:
            request.AddProperty(pid)
        except Exception:
            pass     # an older Windows without this property id - the node just lacks it
    request.TreeScope = _SCOPE_SUBTREE
    request.AutomationElementMode = _ELEMENT_MODE_NONE
    if raw_view:
        request.TreeFilter = uia.RawViewCondition
    return request


def read_page_nodes(hwnd: int, timeout_sec: float = 3.0, raw_view: bool = False) -> Dict[str, Any]:
    """
    Reads every node of the page content hosted in hwnd (not browser chrome) in one cached UIA
    call. Returns {"docs": [[node, ...], ...]} - one flat pre-order list per Document element,
    each node's "parent" an index into its own list (-1 = the document). Empty on any failure,
    like read_page_text.
    """
    from core.vsdc import vsdc_uia_text as uia_text

    empty: Dict[str, Any] = {"docs": []}
    if not hwnd or not uia_text.user32.IsWindow(hwnd):
        return empty

    def _do_read():
        uia, uia_client = uia_text._get_uia_on_worker()
        if not uia:
            return empty
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return empty
        condition = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, CT_DOCUMENT)
        found = root.FindAllBuildCache(_SCOPE_DESCENDANTS, condition, build_cache_request(uia, raw_view))
        docs: List[List[Dict[str, Any]]] = []
        for d in range(found.Length if found else 0):
            nodes: List[Dict[str, Any]] = []
            _walk(found.GetElement(d), -1, 0, nodes)
            docs.append(nodes)
        return {"docs": docs}

    result = uia_text._run_with_timeout(_do_read, timeout_sec)
    return result if result is not None else empty


def lines_from_nodes(docs: List[List[Dict[str, Any]]], include_selection: bool = False,
                     max_lines: Optional[int] = None) -> List[str]:
    """Today's read_page_text lines, rebuilt from nodes - the 14.2 identity gate compares these."""
    lines: List[str] = []
    for nodes in docs:
        for n in nodes:
            if max_lines is not None and len(lines) >= max_lines:
                return lines[:max_lines]
            name, ctype = n["name"], n["ctype"]
            if name:
                lines.append(name)
            value = n.get("value", "")
            if value and value != name:
                lines.append(value)
                if include_selection and ctype == CT_COMBOBOX:
                    lines.append(SELECTED_PREFIX + (f"{name} = {value}" if name else value))
            if include_selection and name and ctype in _CHOICE and n.get("selected"):
                lines.append(SELECTED_PREFIX + name)
    return lines