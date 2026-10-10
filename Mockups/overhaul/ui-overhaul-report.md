# UI overhaul — report (2026-10-10 18:51)

Deadline: 2026-10-12T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 17 |
| In progress | 0 |
| Retry | 0 |
| Done | 19 |
| Blocked | 0 |

Runs: 28   output tokens: 898468   API-equivalent cost: $50.67

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | haiku | 40bc942 | Baseline: full test suite, start-up and memory numbers, current snapshots | Baseline: startup/memory tests pass, 31 UI screenshots rendered, cloud environment viable for offscreen testing |
| W0-2 | Done | sonnet | 537924a | Behaviour lock: tests for every 'must still work' item, green on the unchanged code | Behaviour lock: 8 offscreen test files tests/test_ui_lock_*.py (R1-R9 gaps) + goldens, no app code changed; Tracker CSV export bug (no import csv) locked as-is; |
| W0-3 | Done | mechanical | 5191b06 | Screen inventory | Screen inventory written to Mockups/INVENTORY.md |
| W0-D | Done | fable | e0d32a5 | Fleet redesign mockup (cloud, Fable 5.1): every screen in one new HTML mockup | Fleet mockup by Fable 5.1 (cloud); owner decisions and BUILD-PLAN.md in 9d66d4e |
| W0-R | Done | opus | 946bebe | Review the safety net | 5/5 mutations caught (detail, search, settings, audit, sync); added audit search-by-id case; INVENTORY covers all 31 PNGs; 147 lock tests pass |
| T-1 | Done | haiku | 5595c31 | Tokens: ui/utils/tokens.py from the :root block of Mockups/fleet/index.html (every colour, ... | tokens.py from the fleet :root block; theme.py reads it byte-identical; 3 token tests green |
| K-1 | Done | opus | 1e69df0 | Kit: Primitives: LabelledAction (primary / soft / secondary / ghost / danger, sm, disabled ... | kit.py primitives + KIT tokens + minimal ui_gallery; 32 tests pass; danger outlined per mockup |
| K-2 | Done | sonnet | 61833da | Kit: Inputs: Input (leading icon, trailing action slot), Select, TextArea, Stepper, Checkbox ... | Kit inputs: FieldLabel, Input, Select, TextArea, Stepper, Checkbox, Switch; 43 kit tests pass; sidebar ToggleSwitch left for the shell WP |
| K-3 | Done | sonnet | 4169c66 | Kit: Containers: Card (caps title, right-hand slot, danger and em edges, no nesting), ... | Kit containers: Card, SettingRow, SectionLabel, Banner, Toast, EmptyState; 58 kit tests pass, gallery containers page |
| K-4 | Done | sonnet | da633a8 | Kit: Bars: Chip + ChipBar (single-select group, count badge, ▾ menu chip, "More ▾" overflow, ... | kit bars (Chip, ChipBar, StatusStrip, SelectionBar, UnsavedBar), gallery, tests |
| K-5 | Done | opus | 5c8093b | Kit: Data grid: DataGrid (QTableView + delegate) with the white tone and the dark tone, sticky ... | DataGrid (QTableWidget subclass, delegate-drawn white/dark/list tones, pills, service dots, drawn actions, fit_columns), DarkListTable, GridFooter, GridFrame; 9 |
| K-6 | Done | sonnet | 782bc31 | Kit: Frames: DialogFrame (icon, title, one-line purpose, body, footer with one primary, full ... | DialogFrame, Scrim, PageHeader, Tile, LogView, SlidePanel restyle (540 / full width COMPACT); 52 new tests pass; frames gallery page |
| K-7 | Done | sonnet | fd10027 | Kit: tools/ui_gallery.py: every component, every state (normal, hover, focus, disabled, ... | Gallery states page (hover/focus/disabled), 14 PNGs at 100/125, kit reference docstring, 38 new keyboard/tooltip/elision/gallery tests, Chip Enter |
| K-R | Done | opus | e9a31bc | Review phase K (kit) | Kit reviewed and frozen; 5 kit fixes, gallery uses real Windows fonts, ToggleSwitch/FilterChip users migrated; only pre-existing PAN + order-leak failures |
| R-1 | Done | mechanical | 7773fd8 | Responsive and shell: ui/utils/responsive.py is already there (WIDE / COMPACT with hysteresis); add a ... | add WidthAware mixin to ui/utils/responsive.py for kit propagation, column toggling and chip moves |
| R-2 | Done | sonnet | 5c5ae20 | Responsive and shell: Sidebar on the kit: real logo (sera_mark.svg), brand not truncated, groups Clients / ... | Sidebar on the kit: real logo, CLIENTS/TOOLS/ADMIN groups, one All Clients, Display for all, Staff Users removed, soft Auto-sync pill; 17 new tests |
| R-3 | Done | haiku | 58c6edd | Responsive and shell: Display scale: DisplayScaleControl rebuilt as a SettingRow + two LabelledActions; ... | Display scale on the kit: SettingRow + Apply/Restart LabelledActions, DialogFrame dialog; same QSettings and restart path; tests pass; one real-screen check add |
| R-R | Done | sonnet | 8d136ee | Review phase R (responsive and shell) | Phase R reviewed: sidebar clean at 1366/1024/1366@125%; fold thresholds and Ctrl+B / scale shortcuts locked by tests; no app code changed |
| S-1 | Done | sonnet | 8c2e4dc | Screens: Settings hub shell + unsaved bar + dirty tracking (IMPLEMENTATION 4.4) + General page | Settings hub + unsaved bar + dirty/discard/close prompt + General page on kit; lock + 13 new tests green, goldens in Mockups/built/settings |
| S-2 | Not started | haiku |  | Screens: Settings: Action buttons, Tracker, Export, Backup, Purge |  |
| S-3 | Not started | haiku |  | Screens: Settings: SCC vault presets |  |
| S-4 | Not started | opus |  | Screens: Settings: Columns (MCL) with Shows-on write-through (IMPLEMENTATION 4.3) + the three ... |  |
| S-5 | Not started | sonnet |  | Screens: Settings: Services; MCL manager, MCL column edit, Service manager, Service edit dialogs |  |
| S-6 | Not started | sonnet |  | Screens: All Clients / Search: header, chips (combo stays hidden), grid tone white, Columns ▾ menu ... |  |
| S-7 | Not started | opus |  | Screens: All Clients: selection bar (IMPLEMENTATION 4.1) and COMPACT behaviour |  |
| S-8 | Not started | haiku |  | Screens: Client detail panel |  |
| S-9 | Not started | sonnet |  | Screens: Manage Clients onto the kit (list tone white, chips, cards, bulk bar as SelectionBar) |  |
| S-10 | Not started | sonnet |  | Screens: Tracker Dump onto the kit (dark tone grid, tiles, chips, row actions by delegate; WIDE ... |  |
| S-11 | Not started | sonnet |  | Screens: Audit log (IMPLEMENTATION 4.5) |  |
| S-12 | Not started | opus |  | Screens: Sera Sync: feature map first (IMPLEMENTATION 4.6), then strip, three tabs, device cards, ... |  |
| S-13 | Not started | sonnet |  | Screens: Small dialogs: CSV import (+ summary), Change master password, Office recovery, Rejoin ... |  |
| S-14 | Not started | haiku |  | Screens: Remaining not-drawn dialogs by recipe: Container inspector, Create client from capture, ... |  |
| S-R | Not started | opus |  | Review phase S (screens) |  |
| F-1 | Not started | mechanical |  | Finish: Rewrite docs/Sera_UI.md from the kit docstring and tokens; update Mockups/README.md and ... |  |
| F-2 | Not started | mechanical |  | Finish: Real screenshots of every screen into Mockups/built/ (fictional data). |  |
| F-R | Not started | opus |  | Review phase F (finish) |  |

## Decisions taken for you

- **W0-2** Bulk archive/delete write no audit row, and Restore asks no confirmation, in Manage Clients. Keep or fix? → Locked as-is in tests/test_ui_lock_manage.py; the overhaul must not change it unless the owner asks (Old code behaviour, not a regression to hide; changing it is a product decision for a later WP)
- **W0-2** Manage Clients 'Save anyway' on a duplicate that also shares a primary key (company name / PAN) still fails with the Validation Error; and Search > Manage services leaves the table selection empty offscreen. Keep? → Locked as current behaviour: the duplicate prompt only lets a save through for identity values that are not primary keys (e.g. proprietor); manage_client_services is tested by its hand-off only (Both are old-code quirks; the real-desktop part of the second is a human check (check-add))
- **W0-2** Tracker Dump > Tools > Export Captures (CSV) always fails: tracker_dump_window.py never imports csv, so the user sees 'Export Failed'. Fix it in W0-2? → Locked as-is (test pins the failure); not fixed, W0-2 must not change app code. Fix it when the Tracker Dump WP touches the file: add 'import csv' and flip tests/test_ui_lock_tracker.py::test_export_csv_currently_fails_because_tracker_dump_window_never_imports_csv (Behaviour-lock WP changes no app code; one-line fix is safe for a later WP.)
- **T-1** How much of the mockup :root is wired into the legacy QSS in theme.py? → Substitute a token only where the literal has the same role (surfaces, text, muted, emerald family, divider line, hover fill, danger hover edge, warn text, font sizes 11/12/13/14/17); keep every other literal (Output is byte-identical to HEAD (17512 chars, checked with a throwaway script); borders and input fills are not mislabelled as hover or side tokens)
- **T-1** Legacy danger colour: #A82424 fill vs mockup --danger #FF4D4D → Keep #A82424 as the legacy danger button fill; #FF4D4D only on the danger hover edge (Byte-identical until screens opt in; K-1 LabelledAction danger must settle the fill and text colour (W1-1 conflict record))
- **T-1** Radii and spacing in the legacy QSS → Radius and spacing tokens are defined in tokens.py but not wired into theme.py (Legacy radii are 5, 7, 9 and 10 px, not the mockup 6, 8, 12 scale; wiring them would change the look before any screen opts in)
- **T-1** Token value format in tokens.py → Keys are CSS names with dashes as underscores; colours upper-case hex; px sizes as int; outer viewer tokens (page, ink, ink2, ink3, rule) included but unused by the app (Matches theme.py hex style and lets string.Template write ${f_m}px; the test compares with the mockup :root case-insensitively)
- **K-1** LabelledAction danger: filled #A82424 (today's theme) or the mockup's outlined red? → Outlined: transparent, --dangerline edge, --dangertx text, --dangerdim on hover (The fleet mockup's .btn.dng is outlined and the mockup's tokens win (decision 2026-10-05); the old filled class stays in theme.py for unconverted screens)
- **K-1** Where do the mockup's component-level literals (outside :root) and half-px font sizes go? → New KIT dict in ui/utils/tokens.py; 12.5px and 10.5px rounded up to 13 and 11 (TOKENS must equal the :root block (test_ui_tokens); kit.py stays hex-free; Qt px font sizes are ints)
- **K-4** Bar behaviors and COMPACT modes → SelectionBar enforces NoFocus and Open keeps label; ChipBar supports single_select, overflow and second-row; UnsavedBar default hidden when clean (Matches IMPLEMENTATION.md 4.1/4.4 and fleet index.html specs)
- **K-5** DataGrid base class: QTableView with a new model, or QTableWidget (which is a QTableView)? → DataGrid subclasses QTableWidget; all drawing is in GridDelegate, which also works on any QTableView (Every grid in the app (search, tracker, audit, sync) is a QTableWidget whose items carry the cell-format highlights (BackgroundRole/ForegroundRole); a drop-in subclass keeps the highlight engine and the attribute/item API tests reach untouched)
- **K-R** Migrate ToggleSwitch / FilterChip users now or leave them to R-2 / S-* packages? → Migrated now: sidebar toggle_admin -> kit.Switch (old ToggleSwitch class deleted); Manage Clients chips + More -> kit.Chip; Tracker Dump filter chips -> kit.Chip (literal ▾ left the label, the chip draws it; lock test text updated, behaviour unchanged) (BUILD-PLAN K-R row requires users migrated and green; attribute names and handlers kept, so it is drop-in)
- **K-R** Kit Toast keeps a single-shot QTimer; does that break 'no timers'? → Kept (It is the auto-hide of the alert it replaces (same show_alert duration API), started only on show; no polling or animation timers anywhere in the kit)
- **R-3** Screen scale row width → SettingRow width auto; combo, Apply and Restart now as its controls (Combo plus two actions need more than the 230 px fixed column; wide 360 px is tighter at 1.25 scale)
- **R-3** Button variants and primary → Apply = soft, Restart now = secondary, dialog Close = secondary; no primary in the dialog (Settings General keeps Save changes as the only primary on that screen (rule 4))
- **R-3** Where the scale row sits on Settings General → DisplayScaleControl added straight to the page layout inside a 12 px side wrapper; the old _setting_row wrapper dropped (The control is now a full SettingRow, so _setting_row would show the title twice; 12 px (s_3) plus the row's 16 px makes the 28 px page rhythm)
- **R-3** Running-at line and hint → Running-at line is the row sub-label (set_sub after Apply); the hint moves to the dialog purpose line, shortened to fit one elided line (SettingRow has one sub-label and DialogFrame purpose is a single elided line; the design puts the running-at line in the row)
- **S-1** Theme combo label → Label 'Dark', data stays 'light' (Matches the mockup and the app's real look; the stored value is the golden-tested key so only the label changed.)
- **S-1** Clipboard-clear stepper step → Step 5 s, range 5-300, setValue is not snapped (Stepper has no setRange/setSuffix; 5 s steps make 5-300 reachable; a saved odd value still loads unchanged.)
- **S-1** Dialog legacy QSS and compact trigger → Keep _apply_global_style for the 9 unconverted pages and child dialogs; compact is driven by dialog width minus nav (below 700 on, above 740 off) (Arrangement-only package; other pages still need the legacy sheet. Dialog width is deterministic where the stacked pane width is stale before layout.)
- **S-1** Unsaved bar and two primary Save buttons → Bar shows on every page when dirty (reserved space); footer Save and bar Save both primary, as in the mockup (Dirty tracking spans all pages, so the bar belongs to the shell. The mockup draws two Save buttons while dirty; both call the same save.)
- **S-1** Close with unsaved changes → Three-way prompt in reject(): Save changes / Discard changes / Keep editing (reject() also serves Esc and the title-bar X, so one override guards all exits; Save closes only if the save left nothing dirty.)

## Checks waiting for you

- #1 (W0-2) R1.6 on a real desktop: from Search, use Edit / Archive / Delete / Manage services on one client and confirm each acts on the client that was clicked (offscreen, open_client_editor leaves the table selection empty after setCurrentCell, so Manage services may report 'Select one or more clients first.') — Not run
- #2 (W0-2) R6.3 on a real PC: open Search, Manage Clients, Tracker Dump, Audit, Settings and Sera Sync at 100 %, 125 % and 150 % Windows scaling and at 1366x768; nothing clipped, no horizontal scroll, text readable — Not run
- #3 (W0-2) Real keyboard on a real desktop: press Ctrl+= / Ctrl+- / Ctrl+0 (alert appears, restart applies the scale), Alt+n and Alt+Ctrl+n on a search result, Ctrl+B, F5 in Tracker Dump, and Tab order through the Manage Clients form; offscreen tests call the handlers/shortcuts directly — Not run
- #4 (K-5) On a real desktop: hover a DataGrid row and its drawn action buttons (hover fill, tooltip per button, click runs the action); drag a column edge and confirm the width survives a window resize — Not run
- #5 (K-6) On a real 125% display, open a SlidePanel in a window narrower than 1080 px: it should fill the width with no left edge, and a DialogFrame (normal and full) should show no clipped title or footer. — Not run
- #6 (K-R) On a real Windows screen at 100 % and 125 %, compare Mockups/built/kit/*.png lettering and sizes with Mockups/fleet/index.html Foundation page (headless browser render was not allowed in the worker) — Not run
- #7 (R-3) On a real 125 % screen at 1366x768 and 1024x700: open Sidebar Display and Settings General Screen scale, check Apply and Restart now fit, then Restart now really restarts Sera and the new scale applies. — Not run
- #8 (R-R) Sidebar on a real 1366x768 laptop at Windows 125 % and Sera display scale 1.00 / 1.25: brand, All Clients + Ctrl K hint, Auto-sync pill not clipped; Ctrl+B, Ctrl+=, Ctrl+-, Ctrl+0 pressed on a real keyboard (offscreen only tests the wiring). — Not run
- #9 (S-1) On a real 125 % screen, open Settings > General and check nothing clips: nav, five cards, unsaved bar, footer Save. — Not run
- #10 (S-1) Edit one General setting then press Close or Esc: confirm the wording of the 'Unsaved changes' prompt (Save changes / Discard changes / Keep editing) is what you want. — Not run
