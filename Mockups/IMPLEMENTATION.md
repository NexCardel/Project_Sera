# Sera UI redesign — implementation plan

Companion to the six mockup specs in this folder (`01`–`06`) and the clickable `redesign/index.html`.
Rollout style follows the SGT overhaul (`docs/sgt-overhaul-*`): phases, numbered work packages (WPs)
with dependencies, a review gate at the end of each phase, hand-off notes, and a list of checks only a
human can do. Written 2026-10-05. Nothing in the app has changed yet.

**Status of the older `docs/Blueprints/Sera_UI_UX_Redesign_Blueprint.md`:** superseded by this
folder. Its five primitives (`SectionLabel`, `Row`, `Divider`, `GhostIconButton`, `Badge`) already
exist in `ui/utils/theme.py` and are documented in `docs/Sera_UI.md`. This plan builds on them and
does not redo them.

---

## 0. The whole plan on one page

- **What:** restyle and re-arrange six screens (All Clients, Client detail, Audit log, Sera Sync,
  Settings General, Settings Columns) as in the mockups, plus Manage Clients, Tracker Dump and the
  display scale picker (phase 4), then bring every other screen onto the same look so the app does
  not end up as a few new screens among old ones. Every changed screen works at every display scale
  and in both width modes (4.7). The SGT lab and the SDIS panels are **out of the overhaul**.
- **How:** build the shared pieces once (phase 1), then use them screen by screen, busiest screens
  first. Each phase ends with a review that checks the "kept as it is" promises against the old code.
- **Rule of the plan:** same data, same shortcuts, same settings, same database writes. Only the
  presentation and the arrangement change. Where a mockup needs new behaviour (selection bar,
  "Shows on" write-through, unsaved bar, audit service names, Sync cards) the behaviour is specified
  in section 4 *before* any code is written.
- **Order:** 0 safety net (+ the fleet mockup, W0-D) → 1 shared pieces → 2 Client detail →
  3 All Clients → 4 Manage Clients, Tracker Dump, Display scale → 5 Settings → 6 Audit log →
  7 Sera Sync → 8 every other screen → 9 docs and final review.
- **Re-planned (2026-10-10):** phase 4 added; SGT lab and SDIS removed; W0-D (a cloud session
  with Fable 5.1 redesigns every screen as a new HTML mockup in `Mockups/fleet/`) added; the
  work-package files moved to `Mockups/overhaul/`.
- **Decided (2026-10-05):** Theme shows "Dark" as the only option. EMAIL and TAN keep type
  `password` [Secret] for now. All screens not mocked up get the same treatment (phase 8).

---

## 1. Rules that are never broken

1. **Same behaviour.** Every control in the "Where every current control goes" tables keeps working,
   with the same shortcut, setting key, database call and audit-log entry as today. A control may move
   or change its label; it may not disappear.
2. **No new secrets on screen.** Masking follows Settings → Password Masking Mode everywhere. New
   surfaces (selection bar, Columns menu, audit panel, Sync cards) never show a password, PIN or key.
   The 30 s (setting `clipboard_clear_seconds`) clipboard wipe stays.
3. **Tokens, not hex.** After phase 1, new code takes colours, radii and spacing from one tokens
   module. No new hard-coded `#xxxxxx` in a window or dialog file.
4. **Red means danger.** Delete / purge / destructive confirmations only. "Off" states are dimmed grey
   with a tooltip that says why. One emerald primary button per screen.
5. **Keep attribute names that tests and other code reach.** Existing UI tests (`test_sync_*_ui.py`,
   `test_unified_settings_lazy.py`, `test_tracker_dump_autofill_button.py`, ...) use widget attributes.
   Rebuild the layout around the same attribute names; change a name only together with its tests.
6. **Do not slow the app or grow its memory.** No heavy imports at module load, no pandas, icons via
   the existing icon-font path (see `tests/test_startup_speed.py`, `tests/test_memory_tuning.py`). The
   shared pieces are plain Qt widgets.
7. **Fictional data.** Test fixtures, gallery data and screenshots use made-up clients, PANs and
   workstation names. Nothing is copied from `master.db`, `~/AmanAssociates_Sera/` or backups.
8. **Small laptop first.** Every screen must work at 1366×768, at 100 / 125 / 150 % Windows scaling,
   at Sera's own display scale 0.80–1.25 and in both width modes (WIDE / COMPACT, see 4.7), with no
   sideways scroll except inside the white data grid.
9. **Do not touch** version numbers, installers or the release files (`version.json`, release
   process). This is a UI change only. Never bulk-delete files in user data folders.
10. **Match the surrounding code**: style, comment density, naming. Change only what the WP needs.
11. **Out of the overhaul:** the SGT lab (`ui/dialogs/sgt_lab_dialog.py`) and the SDIS panels
    (`ui/dialogs/sdis_dialog.py`, `sdis_containers_dialog.py`) keep their current look. No WP edits them.

---

## 2. Where things are

| Thing | Location |
| :--- | :--- |
| Mockup specs, images, clickable page | `Mockups/` (this folder), `Mockups/redesign/index.html` |
| Fleet redesign mockup (W0-D) | `Mockups/fleet/index.html`, `Mockups/fleet/README.md` |
| Work-package tracker and dispatcher | `Mockups/overhaul/` (`ui_overhaul.py`, plan JSON, status CSVs, runner prompts, `run_ui_overhaul.bat`, `watch_ui_worker.bat`) |
| Display scale and width modes | `ui/utils/ui_scale.py`, `ui/utils/responsive.py`, `ui/dialogs/display_scale_dialog.py`, `docs/ui-scale-adaptive-layout-plan.md` |
| Screenshots of today's screens (31 files) | `Mockups/current/` |
| Existing tokens and 5 primitives | `ui/utils/theme.py`, `docs/Sera_UI.md` |
| Existing chip, switch | `FilterChip` (Manage Clients, `ui/windows/admin_window.py`), `ToggleSwitch` (`ui/shell/sidebar.py`) |
| Screens | `ui/windows/{search,client_detail,admin,tracker_dump}_window.py`, `ui/dialogs/*`, `ui/shell/*` |
| Settings hub | `ui/dialogs/unified_settings_dialog.py` (lazy pages; `_build_page_*`) |
| Work branch | `ui-overhaul`, in a git worktree `../APP-ui` (same convention as `../APP-sgt`) |
| Python | `../APP/venv/Scripts/python.exe` |

**Running tests:** use the live T/F/S plugin from `CLAUDE.md`
(`$env:PYTHONPATH="tools"; python -m pytest -p live_tf -p no:terminal -p no:cacheprovider tests`);
single files with plain `python -m pytest <file>`. UI tests run with `QT_QPA_PLATFORM=offscreen`.

**Running the dispatcher:** `Mockups/overhaul/run_ui_overhaul.bat` (from the `../APP-ui` worktree);
`../APP/venv/Scripts/python.exe Mockups/overhaul/ui_overhaul.py show | next | prompt <WP>`. WPs of kind
`cloud` (W0-D) are never started by the dispatcher: print the prompt with `prompt W0-D` and start
a cloud session with it, then `git pull` the branch when it has pushed.

---

## 3. Work packages

> **Superseded 2026-10-10.** The order and the packages below are replaced by
> [`fleet/BUILD-PLAN.md`](fleet/BUILD-PLAN.md) (kit first: T, K, R, S, F), written with the fleet
> mockup (W0-D). Phase 0 (W0-1 to W0-R) stays. Sections 1, 4, 5 and 9 still apply; the WP ids in
> the 4.x headings map to BUILD-PLAN rows by topic (4.1 → S-7, 4.2 → S-6, 4.3 → S-4, 4.4 → S-1,
> 4.5 → S-11, 4.6 → S-12, 4.7 → R-1 / R-3, 4.8 → S-9 / S-10). Models: the tracker
> (`overhaul/ui-overhaul-plan.json`) gives each package one preferred model (Gemini 3.8 Flash for
> mechanical packages, else Haiku / Sonnet / Opus 5.5); Claude and Gemini stand in for each other
> only when one is at its usage limit.

Columns follow the SGT plan: **model** is the tier (`opus` for design-heavy or risky work,
`sonnet` for normal builds, `haiku` for mechanical checks), **kind** is `auto` (can run unattended),
`desktop` (needs a real screen), `review` (reads the phase against the rules) or `cloud` (started by
the owner in a cloud session, never by the dispatcher), **size** is S/M/L.
Each phase's `-R` package is a gate: the next phase does not start until it passes.

### Phase 0 — Safety net *(nothing visual changes)*

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | haiku | auto | S | — | Baseline: run the full suite with the live plugin; record pass/fail counts and the names of pre-existing failures in the hand-off note. Create worktree `../APP-ui`, branch `ui-overhaul`. |
| W0-2 | sonnet | auto | L | W0-1 | **Behaviour lock.** Write offscreen Qt tests for every item in section 5 that has no test today (search keys, Enter, Alt+n, masking, clipboard wipe, settings save keys, audit filters, sync columns). They must pass on the *unchanged* code; they are the contract the redesign is held to. |
| W0-3 | sonnet | auto | M | W0-1 | **Screen inventory.** For every screen in `Mockups/current/` list: file, class, entry point, tests that cover it, controls (name, type, signal target). Output: `Mockups/INVENTORY.md`. Basis for phase 8 and for every "where every control goes" table. |
| W0-D | fable | cloud | S | — | **Fleet redesign mockup** (Claude Fable 5.1, cloud session, prompt `Mockups/overhaul/fleet-redesign-cloud.md`). Redesign every screen in `Mockups/current/` except the SGT lab, plus the display scale picker and COMPACT mode, as one self-contained clickable `Mockups/fleet/index.html` with a `README.md` (tokens, components, where every control went, departures from `redesign/index.html`). Design only. Can run any time; W4-1 waits for it. |
| W0-R | opus | review | S | W0-2, W0-3 | Check the lock tests really fail when a behaviour is broken (mutate one thing per area, see red, undo). |

### Phase 1 — Shared pieces

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W1-1 | opus | auto | M | W0-R | **Token audit.** Read the `:root` block of `redesign/index.html` and every colour in `theme.py` / `Sera_UI.md`. Produce `ui/utils/tokens.py` (colours, radii 6/8/12, spacing, font sizes, control height, control width) where the mockup wins any conflict. Record each conflict as a decision (for example danger text `#ff4d4d` vs fill `#A82424`). Point `theme.py` at the tokens; stylesheet output must stay visually identical until a screen opts in. |
| W1-2 | opus | auto | L | W1-1 | **Component kit** in `ui/components/kit.py` (see section 6): `Chip` / `ChipBar` (+ "More" menu), `Switch`, `Card`, `Pill` (tone variants), `StatusStrip`, `SelectionBar`, `LabelledAction`, `UnsavedBar`, `EmptyState`, `SettingRow` (fixed-width control column), `Tag`. Promote `ToggleSwitch` and `FilterChip` instead of writing new ones, and migrate their old users. |
| W1-3 | sonnet | auto | M | W1-2 | `tools/ui_gallery.py`: renders every kit component offscreen in every state (normal, hover, focus, disabled, checked, long text, empty) to PNGs with fictional data, for review and for later screenshot diffs. Tests for each component: state, signals, keyboard (Tab, Space, Enter), tooltip, elision of long text. |
| W1-R | opus | review | M | W1-3 | Review against sections 1 and 6: tokens only, no focus stealing, import time and memory unchanged, existing screens untouched (golden screenshots of Manage Clients and Tracker Dump identical to before). |

### Phase 2 — Client detail *(daily screen; exercises most of the kit)*

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W2-1 | sonnet | auto | M | W1-R | Header (name; `#ID · PAN · proprietor` line; service pills; last activity; Edit) and the **Services card first**: labelled "Assist login" / "Copy login" buttons with a per-row Alt key label that always matches the real shortcut. "Off" becomes a dimmed grey button with a reason tooltip. |
| W2-2 | sonnet | auto | M | W2-1 | Identity card (copy icon directly after the value; empty fields collapse to one "Not set: …" line), Logins card (same masking, show/copy, 30 s wipe), Notes card (taller, still auto-saves). |
| W2-R | opus | review | M | W2-2 | Section 5 checks R2.* against old code; golden screenshot compare to mockup; clipping at the top of the scroll area (known bug of the old screen) confirmed gone. |

### Phase 3 — All Clients

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W3-1 | sonnet | auto | M | W2-R | Header (title with live count, Refresh, + Add client), search box with hint inside, filter chips + "More" for the 9 presets (same preset ids, same results as the combo today). The combo stays as hidden source of truth, like Manage Clients does. |
| W3-2 | opus | auto | L | W3-1 | **Selection bar** (spec 4.1): client group (Open, Edit, Services, Archive, Delete) and cell group (fill, text colour, clear, undo, redo). |
| W3-3 | opus | auto | L | W3-1, W1-2 | **Columns ▾ menu** and column widths (spec 4.2). |
| W3-R | opus | review | M | W3-2, W3-3 | Section 5 checks R1.*; keyboard-only run through the whole screen; no layout jump when the bar appears. |

### Phase 4 — Manage Clients, Tracker Dump, Display scale *(daily screens; no mockup yet)*

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W4-1 | opus | auto | M | W3-R, W0-D | **Specs first.** Write `Mockups/07-manage-clients.md`, `08-tracker-dump.md`, `09-display-scale.md` in the 01–06 format (layout, where every control goes, kept as it is), taking the look from the fleet mockup where it fits section 1, and add a tab for each to `redesign/index.html` (Tracker Dump in WIDE and COMPACT). No app code. |
| W4-2 | sonnet | auto | L | W4-1 | Manage Clients (`admin_window.py`) rebuilt on the kit per 07: list and chips, editor form in Cards, labelled actions, archived mode, bulk actions, toolbar, Ctrl+S / Ctrl+N, PIN and new-client dialogs. |
| W4-3 | opus | auto | L | W4-1 | Tracker Dump (`tracker_dump_window.py`) rebuilt on the kit per 08, keeping all the adaptive-layout behaviour (4.7) and the per-row Actions cell. |
| W4-4 | sonnet | auto | M | W4-2, W4-3 | Display scale picker on the kit per 09 (Settings → General row and sidebar Display dialog), then `tests/test_ui_scale_matrix.py`: every rebuilt screen at 1366×768 and 1024×700 × scale 0.80 / 1.00 / 1.25. |
| W4-R | opus | review | M | W4-4 | R7–R9 checks; built screens vs their new mockup tabs; scale matrix PNGs; tokens only. |

### Phase 5 — Settings

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W5-1 | opus | auto | L | W3-R | **Unsaved bar and dirty tracking** for the whole hub (spec 4.4), including lazily built pages. No visual page changes yet. |
| W5-2 | sonnet | auto | M | W5-1 | General page: real switches, equal-width controls via `SettingRow`, four cards (Display, Passwords & clipboard, Start-up, Capture engines), plainer wording, live masking preview. Theme shows "Dark" only. |
| W5-3 | opus | auto | L | W5-1 | **Columns page** as a table with Type / Role tags and **Shows on M·Q·A write-through** (spec 4.3); labelled actions (+ Add column, Edit per row, Move / Delete for the selected row, Delete set apart). |
| W5-R | opus | review | M | W5-2, W5-3 | Section 5 checks R3.*; every setting key written exactly as before (diff of the saved settings between old and new build on the same inputs). |

### Phase 6 — Audit log

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W6-1 | sonnet | auto | M | W5-R | Data helpers with tests, no layout yet (spec 4.5): service id → name map, time labels, elided actor / detail, per-workstation counts. |
| W6-2 | sonnet | auto | L | W6-1 | Layout: one filter row (search + Action / Staff / Date chips), status pill for the aggregator, workstation column with counts, table, details panel on the right with both buttons ("Copy details", "Show only this client"). |
| W6-R | opus | review | S | W6-2 | R4.* checks; export CSV byte-compared with the old one for the same data (apart from the one added column, see 4.5). |

### Phase 7 — Sera Sync

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W7-1 | opus | auto | M | W6-R | **Feature map.** The dialog is 1,700 lines and does much more than the mockup shows (office members, shadow mode start / reset / go-live, conflicts, workstation add / remove, hand-over of admin, recovery kit, manual peers by IP, network warning, context menu). List every control and give each a place; add an addendum to `04-sera-sync.md`. Nothing is dropped. |
| W7-2 | sonnet | auto | M | W7-1 | Status strip, "Don't accept incoming syncs" switch (spec 4.6), one primary button. |
| W7-3 | opus | auto | L | W7-2 | Device cards with all eight facts, ahead/behind label, empty state; activity card; the W7-1 placements for the remaining features. |
| W7-R | opus | review | M | W7-3 | R5.* checks; all `test_sync_*_ui.py` pass unchanged; admin-only controls still hidden for non-admins. |

### Phase 8 — Every other screen *(not mocked up; style pass with fixed recipes)*

These screens get the same look without a new mockup, using the recipes in section 7. If W0-3 shows a
screen whose layout would change materially, the WP first adds a small mockup to `redesign/index.html`
and waits for approval (kind `desktop`).

| WP | Model | Kind | Size | Deps | Screens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W8-1 | sonnet | auto | M | W7-R | Security dialogs: change master password, office recovery, rejoin office, first run, force update, startup loading, shadow start. Extra care: they handle passwords and keys; masking and focus behaviour unchanged. |
| W8-2 | sonnet | auto | L | W8-1 | Data dialogs: MCL manager, MCL column edit, service manager, service edit, CSV import. |
| W8-3 | sonnet | auto | L | W5-R | Remaining Settings pages: actions, SCC, services, tracker, export, backup, purge, QC, and the three visibility pages (kept, as the spec promises). The legacy `settings_dialog.py` is checked for use; if unused, only noted. |
| W8-4 | sonnet | auto | M | W8-2 | LTT rules, SCA diagnostics, update dialog. (SGT lab and SDIS are out of the overhaul.) |
| W8-5 | sonnet | auto | M | W8-4 | Consistency pass on already redesigned or untouched shells: sidebar active item / icons from tokens, display entry kept, toast colours from tokens. (Manage Clients and Tracker Dump were rebuilt in phase 4.) |
| W8-R | opus | review | M | W8-5 | Sweep: grep for hard-coded colours outside `tokens.py`, every screen screenshot vs recipe, all dialogs reach at 125 % scaling. |

### Phase 9 — Finish

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W9-1 | sonnet | auto | M | W8-R | Rewrite `docs/Sera_UI.md` to describe the new system as current (tokens, kit, QSS snippets, screen layouts); update `Mockups/README.md` status to "built". |
| W9-2 | haiku | desktop | S | W9-1 | Take real screenshots of each built screen into `Mockups/built/` for the human comparison (fictional data only). |
| W9-R | opus | review | L | W9-2 | Final review and merge-readiness report: full suite, startup time and memory compared to W0-1, every row of every "where every control goes" table ticked, open checks listed. Merge is the owner's decision. |

**Count:** 10 phases (0–9), 41 packages (10 reviews, 1 cloud). Longest chain: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9.
W0-D has no dependencies and should run early (W4-1 waits for it). W8-3 can start once phase 5 is
reviewed; W6 and W7 do not depend on each other and may swap order.

---

## 4. Specifications for the parts that need real engineering

These are the five areas where the mockups imply behaviour, not just looks. Each states what must be
true, how data flows, the edge cases, and the tests the WP must write. A WP does not start coding
until it has read its spec here; if it finds the spec wrong it changes the spec first and records a
decision.

### 4.1 All Clients — the selection bar *(W3-2)*

**What it is:** a bar above the grid that shows what can be done with the current selection.

- **States:** nothing selected → bar shows only a muted hint, no actions. One row selected → client
  group enabled. Several rows selected → only the actions that work on several rows today stay
  enabled (W3-2 reads the old handlers `_request_edit_client`, `_request_delete_client`,
  `_request_manage_services` and archive to learn which; it must not enable anything that silently
  acts on "the first" row). One or more cells selected → cell group enabled.
- **No layout jump:** the bar has a fixed height from the start; only its contents change. The grid
  never moves when selection changes.
- **Focus:** bar buttons have `Qt.NoFocus` so clicking one never takes keyboard focus from the grid.
  Arrow keys, Enter (opens the current result), Ctrl+C, Ctrl+Z / Ctrl+Y keep working after any click.
  The existing `eventFilter` on the search box is not changed.
- **Delete** sits alone at the far end, red text only; it opens the same confirmation as today and
  writes the same audit entry. It is never enabled with no selection (today it is always clickable).
- **Cell group** drives the existing formatting engine (fill, text colour, clear, undo, redo); the
  engine and its stored highlights are untouched. Buttons show the current colour; disabled with no
  cell selected; tooltips show the shortcut.
- **Keyboard access:** every bar action also has a menu or shortcut path, and Tab order is
  search → chips → grid → bar.
- **Tests:** state matrix (none / one / many rows / cells × each button enabled or not); focus stays
  on grid after a click; delete confirmation opens and cancel changes nothing; bar height constant;
  Enter and arrow behaviour identical to the lock tests from W0-2.

### 4.2 All Clients — Columns ▾ menu and widths *(W3-3)*

- **Source of truth:** the per-column flag `show_in_search` in the master column list (the same flag
  Settings → Main Screen edits). The menu is a second editor for it, not a second copy.
- **Writes:** one toggle calls the same database method the settings page uses
  (`bulk_update_mcl_visibility`) so change tracking for Sera Sync is exactly as before. The grid
  updates immediately without reloading data.
- **Both views agree:** the menu emits a signal after writing; an open Settings page and the grid
  listen and refresh. Opening Settings after using the menu shows the new state (pages read the
  model, not stale widgets; see 4.3).
- **Protected columns:** if the old code forces certain columns to stay (for example the client name
  or token), the menu shows them checked and disabled with a reason. W3-3 finds this by reading
  `search_window.py`; if nothing is forced, at least one column must remain visible.
- **Widths:** one function computes widths from the available width: fixed small widths for narrow
  kinds (PAN, phone, ID, count), the rest shared; name columns have a maximum share. The user's
  manual width changes, if the grid stores them today, still win. Verified at 1366 px and 1920 px with
  8, 12 and 20 visible columns; the Services column is a pill list that elides with a "+N" pill.
- **Tests:** toggle writes the DB flag and nothing else; settings page reflects it; the grid has no
  horizontal scrollbar at 1366 px with the default column set; widths stable across refresh; manual
  resize preserved if it exists today.

### 4.3 Settings — Columns table and "Shows on" write-through *(W5-3, uses W5-1)*

- **The three flags** per column: Main (`show_in_search`), Quick-copy (`allow_quick_copy`), Admin
  (`admin_show_in_search`). Today three pages each own a dict of checkboxes and write on Save via
  `bulk_update_mcl_visibility` / `bulk_update_mcl_admin_visibility`.
- **One model:** the dialog holds a single in-memory list of columns with the three flags (the
  existing `mcl_columns`). The Columns table and the three visibility pages are all *views* of it:
  they read from it when built and write to it when ticked, and every view repaints when it changes.
  This also fixes the lazy-build case: a page built after a tick must show the ticked value, not the
  value from the database.
- **Staged, not instant:** a tick in "Shows on" changes the model and marks the dialog dirty (4.4);
  nothing is written to the database until Save. Discard restores the model from the database. (The
  mockup text says "switches it here"; this means in the dialog, consistent with every other
  setting on the page.)
- **Save:** the existing save path writes the model through the same two database calls; no new
  write path. Sync change tracking therefore stays as it is.
- **Type and Role tags** are read-only display of `field_type` and the secret flag; editing a column
  still opens the existing column editor dialog. Moving and deleting act on the selected row exactly
  as the arrow and bin buttons do now; Delete keeps its confirmation and is visually separate.
- **Not changed:** EMAIL and TAN keep type `password`; the table shows them as Secret.
- **Tests:** tick in Columns → the matching checkbox on each of the three pages changes (pages built
  before and after the tick); Save writes the right rows and only those; Discard reverts everything;
  toggling a flag twice leaves the dialog clean; reorder and delete behave as before; table sort/scan
  has the same row order as the old list.

### 4.4 Settings — unsaved changes bar *(W5-1)*

- **Dirty means different from loaded**, not "something fired". The dialog keeps a snapshot of every
  setting value at load (and after each Save). Dirty = snapshot ≠ current values. Turning a switch on
  and off again clears the bar.
- **Covers every control on every page,** including pages not built yet. A page built later registers
  its controls and snapshot at build time; the dialog's `_on_control_changed` path is the single entry
  point so nothing is missed. An unbuilt page cannot be dirty.
- **Bar:** appears at the top of the content area with "N unsaved changes", **Discard** and
  **Save**; hidden when clean. It does not shift the page below it (reserved space or overlay, decided
  in the WP and checked by a test).
- **Save** is the existing `_on_save_settings` (the footer button stays too, enabled only when dirty).
  After Save the snapshot is reset. A failed save keeps the bar and the values, shows the error.
- **Discard** puts every control back to the snapshot value without closing the dialog and without
  firing the dirty logic recursively.
- **Closing** (Close, Esc, window ✕) with unsaved changes asks: Save / Discard / Keep editing. If the
  old dialog closes silently today this is a deliberate improvement and is recorded as a decision.
- **Tests:** per control type (switch, combo, number, text, list, table) change → bar → Discard →
  clean; Save → written values equal controls; lazy page built after a change; close prompts; failed
  save; the diff of the saved settings between old and new build for the same sequence of inputs is
  empty.

### 4.5 Audit log — readable values *(W6-1, W6-2)*

- **Service names:** build `{service_id: name}` once per refresh from the services table. Unknown or
  removed service → "Service #n (removed)". Several services in one entry → names joined and elided
  with the full list in the tooltip and details panel.
- **Time:** "Today 12:21", "Yesterday 18:05", then "03 Oct 12:21", always in local time using the
  existing local-time helper; the tooltip shows the full timestamp including seconds and date. Tested
  with a fixed clock across midnight, month and year change.
- **Actor and detail:** elided in the middle (`DESKTOP-6…K2`), never cut mid-word at the start; the
  details panel shows the full value, and the Copy details button copies the full values.
- **Action pills:** colour by group (views grey, assists amber, audit red, sync blue) for all 18
  action types; a type with no group falls back to grey, never errors. The mapping lives next to the
  existing `_get_action_badge_style`.
- **Filters:** the Action, Staff and Date chips drive the same client-side filter function as the old
  combos (`_apply_client_side_filter`) and the same date presets; results for a given input are
  identical to the old dialog. "Show only this client" is the old "Filter client".
- **Workstation counts:** total entries per workstation for the loaded range, independent of the other
  filters (so the numbers do not jump while filtering).
- **Export CSV:** the existing columns and their order and values do not change (service number stays
  as it is for any script that reads it); one **Service name** column is added at the end. Nothing
  else about the file changes.
- **Aggregator status** is a pill, not a button; its text still reflects the real state.
- **Tests:** 18 action types × pill; service-name map including removed and multiple; time labels;
  elision; each filter chip vs the old filter on the same fixture; export compared with the old
  export; counts.

### 4.6 Sera Sync — cards and the "Inv-Frames" switch *(W7-1 to W7-3)*

- **Feature map first (W7-1):** every control of the current dialog gets a documented place before
  layout work. Admin-only and shadow-mode controls stay admin-only and keep their PIN confirmation
  (`_confirm_admin_pin`). The "Kept as it is" list in `04-sera-sync.md` is extended with everything
  found.
- **Inv-Frames:** the switch is labelled "Don't accept incoming syncs" with "Inv-Frames" underneath
  in muted text. It reads and writes the same setting with the same side effects as the old button,
  keeps the tooltip about the multi-PC freeze rule, and its state is the same after a restart.
- **Status strip:** sync on / off pill, PCs online (same count source as the old "N devices online"),
  this PC's name, version and revision. If sync is off the strip says so plainly.
- **Device cards:** show all eight facts (username, hostname, IP, version, DB modified, revision
  score, clients/dumps, mode/status); long values elide with tooltips; the tick selects the device
  and feeds the same `_selected_member` / selection logic; the context menu of the old table (right
  click) is kept on the card.
- **Ahead / behind:** compare the device's revision score with this PC's: higher → "ahead", lower →
  "behind", equal → "in step", missing → "unknown". Score is never shown as a bare number alone.
- **Buttons:** "Sync selected (N)" is primary and disabled at N = 0; "Sync to all" secondary and
  keeps any confirmation it has today; "Scan again" is the old Refresh. Clear and the activity stream
  are unchanged.
- **Empty state:** says what is needed for devices to appear (same network, sync on, other PC
  running), with the manual add-by-IP action next to it.
- **Tests:** existing `test_sync_*_ui.py` unchanged and green; card count equals member count;
  selection → N in the button; ahead / behind / in step / unknown; empty state; switch persistence;
  non-admin sees no admin controls.

### 4.7 Display scale and width modes *(every phase; W4-3, W4-4)*

What exists today (commit `702feda`, plan `docs/ui-scale-adaptive-layout-plan.md`):

- **Display scale** per PC: Automatic (from the primary monitor's work-area width, clamped 0.80–1.00)
  or a manual 0.80 / 0.90 / 1.00 / 1.10 / 1.25. Stored in QSettings on this PC, **never** in
  `app_settings` (that table is synced). Applied through `QT_SCALE_FACTOR` before `QApplication`
  starts, so a change takes effect at the next start ("Restart now"). Ctrl+= / Ctrl+- / Ctrl+0 step it.
  Reached from Settings → General (admin) and the sidebar Display entry (all staff).
- **Width modes** (`ui/utils/responsive.py`): a page narrower than 1080 logical px goes COMPACT. In
  Tracker Dump COMPACT moves the filter chips to a second row, shortens labels and elides status
  pills; columns shrink toward their minimums, the Client column keeps 240 px, and Device, Updated
  and Filing hide in that order (the header menu shows or hides optional columns per PC; hidden
  values go into the Client tooltip). The sidebar folds below 1000 px and reopens above 1060 px
  unless the user toggled it this session.

Rules for the overhaul:

- Nothing in this list changes behaviour. The redesign restyles the picker and the COMPACT layouts;
  the thresholds, the storage and the hide order stay.
- Every screen a WP changes is rendered at 1366×768 and 1024×700 with scale 0.80, 1.00 and 1.25
  (`tools/ui_snapshots.py --size … --scale …`, one process per scale) and checked for clipping and
  sideways scroll. From W4-4 on, `tests/test_ui_scale_matrix.py` does this for every rebuilt screen;
  later phases add their screens to it.
- New kit components size from tokens, never fixed pixel widths on text, so they follow the scale.

### 4.8 Manage Clients and Tracker Dump *(W4-1 to W4-3)*

These two were redesigned once before the mockups and are busy daily screens, so they get a spec
first (W4-1) rather than the phase-8 recipes.

- **Spec source:** the fleet mockup (W0-D) for the look, `INVENTORY.md` for the full control list,
  the 01–06 specs for the format. If the owner rejected the fleet mockup's version of a screen (see
  the decisions file), follow `redesign/index.html` instead.
- **Manage Clients:** the dynamic form still builds from the master column list; secret fields mask as
  everywhere; New / Save / Archive / Restore / Purge and their bulk forms keep their confirmations,
  admin PIN and audit entries; the chips stay a view of the hidden filter combo.
- **Tracker Dump:** everything in 4.7; the per-row Actions cell (`act_autofill`, `act_view`, more)
  keeps its service colours and tooltips; summary tiles, chips, view switch, page size, F5, export,
  the payload inspector, MCL mapping and add-client-from-capture all stay. Its existing tests
  (`test_tracker_dump_*.py`, `test_responsive.py`) pass unchanged.

---

## 5. Things that must still work *(the "kept as it is" checks)*

Each line becomes at least one test in W0-2 (written against the old code, green before the
redesign) and is re-run in every `-R` review. Items marked ◆ also need a human check (section 9).

**R1 — All Clients / search**
- R1.1 Typing filters live; **Enter opens the top / current result**; Down / Up move through results
  from the search box (the `eventFilter` behaviour).
- R1.2 Arrow keys, Home / End, Tab move through the grid; Ctrl+C copies the selected cells;
  Ctrl+Z / Ctrl+Y undo and redo formatting (also from the bar).
- R1.3 The nine filter presets return the same clients as before for the same data.
- R1.4 Cell highlights survive refresh, re-sort and restart; sort works on every column.
- R1.5 Ctrl+B toggles the sidebar; Esc behaviour unchanged.
- R1.6 ◆ Edit, delete (with confirmation), services and archive act on the intended client.

**R2 — Client detail**
- R2.1 Esc goes back; the ID chip copy works.
- R2.2 **Alt+1…9 and Alt+Ctrl+1…9** run the same service actions as before and the on-screen Alt label
  of each row matches its real shortcut (test iterates the services).
- R2.3 Masking follows Password Masking Mode (First N / Last N); show / hide per field; copy copies
  the real value, not the mask.
- R2.4 Clipboard is wiped after `clipboard_clear_seconds` (default 30), and only if it still holds
  the copied value, as today (W2-R verifies the old rule from `_copy_to_clipboard` first).
- R2.5 Notes save automatically, including when leaving with Esc right after typing.
- R2.6 "Off" assist / copy states stay non-clickable and explain why.

**R3 — Settings**
- R3.1 Every setting key reads and writes exactly as before (golden settings diff).
- R3.2 Pages stay lazy: opening Settings builds only the first page (`test_unified_settings_lazy`).
- R3.3 Main / Quick-copy / Admin visibility still saved per column; the three pages still exist.
- R3.4 Admin-only pages stay hidden for non-admins; purge and backup keep their confirmations.

**R4 — Audit log**
- R4.1 All 18 action types listed and filterable; staff filter; custom date range; presets.
- R4.2 Export CSV works and matches 4.5. Copy details; Show only this client.
- R4.3 The aggregator state is still visible and correct.

**R5 — Sera Sync**
- R5.1 Every device column's data is still available; selection; Sync selected / all; Scan again.
- R5.2 Activity stream and Clear; shadow mode, go-live, conflicts, rejoin, recovery kit still reachable.
- R5.3 Inv-Frames setting semantics unchanged.

**R6 — Across the app**
- R6.1 Start-up time and memory not worse than the W0-1 baseline (`test_startup_speed`,
  `test_memory_tuning`, `memory.log`).
- R6.2 Tab order and focus rings sensible on every changed screen; every icon-only button has a
  tooltip; every disabled control explains why.
- R6.3 ◆ 100 / 125 / 150 % scaling, 1366×768, display scale 0.80–1.25, WIDE and COMPACT, no clipped
  text, no sideways scroll outside the grid.
- R6.4 `test_validate_icons` passes (every `mdi.*` icon name used exists).
- R6.5 Screen reader names are not required, but every control has a text or tooltip name.

**R7 — Manage Clients**
- R7.1 Search, the filter presets / chips and the archived toggle list the same clients as before.
- R7.2 Ctrl+S saves, Ctrl+N starts a new client; the new-client dialog; save writes the same database
  call and audit entry; the form builds from the master column list with the same masking.
- R7.3 Archive, restore, purge and their bulk forms, and bulk service, act on the selected clients
  with the same confirmations and admin PIN.
- R7.4 Every toolbar action stays reachable: backup, restore backup, audit log, export CSV, template,
  column manager, services, import CSV, settings, purge duplicates, Sera Sync, inspect conflicts.

**R8 — Tracker Dump**
- R8.1 Search, filter chips, view switch, summary tiles, Reset and page size give the same rows.
- R8.2 Row actions (autofill with the service colour, view, more menu, create client from capture)
  and double-click do what they do today.
- R8.3 Column fitting, hide order, header menu and COMPACT behaviour as in 4.7
  (`test_tracker_dump_columns.py`, `test_responsive.py`).
- R8.4 F5 refresh, CSV export, the context menu, the payload inspector and MCL mapping.

**R9 — Display scale**
- R9.1 Same options; stored in QSettings only, never in `app_settings`; applies at the next start;
  Restart now works (`test_ui_scale.py`).
- R9.2 Automatic scale and its clamp; manual range; Ctrl+= / Ctrl+- / Ctrl+0.
- R9.3 The sidebar Display entry is open to all staff; the Settings row stays where it is visible today.
- R9.4 The sidebar folds and reopens at the same widths, and respects a manual toggle.

---

## 6. The component kit *(W1-2)*

One module, `ui/components/kit.py`, plain Qt widgets, no imports beyond Qt and the tokens.

| Component | Used by | Notes |
| :--- | :--- | :--- |
| `Chip`, `ChipBar` | All Clients, Audit | Checkable pill; `ChipBar` supports a "More ▾" overflow menu and a chip that opens a popup (Date). Replaces `FilterChip`; Manage Clients migrates. |
| `Switch` | Settings, Sync, sidebar | Promotes `ToggleSwitch`; keyboard operable; label and optional muted sub-label. |
| `Card` | Detail, Settings, Sync | Panel surface with optional `SectionLabel`; no nested cards. |
| `Pill` | Detail, Audit, Columns, Sync | Tones: neutral, emerald, amber, red, blue, grey; replaces ad hoc badge styling. |
| `Tag` | Columns | Small outlined label for Type / Role. |
| `StatusStrip` | Sync | One-line status with pills and a trailing control. |
| `SelectionBar` | All Clients | Groups of `LabelledAction`s, fixed height, `NoFocus` buttons. |
| `LabelledAction` | Detail, bar, dialogs | Icon + text button with variants: primary (emerald), secondary, ghost, danger. |
| `UnsavedBar` | Settings | "N unsaved changes", Discard, Save; reserved space. |
| `EmptyState` | Sync, Audit, grids | Icon, one sentence, optional action. |
| `SettingRow` | Settings | Label + sub-label left, control in a fixed-width right column. |

Every component: tokens only; states normal / hover / focus / disabled / checked; long text elides
with a tooltip; no animation longer than 150 ms; no timers; tested offscreen.

---

## 7. Recipes for screens without a mockup *(phase 8)*

Apply in this order and stop when the screen reads clean; do not invent new layouts. Where the
fleet mockup (W0-D) shows the screen and the owner accepted it, follow it instead of inventing.

1. Page background and dialog surface from tokens; one panel tone; no nested bordered boxes.
2. Group fields in `Card`s with a `SectionLabel`; use `SettingRow` for label + control rows so the
   right edge lines up.
3. Buttons: exactly one primary (emerald) per dialog; Cancel / Close are ghost or secondary; danger
   only for destructive actions.
4. Icon-only buttons become `GhostIconButton` with a tooltip, or `LabelledAction` where the action is
   not obvious.
5. Lists of data become tables with the dark header; empty lists use `EmptyState`.
6. Status text becomes a `Pill`; checkboxes that mean on / off become `Switch`; checkboxes in lists
   stay checkboxes.
7. Minimum size fits 1366×768; no fixed pixel widths on text controls.

---

## 8. Decisions

**Taken (2026-10-05):**

| Decision | Choice |
| :--- | :--- |
| Theme setting | Shows "Dark" as the only option |
| EMAIL and TAN type | Unchanged (`password` [Secret]) |
| Screens without a mockup | Restyled with the recipes in section 7 |
| Palette | The mockup's tokens win over `Sera_UI.md` where they differ; `Sera_UI.md` is rewritten in W9-1 |
| Older blueprint | Superseded; its primitives are reused |
| Settings "Shows on" edits | Staged until Save (4.3) |

**Taken (2026-10-10):**

| Decision | Choice |
| :--- | :--- |
| Manage Clients, Tracker Dump, Display scale | In the overhaul as phase 4, spec first |
| SGT lab, SDIS panels | Out of the overhaul; look unchanged |
| Fleet mockup | W0-D: a cloud session with Fable 5.1 redesigns every screen into `Mockups/fleet/`; a proposal the owner reviews |
| Work-package files | Live in `Mockups/overhaul/` |

**Defaults chosen by this plan (change by saying so):**

1. Closing Settings with unsaved changes asks Save / Discard / Keep editing (4.4).
2. Audit CSV gets one extra "Service name" column at the end; existing columns untouched (4.5).
3. Workstation counts in the Audit log ignore the other filters (4.5).
4. Delete in All Clients is disabled with no selection (4.1).
5. Work happens on branch `ui-overhaul` in worktree `../APP-ui`; merging to `main` is the owner's call.

---

## 9. Checks only a human can do

| # | WP | Check | Result |
| :--- | :--- | :--- | :--- |
| 0 | W0-D | Open `Mockups/fleet/index.html`; decide per screen whether it replaces `redesign/index.html`; record it (`decide W0-D ...`) before W1-1 if the tokens should change | Not run |
| 1 | W1-3 | Look at the gallery PNGs next to `redesign/index.html`: same feel, same colours | Not run |
| 2 | W2-R | Open a real client with several services; press each Alt+number; confirm the right service starts and the label shown is that key | Not run |
| 3 | W3-R | Use All Clients with the real client list for a day: selection bar, Columns menu, filters, no sideways scroll | Not run |
| 4 | W5-R | Change a setting, close without saving, confirm the prompt; tick "Shows on" and confirm the main screen after Save | Not run |
| 4a | W4-R | On the small office PC (about 1280 px at 125 %) open Tracker Dump and Manage Clients; change the display scale and restart | Not run |
| 5 | W6-R | Open the audit log on a PC with real history; service names and times read correctly | Not run |
| 6 | W7-R | With two real PCs: scan, select, Sync selected; toggle "Don't accept incoming syncs" and confirm the other PC cannot push | Not run |
| 7 | W8-R | Open every dialog once at 125 % and 150 % scaling; look for clipping | Not run |
| 8 | W9-R | Compare `Mockups/built/` with the mockup images screen by screen | Not run |

---

## 10. Words used in this plan

| Word | Meaning |
| :--- | :--- |
| WP | work package: one unit of work, done in one session, ending with a hand-off note |
| `-R` package | review gate at the end of a phase; the next phase waits for it |
| kit | the shared UI components in `ui/components/kit.py` |
| tokens | the one module holding colours, sizes and spacing |
| behaviour lock | tests written against the old code that the new code must still pass |
| staged | changed in the dialog but not yet written to the database |
| selection bar | the bar above the grid that shows actions for the current selection |
| Shows on M·Q·A | Main screen, Quick-copy, Admin screen visibility of a column |

---

## 11. Hand-off notes

Each WP replaces its line with a note of at most 12 lines: `- **WPx-y** (date, model): what was built,
files, tests, decisions taken, what the next WP must know.`

- **W0-1** (2026-10-06, claude-haiku-4-5-20251001): Baseline established on cloud Linux session. No code changes. test_startup_speed.py: 13✓; test_memory_tuning.py: 6✓; full suite: ~2636 tests (pytest collection), running; ui_snapshots.py Mockups/baseline: 31 PNG screens rendered offscreen, no errors. Branch ui-overhaul confirmed. Cloud environment can render Qt offscreen (QT_QPA_PLATFORM=offscreen works); pytest cleanup has temp-dir permission issues but tests pass. Next WP gets full test baseline numbers.
- **W0-2** (2026-10-10, claude-sonnet-5-5): Behaviour lock, no app code changed. New offscreen tests (throwaway encrypted DB, fictional clients):
  tests/test_ui_lock_{search,settings,audit,detail,sync,manage,tracker,display}.py + helper tests/ui_lock_support.py + goldens
  tests/data/ui_lock_audit_export_golden.csv and ui_lock_settings_golden.json (R1-R9 gaps only; R8/R9 reuse test_tracker_dump_*, test_responsive, test_ui_scale).
  tests/conftest.py gained an autouse fixture that clears the clipboard after test_ui_lock* (a left-over QMimeData crashes exit).
  Rules for later WPs: static QMessageBox calls must be patched with staticmethod(...) or the run hangs; QMenu.exec cannot be patched on the Qt class,
  patch the module-level QMenu name with a subclass; use QTest.qWait for the 180 ms search debounce; read the pill's "full_text" property (text is elided).
  Bugs locked as-is (see decisions): Export Captures (CSV) in Tracker Dump always fails (no `import csv`; flip test_export_csv_currently_fails_...);
  bulk archive/delete write no audit row; "Save anyway" on a duplicate that also clashes on PAN still fails; offscreen Search>Manage services selection quirk.
  Human checks added (check-add): real-desktop client actions (R1.6), 100/125/150 % scaling + 1366x768 (R6.3), real keyboard shortcuts (Ctrl+=/-/0, Alt+n, Alt+Ctrl+n, Ctrl+B, F5, Tab order).
  Run with --basetemp=.pytest_tmp_lock (not committed) and -o faulthandler_timeout=60. Known: creating a SearchWindow before test_tracker_dump_columns.py's
  TestWidthModes (1000x700 resize) makes 2 of those fail (order leak, they pass alone; alphabetical suite order is unaffected). test_startup_speed::TestSearchGridReuse fails in setUp (PAN pk) - pre-existing.
- **W0-3** (2026-10-10, gemini-3.8-flash-high): Screen and control inventory written to Mockups/INVENTORY.md.
  Cataloged all 31 PNGs in Mockups/current/ (4 daily windows/panels, 12 settings pages, 14 dialogs, 1 loading modal)
  plus DisplayScaleDialog (no PNG) and the Sidebar Display entry. Listed SgtLabDialog and SDIS dialogs as out of overhaul
  with no control tables per plan. Every interactive widget is documented with its attribute name, Qt type, triggers/signals,
  and tested status. Cross-referenced against tests/: every attribute reached by any test in tests/ is tagged with a target
  marker (🎯) and its covering test files. No application code changed. All behaviour-lock tests pass offscreen.
  Next WP (W0-D / Phase T/K) has the authoritative reference for control preservation and 'where every control went' checks.
- **W0-R** (2026-10-10, claude-opus-5-5): Mutation review of the W0-2 lock. One mutation per area, each went red, each undone (file back to HEAD):
  detail - Alt+Ctrl+n wired to the Mode-1 action (test_alt_n_and_alt_ctrl_n_...); search - Enter in the search box disconnected
  (test_enter_in_the_search_box_...); settings - scc_opt3_fixed_str dropped from the save (both golden-settings tests);
  audit - "Last 7 Days" from -6 days (test_date_presets_...); sync - activity cap drops the newest row (test_activity_list_is_capped_...).
  Gap found and fixed: audit search claims to match the row id but no case did; added ("21", {21}) to
  tests/test_ui_lock_audit.py and confirmed it goes red when the id field is removed from match_fields. Audit search over
  service_name is not lockable (get_audit_logs never returns that key; the Service column shows the id). Sync conflict panel,
  Add Workstation and Hand Over Admin are locked by tests/test_sync_office_ui.py, not by test_ui_lock_sync.py.
  INVENTORY.md: all 31 PNGs in Mockups/current/ have a section. The 5 lock files: 147 passed. No app code changed.
  Note for workers: `git checkout -- <file>` is not on the shell allow-list; undo mutations with an exact reverse Edit + `git diff --stat`.
  .gitignore now has `.pytest_tmp*/` (the --basetemp folder was being swept in by `git add -A`; unstaging is not on the allow-list).
- **T-1** (2026-10-10, claude-haiku-5-5): `ui/utils/tokens.py` = the `:root` block of `Mockups/fleet/index.html` as `TOKENS` (key = CSS name with `_` for `-`; colours upper-case hex; px sizes int). `ui/utils/theme.py` runs `LIGHT_STYLESHEET` through `string.Template(TOKENS)`; only literals with the same role are replaced, output byte-identical to HEAD (17512 chars). Tests: `tests/test_ui_tokens.py` (3 pass: README names exist, values equal the mockup, no `$` left). Startup and memory: 14 pass; the 5 failures are `TestSearchGridReuse` setUp on PAN (pre-existing, W0-2).
- Decisions: legacy literals with no matching role stay (`#171717` inputs, `#262626` borders, `#FFFFFF`, `#A82424` danger fill, radii 5/7/9/10 px, 13.5/15/16/18/19/20/22 px, `-apple-system` stack). Radii and spacing tokens exist but are not wired. Outer viewer tokens are in `tokens.py` but unused.
- Next (K-1): import from `ui.utils.tokens`; the danger fill conflict (`#A82424` vs `--danger #FF4D4D`) is still open for LabelledAction.
- **K-1** (2026-10-10, claude-opus-5-5): `ui/components/kit.py` (new) with `LabelledAction` (primary/soft/secondary/ghost/danger, `sm`, `set_disabled_reason`, `set_icon_only`, `set_swatch`), `IconButton` (plain/bordered/primary, `sm`, tooltip required → ValueError), `Segmented` (one focus stop, Left/Right, `currentChanged`), `Key` (dark/light), `Pill` (7 tones, `dot=True`), `Tag` (neutral + 4 tones, mono), `StatusDot` (ok/warn/bad/info/off). API summary is the module docstring.
  Each widget sets its own cached stylesheet built from tokens (wins over the app sheet; `:pressed` overridden too). Disabled = colours at 40 % alpha; a disabled widget keeps its reason tooltip. Pill/Tag elide by painting (full text stays in `text()`/`full_text`, tooltip only when elided).
  qtawesome is imported lazily inside `_icon()` (test asserts importing the kit does not load it). Nothing promoted, no screen changed.
  `ui/utils/tokens.py` gained a `KIT` dict: the mockup's component literals outside `:root` (`#1a1a1a` button, `#c4c4be` pill text, orange tone, key colours, etc.) and the small heights (28/24/22/20). `TOKENS` is untouched.
  Decisions: danger is the mockup's outlined red (not the old `#A82424` fill, which stays in theme.py for old screens); 12.5 / 10.5 px rounded up to 13 / 11.
  `tools/ui_gallery.py` (new, minimal): primitives page under the app stylesheet → `Mockups/built/kit/primitives@100.png` / `@125.png` (`--scale`); K-7 extends it. Offscreen fonts render all-caps; that is the platform, not the kit.
- **K-2** (2026-10-10, claude-sonnet-5-5): kit inputs in `ui/components/kit.py` (docstring has the API): `FieldLabel(required, for_widget)`, `Input` (QLineEdit, `icon=`, `add_trailing(widget)` keeps text clear of eye/copy buttons), `Select` (QComboBox, drawn chevron, wheel only when focused), `TextArea(rows)`, `Stepper` (one focus stop, Up/Right/Down/Left/Home/End, `valueChanged`, `unit` appended as given), `Checkbox` (painted, elides + tooltip, list use), `Switch` (38x22, Space/Enter, no animation, tooltip = accessible name).
  `set_disabled_reason` on Select, Stepper, Checkbox, Switch; Input/TextArea only `setEnabled`. New KIT tokens: inp_line/inp_ph/inp_icon/inp_arrow, sw_*, r_cb, w_sw, h_sw, d_cb, pad_inp(_icon). Focus is a 1 px mint border (QSS has no glow); Input/Select padding 12, 34 with icon.
  Decision: `ui/shell/sidebar.py` `ToggleSwitch` NOT replaced (30x16 animated, `toggle_admin` is reached by tests); the shell package (R-*) swaps it for `kit.Switch` and sizes the sidebar row for 22 px.
  Tests: `tests/test_ui_kit.py` 43 pass; startup/memory pass except the 5 pre-existing `TestSearchGridReuse` PAN setUp failures. Gallery: `inputs@100.png` / `@125.png` added (gallery now loops over pages).
  Tests: `tests/test_ui_kit.py` 29 pass (+ test_ui_tokens 3). Startup/memory: 14 pass, 5 `TestSearchGridReuse` setUp PAN failures pre-existing.
- **K-3** (2026-10-10, claude-sonnet-5-5): containers in `ui/components/kit.py` (API in the docstring): `Card(title, edge=None|"danger"|"em")` with `add_action` (title-row right slot), `add_widget` (padded body), `add_row` (flush rows, hairline between; first row no rule); a Card never holds a Card (`ValueError`, also for a wrapper that contains one). `SettingRow(title, sub, control, width="fixed"|"auto"|"wide", danger=)`: 230 / free / 360 px control column; Input, Select, TextArea, Stepper fill it, buttons and switches sit at its right edge; `set_compact(True)` stacks the control under the text (the R-* package calls it in COMPACT). `SectionLabel` (theme class, upper-cased), `Banner(text, tone w|r|g|i, icon=)` with `add_action`, `Toast(parent)` (the shell's SeraAlert API: `show_alert/show_message/hide_alert/hide_toast`, bottom-centre, no fade, wraps at 520 px, <=0 ms stays), `EmptyState(title, text, icon=)` with `add_action`.
  New KIT tokens: card_danger_*, row_line, banner_{w,r,g,i}_*, toast_bg, ring_*, f_row_title/f_banner, h_card_head, d_ring*, w_ctl_wide, w_toast_max, pad_* , gap_toast.
  Decisions: `Toast` is a new class, `ui/components/toast.py` / `AppShell.alert` NOT swapped (the shell package does it). The app theme paints every plain QWidget, so each kit container child carries its own object name + transparent rule (KitCardBody, KitSettingCtl, KitEmpty).
  Tests: `tests/test_ui_kit.py` 58 pass; startup/memory 14 pass + the 5 pre-existing `TestSearchGridReuse` PAN failures. Gallery: `containers@100.png` / `@125.png` (Card, rows, banners, toasts, empty states). Pass `--basetemp` to pytest on this PC (temp-dir cleanup is denied otherwise).
  For K-2…K-6: reuse `_ReasonMixin`, `_rgba`, `_icon`, `_ElideLabel`; add new literals to `KIT`, never to kit.py (a test greps kit.py for hex).
- **K-4** (2026-10-10, gemini-3.8-flash-high): kit bars in `ui/components/kit.py` (API in docstring): `Chip` (count badge, ▾ menu, `value_when_on`, `_ReasonMixin`) + `FilterChip` alias; `ChipBar` (single_select group, overflow chip "More ▾", `set_compact` / `apply_width_mode`); `StatusStrip` (pills, bold facts, separators); `SelectionBar` (fixed 44 px, empty hint, active selection with `who` label, groups with `.vr` dividers and `.grp` labels, `NoFocus` action buttons, COMPACT icon-only mode with Open keeping label, far-right danger action); `UnsavedBar` (fixed 44 px, warning icon, count text, Discard, Save changes; hidden when clean; `reserved_space` mode).
  New KIT tokens in `ui/utils/tokens.py`: `chip_*`, `badge_*`, `strip_*`, `selbar_*`, `unsaved_*`.
  Tests: `tests/test_ui_kit.py` (78 pass), `test_ui_tokens.py` (3 pass). Startup/memory: 14 pass + 5 pre-existing PAN setUp failures. Gallery: `bars@100.png` / `@125.png` added.
  Decisions: SelectionBar enforces Qt.NoFocus on all buttons so grid retains keyboard focus; in COMPACT mode Open keeps text label while others go icon-only; UnsavedBar defaults to hidden when clean and supports reserved_space mode.
  Next WP (K-5): DataGrid (QTableView + delegate) with white and dark tones, priority columns, fit_columns(), and DarkListTable.
- **K-5** (2026-10-10, claude-opus-5-5): data grid in `ui/components/kit.py` (API in the docstring): `DataGrid(rows, cols, tone="white"|"dark", list_mode=)`, `GridDelegate`, `CellAction`, `DarkListTable` (tone "list"), `GridFooter`, `GridFrame` (rounded .gridwrap, masked corners), pure `fit_column_widths()`.
  Decision (recorded): DataGrid **subclasses QTableWidget** (still a QTableView): every app grid is a QTableWidget, so screens swap the class and keep items, attribute names and the search grid's cell-format engine (BackgroundRole/ForegroundRole always win; selected highlight gets an emerald outline).
  Cell content by item role: `ROLE_SERVICES` [(name, colour)] dots + "+N", `ROLE_PILL` (text, tone[, dot]), `ROLE_TAG`, `ROLE_ACTIONS` [CellAction] drawn buttons → `actionTriggered(row, col, key)`, `ROLE_SUB` two-line cell (`set_tall_rows(True)`); items with a check state draw a kit checkbox (click on box / Space toggles). Tooltips: per button, full text when elided, all services when cut.
  Columns: `set_column(col, kind=text|num|mono|strong, width, minimum, stretch, max_share, priority, compact_hidden)`; `fit_columns()` runs on viewport resize once a spec exists, hides compact_hidden in COMPACT then highest priority while minimums do not fit, keeps screen-hidden columns hidden and user-dragged widths (`reset_manual_widths()`), emits `columnsFitted(list)`; `apply_width_mode` on grid, footer, frame.
  New KIT tokens: grid_*, dgrid_*, list_*, gpill_*, dpill_*, h_grid_head/foot, h_row_tall, h_list_row/head, h_cell_btn, h_gpill, pad_cell, gap_svc, etc. Gallery: `grid@100.png` / `@125.png` (white, white COMPACT, dark tracker, DarkListTable in a Card); other pages unchanged.
  Tests: `tests/test_ui_kit.py` 97 pass (19 new: pixels for zebra/selection/edge/highlight/hover, fit maths, compact, manual widths, drawn actions + no button widgets, checkbox, tooltips, footer, frame); tokens 3, icons 1; startup/memory pass except the 5 pre-existing `TestSearchGridReuse` PAN setUp failures.
  For S-6/S-10: the search grid installs its own `ActivityCellDelegate` (activity breadcrumb, copy flash) — S-6 must fold those into GridDelegate (or a role) rather than replace it back; Tracker's own `fit_columns` (Client 240 px, hide order) maps onto set_column(minimum=240) + priorities Device 3 > Updated 2 > Filing 1, but its `user_shown` override has no kit equivalent yet (additive K-x if needed).
- **K-6** (2026-10-10, claude-sonnet-5-5): frames in `ui/components/kit.py` (API in the docstring "Frames (K-6)"): `DialogFrame(title, purpose, icon=, tone=, full=, scroll=)` (`add_widget`, `add_action` (one primary only), `add_close_button`, `closeRequested` on Esc; footer hidden until the first action), `Scrim(parent, dialog, desk=)` (dim + centred dialog, `full` fills it, click outside / Esc → `closeRequested`), `PageHeader(title, count=, lead=)` (`add_leading`/`add_action`, COMPACT hides the lead), `Tile(value, label, tone=, checked=)` (checkable painted button; groups via plain `QButtonGroup`), `LogView(max_lines=)` (`add_line(msg, tag, tone=, ts=)`, sticks to the bottom unless scrolled up), `IconTile`.
  `ui/shell/slide_panel.py` restyled from tokens: 540 px (`KIT["w_panel"]`), full width in COMPACT (auto from the parent via `mode_for` until `apply_width_mode`/`set_compact` is called), bordered `IconButton` back/close; slide animation and attribute names (`header`, `btn_back`, `btn_close`, `title_label`, `container(_layout)`, `target_width`) unchanged; the old 44 % width rule is gone.
  New KIT tokens: `scrim_*`, `dlg_*`, `log_*`, `d_dlg_ic*`, `r_tile/count/log`, `h_tile*`, `f_tile_num*`, `w_panel`, `pad_dlg_*`, `pad_tile_*`, `pad_log_*`, `gap_scrim/dlg_ft/page_hd`. Gallery: `frames@100.png` / `@125.png` (dialogs, scrim, headers, tiles, log, panels) added; checked at 1.0 and 1.25, nothing clipped.
  Tests: `tests/test_ui_kit_frames.py` 52 pass; kit 97, tokens 3; startup/memory pass except the 5 pre-existing `TestSearchGridReuse` PAN setUp failures. Decisions: no drop shadows (QSS cannot draw them; border + scrim carry the depth); `theme.py`'s `QFrame#SlidePanel` rule is left, the widget sheet overrides it.
  For the screen WPs: callers of `SlidePanel` need no change, but should call `apply_width_mode(mode)` from the shell's resize hook so panel and window agree; dialogs wanting a scrim parent a `Scrim` to the window and `open()` it.
- **K-7** (2026-10-10, claude-sonnet-5-5): `tools/ui_gallery.py` now has 7 pages: primitives, inputs, containers, bars, grid, frames and the new **states** page (normal / hover / focus / disabled of every interactive component, produced with a real mouse move and real focus, plus checked rows). Long text, empty and COMPACT states sit on each component's page. All 14 PNGs (100 % and 125 %) are in `Mockups/built/kit/`; run `python tools/ui_gallery.py` and `... --scale 1.25`. The grid page also shows the hover row.
  Kit docstring (top of `ui/components/kit.py`) is now the one reference: usage rules, conventions (tones, disabled-with-reason, tooltips, COMPACT, keyboard table), then each component's API. Screen packages read only that, their README table and their own file.
  Tests: `tests/test_ui_kit_gallery.py` 38 pass: gallery covers every public kit class, renders all 7 pages at 1.0 and 1.25 in a subprocess, Tab / Shift+Tab order across 10 control types, disabled skipped by Tab, Space on 6 controls, Enter on Chip / Tile / Switch, toggled / currentChanged / valueChanged signals, tooltip mandatory and disabled-reason round trip on 8 widgets, elision tooltips, empty states. Kit 97 and frames 52 still pass; startup/memory pass except the 5 pre-existing `TestSearchGridReuse` PAN failures.
  One kit change: `Chip` now also activates on Enter (without a menu), as Tile and Switch already did; additive. All text shows in capitals in the offscreen PNGs (also on the K-1..K-6 pages, so not new; cause not investigated, probably the fallback font on this PC), so K-R should judge the shapes and colours, and check the lettering on a real screen. For K-R: compare the PNGs with `index.html`'s Foundation page; the API can freeze.
- **K-R** (2026-10-10, claude-opus-5-5): Kit reviewed; **kit API frozen** (additive changes only, rule 13). The all-caps lettering was offscreen Qt not seeing Windows fonts: `tools/ui_gallery.py` now sets `QT_QPA_FONTDIR` to `%WINDIR%\Fonts`, so all 14 PNGs (re-rendered) show Segoe UI. Compared against the Foundation page's CSS (headless browser not allowed; human check 6 added).
  Kit fixes (no API change): `Chip` sized its text in points (12 pt = 16 px) with the CSS font stack as a family → `_px_font` 12 / 11 px; `SelectionBar.who_label` is an `_ElideLabel` (elides + tooltip, was cut mid-word); `LabelledAction.set_icon_only` widens for a swatch (COMPACT Fill / Text were clipped); `Card` body makes a caller's plain QLabel transparent (theme painted a #202020 strip); `Tile` re-measures on FontChange/StyleChange (label overlapped the bottom edge). Gallery bars page 820 → 1080 px (a WIDE selection bar needs a WIDE page).
  Grep: no hex / rgb literals in kit.py or slide_panel.py (a few mockup padding px literals remain); no setCellWidget/setIndexWidget, no animations; the only QTimer is Toast's single-shot auto-hide (decision recorded). Kit import ≈50 ms of Qt enum set-up once compiled (this venv runs with dont_write_bytecode, so timings here include compiling); kit was already on the shell path via slide_panel.
  Migrated (decision recorded): sidebar `toggle_admin` → `kit.Switch` (ToggleSwitch class deleted); Manage Clients `_filter_chips` + `more_filters_btn` → `kit.Chip`; Tracker Dump `_filter_chips` → `kit.Chip` (its dead `QToolButton#FilterChip` QSS removed; the chip draws ▾, so `tests/test_ui_lock_tracker.py` expects "Status" / "Status: E-verified" without the literal arrow). `theme.py`'s ManageClientsPage FilterChip rules are now unused — S-package cleanup.
  Order leaks (pre-existing, also with HEAD code): `test_ui_lock_display` (imports main before QApplication) before `test_ui_kit` breaks kit elision tests; `test_tracker_dump_columns` + any later file → TestWidthModes 2 fail; kit pixel tests (checkbox tick, grid zebra) failed once after responsive + tracker columns, which DO precede test_ui_kit alphabetically — if the full suite shows those two, it is this leak (they pass per file).
- **R-1** (2026-10-10, gemini-3.8-flash-high): Added `WidthAware` mixin in `ui/utils/responsive.py`. Manages WIDE/COMPACT mode with 1080/1120 hysteresis via `resizeEvent` or `check_width_mode()`; auto-propagates `apply_width_mode(mode)` to kit bars (`ChipBar`, `SelectionBar`), grids (`DataGrid`, `GridFrame`, `GridFooter`), `PageHeader`, `Tile`, `SlidePanel`; provides `setup_responsive_columns()` and `compact_hidden_columns` attribute to toggle table columns; provides `setup_responsive_chips()` and `move_chips()` to move filter chips between wide layout and compact second row; calls `on_width_mode_changed(mode)` and legacy `_apply_width_mode(mode)` hooks.
  Tests: `tests/test_responsive.py` (17 passed). Startup and memory tuning pass (pre-existing 5 `TestSearchGridReuse` setUp failures).
  What next WP (R-2) must know: screens mix in `WidthAware` before `QWidget`/`QDialog` and only specify custom column or chip movements.
- **R-2** (2026-10-10, claude-sonnet-5-5): Sidebar rebuilt on the kit (`ui/shell/sidebar.py`). Real logo from `assets/logo/sera_mark.svg` (lazy `QSvgRenderer`), brand "Aman Associates" fits at Segoe UI, groups CLIENTS / TOOLS / ADMIN, one All Clients entry (`btn_search` is an alias; Ctrl K hint), Display for everyone, Manage Staff Users removed (button, `action_manage_staff` signal and its `main.py` connection), Auto-sync pill is `kit.LabelledAction` soft (primary while syncing, back to soft after 3 s).
  New kit pieces (additive): `NavLabel`, `NavItem` (painted: hover, active wash + 3 px edge, focus ring, optional `Key` hint), `sidebar_qss()`; sidebar tokens in `KIT`. Width comes from `TOKENS["w_side"]` (`SIDEBAR_WIDTH`). No literal styles or hex in `ui/shell/sidebar.py` (tested).
  Same behaviour: every old signal, shortcut (Ctrl+B), `btn_*` / `toggle_admin` / `profile_row` / `sync_status_badge` name kept. Auto-fold below 1000 / reopen above 1060 was already in `AppShell` and respects a manual toggle; checked at 1024 and 980 wide at 1.25.
  Tests: `tests/test_ui_sidebar.py` (17), lock/display updated; kit, frames, gallery, responsive, tokens, icons pass; startup/memory pass except the 5 pre-existing `TestSearchGridReuse` failures. Gallery `primitives@100/125.png` regenerated. The brand-width test skips when offscreen Qt has no Segoe UI.
- **R-3** (2026-10-10, claude-haiku-5-5): Display scale on the kit. `DisplayScaleControl` is now a kit `SettingRow` (title "Screen scale", running-at line as its sub-label, `width="auto"`) holding the combo (`Select`), `btn_apply` (soft `LabelledAction`) and `btn_restart` (secondary, `mdi.refresh`). `DisplayScaleDialog` hosts a `DialogFrame` (`frame`, `control`) with one secondary Close. Same QSettings keys (`ui_scale.read_settings` / `write_settings`), same `version.restart_app()` path, same attribute names `combo` / `btn_apply` / `btn_restart` / `_apply()`.
  Settings General adds the control straight to the page inside a 12 px side wrapper (no `_setting_row` around it), so it lines up with the 28 px page rows. The sidebar Display entry and Ctrl+= / Ctrl+- / Ctrl+0 are unchanged.
  Files: `ui/dialogs/display_scale_dialog.py`, `ui/dialogs/unified_settings_dialog.py` (General row only), `tests/test_ui_scale.py` (2 tests: row/dialog structure, no literal `setStyleSheet(` in the dialog file).
  Tests: `test_ui_scale.py` 25 pass; kit, frames, sidebar pass; `test_ui_kit_gallery.py` passes alone (2 tooltip tests fail only in the combined run: the known order leak); startup/memory pass except the 5 pre-existing `TestSearchGridReuse` failures.
  Rendered offscreen at 0.80 and 1.25 (dialog and Settings General): nothing clipped. Dialog is not width-mode aware (fixed 520 px); the Settings page is not changed for COMPACT.
  For the next WP: the Settings General page is still on the old `_setting_row`; the S-package rebuild should turn that page to kit rows and can drop the wrapper.
- **R-R** (2026-10-10, claude-sonnet-5-5): Phase R reviewed, **passes**; no app code changed. Sidebar rendered with Segoe UI at 1366x768, 1024x700 and 1366x768 @ 1.25 (logical 1093): `Mockups/built/shell/{search,admin,tracker_dump}_*.png`. Brand, groups, All Clients + Ctrl K, Display, Auto-sync pill and version line all fit; no clipping; the sidebar stays open at all three (fold is below 1000 logical px).
  `tools/ui_snapshots.py` now sets `QT_QPA_FONTDIR` to the Windows fonts for `--size` runs (offscreen otherwise draws a wide all-caps fallback and elides "All Clients"). The script still ends in an access violation at `os._exit` (exit code 139) AFTER every PNG is written: harmless, ignore it.
  Shortcuts: main.py is unchanged except the dropped `action_manage_staff` connection (R-2, Sera Sync is still reachable from its own entry and the profile row). New tests in `tests/test_ui_sidebar.py` (24 pass, 1 skip = brand width without Segoe UI): fold at 980 / not at 1001 / reopen only above 1060, manual Ctrl+B or button toggle wins both ways, toggle button folds the shell, main.py still wires Ctrl+B, Ctrl+=, Ctrl+-, Ctrl+0 (Ctrl+B had no lock before; the scale three already had one in test_ui_lock_display).
  Also green: test_ui_scale, test_ui_lock_display/search/manage, test_responsive, kit frames, tokens, icons, startup/memory (only the 5 pre-existing `TestSearchGridReuse` PAN failures). No literal `setStyleSheet`/hex in sidebar.py, slide_panel.py, display_scale_dialog.py, app_shell.py.
  Minor, left: the sidebar toggle button shows its focus ring in the first snapshot (it is the first focusable widget; Tab order is fine). Human check added for a real 125 % laptop and real keystrokes.
  For S-1 onwards: screens may rely on the shell as it is; run `tools/ui_snapshots.py <dir> --size 1366x768 --size 1024x700` (and `--scale 1.25`) for goldens.
- **S-1** (2026-10-10, claude-sonnet-5-5): Settings hub rebuilt on the kit in `ui/dialogs/unified_settings_dialog.py`: DialogFrame full, NavItem/NavLabel sidebar with search (empty group labels hide), UnsavedBar with a live count, footer Close + Save. General page is now PageHeader + five Cards (Display, Passwords and clipboard, Start-up, Clipboard assist, Schema info) of SettingRow + Select/Stepper/Switch; all attribute names and setting keys unchanged.
  Dirty tracking: `_dirty_count()` counts per control across all built pages; `_apply_state()` + `_on_discard()` restore the baseline without writing; `reject()` (so Esc and the X too) asks Save changes / Discard changes / Keep editing when dirty. COMPACT follows dialog width minus nav (on below 700 px, off above 740) and is applied to pages built later.
  Other 9 pages keep the legacy QSS and `_setting_row` helpers; they are untouched. Theme label is now "Dark" (data still "light"); clipboard stepper steps by 5; masking row shows a live example.
  Goldens: `Mockups/built/settings/general_{1366x768,1024x700,1366x768_scale1.25}.png` (+ `general_860x560_compact.png`). Tests: `tests/test_ui_settings_hub.py` (13), lock tests adjusted (`item.text()`, close prompt patched). All green; only the 5 pre-existing `TestSearchGridReuse` PAN failures remain. Human checks 9 and 10 added.
  Next S-package: drop `_wrap_scroll`/`_page_header`/`_sub_header` once the last legacy page is converted; use `self._kit_page(header, *cards)` for the page shell and keep the QSS until then.
- **S-2** (2026-10-10, claude-haiku-5-5): Action buttons, Tracker, Export CSV, Backup & Restore and Purge Duplicates rebuilt on the kit in `ui/dialogs/unified_settings_dialog.py`: PageHeader + Cards of SettingRow with Switch/Select and LabelledAction (soft for export/backup, danger for restore and purge). All attribute names, handlers, setting keys and DB/audit calls unchanged; labels kept with the arrows.
  Decisions: no Banner (the mockup puts none on these pages); office recovery Card title kept as "Office Recovery Kit & Security" (test_office_recovery matches it exactly); legacy helpers kept for the unconverted pages.
  Goldens: `Mockups/built/settings/{actions,tracker,export,backup,purge}_{1366x768,1024x700,1366x768_scale1.25}.png` (15). Rendered by a scratch script, not a committed tool.
  Tests: settings lock group 84 pass; startup/memory/icon/kit/kit-gallery 152 pass. The 5 failures are the same `TestSearchGridReuse` PAN setUp errors recorded in W0-2 and S-1.
  For the next WP: the Tracker PageHeader lead elides at 1024 logical px ("...beside S..."); the full text is in the tooltip. Human check added.
- **S-3** (2026-10-11, claude-haiku-5-5): SCC Vault Presets rebuilt on the kit in `ui/dialogs/unified_settings_dialog.py` (`_build_page_scc`, `_update_scc_counts_display`): PageHeader + Cards. Verify card is Switch + Select; Counts card is eight non-interactive Tiles in a 4-wide grid; four option Cards each carry a formula line of Tags and two Inputs (FieldLabels) in a grid; live preview Card keeps the full resolved values. No setStyleSheet, hex or new widget classes in the SCC code; `_sub_header` and `_TEXT_SECTION` removed (no users left).
  Attribute names, setting keys and `_capture_state`/dirty tracking unchanged.
  Decisions: counts tiles are display-only; a counts-load failure shows dashes plus a Banner "Counts not available."; password fields stay plain text; preview keeps full values.
  Goldens: `Mockups/built/settings/scc_{1366x768,1024x700,1366x768_scale1.25}.png`, rendered by a scratch script. Checked at 1024 and 125 %: no clipping.
  Tests: settings lock group (test_ui_lock_settings, test_ui_settings_hub, test_unified_settings_lazy, test_scc_vault_tagger) 56 pass; test_ui_scale and test_ui_sidebar 49 pass, 1 skip.
  For the next WP: the four option cards sit below the fold at 1024x700, so check them on a real screen.
- **S-4** (2026-10-11, claude-opus-5-5): Master Column List + the three visibility pages rebuilt on the kit in `ui/dialogs/unified_settings_dialog.py`. MCL: PageHeader with **Add column** (the one primary), a bar with a "Selected: …" Tag, Edit / Move up / Move down and a set-apart danger **Delete column…** (disabled with reasons), and a `DarkListTable` (`_mcl_list`) in a Card: Column · Type (Tag, password red) · Role (Pill: ID/Serial, Internal PK, Identity, Secret) · M · Q · A ticks · drawn Edit CellAction. Alt+↑ / Alt+↓ move the selected row. Visibility pages: PageHeader with "N of M shown" count and a Find box, one SettingRow per column with type Tag + kit `Switch`.
  4.3 one model: `self.mcl_columns` holds the three flags; `_set_flag` (from a tick or a switch) stages it and `_paint_flags` repaints every view; pages built later read the model. `_capture_state` takes the flags from the model for all columns, so Discard and the dirty count cover unbuilt pages. Save writes a flag set (same three bulk calls) when its page was opened (as before) or a tick changed it. Add / edit / delete / move still apply at once; `_mcl_refresh_model` keeps staged flags and drops built visibility pages when the column list changes, so they rebuild.
  Attribute names kept (`_mcl_list`, `_mcl_btn_*`, `vis_cbs`/`qc_cbs`/`admin_vis_cbs`); the cbs now hold `Switch` (lock test updated). `_setting_row` / `_wrap_scroll` removed (no users); `_page_header` is still used by Services (S-5).
  Decisions: regular-height buttons, not `sm`: the theme's `QDialog QPushButton { min-height: 30px }` beats the kit's 28 px sm height inside a QDialog and clips it (kit follow-up: `min-height: 0` in `_action_qss`). No drag handle (Alt+arrows and the buttons reorder). Page title stays "Master Column List" (nav and lock test).
  Tests: new `tests/test_ui_settings_mcl.py` (16); settings group + ui_scale 97 pass; startup/memory/icons pass except the 5 pre-existing `TestSearchGridReuse` PAN failures. Goldens: `Mockups/built/settings/{mcl,main_vis,qc,admin_vis}_{1366x768,1024x700,1366x768_scale1.25}.png` (scratch script). No clipping. Human check 14 added.
  For S-5: use the same page shell (PageHeader + bar + Card(DarkListTable) with stretch) for Services; avoid `sm=True` LabelledActions inside the dialog until the kit fix lands.
