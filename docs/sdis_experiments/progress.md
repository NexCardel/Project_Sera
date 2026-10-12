# SDIS rework experiments: progress

Last updated: 2026-10-12. Pre-dev work in `tools/pre_dev/class_diff/`. Nothing in `core/` is changed by
these experiments. Real captures stay in the git-ignored `tools/pre_dev/class_diff/output/` (client data);
this file holds no page values.

## 1. Why

On 2026-10-07 the owner judged SDIS "not that useful" and started fixing it through experiments. Goal:
compare pages of different clients and tell reliably what is template, what is a datapoint, and which
datapoint on one client's page is the same field on another's.

## 2. Experiments so far

| # | Experiment | State | Where |
|---|---|---|---|
| 1 | **Comp page**: all snapshots of one capture merged into one page (anchor page + new nodes attached at the end; nodes that leave keep `present = no`) | Built, committed `5e675b7` (2026-10-07) | `snapshot_diff.py` (`AnchorPage`), `key_probe.py`, `compare.py --comp` |
| - | **UIA renderer**: a raw UIA read drawn as an HTML page from the screen boxes only | Built (another agent), moved to pre-dev on the owner's word, uncommitted | `render.py`, plan `docs/uia_render/plan.md`, tests `tests/test_class_diff_render.py` |
| 2 | **Cards and rows**: find repeating parts (tiles, list rows, table rows), pair cards by name, compare lists by template | Built (another agent), reviewed and fixed 2026-10-11, uncommitted | `cards.py`, `compare.py --comp --cards [--rho X]`, `render.py --groups`, plan `docs/cards_rows/plan.md`, tests `tests/test_class_diff_cards.py` |
| 3 | **Arrangement ("packing pattern")**: find cards from how elements are packed on screen, and compare clients by that arrangement | Idea agreed in discussion, not planned yet | — |

Tests: 71 pass (cards 29, render 16, align 26).

## 3. Measured values

- **θ = 0.4167** (sibling likeness cut-off): middle of the widest empty gap (0.33–0.50) over 15 real files.
- **ρ not set**: recurrence across clients comes out only as 0.00 / 0.50 / 1.00, two equal gaps, the rule
  picks nothing. `compare.py --cards --rho 0.75` lets the owner try a value. Owner decision.

## 4. Bugs caught, and what fixes them

| # | Bug | Seen on | Status / fix |
|---|---|---|---|
| 1 | IFF tile paired with GSTR-1 (shared description became an anchor, insides paired by position) | GST returns dashboard | ✅ cards paired by name (exp. 2) |
| 2 | Groups at the document's top level never checked | GST dashboard (Chrome) | ✅ fixed |
| 3 | One class (`disableClick`) made GSTR-1A unlike the other tiles | GST dashboard (Chrome) | ✅ element types only |
| 4 | Table cells grouped as fake cards | GSTR-1 B2B | ✅ tables left to `tables.py` |
| 5 | Two unrelated groups shared one "identity" and blocked each other | GST dashboard | ✅ plan §6.1 matching |
| 6 | Client labels guessed by hand; same-client values counted as names | ρ measuring | ✅ GSTIN/PAN read from the page |
| 7 | Firefox: GSTR-1 tile has no wrapper, its parts lie loose → missed | GST dashboard (Firefox) | ❌ exp. 3 |
| 8 | Firefox: GSTR-1A has an extra inner `Group` → likeness below θ → missed | GST dashboard (Firefox) | ❌ exp. 3 (or "skip plain Group" fix) |
| 9 | False group of 4 unrelated single-text boxes | GST dashboard | ❌ exp. 3 |
| 10 | Field rows inside a card paired with the wrong year's card; values reported twice | ITR filed returns (Edge) | ❌ compare fix: inner groups only inside their paired card |
| 11 | `variable_alignment` false alarms; label vs value guessed from shape | since 10-07 | ❌ exp. 3 ("value right of its label") |
| 12 | Chrome and Firefox trees differ, comparing across browsers breaks | Firefox vs Chrome | ❌ exp. 3 |
| 13 | ρ cannot be chosen; r drops when one client has fewer cards | ρ measuring | ❌ owner call, or "a name never changes during a capture" |
| 14 | Name slot tie (description vs code) picked by sort order | GST dashboard | ❌ minor |
| 15 | Hover-opened menus get into reads | GST dashboard render | ❌ needs a visibility flag in the read |
| 16 | Renderer page very wide (element outside the visible area) | GST dashboard render | ❌ renderer: cut to the visible area |
| 17 | Ticking text keeps every snapshot | ITR status | ❌ part of the snapshot decision |

## 5. Decisions and open questions

- **Snapshots**: the owner leans to dropping them (comp page built during capture). Decision after the
  experiments. A `--keep-snapshots` flag was proposed for replay.
- **Renderer is a pre-dev tool**, never in `core/sdis/`.
- **No quick heuristics**: cut-offs are measured on real captures, never picked.
- **Prior art** (for exp. 3): RoadRunner (template vs data by comparing pages), MDR/DEPTA (repeating
  records, DEPTA with screen positions), VIPS (visual page blocks), ViNTs/ViPER (visual record patterns).
  None fits as-is: they read HTML, or need AI/cloud.

## 6. Next

1. Compare fix for bug 10 (small, needed either way).
2. Plan experiment 3 (arrangement): measuring step first.
3. Owner: decide ρ; capture the GST dashboard in Firefox and Chrome, a third client, one client in two
   periods, a div-row list page.
