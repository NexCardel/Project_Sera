"""
core/sca/page_nodes.py - one page read, shaped for the UID picker and the UID-source watcher
=============================================================================================
The picker (capture) and the watcher (run time) MUST read a page the same way, or the node keys they
build would not match. Both go through UiaReader.read_docs.

It is the read tools/pre_dev/class_diff/key_probe.py makes (raw view, with the HTML id, class names and
aria properties that core/sdis/keys.flatten keys on and that selected-row detection needs), moved into
core because the installer does not ship tools/. It is built from core/sgt_i/uia_nodes's cache request,
so the properties and the password rule are the same: a password box's VALUE is never read.

The reader owns its UIA object (like SdisRecorder): never vsdc_uia_text's shared worker, so SGT's reads
can neither wait for it nor see it as a hung read. Create and use one on ONE thread.
"""

from typing import Any, Dict, List, Optional, Tuple

from core.sca.uia_types import CONTROL_TYPE_NAMES

PID_CLASS_NAME = 30012
PID_IS_ENABLED = 30010
PID_ARIA_PROPERTIES = 30102
_EXTRA = (PID_CLASS_NAME, PID_IS_ENABLED, PID_ARIA_PROPERTIES)
_WITH_VALUE = frozenset({50003, 50004, 50016, 50005})       # combo, edit, spinner, hyperlink
BROWSER_OWN_CLASS = "NativeViewHost"                        # Edge's own window panes, not the page


def _s(v: Any) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _aria(raw: str) -> str:
    """AriaProperties minus every "x=false": what is left was set by the page (selected=true...)."""
    keep = []
    for pair in raw.split(";"):
        k, _, v = pair.partition("=")
        k, v = k.strip(), v.strip()
        if k and v != "false":
            keep.append(f"{k}={v}")
    return ";".join(keep)


def _node(el, parent: int, depth: int, uia_nodes) -> Dict[str, Any]:
    c = uia_nodes._cached
    ctype = c(el, uia_nodes.PID_CONTROL_TYPE) or 0
    rect = c(el, uia_nodes.PID_BOUNDING_RECT)
    n: Dict[str, Any] = {
        "parent": parent, "depth": depth, "ctype": ctype,
        "type_name": CONTROL_TYPE_NAMES.get(ctype, str(ctype)),
        "name": _s(c(el, uia_nodes.PID_NAME)),
        "rect": tuple(int(v) for v in rect) if rect else None,
        "id": _s(c(el, uia_nodes.PID_AUTOMATION_ID)),
        "cls": _s(c(el, PID_CLASS_NAME)),
        "aria": _aria(_s(c(el, PID_ARIA_PROPERTIES))),
        "value": "",
    }
    if c(el, uia_nodes.PID_SELECTION_IS_SELECTED) is True:
        n["selected"] = True
    if ctype in _WITH_VALUE and not c(el, uia_nodes.PID_IS_PASSWORD):
        n["value"] = _s(c(el, uia_nodes.PID_VALUE))
    return n


def _walk(el, parent: int, depth: int, out: List[Dict[str, Any]], uia_nodes) -> None:
    try:
        children = el.GetCachedChildren()
        count = children.Length if children else 0
    except Exception:
        return
    for i in range(count):
        child = children.GetElement(i)
        if uia_nodes._cached(child, PID_CLASS_NAME) == BROWSER_OWN_CLASS:
            continue
        out.append(_node(child, parent, depth, uia_nodes))
        _walk(child, len(out) - 1, depth + 1, out, uia_nodes)


class UiaReader:
    def __init__(self) -> None:
        self._uia: Optional[Tuple[Any, Any]] = None
        self._bar_cache: Dict[int, Any] = {}

    def _ensure(self) -> Tuple[Any, Any]:
        if self._uia is None:
            import comtypes
            import comtypes.client
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            client = comtypes.client.GetModule("UIAutomationCore.dll")
            try:
                uia = comtypes.client.CreateObject(client.CUIAutomation8, interface=client.IUIAutomation)
            except Exception:
                uia = comtypes.client.CreateObject(client.CUIAutomation, interface=client.IUIAutomation)
            self._uia = (uia, client)
        return self._uia

    def read_docs(self, hwnd: int) -> List[List[Dict[str, Any]]]:
        """The on-screen Documents of a browser window, each a flat pre-order node list ("parent" indexes
        the same list). [] on any failure: a read must never raise into its caller."""
        try:
            from core.sgt_i import uia_nodes
            uia, client = self._ensure()
            root = uia.ElementFromHandle(hwnd)
            if not root:
                return []
            request = uia_nodes.build_cache_request(uia, True)
            for pid in _EXTRA + (uia_nodes.PID_SELECTION_IS_SELECTED,):
                try:
                    request.AddProperty(pid)
                except Exception:
                    pass
            cond = uia.CreatePropertyCondition(client.UIA_ControlTypePropertyId, uia_nodes.CT_DOCUMENT)
            found = root.FindAllBuildCache(4, cond, request)          # TreeScope_Descendants
            docs = []
            for d in range(found.Length if found else 0):
                el = found.GetElement(d)
                if uia_nodes._cached(el, uia_nodes.PID_IS_OFFSCREEN):  # never a background tab's page
                    continue
                nodes: List[Dict[str, Any]] = []
                _walk(el, -1, 0, nodes, uia_nodes)
                if nodes:
                    docs.append(nodes)
            return docs
        except Exception:
            return []

    def read_url(self, hwnd: int) -> str:
        """The browser's address bar text ('' when not found); a property read only."""
        try:
            from core.vsdc import vsdc_uia_text as uia_text
            uia, client = self._ensure()
            return uia_text.address_bar_here(uia, client, hwnd, self._bar_cache) or ""
        except Exception:
            return ""
