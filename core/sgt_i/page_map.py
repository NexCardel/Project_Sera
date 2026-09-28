"""
core/sgt_i/page_map.py - the page as a floor plan: nodes, zones, sections, layout pairs
======================================================================================
Blueprint 14.4 step 1. Turns one page read into a structured map that every later SGT-I step
uses:

* Node       - one piece of text with its role, box, table row/column, heading level,
               landmark, selected state and value. Built from the W2-1 cached UIA read
               (nodes_from_uia) or from OCR line boxes (nodes_from_ocr), so canvas portals get
               the same map with less detail.
* zones      - header (logged-in identity), main, dialog (what just happened), stepper
               (progress, never an event), help (never evidence), navigation, footer (ignored).
* sections   - a value belongs to the heading it sits under ("Landlord details" -> PAN).
* pairs      - label -> value by layout: an input's own label, the chosen option of a choice
               group, the column header over a table cell, then the nearest text to the right
               or directly below. Never by line order.
* furniture  - optional: runs of text the portal shows on many different pages (learnt by the
               atlas) join the navigation zone, for menus and headers the markup did not label.

Pure functions over plain data: no UIA, no I/O, no state. Portal-neutral: the only words used
are generic UI words, overridable through `vocab` (portal wording belongs in config).
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

Box = Tuple[int, int, int, int]      # left, top, width, height (UIA BoundingRectangle order)

# ---- zones ------------------------------------------------------------------------------------
HEADER, MAIN, DIALOG, STEPPER, HELP, NAVIGATION, FOOTER = (
    "header", "main", "dialog", "stepper", "help", "navigation", "footer")

# How far each zone is trusted as evidence (a base weight; step 7 does the real weighing).
ZONE_TRUST = {DIALOG: 1.0, MAIN: 0.8, HEADER: 0.5, STEPPER: 0.1,
              HELP: 0.0, NAVIGATION: 0.0, FOOTER: 0.0}
_NO_PAIRS = frozenset({NAVIGATION, FOOTER, STEPPER})

# Generic UI words (not portal wording); callers may override any key.
DEFAULT_VOCAB: Dict[str, Tuple[str, ...]] = {
    "help": ("faq", "faqs", "frequently asked", "help", "how to", "instructions", "guidelines",
             "need assistance"),
    "stepper": ("step", "steps", "progress", "stepper", "wizard"),
}

# ---- roles ------------------------------------------------------------------------------------
# UIA control type -> generic role.
_CTYPE_ROLE = {
    50000: "button", 50002: "checkbox", 50003: "combobox", 50004: "edit", 50005: "link",
    50006: "image", 50007: "listitem", 50008: "list", 50011: "menuitem", 50013: "radio",
    50016: "edit", 50018: "tab", 50019: "tabitem", 50020: "text", 50026: "group",
    50028: "table", 50029: "cell", 50032: "dialog", 50034: "header", 50035: "columnheader",
    50036: "table",
}
# ARIA role (Chromium's AriaRole property) -> generic role; wins over the control type.
_ARIA_ROLE = {
    "heading": "heading", "dialog": "dialog", "alertdialog": "dialog", "cell": "cell",
    "gridcell": "cell", "columnheader": "columnheader", "rowheader": "rowheader",
    "table": "table", "grid": "table", "option": "option", "tab": "tabitem", "tablist": "tab",
    "textbox": "edit", "searchbox": "edit", "combobox": "combobox", "radio": "radio",
    "checkbox": "checkbox", "button": "button", "link": "link", "listbox": "list",
}
# ARIA landmark roles and UIA LandmarkType ids -> landmark name.
_ARIA_LANDMARK = {
    "banner": "banner", "contentinfo": "contentinfo", "navigation": "navigation",
    "main": "main", "search": "search", "complementary": "complementary", "form": "form",
    "dialog": "dialog", "alertdialog": "dialog",
}
_UIA_LANDMARK = {80001: "form", 80002: "main", 80003: "navigation", 80004: "search"}
_LANDMARK_ZONE = {"banner": HEADER, "contentinfo": FOOTER, "navigation": NAVIGATION,
                  "search": NAVIGATION, "dialog": DIALOG}

_INPUTS = frozenset({"edit", "combobox"})
_OPTIONS = frozenset({"radio", "option"})
_NOT_CONTENT = frozenset({"button", "image", "list", "tab", "table", "group", "header",
                          "dialog", "checkbox", "menuitem"})
_ELLIPSIS = ("...", "…")
_DIGITS = re.compile(r"^\(?\d{1,2}[.)]?$")
_NUMBERED = re.compile(r"^(?:step\s*)?(\d{1,2})\b[.):]?\s*\S", re.I)


@dataclass(frozen=True)
class Node:
    """One piece of text on the page. Indices (parent, table, section) point into PageMap.nodes /
    PageMap.sections; -1 = none."""
    index: int
    text: str
    role: str = "text"
    box: Optional[Box] = None
    row: Optional[int] = None          # table row / column (inherited by a cell's text children)
    col: Optional[int] = None
    table: int = -1                    # nearest table ancestor
    heading: int = 0                   # 1..9, 0 = not a heading
    landmark: str = ""                 # this node's own landmark, if any
    selected: Optional[bool] = None
    value: str = ""
    parent: int = -1
    depth: int = 0
    zone: str = MAIN
    section: int = -1
    step: str = ""                     # stepper only: done / current / upcoming / "" (unknown)

    @property
    def truncated(self) -> bool:
        return self.text.endswith(_ELLIPSIS)

    @property
    def trust(self) -> float:
        """Zone trust, halved for text the page cut short ("RAMESH KUMAR SH...")."""
        return ZONE_TRUST.get(self.zone, 0.0) * (0.5 if self.truncated else 1.0)

    @property
    def is_content(self) -> bool:
        """Text that can be a value: not a control's own name, an option or a heading."""
        return bool(self.text) and not self.heading and self.role not in _NOT_CONTENT \
            and self.role not in _INPUTS and self.role not in _OPTIONS


@dataclass(frozen=True)
class Section:
    index: int
    heading: str
    level: int
    node: int
    parent: int = -1


@dataclass(frozen=True)
class Pair:
    label: str
    value: str
    method: str                        # control / choice / column / right / below
    label_node: int
    value_node: int
    zone: str
    section: int
    row: Optional[int] = None          # table pairs: the row


@dataclass(frozen=True)
class PageMap:
    nodes: Tuple[Node, ...]
    sections: Tuple[Section, ...]
    pairs: Tuple[Pair, ...]

    def section_path(self, index: int) -> Tuple[str, ...]:
        """Heading texts from the outermost section down to the one given (a section index)."""
        path: List[str] = []
        while 0 <= index < len(self.sections):
            path.append(self.sections[index].heading)
            index = self.sections[index].parent
        return tuple(reversed(path))

    def pairs_in(self, heading: str) -> List[Pair]:
        """Pairs whose section path contains this heading (case-insensitive)."""
        want = heading.casefold()
        return [p for p in self.pairs
                if any(h.casefold() == want for h in self.section_path(p.section))]

    def content(self, zones: Iterable[str] = (MAIN, DIALOG, HEADER)) -> List[Node]:
        zones = set(zones)
        return [n for n in self.nodes if n.zone in zones and n.is_content]


# ---- builders ---------------------------------------------------------------------------------

def nodes_from_uia(docs: Sequence[Sequence[Dict[str, Any]]]) -> List[Node]:
    """Nodes from core.sgt_i.uia_nodes.read_page_nodes()["docs"] (pre-order dicts per document).
    Documents are concatenated; parents re-indexed. Names are the node's text."""
    out: List[Node] = []
    for doc in docs:
        base = len(out)
        for i, d in enumerate(doc):
            aria = (d.get("role") or "").strip().lower()
            role = _ARIA_ROLE.get(aria) or _CTYPE_ROLE.get(d.get("ctype") or 0, "other")
            if d.get("heading"):
                role = "heading"
            landmark = _ARIA_LANDMARK.get(aria) or _UIA_LANDMARK.get(d.get("landmark") or 0, "")
            parent = d.get("parent", -1)
            parent = base + parent if parent >= 0 else -1
            grid = d.get("grid")
            row, col = (grid if grid else (None, None))
            table = -1
            if parent >= 0:
                p = out[parent]
                table = parent if p.role == "table" else p.table
                if row is None and p.row is not None:
                    row, col = p.row, p.col
                if role == "listitem" and p.role == "list" and p.parent >= 0 \
                        and out[p.parent].role == "combobox":
                    role = "option"                        # a drop-down's list, not content
            rect = d.get("rect")
            out.append(Node(
                index=base + i, text=d.get("name") or "", role=role,
                box=tuple(rect) if rect and rect[2] > 0 and rect[3] > 0 else None,
                row=row, col=col, table=table, heading=d.get("heading") or 0,
                landmark=landmark, selected=d.get("selected"), value=d.get("value") or "",
                parent=parent, depth=d.get("depth", 0)))
    return out


def ocr_line_boxes(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Line boxes from a VsdcOCR scan result ({"lines": [str], "words": [{text,x,y,width,height}]}):
    Windows OCR joins a line's words with spaces, so each line takes that many words in order."""
    words, lines, w = result.get("words") or [], result.get("lines") or [], 0
    out: List[Dict[str, Any]] = []
    for text in lines:
        count = len(text.split())
        chunk = words[w:w + count]
        w += count
        if not chunk:
            continue
        left = min(c["x"] for c in chunk)
        top = min(c["y"] for c in chunk)
        right = max(c["x"] + c["width"] for c in chunk)
        bottom = max(c["y"] + c["height"] for c in chunk)
        out.append({"text": text, "x": left, "y": top, "width": right - left, "height": bottom - top})
    return out


def nodes_from_ocr(lines: Sequence[Dict[str, Any]], heading_ratio: float = 1.35) -> List[Node]:
    """Nodes from OCR line boxes ({text, x, y, width, height}), in reading order. OCR has no
    roles: a short line noticeably taller than the median line is taken as a heading."""
    boxes = [(str(l.get("text") or "").strip(), (int(l["x"]), int(l["y"]), int(l["width"]),
             int(l["height"]))) for l in lines]
    boxes = [(t, b) for t, b in boxes if t and b[2] > 0 and b[3] > 0]
    boxes = [tb for row in _rows(boxes, key=lambda tb: tb[1]) for tb in row]
    heights = sorted(b[3] for _, b in boxes)
    median = heights[len(heights) // 2] if heights else 0
    out: List[Node] = []
    for t, b in boxes:
        tall = median and b[3] >= heading_ratio * median and len(t.split()) <= 8 \
            and any(c.isalpha() for c in t)
        out.append(Node(index=len(out), text=t, role="heading" if tall else "text", box=b,
                        heading=2 if tall else 0))
    return out


# ---- geometry helpers -------------------------------------------------------------------------

def _cy(b: Box) -> float:
    return b[1] + b[3] / 2.0


def _same_row(a: Box, b: Box) -> bool:
    return abs(_cy(a) - _cy(b)) <= min(a[3], b[3]) / 2.0


def _rows(items: List[Any], key) -> List[List[Any]]:
    """Group items with boxes into rows (vertical centres close), each row sorted left to right."""
    rows: List[List[Any]] = []
    for it in sorted(items, key=lambda i: (_cy(key(i)), key(i)[0])):
        if rows and _same_row(key(rows[-1][0]), key(it)):
            rows[-1].append(it)
        else:
            rows.append([it])
    return [sorted(r, key=lambda i: key(i)[0]) for r in rows]


def _words_match(text: str, words: Iterable[str]) -> bool:
    low = text.casefold()
    return any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in words)


# ---- zones, stepper, sections -----------------------------------------------------------------

def classify_zones(nodes: Sequence[Node], vocab: Optional[Dict[str, Tuple[str, ...]]] = None,
                   header_band_px: int = 100) -> List[Node]:
    """Zone for every node: dialog > navigation/footer/search/banner landmarks > stepper > main.
    Without a banner landmark, what comes before the main landmark (or, with no landmarks at all,
    the top header_band_px of the page) is the header. Help is decided later, by section."""
    vocab = {**DEFAULT_VOCAB, **(vocab or {})}
    zones: List[str] = []
    in_main: List[bool] = []
    for n in nodes:
        pz = zones[n.parent] if n.parent >= 0 else ""
        own = DIALOG if n.role == "dialog" else _LANDMARK_ZONE.get(n.landmark, "")
        zones.append(DIALOG if pz == DIALOG else (own or pz))
        in_main.append(n.landmark == "main" or (n.parent >= 0 and in_main[n.parent]))
    has_banner = any(n.landmark == "banner" for n in nodes)
    first_main = next((i for i, m in enumerate(in_main) if m), None)
    tops = [n.box[1] for n in nodes if n.box]
    page_top = min(tops) if tops else 0
    out: List[Node] = []
    for n, z in zip(nodes, zones):
        if not z and not has_banner:
            if first_main is not None:
                z = HEADER if n.index < first_main else ""
            elif n.box and n.box[1] - page_top < header_band_px:
                z = HEADER
        out.append(replace(n, zone=z or MAIN))
    return _mark_stepper(out, vocab)


def _mark_stepper(nodes: List[Node], vocab: Dict[str, Tuple[str, ...]]) -> List[Node]:
    """A stepper is a row of >= 3 short step labels that are numbered (separate "1" "2" markers or
    "1. Income") or sit in a container named like a stepper. Each step gets
    done / current / upcoming from the selected (current) step, when the page says which it is."""
    nodes = list(nodes)
    cand = [n for n in nodes if n.box and n.zone == MAIN and n.text and n.role not in _INPUTS
            and n.role not in ("cell", "columnheader", "heading") and n.row is None]
    for row in _rows(cand, key=lambda n: n.box):
        markers = [n for n in row if _DIGITS.match(n.text)]
        steps = [n for n in row if not _DIGITS.match(n.text)]
        if len(steps) < 3 or any(len(n.text) > 40 or len(n.text.split()) > 5 for n in steps):
            continue
        numbered = len(markers) >= len(steps) - 1 or all(_NUMBERED.match(n.text) for n in steps)
        named = any(n.parent >= 0 and _words_match(nodes[n.parent].text, vocab["stepper"])
                    for n in steps)
        if not (numbered or named):     # a plain tab bar or a row of labels is not a stepper
            continue
        current = next((i for i, n in enumerate(steps) if n.selected), None)
        for i, n in enumerate(steps):
            state = "" if current is None else ("done" if i < current else
                                                "current" if i == current else "upcoming")
            nodes[n.index] = replace(n, zone=STEPPER, step=state)
        for n in markers:
            nodes[n.index] = replace(n, zone=STEPPER)
    return nodes


def scope_sections(nodes: Sequence[Node], vocab: Optional[Dict[str, Tuple[str, ...]]] = None
                   ) -> Tuple[List[Node], List[Section]]:
    """Each main/dialog node belongs to the nearest heading before it (document order), headings
    nesting by level; main and dialog keep separate heading stacks. Main content under a help
    heading ("FAQ", "How to ...") moves to the help zone."""
    vocab = {**DEFAULT_VOCAB, **(vocab or {})}
    sections: List[Section] = []
    stacks: Dict[str, List[int]] = {MAIN: [], DIALOG: []}
    out: List[Node] = []
    for n in nodes:
        stack = stacks.get(n.zone)
        if stack is None:
            out.append(n)
            continue
        if n.heading and n.text:
            while stack and sections[stack[-1]].level >= n.heading:
                stack.pop()
            sections.append(Section(len(sections), n.text, n.heading, n.index,
                                    stack[-1] if stack else -1))
            stack.append(len(sections) - 1)
        sec = stack[-1] if stack else -1
        zone = n.zone
        if zone == MAIN and sec >= 0:
            s_idx = sec
            while s_idx >= 0:
                if _words_match(sections[s_idx].heading, vocab["help"]):
                    zone = HELP
                    break
                s_idx = sections[s_idx].parent
        out.append(replace(n, section=sec, zone=zone))
    return out, sections


# ---- layout pairing ---------------------------------------------------------------------------

def _label_text(text: str) -> str:
    return text.strip().rstrip(":*").strip()


def _looks_label(n: Node) -> bool:
    return n.text.rstrip().rstrip("*").endswith(":")


def pair_by_layout(nodes: Sequence[Node], max_right_gap: int = 500) -> List[Pair]:
    """Label -> value pairs, most certain first: an input's own name (or the text beside it) with
    its value; a choice group's label with its SELECTED option (never the option list); a table
    column header with each cell; then free text with the nearest text to its right on the same
    row, else directly below. Pairs never cross a zone or a section; navigation, footer and
    stepper are skipped. A node is used once, as a label or as a value."""
    pairs: List[Pair] = []
    used: set = set()
    live = [n for n in nodes if n.zone not in _NO_PAIRS]
    free = [n for n in live if n.box and n.is_content and n.row is None]

    def add(label: Node, value_text: str, value: Node, method: str, row=None) -> None:
        pairs.append(Pair(_label_text(label.text), value_text, method, label.index, value.index,
                          value.zone, value.section, row))
        used.update((label.index, value.index))

    def beside(target: Node) -> Optional[Node]:
        """The free text just left of, else just above, a node - its visual label."""
        if not target.box:
            return None
        t = target.box
        left = [n for n in free if n.index not in used and n.zone == target.zone
                and n.section == target.section and _same_row(n.box, t)
                and n.box[0] + n.box[2] <= t[0] + 2 and t[0] - (n.box[0] + n.box[2]) <= max_right_gap]
        if left:
            return max(left, key=lambda n: n.box[0] + n.box[2])
        above = [n for n in free if n.index not in used and n.zone == target.zone
                 and n.section == target.section and n.box[1] + n.box[3] <= t[1] + 2
                 and t[1] - (n.box[1] + n.box[3]) <= 2 * t[3] + 8
                 and n.box[0] < t[0] + t[2] and n.box[0] + n.box[2] > t[0]]
        return max(above, key=lambda n: n.box[1]) if above else None

    # 1. inputs: the name is the label; a nameless input takes the text beside it.
    for n in live:
        if n.role in _INPUTS and n.value and n.value != n.text:
            label = n if n.text else beside(n)
            if label is not None:
                add(label, n.value, n, "control")

    # 2. choice groups (radio buttons / options sharing a parent): the selected one is the value.
    groups: Dict[int, List[Node]] = {}
    for n in live:
        if n.role in _OPTIONS:
            groups.setdefault(n.parent, []).append(n)
    for opts in groups.values():
        used.update(o.index for o in opts)
        names = {o.text for o in opts}
        used.update(n.index for n in free if n.text in names and n.section == opts[0].section)
        chosen = next((o for o in opts if o.selected), None)
        label = beside(min(opts, key=lambda o: o.index))
        if label is None and opts[0].parent >= 0 and nodes[opts[0].parent].text:
            label = nodes[opts[0].parent]                  # a named radio group / fieldset
        if label is not None and chosen is not None:
            add(label, chosen.text, chosen, "choice")

    # 3. tables: column header over each cell (first text in the cell).
    headers: Dict[Tuple[int, int], Node] = {}
    cells: Dict[Tuple[int, int, int], Node] = {}
    for n in live:
        if n.row is None or not n.text or n.index in used:
            continue
        if n.role == "columnheader" or (n.parent >= 0 and nodes[n.parent].role == "columnheader"):
            headers.setdefault((n.table, n.col), n)
        elif n.role not in _NOT_CONTENT:
            cells.setdefault((n.table, n.row, n.col), n)
    if not headers:     # no marked header cells: the first row of each table heads it
        first: Dict[int, int] = {}
        for (t, r, _c) in cells:
            first[t] = min(first.get(t, r), r)
        for key in [k for k in cells if k[1] == first[k[0]]]:
            headers[(key[0], key[2])] = cells.pop(key)
    for (t, r, c), cell in sorted(cells.items(), key=lambda kv: kv[1].index):
        head = headers.get((t, c))
        if head is not None:
            pairs.append(Pair(_label_text(head.text), cell.text, "column", head.index, cell.index,
                              cell.zone, cell.section, r))
            used.add(cell.index)
    used.update(h.index for h in headers.values())

    # 4. free text: right on the same row, else directly below (column-header layouts go below).
    def right_of(n: Node) -> Optional[Node]:
        b = n.box
        row = [m for m in free if m.index != n.index and m.zone == n.zone
               and m.section == n.section and _same_row(m.box, b) and m.box[0] >= b[0] + b[2] - 2
               and m.box[0] - (b[0] + b[2]) <= max_right_gap]
        return min(row, key=lambda m: m.box[0]) if row else None

    def below(n: Node) -> Optional[Node]:
        b = n.box
        col = [m for m in free if m.index != n.index and m.zone == n.zone
               and m.section == n.section and m.box[1] >= b[1] + b[3] - 2
               and m.box[1] - (b[1] + b[3]) <= 2 * b[3] + 8
               and (m.box[0] < b[0] + b[2] and m.box[0] + m.box[2] > b[0])]
        return min(col, key=lambda m: (m.box[1], abs(m.box[0] - b[0]))) if col else None

    for n in sorted(free, key=lambda n: (_cy(n.box), n.box[0])):
        if n.index in used:
            continue
        r, d = right_of(n), below(n)
        if r is not None and d is not None:
            rd = below(r)
            if rd is not None and _same_row(rd.box, d.box) and not _looks_label(d):
                r = None                                   # labels over values, in columns
        for v, method in ((r, "right"), (d, "below")):
            if v is not None and v.index not in used and not _looks_label(v):
                add(n, v.text, v, method)
                break
    return pairs


def mark_furniture(nodes: Sequence[Node], frequent: Callable[[str], bool],
                   min_run: int = 3) -> List[Node]:
    """Portal furniture the page's markup did not label (a menu with no navigation landmark, a
    header drawn in the main area, any page read as plain lines) moves to the navigation zone, so
    it is never a pair, never page-kind evidence and never a fingerprint. `frequent(text)` says
    whether a text is one the portal shows on many different pages (the atlas counts that).

    Only a RUN of at least `min_run` frequent texts in reading order is furniture: a menu, a footer,
    a header strip. A lone frequent line ("Acknowledgement No", "Status") is a data label that
    many pages share and stays where it is. Empty container nodes do not break a run; any other
    zone does. Dialogs and steppers are never touched."""
    out = list(nodes)
    run: List[int] = []

    def close() -> None:
        if len(run) >= min_run:
            for i in run:
                out[i] = replace(out[i], zone=NAVIGATION)
        run.clear()

    for i, n in enumerate(out):
        if not n.text.strip():
            continue
        if n.zone in (MAIN, HEADER) and frequent(n.text):
            run.append(i)
        else:
            close()
    close()
    return out


def build_page_map(nodes: Sequence[Node], vocab: Optional[Dict[str, Tuple[str, ...]]] = None,
                   header_band_px: int = 100, max_right_gap: int = 500,
                   furniture: Optional[Callable[[str], bool]] = None,
                   furniture_min_run: int = 3) -> PageMap:
    """Zones -> furniture -> sections (and help) -> pairs, over nodes from nodes_from_uia /
    nodes_from_ocr. `furniture` is the atlas's "shown on many pages" test (see mark_furniture)."""
    zoned = classify_zones(nodes, vocab, header_band_px)
    if furniture is not None:
        zoned = mark_furniture(zoned, furniture, furniture_min_run)
    scoped, sections = scope_sections(zoned, vocab)
    return PageMap(tuple(scoped), tuple(sections), tuple(pair_by_layout(scoped, max_right_gap)))
