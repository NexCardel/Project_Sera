# Fleet redesign: build plan (kit first)

Companion to `README.md` (the design) and `Mockups/IMPLEMENTATION.md` (rules, "kept as it is"
checks, test plan). This file replaces the **order** of IMPLEMENTATION.md's phases 1 to 7 with a
reuse-first order; its rules (section 1), behaviour checks (section 5) and engineering specs
(section 4) still apply unchanged. Written 2026-10-10; nothing in the app has changed yet.

## 1. The idea in one paragraph

Every screen in the mockup is made from one token block and about twenty components. So the build
makes those once, proves them once (gallery + tests), and then every screen work package is only
*arrangement*: it may import from `ui/utils/tokens.py` and `ui/components/kit.py`, and it may not
define a colour, a radius, a stylesheet string or a new widget class of its own. That keeps every
screen uniform by construction, and it keeps each screen package small enough for one short session:
the agent reads the kit's docstrings and the screen's control table, not the whole codebase.

## 2. Rules that make reuse real (added to IMPLEMENTATION.md section 1)

11. **Screens import, they do not style.** No `setStyleSheet(` with a literal in any file under
    `ui/windows/`, `ui/dialogs/` or `ui/shell/` once the screen is converted. A test greps for it.
12. **One widget per look.** If a screen needs something the kit lacks, the work package stops, adds
    it to the kit (with gallery state and test) in a small kit package, then continues. No local
    one-offs "for now".
13. **Kit API is frozen after K-R** except by additive changes (new variant, new optional argument).
    Screens never subclass kit widgets to change their look; they compose them.
14. **The gallery is the contract.** `tools/ui_gallery.py` renders every component in every state to
    `Mockups/built/kit/*.png`; a screen package may not change those images.
15. **Token conservation.** A screen package's prompt is: the kit module's docstring (about 150 lines),
    the screen's "where every control went" table from `README.md`, the screen's current file, and its
    tests. Nothing else is read unless a test fails.

## 3. Phases and work packages

Columns as in IMPLEMENTATION.md: model tier, kind (`auto` / `desktop` / `review`), size S/M/L, deps.
Each phase ends with a review gate (`-R`).

### Phase T: tokens *(1 package)*

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| T-1 | sonnet | auto | S | W0-R | `ui/utils/tokens.py` from the `:root` block of `Mockups/fleet/index.html` (every colour, radius, spacing, font size, control size, widths). Point `ui/utils/theme.py` at it; existing QSS output byte-identical until a screen opts in. A test asserts every token name in the README table exists. |

### Phase K: the kit *(built once, in dependency order; each package adds gallery states and tests)*

| WP | Model | Kind | Size | Deps | Components |
| :--- | :--- | :--- | :--- | :--- | :--- |
| K-1 | opus | auto | M | T-1 | **Primitives**: `LabelledAction` (primary / soft / secondary / ghost / danger, `sm`, disabled with reason tooltip, icon + text), `IconButton` (bordered, primary, `sm`, tooltip mandatory), `Segmented`, `Key` (keyboard hint), `Pill` (7 tones), `Tag` (4 tones), `StatusDot`. Promote nothing yet; these are the leaves everything else uses. |
| K-2 | sonnet | auto | M | K-1 | **Inputs**: `Input` (leading icon, trailing action slot), `Select`, `TextArea`, `Stepper`, `Checkbox` (list use only), `Switch` (promotes `ToggleSwitch` from the sidebar; keyboard operable, disabled with reason). `FieldLabel` (label above field). |
| K-3 | sonnet | auto | M | K-1 | **Containers**: `Card` (caps title, right-hand slot, `danger` and `em` edges, no nesting), `SettingRow` (title + sub-label, fixed 230 px control column, `auto` / `wide`), `SectionLabel` (reuse the existing one), `Banner` (w / r / g / i), `Toast` (promote the shell's alert), `EmptyState`. |
| K-4 | sonnet | auto | M | K-1, K-2 | **Bars**: `Chip` + `ChipBar` (single-select group, count badge, ▾ menu chip, "More ▾" overflow, COMPACT second-row mode) promoting `FilterChip` from Manage Clients; `StatusStrip`; `SelectionBar` (fixed height, groups with dividers, `NoFocus` buttons, hint when empty, icon-only mode for COMPACT); `UnsavedBar`. |
| K-5 | opus | auto | L | K-1 | **Data grid**: `DataGrid` (QTableView + delegate) with the white tone and the dark tone, sticky header, zebra, mint selection with emerald edge, `GridFooter` with hints, cell pills (service dots, status pills), cell action buttons drawn by the delegate (never widgets per row), priority column classes with `fit_columns()` for COMPACT, `DarkListTable` for dialogs. Keeps the search grid's highlight engine untouched. |
| K-6 | sonnet | auto | M | K-1, K-3 | **Frames**: `DialogFrame` (icon, title, one-line purpose, body, footer with one primary, `full` variant, scrim), `PageHeader` (title, count, lead, actions), `SlidePanel` restyle (540 px, full width in COMPACT), `Tile` (summary tile), `LogView` (monospace activity log with tag colours). |
| K-7 | sonnet | auto | M | K-1…K-6 | `tools/ui_gallery.py`: every component, every state (normal, hover, focus, disabled, checked, long text, empty, COMPACT) at 100 % and 125 % to `Mockups/built/kit/`. Tests per component: state, signals, Tab / Space / Enter, tooltip presence, elision. Kit docstring written as the one document screen packages read. |
| K-R | opus | review | M | K-7 | Gallery PNGs against `index.html`'s Foundation page side by side; tokens only (grep); no timers, no per-row widgets; import time and memory unchanged (`test_startup_speed`, `test_memory_tuning`); `ToggleSwitch` and `FilterChip` users migrated and green. **Kit API frozen.** |

### Phase R: responsive and shell *(the two things every daily screen needs)*

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| R-1 | sonnet | auto | S | K-R | `ui/utils/responsive.py` is already there (WIDE / COMPACT with hysteresis); add a `WidthAware` mixin that calls `apply_width_mode(mode)` on kit bars and grids so screens only list which columns and chips move. |
| R-2 | sonnet | auto | M | K-R | **Sidebar** on the kit: real logo (`sera_mark.svg`), brand not truncated, groups Clients / Tools / Admin, one All Clients entry, Display entry for everyone, Manage Staff Users removed, Auto-sync pill as `LabelledAction soft`, auto-collapse below 1000 with manual override. |
| R-3 | sonnet | auto | S | K-R | **Display scale**: `DisplayScaleControl` rebuilt as a `SettingRow` + two `LabelledAction`s; `DisplayScaleDialog` on `DialogFrame`. Same QSettings keys, same restart path. |
| R-R | opus | review | S | R-3 | Sidebar screenshots at 1366 / 1024 / 1366 @ 125 %; shortcuts Ctrl+B, Ctrl+= / − / 0 unchanged. |

### Phase S: screens *(arrangement only; ordered by how much kit each one exercises)*

Each package: read the kit docstring and the screen's table in `README.md`; rebuild the layout
around the existing attribute names; run the screen's lock tests from W0-2; add a golden screenshot
at 1366, 1024 and 1366 @ 125 % to `Mockups/built/`. A screen that needs a kit change stops and
files a K-x package first.

| WP | Model | Kind | Size | Deps | Screen | Kit used |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| S-1 | sonnet | auto | M | R-R | Settings hub shell + unsaved bar + dirty tracking (IMPLEMENTATION 4.4) + General page | DialogFrame full, nav, Card, SettingRow, Switch, Select, Stepper, UnsavedBar |
| S-2 | sonnet | auto | M | S-1 | Settings: Action buttons, Tracker, Export, Backup, Purge | same + Banner, danger Card |
| S-3 | sonnet | auto | M | S-1 | Settings: SCC vault presets | Card, Tile, formula Tags, Input |
| S-4 | opus | auto | L | S-1, K-5 | Settings: Columns (MCL) with Shows-on write-through (IMPLEMENTATION 4.3) + the three visibility pages | DarkListTable, Tag, Pill, Checkbox, Switch |
| S-5 | sonnet | auto | M | S-4 | Settings: Services; MCL manager, MCL column edit, Service manager, Service edit dialogs | DialogFrame, DarkListTable, FieldLabel, Select |
| S-6 | sonnet | auto | M | R-R, K-5 | All Clients / Search: header, chips (combo stays hidden), grid tone white, Columns ▾ menu (IMPLEMENTATION 4.2) | PageHeader, ChipBar, DataGrid, GridFooter |
| S-7 | opus | auto | L | S-6 | All Clients: selection bar (IMPLEMENTATION 4.1) and COMPACT behaviour | SelectionBar, fit_columns |
| S-8 | sonnet | auto | M | S-6 | Client detail panel | SlidePanel, Card, LabelledAction, Key, Pill, EmptyState line |
| S-9 | sonnet | auto | M | S-7 | Manage Clients onto the kit (list tone white, chips, cards, bulk bar as SelectionBar) | ChipBar, DataGrid (list mode), Card, SelectionBar |
| S-10 | sonnet | auto | M | S-7 | Tracker Dump onto the kit (dark tone grid, tiles, chips, row actions by delegate; WIDE pixel-identical to today's COMPACT behaviour) | Tile, ChipBar, Segmented, DataGrid dark |
| S-11 | sonnet | auto | L | S-6 | Audit log (IMPLEMENTATION 4.5) | DialogFrame full, ChipBar, DataGrid, Card details |
| S-12 | opus | auto | L | S-6 | Sera Sync: feature map first (IMPLEMENTATION 4.6), then strip, three tabs, device cards, activity | StatusStrip, Tabs, Card, LogView, Banner |
| S-13 | sonnet | auto | M | K-R | Small dialogs: CSV import (+ summary), Change master password, Office recovery, Rejoin office (+ import), First run (3 pages + join request), Force update, Loading, Add client, Admin PIN, Shadow start | DialogFrame, FieldLabel, Banner, Progress |
| S-14 | sonnet | auto | M | S-13 | Remaining not-drawn dialogs by recipe: Container inspector, Create client from capture, Map datapoints, Add workstation code, Add / Remove PC by IP, SCA diagnostics, LTT rules, ordinary update dialog, every QMessageBox through one helper | DialogFrame |
| S-R | opus | review | L | S-14 | Every "where every control went" row ticked; every golden screenshot compared with `index.html`; grep: no literal stylesheet, no hex outside tokens; full suite; start-up time and memory vs baseline. |

### Phase F: finish

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| F-1 | sonnet | auto | M | S-R | Rewrite `docs/Sera_UI.md` from the kit docstring and tokens; update `Mockups/README.md` and this file's status to "built". Remove `settings_dialog.py` if nothing opens it (owner: not live). |
| F-2 | haiku | desktop | S | F-1 | Real screenshots of every screen into `Mockups/built/` (fictional data). |
| F-R | opus | review | L | F-2 | Merge-readiness report as in IMPLEMENTATION W8-R. Merge is the owner's call. |

**Count:** 5 phases, 31 packages (5 reviews). Longest chain: T-1 → K-1 → K-5 → K-7 → K-R → R-R →
S-6 → S-7 → S-9/S-10 → S-R → F-R. S-1…S-5 (settings) and S-13 can run as soon as K-R passes, in
parallel with S-6…S-12.

## 4. What each kit component must offer (from the mockup)

| Component | Variants / states the screens use |
| :--- | :--- |
| LabelledAction | primary, soft, secondary, ghost, danger; sm; disabled + reason; icon-only mode (COMPACT selection bar, Open keeps its label); colour swatch slot (Fill / Text) |
| IconButton | plain, bordered, primary, sm; tooltip required |
| Chip / ChipBar | on/off, count badge, value-when-on ("Client: Unregistered"), ▾ menu, More ▾ overflow, single-select group, second-row mode |
| Switch | on/off, disabled + reason; sidebar admin switch and every on/off setting |
| Pill | neutral, emerald, amber, red, blue, purple, orange; with StatusDot |
| Tag | neutral, emerald, amber, red, blue; mono |
| Card | caps title, right slot, danger edge, emerald edge, body padding, no nesting |
| SettingRow | control column 230 (200 in COMPACT), `auto`, `wide` (wraps in COMPACT) |
| StatusStrip | pills, bold facts, separators, wraps |
| SelectionBar | empty hint, client group, cell group, far-right danger; fixed 44 px; NoFocus |
| UnsavedBar | count text, Discard, Save; hidden when clean; reserved space |
| EmptyState | icon ring, title, sentence, optional actions |
| Banner | attention, error, success, information; dismiss slot |
| Toast | bottom-centre of content area |
| DataGrid | white and dark tones; sticky header; zebra; selection; highlight cells; service dots; status pills; row action buttons; priority columns; footer hints; list mode (Manage Clients) |
| DarkListTable | selected row, tags and pills in cells |
| DialogFrame | icon tone (emerald, amber, blue), title, purpose, body, footer with one primary; full-window; desktop scrim |
| PageHeader | title, count, lead (hidden in COMPACT), actions |
| Tile | value colour, caption, on state; smaller in COMPACT |
| LogView | timestamp + tag colours (PEER, SYNC, GUARD, NETWORK, ERROR) |
| Progress | emerald bar |

## 5. Decisions carried into the build

- Tracker Dump: dark grid tone; every other data grid white.
- One All Clients entry; Manage Staff Users and the legacy settings dialog are not built.
- Sera Sync tabs visible to all; admin actions keep PIN confirmation.
- COMPACT selection bar: icons with tooltips.
- Display entry always visible; display scale saved per PC, never synced.
- Audit log stays admin-only. Column types (Email, TAN) are schema data edited in Settings → Columns, not design.
- Redesign only: no sync protocol change (no revision score / ahead-behind label).
