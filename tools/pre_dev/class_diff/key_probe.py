"""
tools/pre_dev/class_diff/key_probe.py - PRE-DEV TEST copy of tools/sgt_i_key_probe.py
===================================================================================
Pre-development test for class-path comparison (compare.py beside this file). Same probe as
tools/sgt_i_key_probe.py, plus: it reads the page link from the browser's address bar and writes
a JSON of the raw-view nodes next to the .txt, which compare.py reads.

Standalone diagnostic, like tools/vsdc_uia_probe.py. NOT wired into the app. Strictly a read:
never types, clicks or submits anything.

An HTML element can be told apart by its attributes - id, class, role, aria-*, title,
placeholder. The browser never hands SGT the HTML itself; it hands over the UI Automation tree,
and Chromium copies SOME attributes into it. This probe reads the page open in the browser in
UIA's CONTROL view (what SGT records) and its RAW view (every element Chromium exposes), asks for
every attribute-carrying UIA property, and writes one .txt report: a short summary, then ONE
compact tree of the page - only elements with text or a key, each with its keys beside it.

What reaches UIA (checked live in Edge, 2026-09-30):

    HTML                                   UIA property          shown as
    id="pan"                               AutomationId          #pan
    class="form-control ng-valid"          ClassName             .form-control ng-valid
    role="tab" (or the tag's own role)     AriaRole              role=tab   (hidden when it only
                                                                            repeats the element type)
    aria-required / aria-live / level...   AriaProperties        aria: required=true;...
                                           (Chromium's defaults - every x=false etc. - are dropped)
    title / placeholder                    HelpText              help: ...
    aria-description / aria-describedby    FullDescription       desc: ...
    required / disabled                    IsRequiredForForm / IsEnabled
    <a href>                               Value                 => the link's address

What never reaches UIA, so no UIA reader can use it: the `name` attribute, `data-*`,
framework attributes (formcontrolname, ng-*, v-*), inline style, and the HTML tag itself.

A "~" in front of a tree line: SGT's control view does not have that element (matched by type,
name, box, id, class). An id or class on a plain <div>/<span> sits on such a container; the text
inside it still reaches SGT, the keys do not.

Usage:
    python tools/pre_dev/class_diff/key_probe.py                 # 30 s capture; switch to the browser within 8 s
    python tools/pre_dev/class_diff/key_probe.py --seconds 60    # a longer capture
    python tools/pre_dev/class_diff/key_probe.py --once          # one read only (the old behaviour)
    python tools/pre_dev/class_diff/key_probe.py --delay 15 --title "GST"
    python tools/pre_dev/class_diff/key_probe.py --timing        # cost of SGT reading the raw view

TIMING (--timing): reads the page with SGT's OWN reader (core/sgt_i/uia_nodes.read_page_nodes -
the same properties production caches, not this probe's extra ones) --rounds times in each view,
control and raw alternating so neither gets a warmer cache, and prints the milliseconds (median,
90th percentile, worst), elements and texts per view and the raw/control ratio. Writes nothing.
Run it on a light page and on the heaviest page you use (a long list): SGT reads every second.

CAPTURE (default): like SGT, the probe keeps reading the page - every --interval (1 s) for
--seconds (30 s) - while you use it normally. A read is kept only when the page's keys or texts
changed, and every kept read is merged by key into one map per page link (link_map.py), so the
capture ends as ONE page holding every node that appeared: form, popup, success message. The
console shows each change as it happens. Every kept read also goes to snapshot_diff.AnchorPage,
which builds the link's COMP PAGE (the anchor page plus every node attached later; see
snapshot_diff.py) and writes comp_<start>_<n>__<page>.json/.csv/.txt for compare.py --comp. One capture = one session (one client): compare.py
compares the latest capture of a link with the previous one. Files: capture_<start>_<n>.txt/.json
(n = the n-th page link the capture visited).

--once report: tools/pre_dev/class_diff/output/key_probe_YYYYMMDD_HHMMSS.txt + .json (git-ignored - it holds what the
page shows, client data included; keep it on this PC). A password field's content is never read.
Checked in Edge (Chromium); Firefox exposes UIA differently.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.sgt_i import uia_nodes                                                        # noqa: E402
from core.sgt_i.uia_nodes import _cached                                                # noqa: E402
from tools.vsdc_uia_probe import BROWSER_EXE_NAMES, CONTROL_TYPE_NAMES, get_foreground_info  # noqa: E402
from tools.vsdc_x_html_sample_test import find_hwnd_by_title_substring                  # noqa: E402

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from keys import breadcrumb, flatten as key_flatten, page_link, page_slug                                               # noqa: E402

OUT_DIR = os.path.join(HERE, "output")
READ_TIMEOUT = 20.0
WARM_READS = 2              # the first reads wake Chromium's accessibility tree up
CARRIES_MAX = 70

# UIA property ids (UIAutomationClient.h) beyond what uia_nodes caches.
PID_CLASS_NAME = 30012
PID_HELP_TEXT = 30013
PID_IS_ENABLED = 30010
PID_IS_REQUIRED = 30025
PID_ARIA_PROPERTIES = 30102
PID_FULL_DESCRIPTION = 30159
_EXTRA = (PID_CLASS_NAME, PID_HELP_TEXT, PID_IS_ENABLED, PID_IS_REQUIRED, PID_ARIA_PROPERTIES,
          PID_FULL_DESCRIPTION)
_WITH_VALUE = frozenset({50003, 50004, 50016, 50005})       # combo, edit, spinner, hyperlink

# The keys, in report order: (node field, how it is shown).
KEYS = (("id", "#"), ("cls", "."), ("role", "role="), ("aria", "aria: "), ("help", "help: "),
        ("desc", "desc: "))


# Chromium gives every element an implied role; these only repeat the element type (Text,
# Group, Edit, Hyperlink...) and are hidden. A role that adds something (tab, status, alert,
# dialog, tablist...) is shown.
_TYPE_ROLES = frozenset({"description", "group", "generic", "paragraph", "text", "statictext",
                         "textbox", "link", "heading", "region", "document", "button", "checkbox",
                         "radio", "combobox", "img", "image", "list", "listitem", "cell", "row",
                         "table", "columnheader", "rowheader", "gridcell", "section"})
_INPUT_TYPES = frozenset({50003, 50004, 50016})              # where readonly=true means something
BROWSER_OWN_CLASS = "NativeViewHost"                          # Edge's own window panes, not the page


def _str(v: Any) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _aria(raw: str, ctype: int) -> str:
    """AriaProperties minus Chromium's defaults: every "x=false", readonly=true on anything but an
    input, and relevant=additions [text] (the aria-live default). What is left was set by the page or
    means something (required=true, level=2, selected=true, live=polite...)."""
    keep = []
    for pair in raw.split(";"):
        k, _, v = pair.partition("=")
        k, v = k.strip(), v.strip()
        if not k or v == "false" or (k == "relevant" and v in ("additions", "additions text")) \
                or ((k, v) == ("readonly", "true") and ctype not in _INPUT_TYPES):
            continue
        keep.append(f"{k}={v}")
    return ";".join(keep)


def _node(el, parent: int, depth: int) -> Dict[str, Any]:
    ctype = _cached(el, uia_nodes.PID_CONTROL_TYPE) or 0
    rect = _cached(el, uia_nodes.PID_BOUNDING_RECT)
    n: Dict[str, Any] = {
        "parent": parent, "depth": depth, "ctype": ctype,
        "name": _str(_cached(el, uia_nodes.PID_NAME)),
        "rect": tuple(int(v) for v in rect) if rect else None,
        "id": _str(_cached(el, uia_nodes.PID_AUTOMATION_ID)),
        "cls": _str(_cached(el, PID_CLASS_NAME)),
        "role": _str(_cached(el, uia_nodes.PID_ARIA_ROLE)),
        "aria": _aria(_str(_cached(el, PID_ARIA_PROPERTIES)), ctype),
        "help": _str(_cached(el, PID_HELP_TEXT)),
        "desc": _str(_cached(el, PID_FULL_DESCRIPTION)),
        "required": bool(_cached(el, PID_IS_REQUIRED)),
        "disabled": _cached(el, PID_IS_ENABLED) is False,
        "value": "",
    }
    if n["role"].lower() in _TYPE_ROLES:
        n["role"] = ""
    if ctype in _WITH_VALUE and not _cached(el, uia_nodes.PID_IS_PASSWORD):
        n["value"] = _str(_cached(el, uia_nodes.PID_VALUE))
    return n


def _walk(el, parent: int, depth: int, out: List[Dict[str, Any]]) -> None:
    try:
        children = el.GetCachedChildren()
        count = children.Length if children else 0
    except Exception:
        return
    for i in range(count):
        child = children.GetElement(i)
        if _cached(child, PID_CLASS_NAME) == BROWSER_OWN_CLASS:
            continue
        out.append(_node(child, parent, depth))
        _walk(child, len(out) - 1, depth + 1, out)


def read_keys(hwnd: int, raw_view: bool, timeout_sec: float = READ_TIMEOUT) -> List[List[Dict[str, Any]]]:
    """One cached read, like uia_nodes.read_page_nodes, with every attribute-carrying property."""
    from core.vsdc import vsdc_uia_text as uia_text

    def _do():
        uia, uia_client = uia_text._get_uia_on_worker()
        if not uia:
            return []
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return []
        request = uia_nodes.build_cache_request(uia, raw_view)
        for pid in _EXTRA:
            try:
                request.AddProperty(pid)
            except Exception:
                pass
        condition = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, uia_nodes.CT_DOCUMENT)
        found = root.FindAllBuildCache(4, condition, request)       # TreeScope_Descendants
        docs = []
        elements = [found.GetElement(d) for d in range(found.Length if found else 0)]
        # Never a background tab's page. The request caches elements only (no live reference), so
        # CurrentIsOffscreen cannot be read here: the cached IsOffscreen decides.
        for element in [e for e in elements if not _cached(e, uia_nodes.PID_IS_OFFSCREEN)]:
            nodes: List[Dict[str, Any]] = []
            _walk(element, -1, 0, nodes)
            docs.append(nodes)
        return docs

    return uia_text._run_with_timeout(_do, timeout_sec) or []


def read_url(hwnd: int, timeout_sec: float = 5.0) -> str:
    """The browser's address bar (Chrome / Edge / Firefox all name it "...address..."), or ""."""
    from core.vsdc import vsdc_uia_text as uia_text

    def _do():
        uia, uia_client = uia_text._get_uia_on_worker()
        root = uia.ElementFromHandle(hwnd) if uia else None
        if not root:
            return ""
        edits = root.FindAll(4, uia.CreateOrCondition(           # Edit, or Firefox's ComboBox
            uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, 50004),
            uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, 50003)))
        for i in range(edits.Length if edits else 0):
            edit = edits.GetElement(i)
            if "address" in (edit.CurrentName or "").lower():
                p = edit.GetCurrentPattern(uia_client.UIA_ValuePatternId)
                if p:
                    return p.QueryInterface(uia_client.IUIAutomationValuePattern).CurrentValue or ""
        return ""

    return uia_text._run_with_timeout(_do, timeout_sec) or ""


# ---- report (pure) --------------------------------------------------------------------------

def _type(n: Dict[str, Any]) -> str:
    return CONTROL_TYPE_NAMES.get(n.get("ctype") or 0, str(n.get("ctype")))


def _clip(s: str, n: int = CARRIES_MAX) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _identity(n: Dict[str, Any]) -> tuple:
    return (n.get("ctype"), n.get("name"), n.get("rect"), n.get("id"), n.get("cls"))


def _has_key(n: Dict[str, Any]) -> bool:
    return any(n.get(k) for k, _ in KEYS)


def _keys_line(n: Dict[str, Any]) -> str:
    parts = [f"{mark}{n[k]}" for k, mark in KEYS if n.get(k)]
    if n.get("required"):
        parts.append("[required]")
    if n.get("disabled"):
        parts.append("[disabled]")
    return "  ".join(parts)


# Page-tree columns (characters). Longer text is cut with "…" so the columns stay straight.
COL_TEXT, COL_ELEMENT, COL_ATTRS, COL_SGT, COL_SEEN, COL_VALUES = 46, 12, 44, 8, 9, 40
INDENT = 3


def _tree_header(capture: bool) -> List[str]:
    cols = [("TEXT  (indented = inside the line above)", COL_TEXT), ("ELEMENT", COL_ELEMENT),
            ("ATTRIBUTES", COL_ATTRS), ("IN SGT", COL_SGT)]
    if capture:
        cols += [("SEEN", COL_SEEN), ("VALUES (when it changed)", COL_VALUES)]
    return ["  ".join(f"{name:{w}}" for name, w in cols) + "  KEY",
            "  ".join("-" * w for _, w in cols) + "  " + "-" * 40]


def _cell(s: str, width: int, indent: int = 0) -> str:
    """`s` on one line (inner whitespace runs kept to the double spaces we put in), after `indent`
    spaces, cut with "…" to fit `width`."""
    parts = [" ".join(part.split()) for part in (s or "").split("  ")]
    s = " " * indent + "  ".join(x for x in parts if x)
    return (s if len(s) <= width else s[:width - 1] + "…").ljust(width)


def _tree(docs: List[List[Dict[str, Any]]], in_control: Optional[set] = None,
          flat: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """The page as one compact tree in columns: TEXT (indented by nesting), ELEMENT,
    ATTRIBUTES (#id .classes role= aria: help: desc: [required] [disabled]), IN SGT ("no" = SGT's
    control view does not have this element: a <div>/<span> container whose text still reaches
    SGT), KEY (keys.py - the key compare.py, link_map.py and snapshot_diff.py match it by).
    Only elements with text or an attribute; a text that only repeats its parent's is dropped; a
    blank line before every top-level element.

    One read: `docs` + `in_control`. A capture: `flat` = LinkMap.to_flat() (one list, keys,
    first-seen and values given) and `docs` = [its nodes]; IN SGT then comes from each node's own
    "sgt" mark, and two more columns show: SEEN (seconds into the capture it first appeared) and
    VALUES (every value it had, in order - only when it changed)."""
    capture = flat is not None
    if flat is None:
        flat = key_flatten({"docs": [[dict(n, type_name=_type(n)) for n in doc] for doc in docs]})
    lines: List[str] = []
    offset = 0
    for d, doc in enumerate(docs):
        base, offset = offset, offset + len(doc)
        header = len(lines)
        anc: Dict[int, int] = {}           # node -> nearest kept ancestor-or-self (-1 = none)
        depth: Dict[int, int] = {}
        for i, n in enumerate(doc):
            p = n.get("parent", -1)
            up = anc.get(p, -1) if p >= 0 else -1
            text = n.get("name") or n.get("value")
            keep = _has_key(n) or n.get("required") or n.get("disabled") or (
                text and not (up >= 0 and not _has_key(n) and n.get("name") == doc[up].get("name")))
            if not keep:
                anc[i] = up
                continue
            anc[i] = i
            depth[i] = depth[up] + 1 if up >= 0 else 0
            name = " ".join((n.get("name") or "").split())
            value = " ".join((n.get("value") or "").split()) if n.get("ctype") in _INPUT_TYPES else ""
            shown = f"{name}  =  {value}" if value and name and value != name else (value or name)
            shown = f'"{shown}"' if shown else "(box)"
            if depth[i] == 0 and len(lines) > header:
                lines.append("")
            e = flat[base + i]
            sees = n.get("sgt", True) if capture else _identity(n) in (in_control or set())
            cells = [_cell(shown, COL_TEXT, INDENT * min(depth[i], 8)), _cell(_type(n), COL_ELEMENT),
                     _cell(_keys_line(n), COL_ATTRS), _cell("" if sees else "no", COL_SGT)]
            if capture:
                values = e.get("values") or []
                cells += [_cell(e.get("first", ""), COL_SEEN),
                          _cell("  ->  ".join(values) if len(values) > 1 else "", COL_VALUES)]
            lines.append("  ".join(cells) + "  " + e["key"])
        if len(docs) > 1 and len(lines) > header:          # empty documents (browser panes) unlisted
            lines[header:header] = [f"=== document {d + 1} ===", ""]
    return _tree_header(capture) + lines


def build_report(control: List[List[Dict[str, Any]]], raw: List[List[Dict[str, Any]]],
                 title: str = "", process: str = "") -> List[str]:
    c_all = [n for doc in control for n in doc]
    r_all = [n for doc in raw for n in doc]
    in_control = {_identity(n) for n in c_all}
    ids = Counter(n["id"] for n in r_all if n.get("id"))
    classes = Counter(c for n in r_all for c in (n.get("cls") or "").split())

    out = [
        f"Window title : {title}",
        f"Process      : {process}",
        f"Read at      : {datetime.now():%Y-%m-%d %H:%M:%S}",
        "=" * 100,
        "SUMMARY - how many elements carry each key",
        "=" * 100,
        f"  {'key':30s} {'control view (SGT)':>20s} {'raw view (all)':>16s}",
        f"  {'elements':30s} {len(c_all):20d} {len(r_all):16d}",
    ]
    labels = {"id": "id", "cls": "class", "role": "role (beyond element type)", "aria": "aria-* properties",
              "help": "title / placeholder", "desc": "description"}
    for k, _ in KEYS:
        out.append(f"  {labels[k]:30s} {sum(1 for n in c_all if n.get(k)):20d} {sum(1 for n in r_all if n.get(k)):16d}")
    for flag in ("required", "disabled"):
        out.append(f"  {flag:30s} {sum(1 for n in c_all if n.get(flag)):20d} {sum(1 for n in r_all if n.get(flag)):16d}")
    dups = sorted(a for a, k in ids.items() if k > 1)
    digits = sorted(a for a in ids if any(ch.isdigit() for ch in a))
    out += [
        "",
        f"  ids used more than once : {len(dups)}" + (f"  ({', '.join(dups[:10])})" if dups else ""),
        f"  ids holding a digit     : {len(digits)}  - often generated per session; compare with a second session"
        + (f"  ({', '.join(digits[:10])})" if digits else ""),
        "  Never exposed through UIA: the name attribute, data-*, formcontrolname / ng-* / v-*, style, the tag.",
    ]
    if classes:
        out += ["", "  Most used class names (raw view):"]
        out += [f"    {k:5d} x .{c}" for c, k in classes.most_common(15)]

    out += ["", "=" * 100,
            "PAGE TREE",
            "=" * 100] + _tree(raw, in_control)
    return out


def _say(msg: str) -> None:
    print(msg, flush=True)


def _window_title(hwnd: int) -> str:
    import ctypes
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def browser_name_of_hwnd(hwnd: int) -> str:
    """Returns the process executable name of hwnd without .exe, lower case."""
    if not hwnd:
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        proc_name = ""
        if hproc:
            name_buff = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(hproc, 0, name_buff, ctypes.byref(size)):
                full_path = name_buff.value
                proc_name = full_path.split("\\")[-1].lower()
            kernel32.CloseHandle(hproc)
        if proc_name.endswith(".exe"):
            proc_name = proc_name[:-4]
        return proc_name
    except Exception:
        return ""


def _typed(docs: List[List[Dict[str, Any]]], in_control: Optional[set] = None) -> List[List[Dict[str, Any]]]:
    """The raw-view nodes as stored: type name added, rect as a list, and (when the control view
    was read) whether SGT's control view has the node."""
    return [[dict(n, type_name=_type(n), rect=list(n["rect"]) if n.get("rect") else None,
                  **({"sgt": _identity(n) in in_control} if in_control is not None else {}))
             for n in doc] for doc in docs]


def capture(hwnd: int, seconds: float, interval: float, session: str = "",
            anchor: str = "settled") -> Dict[str, Dict[str, Any]]:
    """Poll the window for `seconds`, like SGT: every `interval` read the page (raw view); keep the
    read only when its keys or texts changed since the last kept read OF THAT PAGE LINK, and merge
    every kept read into that link's map AND hand it to snapshot_diff.AnchorPage, which builds the
    link's comp page (anchor page + attached nodes). Returns {page link: state}. Ctrl+C ends it early."""
    import link_map
    from snapshot_diff import AnchorPage

    pages: Dict[str, Dict[str, Any]] = {}
    start = time.monotonic()
    try:
        while True:
            t = time.monotonic() - start
            if t >= seconds:
                break
            url = read_url(hwnd)
            title = _window_title(hwnd)
            page = page_link(url) if url else "title: " + title
            raw = read_keys(hwnd, True)
            browser = browser_name_of_hwnd(hwnd)
            st = pages.setdefault(page, {"title": title, "polls": 0, "snapshots": [], "last": None,
                                         "map": link_map.LinkMap("", page, browser=browser),
                                         "anchor": AnchorPage(page, session, browser, anchor), "quiet": False})
            st["polls"] += 1
            if raw:
                docs = _typed(raw)
                sig = tuple((e["key"], e["text"]) for e in key_flatten({"docs": docs}))
                if sig != st["last"]:
                    control = read_keys(hwnd, False)
                    docs = _typed(raw, {_identity(n) for doc in control for n in doc})
                    seen = f"+{t:.1f}s"
                    first = not st["snapshots"]
                    st["snapshots"].append({"t": round(t, 1), "docs": docs})
                    st["last"] = sig
                    st["map"].add({"docs": docs}, seen)
                    row = st["anchor"].add({"docs": docs}, seen, quiet_before=st["quiet"])
                    st["quiet"] = False
                    if first:
                        _say(f"  {seen:>7s}  page: {page[-60:]}")
                    else:
                        what = []
                        for tag, k in (("+", "attached_texts"), ("-", "left_texts"), ("<", "returned_texts")):
                            if row[k]:
                                what.append(f"{tag}{len(row[k])}: " + " | ".join(x[:25] for x in row[k][:3]))
                        _say(f"  {seen:>7s}  {row['phase']:6s} {row['nodes']:4d} nodes  "
                             + ("  ".join(what) if what else f"(values: {row['text_changed']} changed)"))
                else:
                    st["quiet"] = True
            time.sleep(max(0.0, interval - (time.monotonic() - start - t)))
    except KeyboardInterrupt:
        _say("  stopped early (Ctrl+C)")
    return pages


def capture_report(page: str, st: Dict[str, Any], started: datetime, seconds: float, interval: float) -> List[str]:
    lm = st["map"]
    flat = lm.to_flat()
    out = [
        f"Capture      : {started:%Y-%m-%d %H:%M:%S}, {seconds:g} s, a read every {interval:g} s",
        f"Page link    : {page}",
        f"Breadcrumb   : {breadcrumb(flat) or '(none on this page)'}",
        f"Window title : {st['title']}",
        f"Polls        : {st['polls']}   kept (keys or texts changed): {len(st['snapshots'])}",
        f"Nodes        : {len(flat)} in the merged page   with text: {sum(1 for e in flat if e['text'])}",
        "=" * 100,
        "EVENTS - when a part of the page appeared that this capture had not seen before",
        "=" * 100,
    ]
    for ev in lm.events:
        for b in ev["blocks"]:
            if b["texts"]:
                out.append(f"  {ev['read']:>7s}  {b['nodes']:3d} nodes   " + " | ".join(t[:40] for t in b["texts"][:4]))
    if not lm.events:
        out.append("  (none - the page kept the same parts for the whole capture)")
    out += ["", "=" * 100, "PAGE TREE - every node the capture saw, merged by key", "=" * 100]
    out += _tree([[dict(e["node"], parent=e["parent"]) for e in flat]], flat=flat)
    return out


TIMING_TIMEOUT = 30.0      # a heavy page in raw view may take seconds - measure it, don't cut it off


def _percentile(values: List[float], q: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def timing(hwnd: int, rounds: int) -> int:
    """SGT's reader, control vs raw view, alternating. Prints ms and sizes; writes nothing."""
    rounds = max(3, rounds)
    ms: Dict[str, List[float]] = {"control": [], "raw": []}
    size: Dict[str, tuple] = {}
    for view in ("control", "raw"):                               # warm both once, uncounted
        uia_nodes.read_page_nodes(hwnd, TIMING_TIMEOUT, raw_view=view == "raw")
    _say(f"Timing SGT's reader: {rounds} reads per view, alternating...")
    for r in range(rounds):
        order = ("control", "raw") if r % 2 == 0 else ("raw", "control")
        for view in order:
            t0 = time.perf_counter()
            res = uia_nodes.read_page_nodes(hwnd, TIMING_TIMEOUT, raw_view=view == "raw")
            ms[view].append((time.perf_counter() - t0) * 1000)
            docs = res.get("docs") or []
            nodes = sum(len(d) for d in docs)
            texts = sum(1 for d in docs for n in d if (n.get("name") or "").strip())
            if not nodes:
                _say(f"  {view} read came back empty (timed out or page changed) - try again on a settled page")
                return 1
            size[view] = (nodes, texts)
    _say("")
    _say(f"  {'view':8s} {'median':>9s} {'p90':>9s} {'worst':>9s} {'elements':>9s} {'texts':>7s}")
    for view in ("control", "raw"):
        v = ms[view]
        _say(f"  {view:8s} {_percentile(v, 0.5):7.0f}ms {_percentile(v, 0.9):7.0f}ms {max(v):7.0f}ms "
             f"{size[view][0]:9d} {size[view][1]:7d}")
    ratio = _percentile(ms["raw"], 0.5) / max(0.001, _percentile(ms["control"], 0.5))
    _say("")
    _say(f"  raw view = {ratio:.1f}x the control view's time, {size['raw'][0] / max(1, size['control'][0]):.1f}x its elements")
    _say("  (SGT reads once a second; a read above ~350 ms would use more than a third of that.)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Show a page's HTML keys as SGT sees them (read-only).")
    ap.add_argument("--delay", type=int, default=8, help="seconds to switch to the browser (default 8)")
    ap.add_argument("--title", default="", help="read the window whose title contains this, instead of the foreground one")
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to capture (default 30)")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between reads while capturing (default 1)")
    ap.add_argument("--once", action="store_true", help="one read only (the old behaviour), no capture")
    ap.add_argument("--anchor", choices=("settled", "first"), default="settled",
                    help="comp page: settled (default) = the anchor page is fixed once the loaded page sits still "
                         "for a poll; first = the first snapshot alone")
    ap.add_argument("--timing", action="store_true", help="time SGT's reader in control vs raw view; writes nothing")
    ap.add_argument("--rounds", type=int, default=15, help="reads per view for --timing (default 15)")
    args = ap.parse_args(argv)

    if args.title:
        hwnd, title = find_hwnd_by_title_substring(args.title)
        process = ""
        if not hwnd:
            _say(f"No window with {args.title!r} in its title.")
            return 1
    else:
        _say(f"Switch to the browser tab now - reading the foreground window in {args.delay}s...")
        for left in range(args.delay, 0, -1):
            _say(f"  {left}...")
            time.sleep(1)
        hwnd, title, process = get_foreground_info()
        if not hwnd or not any(b in process for b in BROWSER_EXE_NAMES):
            _say(f"The foreground window ({process or 'none'}) is not a browser. Focus the portal tab and re-run.")
            return 1
    _say(f"Window: {title!r}")
    if args.timing:
        return timing(hwnd, args.rounds)
    for _ in range(WARM_READS):
        read_keys(hwnd, False)
    os.makedirs(OUT_DIR, exist_ok=True)

    if not args.once:
        started = datetime.now()
        session = f"{started:%Y%m%d_%H%M%S}"
        _say(f"Capturing for {args.seconds:g} s - use the page normally (type, open popups, submit). Ctrl+C stops early.")
        pages = capture(hwnd, args.seconds, args.interval, session, args.anchor)
        _say("Done.")
        for n, (page, st) in enumerate(pages.items(), 1):
            if not st["snapshots"]:
                continue
            path = os.path.join(OUT_DIR, f"capture_{session}_{n}__{page_slug(page)}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(capture_report(page, st, started, args.seconds, args.interval)) + "\n")
            browser = browser_name_of_hwnd(hwnd)
            record = {"capture": True, "session": session, "page": page, "title": st["title"],
                      "read_at": started.isoformat(timespec="seconds"), "seconds": args.seconds,
                      "interval": args.interval, "polls": st["polls"], "snapshots": st["snapshots"],
                      "browser": browser}
            with open(path[:-4] + ".json", "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False)
            _say(f"  {page[-50:]}: {st['polls']} polls, {len(st['snapshots'])} kept, "
                 f"{len(st['map'].to_flat())} nodes, {len(st['map'].events)} events")
            _say(f"  Report: {path}")
            from snapshot_diff import write_comp
            apg = st["anchor"]
            comp = apg.comp_flat()
            out = write_comp(apg, Path(path[:-4].replace(os.sep + "capture_", os.sep + "comp_")))
            _say(f"  Comp page: {len(comp)} nodes (anchor {sum(1 for e in comp if e['origin'] == 'anchor')}, "
                 f"attached {sum(1 for e in comp if e['origin'] == 'attached')}; anchor fixed at "
                 f"{apg.settled_at or 'never'}) -> {out['json'].name}")
        return 0

    url = read_url(hwnd)
    page = page_link(url) if url else ""
    _say(f"Page link: {page or '(address bar not readable - compare.py will fall back to the window title)'}")
    _say("Reading (control view, then raw view)...")
    control = read_keys(hwnd, False)
    raw = read_keys(hwnd, True)
    if not control and not raw:
        _say("Nothing read - the page may still be loading, or UIA timed out. Try again.")
        return 1

    lines = build_report(control, raw, title, process)
    stamp = datetime.now()
    path = os.path.join(OUT_DIR, f"key_probe_{stamp:%Y%m%d_%H%M%S}__{page_slug(page or title)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        crumb = breadcrumb(key_flatten({"docs": _typed(raw)}))
        f.write(f"Page link    : {page}\nBreadcrumb   : {crumb or '(none on this page)'}\n" + "\n".join(lines) + "\n")
    browser = browser_name_of_hwnd(hwnd)
    # The raw-view nodes for compare.py, each marked whether SGT's control view has it.
    record = {"title": title, "page": page, "read_at": stamp.isoformat(timespec="seconds"),
              "docs": _typed(raw, {_identity(n) for doc in control for n in doc}),
              "browser": browser}
    with open(path[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    for line in lines[4:20]:
        _say(line)
    _say(f"\nFull report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
