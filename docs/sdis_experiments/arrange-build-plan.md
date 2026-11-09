# Build plan: `arrange.py` from Fable's prototype scripts

Written 2026-10-15 for the agent who builds experiment 3. The design is `cards-solution.md` (same
folder). This plan says which of Fable's scripts in `scripts/` to port, which to skip, and what the
port lacks, so you don't have to re-derive any of it.

## 0. Read this and nothing more (to save tokens)

1. `cards-solution.md`: §1, §3 (the design), §5 (cut-offs), §8 (WPs). Skim §2 for numbers; skip §7.
2. `scripts/arr.py`, `scripts/s11_pipeline.py`, and in `scripts/s6_proto.py` only `union()` and `area()`.
3. `scripts/s8_fixtures.py`: only `firefox_dashboard()` and the IFF / GSTR-1 trap.
4. `tools/pre_dev/class_diff/cards.py` and `compare.py`: only the parts named in the WP you are on.

Do **not** run or read `s1`–`s5`, `s7`, `s9`, `s10` beyond the one function a WP names below. Their
results are already in the report.

## 1. Rules

- Pre-dev only: new file `tools/pre_dev/class_diff/arrange.py` beside `cards.py`. Nothing in `core/` changes.
- Work in your own git worktree. Other sessions commit in the main checkout.
- Fictional fixtures only in tests. Console and CSV output: counts and shapes, never a page value.
- Cut-offs are **measured**, never picked: the widest empty gap, or refuse to run (as `THETA` in `cards.py`).
- Deterministic: sort explicitly. Every WP gets a test that feeds the same input twice, with key order shuffled.
- Narrow tests: `python -m pytest tests/test_class_diff_arrange.py tests/test_class_diff_cards.py tests/test_class_diff_align.py -q -p no:cacheprovider --basetemp=<scratch>/bt`.
  The full suite only via the live plugin (see `CLAUDE.md`).
- `tests/test_sdis_tables.py::test_command_line_prints_no_values` already fails on main. It is not yours.
- Owner questions (report §9) are unanswered. Build to the report's recommendations and keep each one
  behind a single constant or flag, so the owner's answer is a one-line change.

## 2. What to port and what to skip

| Script | Keep | What happens to it |
|---|---|---|
| `arr.py` | `leaves`, `step`, `tokens`, `tandem_runs_relaxed`, `relaxed` | Port into `arrange.py` (WP1, WP2). Drop `tandem_runs` (exact) and `runs_report`. |
| `s11_pipeline.py` | `edits`, `extend_edits`, `phase`, `label_period`, `roles`, `pipeline` | **The reference.** Port into `arrange.py` (WP2, WP3). Remove the module-level run loop. |
| `s6_proto.py` | `union`, `area` | Port. Skip the rest: `enriched` tokens and the 0.9 similarity `extend` are superseded by s11's edit-based extension. |
| `s8_fixtures.py` | `firefox_dashboard()`, the IFF / GSTR-1 trap | Rebuild as fixtures inside `tests/test_class_diff_arrange.py`. |
| `s9_tolerance.py` | its overlap and dx histograms | The model for `arrange.py --steps` (WP1). |
| `s4_members.py`, `s7_validate.py` | the likeness histogram; the recurrence per slot | The models for `--likeness` (WP2) and `--recurrence` (WP3). |
| `s2_bugs.py` | the bug 10 count (inner groups paired across cards) | One check in WP4: before 7 of 8 cross-paired, after 0. |
| `common.py`, `s1`, `s3`, `s5`, `s10` | nothing | Skip. `common.py` has Linux paths (`/home/user/...`). `s10` explored a ranking that s11 replaced. |

## 3. Where the prototype differs from the report (fix while porting)

1. **Tolerances:** `step()` uses `vover > 0` and `TOL = 3`. The report wants OVL = 4 px for "same line"
   and TOLX = 5 px for "same column". Use two named constants, checked against `--steps` before use.
2. **Leaves:** the prototype has no Table placeholder (a whole Table = one leaf, rows from
   `core.sdis.tables.extract()`). It also has no `ticking` flag (2+ values) or `attached` flag. Add them (report §3.1).
3. **Input:** it reads comp `nodes` (has `present`) or `flatten()` of a read (`present_only=False`).
   Keep both paths, with one entry point that takes either.
4. **Group records** are tuples with indices (`parent = (group, member)`). Make them small dataclasses:
   members, period, kind, slots, roles, parent.
5. **Missing entirely:** slot alignment with `own:` slots for inserted leaves, `kind()`
   (cards / list / fields), the corpus-wide R, the compare, and the overlay. These are WP3 to WP5.
6. `EDITS_PER_TOKEN = 0.185` is provisional (two pages). Keep it as a constant that `--likeness`
   re-measures, and refuse to run when the gap is not empty.

## 4. Work packages (the order of report §8)

| WP | Builds | Ported from | Done when |
|---|---|---|---|
| **1** Leaves and tokens | `leaves()`, `step()`, `tokens()`, Table / ticking / attached; CLI `--steps` (histograms) | `arr.py`, `s9` | Tests: leaf selection cases, the 9 step classes at their boundaries, Edge = Chrome = Firefox token strings on `tests/class_diff_align/`. `--steps` run on every file in `output/`; OVL and TOLX written to `docs/cards_rows/arrangement.md` § Decisions. |
| **2** Runs | relaxed tandem seeds, `phase`, `extend_edits`, ranking / acceptance / nesting, `label_period`; CLI `--likeness`, and `arrange.py FILE` (shape report) | `s11`, `s6`, `arr.py` | Tests: ITR-like page (5 cards, one with an optional link, one wrapped title), Firefox dashboard, 2-tile page, phase trap, rows-only cards (2 × 12 → 8 × 3), no repeat, Table page. Real ITR pair: 5 and 4 cards, as in report §2.4. E re-measured. |
| **3** Slots, roles, kinds | slot alignment (`own:`), `roles`, `kind()`, corpus R; CLI `--recurrence` | `s11.roles`, `s7` | Tests from report §8 WP3. R printed per slot per link; its cut-off stays "not set" until ≥ 3 captures per link exist. |
| **4** Compare | `compare.py --comp --arrange`: pairing by period, cards by name, fields by place, lists by template, nested groups only inside the pair, rest through `align()` with placeholders, `label_dir` + `ticking` columns | `s2` (bug 10 check) | Tests from report §8 WP4. On the real ITR pair: every value once; bug 10 cross-pairs = 0; counts vs today's `--cards` in the WP note. `--cards` unchanged. |
| **5** Overlay | `render.py --arrange`: member boxes, nested boxes, name slot, label → value arrows | — | Off by default; the boxes are drawn; the page is not widened. The owner checks it by eye. |
| **6** Retire the tree finder | `cards.py` shrinks to `--histogram` and the witness column | — | **Only after the owner has seen the overlays of the GST dashboard in two browsers.** Do not start without the owner's go. |

## 5. Hand back after each WP

A short note in `docs/cards_rows/arrangement.md`:
- what was built;
- the narrow test result;
- the measured numbers (counts only);
- anything that did not match the report.

Commit per WP on your branch. Do not merge into main yourself.
