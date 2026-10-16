# SDIS rework experiments: progress and open problems

Last updated: 2026-10-16 (experiment 3 built and run on new real captures: section 6.6). This file is written to be read **on its own**: it does not assume access to the
repository. File names are given only so that the owner can find things; a reader does not need to open
them. It holds no client values (counts, shapes and pixel numbers only).

If you are a consultant reading this: section 9 says what we want from you. The full design report for
experiment 3 (`cards-solution.md`, about 500 lines) may be uploaded alongside this file. Section 6 gives
the parts of it that matter here.

## 1. Background

**Sera** is a desktop app (Windows, Python) for a tax-practice office in India. Staff work on government
portals in an ordinary browser (Edge, Chrome or Firefox). These are the Income Tax e-filing portal (ITR)
and the GST portal, each holding the pages of many clients. Sera watches what is on screen and records the
datapoints of each client: return filed, acknowledgement number, filing date, status, and so on.

**How a page is read.** Sera reads the browser through **Windows UI Automation (UIA)**, not the HTML. A
UIA read is a tree of elements. Each element has a type (Group, Text, Hyperlink, Button, Edit, ComboBox,
Image, ListItem, Table, Tab and so on), a name (its text), an automation id, a class, and a **screen box**
(left, top, width, height in pixels). UIA is not the DOM:
- browsers drop or add wrapper elements differently (Chrome, Edge and Firefox give different trees for the same page);
- a parent's name is often its children's texts joined;
- there is no reliable "this is a table header" flag;
- background tabs can be exposed as well (handled elsewhere).

**SDIS ("Sera Distill")** is the engine meant to find a portal's datapoints by itself, so that nobody
has to hand-write a field spec for every page. The idea is **differential analysis**: compare the same
page (same link) across different clients. Text that is the same for every client is the template (labels,
furniture); text that differs is data. The owner then picks datapoints in a dialog. The datapoints are
ranked by plain maths and statistics.

**Capture.** While a page is open, Sera takes a UIA read about every second (a snapshot). A capture is all
the snapshots of one visit to one page. In experiment 1 the snapshots of a capture are merged into one
**comp page** (section 3).

## 2. The owner's rules (hard constraints on any solution)

1. **Fix the kind, not the case.** The code knows no portal, class name, wording, language or browser.
   Shapes, boxes, texts and counts only. A fix must work for every portal, not just the page in front of us.
2. **No AI, no machine learning, no cloud.** Plain maths and statistics: counting, voting, distances, alignment.
3. **No quick heuristics.** Every cut-off is **measured** on real captures with a stated rule. The usual
   rule is the middle of the widest empty gap in a histogram. If a measurement shows no clear separation,
   the method says so and refuses to pick a number. Patches such as "same value type + has a letter or
   digit" were rejected and reverted.
4. **Deterministic.** The same input always gives the same output.
5. **Prove it on real captures.** No engineered corpora, and "feed it more clients" does not count as a
   proof. Fictional test fixtures are allowed for cases the real captures lack, but they were designed by
   the same AI that wrote the method (a known weakness, P21).
6. **Privacy.** Production must not store client values. Reports and logs hold counts and shapes only.
7. Experiments are **pre-development tools** (`tools/pre_dev/class_diff/`). The production engine
   (`core/sdis/`) is not changed by them.

## 3. Experiments so far

| # | Experiment | What it does | State |
|---|---|---|---|
| 1 | **Comp page** | All snapshots of one capture are merged into one page. An anchor page is fixed once the loaded page sits still for a poll. Nodes that appear later are attached at the end, and nodes that leave keep `present = false`. Every node keeps the list of values it showed (`values`). | Built, committed (2026-10-07) |
| – | **UIA renderer** | Draws a raw UIA read as an HTML page from the screen boxes only, so a person can see what the engine sees. Pre-dev tool only. | Built |
| 2 | **Cards and rows, tree method** | In the UIA tree, siblings are grouped by likeness: Jaccard similarity of their element-type signatures ≥ θ. Each group is cut into slots, cards are paired across clients by a name slot, and lists are compared by template. | Built, reviewed and fixed 2026-10-11 |
| 3 | **Arrangement ("packing pattern")** | Ignores the tree. The leaf elements and the direction from each leaf to the next form a string, and repeats in that string are the cards. | Designed and prototyped 2026-10-12/15. **Built 2026-10-16** (WP1–WP5, pre-dev). Works on the ITR cards; **finds no tiles on the real GST dashboard**. See section 6.6. |

Pre-dev tests: 71 pass (cards 29, render 16, align 26). One cards test fails for an unrelated reason
(P-X1, section 7).

**What a comp page node holds** (the input to every method): `id`, `parent`, `depth`, `type`, `text`,
`key` (a path such as `Group[2]/Text[1]`, with sibling counters), `loose` (the key without counters),
`cls`, `values` (every text it showed), `present`, `left` / `came_back` (snapshot counts), and the raw
UIA fields (`rect` = screen box, automation `id`, `role`, `aria`, and so on).

## 4. Measured values

- **θ = 0.4167** (experiment 2, sibling likeness cut-off). Over 15 real files, the widest empty gap is
  0.33 to 0.50, and θ is its middle.
- **ρ not set** (experiment 2, name-recurrence cut-off). Per slot, r is the share of one page's texts that
  also appear on another client's page. On today's captures r comes out only as 0.00, 0.50 or 1.00. That
  gives two equal gaps, and the widest-gap rule picks nothing.
- Experiment 3 (from two ITR pages, **provisional**):
  - same-line vertical overlap: empty band 0 to 8 px, cut-off **4 px**;
  - same-column |dx|: empty band 1 to 10 px, cut-off **5 px**;
  - edits per period token between card members: true members ≤ 0.037, nearest false candidate 0.333,
    cut-off **0.185**.
- Experiment 3 as built, re-measured 2026-10-16 on six captures (three ITR, two GST dashboards, one GSTR-1
  summary page):
  - edits per token: gap 0.037 to 0.250, cut-off **0.14**;
  - the empty bands narrowed to overlap (0, 5) px and dx (1, 6) px. The 4 px and 5 px cut-offs still lie
    inside but are no longer the middles (2.5 and 3.5). Re-measuring them is an open owner decision.

## 5. Problems (bugs) from the cards and rows work

Status codes: ✅ fixed and stays fixed · 🟡 solved in the experiment 3 design, still to be confirmed on
real captures · ❌ open.

| # | Problem | Seen on | Status |
|---|---|---|---|
| 1 | The IFF tile was paired with the GSTR-1 tile. A shared description text became an anchor, and the tiles' insides were then paired by position. | GST returns dashboard | ✅ cards are paired by name text only |
| 2 | Groups at the document's top level were never checked | GST dashboard, Chrome | ✅ (exp. 3 has no tree at all) |
| 3 | One CSS class (`disableClick`) made one tile unlike the other tiles | GST dashboard, Chrome | ✅ element types only, classes never read |
| 4 | Table cells were grouped as fake cards | GSTR-1 B2B | ✅ a Table is handled by a separate table extractor |
| 5 | Two unrelated groups shared one "identity" and blocked each other | GST dashboard | ✅ each group is paired once |
| 6 | Client labels were guessed by hand, and the same client's values were counted as names | ρ measuring | ✅ client identity (PAN/GSTIN) read from the page |
| 7 | Firefox: the GSTR-1 tile has no wrapper element, so its parts lie loose and the tree method misses it | GST dashboard, Firefox | 🟡 exp. 3 ignores wrappers. On a Firefox-shaped fixture, tree method 3 of 5 tiles, arrangement 5 of 5. ❌ **Real Firefox dashboard (2026-10-16): the arrangement finds no tile at all** (section 6.6). The tree finds 4 tiles in one group and 3 more elsewhere. |
| 8 | Firefox: the GSTR-1A tile has an extra inner Group, so its likeness drops below θ and it is missed | GST dashboard, Firefox | ❌ same as 7: solved on the fixture, not on the real page |
| 9 | Four unrelated single-text boxes were grouped as cards by type likeness | GST dashboard | 🟡 a one-leaf period is never a group. On the real dashboards the arrangement forms only small 2- to 4-leaf groups; whether those are real has not been checked by eye. |
| 10 | Field rows inside a card were paired with another card's field rows, and every value was reported twice. Of 8 inner-group pairs, **7 joined rows of different cards.** | ITR filed returns, Edge | 🟡 nested groups are compared only inside their paired card. Prototype on the real pair: 4 card pairs, 80 fixed + 28 variable slots, each value once. |
| 11 | Label vs value is guessed from shape (`variable_alignment`): 5 false alarms on the ITR pair, all short status words | since 10-07 | 🟡 **inside groups only.** A value's label is the nearest constant ("label") slot before it in the card. The direction is recorded: right of the label for field rows, below it for the status column and the timeline. **Outside groups: still open.** |
| 12 | Chrome and Firefox trees differ, so comparing across browsers breaks | Firefox vs Chrome | 🟡 the leaf token strings are identical across Edge, Chrome and Firefox on a fixture (0 edits), while boxes move by up to 12 px. A text that wraps in one browser and not the other is still a risk. Not testable on the real dashboard while no tiles are found there. |
| 13 | ρ cannot be chosen, and r drops when one client has fewer cards | ρ measuring | ❌ **reframed.** r is replaced by a corpus-wide recurrence R per slot (section 6.4). Its cut-off needs ≥ 3 captures per link (clients × periods), which we do not have. Until then cards are compared as lists. |
| 14 | A tie between two name-slot candidates (description vs code) was broken by sort order | GST dashboard | 🟡 the earliest position in the card wins |
| 15 | Hover-opened menus and popups get into reads | GST dashboard render | ❌ nodes that came and left are dropped, but a popup open for the whole capture still gets in. Needs a visibility signal in the read. |
| 16 | The rendered page is very wide (one element outside the visible area) | GST dashboard render | ❌ renderer issue only. Cut to the visible area. |
| 17 | Ticking text (a live clock or counter) keeps every snapshot | ITR status | ❌ handled, not decided. A leaf that showed 2+ values is flagged `ticking` and is never a name. Whether snapshots are kept at all is the owner's call (P-X2). |

## 6. Experiment 3: the arrangement method (the design under review)

### 6.1 The idea (owner's)

Take the leaf elements and their screen boxes. Write down, for each leaf, its kind and where the next
leaf sits relative to it. Elements held together by a **repeating arrangement** form one card or row.
Nesting falls out by itself: small repeats sit inside big ones. Two clients are then compared top-down,
by arrangement (directions and order, not pixels): cards are paired by their name text, and fields by
their place in the matched card. Known limit: two cards with identical layouts (IFF vs GSTR-1) can only
be told apart by their name text.

### 6.2 The method, as designed and prototyped

1. **Leaves** are nodes that have a text, or whose type is Button, Edit, ComboBox, CheckBox, RadioButton
   or Image. The box must have positive width and height, and on a comp page the node must be `present`.
   Document (DOM) order is kept. Each Table becomes one leaf.
2. **Token** = (element type, step to the next leaf). The step is one of nine directions taken from the
   boxes:
   - same line: `R`, `L` or `O` (overlapping). "Same line" means the vertical overlap is more than OVL px;
   - other line: `D` or `U`, plus `L` or `R` when the left edges differ by more than TOLX px.
3. **Runs** are tandem repeats of the token string, with period ≥ 2. When members are compared, the
   **last token's step is left free**: it points out of the member, and without this a 2-card page never repeats.
4. Members may differ by a few edits per period token (E). This lets a card with one optional extra
   link still join the run.
5. **Phase**: `ABC ABC` is also `BCA BCA`, so a repeat has no natural start. The rotation with the
   smallest total member-box area is taken. Likeness cannot decide it: a window shifted by one leaf still
   matches 26 of 27 tokens.
6. **Nesting**: a run inside one member of another run is nested in it. The **label witness** sets the
   period. It is the smallest multiple of rows at which some position shows the same text in every
   member. This merges rows into the cards they belong to.
7. **Slots** = positions in the period. Each slot gets a role across the members: `label` (same text in
   every member), `distinct`, `mixed`, `look` (no text) or `ticking`.
8. **Kind** of a group:
   - `cards` when a slot is a name (section 6.4);
   - otherwise `list` when the member count varies;
   - otherwise `fields`.
9. **Comparing two pages**:
   - groups are paired by period likeness;
   - cards by equal name text, fields by place, lists by template and counts;
   - nested groups only inside the paired member;
   - everything outside groups goes through the existing sequence alignment;
   - every value is reported once.
10. The tree method is retired as the finder and kept only as a "does the tree agree" column.

### 6.3 Results on the real captures

The only real captures in this experiment are two ITR "View Filed Returns" pages, both in Edge, from two
clients.
- One page has 497 nodes and 205 leaves. The method finds **5 cards of 27 leaves**, each with 5 nested
  field rows and a 3-step timeline.
- The other page has 429 nodes and 179 leaves. It finds **4 cards** of 27, 28, 27 and 27 leaves: one card
  has an optional extra link, which breaks the exact repeat. Edit tolerance and phase recover it.
- Cost: 11 to 21 ms per page in plain Python.

A wrapped text changes the UIA tree (an extra Group level) but not the leaf sequence. That is why
experiment 2 split the timeline slots and experiment 3 does not.

### 6.4 What replaces ρ

For each slot, over all captures of the link (all clients, all periods):

    R = 1 − (distinct texts − members of the largest capture) / (occurrences − members of the largest capture)

R is 1 when the slot's vocabulary is closed (the same names on every capture) and 0 when every capture
brings new texts. It does not depend on which page is "this" one. On the ITR pair the card title slot has
R = 1.00, one slot 0.25, and five slots 0.00. The cut-off on R can only be measured once ≥ 3 captures per
link exist. Until then the name slot is `unknown` and cards are compared as a list.

### 6.5 Risks the design itself names

- Identical layouts with different meaning can only be separated by name text. With one client or one
  period there is no recurrence, so nothing is paired.
- A text that wraps differently between browsers or clients turns an R step into a D step and breaks a
  token. One token in a 27-leaf card is within tolerance; one token in a 2-leaf row is not. **Lists of
  short rows are the exposed case.**
- Two different groups with the same period side by side would merge into one run. Gaps between blocks
  (110+ px, against 0 to 20 px inside a block) could split them, but this is not built.
- Anonymous rows (no label) with an optional leaf: a wrong phase can win. This is untested.
- A list of single values (period 1, for example one ARN per line) is never a group. It falls back to
  sequence alignment and its known limit (P15).
- A list whose rows all share one value makes that value a `label`.
- DOM order is not screen order when a portal places a column out of order (a sticky side panel first in
  the DOM). Runs still form, but "nearest label before the value" can point at the wrong label.
- All cut-offs (OVL 4 px, TOLX 5 px, E 0.185) come from **two pages of one portal in one browser.**

### 6.6 Built, and run on new real captures (2026-10-16)

The method was built as a pre-dev tool (`arrange.py`, with `compare.py --arrange` and `render.py --arrange`)
and run on six real captures: three ITR "View Filed Returns" pages (two in Edge, a third client in Chrome),
the GST returns dashboard in Chrome and in Firefox, and one GSTR-1 summary page.

**Results, both methods** (top-level repeating groups, counts only):

| Capture | Tree method (experiment 2) | Arrangement (experiment 3) |
|---|---|---|
| ITR, client 1, Edge | 5 cards | 5 cards, 10 nested groups |
| ITR, client 2, Edge | 4 cards (one with an extra link) | 4 cards, 8 nested |
| ITR, client 3, Chrome | 6 cards | 6 cards, 12 nested (5 before the comp page fix below) |
| GST dashboard, Chrome | tiles found but split in two groups (3 + 2) | **no tile group** |
| GST dashboard, Firefox | 4 tiles in one group, plus a 3-member group | **no tile group** |

**Why the arrangement finds no GST tiles.** The method looks for a layout that repeats. The real tiles
do not share one layout: each return form shows its own set of parts.
- GSTR-1: a description, the code, the frequency, a status and 2 buttons.
- GSTR-1A: a description, the code and 1 button.
- GSTR-3B: a description, the code, the frequency, a due date and 2 buttons.
- GSTR-2A: a description, the code and 2 buttons.

In leaf tokens the tiles differ by 8 to 21 positions out of about 25. The method allows about 0.14 edits
per position, measured on the ITR cards, which really are identical. This holds on the plain last snapshot
as well, so it is the method, not the comp page. The tree method finds the tiles because they are sibling
subtrees, whatever they hold.

**Why the design report said it worked.** The report had only the two ITR captures, and on those its
numbers were reproduced exactly. For the GST dashboard and the Firefox bugs (7, 8, 9, 12) it had no
capture, and said so (its section 2.3 and question 1). It tested them on a fictional dashboard built by the
same AI. In that fixture every tile holds the same five parts (a link, the code, "Status-", a status word,
a View button); only the texts differ. So the fixture repeated perfectly and the real page does not. This
is exactly problem P21 (fixtures designed by the method's author).

**A comp page bug found and fixed on the way.** In all three new captures the anchor page was fixed at
+0.0 s: the first read already showed text and the next 5 to 9 polls saw no change, so the settle rule
worked as written. Content that loaded later (the 6th ITR card at +9 s, GST tile parts at +11 s and +25 s)
was then appended at the END of the comp page, away from its siblings. Now a new node goes where its
snapshot showed it, and the comp page is walked as a tree, so a subtree is never split. That brought the
third ITR client from 5 to 6 cards. It does not change the GST result.

**A compare bug found and fixed in review.** With no name cut-off measured, two pages with the same number
of tiles were paired by position, so IFF was paired with GSTR-1 (bug 1 back). Now a group that may hold a
name is compared by template until the name cut-off exists.

**Where this leaves the choice.** On pages with identical cards (ITR), the arrangement is as good as the
tree and better at nesting. On pages whose repeating units hold different contents (GST tiles), only the
tree method finds anything. Neither method is enough on its own. Two directions are open (question J in
section 9):
1. a different notion of "repeating unit": things that sit side by side and play the same role, even when
   their contents differ;
2. a hybrid: the tree proposes sibling groups (it finds the tiles), and the arrangement checks and repairs
   them with leaves and boxes (it handles missing or extra wrappers, bugs 7 and 8).

## 7. Other open SDIS problems (outside the cards work)

| # | Problem | Notes |
|---|---|---|
| P5 | Furniture counted as data: rotating notices, "Site last updated on …" | It changes by day or visit, not by client. Needs the same client over time, so captures on different days. |
| P15 | Two look-alike values with no anchor between them, one missing on the other page: the first is paired silently | The information is not on the page. The plan is to mark these as ambiguous (low %), then use screen position, value type and many clients. Period-1 lists land here. |
| P18 | A capture that never shows a PAN or GSTIN cannot be told apart from a capture of the same client | No identity on the pages visited. Ignore such captures, or count them with a lower weight? |
| P21 | The fictional tests were designed by the same AI that designed the method | The real proof is a real-portal page with different list lengths. |
| `variable_alignment` outside groups | Label vs value for page-level fields (not in a repeating group) | The design says "a page-level field block is a fields group of one member" and applies the same nearest-label rule. Not designed in detail. |
| Corpus mining | Staff PCs send their capture files to the admin PC, but the miner never reads the received files, and same-named day files from two PCs collide | Engineering, not method. |
| P-X3 | Comp page order: content that loaded after the anchor page was fixed went to the end of the page | **Fixed 2026-10-16** (section 6.6). The settle rule ("fixed after one quiet poll") is unchanged. Pages that pause while loading still get fixed early, which is harmless now that order is kept. |
| P-X1 | One pre-dev test fails because of an unrelated missing `Any` import in another module | Engineering. Queued separately. |
| P-X2 | Keep snapshots, or build the comp page during capture and drop them? | The owner leans to dropping them. The decision waits for the experiments. |

Relevance ranking itself (which datapoints matter, shown as percentages) is planned but not designed in
detail. The rough plan:
- template vs data by voting across clients;
- noise vs data over time;
- pairing confidence from nearby label, screen position and value type;
- each slot learns what it usually holds.

It needs about 5 or more clients per page.

## 8. Captures we have, and what is missing

| Have | Missing |
|---|---|
| ITR "View Filed Returns": 3 clients (2 in Edge, 1 in Chrome), 1 period | GST dashboard from a **second client** in each browser (the name cut-off needs ≥ 3 captures from ≥ 2 clients) |
| GST returns dashboard: Chrome and Firefox (2026-10-16), one client each; GSTR-1 summary page (Chrome) | One client in two periods (needed for the R cut-off) |
| Earlier GST dashboard captures (experiment 2, Chrome and Edge) | |
| Fictional fixtures: one page read live in Edge, Chrome and Firefox; a tables page in 3 browsers | A page with a real list inside a card (list vs fields); a div-row list page; a page with no repetition; a real hover popup |

## 9. Open questions (what we want a second opinion on)

**Owner decisions pending** (from the design report; each has the designer's recommendation):

1. **Captures first?** Should the GST dashboard (Chrome + Firefox), a third ITR client and a second period
   be captured before any cut-off is fixed? *Rec.: yes. The build can start with test values meanwhile.*
2. **Equal-count nested runs:** compare them as fields (row 1 with row 1), or as a list whenever each row
   carries its own repeated label? *Rec.: "count varies" is the only list rule for now.*
3. **The tree method:** keep it as a witness column, or drop it right away? *Rec.: keep it until the
   dashboard overlays in two browsers have been checked by eye.*
4. **Period-1 runs:** never a group, or a list when the leaves sit on a lattice? *Rec.: never a group.*

**Questions for the consultant:**

- A. Is the arrangement method sound as a general record finder for UIA reads, or is there a simpler or
  better-founded formulation? Examples: a known string algorithm for approximate tandem repeats, a
  2-D layout grammar, or a grid or lattice model of the boxes.
- B. Is "smallest total member-box area" a principled phase rule? When would it fail?
- C. The wrap risk (an `R` step becoming `D`): is there a direction encoding or a tolerance that survives
  text wrapping without becoming a heuristic?
- D. R as a replacement for ρ: is the formula sound? Is there a better statistic for "this slot names a
  thing" that works with very few captures (2 to 5)?
- E. How should the method behave when the data is too thin to measure a cut-off? It currently refuses
  and falls back to "list". Is that the right fallback?
- F. Label vs value outside repeating groups (bug 11's remaining half), and look-alike values with no
  anchor (P15): what would you do, within the rules in section 2?
- G. Hover popups (bug 15) with no visibility flag in UIA: any signal in boxes, timing or the comp
  page's present/left history that separates a popup from real content?
- H. Which risk in section 6.5 would you test first, and with what kind of real page?
- I. Prior art we should know beyond RoadRunner, MDR/DEPTA, VIPS and ViNTs/ViPER (all HTML-based)?
  Anything on accessibility trees or UIA?
- **J. (Most important, added 2026-10-16.) Repeating units whose contents differ.** The real GST dashboard
  tiles do not share one layout (section 6.6): GSTR-1 has a status and 2 buttons, GSTR-1A has 1 button,
  GSTR-3B has a due date. The arrangement finds no tiles; the tree method finds them because they are
  sibling subtrees, but splits them across groups. How should a method find "units that sit side by side
  and play the same role" when their contents differ, within the rules in section 2 (no AI, measured
  cut-offs, nothing known about a portal or browser)? Is the hybrid sound (the tree proposes sibling
  groups, the arrangement checks and repairs them with leaves and boxes)? Or is there a better single
  formulation, for example one built on what tiles always share (a code or title in the same place, a
  similar box size, a grid of equal positions)?
- K. Fixtures written by the method's author passed while the real page failed (P21). How would you test
  such a method so this cannot happen again, given that only a few real captures exist per page?
