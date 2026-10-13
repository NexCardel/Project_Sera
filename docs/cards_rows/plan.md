# Cards and rows: plan (SDIS pre-dev experiment 2)

Find the parts of a page that **repeat** (tiles/cards, list rows, table rows), then compare two
pages **card by card** instead of as one long list. Pre-dev only: everything lives in
`tools/pre_dev/class_diff/`. Nothing in `core/` changes (`core/sdis/align.py`, `keys.py`,
`tables.py` are shared with production: import them, never edit them).

Rules that hold everywhere (the owner's):
- **Fix the kind, not the case.** No portal, class name, wording or language is known to the code.
  Shapes, boxes, texts and counts only.
- **No quick heuristics.** Where a number is needed (a similarity cut-off), it is **measured** on the
  real captures and written down in §9 with the numbers that justify it.
- **Real captures stay on this PC** (`tools/pre_dev/class_diff/output/`, git-ignored). Console and
  reports show counts and shapes, never page text, except files written into `output/`.
  Test fixtures are fictional.

## 1. The problem it fixes (seen 2026-10-11)

GST returns dashboard, two captures. Mid-quarter month: tiles *IFF* and *GSTR-2A*. Quarter-end month:
*GSTR-1, GSTR-1A, GSTR-2B, GSTR-3B, GSTR-2A*. The IFF and GSTR-1 tiles share their description
("Details of outward supplies of goods or services"). `align.py` treats the page as one long
list: that shared text became an anchor, and the tiles' insides were then paired by shape:
`GSTR-1 ↔ Invoice Furnishing Facility`, `Status- ↔ End Date -`, `Filed ↔ 13/09/2026`. All wrong.
A card must be matched **as a whole**, by what it is, before its fields are compared.

## 2. Inputs and words

- Input: a comp page (`comp_*.json`, `nodes` list; `parent` indexes the list; the raw node is
  `nodes[i]["node"]` with `rect`, `name`, `ctype`, `cls`…), or a single read (`key_probe_*.json`) /
  capture snapshot via `keys.flatten()`. Write one loader that gives the same flat list for all.
- **Group**: a set of ≥2 sibling subtrees that are built alike (§3). Each subtree is a **member**.
- **Kind** of a group: `table` (rows of a Table element), `list` (members are anonymous rows), or
  `cards` (members are named things that recur across pages). Decided in §5.
- **Slot**: a node's place inside its member (§4). Every member of a group has the same slots,
  give or take the optional ones.

## 3. Step 1: find groups (one page)

Work bottom-up over the tree (children before parents) so nested groups are found first.

1. **Tables first.** For every Table element use `core.sdis.tables.extract()`. Its body rows become
   one group of kind `table`; a cell's slot is its **column name** (`tables.column_names`).
   Do not re-detect rows inside a Table with the generic rule.
2. **Signature of a subtree.** For a node M, `sig(M)` = the set of **relative shapes** of the nodes
   under M that carry text or a look (Button, Edit, ComboBox, CheckBox, RadioButton, Image,
   Hyperlink). A relative shape is the path from M down to the node, each step written as
   `Type.cleaned-classes` (classes through `keys.clean_classes`, so state tokens like `active`,
   `ng-dirty`, learned ones, drop out), **without** sibling counters.
3. **Collapse wrappers.** Before comparing, walk down from M while M has exactly one child and no
   text of its own: the member's root is the first node with ≥2 children or a text. A list item
   wrapped in two plain divs and one wrapped in one div then look the same.
4. **Siblings that are alike.** For each parent P with children C1…Ck (k ≥ 2), compare every pair:
   `like(Ci, Cj)` = same root element type AND `jaccard(sig(Ci), sig(Cj)) ≥ θ`. Build the
   connected components of `like`. A component with ≥2 members, each holding at least one text, is a
   group. (θ is measured: §8.)
   - Not contiguous is fine (a heading between two cards does not split them).
   - Two different components under one parent are two groups (a row of buttons and a row of cards).
5. **Geometry as a second witness, not a gate.** For each group record whether members sit on a
   lattice: same width and stacked (rows), or same height and side by side / wrapping (cards). Put it
   in the report; it does not create or kill a group.
6. **Nesting.** A group found inside a member of another group keeps a link to it
   (`parent_group`, `parent_member`): rows of a table inside a card belong to that card.
7. **Not a group:** a parent's single child; siblings whose signatures are empty; the page's top
   level sections (header / nav / footer differ in signature, so the rule already leaves them out:
   test it, §7).

## 4. Step 2: slots (one page)

- The slot of a node inside member M = its path from M's root **with** the sibling counters inside
  the member kept (`Group.f-wt / Text[1]` and `Text[2]` are different slots: label vs value), and
  classes cleaned as in §3.2.
- Members of one group can differ (an optional tag, an extra class that `clean_classes` does not
  drop). Line up each member's text nodes against the group's **template** = the member with the
  most slots, using `align.align()` on the relative shapes. A node that pairs with a template slot
  takes that slot; one that pairs with nothing gets its own slot (optional field).
- For `table` groups the slot is the column name (§3.1).

## 5. Step 3: what each slot is, and what kind the group is

Per group, per slot, collect the texts of all members.

**On one page:**
- `label`: the same text in every member that has the slot ("Status-", "VIEW").
- `distinct`: a different text in every member (no repeat within the page).
- `mixed`: some repeat ("Filed", "Filed", "Not filed").

**Across pages** (every comp page in `output/` for the same page link; a different client or a
different period both count): for each `distinct` slot, the **recurrence** `r` = share of its texts
that also appear in the same slot on at least one *other* capture.
- The slot with `distinct` on every page and the highest `r` is the group's **name slot**, if
  `r ≥ ρ` (measured, §8). The group is `cards`: members are named things (GSTR-1, GSTR-2A…).
- No such slot: the group is `list` (rows are anonymous: one client's row 3 is not another's
  row 3). Tables are `table` (a list with column names).
- Every other slot: `label` stays label; `distinct` and `mixed` slots are **values**.
- With only one capture of the link there is no recurrence: the kind is `unknown`, reported, and
  compared as `list` (§6). The report says how many captures it was based on.

## 6. Step 4: compare two pages with groups

1. **Pair groups** between the two pages: same parent shape (the parent's key without counters)
   and best `jaccard` of the union of member signatures; each group used once; ties broken by
   page order. Unpaired groups are reported as only-one-side.
2. **Cards: pair members.**
   - Name slot known on both: equal name text ⇒ same card. Different or missing ⇒ not paired.
   - No name slot (unknown): score = slots with equal text / slots present in either member; pair
     only if the two share **more than they differ** (score > 0.5), best overall assignment
     (small n: exact assignment, deterministic tie-break by member order).
   - Unpaired members: `card only latest` / `card only previous` (a tile one period does not show).
   - Inside a paired card: compare slot by slot → fixed / variable as today.
3. **Lists and tables: do not pair rows.** Compare the **template**: one row per slot with its role
   (label / value), its values from both pages (first few + count), and the row counts on each
   side. Every value slot is a datapoint column.
4. **The rest of the page** (outside every group): run the existing `align.align()` on the flat
   list with each group's members **removed** and one placeholder node per group put in its place
   (shape = group signature, text = ""), so anchors around the groups still line up and nothing
   inside a group can become an anchor for something outside it (the IFF/GSTR-1 trap).
5. `variable_alignment` is **not** applied to nodes inside groups (their pairing is no longer a
   guess). Outside groups it stays as today.

## 7. What to build

- `tools/pre_dev/class_diff/cards.py`: `find_groups(flat) -> groups`, `slots(group)`,
  `classify(groups_by_capture)`, `compare_groups(a, b)`; CLI:
  `python tools/pre_dev/class_diff/cards.py [FILE]`: the groups of a page as a shape-only report
  (kind, member count, slots with role, lattice, nesting), and `--html` → `output/cards_<file>.html`
  using the renderer (`render.py`) with an overlay: each group outlined, each member boxed, the name
  slot marked. Add the overlay to `render.py` as an option (`--groups`), not a copy of it.
- `compare.py --comp --cards`: the card-aware compare of §6. Without `--cards` the output stays
  exactly as now (experiment 1 must keep working). New CSV columns: `group`, `group_kind`,
  `member`, `card_name`, `slot`, `slot_role`, `pairing` (name / content / template / page).
- Tests `tests/test_class_diff_cards.py` (fictional fixtures built in the test):
  - the IFF/GSTR-1 trap: two "tiles" with the same description, different code ⇒ not paired;
  - the same named card with a different status value ⇒ paired, status variable;
  - 2 tiles vs 5 tiles ⇒ shared ones paired, rest only-one-side;
  - list rows 3 vs 7 ⇒ no row pairing, template compared, row counts reported;
  - a Table ⇒ kind `table`, slots = column names (reuses `tables.extract`);
  - wrapper chains of different depth ⇒ still one group;
  - an optional slot present in some members only;
  - a state class on one member (`active`) ⇒ still the same signature;
  - nested group (rows inside a card) ⇒ linked to its card;
  - header / nav / footer siblings ⇒ no group;
  - outside-group alignment unchanged and no group text used as an anchor outside its group;
  - determinism: same input twice, and input with shuffled JSON key order ⇒ identical output.
  Run the suite the project way (CLAUDE.md: live T/F/S plugin; `--basetemp` needed here).

## 8. Measuring θ and ρ (and checking the result)

The owner captures; the builder measures. Needed captures (each with `key_probe.py --seconds 60`,
using the page so it changes):
1. GST returns dashboard: a mid-quarter month and a quarter-end month (same or different client).
2. The same dashboard for a second client.
3. A list page with div rows (not a Table), e.g. an ITR list page.
4. A table page with rows for two clients (e.g. GSTR-1 B2B summary).
5. One page with no repetition at all (a login / welcome page) as the negative.

Measure and write in §9 (counts only):
- the histogram of `jaccard(sig, sig)` over **all sibling pairs** on all pages. Expect two humps
  (unrelated siblings near 0, members near 1). θ = the middle of the widest empty gap. If there is
  no clear gap, say so and stop: do not pick a number.
- the same for `r` over all distinct slots, for ρ.
- per page: groups found, kind, members, slots by role, and for each compare: cards paired by
  name / by content / unpaired, and how many rows changed status versus the current `--comp`.
The owner checks the `--html` overlays by eye.

## 9. Decisions (current status, rewritten 2026-10-11 after the review)

### 9.1 What is built
- `tools/pre_dev/class_diff/cards.py`: `find_groups`, slots, `roles`, `classify`, `recurrence`,
  `match_group`, `compare_groups`, `outside_flat`; CLI `--histogram`, `--recurrence`.
- `compare.py --comp --cards [--rho X]`: CSV `compare_cards_<session>__<page>.csv` with the §7 columns.
  `--rho` is an experiment knob for trying a cut-off before ρ is measured.
- `render.py --groups`: members boxed, distinct / name slots marked.
- Tests: `tests/test_class_diff_cards.py` (29), 71 with the render and align tests. All pass.

### 9.2 Fixes from the review (2026-10-11)
1. **Top-level siblings.** Children of the document (`parent = -1`) were never compared, so the
   dashboard's return tiles (direct children of the document) were never a group. Now each document's
   top level is one sibling set. Group identity for those uses `(document)`, never `fl[-1]`.
2. **Signature and slots are element types only.** `Type.classes` paths made one class on an outer
   element change every path under it: the disabled GSTR-1A tile (`disableClick`) scored 0.00 against
   the other tiles, and its fields got slots of their own. Signatures use type paths; slots use the
   `loose` key (types + sibling counters). Classes play no part in grouping or slots.
3. **No generic groups inside a Table** (§3.1): Table subtrees are left to `tables.extract()`. The
   B2B summary had 20+ two-cell "groups" from table rows before this.
4. **Groups are matched across pages by §6.1** (`match_group`: same source, same parent shape, same
   member root type, best signature jaccard), not by a loose identity string. On the dashboard two
   top-level groups had the same identity (`(document) || Group`) and blocked each other.
5. **One client rule.** `classify` and the recurrence report both count only OTHER clients' captures
   (`recurrence()`); the client is read from the page (PAN / GSTIN via `core.sdis.identity.client_ids`).
   `--clients` is only an override. Same-client captures make a group `unknown`.

### 9.3 θ = 0.4167 (measured)
15 files (9 comp pages, 6 single reads), 1467 sibling pairs with the same root type, 33 distinct values.
Widest empty gap 0.3333 to 0.5000 (0.167); next 0.9000 to 1.0000 (0.100). θ = the middle, 0.4167.
The same gap won before the fixes; it is now clearly the widest.

### 9.4 ρ: not set (tie)
Recurrence against other clients (`cards.py --recurrence "output/comp_*.json"`), every distinct slot:
r = 1.00 (menus, headings, tile codes and descriptions), 0.50 (a period dropdown both clients share;
one more slot), 0.00 (client-specific values). Two gaps of 0.50 each: the rule picks nothing.
`RHO = None`, so `--cards` compares every group as its template until it is set.

Two facts for the owner's decision:
- Any ρ in (0.50, 1.00] gives the same result on today's captures.
- r depends on how many cards each capture has (9.2 of the old notes): a 5-tile page against a 2-tile
  page of another client gives r = 1/5 for the codes. With more periods per client it rises. ρ near 1.0
  would make such pairs `list`.
- A possible sharper rule, not built: a name never changes during one capture, while a picked value
  does (a comp node keeps every value it showed: `values`). The 0.50 dropdown slot changed during the
  capture; the tile codes did not.

### 9.5 Result on the real captures (with `--rho 0.75`, to see the pairing)
- Dashboard, 5 tiles (ZS) vs 5 tiles (ZR): all five tiles paired by name, GSTR-1A included.
- Dashboard, IFF capture (ZS, 2 tiles) vs a 5-tile capture (ZR): GSTR-2A paired; IFF `only latest`;
  GSTR-1, 1A, 2B, 3B `only previous`. **The IFF ↔ GSTR-1 mispairing is gone.**
- GSTR-1B2B: the table is a `table` group; no generic groups inside it.

### 9.6 Known limits
- A false group at the document's top level on the dashboard: four single-text boxes (language picker,
  "File Returns", the news ticker, the mandatory-fields note) are alike by type paths. It is compared as a
  template, so nothing is mispaired, but those texts no longer go through the normal compare.
- When two slots have the same top r, the name slot is the first by slot name (deterministic, not
  meaningful). On the dashboard that can be the tile's description link instead of its code; pairing is
  the same here because the descriptions differ between tiles that differ.
- Hover popups (§10).

### 9.7 Measurement history (superseded)
Before the fixes, θ was measured without the top-level siblings and with class paths, and the client
labels were first guessed from text overlap (wrong) and then read from the GSTIN. Those numbers are
replaced by 9.3 and 9.4.

## 10. Known problem, not in this plan: hover popups

In `render_key_probe_20261011_115314__…dashboard.html` texts overlap because the owner's mouse was over
the menu: the menu's dropdown was really open during the read. Popups opened by hovering get into
reads and comp pages. Address separately (the comp page already keeps a `present` flag; a popup
that opens and closes is marked as left).
