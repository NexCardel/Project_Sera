"""
tools/sgt_lines_equivalence.py - the 14.2 gate: are node-rebuilt lines identical to today's?
================================================================================================
Blueprint 14.2 "the one shared part": the Core reads *lines*
(core/vsdc/vsdc_uia_text._collect_descendant_lines), SGT-I wants *nodes*
(core/sgt_i/uia_nodes.read_page_nodes). Both must already agree - W2-1's
uia_nodes.lines_from_nodes() rebuilds today's lines from a node read - before the Core is
allowed to switch reader. This tool is the actual gate check, in three modes:

  synthetic (always runs)  - built-in fixture pages, read by BOTH functions from the same
                              in-memory element tree (a fake UIA layer, no browser or COM). No
                              window needed, so this mode always has something to check and is
                              the one tests/test_sgt_lines_equivalence.py runs on every commit.
  corpus    (best effort)  - pages already recorded by core/sgt/sgt_corpus.py that also carry a
                              node dump. Nothing does yet (that's W2-4); until then this mode
                              reports 0 comparable records rather than failing.
  live      (best effort)  - an already-open window matched by --title, read with BOTH real
                              readers. Strictly a read of what is already on screen - this tool
                              never opens, clicks or types anything.

Usage:
  python tools/sgt_lines_equivalence.py                    # synthetic + corpus
  python tools/sgt_lines_equivalence.py --title "GST"      # + a live window whose title has "GST"
  python tools/sgt_lines_equivalence.py --corpus PATH       # a corpus dir other than the default
  python tools/sgt_lines_equivalence.py --skip-corpus
"""

import argparse
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sgt.sgt_corpus import corpus_dir, load_pages   # noqa: E402
from core.sgt_i import uia_nodes as nodes_mod             # noqa: E402
from core.vsdc import vsdc_uia_text as text_mod           # noqa: E402

Diff = Tuple[int, Optional[str], Optional[str]]   # index, today's line, node line ("" side if missing)


def _diff_lines(today: List[str], nodes: List[str]) -> List[Diff]:
    out: List[Diff] = []
    for i in range(max(len(today), len(nodes))):
        a = today[i] if i < len(today) else None
        b = nodes[i] if i < len(nodes) else None
        if a != b:
            out.append((i, a, b))
    return out


# ---------------------------------------------------------------------------------------------
# Synthetic mode: one fake element tree read by both real functions, no browser or COM.
# ---------------------------------------------------------------------------------------------

# Sentinel pattern/property ids - only need to be distinct and consistent between the fake
# uia_client and the fake element's GetCurrentPattern, never real UIAutomationClient.h values.
_PID_VALUE, _PID_SELECTION, _PID_TOGGLE, _PID_IS_PASSWORD = object(), object(), object(), object()


class _FakeUiaClient:
    TreeScope_Descendants = "descendants"
    UIA_ValuePatternId = _PID_VALUE
    UIA_SelectionItemPatternId = _PID_SELECTION
    UIA_TogglePatternId = _PID_TOGGLE
    UIA_IsPasswordPropertyId = _PID_IS_PASSWORD
    IUIAutomationValuePattern = "IValue"
    IUIAutomationSelectionItemPattern = "ISelectionItem"
    IUIAutomationTogglePattern = "IToggle"


class _FakeUia:
    def CreateTrueCondition(self):
        return True


class _FakePattern:
    """QueryInterface is a no-op here - the fake already exposes the Current* the real
    interface-specific wrapper would, so .QueryInterface(iface).CurrentX works unchanged."""

    def __init__(self, **current):
        self._current = current

    def QueryInterface(self, _iface):
        return self

    def __getattr__(self, name):
        return self._current[name]


class _Collection:
    def __init__(self, items: List[Any]):
        self._items = items
        self.Length = len(items)

    def GetElement(self, i: int) -> Any:
        return self._items[i]


class FakeElement:
    """One page node, read through BOTH the live pattern-based API
    (vsdc_uia_text._collect_descendant_lines) and the cached-property API
    (core.sgt_i.uia_nodes._walk/_node) - so a single fixture tree is exactly the same 'page'
    for each reader, and any mismatch is a real difference between the two implementations."""

    def __init__(self, name: str = "", ctype: int = 0, value: Optional[str] = None,
                 selected: Optional[bool] = None, heading: Optional[int] = None,
                 is_password: bool = False, children: Tuple["FakeElement", ...] = ()):
        self.name = name
        self.ctype = ctype
        self.value = value
        self.selected = selected
        self.heading = heading
        self.is_password = is_password
        self.children = list(children)

    # --- live API ---
    @property
    def CurrentName(self) -> str:
        return self.name

    @property
    def CurrentControlType(self) -> int:
        return self.ctype

    def GetCurrentPattern(self, pattern_id):
        if pattern_id is _PID_VALUE and self.ctype in text_mod._VALUE_BEARING_CONTROL_TYPES:
            return _FakePattern(CurrentValue=self.value or "")
        if pattern_id is _PID_SELECTION and self.ctype == text_mod.UIA_RADIOBUTTON_CONTROL_TYPE_ID \
                and self.selected is not None:
            return _FakePattern(CurrentIsSelected=bool(self.selected))
        if pattern_id is _PID_TOGGLE and self.ctype == text_mod.UIA_CHECKBOX_CONTROL_TYPE_ID \
                and self.selected is not None:
            return _FakePattern(CurrentToggleState=1 if self.selected else 0)
        return None

    def GetCurrentPropertyValue(self, pid):
        if pid is _PID_IS_PASSWORD:
            return bool(self.is_password)
        return None

    def FindAll(self, _scope, _cond) -> _Collection:
        out: List[FakeElement] = []

        def walk(el: "FakeElement") -> None:
            for c in el.children:
                out.append(c)
                walk(c)

        walk(self)
        return _Collection(out)

    # --- cached API (same field names as core.sgt_i.uia_nodes) ---
    def GetCachedPropertyValue(self, pid):
        if pid == nodes_mod.PID_NAME:
            return self.name
        if pid == nodes_mod.PID_CONTROL_TYPE:
            return self.ctype
        if pid == nodes_mod.PID_BOUNDING_RECT:
            return (1.0, 2.0, 3.0, 4.0)
        if pid == nodes_mod.PID_HEADING_LEVEL:
            return nodes_mod._HEADING_NONE + self.heading if self.heading else None
        if pid == nodes_mod.PID_IS_PASSWORD:
            return self.is_password
        if pid == nodes_mod.PID_VALUE and self.ctype in nodes_mod._VALUE_BEARING:
            return self.value or ""
        if pid == nodes_mod.PID_SELECTION_IS_SELECTED and self.ctype == nodes_mod.CT_RADIOBUTTON:
            return self.selected
        if pid == nodes_mod.PID_TOGGLE_STATE and self.ctype == nodes_mod.CT_CHECKBOX:
            return 1 if self.selected else 0
        return None

    def GetCachedChildren(self) -> _Collection:
        return _Collection(self.children)


def _combobox(name: str, value: str) -> FakeElement:
    return FakeElement(name, text_mod.UIA_COMBOBOX_CONTROL_TYPE_ID, value=value)


def _edit(name: str, value: str) -> FakeElement:
    return FakeElement(name, text_mod.UIA_EDIT_CONTROL_TYPE_ID, value=value)


def _password_edit(name: str, value: str) -> FakeElement:
    return FakeElement(name, text_mod.UIA_EDIT_CONTROL_TYPE_ID, value=value, is_password=True)


def _radio(name: str, selected: bool) -> FakeElement:
    return FakeElement(name, text_mod.UIA_RADIOBUTTON_CONTROL_TYPE_ID, selected=selected)


def _checkbox(name: str, selected: bool) -> FakeElement:
    return FakeElement(name, text_mod.UIA_CHECKBOX_CONTROL_TYPE_ID, selected=selected)


def synthetic_pages() -> List[Tuple[str, FakeElement]]:
    """One fixture tree per 14.1 case this shared read has to get right, plus the plain edges
    (empty name skipped, value == name not duplicated)."""
    return [
        ("headings_and_groups", FakeElement("", children=(
            FakeElement("Personal details", heading=2),
            FakeElement("", children=(          # empty-name container: no line of its own
                _edit("First Name", "Test"),
                FakeElement("Untitled", children=()),
            )),
        ))),
        ("filed_under_option_list", FakeElement("", children=(  # "Belated" read from the option list
            FakeElement("Filed u/s", heading=3),
            _radio("139(1) - On or before due date", False),
            _radio("139(4) - Belated", True),
            _radio("139(5) - Revised", False),
        ))),
        ("checkbox_and_combobox", FakeElement("", children=(
            _checkbox("I accept the terms", True),
            _checkbox("Subscribe to updates", False),
            _combobox("Assessment Year", "2026-27"),
        ))),
        ("value_equals_name_not_duplicated", FakeElement("", children=(
            _edit("GSTIN", "GSTIN"),             # a field whose value happens to echo its label
        ))),
        ("password_value_never_read", FakeElement("", children=(
            _password_edit("Portal Password", "hunter2"),   # label kept, value must never surface
        ))),
    ]


def check_synthetic(include_selection: bool = True) -> List[Tuple[str, List[Diff]]]:
    """Runs both real readers over the same fixture trees. Returns (page, diffs) for pages that
    disagree - empty when the two are equivalent."""
    uia, uia_client = _FakeUia(), _FakeUiaClient()
    mismatches: List[Tuple[str, List[Diff]]] = []
    for name, root in synthetic_pages():
        today = text_mod._collect_descendant_lines(uia, uia_client, root, include_selection=include_selection)
        walked: List[Dict[str, Any]] = []
        nodes_mod._walk(root, -1, 0, walked)
        rebuilt = nodes_mod.lines_from_nodes([walked], include_selection=include_selection)
        diffs = _diff_lines(today, rebuilt)
        if diffs:
            mismatches.append((name, diffs))
    return mismatches


# ---------------------------------------------------------------------------------------------
# Corpus mode: recorded pages, once W2-4 gives them a node dump too.
# ---------------------------------------------------------------------------------------------

CORPUS_NODE_KEY = "nodes"   # the field W2-4 is expected to add: {"docs": [[node, ...], ...]}


def check_corpus(directory: Optional[Path] = None) -> Tuple[int, int, List[Tuple[str, List[Diff]]]]:
    """Returns (records seen, records with no node dump yet, mismatches). A record counts as
    comparable only once it carries CORPUS_NODE_KEY - today that is none of them."""
    pages = load_pages(directory)
    no_nodes = 0
    mismatches: List[Tuple[str, List[Diff]]] = []
    for rec in pages:
        docs = rec.get(CORPUS_NODE_KEY)
        if not docs:
            no_nodes += 1
            continue
        rebuilt = nodes_mod.lines_from_nodes(docs, include_selection=True)
        diffs = _diff_lines(rec.get("lines") or [], rebuilt)
        if diffs:
            mismatches.append((rec.get("hash") or rec.get("url") or "?", diffs))
    return len(pages), no_nodes, mismatches


# ---------------------------------------------------------------------------------------------
# Live mode: an already-open window, matched by title. Never opens or clicks anything.
# ---------------------------------------------------------------------------------------------

def find_hwnd_by_title_substring(substr: str, timeout_sec: float = 5.0) -> Optional[int]:
    user32 = ctypes.windll.user32
    found: Dict[str, Any] = {}
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if substr.lower() in buf.value.lower():
            found["hwnd"] = hwnd
            return False
        return True

    proc = EnumWindowsProc(callback)
    user32.EnumWindows(proc, 0)
    return found.get("hwnd")


def check_live(title: str) -> Optional[Tuple[str, List[Diff]]]:
    """Reads the already-open window whose title contains `title` with both real readers.
    None if no such window, or if a reader found nothing to read."""
    hwnd = find_hwnd_by_title_substring(title)
    if not hwnd:
        print(f"!! no visible window titled like {title!r}")
        return None
    today = text_mod.read_page_text(hwnd, include_selection=True)["lines"]
    docs = nodes_mod.read_page_nodes(hwnd)["docs"]
    if not today and not docs:
        print(f"!! window {title!r} (hwnd={hwnd}) gave neither reader anything to read")
        return None
    rebuilt = nodes_mod.lines_from_nodes(docs, include_selection=True)
    diffs = _diff_lines(today, rebuilt)
    return (title, diffs) if diffs else None


# ---------------------------------------------------------------------------------------------

def _report(label: str, diffs: List[Diff]) -> None:
    print(f"  DIFFER: {label} ({len(diffs)} line(s))")
    for i, a, b in diffs[:5]:
        print(f"    line {i}: today={a!r} nodes={b!r}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")   # portal text (e.g. an emoji) must not crash the report on cp1252
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=None, help="corpus directory (default: the real one)")
    ap.add_argument("--skip-corpus", action="store_true", help="skip the corpus check")
    ap.add_argument("--title", default=None, help="substring of an already-open window's title")
    args = ap.parse_args(argv)

    ok = True

    print(f"== synthetic ({len(synthetic_pages())} fixture pages)")
    mismatches = check_synthetic()
    if mismatches:
        ok = False
        for name, diffs in mismatches:
            _report(name, diffs)
    else:
        print("  all identical")

    if not args.skip_corpus:
        directory = args.corpus or corpus_dir()
        seen, no_nodes, mismatches = check_corpus(args.corpus)
        print(f"\n== corpus ({directory})")
        print(f"  {seen} record(s), {seen - no_nodes} comparable (have a {CORPUS_NODE_KEY!r} node dump)")
        if seen - no_nodes == 0:
            print("  nothing to compare yet - W2-4 has not added node dumps to the corpus")
        elif mismatches:
            ok = False
            for label, diffs in mismatches:
                _report(label, diffs)
        else:
            print("  all identical")

    if args.title:
        print(f"\n== live window matching {args.title!r}")
        result = check_live(args.title)
        if result is not None:
            ok = False
            _report(*result)
        else:
            print("  no mismatch (see above for whether a window was actually read)")

    print(f"\n{'PASS' if ok else 'FAIL'} - 14.2 gate {'holds' if ok else 'is not met'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
