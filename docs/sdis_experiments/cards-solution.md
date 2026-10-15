# SDIS cards and rows: the solution design (experiment 3)

Written 2026-10-12 by the design agent, from the captures in `docs/sdis_experiments/captures/` (two ITR
"View Filed Returns" comp pages, Edge, two clients: `comp_20261012_121513` and `comp_20261012_121834`),
the three-browser test fixtures in `tests/class_diff_align/` and `tests/class_diff_tables/`, and the
code of experiment 2. Nothing in the project was changed; scratch scripts lived in `/tmp/sdis_cards_design`.
Counts, shapes, slot positions and pixel numbers only. No page value appears here.

## 1. Summary

**Verdict on the arrangement idea: adopt, with changes.** The core of the owner's idea holds up on the
captures: the leaf elements and the direction from each leaf to the next form a string whose repeats
are the cards, and the tree's wrappers play no part. Three things had to change to make it work:

1. The last step of a member points out of the member (to the next card, or to whatever follows the last
   card). It must be left free when members are compared, or a two-card page never repeats.
2. A repeat has no natural start: `ABC ABC` is also `BCA BCA`. The start (phase) is the rotation whose
   member boxes have the smallest total area. Likeness alone cannot do it: a window shifted by one leaf
   still matches 26 of 27 tokens.
3. Directions alone cannot tell a list of rows from the cards those rows sit in when the cards hold
   nothing but rows (the test fixture). The texts decide: the period is the smallest multiple at which
   some position shows the same text in every member (a label).

The solution in ten lines:

1. Leaves = nodes that carry a text or a look (Button, Edit, ComboBox, CheckBox, RadioButton, Image),
   with a non-empty box, present at the end of the capture. Document order. Tables are one leaf each.
2. Token = (kind, step to the next leaf), the step one of nine directions from the boxes.
3. Runs = tandem repeats of the token string (period ≥ 2), last position matched by kind only.
4. Members may differ by a few edits per period token (one optional link in a 27-leaf card).
5. Phase = the rotation with the smallest total member-box area; then runs ranked by leaves covered.
6. Nesting = a run inside one member of another run; the label witness merges rows into cards.
7. Slots = positions in the period; roles across members: label, distinct, mixed, look, ticking.
8. Kind = cards when a slot is a name (recurs across captures), list when the member count varies,
   fields otherwise. Fields and cards are compared slot by slot; lists by template and counts.
9. Across two pages: runs paired by their period, cards by name, fields by place, nested runs only
   inside the paired member. A value is known by the nearest label slot before it. Every value once.
10. The tree method is retired as the finder and kept as a witness column; `tables.py` stays as is.

On the real captures the prototype finds 5 cards of 27 leaves and 4 cards of 27/28/27/27, with the five
field rows and the three timeline steps nested in each card, in 11 to 21 ms per page. Three of the
seven open bugs (7, 8, 12) cannot be reproduced on these captures (no GST dashboard, no Firefox); they
are reproduced on fictional fixtures instead, where the tree method misses 2 of 5 tiles and the
arrangement finds all 5.

## 2. What the captures show

### 2.1 What exists and what is missing

| Capture | Page link | Browser | Client | Nodes | Leaves | Cards |
|---|---|---|---|---|---|---|
| `comp_20261012_121513` | ITR dashboard, View Filed Returns | Edge | #1 | 497 | 205 | 5 |
| `comp_20261012_121834` | the same | Edge | #2 | 429 | 179 | 4 |

Both comp pages: 20 snapshots, 1 s apart, anchor settled at +1.0 s, 0 attached, 0 gone by the end.
Nodes present=false: 1 (121513) and 0. Nodes without a box: 13 per page, all text-less Groups (four
with role=form). Node types: Group 260/222, Text 163/138, Hyperlink 29/28, Button 25/22, plus MenuItem,
Tab, ListItem, Image, ComboBox, List.

Missing cases, taken from fixtures instead: GST dashboard (bugs 1, 3, 5, 9, 14), Firefox and Chrome
reads of any real page (bugs 7, 8, 12), a div-row list page (list vs fields), a table page with two
clients (bug 4), a page with no repetition. The fixtures used: `tests/class_diff_align/client_{A,B}_{msedge,chrome,firefox}.json`
(the same fictional page read live in three browsers: 98 and 124 nodes, 52 and 69 leaves),
`tests/class_diff_tables/tables_{edge,chrome,firefox}.json` (251, 251, 257 nodes), and the fixtures
built inside `tests/test_class_diff_cards.py` (tile pages, top-level tiles, IFF/GSTR-1 trap).

Narrow tests: `tests/test_class_diff_cards.py` and `tests/test_class_diff_align.py` run here: 54 pass,
1 fails (`test_recurrence_report_gives_r_per_slot`). The failure is not in cards.py: `client_of()` imports
`core.sdis.identity`, whose import chain reaches `core/vsdc/vsdc_regex.py`, which uses `Any` in an annotation
without importing it (`NameError` at line 494) once `nameparser` is installed; without `nameparser` the
chain fails one step earlier. `tests/test_class_diff_render.py` cannot be collected on Linux: it imports
`tools.vsdc_uia_probe`, which needs `comtypes` (Windows only).

### 2.2 The shape of one card (shapes only)

A card is a Group at depth 4 with five children: a title block (1 text), a status column (2 texts,
label above value), a timeline Tab (3 steps, each an icon text, a title and a date), a field block
(5 rows of label and value, then 1 hyperlink) and an action column (1 hyperlink, 3 buttons). 27 leaves.
Card boxes: 1091 px wide, heights 365, 366, 344, 344, 345 (121513) and 344, 365, 345, 344 (121834).
Vertical pitch between cards: 388, 389, 367, 367 and 389, 388, 368. The 21 px difference is one
wrapped timeline title (a Text box 38 px tall instead of 18).

A wrapped text changes the tree: the timeline step whose title wraps gets one extra Group level
(`Group / Group / Text` instead of `Group / Text`). That is why experiment 2 reports the timeline as
2-member groups and gives the wrapped step's texts slots of their own (`own:` slots, 3 of 5 members).
Wrapped texts per page: 8 and 6 (of 136 and 114 Text leaves); all but the page heading sit in the
timeline. The leaf sequence does not change: 27 leaves either way.

### 2.3 Bug reproductions (counts)

**Bug 10, inner groups paired across cards.** `cards.find_groups` gives 14 and 13 groups: the 5/4-card
group, and inside every card a 5-member field-row group and a 2- or 3-member timeline group.
`match_group` pairs the inner groups by signature with ties to the earlier group: of 8 inner-group pairs,
**7 join rows of different cards** and 1 the same card (card names compared as equal/unequal only). With
`--rho 0.75` the card group becomes `cards` (192 rows paired by name) and the inner groups still produce
32 rows of their own (20 content-paired, 12 template): each field value is reported twice.

**Bug 11, label by shape.** Plain `--comp` on the two pages: fixed 98, semi-variable 20, variable 26,
variable_alignment 5, only-latest 31, only-previous 7, ambiguous flags 3. The 5 `variable_alignment`
rows are short status words (4 of shape len 4, letters only) that repeat in some cards and differ in
others: a mixed slot, not a label. Inside the cards, the label of a value sits to its **left** for the
five field rows (step R) and **above** it for the status column and the timeline dates (step D). A rule
"value right of its label" alone would miss the second kind.

**Bug 15, popups.** No hover popup in these captures. The one present=false node (121513) is a
one-digit Text in the header that left at +13 s: a digit of a ticking counter, not a popup. The design
cannot be checked against a real popup here.

**Bug 17, ticking text.** Nodes with 2+ values: 3 (121513) and 2 (121834), all one-digit Texts in the
same header Group (5 values each over 20 s). No node inside a card changed during either capture.

**Bugs 7, 8, 12 (Firefox / cross-browser).** Not reproducible here. On the fixtures: the fictional
page read in Edge, Chrome and Firefox has the same 98 nodes and the same 52 leaves in all three (the
page is too plain to show the wrapper problem); boxes differ by up to 12 px in x, 7 px in y and 252 px
in one width (Firefox). On a Firefox-shaped fixture built for this report (five top-level tiles, tile 3
without a wrapper, tile 4 with an extra inner Group around its link, plus four unrelated single-text
boxes) `find_groups` finds **3 of 5** tiles; the arrangement finds 5 of 5 (period 5, 4 of the run's
leaves lying loose at the top level) and the four boxes form no run (tokens ComboBox-L, Button-D,
Text-D, Text-DL: four different tokens).

**Bugs 9, 13, 14.** Need the dashboard captures; see the walk-through in section 4 for what the
design does on the fixtures.

### 2.4 Measurements for the arrangement

**Leaves.** 205 and 179 per page: Text 136/114, Hyperlink 28/27, Button 25/22, MenuItem 5, ListItem 4,
Image 3, ComboBox 2, Group 2 (a Group with its own name: one breadcrumb-like navigation Group and one
plain Group). Look-only leaves (no text): 1 per page (an Image). Text or look nodes without a box: 0.
A Text whose text only repeats its parent's (flatten blanks it) is not a leaf; its parent Hyperlink is.

**Steps between consecutive leaves** (121513 / 121834):

| step | R (same line, right) | O (same line, overlapping) | L | D (below, same x) | DL | DR | UR |
|---|---|---|---|---|---|---|---|
| count | 88 / 80 | 12 / 12 | 1 / 1 | 26 / 22 | 58 / 48 | 8 / 7 | 11 / 8 |

The 12 overlapping steps are a look inside a text leaf (Image in a Button, Hyperlink in a ListItem, Text
in a Hyperlink with a different text) and a ComboBox over a Text. They repeat identically in every member.

**The two tolerances the step needs, measured:**
- Same line or not: consecutive leaves either overlap vertically by **8 px or more** (101 / 93 pairs,
  minimum 9 and 8) or **not at all** (103 / 85 pairs, largest overlap 0). The empty band is 0 to 8 px.
  Middle: 4 px.
- Same column or not (for steps to another line): |dx| is 0 (21 / 18 steps) or 1 px (4 steps per page,
  a title at x 242 above a column at x 243), then nothing until 10 px. The empty band is 1 to 10 px.
  Middle: 5 px. (The prototype used 3 px, inside the band.)

Gaps between lines (D steps, bottom of one leaf to top of the next): 0 to 20 px inside a block (61 / 50
steps), 40 to 50 px between blocks of a card (12 / 12), 110 to 140 px between cards and sections (5 / 4).

**Exact tandem repeats of the token string** (kind + direction, no texts): 205 leaves give 20 distinct
tokens and 32 runs in 5 ms; 179 leaves, 21 tokens, 28 runs, 3 ms. On 121513 the biggest run is period
27 × 5 members, starting at the first card's title and ending at the last card's last button: exactly
the five cards. Inside its span: five runs of period 2 × 5 (the field rows) and five of period 3 × 2 or
3 (timeline steps). On 121834 the biggest exact run is period 27 × 2 only, and phase-shifted: one card
holds an extra leaf (a Hyperlink, inserted at position 22: 28 leaves), so the exact repeat breaks and
the maximal run slides into the neighbouring cards' tails.

**How alike true members are** (SequenceMatcher ratio of the token strings, last direction free):
true card pairs 1.000 on 121513 (10 pairs) and 0.982 to 1.000 on 121834 (6 pairs; the 28-leaf card
against 27-leaf ones). A card against a non-member window of the same length (header, nav, footer):
at most 0.296 and 0.333. A card against a window shifted by 1 to 26 leaves: up to **0.963**. In edits
per period token: true members 0 and 1/27 = 0.037; the nearest false candidates on small periods are
1 edit in 3 tokens = 0.333 (a 3-leaf timeline step against a 2-leaf field row). Widest empty gap:
0.037 to 0.333, middle 0.185. Two pages of one portal: provisional (section 5).

**Pixel spread of the repeat** across the 5 members of 121513, per position: x offset from the
member's box origin identical in all members (spread 0 at all 27 positions); y offset spread 0 at 25
positions, 10 to 22 px at 2 (below the wrapped title); width spread above 3 px at 7 positions (values
of different length, up to 31 px); height spread above 3 px at 1 position (the wrapped title, 20 px).
Member boxes: all 1040 px wide, heights 264 to 286. Directions are unchanged by any of this; only an
exact-pixel rule would break.

**Phase.** For the 27-period run, the rotation with the smallest total member-box area is rotation 0
(1 418 560 px², next 1 770 624) on 121513; on 121834 the seed itself is phase-shifted by 5 and the
area rule brings it back to the card title. A wrong-period seed (28 × 2, from the extra leaf) scores
1 191 640 against 1 126 320 for the right one once both are grown to 4 members: the area tells them
apart, the number of label positions too (0 against 13).

**Label witness on the fixture.** The fictional page's returns are div rows with no card wrapper in
UIA: the exact run is period 2 × 12 (3 cards × 4 rows) and 2 × 20. The smallest multiple at which a
position has the same text in every member is 8 (members 3 and 5, label at position 0): the cards.
Experiment 2 lumps the same rows with the profile block into one 13- or 21-member group.

**Slots and recurrence across the two clients** (card run, 27 positions): on 121513 18 label, 7
distinct, 2 mixed; on 121834 13 label, 7 distinct, 7 mixed (the fourth card lacks some texts, so slots
that are labels on one page are mixed on the other: a comparison must take the role from the union).
Distinct positions: 0, 5, 8, 11, 15, 19, 22 on both pages. Texts shared between the two clients, per
distinct position: position 0 shares 4 texts (r 0.80 and 1.00), position 19 shares 1 (0.20 / 0.25),
the other five share 0. Every node in every card showed one value during its capture: the "never
changes during a capture" rule separates nothing here.

**Cross-page pairing by position 0:** 4 card pairs, 1 only on 121513, 0 only on 121834; slot by slot
inside the pairs: 80 fixed, 28 variable, every value once.

**Cost.** Whole prototype (leaves, tokens, runs, phase, extension, nesting, roles): 11 to 21 ms for
497 nodes in plain Python. Tandem search is O(n²) comparisons of short slices; n is the leaf count
(about 40 % of the nodes).

## 3. The design, step by step

Words: a **leaf** is an element with a text or a look. A **token** is a leaf's kind and its step to the
next leaf. A **run** is a tandem repeat in the token string; its **members** are the repeats, its
**period** the token pattern. A **slot** is a position in the period. A **group** is an accepted run
with its kind: `cards`, `list` or `fields`. Nothing below knows a portal, a class name, a wording, a
language or a browser.

### 3.1 Step 1: leaves

From the flat list (`keys.flatten()` for a read, `nodes` of a comp page), in document order:
- keep a node when it has a text (`e["text"]`, so a text that only repeats its parent's is already
  blanked) **or** its type is one of Button, Edit, ComboBox, CheckBox, RadioButton, Image;
- and its box has a positive width and height (zero and missing boxes are text-less containers on
  these pages; a text with no box would be invisible and is dropped, counted in the report);
- and, on a comp page, `present` is true. A node that came after the anchor and left again is a popup
  or a tick and is not a leaf. A node that came and stayed is a leaf flagged `attached`.
- A leaf whose comp `values` holds 2+ texts is flagged `ticking` (bug 17). It stays a leaf (it holds
  a place in the arrangement) but it is never a name and is reported as `ticking`, not `variable`.
- Every Table subtree (`tables._is_table`) becomes **one leaf of kind Table** (its box is the table's);
  the rows come from `tables.extract()` as today and form a `table` group of their own, slots = column
  names. Nothing inside a Table enters the token string (bug 4 stays fixed).

Hover popups (bug 15) are only half covered: a popup open during the whole capture is present and
becomes leaves. The proper fix is a visibility flag in the read; until then the report lists the
`attached` leaves so the owner sees them.

### 3.2 Step 2: tokens

Token of leaf i = (kind, step), kind = the element type, step = the direction from leaf i's box to leaf
i+1's box:
- same line when the boxes overlap vertically by more than **OVL** px: `R` (next starts at or past this
  one's right edge, minus **TOLX**), `L` (next ends at or before this one's left edge), else `O`;
- otherwise `D` / `U` (next is below / above) with `L` / `R` added when its left edge differs from this
  one's by more than **TOLX**.
OVL and TOLX are measured (section 5): the current values are 4 px and 5 px.

The last leaf of the page gets step `END`. Document order is used, not reading order: a card has
several columns and reading order would shuffle them; document order is the DOM order and is the same
in Edge, Chrome and Firefox (measured identical on the fixtures).

### 3.3 Step 3: runs

1. **Seeds**: every maximal tandem repeat (period L ≥ 2, k ≥ 2 members, primitive, not extendable)
   where two consecutive windows are equal **except the last token's step** (kind must match). A
   period of one leaf is never a group: one leaf has no arrangement, only a direction, and a stack of
   unrelated texts would qualify (bug 9).
2. **Phase**: for each seed try the L rotations; take the one whose k member boxes (union of the
   members' leaf boxes) have the smallest total area; ties go to the earliest start.
3. **Extension**: grow the run left and right while a window of length L ± a (a = ⌊E × L⌋ edits,
   E measured) is within a token edits of the period (edit distance on the token lists, last step
   free); among the window lengths take the fewest edits. This catches the 28-leaf card and the fifth
   card whose last step leads out of the group.
4. **Ranking and acceptance**: order the grown runs by leaves covered (most first), then total member
   area (smallest first), then start. Walk the list: accept a run when it does not overlap an accepted
   run, or lies wholly inside one member of an accepted run (then it is **nested** in that member).
   Partly overlapping runs are dropped (a wrong-period seed loses to the right one on area).
5. **Label witness**: for an accepted run with equal-length members, find the smallest divisor m of
   the member count at which some position holds the same text in every merged member of m rows; merge.
   Rows become the cards they belong to when the cards have labels; anonymous rows stay rows.

### 3.4 Step 4: slots, roles

- Each member is aligned to the period (the same edit alignment as in 3.3): a leaf that matches a
  period position takes that slot; an inserted leaf gets an `own:` slot (an optional field). A slot
  missing in a member is simply absent there.
- Roles per slot, across the members of the page: `label` (same text in every member that has it),
  `distinct` (all different), `mixed`, `look` (no text), `ticking`. The same definitions as plan §5.
- Nested runs: their members become **sub-slots** of the parent member; the parent's positions stay
  the slots used for comparison (section 3.6 says when a nested run is a list instead).

### 3.5 Step 5: kinds, and the name slot

For each group, in this order:
1. **cards** when the group has a **name slot**: a slot `distinct` on every capture of the link and
   `ticking` nowhere, whose texts recur across captures (3.5.1). Members are things with an identity.
2. else **list** when the member count differs across the group's occurrences: across the members of its
   parent group on the same page (a nested run with 5 rows in one card and 7 in another), or across
   the captures of the link. Rows are anonymous.
3. else **fields**: the same count everywhere. Each slot is a field, paired by place.

Lists do not pair rows; fields and cards do. The ITR field rows (5 in every card, labels distinct within
a card and constant across cards) are fields; the timeline (3 steps everywhere) is fields; the fixture's
returns (3 rows against 5) are a list; its cards have no name slot in two captures of two clients
(nothing recurs), so they are a list of cards compared by template.

**3.5.1 What replaces ρ.** Experiment 2's r is the share of one page's texts that appear on another
page: it depends on how many cards each page has (bug 13). Replace it by one number per slot over the
**whole corpus** of captures of the link (all clients, all periods):

    R = 1 − (distinct texts − members of the largest capture) / (occurrences − members of the largest capture)

R is 1 when the slot's vocabulary is closed (the same names on every capture), 0 when every capture
brings new texts, and it does not depend on which page is "this" one. Two captures of 2 and 5 tiles
sharing one name give R = 0.5; a third 5-tile capture lifts it to 0.86 while values stay at 0. The
cut-off on R is measured by the widest-gap rule over all distinct slots of all links, once there are
captures of at least three periods or clients per link; until then the name slot is `unknown` and the
group is compared as a list, exactly as today. On the ITR captures: position 0 has R = 1.00, position 19
R = 0.25, five positions 0.00.

Tie between two name candidates (bug 14): the earliest position in the period wins (a title comes
before its description). The "a name never changes during one capture" rule is kept as a necessary
condition (a ticking slot is never a name), not as the decision: on these captures every card slot is
stable, so it decides nothing.

### 3.6 Step 6: comparing two pages

1. **Pair groups** top-down. Two top-level groups pair when their periods are within E edits per token
   of each other (the same rule as 3.3) and their parents pair (top level, or the same member of paired
   parents); best edits first, each group once, ties by page order. Unpaired groups: `only latest` /
   `only previous`.
2. **cards**: members paired by equal name text. Different or missing name: not paired (the IFF /
   GSTR-1 trap). Inside a pair, slot by slot: fixed / variable / only-one-side, and for nested groups
   recurse **inside the pair only** (bug 10): a field row of card X is never compared with card Y's.
3. **fields**: slot by slot, by place.
4. **list** and **table**: template (slot, role, first values, counts) and member counts, no row pairing.
5. **The rest of the page**: the leaves outside every accepted group, with one placeholder leaf per
   top-level group (shape = the period, text = ""), through `align.align()` as in plan §6.4, so no group
   text anchors anything outside it.
6. **Label of a value** (bug 11): inside any group, a value's label is the nearest `label` slot before
   it in the member (the step from that label to the value, R or D, is recorded in the CSV as
   `label_dir`). `variable_alignment` is never applied inside a group. Outside groups it stays until the
   same nearest-label rule is applied there too (it can be, with the same leaves and steps: a page-level
   field block is a fields group of one member; this is a later step).
7. Every leaf is reported once: the card rows carry the field values; nested fields groups produce no
   rows of their own.

### 3.7 Cross-browser (bug 12)

The token string uses the DOM order and the direction classes only. On the fixture read in Edge, Chrome
and Firefox the strings are identical (52 and 69 tokens, 0 edits) while the boxes move by up to 12 px
and one width by 252 px. The two tolerances (OVL, TOLX) sit in bands wide enough for that. What can
still break: a text that wraps in one browser and not in the other changes a step from R to D (one
label and value on one line against two lines), and Firefox exposes some containers as leaves with a
name (the tables fixture shows 4 ListItems and 6 extra nodes in Firefox). Both are measured, not
assumed: section 5.

### 3.8 The tree method

Retired as the finder: it is the source of bugs 7, 8, 9, 12 and of the wrapped-text slot splits (2.2).
Kept: `--histogram` and `find_groups` for one comparison column in the shape report (`tree agrees:
yes / no / partial`, the share of a group's leaves that the tree method puts in the same group), until
the owner has seen the overlays of a few more pages. `tables.py`, `align.py`, `keys.py` are used as they
are; nothing in `core/` changes.

### 3.9 Determinism and cost

Every step sorts explicitly (document order, then numeric ties); no dict or set iteration decides
anything. Shuffled JSON key order gives the same output (to be tested as today). Cost: leaves O(n),
tokens O(n), tandem seeds O(n²) slice comparisons on the leaf count (205 leaves: 5 ms), phase
O(L × k) box unions per seed, extension O(k × L²), ranking O(runs × log). Measured 11 to 21 ms per
497-node page. A 500-node page with 250 leaves stays well under 100 ms.

## 4. Bug by bug

| # | Bug | What the design does | Checked on |
|---|---|---|---|
| 1 | IFF tile paired with GSTR-1 | Cards pair by equal name text only; the shared description is a `label` slot and never a name. Prototype on the trap fixture: 1 pair (the shared tile), the IFF tile unpaired. Stays fixed. | fixture |
| 2 | Top-level Groups never checked | There is no tree: leaves are leaves wherever they hang. The top-level tiles fixture gives one run of 5. Stays fixed. | fixture |
| 3 | A class made a tile unlike the others | Tokens hold kind and step only; classes are never read. Stays fixed. | by construction |
| 4 | Table cells as fake cards | A Table is one leaf; rows come from `tables.extract()`. Stays fixed. | tables fixture (3 browsers) |
| 5 | Two groups shared an identity | Groups pair by period likeness and parent, each once (3.6.1). Stays fixed. | fixture |
| 6 | Client labels guessed | Unchanged: the client comes from the page (`identity.client_ids`). R (3.5.1) counts only other clients' captures, as today. Stays fixed. | — |
| 7 | Firefox drops a tile's wrapper | Wrappers play no part. Fixture: tree method 3 of 5, arrangement 5 of 5 with 4 leaves loose at the top level. **Solved**, to be confirmed on a real Firefox dashboard. | fixture |
| 8 | Firefox adds an inner Group | Same: the extra Group is not a leaf. Fixture: found. **Solved**, same caveat. | fixture |
| 9 | Four unrelated single-text boxes grouped | A one-leaf period is never a run; four boxes with four different tokens form nothing. Their texts go through the normal compare again. **Solved** on the fixture; the real dashboard must confirm the four boxes do not happen to share a token. | fixture |
| 10 | Inner groups paired across cards, values twice | Nested groups are compared only inside the paired card; nested fields produce no rows of their own. Prototype: 4 card pairs, 80 fixed + 28 variable slots, each value once (against 7 of 8 inner groups cross-paired today). **Solved**. | real captures |
| 11 | Label guessed from shape | Inside groups: label = nearest label slot before the value, direction recorded (R for the 5 field rows, D for the status column and timeline dates). `variable_alignment` not applied inside groups. **Solved inside groups; outside groups unchanged** until the page-level fields step (3.6.6). | real captures |
| 12 | Chrome and Firefox trees differ | Tokens are the same across the three browsers on the fixture (0 edits); pixels differ but stay inside the measured bands. **Solved for the tree shape; the wrap case stays a risk** (section 6). | fixture |
| 13 | ρ cannot be chosen | r replaced by the corpus-wide R (3.5.1), independent of the page's card count; cut-off measured by the widest gap once three captures per link exist; `unknown` until then, as today. **Reframed, not yet measured**: 2 captures give R = 1.00 against ≤ 0.25 on the ITR page. | real captures |
| 14 | Name slot tie by sort order | Earliest position in the period wins. **Solved** (deterministic and meaningful). | — |
| 15 | Hover popups in reads | Nodes that came and left are not leaves; nodes that came and stayed are flagged `attached`. A popup open for the whole capture still gets in. **Not solved**: needs the visibility flag in the read. | real captures (no popup present) |
| 16 | Renderer page very wide | Renderer only; outside this design. The overlay (WP5) draws member boxes from the leaf boxes, which cannot widen the page. **Not solved here.** | — |
| 17 | Ticking text keeps every snapshot | A leaf with 2+ values is `ticking`: never a name, reported as ticking, not variable. The snapshot decision itself stays the owner's. **Handled, not decided.** | real captures (3 and 2 ticking leaves, all header digits) |

## 5. Measurements still needed, and the rule for each cut-off

| Cut-off | Where it acts | Rule | Measured so far | Still needed |
|---|---|---|---|---|
| OVL (same line) | step 3.2 | vertical overlap of consecutive leaves; histogram over all pages; the cut is the middle of the widest empty band between "overlap" and "no overlap" | band 0 to 8 px on two ITR pages (4 px) | dashboard (Chrome, Firefox), a table page, a list page |
| TOLX (same column) | step 3.2 | \|dx\| of steps to another line; widest empty band above the jitter | band 1 to 10 px (5 px) | same pages |
| E (edits per token) | extension 3.3.3, group pairing 3.6.1 | edits / period length between true members (from the exact seeds) against the nearest non-member window; widest gap | 0.037 against 0.333 (0.185) on two pages | every new capture; a dashboard with an optional element on one tile is the important case |
| R cut (name slot) | 3.5.1 | R over all distinct slots of all links; widest gap | 1.00 / 0.25 / 0.00 on one link, two clients | at least three captures per link, two clients and two periods |
| period-1 runs | 3.3.1 | definition, not a cut-off | — | confirm on the dashboard that the four unrelated boxes form no run |
| count variation (list) | 3.5.2 | definition | ITR fields 5 everywhere; fixture rows 3 against 5 | a page where a card holds a real list of rows |

Each is measured with a `--steps` / `--likeness` / `--recurrence` report (WP1, WP2, WP6) that prints
histograms only. If a band is not empty, the report says so and the step refuses to run with that
cut-off, as `THETA` does today.

## 6. Risks and failure cases

- **Identical layouts, different things.** The arrangement cannot tell IFF from GSTR-1; only the name
  text can. With one client or one period there is no recurrence, so the group is a list and nothing
  is paired. Known and accepted.
- **A wrap that differs between browsers or clients** turns an R step into a D step and breaks a token.
  One token in 27 is within E; one token in a 2-leaf row is not. Lists of short rows are the exposed case.
- **Two groups with the same period side by side** (a row of buttons after a row of buttons) merge into
  one run. The label witness or the gap between them (110+ px against 0 to 20 inside a block) could
  split them; not built until a page shows it.
- **Anonymous rows with an optional leaf**: with no labels, a wrong phase can win when the extra leaf
  shifts the period by one slot. The area rule handled the ITR case; a list without labels is untested.
- **Period-1 runs are never groups**: a list of single values (one ARN per line) goes through
  `align()` as look-alikes, with align's known limit.
- **A list whose rows all share a value** makes that value a `label`. Harmless for the compare (fixed
  text), misleading in the shape report.
- **Popups open for the whole capture** still enter the leaves (bug 15).
- **The corpus R needs captures the owner does not have yet**; until then cards are lists, as today.
- **Document order is not the screen order** when a portal renders a column out of order (a sticky
  side panel placed first in the DOM); the directions then show U steps, which repeat the same way in
  every member, so runs still form, but the "nearest label before the value" rule can point at the
  wrong label. To be watched in the overlays.

## 7. Prior art

- **RoadRunner** (Crescenzi, Mecca, Merialdo 2001): infers the template by aligning two pages of the
  same site; what differs is data. The same idea as `align.py` and the cross-page roles here. Failed for
  them: exponential search over HTML tag sequences, no notion of a record's box. Borrowed: template vs
  data from two pages; labels as texts constant across pages.
- **MDR / DEPTA** (Liu, Grossman, Zhai 2003; Zhai, Liu 2005): data regions are consecutive sibling
  subtrees whose tag strings are within an edit-distance threshold (0.3, picked); DEPTA adds the
  rendered boxes to delimit records and partial tree alignment to line up optional fields. Closest to
  experiment 2 and to this design. Failed for them: the tree (wrapper differences break the sibling
  rule, exactly bugs 7 and 8) and the picked threshold. Borrowed: tandem repeats with an edit allowance,
  alignment to a template for optional fields, record boxes that do not overlap (the phase rule).
- **VIPS** (Cai, Yu, Wen, Ma 2003): blocks from visual separators (gaps) with a coherence threshold
  and thirteen hand rules. Failed: the rules are heuristics and need DOM plus CSS. Borrowed: gaps
  between blocks are larger than gaps inside them (measured here: 110+ px against 0 to 20), kept as a
  possible splitter, not a rule yet.
- **ViNTs / ViPER** (Zhao et al. 2005; Simon, Lausen 2005): records from visual signatures of content
  lines, and ViPER's primitive tandem repeats over tag sequences weighted by the elements' boxes and
  the page's visual centre. Failed: built for search-result pages with one main region. Borrowed:
  primitive tandem repeats as the seed, the area of the repeat as the tie-breaker.
None read UIA; all read HTML. Here the leaves are what UIA exposes, which is why wrappers cannot be
trusted and why the leaf string, not the tree, is the input.

## 8. Build plan

Every package: pre-dev only (`tools/pre_dev/class_diff/`), fictional fixtures built in the test file,
counts-only console, determinism test (same input twice, shuffled key order), run the narrow tests the
project way. A new module `arrange.py` beside `cards.py`; `cards.py` keeps the tree method for the
witness column.

**WP1 Leaves and tokens.** `arrange.py`: `leaves(flat, docs)`, `step(box_a, box_b)`, `tokens(flat,
leaves)`, Table placeholder leaves, `attached` and `ticking` flags. CLI `arrange.py --steps FILE...`:
histograms of vertical overlap and |dx| (counts only). Tests: leaf selection (zero box, present=false,
blanked text, look-only Image, Table placeholder), the nine step classes at their boundaries, document
order kept, Chrome / Firefox / Edge fixture strings equal. Measurement: OVL and TOLX from `--steps` on
every capture in `output/`, written into a `## Decisions` section of a new `docs/cards_rows/arrangement.md`.

**WP2 Runs.** `tandem_runs(tokens)` (relaxed last step, period ≥ 2, primitive, maximal), `phase()`,
`extend()`, ranking and acceptance with nesting, the label witness. CLI `arrange.py --likeness`: edits
per token between seed members and against non-member windows (histogram), and `arrange.py FILE`: the
shape report (runs, members, lengths, nested, boxes). Tests: the ITR-like page (5 cards of title +
status column + 3-step timeline + 5 field rows + link + 3 buttons; one card with an optional link; one
wrapped title giving a taller box); the Firefox-shaped page (wrapper-less tile, extra inner Group,
four unrelated boxes); the two-tile page (relaxed last step); the phase trap (identical cards, seed
starting mid-card); rows-only cards (label witness merges 2 × 12 into 8 × 3); a page with no repeat;
a Table page. Measurement: E from `--likeness` on all captures.

**WP3 Slots, roles, kinds.** `slots()`, `roles()`, `kind()` (name / count variation / fields), nested
sub-slots, the corpus R (`recurrence()` rewritten) with `--recurrence` printing R per slot per link.
Tests: optional leaf gets an `own:` slot; roles; a nested run with 5 rows in every card is fields, with
5 and 7 is a list; name slot by R with a test value; tie goes to the earliest position; a ticking slot
is never a name; same-client captures do not make names. Measurement: R over all links once the
dashboard captures exist; the cut-off written down or "not set" with the histogram.

**WP4 Compare.** `compare.py --comp --arrange`: group pairing by period, cards by name, fields by
place, lists by template, nested groups inside pairs only, the rest through `align()` with
placeholders, `label_dir` and `ticking` columns, every leaf once. `--cards` kept unchanged until the
owner retires it. Tests: the IFF / GSTR-1 trap; 2 tiles against 5; same card with a different status;
the ITR-like pair where the inner rows must never cross cards and no value appears twice; a list 3
against 7 rows; group text never an anchor outside its group; the fixture clients A / B in three
browsers give the same pairs. Measurement: on the real pair, counts of fixed / variable / only-one-side
and of rows against today's `--cards`.

**WP5 Overlay.** `render.py --arrange`: member boxes, nested member boxes, name slot and label-to-value
arrows (R / D) from the leaf boxes; no page widening. Test: box elements present, off by default.
Owner checks by eye.

**WP6 Retire the tree finder.** `cards.py` reduced to `--histogram` and the witness column; plan
`docs/cards_rows/plan.md` gets a pointer to `arrangement.md`; `progress.md` updated. After the owner
has seen the overlays of the dashboard in two browsers.

## 9. Questions for the owner

1. **Captures.** The design is measured on one portal page in one browser. Before WP2's cut-offs are
   fixed, capture: the GST returns dashboard in Chrome and in Firefox (bugs 7, 8, 9, 12), a third client
   of the ITR page, one client in two periods (R), a page with a real list inside a card (list against
   fields). Recommendation: do this first; the build can start with the test values meanwhile.
2. **Fields paired by place when counts are equal.** A list with the same row count on every capture
   is compared as fields (row 1 with row 1). Alternative: treat a nested run as a list whenever its
   rows carry their own repeated label (`Period` in every row). Recommendation: keep "count varies" as
   the only list rule until a page shows the other case; it is simpler and the ITR fields need it.
3. **The tree method.** Keep it as a witness column for a while, or drop it in WP6 straight away.
   Recommendation: keep it until the dashboard overlays in two browsers have been checked by eye, then
   drop it.
4. **Period-1 runs.** Never a group (this design) or a list when the leaves sit on a lattice.
   Recommendation: never a group; a column of single values goes through `align()`.
5. **The failing recurrence test and the Linux collection failures** are outside this work: `Any` not
   imported in `core/vsdc/vsdc_regex.py` (line 494), `nameparser` and `comtypes` missing here.
   Recommendation: fix the import on the main branch; the other two are environment only.
