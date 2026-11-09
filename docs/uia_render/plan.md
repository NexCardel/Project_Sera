# UIA render: plan

Build a script that turns any SDIS / class_diff raw-view UIA read into an HTML page that looks as
close as possible to the page the browser showed. It uses **only what the read holds**: the control
type, screen box, name, value, class names, ids, aria. It has no knowledge of any portal.

"Full consistency" means two things here:

1. **The same input always gives the same output, byte for byte.** Nothing depends on time,
   random numbers, the order of dict keys, or the machine.
2. **The same rules apply to every read, from every source and every browser.** No case-by-case
   fixes for one page ("fix the kind, not the case").

A working prototype exists: `docs/uia_render/prototype/` (`render_uia.py` + `render_uia_template.html`).
It rendered `capture_20261011_094427_1__return.gst.gov.in_returns_auth_gstr1_b2b_summary.json`
well (header, menu, breadcrumb, info block, buttons, 7-row table, footer all in place). Read
it first. The plan below is what it lacks.

---

## 1. Where it lives and how it runs

- Engine: `tools/pre_dev/class_diff/render.py` (was core/sdis/render.py; moved 2026-10-11, pre-dev tool), pure Python, no Qt, no UIA, no network. It imports nothing
  from `core.vsdc` at module import time (start-up rule: heavy imports only when needed).
- Pre-dev alias: `tools/pre_dev/class_diff/render.py`, the same alias pattern as
  `tools/pre_dev/class_diff/tables.py` / `align.py`.
- HTML template: inline in the module as one string constant, or `core/sdis/render_template.html`
  loaded with `importlib.resources`-style path logic. Pick one and keep it. No CDN, no external
  font, no external script: the page must open offline.
- CLI:

```
python tools/pre_dev/class_diff/render.py <file> [--snapshot N] [--doc N|all] [--record N]
                                                [--comp-view present|all] [--out PATH] [--open]
```

  - With no `--out`, the HTML goes next to the input: `render_<input stem without its prefix>.html`.
    Inputs from the recorder folder go to `tools/pre_dev/class_diff/output/` (never into
    `sdis_capture/`, which the excavator ships to the admin PC).
  - `--open` opens it in the default browser (`webbrowser.open`), off by default.
- Python API: `render_html(docs, meta) -> str`, plus `load(path, ...) -> (docs, meta)` per
  input kind. Tests call the API.

## 2. Inputs (detect the kind from the content, not from the file name)

| Kind | How to tell | Where the nodes are | Notes |
|---|---|---|---|
| key_probe capture | JSON with `"capture": true` | `snapshots[N]["docs"]` (list of docs, each a flat node list) | nodes carry `type_name`; default N = last |
| key_probe single read (`--once`) | JSON with `docs` at top level | `docs` | |
| comp page | JSON with `"comp_page": true` | `nodes[i]["node"]` (the raw node); `nodes[i]["parent"]` indexes `nodes` | one merged doc; see §4.6 |
| recorder record file | `.jsonl`, each line `{v:1, ts, session, url, link, title, browser, docs}` | `docs` of line N | no `type_name`: map `ctype` (§3); can be hundreds of MB, so stream lines, never `read_text()` the whole file |
| corpus file on the admin PC | same as recorder (`data_dir()/corpus/<device>/sdis_*.jsonl`) | same | |

`--record N` picks the line of a `.jsonl` (default: the last one). `--record list` prints one line per
record (index, ts, link, title, node count) and writes nothing.

Raw node fields seen in real reads: `parent, depth, ctype, name, rect [x,y,w,h], id, cls, role,
aria, help, desc, required, disabled, value, type_name, sgt, aid`. Every field except `parent` may be
missing. `rect` may be `null`, `[0,0,0,0]`, or have negative x/y (the page was scrolled).

## 3. Control types

Use `CONTROL_TYPE_NAMES` from `tools/vsdc_uia_probe.py` (as key_probe.py does) to turn `ctype`
into a name when `type_name` is missing. Do not hand-copy the table. Unknown ctype → the number as
text, and draw it as a plain container.

## 4. Rendering rules (all generic)

### 4.1 Coordinates
- Use each node's own `rect`. Skip nodes with no rect or width/height ≤ 0 (still keep them in
  the tree for the "show every box" overlay list and the node table, §4.8).
- Origin: subtract the minimum x and y over the drawn nodes of the rendered doc, so negative
  (scrolled) boxes become visible. Page size = max right / bottom after the shift.
- Never rescale: 1 UIA pixel = 1 CSS pixel. Show the page at 100%. The viewer scrolls.
- Nodes whose box lies fully outside their parent chain's union (a closed popup left in a comp page)
  are still drawn where UIA said.

### 4.2 Which text is drawn (no double drawing)
- A node's text is its `name`. If `name` is empty and `value` is set (Edit, ComboBox), the
  value is the text.
- Draw a node's text only when **no descendant** has a non-empty name ("innermost named node
  wins"). This is what the prototype does: a ListItem "Dashboard" holding a Hyperlink "Dashboard"
  holding a Text "Dashboard" draws once, from the Text.
- Exception to resolve and test: a parent whose name is LONGER than the join of its
  named descendants' names (text that sits directly in the parent, outside child elements). Draw the
  remainder? Decide by measuring on the real captures in `output/` (§7): count how often it
  happens and what the leftover looks like. Write the decision in this file.

### 4.3 Font size and fit
- A Text-type node: font size = round(box height × 0.78), minimum 8 px.
- Any other drawn node (Button, Hyperlink, DataItem without inner Text…): its box is often much
  taller than one line (the prototype drew menu buttons at 38 px until fixed). Use
  `min(box height, line height)` where line height = the **median box height of drawn Text
  nodes in this doc** (fallback 18).
- Then shrink in 0.5 px steps until the text width fits the box width + 1 px (min 7 px).
  This needs the browser, so it runs in the page's JS at load. It is deterministic for one browser.
  The Python output stays byte-identical.
- One line, `white-space: nowrap`. A box taller than 1.8 × line height with a long name may wrap
  instead (multi-line paragraph). Decide from measurement on the captures, as in §4.2.
- Text sits vertically centred in its box; horizontal alignment comes from the box (the box is
  already where the browser drew the text). No left/centre guessing.

### 4.4 Look by control type (no portal styling, no colours from class names)
| Type | Look |
|---|---|
| Button | 1 px border, 4 px radius, light fill, text centred |
| Hyperlink, and text inside a Hyperlink (up to the nearest Button) | link colour + underline |
| Edit | 1 px border, white fill, text = value; `required` → a small "*" after the box |
| ComboBox | as Edit plus a "▾" at the right edge |
| CheckBox / RadioButton | a small square / circle at the box's left; ticked state only if the read carries it (toggle/selection fields, if present) |
| Table | 1 px border |
| DataItem, HeaderItem, Header | cell border |
| Image | dashed border, hatched fill, name as tooltip (no pixels in a UIA read) |
| ListItem with class token `active`, or `aria` selected | tab outline (generic: "this item is the selected one") |
| `disabled: true` | 50 % opacity |
| Group, List, Pane, Document, Custom | not drawn (they appear only in the overlay) |

Icon fonts: a name made only of Private Use Area code points (U+E000–U+F8FF) is an icon. Draw a
small dashed box with the code (`U+F055`). Never map codes to icons: that is outside knowledge.

### 4.5 Paint order (deterministic)
1. Containers with a look (Table, DataItem, Button, Edit…): bigger area first, ties broken by node
   index.
2. Texts last, in node index order.

Never depend on dict or set iteration order. Sort explicitly.

### 4.6 Several docs, comp pages
- A read has several docs (Chrome often has empty ones; Firefox exposes background tabs as
  Documents). Default: render the doc with the most nodes **that have a non-zero rect**. `--doc all`
  renders each non-empty doc as its own section, one after another, each with its own origin, and a
  heading "doc k: n nodes".
- Comp page: `--comp-view present` (default) draws only nodes with `present: true`. `all`
  also draws nodes that left (closed dropdowns, popups) at their last box, in a muted style
  (dashed, 40 % opacity), and their texts. Nodes added later (`origin: attached`) get a thin
  coloured left edge in both views, so the attached parts are visible. The tooltip shows
  `origin`, `first`, `last`, `values` for comp nodes.

### 4.7 The page chrome
- Sticky top bar: page link, title, browser, source file name, snapshot/record index, node count,
  page size. Options: "show every UIA box" (outlines every node, including Groups), "show node
  index" (small index tag on each drawn node).
- Hover any drawn element: tooltip with type, `#id`, `.classes`, box, name, value, aria, key (if
  the input has keys), comp fields (§4.6).
- Light and dark theme for the chrome only. The rendered page itself is always white with dark
  text (it imitates a web page).

### 4.8 The node table (below the page)
A collapsible table of every node in the rendered doc: index, depth (indented), type, name, value,
box, classes, drawn yes/no and why not (no rect / zero size / text drawn by a descendant /
container without a look). Clicking a row highlights the node in the render. This is the
"consistency" check a person uses: every node is accounted for.

## 5. Privacy (hard rules)
- Reads hold real client data (GSTINs, names, amounts). The HTML is written only to
  `tools/pre_dev/class_diff/output/` (git-ignored) or a path the user gives. Never into `docs/`,
  `tests/` or any tracked folder.
- Never publish a render (artifact, gist, upload). Never put client data in logs or console
  output beyond counts and the output path.
- Test fixtures (§6) are fictional only.

## 6. Tests: `tests/test_sdis_render.py`
Fictional fixtures only (build node lists in the test, or reuse the fictional fixture approach of
`tests/class_diff_tables/`). Cover:
- each input kind of §2 is detected and loaded (capture, single read, comp, jsonl line N,
  `--record list`);
- jsonl is streamed (a file with 10 000 lines loads line 9 999 without reading the rest into
  memory: check with a generator spy or a size bound);
- determinism: rendering the same input twice gives identical bytes, and shuffling the JSON key order
  of the input gives identical bytes;
- innermost-named rule (ListItem > Hyperlink > Text draws once);
- negative-y origin shift; zero/null rect skipped but listed in the node table with its reason;
- missing `type_name` → name from `CONTROL_TYPE_NAMES`; unknown ctype → number;
- PUA icon → `U+XXXX` box, no other mapping;
- line-height rule: a Button 49 px tall gets font from the median Text height, not 49 × 0.78;
- comp view present vs all; attached marker;
- `--doc all` sections; default doc choice;
- no external URL anywhere in the output (`http://`, `https://`, `//cdn` absent);
- output path: never inside `sdis_capture/`.

Run the suite the project way (CLAUDE.md: live T/F/S plugin, background log; `--basetemp` needed
here, see the test-suite rule).

## 7. Measured check on the real captures (no new captures needed)
Run the renderer on every `capture_*.json` and `comp_*.json` in `tools/pre_dev/class_diff/output/`
and on the last record of one recorder `.jsonl` (this PC: `~/AmanAssociates_Sera/sdis_capture/`).
For each, report counts only (no page text in the report): nodes, drawn texts, skipped (by reason),
texts that needed shrinking by more than 30 %, overlapping drawn texts (two text boxes intersecting
by more than half the smaller one). Use those numbers to settle §4.2 and §4.3's open
points. Put the numbers and the decisions in this file under "## 9. Decisions". The user checks the
renders by eye against the real portal page.

## 8. Out of scope
- No screenshot, no pixel capture, no OCR: UIA data only.
- No change to the recorder, key_probe capture, SGT, the excavator or anything that runs in the app.
- No CSS from class names (Bootstrap or otherwise), no per-portal rules, no icon mapping.
- Card / list detection (the planned rows-cards experiment) is separate. Don't build it here.

## 9. Decisions

Built: `tools/pre_dev/class_diff/render.py` (engine + CLI; moved out of core/sdis on 2026-10-11, it is a pre-dev tool),
`tests/test_class_diff_render.py` (16 tests, fictional fixtures, all pass). The page's script measures
text in the browser (Edge, headless) and writes its counts to `body[data-fit]`; Python decides
everything else.

### 9.1 Measured on the real captures (counts only; `auto` doc; fit measured in headless Edge)

| input | view | nodes | texts | boxes | icons | overlaps | line h | fit: shrunk | shrunk >30 % | overflow at 7 px | of which tall |
|---|---|---|---|---|---|---|---|---|---|---|---|
| capture 003517 (dashboard) | present | 173 | 69 | 15 | 15 | 0 | 18 | 2 | 2 | 1 | 1 |
| capture 004748 (dashboard) | present | 199 | 78 | 19 | 13 | 0 | 18 | 4 | 4 | 1 | 1 |
| capture 094427 (B2B summary) | present | 281 | 100 | 75 | 16 | 0 | 18 | 2 | 1 | 1 | 1 |
| comp 003517 | present / all | 186 | 69 / 76 | 15 / 20 | 15 | 0 / 0 | 18 | 2 / 2 | 2 / 2 | 1 / 1 | 1 / 1 |
| comp 004748 | present / all | 211 | 78 / 84 | 19 / 23 | 13 | 0 / 2 | 18 | 4 / 4 | 4 / 4 | 1 / 1 | 1 / 1 |
| comp 094427 | present / all | 281 | 100 | 75 | 16 | 0 | 18 | 2 | 1 | 1 | 1 |
| recorder sdis_2026-10-09, last line | present | 191 | 75 | 18 | 13 | 0 | 23 | 3 | 3 | 0 | 0 |

- Skipped in the present view, by reason: container without a look 66–85 per read, text drawn by
  a descendant 28–46, zero size 2–8, left (not in present view) 12–13 (comp only).
- The 2 overlaps in comp 004748 `all` both involve a node that left the page (a closed popup drawn
  over live text). They appear only in `all`, as intended.
- Left nodes in `all` add 7 texts and 5 boxes (003517) and 6 texts and 4 boxes (004748).
- Active tabs (ListItem with class `active` or `aria selected=true`): 6 across the inputs.
- Drawn text by type across all capture and comp docs: Text 414, Button 62, ComboBox 13,
  ListItem 12, Hyperlink 6. Drawn boxes: DataItem 112, Button 84, ComboBox 17, Image 6, Table 2,
  ListItem 6. No Group, List, Pane, Document or Custom text was drawn.

### 9.2 Open points settled by the measurement

- **§4.2, parent text longer than its named descendants: no remainder is drawn.** In the three
  captures and their comp pages (both views), every parent that has drawn descendant text has a name
  equal to the joined text of those descendants (whitespace ignored): 30, 31 and 103 parents, zero
  mismatches. The record file was not checked this way. The innermost-named rule loses nothing on
  this data. Revisit only if a new read shows a parent with extra text.
- **§4.3, multi-line wrap: not built, nowrap kept.** At most 1 text per read overflows its box at
  the 7 px floor, and it is in a box taller than 1.8 line heights (the only wrap candidate). One
  case per read is not enough to write a wrap rule. The overflowing text stays in one line and
  runs past its box (the page does not clip it). The user checks that one text by eye.
- **§4.3, shrink: 2–4 texts per read shrink, and 1–4 of them by more than 30 %.** Each is a text
  wider than its box in the browser's font. The floor is 7 px.
- **§4.6, comp views:** `present` (the default) keeps the 12–13 left nodes out. `all` shows them
  at 40 % opacity with dashed boxes; attached nodes get a green left edge in both views.

### 9.3 Choices I made (the plan left them open or they differ from the plan's text)

1. **Output name keeps the kind prefix**: `render_capture_…`, `render_comp_…`, `render_sdis_…_line<N>`.
   The plan's rule (strip the prefix) made a capture and a comp page from one session write the
   same file; the second overwrote the first. A record line gets `_line<N>` so lines do not overwrite.
2. **`--record list` prints index, time, host and node count, not link or title.** §2 asked for the
   link and title; §5 says no client data on the console beyond counts and the path. §5 wins. The
   host is kept (domain only).
3. **Output folders**: recorder (`sdis_capture`) and corpus inputs, and `.jsonl` inputs, go to
   `tools/pre_dev/class_diff/output/`. `check_out` refuses any path under `sdis_capture` and any
   path inside the repo outside that output folder.
4. **`CONTROL_TYPE_NAMES` is imported lazily**, only when a read has no `type_name`:
   `tools/vsdc_uia_probe.py` imports `comtypes` at module load, which the engine should not pay for.
5. **A descendant takes over a parent's text only when it draws its own text** (in view, with a
   box, and with text). The plan's literal rule (any named descendant) would hide a parent's text
   behind a child that left the page or has no box. Same result on all current data.
6. **Text is `name`, or `value` when the name is empty** (any type, not only Edit and ComboBox).
7. **Groups, Lists, Panes and Documents draw their name as text but no box** (the prototype's
   behaviour). The plan's table said "not drawn", which could mean the text too. No such text occurs
   in the current reads, so the choice has no effect yet.
8. **Image draws a dashed box; its name is only in the tooltip** (plan 4.4). The node table still
   lists the name.
9. **CheckBox and RadioButton get a mark, not a tick.** A read carries no toggle state, so no tick.
10. **Positions are integers and the origin is shifted to the drawn boxes' minimum x and y.**
    The page is never scaled: 1 UIA px = 1 CSS px.
11. **Index numbers are 0-based**, as in the read's own `parent` field. `--snapshot`, `--doc` and
    `--record` use the same numbering.
12. **Determinism**: the Python output is byte-identical for the same input and for shuffled key
    order (tested). The fit step runs in the browser, so its result depends on that browser's fonts.
    Numbers in 9.1 are from headless Edge.
13. **Nothing the page's script does is written back to Python.** The fit counts go to
    `body[data-fit]` only, for measurement.
14. **Not run**: the full project suite. This change adds files and touches no existing module. The
    16 tests in `tests/test_sdis_render.py` pass under the live T/F/S plugin.

### 9.4 Still open

- The 1 overflowing text per read (9.2): check it by eye on the real portal page. If it looks
  wrong, that is the case for a wrap rule.
- Tick state for checkboxes and radios, if a future read carries it.
