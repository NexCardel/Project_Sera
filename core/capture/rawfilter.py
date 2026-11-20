"""
core/capture/rawfilter.py - the control view, rebuilt from a raw-view read (UCW WP5)
===================================================================================
UI Automation defines the control view as the raw view filtered to elements whose IsControlElement is
true. If that holds for the portals' pages in the browsers the office uses, one raw read per page could
serve both SGT (control view) and the SDIS recorder (raw view). tools/ucw_probe.py checks it page by page
on an office PC (WP0): same lines from control_view_of(raw) as from a control-view read.

Until the probe says so on the office PCs, the worker reads twice, back to back on the window's lane
(WP5's second path): this module is used by the probe and its tests only.

A raw node carries "ctl" (read_cached_subtree(..., control_flag=True)). Nodes that are not control
elements are dropped; each kept node's parent becomes its nearest kept ancestor (-1 = the document) and
its depth is recounted, so the result is shaped like a control-view read.
"""

from typing import Any, Dict, List


def control_view_of(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    new_index: Dict[int, int] = {}          # raw index -> index in out (kept nodes only)
    kept_parent: Dict[int, int] = {}        # raw index -> nearest kept ancestor's index in out (-1 = document)
    for i, n in enumerate(nodes):
        parent = n.get("parent", -1)
        above = -1 if parent < 0 else (new_index[parent] if parent in new_index else kept_parent.get(parent, -1))
        if n.get("ctl", True):
            node = {k: v for k, v in n.items() if k != "ctl"}
            node["parent"] = above
            node["depth"] = 0 if above < 0 else out[above]["depth"] + 1
            new_index[i] = len(out)
            out.append(node)
        else:
            kept_parent[i] = above
    if nodes and out and nodes[0].get("gecko") and not out[0].get("gecko"):
        out[0]["gecko"] = True              # lines_from_nodes reads Gecko's flag off the first node
    return out
