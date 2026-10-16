# Arrangement (experiment 3): build notes and decisions

Design: `docs/sdis_experiments/cards-solution.md`. Build plan: `docs/sdis_experiments/arrange-build-plan.md`.
Code: `tools/pre_dev/class_diff/arrange.py` (new), `compare.py --comp --arrange` (new flag), `render.py --arrange`
(new flag, off by default). `cards.py` and everything in `core/` are unchanged.

Counts and pixel numbers only in this note. No page value appears here.

## Status by work package

| WP | What | State |
|---|---|---|
| 1 Leaves and tokens | `leaves()`, `step()`, `tokens()`, Table / ticking / attached, `--steps` | built, tested |
| 2 Runs | tandem seeds, phase, extension, ranking, nesting, label witness, `--likeness`, shape report | built, tested |
| 3 Slots, roles, kinds | `align` to the first member, roles, `name_slot()`, `recurrence()`, `--recurrence` | built, tested; name cut-off not set |
| 4 Compare | `compare.py --comp --arrange` | built, run on the ITR pair |
| 5 Overlay | `render.py --arrange`: member boxes, name and distinct marks | built, tested; arrows not drawn; not checked by eye |
| 6 Retire the tree finder | `cards.py` reduced | **not started**: needs the owner's go after the overlays are seen |

## Decisions (measured)

`OVL` and `TOLX` are read from `--steps` on the comp pages in `output/` (two ITR pages, one GST page).

- **OVL = 4 px** (same line when the vertical overlap is more than 4). The overlap values have a separating band
  (0, 8): no pair overlaps by 1 to 8 px, and 202 pairs have no overlap at all. Middle of the band: 4.
- **TOLX = 5 px** (same column). The other-line |dx| values have an empty band (1, 10): 0 and 1 px, then 10 px.
  Middle: 5.
- **EDITS_PER_TOKEN = 0.14**. True members sit at 0.000 to 0.037 edits per token; the nearest window beside a
  group sits at 0.250 (one 4-leaf group). Gap (0.037, 0.250), middle 0.14. Measured with `--likeness`, which
  refuses to run if the constant leaves the gap.
- **NAME_CUT: not set.** A name needs a corpus R at or above a cut-off, measured over at least three captures per
  link and two clients. The ITR pair is two captures of two clients, so no name is decided yet.
- **Owner questions (design section 9)**, built as recommended: "count varies" is the only list rule; a one-leaf
  period is never a group; the tree method stays (cards.py unchanged).

## Numbers (counts only)

- Leaves: ITR 121513 = 205, ITR 121834 = 179, GST gstr1sum = 70.
- Fixtures: the three-browser fixtures give identical token strings on both clients (Edge = Chrome = Firefox).
  Leaves per fixture client: 42 and 59. The design's 52 and 69 counted the text nodes inside the fixture's
  table; a Table is one leaf here.
- ITR 121513: five cards of 27 leaves (period 27); five field-row runs (period 2, five members) and five timeline
  runs (period 3, three members), each nested in one card.
- ITR 121834: four cards (27, 27, 28, 27 leaves). The 28-leaf card has one extra link: an `own:` slot, present in
  one member. Four field-row runs and four timeline runs nested in the cards.
- Corpus R over the two ITR captures (cards family, 27 positions): position 0 = 1.00, position 19 = 0.25, five
  positions = 0.00. These are the design's own numbers (section 2.4), reproduced exactly.
- `compare.py --comp --arrange` on the ITR pair (latest = 121834, previous = 121513), re-measured 2026-10-16
  after the fix below:
  - top-level pairs: 3, all compared by template (34 slot rows + 3 row-count rows). The card group (4 against 5)
    because the counts differ; the two small groups because they hold a distinct slot and no name is measured;
  - no card is paired with another card yet: that needs NAME_CUT, so no fixed / variable rows come from inside groups;
  - rows outside groups: 45 = 31 fixed, 8 semi-variable, 4 variable, 1 variable_alignment, 1 only previous.
  - (An earlier version of this note gave "95 fixed + 28 variable" inside groups. The run did not show that: the
    card group was already compared by template, and the two small groups were paired by place, 15 fixed.)
- `compare.py --comp --cards` on the same pair (today's tree method, for reference): 12 groups paired, 1 only
  latest, 2 only previous, 40 rows outside groups.

## Departures from the design (and why)

1. **Phase uses the mean member-box area, not the total** (section 3.3.2). The total falls when a rotation drops
   a member: on 121513 the true alignment has five members and a wrong one four, and the total chose the wrong one.
   The mean is compared exactly (a fraction), so ties are deterministic.
2. **A rotation must really repeat** (found while building, fixed). The last step is free in the relaxed compare,
   so a rotation can start on a position the run never compared. `phase()` only takes rotations whose members are
   relaxed-equal to the first, and reports how many repeat.
3. **Likeness is measured beside each group, not over all windows.** Over all windows, two-leaf periods matched
   leaves of other groups at 0.000 (82 windows), which says nothing about the extension that E gates. The
   measured gap is between the members and the candidates the extension actually tests.
4. **Corpus R needs two clients** (`recurrence()` returns None for one client). Same-client captures do not make
   names, as the WP3 test list says. Families are matched by period and position on the page, not by text.
5. **Lists are compared by template when the counts differ** (design 3.5). Compare reports one row per slot (first
   values, count) and one row with the member counts; the cards with 5 against 4 on the ITR pair are a list.
   **Fixed in review (2026-10-16):** with no name known, groups with EQUAL counts were paired by place, so two
   pages with two tiles each paired IFF with GSTR-1 (bug 1 back). Now a top-level group without a known name is
   compared by template whenever either side has a distinct slot (a possible name whose R is not measured,
   design 3.5.1). Only a group with no distinct slot is still paired by place. Test:
   `test_without_a_name_cut_equal_counts_are_never_paired_by_place`.
6. **Nested groups produce no rows of their own** (design 3.6.7). Their leaves are slots of the parent's members,
   so the card rows carry them, and nested runs pair only inside a pair of members (bug 10). The design's bug-10
   check ("before 7 of 8 cross-paired, after 0") was not run as a separate measurement.
7. **Outside rows:** the group leaves are removed from each page and the rest goes through `compare_flat()`. Group
   texts never anchor anything outside (the design's placeholders are not compared by `_compared()`, so they would
   have done nothing).
8. **Label direction** (`label_dir`) is recorded only on value slots with a label slot before them in the same
   member; the step is R or D as measured in design section 2.3.

## Not done

- **Label-to-value arrows** in the overlay (WP5). The boxes and the name / distinct marks are drawn.
- **The overlay has not been checked by eye**, and I did not check that the page is not widened.
- **WP6** (retire the tree finder) waits for the owner's go, as the plan says.
- **Dashboard captures** (GST in Chrome and Firefox, a third ITR client, a second period for R): needed for the
  cut-offs marked "not set" and for bugs 7, 8, 9 and 12 (design section 9, question 1). The GST capture in
  `output/` is one client, one period.
- The GST page shows four runs (periods 2, 4, 3, 2) and none is confirmed against the page. The design says the
  four unrelated boxes must not form a run; that needs the owner's look.

## Run on the new captures (2026-10-16)

Captures: GST returns dashboard in Chrome (`005456`) and Firefox (`005952`), a third ITR client in Chrome
(`011303`), plus the earlier ITR pair and the GSTR-1 summary page.

**Comp page order fixed** (`snapshot_diff.py`, `AnchorPage`). In all three new captures the anchor page was
fixed at +0.0s: the first read already showed text and the next 5 to 9 polls saw no change, so the rule
("fixed after one quiet poll") is working as written. The 6th ITR card and the GST tile contents loaded later
(+9 s, +11 s, +25 s). Until now those attached nodes went to the END of the comp page, which split them from
their siblings: 15, 7 and 4 subtrees were not contiguous. Now a new node goes after the node before it in its
snapshot, and the comp page is walked as a tree (`_tree_order`), so a subtree is never split, even when
alignment pairs one inner node with an older node elsewhere (it did: one button of the 6th card). Result: 0
split subtrees on every comp page. Tests: `tests/test_class_diff_comp_order.py`. The settle rule itself is
unchanged.

**Both methods, after the fix** (top-level groups; counts only):

| Capture | Tree method (cards.py) | Arrangement |
|---|---|---|
| ITR `121513` (Edge) | 5 cards × 27 | 5 cards × 27, 10 nested |
| ITR `121834` (Edge) | 4 cards (27/28/27/27) | 4 cards (27/28/27/27), 8 nested |
| ITR `011303` (Chrome) | 6 cards × 27 | 6 cards × 27, 12 nested (was 5 before the fix) |
| GST dashboard Chrome | tiles split in two groups: 3 (GSTR-1A, 2B, 3B) + 2 (one with GSTR-1) | **no tile group** (biggest: 3 × 3 leaves) |
| GST dashboard Firefox | 4 tiles in one group (GSTR-1, 3B, 2B, 2A) + a 3-member group | **no tile group** (biggest: 3 × 4 leaves) |

**The GST tiles are a limit of the method, not of the comp page.** The tiles hold different contents (8 to
13 texts each; in leaf tokens member 1 is 8 edits from member 0, member 2 is 21), so the leaf string has no
tandem repeat and no edit allowance from the measured E (0.14 per token) covers it. The same holds on the
plain last snapshot of each capture, so the order fix cannot help here. The tree finds them because the
tiles are sibling subtrees, whatever they hold. Open design question (for the consultant): how to find
repeating units whose contents differ.

**Cut-offs on the wider corpus:** the empty bands narrowed. Overlap: (0, 5) (was (0, 8)); dx: (1, 6) (was
(1, 10)). OVL = 4 and TOLX = 5 still lie inside, so `--steps` passes, but they are no longer the middles
(2.5 and 3.5). Owner decision: re-measure and move them to the middles, or wait for more captures.
EDITS_PER_TOKEN = 0.14 still lies in its gap (0.037, 0.250).

## Tests

- `tests/test_class_diff_arrange.py`: 35 tests (WP1 to WP5), fictional fixtures built in the file, plus the
  three-browser align fixtures.
- Narrow run (`tests/test_class_diff_arrange.py`, `test_class_diff_cards.py`, `test_class_diff_align.py`,
  `test_class_diff_render.py`, `test_sdis_package.py`): 119 passed (2026-10-16, after the review fix).
