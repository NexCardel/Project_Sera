"""
tools/sgt_i_key_probe.py - see a page's HTML keys the way SGT sees them
=======================================================================
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
    python tools/sgt_i_key_probe.py                    # switch to the browser within 8 s
    python tools/sgt_i_key_probe.py --delay 15
    python tools/sgt_i_key_probe.py --title "GST"      # a window whose title contains "GST"

Report: tools/vsdc_uia_probe_output/key_probe_YYYYMMDD_HHMMSS.txt (git-ignored - it holds what the
page shows, client data included; keep it on this PC). A password field's content is never read.
Checked in Edge (Chromium); Firefox exposes UIA differently.
"""

import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.sgt_i import uia_nodes                                                        # noqa: E402
from core.sgt_i.uia_nodes import _cached                                                # noqa: E402
from tools.vsdc_uia_probe import BROWSER_EXE_NAMES, CONTROL_TYPE_NAMES, get_foreground_info  # noqa: E402
from tools.vsdc_x_html_sample_test import find_hwnd_by_title_substring                  # noqa: E402

OUT_DIR = os.path.join(ROOT, "tools", "vsdc_uia_probe_output")
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
        for d in range(found.Length if found else 0):
            nodes: List[Dict[str, Any]] = []
            _walk(found.GetElement(d), -1, 0, nodes)
            docs.append(nodes)
        return docs

    return uia_text._run_with_timeout(_do, timeout_sec) or []


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


def _tree(docs: List[List[Dict[str, Any]]], in_control: set) -> List[str]:
    """The raw view as one compact tree: only elements with text or a key, indented by their kept
    ancestors, a text line that only repeats its parent's name dropped. "~" = SGT's control view
    does not have this element (a <div>/<span> container - its text still reaches SGT)."""
    lines: List[str] = []
    for d, doc in enumerate(docs):
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
            parts = [f"{'~' if _identity(n) not in in_control else ' '} {'  ' * depth[i]}[{_type(n)}]"]
            keys = _keys_line(n)
            if keys:
                parts.append(keys)
            if n.get("name"):
                parts.append(repr(_clip(n["name"], 80)))
            if n.get("value") and n["value"] != n.get("name"):
                parts.append(f"=> {_clip(n['value'], 60)!r}")
            lines.append(" ".join(parts))
        if len(docs) > 1 and len(lines) > header:          # empty documents (browser panes) unlisted
            lines.insert(header, f"--- document {d + 1} ---")
    return lines


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
            "PAGE TREE - elements with text or a key; '~' = a container SGT's read does not have",
            "=" * 100] + _tree(raw, in_control)
    return out


def _say(msg: str) -> None:
    print(msg, flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Show a page's HTML keys as SGT sees them (read-only).")
    ap.add_argument("--delay", type=int, default=8, help="seconds to switch to the browser (default 8)")
    ap.add_argument("--title", default="", help="read the window whose title contains this, instead of the foreground one")
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

    _say("Reading (control view, then raw view)...")
    for _ in range(WARM_READS):
        read_keys(hwnd, False)
    control = read_keys(hwnd, False)
    raw = read_keys(hwnd, True)
    if not control and not raw:
        _say("Nothing read - the page may still be loading, or UIA timed out. Try again.")
        return 1

    lines = build_report(control, raw, title, process)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"key_probe_{datetime.now():%Y%m%d_%H%M%S}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    for line in lines[4:20]:
        _say(line)
    _say(f"\nFull report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
