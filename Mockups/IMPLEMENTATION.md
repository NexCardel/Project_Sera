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
  Settings General, Settings Columns) as in the mockups, then bring every other screen onto the same
  look so the app does not end up as a few new screens among old ones.
- **How:** build the shared pieces once (phase 1), then use them screen by screen, busiest screens
  first. Each phase ends with a review that checks the "kept as it is" promises against the old code.
- **Rule of the plan:** same data, same shortcuts, same settings, same database writes. Only the
  presentation and the arrangement change. Where a mockup needs new behaviour (selection bar,
  "Shows on" write-through, unsaved bar, audit service names, Sync cards) the behaviour is specified
  in section 4 *before* any code is written.
- **Order:** 0 safety net → 1 shared pieces → 2 Client detail → 3 All Clients → 4 Settings →
  5 Audit log → 6 Sera Sync → 7 every other screen → 8 docs and final review.
- **Decided (2026-10-05):** Theme shows "Dark" as the only option. EMAIL and TAN keep type
  `password` [Secret] for now. All screens not mocked up get the same treatment (phase 7).

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
   with no sideways scroll except inside the white data grid.
9. **Do not touch** version numbers, installers or the release files (`version.json`, release
   process). This is a UI change only. Never bulk-delete files in user data folders.
10. **Match the surrounding code**: style, comment density, naming. Change only what the WP needs.

---

## 2. Where things are

| Thing | Location |
| :--- | :--- |
| Mockup specs, images, clickable page | `Mockups/` (this folder), `Mockups/redesign/index.html` |
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

---

## 3. Work packages

Columns follow the SGT plan: **model** is the tier (`opus` for design-heavy or risky work,
`sonnet` for normal builds, `haiku` for mechanical checks), **kind** is `auto` (can run unattended),
`desktop` (needs a real screen) or `review` (reads the phase against the rules), **size** is S/M/L.
Each phase's `-R` package is a gate: the next phase does not start until it passes.

### Phase 0 — Safety net *(nothing visual changes)*

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | haiku | auto | S | — | Baseline: run the full suite with the live plugin; record pass/fail counts and the names of pre-existing failures in the hand-off note. Create worktree `../APP-ui`, branch `ui-overhaul`. |
| W0-2 | sonnet | auto | L | W0-1 | **Behaviour lock.** Write offscreen Qt tests for every item in section 5 that has no test today (search keys, Enter, Alt+n, masking, clipboard wipe, settings save keys, audit filters, sync columns). They must pass on the *unchanged* code; they are the contract the redesign is held to. |
| W0-3 | sonnet | auto | M | W0-1 | **Screen inventory.** For every screen in `Mockups/current/` list: file, class, entry point, tests that cover it, controls (name, type, signal target). Output: `Mockups/INVENTORY.md`. Basis for phase 7 and for every "where every control goes" table. |
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

### Phase 4 — Settings

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W4-1 | opus | auto | L | W3-R | **Unsaved bar and dirty tracking** for the whole hub (spec 4.4), including lazily built pages. No visual page changes yet. |
| W4-2 | sonnet | auto | M | W4-1 | General page: real switches, equal-width controls via `SettingRow`, four cards (Display, Passwords & clipboard, Start-up, Capture engines), plainer wording, live masking preview. Theme shows "Dark" only. |
| W4-3 | opus | auto | L | W4-1 | **Columns page** as a table with Type / Role tags and **Shows on M·Q·A write-through** (spec 4.3); labelled actions (+ Add column, Edit per row, Move / Delete for the selected row, Delete set apart). |
| W4-R | opus | review | M | W4-2, W4-3 | Section 5 checks R3.*; every setting key written exactly as before (diff of the saved settings between old and new build on the same inputs). |

### Phase 5 — Audit log

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W5-1 | sonnet | auto | M | W4-R | Data helpers with tests, no layout yet (spec 4.5): service id → name map, time labels, elided actor / detail, per-workstation counts. |
| W5-2 | sonnet | auto | L | W5-1 | Layout: one filter row (search + Action / Staff / Date chips), status pill for the aggregator, workstation column with counts, table, details panel on the right with both buttons ("Copy details", "Show only this client"). |
| W5-R | opus | review | S | W5-2 | R4.* checks; export CSV byte-compared with the old one for the same data (apart from the one added column, see 4.5). |

### Phase 6 — Sera Sync

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W6-1 | opus | auto | M | W5-R | **Feature map.** The dialog is 1,700 lines and does much more than the mockup shows (office members, shadow mode start / reset / go-live, conflicts, workstation add / remove, hand-over of admin, recovery kit, manual peers by IP, network warning, context menu). List every control and give each a place; add an addendum to `04-sera-sync.md`. Nothing is dropped. |
| W6-2 | sonnet | auto | M | W6-1 | Status strip, "Don't accept incoming syncs" switch (spec 4.6), one primary button. |
| W6-3 | opus | auto | L | W6-2 | Device cards with all eight facts, ahead/behind label, empty state; activity card; the W6-1 placements for the remaining features. |
| W6-R | opus | review | M | W6-3 | R5.* checks; all `test_sync_*_ui.py` pass unchanged; admin-only controls still hidden for non-admins. |

### Phase 7 — Every other screen *(not mocked up; style pass with fixed recipes)*

These screens get the same look without a new mockup, using the recipes in section 7. If W0-3 shows a
screen whose layout would change materially, the WP first adds a small mockup to `redesign/index.html`
and waits for approval (kind `desktop`).

| WP | Model | Kind | Size | Deps | Screens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W7-1 | sonnet | auto | M | W6-R | Security dialogs: change master password, office recovery, rejoin office, first run, force update, startup loading, shadow start. Extra care: they handle passwords and keys; masking and focus behaviour unchanged. |
| W7-2 | sonnet | auto | L | W7-1 | Data dialogs: MCL manager, MCL column edit, service manager, service edit, CSV import. |
| W7-3 | sonnet | auto | L | W4-R | Remaining Settings pages: actions, SCC, services, tracker, export, backup, purge, QC, and the three visibility pages (kept, as the spec promises). The legacy `settings_dialog.py` is checked for use; if unused, only noted. |
| W7-4 | sonnet | auto | M | W7-2 | SGT lab, SDIS dialogs, LTT rules, SCA diagnostics, update dialog. |
| W7-5 | sonnet | auto | M | W7-4 | Consistency pass on already redesigned or untouched shells: sidebar active item / icons from tokens, Manage Clients and Tracker Dump moved onto kit components where they duplicate them, toast colours from tokens. |
| W7-R | opus | review | M | W7-5 | Sweep: grep for hard-coded colours outside `tokens.py`, every screen screenshot vs recipe, all dialogs reach at 125 % scaling. |

### Phase 8 — Finish

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W8-1 | sonnet | auto | M | W7-R | Rewrite `docs/Sera_UI.md` to describe the new system as current (tokens, kit, QSS snippets, screen layouts); update `Mockups/README.md` status to "built". |
| W8-2 | haiku | desktop | S | W8-1 | Take real screenshots of each built screen into `Mockups/built/` for the human comparison (fictional data only). |
| W8-R | opus | review | L | W8-2 | Final review and merge-readiness report: full suite, startup time and memory compared to W0-1, every row of every "where every control goes" table ticked, open checks listed. Merge is the owner's decision. |

**Count:** 8 phases, 35 packages (9 reviews). Longest chain: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. W7-3 can
start once phase 4 is reviewed; W5 and W6 do not depend on each other and may swap order.

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

### 4.3 Settings — Columns table and "Shows on" write-through *(W4-3, uses W4-1)*

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

### 4.4 Settings — unsaved changes bar *(W4-1)*

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

### 4.5 Audit log — readable values *(W5-1, W5-2)*

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

### 4.6 Sera Sync — cards and the "Inv-Frames" switch *(W6-1 to W6-3)*

- **Feature map first (W6-1):** every control of the current dialog gets a documented place before
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
- R6.3 ◆ 100 / 125 / 150 % scaling, 1366×768, no clipped text, no sideways scroll outside the grid.
- R6.4 `test_validate_icons` passes (every `mdi.*` icon name used exists).
- R6.5 Screen reader names are not required, but every control has a text or tooltip name.

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

## 7. Recipes for screens without a mockup *(phase 7)*

Apply in this order and stop when the screen reads clean; do not invent new layouts.

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
| Palette | The mockup's tokens win over `Sera_UI.md` where they differ; `Sera_UI.md` is rewritten in W8-1 |
| Older blueprint | Superseded; its primitives are reused |
| Settings "Shows on" edits | Staged until Save (4.3) |

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
| 1 | W1-3 | Look at the gallery PNGs next to `redesign/index.html`: same feel, same colours | Not run |
| 2 | W2-R | Open a real client with several services; press each Alt+number; confirm the right service starts and the label shown is that key | Not run |
| 3 | W3-R | Use All Clients with the real client list for a day: selection bar, Columns menu, filters, no sideways scroll | Not run |
| 4 | W4-R | Change a setting, close without saving, confirm the prompt; tick "Shows on" and confirm the main screen after Save | Not run |
| 5 | W5-R | Open the audit log on a PC with real history; service names and times read correctly | Not run |
| 6 | W6-R | With two real PCs: scan, select, Sync selected; toggle "Don't accept incoming syncs" and confirm the other PC cannot push | Not run |
| 7 | W7-R | Open every dialog once at 125 % and 150 % scaling; look for clipping | Not run |
| 8 | W8-R | Compare `Mockups/built/` with the mockup images screen by screen | Not run |

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

*(none yet)*
